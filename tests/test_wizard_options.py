"""Opções do wizard de primeira execução e segurança do TTS."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from src.ui import accessibility as acc
from src.ui import wizard as wz

LOCALES = Path(__file__).resolve().parent.parent / "src" / "locales"


class _Cfg:
    def __init__(self, data=None):
        self.data = dict(data or {})

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


def test_toggle_keys_are_unique():
    keys = [tg.key for tg in wz.ALL_TOGGLES]
    assert len(keys) == len(set(keys))


def test_wizard_exposes_voice_and_accessibility_options():
    keys = {tg.key for tg in wz.A11Y_TOGGLES}
    assert {"a11y_screen_reader", "a11y_high_contrast", "a11y_reduce_motion",
            "a11y_large_ui_font", "a11y_keyboard_nav"} <= keys


def test_tts_is_off_by_default_and_keyboard_nav_on():
    defaults = wz.initial_toggle_values(lambda _k, d: d)
    assert defaults["a11y_screen_reader"] is False
    assert defaults["a11y_keyboard_nav"] is True


def test_initial_values_respect_saved_config():
    cfg = _Cfg({"a11y_screen_reader": True, "lint_enabled": False})
    values = wz.initial_toggle_values(cfg.get)
    assert values["a11y_screen_reader"] is True and values["lint_enabled"] is False


def test_apply_toggles_writes_every_option_as_bool():
    cfg = _Cfg()
    wz.apply_toggles(cfg, {tg.key: True for tg in wz.ALL_TOGGLES})
    assert set(cfg.data) == {tg.key for tg in wz.ALL_TOGGLES}
    assert all(v is True for v in cfg.data.values())


def test_apply_toggles_ignores_unknown_keys():
    cfg = _Cfg()
    wz.apply_toggles(cfg, {"nao_existe": True})
    assert cfg.data == {}


def test_navigation_lists_match_step_count():
    assert len(wz.FirstRunWizard._NAV_KEYS) == wz.FirstRunWizard.STEPS
    assert len(wz.FirstRunWizard._NAV_ICONS) == wz.FirstRunWizard.STEPS


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_new_toggle_texts_exist_in_primary_locales(locale):
    data = json.loads((LOCALES / f"{locale}.json").read_text(encoding="utf-8"))
    new = [tg for tg in wz.ALL_TOGGLES if tg.key.startswith(("a11y_", "lsp_", "line_", "word_", "session_", "auto_check"))]
    for tg in new:
        assert tg.label_key in data, tg.label_key
        if tg.desc_key:
            assert tg.desc_key in data, tg.desc_key
    for key in ("wiz.nav_voice", "wiz.voice_title", "wiz.tts_test", "wiz.tts_detected", "wiz.tts_missing"):
        assert key in data


@pytest.fixture
def captured(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(acc.subprocess, "run", fake_run)
    return calls


def test_windows_tts_never_puts_text_in_the_command(captured):
    bridge = acc.ScreenReaderBridge()
    hostile = "\u2019; Start-Process calc; \u2018 and $(whoami)"
    bridge._speak_windows(hostile, interrupt=False)
    cmd, kwargs = captured[0]
    assert all(hostile not in part and "calc" not in part for part in cmd)
    assert kwargs["env"]["CODIS_TTS_TEXT"] == hostile


def test_posix_tts_passes_text_after_end_of_options(captured, monkeypatch):
    monkeypatch.setattr(acc, "_which", lambda name: name == "spd-say")
    bridge = acc.ScreenReaderBridge()
    bridge._speak_linux("-f /etc/passwd", interrupt=True)
    cmd = captured[0][0]
    assert cmd[-2:] == ["--", "-f /etc/passwd"]


class _Var:
    def __init__(self, value=None):
        self._v = value

    def get(self):
        return self._v

    def set(self, value):
        self._v = value

    def trace_add(self, *_a, **_k):
        return None


@pytest.fixture
def wizard(monkeypatch):
    from unittest.mock import MagicMock

    from src.theme.base import THEMES, _define_themes

    _define_themes()
    monkeypatch.setattr(wz, "tk", MagicMock())
    monkeypatch.setattr(wz, "DropDown", MagicMock())
    instance = object.__new__(wz.FirstRunWizard)
    instance._content = MagicMock()
    instance._opts = {tg.key: _Var(tg.default) for tg in wz.ALL_TOGGLES}
    instance._lint_var = instance._opts["lint_enabled"]
    instance._ac_var = instance._opts["autocomplete"]
    instance._mm_var = instance._opts["minimap"]
    instance._fmt_var = instance._opts["format_on_save"]
    instance._timeline_var = instance._opts["timeline_enabled"]
    instance._autosave_var = instance._opts["autosave"]
    instance._locale_var = _Var("pt-BR")
    instance._theme_var = _Var("nord")
    instance._font_var = _Var("Consolas")
    instance._size_var = _Var(14)
    instance._tab_var = _Var(4)
    instance._shell_var2 = _Var("bash")
    instance._tts_bridge = MagicMock(available=True)
    instance._tts_status = None
    return instance, THEMES["nord"]


@pytest.mark.parametrize("step", ["_step_features", "_step_extra", "_step_voice", "_step_summary"])
def test_new_and_extended_steps_build_without_errors(wizard, step):
    instance, theme = wizard
    getattr(instance, step)(theme)


def test_test_voice_speaks_when_engine_is_available(wizard):
    instance, _theme = wizard
    instance._test_voice()
    instance._tts_bridge.speak.assert_called_once()


def test_test_voice_is_silent_without_engine(wizard):
    instance, _theme = wizard
    instance._tts_bridge.available = False
    instance._test_voice()
    instance._tts_bridge.speak.assert_not_called()


def test_real_default_config_matches_wizard_defaults():
    """O padrão efetivo do app (DEFAULT_CONFIG) não pode contradizer o do wizard."""
    from src.constants import DEFAULT_CONFIG

    assert DEFAULT_CONFIG["a11y_screen_reader"] is False
    for tg in wz.ALL_TOGGLES:
        if tg.key in DEFAULT_CONFIG:
            assert bool(DEFAULT_CONFIG[tg.key]) is tg.default, tg.key
