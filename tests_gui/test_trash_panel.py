"""Testes reais (Xvfb) da lixeira: fachada TrashManager + TrashPanel."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from src.ui import trash_manager as tm


def pump(root, cond, timeout: float = 6.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        root.update()
        if cond():
            return True
        time.sleep(0.01)
    return cond()


class Cfg(dict):
    def get(self, k, d=None):  # noqa: D401
        return dict.get(self, k, d)


@pytest.fixture
def ws(tmp_path, monkeypatch):
    w = tmp_path / "proj"
    w.mkdir()
    reg = tm.TrashRegistry(legacy_dir=tmp_path / "legacy_home_trash")
    monkeypatch.setattr(tm.TrashManager, "_registry", reg)
    monkeypatch.setattr(tm.TrashManager, "_undo", [])
    tm.TrashManager.set_context(w, Cfg())
    yield w
    tm.TrashManager.set_context(None, Cfg())


@pytest.fixture
def panel(tk_root, ws, monkeypatch):
    from src.theme.manager import ThemeManager

    monkeypatch.setattr(tm.messagebox, "askyesno", lambda *a, **k: True)
    monkeypatch.setattr(tm.messagebox, "showwarning", lambda *a, **k: None)
    p = tm.TrashPanel(tk_root, ThemeManager("fluent_dark"), Cfg())
    p.pack(fill="both", expand=True)
    tk_root.update()
    yield p
    p.destroy()


def _mk(ws: Path, name: str, data: str = "x") -> Path:
    f = ws / name
    f.write_text(data)
    return f


def test_delete_goes_to_project_root_not_home(ws):
    f = _mk(ws, "a.txt")
    e = tm.TrashManager.delete(f)
    assert e is not None and e.path.parent == ws / ".codis_trash"
    assert not f.exists()
    assert tm.TrashManager.count() == 1


def test_permanent_delete(ws):
    f = _mk(ws, "p.txt")
    assert tm.TrashManager.delete(f, permanent=True) is None
    assert not f.exists() and tm.TrashManager.count() == 0


def test_undo_last_restores_most_recent(ws):
    a, b = _mk(ws, "a"), _mk(ws, "b")
    tm.TrashManager.delete(a)
    tm.TrashManager.delete(b)
    assert tm.TrashManager.undo_last() == b
    assert b.exists() and not a.exists()
    assert tm.TrashManager.undo_last() == a
    assert tm.TrashManager.undo_last() is None


def test_system_backend_falls_back_to_project_when_s2t_fails(ws, monkeypatch):
    tm.TrashManager.set_context(ws, Cfg(trash_backend="system"))
    monkeypatch.setattr(tm, "HAS_SEND2TRASH", True)

    def boom(_p):
        raise RuntimeError("sem lixeira")

    monkeypatch.setattr(tm, "_get_send2trash", lambda: boom)
    f = _mk(ws, "s.txt")
    e = tm.TrashManager.delete(f)
    assert e is not None and (ws / ".codis_trash").is_dir()


def test_system_backend_uses_send2trash(ws, monkeypatch):
    tm.TrashManager.set_context(ws, Cfg(trash_backend="system"))
    monkeypatch.setattr(tm, "HAS_SEND2TRASH", True)
    seen: list[str] = []
    monkeypatch.setattr(tm, "_get_send2trash", lambda: seen.append)
    f = _mk(ws, "s.txt")
    assert tm.TrashManager.delete(f) is None
    assert seen == [str(f)]
    assert f.exists()


def test_panel_lists_and_sizes_folders_in_background(tk_root, panel, ws):
    d = ws / "pasta"
    d.mkdir()
    (d / "f").write_bytes(b"1" * 2048)
    tm.TrashManager.delete(d)
    tm.TrashManager.delete(_mk(ws, "n.txt", "hello"))
    panel.refresh()
    assert len(panel._tree.get_children()) == 2
    assert pump(tk_root, lambda: "2.0 KB" in " ".join(
        str(panel._tree.item(i, "values")) for i in panel._tree.get_children()))


def test_panel_filter_and_sort(tk_root, panel, ws):
    for n, s in (("alpha.txt", "1"), ("beta.txt", "1" * 500), ("gamma.log", "1" * 50)):
        tm.TrashManager.delete(_mk(ws, n, s))
    panel.refresh()
    panel._clear_placeholder()
    panel._filter.set("txt")
    tk_root.update()
    names = [panel._tree.item(i, "values")[0] for i in panel._tree.get_children()]
    assert len(names) == 2 and all("txt" in n for n in names)
    panel._filter.set("")
    panel._sort_by("size")
    tk_root.update()
    first = panel._tree.item(panel._tree.get_children()[0], "values")[0]
    assert "beta" in first
    panel._sort_by("size")
    first = panel._tree.item(panel._tree.get_children()[0], "values")[0]
    assert "gamma" in first or "alpha" in first


def test_panel_restore_selected(tk_root, panel, ws):
    f = _mk(ws, "r.txt", "back")
    tm.TrashManager.delete(f)
    panel.refresh()
    panel._select_all()
    panel._on_restore()
    assert f.read_text() == "back"
    assert panel._tree.get_children() == ()


def test_panel_delete_permanently_runs_in_background(tk_root, panel, ws):
    for n in "abc":
        tm.TrashManager.delete(_mk(ws, n))
    panel.refresh()
    panel._select_all()
    panel._on_delete_perm()
    assert pump(tk_root, lambda: tm.TrashManager.count() == 0 and not panel._busy)
    assert panel._tree.get_children() == ()


def test_panel_empty_trash(tk_root, panel, ws):
    for n in "ab":
        tm.TrashManager.delete(_mk(ws, n))
    panel.refresh()
    panel._on_empty()
    assert pump(tk_root, lambda: tm.TrashManager.count() == 0 and not panel._busy)
    assert panel._empty_label.winfo_manager() == "place"


def test_panel_restore_to_other_folder(tk_root, panel, ws, tmp_path):
    f = _mk(ws, "mv.txt", "dest")
    tm.TrashManager.delete(f)
    panel.refresh()
    panel._select_all()
    out = tmp_path / "elsewhere"
    out.mkdir()
    panel._on_restore(out)
    assert (out / "mv.txt").read_text() == "dest"
    assert not f.exists()


def test_panel_shows_legacy_items(tk_root, ws, tmp_path, monkeypatch):
    import json

    from src.theme.manager import ThemeManager

    leg = tmp_path / "legacy_home_trash"
    leg.mkdir()
    (leg / "20200101_000000_ab_old.txt").write_text("L")
    (leg / ".meta.json").write_text(json.dumps([{
        "id": "20200101_000000_ab_old.txt", "original": str(ws / "old.txt"), "name": "old.txt",
        "is_dir": False, "deleted_at": "2020-01-01T00:00:00"}]))
    monkeypatch.setattr(tm.messagebox, "askyesno", lambda *a, **k: True)
    p = tm.TrashPanel(tk_root, ThemeManager("fluent_dark"), Cfg())
    p.pack()
    tk_root.update()
    try:
        (iid,) = p._tree.get_children()
        assert "old.txt" in p._tree.item(iid, "values")[0]
        p._select_all()
        p._on_restore()
        assert (ws / "old.txt").read_text() == "L"
    finally:
        p.destroy()


def test_panel_apply_theme_rebuilds_and_keeps_data(tk_root, panel, ws):
    from src.theme.manager import ThemeManager

    tm.TrashManager.delete(_mk(ws, "t.txt"))
    panel.refresh()
    panel.apply_theme(ThemeManager("fluent_dark").theme)
    tk_root.update()
    assert len(panel._tree.get_children()) == 1
    assert panel._placeholder_on and panel._query() == ""
