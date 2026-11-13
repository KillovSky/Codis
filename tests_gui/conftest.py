"""Testes de GUI reais: Tkinter de verdade em um display (Xvfb). Pulados sem display.

Rodar com: ``xvfb-run -a python -m pytest tests_gui`` (ou DISPLAY já configurado).
Não usa os stubs de Tkinter de ``tests/``.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ["HOME"] = tempfile.mkdtemp(prefix="codis-gui-home-")

try:
    import tkinter as tk
except ImportError:  # pragma: no cover
    tk = None


@pytest.fixture(scope="session")
def tk_root():
    if tk is None or not os.environ.get("DISPLAY"):
        pytest.skip("sem tkinter ou DISPLAY")
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"sem display utilizável: {exc}")
    root.geometry("900x420+0+0")
    yield root
    root.destroy()


class Driver:
    """Gira o laço de eventos e injeta teclas como um usuário."""

    SYMS = {
        " ": "space", "/": "slash", "-": "minus", ".": "period", "\r": "Return", "\t": "Tab",
        "=": "equal", ":": "colon", ";": "semicolon", "_": "underscore", ",": "comma",
        "!": "exclam", "@": "at", "#": "numbersign", "$": "dollar", "%": "percent",
        "^": "asciicircum", "&": "ampersand", "*": "asterisk", "(": "parenleft",
        ")": "parenright", "+": "plus", "{": "braceleft", "}": "braceright", "|": "bar",
        '"': "quotedbl", "<": "less", ">": "greater", "?": "question", "~": "asciitilde",
        "'": "apostrophe", "`": "grave", "[": "bracketleft", "]": "bracketright",
        "\\": "backslash",
    }
    SHIFTED = set('!@#$%^&*()_+{}|:"<>?~')

    def __init__(self, root, panel):
        self.root, self.panel = root, panel

    @property
    def view(self):
        return self.panel._view

    def spin(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.root.update()
            time.sleep(0.003)

    def wait(self, predicate, timeout=8.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.root.update()
            if predicate():
                return True
            time.sleep(0.004)
        return False

    def key(self, keysym, state=0):
        self.view.event_generate("<Key>", keysym=keysym, state=state)
        self.root.update()

    def type(self, text):
        for ch in text:
            self.key(self.SYMS.get(ch, ch), state=1 if (ch.isupper() or ch in self.SHIFTED) else 0)

    def text(self):
        return self.view.get("1.0", "end-1c")


@pytest.fixture
def make_panel(tk_root):
    created = []

    def factory(cwd="/tmp"):
        from src.config import ConfigManager
        from src.theme.base import _define_themes
        from src.theme.manager import ThemeManager
        from src.ui.terminal import TerminalPanel

        _define_themes()
        panel = TerminalPanel(tk_root, ThemeManager("fluent_dark"), ConfigManager(), cwd=cwd)
        panel.pack(fill="both", expand=True)
        created.append(panel)
        driver = Driver(tk_root, panel)
        driver.spin(0.6)
        panel._view.focus_force()
        tk_root.update()
        return driver

    yield factory
    for panel in created:
        panel.destroy()
    tk_root.update()


@pytest.fixture(autouse=True)
def _collect_tk_garbage_on_main_thread():
    """Finaliza objetos Tk órfãos na thread principal, antes e depois de cada teste.

    Se o coletor de lixo rodar numa thread de trabalho (rede, capa do player),
    ``tkinter.Variable.__del__`` faz uma chamada Tcl entre threads; sem
    ``mainloop`` ativo (os testes usam ``update()``) cada chamada dorme ~1 s
    e congela essa thread, gerando falhas intermitentes por timeout.
    """
    import gc

    gc.collect()
    yield
    gc.collect()
