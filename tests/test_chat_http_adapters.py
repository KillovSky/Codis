import queue
import time

from src.chat.models import ChatProfile
from src.chat.session import ChatSession
from tests.chat_fakes import FakeHTTP, wait_for


def prof(proto, url, **kw):
    return ChatProfile(name="t", protocol=proto, host="127.0.0.1", port=1, nick="me",
                       keep_history=False, extra={"api_base": url, **kw.pop("extra", {})}, **kw)


def test_telegram_polling_naming_send_and_plain_text():
    h = FakeHTTP()
    updates: queue.Queue = queue.Queue()
    sent = []

    def get_updates(path, q, body, hd):
        try:
            return 200, {"ok": True, "result": [updates.get(timeout=0.3)]}
        except queue.Empty:
            return 200, {"ok": True, "result": []}

    h.routes[("POST", "/botTOK/getMe")] = lambda *a: (200, {"ok": True, "result": {"username": "codisbot"}})
    h.routes[("POST", "/botTOK/getUpdates")] = get_updates
    h.routes[("POST", "/botTOK/getChat")] = lambda p, q, b, hd: (200, {"ok": True, "result": {"id": -100, "username": "devs"}})
    h.routes[("POST", "/botTOK/sendMessage")] = lambda p, q, b, hd: (sent.append(b) or (200, {"ok": True}))
    s = ChatSession(prof("telegram", h.url, token=ChatProfile.encode("TOK"), channels=["@devs"]))
    s.connect()
    try:
        assert wait_for(lambda: s.connected and "@devs" in s.channels)
        updates.put({"update_id": 5, "message": {"date": 1700000000, "chat": {"id": -100, "username": "devs"},
                                                   "from": {"username": "ana"}, "text": "oi <b>codis</b> me"}})
        assert wait_for(lambda: any(m.nick == "ana" for m in s.channels["@devs"].messages))
        s.select("@devs")
        assert s.send("a < b & c > d")
        assert wait_for(lambda: sent)
        assert sent[0]["text"] == "a < b & c > d" and "parse_mode" not in sent[0] and sent[0]["chat_id"] == "-100"
        assert wait_for(lambda: any(b and b.get("offset") == 6 for _, _, b in h.log))
    finally:
        s.disconnect(); h.close()


def test_telegram_invalid_token_is_fatal():
    h = FakeHTTP()
    h.routes[("POST", "/botBAD/getMe")] = lambda *a: (401, {"ok": False, "description": "Unauthorized"})
    s = ChatSession(prof("telegram", h.url, token=ChatProfile.encode("BAD")))
    s.connect()
    try:
        assert wait_for(lambda: any("token inválido" in m.text for m in s.channels["*server*"].messages))
        time.sleep(0.4)
        assert sum(1 for m, p, _ in h.log if p.endswith("getMe")) == 1
    finally:
        s.disconnect(); h.close()


def test_matrix_login_sync_display_names_send_and_no_self_echo():
    h = FakeHTTP()
    sent = []
    sync_n = [0]

    def sync(path, q, body, hd):
        sync_n[0] += 1
        if sync_n[0] == 1:
            return 200, {"next_batch": "s1", "rooms": {"join": {"!r:x": {
                "state": {"events": [
                    {"type": "m.room.name", "content": {"name": "Dev"}},
                    {"type": "m.room.member", "state_key": "@ana:x", "content": {"membership": "join", "displayname": "Ana"}},
                    {"type": "m.room.member", "state_key": "@me:x", "content": {"membership": "join"}}]},
                "timeline": {"events": [
                    {"type": "m.room.message", "sender": "@ana:x", "origin_server_ts": 1700000000000,
                     "content": {"msgtype": "m.text", "body": "olá me"}},
                    {"type": "m.room.message", "sender": "@ana:x",
                     "content": {"msgtype": "m.image", "body": "foto.png", "url": "mxc://x/abc"}}]}}}}}
        time.sleep(0.2)
        return 200, {"next_batch": f"s{sync_n[0]}", "rooms": {}}

    h.routes[("POST", "/_matrix/client/v3/login")] = lambda p, q, b, hd: (
        200, {"access_token": "T", "user_id": "@me:x"} if b["password"] == "pw" else {})
    h.routes[("GET", "/_matrix/client/v3/sync")] = sync
    h.routes[("POST", "/_matrix/client/v3/join/")] = lambda p, q, b, hd: (200, {"room_id": "!r:x"})
    h.routes[("PUT", "/_matrix/client/v3/rooms/")] = lambda p, q, b, hd: (sent.append((p, b)) or (200, {"event_id": "$1"}))
    s = ChatSession(prof("matrix", h.url, jid="@me:x", password=ChatProfile.encode("pw"), channels=["#dev:x"]))
    s.connect()
    try:
        assert wait_for(lambda: "Dev" in s.channels and len(s.channels["Dev"].messages) >= 3)
        msgs = s.channels["Dev"].messages
        ana = [m for m in msgs if m.nick == "Ana"]
        assert ana[0].text == "olá me" and ana[0].mention and abs(ana[0].ts - 1700000000) < 1
        assert "download/x/abc" in ana[1].text
        assert {n for n, _ in s.members("Dev")} >= {"Ana", "me"}
        s.select("Dev")
        s.send("oi ana")
        s.send("/me ri")
        assert wait_for(lambda: len(sent) == 2)
        assert sent[0][1] == {"msgtype": "m.text", "body": "oi ana"} and sent[1][1]["msgtype"] == "m.emote"
        assert "!r:x" in sent[0][0]
        assert any(m == "POST" and "/join/" in p and b == {} for m, p, b in h.log)
    finally:
        s.disconnect(); h.close()


def test_matrix_wrong_password_fatal_and_bad_token_fatal():
    h = FakeHTTP()
    h.routes[("POST", "/_matrix/client/v3/login")] = lambda *a: (403, {"errcode": "M_FORBIDDEN", "error": "Invalid password"})
    h.routes[("GET", "/_matrix/client/v3/account/whoami")] = lambda *a: (401, {"error": "Unknown token"})
    s = ChatSession(prof("matrix", h.url, jid="u", password=ChatProfile.encode("x")))
    s.connect()
    s2 = ChatSession(prof("matrix", h.url, token=ChatProfile.encode("t")))
    s2.connect()
    try:
        assert wait_for(lambda: any("login falhou" in m.text for m in s.channels["*server*"].messages))
        assert wait_for(lambda: any("token inválido" in m.text for m in s2.channels["*server*"].messages))
        time.sleep(0.3)
        assert sum(1 for m, p, _ in h.log if p.endswith("/login")) == 1
    finally:
        s.disconnect(); s2.disconnect(); h.close()
