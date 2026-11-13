import time

import pytest

from src.chat.adapters.irc import IRCAdapter
from src.chat.models import ChatProfile
from src.chat.session import ChatSession
from tests.chat_fakes import FakeIRC, wait_for


def mk(port, **kw):
    kw.setdefault("channels", ["#t"])
    return ChatProfile(name="t", protocol="irc", host="127.0.0.1", port=port, nick="me",
                       keep_history=False, **kw)


@pytest.fixture
def srv():
    s = FakeIRC()
    yield s
    s.close()


def session_for(port, **kw):
    s = ChatSession(mk(port, **kw))
    s.connect()
    return s


def test_registration_join_names_topic(srv):
    s = session_for(srv.port)
    try:
        assert wait_for(lambda: s.connected and "#t" in s.channels and s.channels["#t"].nicks)
        assert s.channels["#t"].topic == "Topico de teste"
        assert s.members("#t")[0] == ("op", "@")
        assert s.active == "#t"
    finally:
        s.disconnect()


def test_lf_only_server_is_understood():
    srv = FakeIRC(eol=b"\n")
    s = session_for(srv.port)
    try:
        assert wait_for(lambda: s.connected)
    finally:
        s.disconnect(); srv.close()


def test_crlf_injection_is_neutralised(srv):
    s = session_for(srv.port)
    try:
        assert wait_for(lambda: s.connected and s.active == "#t")
        s.send("oi\r\nQUIT :pwned")
        assert wait_for(lambda: any(l.startswith("PRIVMSG #t :") for l in srv.received))
        time.sleep(0.2)
        assert not any(l.startswith("QUIT :pwned") for l in srv.received)
    finally:
        s.disconnect()


def test_long_message_is_split_under_512(srv):
    s = session_for(srv.port)
    try:
        assert wait_for(lambda: s.connected and s.active == "#t")
        s.send("palavra " * 200)
        assert wait_for(lambda: sum(l.startswith("PRIVMSG #t") for l in srv.received) >= 4)
        assert all(len(l.encode()) + 2 <= 512 for l in srv.received)
    finally:
        s.disconnect()


def test_sasl_plain_flow():
    srv = FakeIRC()
    s = session_for(srv.port, sasl=True, password=ChatProfile.encode("segredo"))
    try:
        assert wait_for(lambda: s.connected)
        assert srv.sasl_ok
        assert not any(l.startswith("PASS") for l in srv.received)
    finally:
        s.disconnect(); srv.close()


def test_incoming_dm_action_mention_and_unread(srv):
    s = session_for(srv.port)
    try:
        assert wait_for(lambda: s.connected and s.active == "#t")
        s.select("*server*")
        srv.send(":bob!u@h PRIVMSG #t :oi me, tudo bem?")
        srv.send(":bob!u@h PRIVMSG #t :\x01ACTION dança\x01")
        srv.send(":ana!u@h PRIVMSG me :psiu")
        assert wait_for(lambda: "ana" in s.channels and s.channels["#t"].unread == 2)
        assert s.channels["#t"].mentions == 1
        assert s.channels["ana"].kind == "dm" and s.channels["ana"].mentions == 1
        kinds = [m.kind for m in s.channels["#t"].messages if m.nick == "bob"]
        assert kinds == ["msg", "action"]
    finally:
        s.disconnect()


def test_auto_reconnect_rejoins(srv):
    IRCAdapter.backoff_base = 0.05
    s = session_for(srv.port)
    try:
        assert wait_for(lambda: s.connected and s.active == "#t")
        srv.received.clear()
        srv.drop()
        assert wait_for(lambda: not s.connected, 3)
        assert wait_for(lambda: s.connected and any(l.startswith("JOIN #t") for l in srv.received), 8)
        assert any("Conexão perdida" in m.text for m in s.channels["#t"].messages)
    finally:
        IRCAdapter.backoff_base = 1.0
        s.disconnect()


def test_quit_only_in_channels_where_nick_is_present(srv):
    s = session_for(srv.port)
    try:
        assert wait_for(lambda: s.connected and s.active == "#t")
        srv.send(":op!u@h QUIT :bye")
        assert wait_for(lambda: "op" not in s.channels["#t"].nicks)
        assert wait_for(lambda: any(m.kind == "part" and m.nick == "op" for m in s.channels["#t"].messages))
    finally:
        s.disconnect()


def test_commands(srv):
    s = session_for(srv.port)
    try:
        assert wait_for(lambda: s.connected and s.active == "#t")
        s.send("/me acena")
        s.send("/msg bob oi bob")
        s.send("//nao-e-comando")
        s.send("/join outro")
        assert wait_for(lambda: "JOIN #outro" in srv.received and "PRIVMSG #t :/nao-e-comando" in srv.received)
        assert "PRIVMSG #t :\x01ACTION acena\x01" in srv.received
        assert "PRIVMSG bob :oi bob" in srv.received
        assert "PRIVMSG #t :/nao-e-comando" in srv.received
        s.send("/foo")
        assert s.channels["#t"].messages[-1].kind == "err"
        s.disconnect()
        assert wait_for(lambda: not s.connected)
        n = len(s.channels["#t"].messages)
        assert s.send("oi") is False
    finally:
        s.disconnect()


def test_message_bounded_and_trim_keeps_absolute_index():
    from src.chat.session import MAX_MESSAGES
    s = ChatSession(mk(1, channels=[]))
    for i in range(MAX_MESSAGES + 700):
        s._post("#x", "a", str(i))
    ch = s.channels["#x"]
    assert len(ch.messages) <= MAX_MESSAGES and ch.total == MAX_MESSAGES + 700
    base, msgs = s.snapshot("#x", ch.total - 3)
    assert [m.text for m in msgs] == [str(i) for i in range(MAX_MESSAGES + 697, MAX_MESSAGES + 700)]
