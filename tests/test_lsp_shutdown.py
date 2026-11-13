"""Garante que parar servidores LSP nunca bloqueia nem deixa processos vivos."""

from __future__ import annotations

import os
import sys
import textwrap
import time

import pytest

from src.lsp import client as lsp_client
from src.lsp.client import LSPClient, LSPManager

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="usa grupos de processo POSIX")

HUNG_SERVER = textwrap.dedent(
    """
    import os, subprocess, sys, time
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    target = os.environ.get("CHILD_PID_FILE")
    if target:
        with open(target, "w") as handle:
            handle.write(str(child.pid))
    time.sleep(600)
    """
)


class _FakeTk:
    def after(self, *_args, **_kwargs):
        return None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as handle:
            return handle.read().split()[2] != "Z"
    except OSError:
        return True


@pytest.fixture
def hung_server(monkeypatch):
    monkeypatch.setattr(lsp_client, "_find_server", lambda _lang: [sys.executable, "-c", HUNG_SERVER])


def _start(lang: str = "python") -> LSPClient:
    client = LSPClient(lang, None, _FakeTk())
    assert client.start()
    return client


def test_stop_returns_quickly_even_if_server_ignores_shutdown(hung_server):
    client = _start()
    began = time.monotonic()
    client.stop(grace=0.5)
    assert time.monotonic() - began < 4
    assert client._proc.poll() is not None


@posix_only
def test_stop_kills_child_processes_of_the_server(hung_server, monkeypatch, tmp_path):
    pid_file = tmp_path / "child.pid"
    monkeypatch.setenv("CHILD_PID_FILE", str(pid_file))
    client = _start()
    for _ in range(100):
        if pid_file.exists() and pid_file.read_text().strip():
            break
        time.sleep(0.05)
    child_pid = int(pid_file.read_text())
    assert _alive(child_pid)
    client.stop(grace=0.3)
    for _ in range(100):
        if not _alive(child_pid):
            break
        time.sleep(0.05)
    assert not _alive(child_pid)


def test_send_never_blocks_when_server_stops_reading(hung_server):
    client = _start()
    try:
        began = time.monotonic()
        for _ in range(200):
            client._send({"jsonrpc": "2.0", "method": "x", "params": {"blob": "a" * 50_000}})
        assert time.monotonic() - began < 2
    finally:
        client.stop(grace=0.2)


def test_stop_all_stops_many_hung_servers_within_deadline(hung_server):
    manager = LSPManager(_FakeTk())
    clients = []
    for lang in ("python", "javascript", "go", "rust"):
        clients.append(_start(lang))
        manager._clients[lang] = clients[-1]
    began = time.monotonic()
    manager.stop_all(timeout=3.0)
    assert time.monotonic() - began < 5
    assert all(c._proc.poll() is not None for c in clients)
    assert not manager._clients
