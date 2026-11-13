"""CollabPanel de ponta a ponta: dois painéis, rede real, editores tk.Text reais."""

from __future__ import annotations

import gc
import socket
import time
import tkinter as tk

import pytest

from src.collab.net import HAS_WS

pytestmark = pytest.mark.skipif(not HAS_WS, reason="websockets não instalado")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait(root, predicate, timeout=8.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        root.update()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


@pytest.fixture()
def env(tk_root, monkeypatch):
    from src.theme.base import _define_themes
    from src.theme.manager import ThemeManager, _patch_all_themes, _patch_theme
    from src.ui import collab_panel as cp

    _define_themes()
    _patch_all_themes()
    tmgr = ThemeManager("fluent_dark")
    _patch_theme(tmgr.theme)

    class Cfg:
        def __init__(self): self.d = {}
        def get(self, k, default=None): return self.d.get(k, default)
        def set(self, k, v): self.d[k] = v

    errors: list[str] = []
    monkeypatch.setattr(cp.messagebox, "showerror", lambda t, m, **k: errors.append(m))
    monkeypatch.setattr(cp.messagebox, "showwarning", lambda t, m, **k: errors.append(m))

    wins: list[tk.Toplevel] = []

    def make(editor_factory, new_editor=None):
        win = tk.Toplevel(tk_root)
        win.geometry("480x760+0+0")
        wins.append(win)
        panel = cp.CollabPanel(win, tmgr, Cfg(), get_editor_widget=editor_factory,
                               new_editor=new_editor)
        panel.pack(fill="both", expand=True)
        return panel

    def text_widget(content=""):
        host = tk.Toplevel(tk_root)
        host.geometry("500x300+500+0")
        wins.append(host)
        t = tk.Text(host)
        t.pack(fill="both", expand=True)
        t.insert("1.0", content)
        tk_root.update()
        return t

    gc.collect()
    yield make, text_widget, errors, tk_root
    for w in wins:
        try:
            w.destroy()
        except tk.TclError:
            pass
    gc.collect()


def _host(panel, port):
    panel._user_var.set("ana")
    panel._port_var.set(str(port))
    panel._host_pass_var.set("")
    panel._on_host()


def _join(panel, port, room, password, user="beto"):
    panel._user_var.set(user)
    panel._join_host_var.set("127.0.0.1")
    panel._join_port_var.set(str(port))
    panel._join_room_var.set(room)
    panel._join_pass_var.set(password)
    panel._on_join()


def test_host_generates_password_and_guest_gets_document_in_new_tab(env):
    make, text_widget, errors, root = env
    host_text = text_widget("print('olá')\n")
    guest_text = text_widget("")
    opened = []
    host = make(lambda: host_text)
    guest = make(lambda: None, new_editor=lambda: (opened.append(1), guest_text)[1])
    port = _free_port()
    _host(host, port)
    assert host._host_pass_var.get(), "senha deve ser gerada quando em branco"
    assert host._room_id and host._room_id in host._room_info_var.get()
    assert "Senha" in host._invite and host._room_id in host._invite
    _join(guest, port, host._room_id, host._host_pass_var.get())
    assert _wait(root, lambda: guest_text.get("1.0", "end-1c") == "print('olá')\n")
    assert opened == [1], "convidado deve receber uma aba nova"
    host._on_stop_host()


def test_concurrent_typing_converges_and_keeps_local_cursor(env):
    make, text_widget, errors, root = env
    ht, gt = text_widget("linha 1\nlinha 2\nlinha 3\n"), text_widget("")
    host, guest = make(lambda: ht), make(lambda: None, new_editor=lambda: gt)
    port = _free_port()
    _host(host, port)
    _join(guest, port, host._room_id, host._host_pass_var.get())
    assert _wait(root, lambda: gt.get("1.0", "end-1c") == ht.get("1.0", "end-1c") != "")
    gt.mark_set("insert", "3.3")
    ht.insert("1.0", "HOST>")
    gt.insert("end-1c", "<GUEST")
    ht.insert("2.0", "meio ")
    assert _wait(root, lambda: ht.get("1.0", "end-1c") == gt.get("1.0", "end-1c")
                 and "HOST>" in gt.get("1.0", "end-1c") and "<GUEST" in ht.get("1.0", "end-1c"))
    assert gt.get("3.0", "3.0 lineend") .startswith("linha 3") or "linha 3" in gt.get("1.0", "end")
    assert gt.index("insert").startswith("3.")
    host._on_stop_host()


def test_chat_message_shows_once_for_sender_and_reaches_other(env):
    make, text_widget, errors, root = env
    ht, gt = text_widget("x"), text_widget("")
    host, guest = make(lambda: ht), make(lambda: None, new_editor=lambda: gt)
    port = _free_port()
    _host(host, port)
    _join(guest, port, host._room_id, host._host_pass_var.get())
    assert _wait(root, lambda: guest._session is not None and guest._session.synced)
    guest._chat_var.set("oi pessoal")
    guest._on_chat_send()
    assert _wait(root, lambda: any(m[2] == "oi pessoal" for m in host._chat_history))
    _wait(root, lambda: False, 0.4)
    assert [m for m in guest._chat_history if m[2] == "oi pessoal"].__len__() == 1
    assert any(m[1] == "->" and "beto" in m[2] for m in host._chat_history)
    host._on_stop_host()


def test_wrong_password_shows_error_and_resets_buttons(env):
    make, text_widget, errors, root = env
    ht, gt = text_widget("x"), text_widget("")
    host, guest = make(lambda: ht), make(lambda: None, new_editor=lambda: gt)
    port = _free_port()
    _host(host, port)
    _join(guest, port, host._room_id, "senha-errada")
    assert _wait(root, lambda: any("Senha incorreta" in e for e in errors))
    assert _wait(root, lambda: str(guest._join_btn.cget("state")) == "normal")
    assert guest._session is None
    host._on_stop_host()


def test_unknown_room_is_reported(env):
    make, text_widget, errors, root = env
    ht, gt = text_widget("x"), text_widget("")
    host, guest = make(lambda: ht), make(lambda: None, new_editor=lambda: gt)
    port = _free_port()
    _host(host, port)
    _join(guest, port, "SALAXX", host._host_pass_var.get())
    assert _wait(root, lambda: any("não encontrada" in e for e in errors))
    host._on_stop_host()


def test_closing_shared_tab_ends_session_cleanly(env):
    make, text_widget, errors, root = env
    ht, gt = text_widget("abc"), text_widget("")
    host, guest = make(lambda: ht), make(lambda: None, new_editor=lambda: gt)
    port = _free_port()
    _host(host, port)
    _join(guest, port, host._room_id, host._host_pass_var.get())
    assert _wait(root, lambda: gt.get("1.0", "end-1c") == "abc")
    gt.master.destroy()
    assert _wait(root, lambda: guest._session is None)
    host._on_stop_host()


def test_host_stop_notifies_guest_and_frees_port(env):
    make, text_widget, errors, root = env
    ht, gt = text_widget("abc"), text_widget("")
    host, guest = make(lambda: ht), make(lambda: None, new_editor=lambda: gt)
    port = _free_port()
    _host(host, port)
    _join(guest, port, host._room_id, host._host_pass_var.get())
    assert _wait(root, lambda: gt.get("1.0", "end-1c") == "abc")
    host._on_stop_host()
    assert _wait(root, lambda: guest._session is None)
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", port))


def test_app_glue_binds_real_editor_and_joins_in_new_tab(tk_root, monkeypatch):
    """Regressão: o app lia o editor com EditorPane.get (inexistente) e via sempre ''."""
    from src.app import CodisApp
    from src.ui import collab_panel as cp

    monkeypatch.setattr(cp.messagebox, "showerror", lambda *a, **k: None)
    tk_root.geometry("1100x700+0+0")
    app = CodisApp(tk_root)
    app._cfg.set("workspace_trust", False)
    pane = app.new_file()
    pane.text.insert("1.0", "conteúdo do app\n")
    tk_root.update()

    app._open_collab()
    panel = app._collab_panel
    editor = panel._current_editor()
    assert editor is pane.text and editor.get("1.0", "end-1c") == "conteúdo do app\n"

    port = _free_port()
    panel._user_var.set("host")
    panel._port_var.set(str(port))
    panel._on_host()
    assert panel._room_id
    tabs_before = len(app._editors)

    from src.collab.net import CollabClient
    from src.collab.session import CollabSession
    from src.collab.tk_adapter import TkTextAdapter

    guest_text = tk.Text(tk_root)
    guest = CollabSession(CollabClient("127.0.0.1", port, panel._room_id, "conv",
                                       panel._host_pass_var.get()),
                          TkTextAdapter(guest_text), lambda ev: None)
    guest._client.connect()
    assert _wait(tk_root, lambda: (guest.tick(), guest.synced)[1])
    assert guest_text.get("1.0", "end-1c") == "conteúdo do app\n"

    guest_text.insert("end-1c", "linha do convidado\n")
    assert _wait(tk_root, lambda: (guest.tick(), "linha do convidado" in pane.text.get("1.0", "end"))[1])
    assert len(app._editors) == tabs_before
    guest.close()
    panel.disconnect()
    app._collab_win.destroy()
