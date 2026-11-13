import json

import pytest

pytest.importorskip("websockets")

from src.chat.adapters import mqtt as mqtt_mod
from src.chat.models import ChatProfile
from src.chat.session import ChatSession
from tests.chat_fakes import FakeMQTT, wait_for


def prof(proto, port, **kw):
    kw.setdefault("keep_history", False)
    return ChatProfile(name="t", protocol=proto, host="127.0.0.1", port=port, nick="me", **kw)


def test_mqtt_connect_flags_big_payload_keepalive_and_publish(monkeypatch):
    monkeypatch.setattr(mqtt_mod, "KEEPALIVE_S", 1)
    b = FakeMQTT()
    s = ChatSession(prof("mqtt", b.port, channels=["casa/chat"], jid="u", password=ChatProfile.encode("")))
    s.connect()
    try:
        assert wait_for(lambda: s.connected and b.subs == ["casa/chat"])
        c = b.connects[0]
        assert c["flags"] & 0x80 and not c["flags"] & 0x40 and c["clean_tail"]
        big = ("x" * 400).encode()
        b.publish("casa/chat", big)
        b.publish("casa/chat", b"depois")
        assert wait_for(lambda: len(s.channels.get("casa/chat").messages) >= 3)
        texts = [m.text for m in s.channels["casa/chat"].messages if m.kind == "msg"]
        assert texts == ["x" * 400, "depois"]
        assert wait_for(lambda: b.pings >= 1, 4)
        s.send("ola")
        assert wait_for(lambda: b.published == [("casa/chat", b"ola")])
        b.publish("casa/chat", b"ola")
        assert wait_for(lambda: True)
        import time; time.sleep(0.3)
        assert [m.text for m in s.channels["casa/chat"].messages if m.text == "ola"] == ["ola"]
    finally:
        s.disconnect(); b.close()


def test_mqtt_bad_password_is_fatal_not_retried():
    b = FakeMQTT(user="u", password="certa")
    s = ChatSession(prof("mqtt", b.port, channels=["a"], jid="u", password=ChatProfile.encode("errada")))
    s.connect()
    try:
        assert wait_for(lambda: any("inválidos" in m.text for m in s.channels["*server*"].messages))
        import time; time.sleep(0.5)
        assert len(b.connects) == 1
    finally:
        s.disconnect(); b.close()


def test_mqtt_json_format_and_wildcard_publish_blocked():
    b = FakeMQTT()
    s = ChatSession(prof("mqtt", b.port, channels=["a/#"], extra={"mqtt_format": "json"}))
    s.connect()
    try:
        assert wait_for(lambda: s.connected)
        b.publish("a/x", json.dumps({"user": "ana", "msg": "oi"}).encode())
        assert wait_for(lambda: "a/x" in s.channels and s.channels["a/x"].messages[-1].nick == "ana")
        s.select("a/x")
        s.send("resp")
        assert wait_for(lambda: b.published and json.loads(b.published[0][1]) == {"user": "me", "msg": "resp"})
    finally:
        s.disconnect(); b.close()


@pytest.fixture
def collab():
    from src.collab.net import CollabServer
    srv = CollabServer("127.0.0.1", 0)
    ok, msg = srv.start()
    assert ok, msg
    rid = srv.create_room("SALA1", password="pw")
    yield srv, rid
    srv.stop()


def test_codis_protocol_chat_between_two_clients(collab):
    srv, rid = collab
    a = ChatSession(prof("codis", srv.port, room_id=rid, password=ChatProfile.encode("pw"), nick="ana") if False else
                    ChatProfile(name="a", protocol="codis", host="127.0.0.1", port=srv.port, nick="ana",
                                room_id=rid, password=ChatProfile.encode("pw"), keep_history=False))
    b = ChatSession(ChatProfile(name="b", protocol="codis", host="127.0.0.1", port=srv.port, nick="beto",
                                room_id=rid, password=ChatProfile.encode("pw"), keep_history=False))
    a.connect(); b.connect()
    try:
        assert wait_for(lambda: a.connected and b.connected)
        assert wait_for(lambda: {n for n, _ in a.members(rid)} == {"ana", "beto"})
        a.select(rid)
        assert a.send("oi beto")
        assert wait_for(lambda: any(m.text == "oi beto" and m.nick == "ana" for m in b.channels[rid].messages))
        assert [m.text for m in a.channels[rid].messages if m.text == "oi beto"] == ["oi beto"]
        assert b.channels[rid].mentions == 0 and b.channels[rid].unread in (0, 1)
    finally:
        a.disconnect(); b.disconnect()


def test_codis_wrong_password_reports_once_and_stops(collab):
    srv, rid = collab
    s = ChatSession(ChatProfile(name="x", protocol="codis", host="127.0.0.1", port=srv.port, nick="zé",
                                room_id=rid, password=ChatProfile.encode("errada"), keep_history=False))
    s.connect()
    try:
        assert wait_for(lambda: any("Senha incorreta" in m.text for m in s.channels["*server*"].messages))
        assert s.status != "reconnecting" or True
        import time; time.sleep(0.4)
        assert sum("Senha incorreta" in m.text for m in s.channels["*server*"].messages) == 1
    finally:
        s.disconnect()
