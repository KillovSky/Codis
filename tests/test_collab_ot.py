"""Propriedades do OT do colab: convergência (TP1) e simulação cliente/servidor."""

from __future__ import annotations

import random
from collections import deque

import pytest

from src.collab.ot import (
    ClientOT,
    ResyncNeeded,
    RoomDoc,
    TextOp,
    apply_op,
    apply_ops,
    diff_change,
    transform,
    validate_change,
    xform,
)


def _rand_op(rng: random.Random, doc: str) -> TextOp | None:
    if rng.random() < 0.55 or not doc:
        pos = rng.randint(0, len(doc))
        return TextOp("insert", pos, text="".join(rng.choice("xyz\n") for _ in range(rng.randint(1, 4))))
    pos = rng.randint(0, len(doc) - 1)
    return TextOp("delete", pos, length=rng.randint(1, min(6, len(doc) - pos)))


def _rand_change(rng: random.Random, doc: str) -> list[TextOp]:
    ops: list[TextOp] = []
    cur = doc
    for _ in range(rng.randint(1, 3)):
        op = _rand_op(rng, cur)
        if op:
            ops.append(op)
            cur = apply_op(cur, op)
    return ops


@pytest.mark.parametrize("seed", range(6))
def test_tp1_single_ops_converge(seed):
    rng = random.Random(seed)
    for _ in range(4000):
        doc = "".join(rng.choice("abcdef\n") for _ in range(rng.randint(0, 14)))
        a, b = _rand_op(rng, doc), _rand_op(rng, doc)
        if not a or not b:
            continue
        for a_after in (True, False):
            a_t = xform(a, b, a_after)
            b_t = xform(b, a, not a_after)
            left = apply_ops(apply_op(doc, a), b_t)
            right = apply_ops(apply_op(doc, b), a_t)
            assert left == right, (doc, a, b, a_after, left, right)


@pytest.mark.parametrize("seed", range(6))
def test_tp1_changes_converge(seed):
    rng = random.Random(100 + seed)
    for _ in range(2500):
        doc = "".join(rng.choice("abcdef\n") for _ in range(rng.randint(0, 20)))
        A, B = _rand_change(rng, doc), _rand_change(rng, doc)
        for a_after in (True, False):
            A_t, B_t = transform(A, B, a_after)
            left = apply_ops(apply_ops(doc, A), B_t)
            right = apply_ops(apply_ops(doc, B), A_t)
            assert left == right, (doc, A, B, a_after)
            assert validate_change(apply_ops(doc, B), A_t)


def test_concurrent_insert_inside_delete_keeps_inserted_text():
    doc = "abcdef"
    delete = TextOp("delete", 1, length=4)
    insert = TextOp("insert", 3, text="XY")
    d_t, i_t = transform([delete], [insert], a_after=True)
    assert apply_ops(apply_ops(doc, [delete]), i_t) == "aXYf"
    assert apply_ops(apply_ops(doc, [insert]), d_t) == "aXYf"


def test_diff_change_roundtrips_and_is_minimal():
    rng = random.Random(5)
    for _ in range(3000):
        old = "".join(rng.choice("abc\n") for _ in range(rng.randint(0, 25)))
        new = "".join(rng.choice("abc\n") for _ in range(rng.randint(0, 25)))
        ops = diff_change(old, new)
        assert apply_ops(old, ops) == new
        assert len(ops) <= 2
        assert validate_change(old, ops)
    assert diff_change("abc", "abc") == []
    assert [o.op for o in diff_change("hello", "heXllo")] == ["insert"]


def test_room_rejects_invalid_and_stale_revisions():
    room = RoomDoc("abc")
    with pytest.raises(ResyncNeeded):
        room.receive(5, [TextOp("insert", 0, text="x")])
    with pytest.raises(ResyncNeeded):
        room.receive(0, [TextOp("delete", 2, length=9)])
    assert room.content == "abc" and room.rev == 0


def test_room_history_trim_forces_resync_for_old_clients():
    room = RoomDoc("")
    room.MAX_HISTORY, room.TRIM_TO = 10, 4
    for i in range(12):
        room.receive(room.rev, [TextOp("insert", 0, text="a")])
    assert room.offset > 0
    with pytest.raises(ResyncNeeded):
        room.receive(0, [TextOp("insert", 0, text="b")])


class _SimClient:
    def __init__(self, name: str, content: str, rev: int) -> None:
        self.name, self.doc = name, content
        self.ot = ClientOT(rev)
        self.inbox: deque = deque()
        self.outbox: deque = deque()

    def edit(self, rng: random.Random) -> None:
        ops = _rand_change(rng, self.doc)
        self.doc = apply_ops(self.doc, ops)
        sent = self.ot.local(ops)
        if sent:
            self.outbox.append(sent)

    def receive(self) -> None:
        kind, rev, ops = self.inbox.popleft()
        if kind == "op":
            self.doc = apply_ops(self.doc, self.ot.remote(ops, rev))
        else:
            nxt = self.ot.ack(rev)
            if nxt:
                self.outbox.append(nxt)


@pytest.mark.parametrize("seed", range(40))
def test_multi_client_simulation_converges(seed):
    rng = random.Random(seed)
    room = RoomDoc("base text\nline two\n")
    clients = [_SimClient(f"c{i}", room.content, room.rev) for i in range(rng.randint(2, 4))]

    def server_step(client: _SimClient) -> None:
        rev, ops = client.outbox.popleft()
        applied = room.receive(rev, ops)
        for other in clients:
            if other is client:
                other.inbox.append(("ack", room.rev, []))
            else:
                other.inbox.append(("op", room.rev, applied))

    for _ in range(rng.randint(60, 160)):
        action = rng.random()
        c = rng.choice(clients)
        if action < 0.45:
            c.edit(rng)
        elif action < 0.75 and c.outbox:
            server_step(c)
        elif c.inbox:
            c.receive()
    while any(c.outbox or c.inbox for c in clients):
        for c in clients:
            if c.outbox:
                server_step(c)
            if c.inbox:
                c.receive()
    assert all(c.doc == room.content for c in clients), [c.doc for c in clients]
    assert all(not c.ot.pending and c.ot.rev == room.rev for c in clients)
