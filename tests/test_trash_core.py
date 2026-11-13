"""Testes do núcleo da lixeira (src/trash_core.py)."""

from __future__ import annotations

import errno
import json
import os
import threading
from pathlib import Path

import pytest

from src import trash_core as tc

SHM = Path("/dev/shm")


def _dev(p: Path) -> int:
    return os.lstat(p).st_dev


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    w = tmp_path / "proj"
    w.mkdir()
    return w


def test_put_goes_to_workspace_root_and_is_atomic_rename(ws: Path) -> None:
    f = ws / "sub" / "a.txt"
    f.parent.mkdir()
    f.write_text("oi")
    ino = os.lstat(f).st_ino
    root = tc.trash_root_for(f, ws)
    assert root == ws
    e = tc.TrashStore.for_root(root).put(f)
    assert not f.exists()
    assert e.path.parent == ws / ".codis_trash"
    assert os.lstat(e.path).st_ino == ino


def test_trash_folder_is_self_gitignored(ws: Path) -> None:
    f = ws / "x.txt"
    f.write_text("x")
    tc.TrashStore.for_root(ws).put(f)
    gi = (ws / ".codis_trash" / ".gitignore").read_text()
    assert "*" in gi


def test_restore_roundtrip_and_conflict_suffix(ws: Path) -> None:
    f = ws / "doc.tar.gz"
    f.write_text("v1")
    st = tc.TrashStore.for_root(ws)
    e = st.put(f)
    f.write_text("novo")
    dest = st.restore(e.id)
    assert dest.name == "doc_restaurado.tar.gz"
    assert dest.read_text() == "v1"
    assert f.read_text() == "novo"
    assert st.entries() == []


def test_restore_recreates_missing_parent(ws: Path) -> None:
    d = ws / "a" / "b"
    d.mkdir(parents=True)
    f = d / "f.txt"
    f.write_text("z")
    st = tc.TrashStore.for_root(ws)
    e = st.put(f)
    (ws / "a" / "b").rmdir()
    (ws / "a").rmdir()
    assert st.restore(e.id) == f
    assert f.read_text() == "z"


def test_directory_roundtrip(ws: Path) -> None:
    d = ws / "pasta"
    (d / "in").mkdir(parents=True)
    (d / "in" / "f").write_text("1")
    st = tc.TrashStore.for_root(ws)
    e = st.put(d)
    assert e.is_dir and not d.exists()
    st.restore(e.id)
    assert (d / "in" / "f").read_text() == "1"


def test_symlink_is_moved_not_followed(ws: Path, tmp_path: Path, make_symlink) -> None:
    target = tmp_path / "alvo.txt"
    target.write_text("keep")
    link = ws / "lnk"
    make_symlink(link, target)
    st = tc.TrashStore.for_root(ws)
    st.put(link)
    assert target.read_text() == "keep"
    assert not os.path.lexists(link)


def test_purge_and_empty(ws: Path) -> None:
    st = tc.TrashStore.for_root(ws)
    for n in "abc":
        (ws / n).write_text(n)
        st.put(ws / n)
    first = st.entries()[0]
    assert st.purge(first.id)
    assert len(st.entries()) == 2
    assert st.empty() == 2
    assert st.entries() == []


def test_refuses_to_trash_the_trash(ws: Path) -> None:
    (ws / "x").write_text("1")
    st = tc.TrashStore.for_root(ws)
    e = st.put(ws / "x")
    with pytest.raises(tc.TrashError):
        st.put(e.path)


def test_missing_file_raises(ws: Path) -> None:
    with pytest.raises(FileNotFoundError):
        tc.TrashStore.for_root(ws).put(ws / "nada")


def test_corrupt_meta_is_rebuilt_from_disk(ws: Path) -> None:
    f = ws / "keep.txt"
    f.write_text("k")
    st = tc.TrashStore.for_root(ws)
    st.put(f)
    (ws / ".codis_trash" / ".meta.json").write_text("{ isso nao e json")
    items = st.entries()
    assert [e.name for e in items] == ["keep.txt"]


def test_v1_list_format_still_readable(ws: Path) -> None:
    tdir = ws / ".codis_trash"
    tdir.mkdir()
    (tdir / "20200101_000000_ab_f.txt").write_text("v")
    (tdir / ".meta.json").write_text(
        json.dumps([{"id": "20200101_000000_ab_f.txt", "original": str(ws / "f.txt"),
                     "name": "f.txt", "is_dir": False, "deleted_at": "2020-01-01T00:00:00"}])
    )
    assert [e.name for e in tc.TrashStore(tdir).entries()] == ["f.txt"]


def test_orphan_meta_entries_are_dropped(ws: Path) -> None:
    f = ws / "g.txt"
    f.write_text("g")
    st = tc.TrashStore.for_root(ws)
    e = st.put(f)
    e.path.unlink()
    assert st.entries() == []


def test_purge_expired_by_age(ws: Path) -> None:
    st = tc.TrashStore.for_root(ws)
    (ws / "old").write_text("o")
    (ws / "new").write_text("n")
    old = st.put(ws / "old")
    st.put(ws / "new")
    raw = st._read()
    for r in raw:
        if r["id"] == old.id:
            r["deleted_at"] = "2000-01-01T00:00:00"
    st._write(raw)
    assert st.purge_expired(older_than_days=30) == 1
    assert [e.name for e in st.entries()] == ["new"]


def test_purge_expired_by_size_cap_removes_oldest_first(ws: Path) -> None:
    st = tc.TrashStore.for_root(ws)
    ids = []
    for i, n in enumerate(("a", "b", "c")):
        (ws / n).write_bytes(b"x" * 1000)
        ids.append(st.put(ws / n).id)
    raw = st._read()
    for i, r in enumerate(raw):
        r["deleted_at"] = f"2020-01-0{i + 1}T00:00:00"
    st._write(raw)
    assert st.purge_expired(max_bytes=1500) == 2
    assert [e.name for e in st.entries()] == ["c"]


def test_concurrent_puts_keep_meta_consistent(ws: Path) -> None:
    st = tc.TrashStore.for_root(ws)
    for i in range(40):
        (ws / f"f{i}").write_text(str(i))

    def work(i: int) -> None:
        tc.TrashStore.for_root(ws).put(ws / f"f{i}")

    ts = [threading.Thread(target=work, args=(i,)) for i in range(40)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(st.entries()) == 40
    assert len(st._read()) == 40


def test_tree_size_and_human_size(ws: Path) -> None:
    (ws / "d").mkdir()
    (ws / "d" / "a").write_bytes(b"1" * 10)
    (ws / "d" / "b").write_bytes(b"1" * 20)
    assert tc.tree_size(ws / "d") == 30
    assert tc.human_size(None) == "…"
    assert tc.human_size(512) == "512 B"
    assert tc.human_size(1536) == "1.5 KB"


def test_tree_size_honours_cancel(ws: Path) -> None:
    (ws / "d").mkdir()
    ev = threading.Event()
    ev.set()
    assert tc.tree_size(ws / "d", cancel=ev) is None


needs_shm = pytest.mark.skipif(
    not SHM.is_dir() or not os.access(SHM, os.W_OK), reason="/dev/shm indisponível"
)


@needs_shm
def test_real_other_volume_gets_its_own_trash_never_home(tmp_path: Path) -> None:
    """Cenário do bug: arquivo em outro disco que o ~/.codis."""
    base = SHM / f"codis_t_{os.getpid()}"
    base.mkdir()
    try:
        if _dev(base) == _dev(tmp_path):
            pytest.skip("/dev/shm e tmp_path no mesmo dispositivo")
        f = base / "work" / "big.bin"
        f.parent.mkdir()
        f.write_bytes(b"0" * 4096)
        ino = os.lstat(f).st_ino
        root = tc.trash_root_for(f, base)
        assert root == base
        e = tc.TrashStore.for_root(root).put(f)
        assert _dev(e.path) == _dev(base)
        assert os.lstat(e.path).st_ino == ino
    finally:
        import shutil

        shutil.rmtree(base, ignore_errors=True)


def test_cross_device_is_refused_not_copied(ws: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = ws / "x"
    f.write_text("x")

    def boom(*_a, **_k):
        raise OSError(errno.EXDEV, "cross-device")

    monkeypatch.setattr(tc.os, "rename", boom)
    with pytest.raises(tc.CrossDeviceError):
        tc.TrashStore.for_root(ws).put(f)
    assert f.exists()


def test_root_demoted_to_same_volume_for_nested_mount(ws: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mnt = ws / "mnt"
    mnt.mkdir()
    f = mnt / "deep" / "f.txt"
    f.parent.mkdir()
    f.write_text("1")
    real = tc._dev

    def fake(p: Path):
        p = Path(p)
        if p == mnt or tc._is_relative_to(p, mnt):
            return 999
        return real(p)

    monkeypatch.setattr(tc, "_dev", fake)
    assert tc.trash_root_for(f, ws) == mnt


def test_workspace_itself_uses_parent(ws: Path) -> None:
    assert tc.trash_root_for(ws, ws) == ws.parent
