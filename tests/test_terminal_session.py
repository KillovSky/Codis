"""Sessões de terminal com PTY real: eco, Ctrl+C, resize, cursor e encerramento."""

from __future__ import annotations

import os
import shutil
import sys
import time

import pytest

from src.terminal.backends import PipeBackend, interactive_command, terminal_env
from src.terminal.session import TerminalSession

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="usa shell POSIX")

SH = shutil.which("sh") or "/bin/sh"


def _has_prompt(session):
    text = session.screen.text()
    return "$" in text or "#" in text


def wait_for(session, predicate, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        session.pump(budget=0.02)
        if predicate():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def make_session(tmp_path):
    created = []

    def factory(cmd=None, rows=24, cols=80, **kw):
        s = TerminalSession("t", cmd or [SH], str(tmp_path), rows, cols, **kw)
        created.append(s)
        return s

    yield factory
    for s in created:
        s.terminate()


def test_interactive_command_echoes_and_runs(make_session):
    s = make_session()
    assert wait_for(s, lambda: _has_prompt(s))
    s.type_input(b"echo hello-codis\r")
    assert wait_for(s, lambda: s.screen.text().count("hello-codis") >= 2)


def test_ctrl_c_interrupts_foreground_program(make_session):
    s = make_session()
    assert wait_for(s, lambda: _has_prompt(s))
    s.type_input(b"sleep 30\r")
    time.sleep(0.3)
    began = time.monotonic()
    s.type_input(b"\x03")
    s.type_input(b"echo after-interrupt\r")
    assert wait_for(s, lambda: s.screen.text().count("after-interrupt") >= 2, timeout=5)
    assert time.monotonic() - began < 5


def test_resize_reaches_the_program(make_session):
    s = make_session(rows=24, cols=80)
    assert wait_for(s, lambda: _has_prompt(s))
    s.resize(30, 100)
    s.type_input(b"stty size\r")
    assert wait_for(s, lambda: "30 100" in s.screen.text())


def test_cursor_addressing_program_draws_in_place(make_session):
    s = make_session()
    assert wait_for(s, lambda: _has_prompt(s))
    s.type_input(b"printf '\\033[2J\\033[5;10Hxyz'\r")
    assert wait_for(s, lambda: "xyz" in s.screen.line_text(4))
    assert s.screen.line_text(4).index("xyz") == 9


def test_environment_and_controlling_tty(make_session):
    s = make_session()
    assert wait_for(s, lambda: _has_prompt(s))
    s.type_input(b"echo T=$TERM; tty\r")
    assert wait_for(s, lambda: "T=xterm-256color" in s.screen.text())
    assert wait_for(s, lambda: "/dev/" in s.screen.text())


def test_exit_marks_session_and_reports(make_session):
    s = make_session()
    assert wait_for(s, lambda: _has_prompt(s))
    s.type_input(b"exit\r")
    assert wait_for(s, lambda: s.exited)
    assert "encerrado" in s.screen.text()


def test_scrollback_history_keeps_lines_that_left_the_screen(make_session):
    s = make_session(rows=10)
    assert wait_for(s, lambda: _has_prompt(s))
    s.type_input(b"seq 1 100\r")
    assert wait_for(s, lambda: "100" in s.screen.text())
    assert len(s.history) >= 80
    first = "".join(text for text, _ in s.history[0])
    assert first.strip() != ""


def test_huge_output_never_blocks_the_ui_thread(make_session):
    s = make_session()
    assert wait_for(s, lambda: _has_prompt(s))
    s.type_input(b"yes abcdefghijklmnopqrstuvwxyz | head -c 8000000; echo FIM\r")
    worst = 0.0
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        began = time.monotonic()
        s.pump(budget=0.008)
        worst = max(worst, time.monotonic() - began)
        if "FIM" in s.screen.text():
            break
        time.sleep(0.001)
    assert "FIM" in s.screen.text()
    assert worst < 0.25


def test_utf8_split_across_reads_is_decoded(make_session):
    s = make_session()
    assert wait_for(s, lambda: _has_prompt(s))
    s.type_input(b"printf '\\303\\251 \\342\\202\\254\\n'\r")
    assert wait_for(s, lambda: "\u00e9 \u20ac" in s.screen.text())


def test_terminate_kills_the_whole_process_group(make_session, tmp_path):
    s = make_session()
    assert wait_for(s, lambda: _has_prompt(s))
    marker = tmp_path / "child.pid"
    s.type_input(f"sleep 120 & echo $! > {marker}\r".encode())
    assert wait_for(s, lambda: marker.exists() and marker.read_text().strip() != "")
    child = int(marker.read_text())
    s.terminate()
    for _ in range(100):
        try:
            os.kill(child, 0)
        except OSError:
            break
        try:
            with open(f"/proc/{child}/stat", encoding="utf-8") as handle:
                if handle.read().split()[2] == "Z":
                    break
        except OSError:
            break
        time.sleep(0.05)
    else:
        pytest.fail("o processo filho continuou vivo")


def test_pipe_backend_gets_local_echo_and_line_editing(tmp_path):
    backend = PipeBackend([SH], str(tmp_path), terminal_env())
    s = TerminalSession("p", [SH], str(tmp_path), 24, 80, backend=backend)
    try:
        s.type_input(b"echo hii\x7f\x7fyo\r")
        assert wait_for(s, lambda: "hyo" in s.screen.text().replace("echo hyo", "") )
    finally:
        s.terminate()


def test_startup_failure_is_reported_not_raised(tmp_path):
    s = TerminalSession("x", ["/nao/existe/shell"], str(tmp_path), 24, 80)
    assert s.exited and s.error
    assert "Erro" in s.screen.text()
    s.terminate()


def test_interactive_command_strips_blocking_flags():
    assert interactive_command(["powershell.exe", "-NoLogo", "-NonInteractive", "-NoProfile"]) == ["powershell.exe", "-NoLogo"]
    assert interactive_command(["bash", "--norc"]) == ["bash"]
    assert interactive_command(["bash", "--norc"], load_rc=False) == ["bash", "--norc"]
    assert interactive_command(["pwsh", "-NonInteractive"], load_rc=False) == ["pwsh"]


def test_terminal_env_sets_term_and_utf8(monkeypatch):
    for key in ("LANG", "LC_ALL", "LC_CTYPE"):
        monkeypatch.delenv(key, raising=False)
    env = terminal_env()
    assert env["TERM"] == "xterm-256color" and env["COLORTERM"] == "truecolor"
    assert "UTF-8" in env["LANG"].upper()


def test_child_env_restores_library_path_saved_by_pyinstaller(monkeypatch):
    from src.terminal.backends import child_process_env

    env = child_process_env({"LD_LIBRARY_PATH": "/tmp/_MEI123", "LD_LIBRARY_PATH_ORIG": "/usr/lib/x", "A": "1"})
    assert env["LD_LIBRARY_PATH"] == "/usr/lib/x" and "LD_LIBRARY_PATH_ORIG" not in env and env["A"] == "1"
    env = child_process_env({"LD_LIBRARY_PATH": "/tmp/_MEI123", "LD_LIBRARY_PATH_ORIG": ""})
    assert "LD_LIBRARY_PATH" not in env
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert "LD_LIBRARY_PATH" not in child_process_env({"LD_LIBRARY_PATH": "/tmp/_MEI123"})
    monkeypatch.undo()
    assert child_process_env({"LD_LIBRARY_PATH": "/keep"})["LD_LIBRARY_PATH"] == "/keep"
