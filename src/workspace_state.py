"""Estado compartilhado de confiança do workspace aberto.

Mantido fora dos módulos de interface para que camadas sem Tkinter, como as
ferramentas de IA, possam consultar se o workspace atual está em modo restrito.
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_restricted = False


def set_restricted(value: bool) -> None:
    """Define se o workspace atual está em modo restrito (não confiável)."""
    global _restricted
    with _lock:
        _restricted = bool(value)


def is_restricted() -> bool:
    """Indica se recursos que executam código do workspace devem ficar bloqueados."""
    with _lock:
        return _restricted
