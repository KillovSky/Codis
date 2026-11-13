"""Ciclo de vida do KEngine/KGlassPane: não renderizar o que ninguém vê."""

from __future__ import annotations

import time
import tkinter as tk

import pytest

from src.ui.k_ui._engine import KEngine
from src.ui.k_ui._widgets import KGlassPane


def _pump(root: tk.Misc, ms: int) -> None:
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        root.update()
        time.sleep(0.003)


@pytest.fixture()
def engine(tk_root):
    top = tk.Toplevel(tk_root)
    top.geometry("640x400+0+0")
    eng = KEngine(top, dark=True, profile="minimal")
    eng.place(x=0, y=0, relwidth=1.0, relheight=1.0)
    _pump(top, 150)
    yield top, eng
    eng.stop()
    top.destroy()


def _count_renders(top, eng, ms=400) -> int:
    n = [0]
    orig = eng._render

    def counted():
        n[0] += 1
        orig()

    eng._render = counted
    _pump(top, ms)
    eng._render = orig
    return n[0]


def test_engine_renders_when_visible(engine):
    top, eng = engine
    assert _count_renders(top, eng) > 0


def test_suspended_engine_does_not_render_and_resumes(engine):
    top, eng = engine
    eng.set_suspended(True)
    _pump(top, 300)
    assert _count_renders(top, eng) == 0
    eng.set_suspended(False)
    assert _count_renders(top, eng) > 0


def test_unmapped_engine_idles(engine):
    top, eng = engine
    eng.place_forget()
    _pump(top, 300)
    assert _count_renders(top, eng) == 0
    eng.place(x=0, y=0, relwidth=1.0, relheight=1.0)
    _pump(top, 400)
    assert _count_renders(top, eng, 600) > 0


def test_glass_pane_reuses_canvas_items(tk_root):
    top = tk.Toplevel(tk_root)
    top.geometry("500x300+0+0")
    ec = tk.Frame(top)
    ec.pack(fill="both", expand=True)
    pane = KGlassPane(dark=True)
    pane.attach(ec)
    try:
        cv = pane._spectrum
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and not (cv.winfo_width() > 1 and cv.find_all()):
            _pump(top, 50)
        _pump(top, 300)
        ids_a = list(cv.find_all())
        assert ids_a
        _pump(top, 400)
        assert list(cv.find_all()) == ids_a, "itens devem ser reaproveitados, não recriados"
        assert len(ids_a) == KGlassPane._SEGMENTS
    finally:
        pane.detach()
        top.destroy()


def _stable_items(top, widget, timeout=3.0):
    """Espera layout assentar e devolve o conjunto de itens do canvas."""
    end = time.monotonic() + timeout
    while time.monotonic() < end and not (widget.winfo_width() > 4 and widget.find_all()):
        _pump(top, 50)
    _pump(top, 300)
    return list(widget.find_all())


@pytest.mark.parametrize("cls_name", ["KTopBar", "KStatusBar"])
def test_bars_reuse_canvas_items(tk_root, cls_name):
    """Antes: delete('all') + ~180 create_rectangle a cada frame (lentidão)."""
    from src.ui.k_ui import _widgets

    top = tk.Toplevel(tk_root)
    top.geometry("700x120+0+0")
    if cls_name == "KTopBar":
        bar = _widgets.KTopBar(top, lambda: None, lambda: None, dark=True)
    else:
        bar = _widgets.KStatusBar(top, lambda: None, dark=True)
    bar.pack(fill="x")
    bar.focus_displayof = lambda: object()
    try:
        ids = _stable_items(top, bar)
        assert ids
        _pump(top, 500)
        assert list(bar.find_all()) == ids, "itens devem ser reaproveitados entre frames"
        assert len(ids) < 120, "poucos itens (antes: >180 só na faixa)"
    finally:
        bar.stop()
        top.destroy()
