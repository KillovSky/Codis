"""
Codis - Utilitários de tema reutilizáveis.

Centraliza helpers que antes eram copiados em cada componente:

- ``cfg``        : aplica um dict de opções tk a um widget com supressão de TclError
- ``cfg_many``   : aplica o mesmo dict a múltiplos widgets
- ``theme_map``  : retorna dict {opção: valor_do_tema} para uso com cfg/cfg_many
- ``recolor``    : aplica paleta de tema a um mapping widget -> {opção: attr_tema}
- ``lsp_loc``    : extrai (uri, linha) de um objeto/dict de localização LSP

Esses helpers eliminam repetição em apply_theme() e _cb() espalhados pelo codebase.
"""

from __future__ import annotations

import contextlib
import tkinter as tk
from collections.abc import Callable
from typing import Any

from src.constants import FONT_UI


def cfg(widget: tk.Widget, **options: Any) -> None:
    """Aplica opções a um widget suprimindo TclError (widget destruído).

    Substitui o padrão:
        with contextlib.suppress(tk.TclError):
            w.config(bg=..., fg=...)
    """
    with contextlib.suppress(tk.TclError, AttributeError):
        widget.config(**options)


def cfg_many(widgets: list[tk.Widget], **options: Any) -> None:
    """Aplica as mesmas opções a vários widgets de uma vez."""
    for w in widgets:
        cfg(w, **options)


def recolor(mapping: dict[tk.Widget, dict[str, str]]) -> None:
    """Aplica paleta de cores a múltiplos widgets via um mapeamento.

    Args:
        mapping: {widget: {opção_tk: valor_cor}}

    Exemplo::

        recolor({
            self._frame:  {"bg": t.bg},
            self._label:  {"bg": t.bg, "fg": t.fg},
            self._entry:  {"bg": t.input_bg, "fg": t.fg,
                           "insertbackground": t.fg},
        })
    """
    for widget, opts in mapping.items():
        cfg(widget, **opts)


def make_toolbar_button(
    parent: tk.Widget,
    theme: Any,
    label: str,
    cmd: Callable[[], Any],
    *,
    accent: bool = False,
    side: str = "left",
    outer_padx: int = 2,
    outer_pady: int = 4,
    inner_padx: int = 8,
    inner_pady: int = 4,
    font_size: int = 8,
) -> tk.Label:
    """Cria um botão de toolbar (``tk.Label`` clicável, com hover) padronizado.

    Usado pelos painéis ``sql_studio``, ``task_manager``, ``diff_panel``,
    ``formatter_panel``, ``env_manager`` e ``pip_manager``; garante hover
    consistente (``<Enter>``/``<Leave>``) em todo botão de toolbar.

    Args:
        parent: Widget-pai onde o botão será posicionado (``pack``).
        theme: Objeto de tema atual (precisa de ``accent``, ``sidebar_bg``,
            ``fg``, ``btn_hover``).
        label: Texto exibido no botão.
        cmd: Callback sem argumentos, chamado no clique (``<Button-1>``).
        accent: Se ``True``, usa a cor de destaque (``theme.accent``) como
            fundo e texto branco, em vez do estilo neutro padrão.
        side: Lado do ``pack()`` (``"left"`` ou ``"right"``).
        outer_padx: Espaçamento externo horizontal do ``pack()``.
        outer_pady: Espaçamento externo vertical do ``pack()``.
        inner_padx: Espaçamento interno horizontal do próprio label.
        inner_pady: Espaçamento interno vertical do próprio label.
        font_size: Tamanho da fonte (``FONT_UI``).

    Returns:
        O ``tk.Label`` criado (já posicionado e com binds aplicados).
    """
    bg = theme.accent if accent else theme.sidebar_bg
    fg = "#FFF" if accent else theme.fg
    b = tk.Label(
        parent,
        text=label,
        bg=bg,
        fg=fg,
        font=(FONT_UI, font_size),
        padx=inner_padx,
        pady=inner_pady,
        cursor="hand2",
    )
    b.pack(side=side, padx=outer_padx, pady=outer_pady)
    b.bind("<Button-1>", lambda _e: cmd())
    b.bind("<Enter>", lambda _e: b.config(bg=theme.btn_hover))
    b.bind("<Leave>", lambda _e: b.config(bg=bg))
    return b


def lsp_loc_uri(loc: Any) -> str:
    """Extrai a URI de um objeto/dict de localização LSP."""
    if hasattr(loc, "uri"):
        return loc.uri
    return loc.get("uri", "") if isinstance(loc, dict) else ""


def lsp_loc_line(loc: Any) -> int:
    """Extrai a linha (1-based) de um objeto/dict de localização LSP."""
    if hasattr(loc, "range"):
        start = loc.range.start
        line = start.line if hasattr(start, "line") else 0
    else:
        start = loc.get("range", {}).get(
            "start", {}) if isinstance(loc, dict) else {}
        line = start.get("line", 0) if isinstance(start, dict) else 0
    return line + 1


def lsp_loc_pair(loc: Any) -> tuple[str, int]:
    """Retorna (uri, linha_1based) de uma localização LSP."""
    return lsp_loc_uri(loc), lsp_loc_line(loc)


def _hex_to_rgb01(h: str) -> tuple[float, float, float]:
    """Converte hex (#rgb ou #rrggbb) para RGB normalizado (0.0-1.0)."""
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return r / 255.0, g / 255.0, b / 255.0


def _rgb01_to_hex(r: float, g: float, b: float) -> str:
    """Converte RGB normalizado (0.0-1.0, com clamp) para hex #rrggbb."""
    ri = max(0, min(255, round(r * 255)))
    gi = max(0, min(255, round(g * 255)))
    bi = max(0, min(255, round(b * 255)))
    return f"#{ri:02x}{gi:02x}{bi:02x}"


def hex_to_rgb(h: str) -> tuple[int, int, int]:
    """Converte hex (#rgb ou #rrggbb) para RGB inteiro (0-255 por canal).

    Usado por ``minimap``, ``task_manager`` e ``zen_media``.
    """
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def alpha_blend_hex(fg: str, bg: str, alpha: float) -> str:
    """Blenda ``fg`` sobre ``bg`` com opacidade ``alpha``; retorna hex #rrggbb.

    Tkinter não aceita cores hex com canal alpha (#rrggbbaa) - esta função
    simula o efeito calculando a cor resultante diretamente. Usado por
    ``minimap``, ``task_manager`` e ``zen_media``.

    Em caso de hex inválido, retorna ``fg`` sem modificação (preserva o
    comportamento original desses módulos).
    """
    try:
        fr, fg_g, fb = hex_to_rgb(fg)
        br, bg_g, bb = hex_to_rgb(bg)
        r = int(fr * alpha + br * (1 - alpha))
        g = int(fg_g * alpha + bg_g * (1 - alpha))
        b = int(fb * alpha + bb * (1 - alpha))
        return f"#{r:02x}{g:02x}{b:02x}"
    except (ValueError, IndexError):
        return fg


def hex_blend(base: str, overlay: str, alpha: float) -> str:
    """Mistura duas cores hex (#rrggbb) com alpha (0.0-1.0).

    Usado por `src/editor/_pane_lsp.py`, `src/theme/manager.py` e
    `src/ui/theme_preview_panel.py`. Usa ``_hex_to_rgb01`` para a
    normalização hex 3↔6 dígitos - o ``round()`` no caminho de ida e volta (int 0-255 -> float 0.0-1.0
    -> int 0-255) é seguro porque ``round(c / 255.0 * 255.0) == c`` para
    todo ``c`` em 0-255.

    Args:
        base:    Cor de fundo (#rgb ou #rrggbb).
        overlay: Cor sobreposta (#rgb ou #rrggbb).
        alpha:   Opacidade da sobreposição (0 = base pura, 1 = overlay pura).

    Returns:
        Cor resultante em formato #rrggbb.
    """
    try:
        br, bg_, bb = (round(c * 255) for c in _hex_to_rgb01(base))
        or_, og, ob = (round(c * 255) for c in _hex_to_rgb01(overlay))
        r = min(255, int(br + (or_ - br) * alpha))
        g = min(255, int(bg_ + (og - bg_) * alpha))
        b = min(255, int(bb + (ob - bb) * alpha))
        return f"#{r:02x}{g:02x}{b:02x}"
    except (ValueError, IndexError):
        return base


def _rgb_to_hsl(r: float, g: float, b: float) -> tuple[float, float, float]:
    """Converte RGB (0-1) para HSL (h em 0-1, s em 0-1, l em 0-1)."""
    mx, mn = max(r, g, b), min(r, g, b)
    l = (mx + mn) / 2.0
    if mx == mn:
        return 0.0, 0.0, l
    d = mx - mn
    s = d / (2.0 - mx - mn) if l > 0.5 else d / (mx + mn)
    if mx == r:
        h = (g - b) / d + (6.0 if g < b else 0.0)
    elif mx == g:
        h = (b - r) / d + 2.0
    else:
        h = (r - g) / d + 4.0
    return h / 6.0, s, l


def _hue_to_rgb(p: float, q: float, t: float) -> float:
    """Calcula um canal RGB a partir do hue, usado por ``_hsl_to_rgb``.

    Implementa a função auxiliar padrão de conversão HSL->RGB (mesma
    usada pelo algoritmo de referência do W3C CSS Color), aplicada três
    vezes por ``_hsl_to_rgb`` (uma para cada canal, deslocando ``t`` em
    ±1/3) para reconstruir r, g e b a partir de hue/saturação/luminosidade.
    """
    if t < 0:
        t += 1.0
    if t > 1:
        t -= 1.0
    if t < 1.0 / 6.0:
        return p + (q - p) * 6.0 * t
    if t < 1.0 / 2.0:
        return q
    if t < 2.0 / 3.0:
        return p + (q - p) * (2.0 / 3.0 - t) * 6.0
    return p


def _hsl_to_rgb(h: float, s: float, l: float) -> tuple[float, float, float]:
    """Converte HSL (0-1 cada) para RGB (0-1)."""
    if s == 0:
        return l, l, l
    q = l * (1 + s) if l < 0.5 else l + s - l * s
    p = 2 * l - q
    r = _hue_to_rgb(p, q, h + 1.0 / 3.0)
    g = _hue_to_rgb(p, q, h)
    b = _hue_to_rgb(p, q, h - 1.0 / 3.0)
    return r, g, b


def hex_vibrance(
    hex_color: str, sat_boost: float = 0.0, light_boost: float = 0.0
) -> str:
    """Aumenta saturação e/ou luminosidade de uma cor preservando a matiz.

    Usado pelo boost holográfico: intensifica cores sem trocar o "tom"
    de cada tema, mantendo a identidade visual de cada paleta.

    Cores acromáticas (preto, branco, cinza - saturação original 0) são
    tratadas como caso especial: como a matiz (hue) é matematicamente
    indefinida nesses casos, aplicar ``sat_boost`` é ignorado para evitar
    "inventar" uma cor com tom diferente do original (ex.: cinza virando
    vermelho). Apenas ``light_boost`` é aplicado a cores acromáticas.

    Args:
        hex_color: Cor de origem (#rgb ou #rrggbb).
        sat_boost: Delta de saturação, somado ao valor atual (-1.0 a 1.0).
        light_boost: Delta de luminosidade, somado ao valor atual (-1.0 a 1.0).

    Returns:
        Cor resultante em formato #rrggbb. Em caso de erro de parsing,
        retorna a cor original sem modificação.
    """
    try:
        r, g, b = _hex_to_rgb01(hex_color)
        h, s, l = _rgb_to_hsl(r, g, b)
        if s <= 0.0:
            l = max(0.0, min(1.0, l + light_boost))
            return _rgb01_to_hex(l, l, l)
        s = max(0.0, min(1.0, s + sat_boost))
        l = max(0.0, min(1.0, l + light_boost))
        r2, g2, b2 = _hsl_to_rgb(h, s, l)
        return _rgb01_to_hex(r2, g2, b2)
    except (ValueError, IndexError, ZeroDivisionError):
        return hex_color


def hex_glow(hex_color: str, intensity: float = 0.35) -> str:
    """Gera uma variante "glow" de uma cor de acento, mais clara e saturada.

    Usado para bordas com brilho, indicadores de foco e realces
    holográficos - clareia a cor mantendo o tom, simulando um brilho
    neon sutil sem saturar para branco puro.

    Args:
        hex_color: Cor de acento de origem.
        intensity: Quanto clarear/saturar (0.0-1.0). Padrão 0.35.

    Returns:
        Cor resultante em formato #rrggbb.
    """
    return hex_vibrance(
        hex_color, sat_boost=intensity * 0.4, light_boost=intensity * 0.25
    )
