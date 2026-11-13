"""Diagnóstico opcional de travamentos da interface (``CODIS_STALL_TRACE=1``).

Se o Codis ficar lento/travado (ex.: demorar muito para iniciar), execute com::

    CODIS_STALL_TRACE=1 python run.py        # Windows (PowerShell): $env:CODIS_STALL_TRACE=1

Uma thread vigia o laço principal do Tk: quando ele fica parado por mais de
``CODIS_STALL_MS`` (padrão 1500 ms), registra no log ``Codis.Stall`` a pilha
da thread principal naquele instante - ou seja, **o que** está bloqueando.
Também registra o tempo de cada fase de inicialização. Desligado por padrão
(sem custo).
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
import traceback
from typing import Any

logger = logging.getLogger("Codis.Stall")


def enabled() -> bool:
    """True quando ``CODIS_STALL_TRACE`` está ligado no ambiente."""
    return os.environ.get("CODIS_STALL_TRACE", "").strip().lower() in ("1", "true", "yes", "on")


class StallWatchdog:
    """Vigia o laço do Tk e loga a pilha principal durante bloqueios longos."""

    def __init__(self, root: Any, threshold_ms: int = 1500, beat_ms: int = 200) -> None:
        self._root = root
        self._threshold = threshold_ms / 1000.0
        self._beat_ms = beat_ms
        self._last = time.monotonic()
        self._main_id = threading.get_ident()
        self._stop = False
        self._reported_at = 0.0
        self.stalls: list[tuple[float, str]] = []
        self._t0 = time.monotonic()

    def start(self) -> None:
        """Inicia o heartbeat (no Tk) e a thread vigia."""
        self._beat()
        threading.Thread(target=self._watch, name="CodisStallWatchdog", daemon=True).start()
        logger.warning("CODIS_STALL_TRACE ativo (limite %.0f ms)", self._threshold * 1000)

    def stop(self) -> None:
        """Encerra o vigia."""
        self._stop = True

    def mark(self, phase: str) -> None:
        """Registra o tempo decorrido desde o início até ``phase``."""
        logger.warning("[startup] %-28s +%.2fs", phase, time.monotonic() - self._t0)

    def _beat(self) -> None:
        if self._stop:
            return
        self._last = time.monotonic()
        try:
            self._root.after(self._beat_ms, self._beat)
        except Exception:
            self._stop = True

    def _watch(self) -> None:
        while not self._stop:
            time.sleep(0.25)
            gap = time.monotonic() - self._last
            if gap < self._threshold or time.monotonic() - self._reported_at < self._threshold:
                continue
            self._reported_at = time.monotonic()
            frame = sys._current_frames().get(self._main_id)
            stack = "".join(traceback.format_stack(frame)[-8:]) if frame else "(sem pilha)"
            self.stalls.append((gap, stack))
            logger.warning("Interface parada há %.1fs. Pilha da thread principal:\n%s", gap, stack)


def maybe_start(root: Any) -> StallWatchdog | None:
    """Inicia o vigia se ``CODIS_STALL_TRACE`` estiver ligado."""
    if not enabled():
        return None
    try:
        ms = int(os.environ.get("CODIS_STALL_MS", "1500"))
    except ValueError:
        ms = 1500
    dog = StallWatchdog(root, ms)
    dog.start()
    return dog
