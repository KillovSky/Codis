"""Codis - Key Remapper.

Permite ao usuário remapear qualquer atalho de teclado da aplicação.
Os atalhos customizados são persistidos na configuração e aplicados
em toda a sessão.

Arquitetura
-----------
- ``KeyRemapperStore``   : lê/grava ``keybindings.json`` separado do config.json
- ``KeyRemapperDialog``  : janela de edição com lista pesquisável e captura interativa
- ``KeyRemapperManager`` : aplica os bindings no root do Tk e é usado pelo CodisApp
"""

from __future__ import annotations

import contextlib
import json
import logging
import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox
from typing import Any

from src.constants import CONFIG_DIR, FONT_MONO, FONT_UI
from src.i18n import _t
from src.ui.grab_safe import safe_grab_set

logger = logging.getLogger("Codis")

_KEYBINDINGS_FILE = CONFIG_DIR / "keybindings.json"


def _get_default_bindings() -> list[dict[str, str]]:
    """Returns the default binding list with localised labels."""
    return [
        {
            "id": "file.new",
            "label": _t("keyremap.cmd.file_new"),
            "default": "<Control-n>",
            "current": "",
        },
        {
            "id": "file.open",
            "label": _t("keyremap.cmd.file_open"),
            "default": "<Control-o>",
            "current": "",
        },
        {
            "id": "file.save",
            "label": _t("keyremap.cmd.file_save"),
            "default": "<Control-s>",
            "current": "",
        },
        {
            "id": "file.save_as",
            "label": _t("keyremap.cmd.file_save_as"),
            "default": "<Control-S>",
            "current": "",
        },
        {
            "id": "file.save_all",
            "label": _t("keyremap.cmd.file_save_all"),
            "default": "",
            "current": "",
        },
        {
            "id": "file.close_tab",
            "label": _t("keyremap.cmd.file_close_tab"),
            "default": "<Control-w>",
            "current": "",
        },
        {
            "id": "file.exit",
            "label": _t("keyremap.cmd.file_exit"),
            "default": "<Alt-F4>",
            "current": "",
        },
        {
            "id": "edit.undo",
            "label": _t("keyremap.cmd.edit_undo"),
            "default": "<Control-z>",
            "current": "",
        },
        {
            "id": "edit.redo",
            "label": _t("keyremap.cmd.edit_redo"),
            "default": "<Control-y>",
            "current": "",
        },
        {
            "id": "edit.cut",
            "label": _t("keyremap.cmd.edit_cut"),
            "default": "<Control-x>",
            "current": "",
        },
        {
            "id": "edit.copy",
            "label": _t("keyremap.cmd.edit_copy"),
            "default": "<Control-c>",
            "current": "",
        },
        {
            "id": "edit.paste",
            "label": _t("keyremap.cmd.edit_paste"),
            "default": "<Control-v>",
            "current": "",
        },
        {
            "id": "edit.select_all",
            "label": _t("keyremap.cmd.edit_select_all"),
            "default": "<Control-a>",
            "current": "",
        },
        {
            "id": "edit.find",
            "label": _t("keyremap.cmd.edit_find"),
            "default": "<Control-f>",
            "current": "",
        },
        {
            "id": "edit.replace",
            "label": _t("keyremap.cmd.edit_replace"),
            "default": "<Control-h>",
            "current": "",
        },
        {
            "id": "edit.find_files",
            "label": _t("keyremap.cmd.edit_find_files"),
            "default": "<Control-F>",
            "current": "",
        },
        {
            "id": "edit.comment",
            "label": _t("keyremap.cmd.edit_comment"),
            "default": "<Control-slash>",
            "current": "",
        },
        {
            "id": "edit.duplicate",
            "label": _t("keyremap.cmd.edit_duplicate"),
            "default": "<Control-d>",
            "current": "",
        },
        {
            "id": "edit.delete_line",
            "label": _t("keyremap.cmd.edit_delete_line"),
            "default": "<Control-K>",
            "current": "",
        },
        {
            "id": "edit.move_up",
            "label": _t("keyremap.cmd.edit_move_up"),
            "default": "<Alt-Up>",
            "current": "",
        },
        {
            "id": "edit.move_down",
            "label": _t("keyremap.cmd.edit_move_down"),
            "default": "<Alt-Down>",
            "current": "",
        },
        {
            "id": "edit.paste_history",
            "label": _t("keyremap.cmd.edit_paste_history"),
            "default": "<Control-Shift-V>",
            "current": "",
        },
        {
            "id": "view.command_palette",
            "label": _t("keyremap.cmd.view_palette"),
            "default": "<Control-P>",
            "current": "",
        },
        {
            "id": "view.quick_open",
            "label": _t("keyremap.cmd.view_quick_open"),
            "default": "<Control-p>",
            "current": "",
        },
        {
            "id": "view.terminal",
            "label": _t("keyremap.cmd.view_terminal"),
            "default": "<Control-grave>",
            "current": "",
        },
        {
            "id": "view.problems",
            "label": _t("keyremap.cmd.view_problems"),
            "default": "<Control-M>",
            "current": "",
        },
        {
            "id": "view.zoom_in",
            "label": _t("keyremap.cmd.view_zoom_in"),
            "default": "<Control-equal>",
            "current": "",
        },
        {
            "id": "view.zoom_out",
            "label": _t("keyremap.cmd.view_zoom_out"),
            "default": "<Control-minus>",
            "current": "",
        },
        {
            "id": "view.zoom_reset",
            "label": _t("keyremap.cmd.view_zoom_reset"),
            "default": "<Control-0>",
            "current": "",
        },
        {
            "id": "view.fullscreen",
            "label": _t("keyremap.cmd.view_fullscreen"),
            "default": "<F11>",
            "current": "",
        },
        {
            "id": "view.split",
            "label": _t("keyremap.cmd.view_split"),
            "default": "",
            "current": "",
        },
        {
            "id": "view.settings",
            "label": _t("keyremap.cmd.view_settings"),
            "default": "<Control-comma>",
            "current": "",
        },
        {
            "id": "view.color_picker",
            "label": _t("keyremap.cmd.view_color_picker"),
            "default": "<Control-Shift-C>",
            "current": "",
        },
        {
            "id": "view.regex",
            "label": _t("keyremap.cmd.view_regex"),
            "default": "<Control-Shift-R>",
            "current": "",
        },
        {
            "id": "view.ia",
            "label": _t("keyremap.cmd.view_ia"),
            "default": "<Control-Shift-A>",
            "current": "",
        },
        {
            "id": "view.scratchpad",
            "label": _t("keyremap.cmd.view_scratchpad"),
            "default": "<Control-Shift-N>",
            "current": "",
        },
        {
            "id": "view.snippets",
            "label": _t("keyremap.cmd.view_snippets"),
            "default": "<Control-Shift-J>",
            "current": "",
        },
        {
            "id": "go.line",
            "label": _t("keyremap.cmd.go_line"),
            "default": "<Control-g>",
            "current": "",
        },
        {
            "id": "go.next_tab",
            "label": _t("keyremap.cmd.go_next_tab"),
            "default": "<Control-Tab>",
            "current": "",
        },
        {
            "id": "go.prev_tab",
            "label": _t("keyremap.cmd.go_prev_tab"),
            "default": "<Control-Shift-Tab>",
            "current": "",
        },
        {
            "id": "go.next_problem",
            "label": _t("keyremap.cmd.go_next_problem"),
            "default": "<F8>",
            "current": "",
        },
        {
            "id": "go.prev_problem",
            "label": _t("keyremap.cmd.go_prev_problem"),
            "default": "<Shift-F8>",
            "current": "",
        },
        {
            "id": "run.run_file",
            "label": _t("keyremap.cmd.run_file"),
            "default": "<F5>",
            "current": "",
        },
        {
            "id": "run.format",
            "label": _t("keyremap.cmd.run_format"),
            "default": "<Shift-Alt-f>",
            "current": "",
        },
        {
            "id": "tools.bookmark_add",
            "label": _t("keyremap.cmd.tools_bookmark_add"),
            "default": "<Control-F2>",
            "current": "",
        },
        {
            "id": "tools.bookmark_next",
            "label": _t("keyremap.cmd.tools_bookmark_next"),
            "default": "<F2>",
            "current": "",
        },
        {
            "id": "tools.zen_mode",
            "label": _t("keyremap.cmd.tools_zen_mode"),
            "default": "",
            "current": "",
        },
        {
            "id": "tools.vim_mode",
            "label": _t("keyremap.cmd.tools_vim_mode"),
            "default": "<Control-Alt-v>",
            "current": "",
        },
        {
            "id": "tools.kui",
            "label": _t("keyremap.cmd.tools_kui"),
            "default": "<Control-Alt-n>",
            "current": "",
        },
        {
            "id": "tools.pomodoro",
            "label": _t("keyremap.cmd.tools_pomodoro"),
            "default": "<Control-Alt-p>",
            "current": "",
        },
    ]


class KeyRemapperStore:
    """Persiste e lê o arquivo keybindings.json."""

    def __init__(self) -> None:
        """Inicializa a store de atalhos de teclado com persistência em arquivo."""
        self._bindings: list[dict[str, str]] = []
        self._load()

    def _load(self) -> None:
        self._bindings = [dict(b) for b in _get_default_bindings()]
        if not _KEYBINDINGS_FILE.exists():
            return
        try:
            raw: Any = json.loads(
                _KEYBINDINGS_FILE.read_text(encoding="utf-8"))
            overrides: dict[str, str] = raw if isinstance(raw, dict) else {}
            for b in self._bindings:
                if b["id"] in overrides:
                    b["current"] = overrides[b["id"]]
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("keybindings.json corrompido: %s", exc)

    def save(self) -> None:
        """Persiste o mapa de atalhos atual no arquivo de configuração."""
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            overrides = {b["id"]: b["current"]
                         for b in self._bindings if b["current"]}
            _KEYBINDINGS_FILE.write_text(
                json.dumps(overrides, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("Falha ao salvar keybindings.json: %s", exc)

    def set(self, binding_id: str, key: str) -> None:
        """Define ou redefine o atalho de uma ação."""
        for b in self._bindings:
            if b["id"] == binding_id:
                b["current"] = key
                return

    def reset(self, binding_id: str) -> None:
        """Restaura o atalho de uma ação para o valor padrão."""
        self.set(binding_id, "")

    def reset_all(self) -> None:
        """Restaura todos os atalhos para os valores padrão."""
        for b in self._bindings:
            b["current"] = ""

    @property
    def bindings(self) -> list[dict[str, str]]:
        """Retorna uma cópia do dicionário de atalhos atual."""
        return self._bindings


class KeyRemapperManager:
    """Aplica os keybindings no widget raiz do Tkinter."""

    def __init__(self, root: tk.Tk, store: KeyRemapperStore) -> None:
        """Inicializa o gerenciador de atalhos de teclado."""
        self._root = root
        self._store = store
        self._handlers: dict[str, Callable] = {}
        self._applied_keys: list[str] = []

    def register(self, binding_id: str, handler: Callable) -> None:
        """Registra uma ação com seu atalho padrão."""
        self._handlers[binding_id] = handler

    def apply_all(self) -> None:
        """Aplica todos os atalhos registrados ao widget root."""
        for key in self._applied_keys:
            with contextlib.suppress(tk.TclError):
                self._root.unbind(key)
        self._applied_keys.clear()

        for b in self._store.bindings:
            bid = b["id"]
            handler = self._handlers.get(bid)
            if not handler:
                continue
            key = b["current"] or b["default"]
            if not key:
                continue
            with contextlib.suppress(tk.TclError):
                self._root.bind(key, lambda _e, h=handler: h())
                self._applied_keys.append(key)


def _tk_key_to_human(tk_key: str) -> str:
    """Converte string Tk (<Control-s>) para exibição legível (Ctrl+S)."""
    if not tk_key:
        return ""
    s = tk_key.strip("<>")
    parts = s.split("-")
    mapped = []
    for p in parts:
        p_lower = p.lower()
        if p_lower == "control":
            mapped.append("Ctrl")
        elif p_lower == "shift":
            mapped.append("Shift")
        elif p_lower == "alt":
            mapped.append("Alt")
        elif p_lower == "grave":
            mapped.append("`")
        elif p_lower == "slash":
            mapped.append("/")
        elif p_lower == "equal":
            mapped.append("=")
        elif p_lower == "minus":
            mapped.append("-")
        elif p_lower == "comma":
            mapped.append(",")
        else:
            mapped.append(p.upper() if len(p) == 1 else p)
    return "+".join(mapped)


def _human_to_tk_key(human: str) -> str:
    """Converte exibição legível (Ctrl+Shift+S) para string Tk (<Control-Shift-S>)."""
    if not human:
        return ""
    parts = human.split("+")
    mapped = []
    for p in parts:
        p_lower = p.lower()
        if p_lower == "ctrl":
            mapped.append("Control")
        elif p_lower == "shift":
            mapped.append("Shift")
        elif p_lower == "alt":
            mapped.append("Alt")
        elif p == "`":
            mapped.append("grave")
        elif p == "/":
            mapped.append("slash")
        elif p == "=":
            mapped.append("equal")
        elif p == "-":
            mapped.append("minus")
        elif p == ",":
            mapped.append("comma")
        else:
            mapped.append(p)
    return "<" + "-".join(mapped) + ">"


class KeyRemapperDialog(tk.Toplevel):
    """Janela de remapeamento de atalhos.

    Exibe lista pesquisável de todos os comandos. O usuário pode clicar
    em um item e pressionar a nova combinação de teclas para capturá-la
    interativamente, ou digitar manualmente.
    """

    def __init__(
        self,
        parent: tk.Widget,
        tmgr: Any,
        store: KeyRemapperStore,
        on_apply: Callable[[], None],
    ) -> None:
        """Inicializa o diálogo de remapeamento de atalhos de teclado."""
        t = tmgr.theme
        super().__init__(parent)
        self.title(_t("keyremap.window_title"))
        self.configure(bg=t.floating_bg)
        self.geometry("760x560")
        self.minsize(600, 400)
        self.transient(parent)
        safe_grab_set(self)

        self._tmgr = tmgr
        self._store = store
        self._on_apply = on_apply
        self._capturing = False
        self._selected_id: str | None = None

        self._build(t)
        self._populate()
        self._center(parent)

    def _build(self, t: Any) -> None:
        tk.Frame(self, bg=t.accent, height=2).pack(fill="x")

        hdr = tk.Frame(self, bg=t.floating_bg)
        hdr.pack(fill="x", padx=14, pady=(10, 4))
        tk.Label(
            hdr,
            text=_t("keyremap.title"),
            bg=t.floating_bg,
            fg=t.t_func,
            font=(FONT_UI, 12, "bold"),
        ).pack(side="left")

        reset_all_btn = tk.Label(
            hdr,
            text=_t("keyremap.restore_all"),
            bg=t.btn_bg,
            fg=t.fg,
            font=(FONT_UI, 8),
            cursor="hand2",
            padx=8,
            pady=3,
            relief="flat",
        )
        reset_all_btn.pack(side="right")
        reset_all_btn.bind("<Button-1>", lambda _: self._reset_all())

        search_frame = tk.Frame(self, bg=t.floating_bg)
        search_frame.pack(fill="x", padx=14, pady=(0, 4))
        tk.Label(
            search_frame,
            text="🔍",
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 10),
        ).pack(side="left")
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._populate())
        tk.Entry(
            search_frame,
            textvariable=self._search_var,
            bg=t.input_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            bd=0,
            font=(FONT_UI, 10),
            highlightthickness=1,
            highlightbackground=t.border,
        ).pack(side="left", fill="x", expand=True, padx=4, pady=2)

        cols = tk.Frame(self, bg=t.floating_bg)
        cols.pack(fill="x", padx=14, pady=(0, 2))
        for text, w in (
            (_t("keyremap.col_command"), 30),
            (_t("keyremap.col_default"), 18),
            (_t("keyremap.col_current"), 18),
        ):
            tk.Label(
                cols,
                text=text,
                bg=t.floating_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 8, "bold"),
                width=w,
                anchor="w",
            ).pack(side="left")

        list_frame = tk.Frame(self, bg=t.floating_bg)
        list_frame.pack(fill="both", expand=True, padx=14, pady=2)

        scrollbar = tk.Scrollbar(
            list_frame, orient="vertical", bg=t.scroll_thumb)
        self._listbox = tk.Listbox(
            list_frame,
            bg=t.editor_bg,
            fg=t.fg,
            selectbackground=t.sel_bg,
            selectforeground=t.sel_fg,
            font=(FONT_MONO, 9),
            relief="flat",
            bd=0,
            activestyle="none",
            highlightthickness=0,
            yscrollcommand=scrollbar.set,
        )
        scrollbar.config(command=self._listbox.yview)
        scrollbar.pack(side="right", fill="y")
        self._listbox.pack(side="left", fill="both", expand=True)
        self._listbox.bind("<<ListboxSelect>>", self._on_select)

        sep = tk.Frame(self, bg=t.border, height=1)
        sep.pack(fill="x", padx=14, pady=4)

        edit_frame = tk.Frame(self, bg=t.floating_bg)
        edit_frame.pack(fill="x", padx=14, pady=(0, 4))

        tk.Label(
            edit_frame,
            text=_t("keyremap.new_shortcut"),
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
        ).pack(side="left")

        self._key_var = tk.StringVar()
        self._key_entry = tk.Entry(
            edit_frame,
            textvariable=self._key_var,
            bg=t.input_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            bd=0,
            font=(FONT_MONO, 10),
            width=22,
            highlightthickness=1,
            highlightbackground=t.border,
        )
        self._key_entry.pack(side="left", padx=6)

        self._capture_btn = tk.Label(
            edit_frame,
            text=_t("keyremap.capture_btn"),
            bg=t.btn_bg,
            fg=t.fg,
            font=(FONT_UI, 9),
            cursor="hand2",
            padx=8,
            pady=3,
            relief="flat",
        )
        self._capture_btn.pack(side="left", padx=4)
        self._capture_btn.bind("<Button-1>", lambda _: self._start_capture())

        self._status_lbl = tk.Label(
            edit_frame,
            text="",
            bg=t.floating_bg,
            fg=t.t_string,
            font=(FONT_UI, 8),
        )
        self._status_lbl.pack(side="left", padx=6)

        foot = tk.Frame(self, bg=t.floating_bg)
        foot.pack(fill="x", padx=14, pady=(4, 12))

        tk.Label(
            foot,
            text=_t("keyremap.hint"),
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        ).pack(side="left")

        for text, cmd, primary in (
            (_t("keyremap.apply"), self._apply_selected, True),
            (_t("keyremap.reset"), self._reset_selected, False),
            (_t("keyremap.close"), self.destroy, False),
        ):
            btn = tk.Label(
                foot,
                text=text,
                bg=t.btn_primary if primary else t.btn_bg,
                fg=t.btn_primary_fg if primary else t.fg,
                font=(FONT_UI, 9, "bold" if primary else "normal"),
                cursor="hand2",
                padx=10,
                pady=4,
                relief="flat",
            )
            btn.pack(side="right", padx=3)
            btn.bind("<Button-1>", lambda _, c=cmd: c())

    def _populate(self) -> None:
        t = self._tmgr.theme
        q = self._search_var.get().lower()
        self._listbox.delete(0, "end")
        self._row_ids: list[str] = []

        for b in self._store.bindings:
            if q and q not in b["label"].lower() and q not in b["id"].lower():
                continue
            default_human = _tk_key_to_human(b["default"])
            current_human = _tk_key_to_human(
                b["current"]) if b["current"] else ""
            custom_marker = "✎ " if b["current"] else "  "
            active = current_human or default_human or "-"
            line = f"  {b['label']:<32}  {default_human:<18}  {custom_marker}{active}"
            self._listbox.insert("end", line)
            self._row_ids.append(b["id"])

            if b["current"]:
                idx = len(self._row_ids) - 1
                self._listbox.itemconfig(idx, fg=t.t_string)

    def _on_select(self, _e: Any = None) -> None:
        sel = self._listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx >= len(self._row_ids):
            return
        self._selected_id = self._row_ids[idx]
        for b in self._store.bindings:
            if b["id"] == self._selected_id:
                current = _tk_key_to_human(
                    b["current"]) if b["current"] else ""
                self._key_var.set(current)
                self._status_lbl.config(
                    text=_t("keyremap.id_hint", id=b["id"]))
                break

    def _start_capture(self) -> None:
        if not self._selected_id:
            self._status_lbl.config(text=_t("keyremap.no_command"))
            return

        t = self._tmgr.theme
        self._capturing = True
        self._capture_btn.config(
            text=_t("keyremap.capturing"),
            bg="#c62828",
            fg="white",
        )
        self._status_lbl.config(text="")

        capture_win = tk.Toplevel(self)
        capture_win.title(_t("keyremap.capture_win"))
        capture_win.configure(bg=t.floating_bg)
        capture_win.geometry("320x120")
        capture_win.transient(self)
        safe_grab_set(capture_win)
        capture_win.resizable(False, False)

        tk.Label(
            capture_win,
            text=_t("keyremap.capture_hint"),
            bg=t.floating_bg,
            fg=t.fg,
            font=(FONT_UI, 11),
            justify="center",
        ).pack(expand=True)

        def _on_key(event: tk.Event) -> str:
            if event.keysym == "Escape":
                capture_win.destroy()
                self._stop_capture()
                return "break"

            parts = []
            if event.state & 0x4:
                parts.append("Control")
            if event.state & 0x1:
                parts.append("Shift")
            if event.state & 0x8:
                parts.append("Alt")

            sym = event.keysym
            if sym in (
                "Control_L",
                "Control_R",
                "Shift_L",
                "Shift_R",
                "Alt_L",
                "Alt_R",
                "Meta_L",
                "Meta_R",
            ):
                return "break"

            parts.append(sym)
            tk_key = "<" + "-".join(parts) + ">"
            human = _tk_key_to_human(tk_key)
            self._key_var.set(human)
            capture_win.destroy()
            self._stop_capture()
            return "break"

        capture_win.bind("<KeyPress>", _on_key)
        capture_win.focus_force()

    def _stop_capture(self) -> None:
        t = self._tmgr.theme
        self._capturing = False
        self._capture_btn.config(
            text=_t("keyremap.capture_btn"), bg=t.btn_bg, fg=t.fg)

    def _apply_selected(self) -> None:
        if not self._selected_id:
            return
        human = self._key_var.get().strip()
        tk_key = _human_to_tk_key(human) if human else ""

        self._store.set(self._selected_id, tk_key)
        self._store.save()
        self._on_apply()
        self._status_lbl.config(text=_t("keyremap.applied"))
        self._populate()

    def _reset_selected(self) -> None:
        if not self._selected_id:
            return
        self._store.reset(self._selected_id)
        self._key_var.set("")
        self._store.save()
        self._on_apply()
        self._status_lbl.config(text=_t("keyremap.reset_done"))
        self._populate()

    def _reset_all(self) -> None:
        if not messagebox.askyesno(
            _t("keyremap.reset_all_title"),
            _t("keyremap.reset_all_confirm"),
            parent=self,
        ):
            return
        self._store.reset_all()
        self._key_var.set("")
        self._store.save()
        self._on_apply()
        self._status_lbl.config(text=_t("keyremap.reset_all_done"))
        self._populate()

    def _center(self, parent: tk.Widget) -> None:
        self.update_idletasks()
        try:
            px = parent.winfo_rootx()
            py = parent.winfo_rooty()
            pw = parent.winfo_width()
            ph = parent.winfo_height()
        except tk.TclError:
            return
        w = self.winfo_width()
        h = self.winfo_height()
        self.geometry(f"+{px + (pw - w) // 2}+{py + (ph - h) // 2}")
