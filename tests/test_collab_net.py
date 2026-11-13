"""Colab de ponta a ponta com sockets reais (sem Tk)."""

from __future__ import annotations

import random
import time

import pytest

from src.collab.net import HAS_WS, CollabClient, CollabServer
from src.collab.ot import TextOp, apply_ops
from src.collab.session import CollabSession

pytestmark = pytest.mark.skipif(not HAS_WS, reason="websockets não instalado")


class StrEditor:
    """Editor falso: um texto em memória."""

    def __init__(self, text: str = "") -> None:
        self.text = text
        self.full_replaces = 0

    def get_text(self) -> str:
        return self.text

    def apply_ops(self, ops: list[TextOp]) -> None:
        self.text = apply_ops(self.text, ops)

    def set_text(self, text: str) -> None:
        self.text = text
        self.full_replaces += 1

    def alive(self) -> bool:
        return True

    def type_at(self, pos: int, s: str) -> None:
        self.text = self.text[:pos] + s + self.text[pos:]

    def erase(self, pos: int, n: int) -> None:
        self.text = self.text[:pos] + self.text[pos + n:]


class Peer:
    def __init__(self, server: CollabServer, room: str, user: str, password: str = "") -> None:
        self.editor = StrEditor()
        self.events: list[dict] = []
        self.client = CollabClient("127.0.0.1", server.port, room, user, password)
        self.session = CollabSession(self.client, self.editor, self.events.append)
        self.client.connect()

    def tick(self) -> None:
        self.session.tick()

    def kinds(self) -> list[str]:
        return [e["type"] for e in self.events]


def pump(peers: list[Peer], seconds: float = 0.6, until=None) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        for p in peers:
            p.tick()
        if until and until():
            return True
        time.sleep(0.01)
    return until() if until else True


def settle(peers: list[Peer], server: CollabServer, room: str, timeout: float = 8.0) -> bool:
    """Roda ticks até todos ficarem ociosos e iguais ao servidor."""
    def done() -> bool:
        target = server._rooms[room].doc.content
        return all(p.session.idle and p.editor.text == target for p in peers)
    return pump(peers, timeout, until=done)


@pytest.fixture()
def server():
    srv = CollabServer("127.0.0.1", 0)
    ok, msg = srv.start()
    assert ok, msg
    yield srv
    srv.stop()


def _join(server, room, user, password="") -> Peer:
    p = Peer(server, room, user, password)
    assert pump([p], 4, until=lambda: p.session.synced or "error" in p.kinds())
    return p


def test_sync_delivers_initial_document_and_peers(server):
    server.create_room("R1", initial_content="olá mundo")
    a = _join(server, "R1", "ana")
    b = _join(server, "R1", "beto")
    assert a.editor.text == b.editor.text == "olá mundo"
    pump([a, b], 0.5)
    assert "peers" in a.kinds() and "peers" in b.kinds()


def test_rejects_unknown_room_wrong_password_and_duplicate_user(server):
    server.create_room("R1", password="segredo")
    x = Peer(server, "NOPE", "ana")
    assert pump([x], 3, until=lambda: "error" in x.kinds())
    assert "não encontrada" in [e for e in x.events if e["type"] == "error"][0]["msg"]

    y = Peer(server, "R1", "ana", password="errada")
    assert pump([y], 3, until=lambda: "error" in y.kinds())
    assert "Senha incorreta" in [e for e in y.events if e["type"] == "error"][0]["msg"]

    ok = _join(server, "R1", "ana", "segredo")
    assert ok.session.synced
    dup = Peer(server, "R1", "ana", "segredo")
    assert pump([dup, ok], 3, until=lambda: "error" in dup.kinds())
    assert "em uso" in [e for e in dup.events if e["type"] == "error"][0]["msg"]


def test_solo_user_typing_in_the_middle_stays_consistent(server):
    """Regressão: antes, o servidor ficava 'helloXY' enquanto o editor tinha 'heXlYlo'."""
    server.create_room("R3")
    c = _join(server, "R3", "carla")
    c.editor.type_at(0, "hello")
    pump([c], 0.3)
    c.editor.type_at(2, "X")
    pump([c], 0.3)
    c.editor.type_at(4, "Y")
    assert settle([c], server, "R3")
    assert c.editor.text == "heXlYlo" == server._rooms["R3"].doc.content


def test_two_users_concurrent_edits_converge(server):
    server.create_room("R2", initial_content="linha 1\nlinha 2\n")
    a, b = _join(server, "R2", "ana"), _join(server, "R2", "beto")
    a.editor.type_at(0, "A>")
    b.editor.type_at(len(b.editor.text), "<B")
    assert settle([a, b], server, "R2")
    assert a.editor.text == b.editor.text == server._rooms["R2"].doc.content
    assert a.editor.text.startswith("A>") and a.editor.text.endswith("<B")


def test_insert_inside_concurrent_delete_is_preserved(server):
    server.create_room("R4", initial_content="abcdef")
    a, b = _join(server, "R4", "ana"), _join(server, "R4", "beto")
    a.editor.erase(1, 4)
    b.editor.type_at(3, "XY")
    assert settle([a, b], server, "R4")
    assert a.editor.text == b.editor.text == "aXYf"


@pytest.mark.parametrize("seed", list(range(1, 4)))
def test_three_users_random_concurrent_edits_converge(server, seed):
    rng = random.Random(seed)
    server.create_room("RX", initial_content="".join(rng.choice("abc\n") for _ in range(60)))
    peers = [_join(server, "RX", n) for n in ("p1", "p2", "p3")]
    for _ in range(80):
        p = rng.choice(peers)
        text = p.editor.text
        if rng.random() < 0.6 or not text:
            p.editor.type_at(rng.randint(0, len(text)), "".join(rng.choice("xyz") for _ in range(rng.randint(1, 3))))
        else:
            pos = rng.randint(0, len(text) - 1)
            p.editor.erase(pos, rng.randint(1, min(5, len(text) - pos)))
        pump(peers, rng.choice([0.0, 0.0, 0.02, 0.05]))
    assert settle(peers, server, "RX", timeout=12)
    assert len({p.editor.text for p in peers}) == 1
    assert all(p.session.resyncs == 0 for p in peers)


def test_late_joiner_gets_current_state_and_can_edit(server):
    server.create_room("R5", initial_content="x")
    a = _join(server, "R5", "ana")
    a.editor.type_at(1, "yz")
    assert settle([a], server, "R5")
    b = _join(server, "R5", "beto")
    assert b.editor.text == "xyz"
    b.editor.type_at(0, ">")
    assert settle([a, b], server, "R5")
    assert a.editor.text == ">xyz"


def test_large_document_over_one_mib_syncs(server):
    big = ("linha de código bem comprida " * 40 + "\n") * 2500
    assert len(big) > 2 * 1024 * 1024
    server.create_room("BIG", initial_content=big)
    p = _join(server, "BIG", "ana")
    assert p.editor.text == big


def test_chat_and_cursor_events_are_broadcast(server):
    server.create_room("R6")
    a, b = _join(server, "R6", "ana"), _join(server, "R6", "beto")
    a.client.send({"type": "chat", "msg": "oi"})
    a.client.send({"type": "cursor", "line": 3, "col": 4})
    pump([a, b], 1.0, until=lambda: "chat" in b.kinds() and "cursor" in b.kinds())
    chat = [e for e in b.events if e["type"] == "chat"][0]
    assert chat["user"] == "ana" and chat["msg"] == "oi" and chat["ts"]
    cur = [e for e in b.events if e["type"] == "cursor"][0]
    assert (cur["line"], cur["col"], cur["user"]) == (3, 4, "ana")
    assert "chat" in a.kinds()


def test_invalid_change_triggers_resync_instead_of_corruption(server):
    server.create_room("R7", initial_content="abc")
    a = _join(server, "R7", "ana")
    a.client.send({"type": "op", "rev": 0, "ops": [TextOp("delete", 1, length=99).to_dict()]})
    pump([a], 1.0, until=lambda: a.session.resyncs >= 1)
    assert a.session.resyncs == 1
    assert server._rooms["R7"].doc.content == "abc" and a.editor.text == "abc"


def test_disconnect_emits_closed_event_and_server_drops_peer(server):
    server.create_room("R8")
    a, b = _join(server, "R8", "ana"), _join(server, "R8", "beto")
    b.client.disconnect()
    pump([a, b], 1.0, until=lambda: any(e["type"] == "closed" for e in b.events))
    assert any(e["type"] == "closed" for e in b.events)
    pump([a], 1.0)
    last_peers = [e for e in a.events if e["type"] == "peers"][-1]
    assert [u["user"] for u in last_peers["users"]] == ["ana"]


def test_start_fails_cleanly_when_port_is_busy(server):
    other = CollabServer("127.0.0.1", server.port)
    ok, msg = other.start()
    assert ok is False and msg


def test_max_peers_is_enforced():
    srv = CollabServer("127.0.0.1", 0, max_peers=2)
    ok, msg = srv.start()
    assert ok, msg
    try:
        srv.create_room("RM")
        a, b = _join(srv, "RM", "a"), _join(srv, "RM", "b")
        c = Peer(srv, "RM", "c")
        assert pump([a, b, c], 3, until=lambda: "error" in c.kinds())
        assert "cheia" in [e for e in c.events if e["type"] == "error"][0]["msg"]
    finally:
        srv.stop()
