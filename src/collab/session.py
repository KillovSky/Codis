"""Codis - Sessão de colaboração (lógica de sincronização, sem Tk).

Liga um :class:`~src.collab.net.CollabClient` a um editor abstrato.
Tudo roda na thread de interface, em :meth:`CollabSession.tick`:

1. **Captura local primeiro:** compara o texto do editor com a última versão
   conhecida e transforma a diferença em uma mudança OT. Assim nenhuma edição
   digitada entre dois ticks é perdida quando chega uma operação remota.
2. **Depois** processa os eventos do servidor em ordem: ``ack`` libera a
   próxima mudança, ``op`` é transformada contra o que está pendente e aplicada
   de forma incremental (cursor, seleção e rolagem do usuário são preservados),
   ``sync`` substitui o documento por completo.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import Protocol

from src.collab.net import CollabClient
from src.collab.ot import (
    ClientOT,
    TextOp,
    apply_ops,
    diff_change,
    ops_from_dicts,
)


class EditorAdapter(Protocol):
    """Contrato mínimo do editor sincronizado."""

    def get_text(self) -> str:
        """Texto completo atual."""

    def apply_ops(self, ops: list[TextOp]) -> None:
        """Aplica uma mudança de forma incremental."""

    def set_text(self, text: str) -> None:
        """Substitui todo o texto."""

    def alive(self) -> bool:
        """Se o editor ainda existe (aba aberta)."""


class CollabSession:
    """Sincroniza um editor com uma sala usando OT."""

    def __init__(
        self,
        client: CollabClient,
        editor: EditorAdapter,
        on_event: Callable[[dict], None],
    ) -> None:
        self._client = client
        self._editor = editor
        self._on_event = on_event
        self._ot = ClientOT(0)
        self._baseline = ""
        self._synced = False
        self.resyncs = 0

    @property
    def synced(self) -> bool:
        """Se o documento inicial já foi recebido."""
        return self._synced

    @property
    def idle(self) -> bool:
        """Sem edições locais pendentes nem eventos na fila."""
        return not self._ot.pending and self._client.events.empty()

    @property
    def revision(self) -> int:
        """Última revisão do servidor integrada."""
        return self._ot.rev

    def tick(self) -> None:
        """Um ciclo: captura edições locais e processa eventos do servidor."""
        if self._synced:
            self._capture_local()
        for event in self._client.drain():
            self._handle(event)

    def _capture_local(self) -> None:
        if not self._editor.alive():
            return
        current = self._editor.get_text()
        if current == self._baseline:
            return
        ops = diff_change(self._baseline, current)
        self._baseline = current
        to_send = self._ot.local(ops)
        if to_send:
            self._send(*to_send)

    def _send(self, rev: int, ops: list[TextOp]) -> None:
        self._client.send({"type": "op", "rev": rev, "ops": [o.to_dict() for o in ops]})

    def _handle(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "sync":
            self._on_sync(event)
        elif kind == "op" and self._synced:
            self._on_remote(event)
        elif kind == "ack" and self._synced:
            nxt = self._ot.ack(int(event.get("rev", self._ot.rev)))
            if nxt:
                self._send(*nxt)
        else:
            self._on_event(event)

    def _on_sync(self, event: dict) -> None:
        content = str(event.get("content", ""))
        had_pending = self._ot.pending
        self._ot.reset(int(event.get("rev", 0)))
        if self._editor.alive() and self._editor.get_text() != content:
            self._editor.set_text(content)
        self._baseline = content
        if self._synced:
            self.resyncs += 1
        self._synced = True
        self._on_event({"type": "synced", "rev": self._ot.rev, "resync": had_pending})

    def _on_remote(self, event: dict) -> None:
        try:
            ops = ops_from_dicts(event.get("ops", []))
        except ValueError:
            return
        to_apply = self._ot.remote(ops, int(event.get("rev", self._ot.rev)))
        if self._editor.alive() and to_apply:
            self._editor.apply_ops(to_apply)
        self._baseline = apply_ops(self._baseline, to_apply)
        self._on_event({"type": "remote_op", "user": str(event.get("user", "?")),
                        "count": len(to_apply)})

    def close(self) -> None:
        """Encerra a conexão."""
        with contextlib.suppress(Exception):
            self._client.disconnect()
