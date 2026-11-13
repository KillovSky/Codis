"""Gerenciador de lixeira do Codis.

Onde os arquivos vão
--------------------
Por padrão (``trash_backend = "workspace"``) o item é movido para
``<pasta-de-trabalho>/.codis_trash/`` com um ``rename`` atômico no **mesmo
volume** - apagar um arquivo de 100 GB é instantâneo e nunca vira uma cópia
entre discos.  Com
``trash_backend = "system"`` usa a lixeira nativa do SO (``send2trash``) e
cai para a lixeira do projeto se ela falhar.

Uso::

    TrashManager.set_context(workspace, cfg)      # feito pela árvore de arquivos
    TrashManager.delete(path)                     # lixeira
    TrashManager.delete(path, permanent=True)     # exclusão definitiva
    TrashManager.undo_last()                      # desfaz a última exclusão

    panel = TrashPanel(parent, tmgr, cfg)         # painel integrado
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import importlib.util
import logging
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from src.constants import FONT_UI
from src.i18n import _t
from src.trash_core import (
    CrossDeviceError,
    TrashEntry,
    TrashError,
    TrashRegistry,
    TrashStore,
    _remove_tree,
    human_size,
    trash_root_for,
    tree_size,
)

logger = logging.getLogger("Codis.Trash")

HAS_SEND2TRASH = importlib.util.find_spec("send2trash") is not None
_S2T_CACHE: list[Any] = []


def _get_send2trash() -> Callable[[str], None] | None:
    """Retorna ``send2trash.send2trash`` importando sob demanda (ou ``None``)."""
    if not _S2T_CACHE:
        try:
            from send2trash import send2trash as fn
        except ImportError:
            fn = None
        _S2T_CACHE.append(fn)
    return _S2T_CACHE[0]


class TrashManager:
    """Ponto de entrada único para exclusão/restauração (API estática)."""

    _workspace: Path | None = None
    _cfg: Any = None
    _registry = TrashRegistry()
    _undo: list[tuple[Path, str]] = []
    _undo_lock = threading.Lock()

    @classmethod
    def set_context(cls, workspace: Path | str | None, cfg: Any = None) -> None:
        """Informa a pasta de trabalho atual (e opcionalmente a config)."""
        cls._workspace = Path(workspace) if workspace else None
        if cfg is not None:
            cls._cfg = cfg

    @classmethod
    def workspace(cls) -> Path | None:
        return cls._workspace

    @classmethod
    def _setting(cls, key: str, default: Any) -> Any:
        try:
            return cls._cfg.get(key, default) if cls._cfg is not None else default
        except Exception:  # noqa: BLE001
            return default

    @classmethod
    def backend(cls) -> str:
        v = str(cls._setting("trash_backend", "workspace")).lower()
        return v if v in ("workspace", "system") else "workspace"

    @classmethod
    def delete(cls, path: Path | str, *, permanent: bool = False) -> TrashEntry | None:
        """Move *path* para a lixeira (ou apaga de vez com ``permanent=True``).

        Raises:
            CrossDeviceError: a lixeira ficaria em outro volume e não há lixeira
                nativa para assumir (nada é copiado).
            OSError: demais falhas de E/S.
        """
        p = Path(path)
        if permanent:
            if not _remove_tree(p):
                raise OSError(f"Não foi possível excluir '{p}'.")
            return None

        if cls.backend() == "system" and HAS_SEND2TRASH:
            fn = _get_send2trash()
            if fn is not None:
                try:
                    fn(str(p))
                    return None
                except Exception as exc:  # noqa: BLE001
                    logger.warning("send2trash falhou para '%s': %s - usando lixeira do projeto", p, exc)

        store = TrashStore.for_root(trash_root_for(p, cls._workspace))
        try:
            entry = store.put(p)
        except (CrossDeviceError, PermissionError) as exc:
            fn = _get_send2trash() if HAS_SEND2TRASH else None
            if fn is None:
                raise
            logger.info("Lixeira do projeto indisponível (%s); usando a do sistema.", exc)
            fn(str(p))
            return None
        with cls._undo_lock:
            cls._undo.append((store.dir, entry.id))
            del cls._undo[:-50]
        return entry

    @classmethod
    def undo_last(cls) -> Path | None:
        """Restaura a exclusão mais recente desta sessão (ou ``None``)."""
        while True:
            with cls._undo_lock:
                if not cls._undo:
                    return None
                store_dir, tid = cls._undo.pop()
            try:
                return TrashStore(store_dir).restore(tid)
            except (OSError, TrashError):
                continue

    @classmethod
    def list_entries(cls) -> list[TrashEntry]:
        """Itens visíveis (lixeira do projeto + herdados), mais recentes primeiro."""
        return cls._registry.entries(cls._workspace)

    @classmethod
    def count(cls) -> int:
        return cls._registry.total_count(cls._workspace)

    @classmethod
    def restore(cls, entry: TrashEntry | str, *, to: Path | str | None = None) -> bool:
        """Restaura um item. Aceita :class:`TrashEntry` ou o id."""
        try:
            if isinstance(entry, str):
                found = cls._registry.find(entry, cls._workspace)
                if found is None:
                    return False
                entry = found
            cls._registry.restore(entry, to=to)
            return True
        except (OSError, TrashError) as exc:
            logger.error("Erro ao restaurar '%s': %s", getattr(entry, "id", entry), exc)
            return False

    @classmethod
    def delete_permanent(cls, entry: TrashEntry | str, *, cancel: threading.Event | None = None) -> bool:
        if isinstance(entry, str):
            found = cls._registry.find(entry, cls._workspace)
            if found is None:
                return False
            entry = found
        return cls._registry.purge(entry, cancel=cancel)

    @classmethod
    def empty_trash(cls, *, cancel: threading.Event | None = None,
                    progress: Callable[[int, int], None] | None = None) -> int:
        return cls._registry.empty(cls._workspace, cancel=cancel, progress=progress)

    @classmethod
    def purge_expired(cls) -> int:
        """Aplica retenção por idade/tamanho (configurações ``trash_*``)."""
        days = int(cls._setting("trash_retention_days", 0) or 0)
        max_mb = int(cls._setting("trash_max_mb", 0) or 0)
        if days <= 0 and max_mb <= 0 or cls._workspace is None:
            return 0
        n = 0
        for st in cls._registry.stores_for(cls._workspace):
            n += st.purge_expired(older_than_days=days, max_bytes=max_mb * 1024 * 1024)
        return n

    @classmethod
    def purge_expired_async(cls, done: Callable[[int], None] | None = None) -> None:
        def _run() -> None:
            try:
                n = cls.purge_expired()
            except Exception:  # noqa: BLE001
                logger.debug("purge_expired falhou", exc_info=True)
                n = 0
            if done:
                done(n)

        threading.Thread(target=_run, daemon=True, name="codis-trash-purge").start()


def _ago(iso: str) -> str:
    """'agora', '5 min', '3 h', '2 d' ou a data (YYYY-MM-DD)."""
    try:
        when = _dt.datetime.fromisoformat(iso)
    except ValueError:
        return iso[:10]
    secs = (_dt.datetime.now() - when).total_seconds()
    if secs < 60:
        return _t("trash.ago_now", "agora")
    if secs < 3600:
        return f"{int(secs // 60)} min"
    if secs < 86400:
        return f"{int(secs // 3600)} h"
    if secs < 86400 * 14:
        return f"{int(secs // 86400)} d"
    return iso[:10]


def reveal_in_file_manager(path: Path) -> None:
    """Abre a pasta no gerenciador de arquivos do SO (melhor esforço)."""
    target = path if path.is_dir() else path.parent
    try:
        if sys.platform == "win32":
            os.startfile(str(target))  # type: ignore[attr-defined]  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])  # noqa: S603,S607
        else:
            subprocess.Popen(["xdg-open", str(target)])  # noqa: S603,S607
    except OSError as exc:
        logger.debug("Não foi possível abrir '%s': %s", target, exc)


_COLS = ("name", "origin", "size", "date")


class TrashPanel(tk.Frame):
    """Painel da lixeira: filtro, colunas ordenáveis, restaurar/excluir em lote.

    Operações demoradas (excluir pasta grande, medir tamanho) rodam em threads
    de background e voltam à UI por uma fila - a interface nunca congela.
    """

    def __init__(self, parent: tk.Widget, tmgr: Any, cfg: Any, workspace: Path | None = None) -> None:
        """Inicializa o painel da lixeira do workspace."""
        t = tmgr.theme
        super().__init__(parent, bg=t.sidebar_bg)
        self._tmgr = tmgr
        self._cfg = cfg
        if workspace is not None:
            TrashManager.set_context(workspace, cfg)
        self._entries: dict[str, TrashEntry] = {}
        self._sort_key = "date"
        self._sort_rev = True
        self._filter = tk.StringVar()
        self._ui_q: queue.Queue[Callable[[], None]] = queue.Queue()
        self._pump_job: str | None = None
        self._busy = False
        self._cancel = threading.Event()
        self._sizing: threading.Thread | None = None
        self._build()
        self._filter_trace = self._filter.trace_add("write", lambda *_: self._render())
        self.bind("<Destroy>", self._on_destroy, add="+")
        self.refresh()

    def _build(self) -> None:
        t = self._tmgr.theme
        err = getattr(t, "error_fg", "#ff5555")

        hdr = tk.Frame(self, bg=t.sidebar_bg, height=34)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text=f"🗑  {_t('trash.title', 'LIXEIRA')}", bg=t.sidebar_bg,
                 fg=getattr(t, "sidebar_header_fg", t.fg) or t.fg,
                 font=(FONT_UI, 9, "bold")).pack(side="left", padx=8)

        self._empty_btn = self._link(hdr, _t("trash.empty_action", "Esvaziar"), self._on_empty, hover=err)
        self._empty_btn.pack(side="right", padx=4)
        self._link(hdr, "↻", self.refresh).pack(side="right", padx=2)

        self._where = tk.Label(self, bg=t.sidebar_bg, fg=t.fg_muted, font=(FONT_UI, 8),
                               anchor="w", justify="left", wraplength=380)
        self._where.pack(fill="x", padx=8)

        search = tk.Frame(self, bg=t.sidebar_bg)
        search.pack(fill="x", padx=6, pady=(4, 2))
        self._filter.set("")
        self._placeholder_on = False
        self._search = tk.Entry(search, textvariable=self._filter, bg=t.input_bg, fg=t.fg,
                                insertbackground=t.fg, relief="flat", font=(FONT_UI, 9))
        self._search.pack(fill="x", ipady=3)
        self._set_placeholder()
        self._search.bind("<FocusIn>", self._clear_placeholder)
        self._search.bind("<FocusOut>", lambda _e: self._set_placeholder())

        box = tk.Frame(self, bg=t.sidebar_bg)
        box.pack(fill="both", expand=True)
        style = ttk.Style()
        style.configure("Trash.Treeview", background=t.sidebar_bg, foreground=t.sidebar_fg or t.fg,
                        fieldbackground=t.sidebar_bg, borderwidth=0, rowheight=24, font=(FONT_UI, 9))
        style.configure("Trash.Treeview.Heading", background=t.sidebar_bg, foreground=t.sidebar_fg or t.fg)
        style.map("Trash.Treeview", background=[("selected", t.sel_bg)], foreground=[("selected", t.sel_fg)])

        self._tree = ttk.Treeview(box, columns=_COLS, show="headings", selectmode="extended",
                                  style="Trash.Treeview")
        widths = {"name": 150, "origin": 130, "size": 70, "date": 70}
        for c in _COLS:
            self._tree.heading(c, text=_t(f"trash.col_{c}", c), command=lambda k=c: self._sort_by(k))
            self._tree.column(c, width=widths[c], stretch=c in ("name", "origin"),
                              anchor="e" if c == "size" else "w")
        vsb = ttk.Scrollbar(box, orient="vertical", command=self._tree.yview, style="Thin.Vertical.TScrollbar")
        vsb.pack(side="right", fill="y")
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.pack(fill="both", expand=True)

        self._tree.bind("<Button-3>", self._ctx_menu)
        self._tree.bind("<Double-Button-1>", lambda _e: self._on_restore())
        self._tree.bind("<Return>", lambda _e: self._on_restore())
        self._tree.bind("<Delete>", lambda _e: self._on_delete_perm())
        self._tree.bind("<Control-a>", lambda _e: self._select_all() or "break")

        bar = tk.Frame(self, bg=t.sidebar_bg)
        bar.pack(fill="x", padx=4, pady=(4, 2))
        self._restore_btn = self._button(bar, _t("trash.restore", "↩ Restaurar"), self._on_restore)
        self._restore_btn.pack(side="left", padx=(0, 4))
        self._del_btn = self._button(bar, _t("trash.delete_perm", "Excluir permanentemente"), self._on_delete_perm)
        self._del_btn.pack(side="left")

        self._status = tk.Label(self, bg=t.sidebar_bg, fg=t.fg_muted, font=(FONT_UI, 8), anchor="w")
        self._status.pack(fill="x", padx=8, pady=(0, 4))

        self._empty_label = tk.Label(self, text=_t("trash.is_empty", "A lixeira está vazia"), bg=t.sidebar_bg,
                                     fg=t.fg_muted, font=(FONT_UI, 9, "italic"))

    def _link(self, parent: tk.Widget, text: str, cmd: Callable[[], None], hover: str | None = None) -> tk.Label:
        t = self._tmgr.theme
        lbl = tk.Label(parent, text=text, bg=t.sidebar_bg, fg=t.fg_muted, font=(FONT_UI, 10),
                       cursor="hand2", padx=4)
        lbl.bind("<Button-1>", lambda _e: cmd())
        lbl.bind("<Enter>", lambda _e: lbl.config(fg=hover or t.fg))
        lbl.bind("<Leave>", lambda _e: lbl.config(fg=t.fg_muted))
        return lbl

    def _button(self, parent: tk.Widget, text: str, cmd: Callable[[], None]) -> tk.Button:
        t = self._tmgr.theme
        return tk.Button(parent, text=text, bg=getattr(t, "btn_bg", t.sel_bg), fg=t.fg, relief="flat", bd=0,
                         padx=6, pady=3, font=(FONT_UI, 9), cursor="hand2", command=cmd)

    def _set_placeholder(self) -> None:
        if not self._filter.get():
            self._placeholder_on = True
            self._search.configure(fg=self._tmgr.theme.fg_muted)
            self._search.delete(0, "end")
            self._search.insert(0, _t("trash.search_ph", "Filtrar…"))

    def _clear_placeholder(self, _e: Any = None) -> None:
        if self._placeholder_on:
            self._placeholder_on = False
            self._search.delete(0, "end")
            self._search.configure(fg=self._tmgr.theme.fg)

    def _query(self) -> str:
        return "" if self._placeholder_on else self._filter.get().strip().lower()

    def refresh(self) -> None:
        """Relê a lixeira do disco e redesenha."""
        ws = TrashManager.workspace()
        if ws is None:
            self._where.config(text=_t("trash.no_workspace", "Abra uma pasta para ver a lixeira do projeto."))
        else:
            self._where.config(text=_t("trash.location", path=str(ws / ".codis_trash"),
                                       default="Lixeira deste projeto: {path}"))
        self._entries = {e.id: e for e in TrashManager.list_entries()}
        self._render()
        self._start_sizing()

    def _rel_origin(self, e: TrashEntry) -> str:
        ws = TrashManager.workspace()
        parent = Path(e.original).parent
        if ws is not None:
            with contextlib.suppress(ValueError):
                rel = parent.relative_to(ws)
                return "." if str(rel) == "." else str(rel)
        return str(parent)

    def _visible(self) -> list[TrashEntry]:
        q = self._query()
        items = list(self._entries.values())
        if q:
            items = [e for e in items if q in e.name.lower() or q in e.original.lower()]
        keyf: dict[str, Callable[[TrashEntry], Any]] = {
            "name": lambda e: e.name.lower(),
            "origin": lambda e: self._rel_origin(e).lower(),
            "size": lambda e: e.size if e.size is not None else -1,
            "date": lambda e: e.deleted_at,
        }
        items.sort(key=keyf[self._sort_key], reverse=self._sort_rev)
        return items

    def _row(self, e: TrashEntry) -> tuple[str, str, str, str]:
        icon = "📁" if e.is_dir else "📄"
        return (f"{icon} {e.name}", self._rel_origin(e), human_size(e.size), _ago(e.deleted_at))

    def _render(self) -> None:
        keep = set(self._tree.selection())
        self._tree.delete(*self._tree.get_children())
        items = self._visible()
        for e in items:
            self._tree.insert("", "end", iid=e.id, values=self._row(e))
        for iid in keep:
            if self._tree.exists(iid):
                self._tree.selection_add(iid)
        if self._entries:
            self._empty_label.place_forget()
        else:
            self._empty_label.place(relx=0.5, rely=0.5, anchor="center")
        self._update_status(len(items))
        self._update_heading_arrows()
        t = self._tmgr.theme
        self._empty_btn.config(fg=getattr(t, "error_fg", "#ff5555") if self._entries else t.fg_muted)

    def _update_status(self, shown: int) -> None:
        if self._busy:
            return
        total = len(self._entries)
        known = [e.size for e in self._entries.values() if e.size is not None]
        size = human_size(sum(known)) + ("+" if len(known) < total else "")
        if shown != total:
            txt = _t("trash.status_filtered", shown=shown, n=total, size=size, default="{shown} de {n} · {size}")
        else:
            txt = _t("trash.status", n=total, size=size, default="{n} item(ns) · {size}")
        self._status.config(text=txt)

    def _update_heading_arrows(self) -> None:
        for c in _COLS:
            arrow = (" ▼" if self._sort_rev else " ▲") if c == self._sort_key else ""
            self._tree.heading(c, text=_t(f"trash.col_{c}", c) + arrow)

    def _sort_by(self, key: str) -> None:
        if key == self._sort_key:
            self._sort_rev = not self._sort_rev
        else:
            self._sort_key, self._sort_rev = key, key in ("size", "date")
        self._render()

    def _post(self, fn: Callable[[], None]) -> None:
        """Agenda *fn* na thread da UI (seguro a partir de qualquer thread)."""
        self._ui_q.put(fn)

    def _ensure_pump(self) -> None:
        if self._pump_job is None:
            self._pump_job = self.after(80, self._pump)

    def _pump(self) -> None:
        self._pump_job = None
        try:
            while True:
                self._ui_q.get_nowait()()
        except queue.Empty:
            pass
        except tk.TclError:
            return
        if self._busy or (self._sizing is not None and self._sizing.is_alive()) or not self._ui_q.empty():
            self._ensure_pump()

    def _start_sizing(self) -> None:
        """Mede em background o tamanho das pastas ainda sem tamanho conhecido."""
        todo = [e for e in self._entries.values() if e.size is None]
        if not todo or (self._sizing is not None and self._sizing.is_alive()):
            return

        def work() -> None:
            for e in todo:
                if self._cancel.is_set():
                    return
                size = tree_size(e.path, cancel=self._cancel)
                if size is None:
                    return
                with contextlib.suppress(OSError):
                    TrashStore(e.store).set_size(e.id, size)  # type: ignore[arg-type]
                self._post(lambda e=e, s=size: self._set_size(e.id, s))

        self._sizing = threading.Thread(target=work, daemon=True, name="codis-trash-size")
        self._sizing.start()
        self._ensure_pump()

    def _set_size(self, eid: str, size: int) -> None:
        e = self._entries.get(eid)
        if e is None:
            return
        e.size = size
        if self._tree.exists(eid):
            self._tree.item(eid, values=self._row(e))
        self._update_status(len(self._tree.get_children()))

    def _run_bg(self, work: Callable[[Callable[[int, int], None]], Any], done: Callable[[Any], None]) -> None:
        if self._busy:
            return
        self._busy = True
        self._cancel.clear()
        self._set_buttons("disabled")

        def progress(i: int, n: int) -> None:
            self._post(lambda: self._status.config(
                text=_t("trash.working", done=i, total=n, default="Excluindo… {done}/{total}")))

        def runner() -> None:
            try:
                result = work(progress)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Operação da lixeira falhou: %s", exc)
                result = exc

            def finish() -> None:
                self._busy = False
                self._set_buttons("normal")
                done(result)

            self._post(finish)

        threading.Thread(target=runner, daemon=True, name="codis-trash-op").start()
        self._ensure_pump()

    def _set_buttons(self, state: str) -> None:
        for b in (self._restore_btn, self._del_btn):
            with contextlib.suppress(tk.TclError):
                b.config(state=state)

    def _on_destroy(self, e: Any) -> None:
        if e.widget is self:
            self._cancel.set()

    def _selected(self) -> list[TrashEntry]:
        return [self._entries[i] for i in self._tree.selection() if i in self._entries]

    def _select_all(self) -> None:
        self._tree.selection_set(self._tree.get_children())

    def _changed(self) -> None:
        self.event_generate("<<TrashChanged>>", when="tail")
        with contextlib.suppress(tk.TclError):
            self.winfo_toplevel().event_generate("<<TrashChanged>>", when="tail")

    def _on_restore(self, to_dir: Path | None = None) -> None:
        items = self._selected()
        if not items:
            return
        ok = failed = 0
        for e in items:
            target = (to_dir / e.name) if to_dir else None
            if TrashManager.restore(e, to=target):
                ok += 1
            else:
                failed += 1
        if failed:
            messagebox.showwarning(_t("trash.title", "Lixeira"),
                                   _t("trash.restore_partial", ok=ok, failed=failed,
                                      default="{ok} restaurado(s), {failed} falhou(aram)."), parent=self)
        self.refresh()
        self._changed()

    def _on_restore_to(self) -> None:
        if not self._selected():
            return
        d = filedialog.askdirectory(parent=self, title=_t("trash.restore_to", "Restaurar em…"))
        if d:
            self._on_restore(Path(d))

    def _on_delete_perm(self) -> None:
        items = self._selected()
        if not items:
            return
        msg = _t("trash.confirm_perm", n=len(items), default="Excluir permanentemente {n} item(ns)?")
        if not messagebox.askyesno(_t("trash.title", "Lixeira"), msg, icon="warning", parent=self):
            return

        def work(progress: Callable[[int, int], None]) -> int:
            n = 0
            for i, e in enumerate(items, 1):
                if TrashManager.delete_permanent(e, cancel=self._cancel):
                    n += 1
                progress(i, len(items))
            return n

        self._run_bg(work, lambda _r: (self.refresh(), self._changed()))

    def _on_empty(self) -> None:
        total = len(self._entries)
        if total == 0:
            return
        known = [e.size for e in self._entries.values() if e.size is not None]
        size = human_size(sum(known)) + ("+" if len(known) < total else "")
        msg = _t("trash.confirm_empty", n=total, size=size,
                 default="Esvaziar a lixeira? {n} item(ns) ({size}) serão excluídos permanentemente.")
        if not messagebox.askyesno(_t("trash.title", "Lixeira"), msg, icon="warning", parent=self):
            return
        self._run_bg(lambda progress: TrashManager.empty_trash(cancel=self._cancel, progress=progress),
                     lambda r: (logger.info("Lixeira esvaziada: %s", r), self.refresh(), self._changed()))

    def _open_origin(self, e: TrashEntry) -> None:
        parent = Path(e.original).parent
        reveal_in_file_manager(parent if parent.exists() else e.path)

    def _copy_origin(self, e: TrashEntry) -> None:
        self.clipboard_clear()
        self.clipboard_append(e.original)

    def _ctx_menu(self, ev: tk.Event) -> None:
        item = self._tree.identify_row(ev.y)
        if item and item not in self._tree.selection():
            self._tree.selection_set(item)
        sel = self._selected()
        t = self._tmgr.theme
        menu = tk.Menu(self, tearoff=False, bg=t.floating_bg, fg=t.fg, activebackground=t.sel_bg,
                       activeforeground=t.sel_fg, relief="flat")
        if sel:
            menu.add_command(label=_t("trash.restore", "↩ Restaurar"), command=self._on_restore)
            menu.add_command(label=_t("trash.restore_to", "Restaurar em…"), command=self._on_restore_to)
            menu.add_command(label=_t("trash.delete_perm", "Excluir permanentemente"),
                             command=self._on_delete_perm)
            menu.add_separator()
            if len(sel) == 1:
                menu.add_command(label=_t("trash.open_folder", "Abrir pasta de origem"),
                                 command=lambda: self._open_origin(sel[0]))
                menu.add_command(label=_t("trash.copy_path", "Copiar caminho original"),
                                 command=lambda: self._copy_origin(sel[0]))
                menu.add_separator()
        menu.add_command(label=_t("trash.select_all", "Selecionar tudo"), command=self._select_all)
        try:
            menu.tk_popup(ev.x_root, ev.y_root)
        finally:
            menu.grab_release()

    def apply_theme(self, t: Any) -> None:
        """Reaplica o tema ao painel (reconstrói os widgets)."""
        self._filter.trace_remove("write", self._filter_trace)
        for w in self.winfo_children():
            w.destroy()
        self.config(bg=t.sidebar_bg)
        self._build()
        self._filter_trace = self._filter.trace_add("write", lambda *_: self._render())
        self.refresh()
