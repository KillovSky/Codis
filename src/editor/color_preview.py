"""Codis - ColorPreview: exibição de swatches de cor inline no editor.

Renderiza pequenos retângulos coloridos ao lado de valores de cor no código
(hex, rgb, rgba, hsl, hsla, named CSS colors). Funciona em arquivos CSS,
SCSS, LESS, HTML, JS, TS, Python e qualquer linguagem com strings de cor.

Clicar em um swatch abre o Color Picker do sistema para editar a cor
diretamente no código.

Implementação: usa tags Tkinter com background colorido em um caractere
unicode de bloco sólido (█) inserido como texto de leitura, sem modificar
o conteúdo real do editor.
"""

from __future__ import annotations

import colorsys
import contextlib
import logging
import re
import tkinter as tk
import tkinter.colorchooser as tkcc
from typing import TYPE_CHECKING

from src.i18n import _t

if TYPE_CHECKING:
    from src.theme.base import Theme

logger = logging.getLogger("Codis")

_COLOR_PATTERNS: list[tuple[re.Pattern, str]] = [
    (
        re.compile(
            r"#([0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{4}|[0-9a-fA-F]{3})\b"),
        "hex",
    ),
    (
        re.compile(
            r"rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})"
            r"(?:\s*,\s*[\d.]+)?\s*\)"
        ),
        "rgb",
    ),
    (
        re.compile(
            r"hsla?\(\s*(\d{1,3})\s*,\s*(\d{1,3})%\s*,\s*(\d{1,3})%"
            r"(?:\s*,\s*[\d.]+)?\s*\)"
        ),
        "hsl",
    ),
]

_NAMED_COLORS: dict[str, str] = {
    "red": "#ff0000",
    "green": "#008000",
    "blue": "#0000ff",
    "white": "#ffffff",
    "black": "#000000",
    "yellow": "#ffff00",
    "orange": "#ffa500",
    "purple": "#800080",
    "pink": "#ffc0cb",
    "cyan": "#00ffff",
    "magenta": "#ff00ff",
    "lime": "#00ff00",
    "teal": "#008080",
    "navy": "#000080",
    "gray": "#808080",
    "grey": "#808080",
    "silver": "#c0c0c0",
    "maroon": "#800000",
    "olive": "#808000",
    "coral": "#ff7f50",
    "salmon": "#fa8072",
    "gold": "#ffd700",
    "khaki": "#f0e68c",
    "violet": "#ee82ee",
    "indigo": "#4b0082",
    "crimson": "#dc143c",
    "turquoise": "#40e0d0",
    "transparent": "#808080",
}

_SWATCH_TAG_PREFIX = "color_swatch_"

_SWATCH_CHAR = "█"

_DEBOUNCE_MS = 300

_MAX_SWATCHES = 500


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int] | None:
    """Converte string hex para tupla (r, g, b).

    Args:
        hex_color: String hex com ou sem '#', 3, 4, 6 ou 8 dígitos.

    Returns:
        tuple[int, int, int] | None: Componentes RGB (0-255) ou None se inválido.
    """
    h = hex_color.lstrip("#")
    try:
        if len(h) in (3, 4):
            r, g, b = int(h[0] * 2, 16), int(h[1] * 2, 16), int(h[2] * 2, 16)
        elif len(h) in (6, 8):
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        else:
            return None
        return (r, g, b)
    except ValueError:
        return None


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    """Converte componentes RGB para string hex.

    Args:
        r: Canal vermelho (0-255).
        g: Canal verde (0-255).
        b: Canal azul (0-255).

    Returns:
        str: String hex no formato '#rrggbb'.
    """
    return f"#{r:02x}{g:02x}{b:02x}"


def _contrast_fg(r: int, g: int, b: int) -> str:
    """Calcula a cor de texto com melhor contraste (preto ou branco).

    Usa luminância relativa segundo WCAG 2.1 para garantir legibilidade.

    Args:
        r: Canal vermelho (0-255).
        g: Canal verde (0-255).
        b: Canal azul (0-255).

    Returns:
        str: '#000000' ou '#ffffff'.
    """
    lum = 0.2126 * (r / 255) + 0.7152 * (g / 255) + 0.0722 * (b / 255)
    return "#000000" if lum > 0.5 else "#ffffff"


def _parse_color(match_text: str, kind: str) -> str | None:
    """Parseia um match de cor para hex canônico.

    Args:
        match_text: Texto completo do match (ex: '#ff0000', 'rgb(255,0,0)').
        kind: Tipo do match ('hex', 'rgb', 'hsl').

    Returns:
        str | None: Hex no formato '#rrggbb' ou None se não parseável.
    """
    if kind == "hex":
        return (
            _rgb_to_hex(*(_hex_to_rgb(match_text) or (0, 0, 0)))
            if _hex_to_rgb(match_text)
            else None
        )

    if kind == "rgb":
        m = re.search(r"(\d+),\s*(\d+),\s*(\d+)", match_text)
        if m:
            r, g, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
            return _rgb_to_hex(min(r, 255), min(g, 255), min(b, 255))

    if kind == "hsl":
        m = re.search(r"(\d+),\s*(\d+)%,\s*(\d+)%", match_text)
        if m:
            h, s, l_ = (
                int(m.group(1)) / 360,
                int(m.group(2)) / 100,
                int(m.group(3)) / 100,
            )
            r, g, b = colorsys.hls_to_rgb(h, l_, s)
            return _rgb_to_hex(int(r * 255), int(g * 255), int(b * 255))

    return None


class ColorPreview:
    """Gerencia swatches de cor inline no widget de texto do editor.

    Varre o documento periodicamente para detectar valores de cor e insere
    pequenos blocos coloridos ao lado de cada ocorrência. Clicar em um
    swatch abre o color picker para edição direta.

    Args:
        txt: Widget tk.Text do editor.
        theme: Tema visual ativo.
        on_edit_callback: Callback(old_text, new_text, line, col) acionado
            quando o usuário edita uma cor pelo color picker.
    """

    def __init__(
        self,
        txt: tk.Text,
        theme: Theme,
        on_edit_callback: callable | None = None,
    ) -> None:
        """Inicializa o ColorPreview.

        Args:
            txt: Widget tk.Text do editor.
            theme: Objeto Theme com as cores do tema ativo.
            on_edit_callback: Callback(old_text, new_hex, line, col) para edição de cor.
        """
        self._txt = txt
        self._theme = theme
        self._on_edit = on_edit_callback
        self._enabled = False
        self._after_id: str | None = None

        self._swatches: dict[str, tuple[int, int, str, str]] = {}
        self._swatch_counter = 0

    def set_enabled(self, enabled: bool) -> None:
        """Habilita ou desabilita os swatches de cor.

        Args:
        """
        self._enabled = enabled
        if enabled:
            self._schedule_scan()
        else:
            self.clear()

    def notify_change(self) -> None:
        """Notifica que o conteúdo do editor mudou - re-escaneia com debounce."""
        if self._enabled:
            self._schedule_scan()

    def clear(self) -> None:
        """Remove todos os marcadores de prévia de cor do widget de texto."""
        for tag in list(self._swatches.keys()):
            self._remove_swatch(tag)
        self._swatches.clear()

    def apply_theme(self, theme: Theme) -> None:
        """Aplica novo tema e re-renderiza os swatches.

        Args:
            theme: Novo objeto Theme com as cores atualizadas.
        """
        self._theme = theme
        if self._enabled:
            self.clear()
            self._schedule_scan()

    def _schedule_scan(self) -> None:
        """Agenda varredura com debounce."""
        if self._after_id:
            with contextlib.suppress(tk.TclError):
                self._txt.after_cancel(self._after_id)
        self._after_id = self._txt.after(_DEBOUNCE_MS, self._scan)

    def _scan(self) -> None:
        """Varre o documento por valores de cor e renderiza swatches."""
        self._after_id = None
        if not self._txt.winfo_exists():
            return
        if not self._enabled:
            return

        try:
            content = self._txt.get("1.0", "end-1c")
        except tk.TclError:
            return

        self.clear()

        lines = content.splitlines()
        swatch_count = 0

        for line_idx, line in enumerate(lines):
            if swatch_count >= _MAX_SWATCHES:
                break
            line_num = line_idx + 1

            for pattern, kind in _COLOR_PATTERNS:
                for m in pattern.finditer(line):
                    if swatch_count >= _MAX_SWATCHES:
                        break
                    col = m.start()
                    hex_color = _parse_color(m.group(0), kind)
                    if hex_color:
                        self._insert_swatch(
                            line_num, col, m.group(0), hex_color)
                        swatch_count += 1

            for name, hex_color in _NAMED_COLORS.items():
                pattern = re.compile(rf"\b{re.escape(name)}\b", re.IGNORECASE)
                for m in pattern.finditer(line):
                    if swatch_count >= _MAX_SWATCHES:
                        break
                    self._insert_swatch(line_num, m.start(),
                                        m.group(0), hex_color)
                    swatch_count += 1

    def _insert_swatch(
        self,
        line: int,
        col: int,
        original_text: str,
        hex_color: str,
    ) -> None:
        """Aplica tag de cor de fundo sobre o próprio texto da cor (sem inserir caracteres).

        Não modifica o conteúdo do editor - apenas aplica uma tag Tkinter
        sobre o span do valor de cor para exibir a cor como fundo do texto.

        Args:
            line: Número da linha (1-based).
            col: Coluna de início do valor de cor (0-based).
            original_text: Texto original da cor (para substituição posterior).
            hex_color: Cor em formato '#rrggbb'.
        """
        rgb = _hex_to_rgb(hex_color)
        if not rgb:
            return

        if hex_color in ("#808080",) and original_text.lower() == "transparent":
            return

        fg = _contrast_fg(*rgb)
        tag = f"{_SWATCH_TAG_PREFIX}{self._swatch_counter}"
        self._swatch_counter += 1

        end_col = col + len(original_text)
        try:
            self._txt.tag_configure(
                tag, background=hex_color, foreground=fg, relief="raised", borderwidth=1
            )
            self._txt.tag_add(tag, f"{line}.{col}", f"{line}.{end_col}")

            self._swatches[tag] = (line, col, original_text, hex_color)

            self._txt.tag_bind(
                tag, "<Button-1>", lambda _e, t=tag: self._on_swatch_click(t)
            )
            self._txt.tag_bind(
                tag, "<Enter>", lambda _e, t=tag: self._txt.config(
                    cursor="hand2")
            )
            self._txt.tag_bind(
                tag, "<Leave>", lambda _e: self._txt.config(cursor=""))
        except tk.TclError:
            pass

    def _remove_swatch(self, tag: str) -> None:
        """Remove um swatch específico (apenas a tag, sem deletar conteúdo).

        Args:
            tag: Nome da tag Tkinter do swatch a remover.
        """
        try:
            self._txt.tag_remove(tag, "1.0", "end")
            self._txt.tag_delete(tag)
        except tk.TclError:
            pass

    def _on_swatch_click(self, tag: str) -> None:
        """Abre o color picker ao clicar em um swatch.

        Args:
            tag: Nome da tag Tkinter do swatch clicado.
        """
        info = self._swatches.get(tag)
        if not info:
            return

        line, col, original_text, current_hex = info

        _hex_to_rgb(current_hex) or (128, 128, 128)

        result = tkcc.askcolor(
            color=current_hex,
            title=_t("color.picker_title", "Escolher cor"),
            initialcolor=current_hex,
        )
        if result and result[1]:
            new_hex = result[1]
            if new_hex != current_hex and self._on_edit:
                with contextlib.suppress(tk.TclError, AttributeError):
                    self._on_edit(original_text, new_hex, line, col)

            if self._enabled:
                self._schedule_scan()

    def destroy(self) -> None:
        """Cancela jobs pendentes e remove o overlay de prévia de cor."""
        if self._after_id:
            with contextlib.suppress(tk.TclError):
                self._txt.after_cancel(self._after_id)
        self.clear()
