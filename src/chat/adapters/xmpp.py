"""Cliente XMPP/Jabber (RFC 6120/6121 + MUC XEP-0045) com parser XML incremental."""

from __future__ import annotations

import base64
import contextlib
import socket
import threading
import xml.etree.ElementTree as ET
from datetime import datetime
from xml.sax.saxutils import escape, quoteattr

from src.chat.adapters.base import Adapter, AdapterFatal
from src.chat.models import ChatProtocol
from src.chat.util import ssl_context

NS_STREAM = "http://etherx.jabber.org/streams"
NS_TLS = "urn:ietf:params:xml:ns:xmpp-tls"
NS_SASL = "urn:ietf:params:xml:ns:xmpp-sasl"
NS_BIND = "urn:ietf:params:xml:ns:xmpp-bind"
NS_MUC = "http://jabber.org/protocol/muc"
NS_MUCU = "http://jabber.org/protocol/muc#user"
C = "{jabber:client}"


def _x(ns: str, tag: str) -> str:
    return f"{{{ns}}}{tag}"


def _flush(parser: ET.XMLPullParser) -> None:
    """Força o Expat a processar já os bytes recebidos.

    Versões recentes do Expat adiam o reparse de dados parciais; num stream XMPP
    o servidor aguarda o cliente, então o fim de um stanza nunca seria entregue.
    """
    flush = getattr(parser, "flush", None)
    if flush is not None:
        flush()


class _Restart(Exception):
    """Sinaliza que o stream XML precisa ser reiniciado (após TLS ou SASL)."""


class XMPPAdapter(Adapter):
    echoes_self = False
    learn_nicks = False

    def __init__(self, *a, **k) -> None:
        super().__init__(*a, **k)
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()
        self._n = 0
        self._reset()

    def _reset(self) -> None:
        self._tls = False
        self._authed = False
        self._bound = False
        self._full_jid = ""
        self._rooms: dict[str, str] = {}
        self._joined: set[str] = set()
        self._occ: dict[str, list[str]] = {}

    @property
    def _local(self) -> str:
        return (self.p.jid or "").split("@")[0] or self.p.nick

    @property
    def _domain(self) -> str:
        return (self.p.jid or "").split("@", 1)[-1].split("/")[0]

    def _id(self) -> str:
        self._n += 1
        return f"codis{self._n}"

    def _send_raw(self, xml: str) -> None:
        s = self._sock
        if not s:
            raise ConnectionError("sem conexão")
        with self._lock:
            s.sendall(xml.encode("utf-8"))

    def _open_stream(self) -> None:
        self._send_raw(f'<?xml version="1.0"?><stream:stream to={quoteattr(self._domain)} xmlns="jabber:client" '
                       f'xmlns:stream="{NS_STREAM}" version="1.0">')

    def _session_once(self) -> None:
        p = self.p
        if "@" not in (p.jid or ""):
            raise AdapterFatal("XMPP: informe o JID completo (usuario@dominio).")
        self._reset()
        host = p.host or self._domain
        try:
            raw = socket.create_connection((host, p.port), timeout=15)
            if p.proto == ChatProtocol.XMPPS:
                raw = ssl_context(p.verify_tls).wrap_socket(raw, server_hostname=self._domain)
                self._tls = True
        except OSError as exc:
            raise ConnectionError(f"XMPP: {exc}") from exc
        self._sock = raw
        raw.settimeout(30)
        try:
            self._open_stream()
            self._read_loop()
        finally:
            with contextlib.suppress(OSError):
                raw.close()
            self._sock = None

    def _read_loop(self) -> None:
        raw = self._sock
        parser = ET.XMLPullParser(("start", "end"))
        depth = 0
        while not self._stop.is_set():
            try:
                chunk = raw.recv(8192)
            except TimeoutError:
                self._send_raw(" ")
                continue
            if not chunk:
                raise ConnectionError("o servidor fechou a conexão")
            self.ev.raw(chunk[:200].decode("utf-8", "replace"))
            try:
                parser.feed(chunk)
                _flush(parser)
                for event, el in parser.read_events():
                    if event == "start":
                        depth += 1
                        continue
                    depth -= 1
                    if depth == 0:
                        raise ConnectionError("o servidor encerrou o stream")
                    if depth == 1:
                        try:
                            self._stanza(el)
                        except _Restart:
                            parser = ET.XMLPullParser(("start", "end"))
                            depth = 0
                            raw = self._sock
                            self._open_stream()
                            break
                        finally:
                            el.clear()
            except ET.ParseError as exc:
                raise ConnectionError(f"XML inválido do servidor: {exc}") from exc

    def _close(self) -> None:
        s = self._sock
        if s:
            with contextlib.suppress(Exception):
                self._send_raw("</stream:stream>")
            with contextlib.suppress(OSError):
                s.shutdown(socket.SHUT_RDWR)

    def _stanza(self, el: ET.Element) -> None:
        tag = el.tag
        if tag == _x(NS_STREAM, "features"):
            self._features(el)
        elif tag == _x(NS_TLS, "proceed"):
            self._sock = ssl_context(self.p.verify_tls).wrap_socket(self._sock, server_hostname=self._domain)
            self._sock.settimeout(30)
            self._tls = True
            raise _Restart
        elif tag == _x(NS_TLS, "failure"):
            raise AdapterFatal("XMPP: o servidor recusou STARTTLS.")
        elif tag == _x(NS_SASL, "success"):
            self._authed = True
            raise _Restart
        elif tag == _x(NS_SASL, "failure"):
            raise AdapterFatal("XMPP: autenticação falhou (usuário ou senha incorretos).")
        elif tag == C + "iq":
            self._iq(el)
        elif tag == C + "message":
            self._message(el)
        elif tag == C + "presence":
            self._presence(el)
        elif tag == _x(NS_STREAM, "error"):
            cond = next((c.tag.split("}")[-1] for c in el if "xmpp-streams" in c.tag), "erro de stream")
            raise ConnectionError(f"XMPP: {cond}")

    def _features(self, el: ET.Element) -> None:
        p = self.p
        if not self._tls and el.find(_x(NS_TLS, "starttls")) is not None and p.tls:
            self._send_raw(f"<starttls xmlns='{NS_TLS}'/>")
            return
        if not self._tls and p.tls and p.proto == ChatProtocol.XMPP:
            raise AdapterFatal("XMPP: o servidor não oferece STARTTLS (desmarque TLS para usar sem criptografia).")
        mech = el.find(_x(NS_SASL, "mechanisms"))
        if mech is not None and not self._authed:
            names = {m.text for m in mech}
            if "PLAIN" in names and p.decoded_password():
                blob = base64.b64encode(f"\0{self._local}\0{p.decoded_password()}".encode()).decode()
                self._send_raw(f'<auth xmlns="{NS_SASL}" mechanism="PLAIN">{blob}</auth>')
            elif "ANONYMOUS" in names and not p.decoded_password():
                self._send_raw(f'<auth xmlns="{NS_SASL}" mechanism="ANONYMOUS"/>')
            else:
                raise AdapterFatal(f"XMPP: mecanismos do servidor ({', '.join(sorted(n for n in names if n))}) "
                                   "não suportados (use PLAIN com senha).")
            return
        if self._authed and not self._bound and el.find(_x(NS_BIND, "bind")) is not None:
            self._send_raw(f'<iq type="set" id="bind1"><bind xmlns="{NS_BIND}">'
                           f"<resource>Codis</resource></bind></iq>")

    def _iq(self, el: ET.Element) -> None:
        typ, frm, iid = el.get("type"), el.get("from"), el.get("id", "")
        if el.get("id") == "bind1" and typ == "result":
            jid = el.findtext(f"{_x(NS_BIND, 'bind')}/{_x(NS_BIND, 'jid')}") or self.p.jid
            self._full_jid, self._bound = jid, True
            self._mark_up(f"XMPP: conectado como {jid}")
            self._send_raw("<presence/>")
            for room in self.p.channels:
                self.join(room)
        elif el.get("id") == "bind1" and typ == "error":
            raise AdapterFatal("XMPP: o servidor recusou o vínculo de recurso.")
        elif typ == "get":
            to = f" to={quoteattr(frm)}" if frm else ""
            if el.find("{urn:xmpp:ping}ping") is not None:
                self._send_raw(f'<iq type="result" id={quoteattr(iid)}{to}/>')
            elif el.find("{jabber:iq:version}query") is not None:
                self._send_raw(f'<iq type="result" id={quoteattr(iid)}{to}><query xmlns="jabber:iq:version">'
                               "<name>Codis Chat</name><version>1</version></query></iq>")
            else:
                self._send_raw(f'<iq type="error" id={quoteattr(iid)}{to}><error type="cancel">'
                               '<service-unavailable xmlns="urn:ietf:params:xml:ns:xmpp-stanzas"/></error></iq>')

    def _message(self, el: ET.Element) -> None:
        frm, typ = el.get("from", ""), el.get("type", "normal")
        bare, _, res = frm.partition("/")
        body = el.findtext(C + "body")
        if typ == "error":
            cond = next((c.tag.split("}")[-1] for c in (el.find(C + "error") or []) if "stanzas" in c.tag), "erro")
            self.ev.error(f"XMPP: {bare}: {cond}")
            return
        ts = None
        d = el.find("{urn:xmpp:delay}delay")
        if d is not None and d.get("stamp"):
            with contextlib.suppress(ValueError):
                ts = datetime.fromisoformat(d.get("stamp", "").replace("Z", "+00:00")).timestamp()
        if typ == "groupchat":
            subj = el.find(C + "subject")
            if subj is not None and not body:
                self.ev.topic(bare, subj.text or "")
                return
            if not body:
                return
            if res and res == self._rooms.get(bare) and ts is None:
                return
            self._emit(bare, res or "*", body, ts)
        elif body:
            self._emit(bare, bare.split("@")[0], body, ts)

    def _emit(self, channel: str, nick: str, body: str, ts: float | None) -> None:
        if body.startswith("/me "):
            self.ev.message(channel, nick, body[4:], "action", ts)
        else:
            self.ev.message(channel, nick, body, "msg", ts)

    def _presence(self, el: ET.Element) -> None:
        frm, typ = el.get("from", ""), el.get("type")
        bare, _, res = frm.partition("/")
        if bare not in self._rooms:
            return
        x = el.find(_x(NS_MUCU, "x"))
        codes = {s.get("code") for s in x.findall(_x(NS_MUCU, "status"))} if x is not None else set()
        if typ == "error":
            cond = next((c.tag.split("}")[-1] for c in (el.find(C + "error") or []) if "stanzas" in c.tag), "erro")
            if cond == "conflict":
                self._rooms[bare] = self._rooms[bare] + "_"
                self._send_join(bare)
                return
            self._rooms.pop(bare, None)
            self.ev.error(f"XMPP: não foi possível entrar em {bare} ({cond}).")
        elif typ == "unavailable":
            if "110" in codes:
                self._joined.discard(bare)
                self.ev.part(bare, self._rooms.get(bare, self._local), "")
            elif bare in self._joined:
                self.ev.part(bare, res, "")
        elif "110" in codes:
            self._joined.add(bare)
            self._rooms[bare] = res or self._rooms[bare]
            self.ev.join(bare, self._rooms[bare])
            self.ev.names(bare, self._occ.pop(bare, []) + [self._rooms[bare]])
        elif bare in self._joined:
            self.ev.join(bare, res)
        else:
            self._occ.setdefault(bare, []).append(res)

    def _send_join(self, room: str, key: str = "") -> None:
        pw = f"<password>{escape(key)}</password>" if key else ""
        self._send_raw(f"<presence to={quoteattr(room + '/' + self._rooms[room])}><x xmlns='{NS_MUC}'>"
                       f"<history maxstanzas='20'/>{pw}</x></presence>")

    def join(self, channel: str, key: str = "") -> None:
        room = channel if "@" in channel else f"{channel}@conference.{self._domain}"
        self._rooms[room] = self.p.x("muc_nick") or self._local
        self._occ[room] = []
        self._send_join(room, key)

    def part(self, channel: str, reason: str = "") -> None:
        if channel in self._rooms:
            self._send_raw(f"<presence to={quoteattr(channel + '/' + self._rooms[channel])} type='unavailable'/>")
            self._rooms.pop(channel, None)
            self._joined.discard(channel)
        self.ev.part(channel, self._local, "")

    def _msg(self, target: str, body: str) -> None:
        typ = "groupchat" if target in self._rooms else "chat"
        self._send_raw(f"<message to={quoteattr(target)} type='{typ}' id={quoteattr(self._id())}>"
                       f"<body>{escape(body)}</body></message>")

    def _deliver(self, target: str, text: str) -> None:
        self._msg(target, text)

    def _deliver_action(self, target: str, text: str) -> None:
        self._msg(target, "/me " + text)

    @property
    def current_nick(self) -> str:
        return self._local

    def is_dm(self, name: str) -> bool:
        return name not in self._rooms

    def handle_command(self, cmd: str, rest: str, active: str) -> bool:
        if cmd == "TOPIC" and active in self._rooms:
            self._send_raw(f"<message to={quoteattr(active)} type='groupchat'><subject>{escape(rest)}</subject></message>")
            return True
        if cmd == "NICK" and rest and active in self._rooms:
            self._rooms[active] = rest.split()[0]
            self._send_join(active)
            return True
        return False

    def commands(self) -> list[str]:
        return ["topic", "nick"]
