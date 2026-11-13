"""Codis - Adaptador entre :class:`CollabSession` e um ``tk.Text``.

Aplica mudanças remotas de forma **incremental** (insert/delete pontuais),
de modo que cursor, seleção, rolagem e realce do usuário local são
preservados, em vez de reescrever o documento inteiro a cada tecla remota.

Cuidado com emoji: o Tk 8.6 conta caracteres fora do BMP (U+10000+) como
*duas* unidades de coluna, enquanto o Python conta uma. Por isso os índices
são calculados em linha/coluna a partir do texto Python, somando o excedente.
"""

from __future__ import annotations

import contextlib
import re
import tkinter as tk

from src.collab.ot import TextOp, apply_op

_ASTRAL = re.compile("[\U00010000-\U0010ffff]")


def tk_index(text: str, pos: int) -> str:
    """Índice ``linha.coluna`` do Tk para a posição ``pos`` (em code points)."""
    pos = max(0, min(pos, len(text)))
    line_start = text.rfind("\n", 0, pos) + 1
    line = text.count("\n", 0, pos) + 1
    segment = text[line_start:pos]
    column = len(segment) + len(_ASTRAL.findall(segment))
    return f"{line}.{column}"


class TkTextAdapter:
    """Expõe um ``tk.Text`` como editor sincronizável."""

    def __init__(self, widget: tk.Text) -> None:
        self._w = widget

    @property
    def widget(self) -> tk.Text:
        """O ``tk.Text`` subjacente."""
        return self._w

    def alive(self) -> bool:
        """Se o widget ainda existe."""
        try:
            return bool(self._w.winfo_exists())
        except tk.TclError:
            return False

    def get_text(self) -> str:
        """Texto completo (sem o newline final que o Tk acrescenta)."""
        try:
            return self._w.get("1.0", "end-1c")
        except tk.TclError:
            return ""

    def _writable(self):
        """Contexto que libera temporariamente um editor somente-leitura."""
        return _Writable(self._w)

    def apply_ops(self, ops: list[TextOp]) -> None:
        """Aplica ``ops`` em sequência, de forma incremental."""
        with self._writable():
            current = self.get_text()
            for op in ops:
                try:
                    if op.op == "insert":
                        self._w.insert(tk_index(current, op.pos), op.text)
                    else:
                        end = min(op.pos + op.length, len(current))
                        self._w.delete(tk_index(current, op.pos), tk_index(current, end))
                except tk.TclError:
                    return
                current = apply_op(current, op)

    def set_text(self, text: str) -> None:
        """Substitui o texto inteiro, tentando manter cursor e rolagem."""
        w = self._w
        with self._writable():
            try:
                insert = w.index("insert")
                first_view = w.yview()[0]
                w.delete("1.0", "end")
                w.insert("1.0", text)
                with contextlib.suppress(tk.TclError):
                    w.mark_set("insert", insert)
                w.yview_moveto(first_view)
            except tk.TclError:
                return


class _Writable:
    """Habilita escrita num Text desabilitado e restaura o estado depois."""

    def __init__(self, widget: tk.Text) -> None:
        self._w = widget
        self._state = "normal"

    def __enter__(self) -> None:
        with contextlib.suppress(tk.TclError):
            self._state = str(self._w.cget("state"))
            if self._state != "normal":
                self._w.configure(state="normal")

    def __exit__(self, *_exc: object) -> None:
        with contextlib.suppress(tk.TclError):
            if self._state != "normal":
                self._w.configure(state=self._state)
