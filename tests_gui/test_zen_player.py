"""Testes GUI do Zen Player: attach/detach, EQ, capas e integração com o app."""

from __future__ import annotations

import time
import tkinter as tk

import pytest


def _pump(root, ms: int) -> None:
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        root.update()
        time.sleep(0.005)


def _wait(root, predicate, timeout_ms: int = 5000) -> bool:
    """Bombeia o Tk até ``predicate()`` ser verdadeiro (ou estourar o tempo)."""
    end = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < end:
        root.update()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


@pytest.fixture()
def rig(tk_root, tmp_path, monkeypatch):
    from PIL import Image

    from src.config import ConfigManager
    from src.theme.manager import ThemeManager
    from src.ui.zen_media import ZenMediaPlayer
    from src.ui.zen_player import ZenPlayerHost

    monkeypatch.setenv("HOME", str(tmp_path))
    song = tmp_path / "Artista - Faixa.mp3"
    song.write_bytes(b"x")
    Image.new("RGB", (80, 80), "#e91e63").save(tmp_path / "Artista - Faixa.png")
    other = tmp_path / "outra.wav"
    other.write_bytes(b"x")

    cfg = ConfigManager()
    tmgr = ThemeManager(cfg.get("theme", "fluent_dark"))
    holder = tk.Frame(tk_root)
    holder.pack(fill="both", expand=True)
    player = ZenMediaPlayer(tk_root=tk_root)
    player._available = False
    player.set_playlist([{"name": "Artista - Faixa", "url": str(song)},
                         {"name": "outra", "url": str(other)}])
    host = ZenPlayerHost(tk_root, player, cfg, tmgr, dock_before=holder)
    yield host, player, cfg, holder
    host.destroy()
    holder.destroy()


def test_attach_detach_roundtrip(tk_root, rig):
    host, _player, cfg, holder = rig
    host.show()
    _pump(tk_root, 100)
    assert host.mode == "floating" and host._win is not None and host.visible

    host.toggle_dock()
    _pump(tk_root, 100)
    assert host.mode == "docked" and host._win is None
    assert host._view._compact is True
    assert host._view.master is tk_root
    assert cfg.get("zen_player_mode") == "docked"
    assert str(host._view) in [str(w) for w in tk_root.pack_slaves()]

    host.toggle_dock()
    _pump(tk_root, 100)
    assert host.mode == "floating" and host._win is not None
    assert host._view._compact is False


def test_hide_and_show_keep_mode(tk_root, rig):
    host, *_ = rig
    host.set_mode("docked")
    host.hide()
    assert not host.visible
    host.show()
    assert host.mode == "docked" and host.visible


def test_cover_and_metadata_load_from_sidecar(tk_root, rig):
    host, *_ = rig
    host.show()
    assert _wait(tk_root, lambda: host._view._info.origin != "none")
    info = host._view._info
    assert info.origin == "sidecar"
    assert info.title == "Faixa" and info.artist == "Artista"
    assert host._view._cover_photo is not None
    assert info.accent and info.accent.startswith("#")


def test_next_track_uses_generated_cover(tk_root, rig):
    host, player, *_ = rig
    host.show()
    assert _wait(tk_root, lambda: host._view._info.origin == "sidecar")
    player.next()
    assert _wait(tk_root, lambda: host._view._info.origin == "generated")


def test_equalizer_panel_updates_engine_and_config(tk_root, rig):
    host, player, cfg, _ = rig
    host.show()
    view = host._view
    view.show_page("eq")
    view._eq.apply_preset("Bass Boost")
    assert player.eq_gains[0] == 6 and player.eq_enabled
    assert cfg.get("zen_eq_gains")[0] == 6
    view._eq.set_enabled(False)
    assert player.eq_enabled is False
    view._eq.reset()
    assert player.eq_gains == [0.0] * 10 and view._eq.current_preset_name() == "Flat"


def test_equalizer_band_drag_and_custom_preset(tk_root, rig, monkeypatch):
    host, player, cfg, _ = rig
    host.show()
    view = host._view
    view.show_page("eq")
    _pump(tk_root, 100)
    eq = view._eq
    eq._set_band(3, 4.0)
    assert player.eq_gains[3] == 4.0 and eq.current_preset_name() == "Personalizado"
    monkeypatch.setattr("tkinter.simpledialog.askstring", lambda *a, **k: "Meu Som")
    eq._save_preset()
    assert cfg.get("zen_eq_custom")["Meu Som"][3] == 4.0
    eq.reset()
    eq.apply_custom("Meu Som")
    assert player.eq_gains[3] == 4.0 and eq.current_preset_name() == "Meu Som"


def test_eq_state_restored_by_new_host(tk_root, rig):
    host, player, cfg, holder = rig
    cfg.set("zen_eq_gains", [1, 2, 3, 0, 0, 0, 0, 0, 0, -3])
    cfg.set("zen_eq_enabled", True)
    from src.ui.zen_media import ZenMediaPlayer
    from src.ui.zen_player import ZenPlayerHost

    fresh = ZenMediaPlayer(tk_root=tk_root)
    ZenPlayerHost(tk_root, fresh, cfg, host._tmgr, dock_before=holder)
    assert fresh.eq_gains[:3] == [1.0, 2.0, 3.0] and fresh.eq_gains[-1] == -3.0


def test_seek_and_volume_controls(tk_root, rig):
    host, player, *_ = rig
    host.show()
    view = host._view
    view._volume_set(0.25)
    assert player.volume == 25
    view._toggle_mute()
    assert player.volume == 0
    view._toggle_mute()
    assert player.volume == 25


def test_listeners_do_not_steal_each_other(tk_root, rig):
    _host, player, *_ = rig
    calls: list[str] = []

    class A:
        def cb(self): calls.append("a")

    class B:
        def cb(self): calls.append("b")

    a, b = A(), B()
    player.on_change(a.cb)
    player.on_change(b.cb)
    player._notify_change()
    assert sorted(calls) == ["a", "b"]
    player.remove_listener(a)


def test_minimize_calls_callback_and_hides(tk_root, rig):
    host, *_ = rig
    seen: list[int] = []
    host._on_minimize = lambda: seen.append(1)
    host.show()
    host.minimize()
    assert seen == [1] and not host.visible


def test_app_integration_menu_and_shortcut(tk_root, tmp_path):
    from src.app import CodisApp

    tk_root.geometry("1100x700+0+0")
    app = CodisApp(tk_root)
    tk_root.update()
    assert "<Control-Alt-Key-p>" in set(tk_root.bind())
    app._toggle_zen_player()
    _pump(tk_root, 150)
    assert app._zen_player is not None and app._zen_player.visible
    app._toggle_zen_player()
    assert not app._zen_player.visible
