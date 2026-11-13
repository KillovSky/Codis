"""Paleta de comandos do Codis (Ctrl+Shift+P).

Recursos:
- Rows clicáveis com hover real (Canvas em vez de Listbox puro)
- Shortcut visível no lado direito de cada item
- Cabeçalho de modo (>, @, :) com descrição inline
- Animação de foco suave
"""

from __future__ import annotations

import contextlib
import logging
import sys
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import ttk
from typing import Any

from src.constants import FONT_UI
from src.theme.manager import ThemeManager

logger = logging.getLogger("Codis")

_CMD_ICON = "⌘" if sys.platform == "darwin" else "⌃"

_MODE_HINTS = {
    ">": ("Comandos", "Execute um comando do editor"),
    "@": ("Símbolos", "Navegue pelos símbolos do arquivo"),
    ":": ("Ir para linha", "Digite o número da linha"),
}


class CommandPalette(tk.Toplevel):
    """
    Paleta de comandos flutuante.
    """

    def __init__(
        self,
        parent: tk.Widget,
        tmgr: ThemeManager,
        commands: list[tuple[str, str, Callable]],
        recent_files: list[str],
        recent_callback: Callable[[str], None] | None = None,
        goto_line_callback: Callable[[int], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.wm_overrideredirect(True)
        self.wm_attributes("-topmost", True)
        self._tmgr = tmgr
        self._all_commands = commands
        self._recent_files = recent_files
        self._recent_callback = recent_callback
        self._goto_line_callback = goto_line_callback
        self._items: list[tuple[str, str, str, str]] = []
        self._filtered: list[int] = []
        self._filter_job: str | None = None
        self._sel_idx = 0
        self._history: list[str] = []
        self._row_widgets: list[tk.Frame] = []

        self._build()
        self._center(parent)
        self.after(50, self._entry.focus_set)
        self.bind("<Escape>", lambda _: self.destroy())
        self.bind("<FocusOut>", self._on_focus_out)

    def _center(self, parent: tk.Widget) -> None:
        pw = parent.winfo_rootx() + parent.winfo_width() // 2
        ph = parent.winfo_rooty() + parent.winfo_height() // 4
        w, h = 580, 420
        self.geometry(f"{w}x{h}+{pw - w // 2}+{ph}")

    def _build(self) -> None:
        t = self._tmgr.theme

        outer = tk.Frame(
            self,
            bg=t.floating_bg,
            highlightthickness=1,
            highlightbackground=t.floating_border,
        )
        outer.pack(fill="both", expand=True)

        input_bg = getattr(t, "editor_bg", t.floating_bg)
        entry_row = tk.Frame(outer, bg=input_bg)
        entry_row.pack(fill="x", padx=0, pady=0)

        self._mode_icon = tk.Label(
            entry_row,
            text="  >  ",
            bg=input_bg,
            fg=getattr(t, "accent", t.fg_muted),
            font=(FONT_UI, 12, "bold"),
        )
        self._mode_icon.pack(side="left", pady=8)

        self._var = tk.StringVar()
        self._var.trace_add("write", lambda *_: self._schedule_filter())
        self._entry = tk.Entry(
            entry_row,
            textvariable=self._var,
            bg=input_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            bd=0,
            font=(FONT_UI, 13),
            highlightthickness=0,
        )
        self._entry.pack(side="left", fill="x", expand=True, pady=10)

        clear_btn = tk.Label(
            entry_row,
            text="✕ ",
            bg=input_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 10),
            padx=8,
            cursor="hand2",
        )
        clear_btn.pack(side="right", pady=10)
        clear_btn.bind("<Button-1>", lambda _: self._var.set(""))

        tk.Frame(outer, bg=t.border, height=1).pack(fill="x")

        self._mode_hint = tk.Label(
            outer,
            text="  Digite '>' para comandos · '@' para símbolos · ':N' para linha",
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            anchor="w",
            pady=4,
        )
        self._mode_hint.pack(fill="x")
        tk.Frame(outer, bg=t.border, height=1).pack(fill="x")

        list_frame = tk.Frame(outer, bg=t.floating_bg)
        list_frame.pack(fill="both", expand=True)

        self._list_canvas = tk.Canvas(
            list_frame,
            bg=t.floating_bg,
            highlightthickness=0,
            bd=0,
        )
        vsb = ttk.Scrollbar(
            list_frame, orient="vertical", command=self._list_canvas.yview
        )
        self._list_canvas.config(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self._list_canvas.pack(fill="both", expand=True)

        self._rows_frame = tk.Frame(self._list_canvas, bg=t.floating_bg)
        self._rows_win = self._list_canvas.create_window(
            (0, 0), window=self._rows_frame, anchor="nw"
        )
        self._rows_frame.bind("<Configure>", self._on_rows_configure)
        self._list_canvas.bind("<Configure>", self._on_canvas_configure)
        self._list_canvas.bind("<MouseWheel>", self._on_wheel)
        self._list_canvas.bind(
            "<Button-4>", lambda _: self._list_canvas.yview_scroll(-3, "units")
        )
        self._list_canvas.bind(
            "<Button-5>", lambda _: self._list_canvas.yview_scroll(3, "units")
        )

        tk.Frame(outer, bg=t.border, height=1).pack(fill="x")
        footer = tk.Frame(outer, bg=t.floating_bg)
        footer.pack(fill="x")

        self._desc = tk.Label(
            footer,
            text="",
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            anchor="w",
            padx=10,
            pady=4,
        )
        self._desc.pack(side="left", fill="x", expand=True)

        tk.Label(
            footer,
            text="↑↓ Navegar   ↵ Executar   Esc Fechar",
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 7),
            anchor="e",
            padx=10,
        ).pack(side="right")

        self._entry.bind("<Up>", lambda _: self._nav(-1))
        self._entry.bind("<Down>", lambda _: self._nav(+1))
        self._entry.bind("<Return>", lambda _: self._accept())
        self._entry.bind("<Tab>", lambda _: self._nav(+1))

        self._load_items()
        self._filter()

    def _on_rows_configure(self, _e=None) -> None:
        self._list_canvas.config(scrollregion=self._list_canvas.bbox("all"))

    def _on_canvas_configure(self, e) -> None:
        self._list_canvas.itemconfig(self._rows_win, width=e.width)

    def _on_wheel(self, e) -> None:
        delta = -1 * (e.delta // 120) if e.delta else (1 if e.num == 5 else -1)
        self._list_canvas.yview_scroll(delta, "units")

    def _load_items(self) -> None:
        self._items = []
        for label in self._history[-5:]:
            for l_, d, cb in self._all_commands:
                if l_ == label:
                    self._items.append((l_, d, cb, "🕐"))
                    break

        for label, desc, cb in self._all_commands:
            if not any(it[0] == label for it in self._items):
                self._items.append((label, desc, cb, _CMD_ICON))

        for f in self._recent_files[:20]:
            name = Path(f).name if f else f
            cb = (
                (lambda p=f: self._recent_callback(p))
                if self._recent_callback
                else (lambda: None)
            )
            self._items.append((name, f, cb, "📄"))

    def _schedule_filter(self) -> None:
        if self._filter_job:
            with contextlib.suppress(Exception):
                self.after_cancel(self._filter_job)
        self._filter_job = self.after(40, self._filter)

    def _filter(self) -> None:
        self._filter_job = None
        query = self._var.get()
        raw_q = query.lower()

        mode = "all"
        if query.startswith(">"):
            mode = "cmd"
            raw_q = query[1:].lstrip().lower()
            self._mode_icon.config(text="  >  ")
            self._mode_hint.config(
                text="  Comandos - filtre por nome do comando")
        elif query.startswith("@"):
            mode = "symbol"
            raw_q = query[1:].lstrip().lower()
            self._mode_icon.config(text="  @  ")
            self._mode_hint.config(
                text="  Símbolos - funções, classes e variáveis")
        elif query.startswith(":"):
            mode = "line"
            raw_q = query[1:]
            self._mode_icon.config(text="  :  ")
            self._mode_hint.config(
                text="  Ir para linha - pressione Enter para navegar"
            )
        else:
            self._mode_icon.config(
                text="  >  ",
                fg=getattr(self._tmgr.theme, "accent",
                           self._tmgr.theme.fg_muted),
            )
            self._mode_hint.config(
                text="  Digite '>' comandos · '@' símbolos · ':N' linha"
            )

        results: list[int] = []
        for i, (label, _desc, _, icon) in enumerate(self._items):
            if mode == "symbol" and icon not in ("⚙", "ƒ", "ι", "λ", "◆", "⬡"):
                continue
            if mode == "cmd" and icon == "📄":
                continue
            if not raw_q or self._fuzzy(raw_q, label.lower()):
                results.append(i)

        self._filtered = results[:60]
        self._sel_idx = 0
        self._render_rows()

    def _render_rows(self) -> None:
        t = self._tmgr.theme
        for w in self._rows_frame.winfo_children():
            with contextlib.suppress(tk.TclError):
                w.destroy()
        self._row_widgets = []

        for pos, item_idx in enumerate(self._filtered):
            label, _desc_item, _, icon = self._items[item_idx]
            is_sel = pos == self._sel_idx

            bg = t.sel_bg if is_sel else t.floating_bg
            fg = t.sel_fg if is_sel else t.fg
            desc_fg = getattr(t, "fg_muted", t.fg) if not is_sel else t.sel_fg

            row = tk.Frame(self._rows_frame, bg=bg, cursor="hand2")
            row.pack(fill="x", padx=2, pady=0)

            tk.Label(
                row,
                text=f"  {icon}  ",
                bg=bg,
                fg=fg,
                font=(FONT_UI, 10),
                width=4,
                anchor="center",
            ).pack(side="left", pady=4)

            tk.Label(
                row,
                text=label,
                bg=bg,
                fg=fg,
                font=(FONT_UI, 10),
                anchor="w",
            ).pack(side="left", fill="x", expand=True, pady=4)

            short = _desc_item[:36] + \
                "…" if len(_desc_item) > 36 else _desc_item
            tk.Label(
                row,
                text=f"{short}  ",
                bg=bg,
                fg=desc_fg,
                font=(FONT_UI, 8),
                anchor="e",
            ).pack(side="right", padx=6, pady=4)

            def _enter(_e, p=pos):
                self._highlight_row(p, hover=True)

            def _leave(_e, p=pos):
                self._highlight_row(p, hover=False)

            def _click(_e, p=pos):
                self._sel_idx = p
                self._render_rows()
                self.after(80, self._accept)

            for w in row.winfo_children() + [row]:
                w.bind("<Enter>", _enter)
                w.bind("<Leave>", _leave)
                w.bind("<Button-1>", _click)

            self._row_widgets.append(row)

        if self._filtered:
            self._update_desc(self._sel_idx)

    def _highlight_row(self, pos: int, hover: bool) -> None:
        """Aplica cor de hover/seleção para uma linha."""
        t = self._tmgr.theme
        if pos >= len(self._row_widgets):
            return
        row = self._row_widgets[pos]
        is_sel = pos == self._sel_idx
        if is_sel:
            bg, fg = t.sel_bg, t.sel_fg
        elif hover:
            bg = getattr(t, "tab_hover_bg", t.sel_bg)
            fg = t.fg
        else:
            bg, fg = t.floating_bg, t.fg

        with contextlib.suppress(tk.TclError):
            row.config(bg=bg)
            for child in row.winfo_children():
                child.config(bg=bg, fg=fg)

    def _accept(self) -> None:
        query = self._var.get()

        if query.startswith(":"):
            try:
                line = int(query[1:])
                if self._goto_line_callback:
                    self._goto_line_callback(line)
            except ValueError:
                pass
            self.destroy()
            return

        if not self._filtered:
            self.destroy()
            return

        idx = (
            self._filtered[self._sel_idx]
            if self._sel_idx < len(self._filtered)
            else self._filtered[0]
        )
        label, _desc, cb, _ = self._items[idx]
        self._history.append(label)
        self.destroy()
        with contextlib.suppress(tk.TclError):
            cb()

    @classmethod
    def open(
        cls,
        parent: tk.Widget,
        tmgr: ThemeManager,
        commands: list[tuple[str, str, Callable]],
        recent_files: list[str],
        recent_callback: Callable[[str], None] | None = None,
        goto_line_callback: Callable[[int], None] | None = None,
    ) -> CommandPalette:
        return cls(
            parent, tmgr, commands, recent_files, recent_callback, goto_line_callback
        )

    @staticmethod
    def _fuzzy(query: str, label: str) -> bool:
        it = iter(label)
        return all(c in it for c in query)

    def _nav(self, direction: int) -> None:
        count = len(self._filtered)
        if count == 0:
            return
        new = max(0, min(count - 1, self._sel_idx + direction))
        self._sel_idx = new
        self._render_rows()
        if new < len(self._row_widgets):
            row = self._row_widgets[new]
            try:
                row.update_idletasks()
                y = row.winfo_y()
                h = row.winfo_height()
                ch = self._list_canvas.winfo_height()
                region = self._list_canvas.bbox("all")
                total_h = region[3] if region else 1
                frac = max(0.0, min(1.0, (y + h / 2 - ch / 2) / total_h))
                self._list_canvas.yview_moveto(frac)
            except tk.TclError:
                pass
        self._update_desc(new)

    def _update_desc(self, pos: int) -> None:
        if pos < len(self._filtered):
            item_idx = self._filtered[pos]
            _, desc, _, _ = self._items[item_idx]
            self._desc.config(text=f"  {desc}")

    def _on_focus_out(self, _e: Any) -> None:
        self.after(100, lambda: self.destroy()
                   if self.winfo_exists() else None)
