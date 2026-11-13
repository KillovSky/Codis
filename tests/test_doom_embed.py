"""Testes de :mod:`src.ui.window_embed` (backend Win32 com user32 falso) e
:mod:`src.ui.doom_embed` (descoberta de motor/IWAD e linha de comando).

O backend Win32 real só roda no Windows; aqui a camada ``Win32Calls`` é
substituída por um fake que registra a ordem das chamadas, validando a regra
da API (estilo ``WS_CHILD`` ANTES do ``SetParent``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.ui import doom_embed as de
from src.ui import window_embed as we


class FakeCalls:
    """Implementa a interface de ``Win32Calls`` sobre um dicionário de janelas."""

    def __init__(self) -> None:
        self.windows: dict[int, dict] = {}
        self.log: list[tuple] = []
        self.keys: set[int] = set()
        self.fg_pid = 0
        self.allow_parent = True
        self.error = 0

    def add(self, hwnd, pid, *, cls="SDL_app", size=(640, 480), visible=True,
            owner=0, style=0x80000000 | 0x00CF0000, ex=0x40000):
        self.windows[hwnd] = dict(pid=pid, cls=cls, size=size, visible=visible,
                                  owner=owner, style=style, ex=ex, parent=0)

    def top_level_windows(self):
        return [h for h, w in self.windows.items() if w["parent"] == 0]

    def is_window(self, h): return h in self.windows
    def is_visible(self, h): return self.windows[h]["visible"]
    def owner(self, h): return self.windows[h]["owner"]
    def parent(self, h): return self.windows[h]["parent"]
    def pid_of(self, h): return self.windows[h]["pid"]
    def class_name(self, h): return self.windows[h]["cls"]
    def rect_size(self, h): return self.windows[h]["size"]
    def client_size(self, h): return self.windows[h]["size"]
    def get_style(self, h): return self.windows[h]["style"]
    def get_exstyle(self, h): return self.windows[h]["ex"]

    def set_style(self, h, v):
        self.log.append(("style", h, v))
        self.windows[h]["style"] = v

    def set_exstyle(self, h, v):
        self.log.append(("exstyle", h, v))
        self.windows[h]["ex"] = v

    def set_parent(self, h, parent):
        self.log.append(("parent", h, parent))
        if self.allow_parent or parent == 0:
            self.windows[h]["parent"] = parent
        else:
            self.error = 5

    def last_error(self): return self.error
    def format_error(self, code): return f"erro {code}"

    def set_pos(self, h, x, y, w, hh, flags):
        self.log.append(("pos", h, w, hh, flags))
        if w and hh:
            self.windows[h]["size"] = (w, hh)

    def set_focus(self, h):
        self.log.append(("focus", h))
        return True
    def post_close(self, h): self.log.append(("close", h))
    def release_cursor_clip(self): self.log.append(("unclip",))
    def key_down(self, vk): return vk in self.keys
    def foreground_pid(self): return self.fg_pid
    def enable_mixed_dpi_hosting(self): self.log.append(("dpi",))


HOST = 0x1000


@pytest.fixture
def calls() -> FakeCalls:
    return FakeCalls()


@pytest.fixture
def backend(calls) -> we.Win32Backend:
    return we.Win32Backend(calls)


def test_find_window_prefers_visible_largest_of_pid(calls, backend):
    calls.add(1, 99, size=(300, 200))
    calls.add(2, 99, size=(1280, 720))
    calls.add(3, 99, size=(1920, 1080), visible=False)
    calls.add(4, 55, size=(2000, 2000))
    assert backend.find_window(99) == 2


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(owner=7),
        dict(cls="IME"),
        dict(cls="SDL_HelperWindowInputCatcher"),
        dict(size=(50, 50)),
        dict(ex=0x80),
    ],
)
def test_find_window_ignores_auxiliary_windows(calls, backend, kwargs):
    calls.add(1, 99, **kwargs)
    assert backend.find_window(99) is None


def test_embed_sets_child_style_before_set_parent(calls, backend):
    calls.add(10, 99)
    backend.embed(10, HOST, 800, 600)
    kinds = [e[0] for e in calls.log]
    assert kinds.index("style") < kinds.index("parent")
    style = calls.windows[10]["style"]
    assert style & 0x40000000
    assert not style & 0x80000000
    assert not style & 0x00C00000
    assert not style & 0x00040000
    assert not calls.windows[10]["ex"] & 0x40000
    assert calls.windows[10]["parent"] == HOST
    assert calls.windows[10]["size"] == (800, 600)
    assert backend.is_embedded(10, HOST)


def test_embed_failure_rolls_back_and_raises(calls, backend):
    calls.add(10, 99)
    before = (calls.windows[10]["style"], calls.windows[10]["ex"])
    calls.allow_parent = False
    with pytest.raises(we.EmbedError, match="SetParent"):
        backend.embed(10, HOST, 800, 600)
    assert (calls.windows[10]["style"], calls.windows[10]["ex"]) == before


def test_detach_restores_original_state(calls, backend):
    calls.add(10, 99)
    orig = (calls.windows[10]["style"], calls.windows[10]["ex"])
    backend.embed(10, HOST, 800, 600)
    backend.detach(10)
    assert calls.windows[10]["parent"] == 0
    assert (calls.windows[10]["style"], calls.windows[10]["ex"]) == orig


def test_detach_and_close_ignore_destroyed_window(calls, backend):
    backend.detach(404)
    backend.request_close(404)
    assert calls.log == []


def test_exit_hotkey_requires_all_keys_and_our_foreground(calls, backend):
    calls.keys = {0x11, 0x10, 0x7B}
    calls.fg_pid = 1234
    assert backend.exit_hotkey_down([1234, 99]) is True
    assert backend.exit_hotkey_down([77]) is False
    calls.keys = {0x11, 0x7B}
    assert backend.exit_hotkey_down([1234]) is False


def test_to_long_ptr_wraps_to_signed():
    bits = 64 if we.ctypes.sizeof(we.ctypes.c_void_p) == 8 else 32
    assert we._to_long_ptr(0x40000000) == 0x40000000
    assert we._to_long_ptr((1 << bits) - 1) == -1


@pytest.mark.parametrize(
    ("name", "family"),
    [
        ("gzdoom.exe", "zdoom"), ("/usr/bin/uzdoom", "zdoom"),
        ("chocolate-doom", "chocolate"), ("crispy-doom.exe", "chocolate"),
        ("prboom-plus", "boom"), ("dsda-doom", "boom"), ("woof", "boom"),
        ("doom", "generic"),
    ],
)
def test_classify_engine(name, family):
    assert de.classify_engine(name) == family


def test_build_argv_per_family(tmp_path):
    exe, wad = Path("/g/gzdoom"), Path("/w/doom2.wad")
    z = de.build_argv(exe, "zdoom", wad, 800, 600)
    assert z[:3] == [str(exe), "-iwad", str(wad)]
    assert z[-2:] == ["+vid_fullscreen", "0"]
    assert "-width" in z
    c = de.build_argv(exe, "chocolate", wad, 800, 600, ("-nosound",))
    assert "-window" in c and "800x600" in c and c[-1] == "-nosound"
    assert de.build_argv(exe, "generic", wad, 1, 1)[-1] == "-window"


def test_find_iwad_is_case_insensitive_and_ordered(tmp_path):
    (tmp_path / "DOOM.WAD").write_bytes(b"x")
    (tmp_path / "Doom2.Wad").write_bytes(b"x")
    found = de.find_iwad(tmp_path, env={}, home=tmp_path / "nohome")
    assert found is not None and found.name.lower() == "doom2.wad"


def test_find_iwad_env_and_configured_precedence(tmp_path):
    wad_dir = tmp_path / "wads"
    wad_dir.mkdir()
    (wad_dir / "freedoom1.wad").write_bytes(b"x")
    env = {"DOOMWADDIR": str(wad_dir)}
    assert de.find_iwad(tmp_path / "e", env=env, home=tmp_path).name == "freedoom1.wad"
    custom = tmp_path / "meu.wad"
    custom.write_bytes(b"x")
    assert de.find_iwad(tmp_path / "e", str(custom), env=env, home=tmp_path) == custom
    assert de.find_iwad(tmp_path / "e", env={}, home=tmp_path / "vazio") is None


def test_find_engine_order(tmp_path):
    fake = tmp_path / "meu-engine"
    fake.write_bytes(b"x")
    assert de.find_engine(str(fake), env={}, home=tmp_path) == fake
    assert de.find_engine(None, env={"CODIS_DOOM_ENGINE": str(fake)}, home=tmp_path) == fake
    which = {"chocolate-doom": "/usr/bin/chocolate-doom"}.get
    assert de.find_engine(None, env={}, home=tmp_path, which=which) == Path("/usr/bin/chocolate-doom")
    assert de.find_engine("/nao/existe", env={}, home=tmp_path, which=which) == Path("/usr/bin/chocolate-doom")


def test_resolve_plan_errors_and_success(tmp_path, monkeypatch):
    class Cfg(dict):
        pass

    monkeypatch.setattr(de, "find_engine", lambda *_a, **_k: None)
    with pytest.raises(de.DoomLaunchError) as ei:
        de.resolve_plan(Cfg())
    assert ei.value.kind == "engine"

    engine = tmp_path / "gzdoom"
    engine.write_bytes(b"x")
    monkeypatch.setattr(de, "find_engine", lambda *_a, **_k: engine)
    monkeypatch.setattr(de, "find_iwad", lambda *_a, **_k: None)
    with pytest.raises(de.DoomLaunchError) as ei:
        de.resolve_plan(Cfg())
    assert ei.value.kind == "iwad"

    wad = tmp_path / "doom2.wad"
    monkeypatch.setattr(de, "find_iwad", lambda *_a, **_k: wad)
    plan = de.resolve_plan(Cfg(doom_extra_args="-nomonsters -skill 2"))
    assert plan.family == "zdoom" and plan.iwad == wad
    assert plan.extra_args == ("-nomonsters", "-skill", "2")


def test_doom_locale_keys_present_in_all_locales():
    import json

    root = Path(__file__).resolve().parents[1] / "src" / "locales"
    keys = {
        "doom.hint", "doom.btn_back", "doom.btn_focus", "doom.status_loading",
        "doom.status_embedded", "doom.status_closed", "doom.err_no_engine",
        "doom.err_no_iwad", "doom.err_launch", "doom.err_exited",
        "doom.err_timeout", "doom.status_embed_failed", "doom.status_external",
    }
    for f in (p for p in root.glob("*.json") if not p.name.startswith(".")):
        data = json.loads(f.read_text(encoding="utf-8"))
        assert keys <= set(data), f"{f.name} sem chaves doom.*"
        assert "{hotkey}" in data["doom.hint"], f.name
