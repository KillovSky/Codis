"""Codis - FocusBlock: foco visual no bloco de código atual.

Quando ativado, escurece (dimming) todas as linhas que **não fazem parte**
do bloco estrutural atual sob o cursor (função, método, classe ou bloco
de alto nível). O restante do código fica suavemente desfocado, guiando
o olhar do desenvolvedor para o que está editando agora.

Comportamento:
  - Detecta automaticamente o bloco (via indentação + AST) sob o cursor.
  - Aplica tag ``focus_dim`` com foreground rebaixado nas linhas de fora.
  - Atualiza em tempo real conforme o cursor se move.
  - Pode ser ativado/desativado via ``toggle()``.
  - Compatível com syntax highlight: usa ``elide=False`` para não quebrar
    a renderização existente, apenas sobrepõe a cor.

Uso::

    from src.editor.focus_block import FocusBlock
    fb = FocusBlock(text_widget, theme)
    fb.toggle()       # ativa/desativa
    fb.set_enabled(True)
    fb.destroy()
"""

from __future__ import annotations

import contextlib
import logging
import tkinter as tk
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.theme.base import Theme

logger = logging.getLogger("Codis")

_TAG_DIM = "focus_block_dim"
_DEBOUNCE = 150
_DIM_ALPHA = 0.30


def _mix_hex(fg: str, bg: str, alpha: float) -> str:
    """Mistura duas cores hex com um fator alpha (fg sobre bg).

    Args:
        fg:    Cor do foreground (#rrggbb).
        bg:    Cor do background (#rrggbb).
        alpha: Fator de opacidade do fg (0.0 = só bg, 1.0 = só fg).

    Returns:
        Cor resultante em formato '#rrggbb'.
    """
    try:
        fr, fg_, fb_ = int(fg[1:3], 16), int(fg[3:5], 16), int(fg[5:7], 16)
        br, bg_, bb = int(bg[1:3], 16), int(bg[3:5], 16), int(bg[5:7], 16)
        r = int(fr * alpha + br * (1 - alpha))
        g = int(fg_ * alpha + bg_ * (1 - alpha))
        b = int(fb_ * alpha + bb * (1 - alpha))
        return f"#{r:02x}{g:02x}{b:02x}"
    except (ValueError, IndexError):
        return fg


def _detect_block_range(lines: list[str], cursor_line: int) -> tuple[int, int]:
    """Detecta o intervalo [start, end] do bloco estrutural sob o cursor.

    Usa indentação: sobe até encontrar um nível menor e desce até o
    bloco terminar. Retorna (1, total) se a linha está no nível raiz.

    Args:
        lines:       Lista de linhas do documento.
        cursor_line: Linha atual do cursor (1-based).

    Returns:
        Tupla (start_line, end_line) ambas 1-based, inclusivo.
    """
    total = len(lines)
    idx = cursor_line - 1

    if idx < 0 or idx >= total:
        return (1, total)

    def indent_of(i: int) -> int:
        """Retorna o nível de indentação (nº de espaços) da linha ``idx``."""
        if i < 0 or i >= total:
            return 0
        line = lines[i]
        stripped = line.lstrip()
        if not stripped:
            return -1
        return len(line) - len(stripped)

    cur_indent = indent_of(idx)

    if cur_indent <= 0:
        return (1, total)

    start = idx
    for i in range(idx - 1, -1, -1):
        ind = indent_of(i)
        if ind == -1:
            continue
        if ind < cur_indent:
            start = i + 1
            start = i
            break
    else:
        start = 0

    header_indent = indent_of(start)
    end = idx
    for i in range(idx + 1, total):
        ind = indent_of(i)
        if ind == -1:
            continue
        if ind <= header_indent:
            end = i - 1
            break
    else:
        end = total - 1

    return (start + 1, end + 1)


class FocusBlock:
    """Dimming de linhas fora do bloco atual para foco visual.

    Args:
        txt:   Widget tk.Text do editor.
        theme: Tema visual ativo.
    """

    def __init__(self, txt: tk.Text, theme: Theme) -> None:
        """Inicializa o realce de bloco focado para o widget de texto ``txt``."""
        self._txt = txt
        self._theme = theme
        self._enabled = False
        self._after_id: str | None = None
        self._last_range: tuple[int, int] = (0, 0)

        self._configure_tags()

        self._txt.bind("<KeyRelease>", self._schedule, add=True)
        self._txt.bind("<ButtonRelease>", self._schedule, add=True)

    def toggle(self) -> bool:
        """Alterna o modo Focus Block.

        Returns:
            bool: True se ativou, False se desativou.
        """
        self._enabled = not self._enabled
        if self._enabled:
            self._update()
        else:
            self._clear()
        return self._enabled

    def set_enabled(self, value: bool) -> None:
        """Ativa ou desativa sem alternar."""
        if value != self._enabled:
            self.toggle()

    @property
    def is_enabled(self) -> bool:
        """Retorna ``True`` se o realce de foco de bloco está ativo."""
        return self._enabled

    def apply_theme(self, theme: Theme) -> None:
        """Reconfigura as tags com as novas cores do tema."""
        self._theme = theme
        self._configure_tags()
        if self._enabled:
            self._update()

    def destroy(self) -> None:
        """Cancela timers e remove tags."""
        if self._after_id:
            with contextlib.suppress(tk.TclError):
                self._txt.after_cancel(self._after_id)
        self._clear()

    def _configure_tags(self) -> None:
        t = self._theme
        fg = getattr(t, "fg", "#cdd6f4")
        bg = getattr(t, "bg", "#1e1e2e")
        dim_color = _mix_hex(fg, bg, _DIM_ALPHA)
        try:
            self._txt.tag_configure(
                _TAG_DIM,
                foreground=dim_color,
            )
            self._txt.tag_lower(_TAG_DIM)
        except tk.TclError:
            pass

    def _schedule(self, _e=None) -> None:
        if not self._enabled:
            return
        if self._after_id:
            with contextlib.suppress(tk.TclError):
                self._txt.after_cancel(self._after_id)
        self._after_id = self._txt.after(_DEBOUNCE, self._update)

    def _update(self) -> None:
        self._after_id = None
        if not self._txt.winfo_exists() or not self._enabled:
            return
        try:
            content = self._txt.get("1.0", "end-1c")
            cursor = self._txt.index(tk.INSERT)
            cur_line = int(cursor.split(".")[0])
        except tk.TclError:
            return

        lines = content.splitlines()
        total = len(lines)
        start, end = _detect_block_range(lines, cur_line)

        if (start, end) == self._last_range:
            return
        self._last_range = (start, end)

        with contextlib.suppress(tk.TclError):
            self._txt.tag_remove(_TAG_DIM, "1.0", "end")

        if start > 1:
            with contextlib.suppress(tk.TclError):
                self._txt.tag_add(_TAG_DIM, "1.0", f"{start}.0")

        if end < total:
            with contextlib.suppress(tk.TclError):
                self._txt.tag_add(_TAG_DIM, f"{end + 1}.0", "end")

    def _clear(self) -> None:
        self._last_range = (0, 0)
        with contextlib.suppress(tk.TclError):
            if self._txt.winfo_exists():
                self._txt.tag_remove(_TAG_DIM, "1.0", "end")
