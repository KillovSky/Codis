"""Testes de src/_font_safety.py (proteção contra segfault Tk/Xft)."""

from __future__ import annotations

import os
import sys

import pytest

from src import _font_safety as fs


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    monkeypatch.delenv("_CODIS_FONT_SAFETY", raising=False)
    monkeypatch.delenv("CODIS_SAFE_FONTS", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(fs.sys, "platform", "linux")
    base = tmp_path / "fonts.conf"
    base.write_text("<fontconfig/>", encoding="utf-8")
    monkeypatch.setenv("FONTCONFIG_FILE", str(base))
    yield
    os.environ.pop("_CODIS_FONT_SAFETY", None)


def test_applies_on_old_tk(monkeypatch):
    monkeypatch.setattr(fs, "_tcl_patchlevel", lambda: (8, 6, 12))
    assert fs.apply_font_safety() is True
    conf = open(os.environ["FONTCONFIG_FILE"], encoding="utf-8").read()
    assert 'name="color"' in conf and "fonts.conf" in conf


def test_skips_on_fixed_tk(monkeypatch):
    monkeypatch.setattr(fs, "_tcl_patchlevel", lambda: (8, 6, 14))
    assert fs.apply_font_safety() is False


def test_env_override_forces_and_disables(monkeypatch):
    monkeypatch.setattr(fs, "_tcl_patchlevel", lambda: (8, 6, 14))
    monkeypatch.setenv("CODIS_SAFE_FONTS", "1")
    assert fs.apply_font_safety() is True
    monkeypatch.delenv("_CODIS_FONT_SAFETY")
    monkeypatch.setattr(fs, "_tcl_patchlevel", lambda: (8, 6, 12))
    monkeypatch.setenv("CODIS_SAFE_FONTS", "0")
    assert fs.apply_font_safety() is False


def test_idempotent(monkeypatch):
    monkeypatch.setattr(fs, "_tcl_patchlevel", lambda: (8, 6, 12))
    assert fs.apply_font_safety() is True
    assert fs.apply_font_safety() is False


def test_noop_outside_linux(monkeypatch):
    monkeypatch.setattr(fs.sys, "platform", "win32")
    monkeypatch.setattr(fs, "_tcl_patchlevel", lambda: (8, 6, 12))
    assert fs.apply_font_safety() is False
    assert sys.platform != "never"
