"""Codis - incorporar *qualquer* programa no editor (generalização do DOOM).

Dois modos, ambos sobre :class:`~src.ui.window_embed.EmbeddedAppPanel`:

* **Iniciar** - o usuário indica um ``.exe`` (e argumentos); o Codis o executa
  e reparenta a janela. Ao fechar o painel, o programa é encerrado.
* **Adotar** - o usuário indica (ou escolhe numa lista, como no Gerenciador de
  Tarefas) um programa **já em execução**, pelo nome do executável
  (``notepad.exe``) ou por uma janela específica. O Codis reparenta a janela;
  ao fechar o painel ela volta ao desktop e o programa **continua rodando**.

Limites do sistema operacional (mostrados ao usuário quando ocorrem):
programas elevados (administrador) não podem ser reparentados por um Codis
sem elevação (UIPI), e apps UWP/Store usam ``ApplicationFrameHost`` e não
aceitam ``SetParent``. Jogos em tela cheia exclusiva também não.
"""

from __future__ import annotations

import logging
import os
import shlex
import sys
import tkinter as tk
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Any

from src.i18n import _t
from src.ui import window_embed as we

logger = logging.getLogger("Codis.AppEmbed")

__all__ = [
    "AppSpec",
    "AppEmbedDialog",
    "list_candidate_windows",
    "normalize_exe_name",
    "pids_for_exe_name",
    "start_adopt",
    "start_launch",
    "launch_external",
]

_RECENT_KEY = "embed_recent"
_RECENT_MAX = 12

_BLOCKED_EXES = frozenset(
    {"explorer.exe", "dwm.exe", "applicationframehost.exe", "textinputhost.exe",
     "searchhost.exe", "startmenuexperiencehost.exe", "shellexperiencehost.exe",
     "systemsettings.exe", "lockapp.exe"}
)


@dataclass(frozen=True)
class AppSpec:
    """Programa a lançar dentro do editor."""

    exe: Path
    args: tuple[str, ...] = ()
    cwd: Path | None = None

    @property
    def label(self) -> str:
        """Nome curto para a barra do painel."""
        return self.exe.name


def normalize_exe_name(name: str) -> str:
    """Nome de executável comparável: minúsculo, sem aspas/caminho; ``.exe`` no Windows."""
    name = name.strip().strip('"').strip("'").replace("\\", "/").rsplit("/", 1)[-1].lower()
    if sys.platform == "win32" and name and "." not in name:
        name += ".exe"
    return name


def _name_matches(proc_name: str, wanted: str) -> bool:
    got = normalize_exe_name(proc_name)
    if got == wanted:
        return True
    return sys.platform != "win32" and len(got) >= 15 and wanted.startswith(got)


def pids_for_exe_name(name: str) -> list[int]:
    """PIDs de todos os processos cujo executável se chama ``name`` (+ descendentes).

    Descendentes entram porque navegadores/launchers abrem a janela num filho.
    Usa ``psutil`` (dependência do Codis); sem ele devolve lista vazia.
    """
    wanted = normalize_exe_name(name)
    if not wanted:
        return []
    try:
        import psutil
    except ImportError:  # pragma: no cover
        return []
    me = os.getpid()
    pids: set[int] = set()
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            if proc.info["pid"] != me and _name_matches(proc.info["name"] or "", wanted):
                pids.add(proc.info["pid"])
                pids.update(c.pid for c in proc.children(recursive=True))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    pids.discard(me)
    return sorted(pids)


def list_candidate_windows(backend: we.WindowBackend) -> list[we.WindowInfo]:
    """Janelas incorporáveis, sem as do próprio Codis e do shell do sistema."""
    me = os.getpid()
    out = [
        w for w in backend.list_windows()
        if w.pid != me and w.exe.lower() not in _BLOCKED_EXES
    ]
    out.sort(key=lambda w: (w.exe.lower(), w.title.lower()))
    return out


def _labels(name: str, *, adopted: bool) -> we.EmbedLabels:
    hot = we.EXIT_HOTKEY_TEXT
    back = _t("embed.btn_release", "Devolver janela") if adopted else _t("embed.btn_back", "Voltar ao código")
    return we.EmbedLabels(
        hint=_t("embed.hint", "{name}  ·  {hotkey}: voltar ao código", name=name, hotkey=hot),
        back=back,
        focus=_t("embed.btn_focus", "Focar"),
        loading=_t("embed.status_loading", "Incorporando {name}...", name=name),
        embedded=_t("embed.status_embedded", "{name} incorporado. {hotkey} para voltar.", name=name, hotkey=hot),
        closed=_t("embed.status_released", "{name} devolvido ao desktop.", name=name)
        if adopted else _t("embed.status_closed", "{name} encerrado. De volta ao código.", name=name),
        err_exited=_t("embed.err_exited", "{name} encerrou antes de abrir a janela (código {{code}}). {{log}}", name=name),
        err_timeout=_t("embed.err_timeout", "{name} não abriu nenhuma janela em {{seconds}} s.", name=name),
        err_launch=_t("embed.err_launch", "Falha ao iniciar {name}: {{reason}}", name=name),
        embed_failed=_t(
            "embed.err_embed",
            "Não foi possível incorporar {name}: {{reason}}. Programas executados como "
            "administrador, apps da Microsoft Store e jogos em tela cheia exclusiva não aceitam.",
            name=name,
        ),
    )


_THEME = we.EmbedTheme(
    bg="#101114", bar_bg="#1b1d23", bar_fg="#c9d1d9", btn_bg="#2b2f3a",
    btn_fg="#e6edf3", boot_fg="#8b949e", font="Segoe UI" if sys.platform == "win32" else "TkDefaultFont",
)


def _place(container: tk.Widget, panel: we.EmbeddedAppPanel) -> None:
    panel.place(x=0, y=0, relwidth=1, relheight=1)
    panel.lift()
    panel.update_idletasks()


def start_launch(
    container: tk.Widget,
    spec: AppSpec,
    *,
    backend: we.WindowBackend | None = None,
    on_status: Callable[[str], None] | None = None,
    on_close: Callable[[], None] | None = None,
) -> we.EmbeddedAppPanel:
    """Executa ``spec`` e incorpora a janela dele sobre ``container``.

    Raises:
        we.EmbedError: se a plataforma não permite incorporar.
    """
    backend = backend if backend is not None else we.get_backend()
    if backend is None:
        raise we.EmbedError(_t("embed.status_external", "Incorporar janelas não é possível neste ambiente."))
    panel = we.EmbeddedAppPanel(
        container,
        [str(spec.exe), *spec.args],
        backend=backend,
        labels=_labels(spec.label, adopted=False),
        theme=_THEME,
        cwd=str(spec.cwd or spec.exe.parent),
        env=os.environ.copy(),
        log_path=None,
        on_status=on_status,
        on_close=on_close,
    )
    _place(container, panel)
    panel.start()
    return panel


def start_adopt(
    container: tk.Widget,
    *,
    exe_name: str = "",
    window: we.WindowInfo | None = None,
    backend: we.WindowBackend | None = None,
    on_status: Callable[[str], None] | None = None,
    on_close: Callable[[], None] | None = None,
) -> we.EmbeddedAppPanel:
    """Adota a janela de um programa em execução (por nome do exe e/ou janela exata).

    Raises:
        we.EmbedError: sem backend ou sem ``exe_name``/``window``.
    """
    backend = backend if backend is not None else we.get_backend()
    if backend is None:
        raise we.EmbedError(_t("embed.status_external", "Incorporar janelas não é possível neste ambiente."))
    name = exe_name or (window.exe if window else "")
    if not name and window is None:
        raise we.EmbedError(_t("embed.err_no_target", "Informe o nome do executável ou escolha uma janela."))
    resolver: Callable[[], Sequence[int]] | None = (lambda: pids_for_exe_name(name)) if name else None
    panel = we.EmbeddedAppPanel(
        container,
        [],
        backend=backend,
        labels=_labels(name or (window.title if window else "?"), adopted=True),
        theme=_THEME,
        window_timeout=6.0,
        on_status=on_status,
        on_close=on_close,
        adopt_handle=window.handle if window else None,
        adopt_pids=resolver,
    )
    _place(container, panel)
    panel.start()
    return panel


def launch_external(spec: AppSpec) -> None:
    """Fallback sem incorporação: abre o programa em janela própria."""
    import subprocess

    try:
        subprocess.Popen(  # noqa: S603
            [str(spec.exe), *spec.args], cwd=str(spec.cwd or spec.exe.parent),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise we.EmbedError(str(exc)) from exc


class AppEmbedDialog(tk.Toplevel):
    """Escolha entre *adotar janela em execução* e *iniciar um .exe*.

    Args:
        on_adopt: ``(exe_name, window_or_None)`` -> incorpora.
        on_launch: ``(AppSpec)`` -> inicia e incorpora.
        cfg: configuração (``get``/``set``) para lembrar os recentes.
    """

    def __init__(
        self,
        master: tk.Misc,
        tmgr: Any,
        backend: we.WindowBackend | None,
        on_adopt: Callable[[str, we.WindowInfo | None], None],
        on_launch: Callable[[AppSpec], None],
        cfg: Any | None = None,
    ) -> None:
        """Monta o diálogo modal-leve (não bloqueia o editor)."""
        super().__init__(master)
        t = tmgr.theme
        self._t = t
        self._backend = backend
        self._on_adopt, self._on_launch, self._cfg = on_adopt, on_launch, cfg
        self._windows: list[we.WindowInfo] = []
        self.title(_t("embed.dlg_title", "Incorporar programa no Codis"))
        self.configure(bg=t.panel_bg)
        self.geometry("720x520")
        self.minsize(560, 420)
        self.transient(master)

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=10, pady=10)
        self._build_adopt(nb)
        self._build_launch(nb)
        self.bind("<Escape>", lambda _e: self.destroy())
        self.after(30, self._refresh)

    def _build_adopt(self, nb: ttk.Notebook) -> None:
        tab = tk.Frame(nb, bg=self._t.panel_bg)
        nb.add(tab, text=_t("embed.tab_adopt", "Adotar janela em execução"))
        top = tk.Frame(tab, bg=self._t.panel_bg)
        top.pack(fill="x", padx=8, pady=(8, 4))
        tk.Label(top, text=_t("embed.lbl_exe", "Nome do .exe:"), bg=self._t.panel_bg, fg=self._t.fg).pack(side="left")
        self._exe_var = tk.StringVar()
        ent = ttk.Entry(top, textvariable=self._exe_var, width=26)
        ent.pack(side="left", padx=6)
        ent.bind("<Return>", lambda _e: self._do_adopt())
        ttk.Button(top, text=_t("embed.btn_adopt", "Adotar"), command=self._do_adopt).pack(side="left")
        ttk.Button(top, text=_t("embed.btn_refresh", "Atualizar lista"), command=self._refresh).pack(side="right")

        flt = tk.Frame(tab, bg=self._t.panel_bg)
        flt.pack(fill="x", padx=8)
        tk.Label(flt, text=_t("embed.lbl_filter", "Filtrar:"), bg=self._t.panel_bg, fg=self._t.fg_muted).pack(side="left")
        self._filter = tk.StringVar()
        self._filter.trace_add("write", lambda *_: self._fill())
        ttk.Entry(flt, textvariable=self._filter).pack(side="left", fill="x", expand=True, padx=6)

        cols = ("exe", "title", "pid")
        self._tree = ttk.Treeview(tab, columns=cols, show="headings", selectmode="browse")
        for col, text, w in (
            ("exe", _t("embed.col_exe", "Executável"), 160),
            ("title", _t("embed.col_title", "Janela"), 380),
            ("pid", "PID", 70),
        ):
            self._tree.heading(col, text=text)
            self._tree.column(col, width=w, anchor="w")
        self._tree.pack(fill="both", expand=True, padx=8, pady=8)
        self._tree.bind("<Double-1>", lambda _e: self._do_adopt())
        self._tree.bind("<<TreeviewSelect>>", self._on_select)
        self._note = tk.Label(
            tab, bg=self._t.panel_bg, fg=self._t.fg_muted, justify="left", anchor="w", wraplength=660,
            text=_t(
                "embed.note",
                "A janela volta ao desktop (e o programa continua aberto) quando você devolve ou "
                f"usa {we.EXIT_HOTKEY_TEXT}. Programas como administrador e apps da Store podem recusar.",
            ),
        )
        self._note.pack(fill="x", padx=8, pady=(0, 8))

    def _build_launch(self, nb: ttk.Notebook) -> None:
        tab = tk.Frame(nb, bg=self._t.panel_bg)
        nb.add(tab, text=_t("embed.tab_launch", "Iniciar programa"))
        self._path_var, self._args_var = tk.StringVar(), tk.StringVar()
        for row, (label, var, browse) in enumerate((
            (_t("embed.lbl_path", "Executável:"), self._path_var, True),
            (_t("embed.lbl_args", "Argumentos:"), self._args_var, False),
        )):
            tk.Label(tab, text=label, bg=self._t.panel_bg, fg=self._t.fg).grid(row=row, column=0, sticky="w", padx=8, pady=8)
            ttk.Entry(tab, textvariable=var, width=60).grid(row=row, column=1, sticky="ew", padx=4)
            if browse:
                ttk.Button(tab, text="...", width=3, command=self._browse).grid(row=row, column=2, padx=4)
        tab.columnconfigure(1, weight=1)
        ttk.Button(tab, text=_t("embed.btn_launch", "Iniciar e incorporar"), command=self._do_launch).grid(
            row=2, column=1, sticky="e", padx=4, pady=10)
        tk.Label(tab, text=_t("embed.lbl_recent", "Recentes:"), bg=self._t.panel_bg, fg=self._t.fg_muted).grid(
            row=3, column=0, sticky="nw", padx=8)
        self._recent = tk.Listbox(tab, height=8, bg=self._t.editor_bg, fg=self._t.fg, relief="flat")
        self._recent.grid(row=3, column=1, columnspan=2, sticky="nsew", padx=4, pady=4)
        tab.rowconfigure(3, weight=1)
        self._recent_items = self._load_recent()
        for item in self._recent_items:
            self._recent.insert("end", item)
        self._recent.bind("<Double-1>", self._pick_recent)

    def _refresh(self) -> None:
        try:
            self._windows = list_candidate_windows(self._backend) if self._backend else []
        except Exception as exc:  # noqa: BLE001
            logger.warning("Falha ao listar janelas: %s", exc)
            self._windows = []
        self._fill()

    def _fill(self) -> None:
        self._tree.delete(*self._tree.get_children())
        q = self._filter.get().strip().lower()
        for i, w in enumerate(self._windows):
            if q and q not in w.exe.lower() and q not in w.title.lower():
                continue
            self._tree.insert("", "end", iid=str(i), values=(w.exe or "?", w.title, w.pid))

    def _selected(self) -> we.WindowInfo | None:
        sel = self._tree.selection()
        return self._windows[int(sel[0])] if sel else None

    def _on_select(self, _e: tk.Event) -> None:
        w = self._selected()
        if w:
            self._exe_var.set(w.exe)

    def _do_adopt(self) -> None:
        win = self._selected()
        name = self._exe_var.get().strip()
        if win is not None and (not name or normalize_exe_name(name) == normalize_exe_name(win.exe)):
            target: we.WindowInfo | None = win
            name = win.exe
        else:
            target = None
        if not name and target is None:
            return
        self._remember(f"adopt:{name}")
        self.destroy()
        self._on_adopt(name, target)

    def _browse(self) -> None:
        kinds = [("Executáveis", "*.exe"), ("Todos", "*.*")] if sys.platform == "win32" else [("Todos", "*")]
        path = filedialog.askopenfilename(parent=self, filetypes=kinds)
        if path:
            self._path_var.set(path)

    def _do_launch(self) -> None:
        raw = self._path_var.get().strip().strip('"')
        if not raw or not Path(raw).expanduser().is_file():
            self._note.config(text=_t("embed.err_path", "Caminho do executável inválido."))
            return
        try:
            args = tuple(shlex.split(self._args_var.get(), posix=sys.platform != "win32"))
        except ValueError:
            args = ()
        spec = AppSpec(Path(raw).expanduser(), args)
        self._remember("launch:" + " ".join([raw, *args]))
        self.destroy()
        self._on_launch(spec)

    def _pick_recent(self, _e: tk.Event) -> None:
        sel = self._recent.curselection()
        if not sel:
            return
        kind, _, rest = self._recent_items[sel[0]].partition(":")
        if kind == "adopt":
            self._exe_var.set(rest)
            self._do_adopt()
        else:
            parts = shlex.split(rest, posix=sys.platform != "win32")
            self._path_var.set(parts[0] if parts else "")
            self._args_var.set(" ".join(parts[1:]))

    def _load_recent(self) -> list[str]:
        if self._cfg is None:
            return []
        try:
            return [str(x) for x in (self._cfg.get(_RECENT_KEY, []) or [])][:_RECENT_MAX]
        except Exception:  # noqa: BLE001
            return []

    def _remember(self, entry: str) -> None:
        if self._cfg is None or not entry.split(":", 1)[-1].strip():
            return
        items = [entry] + [e for e in self._load_recent() if e != entry]
        try:
            self._cfg.set(_RECENT_KEY, items[:_RECENT_MAX])
        except Exception as exc:  # noqa: BLE001
            logger.debug("Não salvou recentes: %s", exc)
