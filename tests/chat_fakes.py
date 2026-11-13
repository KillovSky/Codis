"""Servidores falsos (sockets reais em localhost) para testar o chat ponta a ponta."""

from __future__ import annotations

import base64
import socket
import threading
import time


def wait_for(cond, timeout=5.0, step=0.02):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(step)
    return bool(cond())


class FakeIRC:
    """Servidor IRC mínimo: CAP LS/REQ, SASL PLAIN, registro e eco de JOIN."""

    def __init__(self, eol=b"\r\n", caps="sasl server-time multi-prefix", require_sasl=False):
        self.eol, self.caps, self.require_sasl = eol, caps, require_sasl
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.received: list[str] = []
        self.conns: list[socket.socket] = []
        self.nick = "?"
        self.sasl_ok = False
        self._stop = False
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while not self._stop:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            self.conns.append(c)
            threading.Thread(target=self._serve, args=(c,), daemon=True).start()

    def send(self, line: str, conn=None):
        c = conn or (self.conns[-1] if self.conns else None)
        if c:
            try:
                c.sendall(line.encode() + self.eol)
            except OSError:
                pass

    def drop(self):
        for c in self.conns:
            try:
                c.shutdown(socket.SHUT_RDWR)
                c.close()
            except OSError:
                pass

    def close(self):
        self._stop = True
        self.drop()
        self.sock.close()

    def _serve(self, c):
        buf = b""
        registered = False
        in_cap = False
        while True:
            try:
                d = c.recv(4096)
            except OSError:
                return
            if not d:
                return
            buf += d
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = raw.rstrip(b"\r").decode()
                self.received.append(line)
                cmd, _, rest = line.partition(" ")
                if cmd == "CAP":
                    sub = rest.split()[0]
                    if sub == "LS":
                        in_cap = True
                        self.send(f":srv CAP * LS :{self.caps}", c)
                    elif sub == "REQ":
                        self.send(f":srv CAP * ACK :{rest.split(':', 1)[1]}", c)
                    elif sub == "END":
                        in_cap = False
                elif cmd == "NICK":
                    self.nick = rest
                elif cmd == "AUTHENTICATE":
                    if rest == "PLAIN":
                        self.send("AUTHENTICATE +", c)
                    else:
                        self.sasl_ok = len(base64.b64decode(rest).decode().split("\0")) == 3
                        self.send(f":srv 903 {self.nick} :SASL successful", c)
                elif cmd == "JOIN":
                    ch = rest.split()[0]
                    self.send(f":{self.nick}!u@h JOIN {ch}", c)
                    self.send(f":srv 332 {self.nick} {ch} :Topico de teste", c)
                    self.send(f":srv 353 {self.nick} = {ch} :@op +voz {self.nick}", c)
                    self.send(f":srv 366 {self.nick} {ch} :End of NAMES", c)
                elif cmd == "PING":
                    self.send(f":srv PONG srv {rest}", c)
                if not registered and self.nick != "?" and not in_cap and any(
                        x.startswith("USER ") for x in self.received):
                    registered = True
                    self.send(f":srv 001 {self.nick} :Bem-vindo {self.nick}", c)


class FakeMQTT:
    """Broker MQTT 3.1.1 mínimo (CONNECT/SUBSCRIBE/PUBLISH/PINGREQ) com validação estrita."""

    def __init__(self, user=None, password=None):
        from src.chat.util import mqtt_decode_len, mqtt_encode_len
        self._dec, self._enc = mqtt_decode_len, mqtt_encode_len
        self.user, self.password = user, password
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.connects: list[dict] = []
        self.pings = 0
        self.subs: list[str] = []
        self.published: list[tuple[str, bytes]] = []
        self.conns: list[socket.socket] = []
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            self.conns.append(c)
            threading.Thread(target=self._serve, args=(c,), daemon=True).start()

    def publish(self, topic: str, payload: bytes, qos=0):
        t = len(topic.encode()).to_bytes(2, "big") + topic.encode()
        pid = b"\x00\x07" if qos else b""
        body = t + pid + payload
        pkt = bytes([0x30 | (qos << 1)]) + self._enc(len(body)) + body
        for c in self.conns:
            c.sendall(pkt)

    def close(self):
        for c in self.conns:
            try:
                c.close()
            except OSError:
                pass
        self.sock.close()

    def _serve(self, c):
        buf = b""
        while True:
            try:
                d = c.recv(4096)
            except OSError:
                return
            if not d:
                return
            buf += d
            while len(buf) >= 2:
                dec = self._dec(buf, 1)
                if not dec or len(buf) < dec[1] + dec[0]:
                    break
                rem, start = dec
                first, body, buf = buf[0], buf[start:start + rem], buf[start + rem:]
                t = first >> 4
                if t == 1:
                    flags = body[7]
                    pos = 10
                    cid_len = int.from_bytes(body[pos:pos + 2], "big"); pos += 2
                    cid = body[pos:pos + cid_len].decode(); pos += cid_len
                    user = pw = None
                    if flags & 0x80:
                        ul = int.from_bytes(body[pos:pos + 2], "big"); pos += 2
                        user = body[pos:pos + ul].decode(); pos += ul
                    if flags & 0x40:
                        pl = int.from_bytes(body[pos:pos + 2], "big"); pos += 2
                        pw = body[pos:pos + pl].decode(); pos += pl
                    keep = int.from_bytes(body[8:10], "big")
                    self.connects.append({"cid": cid, "user": user, "pw": pw, "flags": flags, "keep": keep,
                                          "clean_tail": pos == len(body)})
                    ok = self.user is None or (user == self.user and pw == self.password)
                    c.sendall(bytes([0x20, 2, 0, 0 if ok else 4]))
                elif t == 8:
                    pid = body[:2]
                    tl = int.from_bytes(body[2:4], "big")
                    self.subs.append(body[4:4 + tl].decode())
                    c.sendall(bytes([0x90, 3]) + pid + b"\x00")
                elif t == 3:
                    tl = int.from_bytes(body[:2], "big")
                    self.published.append((body[2:2 + tl].decode(), body[2 + tl:]))
                elif t == 12:
                    self.pings += 1
                    c.sendall(b"\xd0\x00")


class FakeHTTP:
    """Servidor HTTP/JSON local. ``routes``: (METHOD, prefixo) -> fn(path, query, body) -> (status, obj)."""

    def __init__(self):
        import http.server
        import json
        import urllib.parse
        outer = self
        self.routes: dict[tuple[str, str], object] = {}
        self.log: list[tuple[str, str, object]] = []

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _do(self):
                u = urllib.parse.urlparse(self.path)
                q = dict(urllib.parse.parse_qsl(u.query))
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                try:
                    body = json.loads(raw) if raw else None
                except ValueError:
                    body = raw
                path = urllib.parse.unquote(u.path)
                outer.log.append((self.command, path, body))
                for (m, pre), fn in sorted(outer.routes.items(), key=lambda kv: -len(kv[0][1])):
                    if m == self.command and path.startswith(pre):
                        res = fn(path, q, body, dict(self.headers))
                        break
                else:
                    res = (404, {"error": "no route " + path})
                status, obj = res
                data = json.dumps(obj).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PUT = _do

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class FakeWS:
    """Servidor WebSocket local; ``handler(ws)`` é uma corrotina por conexão."""

    def __init__(self, handler):
        import asyncio
        from websockets.asyncio.server import serve
        self.loop = asyncio.new_event_loop()
        self.conns = 0
        ready = threading.Event()

        async def main():
            async def wrap(ws):
                self.conns += 1
                await handler(ws)
            self.srv = await serve(wrap, "127.0.0.1", 0)
            self.port = self.srv.sockets[0].getsockname()[1]
            ready.set()
            await self.srv.wait_closed()

        threading.Thread(target=lambda: self.loop.run_until_complete(main()), daemon=True).start()
        assert ready.wait(5)
        self.url = f"ws://127.0.0.1:{self.port}"

    def close(self):
        self.loop.call_soon_threadsafe(self.srv.close)


class FakeXMPP:
    """Servidor XMPP mínimo (sem TLS): SASL PLAIN, bind, MUC com 2 ocupantes, eco e fragmentação TCP."""

    def __init__(self, password="pw", fragment=False):
        self.password, self.fragment = password, fragment
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(3)
        self.port = self.sock.getsockname()[1]
        self.received: list[str] = []
        self.conns: list[socket.socket] = []
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            self.conns.append(c)
            threading.Thread(target=self._serve, args=(c,), daemon=True).start()

    def _out(self, c, xml: str):
        data = xml.encode()
        if self.fragment:
            for i in range(0, len(data), 7):
                c.sendall(data[i:i + 7])
                time.sleep(0.001)
        else:
            c.sendall(data)

    HDR = ('<?xml version="1.0"?><stream:stream from="ex.org" id="s1" version="1.0" xmlns="jabber:client" '
           'xmlns:stream="http://etherx.jabber.org/streams">')

    def push(self, xml: str):
        self._out(self.conns[-1], xml)

    def _serve(self, c):
        import base64
        import re
        buf = ""
        while True:
            try:
                d = c.recv(4096)
            except OSError:
                return
            if not d:
                return
            buf += d.decode()
            while True:
                m = re.match(r"\s*(<\?xml[^>]*\?>)?\s*<stream:stream[^>]*>", buf)
                if m:
                    buf = buf[m.end():]
                    self.received.append("STREAM")
                    feats = ('<stream:features><bind xmlns="urn:ietf:params:xml:ns:xmpp-bind"/></stream:features>'
                             if any("AUTH" == r for r in self.received) else
                             '<stream:features><mechanisms xmlns="urn:ietf:params:xml:ns:xmpp-sasl">'
                             '<mechanism>PLAIN</mechanism></mechanisms></stream:features>')
                    self._out(c, self.HDR + feats)
                    continue
                m = re.match(r"\s*<auth [^>]*>([^<]*)</auth>", buf)
                if m:
                    buf = buf[m.end():]
                    ok = base64.b64decode(m.group(1)) == f"\0ana\0{self.password}".encode()
                    self.received.append("AUTH")
                    self._out(c, '<success xmlns="urn:ietf:params:xml:ns:xmpp-sasl"/>' if ok else
                              '<failure xmlns="urn:ietf:params:xml:ns:xmpp-sasl"><not-authorized/></failure>')
                    continue
                m = re.match(r"\s*<iq [^>]*id=\"bind1\".*?</iq>", buf, re.S)
                if m:
                    buf = buf[m.end():]
                    self._out(c, '<iq type="result" id="bind1"><bind xmlns="urn:ietf:params:xml:ns:xmpp-bind">'
                                 '<jid>ana@ex.org/Codis</jid></bind></iq>')
                    continue
                m = re.match(r"\s*<presence to=[\"']([^\"']*)/([^\"']*)[\"'][^>]*>.*?</presence>", buf, re.S)
                if m:
                    buf = buf[m.end():]
                    room, nick = m.group(1), m.group(2)
                    self.received.append(f"JOIN {room}/{nick}")
                    mu = "http://jabber.org/protocol/muc#user"
                    self._out(c, f'<presence from="{room}/beto" xmlns="jabber:client"><x xmlns="{mu}"/></presence>'
                                 f'<presence from="{room}/{nick}"><x xmlns="{mu}"><status code="110"/></x></presence>'
                                 f'<message from="{room}" type="groupchat"><subject>Sala &amp; teste</subject></message>'
                                 f'<message from="{room}/beto" type="groupchat"><body>oi ana &lt;3 &amp; tchau</body></message>')
                    continue
                m = re.match(r"\s*<message [^>]*>.*?</message>", buf, re.S)
                if m:
                    self.received.append(m.group(0))
                    buf = buf[m.end():]
                    continue
                m = re.match(r"\s*<presence/>", buf)
                if m:
                    buf = buf[m.end():]
                    continue
                break
