"""Efeitos visuais baseados em ``after()`` do Tkinter (sem threads)."""

from __future__ import annotations

import contextlib
import tkinter as tk


def fade_in(
    win: tk.Toplevel,
    *,
    duration_ms: int = 180,
    steps: int = 12,
    target_alpha: float = 1.0,
) -> None:
    """Abre uma Toplevel com fade-in suave.

    Args:
        win: Toplevel a animar.
        duration_ms: Duração total da animação.
        steps: Número de quadros da animação.
        target_alpha: Opacidade final desejada (0.0-1.0). O fade termina
            neste valor, preservando transparências "vidro" (ex.: dropdowns
            holográficos com ``-alpha 0.97``).
    """
    target_alpha = max(0.0, min(1.0, target_alpha))
    interval = max(1, duration_ms // steps)
    step_size = target_alpha / steps

    def _step(alpha: float = 0.0) -> None:
        if not win.winfo_exists():
            return
        alpha = min(target_alpha, alpha + step_size)
        with contextlib.suppress(Exception):
            win.wm_attributes("-alpha", alpha)
        if alpha < target_alpha:
            win.after(interval, lambda: _step(alpha))

    with contextlib.suppress(Exception):
        win.wm_attributes("-alpha", 0.0)
    win.after(10, _step)
