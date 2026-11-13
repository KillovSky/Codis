"""Regressão: o dropdown da barra de menus não pode fechar sozinho ao mover
o mouse rápido entre itens (inclusive entre linhas com submenu e comuns)."""

from __future__ import annotations

import random
import time
import tkinter as tk

import pytest


def _pump(root, ms: int) -> None:
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        root.update()
        time.sleep(0.002)


def _rows(win: tk.Misc) -> dict[str, tk.Misc]:
    found: dict[str, tk.Misc] = {}

    def walk(widget: tk.Misc) -> None:
        for child in widget.winfo_children():
            if child.bind("<Enter>"):
                texts = [c.cget("text").strip() for c in child.winfo_children()
                         if isinstance(c, tk.Label) and c.cget("text")]
                if texts:
                    found.setdefault(texts[0], child)
            walk(child)

    walk(win)
    return found


def _hover(root, row) -> None:
    root.event_generate(
        "<Motion>", warp=True,
        x=row.winfo_rootx() - root.winfo_rootx() + 8,
        y=row.winfo_rooty() - root.winfo_rooty() + 6)
    for w in [row, *row.winfo_children()]:
        try:
            w.event_generate("<Enter>")
        except tk.TclError:
            pass


@pytest.fixture()
def bar(tk_root):
    from src.theme.base import _define_themes
    from src.theme.manager import ThemeManager, _patch_all_themes, _patch_theme
    from src.ui.menu_bar import MenuBar, MenuDef

    _define_themes()
    _patch_all_themes()
    tmgr = ThemeManager("fluent_dark")
    _patch_theme(tmgr.theme)
    tk_root.geometry("900x500+0+0")
    menubar = MenuBar(tk_root, tmgr)
    menubar.pack(fill="x", side="top")
    sub = MenuDef()
    for i in range(4):
        sub.add_command(label=f"sub{i}", command=lambda: None)
    menu = MenuDef()
    menu.add_command(label="Novo")
    menu.add_command(label="Abrir")
    menu.add_cascade(label="Recentes", menu=sub)
    menu.add_command(label="Salvar")
    menu.add_cascade(label="Exportar", menu=sub)
    menu.add_command(label="Sair")
    menubar.add_cascade("Arquivo", menu)
    tk_root.update()
    yield menubar
    menubar._close_all()
    menubar.destroy()


def test_rapid_hover_never_closes_menu(tk_root, bar):
    rng = random.Random(7)
    sequence = ["Recentes", "Salvar", "Exportar", "Novo", "Recentes", "Abrir",
                "Exportar", "Sair"]
    for trial in range(25):
        bar._close_all()
        _pump(tk_root, 40)
        bar._open(bar._buttons[0])
        _pump(tk_root, 120)
        rows = _rows(bar._buttons[0]._drop._win)
        for name in sequence:
            assert bar._active is not None, f"menu fechou sozinho na tentativa {trial} ({name})"
            _hover(tk_root, rows[name])
            _pump(tk_root, rng.choice([4, 8, 15, 25, 40, 60]))
        _pump(tk_root, 300)
        assert bar._active is not None, f"menu fechou sozinho na tentativa {trial}"


def test_pointer_detection_inside_and_outside(tk_root, bar):
    bar._open(bar._buttons[0])
    _pump(tk_root, 150)
    rows = _rows(bar._buttons[0]._drop._win)
    _hover(tk_root, rows["Abrir"])
    _pump(tk_root, 80)
    assert bar._pointer_in_menus() is True
    tk_root.event_generate("<Motion>", warp=True, x=600, y=400)
    _pump(tk_root, 80)
    assert bar._pointer_in_menus() is False


def test_chain_windows_follow_submenu(tk_root, bar):
    bar._open(bar._buttons[0])
    _pump(tk_root, 150)
    assert len(bar._chain_windows()) == 1
    rows = _rows(bar._buttons[0]._drop._win)
    _hover(tk_root, rows["Recentes"])
    _pump(tk_root, 150)
    assert len(bar._chain_windows()) == 2
