"""Chat: interface real (Tk + Xvfb) conectada a um servidor IRC falso em localhost."""

from __future__ import annotations

import time

import pytest

from src.chat.models import ChatProfile
from tests.chat_fakes import FakeIRC


@pytest.fixture
def env(tk_root, monkeypatch):
    from src.theme.base import _define_themes
    from src.theme.manager import ThemeManager
    from src.ui import chat_panel as cp

    _define_themes()
    saved: list = []
    monkeypatch.setattr(cp, "_load_chat_profiles", lambda: [])
    monkeypatch.setattr(cp, "_save_chat_profiles", lambda p: saved.append(list(p)))
    srv = FakeIRC()
    prof = ChatProfile(name="Srv", protocol="irc", host="127.0.0.1", port=srv.port, nick="me",
                       channels=["#a", "#b"], keep_history=False)
    tm = ThemeManager("fluent_dark")
    panel = cp.ChatPanel(tk_root, tm, None)
    panel._profiles = [prof]
    panel.pack(fill="both", expand=True)

    def pump(cond=lambda: False, timeout=6.0):
        end = time.time() + timeout
        while time.time() < end:
            tk_root.update()
            if cond():
                return True
            time.sleep(0.01)
        return cond()

    class E:  # noqa: N801
        pass
    e = E()
    e.root, e.srv, e.panel, e.pump, e.cp, e.tm, e.saved, e.prof = tk_root, srv, panel, pump, cp, tm, saved, prof
    panel._ensure_session(prof)
    assert pump(lambda: panel._sessions["Srv"].connected and "#a" in panel._sessions["Srv"].channels
                and "#b" in panel._sessions["Srv"].channels)
    panel._select("Srv", "#a")
    yield e
    panel.shutdown()
    panel.destroy()
    srv.close()
    for w in list(tk_root.winfo_children()):
        if w.winfo_class() == "Toplevel":
            w.destroy()
    tk_root.update()


def text(e) -> str:
    return e.panel._text.get("1.0", "end")


def test_messages_render_incrementally_with_grouping_and_mention(env):
    e = env
    for line in (":ana!u@h PRIVMSG #a :oi pessoal", ":ana!u@h PRIVMSG #a :segunda", ":bob!u@h PRIVMSG #a :me vê isso"):
        e.srv.send(line)
    assert e.pump(lambda: "me vê isso" in text(e))
    t = text(e)
    assert t.count("ana") == 1
    assert "Hoje" in t
    sel = e.panel._text
    ranges = sel.tag_ranges("mrow")
    assert ranges
    first_index = sel.index("end-1c")
    e.srv.send(":ana!u@h PRIVMSG #a :nova")
    assert e.pump(lambda: "nova" in text(e))
    assert "oi pessoal" in text(e) and float(sel.index("end-1c")) > float(first_index)


def test_scrollback_is_not_yanked_to_bottom(env):
    e = env
    sess = e.panel._sessions["Srv"]
    for i in range(300):
        sess._post("#a", "ana", f"linha {i}")
    assert e.pump(lambda: "linha 299" in text(e))
    e.panel._text.yview_moveto(0.0)
    e.root.update()
    before = e.panel._text.yview()[0]
    sess._post("#a", "bob", "chegou nova")
    assert e.pump(lambda: "chegou nova" in text(e))
    assert e.panel._text.yview()[0] <= before + 0.05
    assert e.panel._jump.winfo_ismapped() and "1" in e.panel._jump.cget("text")
    e.panel._scroll_end()
    e.root.update()
    assert e.panel._at_bottom() and not e.panel._jump.winfo_ismapped()


def test_unread_badges_and_selection_clears(env):
    e = env
    e.srv.send(":ana!u@h PRIVMSG #b :ping me")
    e.srv.send(":ana!u@h PRIVMSG #b :outro")
    sess = e.panel._sessions["Srv"]
    assert e.pump(lambda: sess.channels["#b"].unread == 2)
    assert sess.channels["#b"].mentions == 1
    assert e.panel.unread_summary() == (2, 1)
    get = lambda: next(r for r in e.panel._rows if r.kind == "chan" and r.name == "#b").data[1:3]  # noqa: E731
    assert e.pump(lambda: get() == (2, 1))
    e.panel._select("Srv", "#b")
    assert e.panel.unread_summary() == (0, 0)


def test_composer_send_history_and_multiline_height(env):
    e = env
    box = e.panel._input
    box.insert("1.0", "olá mundo")
    e.panel._on_send()
    assert e.pump(lambda: "PRIVMSG #a :olá mundo" in e.srv.received)
    assert box.get("1.0", "end-1c") == ""
    e.panel._hist_up(None)
    assert box.get("1.0", "end-1c") == "olá mundo"
    box.delete("1.0", "end")
    box.insert("1.0", "a\nb\nc")
    e.panel._on_input_change()
    assert int(box.cget("height")) == 3
    e.panel._on_send()
    assert e.pump(lambda: all(f"PRIVMSG #a :{c}" in e.srv.received for c in "abc"))


def test_tab_completion_cycles_nicks_and_commands(env):
    e = env
    box = e.panel._input
    box.insert("1.0", "v")
    e.panel._on_tab(None)
    assert box.get("1.0", "end-1c") == "voz: "
    box.delete("1.0", "end")
    box.insert("1.0", "ola @o")
    e.panel._on_tab(None)
    assert box.get("1.0", "end-1c") == "ola @op "
    box.delete("1.0", "end")
    box.insert("1.0", "/par")
    e.panel._on_tab(None)
    assert box.get("1.0", "end-1c") == "/part "


def test_search_highlights_and_navigates(env):
    e = env
    sess = e.panel._sessions["Srv"]
    for w in ("gato", "cachorro", "gato preto", "peixe"):
        sess._post("#a", "ana", w)
    assert e.pump(lambda: "peixe" in text(e))
    e.panel._toggle_search(True)
    e.panel._search_var.set("gato")
    e.root.update()
    assert len(e.panel._search_hits) == 2
    assert e.panel._search_count.cget("text") == "2/2"
    e.panel._search_step(1)
    assert e.panel._search_count.cget("text") == "1/2"
    e.panel._toggle_search()
    assert not e.panel._text.tag_ranges("found")


def test_urls_are_clickable_only_for_http(env, monkeypatch):
    e = env
    opened = []
    monkeypatch.setattr(e.cp.webbrowser, "open", lambda u: opened.append(u))
    e.panel._sessions["Srv"]._post("#a", "ana", "veja www.exemplo.com/x e javascript:alert(1)")
    assert e.pump(lambda: "exemplo" in text(e))
    rng = e.panel._text.tag_ranges("url")
    assert len(rng) == 2
    x, y, *_ = e.panel._text.bbox(rng[0])

    class Ev:  # noqa: N801
        pass
    ev = Ev(); ev.x, ev.y = x + 2, y + 2
    e.panel._on_url_click(ev)
    assert opened == ["https://www.exemplo.com/x"]


def test_new_message_while_unfocused_server_tab_and_commands(env):
    e = env
    e.panel._select("Srv", "*server*")
    e.panel._input.insert("1.0", "/join #c")
    e.panel._on_send()
    assert e.pump(lambda: "JOIN #c" in e.srv.received)
    assert e.pump(lambda: e.panel._cur == ("Srv", "#c"))
    e.panel._input.insert("1.0", "/foo")
    e.panel._on_send()
    assert e.pump(lambda: "Comando desconhecido" in text(e))


def test_quick_switcher_lists_and_selects(env):
    e = env
    e.panel._quick_switch()
    e.root.update()
    from tkinter import Listbox, Toplevel
    dlg = [w for w in e.panel.winfo_children() if isinstance(w, Toplevel)][-1]
    lb = [w for w in dlg.winfo_children()[0].winfo_children() if isinstance(w, Listbox)][0]
    items = [lb.get(i) for i in range(lb.size())]
    assert any(i.startswith("#b") for i in items)
    ent = dlg.winfo_children()[0].winfo_children()[0]
    ent.insert(0, "#b")
    e.root.update()
    ent.event_generate("<Return>")
    e.root.update()
    assert e.panel._cur == ("Srv", "#b")


def test_theme_change_rebuilds_without_losing_sessions(env):
    e = env
    e.panel._sessions["Srv"]._post("#a", "ana", "mensagem antiga")
    assert e.pump(lambda: "mensagem antiga" in text(e))
    e.tm.set("fluent_light") if "fluent_light" in __import__("src.theme.base", fromlist=["THEMES"]).THEMES else None
    e.panel.rebuild()
    e.root.update()
    assert e.pump(lambda: "mensagem antiga" in text(e))
    assert e.panel._sessions["Srv"].connected


def test_posts_from_many_threads_never_crash_ui(env):
    import threading
    e = env
    sess = e.panel._sessions["Srv"]

    def spam(n):
        for i in range(150):
            sess._post("#a", f"u{n}", f"msg {n}-{i}")
    ts = [threading.Thread(target=spam, args=(n,)) for n in range(6)]
    [t.start() for t in ts]
    assert e.pump(lambda: all(not t.is_alive() for t in ts) and "msg 5-149" in text(e), 10)
    assert text(e).count("msg ") == 900


def test_shutdown_disconnects_all_sessions(env):
    e = env
    e.panel.shutdown()
    assert e.pump(lambda: not e.panel._sessions["Srv"].connected, 3)


def test_profile_dialog_validates_saves_and_encodes(env):
    e = env
    from src.ui.chat_profile_dialog import ChatProfileDialog
    d = ChatProfileDialog(e.root, e.tm, taken_names={"Srv"}, protocol="irc")
    e.root.update()
    d._vars["name"].set("Srv")
    d._save()
    assert d.result is None and "Já existe" in d._msg.cget("text")
    d._vars["name"].set("Novo")
    d._vars["host"].set("irc.exemplo.org")
    d._vars["nick"].set("com espaço")
    d._save()
    assert d.result is None and "Nick" in d._msg.cget("text")
    d._vars["nick"].set("ana")
    d._vars["password"].set("segredo")
    d._vars["channels"].set("#a, #b ;#c")
    d._save()
    assert d.result and d.result.channels == ["#a", "#b", "#c"]
    assert d.result.decoded_password() == "segredo" and d.result.password != "segredo"
    assert d.result.keep_history and d.result.auto_reconnect


def test_profile_dialog_switching_protocol_and_edit_roundtrip(env):
    e = env
    from src.chat.models import ChatProfile
    from src.ui.chat_profile_dialog import ChatProfileDialog
    p = ChatProfile(name="Disc", protocol="discord", token=ChatProfile.encode("TKN"), channels=["1", "2"],
                    extra={"token_kind": "bot"}, keep_history=False)
    d = ChatProfileDialog(e.root, e.tm, profile=p, taken_names={"Disc"})
    e.root.update()
    assert d._vars["token"].get() == "TKN" and d._vars["channels"].get() == "1, 2"
    d._save()
    assert d.result and d.result.decoded_token() == "TKN" and d.result.extra["token_kind"] == "bot"
    assert d.result.keep_history is False
    d2 = ChatProfileDialog(e.root, e.tm, protocol="irc")
    d2._pick("matrix")
    e.root.update()
    assert "jid" in d2._vars and "nick" not in d2._vars
    d2._pick("irc")
    assert "nick" in d2._vars
