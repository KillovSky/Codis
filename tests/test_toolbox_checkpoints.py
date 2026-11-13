"""Integração ToolExecutor + checkpoints + proteção de alterações não salvas."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.ia.toolbox import ToolExecutor


class FakeText:
    def __init__(self, content: str):
        self.content = content

    def get(self, *_a):
        return self.content


def fake_editor(path: Path, content: str, modified: bool):
    return SimpleNamespace(path=path, modified=modified, text=FakeText(content), lang="python")


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    w = tmp_path / "proj"
    w.mkdir()
    return w


def make(ws: Path, editors=()):
    ex = ToolExecutor(get_editor=lambda: (editors[0] if editors else None),
                      get_workspace=lambda: str(ws), get_open_editors=lambda: list(editors))
    ex.set_session("sess1")
    return ex


def test_apply_edit_then_undo_turn(ws):
    f = ws / "a.py"
    f.write_text("x = 1\n")
    ex = make(ws)
    ex.begin_turn("troca x")
    out = ex.execute("apply_edit", {"path": "a.py", "edits": [{"old_text": "x = 1", "new_text": "x = 2"}]})
    assert f.read_text() == "x = 2\n", out
    assert ex.end_turn() is not None
    assert not list(ws.glob("*.bak")), "não deve espalhar .bak quando há checkpoint"
    rep = ex.undo_turn()
    assert rep.ok and f.read_text() == "x = 1\n"


def test_write_new_file_then_undo_removes_it(ws):
    ex = make(ws)
    ex.begin_turn("novo")
    ex.execute("write_file", {"path": "sub/novo.py", "content": "print(1)\n"})
    ex.end_turn()
    assert (ws / "sub" / "novo.py").exists()
    ex.undo_turn()
    assert not (ws / "sub" / "novo.py").exists()


def test_delete_directory_is_reversible_not_rmtree(ws):
    d = ws / "pkg"
    d.mkdir()
    (d / "m.py").write_text("keep")
    ex = make(ws)
    ex.begin_turn("apaga pkg")
    out = ex.execute("delete_directory", {"path": "pkg", "recursive": True})
    ex.end_turn()
    assert not d.exists() and "lixeira" in out
    assert any((ws / ".codis_trash").iterdir())
    assert ex.undo_turn().ok
    assert (d / "m.py").read_text() == "keep"


def test_delete_file_goes_to_trash_without_deleted_bak(ws):
    f = ws / "x.txt"
    f.write_text("1")
    ex = make(ws)
    ex.execute("delete_file", {"path": "x.txt"})
    assert not f.exists() and not list(ws.glob("*.deleted.bak"))


def test_delete_workspace_root_refused(ws):
    ex = make(ws)
    out = ex.execute("delete_directory", {"path": ".", "recursive": True})
    assert ws.exists() and (ws / ".codis_trash").exists() is False
    assert out and "[Erro" not in out.split("\n")[0] or ws.exists()
    assert not out.startswith("✅")


def test_move_and_undo(ws):
    (ws / "a.txt").write_text("m")
    ex = make(ws)
    ex.begin_turn("mv")
    ex.execute("move_file", {"src": "a.txt", "dst": "b.txt"})
    ex.end_turn()
    assert (ws / "b.txt").exists()
    ex.undo_turn()
    assert (ws / "a.txt").read_text() == "m" and not (ws / "b.txt").exists()


def test_move_refuses_to_overwrite_existing_destination(ws):
    (ws / "a.txt").write_text("a")
    (ws / "b.txt").write_text("b")
    ex = make(ws)
    out = ex.execute("move_file", {"src": "a.txt", "dst": "b.txt"})
    assert (ws / "b.txt").read_text() == "b" and (ws / "a.txt").exists() and "existe" in out


def test_write_blocked_when_editor_has_unsaved_changes(ws):
    f = ws / "a.py"
    f.write_text("disco\n")
    ed = fake_editor(f, "buffer do usuario\n", modified=True)
    ex = make(ws, [ed])
    out = ex.execute("write_file", {"path": "a.py", "content": "ia\n"})
    assert "Bloqueado" in out and f.read_text() == "disco\n"
    out = ex.execute("delete_file", {"path": "a.py"})
    assert "Bloqueado" in out and f.exists()


def test_write_allowed_when_editor_is_saved(ws):
    f = ws / "a.py"
    f.write_text("disco\n")
    ex = make(ws, [fake_editor(f, "disco\n", modified=False)])
    ex.execute("write_file", {"path": "a.py", "content": "ia\n"})
    assert f.read_text() == "ia\n"


def test_edit_reads_unsaved_buffer_but_write_is_blocked(ws):
    """A IA vê o buffer atual (não o disco velho), mas não o sobrescreve."""
    f = ws / "a.py"
    f.write_text("velho\n")
    ex = make(ws, [fake_editor(f, "novo no buffer\n", modified=True)])
    assert ex._read(f) == "novo no buffer\n"
    out = ex.execute("apply_edit", {"path": "a.py", "edits": [{"old_text": "novo", "new_text": "x"}]})
    assert "Bloqueado" in out and f.read_text() == "velho\n"


def test_patch_files_dry_run_does_not_create_files(ws):
    ex = make(ws)
    out = ex.execute("patch_files", {"dry_run": True, "patches": [
        {"path": "nao_deve_existir.py", "create_if_missing": True,
         "edits": [{"old_text": "", "new_text": "x"}]}]})
    assert not (ws / "nao_deve_existir.py").exists(), out


def test_patch_files_create_is_undoable_as_creation(ws):
    ex = make(ws)
    ex.begin_turn("cria via patch")
    ex.execute("patch_files", {"patches": [
        {"path": "n.py", "create_if_missing": True, "edits": [{"old_text": "", "new_text": "x", "regex": True}]}]})
    ex.end_turn()
    ex.undo_turn()
    assert not (ws / "n.py").exists()


def test_without_session_falls_back_to_bak(ws):
    f = ws / "a.py"
    f.write_text("v0\n")
    ex = ToolExecutor(get_editor=lambda: None, get_workspace=lambda: str(ws))
    ex.execute("write_file", {"path": "a.py", "content": "v1\n"})
    assert (ws / "a.py.bak").read_text() == "v0\n"
