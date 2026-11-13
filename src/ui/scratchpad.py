"""Codis - ScratchpadPane.

Recursos:
- Múltiplas abas de rascunho nomeadas (até 5)
- Salvo automaticamente (debounce 500ms) por aba
- Contador de palavras/linhas/chars em tempo real
- Botão de inserção rápida: data/hora, separador, etc.
- Busca inline com Ctrl+F
- Exportar aba para arquivo .txt
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, ttk

from src.constants import FONT_MONO, FONT_UI
from src.i18n import _t

logger = logging.getLogger("Codis")

_SCRATCH_DIR = Path.home() / ".codis" / "scratchpads"
_STATE_FILE = _SCRATCH_DIR / "_state.json"
_MAX_TABS = 5
_DEFAULT_TABS = ["Rascunho 1"]


class ScratchpadPane(tk.Frame):
    """
    Bloco de notas persistente com múltiplas abas nomeadas.

    Cada aba é salva individualmente em ~/.codis/scratchpads/<nome>.txt
    com debounce de 500ms. Duplo-clique na aba para renomear.
    """

    def __init__(self, parent: tk.Widget, tmgr) -> None:
        """Inicializa o painel de rascunho (scratchpad)."""
        t = tmgr.theme
        super().__init__(parent, bg=t.sidebar_bg)
        self._tmgr = tmgr
        self._save_job: str | None = None
        self._tabs: list[str] = []
        self._active = 0
        self._contents: dict[str, str] = {}
        self._search_visible = False

        _SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
        self._load_state()
        self._build()
        self._load_tab(self._active)

    def _load_state(self) -> None:
        try:
            state = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
            self._tabs = state.get("tabs", _DEFAULT_TABS) or _DEFAULT_TABS
            self._active = min(state.get("active", 0), len(self._tabs) - 1)
        except (OSError, json.JSONDecodeError, ValueError):
            self._tabs = list(_DEFAULT_TABS)
            self._active = 0

    def _save_state(self) -> None:
        with contextlib.suppress(OSError):
            _STATE_FILE.write_text(
                json.dumps(
                    {"tabs": self._tabs, "active": self._active}, ensure_ascii=False
                ),
                encoding="utf-8",
            )

    def _tab_file(self, name: str) -> Path:
        safe = re.sub(r"[^\w\-. ]", "_", name)[:40]
        return _SCRATCH_DIR / f"{safe}.txt"

    def _load_tab(self, idx: int) -> None:
        if not self._tabs:
            return
        name = self._tabs[idx]
        if name not in self._contents:
            try:
                self._contents[name] = self._tab_file(name).read_text(
                    encoding="utf-8", errors="replace"
                )
            except OSError:
                self._contents[name] = ""
        with contextlib.suppress(tk.TclError):
            self._text.delete("1.0", "end")
            self._text.insert("1.0", self._contents[name])
            self._update_stats()

    def _save_current(self) -> None:
        self._save_job = None
        if not self._tabs:
            return
        name = self._tabs[self._active]
        content = self._text.get("1.0", "end-1c")
        self._contents[name] = content
        with contextlib.suppress(OSError):
            f = self._tab_file(name)
            tmp = f.with_suffix(".tmp")
            tmp.write_text(content, encoding="utf-8")
            tmp.replace(f)
        self._save_state()
        with contextlib.suppress(tk.TclError):
            self._status_lbl.config(text=_t("scratch.saved", "✔ salvo"))
            self.after(2000, lambda: self._status_lbl.config(text=""))

    def _schedule_save(self) -> None:
        if self._save_job:
            with contextlib.suppress(tk.TclError):
                self.after_cancel(self._save_job)
        self._save_job = self.after(500, self._save_current)
        self._status_lbl.config(text="...")

    def _build(self) -> None:
        t = self._tmgr.theme

        hdr = tk.Frame(self, bg=t.activity_bg, height=34)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        tk.Label(hdr, text="  📝", bg=t.activity_bg, fg=t.fg, font=(FONT_UI, 10)).pack(
            side="left"
        )

        self._status_lbl = tk.Label(
            hdr, text="", bg=t.activity_bg, fg=t.ok_fg, font=(FONT_UI, 7)
        )
        self._status_lbl.pack(side="right", padx=6)

        exp_btn = tk.Label(
            hdr,
            text="↑",
            bg=t.activity_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 11),
            cursor="hand2",
            padx=4,
        )
        exp_btn.pack(side="right")
        exp_btn.bind("<Button-1>", self._export)
        self._tooltip(exp_btn, "Exportar como .txt")

        srch_btn = tk.Label(
            hdr,
            text="🔍",
            bg=t.activity_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
            cursor="hand2",
            padx=4,
        )
        srch_btn.pack(side="right")
        srch_btn.bind("<Button-1>", self._toggle_search)
        self._tooltip(srch_btn, "Busca (Ctrl+F)")

        ins_btn = tk.Label(
            hdr,
            text="⊕",
            bg=t.activity_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 11),
            cursor="hand2",
            padx=4,
        )
        ins_btn.pack(side="right")
        ins_btn.bind("<Button-1>", self._quick_insert_menu)
        self._tooltip(ins_btn, "Inserir data / separador")

        self._tabs_frame = tk.Frame(self, bg=t.tabbar_bg, height=26)
        self._tabs_frame.pack(fill="x")
        self._tabs_frame.pack_propagate(False)
        self._rebuild_tabs()

        self._search_frame = tk.Frame(self, bg=t.editor_bg)
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._do_search())
        srch_inner = tk.Frame(self._search_frame, bg=t.editor_bg)
        srch_inner.pack(fill="x", padx=4, pady=2)
        tk.Label(
            srch_inner, text="🔍", bg=t.editor_bg, fg=t.fg_muted, font=(FONT_UI, 9)
        ).pack(side="left")
        self._search_entry = tk.Entry(
            srch_inner,
            textvariable=self._search_var,
            bg=t.input_bg,
            fg=t.fg,
            insertbackground=t.t_func,
            relief="flat",
            bd=2,
            font=(FONT_UI, 9),
        )
        self._search_entry.pack(side="left", fill="x", expand=True, padx=4)
        self._match_lbl = tk.Label(
            srch_inner, text="", bg=t.editor_bg, fg=t.fg_muted, font=(FONT_UI, 8)
        )
        self._match_lbl.pack(side="left", padx=4)
        tk.Label(
            srch_inner,
            text="✕",
            bg=t.editor_bg,
            fg=t.fg_muted,
            cursor="hand2",
            font=(FONT_UI, 9),
        ).pack(side="right", padx=4)
        self.bind("<Button-1>", self._hide_search)
        self._search_entry.bind("<Escape>", self._hide_search)
        self._search_entry.bind(
            "<Return>", lambda e: self._do_search(next_match=True))

        txt_frame = tk.Frame(self, bg=t.sidebar_bg)
        txt_frame.pack(fill="both", expand=True)

        vsb = ttk.Scrollbar(txt_frame, orient="vertical")
        vsb.pack(side="right", fill="y")

        self._text = tk.Text(
            txt_frame,
            bg=t.editor_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            font=(FONT_MONO, 10),
            relief="flat",
            bd=0,
            padx=10,
            pady=8,
            wrap="word",
            undo=True,
        )
        self._text.config(yscrollcommand=vsb.set)
        vsb.config(command=self._text.yview)
        self._text.pack(fill="both", expand=True)
        self._text.tag_config(
            "search_hi", background=getattr(t, "sel_bg", "#264f78"), foreground=t.fg
        )
        self._text.bind("<KeyRelease>", self._on_change)
        self._text.bind("<Control-f>", self._toggle_search)
        self._text.bind("<Control-F>", self._toggle_search)
        self._text.bind("<Control-s>", lambda e: self._save_current())

        footer = tk.Frame(self, bg=t.activity_bg, height=20)
        footer.pack(fill="x")
        footer.pack_propagate(False)
        self._stats_lbl = tk.Label(
            footer,
            text="",
            bg=t.activity_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 7),
            anchor="e",
        )
        self._stats_lbl.pack(side="right", padx=6, fill="y")

        clr = tk.Label(
            footer,
            text=_t("scratch.clear", "✕ limpar"),
            bg=t.activity_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 7),
            cursor="hand2",
        )
        clr.pack(side="left", padx=6, fill="y")
        clr.bind("<Button-1>", self._clear)

    def _rebuild_tabs(self) -> None:
        t = self._tmgr.theme
        for w in self._tabs_frame.winfo_children():
            w.destroy()

        for i, name in enumerate(self._tabs):
            is_active = i == self._active
            bg = t.tab_active_bg if is_active else t.tab_bg
            fg = t.tab_active_fg if is_active else t.tab_fg

            tab_frame = tk.Frame(self._tabs_frame, bg=bg, cursor="hand2")
            tab_frame.pack(side="left", fill="y", padx=(0, 1))

            lbl = tk.Label(
                tab_frame,
                text=f" {name} ",
                bg=bg,
                fg=fg,
                font=(FONT_UI, 8, "bold" if is_active else "normal"),
                padx=2,
                cursor="hand2",
            )
            lbl.pack(side="left", fill="y")

            if is_active and len(self._tabs) > 1:
                close = tk.Label(
                    tab_frame,
                    text="✕",
                    bg=bg,
                    fg=t.fg_muted,
                    font=(FONT_UI, 7),
                    cursor="hand2",
                    padx=2,
                )
                close.pack(side="left", fill="y")
                close.bind("<Button-1>", lambda e, idx=i: self._close_tab(idx))

            if is_active:
                tk.Frame(tab_frame, bg=t.accent, height=2).pack(
                    side="bottom", fill="x")

            for w in (tab_frame, lbl):
                w.bind("<Button-1>", lambda e, idx=i: self._switch_tab(idx))
                w.bind("<Double-1>", lambda e, idx=i: self._rename_tab(idx))

        if len(self._tabs) < _MAX_TABS:
            add = tk.Label(
                self._tabs_frame,
                text=" ⊕ ",
                bg=t.tab_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 9),
                cursor="hand2",
            )
            add.pack(side="left", fill="y", padx=2)
            add.bind("<Button-1>", lambda e: self._new_tab())

    def _switch_tab(self, idx: int) -> None:
        if self._tabs:
            self._contents[self._tabs[self._active]
                           ] = self._text.get("1.0", "end-1c")
        self._active = idx
        self._load_tab(idx)
        self._rebuild_tabs()

    def _new_tab(self) -> None:
        n = len(self._tabs) + 1
        name = f"Rascunho {n}"
        while name in self._tabs:
            n += 1
            name = f"Rascunho {n}"
        self._tabs.append(name)
        self._contents[name] = ""
        self._switch_tab(len(self._tabs) - 1)

    def _close_tab(self, idx: int) -> None:
        if len(self._tabs) <= 1:
            return
        name = self._tabs.pop(idx)
        self._contents.pop(name, None)
        new_idx = min(idx, len(self._tabs) - 1)
        self._active = new_idx
        self._load_tab(new_idx)
        self._rebuild_tabs()
        self._save_state()

    def _rename_tab(self, idx: int) -> None:
        t = self._tmgr.theme
        old = self._tabs[idx]
        win = tk.Toplevel(self)
        win.wm_overrideredirect(True)
        win.wm_attributes("-topmost", True)
        win.configure(bg=t.floating_bg)
        win.geometry(
            f"220x36+{self.winfo_rootx() + 40}+{self.winfo_rooty() + 30}")
        var = tk.StringVar(value=old)
        ent = tk.Entry(
            win,
            textvariable=var,
            bg=t.input_bg,
            fg=t.fg,
            insertbackground=t.t_func,
            relief="flat",
            bd=2,
            font=(FONT_UI, 9),
            width=20,
        )
        ent.pack(fill="both", expand=True, padx=4, pady=4)
        ent.select_range(0, "end")
        ent.focus_set()

        def _confirm(e=None):
            new = var.get().strip()
            if new and new != old and new not in self._tabs:
                content = self._contents.pop(old, "")
                self._tabs[idx] = new
                self._contents[new] = content
                self._rebuild_tabs()
                self._save_state()
            win.destroy()

        ent.bind("<Return>", _confirm)
        ent.bind("<Escape>", lambda e: win.destroy())
        win.bind("<FocusOut>", lambda e: win.destroy())

    def _on_change(self, _e=None) -> None:
        self._update_stats()
        self._schedule_save()

    def _update_stats(self) -> None:
        content = self._text.get("1.0", "end-1c")
        words = len(re.findall(r"\S+", content))
        chars = len(content)
        lines = content.count("\n") + 1 if content else 0
        with contextlib.suppress(tk.TclError):
            self._stats_lbl.config(
                text=f"{words} palavras - {chars} chars - {lines} linhas"
            )

    def _clear(self, _e=None) -> None:
        self._text.delete("1.0", "end")
        self._on_change()

    def _toggle_search(self, _e=None) -> None:
        if self._search_visible:
            self._hide_search()
        else:
            self._search_frame.pack(after=self._tabs_frame, fill="x")
            self._search_visible = True
            self._search_entry.focus_set()
        return "break"

    def _hide_search(self, _e=None) -> None:
        self._search_frame.pack_forget()
        self._search_visible = False
        self._text.tag_remove("search_hi", "1.0", "end")
        self._match_lbl.config(text="")
        self._text.focus_set()

    def _do_search(self, next_match: bool = False) -> None:
        q = self._search_var.get()
        self._text.tag_remove("search_hi", "1.0", "end")
        if not q:
            self._match_lbl.config(text="")
            return
        count = 0
        start = "1.0"
        first = None
        while True:
            pos = self._text.search(q, start, nocase=True, stopindex="end")
            if not pos:
                break
            end = f"{pos}+{len(q)}c"
            self._text.tag_add("search_hi", pos, end)
            if first is None:
                first = pos
            count += 1
            start = end
        self._match_lbl.config(
            text=f"{count} resultado{'s' if count != 1 else ''}")
        if first:
            self._text.see(first)

    def _quick_insert_menu(self, event: tk.Event) -> None:
        t = self._tmgr.theme
        menu = tk.Menu(
            self,
            tearoff=False,
            bg=t.floating_bg,
            fg=t.fg,
            activebackground=t.sel_bg,
            activeforeground=t.sel_fg,
            relief="flat",
            bd=0,
        )
        now = datetime.now()
        menu.add_command(
            label=f"📅  Data: {now.strftime('%d/%m/%Y')}",
            command=lambda: self._insert(now.strftime("%d/%m/%Y")),
        )
        menu.add_command(
            label=f"🕐  Data+Hora: {now.strftime('%d/%m/%Y %H:%M')}",
            command=lambda: self._insert(now.strftime("%d/%m/%Y %H:%M")),
        )
        menu.add_separator()
        menu.add_command(
            label=_t("scratchpad.separator"),
            command=lambda: self._insert("\n" + "-" * 40 + "\n"),
        )
        menu.add_command(
            label=_t("scratchpad.title_block"),
            command=lambda: self._insert("\n" + "=" * 40 + "\n"),
        )
        menu.add_separator()
        menu.add_command(
            label=_t("scratchpad.task_open"), command=lambda: self._insert("[ ] ")
        )
        menu.add_command(
            label=_t("scratchpad.task_done"), command=lambda: self._insert("[x] ")
        )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _insert(self, text: str) -> None:
        try:
            self._text.insert("insert", text)
        except tk.TclError:
            self._text.insert("end", text)
        self._on_change()

    def _export(self, _e=None) -> None:
        name = self._tabs[self._active] if self._tabs else "rascunho"
        content = self._text.get("1.0", "end-1c")
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Texto", "*.txt"),
                       ("Markdown", "*.md"), ("Todos", "*.*")],
            initialfile=f"{name}.txt",
            title=_t("scratchpad.export_title", "Exportar bloco de notas"),
        )
        if path:
            with contextlib.suppress(OSError):
                Path(path).write_text(content, encoding="utf-8")

    def _tooltip(self, widget: tk.Widget, text: str) -> None:
        tip: list[tk.Toplevel | None] = [None]

        def _show(e):
            t = self._tmgr.theme
            tip[0] = tk.Toplevel(widget)
            tip[0].wm_overrideredirect(True)
            tip[0].wm_attributes("-topmost", True)
            tk.Label(
                tip[0],
                text=text,
                bg=t.floating_bg,
                fg=t.fg,
                font=(FONT_UI, 8),
                padx=6,
                pady=2,
            ).pack()
            tip[0].geometry(f"+{e.x_root + 10}+{e.y_root + 14}")

        def _hide(e):
            if tip[0]:
                with contextlib.suppress(tk.TclError):
                    tip[0].destroy()
                tip[0] = None

        widget.bind("<Enter>", _show)
        widget.bind("<Leave>", _hide)

    def apply_theme(self, t) -> None:
        """Aplica o tema ``t`` ao scratchpad."""
        self.config(bg=t.sidebar_bg)
        with contextlib.suppress(tk.TclError):
            self._text.config(bg=t.editor_bg, fg=t.fg,
                              insertbackground=t.cursor)
