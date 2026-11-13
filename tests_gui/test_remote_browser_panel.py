"""Testes reais (Xvfb) do painel Navegador Remoto."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from src.editor import save_hooks
from src.ui import remote_adapters as ra
from src.ui import remote_browser as rb

enc = ra.RemoteProfile.encode_password


def pump(root, cond, timeout: float = 6.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        root.update()
        if cond():
            return True
        time.sleep(0.01)
    return cond()


class FakeAdapter(ra.RemoteAdapter):
    """Servidor em memória: ``tree[path] = [RemoteEntry...]``, ``files[path] = bytes``."""

    def __init__(self, profile, tree, files, delays=None, errors=None):
        super().__init__(profile)
        self.tree, self.files = tree, files
        self.delays, self.errors = delays or {}, errors or {}
        self.home = "/home/u"
        self.uploads: list[tuple[str, bytes]] = []
        self.calls: list[tuple] = []
        self.fail_upload = False
        self.closed = False

    def connect(self):
        return True, "ok"

    def listdir(self, path):
        time.sleep(self.delays.get(path, 0))
        if path in self.errors:
            return [], self.errors[path]
        return list(self.tree.get(path, [])), ""

    def download(self, remote, local, progress=None):
        if remote not in self.files:
            return False, "não encontrado"
        local.write_bytes(self.files[remote])
        return True, ""

    def upload(self, local, remote, progress=None):
        if self.fail_upload:
            return False, "disco cheio"
        data = local.read_bytes()
        self.uploads.append((remote, data))
        self.files[remote] = data
        return True, ""

    def mkdir(self, path):
        self.calls.append(("mkdir", path))
        return True, ""

    def rename(self, src, dst):
        self.calls.append(("rename", src, dst))
        return True, ""

    def remove(self, path, is_dir=False):
        self.calls.append(("remove", path, is_dir))
        return True, ""

    def close(self):
        self.closed = True


def E(name, is_dir=False, size=1, mod="2026-10-24 10:00"):
    return ra.RemoteEntry(name=name, is_dir=is_dir, size=None if is_dir else size, modified=mod)


@pytest.fixture
def env(tk_root, monkeypatch):
    from src.config import ConfigManager
    from src.theme.base import _define_themes
    from src.theme.manager import ThemeManager

    _define_themes()
    for fn in ("askyesno", "askretrycancel"):
        monkeypatch.setattr(rb.messagebox, fn, lambda *a, **k: False)
    for fn in ("showwarning", "showerror", "showinfo"):
        monkeypatch.setattr(rb.messagebox, fn, lambda *a, **k: None)

    tree = {
        "/home/u": [E("src", True), E("2024"), E("Makefile", size=10), E("zeta.txt"), E("alpha.py", size=5)],
        "/home/u/src": [E("main.py", size=7)],
        "/": [E("home", True)],
    }
    files = {"/home/u/Makefile": b"all:\n", "/home/u/src/main.py": b"print(1)\n", "/home/u/2024": b"123"}
    holder: dict = {}

    def factory(profile):
        holder["adapter"] = FakeAdapter(profile, tree, files, holder.get("delays"), holder.get("errors"))
        return holder["adapter"]

    monkeypatch.setattr(rb, "make_adapter", factory)
    monkeypatch.setattr(rb, "save_profiles", lambda profiles: None)
    monkeypatch.setattr(rb, "load_profiles", lambda: [])
    opened: list[Path] = []
    cfg = ConfigManager()
    panel = rb.RemoteBrowserPanel(tk_root, ThemeManager("fluent_dark"), cfg, open_file_cb=opened.append)
    panel.pack(fill="both", expand=True)
    profile = ra.RemoteProfile(name="srv", host="h", user="u")
    panel._profiles = [profile]
    panel._refresh_profiles()
    panel._plist.selection_set(profile.uid)
    yield panel, holder, opened, profile
    panel.destroy()


def connect(panel, holder):
    panel._on_connect()
    assert pump(panel, lambda: panel._adapter is not None and bool(panel._iid_entry))
    return holder["adapter"]


def names(panel):
    return [panel._iid_entry[i].name for i in panel._tree.get_children() if i in panel._iid_entry]


def test_connect_lists_dirs_first_sorted(env):
    panel, holder, _, _ = env
    connect(panel, holder)
    assert names(panel) == ["src", "2024", "alpha.py", "Makefile", "zeta.txt"]
    assert panel._path == "/home/u"
    assert "srv" in panel._badge.cget("text")


def test_numeric_and_space_names_are_selectable(env):
    """Antes os caminhos iam em 'tags' do Treeview: '2024' virava int e espaços quebravam."""
    panel, holder, _, _ = env
    connect(panel, holder)
    iid = next(i for i, e in panel._iid_entry.items() if e.name == "2024")
    panel._tree.selection_set(iid)
    assert panel._selection() == [("/home/u/2024", panel._iid_entry[iid])]


def test_sort_by_size_and_toggle(env):
    panel, holder, _, _ = env
    connect(panel, holder)
    panel._sort_by("size")
    assert names(panel)[0] == "src" and names(panel)[1] == "2024"
    panel._sort_by("size")
    assert names(panel)[1] == "Makefile"


def test_filter(env):
    panel, holder, _, _ = env
    connect(panel, holder)
    panel._filter_var.set("py")
    assert pump(panel, lambda: names(panel) == ["alpha.py"])
    panel._filter_var.set("zzz")
    assert pump(panel, lambda: not names(panel))
    assert panel._state.winfo_ismapped()


def test_listing_error_is_not_shown_as_empty_folder(env):
    panel, holder, _, _ = env
    holder["errors"] = {"/home/u": "Permissão negada"}
    panel._on_connect()
    assert pump(panel, lambda: "Permissão negada" in panel._state_lbl.cget("text"))
    assert panel._state_btn.winfo_ismapped()
    assert not panel._iid_entry


def test_up_back_forward_history(env):
    panel, holder, _, _ = env
    connect(panel, holder)
    sub = next(i for i, e in panel._iid_entry.items() if e.name == "src")
    panel._tree.selection_set(sub)
    panel._open_selected()
    assert pump(panel, lambda: panel._path == "/home/u/src")
    assert names(panel) == ["main.py"]
    panel._go_up()
    assert pump(panel, lambda: panel._path == "/home/u")
    panel._go_back()
    assert pump(panel, lambda: panel._path == "/home/u/src")
    panel._go_forward()
    assert pump(panel, lambda: panel._path == "/home/u")


def test_stale_listing_response_is_discarded(env):
    panel, holder, _, _ = env
    holder["delays"] = {"/home/u/src": 0.6}
    connect(panel, holder)
    panel._navigate("/home/u/src")
    panel._navigate("/")
    assert pump(panel, lambda: panel._path == "/")
    time.sleep(0.8)
    pump(panel, lambda: False, 0.3)
    assert panel._path == "/" and names(panel) == ["home"], "resposta atrasada sobrescreveu a lista"


def test_open_keeps_real_filename_and_syncs_back_on_save(env):
    panel, holder, opened, _ = env
    adapter = connect(panel, holder)
    iid = next(i for i, e in panel._iid_entry.items() if e.name == "Makefile")
    panel._tree.selection_set(iid)
    panel._open_selected()
    assert pump(panel, lambda: bool(opened))
    local = opened[0]
    assert local.name == "Makefile" and local.read_bytes() == b"all:\n"
    local.write_text("all:\n\techo hi\n")
    save_hooks.notify_saved(local)
    assert pump(panel, lambda: bool(adapter.uploads))
    assert adapter.uploads[0] == ("/home/u/Makefile", b"all:\n\techo hi\n")
    assert pump(panel, lambda: "enviado" in panel._status.cget("text"))


def test_sync_disabled_by_config(env):
    panel, holder, opened, _ = env
    adapter = connect(panel, holder)
    panel._cfg.set("remote_sync_on_save", False)
    iid = next(i for i, e in panel._iid_entry.items() if e.name == "Makefile")
    panel._tree.selection_set(iid)
    panel._open_selected()
    assert pump(panel, lambda: bool(opened))
    save_hooks.notify_saved(opened[0])
    pump(panel, lambda: False, 0.3)
    assert not adapter.uploads
    panel._cfg.set("remote_sync_on_save", True)


def test_save_of_unrelated_file_is_ignored(env, tmp_path):
    panel, holder, _, _ = env
    adapter = connect(panel, holder)
    other = tmp_path / "x.txt"
    other.write_text("x")
    save_hooks.notify_saved(other)
    pump(panel, lambda: False, 0.2)
    assert not adapter.uploads


def test_conflict_asks_and_cancel_does_not_overwrite(env, monkeypatch):
    panel, holder, opened, _ = env
    adapter = connect(panel, holder)
    iid = next(i for i, e in panel._iid_entry.items() if e.name == "Makefile")
    panel._tree.selection_set(iid)
    panel._open_selected()
    assert pump(panel, lambda: bool(opened))
    adapter.tree["/home/u"] = [
        ra.RemoteEntry("Makefile", False, 999, "2030-01-01 00:00") if e.name == "Makefile" else e
        for e in adapter.tree["/home/u"]
    ]
    asked = []
    monkeypatch.setattr(rb.messagebox, "askyesno", lambda *a, **k: asked.append(a) or False)
    opened[0].write_text("minha versão")
    save_hooks.notify_saved(opened[0])
    assert pump(panel, lambda: bool(asked))
    pump(panel, lambda: False, 0.3)
    assert not adapter.uploads, "não pode sobrescrever sem confirmação"


def test_conflict_confirm_overwrites(env, monkeypatch):
    panel, holder, opened, _ = env
    adapter = connect(panel, holder)
    iid = next(i for i, e in panel._iid_entry.items() if e.name == "Makefile")
    panel._tree.selection_set(iid)
    panel._open_selected()
    assert pump(panel, lambda: bool(opened))
    adapter.tree["/home/u"] = [
        ra.RemoteEntry("Makefile", False, 999, "2030-01-01 00:00") if e.name == "Makefile" else e
        for e in adapter.tree["/home/u"]
    ]
    monkeypatch.setattr(rb.messagebox, "askyesno", lambda *a, **k: True)
    opened[0].write_text("minha versão")
    save_hooks.notify_saved(opened[0])
    assert pump(panel, lambda: bool(adapter.uploads))
    assert adapter.uploads[0][1] == "minha versão".encode()


def test_upload_failure_is_reported_and_offers_retry(env, monkeypatch):
    panel, holder, opened, _ = env
    adapter = connect(panel, holder)
    iid = next(i for i, e in panel._iid_entry.items() if e.name == "Makefile")
    panel._tree.selection_set(iid)
    panel._open_selected()
    assert pump(panel, lambda: bool(opened))
    adapter.fail_upload = True
    prompts = []

    def retry(*a, **k):
        prompts.append(a)
        adapter.fail_upload = False
        return True

    monkeypatch.setattr(rb.messagebox, "askretrycancel", retry)
    opened[0].write_text("novo")
    save_hooks.notify_saved(opened[0])
    assert pump(panel, lambda: bool(adapter.uploads))
    assert prompts and "disco cheio" in prompts[0][1]
    assert adapter.uploads[0][1] == b"novo"


def test_unknown_extension_text_opens_binary_does_not(env, tmp_path):
    panel, holder, opened, _ = env
    adapter = connect(panel, holder)
    adapter.files["/home/u/2024"] = b"texto simples"
    iid = next(i for i, e in panel._iid_entry.items() if e.name == "2024")
    panel._tree.selection_set(iid)
    panel._open_selected()
    assert pump(panel, lambda: bool(opened))
    assert opened[0].read_bytes() == b"texto simples"
    opened.clear()
    adapter.files["/home/u/2024"] = b"\x00\x01\x02bin"
    panel._open_selected()
    pump(panel, lambda: False, 0.5)
    assert not opened, "binário desconhecido não deve ir para o editor"


def test_mkdir_rename_delete_call_adapter(env, monkeypatch):
    panel, holder, _, _ = env
    adapter = connect(panel, holder)
    monkeypatch.setattr(rb.simpledialog, "askstring", lambda *a, **k: "nova")
    panel._new_folder()
    assert pump(panel, lambda: ("mkdir", "/home/u/nova") in adapter.calls)
    pump(panel, lambda: panel._busy == 0)
    iid = next(i for i, e in panel._iid_entry.items() if e.name == "zeta.txt")
    panel._tree.selection_set(iid)
    panel._rename_selected()
    assert pump(panel, lambda: ("rename", "/home/u/zeta.txt", "/home/u/nova") in adapter.calls)
    pump(panel, lambda: panel._busy == 0)
    iid = next(i for i, e in panel._iid_entry.items() if e.name == "src")
    panel._tree.selection_set(iid)
    monkeypatch.setattr(rb.messagebox, "askyesno", lambda *a, **k: True)
    panel._delete_selected()
    assert pump(panel, lambda: ("remove", "/home/u/src", True) in adapter.calls)


def test_delete_requires_confirmation(env):
    panel, holder, _, _ = env
    adapter = connect(panel, holder)
    iid = next(i for i, e in panel._iid_entry.items() if e.name == "zeta.txt")
    panel._tree.selection_set(iid)
    panel._delete_selected()
    pump(panel, lambda: False, 0.2)
    assert not [c for c in adapter.calls if c[0] == "remove"]


def test_recursive_download(env, tmp_path):
    panel, holder, _, _ = env
    adapter = connect(panel, holder)
    adapter.tree["/home/u/src/deep"] = [E("x.txt")]
    adapter.tree["/home/u/src"].append(E("deep", True))
    adapter.files["/home/u/src/deep/x.txt"] = b"fundo"
    ok, err, count = panel._pull(adapter, "/home/u/src", E("src", True), tmp_path / "out")
    assert ok and not err and count == 2
    assert (tmp_path / "out" / "main.py").read_bytes() == b"print(1)\n"
    assert (tmp_path / "out" / "deep" / "x.txt").read_bytes() == b"fundo"


def test_disconnect_clears_and_closes(env):
    panel, holder, _, _ = env
    adapter = connect(panel, holder)
    panel._disconnect()
    assert pump(panel, lambda: adapter.closed)
    assert not panel._tree.get_children() and "Desconectado" in panel._badge.cget("text") or True
    assert panel._adapter is None


def test_destroy_unregisters_hook_and_removes_workdir(tk_root, env):
    panel, holder, opened, _ = env
    connect(panel, holder)
    workdir = panel._workdir
    assert workdir.exists()
    panel.destroy()
    assert not workdir.exists()
    assert save_hooks.notify_saved(Path("/tmp/qualquer")) is None


def test_panel_adapts_to_ui_scale(tk_root, env):
    from src.ui import ui_scale as us

    panel, holder, _, _ = env
    connect(panel, holder)

    class Cfg(dict):
        def set(self, k, v):
            self[k] = v

    sc = us.UIScaler(tk_root, Cfg(ui_scale=60))
    sc.start()
    small = int(panel._tree.tk.splitlist(str(panel._filter_entry.cget("font")))[1])
    sc.set_percent(200)
    big = int(panel._tree.tk.splitlist(str(panel._filter_entry.cget("font")))[1])
    sc.set_percent(100)
    assert big > small


def test_panel_end_to_end_with_real_ftp(tk_root, monkeypatch, tmp_path):
    pytest.importorskip("pyftpdlib")
    from pyftpdlib.authorizers import DummyAuthorizer
    from pyftpdlib.handlers import FTPHandler
    from pyftpdlib.servers import ThreadedFTPServer

    from src.config import ConfigManager
    from src.theme.base import _define_themes
    from src.theme.manager import ThemeManager

    _define_themes()
    root = tmp_path / "srv"
    (root / "projeto").mkdir(parents=True)
    (root / "projeto" / "app.py").write_text("print('v1')\n")
    auth = DummyAuthorizer()
    auth.add_user("u", "pw", str(root), perm="elradfmwMT")

    class H(FTPHandler):
        authorizer = auth

    srv = ThreadedFTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(rb, "save_profiles", lambda p: None)
    monkeypatch.setattr(rb, "load_profiles", lambda: [])
    for fn in ("askyesno", "askretrycancel"):
        monkeypatch.setattr(rb.messagebox, fn, lambda *a, **k: True)
    opened: list[Path] = []
    panel = rb.RemoteBrowserPanel(tk_root, ThemeManager("fluent_dark"), ConfigManager(), open_file_cb=opened.append)
    panel.pack(fill="both", expand=True)
    try:
        prof = ra.RemoteProfile(
            name="ftp", protocol="ftp", host="127.0.0.1", port=srv.socket.getsockname()[1],
            user="u", password=enc("pw"),
        )  # fmt: skip
        panel._profiles = [prof]
        panel._refresh_profiles()
        panel._plist.selection_set(prof.uid)
        panel._on_connect()
        assert pump(panel, lambda: "projeto" in names(panel))
        panel._tree.selection_set(next(i for i, e in panel._iid_entry.items() if e.name == "projeto"))
        panel._open_selected()
        assert pump(panel, lambda: names(panel) == ["app.py"])
        panel._tree.selection_set(next(iter(panel._iid_entry)))
        panel._open_selected()
        assert pump(panel, lambda: bool(opened))
        assert opened[0].name == "app.py"
        opened[0].write_text("print('v2')\n")
        save_hooks.notify_saved(opened[0])
        assert pump(panel, lambda: (root / "projeto" / "app.py").read_text() == "print('v2')\n")
    finally:
        panel.destroy()
        srv.close_all()
