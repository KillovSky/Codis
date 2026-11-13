"""Tooltip hover da KUI do Codis.

Implementa ``KHoverTooltip``, o widget de tooltip contextual utilizado
pela interface KEngine para exibir informações sobre elementos
visuais ao passar o mouse.
"""

from __future__ import annotations

import contextlib
import math
import tkinter as tk
from typing import Any

from ._colors import _pal

try:
    from src.i18n import _t as _i18n_t
except ImportError:

    def _i18n_t(key: str, **kw) -> str:

        return key.split(".")[-1]

_TAU = math.tau


class KHoverTooltip:
    """Sistema de aprendizado do Cosmos Engine.

    - Tooltip fixo no canto inferior esquerdo (não segue objeto)
    - Delay de 1.5s para aparecer, permanece 10s
    - Botões ◀ ▶ / teclas <- -> para navegar entre fatos
    - Suporte a áreas circulares e retangulares
    - 100% i18n via _i18n_t()
    """

    _DELAY_MS = 1500
    _SHOW_MS = 10000
    _PANEL_W = 420
    _PANEL_H = 100

    def __init__(self, canvas: tk.Canvas, dark: bool = True) -> None:
        """Inicializa o tooltip hover da KUI."""
        self._cv = canvas
        self._dark = dark
        self._areas: list[dict] = []
        self._hover_key: str | None = None
        self._hover_kw: dict = {}
        self._tip_id: int | None = None
        self._hide_id: int | None = None
        self._visible = False
        self._tip_items: list[int] = []
        self._fact_keys: list[str] = []
        self._fact_idx: int = 0
        self._fixed_mx: float = 0.0
        self._fixed_my: float = 0.0

    def clear_areas(self) -> None:
        """Remove todas as áreas registradas e oculta o painel."""
        self._areas.clear()
        self._hide()

    def register_circle(
        self,
        cx: float,
        cy: float,
        r: float,
        i18n_key: str,
        kw: dict | None = None,
        on_click: Any = None,
        extra_keys: list[str] | None = None,
    ) -> None:
        """Registra área circular para hover/clique."""
        self._areas.append(
            {
                "kind": "circle",
                "cx": cx,
                "cy": cy,
                "r": r,
                "key": i18n_key,
                "kw": kw or {},
                "on_click": on_click,
                "extra_keys": extra_keys or [],
            }
        )

    def on_click(self, e: tk.Event) -> None:
        """Trata evento de clique para reposicionar ou ocultar o tooltip."""
        mx, my = e.x, e.y
        if self._visible and self._tip_items:
            self._handle_nav_click(mx, my)
            return
        for area in self._areas:
            cb = area.get("on_click")
            if cb and self._in_area(mx, my, area):
                with contextlib.suppress(tk.TclError):
                    cb()
                return

    def on_motion(self, e: tk.Event) -> None:
        """Trata movimento do mouse para atualizar a posição do tooltip."""
        mx, my = e.x, e.y
        hit_key = None
        hit_kw: dict = {}
        hit_extra: list[str] = []
        for area in self._areas:
            if self._in_area(mx, my, area):
                hit_key = area["key"]
                hit_kw = area["kw"]
                hit_extra = area.get("extra_keys", [])
                break

        if hit_key != self._hover_key:
            self._hover_key = hit_key
            self._hover_kw = hit_kw
            self._cancel_timers()
            self._hide()
            if hit_key:
                self._fixed_mx = mx
                self._fixed_my = my
                self._fact_keys = [hit_key] + hit_extra
                self._fact_idx = 0
                self._tip_id = self._cv.after(
                    self._DELAY_MS, lambda k=hit_key, kw=hit_kw: self._show(
                        k, kw)
                )

    def on_leave(self) -> None:
        """Mouse saiu do canvas."""
        self._hover_key = None
        self._cancel_timers()
        self._hide()

    def nav_prev(self) -> None:
        """Navega para o fato anterior ( <- )."""
        n = len(self._fact_keys)
        if n > 1 and self._visible:
            self._fact_idx = (self._fact_idx - 1) % n
            self._cancel_timers()
            self._render_panel()
            self._hide_id = self._cv.after(self._SHOW_MS, self._hide)

    def nav_next(self) -> None:
        """Navega para o próximo fato ( -> )."""
        n = len(self._fact_keys)
        if n > 1 and self._visible:
            self._fact_idx = (self._fact_idx + 1) % n
            self._cancel_timers()
            self._render_panel()
            self._hide_id = self._cv.after(self._SHOW_MS, self._hide)

    def _show(self, key: str, kw: dict) -> None:
        """Exibe o componente ou conteúdo indicado.

        Args:
            key: Chave de identificação.
            _kw: Parâmetro da operação.
        """
        if not self._cv.winfo_exists():
            return
        self._hide()
        self._visible = True
        self._render_panel()
        self._hide_id = self._cv.after(self._SHOW_MS, self._hide)

    def _render_panel(self) -> None:
        """Desenha o painel de fatos no canto inferior esquerdo."""
        if not self._cv.winfo_exists() or not self._fact_keys:
            return
        for item in self._tip_items:
            with contextlib.suppress(tk.TclError):
                self._cv.delete(item)
        self._tip_items = []

        W = self._cv.winfo_width()
        H = self._cv.winfo_height()
        key = self._fact_keys[self._fact_idx]
        kw = self._hover_kw
        try:
            text = _i18n_t(key, **kw)
        except (KeyError, AttributeError):
            text = key

        if "|" in text:
            title, body = text.split("|", 1)
        else:
            title = ""
            body = text

        pal = _pal(self._dark)
        p_glass = pal["glass"]
        p_accent = pal["accent"]
        p_fg = pal["fg"]
        p_dim = pal["fg_dim"]
        p_nav = pal["accent2"]

        PW = min(self._PANEL_W, W - 20)
        PH = self._PANEL_H
        bx = 12
        by = H - PH - 12
        ids: list[int] = []

        ids.append(
            self._cv.create_rectangle(
                bx + 3,
                by + 3,
                bx + PW + 3,
                by + PH + 3,
                fill="#000000",
                outline="",
                stipple="gray25",
            )
        )

        ids.append(
            self._cv.create_rectangle(
                bx, by, bx + PW, by + PH, fill=p_glass, outline=p_accent, width=1
            )
        )

        ids.append(
            self._cv.create_rectangle(
                bx, by, bx + PW, by + 3, fill=p_accent, outline=""
            )
        )

        n_facts = len(self._fact_keys)
        nav_text = f"{self._fact_idx + 1}/{n_facts}" if n_facts > 1 else ""
        if nav_text:
            ids.append(
                self._cv.create_text(
                    bx + PW - 8,
                    by + 12,
                    text=nav_text,
                    fill=p_dim,
                    font=("Segoe UI", 7),
                    anchor="e",
                )
            )

        tx = bx + 10
        ty = by + 10
        if title.strip():
            ids.append(
                self._cv.create_text(
                    tx,
                    ty,
                    text=title.strip(),
                    fill=p_accent,
                    font=("Segoe UI", 9, "bold"),
                    anchor="nw",
                )
            )
            ty += 18

        words = body.strip().split()
        lines_: list[str] = []
        cur = ""
        max_chars = max(10, (PW - 24) // 7)
        for w in words:
            test = (cur + " " + w).strip()
            if len(test) > max_chars and cur:
                lines_.append(cur)
                cur = w
            else:
                cur = test
        if cur:
            lines_.append(cur)
        for i, line in enumerate(lines_[:4]):
            ids.append(
                self._cv.create_text(
                    tx,
                    ty + i * 14,
                    text=line,
                    fill=p_fg,
                    font=("Segoe UI", 8),
                    anchor="nw",
                )
            )

        if n_facts > 1:
            btn_y = by + PH - 16
            self._nav_prev_box = (bx + 8, btn_y - 8, bx + 30, btn_y + 8)
            ids.append(
                self._cv.create_text(
                    bx + 19,
                    btn_y,
                    text=_i18n_t("kui.nav_prev"),
                    fill=p_nav,
                    font=("Segoe UI", 7),
                    anchor="center",
                    tags="nav_prev",
                )
            )
            self._nav_next_box = (bx + PW - 48, btn_y -
                                  8, bx + PW - 8, btn_y + 8)
            ids.append(
                self._cv.create_text(
                    bx + PW - 28,
                    btn_y,
                    text=_i18n_t("kui.nav_next"),
                    fill=p_nav,
                    font=("Segoe UI", 7),
                    anchor="center",
                    tags="nav_next",
                )
            )
        else:
            self._nav_prev_box = None
            self._nav_next_box = None

        self._tip_items = ids
        self._panel_bx = bx
        self._panel_by = by
        self._panel_bw = PW
        self._panel_bh = PH

    def _handle_nav_click(self, mx: float, my: float) -> None:
        """Clique nos botões ◀ ▶."""
        n = len(self._fact_keys)
        if n <= 1:
            return
        prev = getattr(self, "_nav_prev_box", None)
        nxt = getattr(self, "_nav_next_box", None)
        if prev and prev[0] <= mx <= prev[2] and prev[1] <= my <= prev[3]:
            self.nav_prev()
        elif nxt and nxt[0] <= mx <= nxt[2] and nxt[1] <= my <= nxt[3]:
            self.nav_next()
        else:
            bx = getattr(self, "_panel_bx", -1)
            by = getattr(self, "_panel_by", -1)
            bw = getattr(self, "_panel_bw", 0)
            bh = getattr(self, "_panel_bh", 0)
            if not (bx <= mx <= bx + bw and by <= my <= by + bh):
                for area in self._areas:
                    cb = area.get("on_click")
                    if cb and self._in_area(mx, my, area):
                        with contextlib.suppress(tk.TclError):
                            cb()
                        return

    @staticmethod
    def _in_area(mx: float, my: float, area: dict) -> bool:
        """Executa 'in area' e retorna o resultado.

        Args:
            mx: Parâmetro da operação.
            my: Parâmetro da operação.
            area: Parâmetro da operação.
        """
        if area["kind"] == "circle":
            dx = mx - area["cx"]
            dy = my - area["cy"]
            return dx * dx + dy * dy <= area["r"] ** 2
        return area["x1"] <= mx <= area["x2"] and area["y1"] <= my <= area["y2"]

    def _cancel_timers(self) -> None:
        """Cancela todos os timers pendentes de show e hide do tooltip."""
        for attr in ("_tip_id", "_hide_id"):
            job = getattr(self, attr, None)
            if job is not None:
                with contextlib.suppress(tk.TclError):
                    self._cv.after_cancel(job)
                setattr(self, attr, None)

    def _hide(self) -> None:
        """Oculta o componente ou elemento."""
        self._cancel_timers()
        for item in self._tip_items:
            with contextlib.suppress(tk.TclError):
                self._cv.delete(item)
        self._tip_items = []
        self._visible = False
