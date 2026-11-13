"""Codis - utilitário para grab_set() seguro em janelas Toplevel.

Problema:
    Em Linux/X11 e em binários compilados com PyInstaller --onefile,
    chamar ``grab_set()`` imediatamente após criar um ``tk.Toplevel``
    lança ``TclError: grab failed: window not viewable`` porque a janela
    ainda não foi mapeada no display pelo servidor X.

Solução:
    ``safe_grab_set()`` força ``update_idletasks()`` + ``wait_visibility()``
    antes de ``grab_set()``, com fallback de retry via ``after()``.

Uso:
    from src.ui.grab_safe import safe_grab_set

    class MeuDialog(tk.Toplevel):
        def __init__(self, parent):
            super().__init__(parent)
            ...
            safe_grab_set(self)   # em vez de self.grab_set()
"""

from __future__ import annotations

import contextlib
import logging
import tkinter as tk

logger = logging.getLogger("Codis.GrabSafe")


def safe_grab_set(
    win: tk.Toplevel, *, retry_ms: int = 60, max_retries: int = 5
) -> None:
    """Chama grab_set() de forma segura após garantir que a janela está visível.

    ``wait_visibility()`` pode bloquear indefinidamente se a janela nunca
    se tornar visível; por isso qualquer ``TclError`` na primeira tentativa
    é tratado delegando o trabalho ao mecanismo de retry assíncrono via
    ``after()``, que não bloqueia o mainloop.

    Args:
        win: Janela Toplevel alvo.
        retry_ms: Milissegundos de espera entre tentativas.
        max_retries: Número máximo de tentativas antes de desistir.
    """
    try:
        win.update_idletasks()
        win.wait_visibility()
        win.grab_set()
        return
    except tk.TclError as exc:
        logger.debug(
            "grab_set() falhou na primeira tentativa (%s) - reagendando", exc)

    _retry_grab(win, retry_ms=retry_ms, attempts_left=max_retries)


def _retry_grab(win: tk.Toplevel, *, retry_ms: int, attempts_left: int) -> None:
    """Reagenda tentativas de grab_set() via after() até a janela ficar visível.

    Args:
        win: Janela Toplevel alvo.
        retry_ms: Milissegundos de espera entre tentativas.
        attempts_left: Tentativas restantes antes de desistir.
    """
    if attempts_left <= 0:
        logger.warning(
            "safe_grab_set: desistindo após máximo de tentativas (%s)", win)
        return

    def _try() -> None:
        """Reavalia a visibilidade da janela e tenta o grab novamente."""
        if not win.winfo_exists():
            return
        try:
            win.update_idletasks()
            if win.winfo_viewable():
                win.grab_set()
                return
        except tk.TclError:
            pass
        _retry_grab(win, retry_ms=retry_ms, attempts_left=attempts_left - 1)

    with contextlib.suppress(Exception):
        win.after(retry_ms, _try)
