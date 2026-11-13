"""Testes GUI: navegação por abas, sidebar animada e resize fluido da janela."""

from __future__ import annotations

import time
from pathlib import Path

import tkinter as tk

import pytest


@pytest.fixture(scope="module")
def app(tk_root):
    from src.app import CodisApp

    tk_root.geometry("1100x700+0+0")
    instance = CodisApp(tk_root)
    instance._cfg.set("workspace_trust", False)
    tk_root.update()
    yield instance
    for child in list(tk_root.winfo_children()):
        try:
            child.destroy()
        except tk.TclError:
            pass


def _pump(root, ms: int) -> None:
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        root.update()
        time.sleep(0.005)


@pytest.fixture()
def three_files(app, tk_root, tmp_path: Path):
    while app._editors:
        app._editors[0].modified = False
        app._close_tab(0)
    app._closed_tabs.clear()
    paths = []
    for name in ("a.py", "b.py", "c.py"):
        path = tmp_path / name
        path.write_text(f"# {name}\n", encoding="utf-8")
        app.open_file(path)
        paths.append(path)
    tk_root.update()
    return paths


def test_shortcuts_registered(tk_root, app):
    bound = set(tk_root.bind())
    for seq in ("<Control-Key-1>", "<Control-Key-9>", "<Control-Key-Prior>",
                "<Control-Key-Next>", "<Control-Shift-Key-T>"):
        assert seq in bound, seq


def test_goto_tab_number(app, three_files):
    app._goto_tab_number(1)
    assert app._active_idx == 0
    app._goto_tab_number(2)
    assert app._active_idx == 1
    app._goto_tab_number(9)
    assert app._active_idx == 2
    app._goto_tab_number(7)
    assert app._active_idx == 2


def test_reopen_closed_tab(app, tk_root, three_files):
    victim = three_files[1]
    idx = app._find_editor_for_path(victim)
    app._close_tab(idx)
    assert app._find_editor_for_path(victim) is None
    app._reopen_closed_tab()
    tk_root.update()
    assert app._find_editor_for_path(victim) is not None


def test_reopen_skips_deleted_files(app, tk_root, three_files):
    gone = three_files[0]
    app._close_tab(app._find_editor_for_path(gone))
    gone.unlink()
    before = len(app._editors)
    app._reopen_closed_tab()
    assert len(app._editors) == before


def test_gc_is_deferred_and_coalesced(app, tk_root, three_files):
    app._close_tab(0)
    first = app._gc_job
    assert first is not None
    app._close_tab(0)
    assert app._gc_job is not None and app._gc_job != first
    _pump(tk_root, 600)
    assert app._gc_job is None


def test_sidebar_animation_settles(app, tk_root):
    app._show_sidebar()
    _pump(tk_root, 300)
    width = app._cfg.get("sidebar_width", 220)
    assert abs(app._sidebar.winfo_width() - width) <= 8
    app._hide_sidebar()
    _pump(tk_root, 300)
    assert app._sidebar.winfo_width() <= 8


def test_sidebar_reduce_motion_is_instant(app, tk_root):
    app._cfg.set("a11y_reduce_motion", True)
    try:
        app._show_sidebar()
        assert getattr(app, "_sidebar_anim_job", None) is None
    finally:
        app._cfg.set("a11y_reduce_motion", False)
        app._hide_sidebar()
        _pump(tk_root, 200)


def test_window_resize_suspends_and_resumes_overlays(app, tk_root, three_files):
    _pump(tk_root, 300)
    tk_root.geometry("1000x650")
    tk_root.update()
    tk_root.geometry("900x600")
    tk_root.update()
    assert app._win_resizing is True
    _pump(tk_root, 400)
    assert app._win_resizing is False
    assert app._win_resize_job is None
