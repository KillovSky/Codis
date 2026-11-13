"""Codis - Navegador de Arquivos Remotos (SSH/SFTP, FTP/FTPS, HTTP, WebDAV).

Interface do painel. Toda a lógica de protocolo vive em
:mod:`src.ui.remote_adapters` (testável sem Tkinter).

Destaques
---------
* navegação com histórico (Alt+←/→), breadcrumbs clicáveis e caminho editável;
* ordenação por coluna, filtro instantâneo, seleção múltipla;
* menu de contexto completo (arquivo, pasta e área vazia): abrir, baixar,
  enviar, nova pasta, renomear, excluir, copiar caminho;
* download/upload recursivo de pastas com barra de progresso;
* arquivos abertos mantêm o nome original e são reenviados ao salvar
  (qualquer forma de salvar: manual, "salvar tudo" ou autosave), com detecção
  de conflito caso o arquivo tenha mudado no servidor;
* erros aparecem no próprio painel (sem pop-ups em cascata) e nunca são
  confundidos com "pasta vazia".
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import queue
import shutil
import subprocess
import tempfile
import threading
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any

from src.constants import FONT_MONO, FONT_UI
from src.editor import save_hooks
from src.i18n import _t
from src.ui.remote_adapters import (
    RemoteAdapter,
    RemoteEntry,
    RemoteProfile,
    SSHAdapter,
    classify,
    file_icon,
    format_size,
    join_path,
    load_profiles,
    looks_binary,
    make_adapter,
    parent_path,
    save_profiles,
)
from src.ui.remote_profile_dialog import ProfileDialog
from src.ui.ui_scale import fit_window, px

logger = logging.getLogger("Codis.RemoteBrowser")

_UP_IID = "__up__"
MAX_OPEN_BYTES = 20 * 1024 * 1024
MAX_WALK_FILES = 5000
MAX_WALK_DEPTH = 12


class _Opened:
    """Arquivo remoto aberto localmente: de onde veio e como estava."""

    def __init__(self, profile: RemoteProfile, remote: str, entry: RemoteEntry | None) -> None:
        self.profile = profile
        self.remote = remote
        self.baseline = (entry.size, entry.modified) if entry else None


class RemoteBrowserPanel(tk.Frame):
    """Painel de exploração de arquivos remotos.

    Args:
        parent: widget pai.
        tmgr: gerenciador de temas (``tmgr.theme``).
        cfg: configuração (``remote_sync_on_save``).
        open_file_cb: chamado para abrir um arquivo local no editor.
    """

    def __init__(
        self,
        parent: tk.Widget,
        tmgr: Any,
        cfg: Any,
        open_file_cb: Callable[[Path], None] | None = None,
    ) -> None:
        t = tmgr.theme
        super().__init__(parent, bg=t.sidebar_bg)
        self._tmgr, self._cfg, self._t = tmgr, cfg, t
        self._open_file_cb = open_file_cb

        self._profiles: list[RemoteProfile] = load_profiles()
        self._adapter: RemoteAdapter | None = None
        self._profile: RemoteProfile | None = None
        self._session = 0
        self._nav_token = 0
        self._path = "/"
        self._history: list[str] = []
        self._hist_idx = -1
        self._entries: list[RemoteEntry] = []
        self._iid_entry: dict[str, RemoteEntry] = {}
        self._sort_col, self._sort_rev = "name", False
        self._busy = 0
        self._opened: dict[str, _Opened] = {}
        self._tunnel_proc: subprocess.Popen | None = None
        self._workdir = Path(tempfile.mkdtemp(prefix="codis_remote_"))
        self._filter_var = tk.StringVar()
        self._path_var = tk.StringVar()
        self._side_visible = True
        self._autocollapsed = False
        self._last_progress = 0.0
        self._uiq: queue.SimpleQueue[tuple[Callable[..., None], tuple[Any, ...]]] = queue.SimpleQueue()
        self._poll_job: str | None = None

        self._build_ui()
        self._refresh_profiles()
        self._show_state(_t("rbrowser.empty_hint", "Selecione uma conexão (duplo clique) ou crie uma nova."))
        self._unregister_hook = save_hooks.register(self._on_local_saved)
        self.bind("<Destroy>", self._on_destroy, add="+")
        self._poll_job = self.after(25, self._drain_ui_queue)

    def _btn(self, parent: tk.Misc, text: str, cmd: Callable[[], None], *, accent: bool = False, tip: str = "") -> tk.Button:
        t = self._t
        b = tk.Button(
            parent, text=text, command=cmd, relief="flat", cursor="hand2", font=(FONT_UI, 9),
            bg=t.btn_primary if accent else t.btn_bg, fg=t.btn_primary_fg if accent else t.btn_fg,
            activebackground=t.btn_hover, activeforeground=t.btn_fg, bd=0, padx=px(8), pady=px(3),
        )  # fmt: skip
        b._tip = tip  # type: ignore[attr-defined]
        return b

    def _setup_styles(self) -> None:
        """Estilo próprio dos Treeviews: bordas discretas em vez do contorno claro."""
        t = self._t
        style = ttk.Style(self)
        style.configure(
            "Remote.Treeview", bordercolor=t.border, lightcolor=t.panel_bg, darkcolor=t.panel_bg,
            borderwidth=0, relief="flat",
        )  # fmt: skip
        style.configure("Remote.Treeview.Heading", relief="flat", borderwidth=0)

    def _build_ui(self) -> None:
        t = self._t
        self._setup_styles()
        hdr = tk.Frame(self, bg=t.activity_bg)
        hdr.pack(fill="x")
        self._side_btn = self._btn(hdr, "☰", self._toggle_side)
        self._side_btn.pack(side="left", padx=(px(8), 0), pady=px(8))
        tk.Label(hdr, text="🗂", bg=t.activity_bg, fg=t.accent, font=(FONT_UI, 15)).pack(side="left", padx=(px(8), px(4)))
        tk.Label(
            hdr, text=_t("rbrowser.title", "Arquivos Remotos"), bg=t.activity_bg, fg=t.fg,
            font=(FONT_UI, 12, "bold"),
        ).pack(side="left", pady=px(8))  # fmt: skip
        self._badge = tk.Label(hdr, text="", bg=t.activity_bg, fg=t.fg_muted, font=(FONT_UI, 9))
        self._badge.pack(side="right", padx=px(12))
        self._set_badge(None)
        tk.Frame(self, bg=t.border, height=1).pack(fill="x")

        self._pane = tk.PanedWindow(
            self, orient="horizontal", bg=t.border, sashwidth=px(4), sashrelief="flat", bd=0, opaqueresize=True,
        )  # fmt: skip
        self._pane.pack(fill="both", expand=True)
        self._side = self._build_side(self._pane)
        self._main = self._build_main(self._pane)
        self._pane.add(self._side, minsize=px(150), width=px(220), stretch="never")
        self._pane.add(self._main, minsize=px(260), stretch="always")
        self.bind("<Configure>", self._on_configure, add="+")

        self._status_bar = tk.Frame(self, bg=t.statusbar_bg)
        self._status_bar.pack(fill="x", side="bottom")
        self._status = tk.Label(
            self._status_bar, text="", bg=t.statusbar_bg, fg=t.statusbar_fg, font=(FONT_UI, 9), anchor="w",
        )  # fmt: skip
        self._status.pack(side="left", fill="x", expand=True, padx=px(10), pady=px(3))
        self._progress = ttk.Progressbar(self._status_bar, mode="indeterminate", length=px(120))

    def _build_side(self, parent: tk.Misc) -> tk.Frame:
        t = self._t
        side = tk.Frame(parent, bg=t.panel_bg)
        bar = tk.Frame(side, bg=t.panel_bg)
        bar.pack(fill="x", padx=px(6), pady=(px(8), px(4)))
        tk.Label(
            bar, text=_t("rbrowser.connections", "CONEXÕES"), bg=t.panel_bg, fg=t.fg_muted, font=(FONT_UI, 8, "bold"),
        ).pack(side="left")  # fmt: skip
        self._btn(bar, "＋", self._on_new, accent=True).pack(side="right")
        self._btn(bar, "✎", self._on_edit).pack(side="right", padx=px(3))
        self._btn(bar, "🗑", self._on_delete).pack(side="right")

        wrap = tk.Frame(side, bg=t.panel_bg)
        wrap.pack(fill="both", expand=True, padx=px(4), pady=(0, px(6)))
        self._plist = ttk.Treeview(wrap, show="tree", selectmode="browse", columns=("proto",), style="Remote.Treeview")
        self._plist.column("#0", width=px(130), minwidth=px(60), stretch=True)
        self._plist.column("proto", width=px(52), minwidth=px(40), anchor="e", stretch=False)
        self._plist.tag_configure("connected", foreground=t.ok_fg)
        self._plist.bind("<<TreeviewSelect>>", self._on_profile_select)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self._plist.yview)
        self._plist.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._plist.pack(side="left", fill="both", expand=True)
        self._plist.bind("<Double-1>", lambda _e: self._on_connect())
        self._plist.bind("<Return>", lambda _e: self._on_connect())
        self._plist.bind("<Button-3>", self._profile_menu)
        self._plist.bind("<Delete>", lambda _e: self._on_delete())
        return side

    def _build_main(self, parent: tk.Misc) -> tk.Frame:
        t = self._t
        main = tk.Frame(parent, bg=t.panel_bg)

        nav = tk.Frame(main, bg=t.panel_bg)
        nav.pack(fill="x", padx=px(6), pady=(px(8), px(4)))
        self._back_btn = self._btn(nav, "←", self._go_back)
        self._fwd_btn = self._btn(nav, "→", self._go_forward)
        self._up_btn = self._btn(nav, "↑", self._go_up)
        self._home_btn = self._btn(nav, "⌂", self._go_home)
        self._refresh_btn = self._btn(nav, "⟳", self._refresh)
        for b in (self._back_btn, self._fwd_btn, self._up_btn, self._home_btn, self._refresh_btn):
            b.pack(side="left", padx=(0, px(3)))
        self._path_box = tk.Frame(nav, bg=t.input_bg, highlightthickness=1, highlightbackground=t.border)
        self._path_box.pack(side="left", fill="x", expand=True, padx=(px(4), 0))
        self._crumbs = tk.Frame(self._path_box, bg=t.input_bg)
        self._crumbs.pack(fill="x", expand=True)
        self._crumbs.bind("<Button-1>", lambda _e: self._edit_path())
        self._path_entry = tk.Entry(
            self._path_box, textvariable=self._path_var, bg=t.input_bg, fg=t.fg, insertbackground=t.fg,
            relief="flat", font=(FONT_MONO, 10),
        )  # fmt: skip
        self._path_entry.bind("<Return>", self._commit_path)
        self._path_entry.bind("<Escape>", lambda _e: self._end_edit_path())
        self._path_entry.bind("<FocusOut>", lambda _e: self._end_edit_path())

        flt = tk.Frame(main, bg=t.panel_bg)
        flt.pack(fill="x", padx=px(6), pady=(0, px(4)))
        tk.Label(flt, text="🔍", bg=t.panel_bg, fg=t.fg_muted, font=(FONT_UI, 9)).pack(side="left")
        self._filter_entry = tk.Entry(
            flt, textvariable=self._filter_var, bg=t.input_bg, fg=t.fg, insertbackground=t.fg, relief="flat",
            font=(FONT_UI, 10), highlightthickness=1, highlightbackground=t.border, highlightcolor=t.accent,
        )  # fmt: skip
        self._filter_entry.pack(side="left", fill="x", expand=True, padx=px(4), ipady=px(2))
        self._filter_var.trace_add("write", lambda *_a: self._render())
        self._filter_entry.bind("<Escape>", lambda _e: self._filter_var.set(""))
        self._count = tk.Label(flt, text="", bg=t.panel_bg, fg=t.fg_muted, font=(FONT_UI, 9))
        self._count.pack(side="right", padx=(px(6), 0))

        box = tk.Frame(main, bg=t.panel_bg)
        box.pack(fill="both", expand=True, padx=px(6), pady=(0, px(6)))
        cols = ("size", "modified")
        self._tree = ttk.Treeview(box, columns=cols, selectmode="extended", style="Remote.Treeview")
        heads = {"#0": ("name", _t("rbrowser.col_name", "Nome")), "size": ("size", _t("rbrowser.col_size", "Tamanho")),
                 "modified": ("modified", _t("rbrowser.col_modified", "Modificado"))}  # fmt: skip
        for col, (key, text) in heads.items():
            self._tree.heading(col, text=text, command=lambda k=key: self._sort_by(k))
        self._tree.column("#0", width=px(260), minwidth=px(120), stretch=True)
        self._tree.column("size", width=px(80), minwidth=px(60), anchor="e", stretch=False)
        self._tree.column("modified", width=px(140), minwidth=px(90), stretch=False)
        self._tree.tag_configure("dir", foreground=t.accent)
        self._tree.tag_configure("link", foreground=t.fg_muted)
        self._tree.tag_configure("broken", foreground=t.err_fg)
        vsb = ttk.Scrollbar(box, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self._tree.pack(side="left", fill="both", expand=True)
        self._box = box

        self._state = tk.Frame(box, bg=t.panel_bg)
        self._state_lbl = tk.Label(
            self._state, text="", bg=t.panel_bg, fg=t.fg_muted, font=(FONT_UI, 10), wraplength=px(360), justify="center",
        )  # fmt: skip
        self._state_lbl.pack(expand=True, pady=(px(40), px(6)))
        self._state_btn = self._btn(self._state, _t("rbrowser.retry", "Tentar novamente"), self._refresh, accent=True)

        self._tree.bind("<Configure>", self._adapt_columns, add="+")
        self._tree.bind("<Double-1>", self._on_double)
        self._tree.bind("<Return>", lambda _e: self._open_selected())
        self._tree.bind("<BackSpace>", lambda _e: self._go_up())
        self._tree.bind("<Delete>", lambda _e: self._delete_selected())
        self._tree.bind("<F2>", lambda _e: self._rename_selected())
        self._tree.bind("<Button-3>", self._file_menu)
        self._tree.bind("<Button-2>", self._file_menu)
        for seq, fn in (
            ("<F5>", self._refresh),
            ("<Control-l>", self._edit_path),
            ("<Control-f>", lambda: self._filter_entry.focus_set()),
            ("<Alt-Left>", self._go_back),
            ("<Alt-Right>", self._go_forward),
            ("<Control-n>", self._new_folder),
        ):
            self.bind_all_children(seq, fn)
        self._update_nav()
        return main

    def bind_all_children(self, seq: str, fn: Callable[[], None]) -> None:
        """Atalhos válidos quando o foco está em qualquer widget do painel."""
        for w in (self._tree, self._filter_entry, self._path_entry, self._plist):
            w.bind(seq, lambda _e, f=fn: (f(), "break")[1], add="+")

    def _ui(self, fn: Callable[..., None], *args: Any) -> None:
        """Agenda ``fn`` na thread da UI. Seguro a partir de qualquer thread.

        Usa uma fila drenada por ``after`` na thread principal em vez de chamar
        ``after`` da thread de trabalho (o que falha com ``RuntimeError`` se o
        ``mainloop`` não estiver ativo e depende de marshaling do Tcl).
        """
        self._uiq.put((fn, args))

    def _drain_ui_queue(self) -> None:
        self._poll_job = None
        while True:
            try:
                fn, args = self._uiq.get_nowait()
            except queue.Empty:
                break
            try:
                fn(*args)
            except tk.TclError:
                if not self.winfo_exists():
                    return
                logger.exception("Callback de UI falhou")
            except Exception:  # noqa: BLE001
                logger.exception("Callback de UI falhou")
        with contextlib.suppress(tk.TclError):
            self._poll_job = self.after(25, self._drain_ui_queue)

    def _set_status(self, text: str, *, error: bool = False) -> None:
        t = self._t
        with contextlib.suppress(tk.TclError):
            self._status.configure(text=text, fg=t.err_fg if error else t.statusbar_fg)

    def _set_badge(self, profile: RemoteProfile | None, *, error: bool = False) -> None:
        t = self._t
        if profile is None:
            text, color = "○ " + _t("rbrowser.disconnected", "Desconectado"), t.fg_muted
        elif error:
            text, color = "● " + _t("rbrowser.conn_error", "Erro de conexão"), t.err_fg
        else:
            text, color = f"● {profile.name}", t.ok_fg
        with contextlib.suppress(tk.TclError):
            self._badge.configure(text=text, fg=color)

    def _begin(self, msg: str) -> None:
        self._busy += 1
        self._set_status(msg)
        if self._busy == 1:
            self._progress.configure(mode="indeterminate", value=0)
            self._progress.pack(side="right", padx=px(8))
            self._progress.start(12)
            with contextlib.suppress(tk.TclError):
                self.configure(cursor="watch")

    def _end(self) -> None:
        self._busy = max(0, self._busy - 1)
        if self._busy == 0:
            with contextlib.suppress(tk.TclError):
                self._progress.stop()
                self._progress.pack_forget()
                self.configure(cursor="")

    def _report_progress(self, done: int, total: int | None) -> None:
        """Atualiza a barra (limitado a ~10x/s); chamado de threads."""
        import time

        now = time.monotonic()
        if now - self._last_progress < 0.1 and (total is None or done < total):
            return
        self._last_progress = now

        def _apply() -> None:
            with contextlib.suppress(tk.TclError):
                if total:
                    self._progress.stop()
                    self._progress.configure(mode="determinate", maximum=total, value=done)
                self._status.configure(text=f"{format_size(done)}" + (f" / {format_size(total)}" if total else ""))

        self._ui(_apply)

    def _show_state(self, text: str, *, retry: bool = False, error: bool = False) -> None:
        t = self._t
        self._state_lbl.configure(text=text, fg=t.err_fg if error else t.fg_muted)
        if retry:
            self._state_btn.pack(pady=(0, px(40)))
        else:
            self._state_btn.pack_forget()
        self._state.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._state.lift()

    def _hide_state(self) -> None:
        self._state.place_forget()

    _PROTO_BADGE = {"webdav": "DAV", "webdavs": "DAVS", "sftp": "SFTP"}

    def _adapt_columns(self, event: tk.Event) -> None:
        """Oculta colunas secundárias quando a lista fica estreita (evita corte mudo)."""
        if event.width >= px(520):
            wanted: tuple[str, ...] = ("size", "modified")
        elif event.width >= px(330):
            wanted = ("size",)
        else:
            wanted = ()
        if tuple(self._tree.cget("displaycolumns")) != wanted:
            self._tree.configure(displaycolumns=wanted)

    def _toggle_side(self) -> None:
        if self._side_visible:
            self._pane.forget(self._side)
        else:
            self._pane.add(self._side, before=self._main, minsize=px(150), width=px(220), stretch="never")
        self._side_visible = not self._side_visible

    def _on_configure(self, event: tk.Event) -> None:
        if event.widget is not self or self._autocollapsed:
            return
        self._autocollapsed = True
        if event.width < px(620) and self._side_visible:
            self._toggle_side()

    def _refresh_profiles(self) -> None:
        self._plist.delete(*self._plist.get_children())
        for p in self._profiles:
            connected = self._profile is not None and p.uid == self._profile.uid and self._adapter is not None
            label = f"{'● ' if connected else '  '}{p.name}"
            self._plist.insert(
                "", "end", iid=p.uid, text=label, values=(self._PROTO_BADGE.get(p.protocol, p.protocol.upper()),),
                tags=("connected",) if connected else (),
            )

    def _on_profile_select(self, _e: object = None) -> None:
        p = self._selected_profile()
        if p is not None and self._busy == 0:
            user = f"{p.user}@" if p.user else ""
            self._set_status(f"{p.protocol}://{user}{p.host}:{p.port}")
        self._update_nav()

    def _selected_profile(self) -> RemoteProfile | None:
        sel = self._plist.selection()
        return next((p for p in self._profiles if sel and p.uid == sel[0]), None)

    def _on_new(self) -> None:
        dlg = ProfileDialog(self, self._tmgr)
        self.wait_window(dlg)
        if dlg.result:
            self._profiles.append(dlg.result)
            self._persist_profiles()
            self._refresh_profiles()
            self._plist.selection_set(dlg.result.uid)

    def _on_edit(self) -> None:
        old = self._selected_profile()
        if old is None:
            return
        dlg = ProfileDialog(self, self._tmgr, old)
        self.wait_window(dlg)
        if dlg.result:
            self._profiles[self._profiles.index(old)] = dlg.result
            self._persist_profiles()
            if self._profile is not None and self._profile.uid == old.uid:
                self._profile = dlg.result
            self._refresh_profiles()
            self._plist.selection_set(dlg.result.uid)

    def _on_delete(self) -> None:
        p = self._selected_profile()
        if p is None:
            return
        if not messagebox.askyesno(
            _t("rbrowser.delete_title", "Remover conexão"),
            _t("rbrowser.delete_confirm", "Remover a conexão '{name}'?", name=p.name),
            parent=self,
        ):
            return
        if self._profile is not None and self._profile.uid == p.uid:
            self._disconnect()
        self._profiles.remove(p)
        self._persist_profiles()
        self._refresh_profiles()

    def _persist_profiles(self) -> None:
        try:
            save_profiles(self._profiles)
        except OSError as exc:
            logger.warning("Falha ao salvar perfis: %s", exc)
            self._set_status(str(exc), error=True)

    def _profile_menu(self, event: tk.Event) -> None:
        iid = self._plist.identify_row(event.y)
        if iid:
            self._plist.selection_set(iid)
        p = self._selected_profile()
        m = self._menu()
        m.add_command(label=_t("rbrowser.btn_new", "Nova conexão…"), command=self._on_new)
        if p:
            connected = self._adapter is not None and self._profile is not None and self._profile.uid == p.uid
            m.add_separator()
            if connected:
                m.add_command(label=_t("rbrowser.disconnect", "Desconectar"), command=self._disconnect)
            else:
                m.add_command(label=_t("rbrowser.btn_connect", "Conectar"), command=self._on_connect)
            m.add_command(label=_t("rbrowser.btn_edit", "Editar…"), command=self._on_edit)
            m.add_command(label=_t("rbrowser.btn_dup", "Duplicar"), command=self._duplicate_profile)
            m.add_command(label=_t("rbrowser.btn_delete", "Remover"), command=self._on_delete)
            if p.protocol in ("ssh", "sftp") and connected:
                m.add_separator()
                m.add_command(label=_t("rbrowser.tunnel", "Túnel de debug (5678)"), command=self.open_debug_tunnel)
        m.tk_popup(event.x_root, event.y_root)

    def _duplicate_profile(self) -> None:
        from dataclasses import replace
        from uuid import uuid4

        p = self._selected_profile()
        if p is None:
            return
        clone = replace(p, name=f"{p.name} (2)", uid=uuid4().hex[:10])
        self._profiles.append(clone)
        self._persist_profiles()
        self._refresh_profiles()

    def _menu(self) -> tk.Menu:
        t = self._t
        return tk.Menu(
            self, tearoff=0, bg=t.floating_bg, fg=t.fg, activebackground=t.sel_bg, activeforeground=t.sel_fg,
            bd=0, relief="flat", font=(FONT_UI, 9),
        )  # fmt: skip

    def _on_connect(self) -> None:
        p = self._selected_profile()
        if p is None:
            return
        self._disconnect(silent=True)
        self._session += 1
        session = self._session
        adapter = make_adapter(p)
        self._begin(_t("rbrowser.connecting", "Conectando a {name}…", name=p.name))
        self._show_state(_t("rbrowser.connecting", "Conectando a {name}…", name=p.name))

        def _work() -> None:
            ok, msg = adapter.connect()
            self._ui(self._connect_done, session, adapter, p, ok, msg)

        threading.Thread(target=_work, daemon=True).start()

    def _connect_done(self, session: int, adapter: RemoteAdapter, p: RemoteProfile, ok: bool, msg: str) -> None:
        self._end()
        if session != self._session:
            threading.Thread(target=adapter.close, daemon=True).start()
            return
        if not ok:
            threading.Thread(target=adapter.close, daemon=True).start()
            self._set_badge(p, error=True)
            self._set_status(msg, error=True)
            self._show_state(f"✗ {msg}", retry=True, error=True)
            return
        self._adapter, self._profile = adapter, p
        self._set_badge(p)
        self._refresh_profiles()
        self._plist.selection_set(p.uid)
        self._history, self._hist_idx = [], -1
        self._set_status(_t("rbrowser.connected_to", "Conectado a {name}", name=p.name))
        self._navigate(adapter.home)

    def _disconnect(self, *, silent: bool = False) -> None:
        self._session += 1
        self._nav_token += 1
        adapter, self._adapter = self._adapter, None
        self._profile = None
        if adapter is not None:
            threading.Thread(target=adapter.close, daemon=True).start()
        self._stop_tunnel()
        self._entries, self._iid_entry = [], {}
        with contextlib.suppress(tk.TclError):
            self._tree.delete(*self._tree.get_children())
        self._set_badge(None)
        self._count.configure(text="")
        self._render_crumbs("")
        self._update_nav()
        self._refresh_profiles()
        if not silent:
            self._show_state(_t("rbrowser.empty_hint", "Selecione uma conexão (duplo clique) ou crie uma nova."))
            self._set_status(_t("rbrowser.disconnected", "Desconectado"))

    def _navigate(self, path: str, *, push: bool = True) -> None:
        adapter = self._adapter
        if adapter is None:
            return
        self._nav_token += 1
        token, session = self._nav_token, self._session
        self._begin(_t("rbrowser.loading", "Carregando {path}…", path=path))

        def _work() -> None:
            entries, err = adapter.listdir(path)
            self._ui(self._navigate_done, session, token, path, push, entries, err)

        threading.Thread(target=_work, daemon=True).start()

    def _navigate_done(
        self, session: int, token: int, path: str, push: bool, entries: list[RemoteEntry], err: str
    ) -> None:
        self._end()
        if session != self._session or token != self._nav_token:
            return
        if err:
            self._set_status(err, error=True)
            if not self._entries and not self._iid_entry:
                self._show_state(f"✗ {err}", retry=True, error=True)
            else:
                messagebox.showwarning(_t("rbrowser.list_fail", "Não foi possível abrir a pasta"), f"{path}\n\n{err}", parent=self)
            self._render_crumbs(self._path)
            return
        self._path = path
        if push:
            self._history = self._history[: self._hist_idx + 1] + [path]
            self._hist_idx = len(self._history) - 1
        self._entries = entries
        self._filter_var.set("") if self._filter_var.get() else None
        self._render_crumbs(path)
        self._render()
        self._update_nav()
        self._set_status(_t("rbrowser.items", "{n} itens", n=len(entries)))

    def _refresh(self) -> None:
        if self._adapter is None:
            if self._selected_profile():
                self._on_connect()
            return
        self._navigate(self._path, push=False)

    def _go_up(self) -> None:
        if self._adapter and self._path not in ("/", ""):
            self._navigate(parent_path(self._path))

    def _go_home(self) -> None:
        if self._adapter:
            self._navigate(self._adapter.home)

    def _go_back(self) -> None:
        if self._hist_idx > 0:
            self._hist_idx -= 1
            self._navigate(self._history[self._hist_idx], push=False)

    def _go_forward(self) -> None:
        if self._hist_idx < len(self._history) - 1:
            self._hist_idx += 1
            self._navigate(self._history[self._hist_idx], push=False)

    def _update_nav(self) -> None:
        on = self._adapter is not None
        states = {
            self._back_btn: on and self._hist_idx > 0,
            self._fwd_btn: on and self._hist_idx < len(self._history) - 1,
            self._up_btn: on and self._path not in ("/", ""),
            self._home_btn: on,
            self._refresh_btn: on or self._selected_profile() is not None,
        }
        for btn, enabled in states.items():
            btn.configure(state="normal" if enabled else "disabled")

    def _render_crumbs(self, path: str) -> None:
        t = self._t
        for w in self._crumbs.winfo_children():
            w.destroy()
        if not path:
            return
        parts = [p for p in path.split("/") if p]
        targets = ["/"] + ["/" + "/".join(parts[: i + 1]) for i in range(len(parts))]
        labels = ["/"] + parts
        if len(labels) > 5:
            labels, targets = ["…"] + labels[-4:], [targets[-5]] + targets[-4:]
        for i, (lab, tgt) in enumerate(zip(labels, targets, strict=True)):
            if i:
                tk.Label(self._crumbs, text="›", bg=t.input_bg, fg=t.fg_muted, font=(FONT_UI, 9)).pack(side="left")
            last = i == len(labels) - 1
            b = tk.Button(
                self._crumbs, text=lab, relief="flat", bd=0, cursor="hand2", bg=t.input_bg,
                fg=t.fg if last else t.accent, activebackground=t.btn_hover, activeforeground=t.fg,
                font=(FONT_UI, 9, "bold" if last else "normal"), padx=px(4), pady=px(2),
                command=lambda p=tgt: self._navigate(p),
            )  # fmt: skip
            b.pack(side="left")
        tk.Label(self._crumbs, text="✎", bg=t.input_bg, fg=t.fg_muted, font=(FONT_UI, 9)).pack(side="right", padx=px(4))
        for w in self._crumbs.winfo_children():
            if isinstance(w, tk.Label):
                w.bind("<Button-1>", lambda _e: self._edit_path())

    def _edit_path(self) -> None:
        if self._adapter is None:
            return
        self._path_var.set(self._path)
        self._crumbs.pack_forget()
        self._path_entry.pack(fill="x", expand=True, padx=px(4), pady=px(3))
        self._path_entry.focus_set()
        self._path_entry.select_range(0, "end")

    def _end_edit_path(self) -> None:
        if self._path_entry.winfo_ismapped():
            self._path_entry.pack_forget()
            self._crumbs.pack(fill="x", expand=True)

    def _commit_path(self, _e: object = None) -> None:
        target = self._path_var.get().strip() or "/"
        self._end_edit_path()
        self._tree.focus_set()
        self._navigate(target)

    def _sort_by(self, key: str) -> None:
        self._sort_rev = not self._sort_rev if self._sort_col == key else False
        self._sort_col = key
        self._render()

    def _sorted(self, entries: list[RemoteEntry]) -> list[RemoteEntry]:
        keyfn: dict[str, Callable[[RemoteEntry], Any]] = {
            "name": lambda e: e.name.lower(),
            "size": lambda e: e.size or 0,
            "modified": lambda e: e.modified,
        }
        ordered = sorted(entries, key=keyfn[self._sort_col], reverse=self._sort_rev)
        return sorted(ordered, key=lambda e: not e.is_dir)

    def _render(self) -> None:
        if self._adapter is None:
            return
        needle = self._filter_var.get().strip().lower()
        visible = [e for e in self._entries if not needle or needle in e.name.lower()]
        self._tree.delete(*self._tree.get_children())
        self._iid_entry = {}
        if self._path not in ("/", ""):
            self._tree.insert("", "end", iid=_UP_IID, text="  ⬆  ..", tags=("dir",))
        for i, e in enumerate(self._sorted(visible)):
            iid = f"e{i}"
            self._iid_entry[iid] = e
            tags = ["dir"] if e.is_dir else []
            if e.is_link:
                tags.append("broken" if e.broken else "link")
            name = f"  {file_icon(e.name, e.is_dir)}  {e.name}" + (f"  → {e.link_target}" if e.link_target else "")
            self._tree.insert("", "end", iid=iid, text=name, values=(e.size_text, e.modified), tags=tags)
        arrow = " ▼" if self._sort_rev else " ▲"
        for col, key in (("#0", "name"), ("size", "size"), ("modified", "modified")):
            base = {"name": _t("rbrowser.col_name", "Nome"), "size": _t("rbrowser.col_size", "Tamanho"),
                    "modified": _t("rbrowser.col_modified", "Modificado")}[key]  # fmt: skip
            self._tree.heading(col, text=base + (arrow if key == self._sort_col else ""))
        n_dirs = sum(e.is_dir for e in visible)
        self._count.configure(text=_t("rbrowser.counts", "{d} pastas, {f} arquivos", d=n_dirs, f=len(visible) - n_dirs))
        if not visible:
            msg = (
                _t("rbrowser.no_match", "Nenhum item corresponde ao filtro.")
                if needle
                else _t("rbrowser.empty_dir", "Pasta vazia.")
            )
            self._show_state(msg)
        else:
            self._hide_state()

    def _selection(self) -> list[tuple[str, RemoteEntry]]:
        """Itens selecionados como (caminho remoto, entrada); ignora o '..'."""
        out = []
        for iid in self._tree.selection():
            e = self._iid_entry.get(iid)
            if e is not None:
                out.append((join_path(self._path, e.name), e))
        return out

    def _on_double(self, event: tk.Event) -> None:
        if self._tree.identify_row(event.y):
            self._open_selected()

    def _open_selected(self) -> None:
        sel = self._tree.selection()
        if sel and sel[0] == _UP_IID:
            self._go_up()
            return
        items = self._selection()
        if not items:
            return
        remote, entry = items[0]
        if entry.is_dir:
            self._navigate(remote)
        else:
            self._open_file(remote, entry)

    def _file_menu(self, event: tk.Event) -> None:
        if self._adapter is None:
            return
        iid = self._tree.identify_row(event.y)
        if iid and iid not in self._tree.selection():
            self._tree.selection_set(iid)
        elif not iid:
            self._tree.selection_remove(*self._tree.selection())
        items = self._selection()
        ro = self._adapter.read_only
        m = self._menu()
        if items:
            first = items[0][1]
            m.add_command(label=_t("rbrowser.ctx_open", "Abrir"), command=self._open_selected)
            m.add_command(label=_t("rbrowser.ctx_download", "Baixar…"), command=self._download_selected)
            if first.is_dir and not ro:
                m.add_command(label=_t("rbrowser.ctx_upload_into", "Enviar para esta pasta…"), command=lambda: self._upload_files(items[0][0]))
            m.add_separator()
            if not ro:
                if len(items) == 1:
                    m.add_command(label=_t("rbrowser.ctx_rename", "Renomear") + "\tF2", command=self._rename_selected)
                m.add_command(label=_t("rbrowser.ctx_delete", "Excluir") + "\tDel", command=self._delete_selected)
                m.add_separator()
            m.add_command(label=_t("rbrowser.ctx_copy", "Copiar caminho"), command=lambda: self._copy_paths([p for p, _ in items]))
            m.add_separator()
        if not ro:
            m.add_command(label=_t("rbrowser.ctx_upload", "Enviar arquivos aqui…"), command=self._upload_files)
            m.add_command(label=_t("rbrowser.ctx_mkdir", "Nova pasta") + "…\tCtrl+N", command=self._new_folder)
        m.add_command(label=_t("rbrowser.ctx_refresh", "Atualizar") + "\tF5", command=self._refresh)
        m.tk_popup(event.x_root, event.y_root)

    def _copy_paths(self, paths: list[str]) -> None:
        self.clipboard_clear()
        self.clipboard_append("\n".join(paths))
        self._set_status(_t("rbrowser.copied", "Caminho copiado."))

    def _local_for(self, profile: RemoteProfile, remote: str) -> Path:
        digest = hashlib.sha1(f"{profile.uid}:{remote}".encode(), usedforsecurity=False).hexdigest()[:8]
        folder = self._workdir / profile.uid / digest
        folder.mkdir(parents=True, exist_ok=True)
        return folder / (remote.rsplit("/", 1)[-1] or "arquivo")

    def _open_file(self, remote: str, entry: RemoteEntry) -> None:
        adapter, profile = self._adapter, self._profile
        if adapter is None or profile is None:
            return
        kind = classify(entry.name)
        if kind == "binary":
            if messagebox.askyesno(
                _t("rbrowser.binary_title", "Arquivo binário"),
                _t("rbrowser.binary_ask", "'{name}' não pode ser aberto no editor. Baixar para o disco?", name=entry.name),
                parent=self,
            ):
                self._download_items([(remote, entry)])
            return
        if (entry.size or 0) > MAX_OPEN_BYTES and not messagebox.askyesno(
            _t("rbrowser.big_title", "Arquivo grande"),
            _t("rbrowser.big_ask", "'{name}' tem {size}. Abrir mesmo assim?", name=entry.name, size=format_size(entry.size)),
            parent=self,
        ):
            return
        local = self._local_for(profile, remote)
        session = self._session
        self._begin(_t("rbrowser.downloading", "Baixando {name}…", name=entry.name))

        def _work() -> None:
            ok, msg = adapter.download(remote, local, self._report_progress)
            self._ui(self._open_downloaded, session, profile, remote, entry, local, kind, ok, msg)

        threading.Thread(target=_work, daemon=True).start()

    def _open_downloaded(
        self, session: int, profile: RemoteProfile, remote: str, entry: RemoteEntry,
        local: Path, kind: str, ok: bool, msg: str,
    ) -> None:  # fmt: skip
        self._end()
        if not ok:
            self._set_status(msg, error=True)
            messagebox.showerror(_t("rbrowser.dl_fail_title", "Falha no download"), msg, parent=self)
            return
        if kind == "unknown" and looks_binary(local):
            local.unlink(missing_ok=True)
            if messagebox.askyesno(
                _t("rbrowser.binary_title", "Arquivo binário"),
                _t("rbrowser.binary_ask", "'{name}' não pode ser aberto no editor. Baixar para o disco?", name=entry.name),
                parent=self,
            ):
                self._download_items([(remote, entry)])
            return
        if kind == "image":
            self._open_image(local, remote)
            return
        if self._open_file_cb is None:
            self._set_status(_t("rbrowser.no_editor", "Baixado em {path}", path=str(local)))
            return
        self._opened[str(local.resolve())] = _Opened(profile, remote, entry)
        self._open_file_cb(local)
        self._set_status(_t("rbrowser.opened", "Aberto: {name}", name=entry.name))

    def _open_image(self, local: Path, remote: str) -> None:
        t = self._t
        try:
            win = tk.Toplevel(self)
            win.title(f"🖼 {remote.rsplit('/', 1)[-1]}")
            win.config(bg=t.editor_bg)
            sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
            try:
                from PIL import Image, ImageTk

                with Image.open(local) as src:
                    img_obj = src.copy()
                img_obj.thumbnail((int(sw * 0.7), int(sh * 0.7)))
                img: Any = ImageTk.PhotoImage(img_obj)
            except ImportError:
                img = tk.PhotoImage(file=str(local))
            lbl = tk.Label(win, image=img, bg=t.editor_bg)
            lbl.image = img  # type: ignore[attr-defined]
            lbl.pack(padx=px(8), pady=px(8))
            tk.Label(win, text=remote, bg=t.editor_bg, fg=t.fg_muted, font=(FONT_MONO, 8)).pack(pady=(0, px(6)))
            win.bind("<Escape>", lambda _e: win.destroy())
        except Exception as exc:  # noqa: BLE001
            logger.warning("Visualizador de imagem: %s", exc)
            if self._open_file_cb:
                self._open_file_cb(local)

    def _on_local_saved(self, path: Path) -> None:
        """Observador de save do editor: reenvia ao servidor arquivos remotos."""
        try:
            key = str(path.resolve())
        except OSError:
            return
        info = self._opened.get(key)
        if info is None or not self._cfg.get("remote_sync_on_save", True):
            return
        self._sync_back(path, info)

    def _adapter_for(self, profile: RemoteProfile) -> tuple[RemoteAdapter | None, bool, str]:
        """Adaptador do perfil: o ativo, ou um temporário. Retorna (adapter, temporário, erro)."""
        if self._adapter is not None and self._profile is not None and self._profile.uid == profile.uid:
            return self._adapter, False, ""
        tmp = make_adapter(profile)
        ok, msg = tmp.connect()
        if not ok:
            tmp.close()
            return None, True, msg
        return tmp, True, ""

    def _sync_back(self, local: Path, info: _Opened) -> None:
        name = local.name
        self._begin(_t("rbrowser.syncing", "Enviando {name}…", name=name))

        def _confirm_overwrite() -> bool:
            done = threading.Event()
            answer = {"ok": False}

            def _ask() -> None:
                answer["ok"] = messagebox.askyesno(
                    _t("rbrowser.conflict_title", "Arquivo alterado no servidor"),
                    _t("rbrowser.conflict_msg", "'{name}' mudou no servidor desde que foi aberto.\nSobrescrever com a sua versão?", name=name),
                    parent=self,
                )
                done.set()

            self._ui(_ask)
            done.wait(300)
            return answer["ok"]

        def _work() -> None:
            adapter, temporary, err = self._adapter_for(info.profile)
            if adapter is None:
                self._ui(self._sync_done, local, info, False, err)
                return
            try:
                current = adapter.stat(info.remote)
                changed = (
                    current is not None
                    and info.baseline is not None
                    and (current.size, current.modified) != info.baseline
                )
                if changed and not _confirm_overwrite():
                    self._ui(self._sync_done, local, info, False, _t("rbrowser.sync_cancelled", "Envio cancelado."), True)
                    return
                ok, msg = adapter.upload(local, info.remote, self._report_progress)
                if ok:
                    fresh = adapter.stat(info.remote)
                    info.baseline = (fresh.size, fresh.modified) if fresh else info.baseline
            finally:
                if temporary:
                    adapter.close()
            self._ui(self._sync_done, local, info, ok, msg)

        threading.Thread(target=_work, daemon=True).start()

    def _sync_done(self, local: Path, info: _Opened, ok: bool, msg: str, cancelled: bool = False) -> None:
        self._end()
        if ok:
            self._set_status(_t("rbrowser.synced", "✓ {name} enviado ao servidor", name=local.name))
            return
        self._set_status(msg, error=True)
        if cancelled:
            return
        if messagebox.askretrycancel(
            _t("rbrowser.sync_fail_title", "Falha ao enviar ao servidor"),
            _t("rbrowser.sync_fail_msg", "Suas alterações em '{name}' foram salvas só localmente.\n\n{err}\n\nTentar novamente?", name=local.name, err=msg),
            parent=self,
        ):
            self._sync_back(local, info)

    def _download_selected(self) -> None:
        items = self._selection()
        if items:
            self._download_items(items)

    def _download_items(self, items: list[tuple[str, RemoteEntry]]) -> None:
        adapter = self._adapter
        if adapter is None:
            return
        if len(items) == 1 and not items[0][1].is_dir:
            remote, entry = items[0]
            dest = filedialog.asksaveasfilename(
                parent=self, title=_t("rbrowser.save_as", "Salvar como"), initialfile=entry.name
            )
            targets = [(remote, entry, Path(dest))] if dest else []
        else:
            folder = filedialog.askdirectory(parent=self, title=_t("rbrowser.pick_dest", "Escolher pasta de destino"))
            targets = [(r, e, Path(folder) / e.name) for r, e in items] if folder else []
        if not targets:
            return
        self._begin(_t("rbrowser.downloading_n", "Baixando {n} item(ns)…", n=len(targets)))

        def _work() -> None:
            done, errors = 0, []
            for remote, entry, dest in targets:
                ok, msg, count = self._pull(adapter, remote, entry, dest)
                done += count
                if not ok:
                    errors.append(f"{entry.name}: {msg}")
            self._ui(self._transfer_done, done, errors, False)

        threading.Thread(target=_work, daemon=True).start()

    def _pull(self, adapter: RemoteAdapter, remote: str, entry: RemoteEntry, dest: Path) -> tuple[bool, str, int]:
        """Baixa arquivo ou pasta (recursivo, com limites). -> (ok, erro, arquivos)."""
        if not entry.is_dir:
            dest.parent.mkdir(parents=True, exist_ok=True)
            ok, msg = adapter.download(remote, dest, self._report_progress)
            return ok, msg, int(ok)
        count, first_err = 0, ""
        stack = [(remote, dest, 0)]
        while stack:
            r, d, depth = stack.pop()
            if depth > MAX_WALK_DEPTH or count > MAX_WALK_FILES:
                return False, _t("rbrowser.too_big", "Pasta grande demais (limite de segurança)."), count
            entries, err = adapter.listdir(r)
            if err:
                first_err = first_err or err
                continue
            d.mkdir(parents=True, exist_ok=True)
            for e in entries:
                if e.is_dir:
                    if not e.is_link:
                        stack.append((join_path(r, e.name), d / e.name, depth + 1))
                    continue
                ok, msg = adapter.download(join_path(r, e.name), d / e.name, self._report_progress)
                if ok:
                    count += 1
                else:
                    first_err = first_err or msg
        return not first_err, first_err, count

    def _transfer_done(self, count: int, errors: list[str], uploaded: bool) -> None:
        self._end()
        verb = _t("rbrowser.sent", "enviado(s)") if uploaded else _t("rbrowser.received", "baixado(s)")
        if errors:
            self._set_status(errors[0], error=True)
            messagebox.showwarning(
                _t("rbrowser.transfer_title", "Transferência com falhas"),
                _t("rbrowser.transfer_partial", "{n} arquivo(s) {verb}. Falhas:\n\n{errs}", n=count, verb=verb, errs="\n".join(errors[:8])),
                parent=self,
            )
        else:
            self._set_status(f"✓ {count} {verb}")
        if uploaded:
            self._refresh()

    def _upload_files(self, into: str | None = None) -> None:
        adapter = self._adapter
        if adapter is None or adapter.read_only:
            return
        files = filedialog.askopenfilenames(parent=self, title=_t("rbrowser.pick_upload", "Escolher arquivos para enviar"))
        if not files:
            return
        target_dir = into or self._path
        existing = {e.name for e in self._entries} if into is None else set()
        clash = [Path(f).name for f in files if Path(f).name in existing]
        if clash and not messagebox.askyesno(
            _t("rbrowser.overwrite_title", "Substituir arquivos?"),
            _t("rbrowser.overwrite_msg", "Já existem no servidor:\n{names}\n\nSubstituir?", names="\n".join(clash[:8])),
            parent=self,
        ):
            return
        self._begin(_t("rbrowser.uploading_n", "Enviando {n} arquivo(s)…", n=len(files)))

        def _work() -> None:
            done, errors = 0, []
            for f in files:
                p = Path(f)
                ok, msg = adapter.upload(p, join_path(target_dir, p.name), self._report_progress)
                if ok:
                    done += 1
                else:
                    errors.append(f"{p.name}: {msg}")
            self._ui(self._transfer_done, done, errors, True)

        threading.Thread(target=_work, daemon=True).start()

    def _run_op(self, label: str, fn: Callable[[RemoteAdapter], list[str]]) -> None:
        adapter = self._adapter
        if adapter is None:
            return
        self._begin(label)

        def _work() -> None:
            errors = fn(adapter)
            self._ui(self._op_done, errors)

        threading.Thread(target=_work, daemon=True).start()

    def _op_done(self, errors: list[str]) -> None:
        self._end()
        if errors:
            self._set_status(errors[0], error=True)
            messagebox.showerror(_t("rbrowser.op_fail", "Operação falhou"), "\n".join(errors[:8]), parent=self)
        self._refresh()

    def _new_folder(self) -> None:
        adapter = self._adapter
        if adapter is None or adapter.read_only:
            return
        name = simpledialog.askstring(_t("rbrowser.ctx_mkdir", "Nova pasta"), _t("rbrowser.name_prompt", "Nome:"), parent=self)
        if not name or "/" in name:
            return
        target = join_path(self._path, name.strip())
        self._run_op(
            _t("rbrowser.creating", "Criando pasta…"),
            lambda a: [] if (r := a.mkdir(target))[0] else [r[1]],
        )

    def _rename_selected(self) -> None:
        items = self._selection()
        adapter = self._adapter
        if adapter is None or adapter.read_only or len(items) != 1:
            return
        remote, entry = items[0]
        new = simpledialog.askstring(
            _t("rbrowser.ctx_rename", "Renomear"), _t("rbrowser.name_prompt", "Nome:"), initialvalue=entry.name, parent=self
        )
        if not new or new == entry.name or "/" in new:
            return
        dst = join_path(self._path, new.strip())
        self._run_op(
            _t("rbrowser.renaming", "Renomeando…"),
            lambda a: [] if (r := a.rename(remote, dst))[0] else [r[1]],
        )

    def _delete_selected(self) -> None:
        items = self._selection()
        adapter = self._adapter
        if adapter is None or adapter.read_only or not items:
            return
        names = ", ".join(e.name for _, e in items[:5]) + ("…" if len(items) > 5 else "")
        extra = _t("rbrowser.delete_dir_warn", "\n\nPastas serão excluídas com todo o conteúdo.") if any(e.is_dir for _, e in items) else ""
        if not messagebox.askyesno(
            _t("rbrowser.ctx_delete", "Excluir"),
            _t("rbrowser.delete_items", "Excluir {n} item(ns) do servidor?\n{names}{extra}\n\nIsso não pode ser desfeito.", n=len(items), names=names, extra=extra),
            icon="warning",
            parent=self,
        ):
            return

        def _do(a: RemoteAdapter) -> list[str]:
            errs = []
            for remote, e in items:
                ok, msg = a.remove(remote, e.is_dir and not e.is_link)
                if not ok:
                    errs.append(f"{e.name}: {msg}")
            return errs

        self._run_op(_t("rbrowser.deleting", "Excluindo…"), _do)

    def open_debug_tunnel(self, remote_port: int = 5678, local_port: int = 5678) -> None:
        """Abre túnel SSH de depuração (somente conexões SSH)."""
        if not isinstance(self._adapter, SSHAdapter):
            messagebox.showinfo(
                _t("rbrowser.tunnel_ssh_only", "Somente SSH"),
                _t("rbrowser.tunnel_ssh_only_msg", "Túnel de debug disponível apenas para conexões SSH."),
                parent=self,
            )
            return
        self._stop_tunnel()
        self._tunnel_proc = self._adapter.start_debug_tunnel(remote_port, local_port)
        self._set_status(
            _t("rbrowser.tunnel_open", "Túnel debug SSH aberto.")
            if self._tunnel_proc
            else _t("rbrowser.tunnel_fail", "Falha ao abrir túnel."),
            error=self._tunnel_proc is None,
        )

    def _stop_tunnel(self) -> None:
        proc, self._tunnel_proc = self._tunnel_proc, None
        if proc is not None and proc.poll() is None:
            with contextlib.suppress(OSError):
                proc.terminate()

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is not self:
            return
        self._session += 1
        if self._poll_job is not None:
            with contextlib.suppress(tk.TclError):
                self.after_cancel(self._poll_job)
            self._poll_job = None
        self._unregister_hook()
        self._stop_tunnel()
        adapter, self._adapter = self._adapter, None
        if adapter is not None:
            threading.Thread(target=adapter.close, daemon=True).start()
        shutil.rmtree(self._workdir, ignore_errors=True)


def fit_toplevel(win: tk.Toplevel, width: int = 980, height: int = 680) -> None:
    """Dimensiona uma janela de painel para caber na tela atual."""
    w, h = fit_window(win, px(width), px(height))
    win.geometry(f"{w}x{h}")
    win.minsize(min(px(420), w), min(px(320), h))


from src.ui.remote_adapters import (  # noqa: E402,F401
    PROTO_DEFAULTS,
    FTPAdapter as _FTPAdapter,
    HTTPAdapter as _HTTPAdapter,
    Protocol,
    WebDAVAdapter as _WebDAVAdapter,
    classify as _classify,
    load_profiles as _load_profiles,
    make_adapter as _make_adapter,
    save_profiles as _save_profiles,
)

_SSHAdapter = SSHAdapter
