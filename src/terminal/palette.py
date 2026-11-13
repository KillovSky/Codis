"""Resolução de cores e atributos do emulador para cores CSS do Tk. Sem Tkinter."""

from __future__ import annotations

from .vt import (
    DEFAULT_COLOR,
    FLAG_BOLD,
    FLAG_DIM,
    FLAG_HIDDEN,
    FLAG_REVERSE,
    FLAG_STRIKE,
    FLAG_UNDERLINE,
    FLAG_ITALIC,
)

ANSI_16 = (
    "#000000", "#f44747", "#6a9955", "#d7ba7d", "#569cd6", "#c586c0", "#9cdcfe", "#d4d4d4",
    "#808080", "#f44747", "#b5cea8", "#dcdcaa", "#569cd6", "#c586c0", "#9cdcfe", "#ffffff",
)
_CUBE = (0, 95, 135, 175, 215, 255)


def blend(fg: str, bg: str, ratio: float) -> str:
    """Mistura ``fg`` sobre ``bg``; ``ratio`` 1.0 devolve ``fg``."""
    try:
        f = [int(fg[i:i + 2], 16) for i in (1, 3, 5)]
        b = [int(bg[i:i + 2], 16) for i in (1, 3, 5)]
    except (ValueError, IndexError):
        return fg
    return "#" + "".join(f"{round(b[i] + (f[i] - b[i]) * ratio):02x}" for i in range(3))


def color_to_hex(color: int, palette: tuple[str, ...] = ANSI_16) -> str | None:
    """Converte a cor do emulador (índice 0-255 ou 24 bits com bit 24) em ``#rrggbb``."""
    if color == DEFAULT_COLOR:
        return None
    if color & 0x1000000:
        return f"#{color & 0xFFFFFF:06x}"
    if color < 16:
        return palette[color]
    if color < 232:
        n = color - 16
        return f"#{_CUBE[n // 36]:02x}{_CUBE[(n // 6) % 6]:02x}{_CUBE[n % 6]:02x}"
    level = 8 + (color - 232) * 10
    return f"#{level:02x}{level:02x}{level:02x}"


def resolve_attr(
    attr: tuple[int, int, int],
    default_fg: str,
    default_bg: str,
    palette: tuple[str, ...] = ANSI_16,
) -> dict[str, object]:
    """Opções de tag do Tk (``foreground``, ``background`` e afins) para um atributo."""
    fg, bg, flags = attr
    fg_hex = color_to_hex(fg, palette)
    if fg_hex is not None and flags & FLAG_BOLD and 0 <= fg < 8:
        fg_hex = palette[fg + 8]
    fg_hex = fg_hex or default_fg
    bg_hex = color_to_hex(bg, palette) or default_bg
    if flags & FLAG_REVERSE:
        fg_hex, bg_hex = bg_hex, fg_hex
    if flags & FLAG_DIM:
        fg_hex = blend(fg_hex, bg_hex, 0.6)
    if flags & FLAG_HIDDEN:
        fg_hex = bg_hex
    opts: dict[str, object] = {"foreground": fg_hex}
    if bg_hex != default_bg or flags & FLAG_REVERSE:
        opts["background"] = bg_hex
    if flags & FLAG_UNDERLINE:
        opts["underline"] = True
    if flags & FLAG_STRIKE:
        opts["overstrike"] = True
    opts["_font"] = (bool(flags & FLAG_BOLD), bool(flags & FLAG_ITALIC))
    return opts
