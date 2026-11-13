"""Medição opcional do startup: ``CODIS_STARTUP_PROFILE=1 python run.py``.

Imprime (stderr) o tempo acumulado em cada fase, incluindo o boot do
interpretador e dos imports. Sem a variável de ambiente é um no-op.
"""

from __future__ import annotations

import os
import sys
import time

_ON = os.environ.get("CODIS_STARTUP_PROFILE", "") not in ("", "0")
_T0 = time.perf_counter()
_LAST = _T0
_BOOT = 0.0

if _ON:
    try:
        import psutil

        _BOOT = max(0.0, time.time() - psutil.Process().create_time())
    except Exception:  # noqa: BLE001
        _BOOT = 0.0


def mark(name: str) -> None:
    """Registra o fim de uma fase (``+delta`` desde a marca anterior)."""
    global _LAST
    if not _ON:
        return
    now = time.perf_counter()
    total = now - _T0 + _BOOT
    sys.stderr.write(f"[startup] {total:6.2f}s  (+{now - _LAST:5.2f}s)  {name}\n")
    _LAST = now
