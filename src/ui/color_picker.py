"""Seletor de cores integrado ao Codis.

Provê ``ColorPickerPopup``, uma janela flutuante com seleção de cor via
roda cromática, sliders HSV/RGB e campo hexadecimal, integrada ao
workflow de edição de arquivos CSS, SCSS e similares.
"""

from __future__ import annotations

import colorsys
import contextlib
import logging
import re
import tkinter as tk
from collections.abc import Callable

from src.constants import FONT_MONO, FONT_UI
from src.i18n import _t
from src.ui.grab_safe import safe_grab_set

logger = logging.getLogger("Codis")

_HEX_RE = re.compile(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")
_RGB_RE = re.compile(r"rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)")
_HSL_RE = re.compile(
    r"hsl\(\s*(\d{1,3})\s*,\s*(\d{1,3})%\s*,\s*(\d{1,3})%\s*\)")


def detect_colors_in_line(line: str) -> list[tuple[int, int, str]]:
    """Return list of (start_col, end_col, hex_color) in *line*."""
    results: list[tuple[int, int, str]] = []
    for m in _HEX_RE.finditer(line):
        hex_s = m.group(1)
        if len(hex_s) == 3:
            hex_s = "".join(c * 2 for c in hex_s)
        results.append((m.start(), m.end(), f"#{hex_s.upper()}"))
    for m in _RGB_RE.finditer(line):
        r, g, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
        results.append((m.start(), m.end(), f"#{r:02X}{g:02X}{b:02X}"))
    for m in _HSL_RE.finditer(line):
        h, s, l_ = int(m.group(1)), int(m.group(2)), int(m.group(3))
        r, g, b = _hsl_to_rgb(h, s / 100, l_ / 100)
        results.append((m.start(), m.end(), f"#{r:02X}{g:02X}{b:02X}"))
    return results


def _hsl_to_rgb(h: int, s: float, l_: float) -> tuple[int, int, int]:
    """Executa 'hsl to rgb' e retorna o resultado.

    Args:
        h: Parâmetro da operação.
        s: Parâmetro da operação.
        l: Parâmetro da operação.
    """
    r, g, b = colorsys.hls_to_rgb(h / 360, l_, s)
    return int(r * 255), int(g * 255), int(b * 255)


def _hex_to_rgb(hex_c: str) -> tuple[int, int, int]:
    """Executa 'hex to rgb' e retorna o resultado.

    Args:
        hex_c: Parâmetro da operação.
    """
    hex_c = hex_c.lstrip("#")
    if len(hex_c) == 3:
        hex_c = "".join(c * 2 for c in hex_c)
    return int(hex_c[0:2], 16), int(hex_c[2:4], 16), int(hex_c[4:6], 16)


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    """Executa 'rgb to hex' e retorna o resultado.

    Args:
        r: Parâmetro da operação.
        g: Parâmetro da operação.
        b: Parâmetro da operação.
    """
    return f"#{r:02X}{g:02X}{b:02X}"


def _rgb_to_hsl(r: int, g: int, b: int) -> tuple[int, int, int]:
    """Executa 'rgb to hsl' e retorna o resultado.

    Args:
        r: Parâmetro da operação.
        g: Parâmetro da operação.
        b: Parâmetro da operação.
    """
    h, l_, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    return round(h * 360), round(s * 100), round(l_ * 100)


class ColorPickerPopup(tk.Toplevel):
    """
    Floating color picker with:
    - Visual HSV gradient canvas
    - Hue slider
    - Opacity slider
    - Hex / RGB / HSL inputs
    - Recent colors palette
    - Insert / Copy buttons
    """

    _RECENT: list[str] = []
    _MAX_RECENT = 12

    def __init__(
        self,
        parent: tk.Widget,
        tmgr,
        initial: str = "#4a9eff",
        on_pick: Callable[[str], None] | None = None,
    ) -> None:
        """Inicializa o popup de seleção de cores."""
        t = tmgr.theme
        super().__init__(parent)
        self.title(_t("ui.color_picker"))
        self.resizable(False, False)
        self.config(bg=t.floating_bg)
        self.transient(parent)
        safe_grab_set(self)
        self.wm_attributes("-topmost", True)

        self._tmgr = tmgr
        self._on_pick = on_pick
        self._hex_var = tk.StringVar(value=initial.upper())

        try:
            r, g, b = _hex_to_rgb(initial)
        except ValueError:
            r, g, b = 74, 158, 255

        h, s, v = self._rgb_to_hsv(r, g, b)
        self._h = h
        self._s = s
        self._v = v
        self._alpha = 1.0

        self._build(t)
        self._draw_sv_canvas()
        self._draw_hue_slider()
        self._update_from_hsv()

        self.update_idletasks()
        px = parent.winfo_rootx() + parent.winfo_width() // 2
        py = parent.winfo_rooty() + parent.winfo_height() // 2
        w, h2 = self.winfo_reqwidth(), self.winfo_reqheight()
        self.geometry(f"+{px - w // 2}+{py - h2 // 2}")

    def _build(self, t) -> None:
        """Constrói os widgets e layout interno do componente.

        Args:
            t: Parâmetro da operação.
        """
        self._sv_size = 220
        sv_frame = tk.Frame(self, bg=t.floating_bg)
        sv_frame.pack(padx=12, pady=(12, 4))

        self._sv_canvas = tk.Canvas(
            sv_frame,
            width=self._sv_size,
            height=self._sv_size,
            highlightthickness=1,
            highlightbackground=t.border,
            cursor="crosshair",
        )
        self._sv_canvas.pack()
        self._sv_canvas.bind("<Button-1>", self._on_sv_click)
        self._sv_canvas.bind("<B1-Motion>", self._on_sv_click)

        hue_frame = tk.Frame(self, bg=t.floating_bg)
        hue_frame.pack(fill="x", padx=12, pady=2)
        tk.Label(
            hue_frame,
            text="H",
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            width=2,
        ).pack(side="left")
        self._hue_canvas = tk.Canvas(
            hue_frame,
            width=220,
            height=18,
            highlightthickness=1,
            highlightbackground=t.border,
            cursor="sb_h_double_arrow",
        )
        self._hue_canvas.pack(side="left", padx=4)
        self._hue_canvas.bind("<Button-1>", self._on_hue_click)
        self._hue_canvas.bind("<B1-Motion>", self._on_hue_click)

        alpha_frame = tk.Frame(self, bg=t.floating_bg)
        alpha_frame.pack(fill="x", padx=12, pady=2)
        tk.Label(
            alpha_frame,
            text="A",
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            width=2,
        ).pack(side="left")
        self._alpha_canvas = tk.Canvas(
            alpha_frame,
            width=220,
            height=18,
            highlightthickness=1,
            highlightbackground=t.border,
            cursor="sb_h_double_arrow",
        )
        self._alpha_canvas.pack(side="left", padx=4)
        self._alpha_canvas.bind("<Button-1>", self._on_alpha_click)
        self._alpha_canvas.bind("<B1-Motion>", self._on_alpha_click)

        info_row = tk.Frame(self, bg=t.floating_bg)
        info_row.pack(fill="x", padx=12, pady=6)

        self._preview = tk.Frame(
            info_row,
            width=48,
            height=48,
            bg="#4a9eff",
            highlightthickness=1,
            highlightbackground=t.border,
        )
        self._preview.pack(side="left", padx=(0, 10))
        self._preview.pack_propagate(False)

        inputs = tk.Frame(info_row, bg=t.floating_bg)
        inputs.pack(side="left", fill="both", expand=True)

        ew = {
            "bg": t.input_bg,
            "fg": t.fg,
            "insertbackground": t.cursor,
            "relief": "flat",
            "bd": 0,
            "highlightthickness": 1,
            "highlightbackground": t.border,
            "font": (FONT_MONO, 9),
        }

        hex_row = tk.Frame(inputs, bg=t.floating_bg)
        hex_row.pack(fill="x", pady=1)
        tk.Label(
            hex_row,
            text="Hex",
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            width=5,
            anchor="w",
        ).pack(side="left")
        self._hex_entry = tk.Entry(
            hex_row, textvariable=self._hex_var, width=10, **ew)
        self._hex_entry.pack(side="left")
        self._hex_entry.bind("<Return>", lambda _: self._apply_hex())
        self._hex_entry.bind("<FocusOut>", lambda _: self._apply_hex())

        self._r_var = tk.IntVar()
        self._g_var = tk.IntVar()
        self._b_var = tk.IntVar()
        rgb_row = tk.Frame(inputs, bg=t.floating_bg)
        rgb_row.pack(fill="x", pady=1)
        tk.Label(
            rgb_row,
            text="RGB",
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            width=5,
            anchor="w",
        ).pack(side="left")
        for var, w in ((self._r_var, 4), (self._g_var, 4), (self._b_var, 4)):
            e = tk.Entry(rgb_row, textvariable=var, width=w, **ew)
            e.pack(side="left", padx=1)
            e.bind("<Return>", lambda _, v=var: self._apply_rgb())
            e.bind("<FocusOut>", lambda _, v=var: self._apply_rgb())

        self._hl_var = tk.IntVar()
        self._sl_var = tk.IntVar()
        self._ll_var = tk.IntVar()
        hsl_row = tk.Frame(inputs, bg=t.floating_bg)
        hsl_row.pack(fill="x", pady=1)
        tk.Label(
            hsl_row,
            text="HSL",
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            width=5,
            anchor="w",
        ).pack(side="left")
        for var, _ in ((self._hl_var, "°"), (self._sl_var, "%"), (self._ll_var, "%")):
            e = tk.Entry(hsl_row, textvariable=var, width=4, **ew)
            e.pack(side="left", padx=1)
            e.bind("<Return>", lambda _, v=var: self._apply_hsl())
            e.bind("<FocusOut>", lambda _, v=var: self._apply_hsl())

        palette_frame = tk.Frame(self, bg=t.floating_bg)
        palette_frame.pack(fill="x", padx=12, pady=(2, 6))
        tk.Label(
            palette_frame,
            text=_t("colorpicker.recent", "Recentes"),
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        ).pack(anchor="w")
        self._palette = tk.Frame(palette_frame, bg=t.floating_bg)
        self._palette.pack(fill="x")
        self._rebuild_palette(t)

        btn_row = tk.Frame(self, bg=t.floating_bg)
        btn_row.pack(fill="x", padx=12, pady=(4, 12))

        copy_btn = tk.Label(
            btn_row,
            text=_t("colorpicker.copy", "Copiar"),
            bg=t.btn_bg,
            fg=t.fg,
            font=(FONT_UI, 9),
            cursor="hand2",
            padx=14,
            pady=4,
            relief="flat",
        )
        copy_btn.pack(side="right", padx=4)
        copy_btn.bind("<Button-1>", lambda _: self._copy())
        copy_btn.bind("<Enter>", lambda _,
                      w=copy_btn: w.config(bg=t.btn_hover))
        copy_btn.bind("<Leave>", lambda _, w=copy_btn: w.config(bg=t.btn_bg))

        insert_btn = tk.Label(
            btn_row,
            text=_t("ui.insert_color"),
            bg=t.btn_primary,
            fg="#000",
            font=(FONT_UI, 9, "bold"),
            cursor="hand2",
            padx=14,
            pady=4,
            relief="flat",
        )
        insert_btn.pack(side="right", padx=4)
        insert_btn.bind("<Button-1>", lambda _: self._insert())
        insert_btn.bind("<Enter>", lambda _,
                        w=insert_btn: w.config(bg=t.accent))
        insert_btn.bind("<Leave>", lambda _,
                        w=insert_btn: w.config(bg=t.btn_primary))

        cancel_btn = tk.Label(
            btn_row,
            text=_t("colorpicker.cancel", "Cancelar"),
            bg=t.btn_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
            cursor="hand2",
            padx=10,
            pady=4,
            relief="flat",
        )
        cancel_btn.pack(side="left", padx=4)
        cancel_btn.bind("<Button-1>", lambda _: self.destroy())

    def _draw_sv_canvas(self) -> None:
        """Draw SV gradient for current hue."""
        size = self._sv_size
        self._sv_canvas.delete("all")

        step = max(1, size // 64)
        small = size // step
        img_data: list[str] = []
        for y in range(small):
            row = []
            for x in range(small):
                s = x / small
                v = 1 - y / small
                r, g, b = (
                    round(c * 255) for c in colorsys.hsv_to_rgb(self._h / 360, s, v)
                )
                row.append(f"#{r:02X}{g:02X}{b:02X}")
            img_data.append("{" + " ".join(row) + "}")

        try:
            small_img = tk.PhotoImage(width=small, height=small)
            small_img.put("\n".join(img_data))

            self._sv_img = small_img.zoom(step, step)

            if self._sv_img.width() != size or self._sv_img.height() != size:
                self._sv_img = self._sv_img.subsample(
                    max(1, self._sv_img.width() // size),
                    max(1, self._sv_img.height() // size),
                )
            self._sv_canvas.create_image(0, 0, anchor="nw", image=self._sv_img)
            self._sv_canvas.config(width=size, height=size)
        except (tk.TclError, AttributeError):
            pass

        cx = int(self._s * size)
        cy = int((1 - self._v) * size)
        r2 = 6
        self._sv_canvas.create_oval(
            cx - r2, cy - r2, cx + r2, cy + r2, outline="white", width=2
        )
        self._sv_canvas.create_oval(
            cx - r2 + 2, cy - r2 + 2, cx + r2 - 2, cy + r2 - 2, outline="black", width=1
        )

    def _draw_hue_slider(self) -> None:
        """Renderiza a barra de seleção de matiz (hue) no canvas do seletor."""
        w, h = 220, 18
        self._hue_canvas.delete("all")
        for x in range(w):
            hue = x / w * 360
            r, g, b = (round(c * 255)
                       for c in colorsys.hsv_to_rgb(hue / 360, 1, 1))
            self._hue_canvas.create_line(
                x, 0, x, h, fill=f"#{r:02X}{g:02X}{b:02X}")

        cx = int(self._h / 360 * w)
        self._hue_canvas.create_rectangle(
            cx - 3, 0, cx + 3, h, outline="white", width=2
        )

    def _draw_alpha_slider(self) -> None:
        """Renderiza a barra de seleção de opacidade (alpha) no canvas."""
        w, h = 220, 18
        self._alpha_canvas.delete("all")

        for i in range(0, w, 8):
            for j in range(0, h, 8):
                color = "#bbb" if (i // 8 + j // 8) % 2 == 0 else "#fff"
                self._alpha_canvas.create_rectangle(
                    i, j, i + 8, j + 8, fill=color, outline=""
                )

        r, g, b = _hex_to_rgb(self._hex_var.get())
        for x in range(w):
            alpha = x / w

            cr = int(r * alpha + 255 * (1 - alpha))
            cg = int(g * alpha + 255 * (1 - alpha))
            cb = int(b * alpha + 255 * (1 - alpha))
            self._alpha_canvas.create_line(
                x, 0, x, h, fill=f"#{cr:02X}{cg:02X}{cb:02X}"
            )

        cx = int(self._alpha * w)
        self._alpha_canvas.create_rectangle(
            cx - 3, 0, cx + 3, h, outline="black", width=2
        )

    def _rebuild_palette(self, t=None) -> None:
        """Executa a operação 'rebuild palette'.

        Args:
            t: Parâmetro da operação.
        """
        if t is None:
            t = self._tmgr.theme
        for w in self._palette.winfo_children():
            w.destroy()
        if not self._RECENT:
            return
        for hex_c in self._RECENT[-self._MAX_RECENT:]:
            sw = tk.Frame(
                self._palette,
                bg=hex_c,
                width=18,
                height=18,
                cursor="hand2",
                highlightthickness=1,
                highlightbackground=t.border,
            )
            sw.pack(side="left", padx=1)
            sw.pack_propagate(False)
            sw.bind("<Button-1>", lambda _, c=hex_c: self._set_hex(c))

    def _on_sv_click(self, e) -> None:
        """Trata o evento 'sv click'.

        Args:
            e: Evento Tkinter.
        """
        size = self._sv_size
        self._s = max(0, min(1, e.x / size))
        self._v = max(0, min(1, 1 - e.y / size))
        self._update_from_hsv()

    def _on_hue_click(self, e) -> None:
        """Trata o evento 'hue click'.

        Args:
            e: Evento Tkinter.
        """
        self._h = max(0, min(359, e.x / 220 * 360))
        self._draw_sv_canvas()
        self._draw_hue_slider()
        self._update_from_hsv()

    def _on_alpha_click(self, e) -> None:
        """Trata o evento 'alpha click'.

        Args:
            e: Evento Tkinter.
        """
        self._alpha = max(0, min(1, e.x / 220))
        self._draw_alpha_slider()

    def _apply_hex(self) -> None:
        """Parseia o campo hex e aplica a cor resultante ao seletor."""
        hex_c = self._hex_var.get().strip()
        if not hex_c.startswith("#"):
            hex_c = "#" + hex_c
        try:
            r, g, b = _hex_to_rgb(hex_c)
            self._h, self._s, self._v = self._rgb_to_hsv(r, g, b)
            self._draw_sv_canvas()
            self._draw_hue_slider()
            self._update_from_hsv(update_hex=False)
        except (ValueError, tk.TclError):
            pass

    def _apply_rgb(self) -> None:
        """Lê os campos R, G, B e aplica a cor resultante ao seletor."""
        try:
            r = max(0, min(255, self._r_var.get()))
            g = max(0, min(255, self._g_var.get()))
            b = max(0, min(255, self._b_var.get()))
            hex_c = _rgb_to_hex(r, g, b)
            self._hex_var.set(hex_c)
            self._h, self._s, self._v = self._rgb_to_hsv(r, g, b)
            self._draw_sv_canvas()
            self._draw_hue_slider()
            self._update_from_hsv(update_rgb=False)
        except (ValueError, tk.TclError):
            pass

    def _apply_hsl(self) -> None:
        """Lê os campos H, S, L e aplica a cor resultante ao seletor."""
        try:
            h = self._hl_var.get()
            s = self._sl_var.get() / 100
            l_ = self._ll_var.get() / 100
            r, g, b = _hsl_to_rgb(h, s, l_)
            hex_c = _rgb_to_hex(r, g, b)
            self._hex_var.set(hex_c)
            self._h, self._s, self._v = self._rgb_to_hsv(r, g, b)
            self._draw_sv_canvas()
            self._draw_hue_slider()
            self._update_from_hsv(update_hsl=False)
        except (ValueError, tk.TclError):
            pass

    def _set_hex(self, hex_c: str) -> None:
        """Define o valor de 'hex'.

        Args:
            hex_c: Parâmetro da operação.
        """
        self._hex_var.set(hex_c.upper())
        self._apply_hex()

    def _update_from_hsv(
        self, *, update_hex=True, update_rgb=True, update_hsl=True
    ) -> None:
        """Atualiza 'from hsv'."""
        r, g, b = (
            round(c * 255) for c in colorsys.hsv_to_rgb(self._h / 360, self._s, self._v)
        )
        hex_c = _rgb_to_hex(r, g, b)

        if update_hex:
            self._hex_var.set(hex_c)
        if update_rgb:
            self._r_var.set(r)
            self._g_var.set(g)
            self._b_var.set(b)
        if update_hsl:
            h, s, l_ = _rgb_to_hsl(r, g, b)
            self._hl_var.set(h)
            self._sl_var.set(s)
            self._ll_var.set(l_)

        self._preview.config(bg=hex_c)
        with contextlib.suppress(tk.TclError):
            self._draw_alpha_slider()

    @staticmethod
    def _rgb_to_hsv(r: int, g: int, b: int) -> tuple[float, float, float]:
        """Executa 'rgb to hsv' e retorna o resultado.

        Args:
            r: Parâmetro da operação.
            g: Parâmetro da operação.
            b: Parâmetro da operação.
        """
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        return h * 360, s, v

    def _copy(self) -> None:
        """Copia os dados selecionados."""
        hex_c = self._hex_var.get()
        self.clipboard_clear()
        self.clipboard_append(hex_c)
        ColorPickerPopup._add_recent(hex_c)
        self.destroy()

    def _insert(self) -> None:
        """Insere elemento na posição indicada."""
        hex_c = self._hex_var.get()
        ColorPickerPopup._add_recent(hex_c)
        if self._on_pick:
            self._on_pick(hex_c)
        self.destroy()

    @classmethod
    def _add_recent(cls, hex_c: str) -> None:
        """Executa a operação 'add recent'.

        Args:
            hex_c: Parâmetro da operação.
        """
        if hex_c in cls._RECENT:
            cls._RECENT.remove(hex_c)
        cls._RECENT.append(hex_c)
        if len(cls._RECENT) > cls._MAX_RECENT:
            cls._RECENT.pop(0)
