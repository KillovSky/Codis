"""Vigia de travamentos: detecta bloqueio do laço principal e mostra a pilha."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from src.stall_trace import StallWatchdog, enabled, maybe_start


class FakeRoot:
    def after(self, _ms, _fn):
        return "x"


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("CODIS_STALL_TRACE", raising=False)
    assert not enabled()
    assert maybe_start(FakeRoot()) is None


def test_env_enables(monkeypatch):
    monkeypatch.setenv("CODIS_STALL_TRACE", "1")
    assert enabled()


def test_reports_stack_when_main_thread_blocks():
    dog = StallWatchdog(FakeRoot(), threshold_ms=300)
    dog._main_id = threading.get_ident()
    dog._last = time.monotonic()
    threading.Thread(target=dog._watch, daemon=True).start()

    def bloqueio_lento():
        time.sleep(0.9)

    bloqueio_lento()
    dog.stop()
    assert dog.stalls, "deveria ter detectado o bloqueio"
    gap, stack = dog.stalls[0]
    assert gap >= 0.3
    assert "bloqueio_lento" in stack, "a pilha aponta o que bloqueou"


def test_no_false_positive_with_heartbeat():
    dog = StallWatchdog(SimpleNamespace(after=lambda *_: None), threshold_ms=300)
    dog._main_id = threading.get_ident()
    threading.Thread(target=dog._watch, daemon=True).start()
    end = time.monotonic() + 0.8
    while time.monotonic() < end:
        dog._last = time.monotonic()
        time.sleep(0.05)
    dog.stop()
    assert dog.stalls == []
