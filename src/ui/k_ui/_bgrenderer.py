"""Renderizador de fundo da KUI (K-UI) do Codis.

Implementa ``BgRenderer``, responsável pelo pipeline de renderização das
animações de fundo do engine KEngine: partículas, nebulosas,
planetas e efeitos de parallax.
"""

from __future__ import annotations

import logging
import math
import queue
import random
import threading
import time
from typing import Any

from ._colors import _hex2rgb, _pal

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageTk

    _PIL = True
except ImportError:
    Image = None
    ImageDraw = None
    ImageFilter = None
    ImageTk = None
    _PIL = False

logger = logging.getLogger("Codis.KUI")
_TAU = math.tau


class BgRenderer:
    """Renderiza camadas estáticas (PIL) em thread separada.

    TÁTICA #1 - Segurança de thread via queue.Queue (sem condição de corrida).
    TÁTICA #2 - Reutiliza buffer PIL quando W/H não mudam (evita alocação a cada frame).
    O canvas recebe 1 create_image por frame; toda nebulosa/aurora/raio fica aqui.
    """

    def __init__(self, dark: bool, cfg: dict) -> None:
        """Inicializa o renderizador de fundo com o modo de cor e configuração de perfil."""
        self._dark = dark
        self._cfg = cfg

        self._queue: queue.Queue = queue.Queue(maxsize=1)
        self._photo: Any | None = None
        self._rendering = False
        self._ph = random.uniform(0, _TAU)
        self._aur_ph = random.uniform(0, _TAU)
        self._W = 0
        self._H = 0
        self._bg_interval = 1.0 / max(1, cfg.get("bg_fps", 6))
        self._last_bg_t = 0.0

        self._buf: Any | None = None
        self._scale = 1
        self._render_cost = 0.0
        self._convert_cost = 0.0

    _MAX_BG_DIM = 960
    _DUTY = 4.0
    _MAX_INTERVAL = 3.0

    def _interval(self) -> float:
        cost = self._render_cost + self._convert_cost
        return min(self._MAX_INTERVAL, max(self._bg_interval, self._DUTY * cost))

    def maybe_update(self, W: int, H: int, ph: float, aur_ph: float) -> bool:
        """Dispara thread de renderização se necessário.

        Guarda: ignora W/H menores que 8px - isso ocorre durante o layout
        inicial do Tkinter (winfo_width retorna 1 antes do primeiro Configure).
        Renderizar com W=1,H=1 gera PhotoImage inválido e desperdiça uma thread.
        """
        if W < 8 or H < 8:
            return False
        self._ph = ph
        self._aur_ph = aur_ph
        now = time.monotonic()
        if self._rendering:
            return False
        size_changed = W != self._W or H != self._H
        if size_changed or now - self._last_bg_t > self._interval():
            if size_changed:
                self._W = W
                self._H = H
                self._buf = None
                self._scale = max(1, -(-max(W, H) // self._MAX_BG_DIM))
            self._rendering = True
            self._last_bg_t = now
            t = threading.Thread(
                target=self._render, args=(
                    W, H, ph, aur_ph, self._buf), daemon=True
            )
            t.start()
        return False

    def _render(
        self, W: int, H: int, ph: float, aur_ph: float, buf: Any = None
    ) -> None:
        """Renderiza nebulosa + aurora + raios em thread separada usando PIL.

        Args:
            W: largura do canvas em pixels.
            H: altura do canvas em pixels.
            ph: fase principal de animação (0-2π).
            aur_ph: fase da aurora (0-2π).
            buf: buffer PIL reutilizável (opcional - evita realocação de memória).
        """
        if not _PIL:
            self._rendering = False
            return
        t_start = time.perf_counter()
        try:
            p = _pal(self._dark)
            cfg = self._cfg
            void_rgb = p["void_rgb"]
            out_w, out_h = W, H
            sc = max(1, self._scale)
            if sc > 1:
                W, H = max(8, W // sc), max(8, H // sc)
                buf = None

            if buf is not None and getattr(buf, "size", None) == (W, H):
                img = buf
                ImageDraw.Draw(img).rectangle([0, 0, W, H], fill=void_rgb)
            else:
                img = Image.new("RGB", (W, H), void_rgb)

            if cfg.get("bg_rays"):
                ray_layer = Image.new("RGB", (W, H), (0, 0, 0))
                rd = ImageDraw.Draw(ray_layer)
                ray_rgb = _hex2rgb(p["ray"])
                cx = int(W * 0.74 + W * 0.08 * math.sin(ph * 0.3))
                cy = -int(H * 0.06)
                n_rays = 20
                for i in range(n_rays):
                    t2 = i / n_rays
                    angle = math.pi * 0.03 + t2 * math.pi * 1.5
                    bri = 0.028 + 0.024 * math.sin(ph * 0.5 + t2 * 5)
                    if bri < 0.003:
                        continue
                    rlen = int(max(W, H) * 1.8)
                    ex = int(cx + math.cos(angle) * rlen)
                    ey = int(cy + math.sin(angle) * rlen)
                    half = 0.016
                    rd.line(
                        [(cx, cy), (ex, ey)],
                        fill=(
                            int(ray_rgb[0] * bri * 5),
                            int(ray_rgb[1] * bri * 5),
                            int(ray_rgb[2] * bri * 5),
                        ),
                        width=max(1, int(W * half * 0.9)),
                    )
                ray_b = ray_layer.filter(ImageFilter.GaussianBlur(radius=max(1, 28 // sc)))
                img = Image.blend(img, ray_b, 0.60)

            gal_layer = Image.new("RGB", (W, H), (0, 0, 0))
            gd = ImageDraw.Draw(gal_layer)
            gcx = int(W * (0.48 + 0.04 * math.sin(ph * 0.18)))
            gcy = int(H * (0.50 + 0.03 * math.cos(ph * 0.12)))
            aur_a_rgb = p["aur_rgb_a"]
            aur_b_rgb = p["aur_rgb_b"]
            for layer_i, (scale, intensity) in enumerate(
                [(0.25, 0.22), (0.15, 0.35), (0.09, 0.50), (0.04, 0.65)]
            ):
                rw = int(W * scale)
                rh = int(H * scale * 0.55)
                if rw < 2 or rh < 2:
                    continue
                blend_t = layer_i / 3
                r_gal = int(aur_a_rgb[0] * (1 - blend_t) +
                            aur_b_rgb[0] * blend_t)
                g_gal = int(aur_a_rgb[1] * (1 - blend_t) +
                            aur_b_rgb[1] * blend_t)
                b_gal = int(aur_a_rgb[2] * (1 - blend_t) +
                            aur_b_rgb[2] * blend_t)
                col_gal = (
                    int(r_gal * intensity),
                    int(g_gal * intensity),
                    int(b_gal * intensity),
                )
                gd.ellipse((gcx - rw, gcy - rh, gcx +
                           rw, gcy + rh), fill=col_gal)
            gal_b = gal_layer.filter(
                ImageFilter.GaussianBlur(radius=max(12, min(W, H) // 8))
            )
            img = Image.blend(img, gal_b, 0.45)

            nc = cfg.get("bg_nebula_clouds", 0)
            blur_r = cfg.get("bg_nebula_blur", 0)
            if nc > 0 and blur_r > 0:
                neb = Image.new("RGB", (W, H), (0, 0, 0))
                nd = ImageDraw.Draw(neb)
                neb_cols = [
                    p["neb_a"],
                    p["neb_b"],
                    p["neb_c"],
                    p["neb_d"],
                    p["neb_e"],
                    p["neb_f"],
                ]
                scales = [1.1, 0.92, 0.87, 0.74, 0.70, 0.65]
                centers = [
                    (0.42, 0.42),
                    (0.18, 0.62),
                    (0.76, 0.25),
                    (0.58, 0.76),
                    (0.28, 0.28),
                    (0.50, 0.55),
                ]
                for ci in range(min(nc, len(neb_cols))):
                    col = neb_cols[ci]
                    scale = scales[ci]
                    rx_f, ry_f = centers[ci]
                    pulse = 0.72 + 0.28 * math.sin(ph * 0.28 + ci * 1.2)
                    cx_n = int(W * rx_f + W * 0.06 *
                               math.sin(aur_ph * 0.7 + ci * 1.3))
                    cy_n = int(H * ry_f + H * 0.05 *
                               math.cos(aur_ph * 0.6 + ci * 1.1))
                    rw = int(W * 0.50 * scale * pulse)
                    rh = int(H * 0.42 * scale * pulse)
                    for layer_s, alpha_mul in ((1.0, 1.1), (0.65, 0.70), (0.35, 0.42)):
                        lrw = int(rw * layer_s)
                        lrh = int(rh * layer_s)
                        if lrw > 0 and lrh > 0:
                            c_scaled = tuple(
                                min(255, int(c * alpha_mul)) for c in col)
                            nd.ellipse(
                                (cx_n - lrw, cy_n - lrh, cx_n + lrw, cy_n + lrh),
                                fill=c_scaled,
                            )
                neb_b = neb.filter(ImageFilter.GaussianBlur(radius=max(1, blur_r // sc)))
                img = Image.blend(img, neb_b, 0.92)

            nb = cfg.get("bg_aurora_bands", 0)
            abl = cfg.get("bg_aurora_blur", 0)
            if nb > 0 and abl > 0:
                aur = Image.new("RGB", (W, H), (0, 0, 0))
                ad = ImageDraw.Draw(aur)
                aur_cols = [
                    p["aur_rgb_a"],
                    p["aur_rgb_b"],
                    p["aur_rgb_c"],
                    p["aur_rgb_a"],
                    p["aur_rgb_b"],
                    p["aur_rgb_c"],
                    p["aur_rgb_a"],
                ]
                for i in range(min(nb, len(aur_cols))):
                    band_ph = aur_ph + i * (math.tau / max(nb, 1) * 0.72)
                    amp = H * (0.024 + 0.020 * (i % 3))
                    yb = int(H * (0.028 + i * 0.026) + amp * math.sin(band_ph))
                    yb += int(amp * 0.4 * math.sin(band_ph * 2.1 + i * 0.8))
                    intensity = max(
                        22, int(72 * (0.5 + 0.5 * abs(math.sin(band_ph * 1.5))))
                    )
                    col = aur_cols[i]
                    c_scaled = tuple(int(c * intensity / 255) for c in col)
                    thick = 5 + (i % 3) * 3
                    if 0 <= yb < H:
                        ad.rectangle(
                            (0, max(0, yb - thick), W, min(H, yb + thick)),
                            fill=c_scaled,
                        )
                aur_b = aur.filter(ImageFilter.GaussianBlur(radius=max(1, abl // sc)))
                img = Image.blend(img, aur_b, 0.35)

            vs = cfg.get("vignette_steps", 0)
            if vs > 0:
                vig = Image.new("RGB", (W, H), (0, 0, 0))
                vd = ImageDraw.Draw(vig)
                depth = int(min(W, H) * 0.18)
                for s in range(vs):
                    t2 = 1.0 - s / vs
                    shade = int(t2 * 9)
                    px = int(s * depth / vs)
                    th = max(1, depth // vs + 1)
                    vd.rectangle((px, 0, px + th, H),
                                 fill=(shade, shade, shade))
                    vd.rectangle(
                        (W - px - th, 0, W - px, H), fill=(shade, shade, shade)
                    )
                vig_b = vig.filter(ImageFilter.GaussianBlur(radius=2))
                img = Image.blend(img, vig_b, 0.55)

            if sc > 1:
                img = img.resize((out_w, out_h), Image.BILINEAR)
            try:
                self._queue.put_nowait(img)
                if sc == 1:
                    self._buf = img
            except queue.Full:
                pass
        except Exception as e:
            logger.debug("BgRenderer erro na renderização de fundo: %s", e)
        finally:
            self._render_cost = (0.6 * self._render_cost
                                 + 0.4 * (time.perf_counter() - t_start))
            self._rendering = False

    def get_photo(self) -> Any | None:
        """Retorna PhotoImage atualizado (main thread).

        TÁTICA #1: consome da queue - sem condição de corrida.
        """
        if not _PIL:
            return None
        try:
            img = self._queue.get_nowait()
            t0 = time.perf_counter()
            self._photo = ImageTk.PhotoImage(img)
            self._convert_cost = (0.6 * self._convert_cost
                                  + 0.4 * (time.perf_counter() - t0))
        except queue.Empty:
            pass
        return self._photo
