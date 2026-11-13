"""Codis - Multi-Cursor.

Permite editar em múltiplos pontos do texto simultaneamente,
assim como VSCode, Sublime Text e JetBrains IDEs.

Ativação
--------
- Alt+Click -> adiciona cursor na posição clicada
- Ctrl+Alt+↑/↓ -> adiciona cursor acima/abaixo
- Ctrl+D -> seleciona próxima ocorrência da palavra sob cursor
- Ctrl+Shift+L -> seleciona TODAS as ocorrências
- Escape -> cancela todos os cursores extras

Comportamento
-------------
- Cada cursor tem sua própria "ancora" (índice Tk armazenado como tag)
- Ao digitar/apagar, a ação é replicada em todos os cursores
- Os cursores são exibidos como marcas coloridas no widget Text
"""

from __future__ import annotations

import contextlib
import re
import tkinter as tk
from collections.abc import Callable

_TAG_PREFIX = "mc_cursor_"
_TAG_SEL = "mc_sel_"
_CURSOR_COLOR = "#ff6b6b"
_SEL_COLOR = "#3d2b2b"

_CURSOR_TAG_CFG = {"background": _CURSOR_COLOR, "foreground": "#1a1a2e"}
_SEL_TAG_CFG = {"background": _SEL_COLOR}


class MultiCursorManager:
    """Gerencia múltiplos cursores num widget Text do Tkinter.

    Uso
    ---
    >>> mgr = MultiCursorManager(text_widget)
    >>> mgr.activate()   # liga os bindings
    >>> mgr.deactivate() # desliga e remove todos os cursores extras
    """

    def __init__(self, text: tk.Text) -> None:
        """Inicializa o gerenciador de multi-cursor sobre o widget ``txt``."""
        self._text = text
        self._cursors: list[str] = []
        self._active = False
        self._bindings: list[tuple[str, str]] = []

    def activate(self) -> None:
        """Ativa o modo multi-cursor e registra os bindings de teclado."""
        if self._active:
            return
        self._active = True
        self._bind("<Alt-Button-1>", self._on_alt_click)
        self._bind("<Control-Alt-Up>", lambda e: self._add_cursor_relative(-1))
        self._bind("<Control-Alt-Down>",
                   lambda e: self._add_cursor_relative(+1))
        self._bind("<Control-d>", self._select_next_occurrence)
        self._bind("<Control-Shift-L>", self._select_all_occurrences)
        self._bind("<Escape>", self._on_escape)
        self._bind("<Key>", self._on_key)
        self._bind("<BackSpace>", self._on_backspace)
        self._bind("<Delete>", self._on_delete)

    def deactivate(self) -> None:
        """Desativa o modo multi-cursor e remove todos os cursores extras."""
        self._active = False
        self._clear_all()
        for seq, fid in self._bindings:
            with contextlib.suppress(tk.TclError):
                self._text.unbind(seq, fid)
        self._bindings.clear()

    def add_cursor_at(self, index: str) -> None:
        """Adiciona um cursor extra na posição Tk `index`."""
        canonical = self._text.index(index)
        if canonical in self._cursors:
            return
        self._cursors.append(canonical)
        self._draw_cursor(canonical, len(self._cursors) - 1)

    def _bind(self, seq: str, handler: Callable) -> None:
        fid = self._text.bind(seq, handler, "+")
        self._bindings.append((seq, fid))

    def _clear_all(self) -> None:
        for i in range(len(self._cursors)):
            self._remove_cursor_tag(i)
        self._cursors.clear()

    def _tag_name(self, idx: int) -> str:
        return f"{_TAG_PREFIX}{idx}"

    def _sel_tag_name(self, idx: int) -> str:
        return f"{_TAG_SEL}{idx}"

    def _draw_cursor(self, index: str, cursor_idx: int) -> None:
        tag = self._tag_name(cursor_idx)
        with contextlib.suppress(tk.TclError):
            end = self._text.index(f"{index} +1c")
            self._text.tag_add(tag, index, end)
            self._text.tag_configure(tag, **_CURSOR_TAG_CFG)
            self._text.tag_raise(tag)

    def _remove_cursor_tag(self, cursor_idx: int) -> None:
        tag = self._tag_name(cursor_idx)
        sel_tag = self._sel_tag_name(cursor_idx)
        with contextlib.suppress(tk.TclError):
            self._text.tag_delete(tag)
            self._text.tag_delete(sel_tag)

    def _redraw_all(self) -> None:
        for i, pos in enumerate(self._cursors):
            self._remove_cursor_tag(i)
            self._draw_cursor(pos, i)

    def _on_alt_click(self, event: tk.Event) -> str:
        """Alt+Click: adiciona cursor na posição clicada."""
        idx = self._text.index(f"@{event.x},{event.y}")
        self.add_cursor_at(idx)
        return "break"

    def _add_cursor_relative(self, delta: int) -> str:
        """Adiciona cursor uma linha acima (delta=-1) ou abaixo (delta=+1) do cursor principal."""
        current = self._text.index("insert")
        line, col = map(int, current.split("."))
        new_line = max(1, line + delta)
        new_idx = f"{new_line}.{col}"
        with contextlib.suppress(tk.TclError):
            end_of_line = self._text.index(f"{new_line}.end")
            end_col = int(end_of_line.split(".")[1])
            if col > end_col:
                new_idx = end_of_line
        self.add_cursor_at(new_idx)
        return "break"

    def _select_next_occurrence(self, event: tk.Event) -> str:
        """Ctrl+D: adiciona cursor na próxima ocorrência da palavra selecionada/sob cursor."""
        word = self._get_word_under_cursor()
        if not word:
            return "break"

        start_from = "insert"
        if self._cursors:
            start_from = self._cursors[-1]

        found = self._text.search(
            word, f"{start_from} +1c", stopindex="end", nocase=False
        )
        if not found:
            found = self._text.search(word, "1.0", stopindex=start_from)
        if found:
            self.add_cursor_at(found)
        return "break"

    def _select_all_occurrences(self, event: tk.Event) -> str:
        """Ctrl+Shift+L: seleciona todas as ocorrências."""
        word = self._get_word_under_cursor()
        if not word:
            return "break"

        self._clear_all()
        pos = "1.0"
        while True:
            found = self._text.search(word, pos, stopindex="end", nocase=False)
            if not found:
                break
            self.add_cursor_at(found)
            self._cursors[-1] = found
            self._draw_cursor(found, len(self._cursors) - 1)
            pos = f"{found} +{len(word)}c"

        return "break"

    def _get_word_under_cursor(self) -> str:
        """Retorna a palavra sob o cursor principal, ou o texto selecionado."""
        with contextlib.suppress(tk.TclError):
            if self._text.tag_ranges("sel"):
                return self._text.get("sel.first", "sel.last")
        idx = self._text.index("insert")
        line_text = self._text.get(f"{idx} linestart", f"{idx} lineend")
        col = int(idx.split(".")[1])
        before = line_text[:col]
        after = line_text[col:]
        m_before = re.search(r"\w+$", before)
        m_after = re.match(r"\w+", after)
        word = (m_before.group() if m_before else "") + (
            m_after.group() if m_after else ""
        )
        return word

    def _on_escape(self, event: tk.Event) -> str | None:
        if self._cursors:
            self._clear_all()
            return "break"
        return None

    def _on_key(self, event: tk.Event) -> str | None:
        """Replica teclas imprimíveis em todos os cursores extras."""
        if not self._cursors:
            return None
        char = event.char
        if not char or not char.isprintable():
            return None

        sorted_cursors = sorted(
            self._cursors, key=self._index_key, reverse=True)

        for pos in sorted_cursors:
            with contextlib.suppress(tk.TclError):
                self._text.insert(pos, char)

        self._update_cursor_positions(+1)
        self._redraw_all()
        return None

    def _on_backspace(self, event: tk.Event) -> str | None:
        if not self._cursors:
            return None
        sorted_cursors = sorted(
            self._cursors, key=self._index_key, reverse=True)
        for pos in sorted_cursors:
            with contextlib.suppress(tk.TclError):
                prev = self._text.index(f"{pos} -1c")
                if prev != pos:
                    self._text.delete(prev, pos)
        self._update_cursor_positions(-1)
        self._redraw_all()
        return None

    def _on_delete(self, event: tk.Event) -> str | None:
        if not self._cursors:
            return None
        sorted_cursors = sorted(
            self._cursors, key=self._index_key, reverse=True)
        for pos in sorted_cursors:
            with contextlib.suppress(tk.TclError):
                next_pos = self._text.index(f"{pos} +1c")
                if next_pos != pos:
                    self._text.delete(pos, next_pos)
        self._redraw_all()
        return None

    def _update_cursor_positions(self, char_delta: int) -> None:
        """Atualiza as posições de todos os cursores extras após inserção/remoção."""
        updated: list[str] = []
        for pos in self._cursors:
            with contextlib.suppress(tk.TclError):
                if char_delta > 0:
                    new_pos = self._text.index(f"{pos} +{char_delta}c")
                elif char_delta < 0:
                    new_pos = self._text.index(f"{pos} {char_delta}c")
                else:
                    new_pos = pos
                updated.append(new_pos)
        self._cursors[:] = updated

    @staticmethod
    def _index_key(index: str) -> tuple[int, int]:
        """Converte índice Tk 'line.col' para tupla comparável."""
        try:
            line, col = index.split(".")
            return (int(line), int(col))
        except ValueError:
            return (0, 0)
