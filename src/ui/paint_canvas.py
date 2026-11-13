"""Canvas de desenho do Paint (Tk) sobre ``paint_engine``.

Como o lag foi eliminado:

* A tela é UM ``PhotoImage`` do tamanho do viewport. Zoom/pan renderizam só o viewport
  (``O(viewport)``, não ``O(imagem x zoom)``); desenhar atualiza só o retângulo sujo,
  copiado para dentro da foto pelo próprio Tk (``image copy -to``).
* Eventos de mouse só atualizam o modelo (máscara/imagem) e agendam UM ``flush`` em
  ``after_idle``: se o mouse gera 5 eventos antes do Tk desenhar, desenha-se uma vez.
* A grade de pixels, o xadrez de transparência e as pré-visualizações não criam
  itens de canvas por pixel.
"""

from __future__ import annotations

import contextlib
import logging
import math
import os
import tempfile
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import messagebox, simpledialog
from typing import Any

from src.i18n import _t
from src.ui import paint_engine as pe

logger = logging.getLogger("Codis.Paint")

try:
    from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageTk  # type: ignore

    HAS_PIL = True
except ImportError:  # pragma: no cover
    HAS_PIL = False

CANVAS_W, CANVAS_H = 800, 600
MIN_ZOOM, MAX_ZOOM = 0.0625, 32.0
ZOOM_STEPS = [0.0625, 0.125, 0.1667, 0.25, 0.333, 0.5, 0.667, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0, 16.0, 24.0, 32.0]
STROKE_TOOLS = ("brush", "pixel", "eraser")
RING_TOOLS = ("brush", "pixel", "eraser", "spray")
VIEW_BG = "#404040"
KEEP_VISIBLE = 64

_FONT_CANDIDATES = ("DejaVuSans.ttf", "Arial.ttf", "arial.ttf", "LiberationSans-Regular.ttf", "NotoSans-Regular.ttf",
                    "Helvetica.ttc", "Segoe UI.ttf", "segoeui.ttf")


def load_font(size: int) -> Any:
    for name in _FONT_CANDIDATES:
        with contextlib.suppress(OSError):
            return ImageFont.truetype(name, size)
    with contextlib.suppress(Exception):
        return ImageFont.load_default(size=size)
    return ImageFont.load_default()


def hex_bg(h: str) -> tuple[int, int, int]:
    return pe.hex_to_rgb(h)


class PaintCanvas(tk.Canvas):
    """Canvas de desenho principal."""

    def __init__(self, parent: tk.Widget, tmgr: Any, status_cb: Callable[[str], None] | None = None,
                 zoom_cb: Callable[[float], None] | None = None, **kw: Any) -> None:
        self._xsc = kw.pop("xscrollcommand", None)
        self._ysc = kw.pop("yscrollcommand", None)
        super().__init__(parent, bg=VIEW_BG, cursor="crosshair", highlightthickness=0, **kw)
        self._tmgr = tmgr
        self._status_cb = status_cb
        self._zoom_cb = zoom_cb
        self.on_change: Callable[[], None] | None = None
        self.on_color_pick: Callable[[str, bool], None] | None = None
        self.on_tool_request: Callable[[str], None] | None = None

        self._tool = "brush"
        self._color, self._color2 = "#000000", "#ffffff"
        self._size, self._opacity = 4, 255
        self._fill_shapes = False
        self._tolerance = 30
        self._spray_density = 20

        self.doc = pe.Document(Image.new("RGBA", (CANVAS_W, CANVAS_H), (255, 255, 255, 255))) if HAS_PIL else None
        self._zoom = 1.0
        self._pan = [20, 20]
        self._pan_drag: tuple[int, int, int, int] | None = None
        self._space = False

        self._photo: Any = None
        self._photo_size = (0, 0)
        self._img_item: int | None = None
        self._flush_id: str | None = None
        self._centered = False

        self._mask: Any = None
        self._stroke: dict[str, Any] | None = None
        self._start = (0.0, 0.0)
        self._last: tuple[float, float] | None = None
        self._poly: list[tuple[int, int]] = []
        self._preview: int | None = None
        self._ring: int | None = None
        self._last_status: tuple[int, int] | None = None

        self._selection: pe.Box | None = None
        self._float: dict[str, Any] | None = None
        self._float_photo: Any = None
        self._float_item: int | None = None
        self._sel_drag: tuple[int, int] | None = None
        self._clip: Any = None

        self._bind_events()
        self.bind("<Configure>", self._on_configure)

    @property
    def image(self) -> Any:
        return self.doc.image if self.doc else None

    @property
    def modified(self) -> bool:
        return bool(self.doc and self.doc.modified)

    @property
    def tool(self) -> str:
        return self._tool

    @tool.setter
    def tool(self, v: str) -> None:
        if v != self._tool:
            self.commit_float()
            if self._tool == "polygon" and v != "polygon":
                self._clear_poly()
            self._tool = v
            self._hide_ring()

    @property
    def color(self) -> str:
        return self._color

    @color.setter
    def color(self, v: str) -> None:
        self._color = v

    @property
    def color2(self) -> str:
        return self._color2

    @color2.setter
    def color2(self, v: str) -> None:
        self._color2 = v

    @property
    def size(self) -> int:
        return self._size

    @size.setter
    def size(self, v: int) -> None:
        self._size = max(1, min(200, int(v)))

    @property
    def opacity(self) -> int:
        return self._opacity

    @opacity.setter
    def opacity(self, v: int) -> None:
        self._opacity = max(1, min(255, int(v)))

    @property
    def tolerance(self) -> int:
        return self._tolerance

    @tolerance.setter
    def tolerance(self, v: int) -> None:
        self._tolerance = max(0, min(255, int(v)))

    @property
    def fill_shapes(self) -> bool:
        return self._fill_shapes

    @fill_shapes.setter
    def fill_shapes(self, v: bool) -> None:
        self._fill_shapes = bool(v)

    @property
    def zoom(self) -> float:
        return self._zoom

    def _view_size(self) -> tuple[int, int]:
        return max(1, self.winfo_width()), max(1, self.winfo_height())

    def _clamp_pan(self) -> None:
        if not self.doc:
            return
        vw, vh = self._view_size()
        iw, ih = self.doc.size
        dw, dh = iw * self._zoom, ih * self._zoom
        self._pan[0] = int(max(KEEP_VISIBLE - dw, min(vw - KEEP_VISIBLE, self._pan[0])))
        self._pan[1] = int(max(KEEP_VISIBLE - dh, min(vh - KEEP_VISIBLE, self._pan[1])))

    def to_image(self, ex: float, ey: float) -> tuple[float, float]:
        return (ex - self._pan[0]) / self._zoom, (ey - self._pan[1]) / self._zoom

    def to_screen(self, ix: float, iy: float) -> tuple[float, float]:
        return self._pan[0] + ix * self._zoom, self._pan[1] + iy * self._zoom

    def _clamped(self, ex: int, ey: int) -> tuple[int, int]:
        fx, fy = self.to_image(ex, ey)
        w, h = self.doc.size
        return max(0, min(int(math.floor(fx)), w - 1)), max(0, min(int(math.floor(fy)), h - 1))

    def _on_configure(self, _e: tk.Event) -> None:
        if not self.doc:
            return
        if not self._centered and self.winfo_width() > 8:
            self._centered = True
            self._center_image(fit_if_large=True)
            return
        self._clamp_pan()
        self._render_full()

    def _ensure_photo(self) -> None:
        vw, vh = self._view_size()
        if self._photo is None or self._photo_size != (vw, vh):
            self._photo = ImageTk.PhotoImage(Image.new("RGB", (vw, vh), hex_bg(VIEW_BG)))
            self._photo_size = (vw, vh)
            if self._img_item is None:
                self._img_item = self.create_image(0, 0, anchor="nw", image=self._photo, tags="pil_bg")
                self.tag_lower("pil_bg")
            else:
                self.itemconfigure(self._img_item, image=self._photo)

    def _render_full(self) -> None:
        if not self.doc:
            return
        self._ensure_photo()
        vw, vh = self._photo_size
        img = pe.render_rect(self.doc.image, self._zoom, tuple(self._pan), (0, 0, vw, vh), hex_bg(VIEW_BG),
                             self.doc.maybe_transparent)
        self._photo.paste(img)
        self._update_scrollbars()
        self._redraw_overlays()

    def _blit(self, img: Any, x: int, y: int) -> None:
        small = ImageTk.PhotoImage(img)
        self.tk.call(str(self._photo), "copy", str(small), "-to", x, y)

    def schedule_flush(self) -> None:
        if self._flush_id is None:
            self._flush_id = self.after_idle(self.flush)

    def flush(self) -> None:
        """Desenha o que mudou (chamado em idle; pode ser chamado direto)."""
        if self._flush_id is not None:
            with contextlib.suppress(tk.TclError):
                self.after_cancel(self._flush_id)
            self._flush_id = None
        if not self.doc:
            return
        dirty, everything = self.doc.take_dirty()
        if everything:
            self._render_full()
            self._notify_change()
            return
        if dirty is None:
            return
        self._ensure_photo()
        vw, vh = self._photo_size
        sx0, sy0, sx1, sy1 = pe.image_box_to_screen(dirty, self._zoom, tuple(self._pan))
        sx0, sy0, sx1, sy1 = max(0, sx0), max(0, sy0), min(vw, sx1), min(vh, sy1)
        if sx1 > sx0 and sy1 > sy0:
            img = pe.render_rect(self.doc.image, self._zoom, tuple(self._pan), (sx0, sy0, sx1, sy1),
                                 hex_bg(VIEW_BG), self.doc.maybe_transparent)
            self._blit(img, sx0, sy0)
        self._notify_change()

    def _notify_change(self) -> None:
        if self.on_change:
            with contextlib.suppress(Exception):
                self.on_change()

    def _scroll_extent(self) -> tuple[float, float, float, float]:
        vw, vh = self._view_size()
        iw, ih = self.doc.size
        mx, my = vw * 0.5, vh * 0.5
        return iw * self._zoom + 2 * mx, ih * self._zoom + 2 * my, mx, my

    def _update_scrollbars(self) -> None:
        if not self.doc:
            return
        vw, vh = self._view_size()
        tw, th, mx, my = self._scroll_extent()
        ox, oy = mx - self._pan[0], my - self._pan[1]
        if self._xsc:
            self._xsc(max(0.0, ox / tw), min(1.0, (ox + vw) / tw))
        if self._ysc:
            self._ysc(max(0.0, oy / th), min(1.0, (oy + vh) / th))

    def xview(self, *args: Any) -> Any:
        self._scroll_cmd(0, args)

    def yview(self, *args: Any) -> Any:
        self._scroll_cmd(1, args)

    def _scroll_cmd(self, axis: int, args: tuple) -> None:
        if not self.doc or not args:
            return
        vw, vh = self._view_size()
        tw, th, mx, my = self._scroll_extent()
        total, margin, view = (tw, mx, vw) if axis == 0 else (th, my, vh)
        off = margin - self._pan[axis]
        if args[0] == "moveto":
            off = float(args[1]) * total
        elif args[0] == "scroll":
            n = int(args[1])
            off += n * (view * 0.9 if args[2] == "pages" else 40)
        self._pan[axis] = int(margin - off)
        self._clamp_pan()
        self._render_full()

    def pan_by(self, dx: int, dy: int) -> None:
        self._pan[0] += dx
        self._pan[1] += dy
        self._clamp_pan()
        self._render_full()

    def _set_zoom_at(self, nz: float, ex: float, ey: float) -> None:
        nz = max(MIN_ZOOM, min(MAX_ZOOM, nz))
        if nz == self._zoom:
            return
        k = nz / self._zoom
        self._pan[0] = int(ex - (ex - self._pan[0]) * k)
        self._pan[1] = int(ey - (ey - self._pan[1]) * k)
        self._zoom = nz
        self._clamp_pan()
        self._render_full()
        if self._zoom_cb:
            self._zoom_cb(nz)

    def _zoom_at(self, ex: int, ey: int, direction: int) -> None:
        old = self._zoom
        if direction > 0:
            nz = next((z for z in ZOOM_STEPS if z > old * 1.001), MAX_ZOOM)
        else:
            nz = next((z for z in reversed(ZOOM_STEPS) if z < old * 0.999), MIN_ZOOM)
        self._set_zoom_at(nz, ex, ey)

    def set_zoom(self, z: float) -> None:
        vw, vh = self._view_size()
        self._set_zoom_at(z, vw / 2, vh / 2)

    def zoom_fit(self, margin: int = 24) -> float:
        vw, vh = self._view_size()
        iw, ih = self.doc.size
        z = max(MIN_ZOOM, min(MAX_ZOOM, (vw - 2 * margin) / iw, (vh - 2 * margin) / ih))
        self._zoom = z
        self._center_image()
        if self._zoom_cb:
            self._zoom_cb(z)
        return z

    def _center_image(self, fit_if_large: bool = False) -> None:
        if not self.doc:
            return
        vw, vh = self._view_size()
        iw, ih = self.doc.size
        if fit_if_large and (iw * self._zoom > vw - 24 or ih * self._zoom > vh - 24):
            self._zoom = max(MIN_ZOOM, min(1.0, (vw - 48) / iw, (vh - 48) / ih))
            if self._zoom_cb:
                self._zoom_cb(self._zoom)
        self._pan = [int((vw - iw * self._zoom) // 2), int((vh - ih * self._zoom) // 2)]
        self._clamp_pan()
        self._render_full()

    def _bind_events(self) -> None:
        b = self.bind
        b("<ButtonPress-1>", lambda e: self._on_press(e, True))
        b("<B1-Motion>", lambda e: self._on_drag(e, True))
        b("<ButtonRelease-1>", lambda e: self._on_release(e, True))
        b("<ButtonPress-3>", lambda e: self._on_press(e, False))
        b("<B3-Motion>", lambda e: self._on_drag(e, False))
        b("<ButtonRelease-3>", lambda e: self._on_release(e, False))
        b("<ButtonPress-2>", self._on_mid_press)
        b("<B2-Motion>", self._on_pan_drag)
        b("<ButtonRelease-2>", lambda _e: setattr(self, "_pan_drag", None))
        b("<Control-z>", lambda _e: self.undo() or "break")
        b("<Control-Z>", lambda _e: self.redo() or "break")
        b("<Control-y>", lambda _e: self.redo() or "break")
        b("<Control-Y>", lambda _e: self.redo() or "break")
        b("<Control-c>", lambda _e: self.copy_selection() or "break")
        b("<Control-x>", lambda _e: self.cut_selection() or "break")
        b("<Control-v>", lambda _e: self.paste() or "break")
        b("<Control-a>", lambda _e: self.select_all() or "break")
        b("<Delete>", lambda _e: self.delete_selection())
        b("<Escape>", lambda _e: self.cancel_operation())
        b("<Return>", lambda _e: self._on_return())
        b("<MouseWheel>", self._on_wheel)
        b("<Button-4>", self._on_wheel)
        b("<Button-5>", self._on_wheel)
        b("<Double-Button-1>", self._on_double_click)
        b("<Motion>", self._on_motion)
        b("<Enter>", lambda _e: self.focus_set())
        b("<Leave>", lambda _e: self._hide_ring())
        b("<KeyPress-space>", lambda _e: self._set_space(True))
        b("<KeyRelease-space>", lambda _e: self._set_space(False))
        b("<plus>", lambda _e: self._zoom_at(*[v // 2 for v in self._view_size()], 1))
        b("<minus>", lambda _e: self._zoom_at(*[v // 2 for v in self._view_size()], -1))

    def _set_space(self, v: bool) -> None:
        self._space = v
        self.config(cursor="fleur" if v else "crosshair")

    def _on_return(self) -> None:
        if self._float:
            self.commit_float()
        elif self._tool == "polygon":
            self._finalize_polygon()

    def _on_wheel(self, ev: tk.Event) -> None:
        up = ev.num == 4 or (getattr(ev, "delta", 0) or 0) > 0
        if ev.state & 0x4:
            self._zoom_at(ev.x, ev.y, 1 if up else -1)
        elif ev.state & 0x1:
            self.pan_by(60 if up else -60, 0)
        else:
            self.pan_by(0, 60 if up else -60)

    def _on_mid_press(self, ev: tk.Event) -> None:
        self._pan_drag = (ev.x, ev.y, self._pan[0], self._pan[1])

    def _on_pan_drag(self, ev: tk.Event) -> None:
        if self._pan_drag:
            x0, y0, px, py = self._pan_drag
            self._pan = [px + ev.x - x0, py + ev.y - y0]
            self._clamp_pan()
            self._schedule_full()

    def _schedule_full(self) -> None:
        if self.doc:
            self.doc.dirty_all = True
            self.schedule_flush()

    def _on_press(self, ev: tk.Event, primary: bool) -> None:
        self.focus_set()
        if not self.doc:
            return
        if primary and (self._space or self._tool == "pan"):
            self._pan_drag = (ev.x, ev.y, self._pan[0], self._pan[1])
            self.config(cursor="fleur")
            return
        t = self._tool
        color = self._color if primary else self._color2
        if t == "eraser":
            color = self._color2 if primary else self._color
        fx, fy = self.to_image(ev.x, ev.y)
        cx, cy = self._clamped(ev.x, ev.y)
        self._start = (cx, cy)
        self._last = (fx, fy)
        if t == "eyedrop":
            self._eyedrop(cx, cy, primary)
        elif t == "fill":
            self._fill(cx, cy, color, ev)
        elif t == "text":
            self._insert_text(cx, cy, color)
        elif t == "polygon":
            self._poly.append((cx, cy))
            self._update_poly_preview(cx, cy)
        elif t == "select":
            self._select_press(cx, cy)
        elif t in STROKE_TOOLS or t == "spray":
            self._begin_stroke(t, color, fx, fy)

    def _on_drag(self, ev: tk.Event, primary: bool) -> None:
        if not self.doc:
            return
        if self._pan_drag:
            self._on_pan_drag(ev)
            return
        t = self._tool
        fx, fy = self.to_image(ev.x, ev.y)
        cx, cy = self._clamped(ev.x, ev.y)
        if self._stroke is not None:
            self._stroke_to(fx, fy)
        elif t in ("line", "rect", "ellipse"):
            self._update_shape_preview(cx, cy, primary, shift=bool(ev.state & 0x1))
        elif t == "polygon":
            self._update_poly_preview(cx, cy)
        elif t == "select":
            self._select_drag(cx, cy)
        self._move_ring(ev.x, ev.y)

    def _on_release(self, ev: tk.Event, primary: bool) -> None:
        if not self.doc:
            return
        if self._pan_drag:
            self._pan_drag = None
            self.config(cursor="fleur" if self._space else "crosshair")
            return
        t = self._tool
        cx, cy = self._clamped(ev.x, ev.y)
        if self._stroke is not None:
            self._end_stroke()
        elif t in ("line", "rect", "ellipse"):
            self._finalize_shape(cx, cy, self._color if primary else self._color2, bool(ev.state & 0x1))
        elif t == "select":
            self._select_release(cx, cy)
        self._last = None

    def _on_double_click(self, _e: tk.Event) -> None:
        if self._tool == "polygon":
            self._finalize_polygon()

    def _on_motion(self, ev: tk.Event) -> None:
        if not self.doc:
            return
        self._move_ring(ev.x, ev.y)
        ix, iy = (int(math.floor(v)) for v in self.to_image(ev.x, ev.y))
        if (ix, iy) == self._last_status or not self._status_cb:
            return
        self._last_status = (ix, iy)
        w, h = self.doc.size
        if 0 <= ix < w and 0 <= iy < h:
            r, g, b, a = self.doc.image.getpixel((ix, iy))
            self._status_cb(f"({ix}, {iy})  {pe.rgb_to_hex(r, g, b)}  α={a}")
        else:
            self._status_cb(f"({ix}, {iy}) - fora da imagem")

    def _move_ring(self, ex: int, ey: int) -> None:
        if self._tool not in RING_TOOLS or self._pan_drag or self._space:
            self._hide_ring()
            return
        r = max(1.0, self._size * self._zoom / 2 * (2 if self._tool == "spray" else 1))
        if self._ring is None:
            self._ring = self.create_oval(0, 0, 1, 1, outline="#ffffff", width=1, tags="ring")
            self._ring2 = self.create_oval(0, 0, 1, 1, outline="#000000", width=1, tags="ring")
        for rid, d in ((self._ring, 0), (self._ring2, 1)):
            self.coords(rid, ex - r - d, ey - r - d, ex + r + d, ey + r + d)
        self.tag_raise("ring")

    def _hide_ring(self) -> None:
        if self._ring is not None:
            self.delete("ring")
            self._ring = None

    def _begin_stroke(self, tool: str, color: str, fx: float, fy: float) -> None:
        self.commit_float()
        self.doc.begin()
        aa = tool != "pixel"
        self._stroke = {"tool": tool, "rgb": pe.hex_to_rgb(color), "aa": aa}
        if tool in STROKE_TOOLS:
            self._mask = pe.new_stroke_mask(self.doc)
        self._stroke_to(fx, fy, first=True)

    def _stroke_to(self, fx: float, fy: float, first: bool = False) -> None:
        s = self._stroke
        lx, ly = self._last if (self._last and not first) else (fx, fy)
        if s["tool"] == "pixel":
            fx, fy, lx, ly = math.floor(fx), math.floor(fy), math.floor(lx), math.floor(ly)
        if s["tool"] == "spray":
            pe.spray(self.doc, int(lx), int(ly), int(fx), int(fy), s["rgb"], self._opacity, self._size,
                     self._spray_density)
        else:
            box = pe.stamp_segment(self._mask, lx, ly, fx, fy, self._size, s["aa"])
            if box:
                pe.apply_stroke_region(self.doc, self._mask, box, s["rgb"], self._opacity)
        self._last = (fx, fy)
        self.schedule_flush()

    def _end_stroke(self) -> None:
        self._stroke = None
        self._mask = None
        self.doc.commit()
        self.flush()

    def _fill(self, x: int, y: int, color: str, ev: tk.Event | None = None) -> None:
        self.commit_float()
        self.config(cursor="watch")
        self.update_idletasks()
        try:
            self.doc.begin()
            global_mode = bool(ev is not None and ev.state & 0x4)
            pe.flood_fill(self.doc, x, y, pe.hex_to_rgb(color), self._opacity, self._tolerance, not global_mode)
            self.doc.commit()
        finally:
            self.config(cursor="fleur" if self._space else "crosshair")
        self.flush()

    def _eyedrop(self, x: int, y: int, primary: bool) -> None:
        r, g, b, _a = self.doc.image.getpixel((x, y))
        color = pe.rgb_to_hex(r, g, b)
        if primary:
            self._color = color
        else:
            self._color2 = color
        if self.on_color_pick:
            self.on_color_pick(color, primary)

    def _constrain(self, x0: int, y0: int, x1: int, y1: int, shift: bool) -> tuple[int, int]:
        if not shift:
            return x1, y1
        dx, dy = x1 - x0, y1 - y0
        if self._tool == "line":
            ang = round(math.atan2(dy, dx) / (math.pi / 4)) * (math.pi / 4)
            d = math.hypot(dx, dy)
            return x0 + int(round(d * math.cos(ang))), y0 + int(round(d * math.sin(ang)))
        n = max(abs(dx), abs(dy))
        return x0 + (n if dx >= 0 else -n), y0 + (n if dy >= 0 else -n)

    def _update_shape_preview(self, cx: int, cy: int, primary: bool, shift: bool = False) -> None:
        self.delete("preview")
        x0, y0 = int(self._start[0]), int(self._start[1])
        cx, cy = self._constrain(x0, y0, cx, cy, shift)
        color = self._color if primary else self._color2
        sx0, sy0 = self.to_screen(x0 + 0.5, y0 + 0.5)
        sx1, sy1 = self.to_screen(cx + 0.5, cy + 0.5)
        kw = {"width": max(1, int(self._size * self._zoom)), "dash": (4, 3), "tags": "preview"}
        if self._tool == "line":
            self.create_line(sx0, sy0, sx1, sy1, fill=color, **kw)
        elif self._tool == "rect":
            self.create_rectangle(sx0, sy0, sx1, sy1, outline=color, **kw)
        else:
            self.create_oval(sx0, sy0, sx1, sy1, outline=color, **kw)

    def _finalize_shape(self, cx: int, cy: int, color: str, shift: bool = False) -> None:
        self.delete("preview")
        x0, y0 = int(self._start[0]), int(self._start[1])
        cx, cy = self._constrain(x0, y0, cx, cy, shift)
        if (cx, cy) == (x0, y0) and self._tool != "line":
            return
        self.commit_float()
        self.doc.begin()
        pe.draw_shape(self.doc, self._tool, [(x0, y0), (cx, cy)], pe.hex_to_rgb(color), self._opacity, self._size,
                      self._fill_shapes)
        self.doc.commit()
        self.flush()

    def _update_poly_preview(self, cx: int, cy: int) -> None:
        self.delete("polyprev")
        if not self._poly:
            return
        pts = [self.to_screen(x + 0.5, y + 0.5) for x, y in [*self._poly, (cx, cy)]]
        flat = [v for p in pts for v in p]
        if len(flat) >= 4:
            self.create_line(*flat, fill=self._color, width=max(1, int(self._size * self._zoom)), dash=(4, 3),
                             tags="polyprev")

    def _clear_poly(self) -> None:
        self._poly.clear()
        self.delete("polyprev")

    def _finalize_polygon(self) -> None:
        pts = list(self._poly)
        self._clear_poly()
        if len(pts) < 3:
            return
        self.doc.begin()
        pe.draw_shape(self.doc, "polygon", pts, pe.hex_to_rgb(self._color), self._opacity, self._size,
                      self._fill_shapes)
        self.doc.commit()
        self.flush()

    def _insert_text(self, x: int, y: int, color: str) -> None:
        text = simpledialog.askstring(_t("paint.text_title", "Inserir Texto"),
                                      _t("paint.text_prompt", "Digite o texto:"), parent=self)
        if not text:
            return
        self.commit_float()
        font = load_font(max(8, self._size * 4))
        probe = ImageDraw.Draw(Image.new("L", (1, 1)))
        l, t, r, b = probe.multiline_textbbox((0, 0), text, font=font)
        w, h = self.doc.size
        box = pe.clip_box((x, y, x + r + 2, y + b + 2), w, h)
        if box is None:
            return
        self.doc.begin()
        self.doc.touch(box)
        patch = Image.new("L", (box[2] - box[0], box[3] - box[1]), 0)
        ImageDraw.Draw(patch).multiline_text((0, 0), text, fill=255, font=font)
        pe.composite_patch(self.doc, patch, box, pe.hex_to_rgb(color), self._opacity)
        self.doc.commit()
        self.flush()

    def _norm_box(self, x0: int, y0: int, x1: int, y1: int) -> pe.Box:
        return (min(x0, x1), min(y0, y1), max(x0, x1) + 1, max(y0, y1) + 1)

    def _select_press(self, cx: int, cy: int) -> None:
        sel = self._selection
        if sel and sel[0] <= cx < sel[2] and sel[1] <= cy < sel[3]:
            self._lift_selection()
            self._sel_drag = (cx - self._selection[0], cy - self._selection[1])
            return
        self.commit_float()
        self._selection = None
        self._sel_drag = None
        self.delete("sel_rect")

    def _select_drag(self, cx: int, cy: int) -> None:
        if self._sel_drag and self._float:
            ox, oy = self._sel_drag
            self._move_float(cx - ox, cy - oy)
        else:
            self._selection = self._norm_box(int(self._start[0]), int(self._start[1]), cx, cy)
            self._draw_sel_rect()

    def _select_release(self, cx: int, cy: int) -> None:
        if self._sel_drag:
            self._sel_drag = None
            return
        sel = self._selection
        if sel and (sel[2] - sel[0] < 2 or sel[3] - sel[1] < 2):
            self._selection = None
            self.delete("sel_rect")

    def select_all(self) -> None:
        if not self.doc:
            return
        self.commit_float()
        w, h = self.doc.size
        self._selection = (0, 0, w, h)
        self._draw_sel_rect()
        if self.on_tool_request:
            self.on_tool_request("select")

    def _lift_selection(self) -> None:
        """Levanta os pixels da seleção (deixa a cor secundária no lugar) como camada flutuante."""
        if self._float or not self._selection:
            return
        box = pe.clip_box(self._selection, *self.doc.size)
        if box is None:
            return
        self.doc.begin()
        self.doc.touch(box)
        img = self.doc.image.crop(box)
        self.doc.image.paste(Image.new("RGBA", img.size, (*pe.hex_to_rgb(self._color2), 255)), (box[0], box[1]))
        self.doc.mark(box)
        self._float = {"img": img, "x": box[0], "y": box[1]}
        self._selection = (box[0], box[1], box[2], box[3])
        self.schedule_flush()
        self._draw_float(rebuild=True)

    def _move_float(self, x: int, y: int) -> None:
        f = self._float
        f["x"], f["y"] = x, y
        w, h = f["img"].size
        self._selection = (x, y, x + w, y + h)
        self._draw_float()
        self._draw_sel_rect()

    def _draw_float(self, rebuild: bool = False) -> None:
        f = self._float
        if not f:
            self.delete("float")
            self._float_item = None
            return
        if rebuild or self._float_photo is None:
            w, h = f["img"].size
            sw, sh = max(1, int(round(w * self._zoom))), max(1, int(round(h * self._zoom)))
            scaled = f["img"].resize((sw, sh), Image.NEAREST if self._zoom >= 1 else Image.BILINEAR)
            self._float_photo = ImageTk.PhotoImage(scaled)
            self.delete("float")
            self._float_item = None
        sx, sy = self.to_screen(f["x"], f["y"])
        if self._float_item is None:
            self._float_item = self.create_image(sx, sy, anchor="nw", image=self._float_photo, tags="float")
        else:
            self.coords(self._float_item, sx, sy)
        self.tag_raise("sel_rect")

    def commit_float(self) -> None:
        """Cola a camada flutuante na posição atual (uma única entrada de undo)."""
        f = self._float
        if not f:
            return
        self._float = None
        self._float_photo = None
        self.delete("float")
        self._float_item = None
        img, x, y = f["img"], f["x"], f["y"]
        w, h = self.doc.size
        box = pe.clip_box((x, y, x + img.width, y + img.height), w, h)
        if box is not None:
            self.doc.touch(box)
            piece = img.crop((box[0] - x, box[1] - y, box[2] - x, box[3] - y))
            self.doc.image.paste(Image.alpha_composite(self.doc.image.crop(box), piece), (box[0], box[1]))
            self.doc.mark(box)
        self.doc.commit()
        self._selection = (x, y, x + img.width, y + img.height)
        self.flush()
        self._draw_sel_rect()

    def _draw_sel_rect(self) -> None:
        self.delete("sel_rect")
        if not self._selection:
            return
        x0, y0, x1, y1 = self._selection
        sx0, sy0 = self.to_screen(x0, y0)
        sx1, sy1 = self.to_screen(x1, y1)
        self.create_rectangle(sx0, sy0, sx1, sy1, outline="#0099FF", dash=(5, 3), width=1, tags="sel_rect")
        self.create_rectangle(sx0 + 1, sy0 + 1, sx1 - 1, sy1 - 1, outline="#FFFFFF", dash=(5, 3), width=1,
                              tags="sel_rect")

    def _redraw_overlays(self) -> None:
        self._draw_sel_rect()
        if self._float:
            self._draw_float(rebuild=True)
        if self._poly:
            self._update_poly_preview(*self._poly[-1])

    def _selection_image(self) -> Any:
        if self._float:
            return self._float["img"].copy()
        if self._selection:
            box = pe.clip_box(self._selection, *self.doc.size)
            if box:
                return self.doc.image.crop(box)
        return None

    def copy_selection(self) -> None:
        img = self._selection_image()
        if img is not None:
            self._clip = img

    def cut_selection(self) -> None:
        img = self._selection_image()
        if img is None:
            return
        self._clip = img
        self.delete_selection()

    def delete_selection(self) -> None:
        if not self.doc:
            return
        if self._float:
            self._float = None
            self._float_photo = None
            self.delete("float")
            self._float_item = None
            self.doc.commit()
            self._selection = None
            self.delete("sel_rect")
            self.flush()
            return
        if self._selection:
            box = pe.clip_box(self._selection, *self.doc.size)
            if box:
                self.doc.begin()
                self.doc.touch(box)
                self.doc.image.paste(Image.new("RGBA", (box[2] - box[0], box[3] - box[1]),
                                               (*pe.hex_to_rgb(self._color2), 255)), (box[0], box[1]))
                self.doc.mark(box)
                self.doc.commit()
            self._selection = None
            self.delete("sel_rect")
            self.flush()

    def paste(self) -> None:
        if not self.doc:
            return
        img = self._system_clipboard_image() or self._clip
        if img is None:
            return
        self.commit_float()
        vw, vh = self._view_size()
        x, y = (max(0, int(v)) for v in self.to_image(16, 16))
        w, h = self.doc.size
        x, y = min(x, max(0, w - 1)), min(y, max(0, h - 1))
        self.doc.begin()
        self._float = {"img": img.convert("RGBA"), "x": x, "y": y}
        self._selection = (x, y, x + img.width, y + img.height)
        self._draw_float(rebuild=True)
        self._draw_sel_rect()
        if self.on_tool_request:
            self.on_tool_request("select")

    def _system_clipboard_image(self) -> Any:
        try:
            from PIL import ImageGrab  # type: ignore
            got = ImageGrab.grabclipboard()
            return got if isinstance(got, Image.Image) else None
        except Exception:  # noqa: BLE001
            return None

    def cancel_operation(self) -> None:
        self._clear_poly()
        self.delete("preview")
        if self._stroke is not None:
            self._stroke = None
            self._mask = None
            self.doc.rollback()
            self.flush()
        if self._float:
            self._float = None
            self._float_photo = None
            self.delete("float")
            self._float_item = None
            self.doc.rollback()
            self.flush()
        self._selection = None
        self.delete("sel_rect")

    def undo(self) -> bool:
        if not self.doc:
            return False
        if self._float:
            self.cancel_operation()
            return True
        ok = self.doc.undo()
        self._after_history(ok)
        return ok

    def redo(self) -> bool:
        if not self.doc:
            return False
        self.commit_float()
        ok = self.doc.redo()
        self._after_history(ok)
        return ok

    def _after_history(self, ok: bool) -> None:
        if ok:
            self._selection = None
            self.delete("sel_rect")
            self.flush()

    def _reset_state(self) -> None:
        self._float = None
        self._float_photo = None
        self._selection = None
        self._stroke = None
        self._mask = None
        self._clear_poly()
        self.delete("sel_rect")
        self.delete("float")
        self._float_item = None
        self._last_status = None

    def new_canvas(self, w: int = CANVAS_W, h: int = CANVAS_H, fill: tuple = (255, 255, 255, 255)) -> None:
        self._reset_state()
        self.doc.replace_image(Image.new("RGBA", (w, h), fill), record=False)
        self._zoom = 1.0
        self._center_image(fit_if_large=True)
        self._notify_change()

    def open_image(self, path: Path, quiet: bool = False) -> bool:
        if not HAS_PIL:
            messagebox.showwarning("Pillow", "Instale Pillow:\n  pip install Pillow")
            return False
        try:
            with Image.open(path) as src:
                src.load()
                img = ImageOps.exif_transpose(src).convert("RGBA")
        except Exception as exc:  # noqa: BLE001
            if not quiet:
                messagebox.showerror(_t("paint.open_error", "Erro ao abrir"), str(exc))
            return False
        self._reset_state()
        self.doc.replace_image(img, record=False)
        self._zoom = 1.0
        self._center_image(fit_if_large=True)
        self._notify_change()
        return True

    def save_image(self, path: Path, quiet: bool = False) -> bool:
        self.commit_float()
        try:
            fmt = (path.suffix.lstrip(".") or "png").upper()
            fmt = {"JPG": "JPEG", "TIF": "TIFF"}.get(fmt, fmt)
            out = pe.flatten_for_save(self.doc.image, fmt)
            fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=path.suffix or ".png")
            os.close(fd)
            try:
                out.save(tmp, format=fmt)
                os.replace(tmp, path)
            finally:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
            self.doc.mark_saved()
            self._notify_change()
            return True
        except Exception as exc:  # noqa: BLE001
            if not quiet:
                messagebox.showerror(_t("paint.save_error", "Erro ao salvar"), str(exc))
            logger.warning("falha ao salvar %s: %s", path, exc)
            return False

    def resize_canvas(self, nw: int, nh: int, scale: bool) -> None:
        self.commit_float()
        old = self.doc.image
        if scale:
            new = old.resize((nw, nh), Image.LANCZOS)
        else:
            new = Image.new("RGBA", (nw, nh), (255, 255, 255, 255))
            new.paste(old, (0, 0))
        self._selection = None
        self.delete("sel_rect")
        self.doc.replace_image(new, record=True)
        self._center_image()
        self._notify_change()
