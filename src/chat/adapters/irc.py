"""Cliente IRC (RFC 1459/2812 + IRCv3: CAP, SASL PLAIN, server-time, multi-prefix)."""

from __future__ import annotations

import base64
import contextlib
import socket
import threading
import time
from datetime import datetime

from src.chat.adapters.base import SERVER_TAB, Adapter
from src.chat.models import ChatProtocol
from src.chat.util import clean_line, parse_irc_line, ssl_context

_WANT_CAPS = ("server-time", "multi-prefix", "away-notify")
_INFO_NUMERICS = {
    "002", "003", "004", "250", "251", "252", "253", "254", "255", "265", "266",
    "301", "305", "306", "311", "312", "313", "314", "317", "318", "319", "320", "321", "322",
    "323", "324", "329", "330", "341", "352", "315", "367", "368", "369", "378", "379",
    "381", "396",
}
_IDLE_PING_S = 90
_IDLE_DROP_S = 240


def _tag_time(tags: dict[str, str]) -> float | None:
    raw = tags.get("time")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


class IRCAdapter(Adapter):
    max_bytes = 400
    multiline = False

    def __init__(self, *a, **k) -> None:
        super().__init__(*a, **k)
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()
        self._nick = self.p.nick
        self._reset_state()

    def _reset_state(self) -> None:
        self._chantypes = "#&+!"
        self._pfx = "@+"
        self._avail: set[str] = set()
        self._pending_names: dict[str, list[str]] = {}
        self._authed = False
        self._cap_ended = False
        self._nick = self.p.nick

    def _session_once(self) -> None:
        p = self.p
        self._reset_state()
        use_tls = p.proto == ChatProtocol.IRCS or p.tls
        try:
            raw = socket.create_connection((p.host, p.port), timeout=15)
            if use_tls:
                raw = ssl_context(p.verify_tls).wrap_socket(raw, server_hostname=p.host)
        except OSError as exc:
            raise ConnectionError(f"Falha ao conectar: {exc}") from exc
        self._sock = raw
        raw.settimeout(30)
        pw = p.decoded_password()
        self._want_sasl = bool(p.sasl and pw)
        self._raw("CAP LS 302")
        if pw and not p.sasl:
            self._raw(f"PASS {pw}")
        self._raw(f"NICK {p.nick}")
        self._raw(f"USER {clean_line(p.user or 'codis')} 0 * :{clean_line(p.realname or 'Codis')}")

        buf = b""
        last_rx = time.monotonic()
        try:
            while not self._stop.is_set():
                try:
                    chunk = raw.recv(8192)
                except TimeoutError:
                    idle = time.monotonic() - last_rx
                    if idle > _IDLE_DROP_S:
                        raise ConnectionError("tempo esgotado (sem resposta do servidor)") from None
                    if idle > _IDLE_PING_S:
                        self._raw(f"PING :codis-{int(time.time())}")
                    continue
                if not chunk:
                    raise ConnectionError("o servidor fechou a conexão")
                last_rx = time.monotonic()
                buf += chunk
                while True:
                    nl = buf.find(b"\n")
                    if nl < 0:
                        break
                    line, buf = buf[:nl], buf[nl + 1:]
                    text = line.rstrip(b"\r").decode(p.encoding or "utf-8", errors="replace")
                    if text:
                        self.ev.raw(text)
                        self._dispatch(text)
                if len(buf) > 65536:
                    raise ConnectionError("linha IRC grande demais")
        finally:
            with contextlib.suppress(OSError):
                raw.close()
            self._sock = None

    def _close(self) -> None:
        s = self._sock
        if s:
            with contextlib.suppress(OSError):
                self._raw("QUIT :Codis")
            with contextlib.suppress(OSError):
                s.shutdown(socket.SHUT_RDWR)
            with contextlib.suppress(OSError):
                s.close()

    def _raw(self, line: str) -> None:
        s = self._sock
        if not s:
            return
        data = (clean_line(line) + "\r\n").encode(self.p.encoding or "utf-8", errors="replace")
        with self._lock:
            s.sendall(data[:510] + b"\r\n" if len(data) > 512 else data)

    def _is_chan(self, name: str) -> bool:
        return bool(name) and name[0] in self._chantypes

    def _is_me(self, nick: str) -> bool:
        return nick.casefold() == self._nick.casefold()

    def _cap_end(self) -> None:
        if not self._cap_ended:
            self._cap_ended = True
            self._raw("CAP END")

    def _dispatch(self, line: str) -> None:
        tags, prefix, cmd, a = parse_irc_line(line)
        nick = prefix.split("!")[0]
        ts = _tag_time(tags)
        last = a[-1] if a else ""

        if cmd == "PING":
            self._raw(f"PONG :{last}")
        elif cmd == "CAP":
            self._on_cap(a)
        elif cmd == "AUTHENTICATE":
            if a and a[0] == "+":
                p = self.p
                user = p.x("sasl_user") or p.nick
                blob = base64.b64encode(f"{user}\0{user}\0{p.decoded_password()}".encode()).decode()
                for i in range(0, len(blob), 400):
                    self._raw(f"AUTHENTICATE {blob[i:i + 400]}")
                if len(blob) % 400 == 0:
                    self._raw("AUTHENTICATE +")
        elif cmd == "903":
            self._authed = True
            self.ev.info(SERVER_TAB, "SASL: autenticado.")
            self._cap_end()
        elif cmd in ("902", "904", "905", "906", "907"):
            self.ev.error("SASL falhou: verifique nick/senha.")
            self._cap_end()
        elif cmd == "001":
            if a:
                self._nick = a[0]
            self._mark_up(last)
            pw = self.p.decoded_password()
            if pw and self.p.sasl and not self._authed:
                self._raw(f"PRIVMSG NickServ :IDENTIFY {pw}")
            for ch in self.p.channels:
                self.join(ch)
        elif cmd == "005":
            for tok in a[1:-1]:
                if tok.startswith("CHANTYPES="):
                    self._chantypes = tok.split("=", 1)[1] or self._chantypes
                elif tok.startswith("PREFIX=(") and ")" in tok:
                    self._pfx = tok.split(")", 1)[1]
        elif cmd in ("PRIVMSG", "NOTICE") and len(a) >= 2:
            self._on_msg(cmd, prefix, nick, a[0], a[1], ts)
        elif cmd == "JOIN" and a:
            ch = a[0]
            self.ev.join(ch, nick)
        elif cmd == "PART" and a:
            self.ev.part(a[0], nick, a[1] if len(a) > 1 else "")
        elif cmd == "KICK" and len(a) >= 2:
            self.ev.part(a[0], a[1], f"expulso por {nick}" + (f": {a[2]}" if len(a) > 2 else ""))
        elif cmd == "QUIT":
            self.ev.part("*", nick, last if a else "")
        elif cmd == "NICK" and a:
            if self._is_me(nick):
                self._nick = a[0]
            self.ev.nick(nick, a[0])
        elif cmd == "AWAY":
            pass
        elif cmd == "TOPIC" and len(a) >= 2:
            self.ev.topic(a[0], a[1])
        elif cmd == "332" and len(a) >= 3:
            self.ev.topic(a[1], a[2])
        elif cmd == "331" and len(a) >= 2:
            self.ev.topic(a[1], "")
        elif cmd == "353" and len(a) >= 4:
            ch = a[2]
            self._pending_names.setdefault(ch.casefold(), []).extend(
                n.split("!")[0] for n in a[3].split())
        elif cmd == "366" and len(a) >= 2:
            self.ev.names(a[1], self._pending_names.pop(a[1].casefold(), []))
        elif cmd == "MODE" and a and self._is_chan(a[0]) and len(a) > 2:
            if any(c in a[1] for c in "ov") or any(c in a[1] for c in "hqa"):
                self._raw(f"NAMES {a[0]}")
        elif cmd in ("432", "433", "436", "437"):
            if not self._was_up:
                self._nick = (self._nick + "_")[:30]
                self._raw(f"NICK {self._nick}")
                self.ev.info(SERVER_TAB, f"Nick em uso. Tentando {self._nick}.")
            else:
                self.ev.error(" ".join(a[1:]))
        elif cmd == "ERROR":
            raise ConnectionError(last or "ERROR")
        elif cmd in _INFO_NUMERICS:
            self.ev.info(SERVER_TAB, " ".join(a[1:]))
        elif cmd.isdigit() and int(cmd) >= 400:
            self.ev.error(" ".join(a[1:]) or cmd)
        elif cmd in ("372", "375", "376", "422") and a:
            self.ev.info(SERVER_TAB, last)

    def _on_cap(self, a: list[str]) -> None:
        if len(a) < 2:
            return
        sub = a[1].upper()
        if sub == "LS":
            caps = a[-1]
            self._avail |= {c.split("=")[0] for c in caps.split()}
            if len(a) > 3 and a[2] == "*":
                return
            want = [c for c in _WANT_CAPS if c in self._avail]
            if self._want_sasl and "sasl" in self._avail:
                want.append("sasl")
            elif self._want_sasl:
                self.ev.info(SERVER_TAB, "Servidor sem SASL: usando NickServ IDENTIFY.")
            if want:
                self._raw("CAP REQ :" + " ".join(want))
            else:
                self._cap_end()
        elif sub == "ACK":
            if "sasl" in a[-1].split() and self._want_sasl:
                self._raw("AUTHENTICATE PLAIN")
            else:
                self._cap_end()
        elif sub == "NAK":
            self._cap_end()

    def _on_msg(self, cmd: str, prefix: str, nick: str, target: str, text: str, ts: float | None) -> None:
        from_server = "!" not in prefix and ("." in prefix or not prefix)
        if cmd == "NOTICE":
            if text.startswith("\x01"):
                return
            ch = target if self._is_chan(target) else SERVER_TAB
            self.ev.message(ch, nick or prefix, text, "notice", ts)
            return
        if text.startswith("\x01"):
            body = text.strip("\x01")
            word, _, rest = body.partition(" ")
            word = word.upper()
            if word == "ACTION":
                ch = target if self._is_chan(target) else nick
                self.ev.message(ch, nick, rest, "action", ts)
            elif word == "VERSION":
                self._raw(f"NOTICE {nick} :\x01VERSION Codis Chat\x01")
            elif word == "PING":
                self._raw(f"NOTICE {nick} :\x01PING {rest}\x01")
            return
        ch = target if self._is_chan(target) else (SERVER_TAB if from_server else nick)
        self.ev.message(ch, nick or prefix, text, "msg", ts)

    def _deliver(self, target: str, text: str) -> None:
        self._raw(f"PRIVMSG {target} :{text}")

    def _deliver_action(self, target: str, text: str) -> None:
        self._raw(f"PRIVMSG {target} :\x01ACTION {text}\x01")

    def join(self, channel: str, key: str = "") -> None:
        if not self._is_chan(channel):
            channel = "#" + channel
        self._raw(f"JOIN {channel}" + (f" {key}" if key else ""))

    def part(self, channel: str, reason: str = "") -> None:
        if self._is_chan(channel):
            self._raw(f"PART {channel}" + (f" :{reason}" if reason else ""))
        else:
            self.ev.part(channel, self._nick, "")

    def quit(self, reason: str = "Codis") -> None:
        with contextlib.suppress(Exception):
            self._raw(f"QUIT :{reason}")
        super().quit(reason)

    @property
    def current_nick(self) -> str:
        return self._nick

    def is_dm(self, name: str) -> bool:
        return name != SERVER_TAB and not self._is_chan(name)

    _CMDS = ("JOIN PART MSG NICK TOPIC WHO WHOIS WHOWAS MODE KICK NS CS IDENTIFY RAW LIST NAMES "
             "AWAY INVITE NOTICE CTCP OP DEOP VOICE DEVOICE BAN UNBAN").split()

    def commands(self) -> list[str]:
        return [c.lower() for c in self._CMDS]

    def handle_command(self, cmd: str, rest: str, active: str) -> bool:
        first, _, tail = rest.partition(" ")
        tail = tail.strip()
        if cmd == "JOIN":
            if not first:
                return True
            self.join(first, tail)
        elif cmd in ("MSG", "PRIVMSG"):
            if first and tail:
                self.send(first, tail)
                self.ev.own(first, tail, "own")
        elif cmd == "NICK":
            if first:
                self._raw(f"NICK {first}")
        elif cmd == "TOPIC":
            self._raw(f"TOPIC {active}" + (f" :{rest}" if rest else ""))
        elif cmd in ("WHO", "NAMES"):
            self._raw(f"{cmd} {first or active}")
        elif cmd in ("WHOIS", "WHOWAS", "LIST"):
            self._raw(f"{cmd} {first}".strip())
        elif cmd == "MODE":
            self._raw(f"MODE {rest or active}")
        elif cmd == "KICK":
            if first:
                self._raw(f"KICK {active} {first}" + (f" :{tail}" if tail else ""))
        elif cmd in ("NS", "CS"):
            self._raw(f"PRIVMSG {'NickServ' if cmd == 'NS' else 'ChanServ'} :{rest}")
        elif cmd == "IDENTIFY":
            self._raw(f"PRIVMSG NickServ :IDENTIFY {rest}")
            self.ev.info(SERVER_TAB, "IDENTIFY enviado ao NickServ.")
        elif cmd == "RAW":
            self._raw(rest)
        elif cmd == "AWAY":
            self._raw(f"AWAY :{rest}" if rest else "AWAY")
        elif cmd == "INVITE":
            if first:
                self._raw(f"INVITE {first} {tail or active}")
        elif cmd == "NOTICE":
            if first and tail:
                self._raw(f"NOTICE {first} :{tail}")
        elif cmd == "CTCP":
            if first:
                self._raw(f"PRIVMSG {first} :\x01{(tail or 'VERSION').upper()}\x01")
        elif cmd in ("OP", "DEOP", "VOICE", "DEVOICE", "BAN", "UNBAN"):
            mode = {"OP": "+o", "DEOP": "-o", "VOICE": "+v", "DEVOICE": "-v", "BAN": "+b", "UNBAN": "-b"}[cmd]
            if first:
                self._raw(f"MODE {active} {mode} {first}")
        else:
            return False
        return True
