"""Cliente MQTT 3.1.1 (stdlib): keepalive, remaining-length completo, auth, wildcards."""

from __future__ import annotations

import contextlib
import json
import secrets
import socket
import threading
import time

from src.chat.adapters.base import Adapter, AdapterFatal
from src.chat.util import mqtt_decode_len, mqtt_encode_len, ssl_context

KEEPALIVE_S = 30
_CONNACK_ERR = {1: "versão do protocolo recusada", 2: "client_id recusado",
                3: "broker indisponível", 4: "usuário/senha inválidos", 5: "não autorizado"}


def _s(text: str) -> bytes:
    b = text.encode("utf-8")
    return len(b).to_bytes(2, "big") + b


class MQTTAdapter(Adapter):
    echoes_self = True
    learn_nicks = False

    def __init__(self, *a, **k) -> None:
        super().__init__(*a, **k)
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()
        self._pid = 0
        self._topics: list[str] = []
        self._client_id = ""

    def _write(self, data: bytes) -> None:
        s = self._sock
        if not s:
            raise ConnectionError("sem conexão")
        with self._lock:
            s.sendall(data)

    def _packet(self, first: int, body: bytes) -> bytes:
        return bytes([first]) + mqtt_encode_len(len(body)) + body

    def _next_pid(self) -> int:
        self._pid = self._pid % 65535 + 1
        return self._pid

    def _session_once(self) -> None:
        p = self.p
        try:
            raw = socket.create_connection((p.host, p.port), timeout=15)
            if p.tls:
                raw = ssl_context(p.verify_tls).wrap_socket(raw, server_hostname=p.host)
        except OSError as exc:
            raise ConnectionError(f"MQTT: {exc}") from exc
        self._sock = raw
        raw.settimeout(KEEPALIVE_S / 2)
        user, pw = p.jid or "", p.decoded_password()
        self._client_id = f"{(p.nick or 'Codis')[:16]}-{secrets.token_hex(3)}"
        flags = 0x02 | (0x80 if user else 0) | (0x40 if (user and pw) else 0)
        body = _s("MQTT") + bytes([4, flags]) + KEEPALIVE_S.to_bytes(2, "big") + _s(self._client_id)
        if user:
            body += _s(user)
            if pw:
                body += _s(pw)
        self._write(self._packet(0x10, body))

        buf = b""
        acked = False
        last_tx = time.monotonic()
        try:
            while not self._stop.is_set():
                try:
                    chunk = raw.recv(8192)
                    if not chunk:
                        raise ConnectionError("broker fechou a conexão")
                    buf += chunk
                except TimeoutError:
                    pass
                buf, acked = self._parse(buf, acked)
                if acked and time.monotonic() - last_tx >= KEEPALIVE_S / 2:
                    self._write(b"\xc0\x00")
                    last_tx = time.monotonic()
        finally:
            with contextlib.suppress(OSError):
                raw.close()
            self._sock = None

    def _parse(self, buf: bytes, acked: bool) -> tuple[bytes, bool]:
        while len(buf) >= 2:
            dec = mqtt_decode_len(buf, 1)
            if dec is None:
                break
            rem, start = dec
            if len(buf) < start + rem:
                break
            first, body, buf = buf[0], buf[start:start + rem], buf[start + rem:]
            ptype = first >> 4
            if ptype == 2:
                rc = body[1] if len(body) >= 2 else 255
                if rc != 0:
                    raise AdapterFatal(f"MQTT: {_CONNACK_ERR.get(rc, f'CONNACK rc={rc}')}")
                acked = True
                self._mark_up(f"MQTT: conectado a {self.p.host}")
                self._topics = []
                for t in self.p.channels:
                    self.join(t)
            elif ptype == 3:
                self._on_publish(first, body)
            elif ptype == 9:
                if body[2:] and body[2] == 0x80:
                    self.ev.error("MQTT: assinatura recusada pelo broker.")
        return buf, acked

    def _on_publish(self, first: int, body: bytes) -> None:
        qos = (first >> 1) & 3
        if len(body) < 2:
            return
        tlen = int.from_bytes(body[:2], "big")
        topic = body[2:2 + tlen].decode("utf-8", errors="replace")
        pos = 2 + tlen
        if qos:
            pid = body[pos:pos + 2]
            pos += 2
            self._write(self._packet(0x40 if qos == 1 else 0x50, pid))
        payload = body[pos:].decode(self.p.encoding or "utf-8", errors="replace")
        nick, text = "mqtt", payload
        if payload.startswith("{"):
            try:
                d = json.loads(payload)
                if isinstance(d, dict) and "msg" in d:
                    nick, text = str(d.get("user") or "mqtt"), str(d["msg"])
            except ValueError:
                pass
        self.ev.message(topic, nick, text, "msg", None)

    def _close(self) -> None:
        s = self._sock
        if s:
            with contextlib.suppress(OSError):
                s.sendall(b"\xe0\x00")
            with contextlib.suppress(OSError):
                s.shutdown(socket.SHUT_RDWR)

    def join(self, channel: str, key: str = "") -> None:
        self._write(self._packet(0x82, self._next_pid().to_bytes(2, "big") + _s(channel) + b"\x00"))
        self._topics.append(channel)
        self.ev.join(channel, self.p.nick)

    def part(self, channel: str, reason: str = "") -> None:
        with contextlib.suppress(Exception):
            self._write(self._packet(0xA2, self._next_pid().to_bytes(2, "big") + _s(channel)))
        self.ev.part(channel, self.current_nick, "")

    def _deliver(self, target: str, text: str) -> None:
        if "+" in target or "#" in target:
            raise ValueError("não é possível publicar em tópico com curinga (+ ou #)")
        payload = (json.dumps({"user": self.p.nick, "msg": text}, ensure_ascii=False)
                   if self.p.x("mqtt_format", "plain") == "json" else text)
        self._write(self._packet(0x30, _s(target) + payload.encode("utf-8")))

    @property
    def current_nick(self) -> str:
        return self.p.nick

    def is_dm(self, name: str) -> bool:
        return False
