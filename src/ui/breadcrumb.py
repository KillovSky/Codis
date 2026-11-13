"""Codis - BreadCrumb interativo com dropdown de navegação de arquivos."""

from __future__ import annotations

import contextlib
import logging
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from src.constants import FONT_UI

if TYPE_CHECKING:
    from src.theme.base import Theme
    from src.theme.manager import ThemeManager

logger = logging.getLogger("Codis")

_ICONS: dict[str, str] = {
    ".py": "py",
    ".js": "🟨",
    ".ts": "🔷",
    ".jsx": "⚛",
    ".tsx": "⚛",
    ".html": "htm",
    ".css": "css",
    ".scss": "css",
    ".json": "{}",
    ".md": "md",
    ".txt": "txt",
    ".rs": "rs",
    ".go": "go",
    ".java": "java",
    ".cpp": "yml",
    ".c": "yml",
    ".h": "yml",
    ".sh": "📜",
    ".yaml": "yml",
    ".toml": "yml",
    ".sql": "🗄",
    ".xml": "📰",
    ".env": "🔒",
    ".png": "🖼",
    ".jpg": "🖼",
    ".svg": "🖼",
    ".mp3": "🎵",
    ".mp4": "🎬",
    ".pdf": "📋",
    ".zip": "🗜",
}
_SKIP = {
    "__pycache__",
    ".codis_trash",
    ".git",
    "node_modules",
    ".venv",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
    "dist",
    "build",
}


def _file_icon(p: Path) -> str:
    return "📁" if p.is_dir() else _ICONS.get(p.suffix.lower(), "txt")


class BreadCrumb(tk.Frame):
    """
    Barra de breadcrumb estilo VSCode totalmente interativa.

    Cada segmento é clicável e abre um dropdown com os filhos/irmãos
    daquele nível, permitindo navegar por todo o projeto sem o explorador.
    """

    def __init__(
        self,
        parent: tk.Widget,
        tmgr: ThemeManager,
        on_goto: Callable | None = None,
        on_open: Callable[[Path], None] | None = None,
    ) -> None:
        """Inicializa o widget de breadcrumb no container ``parent``."""
        t = tmgr.theme
        super().__init__(parent, bg=t.breadcrumb_bg, height=22)
        self.pack_propagate(False)
        self._tmgr = tmgr
        self._on_goto = on_goto
        self._on_open = on_open
        self._path: Path | None = None
        self._symbol: str | None = None
        self._popup: tk.Toplevel | None = None

    def set_path(self, path: Path | None, symbol: str | None = None) -> None:
        """Atualiza o breadcrumb para o caminho de arquivo ``path``.

        Evita rebuild se path e symbol não mudaram (navegação dentro do mesmo arquivo).
        """
        if path == self._path and symbol == self._symbol:
            return
        self._path = path
        self._symbol = symbol
        self._rebuild(symbol)

    def apply_theme(self, t: Theme) -> None:
        """Aplica o tema ``t`` ao widget de breadcrumb."""
        self.config(bg=t.breadcrumb_bg)
        self._rebuild()

    def _rebuild(self, symbol: str | None = None) -> None:
        self._close_popup()
        for w in self.winfo_children():
            with contextlib.suppress(tk.TclError):
                w.destroy()
        if not self._path:
            return

        t = self._tmgr.theme
        parts = list(self._path.parts)

        if len(parts) > 5:
            tk.Label(
                self,
                text="...",
                bg=t.breadcrumb_bg,
                fg=t.breadcrumb_sep,
                font=(FONT_UI, 9),
                padx=2,
            ).pack(side="left")
            tk.Label(
                self,
                text=" ] ",
                bg=t.breadcrumb_bg,
                fg=t.breadcrumb_sep,
                font=(FONT_UI, 9),
            ).pack(side="left")
            parts = parts[-4:]

        all_parts = list(self._path.parts)
        visible_start = len(all_parts) - len(parts)

        for i, part in enumerate(parts):
            abs_idx = visible_start + i
            partial = Path(*all_parts[: abs_idx + 1])
            is_last = i == len(parts) - 1
            fg = t.breadcrumb_active if is_last else t.breadcrumb_fg

            lbl = tk.Label(
                self,
                text=part,
                bg=t.breadcrumb_bg,
                fg=fg,
                font=(FONT_UI, 9),
                cursor="hand2",
                padx=4,
            )
            lbl.pack(side="left")

            lbl.bind(
                "<Enter>",
                lambda e, l_=lbl: l_.config(
                    fg=t.breadcrumb_active, bg=getattr(
                        t, "breadcrumb_hover", t.sel_bg)
                ),
            )
            lbl.bind(
                "<Leave>", lambda e, l_=lbl, f=fg: l_.config(
                    fg=f, bg=t.breadcrumb_bg)
            )
            lbl.bind(
                "<Button-1>",
                lambda e, p=partial, last=is_last: self._show_dropdown(
                    e, p, is_last=last
                ),
            )

            if not is_last or symbol:
                tk.Label(
                    self,
                    text=" ] ",
                    bg=t.breadcrumb_bg,
                    fg=t.breadcrumb_sep,
                    font=(FONT_UI, 9),
                ).pack(side="left")

        if symbol:
            sym = tk.Label(
                self,
                text=f"⚙ {symbol}",
                bg=t.breadcrumb_bg,
                fg=t.breadcrumb_active,
                font=(FONT_UI, 9, "italic"),
                cursor="hand2",
                padx=4,
            )
            sym.pack(side="left")

    def _show_dropdown(
        self, event: tk.Event, path: Path, is_last: bool = False
    ) -> None:
        self._close_popup()
        target = path.parent if is_last else path
        if not target.is_dir():
            return

        try:
            entries = sorted(
                [
                    e
                    for e in target.iterdir()
                    if e.name not in _SKIP and not e.name.startswith(".")
                ],
                key=lambda e: (e.is_file(), e.name.lower()),
            )
        except PermissionError:
            return
        if not entries:
            return

        t = self._tmgr.theme
        wx = event.widget.winfo_rootx()
        wy = event.widget.winfo_rooty() + event.widget.winfo_height()

        popup = tk.Toplevel(self)
        popup.wm_overrideredirect(True)
        popup.wm_attributes("-topmost", True)
        popup.configure(bg=t.floating_border)
        self._popup = popup

        inner = tk.Frame(popup, bg=t.floating_bg)
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        tk.Label(
            inner,
            text=f"  {target}",
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 7),
            anchor="w",
            pady=3,
        ).pack(fill="x")
        tk.Frame(inner, bg=t.border, height=1).pack(fill="x")

        row_h = 24
        n = len(entries)
        scr_h = popup.winfo_screenheight()
        max_h = min(n * row_h, int(scr_h * 0.6), 400)
        max_h = max(max_h, row_h * 2)
        max_name_len = max((len(e.name) for e in entries), default=20)
        popup_w = max(260, min(max_name_len * 8 + 60, 520))
        canvas = tk.Canvas(
            inner, bg=t.floating_bg, highlightthickness=0, width=popup_w, height=max_h
        )
        vsb = tk.Scrollbar(inner, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(fill="both", expand=True)

        lf = tk.Frame(canvas, bg=t.floating_bg)
        canvas.create_window((0, 0), window=lf, anchor="nw")
        lf.bind(
            "<Configure>", lambda e: canvas.configure(
                scrollregion=canvas.bbox("all"))
        )
        canvas.bind(
            "<MouseWheel>",
            lambda e: canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"),
        )
        canvas.bind("<Button-4>", lambda e: canvas.yview_scroll(-3, "units"))
        canvas.bind("<Button-5>", lambda e: canvas.yview_scroll(3, "units"))

        current = self._path

        for entry in entries:
            is_cur = entry == current
            row_bg = getattr(
                t, "sel_bg", t.floating_bg) if is_cur else t.floating_bg
            row_fg = t.breadcrumb_active if is_cur else t.fg

            row = tk.Frame(lf, bg=row_bg, cursor="hand2")
            row.pack(fill="x")

            icon_lbl = tk.Label(
                row,
                text=f"  {_file_icon(entry)}",
                bg=row_bg,
                fg=row_fg,
                font=(FONT_UI, 9),
                width=4,
                anchor="w",
            )
            icon_lbl.pack(side="left")

            name_lbl = tk.Label(
                row,
                text=entry.name,
                bg=row_bg,
                fg=row_fg,
                font=(FONT_UI, 9, "bold" if is_cur else "normal"),
                anchor="w",
                pady=2,
            )
            name_lbl.pack(side="left", fill="x", expand=True, padx=2)

            if entry.is_dir():
                tk.Label(
                    row, text="]", bg=row_bg, fg=t.fg_muted, font=(FONT_UI, 9), padx=6
                ).pack(side="right")

            def _click(e, p=entry):
                self._close_popup()
                if p.is_dir():
                    self._path = p / "__dir__"

                    class _Ev:
                        widget = self

                        def __init__(self_i):  # pylint: disable=no-self-argument
                            self_i.widget = self

                    ev = _Ev()
                    ev.widget.winfo_rootx = lambda: wx  # type: ignore
                    ev.widget.winfo_rooty = lambda: wy  # type: ignore
                    ev.widget.winfo_height = lambda: 0  # type: ignore
                    self._path = current
                    self._show_dropdown(ev, p, is_last=False)  # type: ignore
                elif self._on_open:
                    self._on_open(p)

            def _hover(e, r=row):
                r.config(bg=t.sel_bg)
                for c in r.winfo_children():
                    c.config(bg=t.sel_bg)

            def _leave(e, r=row, bg=row_bg):
                r.config(bg=bg)
                for c in r.winfo_children():
                    c.config(bg=bg)

            for w in (row, icon_lbl, name_lbl):
                w.bind("<Button-1>", _click)
                w.bind("<Enter>", _hover)
                w.bind("<Leave>", _leave)

        popup.update_idletasks()
        pw = popup.winfo_reqwidth()
        ph = popup.winfo_reqheight()
        sw = popup.winfo_screenwidth()
        sh = popup.winfo_screenheight()
        fx = min(wx, sw - pw - 8)
        fx = max(fx, 0)
        if wy + ph > sh - 32:
            wy_above = event.widget.winfo_rooty() - ph
            fy = max(wy_above, 0)
        else:
            fy = wy
        popup.geometry(f"+{fx}+{fy}")

        def _bind_scroll(widget):
            widget.bind(
                "<MouseWheel>",
                lambda e: canvas.yview_scroll(-1 if e.delta >
                                              0 else 1, "units"),
            )
            widget.bind(
                "<Button-4>", lambda e: canvas.yview_scroll(-3, "units"))
            widget.bind(
                "<Button-5>", lambda e: canvas.yview_scroll(3, "units"))
            for child in widget.winfo_children():
                _bind_scroll(child)

        _bind_scroll(lf)

        popup.bind("<FocusOut>", lambda _: self._close_popup())
        popup.bind("<Escape>", lambda _: self._close_popup())
        popup.after(10, popup.focus_set)

    def _close_popup(self) -> None:
        if self._popup:
            with contextlib.suppress(tk.TclError):
                self._popup.destroy()
            self._popup = None
