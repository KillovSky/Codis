"""Lógica do TerminalPanel que não depende de widgets: easter egg, cd seguro, observadores."""

from __future__ import annotations

from src.ui.terminal import TerminalPanel, _KeyTap


class FakeSession:
    def __init__(self, cmd=("bash",)):
        self.cmd = list(cmd)
        self.exited = False
        self.typed: list[bytes] = []

    def type_input(self, data: bytes) -> None:
        self.typed.append(data)


class Panel:
    _filter_input = TerminalPanel._filter_input
    _run_easter_egg = TerminalPanel._run_easter_egg
    run_command = TerminalPanel.run_command
    set_cwd = TerminalPanel.set_cwd
    run_file = TerminalPanel.run_file

    def __init__(self, session=None, handler=None):
        self._session = session or FakeSession()
        self._easter_egg_handler = handler
        self._typed = ""
        self._typed_valid = True
        self._cwd = ""
        self.woken = 0

    def _current(self):
        return self._session

    def _wake(self):
        self.woken += 1


def type_line(panel, text, end="Return"):
    for ch in text:
        assert panel._filter_input(ch.encode(), ch) is False
    return panel._filter_input(b"\r", end)


def test_regular_commands_pass_through():
    panel = Panel(handler=lambda cmd: True)
    assert type_line(panel, "ls -la") is False


def test_easter_egg_is_consumed_and_line_cleared():
    seen = []
    panel = Panel(handler=lambda cmd: seen.append(cmd) or True)
    assert type_line(panel, "/codis matrix") is True
    assert seen == ["/codis matrix"]
    assert panel._session.typed == [b"\x15"]


def test_unknown_easter_egg_prints_the_hint_through_the_shell():
    panel = Panel(handler=lambda cmd: False)
    assert type_line(panel, "/codis nope") is True
    sent = b"".join(panel._session.typed)
    assert sent.startswith(b"\x15echo ") and sent.endswith(b"\r") and b"opensesame" in sent


def test_edited_lines_are_not_treated_as_easter_eggs():
    panel = Panel(handler=lambda cmd: True)
    for ch in "/codis matrix":
        panel._filter_input(ch.encode(), ch)
    panel._filter_input(b"\x1b[A", "Up")
    assert panel._filter_input(b"\r", "Return") is False


def test_backspace_and_ctrl_c_reset_tracking():
    panel = Panel(handler=lambda cmd: True)
    type_line_chars = "/codis maX"
    for ch in type_line_chars:
        panel._filter_input(ch.encode(), ch)
    panel._filter_input(b"\x7f", "BackSpace")
    for ch in "trix":
        panel._filter_input(ch.encode(), ch)
    assert panel._filter_input(b"\r", "Return") is True
    for ch in "/codis x":
        panel._filter_input(ch.encode(), ch)
    panel._filter_input(b"\x03", "c")
    assert panel._filter_input(b"\r", "Return") is False


def test_run_command_types_the_command_and_presses_enter():
    panel = Panel()
    panel.run_command("echo hi")
    assert panel._session.typed == [b"echo hi\r"]
    assert panel.woken == 1


def test_multiline_command_uses_carriage_returns():
    panel = Panel()
    panel.run_command("a\nb")
    assert panel._session.typed == [b"a\rb\r"]


def test_run_command_ignores_dead_sessions():
    panel = Panel()
    panel._session.exited = True
    panel.run_command("echo hi")
    assert panel._session.typed == []


def test_set_cwd_quotes_the_path_for_the_shell():
    panel = Panel(FakeSession(["bash"]))
    panel.set_cwd("/tmp/x'; rm -rf ~; '")
    assert panel._session.typed == [b"cd '/tmp/x'\\''; rm -rf ~; '\\'''\r"]
    ps = Panel(FakeSession(["powershell.exe"]))
    ps.set_cwd("C:\\it's")
    assert ps._session.typed == [b"Set-Location -LiteralPath 'C:\\it''s'\r"]
    cmd = Panel(FakeSession(["cmd.exe"]))
    cmd.set_cwd("C:\\a b")
    assert cmd._session.typed == [b'cd /d "C:\\a b"\r']


def test_run_file_picks_interpreter_by_extension():
    panel = Panel()
    panel.run_file("/p/a b.py")
    assert panel._session.typed == [b"python '/p/a b.py'\r"]


def test_key_tap_turns_key_binds_into_observers_and_forwards_the_rest():
    calls = []

    class View:
        def add_key_observer(self, fn):
            calls.append(("observer", fn))

        def bind(self, seq, fn=None, add=None):
            calls.append(("bind", seq))

        def focus_set(self):
            calls.append(("focus",))

    tap = _KeyTap(View())
    tap.bind("<Key>", print, add=True)
    tap.bind("<Return>", print)
    tap.focus_set()
    assert calls == [("observer", print), ("bind", "<Return>"), ("focus",)]
