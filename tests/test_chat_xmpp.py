import time

import pytest

from src.chat.models import ChatProfile
from src.chat.session import ChatSession
from tests.chat_fakes import FakeXMPP, wait_for


def mk(port, pw="pw", **kw):
    return ChatSession(ChatProfile(name="x", protocol="xmpp", host="127.0.0.1", port=port, nick="ana",
                                   jid="ana@ex.org", password=ChatProfile.encode(pw), tls=False,
                                   channels=["sala@conf.ex.org"], keep_history=False, **kw))


@pytest.mark.parametrize("fragment", [False, True])
def test_xmpp_auth_muc_roster_topic_escaping_and_send(fragment):
    srv = FakeXMPP(fragment=fragment)
    s = mk(srv.port)
    s.connect()
    try:
        room = "sala@conf.ex.org"
        assert wait_for(lambda: s.connected and room in s.channels and
                        any(m.nick == "beto" for m in s.channels[room].messages))
        ch = s.channels[room]
        assert ch.topic == "Sala & teste"
        assert [m.text for m in ch.messages if m.nick == "beto"] == ["oi ana <3 & tchau"]
        assert {n for n, _ in s.members(room)} == {"ana", "beto"}
        assert not any(m.kind == "join" and m.nick == "beto" for m in ch.messages)
        assert [m for m in ch.messages if m.nick == "beto"][0].mention is True
        s.select(room)
        s.send('a <b> & "c"')
        assert wait_for(lambda: any(r.startswith("<message") for r in srv.received))
        msg = [r for r in srv.received if r.startswith("<message")][0]
        assert "type='groupchat'" in msg and "a &lt;b&gt; &amp; \"c\"" in msg
        srv.push(f'<presence from="{room}/beto" type="unavailable"><x xmlns="http://jabber.org/protocol/muc#user"/></presence>')
        assert wait_for(lambda: "beto" not in s.channels[room].nicks)
    finally:
        s.disconnect(); srv.sock.close()


def test_xmpp_wrong_password_fatal():
    srv = FakeXMPP(password="outra")
    s = mk(srv.port)
    s.connect()
    try:
        assert wait_for(lambda: any("autenticação falhou" in m.text for m in s.channels["*server*"].messages))
        time.sleep(0.4)
        assert srv.received.count("AUTH") == 1
    finally:
        s.disconnect(); srv.sock.close()


def test_xmpp_direct_message_and_action():
    srv = FakeXMPP()
    s = mk(srv.port)
    s.connect()
    try:
        assert wait_for(lambda: s.connected)
        srv.push('<message from="beto@ex.org/phone" type="chat"><body>/me acena</body></message>')
        assert wait_for(lambda: "beto@ex.org" in s.channels)
        m = s.channels["beto@ex.org"].messages[-1]
        assert m.kind == "action" and m.text == "acena" and s.channels["beto@ex.org"].kind == "dm"
    finally:
        s.disconnect(); srv.sock.close()
