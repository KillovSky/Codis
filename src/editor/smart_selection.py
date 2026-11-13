"""Codis - SmartSelection: expansão e contração de seleção semântica.

Implementa o recurso de "Expand/Shrink Selection" do VS Code:
- ``Alt+Shift+ -> `` (ou ``Ctrl+Shift+A``) expande a seleção para o próximo
  nível semântico (palavra -> expressão -> linha -> bloco -> função -> arquivo).
- ``Alt+Shift+ <- `` contrai a seleção para o nível anterior.

A expansão é feita por análise de tokens e delimitadores no texto, sem
depender de AST (funciona em qualquer linguagem).

que a contração restaure exatamente a seleção antes da última expansão.
"""

from __future__ import annotations

import logging
import re
import tkinter as tk
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger("Codis")

_BLOCK_PAIRS: dict[str, str] = {
    "(": ")",
    "[": "]",
    "{": "}",
    '"': '"',
    "'": "'",
    "`": "`",
    "«": "»",
}
_CLOSERS = set(_BLOCK_PAIRS.values())
_OPENERS = set(_BLOCK_PAIRS.keys())

_WORD_RE = re.compile(r"[\w]")

_EXPR_RE = re.compile(r"[^\s,;()[\]{}\"\'\`]")


class SmartSelection:
    """Gerencia expansão e contração semântica de seleção no editor.

    restaure exatamente a seleção anterior à última expansão.

    Args:
        txt: Widget tk.Text do editor.
    """

    def __init__(self, txt: tk.Text) -> None:
        """Inicializa o SmartSelection.

        Args:
            txt: Widget tk.Text do editor ao qual o sistema será associado.
        """
        self._txt = txt
        self._history: list[tuple[str, str]] = []

    def expand(self) -> None:
        """Expande a seleção atual para o próximo nível semântico.

        Sequência de expansão:
        1. Palavra sob o cursor
        2. Expressão (token delimitado por espaços/operadores)
        3. Conteúdo dentro de delimitadores (parênteses, colchetes, aspas)
        4. Incluindo os delimitadores
        5. Linha completa
        6. Bloco de indentação (parágrafo)
        7. Arquivo inteiro
        """
        try:
            cur_sel = self._get_current_selection()

            if cur_sel:
                self._history.append(cur_sel)
            else:
                pos = self._txt.index("insert")
                self._history.append((pos, pos))

            new_sel = self._compute_expansion(cur_sel)
            if new_sel and new_sel != cur_sel:
                self._apply_selection(*new_sel)
        except tk.TclError as exc:
            logger.debug("SmartSelection.expand error: %s", exc)

    def shrink(self) -> None:
        """Contrai a seleção para o nível anterior (desfaz o último expand)."""
        if not self._history:
            return
        prev = self._history.pop()
        try:
            self._apply_selection(*prev)
        except tk.TclError as exc:
            logger.debug("SmartSelection.shrink error: %s", exc)

    def reset_history(self) -> None:
        """Reinicia o histórico de seleção semântica ao estado inicial."""
        self._history.clear()

    def _compute_expansion(
        self, current: tuple[str, str] | None
    ) -> tuple[str, str] | None:
        """Calcula a próxima expansão a partir da seleção corrente.

        Args:
            current: Tupla (start_idx, end_idx) da seleção atual, ou None.

        Returns:
            tuple[str, str] | None: Nova seleção expandida ou None se já é máxima.
        """
        if not current or current[0] == current[1]:
            return self._select_word()

        start, end = current

        for fn in (
            self._expand_to_expression,
            self._expand_to_inner_block,
            self._expand_to_outer_block,
            self._expand_to_line,
            self._expand_to_paragraph,
            self._expand_to_all,
        ):
            result = fn(start, end)
            if result and result != current:
                return result

        return None

    def _select_word(self) -> tuple[str, str] | None:
        """Seleciona a palavra inteira sob o cursor.

        Returns:
            tuple[str, str] | None: Índices de início e fim da palavra.
        """
        try:
            idx = self._txt.index("insert")
            line_num, col = map(int, idx.split("."))
            line_text = self._txt.get(f"{line_num}.0", f"{line_num}.end")
            if not line_text or col >= len(line_text):
                return (idx, idx)

            char = line_text[col]
            if not _WORD_RE.match(char):
                if col > 0 and _WORD_RE.match(line_text[col - 1]):
                    col -= 1
                else:
                    return (idx, idx)

            start = col
            while start > 0 and _WORD_RE.match(line_text[start - 1]):
                start -= 1

            end = col + 1
            while end < len(line_text) and _WORD_RE.match(line_text[end]):
                end += 1

            return (f"{line_num}.{start}", f"{line_num}.{end}")
        except (tk.TclError, ValueError):
            return None

    def _expand_to_expression(self, start: str, end: str) -> tuple[str, str] | None:
        """Expande a seleção para incluir a expressão completa (token).

        Args:
            start: Índice Tkinter do início da seleção.
            end: Índice Tkinter do fim da seleção.

        Returns:
            tuple[str, str] | None: Seleção expandida ou None.
        """
        try:
            sline, scol = map(int, start.split("."))
            eline, ecol = map(int, end.split("."))
            if sline != eline:
                return None
            line_text = self._txt.get(f"{sline}.0", f"{sline}.end")
            if not line_text:
                return None

            new_start = scol
            while new_start > 0 and _EXPR_RE.match(line_text[new_start - 1]):
                new_start -= 1

            new_end = ecol
            while new_end < len(line_text) and _EXPR_RE.match(line_text[new_end]):
                new_end += 1

            new = (f"{sline}.{new_start}", f"{sline}.{new_end}")
            return new if new != (start, end) else None
        except (tk.TclError, ValueError):
            return None

    def _expand_to_inner_block(self, start: str, end: str) -> tuple[str, str] | None:
        """Expande para o conteúdo interno do bloco delimitador mais próximo.

        Args:
            start: Índice Tkinter do início da seleção.
            end: Índice Tkinter do fim da seleção.

        Returns:
            tuple[str, str] | None: Seleção expandida incluindo apenas conteúdo.
        """
        try:
            content = self._txt.get("1.0", "end-1c")
            start_flat = self._idx_to_flat(start, content)
            end_flat = self._idx_to_flat(end, content)

            open_pos, close_pos, _ = (
                self._find_enclosing_block(  # pylint: disable=no-value-for-parameter
                    content, start_flat, end_flat
                )
            )
            if open_pos is None:
                return None

            new_start = self._flat_to_idx(open_pos + 1, content)
            new_end = self._flat_to_idx(close_pos, content)
            new = (new_start, new_end)
            return new if new != (start, end) else None
        except (tk.TclError, ValueError):
            return None

    def _expand_to_outer_block(self, start: str, end: str) -> tuple[str, str] | None:
        """Expande para incluir os próprios delimitadores do bloco.

        Args:
            start: Índice Tkinter do início da seleção.
            end: Índice Tkinter do fim da seleção.

        Returns:
            tuple[str, str] | None: Seleção expandida incluindo delimitadores.
        """
        try:
            content = self._txt.get("1.0", "end-1c")
            start_flat = self._idx_to_flat(start, content)
            end_flat = self._idx_to_flat(end, content)

            open_pos, close_pos, _ = (
                self._find_enclosing_block(  # pylint: disable=no-value-for-parameter
                    content, start_flat, end_flat
                )
            )
            if open_pos is None:
                return None

            new_start = self._flat_to_idx(open_pos, content)
            new_end = self._flat_to_idx(close_pos + 1, content)
            new = (new_start, new_end)
            return new if new != (start, end) else None
        except (tk.TclError, ValueError):
            return None

    def _expand_to_line(  # pylint: disable=bad-staticmethod-argument
        self, start: str, end: str
    ) -> tuple[str, str] | None:
        """Expande a seleção para a(s) linha(s) completa(s).

        Args:
            start: Índice Tkinter do início da seleção.
            end: Índice Tkinter do fim da seleção.

        Returns:
            tuple[str, str] | None: Seleção cobrindo as linhas completas.
        """
        try:
            sline = int(start.split(".")[0])
            eline = int(end.split(".")[0])
            new = (f"{sline}.0", f"{eline}.end")
            return new if new != (start, end) else None
        except ValueError:
            return None

    def _expand_to_paragraph(self, start: str, end: str) -> tuple[str, str] | None:
        """Expande para o bloco de indentação (parágrafo semântico).

        Encontra as linhas vizinhas com indentação maior ou igual à linha
        de início para definir o bloco.

        Args:
            start: Índice Tkinter do início da seleção.
            end: Índice Tkinter do fim da seleção.

        Returns:
            tuple[str, str] | None: Seleção cobrindo o bloco de indentação.
        """
        try:
            sline = int(start.split(".")[0])
            eline = int(end.split(".")[0])

            ref_text = self._txt.get(f"{sline}.0", f"{sline}.end")
            ref_indent = len(ref_text) - len(ref_text.lstrip())

            new_sline = sline
            total = int(self._txt.index("end").split(".")[0])
            line = sline - 1
            while line >= 1:
                raw = self._txt.get(f"{line}.0", f"{line}.end")
                if not raw.strip():
                    break
                indent = len(raw) - len(raw.lstrip())
                if indent < ref_indent:
                    break
                new_sline = line
                line -= 1

            new_eline = eline
            line = eline + 1
            while line <= total:
                raw = self._txt.get(f"{line}.0", f"{line}.end")
                if not raw.strip():
                    break
                indent = len(raw) - len(raw.lstrip())
                if indent < ref_indent:
                    break
                new_eline = line
                line += 1

            new = (f"{new_sline}.0", f"{new_eline}.end")
            return new if new != (start, end) else None
        except (tk.TclError, ValueError):
            return None

    def _expand_to_all(self, start: str, end: str) -> tuple[str, str] | None:
        """Expande para o arquivo inteiro.

        Args:
            start: Índice Tkinter do início da seleção (não utilizado).
            end: Índice Tkinter do fim da seleção (não utilizado).

        Returns:
        """
        _ = start, end
        try:
            new = ("1.0", self._txt.index("end-1c"))
            return new
        except tk.TclError:
            return None

    def _find_enclosing_block(  # pylint: disable=bad-staticmethod-argument
        self,
        content: str,
        start_flat: int,
        end_flat: int,
    ) -> tuple[int | None, int | None, str | None]:
        """Encontra o par de delimitadores que envolve a seleção.

        Args:
            content: Conteúdo completo do documento como string plana.
            start_flat: Posição plana do início da seleção.
            end_flat: Posição plana do fim da seleção.

        Returns:
            tuple: (posição_abertura, posição_fechamento, char_opener) ou (None, None, None).
        """

        for pos in range(start_flat - 1, -1, -1):
            ch = content[pos]
            if ch in _OPENERS:
                closer = _BLOCK_PAIRS[ch]

                depth = 1
                for fpos in range(pos + 1, len(content)):
                    fc = content[fpos]
                    if fc == ch and ch not in ('"', "'", "`"):
                        depth += 1
                    elif fc == closer:
                        depth -= 1
                        if depth == 0:
                            if fpos >= end_flat:
                                return (pos, fpos, ch)
                            break

        return (None, None, None)

    @staticmethod
    def _idx_to_flat(idx: str, content: str) -> int:
        """Converte índice Tkinter (line.col) para posição plana na string.

        Args:
            idx: Índice no formato 'linha.coluna'.
            content: Conteúdo completo do documento.

        Returns:
            int: Posição plana (0-based) na string de conteúdo.
        """
        line, col = map(int, idx.split("."))
        lines = content.splitlines(keepends=True)
        flat = sum(len(l_) for l_ in lines[: line - 1])
        return flat + col

    @staticmethod
    def _flat_to_idx(flat: int, content: str) -> str:
        """Converte posição plana para índice Tkinter (line.col).

        Args:
            flat: Posição plana (0-based) na string de conteúdo.
            content: Conteúdo completo do documento.

        Returns:
            str: Índice no formato 'linha.coluna'.
        """
        lines = content.splitlines(keepends=True)
        pos = 0
        for i, line in enumerate(lines):
            if pos + len(line) > flat:
                return f"{i + 1}.{flat - pos}"
            pos += len(line)

        return f"{len(lines)}.{flat - pos}"

    def _get_current_selection(self) -> tuple[str, str] | None:
        """Retorna a seleção atual do editor, ou None se não há seleção.

        Returns:
            tuple[str, str] | None: Tupla (start_idx, end_idx) ou None.
        """
        try:
            sel = self._txt.tag_ranges("sel")
            if sel:
                return (str(sel[0]), str(sel[1]))
        except tk.TclError:
            pass
        return None

    def _apply_selection(self, start: str, end: str) -> None:
        """Aplica uma seleção no editor.

        Args:
            start: Índice Tkinter do início da seleção.
            end: Índice Tkinter do fim da seleção.
        """
        self._txt.tag_remove("sel", "1.0", "end")
        self._txt.tag_add("sel", start, end)
        self._txt.mark_set("insert", end)
        self._txt.see(start)
