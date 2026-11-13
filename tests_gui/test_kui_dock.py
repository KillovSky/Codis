"""Regressão do KDock: a sidebar nunca pode existir em dois lugares ao mesmo tempo.

Bug original: o menu lateral do K-UI abria vazio (``pack(in_=overlay)`` é
rejeitado pelo Tk) e a sidebar clássica reaparecia por cima ao alternar
cosmos/editor, abrir pasta ou voltar à tela inicial.
"""

from __future__ import annotations

import time
import tkinter as tk
from types import SimpleNamespace

import pytest

from src.ui.k_ui._colors import _wlift
from src.ui.k_ui._dock import KDock

LEFT, TOP, WIDTH = 48, 58, 260


def _pump(root: tk.Misc, ms: int = 30) -> None:
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        root.update()
        time.sleep(0.002)


@pytest.fixture()
def mini(tk_root):
    """Réplica mínima do layout do Codis: sidebar filha da RAIZ, gerida pelo paned."""
    top = tk.Toplevel(tk_root)
    top.geometry("900x500+0+0")
    paned = tk.PanedWindow(top, orient="horizontal")
    paned.pack(fill="both", expand=True)
    actbar = tk.Frame(paned, width=48, bg="#111")
    paned.add(actbar, width=48, minsize=48, stretch="never")
    sidebar = tk.Frame(top, bg="#222")
    stack = tk.Frame(sidebar, bg="#222")
    stack.pack(fill="both", expand=True)
    tree = tk.Label(stack, text="TREE")
    tree.pack(fill="both", expand=True)
    paned.add(sidebar, width=220, minsize=60, stretch="never")
    content = tk.Frame(paned, bg="#000")
    paned.add(content, stretch="always")
    cosmos = tk.Canvas(content, bg="#000", highlightthickness=0)
    cosmos.place(x=0, y=0, relwidth=1.0, relheight=1.0)
    _wlift(cosmos)

    events: list[bool] = []
    dock = KDock(
        top, paned, sidebar, actbar,
        get_width=lambda: WIDTH,
        get_left=lambda: LEFT,
        get_top=lambda: TOP,
        reduce_motion=lambda: True,
        on_change=events.append,
    )
    dock.GRACE_S = 0.0
    _pump(top)
    yield SimpleNamespace(top=top, paned=paned, actbar=actbar, sidebar=sidebar,
                          tree=tree, content=content, cosmos=cosmos, dock=dock,
                          events=events)
    dock.shutdown()
    top.destroy()


def _panes(m) -> list[str]:
    return [str(p) for p in m.paned.panes()]


def test_enter_float_removes_sidebar_from_paned(mini):
    assert str(mini.sidebar) in _panes(mini)
    mini.dock.enter_float(visible=False)
    _pump(mini.top)
    assert str(mini.sidebar) not in _panes(mini)
    assert not mini.sidebar.winfo_ismapped()
    assert mini.dock.floating and not mini.dock.visible


def test_show_places_real_tree_at_expected_geometry(mini):
    mini.dock.enter_float(visible=False)
    mini.dock.show(animate=False)
    _pump(mini.top)
    assert mini.sidebar.winfo_ismapped()
    assert mini.tree.winfo_ismapped(), "a árvore precisa ficar visível no painel K-UI"
    assert (mini.sidebar.winfo_x(), mini.sidebar.winfo_y()) == (LEFT, TOP)
    assert mini.sidebar.winfo_width() == WIDTH


def test_panel_stays_above_cosmos_canvas(mini):
    mini.dock.enter_float(visible=True)
    _wlift(mini.cosmos)
    _pump(mini.top)
    kids = [str(c) for c in mini.top.winfo_children()]
    assert kids.index(str(mini.sidebar)) > kids.index(str(mini.paned))
    mini.dock.raise_panel()
    assert mini.sidebar.winfo_ismapped()


def test_no_second_sidebar_after_repeated_mode_switches(mini):
    for _ in range(40):
        mini.dock.enter_float(visible=True)
        mini.dock.hide(animate=False)
        mini.dock.show(animate=False)
        mini.dock.leave_float()
        _pump(mini.top, 5)
    _pump(mini.top)
    assert _panes(mini).count(str(mini.sidebar)) == 1
    assert not mini.dock.floating
    assert mini.sidebar.winfo_manager() != "place"


def test_leave_float_returns_sidebar_to_paned_with_content(mini):
    mini.dock.enter_float(visible=True)
    _pump(mini.top)
    was_open = mini.dock.leave_float()
    _pump(mini.top)
    assert was_open is True
    assert str(mini.sidebar) in _panes(mini)
    assert mini.tree.winfo_ismapped()


def test_click_outside_closes_and_notifies(mini):
    mini.dock.enter_float(visible=True)
    mini.events.clear()
    _pump(mini.top)
    mini.content.event_generate("<ButtonPress-1>", x=10, y=10)
    _pump(mini.top)
    assert not mini.dock.visible
    assert not mini.sidebar.winfo_ismapped()
    assert mini.events == [False]


def test_click_on_activity_bar_does_not_close(mini):
    """Antes: o clique no ícone abria a sidebar e o handler global a fechava."""
    mini.dock.enter_float(visible=True)
    _pump(mini.top)
    mini.actbar.event_generate("<ButtonPress-1>", x=5, y=5)
    _pump(mini.top)
    assert mini.dock.visible and mini.sidebar.winfo_ismapped()


def test_click_inside_does_not_close(mini):
    mini.dock.enter_float(visible=True)
    _pump(mini.top)
    mini.tree.event_generate("<ButtonPress-1>", x=3, y=3)
    _pump(mini.top)
    assert mini.dock.visible


def test_grace_period_ignores_click_right_after_show(mini):
    mini.dock.GRACE_S = 0.5
    mini.dock.enter_float(visible=False)
    mini.dock.show(animate=False)
    mini.content.event_generate("<ButtonPress-1>", x=10, y=10)
    _pump(mini.top)
    assert mini.dock.visible


def test_pinned_panel_ignores_outside_click(mini):
    state = {"pin": True}
    mini.dock._get_pinned = lambda: state["pin"]
    mini.dock.enter_float(visible=True)
    _pump(mini.top)
    mini.content.event_generate("<ButtonPress-1>", x=10, y=10)
    _pump(mini.top)
    assert mini.dock.visible


def test_shutdown_removes_only_its_own_binding(mini):
    """Antes: root.unbind('<Button-1>') apagava binds de outros módulos."""
    hits: list[int] = []
    mini.top.bind("<ButtonPress-1>", lambda _e: hits.append(1), add="+")
    mini.dock.shutdown()
    mini.content.event_generate("<ButtonPress-1>", x=1, y=1)
    _pump(mini.top)
    assert hits == [1]


def test_animation_reaches_target(mini):
    mini.dock._reduce_motion = lambda: False
    mini.dock.enter_float(visible=False)
    mini.dock.show(animate=True)
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and mini.sidebar.winfo_x() != LEFT:
        mini.top.update()
        time.sleep(0.003)
    assert mini.sidebar.winfo_x() == LEFT
    mini.dock.hide(animate=True)
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and mini.sidebar.winfo_ismapped():
        mini.top.update()
        time.sleep(0.003)
    assert not mini.sidebar.winfo_ismapped()


def test_relayout_follows_window_resize(mini):
    mini.dock.enter_float(visible=True)
    _pump(mini.top)
    h0 = mini.sidebar.winfo_height()
    mini.top.geometry("900x700+0+0")
    _pump(mini.top, 80)
    mini.dock.relayout()
    _pump(mini.top)
    assert mini.sidebar.winfo_height() > h0
