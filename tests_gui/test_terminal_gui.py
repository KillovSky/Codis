"""O terminal em tempo real, com Tk de verdade e um bash real."""

from __future__ import annotations

import shutil
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="usa shell POSIX")
BASH = shutil.which("bash")


@pytest.fixture
def term(make_panel):
    d = make_panel()
    assert d.wait(lambda: "#" in d.text() or "$" in d.text()), "o shell nao mostrou prompt"
    return d


def test_typing_runs_commands_and_shows_output(term):
    term.type("echo ola-codis\r")
    assert term.wait(lambda: term.text().count("ola-codis") >= 2)


def test_echo_latency_is_interactive(term):
    samples = []
    for ch in "abcdefghij":
        before = term.text().count(ch)
        started = time.perf_counter()
        term.key(ch)
        assert term.wait(lambda ch=ch, before=before: term.text().count(ch) > before, timeout=2)
        samples.append((time.perf_counter() - started) * 1000)
    samples.sort()
    median = samples[len(samples) // 2]
    print(f"\nlatencia de eco (ms): mediana={median:.1f} max={samples[-1]:.1f}")
    assert median < 60


@pytest.mark.skipif(BASH is None, reason="precisa de bash")
def test_history_with_arrow_keys_and_readline_editing(term):
    term.type("echo primeiro\r")
    assert term.wait(lambda: term.text().count("primeiro") >= 2)
    term.key("Up")
    term.key("Return")
    assert term.wait(lambda: term.text().count("primeiro") >= 4)


@pytest.mark.skipif(BASH is None, reason="precisa de bash")
def test_tab_completion_comes_from_the_real_shell(term):
    term.type("ls /usr/bi")
    term.key("Tab")
    assert term.wait(lambda: "/usr/bin/" in term.text().split("\n")[-1] or "/usr/bin/" in term.text())


def test_ctrl_c_interrupts_a_running_program(term):
    term.type("sleep 40\r")
    term.spin(0.4)
    started = time.monotonic()
    term.key("c", state=4)
    term.type("echo livre\r")
    assert term.wait(lambda: term.text().count("livre") >= 2, timeout=5)
    assert time.monotonic() - started < 5


def test_fullscreen_curses_app_uses_alternate_screen_and_restores(term, tmp_path):
    script = tmp_path / "demo.py"
    script.write_text(
        "import curses\n"
        "def main(s):\n"
        "    s.addstr(3, 5, 'CURSES-OK')\n"
        "    s.getch()\n"
        "curses.wrapper(main)\n",
        encoding="utf-8",
    )
    term.type("echo antes\r")
    assert term.wait(lambda: term.text().count("antes") >= 2)
    term.type(f"python3 {script}\r")
    assert term.wait(lambda: "CURSES-OK" in term.text(), timeout=10)
    assert term.panel._current().screen.alt_screen
    term.key("q")
    assert term.wait(lambda: not term.panel._current().screen.alt_screen, timeout=5)
    assert term.wait(lambda: "CURSES-OK" not in term.text())
    assert "antes" in term.text()


def test_text_selection_can_be_copied(term):
    term.type("echo selecione-isto\r")
    assert term.wait(lambda: term.text().count("selecione-isto") >= 2)
    idx = term.view.search("selecione-isto", "end", backwards=True)
    term.view.tag_add("sel", idx, f"{idx}+14c")
    term.view.copy_selection()
    assert term.view.clipboard_get() == "selecione-isto"


def test_paste_goes_to_the_shell_not_into_the_widget(term):
    term.view.clipboard_clear()
    term.view.clipboard_append("echo colado-ok")
    term.view.paste()
    term.key("Return")
    assert term.wait(lambda: term.text().count("colado-ok") >= 2)


def test_widget_text_cannot_be_edited_by_typing_when_no_session(term):
    term.panel.kill_active()
    term.key("a")
    term.key("BackSpace")
    assert "a" not in term.text().strip()


def test_resize_updates_emulator_and_program(term):
    term.root.geometry("640x300+0+0")
    term.root.update()
    assert term.wait(lambda: term.panel._current().screen.cols < 80)
    term.spin(0.4)
    term.view.focus_force()
    term.root.update()
    screen = term.panel._current().screen
    rows, cols = screen.rows, screen.cols
    term.type("stty size\r")
    assert term.wait(lambda: f"{rows} {cols}" in term.text()), (rows, cols, term.text()[-200:])
    term.root.geometry("900x420+0+0")
    term.root.update()
    term.spin(0.3)


def test_switching_tabs_keeps_each_shells_scrollback(term):
    term.type("seq 1 300; echo FIM$((40+2))\r")
    assert term.wait(lambda: "FIM42" in term.text())
    term.panel.new_shell()
    assert term.wait(lambda: len(term.panel._sessions) == 2 and ("#" in term.text() or "$" in term.text()))
    term.type("echo segunda-aba\r")
    assert term.wait(lambda: term.text().count("segunda-aba") >= 2)
    term.panel._switch_shell(0)
    term.spin(0.2)
    text = term.text()
    assert "FIM42" in text and "segunda-aba" not in text
    assert "\n2\n" in text
    term.panel._switch_shell(1)
    assert "segunda-aba" in term.text()


def test_huge_output_keeps_the_ui_responsive(term):
    term.type("seq 1 400000; echo FIM$((40+2))\r")
    worst, frames = 0.0, []
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        began = time.perf_counter()
        term.root.update()
        elapsed = time.perf_counter() - began
        worst = max(worst, elapsed)
        frames.append(elapsed)
        if "FIM42" in term.text():
            break
        time.sleep(0.001)
    frames.sort()
    p95 = frames[int(len(frames) * 0.95)] * 1000
    print(f"\niteracoes do laco de eventos: n={len(frames)} p95={p95:.1f} ms pior={worst * 1000:.1f} ms")
    assert "FIM42" in term.text()
    assert worst < 0.25 and p95 < 15


def test_clear_button_resets_screen_and_history(term):
    term.type("seq 1 200; echo FIM$((40+2))\r")
    assert term.wait(lambda: "FIM42" in term.text())
    term.panel.clear_active()
    assert term.wait(lambda: "FIM42" not in term.text())
    assert term.panel._current().history.maxlen and len(term.panel._current().history) == 0


def test_closing_the_panel_terminates_shell_and_children(make_panel, tmp_path):
    d = make_panel()
    assert d.wait(lambda: "#" in d.text() or "$" in d.text())
    marker = tmp_path / "pid"
    d.type(f"sleep 300 & echo $! > {marker}\r")
    assert d.wait(lambda: marker.exists() and marker.read_text().strip())
    child = int(marker.read_text())
    d.panel.destroy()
    import os

    for _ in range(80):
        try:
            os.kill(child, 0)
            with open(f"/proc/{child}/stat", encoding="utf-8") as fh:
                if fh.read().split()[2] == "Z":
                    break
        except OSError:
            break
        time.sleep(0.1)
    else:
        pytest.fail("processo filho sobreviveu ao fechamento do terminal")
