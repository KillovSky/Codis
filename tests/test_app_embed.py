"""Testes de :mod:`src.ui.app_embed` e do modo "adotar" do window_embed."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.ui import app_embed as ae  # noqa: E402
from src.ui import window_embed as we  # noqa: E402
from test_doom_embed import FakeCalls  # noqa: E402


class Calls(FakeCalls):
    """FakeCalls + consultas usadas pelo seletor de janelas."""

    def __init__(self) -> None:
        super().__init__()
        self.titles: dict[int, str] = {}
        self.images: dict[int, str] = {}
        self.rects: dict[int, tuple] = {}

    def window_title(self, h): return self.titles.get(h, "")
    def process_image(self, pid): return self.images.get(pid, "")
    def is_cloaked(self, h): return False
    def window_rect(self, h): return self.rects.get(h, (10, 20, 650, 500))


def _backend():
    c = Calls()
    c.add(1, 100, size=(800, 600)); c.titles[1] = "Sem título - Bloco de Notas"
    c.images[100] = r"C:\Windows\notepad.exe"
    c.add(2, 200, size=(900, 700)); c.titles[2] = "Calculadora"
    c.images[200] = r"C:\Windows\calc.exe"
    c.add(3, 300, size=(50, 50)); c.titles[3] = "minúscula"
    c.add(4, 400, size=(800, 600), visible=False); c.titles[4] = "oculta"
    c.add(5, 500, size=(800, 600)); c.titles[5] = ""
    c.add(6, 600, size=(800, 600), owner=1); c.titles[6] = "dialogo"
    return c, we.Win32Backend(c)


def test_list_windows_filters_unusable():
    _, b = _backend()
    wins = b.list_windows()
    assert sorted(w.handle for w in wins) == [1, 2]
    notepad = next(w for w in wins if w.handle == 1)
    assert notepad.exe == "notepad.exe" and notepad.pid == 100


def test_candidates_exclude_self_and_shell(monkeypatch):
    c, b = _backend()
    import os
    c.add(7, os.getpid(), size=(800, 600)); c.titles[7] = "o próprio Codis"; c.images[os.getpid()] = "python.exe"
    c.add(8, 800, size=(800, 600)); c.titles[8] = "Explorer"; c.images[800] = r"C:\Windows\explorer.exe"
    assert [w.handle for w in ae.list_candidate_windows(b)] == [2, 1]


def test_find_window_in_prefers_largest_visible():
    c, b = _backend()
    c.add(9, 100, size=(1000, 800))
    assert b.find_window_in([100]) == 9
    assert b.find_window_in([100, 200]) == 9
    assert b.find_window_in([999]) is None


def test_detach_restore_keeps_window_visible_at_original_rect():
    c, b = _backend()
    b.embed(2, 4242, 500, 400)
    assert c.windows[2]["parent"] == 4242
    c.log.clear()
    b.detach(2, restore=True)
    assert c.windows[2]["parent"] == 0
    assert c.windows[2]["style"] & 0x80000000
    last_pos = [e for e in c.log if e[0] == "pos"][-1]
    assert last_pos[4] & 0x0040 and not last_pos[4] & 0x0080
    assert (last_pos[2], last_pos[3]) == (640, 480)
    assert ("close", 2) not in c.log


def test_detach_default_still_hides():
    c, b = _backend()
    b.embed(2, 4242, 500, 400)
    c.log.clear()
    b.detach(2)
    assert any(e[0] == "pos" and e[4] & 0x0080 for e in c.log)


def test_normalize_exe_name():
    assert ae.normalize_exe_name('"C:\\x\\Notepad.EXE"'.replace("\\", "/")) == "notepad.exe"
    if sys.platform != "win32":
        assert ae.normalize_exe_name("firefox") == "firefox"


def test_pids_for_exe_name_finds_self_children_but_not_self():
    import subprocess
    import time

    name = Path(sys.executable).name
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
    try:
        time.sleep(0.3)
        pids = ae.pids_for_exe_name(name)
        assert p.pid in pids
        import os
        assert os.getpid() not in pids
        assert ae.pids_for_exe_name("nao-existe-xyz.exe") == []
    finally:
        p.kill()
