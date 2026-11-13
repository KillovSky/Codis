"""Codis - ThemePreviewPanel.

Painel de pré-visualização de temas estilo wizard.
Exibe uma amostra realista da UI antes de confirmar a troca.

Funcionalidades:
  - Grade de cartões com todos os temas (nome + ícone dark/light)
  - Preview ao vivo: miniatura de código, sidebar, tabbar, status bar
  - Botão de aplicar com escolha Completo / Rápido
  - Navegação por teclado (setas, Enter, Escape)
  - Filtro por nome e por polaridade (dark / light / todos)
"""

from __future__ import annotations

import contextlib
import logging
import tkinter as tk
from collections.abc import Callable
from tkinter import ttk
from typing import TYPE_CHECKING

from src.constants import FONT_MONO, FONT_UI
from src.i18n import _t
from src.theme.base import THEME_ORDER, THEMES, Theme
from src.theme.utils import hex_blend as _hex_blend
from src.ui._perf_utils import debounce_canvas

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    pass

_SAMPLE_CODE = """\
def greet(name: str) -> str:
    # Saudação personalizada
    msg = f"Hello, {name}!"
    return msg

class Counter:
    def __init__(self, start=0):
        self.value = start

    def tick(self) -> int:
        self.value += 1
        return self.value
"""

_SAMPLE_LINES = _SAMPLE_CODE.splitlines()


class _ThemeCard(tk.Frame):
    """Cartão compacto com miniatura de UI e nome do tema."""

    _W = 160
    _H = 110

    def __init__(
        self,
        parent: tk.Widget,
        theme: Theme,
        on_select: Callable[[str], None],
        on_apply: Callable[[str], None],
        *,
        active: bool = False,
    ) -> None:
        """Inicializa um card de preview de tema."""
        t = theme
        border_color = t.accent if active else t.border
        super().__init__(
            parent,
            bg=border_color,
            width=self._W + 2,
            height=self._H + 2,
            cursor="hand2",
        )
        self.pack_propagate(False)

        self._theme = theme
        self._on_select = on_select
        self._on_apply = on_apply
        self._active = active

        self._inner = tk.Frame(self, bg=t.bg, width=self._W, height=self._H)
        self._inner.place(x=1, y=1, width=self._W, height=self._H)
        self._inner.pack_propagate(False)

        self._build_thumbnail()

        label_frame = tk.Frame(self._inner, bg=t.tabbar_bg)
        label_frame.place(x=0, y=self._H - 22, width=self._W, height=22)

        tk.Label(
            label_frame,
            text=("🌙 " if t.dark else "☀ ") + t.name.replace("_", " ").title(),
            bg=t.tabbar_bg,
            fg=t.tab_act_fg if active else t.tab_in_fg,
            font=(FONT_UI, 7, "bold" if active else "normal"),
            anchor="w",
            padx=4,
        ).place(x=0, y=0, width=self._W, height=22)

        for w in self._collect_widgets(self):
            w.bind("<Button-1>", self._on_click)
            w.bind("<Double-Button-1>", self._on_double_click)

    def _collect_widgets(self, w: tk.Widget) -> list:
        result = [w]
        for child in w.winfo_children():
            result.extend(self._collect_widgets(child))
        return result

    def _build_thumbnail(self) -> None:
        t = self._theme
        h = self._H - 22

        sb = tk.Frame(self._inner, bg=t.sidebar_bg, width=28, height=h)
        sb.place(x=0, y=0, width=28, height=h)

        tk.Frame(self._inner, bg=t.activity_bg, width=8, height=h).place(
            x=0, y=0, width=8, height=h
        )

        for i, (icon, fg_) in enumerate(
            [("≡", t.fg_muted), ("⌂", t.fg_muted), ("☰", t.fg_muted)]
        ):
            tk.Label(sb, text=icon, bg=t.sidebar_bg, fg=fg_, font=(FONT_UI, 6)).place(
                x=10, y=5 + i * 16
            )

        tb_h = 14
        tb = tk.Frame(self._inner, bg=t.tabbar_bg, height=tb_h)
        tb.place(x=28, y=0, width=self._W - 28, height=tb_h)
        tk.Frame(tb, bg=t.tab_act_bg, width=38, height=tb_h).place(
            x=0, y=0, width=38, height=tb_h
        )
        tk.Label(
            tb, text="file.py", bg=t.tab_act_bg, fg=t.tab_act_fg, font=(FONT_UI, 5)
        ).place(x=2, y=2)
        tk.Label(
            tb, text="main.py", bg=t.tabbar_bg, fg=t.tab_in_fg, font=(FONT_UI, 5)
        ).place(x=42, y=2)

        ed_y = tb_h
        ed_h = h - tb_h - 10
        ed_w = self._W - 28
        ed = tk.Frame(self._inner, bg=t.editor_bg)
        ed.place(x=28, y=ed_y, width=ed_w, height=ed_h)

        gutter_w = 18
        tk.Frame(ed, bg=t.sidebar_bg, width=gutter_w, height=ed_h).place(
            x=0, y=0, width=gutter_w, height=ed_h
        )

        colors = [t.t_keyword, t.fg, t.t_comment,
                  t.t_string, t.t_func, t.t_number]
        for i in range(min(5, ed_h // 9)):
            col = colors[i % len(colors)]
            bar_w = [30, 50, 40, 55, 35][i % 5]
            tk.Frame(ed, bg=col, height=3, width=bar_w).place(
                x=gutter_w + 3, y=4 + i * 9, width=bar_w, height=3
            )
            num_fg = _hex_blend(t.sidebar_bg, t.fg_muted, 0.7)
            tk.Label(
                ed,
                text=str(i + 1),
                bg=t.sidebar_bg,
                fg=num_fg,
                font=(FONT_UI, 4),
                anchor="e",
                width=2,
            ).place(x=1, y=3 + i * 9)

        sb2 = tk.Frame(self._inner, bg=t.activity_bg, height=10)
        sb2.place(x=28, y=h - 10, width=self._W - 28, height=10)
        tk.Label(
            sb2, text="● main  Ln 1", bg=t.activity_bg, fg=t.fg_muted, font=(FONT_UI, 4)
        ).place(x=2, y=1)

    def _on_click(self, _event: tk.Event) -> None:
        self._on_select(self._theme.name)

    def _on_double_click(self, _event: tk.Event) -> None:
        self._on_apply(self._theme.name)

    def mark_active(self, active: bool) -> None:
        """Atualiza o visual de seleção."""
        t = self._theme
        border = t.accent if active else t.border
        self.config(bg=border)
        self._active = active


class _ThemePreview(tk.Frame):
    """Preview detalhado do tema selecionado."""

    def __init__(self, parent: tk.Widget) -> None:
        """Inicializa a área de preview de tema com código de exemplo."""
        super().__init__(parent, bg="#1e1e1e")
        self._theme: Theme | None = None
        self._build_placeholder()

    def _build_placeholder(self) -> None:
        tk.Label(
            self,
            text=_t("themepreview.select_hint", "← Selecione um tema"),
            bg="#1e1e1e",
            fg="#555555",
            font=(FONT_UI, 10),
        ).pack(expand=True)

    def show(self, theme: Theme) -> None:
        """Renderiza o preview para o tema fornecido."""
        self._theme = theme
        for w in self.winfo_children():
            with contextlib.suppress(tk.TclError):
                w.destroy()
        self.config(bg=theme.bg)
        self._render(theme)

    def _render(self, t: Theme) -> None:
        header = tk.Frame(self, bg=t.tabbar_bg)
        header.pack(fill="x")
        tk.Label(
            header,
            text=("🌙 " if t.dark else "☀ ") + t.name.replace("_", " ").title(),
            bg=t.tabbar_bg,
            fg=t.tab_act_fg,
            font=(FONT_UI, 11, "bold"),
            anchor="w",
            padx=10,
            pady=6,
        ).pack(fill="x")

        body = tk.Frame(self, bg=t.bg)
        body.pack(fill="both", expand=True)

        sidebar = tk.Frame(body, bg=t.sidebar_bg, width=130)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        tk.Label(
            sidebar,
            text=_t("themepreview.explorer_label", "EXPLORER"),
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 7, "bold"),
            anchor="w",
            padx=8,
            pady=4,
        ).pack(fill="x")

        for name, depth in [
            ("src/", 0),
            ("app.py", 1),
            ("config.py", 1),
            ("ui/", 0),
            ("theme.py", 1),
        ]:
            indent = depth * 10
            row = tk.Frame(sidebar, bg=t.sidebar_bg)
            row.pack(fill="x")
            if depth == 0:
                icon, fg_ = "▾", t.fg
            else:
                icon, fg_ = "  🗎", t.fg_muted
            tk.Label(
                row,
                text=icon,
                bg=t.sidebar_bg,
                fg=fg_,
                font=(FONT_UI, 8),
                padx=6 + indent,
            ).pack(side="left")
            col = t.accent if name == "app.py" else fg_
            tk.Label(row, text=name, bg=t.sidebar_bg, fg=col, font=(FONT_UI, 8)).pack(
                side="left"
            )

        ed_frame = tk.Frame(body, bg=t.editor_bg)
        ed_frame.pack(side="left", fill="both", expand=True)

        tabbar = tk.Frame(ed_frame, bg=t.tabbar_bg, height=28)
        tabbar.pack(fill="x")
        tabbar.pack_propagate(False)
        for tab, active in [("app.py", True), ("config.py", False)]:
            bg_ = t.tab_act_bg if active else t.tabbar_bg
            fg_ = t.tab_act_fg if active else t.tab_in_fg
            frm = tk.Frame(tabbar, bg=bg_)
            frm.pack(side="left")
            tk.Label(
                frm, text=tab, bg=bg_, fg=fg_, font=(FONT_UI, 8), padx=10, pady=5
            ).pack()
            if active:
                tk.Frame(frm, bg=t.accent, height=2).pack(fill="x")

        code_area = tk.Frame(ed_frame, bg=t.editor_bg)
        code_area.pack(fill="both", expand=True, padx=0, pady=0)

        gutter = tk.Frame(code_area, bg=t.sidebar_bg, width=36)
        gutter.pack(side="left", fill="y")
        gutter.pack_propagate(False)

        text_area = tk.Frame(code_area, bg=t.editor_bg)
        text_area.pack(side="left", fill="both", expand=True, padx=4)

        for i, line in enumerate(_SAMPLE_LINES[:11]):
            line_bg = _hex_blend(t.editor_bg, t.sel_bg,
                                 0.15) if i == 1 else t.editor_bg
            row = tk.Frame(text_area, bg=line_bg)
            row.pack(fill="x")

            gn = tk.Label(
                gutter,
                text=str(i + 1),
                bg=t.sidebar_bg,
                fg=t.fg_muted,
                font=(FONT_MONO, 7),
                anchor="e",
                width=3,
                pady=0,
            )
            gn.pack(fill="x", padx=(0, 4))

            stripped = line.rstrip()
            if not stripped:
                tk.Label(row, text=" ", bg=line_bg, font=(FONT_MONO, 8)).pack(
                    side="left"
                )
                continue

            indent = len(stripped) - len(stripped.lstrip())
            if indent:
                tk.Label(row, text=" " * indent, bg=line_bg, font=(FONT_MONO, 8)).pack(
                    side="left"
                )

            rest = stripped.lstrip()
            if rest.startswith("#"):
                tk.Label(
                    row,
                    text=rest,
                    bg=line_bg,
                    fg=t.t_comment,
                    font=(FONT_MONO, 8, "italic"),
                ).pack(side="left")
                continue

            tokens = rest.split()
            first = tokens[0] if tokens else ""
            if first in ("def", "class", "return"):
                tk.Label(
                    row,
                    text=first + " ",
                    bg=line_bg,
                    fg=t.t_keyword,
                    font=(FONT_MONO, 8, "bold"),
                ).pack(side="left")
                remainder = rest[len(first):]
                paren = remainder.find("(")
                colon = remainder.find(":")
                split_at = (
                    paren if paren != -1 else colon if colon != -
                    1 else len(remainder)
                )
                name_part = remainder[:split_at].strip()
                rest_part = remainder[split_at:]
                name_col = t.t_func if first == "def" else t.t_type
                if name_part:
                    tk.Label(
                        row,
                        text=name_part,
                        bg=line_bg,
                        fg=name_col,
                        font=(FONT_MONO, 8),
                    ).pack(side="left")
                if rest_part:
                    tk.Label(
                        row, text=rest_part, bg=line_bg, fg=t.fg, font=(
                            FONT_MONO, 8)
                    ).pack(side="left")
            elif "=" in rest and not rest.startswith("="):
                parts = rest.split("=", 1)
                tk.Label(
                    row, text=parts[0] + "=", bg=line_bg, fg=t.fg, font=(FONT_MONO, 8)
                ).pack(side="left")
                val = parts[1].strip() if len(parts) > 1 else ""
                val_col = (
                    t.t_string
                    if val.startswith(('f"', '"', "'"))
                    else t.t_number if val[0:1].isdigit() else t.fg
                )
                tk.Label(
                    row, text=val, bg=line_bg, fg=val_col, font=(FONT_MONO, 8)
                ).pack(side="left")
            else:
                tk.Label(row, text=rest, bg=line_bg, fg=t.fg, font=(FONT_MONO, 8)).pack(
                    side="left"
                )

        statusbar = tk.Frame(ed_frame, bg=t.activity_bg, height=20)
        statusbar.pack(fill="x", side="bottom")
        statusbar.pack_propagate(False)
        tk.Label(
            statusbar,
            text="⎇ main  Python  UTF-8  Ln 2, Col 1  ●",
            bg=t.activity_bg,
            fg=t.statusbar_fg or t.fg,
            font=(FONT_UI, 7),
            anchor="w",
            padx=8,
        ).pack(side="left", fill="y")
        tk.Label(
            statusbar,
            text=t.name.replace("_", " ").title(),
            bg=t.accent,
            fg=t.btn_primary_fg or "#ffffff",
            font=(FONT_UI, 7, "bold"),
            padx=6,
        ).pack(side="right", fill="y")

        pal_frame = tk.Frame(self, bg=t.panel_bg)
        pal_frame.pack(fill="x", pady=(0, 0))
        tk.Label(
            pal_frame,
            text=_t("themepreview.syntax_palette", "Paleta de Sintaxe"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 7),
            anchor="w",
            padx=8,
            pady=3,
        ).pack(fill="x")
        swatches = tk.Frame(pal_frame, bg=t.panel_bg)
        swatches.pack(fill="x", padx=8, pady=(0, 6))
        for label, color in [
            ("kw", t.t_keyword),
            ("str", t.t_string),
            ("num", t.t_number),
            ("fn", t.t_func),
            ("type", t.t_type),
            ("op", t.t_operator),
            ("//", t.t_comment),
            ("@", t.t_decorator),
        ]:
            col_frame = tk.Frame(swatches, bg=t.panel_bg)
            col_frame.pack(side="left", padx=3)
            tk.Frame(col_frame, bg=color, width=18, height=10).pack()
            tk.Label(
                col_frame, text=label, bg=t.panel_bg, fg=t.fg_muted, font=(
                    FONT_UI, 6)
            ).pack()


class ThemePreviewPanel(tk.Toplevel):
    """Janela wizard de seleção de tema com preview ao vivo."""

    _W = 900
    _H = 560

    def __init__(
        self,
        parent: tk.Tk,
        current_theme: Theme,
        on_full: Callable[[str], None] | None = None,
        on_fast: Callable[[str], None] | None = None,
        on_cancel: Callable[[], None] | None = None,
    ) -> None:
        """Inicializa o painel de seleção e preview de temas."""
        super().__init__(parent)
        self._parent = parent
        self._on_full = on_full
        self._on_fast = on_fast
        self._on_cancel = on_cancel
        self._selected: str = current_theme.name
        self._current: str = current_theme.name
        self._cards: dict[str, _ThemeCard] = {}
        self._filter_var = tk.StringVar()
        self._polar_var = tk.StringVar(value="all")
        self._closed = False
        self._reflow_job: str | None = None
        self._cols = 0

        self.title("Codis - Temas")
        self.resizable(True, True)
        self.attributes("-topmost", True)
        self.overrideredirect(False)

        t = current_theme
        self.configure(bg=t.bg)

        self._t = t
        self._center()
        self._build(t)
        self._select(current_theme.name, scroll=True)

        self.bind("<Escape>", lambda _: self._cancel())
        self.bind("<Return>", lambda _: self._apply_fast())
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.focus_force()

    def _center(self) -> None:
        self._parent.update_idletasks()
        px = self._parent.winfo_rootx()
        py = self._parent.winfo_rooty()
        pw = self._parent.winfo_width()
        ph = self._parent.winfo_height()
        x = px + (pw - self._W) // 2
        y = py + (ph - self._H) // 2
        self.geometry(f"{self._W}x{self._H}+{x}+{y}")

    def _build(self, t: Theme) -> None:
        hdr = tk.Frame(self, bg=t.tabbar_bg)
        hdr.pack(fill="x")

        tk.Label(
            hdr,
            text=_t("theme.select_theme", "Selecionar Tema"),
            bg=t.tabbar_bg,
            fg=t.fg,
            font=(FONT_UI, 12, "bold"),
            anchor="w",
            padx=12,
            pady=8,
        ).pack(side="left")

        tk.Button(
            hdr,
            text="✕",
            bg=t.tabbar_bg,
            fg=t.fg_muted,
            activebackground=t.btn_hover,
            activeforeground=t.fg,
            relief="flat",
            bd=0,
            padx=8,
            pady=6,
            cursor="hand2",
            font=(FONT_UI, 10),
            command=self._cancel,
        ).pack(side="right")

        filter_bar = tk.Frame(self, bg=t.sidebar_bg)
        filter_bar.pack(fill="x")

        tk.Label(
            filter_bar,
            text="🔍",
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
            padx=8,
        ).pack(side="left")

        search_entry = tk.Entry(
            filter_bar,
            textvariable=self._filter_var,
            bg=t.input_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            font=(FONT_UI, 9),
            bd=0,
        )
        search_entry.pack(side="left", fill="x",
                          expand=True, pady=4, padx=(0, 8))
        self._filter_var.trace_add("write", lambda *_: self._apply_filter())

        for label, val in [("Todos", "all"), ("🌙 Dark", "dark"), ("☀ Light", "light")]:
            tk.Radiobutton(
                filter_bar,
                text=label,
                variable=self._polar_var,
                value=val,
                bg=t.sidebar_bg,
                fg=t.fg,
                activebackground=t.sidebar_bg,
                activeforeground=t.accent,
                selectcolor=t.sidebar_bg,
                font=(FONT_UI, 8),
                relief="flat",
                bd=0,
                command=self._apply_filter,
            ).pack(side="left", padx=4)

        paned = tk.PanedWindow(
            self, orient="horizontal", bg=t.border, sashwidth=3, sashrelief="flat"
        )
        paned.pack(fill="both", expand=True)

        left = tk.Frame(paned, bg=t.sidebar_bg)
        paned.add(left, width=320, stretch="never")

        canvas_frame = tk.Frame(left, bg=t.sidebar_bg)
        canvas_frame.pack(fill="both", expand=True)

        self._canvas = tk.Canvas(
            canvas_frame, bg=t.sidebar_bg, bd=0, highlightthickness=0
        )
        vscroll = ttk.Scrollbar(
            canvas_frame, orient="vertical", command=self._canvas.yview
        )
        self._canvas.configure(yscrollcommand=vscroll.set)
        vscroll.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)

        self._card_frame = tk.Frame(self._canvas, bg=t.sidebar_bg)
        self._canvas_window = self._canvas.create_window(
            (0, 0), window=self._card_frame, anchor="nw"
        )

        debounce_canvas(
            self._canvas, self._canvas_window, ms=40, extra_widget=self._card_frame
        )
        self._canvas.bind("<Configure>", self._schedule_reflow, add="+")
        self._canvas.bind(
            "<MouseWheel>",
            lambda e: self._canvas.yview_scroll(-1 *
                                                (e.delta // 120), "units"),
        )
        self._canvas.bind(
            "<Button-4>", lambda _: self._canvas.yview_scroll(-1, "units")
        )
        self._canvas.bind(
            "<Button-5>", lambda _: self._canvas.yview_scroll(1, "units"))

        right = tk.Frame(paned, bg=t.bg)
        paned.add(right, stretch="always")

        self._preview = _ThemePreview(right)
        self._preview.pack(fill="both", expand=True)

        footer = tk.Frame(self, bg=t.panel_bg)
        footer.pack(fill="x")

        self._selected_label = tk.Label(
            footer,
            text="",
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            anchor="w",
            padx=12,
        )
        self._selected_label.pack(side="left")

        tk.Button(
            footer,
            text=_t("theme.cancel", "Cancelar"),
            bg=t.btn_bg,
            fg=t.btn_fg,
            activebackground=t.btn_hover,
            activeforeground=t.fg,
            relief="flat",
            bd=0,
            padx=12,
            pady=6,
            cursor="hand2",
            font=(FONT_UI, 9),
            command=self._cancel,
        ).pack(side="right", padx=(4, 8), pady=6)

        self._fast_btn = tk.Button(
            footer,
            text=_t("theme.apply_quick", "Aplicar Rapido"),
            bg=t.btn_bg,
            fg=t.btn_fg,
            activebackground=t.btn_hover,
            activeforeground=t.fg,
            relief="flat",
            bd=0,
            padx=12,
            pady=6,
            cursor="hand2",
            font=(FONT_UI, 9),
            command=self._apply_fast,
        )
        self._fast_btn.pack(side="right", padx=4, pady=6)

        self._full_btn = tk.Button(
            footer,
            text=_t("theme.apply_full", "✦ Aplicar Completo"),
            bg=t.accent,
            fg=t.btn_primary_fg or "#ffffff",
            activebackground=t.sel_bg,
            activeforeground=t.sel_fg,
            relief="flat",
            bd=0,
            padx=12,
            pady=6,
            cursor="hand2",
            font=(FONT_UI, 9, "bold"),
            command=self._apply_full,
        )
        self._full_btn.pack(side="right", padx=4, pady=6)

        self._populate_cards()

    def _column_count(self) -> int:
        """Calcula quantas colunas de cards cabem na largura atual do canvas.

        Cada card ocupa ``_ThemeCard._W`` mais 10 px de padding (5 de cada lado).
        Antes de a janela ser exibida a largura ainda é mínima, e então vale 2.
        """
        try:
            canvas_w = self._canvas.winfo_width()
        except tk.TclError:
            return 2
        card_w = _ThemeCard._W + 10
        return max(1, canvas_w // card_w) if canvas_w > card_w else 2

    def _schedule_reflow(self, _event: tk.Event | None = None) -> None:
        """Agenda (com debounce de 120 ms) o reflow da grade ao redimensionar o canvas."""
        if self._closed:
            return
        if self._reflow_job is not None:
            with contextlib.suppress(tk.TclError):
                self.after_cancel(self._reflow_job)
            self._reflow_job = None
        with contextlib.suppress(tk.TclError):
            self._reflow_job = self.after(120, self._run_reflow)

    def _run_reflow(self) -> None:
        """Reconstrói a grade apenas se o número de colunas mudou."""
        self._reflow_job = None
        if self._closed or self._column_count() == self._cols:
            return
        self._apply_filter()

    def _shutdown(self) -> None:
        """Marca o painel como fechado e cancela trabalho agendado."""
        self._closed = True
        if self._reflow_job is not None:
            with contextlib.suppress(tk.TclError):
                self.after_cancel(self._reflow_job)
            self._reflow_job = None

    def _populate_cards(self, filter_text: str = "", polar: str = "all") -> None:
        """Reconstrói a grade de cards aplicando o filtro atual."""
        if self._closed:
            return
        for w in self._card_frame.winfo_children():
            with contextlib.suppress(tk.TclError):
                w.destroy()
        self._cards.clear()

        col = 0
        row_ = 0
        max_cols = self._column_count()
        self._cols = max_cols

        visible = []
        for name in THEME_ORDER:
            theme = THEMES.get(name)
            if not theme:
                continue
            if filter_text and filter_text.lower() not in name.lower():
                continue
            if polar == "dark" and not theme.dark:
                continue
            if polar == "light" and theme.dark:
                continue
            visible.append(theme)

        for theme in visible:
            try:
                card = _ThemeCard(
                    self._card_frame,
                    theme,
                    on_select=self._select,
                    on_apply=self._apply_fast,
                    active=(theme.name == self._selected),
                )
            except (tk.TclError, AttributeError) as exc:
                logger.warning("Preview do tema '%s' ignorado: %s", theme.name, exc)
                continue
            card.grid(row=row_, column=col, padx=5, pady=5, sticky="nw")
            self._cards[theme.name] = card
            col += 1
            if col >= max_cols:
                col = 0
                row_ += 1

        self._card_frame.update_idletasks()

    def _apply_filter(self) -> None:
        if self._closed:
            return
        self._populate_cards(
            filter_text=self._filter_var.get(),
            polar=self._polar_var.get(),
        )
        card = self._cards.get(self._selected)
        if card:
            card.mark_active(True)

    def _select(self, name: str, scroll: bool = False) -> None:
        """Seleciona um tema e atualiza o preview."""
        theme = THEMES.get(name)
        if not theme:
            return

        prev = self._cards.get(self._selected)
        if prev:
            prev.mark_active(False)

        self._selected = name

        card = self._cards.get(name)
        if card:
            card.mark_active(True)
            if scroll:
                self._canvas.update_idletasks()
                try:
                    cy = card.winfo_y()
                    self._canvas.yview_moveto(
                        cy / max(self._card_frame.winfo_height(), 1)
                    )
                except tk.TclError:
                    pass

        try:
            self._preview.show(theme)
        except (tk.TclError, AttributeError) as exc:
            logger.warning("Falha ao renderizar o preview de '%s': %s", name, exc)

        display = name.replace("_", " ").title()
        current_marker = "  <- atual" if name == self._current else ""
        self._selected_label.config(
            text=f"Selecionado: {display}{current_marker}")

    def _apply_fast(self) -> None:
        if self._closed:
            return
        name = self._selected
        self._shutdown()
        self.destroy()
        if self._on_fast:
            self._on_fast(name)

    def _apply_full(self) -> None:
        if self._closed:
            return
        name = self._selected
        self._shutdown()
        self.destroy()
        if self._on_full:
            self._on_full(name)

    def _cancel(self) -> None:
        if self._closed:
            return
        self._shutdown()
        self.destroy()
        if self._on_cancel:
            self._on_cancel()
