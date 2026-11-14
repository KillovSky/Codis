"""Emulador de terminal VT100/xterm em Python puro, sem dependência de Tkinter.

``VTScreen`` interpreta o fluxo de bytes (já decodificado como texto) de um
pseudo-terminal e mantém a grade de células, o cursor, os modos e as linhas
que saíram do topo da tela. A camada de interface só precisa ler as linhas
alteradas (``take_dirty``) e as roladas para o histórico (``take_scrolled``).

Suporte principal: movimento de cursor, apagamento, inserção e remoção de
linhas e caracteres, regiões de rolagem, tela alternativa, SGR com 16, 256 e
24 bits, caracteres largos, conjunto de linhas do DEC, modos de cursor, colagem
com colchetes e respostas a consultas (posição do cursor e atributos do
dispositivo).
"""

from __future__ import annotations

import re
import unicodedata
from collections import deque

DEFAULT_COLOR = -1

FLAG_BOLD = 1
FLAG_DIM = 2
FLAG_ITALIC = 4
FLAG_UNDERLINE = 8
FLAG_REVERSE = 16
FLAG_HIDDEN = 32
FLAG_STRIKE = 64

Attr = tuple[int, int, int]
Run = tuple[str, int]

_PLAIN = re.compile(r"[^\x00-\x1f\x7f\x80-\x9f]+")
_CSI = re.compile(r"\x1b\[([0-?]*)([ -/]*)([@-~])")
_OSC = re.compile(r"\x1b\]([^\x07\x1b]*)(?:\x07|\x1b\\)")
_ESC_SIMPLE = re.compile(r"\x1b([ -/]*)([0-~])")
_CSI_PREFIX = re.compile(r"\x1b\[[0-?]*[ -/]*\Z")
_OSC_PREFIX = re.compile(r"\x1b\][^\x07\x1b]*\x1b?\Z")
_ESC_PREFIX = re.compile(r"\x1b[ -/]*\Z")
# Corpo de DCS/SOS/PM/APC em "loop desenrolado": corridas de não-ESC separadas por
# ESC que não inicia o terminador ST (ESC \). Cada ESC é literal, então não há
# ambiguidade entre iterações e o casamento é linear (sem backtracking exponencial).
_STRING_BODY = r"[^\x07\x1b]*(?:\x1b(?!\\)[^\x07\x1b]*)*"
_STRING_TERMINATED = re.compile(r"\x1b[PX^_]" + _STRING_BODY + r"(?:\x1b\\|\x07)")
_STRING_PREFIX = re.compile(r"\x1b[PX^_]" + _STRING_BODY + r"\Z")

_MAX_PENDING = 8192
_MAX_SCROLLED = 5000

_DEC_SPECIAL = {
    "`": "\u25c6", "a": "\u2592", "f": "\u00b0", "g": "\u00b1", "j": "\u2518",
    "k": "\u2510", "l": "\u250c", "m": "\u2514", "n": "\u253c", "o": "\u23ba",
    "p": "\u23bb", "q": "\u2500", "r": "\u23bc", "s": "\u23bd", "t": "\u251c",
    "u": "\u2524", "v": "\u2534", "w": "\u252c", "x": "\u2502", "y": "\u2264",
    "z": "\u2265", "{": "\u03c0", "|": "\u2260", "}": "\u00a3", "~": "\u00b7",
}


def char_width(ch: str) -> int:
    """Largura em colunas de ``ch``: 0 (combinante), 1 ou 2 (largo)."""
    if ch < "\u0300":
        return 1
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


class _Row:
    """Uma linha da grade: caracteres e ids de atributo por coluna."""

    __slots__ = ("attrs", "chars")

    def __init__(self, cols: int, blank_attr: int = 0) -> None:
        self.chars: list[str] = [" "] * cols
        self.attrs: list[int] = [blank_attr] * cols

    def resize(self, cols: int) -> None:
        n = len(self.chars)
        if cols < n:
            del self.chars[cols:]
            del self.attrs[cols:]
        elif cols > n:
            self.chars.extend([" "] * (cols - n))
            self.attrs.extend([0] * (cols - n))

    def copy(self) -> _Row:
        other = _Row(0)
        other.chars = self.chars[:]
        other.attrs = self.attrs[:]
        return other


class VTScreen:
    """Estado e interpretador de um terminal virtual."""

    def __init__(self, rows: int = 24, cols: int = 80) -> None:
        self.rows = max(1, rows)
        self.cols = max(1, cols)
        self.attr_table: list[Attr] = [(DEFAULT_COLOR, DEFAULT_COLOR, 0)]
        self._attr_ids: dict[Attr, int] = {self.attr_table[0]: 0}
        self.title = ""
        self.cwd = ""
        self.responses: list[bytes] = []
        self._pending = ""
        self._scrolled: deque[tuple[list[str], list[int]]] = deque(maxlen=_MAX_SCROLLED)
        self._dirty: set[int] = set()
        self.all_dirty = True
        self._reset_state()

    def _reset_state(self) -> None:
        self.grid = [_Row(self.cols) for _ in range(self.rows)]
        self._saved_main: tuple[list[_Row], int, int] | None = None
        self.alt_screen = False
        self.cx = 0
        self.cy = 0
        self._wrap_pending = False
        self._pen: Attr = (DEFAULT_COLOR, DEFAULT_COLOR, 0)
        self._pen_id = 0
        self._saved_cursor = (0, 0, self._pen, 0, ("B", "B"))
        self.top = 0
        self.bottom = self.rows - 1
        self.app_cursor = False
        self.app_keypad = False
        self.autowrap = True
        self.cursor_visible = True
        self.bracketed_paste = False
        self.focus_events = False
        self.mouse_mode = 0
        self.mouse_sgr = False
        self.insert_mode = False
        self.origin_mode = False
        self.tabs = {c for c in range(0, self.cols, 8)}
        self._charsets = ["B", "B"]
        self._gl = 0
        self.all_dirty = True

    def reset(self) -> None:
        """Reinicia a tela e os modos (equivalente a ``ESC c``)."""
        self._reset_state()

    def _attr_id(self, attr: Attr) -> int:
        found = self._attr_ids.get(attr)
        if found is None:
            found = len(self.attr_table)
            self.attr_table.append(attr)
            self._attr_ids[attr] = found
        return found

    def take_dirty(self) -> tuple[bool, set[int]]:
        """Devolve ``(tudo_sujo, linhas_sujas)`` e zera o controle de alterações."""
        full, rows = self.all_dirty, self._dirty
        self.all_dirty = False
        self._dirty = set()
        return full, rows

    def take_scrolled(self) -> list[tuple[list[str], list[int]]]:
        """Linhas que saíram pelo topo da tela principal desde a última chamada."""
        out = list(self._scrolled)
        self._scrolled.clear()
        return out

    def take_responses(self) -> bytes:
        """Bytes a enviar de volta ao programa (respostas a consultas)."""
        out = b"".join(self.responses)
        self.responses.clear()
        return out

    def row_runs(self, r: int, min_len: int = 0) -> list[Run]:
        """Linha ``r`` como trechos ``(texto, atributo)``, sem espaços finais padrão.

        ``min_len`` garante pelo menos essa quantidade de colunas (usado para
        desenhar o cursor depois do fim do texto).
        """
        row = self.grid[r]
        chars, attrs = row.chars, row.attrs
        if not any(attrs):
            text = "".join(chars).rstrip(" ")
            if len(text) < min_len:
                text += " " * (min_len - len(text))
            return [(text, 0)] if text else []
        end = len(chars)
        while end > min_len and chars[end - 1] == " " and attrs[end - 1] == 0:
            end -= 1
        runs: list[Run] = []
        start = 0
        while start < end:
            attr = attrs[start]
            stop = start + 1
            while stop < end and attrs[stop] == attr:
                stop += 1
            text = "".join(chars[start:stop])
            if text:
                runs.append((text, attr))
            start = stop
        return runs

    def text_col(self, r: int, col: int) -> int:
        """Índice de caractere (no texto da linha) correspondente à célula ``col``.

        Células de continuação de caracteres largos não têm texto, e marcas
        combinantes acrescentam caracteres, então os índices diferem das colunas.
        """
        return sum(len(c) for c in self.grid[r].chars[:col])

    def line_text(self, r: int) -> str:
        """Texto simples da linha ``r`` sem espaços à direita."""
        return "".join(self.grid[r].chars).rstrip()

    def text(self) -> str:
        """Conteúdo visível da tela, uma linha por fileira."""
        return "\n".join(self.line_text(r) for r in range(self.rows))

    @staticmethod
    def runs_of(chars: list[str], attrs: list[int]) -> list[Run]:
        """Converte uma linha rolada (listas paralelas) em trechos."""
        if not any(attrs):
            text = "".join(chars).rstrip(" ")
            return [(text, 0)] if text else []
        end = len(chars)
        while end > 0 and chars[end - 1] == " " and attrs[end - 1] == 0:
            end -= 1
        runs: list[Run] = []
        start = 0
        while start < end:
            attr = attrs[start]
            stop = start + 1
            while stop < end and attrs[stop] == attr:
                stop += 1
            text = "".join(chars[start:stop])
            if text:
                runs.append((text, attr))
            start = stop
        return runs

    def feed(self, data: str) -> None:
        """Interpreta ``data``; sequências incompletas ficam para a próxima chamada."""
        if self._pending:
            data = self._pending + data
            self._pending = ""
        i, n = 0, len(data)
        plain_match = _PLAIN.match
        while i < n:
            ch = data[i]
            if ch >= " " and ch != "\x7f" and not ("\x80" <= ch <= "\x9f"):
                m = plain_match(data, i)
                self._put_text(m.group(0))
                i = m.end()
                continue
            if ch == "\x1b":
                consumed = self._escape(data, i)
                if consumed < 0:
                    rest = data[i:]
                    if len(rest) <= _MAX_PENDING:
                        self._pending = rest
                    return
                i = consumed
                continue
            self._control(ch)
            i += 1

    def _control(self, ch: str) -> None:
        if ch == "\n" or ch == "\x0b" or ch == "\x0c":
            self._index()
        elif ch == "\r":
            self.cx = 0
            self._wrap_pending = False
        elif ch == "\x08":
            if self.cx > 0:
                self.cx -= 1
            self._wrap_pending = False
        elif ch == "\t":
            self._tab(1)
        elif ch == "\x0e":
            self._gl = 1
        elif ch == "\x0f":
            self._gl = 0

    def _tab(self, count: int) -> None:
        for _ in range(count):
            nxt = self.cx + 1
            while nxt < self.cols - 1 and nxt not in self.tabs:
                nxt += 1
            self.cx = min(nxt, self.cols - 1)
        self._wrap_pending = False

    def _put_text(self, text: str) -> None:
        charset = self._charsets[self._gl]
        if charset == "0":
            text = "".join(_DEC_SPECIAL.get(c, c) for c in text)
        if text.isascii() and not self.insert_mode:
            self._put_ascii(text)
            return
        pen = self._pen_id
        for ch in text:
            width = char_width(ch)
            if width == 0:
                self._combine(ch)
                continue
            if self._wrap_pending:
                if self.autowrap:
                    self.cx = 0
                    self._index()
                self._wrap_pending = False
            if self.cx + width > self.cols:
                if self.autowrap:
                    self.cx = 0
                    self._index()
                else:
                    self.cx = self.cols - width
            row = self.grid[self.cy]
            if self.insert_mode:
                self._insert_chars(width)
            self._clear_wide_overlap(row, self.cx, width)
            row.chars[self.cx] = ch
            row.attrs[self.cx] = pen
            if width == 2:
                row.chars[self.cx + 1] = ""
                row.attrs[self.cx + 1] = pen
            self._dirty.add(self.cy)
            self.cx += width
            if self.cx >= self.cols:
                self.cx = self.cols - 1
                self._wrap_pending = True

    def _put_ascii(self, text: str) -> None:
        """Escreve texto ASCII em trechos por linha, sem tratar caractere a caractere."""
        pen = self._pen_id
        i, n = 0, len(text)
        cols = self.cols
        while i < n:
            if self._wrap_pending:
                if self.autowrap:
                    self.cx = 0
                    self._index()
                self._wrap_pending = False
            row = self.grid[self.cy]
            room = cols - self.cx
            take = min(room, n - i)
            if not self.autowrap and n - i > room:
                seg = text[i:i + room - 1] + text[-1]
                i = n
            else:
                seg = text[i:i + take]
                i += take
            width = len(seg)
            self._clear_wide_overlap(row, self.cx, width)
            row.chars[self.cx:self.cx + width] = seg
            row.attrs[self.cx:self.cx + width] = [pen] * width
            self._dirty.add(self.cy)
            self.cx += width
            if self.cx >= cols:
                self.cx = cols - 1
                self._wrap_pending = True

    def _combine(self, ch: str) -> None:
        col = self.cx - 1 if not self._wrap_pending else self.cx
        row = self.grid[self.cy]
        while col > 0 and row.chars[col] == "":
            col -= 1
        if 0 <= col < self.cols:
            row.chars[col] += ch
            self._dirty.add(self.cy)

    @staticmethod
    def _clear_wide_overlap(row: _Row, col: int, width: int) -> None:
        if row.chars[col] == "" and col > 0:
            row.chars[col - 1] = " "
        end = col + width
        if end < len(row.chars) and row.chars[end] == "":
            row.chars[end] = " "

    def _index(self) -> None:
        if self.cy == self.bottom:
            self._scroll_up(1)
        elif self.cy < self.rows - 1:
            self.cy += 1
        self._wrap_pending = False

    def _reverse_index(self) -> None:
        if self.cy == self.top:
            self._scroll_down(1)
        elif self.cy > 0:
            self.cy -= 1
        self._wrap_pending = False

    def _scroll_up(self, n: int) -> None:
        n = min(n, self.bottom - self.top + 1)
        if n <= 0:
            return
        keep_history = not self.alt_screen and self.top == 0
        for _ in range(n):
            gone = self.grid.pop(self.top)
            if keep_history:
                self._scrolled.append((gone.chars, gone.attrs))
            self.grid.insert(self.bottom, _Row(self.cols))
        self._mark_range(self.top, self.bottom)

    def _scroll_down(self, n: int) -> None:
        n = min(n, self.bottom - self.top + 1)
        if n <= 0:
            return
        for _ in range(n):
            self.grid.pop(self.bottom)
            self.grid.insert(self.top, _Row(self.cols))
        self._mark_range(self.top, self.bottom)

    def _mark_range(self, first: int, last: int) -> None:
        self._dirty.update(range(first, last + 1))

    def _blank_row(self, r: int) -> None:
        self.grid[r] = _Row(self.cols, self._erase_attr())
        self._dirty.add(r)

    def _erase_attr(self) -> int:
        fg, bg, _flags = self._pen
        if bg == DEFAULT_COLOR:
            return 0
        return self._attr_id((DEFAULT_COLOR, bg, 0))

    def _insert_chars(self, n: int) -> None:
        row = self.grid[self.cy]
        n = min(n, self.cols - self.cx)
        blank = self._erase_attr()
        row.chars[self.cx:] = [" "] * n + row.chars[self.cx: self.cols - n]
        row.attrs[self.cx:] = [blank] * n + row.attrs[self.cx: self.cols - n]
        self._dirty.add(self.cy)

    def _delete_chars(self, n: int) -> None:
        row = self.grid[self.cy]
        n = min(n, self.cols - self.cx)
        blank = self._erase_attr()
        row.chars[self.cx:] = row.chars[self.cx + n:] + [" "] * n
        row.attrs[self.cx:] = row.attrs[self.cx + n:] + [blank] * n
        self._dirty.add(self.cy)

    def _erase_chars(self, n: int) -> None:
        row = self.grid[self.cy]
        end = min(self.cols, self.cx + n)
        blank = self._erase_attr()
        for c in range(self.cx, end):
            row.chars[c] = " "
            row.attrs[c] = blank
        self._dirty.add(self.cy)

    def _erase_line(self, mode: int) -> None:
        row = self.grid[self.cy]
        blank = self._erase_attr()
        if mode == 0:
            span = range(self.cx, self.cols)
        elif mode == 1:
            span = range(0, min(self.cx + 1, self.cols))
        else:
            span = range(self.cols)
        for c in span:
            row.chars[c] = " "
            row.attrs[c] = blank
        self._dirty.add(self.cy)

    def _erase_display(self, mode: int) -> None:
        if mode == 0:
            self._erase_line(0)
            for r in range(self.cy + 1, self.rows):
                self._blank_row(r)
        elif mode == 1:
            for r in range(0, self.cy):
                self._blank_row(r)
            self._erase_line(1)
        elif mode == 2:
            for r in range(self.rows):
                self._blank_row(r)
        elif mode == 3:
            self._scrolled.clear()
            self.clear_history_requested = True

    clear_history_requested = False

    def _insert_lines(self, n: int) -> None:
        if not self.top <= self.cy <= self.bottom:
            return
        n = min(n, self.bottom - self.cy + 1)
        for _ in range(n):
            self.grid.pop(self.bottom)
            self.grid.insert(self.cy, _Row(self.cols, self._erase_attr()))
        self._mark_range(self.cy, self.bottom)
        self.cx = 0
        self._wrap_pending = False

    def _delete_lines(self, n: int) -> None:
        if not self.top <= self.cy <= self.bottom:
            return
        n = min(n, self.bottom - self.cy + 1)
        for _ in range(n):
            self.grid.pop(self.cy)
            self.grid.insert(self.bottom, _Row(self.cols, self._erase_attr()))
        self._mark_range(self.cy, self.bottom)
        self.cx = 0
        self._wrap_pending = False

    def _escape(self, data: str, i: int) -> int:
        """Processa a sequência em ``data[i]``; devolve o próximo índice ou -1 se incompleta."""
        if i + 1 >= len(data):
            return -1
        nxt = data[i + 1]
        if nxt == "[":
            m = _CSI.match(data, i)
            if m:
                self._csi(m.group(1), m.group(2), m.group(3))
                return m.end()
            return -1 if _CSI_PREFIX.match(data, i) else self._skip_bad(data, i)
        if nxt == "]":
            m = _OSC.match(data, i)
            if m:
                self._osc(m.group(1))
                return m.end()
            return -1 if _OSC_PREFIX.match(data, i) else self._skip_bad(data, i)
        if nxt in "PX^_":
            m = _STRING_TERMINATED.match(data, i)
            if m:
                return m.end()
            return -1 if _STRING_PREFIX.match(data, i) else self._skip_bad(data, i)
        m = _ESC_SIMPLE.match(data, i)
        if m:
            self._esc(m.group(1), m.group(2))
            return m.end()
        return -1 if _ESC_PREFIX.match(data, i) else self._skip_bad(data, i)

    @staticmethod
    def _skip_bad(data: str, i: int) -> int:
        return i + 2 if i + 2 <= len(data) else len(data)

    def _esc(self, inter: str, final: str) -> None:
        if inter in ("(", ")"):
            self._charsets[0 if inter == "(" else 1] = final
            return
        if inter:
            return
        if final == "7":
            self._save_cursor()
        elif final == "8":
            self._restore_cursor()
        elif final == "M":
            self._reverse_index()
        elif final == "D":
            self._index()
        elif final == "E":
            self.cx = 0
            self._index()
        elif final == "H":
            self.tabs.add(self.cx)
        elif final == "c":
            self.reset()
        elif final == "=":
            self.app_keypad = True
        elif final == ">":
            self.app_keypad = False

    def _save_cursor(self) -> None:
        self._saved_cursor = (
            self.cx, self.cy, self._pen, int(self._wrap_pending), tuple(self._charsets),
        )

    def _restore_cursor(self) -> None:
        cx, cy, pen, wrap, charsets = self._saved_cursor
        self.cx = min(cx, self.cols - 1)
        self.cy = min(cy, self.rows - 1)
        self._pen = pen
        self._pen_id = self._attr_id(pen)
        self._wrap_pending = bool(wrap)
        self._charsets = list(charsets)

    @staticmethod
    def _params(raw: str, default: int = 0) -> list[int]:
        if not raw:
            return [default]
        out: list[int] = []
        for part in raw.split(";"):
            head = part.split(":", 1)[0]
            out.append(int(head) if head.isdigit() else default)
        return out

    def _csi(self, raw: str, inter: str, final: str) -> None:
        private = raw[:1] if raw[:1] in "?<=>" else ""
        if private:
            raw = raw[1:]
        if private == "?" and final in "hl":
            self._dec_mode(self._params(raw), final == "h")
            return
        if private == ">":
            if final == "c":
                self.responses.append(b"\x1b[>0;0;0c")
            return
        if private or inter:
            return
        p = self._params(raw)
        p0 = p[0]
        n1 = p0 or 1
        if final == "A":
            self._move_y(-n1)
        elif final in "Be":
            self._move_y(n1)
        elif final in "Ca":
            self.cx = min(self.cols - 1, self.cx + n1)
            self._wrap_pending = False
        elif final == "D":
            self.cx = max(0, self.cx - n1)
            self._wrap_pending = False
        elif final == "E":
            self._move_y(n1)
            self.cx = 0
        elif final == "F":
            self._move_y(-n1)
            self.cx = 0
        elif final in "G`":
            self.cx = min(self.cols - 1, max(0, n1 - 1))
            self._wrap_pending = False
        elif final in "Hf":
            row = (p[0] or 1) - 1
            col = ((p[1] if len(p) > 1 else 1) or 1) - 1
            self._goto(row, col)
        elif final == "d":
            self._goto(n1 - 1, self.cx, keep_col=True)
        elif final == "J":
            self._erase_display(p0)
        elif final == "K":
            self._erase_line(p0)
        elif final == "L":
            self._insert_lines(n1)
        elif final == "M":
            self._delete_lines(n1)
        elif final == "@":
            self._insert_chars(n1)
        elif final == "P":
            self._delete_chars(n1)
        elif final == "X":
            self._erase_chars(n1)
        elif final == "S":
            self._scroll_up(n1)
        elif final == "T":
            self._scroll_down(n1)
        elif final == "m":
            self._sgr(raw)
        elif final == "r":
            self._set_region(p)
        elif final == "s":
            self._save_cursor()
        elif final == "u":
            self._restore_cursor()
        elif final == "n":
            if p0 == 5:
                self.responses.append(b"\x1b[0n")
            elif p0 == 6:
                row = self.cy - (self.top if self.origin_mode else 0) + 1
                self.responses.append(f"\x1b[{row};{self.cx + 1}R".encode())
        elif final == "c":
            if p0 == 0:
                self.responses.append(b"\x1b[?62;c")
        elif final == "g":
            if p0 == 3:
                self.tabs.clear()
            elif p0 == 0:
                self.tabs.discard(self.cx)
        elif final == "h":
            if 4 in p:
                self.insert_mode = True
        elif final == "l":
            if 4 in p:
                self.insert_mode = False

    def _move_y(self, delta: int) -> None:
        low, high = (self.top, self.bottom) if self.top <= self.cy <= self.bottom else (0, self.rows - 1)
        self.cy = min(high, max(low, self.cy + delta))
        self._wrap_pending = False

    def _goto(self, row: int, col: int, keep_col: bool = False) -> None:
        if self.origin_mode:
            row = min(self.bottom, max(self.top, self.top + row))
        else:
            row = min(self.rows - 1, max(0, row))
        self.cy = row
        if not keep_col:
            self.cx = min(self.cols - 1, max(0, col))
        self._wrap_pending = False

    def _set_region(self, p: list[int]) -> None:
        top = (p[0] or 1) - 1
        bottom = ((p[1] if len(p) > 1 else self.rows) or self.rows) - 1
        if 0 <= top < bottom < self.rows:
            self.top, self.bottom = top, bottom
        else:
            self.top, self.bottom = 0, self.rows - 1
        self._goto(0, 0)

    def _dec_mode(self, params: list[int], enable: bool) -> None:
        for mode in params:
            if mode == 1:
                self.app_cursor = enable
            elif mode == 6:
                self.origin_mode = enable
                self._goto(0, 0)
            elif mode == 7:
                self.autowrap = enable
            elif mode == 25:
                self.cursor_visible = enable
            elif mode in (47, 1047, 1049):
                self._set_alt_screen(enable, save_cursor=(mode == 1049))
            elif mode in (1000, 1002, 1003):
                self.mouse_mode = mode if enable else 0
            elif mode == 1006:
                self.mouse_sgr = enable
            elif mode == 1004:
                self.focus_events = enable
            elif mode == 2004:
                self.bracketed_paste = enable

    def _set_alt_screen(self, enable: bool, save_cursor: bool) -> None:
        if enable == self.alt_screen:
            return
        if enable:
            if save_cursor:
                self._save_cursor()
            self._saved_main = (self.grid, self.top, self.bottom)
            self.grid = [_Row(self.cols) for _ in range(self.rows)]
            self.alt_screen = True
            self.top, self.bottom = 0, self.rows - 1
        else:
            if self._saved_main is not None:
                self.grid, self.top, self.bottom = self._saved_main
                self._saved_main = None
            self.alt_screen = False
            if save_cursor:
                self._restore_cursor()
        self.all_dirty = True

    def _sgr(self, raw: str) -> None:
        fg, bg, flags = self._pen
        parts = raw.split(";") if raw else ["0"]
        i = 0
        while i < len(parts):
            token = parts[i]
            sub = token.split(":")
            code = int(sub[0]) if sub[0].isdigit() else 0
            if code == 0:
                fg, bg, flags = DEFAULT_COLOR, DEFAULT_COLOR, 0
            elif code == 1:
                flags |= FLAG_BOLD
            elif code == 2:
                flags |= FLAG_DIM
            elif code == 3:
                flags |= FLAG_ITALIC
            elif code in (4, 21):
                flags |= FLAG_UNDERLINE
            elif code == 7:
                flags |= FLAG_REVERSE
            elif code == 8:
                flags |= FLAG_HIDDEN
            elif code == 9:
                flags |= FLAG_STRIKE
            elif code == 22:
                flags &= ~(FLAG_BOLD | FLAG_DIM)
            elif code == 23:
                flags &= ~FLAG_ITALIC
            elif code == 24:
                flags &= ~FLAG_UNDERLINE
            elif code == 27:
                flags &= ~FLAG_REVERSE
            elif code == 28:
                flags &= ~FLAG_HIDDEN
            elif code == 29:
                flags &= ~FLAG_STRIKE
            elif 30 <= code <= 37:
                fg = code - 30
            elif code == 39:
                fg = DEFAULT_COLOR
            elif 40 <= code <= 47:
                bg = code - 40
            elif code == 49:
                bg = DEFAULT_COLOR
            elif 90 <= code <= 97:
                fg = code - 90 + 8
            elif 100 <= code <= 107:
                bg = code - 100 + 8
            elif code in (38, 48):
                color, used = self._extended_color(sub, parts, i)
                i += used
                if color is not None:
                    if code == 38:
                        fg = color
                    else:
                        bg = color
            i += 1
        self._pen = (fg, bg, flags)
        self._pen_id = self._attr_id(self._pen)

    @staticmethod
    def _extended_color(sub: list[str], parts: list[str], i: int) -> tuple[int | None, int]:
        """Lê ``38``/``48`` em formato com ``;`` ou ``:``; devolve (cor, partes extras usadas)."""
        if len(sub) > 1:
            nums = [int(x) if x.isdigit() else 0 for x in sub[1:]]
            if nums and nums[0] == 5 and len(nums) >= 2:
                return min(255, nums[1]), 0
            if nums and nums[0] == 2:
                rgb = nums[-3:] if len(nums) >= 4 else nums[1:4]
                if len(rgb) == 3:
                    return 0x1000000 | (min(255, rgb[0]) << 16) | (min(255, rgb[1]) << 8) | min(255, rgb[2]), 0
            return None, 0
        mode = parts[i + 1] if i + 1 < len(parts) else ""
        if mode == "5" and i + 2 < len(parts):
            value = parts[i + 2]
            return (min(255, int(value)) if value.isdigit() else None), 2
        if mode == "2" and i + 4 < len(parts):
            try:
                r, g, b = (min(255, int(x)) for x in parts[i + 2: i + 5])
            except ValueError:
                return None, 4
            return 0x1000000 | (r << 16) | (g << 8) | b, 4
        return None, 0

    def _osc(self, payload: str) -> None:
        code, _, value = payload.partition(";")
        if code in ("0", "2"):
            self.title = value[:256]
        elif code == "7":
            match = re.match(r"file://[^/]*(/.*)", value)
            if match:
                from urllib.parse import unquote

                self.cwd = unquote(match.group(1))

    def resize(self, rows: int, cols: int) -> None:
        """Ajusta a grade para ``rows`` x ``cols``, preservando o conteúdo possível."""
        rows, cols = max(1, rows), max(1, cols)
        if rows == self.rows and cols == self.cols:
            return
        if rows < self.rows:
            surplus = self.rows - rows
            drop_top = min(surplus, max(0, self.cy + 1 - rows)) if self.cy >= rows else 0
            for _ in range(drop_top):
                gone = self.grid.pop(0)
                if not self.alt_screen:
                    self._scrolled.append((gone.chars, gone.attrs))
                self.cy -= 1
            del self.grid[rows:]
        else:
            for _ in range(rows - self.rows):
                self.grid.append(_Row(cols))
        for row in self.grid:
            row.resize(cols)
        if self._saved_main is not None:
            saved_grid = self._saved_main[0]
            while len(saved_grid) < rows:
                saved_grid.append(_Row(cols))
            del saved_grid[rows:]
            for row in saved_grid:
                row.resize(cols)
        self.rows, self.cols = rows, cols
        self.cx = min(self.cx, cols - 1)
        self.cy = min(self.cy, rows - 1)
        self.top, self.bottom = 0, rows - 1
        self.tabs = {c for c in range(0, cols, 8)}
        self._wrap_pending = False
        self.all_dirty = True

    def clear_screen_and_history(self) -> None:
        """Limpa a tela e descarta o histórico pendente (botão de limpar)."""
        for r in range(self.rows):
            self._blank_row(r)
        self.cx = self.cy = 0
        self._scrolled.clear()
        self._wrap_pending = False
        self.all_dirty = True
