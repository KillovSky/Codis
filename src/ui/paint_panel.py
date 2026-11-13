"""Codis - Editor de Imagens Integrado / Paint.

Ferramentas:
  - Pincel suavizado com anti-aliasing e opacidade
  - Pincel pixel-art (duro, sem AA)
  - Spray / Aerógrafo
  - Borracha
  - Balde de tinta com tolerância ajustável (flood fill otimizado)
  - Linha, Retângulo (outline/fill), Elipse (outline/fill), Polígono
  - Seleção retangular: recortar, copiar, colar, mover, deletar
  - Texto com escolha de fonte e tamanho
  - Conta-gotas (clique esquerdo=cor primária, direito=secundária)
  - Zoom 6.25%-3200% com Ctrl+Scroll; pan com botão do meio/espaço+drag
  - Grid de pixels ativado com zoom >= 8x
  - Desfazer / Refazer ilimitado por memória (Ctrl+Z / Ctrl+Y)
  - Abrir / Salvar PNG, JPG, BMP, WEBP, GIF
  - Paleta de cores rápida (32 swatches) com slots customizáveis
  - Cores primária e secundária (clique direito usa cor secundária)
  - Opacidade do pincel ajustável
  - Redimensionar canvas com ou sem resize da imagem
"""

from __future__ import annotations

import contextlib
import logging
import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, ttk
from typing import Any

from src.constants import FONT_MONO, FONT_UI
from src.i18n import _t
from src.ui.grab_safe import safe_grab_set
from src.ui.paint_canvas import (  # noqa: F401
    CANVAS_H as _CANVAS_H,
    CANVAS_W as _CANVAS_W,
    HAS_PIL as _HAS_PIL,
    MAX_ZOOM as _MAX_ZOOM,
    MIN_ZOOM as _MIN_ZOOM,
    ZOOM_STEPS as _ZOOM_STEPS,
    PaintCanvas,
)

logger = logging.getLogger("Codis.Paint")

_PALETTE_COLORS: list[str] = [
    "#000000",
    "#444444",
    "#888888",
    "#bbbbbb",
    "#ffffff",
    "#ff0000",
    "#ff6600",
    "#ffcc00",
    "#ffff00",
    "#99cc00",
    "#00aa00",
    "#00cc99",
    "#0099ff",
    "#0033ff",
    "#6600cc",
    "#cc00aa",
    "#ff99aa",
    "#ffcc99",
    "#ffff99",
    "#ccffcc",
    "#99ffff",
    "#99ccff",
    "#cc99ff",
    "#ffaaff",
    "#660000",
    "#663300",
    "#996600",
    "#336600",
    "#003366",
    "#330066",
    "#3d2b1f",
    "#f5f5dc",
]

_TOOLS: list[tuple[str, str, str]] = [
    ("brush", "✏", "Pincel suave"),
    ("pixel", "⬛", "Pincel pixel"),
    ("spray", "💨", "Spray/Aerógrafo"),
    ("eraser", "◻", "Borracha"),
    ("fill", "🪣", "Balde de tinta"),
    ("line", "╱", "Linha"),
    ("rect", "▭", "Retângulo"),
    ("ellipse", "◯", "Elipse"),
    ("polygon", "⬡", "Polígono"),
    ("select", "⬚", "Seleção rect."),
    ("text", "T", "Texto"),
    ("eyedrop", "🔍", "Conta-gotas"),
    ("pan", "✋", "Panorâmica"),
]


class PaintPanel(tk.Frame):
    """Painel de edição de imagens do Codis."""

    def __init__(
        self, parent: tk.Widget, tmgr: Any, initial_path: Path | None = None
    ) -> None:
        t = tmgr.theme
        super().__init__(parent, bg=t.panel_bg)
        self._tmgr = tmgr
        self._current_path: Path | None = None
        self._tool_btns: dict[str, tk.Button] = {}

        self._build_ui(t)
        self._select_tool("brush")
        self._canvas.on_change = self._on_canvas_change
        self._canvas.on_tool_request = self._select_tool
        self._bind_shortcuts()

        if initial_path and initial_path.exists():
            self._canvas.open_image(initial_path)
            self._current_path = initial_path
            self._on_canvas_change()

    def _bind_shortcuts(self) -> None:
        """Ctrl+S/Shift+S/O/N e Ctrl+0 (ajustar) - estavam na documentação mas nunca foram ligados."""
        top = self.winfo_toplevel()
        self._shortcut_ids: list[tuple[str, str]] = []

        def inside() -> bool:
            try:
                w = self.focus_get()
                if w is None:
                    return bool(self.winfo_viewable())
                return str(w).startswith(str(self))
            except tk.TclError:
                return False

        def bind(seq: str, fn: Any) -> None:
            def handler(_e: tk.Event) -> str | None:
                if inside():
                    fn()
                    return "break"
                return None
            with contextlib.suppress(tk.TclError):
                self._shortcut_ids.append((seq, top.bind(seq, handler, add="+")))

        bind("<Control-s>", lambda: self._on_save())
        bind("<Control-S>", lambda: self._on_save_as())
        bind("<Control-o>", lambda: self._on_open())
        bind("<Control-n>", lambda: self._on_new())
        bind("<Control-0>", lambda: self._zoom_fit())
        self.bind("<Destroy>", self._unbind_shortcuts, add="+")

    def _unbind_shortcuts(self, event: tk.Event | None = None) -> None:
        if event is not None and event.widget is not self:
            return
        top = self.winfo_toplevel()
        for seq, fid in getattr(self, "_shortcut_ids", []):
            with contextlib.suppress(tk.TclError):
                top.unbind(seq, fid)
        self._shortcut_ids = []

    def _on_canvas_change(self) -> None:
        """Atualiza título/dimensões quando a imagem muda."""
        img = self._canvas.image
        if img is not None:
            pct = int(self._canvas.zoom * 100)
            self._img_info_var.set(f"{img.width}×{img.height} px\n{pct}% zoom")
        name = self._current_path.name if self._current_path else _t("paint.untitled", "Sem título")
        mark = " •" if self._canvas.modified else ""
        self._dim_var.set(f"{name}{mark}")

    def _confirm_discard(self) -> bool:
        """True se pode descartar a imagem atual (salvando antes, se o usuário quiser)."""
        if not self._canvas.modified:
            return True
        ans = messagebox.askyesnocancel(
            _t("paint.unsaved_title", "Alterações não salvas"),
            _t("paint.unsaved_msg", "Salvar as alterações antes de continuar?"), parent=self)
        if ans is None:
            return False
        if ans:
            self._on_save()
            return not self._canvas.modified
        return True

    def _build_ui(self, t: Any) -> None:
        self._build_toolbar(t)
        body = tk.Frame(self, bg=t.panel_bg)
        body.pack(fill="both", expand=True)
        self._build_sidebar(body, t)
        self._build_canvas_area(body, t)
        self._build_statusbar(t)

    def _build_toolbar(self, t: Any) -> None:
        """Toolbar com ferramentas, opções e arquivo."""
        tb = tk.Frame(self, bg=t.sidebar_bg)
        tb.pack(fill="x")
        tb1 = tk.Frame(tb, bg=t.sidebar_bg)
        tb1.pack(fill="x")
        tb2 = tk.Frame(tb, bg=t.sidebar_bg)
        tb2.pack(fill="x", pady=(0, 2))

        tool_frame = tk.Frame(tb1, bg=t.sidebar_bg)
        tool_frame.pack(side="left", padx=2)

        for tool_id, icon, tip in _TOOLS:
            btn = tk.Button(
                tool_frame,
                text=icon,
                width=3,
                height=1,
                bg=t.sidebar_bg,
                fg=t.fg,
                activebackground=t.accent,
                activeforeground="#fff",
                relief="flat",
                font=(FONT_UI, 11),
                bd=0,
                padx=2,
                pady=3,
                command=lambda tid=tool_id: self._select_tool(tid),
            )
            btn.pack(side="left", padx=1, pady=2)
            self._create_tooltip(btn, tip)
            self._tool_btns[tool_id] = btn

        opt_frame = tk.Frame(tb2, bg=t.sidebar_bg)
        opt_frame.pack(side="left", padx=4)

        self._fill_var = tk.BooleanVar(value=False)
        fill_cb = tk.Checkbutton(
            opt_frame,
            text=_t("paint.fill"),
            variable=self._fill_var,
            bg=t.sidebar_bg,
            fg=t.fg,
            selectcolor=t.panel_bg,
            activebackground=t.sidebar_bg,
            activeforeground=t.fg,
            font=(FONT_UI, 8),
            relief="flat",
            command=self._on_fill_toggle,
        )
        fill_cb.pack(anchor="w")
        self._create_tooltip(
            fill_cb, "Preencher formas (retângulo, elipse, polígono)")

        ttk.Separator(tb2, orient="vertical").pack(
            side="left", fill="y", padx=4, pady=4)

        sz_frame = tk.Frame(tb2, bg=t.sidebar_bg)
        sz_frame.pack(side="left", padx=2)
        tk.Label(
            sz_frame,
            text=_t("paint.size_label"),
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        ).pack(side="left")
        self._size_var = tk.IntVar(value=4)
        sz_spin = tk.Spinbox(
            sz_frame,
            from_=1,
            to=200,
            width=4,
            textvariable=self._size_var,
            bg=t.editor_bg,
            fg=t.fg,
            relief="flat",
            buttonbackground=t.sidebar_bg,
            font=(FONT_UI, 9),
            command=self._on_size_change,
        )
        sz_spin.pack(side="left", padx=2)
        sz_spin.bind("<Return>", lambda _: self._on_size_change())

        ttk.Separator(tb2, orient="vertical").pack(
            side="left", fill="y", padx=4, pady=4)

        op_frame = tk.Frame(tb2, bg=t.sidebar_bg)
        op_frame.pack(side="left", padx=2)
        tk.Label(
            op_frame,
            text=_t("paint.opacity_label"),
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        ).pack(side="left")
        self._opacity_var = tk.IntVar(value=100)
        op_scale = ttk.Scale(
            op_frame,
            from_=1,
            to=100,
            orient="horizontal",
            length=70,
            variable=self._opacity_var,
            command=self._on_opacity_change,
        )
        op_scale.pack(side="left", padx=2)
        self._opacity_label = tk.Label(
            op_frame, text="100%", bg=t.sidebar_bg, fg=t.fg, font=(FONT_UI, 8), width=4
        )
        self._opacity_label.pack(side="left")

        ttk.Separator(tb2, orient="vertical").pack(
            side="left", fill="y", padx=4, pady=4)

        tol_frame = tk.Frame(tb2, bg=t.sidebar_bg)
        tol_frame.pack(side="left", padx=2)
        tk.Label(
            tol_frame,
            text=_t("paint.tolerance_label"),
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        ).pack(side="left")
        self._tol_var = tk.IntVar(value=30)
        tol_spin = tk.Spinbox(
            tol_frame,
            from_=0,
            to=255,
            width=4,
            textvariable=self._tol_var,
            bg=t.editor_bg,
            fg=t.fg,
            relief="flat",
            buttonbackground=t.sidebar_bg,
            font=(FONT_UI, 9),
            command=self._on_tol_change,
        )
        tol_spin.pack(side="left")

        ttk.Separator(tb1, orient="vertical").pack(
            side="left", fill="y", padx=4, pady=4)

        file_frame = tk.Frame(tb1, bg=t.sidebar_bg)
        file_frame.pack(side="left", padx=2)
        for label, cmd in [
            ("Novo", self._on_new),
            ("Abrir", self._on_open),
            ("Salvar", self._on_save),
            ("Salvar...", self._on_save_as),
            ("↔ Resize", self._on_resize),
            ("↶", lambda: self._canvas.undo()),
            ("↷", lambda: self._canvas.redo()),
        ]:
            btn = tk.Button(
                file_frame,
                text=label,
                bg=t.panel_bg,
                fg=t.fg,
                activebackground=t.accent,
                activeforeground="#fff",
                relief="flat",
                font=(FONT_UI, 9),
                padx=6,
                pady=2,
                command=cmd,
            )
            btn.pack(side="left", padx=1, pady=3)

        zoom_r = tk.Frame(tb1, bg=t.sidebar_bg)
        zoom_r.pack(side="right", padx=4)
        tk.Button(
            zoom_r,
            text="−",
            font=(FONT_UI, 11),
            relief="flat",
            bg=t.sidebar_bg,
            fg=t.fg,
            padx=4,
            command=self._zoom_out,
        ).pack(side="left")
        self._zoom_label = tk.Label(
            zoom_r, text="100%", bg=t.sidebar_bg, fg=t.fg, font=(FONT_UI, 8), width=5
        )
        self._zoom_label.pack(side="left")
        tk.Button(
            zoom_r,
            text="+",
            font=(FONT_UI, 11),
            relief="flat",
            bg=t.sidebar_bg,
            fg=t.fg,
            padx=4,
            command=self._zoom_in,
        ).pack(side="left")
        tk.Button(
            zoom_r,
            text="⊡",
            font=(FONT_UI, 10),
            relief="flat",
            bg=t.sidebar_bg,
            fg=t.fg,
            padx=4,
            command=self._zoom_fit,
        ).pack(side="left")
        self._create_tooltip(
            zoom_r, "Zoom: Ctrl+Scroll | Botão do meio: panorâmica")

    def _build_sidebar(self, parent: tk.Widget, t: Any) -> None:
        """Sidebar esquerda: cores primária/secundária + paleta."""
        sb = tk.Frame(parent, bg=t.sidebar_bg, width=110)
        sb.pack(side="left", fill="y")
        sb.pack_propagate(False)

        clr_frame = tk.Frame(sb, bg=t.sidebar_bg)
        clr_frame.pack(pady=8)

        tk.Label(
            clr_frame,
            text=_t("paint.colors_label"),
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 7),
        ).pack()

        swatch_area = tk.Frame(clr_frame, bg=t.sidebar_bg)
        swatch_area.pack()

        self._color2_btn = tk.Label(
            swatch_area,
            bg="#ffffff",
            width=4,
            height=2,
            relief="solid",
            bd=1,
            cursor="hand2",
        )
        self._color2_btn.place(x=22, y=12)

        self._color1_btn = tk.Label(
            swatch_area,
            bg="#000000",
            width=4,
            height=2,
            relief="solid",
            bd=2,
            cursor="hand2",
        )
        self._color1_btn.place(x=6, y=2)

        swatch_area.config(width=64, height=46)

        self._color1_btn.bind(
            "<Button-1>", lambda _: self._pick_color(primary=True))
        self._color2_btn.bind(
            "<Button-1>", lambda _: self._pick_color(primary=False))
        self._color1_btn.bind("<Button-3>", lambda _: self._swap_colors())
        self._color2_btn.bind("<Button-3>", lambda _: self._swap_colors())

        swap_btn = tk.Label(
            swatch_area,
            text="⇄",
            bg=t.sidebar_bg,
            fg=t.fg,
            font=(FONT_UI, 9),
            cursor="hand2",
        )
        swap_btn.place(x=44, y=28)
        swap_btn.bind("<Button-1>", lambda _: self._swap_colors())

        tk.Label(
            clr_frame,
            text="↑esc. | ↓fund.",
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 6),
        ).pack()

        ttk.Separator(sb, orient="horizontal").pack(fill="x", padx=4, pady=4)

        tk.Label(
            sb,
            text=_t("paint.palette_label"),
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 7),
        ).pack()

        pal_frame = tk.Frame(sb, bg=t.sidebar_bg)
        pal_frame.pack(padx=4, pady=2)

        self._palette_btns: list[tk.Label] = []
        for i, c in enumerate(_PALETTE_COLORS):
            row, col = divmod(i, 4)
            btn = tk.Label(
                pal_frame, bg=c, width=2, height=1, relief="flat", bd=1, cursor="hand2"
            )
            btn.grid(row=row, column=col, padx=1, pady=1)
            btn.bind("<Button-1>", lambda _,
                     cc=c: self._set_color(cc, primary=True))
            btn.bind("<Button-3>", lambda _,
                     cc=c: self._set_color(cc, primary=False))
            btn.bind(
                "<Double-Button-1>",
                lambda _, b=btn, idx=i: self._customize_palette(b, idx),
            )
            self._palette_btns.append(btn)

        tk.Label(
            sb,
            text="2x-clique: editar cor",
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 6),
        ).pack(pady=(2, 0))

        ttk.Separator(sb, orient="horizontal").pack(fill="x", padx=4, pady=4)

        self._img_info_var = tk.StringVar(value="800×600 px")
        tk.Label(
            sb,
            textvariable=self._img_info_var,
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 7),
            wraplength=100,
        ).pack(padx=4)

    def _build_canvas_area(self, parent: tk.Widget, t: Any) -> None:
        """Área de desenho com scrollbars."""
        frame = tk.Frame(parent, bg=t.panel_bg)
        frame.pack(side="left", fill="both", expand=True)

        vsb = ttk.Scrollbar(frame, orient="vertical")
        hsb = ttk.Scrollbar(frame, orient="horizontal")
        vsb.pack(side="right", fill="y")
        hsb.pack(side="bottom", fill="x")

        self._canvas = PaintCanvas(
            frame,
            self._tmgr,
            status_cb=self._set_status,
            zoom_cb=self._on_zoom_change,
            yscrollcommand=vsb.set,
            xscrollcommand=hsb.set,
        )
        self._canvas.pack(fill="both", expand=True)
        vsb.config(command=self._canvas.yview)
        hsb.config(command=self._canvas.xview)
        self._canvas.on_color_pick = self._on_color_pick

    def _build_statusbar(self, t: Any) -> None:
        progress_bar = tk.Frame(self, bg=t.sidebar_bg, height=22)
        progress_bar.pack(fill="x")
        progress_bar.pack_propagate(False)

        self._status_var = tk.StringVar(
            value="Pronto - Ctrl+Z desfazer · Ctrl+Scroll zoom · Scroll rola · Botão do meio/Espaço: pan · Shift: reta/quadrado/círculo"
        )
        tk.Label(
            progress_bar,
            textvariable=self._status_var,
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            anchor="w",
        ).pack(side="left", padx=8)

        if not _HAS_PIL:
            tk.Label(
                progress_bar,
                text="⚠ Pillow não instalado - pip install Pillow",
                bg=t.sidebar_bg,
                fg="#FFA500",
                font=(FONT_UI, 7),
            ).pack(side="right", padx=6)

        self._dim_var = tk.StringVar(value="")
        tk.Label(
            progress_bar,
            textvariable=self._dim_var,
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        ).pack(side="right", padx=8)

    def _select_tool(self, tool_id: str) -> None:
        t = self._tmgr.theme
        for tid, btn in self._tool_btns.items():
            active = tid == tool_id
            btn.config(
                bg=t.accent if active else t.sidebar_bg,
                fg="#ffffff" if active else t.fg,
                relief="sunken" if active else "flat",
            )
        self._canvas.tool = tool_id
        label = next(
            (tip for tid, _, tip in _TOOLS if tid == tool_id), tool_id)
        self._set_status(f"Ferramenta: {label}")

    def _pick_color(self, primary: bool = True) -> None:
        current = self._canvas.color if primary else self._canvas.color2
        result = colorchooser.askcolor(
            color=current,
            title=(
                _t("paint.pick_color_primary")
                if primary
                else _t("paint.pick_color_secondary")
            ),
        )
        if result and result[1]:
            self._set_color(result[1], primary)

    def _set_color(self, color: str, primary: bool = True) -> None:
        if primary:
            self._canvas.color = color
            self._color1_btn.config(bg=color)
        else:
            self._canvas.color2 = color
            self._color2_btn.config(bg=color)

    def _swap_colors(self) -> None:
        c1 = self._canvas.color
        c2 = self._canvas.color2
        self._set_color(c2, primary=True)
        self._set_color(c1, primary=False)

    def _on_color_pick(self, color: str, primary: bool) -> None:
        self._set_color(color, primary)
        self._set_status(
            f"Cor coletada: {color} ({'primária' if primary else 'secundária'})"
        )

    def _on_fill_toggle(self) -> None:
        self._canvas.fill_shapes = self._fill_var.get()

    def _on_size_change(self) -> None:
        with contextlib.suppress(ValueError, tk.TclError):
            self._canvas.size = self._size_var.get()

    def _on_opacity_change(self, *_: Any) -> None:
        pct = self._opacity_var.get()
        self._canvas.opacity = int(pct * 255 / 100)
        self._opacity_label.config(text=f"{pct}%")

    def _on_tol_change(self) -> None:
        with contextlib.suppress(ValueError, tk.TclError):
            self._canvas.tolerance = self._tol_var.get()

    def _on_zoom_change(self, z: float) -> None:
        pct = int(round(z * 100))
        self._zoom_label.config(text=f"{pct}%")
        img = self._canvas.image
        if img is not None:
            self._img_info_var.set(f"{img.width}×{img.height} px\n{pct}% zoom")

    def _zoom_in(self) -> None:
        old = self._canvas.zoom
        self._canvas.set_zoom(next((z for z in _ZOOM_STEPS if z > old * 1.001), _MAX_ZOOM))

    def _zoom_out(self) -> None:
        old = self._canvas.zoom
        self._canvas.set_zoom(next((z for z in reversed(_ZOOM_STEPS) if z < old * 0.999), _MIN_ZOOM))

    def _zoom_fit(self) -> None:
        """Ajusta o zoom para a imagem caber na janela."""
        self._canvas.zoom_fit()

    def _customize_palette(self, btn: tk.Label, idx: int) -> None:
        current = _PALETTE_COLORS[idx]
        result = colorchooser.askcolor(
            color=current, title=f"Personalizar cor #{idx + 1}"
        )
        if result and result[1]:
            _PALETTE_COLORS[idx] = result[1]
            btn.config(bg=result[1])

    def _on_new(self) -> None:
        if not self._confirm_discard():
            return
        self._canvas.new_canvas()
        self._current_path = None
        self._set_status(_t("paint.new_done", "Canvas em branco criado."))
        self._on_canvas_change()

    def _on_open(self) -> None:
        if not self._confirm_discard():
            return
        types = [
            ("Imagens", "*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tiff *.tif"),
            ("PNG", "*.png"),
            ("JPEG", "*.jpg *.jpeg"),
            ("BMP", "*.bmp"),
            ("GIF", "*.gif"),
            ("Todos", "*.*"),
        ]
        path = filedialog.askopenfilename(title=_t("paint.open_title"), filetypes=types)
        if path:
            p = Path(path)
            if self._canvas.open_image(p):
                self._current_path = p
                self._set_status(f"Aberto: {p.name}")
                self._on_canvas_change()

    def open_path(self, p: Path) -> bool:
        """Abre ``p`` (usado por quem integra o painel)."""
        if self._canvas.open_image(p):
            self._current_path = p
            self._on_canvas_change()
            return True
        return False

    def _on_save(self) -> None:
        if self._current_path:
            if self._canvas.save_image(self._current_path):
                self._set_status(f"Salvo: {self._current_path.name}")
                self._on_canvas_change()
        else:
            self._on_save_as()

    def _on_save_as(self) -> None:
        types = [("PNG", "*.png"), ("JPEG", "*.jpg"), ("BMP", "*.bmp"), ("WebP", "*.webp")]
        path = filedialog.asksaveasfilename(title=_t("paint.save_title"), defaultextension=".png", filetypes=types)
        if path:
            p = Path(path)
            if self._canvas.save_image(p):
                self._current_path = p
                self._set_status(f"Salvo: {p.name}")
                self._on_canvas_change()

    def _on_resize(self) -> None:
        t = self._tmgr.theme
        win = tk.Toplevel(self)
        win.title(_t("paint.resize_title"))
        win.config(bg=t.panel_bg)
        win.resizable(False, False)
        safe_grab_set(win)

        img = self._canvas.image
        cur_w = img.width if img else _CANVAS_W
        cur_h = img.height if img else _CANVAS_H

        tk.Label(
            win,
            text=_t("paint.resize_label"),
            bg=t.panel_bg,
            fg=t.fg,
            font=(FONT_UI, 10),
        ).pack(padx=16, pady=(12, 4))

        row = tk.Frame(win, bg=t.panel_bg)
        row.pack(padx=16, pady=4)
        w_var = tk.StringVar(value=str(cur_w))
        h_var = tk.StringVar(value=str(cur_h))
        tk.Entry(
            row,
            textvariable=w_var,
            bg=t.editor_bg,
            fg=t.fg,
            font=(FONT_MONO, 10),
            width=6,
            relief="flat",
            insertbackground=t.cursor,
        ).pack(side="left")
        tk.Label(row, text=" × ", bg=t.panel_bg, fg=t.fg).pack(side="left")
        tk.Entry(
            row,
            textvariable=h_var,
            bg=t.editor_bg,
            fg=t.fg,
            font=(FONT_MONO, 10),
            width=6,
            relief="flat",
            insertbackground=t.cursor,
        ).pack(side="left")

        resize_img_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            win,
            text=_t("paint.resize_img"),
            variable=resize_img_var,
            bg=t.panel_bg,
            fg=t.fg,
            selectcolor=t.editor_bg,
            activebackground=t.panel_bg,
            relief="flat",
            font=(FONT_UI, 9),
        ).pack(padx=16, pady=4)

        presets = tk.Frame(win, bg=t.panel_bg)
        presets.pack(padx=16, pady=(0, 8))
        tk.Label(
            presets,
            text=_t("paint.resize_presets"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        ).pack(side="left")
        for pw, ph in [(800, 600), (1024, 768), (1920, 1080), (2560, 1440)]:
            b = tk.Label(
                presets,
                text=f"{pw}×{ph}",
                bg=t.btn_bg,
                fg=t.fg,
                font=(FONT_UI, 7),
                cursor="hand2",
                padx=4,
                pady=1,
            )
            b.pack(side="left", padx=2)
            b.bind(
                "<Button-1>",
                lambda _, _w=pw, _h=ph: (
                    w_var.set(str(_w)), h_var.set(str(_h))),
            )

        def _apply() -> None:
            try:
                nw, nh = max(1, min(20000, int(w_var.get()))), max(1, min(20000, int(h_var.get())))
            except ValueError:
                return
            self._canvas.resize_canvas(nw, nh, resize_img_var.get())
            self._on_canvas_change()
            self._set_status(f"Canvas redimensionado para {nw}×{nh} px.")
            win.destroy()

        btn_row = tk.Frame(win, bg=t.panel_bg)
        btn_row.pack(fill="x", padx=12, pady=8)
        tk.Button(
            btn_row,
            text=_t("paint.resize_apply"),
            command=_apply,
            bg=t.accent,
            fg="#fff",
            relief="flat",
            font=(FONT_UI, 10),
        ).pack(side="right", padx=4)
        tk.Button(
            btn_row,
            text=_t("paint.cancel"),
            command=win.destroy,
            bg=t.sidebar_bg,
            fg=t.fg,
            relief="flat",
            font=(FONT_UI, 10),
        ).pack(side="right")

        win.update_idletasks()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        ww, wh = win.winfo_reqwidth(), win.winfo_reqheight()
        win.geometry(f"{ww}x{wh}+{(sw - ww) // 2}+{(sh - wh) // 2}")

    def _set_status(self, msg: str) -> None:
        with contextlib.suppress(tk.TclError):
            self._status_var.set(msg)

    def _create_tooltip(self, widget: tk.Widget, text: str) -> None:
        tip: tk.Toplevel | None = None

        def _show(ev: tk.Event) -> None:
            nonlocal tip
            tip = tk.Toplevel(widget)
            tip.wm_overrideredirect(True)
            tip.wm_geometry(f"+{ev.x_root + 12}+{ev.y_root + 18}")
            tk.Label(
                tip,
                text=text,
                bg="#FFFFCC",
                fg="#333",
                relief="solid",
                bd=1,
                font=(FONT_UI, 8),
                padx=4,
                pady=2,
            ).pack()

        def _hide(_: tk.Event) -> None:
            nonlocal tip
            if tip:
                with contextlib.suppress(tk.TclError):
                    tip.destroy()
                tip = None

        widget.bind("<Enter>", _show, add="+")
        widget.bind("<Leave>", _hide, add="+")
