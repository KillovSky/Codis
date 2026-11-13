"""Registro de observadores de salvamento de arquivos.

Permite que painéis (ex.: navegador remoto) reajam a qualquer gravação feita
pelo editor - salvar, salvar tudo ou autosave - sem acoplar o editor a eles.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger("Codis.SaveHooks")

SaveObserver = Callable[[Path], None]

_lock = threading.Lock()
_observers: list[SaveObserver] = []


def register(observer: SaveObserver) -> Callable[[], None]:
    """Registra ``observer`` e devolve uma função que o remove."""
    with _lock:
        _observers.append(observer)

    def _unregister() -> None:
        with _lock:
            if observer in _observers:
                _observers.remove(observer)

    return _unregister


def notify_saved(path: Path) -> None:
    """Avisa todos os observadores que ``path`` foi gravado em disco."""
    with _lock:
        snapshot = list(_observers)
    for observer in snapshot:
        try:
            observer(path)
        except Exception:  # noqa: BLE001
            logger.exception("Observador de save falhou: %r", observer)
