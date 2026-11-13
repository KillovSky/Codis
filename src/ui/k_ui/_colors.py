"""
K-UI - Paletas de cor e utilitários matemáticos/widget.

Separado do módulo principal para reutilização sem importar
toda a engine gráfica.
"""

from __future__ import annotations

import contextlib
import math
import tkinter as tk
from functools import lru_cache
from typing import Any

_TAU = math.tau


@lru_cache(maxsize=4096)
def _lc(c1: str, c2: str, t_quantized: int) -> str:
    """Interpola entre duas cores hex (#rrggbb) com t quantizado em 256 níveis.

    Args:
        c1: Cor de origem, no formato ``#rrggbb``.
        c2: Cor de destino, no formato ``#rrggbb``.
        t_quantized: Fator de interpolação quantizado, ``int(t * 255)``,
            normalmente gerado por :func:`_lc_q`. Quantizar o fator permite
            que o ``lru_cache`` reaproveite resultados entre chamadas com
            valores de ``t`` muito próximos.

    Returns:
        Cor interpolada em ``#rrggbb``, ou ``c1`` se as cores de entrada
        forem inválidas.
    """
    try:
        t = max(0.0, min(1.0, t_quantized / 255.0))
        r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
        r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
        ri = max(0, min(255, int(r1 + (r2 - r1) * t)))
        gi = max(0, min(255, int(g1 + (g2 - g1) * t)))
        bi = max(0, min(255, int(b1 + (b2 - b1) * t)))
        return f"#{ri:02x}{gi:02x}{bi:02x}"
    except (ValueError, TypeError):
        return c1


def _lc_q(c1: str, c2: str, t: float) -> str:
    """API pública: interpola duas cores com quantização automática para cache."""
    return _lc(c1, c2, int(max(0.0, min(1.0, t)) * 255))


def _dark_palette() -> dict[str, Any]:
    """Retorna a paleta de cores do tema escuro do K-UI."""
    return {
        "void": "#020209",
        "void2": "#04040f",
        "editor": "#03030c",
        "accent": "#7c4dff",
        "accent2": "#00e5ff",
        "accent3": "#ff00cc",
        "fg": "#e8eaf6",
        "fg_dim": "#5c6bc0",
        "fg_muted": "#18183a",
        "border": "#0a0a28",
        "glass": "#07072a",
        "top": "#01010a",
        "star": "#e8eaf6",
        "star2": "#aa00ff",
        "star3": "#29b6f6",
        "aur_a": "#00e5ff",
        "aur_b": "#7c4dff",
        "aur_c": "#ff00cc",
        "meteor": "#ffffff",
        "ok": "#69ff47",
        "warn": "#ffd740",
        "err": "#ff5252",
        "plasma": "#ff00cc",
        "quantum": "#00ffcc",
        "ray": "#7c4dff",
        "text_bg": "#03030c",
        "text_fg": "#e8eaf6",
        "text_sel": "#18186a",
        "text_cur": "#7c4dff",
        "neb_a": (32, 0, 112),
        "neb_b": (0, 20, 72),
        "neb_c": (48, 0, 64),
        "neb_d": (0, 38, 36),
        "neb_e": (56, 0, 24),
        "neb_f": (22, 0, 80),
        "aur_rgb_a": (0, 229, 255),
        "aur_rgb_b": (124, 77, 255),
        "aur_rgb_c": (255, 0, 204),
        "void_rgb": (2, 2, 9),
        "t_link": "#00e5ff",
    }


def _light_palette() -> dict[str, Any]:
    """Retorna a paleta de cores do tema claro do K-UI."""
    return {
        "void": "#0d0d2b",
        "void2": "#11113a",
        "editor": "#0a0a20",
        "accent": "#818cf8",
        "accent2": "#38bdf8",
        "accent3": "#f472b6",
        "fg": "#e2e8f0",
        "fg_dim": "#94a3b8",
        "fg_muted": "#1e293b",
        "border": "#1e1b4b",
        "glass": "#14143a",
        "top": "#0a0a20",
        "star": "#c7d2fe",
        "star2": "#a78bfa",
        "star3": "#7dd3fc",
        "aur_a": "#67e8f9",
        "aur_b": "#a78bfa",
        "aur_c": "#f9a8d4",
        "meteor": "#c7d2fe",
        "ok": "#4ade80",
        "warn": "#facc15",
        "err": "#f87171",
        "plasma": "#f472b6",
        "quantum": "#34d399",
        "ray": "#818cf8",
        "text_bg": "#f8fafc",
        "text_fg": "#1e293b",
        "text_sel": "#c7d2fe",
        "text_cur": "#6366f1",
        "neb_a": (45, 27, 105),
        "neb_b": (30, 58, 95),
        "neb_c": (59, 26, 90),
        "neb_d": (26, 58, 42),
        "neb_e": (58, 32, 16),
        "neb_f": (26, 16, 96),
        "aur_rgb_a": (103, 232, 249),
        "aur_rgb_b": (167, 139, 250),
        "aur_rgb_c": (249, 168, 212),
        "void_rgb": (13, 13, 43),
        "t_link": "#38bdf8",
    }


def _pal(dark: bool) -> dict[str, Any]:
    """Retorna o dicionário de cores do tema dark ou light.

    As chaves ``neb_*`` definem a paleta da nebulosa de fundo: tons de
    roxo-azul profundo, magenta quente e teal, usados na composição do
    gradiente cósmico do K-UI.

    Args:
        dark: Se True, retorna a paleta escura; caso contrário, a clara.
    """
    return _dark_palette() if dark else _light_palette()


def _lerp(a: float, b: float, t: float) -> float:
    """Interpola linearmente entre *a* e *b* pelo fator *t* (0-1)."""
    return a + (b - a) * t


def _hue(h: float) -> tuple[int, int, int]:
    """Converte um ângulo de matiz (0-360) em tupla RGB."""
    s = h / 60.0
    i = int(s)
    f = s - i
    q = int(255 * (1 - f))
    t2 = int(255 * f)
    return [
        (255, t2, 0),
        (q, 255, 0),
        (0, 255, t2),
        (0, q, 255),
        (t2, 0, 255),
        (255, 0, q),
    ][i % 6]


def _hex2rgb(h: str) -> tuple[int, int, int]:
    """Converte cor hex (#rrggbb) em tupla (r, g, b) de inteiros."""
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


def _safe_del(w: Any) -> None:
    """Destrói um widget Tkinter de forma segura, suprimindo exceções."""
    with contextlib.suppress(tk.TclError):
        if w and hasattr(w, "destroy"):
            w.destroy()


def _restack(w: Any, command: str) -> None:
    """Reordena ``w`` na pilha Z do Tkinter via comando Tcl bruto.

    Helper comum a :func:`_wlift` e :func:`_wlower`: ambas operações
    diferem apenas no comando Tcl (``raise``/``lower``) repassado ao
    interpretador do widget.

    Args:
        w: Widget Tkinter a reordenar.
        command: Comando Tcl a executar, ``"raise"`` ou ``"lower"``.
    """
    with contextlib.suppress(tk.TclError):
        w.tk.call(command, w._w)


def _wlift(w: Any) -> None:
    """Eleva um widget ao topo da pilha Z do Tkinter."""
    _restack(w, "raise")


def _wlower(w: Any) -> None:
    """Rebaixa um widget na pilha Z do Tkinter."""
    _restack(w, "lower")
