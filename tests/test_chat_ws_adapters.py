import json
import time

import pytest

pytest.importorskip("websockets")

from src.chat.models import ChatProfile
from src.chat.session import ChatSession
from tests.chat_fakes import FakeHTTP, FakeWS, wait_for


def prof(proto, **kw):
    kw.setdefault("keep_history", False)
    return ChatProfile(name="t", protocol=proto, host="127.0.0.1", port=1, nick="me", **kw)


def test_discord_gateway_intents_names_filter_mentions_and_send():
    got = {}
    sent = []

    async def gw(ws):
        await ws.send(json.dumps({"op": 10, "d": {"heartbeat_interval": 300}}))
        ident = json.loads(await ws.recv())
        got["ident"] = ident
        await ws.send(json.dumps({"op": 0, "t": "READY", "s": 1, "d": {"user": {"id": "9", "username": "codisbot"}}}))
        await ws.send(json.dumps({"op": 0, "t": "GUILD_CREATE", "s": 2, "d": {"name": "G", "channels": [
            {"id": "100", "name": "geral", "type": 0}, {"id": "200", "name": "outro", "type": 0}]}}))
        await ws.send(json.dumps({"op": 0, "t": "MESSAGE_CREATE", "s": 3, "d": {
            "channel_id": "100", "guild_id": "1", "content": "oi <@9> veja", "author": {"id": "5", "username": "ana"},
            "mentions": [{"id": "9", "username": "codisbot"}], "attachments": [{"url": "http://x/y.png"}]}}))
        await ws.send(json.dumps({"op": 0, "t": "MESSAGE_CREATE", "s": 4, "d": {
            "channel_id": "200", "guild_id": "1", "content": "ruído", "author": {"id": "5", "username": "ana"}}}))
        await ws.send(json.dumps({"op": 0, "t": "MESSAGE_CREATE", "s": 5, "d": {
            "channel_id": "dm1", "content": "psiu", "author": {"id": "6", "username": "beto"}}}))
        got["beats"] = 0
        async for raw in ws:
            m = json.loads(raw)
            if m["op"] == 1:
                got["beats"] += 1
                await ws.send(json.dumps({"op": 11}))

    gwsrv = FakeWS(gw)
    h = FakeHTTP()
    h.routes[("POST", "/channels/100/messages")] = lambda p, q, b, hd: (sent.append((b, hd)) or (200, {"id": "1"}))
    h.routes[("POST", "/users/@me/channels")] = lambda p, q, b, hd: (200, {"id": "dm1"})
    h.routes[("POST", "/channels/dm1/messages")] = lambda p, q, b, hd: (sent.append((b, hd)) or (200, {}))
    s = ChatSession(prof("discord", token=ChatProfile.encode("TKN"), channels=["100"],
                         extra={"gateway_url": gwsrv.url, "api_base": h.url}))
    s.connect()
    try:
        assert wait_for(lambda: s.connected and "#geral" in s.channels and "@beto" in s.channels)
        assert got["ident"]["d"]["intents"] == 37377 and got["ident"]["d"]["token"] == "TKN"
        m = [x for x in s.channels["#geral"].messages if x.nick == "ana"][0]
        assert m.text == "oi @codisbot veja http://x/y.png" and m.mention
        assert "#outro" not in s.channels
        assert s.channels["@beto"].kind == "dm"
        s.select("#geral"); s.send("resp")
        s.select("@beto"); s.send("e aí")
        assert wait_for(lambda: len(sent) == 2)
        assert sent[0][0]["content"] == "resp" and sent[0][0]["allowed_mentions"] == {"parse": []}
        assert sent[0][1]["Authorization"] == "Bot TKN"
        assert wait_for(lambda: got["beats"] >= 2, 3)
    finally:
        s.disconnect(); gwsrv.close(); h.close()


def test_discord_privileged_intent_close_is_fatal_with_hint():
    async def gw(ws):
        await ws.send(json.dumps({"op": 10, "d": {"heartbeat_interval": 5000}}))
        await ws.recv()
        await ws.close(code=4014, reason="Disallowed intent")
    srv = FakeWS(gw)
    s = ChatSession(prof("discord", token=ChatProfile.encode("T"), extra={"gateway_url": srv.url}))
    s.connect()
    try:
        assert wait_for(lambda: any("Message Content" in m.text for m in s.channels["*server*"].messages))
        time.sleep(0.5)
        assert srv.conns == 1
    finally:
        s.disconnect(); srv.close()


def _slack_http(h, sent):
    h.routes[("POST", "/api/auth.test")] = lambda *a: (200, {"ok": True, "user_id": "UME", "user": "codis"})
    h.routes[("POST", "/api/conversations.list")] = lambda *a: (200, {"ok": True, "channels": [
        {"id": "C123", "name": "general"}]})
    h.routes[("POST", "/api/conversations.join")] = lambda *a: (200, {"ok": True})
    h.routes[("POST", "/api/users.info")] = lambda p, q, b, hd: (200, {"ok": True, "user": {
        "name": {"UANA": "ana", "UBOB": "bob"}.get(b["user"], "x"), "profile": {}}})
    h.routes[("POST", "/api/chat.postMessage")] = lambda p, q, b, hd: (sent.append(b) or (200, {"ok": True}))


def test_slack_socket_mode_maps_channel_names_users_and_acks():
    acks = []

    async def sock(ws):
        await ws.send(json.dumps({"type": "hello"}))
        await ws.send(json.dumps({"envelope_id": "e1", "type": "events_api", "payload": {"event": {
            "type": "message", "channel": "C123", "user": "UANA", "ts": "1700000000.1",
            "text": "oi <@UBOB> veja <https://x.io|site> &amp; <#C123|general>"}}}))
        await ws.send(json.dumps({"envelope_id": "e2", "type": "events_api", "payload": {"event": {
            "type": "message", "channel": "C123", "user": "UME", "text": "meu eco"}}}))
        async for raw in ws:
            acks.append(json.loads(raw))
    srv = FakeWS(sock)
    h, sent = FakeHTTP(), []
    _slack_http(h, sent)
    h.routes[("POST", "/api/apps.connections.open")] = lambda *a: (200, {"ok": True, "url": srv.url})
    s = ChatSession(prof("slack", token=ChatProfile.encode("xoxb-1"), channels=["general"],
                         extra={"api_base": h.url, "app_token": ChatProfile.encode("xapp-1")}))
    s.connect()
    try:
        assert wait_for(lambda: s.connected and any(m.nick == "ana" for m in s.channels["general"].messages))
        assert "C123" not in s.channels
        m = [x for x in s.channels["general"].messages if x.nick == "ana"][0]
        assert m.text == "oi @bob veja site (https://x.io) & #general"
        assert not any(x.text == "meu eco" for x in s.channels["general"].messages)
        assert wait_for(lambda: [a["envelope_id"] for a in acks] == ["e1", "e2"])
        s.select("general"); s.send("resp")
        assert wait_for(lambda: sent and sent[0] == {"channel": "C123", "text": "resp"})
    finally:
        s.disconnect(); srv.close(); h.close()


def test_slack_falls_back_to_polling_when_rtm_unavailable():
    h, sent = FakeHTTP(), []
    _slack_http(h, sent)
    h.routes[("POST", "/api/rtm.connect")] = lambda *a: (200, {"ok": False, "error": "not_allowed_token_type"})
    calls = []

    def hist(p, q, b, hd):
        calls.append(b)
        if len(calls) == 2:
            return 200, {"ok": True, "messages": [{"user": "UANA", "text": "segundo", "ts": "2.0"},
                                                  {"user": "UANA", "text": "primeiro", "ts": "1.0"}]}
        return 200, {"ok": True, "messages": []}
    h.routes[("POST", "/api/conversations.history")] = hist
    from src.chat.adapters.slack import SlackAdapter
    SlackAdapter.poll_interval = 0.1
    s = ChatSession(prof("slack", token=ChatProfile.encode("xoxb-1"), channels=["general"], extra={"api_base": h.url}))
    s.connect()
    try:
        assert wait_for(lambda: s.connected and len([m for m in s.channels["general"].messages if m.nick == "ana"]) == 2)
        assert [m.text for m in s.channels["general"].messages if m.nick == "ana"] == ["primeiro", "segundo"]
        assert any("polling" in m.text for m in s.channels["*server*"].messages)
        assert wait_for(lambda: len(calls) >= 3 and calls[2]["oldest"] == "2.0")
    finally:
        SlackAdapter.poll_interval = 4.0
        s.disconnect(); h.close()


def test_slack_invalid_token_fatal():
    h = FakeHTTP()
    h.routes[("POST", "/api/auth.test")] = lambda *a: (200, {"ok": False, "error": "invalid_auth"})
    s = ChatSession(prof("slack", token=ChatProfile.encode("bad"), extra={"api_base": h.url}))
    s.connect()
    try:
        assert wait_for(lambda: any("invalid_auth" in m.text for m in s.channels["*server*"].messages))
        time.sleep(0.4)
        assert sum(1 for m, p, _ in h.log if p.endswith("auth.test")) == 1
    finally:
        s.disconnect(); h.close()


def test_mattermost_login_header_token_names_dm_and_send():
    seen = {}

    async def ws_h(ws):
        seen["auth"] = json.loads(await ws.recv())
        await ws.send(json.dumps({"status": "OK", "seq_reply": 1}))
        await ws.send(json.dumps({"event": "hello", "data": {}}))
        await ws.send(json.dumps({"event": "posted", "data": {"sender_name": "@ana", "post": json.dumps(
            {"channel_id": "ch1", "user_id": "u2", "message": "olá me", "create_at": 1700000000000})}}))
        await ws.send(json.dumps({"event": "posted", "data": {"sender_name": "@me", "post": json.dumps(
            {"channel_id": "ch1", "user_id": "u1", "message": "meu eco"})}}))
        await ws.send(json.dumps({"event": "posted", "data": {"post": json.dumps(
            {"channel_id": "dm9", "user_id": "u2", "message": "privado"})}}))
        await ws.wait_closed()
    wsrv = FakeWS(ws_h)
    h, sent = FakeHTTP(), []
    base = "/api/v4"
    h.routes[("POST", base + "/users/login")] = lambda *a: (200, {"id": "u1"})
    h.routes[("GET", base + "/users/me")] = lambda p, q, b, hd: (200, {"id": "u1", "username": "me"})
    h.routes[("GET", base + "/users/me/teams")] = lambda *a: (200, [{"id": "t1", "name": "eq"}])
    h.routes[("GET", base + "/users/me/teams/t1/channels")] = lambda *a: (200, [
        {"id": "ch1", "name": "town-square", "display_name": "Town Square", "type": "O"}])
    h.routes[("GET", base + "/users")] = lambda *a: (200, [{"username": "ana"}, {"username": "me"}])
    h.routes[("GET", base + "/users/u2")] = lambda *a: (200, {"username": "ana"})
    h.routes[("GET", base + "/channels/dm9")] = lambda *a: (200, {"id": "dm9", "type": "D", "name": "u1__u2"})
    h.routes[("POST", base + "/posts")] = lambda p, q, b, hd: (sent.append(b) or (201, {}))
    orig = h.srv.RequestHandlerClass

    class H2(orig):
        def end_headers(self):
            if self.path.endswith("/users/login"):
                self.send_header("Token", "SESSION")
            super().end_headers()
    h.srv.RequestHandlerClass = H2
    s = ChatSession(prof("mattermost", jid="me", password=ChatProfile.encode("pw"), channels=["town-square"],
                         extra={"api_base": h.url, "ws_url": wsrv.url}))
    s.connect()
    try:
        assert wait_for(lambda: s.connected and any(m.nick == "ana" for m in s.channels["town-square"].messages))
        assert seen["auth"]["data"]["token"] == "SESSION"
        ana = [m for m in s.channels["town-square"].messages if m.nick == "ana"][0]
        assert ana.text == "olá me" and ana.mention
        assert not any(m.text == "meu eco" for m in s.channels["town-square"].messages)
        assert wait_for(lambda: "@ana" in s.channels and s.channels["@ana"].kind == "dm")
        assert {n for n, _ in s.members("town-square")} == {"ana", "me"}
        s.select("town-square"); s.send("resp")
        assert wait_for(lambda: sent == [{"channel_id": "ch1", "message": "resp"}])
    finally:
        s.disconnect(); wsrv.close(); h.close()
