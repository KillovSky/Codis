"""Testes do fluxo de saída: exit_save_mode, falhas de salvamento e sessão."""

from __future__ import annotations

from pathlib import Path

import pytest

from src import _app_tools as at


class FakeEditor:
    def __init__(self, name: str | None, modified: bool = True, result=True, boom: bool = False):
        self.path = Path(name) if name else None
        self.modified = modified
        self.saved = 0
        self._result, self._boom = result, boom

    def save(self):
        self.saved += 1
        if self._boom:
            raise OSError("disco cheio")
        return self._result


class App(at._AppToolsMixin):
    def __init__(self, cfg, editors):
        self._cfg = cfg
        self._editors = editors
        self._root = None
        self.session_saved = 0

    def _save_session(self):
        self.session_saved += 1


@pytest.fixture
def ask(monkeypatch):
    calls = {"yncancel": [], "yn": []}
    answers = {"yncancel": True, "yn": True}
    monkeypatch.setattr(at.messagebox, "askyesnocancel",
                        lambda *a, **k: calls["yncancel"].append(a) or answers["yncancel"], raising=False)
    monkeypatch.setattr(at.messagebox, "askyesno",
                        lambda *a, **k: calls["yn"].append(a) or answers["yn"], raising=False)
    return calls, answers


def test_defaults_and_choice_validation(cfg, tmp_path):
    assert cfg.get("exit_save_mode") == "ask"
    assert cfg.get("session_save_on_exit") is True
    assert cfg.get("trash_backend") == "workspace"
    cfg._data["exit_save_mode"] = "bogus"
    cfg._data["trash_backend"] = 42
    cfg._coerce_types()
    assert cfg.get("exit_save_mode") == "ask" and cfg.get("trash_backend") == "workspace"


def test_nothing_modified_never_prompts(cfg, ask):
    calls, _ = ask
    app = App(cfg, [FakeEditor("a.py", modified=False)])
    assert app._resolve_unsaved() is True
    assert calls["yncancel"] == [] and calls["yn"] == []


def test_ask_yes_saves(cfg, ask):
    calls, ans = ask
    e = FakeEditor("a.py")
    assert App(cfg, [e])._resolve_unsaved() is True
    assert e.saved == 1 and len(calls["yncancel"]) == 1


def test_ask_no_discards_without_saving(cfg, ask):
    _, ans = ask
    ans["yncancel"] = False
    e = FakeEditor("a.py")
    assert App(cfg, [e])._resolve_unsaved() is True
    assert e.saved == 0


def test_ask_cancel_aborts_exit(cfg, ask):
    _, ans = ask
    ans["yncancel"] = None
    e = FakeEditor("a.py")
    assert App(cfg, [e])._resolve_unsaved() is False
    assert e.saved == 0


def test_mode_save_saves_all_without_prompt(cfg, ask):
    calls, _ = ask
    cfg.set("exit_save_mode", "save")
    es = [FakeEditor("a"), FakeEditor("b"), FakeEditor("c", modified=False)]
    assert App(cfg, es)._resolve_unsaved() is True
    assert [e.saved for e in es] == [1, 1, 0]
    assert calls["yncancel"] == [] and calls["yn"] == []


def test_mode_discard_never_saves_and_never_prompts(cfg, ask):
    calls, _ = ask
    cfg.set("exit_save_mode", "discard")
    es = [FakeEditor("a"), FakeEditor(None)]
    assert App(cfg, es)._resolve_unsaved() is True
    assert [e.saved for e in es] == [0, 0]
    assert calls["yncancel"] == [] and calls["yn"] == []


def test_save_failure_asks_before_losing_data(cfg, ask):
    calls, ans = ask
    cfg.set("exit_save_mode", "save")
    ans["yn"] = False
    good, bad = FakeEditor("ok.py"), FakeEditor("bad.py", boom=True)
    assert App(cfg, [good, bad])._resolve_unsaved() is False
    assert good.saved == 1 and bad.saved == 1
    assert "bad.py" in calls["yn"][0][1] and "ok.py" not in calls["yn"][0][1]
    ans["yn"] = True
    assert App(cfg, [FakeEditor("bad.py", boom=True)])._resolve_unsaved() is True


def test_cancelled_save_as_counts_as_failure(cfg, ask):
    calls, ans = ask
    cfg.set("exit_save_mode", "save")
    ans["yn"] = False
    assert App(cfg, [FakeEditor(None, result=False)])._resolve_unsaved() is False
    assert len(calls["yn"]) == 1


def test_session_saved_by_default(cfg):
    app = App(cfg, [])
    app._persist_session_on_exit()
    assert app.session_saved == 1


def test_session_not_saved_and_file_removed_when_disabled(cfg, tmp_path, monkeypatch):
    sess = tmp_path / "session.json"
    sess.write_text("{}")
    monkeypatch.setattr(at, "SESSIONS_FILE", sess)
    cfg.set("session_save_on_exit", False)
    app = App(cfg, [])
    app._persist_session_on_exit()
    assert app.session_saved == 0 and not sess.exists()
