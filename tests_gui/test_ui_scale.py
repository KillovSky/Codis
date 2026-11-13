"""Testes reais (Xvfb) da escala global da UI."""

from __future__ import annotations

import tkinter as tk

import pytest

from src.ui import ui_scale as us


class _Cfg(dict):
    def set(self, key, value):
        self[key] = value


@pytest.fixture
def scaler(tk_root):
    cfg = _Cfg(ui_scale=100)
    sc = us.UIScaler(tk_root, cfg)
    sc.start()
    yield sc
    sc.set_percent(100)


def _size(widget):
    parts = widget.tk.splitlist(str(widget.cget("font")))
    return int(parts[1])


def test_clamp_percent():
    assert us.clamp_percent(0) == 0
    assert us.clamp_percent("abc") == 0
    assert us.clamp_percent(10) == us.MIN_PERCENT
    assert us.clamp_percent(999) == us.MAX_PERCENT
    assert us.clamp_percent("125") == 125


def test_scale_down_and_up_without_compounding(tk_root, scaler):
    lbl = tk.Label(tk_root, text="x", font=("DejaVu Sans", 10, "bold"))
    lbl.pack()
    scaler.set_percent(100)
    assert _size(lbl) == 10
    scaler.set_percent(50)
    assert _size(lbl) == 6
    scaler.set_percent(150)
    assert _size(lbl) == 15
    scaler.set_percent(150)
    assert _size(lbl) == 15
    scaler.set_percent(100)
    assert _size(lbl) == 10
    assert "bold" in str(lbl.cget("font"))
    lbl.destroy()


def test_excluded_widget_untouched(tk_root, scaler):
    txt = tk.Text(tk_root, font=("DejaVu Sans Mono", 11))
    us.exclude_from_scale(txt)
    txt.pack()
    scaler.set_percent(200)
    assert _size(txt) == 11
    txt.destroy()


def test_new_widget_captured_on_map(tk_root, scaler):
    scaler.set_percent(200)
    lbl = tk.Label(tk_root, text="novo", font=("DejaVu Sans", 10))
    lbl.pack()
    tk_root.update()
    assert _size(lbl) == 20
    lbl.destroy()


def test_font_changed_by_owner_becomes_new_base(tk_root, scaler):
    lbl = tk.Label(tk_root, text="x", font=("DejaVu Sans", 10))
    lbl.pack()
    scaler.set_percent(200)
    lbl.configure(font=("DejaVu Sans", 8))
    scaler.set_percent(100)
    assert _size(lbl) == 8
    lbl.destroy()


def test_px_follows_factor(scaler):
    scaler.set_percent(150)
    assert us.px(20) == 30
    scaler.set_percent(50)
    assert us.px(20) == 10


def test_treeview_rowheight_scales(tk_root, scaler):
    from tkinter import ttk

    scaler.set_percent(100)
    base = int(ttk.Style(tk_root).lookup("Treeview", "rowheight") or 0)
    scaler.set_percent(200)
    assert int(ttk.Style(tk_root).lookup("Treeview", "rowheight")) == base * 2


def test_auto_percent_in_range(tk_root):
    assert us.AUTO_MIN <= us.auto_percent(tk_root) <= us.AUTO_MAX


def test_fit_window_never_exceeds_screen(tk_root):
    w, h = us.fit_window(tk_root, 99999, 99999)
    assert w <= tk_root.winfo_screenwidth()
    assert h < tk_root.winfo_screenheight()


def test_step_persists_and_reset_goes_auto(tk_root):
    cfg = _Cfg(ui_scale=100)
    sc = us.UIScaler(tk_root, cfg)
    sc.start()
    assert sc.step(+1) == 110 and cfg["ui_scale"] == 110
    assert sc.step(-1) == 100
    sc.reset()
    assert cfg["ui_scale"] == 0
    assert sc.percent == us.auto_percent(tk_root)
    sc.set_percent(100)


def test_fixed_size_frame_scales_and_restores(tk_root, scaler):
    bar = tk.Frame(tk_root, height=26, width=48)
    bar.pack_propagate(False)
    bar.pack()
    scaler.set_percent(100)
    scaler.set_percent(200)
    assert int(bar.cget("height")) == 52 and int(bar.cget("width")) == 96
    scaler.set_percent(200)
    assert int(bar.cget("height")) == 52
    scaler.set_percent(100)
    assert int(bar.cget("height")) == 26
    bar.destroy()


def test_separator_and_propagating_frames_untouched(tk_root, scaler):
    sep = tk.Frame(tk_root, height=1)
    sep.pack_propagate(False)
    flow = tk.Frame(tk_root, height=40)
    scaler.set_percent(200)
    assert int(sep.cget("height")) == 1
    assert int(flow.cget("height")) == 40
    sep.destroy()
    flow.destroy()
