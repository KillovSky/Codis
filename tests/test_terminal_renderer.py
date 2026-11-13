"""O renderizador deve manter o Text igual a histórico + tela, atualizando só o necessário."""

from __future__ import annotations

import re

from src.terminal.backends import Backend
from src.terminal.session import TerminalSession
from src.terminal.view import AttrTagger, ScreenRenderer


class FakeText:
    """Reproduz o modelo de índices do tk.Text: texto sempre termina com uma quebra de linha."""

    def __init__(self):
        self.s = "\n"
        self.tags: dict[str, list[tuple[int, int]]] = {}
        self.tag_calls: list[tuple] = []

    def _lines(self):
        return self.s.split("\n")[:-1]

    def _offset(self, index: str) -> int:
        extra = 0
        m = re.fullmatch(r"(.*?)([+-]\d+)c", index)
        if m:
            index, extra = m.group(1), int(m.group(2))
        if index == "end":
            base = len(self.s)
        else:
            line, col = index.split(".")
            lines = self._lines()
            n = int(line)
            start = sum(len(x) + 1 for x in lines[: n - 1])
            if n > len(lines):
                base = len(self.s)
            else:
                width = len(lines[n - 1])
                base = start + (width if col == "end" else min(int(col), width))
        return max(0, min(len(self.s), base + extra))

    def index(self, index):
        off = self._offset(index)
        before = self.s[:off]
        return f"{before.count(chr(10)) + 1}.{len(before) - (before.rfind(chr(10)) + 1)}"

    def insert(self, index, *args):
        off = self._offset(index)
        text = "".join(a for a in args[0::2] if isinstance(a, str))
        self.s = self.s[:off] + text + self.s[off:]

    def delete(self, a, b=None):
        start, end = self._offset(a), self._offset(b)
        end = min(end, len(self.s) - 1)
        if end > start:
            self.s = self.s[:start] + self.s[end:]

    def get(self, a, b):
        return self.s[self._offset(a): self._offset(b)]

    def tag_remove(self, name, a, b):
        self.tags.pop(name, None)

    def tag_add(self, name, a, b):
        self.tag_calls.append((name, a, b))
        self.tags.setdefault(name, []).append((self._offset(a), self._offset(b)))

    def tag_configure(self, *a, **k):
        pass

    def tag_delete(self, *a):
        pass

    def tag_raise(self, *a):
        pass

    def text(self):
        """Linhas sem espaços à direita (a linha do cursor ganha uma célula em branco)."""
        return [line.rstrip() for line in self._lines()]


class NullBackend(Backend):
    def read(self, size=65536):
        return b""

    def write(self, data):
        pass

    def resize(self, rows, cols):
        pass

    def terminate(self):
        pass

    def close(self):
        pass

    @property
    def alive(self):
        return True


def make(rows=5, cols=20, history=1000):
    session = TerminalSession("t", ["x"], ".", rows, cols, backend=NullBackend(), history=history)
    widget = FakeText()
    tagger = AttrTagger(widget, {(b, i): None for b in (False, True) for i in (False, True)})
    renderer = ScreenRenderer(widget, tagger, history)
    renderer.load(session)
    return session, widget, renderer


def feed(session, data):
    session.screen.feed(data)
    session._collect_history()


def expected(session):
    hist = ["".join(t for t, _ in line) for line in session.history]
    screen = [session.screen.line_text(r) for r in range(session.screen.rows)]
    return hist + screen


def test_initial_layout_has_one_line_per_screen_row():
    session, widget, _ = make(rows=5)
    assert len(widget.text()) == 5


def test_text_equals_history_plus_screen_after_scrolling():
    session, widget, renderer = make(rows=5)
    feed(session, "\r\n".join(str(i) for i in range(1, 13)))
    renderer.render(session)
    assert widget.text() == expected(session)
    assert len(session.history) == 7


def test_only_dirty_rows_are_rewritten():
    session, widget, renderer = make(rows=5)
    feed(session, "a\r\nb\r\nc")
    renderer.render(session)
    feed(session, "\x1b[2;1HZZ")
    renderer.render(session)
    assert widget.text() == expected(session)
    assert widget.text()[1] == "ZZ"


def test_cursor_tag_follows_cursor_position():
    session, widget, renderer = make(rows=5)
    feed(session, "abc")
    renderer.render(session)
    start, end = widget.tags["cursor"][-1]
    assert widget.s[start:end] == " " or end - start == 1
    assert start == widget._offset("1.3")


def test_cursor_past_end_of_text_pads_the_line():
    session, widget, renderer = make(rows=5)
    feed(session, "\x1b[3;8H")
    renderer.render(session)
    assert len(widget._lines()[2]) >= 8


def test_hidden_cursor_has_no_tag():
    session, widget, renderer = make(rows=5)
    feed(session, "\x1b[?25lhi")
    renderer.render(session)
    assert "cursor" not in widget.tags


def test_resize_keeps_line_count_consistent():
    session, widget, renderer = make(rows=6)
    feed(session, "\r\n".join(f"l{i}" for i in range(10)))
    renderer.render(session)
    for rows in (4, 9, 3, 6):
        session.resize(rows, 20)
        renderer.render(session)
        assert len(widget.text()) == renderer.sb + rows
        assert widget.text() == expected(session)


def test_history_is_trimmed_to_the_limit():
    session, widget, renderer = make(rows=4, history=10)
    feed(session, "\r\n".join(str(i) for i in range(50)))
    renderer.render(session)
    assert renderer.sb == 10 == len(session.history)
    assert widget.text() == expected(session)


def test_clearing_history_rebuilds_the_widget():
    session, widget, renderer = make(rows=4)
    feed(session, "\r\n".join(str(i) for i in range(20)))
    renderer.render(session)
    session.screen.feed("\x1b[3J\x1b[H\x1b[2J")
    session._collect_history()
    renderer.render(session)
    assert renderer.sb == 0 and len(widget.text()) == 4


def test_switching_sessions_reloads_identical_content():
    session, widget, renderer = make(rows=5)
    feed(session, "\r\n".join(str(i) for i in range(1, 15)))
    renderer.render(session)
    before = list(widget.text())
    other, _, _ = make(rows=5)
    renderer.load(other)
    renderer.load(session)
    renderer.render(session)
    assert widget.text() == before


def test_alt_screen_does_not_pollute_history():
    session, widget, renderer = make(rows=5)
    feed(session, "shell\r\n")
    renderer.render(session)
    before = len(session.history)
    feed(session, "\x1b[?1049h" + "\r\n".join("x" * 5 for _ in range(30)) + "\x1b[?1049l")
    renderer.render(session)
    assert len(session.history) == before
    assert widget.text()[0] == "shell"


def test_wide_characters_place_the_cursor_by_text_index():
    session, widget, renderer = make(rows=3)
    feed(session, "\u4e2d\u6587x")
    renderer.render(session)
    start, _ = widget.tags["cursor"][-1]
    assert start == widget._offset("1.3")
