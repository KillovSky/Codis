"""Codis - Widgets do Zen Player.

Todos os ícones são desenhados como vetores no ``tk.Canvas`` (polígonos,
linhas e arcos). Nada depende de fonte emoji, o que também evita a classe de
crashes do Tk/Xft com fontes coloridas.
"""

from __future__ import annotations

import contextlib
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from tkinter import filedialog, simpledialog
from typing import Any

from src.constants import FONT_UI
from src.theme.utils import alpha_blend_hex
from src.ui import zen_audio as za


@dataclass
class Palette:
    """Cores do player derivadas do tema + cor de destaque da capa."""

    bg: str = "#14141f"
    surface: str = "#1d1d2b"
    surface_hi: str = "#27273a"
    fg: str = "#e8eaf6"
    fg_dim: str = "#8b90a8"
    accent: str = "#7c4dff"
    track: str = "#2c2c40"

    @classmethod
    def from_theme(cls, theme: Any, accent: str | None = None) -> Palette:
        """Monta a paleta a partir de um objeto de tema (campos opcionais)."""
        bg = getattr(theme, "floating_bg", None) or getattr(theme, "bg", cls.bg)
        fg = getattr(theme, "fg", cls.fg)
        dim = getattr(theme, "fg_muted", cls.fg_dim)
        acc = accent or getattr(theme, "accent", cls.accent)
        try:
            return cls(
                bg=bg,
                surface=alpha_blend_hex(fg, bg, 0.06),
                surface_hi=alpha_blend_hex(fg, bg, 0.12),
                fg=fg,
                fg_dim=dim,
                accent=acc,
                track=alpha_blend_hex(fg, bg, 0.16),
            )
        except (ValueError, TypeError):
            return cls(accent=acc)


def draw_icon(c: tk.Canvas, kind: str, cx: float, cy: float, s: float, color: str,
              tag: str = "icon") -> None:
    """Desenha o ícone ``kind`` centrado em (cx, cy) com "raio" ``s``."""
    kw = {"fill": color, "outline": "", "tags": tag}
    ln = {"fill": color, "width": max(1.6, s * 0.16), "capstyle": "round",
          "joinstyle": "round", "tags": tag}
    if kind == "play":
        c.create_polygon(cx - s * 0.55, cy - s * 0.75, cx - s * 0.55, cy + s * 0.75,
                         cx + s * 0.8, cy, **kw)
    elif kind == "pause":
        w = s * 0.38
        c.create_rectangle(cx - s * 0.7, cy - s * 0.75, cx - s * 0.7 + w, cy + s * 0.75, **kw)
        c.create_rectangle(cx + s * 0.7 - w, cy - s * 0.75, cx + s * 0.7, cy + s * 0.75, **kw)
    elif kind in ("next", "prev"):
        d = 1 if kind == "next" else -1
        c.create_polygon(cx - d * s * 0.75, cy - s * 0.65, cx - d * s * 0.75, cy + s * 0.65,
                         cx + d * s * 0.45, cy, **kw)
        c.create_rectangle(cx + d * s * 0.6 - s * 0.1, cy - s * 0.65,
                           cx + d * s * 0.6 + s * 0.1, cy + s * 0.65, **kw)
    elif kind == "shuffle":
        c.create_line(cx - s, cy + s * 0.6, cx - s * 0.2, cy + s * 0.6, cx + s * 0.3, cy - s * 0.6,
                      cx + s * 0.75, cy - s * 0.6, **ln)
        c.create_line(cx - s, cy - s * 0.6, cx - s * 0.2, cy - s * 0.6, cx + s * 0.3, cy + s * 0.6,
                      cx + s * 0.75, cy + s * 0.6, **ln)
        for yy in (-0.6, 0.6):
            c.create_polygon(cx + s * 0.7, cy + s * yy - s * 0.3, cx + s * 1.05, cy + s * yy,
                             cx + s * 0.7, cy + s * yy + s * 0.3, **kw)
    elif kind in ("repeat", "repeat_one"):
        c.create_line(cx - s * 0.9, cy + s * 0.15, cx - s * 0.9, cy - s * 0.35, cx - s * 0.5, cy - s * 0.65,
                      cx + s * 0.55, cy - s * 0.65, **ln)
        c.create_line(cx + s * 0.9, cy - s * 0.15, cx + s * 0.9, cy + s * 0.35, cx + s * 0.5, cy + s * 0.65,
                      cx - s * 0.55, cy + s * 0.65, **ln)
        c.create_polygon(cx + s * 0.5, cy - s * 0.98, cx + s * 0.95, cy - s * 0.65, cx + s * 0.5, cy - s * 0.32, **kw)
        c.create_polygon(cx - s * 0.5, cy + s * 0.32, cx - s * 0.95, cy + s * 0.65, cx - s * 0.5, cy + s * 0.98, **kw)
        if kind == "repeat_one":
            c.create_text(cx, cy, text="1", fill=color, font=(FONT_UI, max(6, int(s * 0.62)), "bold"),
                          tags=tag)
    elif kind == "eq":
        for i, h in enumerate((0.55, 1.0, 0.7)):
            x = cx + (i - 1) * s * 0.78
            c.create_line(x, cy - s * 0.95, x, cy + s * 0.95, **{**ln, "width": max(1.2, s * 0.1)})
            ky = cy + s * (0.5 - h) * 1.1
            c.create_oval(x - s * 0.28, ky - s * 0.28, x + s * 0.28, ky + s * 0.28, **kw)
    elif kind == "queue":
        for i in range(3):
            y = cy + (i - 1) * s * 0.62
            c.create_line(cx - s * 0.2, y, cx + s * 0.95, y, **ln)
            c.create_oval(cx - s * 0.95, y - s * 0.12, cx - s * 0.65, y + s * 0.18, **kw)
    elif kind in ("dock", "undock"):
        c.create_rectangle(cx - s * 0.95, cy - s * 0.75, cx + s * 0.95, cy + s * 0.75,
                           outline=color, width=max(1.4, s * 0.14), tags=tag)
        y0 = cy + s * 0.1 if kind == "dock" else cy - s * 0.75
        y1 = cy + s * 0.75 if kind == "dock" else cy - s * 0.15
        c.create_rectangle(cx - s * 0.95, y0, cx + s * 0.95, y1, **kw)
    elif kind == "minimize":
        c.create_line(cx - s * 0.7, cy + s * 0.3, cx + s * 0.7, cy + s * 0.3, **ln)
    elif kind == "close":
        c.create_line(cx - s * 0.6, cy - s * 0.6, cx + s * 0.6, cy + s * 0.6, **ln)
        c.create_line(cx + s * 0.6, cy - s * 0.6, cx - s * 0.6, cy + s * 0.6, **ln)
    elif kind == "pin":
        c.create_oval(cx - s * 0.5, cy - s * 0.95, cx + s * 0.5, cy + s * 0.05, **kw)
        c.create_line(cx, cy + s * 0.05, cx, cy + s * 0.95, **ln)
    elif kind in ("volume", "mute"):
        c.create_polygon(cx - s * 0.95, cy - s * 0.3, cx - s * 0.5, cy - s * 0.3, cx - s * 0.05, cy - s * 0.75,
                         cx - s * 0.05, cy + s * 0.75, cx - s * 0.5, cy + s * 0.3, cx - s * 0.95, cy + s * 0.3,
                         **kw)
        if kind == "volume":
            c.create_arc(cx - s * 0.2, cy - s * 0.55, cx + s * 0.6, cy + s * 0.55, start=-50, extent=100,
                         style="arc", outline=color, width=max(1.4, s * 0.14), tags=tag)
            c.create_arc(cx - s * 0.25, cy - s * 0.95, cx + s * 1.0, cy + s * 0.95, start=-50, extent=100,
                         style="arc", outline=color, width=max(1.4, s * 0.14), tags=tag)
        else:
            c.create_line(cx + s * 0.3, cy - s * 0.4, cx + s * 0.95, cy + s * 0.4, **ln)
            c.create_line(cx + s * 0.95, cy - s * 0.4, cx + s * 0.3, cy + s * 0.4, **ln)
    elif kind == "plus":
        c.create_line(cx - s * 0.7, cy, cx + s * 0.7, cy, **ln)
        c.create_line(cx, cy - s * 0.7, cx, cy + s * 0.7, **ln)
    elif kind == "folder":
        c.create_polygon(cx - s, cy - s * 0.55, cx - s * 0.2, cy - s * 0.55, cx + s * 0.05, cy - s * 0.25,
                         cx + s, cy - s * 0.25, cx + s, cy + s * 0.7, cx - s, cy + s * 0.7, **kw)


class FlatButton(tk.Canvas):
    """Botão vetorial com hover/press, estado "ativo" e fundo opcional circular."""

    def __init__(self, parent: tk.Misc, kind: str, command: Callable[[], None] | None,
                 pal: Palette, size: int = 32, icon_scale: float = 0.36,
                 filled: bool = False, tooltip: str = "") -> None:
        super().__init__(parent, width=size, height=size, bg=parent.cget("bg"),
                         highlightthickness=0, bd=0, cursor="hand2")
        self.kind = kind
        self._cmd = command
        self._pal = pal
        self._size = size
        self._scale = icon_scale
        self._filled = filled
        self._active = False
        self._hover = False
        self._down = False
        self._tooltip = tooltip
        self._tip: tk.Toplevel | None = None
        self._tip_job: str | None = None
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Destroy>", lambda _e: self._hide_tip(), add="+")
        self.redraw()

    def set_kind(self, kind: str) -> None:
        """Troca o ícone."""
        if kind != self.kind:
            self.kind = kind
            self.redraw()

    def set_active(self, active: bool) -> None:
        """Marca o botão como ligado (usa a cor de destaque)."""
        if active != self._active:
            self._active = active
            self.redraw()

    def set_palette(self, pal: Palette) -> None:
        """Atualiza cores (tema ou cor da capa)."""
        self._pal = pal
        with contextlib.suppress(tk.TclError):
            self.configure(bg=self.master.cget("bg"))
        self.redraw()

    def redraw(self) -> None:
        """Redesenha o botão conforme o estado atual."""
        self.delete("all")
        s = self._size
        pal = self._pal
        if self._filled:
            fill = pal.accent if not self._down else alpha_blend_hex("#000000", pal.accent, 0.25)
            if self._hover and not self._down:
                fill = alpha_blend_hex("#ffffff", pal.accent, 0.18)
            self.create_oval(1, 1, s - 1, s - 1, fill=fill, outline="")
            color = "#ffffff"
        else:
            if self._hover or self._down:
                self.create_oval(2, 2, s - 2, s - 2,
                                 fill=pal.surface_hi if not self._down else pal.track, outline="")
            color = pal.accent if self._active else (pal.fg if self._hover else pal.fg_dim)
        draw_icon(self, self.kind, s / 2, s / 2, s * self._scale, color)

    def _on_enter(self, _e: tk.Event) -> None:
        self._hover = True
        self.redraw()
        if self._tooltip:
            self._tip_job = self.after(550, self._show_tip)

    def _on_leave(self, _e: tk.Event) -> None:
        self._hover = False
        self._down = False
        self._hide_tip()
        self.redraw()

    def _on_press(self, _e: tk.Event) -> None:
        self._down = True
        self._hide_tip()
        self.redraw()

    def _on_release(self, e: tk.Event) -> None:
        was_down = self._down
        self._down = False
        self.redraw()
        inside = 0 <= e.x <= self._size and 0 <= e.y <= self._size
        if was_down and inside and self._cmd:
            self._cmd()

    def _show_tip(self) -> None:
        self._tip_job = None
        if self._tip is not None or not self._tooltip:
            return
        with contextlib.suppress(tk.TclError):
            tip = tk.Toplevel(self)
            tip.wm_overrideredirect(True)
            tip.attributes("-topmost", True)
            tk.Label(tip, text=self._tooltip, bg=self._pal.surface_hi, fg=self._pal.fg,
                     font=(FONT_UI, 8), padx=7, pady=3, bd=0).pack()
            tip.update_idletasks()
            x = self.winfo_rootx() + self._size // 2 - tip.winfo_reqwidth() // 2
            y = self.winfo_rooty() - tip.winfo_reqheight() - 6
            if y < 0:
                y = self.winfo_rooty() + self._size + 6
            tip.geometry(f"+{max(0, x)}+{y}")
            self._tip = tip

    def _hide_tip(self) -> None:
        if self._tip_job is not None:
            with contextlib.suppress(tk.TclError, ValueError):
                self.after_cancel(self._tip_job)
            self._tip_job = None
        if self._tip is not None:
            with contextlib.suppress(tk.TclError):
                self._tip.destroy()
            self._tip = None


class SeekBar(tk.Canvas):
    """Barra horizontal arrastável (progresso ou volume) com hover e knob."""

    def __init__(self, parent: tk.Misc, pal: Palette, height: int = 18,
                 on_seek: Callable[[float], None] | None = None,
                 on_drag: Callable[[float], None] | None = None,
                 hover_text: Callable[[float], str] | None = None) -> None:
        super().__init__(parent, height=height, bg=parent.cget("bg"), highlightthickness=0,
                         bd=0, cursor="hand2")
        self._pal = pal
        self._value = 0.0
        self._buffer = 0.0
        self._dragging = False
        self._hover = False
        self._hover_x: int | None = None
        self._on_seek = on_seek
        self._on_drag = on_drag
        self._hover_text = hover_text
        self.bind("<Configure>", lambda _e: self.redraw())
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<Motion>", self._motion)
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._move)
        self.bind("<ButtonRelease-1>", self._release)

    @property
    def dragging(self) -> bool:
        """Se o usuário está arrastando o controle."""
        return self._dragging

    def set_value(self, value: float) -> None:
        """Define o valor (0..1) sem disparar callbacks."""
        value = max(0.0, min(1.0, value))
        if abs(value - self._value) > 1e-4 and not self._dragging:
            self._value = value
            self.redraw()

    def set_palette(self, pal: Palette) -> None:
        """Atualiza as cores."""
        self._pal = pal
        with contextlib.suppress(tk.TclError):
            self.configure(bg=self.master.cget("bg"))
        self.redraw()

    def _frac(self, x: int) -> float:
        w = max(self.winfo_width(), 1)
        pad = 7
        return max(0.0, min(1.0, (x - pad) / max(w - 2 * pad, 1)))

    def redraw(self) -> None:
        """Redesenha trilho, preenchimento e knob."""
        self.delete("all")
        w, h = max(self.winfo_width(), 2), max(self.winfo_height(), 2)
        pad, cy = 7, h / 2
        grow = 1 if (self._hover or self._dragging) else 0
        th = 3 + grow * 2
        x1 = w - pad
        self.create_line(pad, cy, x1, cy, fill=self._pal.track, width=th, capstyle="round")
        xv = pad + (x1 - pad) * self._value
        if xv > pad:
            self.create_line(pad, cy, xv, cy, fill=self._pal.accent, width=th, capstyle="round")
        if self._hover or self._dragging:
            r = 6
            self.create_oval(xv - r, cy - r, xv + r, cy + r, fill="#ffffff", outline=self._pal.accent, width=2)
        if self._hover_x is not None and self._hover_text and not self._dragging:
            text = self._hover_text(self._frac(self._hover_x))
            tx = min(max(self._hover_x, 22), w - 22)
            self.create_text(tx, 1, text=text, fill=self._pal.fg, anchor="n", font=(FONT_UI, 7))

    def _enter(self, _e: tk.Event) -> None:
        self._hover = True
        self.redraw()

    def _leave(self, _e: tk.Event) -> None:
        self._hover = False
        self._hover_x = None
        self.redraw()

    def _motion(self, e: tk.Event) -> None:
        self._hover_x = e.x
        self.redraw()

    def _press(self, e: tk.Event) -> None:
        self._dragging = True
        self._value = self._frac(e.x)
        self.redraw()
        if self._on_drag:
            self._on_drag(self._value)

    def _move(self, e: tk.Event) -> None:
        self._value = self._frac(e.x)
        self.redraw()
        if self._on_drag:
            self._on_drag(self._value)

    def _release(self, e: tk.Event) -> None:
        self._value = self._frac(e.x)
        self._dragging = False
        self.redraw()
        if self._on_seek:
            self._on_seek(self._value)


class EqualizerPanel(tk.Frame):
    """Equalizador de 10 bandas com presets, presets próprios e curva suave.

    O estado é persistido na configuração (``zen_eq_*``) e aplicado ao motor
    via ``player.set_equalizer``. Várias instâncias (janela, dock, popup)
    ficam sincronizadas por :meth:`sync_from_player`.
    """

    COL_W = 34
    MARGIN = 30
    H = 190

    def __init__(self, parent: tk.Misc, player: Any, cfg: Any, pal: Palette,
                 on_state_changed: Callable[[], None] | None = None) -> None:
        super().__init__(parent, bg=pal.bg)
        self._player = player
        self._cfg = cfg
        self._pal = pal
        self._on_state_changed = on_state_changed
        self._gains = za.clamp_gains(player.eq_gains)
        self._enabled = player.eq_enabled
        self._custom: dict[str, list[float]] = dict(cfg.get("zen_eq_custom", {}) or {})
        self._drag_band: int | None = None
        self._build()
        self._redraw()

    def _build(self) -> None:
        pal = self._pal
        top = tk.Frame(self, bg=pal.bg)
        top.pack(fill="x", pady=(0, 6))

        self._power = tk.Canvas(top, width=44, height=22, bg=pal.bg, highlightthickness=0,
                                cursor="hand2")
        self._power.pack(side="left")
        self._power.bind("<Button-1>", lambda _e: self.set_enabled(not self._enabled))

        self._preset_var = tk.StringVar(value="Flat")
        self._preset_text = tk.StringVar(value="Flat  ▾")
        self._preset_btn = tk.Menubutton(
            top, textvariable=self._preset_text, bg=pal.surface, fg=pal.fg,
            activebackground=pal.surface_hi, activeforeground=pal.fg, relief="flat", bd=0,
            font=(FONT_UI, 9), padx=12, pady=4, cursor="hand2", indicatoron=False, width=14,
            anchor="w")
        self._preset_btn.pack(side="left", padx=8)
        self._menu = tk.Menu(self._preset_btn, tearoff=False, bg=pal.surface, fg=pal.fg,
                             activebackground=pal.accent, activeforeground="#ffffff", bd=0,
                             relief="flat", font=(FONT_UI, 9))
        self._preset_btn.configure(menu=self._menu)

        self._delete_lbl: tk.Label | None = None
        for text, cmd in (("Resetar", self.reset), ("Salvar preset", self._save_preset),
                          ("Excluir", self._delete_preset)):
            b = tk.Label(top, text=text, bg=pal.bg, fg=pal.fg_dim, font=(FONT_UI, 9),
                         cursor="hand2", padx=6)
            if text == "Excluir":
                self._delete_lbl = b
            else:
                b.pack(side="right")
            b.bind("<Button-1>", lambda _e, c=cmd: c())
            b.bind("<Enter>", lambda _e, w=b: w.configure(fg=pal.fg))
            b.bind("<Leave>", lambda _e, w=b: w.configure(fg=pal.fg_dim))

        width = self.MARGIN + self.COL_W * len(za.EQ_BANDS_HZ)
        self._cv = tk.Canvas(self, width=width, height=self.H, bg=pal.bg, highlightthickness=0)
        self._cv.pack(fill="x")
        self._cv.bind("<Configure>", lambda _e: self._redraw())
        self._cv.bind("<ButtonPress-1>", self._press)
        self._cv.bind("<B1-Motion>", self._drag)
        self._cv.bind("<ButtonRelease-1>", lambda _e: self._release())
        self._cv.bind("<Double-Button-1>", self._reset_band)
        self._cv.bind("<MouseWheel>", self._wheel)
        self._cv.bind("<Button-4>", lambda e: self._wheel(e, 120))
        self._cv.bind("<Button-5>", lambda e: self._wheel(e, -120))
        self._rebuild_menu()

    def _rebuild_menu(self) -> None:
        self._menu.delete(0, "end")
        for name in za.EQ_PRESETS:
            self._menu.add_command(label=name, command=lambda n=name: self.apply_preset(n))
        if self._custom:
            self._menu.add_separator()
            for name in sorted(self._custom):
                self._menu.add_command(label=f"★ {name}",
                                       command=lambda n=name: self.apply_custom(n))

    def current_preset_name(self) -> str:
        """Nome do preset que coincide com os ganhos, ou ``Personalizado``."""
        match = za.match_preset(self._gains)
        if match:
            return match
        for name, gains in self._custom.items():
            if za.clamp_gains(gains) == self._gains:
                return name
        return "Personalizado"

    def sync_from_player(self) -> None:
        """Relê o estado do motor (outra instância pode ter mudado)."""
        self._gains = za.clamp_gains(self._player.eq_gains)
        self._enabled = self._player.eq_enabled
        self._redraw()

    def set_palette(self, pal: Palette) -> None:
        """Atualiza cores e redesenha."""
        self._pal = pal
        self._redraw()

    def set_enabled(self, enabled: bool) -> None:
        """Liga/desliga o equalizador."""
        self._enabled = enabled
        self._commit()

    def apply_preset(self, name: str) -> None:
        """Aplica um preset embutido."""
        self._gains = za.clamp_gains(za.EQ_PRESETS.get(name))
        self._enabled = True
        self._commit()

    def apply_custom(self, name: str) -> None:
        """Aplica um preset salvo pelo usuário."""
        self._gains = za.clamp_gains(self._custom.get(name))
        self._enabled = True
        self._commit()

    def reset(self) -> None:
        """Volta tudo para 0 dB."""
        self.apply_preset(za.DEFAULT_PRESET)

    def _commit(self) -> None:
        self._player.set_equalizer(self._gains, self._enabled)
        with contextlib.suppress(Exception):  # noqa: BLE001
            self._cfg.set("zen_eq_gains", self._gains)
            self._cfg.set("zen_eq_enabled", self._enabled)
        self._redraw()
        if self._on_state_changed:
            self._on_state_changed()

    def _save_preset(self) -> None:
        name = simpledialog.askstring("Salvar preset", "Nome do preset:", parent=self.winfo_toplevel())
        name = (name or "").strip()[:24]
        if not name or name in za.EQ_PRESETS:
            return
        self._custom[name] = list(self._gains)
        with contextlib.suppress(Exception):  # noqa: BLE001
            self._cfg.set("zen_eq_custom", self._custom)
        self._rebuild_menu()
        self._redraw()

    def _delete_preset(self) -> None:
        name = self.current_preset_name()
        if name in self._custom:
            del self._custom[name]
            with contextlib.suppress(Exception):  # noqa: BLE001
                self._cfg.set("zen_eq_custom", self._custom)
            self._rebuild_menu()
            self._redraw()

    def _band_at(self, x: int) -> int:
        return max(0, min(len(self._gains) - 1, int((x - self.MARGIN) // self._col_width())))

    def _col_width(self) -> float:
        width = max(self._cv.winfo_width(), self.MARGIN + len(self._gains))
        return (width - self.MARGIN) / len(self._gains)

    def _gain_at(self, y: int) -> float:
        top, bottom = 22, self.H - 26
        frac = 1 - (y - top) / max(bottom - top, 1)
        gain = za.EQ_MIN_DB + frac * (za.EQ_MAX_DB - za.EQ_MIN_DB)
        return round(max(za.EQ_MIN_DB, min(za.EQ_MAX_DB, gain)) * 2) / 2

    def _press(self, e: tk.Event) -> None:
        self._drag_band = self._band_at(e.x)
        self._set_band(self._drag_band, self._gain_at(e.y))

    def _drag(self, e: tk.Event) -> None:
        if self._drag_band is not None:
            self._set_band(self._drag_band, self._gain_at(e.y))

    def _release(self) -> None:
        self._drag_band = None

    def _reset_band(self, e: tk.Event) -> None:
        self._set_band(self._band_at(e.x), 0.0)

    def _wheel(self, e: tk.Event, delta: int | None = None) -> None:
        d = delta if delta is not None else e.delta
        band = self._band_at(e.x)
        self._set_band(band, self._gains[band] + (0.5 if d > 0 else -0.5))

    def _set_band(self, band: int, gain: float) -> None:
        gain = round(max(za.EQ_MIN_DB, min(za.EQ_MAX_DB, gain)), 1)
        if gain == self._gains[band] and self._enabled:
            return
        self._gains[band] = gain
        self._enabled = True
        self._commit()

    def _redraw(self) -> None:
        pal = self._pal
        accent = pal.accent if self._enabled else pal.fg_dim
        name = self.current_preset_name()
        self._preset_var.set(name)
        self._preset_text.set(f"{name}  ▾")
        if self._delete_lbl is not None:
            if name in self._custom:
                if not self._delete_lbl.winfo_ismapped():
                    self._delete_lbl.pack(side="right")
            else:
                self._delete_lbl.pack_forget()
        p = self._power
        p.delete("all")
        p.create_rectangle(2, 3, 42, 19, fill=accent if self._enabled else pal.track, outline="")
        p.create_oval(2, 3, 18, 19, fill=accent if self._enabled else pal.track, outline="")
        p.create_oval(26, 3, 42, 19, fill=accent if self._enabled else pal.track, outline="")
        kx = 27 if self._enabled else 3
        p.create_oval(kx, 4, kx + 14, 18, fill="#ffffff", outline="")

        c = self._cv
        c.delete("all")
        margin = self.MARGIN
        w = max(c.winfo_width(), margin + len(self._gains))
        col = (w - margin) / len(self._gains)
        top, bottom = 22, self.H - 26
        mid = (top + bottom) / 2

        for db in (-12, -6, 0, 6, 12):
            y = bottom - (db - za.EQ_MIN_DB) / (za.EQ_MAX_DB - za.EQ_MIN_DB) * (bottom - top)
            c.create_line(margin - 4, y, w, y, fill=pal.track if db else pal.fg_dim,
                          dash=() if db == 0 else (2, 4))
            c.create_text(margin - 8, y, text=f"{db:+d}" if db else "0", anchor="e",
                          fill=pal.fg_dim, font=(FONT_UI, 7))

        pts: list[float] = []
        for i, g in enumerate(self._gains):
            x = margin + col * (i + 0.5)
            y = bottom - (g - za.EQ_MIN_DB) / (za.EQ_MAX_DB - za.EQ_MIN_DB) * (bottom - top)
            pts += [x, y]
            c.create_line(x, top, x, bottom, fill=pal.track, width=4, capstyle="round")
            c.create_line(x, mid, x, y, fill=accent, width=4, capstyle="round")
            c.create_text(x, bottom + 14, text=za.EQ_BAND_LABELS[i], fill=pal.fg_dim,
                          font=(FONT_UI, 8))
            if g:
                c.create_text(x, y - 13 if g > 0 else y + 13, text=f"{g:+g}", fill=pal.fg,
                              font=(FONT_UI, 7))
        if len(pts) >= 4:
            c.create_line(*pts, fill=alpha_blend_hex(accent, pal.bg, 0.55), width=2, smooth=True)
        for i in range(len(self._gains)):
            x, y = pts[2 * i], pts[2 * i + 1]
            c.create_oval(x - 7, y - 7, x + 7, y + 7, fill="#ffffff", outline=accent, width=2)


class QueuePanel(tk.Frame):
    """Lista de reprodução com faixa atual destacada, adicionar e remover."""

    def __init__(self, parent: tk.Misc, player: Any, cfg: Any, pal: Palette,
                 on_changed: Callable[[], None] | None = None) -> None:
        super().__init__(parent, bg=pal.bg)
        self._player = player
        self._cfg = cfg
        self._pal = pal
        self._on_changed = on_changed

        bar = tk.Frame(self, bg=pal.bg)
        bar.pack(fill="x", pady=(0, 6))
        self._count = tk.Label(bar, text="", bg=pal.bg, fg=pal.fg_dim, font=(FONT_UI, 9))
        self._count.pack(side="left")
        for kind, tip, cmd in (("folder", "Adicionar pasta", self._add_folder),
                               ("plus", "Adicionar arquivos", self._add_files)):
            FlatButton(bar, kind, cmd, pal, size=28, tooltip=tip).pack(side="right")
        rm = tk.Label(bar, text="Remover", bg=pal.bg, fg=pal.fg_dim, font=(FONT_UI, 9),
                      cursor="hand2", padx=8)
        rm.pack(side="right")
        rm.bind("<Button-1>", lambda _e: self._remove_selected())

        holder = tk.Frame(self, bg=pal.surface)
        holder.pack(fill="both", expand=True)
        self._lb = tk.Listbox(holder, bg=pal.surface, fg=pal.fg, selectbackground=pal.surface_hi,
                              selectforeground=pal.fg, activestyle="none", bd=0,
                              highlightthickness=0, font=(FONT_UI, 10), exportselection=False)
        sb = tk.Scrollbar(holder, orient="vertical", command=self._lb.yview)
        self._lb.configure(yscrollcommand=sb.set)
        self._lb.pack(side="left", fill="both", expand=True, padx=4, pady=4)
        sb.pack(side="right", fill="y")
        self._lb.bind("<Double-Button-1>", self._play_selected)
        self._lb.bind("<Return>", self._play_selected)
        self._lb.bind("<Delete>", lambda _e: self._remove_selected())
        self.refresh()

    def set_palette(self, pal: Palette) -> None:
        """Atualiza as cores."""
        self._pal = pal
        self._lb.configure(selectbackground=pal.surface_hi, selectforeground=pal.fg)
        self.refresh()

    def refresh(self) -> None:
        """Recarrega a lista e destaca a faixa atual."""
        tracks = self._player.playlist
        cur = self._player.current_index
        with contextlib.suppress(tk.TclError):
            self._lb.delete(0, "end")
            for i, t in enumerate(tracks):
                prefix = "▶  " if i == cur and self._player.is_playing else f"{i + 1:>2}  "
                self._lb.insert("end", prefix + t.get("name", "?"))
                if i == cur:
                    self._lb.itemconfig(i, fg=self._pal.accent)
            self._count.configure(text=f"{len(tracks)} faixa(s)")
            if 0 <= cur < len(tracks):
                self._lb.see(cur)

    def _persist(self) -> None:
        with contextlib.suppress(Exception):  # noqa: BLE001
            self._cfg.set("zen_media_playlist", self._player.playlist)
        self.refresh()
        if self._on_changed:
            self._on_changed()

    def _play_selected(self, _e: tk.Event | None = None) -> None:
        sel = self._lb.curselection()
        if sel:
            self._player.play_index(int(sel[0]))

    def _remove_selected(self) -> None:
        for idx in reversed(self._lb.curselection()):
            self._player.remove_track(int(idx))
        self._persist()

    def _add_files(self) -> None:
        exts = " ".join(f"*{e}" for e in sorted(self._player.AUDIO_EXTENSIONS))
        paths = filedialog.askopenfilenames(
            parent=self.winfo_toplevel(), title="Adicionar mídia",
            filetypes=[("Áudio e vídeo", exts), ("Todos", "*.*")])
        from pathlib import Path

        for p in paths:
            self._player.add_track(Path(p).stem, p)
        if paths:
            self._persist()

    def _add_folder(self) -> None:
        folder = filedialog.askdirectory(parent=self.winfo_toplevel(), title="Adicionar pasta")
        if folder:
            self._player.load_folder(folder)
            self._persist()
