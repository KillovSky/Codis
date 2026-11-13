"""Regressões: config por requisição e cancelamento isolado (provedores de IA)."""

from __future__ import annotations

import pytest

from src.ia import providers as P


class Deferred:
    """Thread falsa: guarda o alvo e só executa quando mandamos (determinismo)."""

    pending: list = []

    def __init__(self, target=None, **_kw):
        self._t = target

    def start(self):
        Deferred.pending.append(self._t)

    @classmethod
    def run_all(cls):
        while cls.pending:
            cls.pending.pop(0)()


@pytest.fixture(autouse=True)
def _deferred(monkeypatch):
    Deferred.pending = []
    monkeypatch.setattr(P.threading, "Thread", Deferred)


def _capture(monkeypatch):
    seen = []

    def fake_post_stream(url, payload, headers, on_line, cancelled, timeout=120):
        seen.append({"payload": payload, "cancelled": cancelled, "headers": headers})

    monkeypatch.setattr(P, "_post_stream", fake_post_stream)
    return seen


TOOLS = [{"type": "function", "function": {"name": "t", "description": "d", "parameters": {"type": "object", "properties": {}}}}]


@pytest.mark.parametrize("cls,keycfg", [
    (P.AnthropicProvider, {"chave_api": "k", "modelo": "m"}),
    (P.OpenAIProvider, {"chave_api": "k", "modelo": "m"}),
    (P.OpenAICompatibleProvider, {"url": "http://x/v1/chat/completions", "modelo": "m"}),
])
def test_options_survive_configure_after_send(monkeypatch, cls, keycfg):
    """O bug: configure(original) logo após send() apagava system/ferramentas."""
    seen = _capture(monkeypatch)
    p = cls()
    p.configure(dict(keycfg))
    p.send([{"role": "user", "content": "oi"}], lambda c: None, lambda t: None, lambda e: None,
           options={"sistema": "SYS-DO-PROJETO", "_ferramentas": TOOLS, "_on_tool_call": lambda n, a: "ok"})
    p.configure(dict(keycfg))
    Deferred.run_all()
    payload = seen[0]["payload"]
    blob = str(payload)
    assert "SYS-DO-PROJETO" in blob
    assert "tools" in payload


def test_cfg_snapshot_isolated_from_later_configure(monkeypatch):
    seen = _capture(monkeypatch)
    p = P.AnthropicProvider()
    p.configure({"chave_api": "K1", "modelo": "m1"})
    p.send([{"role": "user", "content": "x"}], lambda c: None, lambda t: None, lambda e: None)
    p.configure({"chave_api": "K2", "modelo": "m2"})
    Deferred.run_all()
    assert seen[0]["headers"]["x-api-key"] == "K1"
    assert seen[0]["payload"]["model"] == "m1"


def test_cancel_then_resend_does_not_revive_old_request(monkeypatch):
    seen = _capture(monkeypatch)
    p = P.AnthropicProvider()
    p.configure({"chave_api": "k"})
    msg = [{"role": "user", "content": "x"}]
    p.send(msg, lambda c: None, lambda t: None, lambda e: None)
    p.cancel()
    p.send(msg, lambda c: None, lambda t: None, lambda e: None)
    Deferred.run_all()
    assert len(seen) == 1
    assert not seen[0]["cancelled"].is_set()


def test_base_reset_cancel_still_clears_for_callers():
    p = P.AnthropicProvider()
    p.cancel()
    assert p._cancelled.is_set()
    p._reset_cancel()
    assert not p._cancelled.is_set()
