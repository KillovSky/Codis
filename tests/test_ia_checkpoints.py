"""Testes dos checkpoints de IA (src/ia/checkpoints.py)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.ia.checkpoints import CheckpointStore


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    w = tmp_path / "proj"
    w.mkdir()
    return w


def _edit(cps: CheckpointStore, p: Path, new: str) -> None:
    cps.record_write(p)
    tmp = p.with_name(p.name + ".t")
    tmp.write_text(new)
    os.replace(tmp, p)
    cps.record_after(p)


def test_undo_edit_restores_content_and_uses_hardlink(ws):
    f = ws / "a.py"
    f.write_text("original")
    ino = os.lstat(f).st_ino
    cps = CheckpointStore(ws, "s1")
    cps.begin("edita a")
    _edit(cps, f, "alterado")
    snap = Path(cps._current.changes[0].snapshot)
    assert os.lstat(snap).st_ino == ino
    assert snap.read_text() == "original"
    cps.commit()
    rep = cps.undo()
    assert rep.ok and f.read_text() == "original"


def test_stale_snapshot_file_does_not_break_recording(ws):
    f = ws / "a.py"
    f.write_text("v0")
    cps = CheckpointStore(ws, "s1")
    cps.begin("ia")
    stale = cps._snap_path(cps._current, 0)
    stale.parent.mkdir(parents=True)
    stale.write_text("lixo de turno interrompido")
    _edit(cps, f, "v1")
    cps.commit()
    assert cps.undo().ok and f.read_text() == "v0"


def test_snapshot_is_inside_project_trash_not_home(ws):
    f = ws / "a.py"
    f.write_text("x")
    cps = CheckpointStore(ws, "s1")
    cps.begin("t")
    _edit(cps, f, "y")
    cps.commit()
    assert (ws / ".codis_trash" / ".ai" / "s1").is_dir()
    assert "*" in (ws / ".codis_trash" / ".gitignore").read_text()


def test_undo_create_removes_file(ws):
    f = ws / "novo.txt"
    cps = CheckpointStore(ws, "s")
    cps.begin("cria")
    cps.record_write(f)
    f.write_text("oi")
    cps.record_after(f)
    cps.commit()
    rep = cps.undo()
    assert rep.removed == [str(f)] and not f.exists()


def test_undo_trashed_file_and_directory(ws):
    f = ws / "del.txt"
    f.write_text("d")
    d = ws / "pasta"
    (d / "sub").mkdir(parents=True)
    (d / "sub" / "x").write_text("1")
    cps = CheckpointStore(ws, "s")
    cps.begin("apaga")
    cps.trash(f)
    cps.trash(d)
    cps.commit()
    assert not f.exists() and not d.exists()
    rep = cps.undo()
    assert rep.ok and f.read_text() == "d" and (d / "sub" / "x").read_text() == "1"


def test_undo_move(ws):
    a, b = ws / "a.txt", ws / "b.txt"
    a.write_text("m")
    cps = CheckpointStore(ws, "s")
    cps.begin("move")
    os.rename(a, b)
    cps.record_move(a, b)
    cps.commit()
    assert cps.undo().ok and a.read_text() == "m" and not b.exists()


def test_user_edit_after_ai_is_a_conflict_not_overwritten(ws):
    f = ws / "a.py"
    f.write_text("v0")
    cps = CheckpointStore(ws, "s")
    cps.begin("ia")
    _edit(cps, f, "v-ia")
    cps.commit()
    f.write_text("v-usuario")
    rep = cps.undo()
    assert rep.conflicts == [str(f)] and not rep.ok
    assert f.read_text() == "v-usuario"
    rep = cps.undo(force=True)
    assert rep.ok and f.read_text() == "v0"


def test_multiple_edits_same_file_one_snapshot_original_wins(ws):
    f = ws / "a.py"
    f.write_text("v0")
    cps = CheckpointStore(ws, "s")
    cps.begin("ia")
    _edit(cps, f, "v1")
    _edit(cps, f, "v2")
    cps.commit()
    assert len(cps.list()[0].changes) == 1
    assert cps.undo().ok and f.read_text() == "v0"


def test_undo_order_latest_first_and_marks_undone(ws):
    f = ws / "a.py"
    f.write_text("v0")
    cps = CheckpointStore(ws, "s")
    cps.begin("um")
    _edit(cps, f, "v1")
    cps.commit()
    cps.begin("dois")
    _edit(cps, f, "v2")
    cps.commit()
    assert cps.undo().ok and f.read_text() == "v1"
    assert cps.undo().ok and f.read_text() == "v0"
    assert cps.undo().summary() == "nada a desfazer"


def test_empty_checkpoint_is_discarded(ws):
    cps = CheckpointStore(ws, "s")
    cps.begin("nada")
    assert cps.commit() is None and cps.list() == []


def test_persists_across_instances(ws):
    f = ws / "a.py"
    f.write_text("v0")
    c1 = CheckpointStore(ws, "s")
    c1.begin("ia")
    _edit(c1, f, "v1")
    c1.commit()
    c2 = CheckpointStore(ws, "s")
    assert [c.label for c in c2.list()] == ["ia"]
    assert c2.undo().ok and f.read_text() == "v0"


def test_record_without_begin_is_noop(ws):
    f = ws / "a.py"
    f.write_text("x")
    cps = CheckpointStore(ws, "s")
    cps.record_write(f)
    assert cps.commit() is None


def test_copy_fallback_when_hardlink_fails(ws, monkeypatch):
    f = ws / "a.py"
    f.write_text("v0")
    monkeypatch.setattr(os, "link", lambda *a, **k: (_ for _ in ()).throw(OSError("xdev")))
    cps = CheckpointStore(ws, "s")
    cps.begin("ia")
    _edit(cps, f, "v1")
    cps.commit()
    assert cps.undo().ok and f.read_text() == "v0"


def test_prune_keeps_only_recent(ws):
    from src.ia import checkpoints as C

    f = ws / "a.py"
    f.write_text("0")
    cps = CheckpointStore(ws, "s")
    for i in range(C.KEEP_CHECKPOINTS + 5):
        cps.begin(str(i))
        _edit(cps, f, str(i + 1))
        cps.commit()
    assert len(cps.list()) == C.KEEP_CHECKPOINTS
