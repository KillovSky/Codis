"""Codis - DocsPanel

Sidebar panel that lists every JSDoc and KDocs declaration found in the
currently active file, with:
  - Live filter by name / kind / tag
  - Click-to-navigate
  - Kind badges colour-coded
  - Refresh on file change
"""

from __future__ import annotations

import contextlib
import re
import tkinter as tk
from collections.abc import Callable
from typing import Any

from src.constants import FONT_MONO, FONT_UI
from src.i18n import _t
from src.theme.manager import ThemeManager


class DocsPanel(tk.Frame):
    """Sidebar panel listing all JSDoc + KDocs annotations."""

    def __init__(
        self,
        parent: tk.Widget,
        tmgr: ThemeManager,
        get_editor: Callable[[], Any | None],
        on_goto: Callable[[int], None] | None = None,
    ) -> None:
        """Cria o painel de documentação do editor.

        Args:
            parent: Widget pai Tkinter.
            config: Instância de ConfigManager.
        """
        t = tmgr.theme
        super().__init__(parent, bg=t.sidebar_bg)
        self._tmgr = tmgr
        self._get_editor = get_editor
        self._on_goto = on_goto
        self._entries: list[dict] = []
        self._filter_job: str | None = None

        self._build()

    def _build(self) -> None:
        """Constrói os widgets e layout interno do componente."""
        t = self._tmgr.theme

        for child in self.winfo_children():
            with contextlib.suppress(tk.TclError):
                child.destroy()

        hdr = tk.Frame(self, bg=t.activity_bg)
        hdr.pack(fill="x")
        tk.Label(
            hdr,
            text=_t("hc.docs_panel.docs"),
            bg=t.activity_bg,
            fg=t.fg,
            font=(FONT_UI, 10, "bold"),
        ).pack(side="left", pady=8, padx=4)

        refresh_btn = tk.Label(
            hdr,
            text="↺",
            bg=t.activity_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 12),
            cursor="hand2",
            padx=6,
        )
        refresh_btn.pack(side="right", pady=6)
        refresh_btn.bind("<Button-1>", lambda _: self.refresh())
        refresh_btn.bind("<Enter>", lambda _,
                         w=refresh_btn: w.config(fg=t.accent))
        refresh_btn.bind("<Leave>", lambda _,
                         w=refresh_btn: w.config(fg=t.fg_muted))

        tk.Frame(self, bg=getattr(t, "border", "#3d4455"),
                 height=1).pack(fill="x")

        fbar = tk.Frame(self, bg=t.sidebar_bg, pady=4)
        fbar.pack(fill="x", padx=6)

        self._filter_var = tk.StringVar()
        self._filter_var.trace_add("write", self._on_filter_change)
        filter_entry = tk.Entry(
            fbar,
            textvariable=self._filter_var,
            bg=t.editor_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            font=(FONT_UI, 9),
            bd=0,
        )
        filter_entry.pack(fill="x", ipady=4, padx=2)
        filter_entry.insert(0, "")

        def _on_focus_in(_):
            if filter_entry.get() == "filter...":
                filter_entry.delete(0, "end")
                filter_entry.config(fg=t.fg)

        def _on_focus_out(_):
            if not filter_entry.get():
                filter_entry.insert(0, "filter...")
                filter_entry.config(fg=t.fg_muted)

        filter_entry.insert(0, "filter...")
        filter_entry.config(fg=t.fg_muted)
        filter_entry.bind("<FocusIn>", _on_focus_in)
        filter_entry.bind("<FocusOut>", _on_focus_out)
        filter_entry.bind(
            "<Escape>",
            lambda _: (filter_entry.delete(0, "end"), self._apply_filter("")),
        )
        self._filter_entry = filter_entry

        toggle_row = tk.Frame(self, bg=t.sidebar_bg)
        toggle_row.pack(fill="x", padx=6, pady=(0, 4))

        self._show_jsdoc = tk.BooleanVar(value=True)
        self._show_kdocs = tk.BooleanVar(value=True)

        for var, label, _ in (
            (self._show_jsdoc, _t("hc.docs_panel.jsdoc"), "#89b4fa"),
            (self._show_kdocs, "KDocs", "#a6e3a1"),
        ):
            btn = tk.Checkbutton(
                toggle_row,
                variable=var,
                text=label,
                bg=t.sidebar_bg,
                fg=t.fg,
                selectcolor=t.sel_bg,
                activebackground=t.sidebar_bg,
                activeforeground=t.fg,
                font=(FONT_UI, 8),
                relief="flat",
                command=self._render_list,
            )
            btn.pack(side="left")

        tk.Frame(self, bg=getattr(t, "border", "#3d4455"),
                 height=1).pack(fill="x")

        list_frame = tk.Frame(self, bg=t.sidebar_bg)
        list_frame.pack(fill="both", expand=True)

        self._canvas = tk.Canvas(
            list_frame, bg=t.sidebar_bg, highlightthickness=0)
        vsb = tk.Scrollbar(list_frame, orient="vertical",
                           command=self._canvas.yview)
        self._canvas.config(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self._canvas.pack(fill="both", expand=True)

        self._list_inner = tk.Frame(self._canvas, bg=t.sidebar_bg)
        self._list_win_id = self._canvas.create_window(
            (0, 0), window=self._list_inner, anchor="nw"
        )

        self._list_inner.bind("<Configure>", self._on_list_configure)
        self._canvas.bind("<Configure>", self._on_canvas_configure)

        for ev in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self._canvas.bind(ev, self._on_wheel)
            self._list_inner.bind(ev, self._on_wheel)

        self._active_tip_row: tk.Frame | None = None

        def _hide_any_tip(_e=None):
            r = self._active_tip_row
            if r is None:
                return
            tip = getattr(r, "_tip", None)
            if tip:
                with contextlib.suppress(tk.TclError):
                    if tip.winfo_exists():
                        tip.destroy()
            r._tip = None
            self._active_tip_row = None

        self._hide_any_tip = _hide_any_tip
        for ev in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self._canvas.bind(ev, _hide_any_tip, add="+")
        self.bind("<FocusOut>", _hide_any_tip, add="+")

        tk.Label(
            self._list_inner,
            text=_t("ui.no_kdocs"),
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
            justify="center",
        )

    def _on_list_configure(self, _):
        """Trata o evento 'list configure'.

        Args:
            _: Parâmetro da operação.
        """
        self._canvas.config(scrollregion=self._canvas.bbox("all"))

    def _on_canvas_configure(self, _):
        """Trata o evento 'canvas configure'.

        Args:
            _: Parâmetro da operação.
        """
        self._canvas.itemconfig(
            self._list_win_id, width=self._canvas.winfo_width())

    def _on_wheel(self, e):
        """Trata o evento 'wheel'.

        Args:
            e: Evento Tkinter.
        """
        delta = -1 * (e.delta // 120) if e.delta else (1 if e.num == 5 else -1)
        self._canvas.yview_scroll(delta, "units")

    def _on_filter_change(self, *_):
        """Trata o evento 'filter change'."""
        if self._filter_job:
            with contextlib.suppress(tk.TclError):
                self.after_cancel(self._filter_job)
        self._filter_job = self.after(
            150, lambda: self._apply_filter(self._filter_var.get())
        )

    def _apply_filter(self, query: str) -> None:
        """Filtra os itens de documentação conforme o texto de consulta."""
        self._filter_job = None
        if query == "filter...":
            query = ""
        self._render_list(query.lower().strip())

    def refresh(self) -> None:
        """Re-parse the active editor file and redraw the list."""
        editor = self._get_editor()
        if editor is None:
            self._entries = []
            self._render_list()
            return

        content = editor.text.get("1.0", "end-1c")
        self._entries = []

        try:
            from src.editor.kdocs import JSDocParser

            _fname = (
                str(editor.path)
                if getattr(editor, "path", None)
                else f"untitled.{getattr(editor, 'lang', 'txt') or 'txt'}"
            )
            docs = JSDocParser.parse_file(content, _fname)
            for sym, doc in docs.items():
                if sym.startswith("__"):
                    continue

                display = re.sub(r"__line_\d+$", "", sym)
                tags = doc.get("tags", {})
                kind = "function"
                if tags.get("class") or tags.get("constructor"):
                    kind = "class"
                elif tags.get("typedef") or tags.get("interface"):
                    kind = "type"
                elif tags.get("type"):
                    kind = "const"
                elif tags.get("module") or tags.get("namespace"):
                    kind = "module"
                params = [
                    p.get("name", "") for p in tags.get("param", tags.get("arg", []))
                ]
                preview = ""
                if params:
                    preview = f"({', '.join(params[:4])})"
                self._entries.append(
                    {
                        "source": "jsdoc",
                        "kind": kind,
                        "name": display,
                        "line": doc.get("_symbol_line", 0),
                        "color": "#89b4fa",
                        "preview": preview,
                        "desc": (doc.get("description") or "")[:80],
                    }
                )
        except (KeyError, AttributeError, TypeError):
            pass

        try:
            from src.editor.kdocs import KDocsParser

            anns = KDocsParser.parse(content)
            for ann in anns:
                symbols_str = ", ".join(ann.symbols) if ann.symbols else ""
                name = symbols_str or ann.content[:40] or f"@kdocs:{ann.kind}"
                self._entries.append(
                    {
                        "source": "kdocs",
                        "kind": ann.kind,
                        "name": name,
                        "line": ann.line,
                        "color": ann.color,
                        "preview": f"@kdocs:{ann.kind}",
                        "desc": ann.content[:80],
                        "assignee": ann.assignee,
                        "priority": ann.priority,
                        "tags": ann.tags,
                    }
                )
        except (AttributeError, TypeError):
            pass

        self._entries.sort(key=lambda e: e.get("line", 0))
        self._render_list()

    def _render_list(self, query: str = "") -> None:
        """Executa a operação 'render list'.

        Args:
            query: Parâmetro da operação.
        """
        t = self._tmgr.theme

        for child in list(self._list_inner.winfo_children()):
            child.destroy()

        show_js = self._show_jsdoc.get()
        show_kdocs = self._show_kdocs.get()

        visible = []
        for entry in self._entries:
            if entry["source"] == "jsdoc" and not show_js:
                continue
            if entry["source"] == "kdocs" and not show_kdocs:
                continue
            if query:
                haystack = (
                    entry["name"]
                    + " "
                    + entry.get("kind", "")
                    + " "
                    + entry.get("desc", "")
                    + " "
                    + " ".join(entry.get("tags", []))
                    + " "
                    + entry.get("assignee", "")
                ).lower()
                if query not in haystack:
                    continue
            visible.append(entry)

        if not visible:
            lbl = tk.Label(
                self._list_inner,
                text=_t("hc.docs_panel.no_results") if query else _t(
                    "ui.no_kdocs"),
                bg=t.sidebar_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 9),
                justify="center",
                pady=20,
            )
            lbl.pack(fill="x", padx=8)
            return

        prev_source = None
        for entry in visible:
            if entry["source"] != prev_source:
                label = (
                    _t("hc.docs_panel.jsdoc") if entry["source"] == "jsdoc" else "KDocs"
                )
                sec = tk.Frame(self._list_inner, bg=t.sidebar_bg)
                sec.pack(fill="x", padx=6, pady=(8, 2))
                tk.Label(
                    sec,
                    text=label.upper(),
                    bg=t.sidebar_bg,
                    fg=t.fg_muted,
                    font=(FONT_UI, 7, "bold"),
                ).pack(side="left")
                tk.Frame(sec, bg=getattr(t, "border", "#3d4455"), height=1).pack(
                    side="left", fill="x", expand=True, padx=4
                )
                prev_source = entry["source"]

            self._make_row(entry, t)

    def _make_row(self, entry: dict, t: Any) -> None:
        """Render one row for a single declaration."""
        row = tk.Frame(self._list_inner, bg=t.sidebar_bg, cursor="hand2")
        row.pack(fill="x", padx=4, pady=1)

        color = entry.get("color", "#cdd6f4")
        kind = entry.get("kind", "")
        name = entry.get("name", "-")
        line = entry.get("line", 0)
        preview = entry.get("preview", "")
        desc = entry.get("desc", "")

        badge = tk.Label(
            row,
            text=f" {kind[:6]} ",
            bg=color,
            fg="#1e1e2e",
            font=(FONT_UI, 7, "bold"),
            padx=2,
            pady=0,
        )
        badge.pack(side="left", padx=(4, 3), pady=3)

        name_str = name + (preview if preview else "")
        name_lbl = tk.Label(
            row,
            text=name_str,
            bg=t.sidebar_bg,
            fg=t.fg,
            font=(FONT_MONO, 9),
            anchor="w",
        )
        name_lbl.pack(side="left", fill="x", expand=True, padx=2)

        line_lbl = tk.Label(
            row,
            text=f":{line}  " if line else "",
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            anchor="e",
        )
        line_lbl.pack(side="right")

        def _enter(_, bg=t.sel_bg):
            for w in (row, badge, name_lbl, line_lbl):
                w.config(bg=bg)

        def _leave(_, bg=t.sidebar_bg, bc=color):
            for w in (row, name_lbl, line_lbl):
                w.config(bg=bg)
            badge.config(bg=bc)

        def _click(_, ln=line):

            if hasattr(row, "_tip") and row._tip and row._tip.winfo_exists():
                row._tip.destroy()
                row._tip = None
            if ln and self._on_goto:
                self._on_goto(ln)
            elif ln:
                editor = self._get_editor()
                if editor:
                    editor.goto_line(ln)

        for w in (row, badge, name_lbl, line_lbl):
            w.bind("<Enter>", _enter)
            w.bind("<Leave>", _leave)
            w.bind("<Button-1>", _click)

        if desc:

            def _show_tip(_, r=row, d=desc):

                self._hide_any_tip()

                with contextlib.suppress(tk.TclError):
                    r._tip = tk.Toplevel(r)
                    r._tip.wm_overrideredirect(True)

                    r._tip.bind(
                        "<FocusOut>", lambda e=None: _hide_tip(None, r))

                    r._tip.geometry("+9999+9999")
                    frm = tk.Frame(
                        r._tip,
                        bg=getattr(t, "floating_bg", t.editor_bg),
                        padx=8,
                        pady=5,
                    )
                    frm.pack()
                    tk.Label(
                        frm,
                        text=d,
                        bg=frm["bg"],
                        fg=t.fg,
                        font=(FONT_UI, 8),
                        wraplength=320,
                        justify="left",
                    ).pack()

                    r._tip.update_idletasks()
                    x = row.winfo_rootx() + row.winfo_width() + 4
                    y = row.winfo_rooty()
                    sw = row.winfo_screenwidth()
                    if x + r._tip.winfo_reqwidth() > sw - 8:
                        x = row.winfo_rootx() - r._tip.winfo_reqwidth() - 4
                    r._tip.geometry(f"+{x}+{y}")

                    self._active_tip_row = r

                    r._tip.after(3000, lambda: _hide_tip(None, r))

            def _hide_tip(_, r=row):
                tip = getattr(r, "_tip", None)
                if tip:
                    with contextlib.suppress(tk.TclError):
                        if tip.winfo_exists():
                            tip.destroy()
                r._tip = None
                if self._active_tip_row is r:
                    self._active_tip_row = None

            def _enhanced_enter(e):
                _enter(e)
                _show_tip(e)

            def _enhanced_leave(e):
                _leave(e)
                _hide_tip(e)

            row.bind("<Enter>", _enhanced_enter)
            row.bind("<Leave>", _enhanced_leave)

    def apply_theme(self, tmgr: ThemeManager) -> None:
        """Executa a operação 'apply theme'.

        Args:
            tmgr: Parâmetro da operação.
        """
        self._tmgr = tmgr
        for child in self.winfo_children():
            with contextlib.suppress(tk.TclError):
                child.destroy()
        self._build()
        self.refresh()
