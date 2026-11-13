"""
K-UI - Utilidades Tk de baixo nível.

``Misc.unbind(sequence, funcid)`` do Tkinter remove **todos** os handlers do
evento no Python < 3.13 (gh-75666) e apenas o indicado nas versões novas.
Como o Codis tem vários módulos que fazem ``root.bind(..., add="+")`` no
mesmo evento (resize suave, atalhos, K-UI), um ``unbind`` ingênuo derrubava
bindings alheios. Estes helpers removem exclusivamente o handler pedido, de
forma idêntica em qualquer versão do Python.
"""

from __future__ import annotations

import contextlib
import tkinter as tk
from collections.abc import Callable
from typing import Any

__all__ = ["bind_add", "unbind_one"]


def bind_add(widget: tk.Misc, sequence: str, func: Callable[..., Any]) -> str:
    """Registra ``func`` em ``sequence`` sem substituir handlers existentes.

    Returns:
        ``funcid`` que deve ser passado a :func:`unbind_one` para remoção.
    """
    return widget.bind(sequence, func, add="+")


def unbind_one(widget: tk.Misc, sequence: str, funcid: str | None) -> None:
    """Remove somente o handler ``funcid`` de ``sequence``.

    Preserva todos os demais handlers registrados no mesmo evento.
    """
    if not funcid:
        return
    with contextlib.suppress(tk.TclError):
        script = str(widget.tk.call("bind", widget._w, sequence))
        kept = [ln for ln in script.split("\n") if ln.strip() and funcid not in ln]
        widget.tk.call("bind", widget._w, sequence, "\n".join(kept))
    with contextlib.suppress(tk.TclError, AttributeError):
        widget.deletecommand(funcid)
