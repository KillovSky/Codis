"""Matrix Client-Server API v3 (long-poll /sync, stdlib)."""

from __future__ import annotations

import json
import urllib.parse
import uuid

from src.chat.adapters.base import SERVER_TAB, Adapter, AdapterFatal
from src.chat.adapters.http import HTTPFailure, request_json
from src.chat.util import split_chars

_FILTER = json.dumps({"room": {"timeline": {"limit": 30}, "state": {"lazy_load_members": True}},
                      "presence": {"types": []}, "account_data": {"types": []}})


class MatrixAdapter(Adapter):
    echoes_self = False
    learn_nicks = True

    def __init__(self, *a, **k) -> None:
        super().__init__(*a, **k)
        self._token = ""
        self._user_id = ""
        self._since = ""
        self._room_by_name: dict[str, str] = {}
        self._name_by_room: dict[str, str] = {}
        self._display: dict[str, str] = {}
        self._txns: set[str] = set()
        self._announced: set[str] = set()

    def _base(self) -> str:
        p = self.p
        override = p.x("api_base")
        if override:
            return override.rstrip("/")
        scheme = "https" if p.tls or p.port == 443 else "http"
        return f"{scheme}://{p.host}:{p.port}"

    def _api(self, method: str, path: str, body=None, params=None, timeout: float = 20):
        hdr = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        if method in ("POST", "PUT") and body is None:
            body = {}
        return request_json(method, self._base() + "/_matrix/client/v3" + path, headers=hdr,
                            body=body, params=params, timeout=timeout, verify=self.p.verify_tls)

    @staticmethod
    def _q(s: str) -> str:
        return urllib.parse.quote(s, safe="")

    def _login(self) -> None:
        p = self.p
        tok = p.decoded_token()
        if tok:
            self._token = tok
            code, who = self._api("GET", "/account/whoami")
            if code != 200:
                raise AdapterFatal(f"Matrix: token inválido ({who.get('error', code) if isinstance(who, dict) else code})")
            self._user_id = who.get("user_id", p.jid)
            return
        user = (p.jid or p.user).strip()
        code, resp = self._api("POST", "/login", {
            "type": "m.login.password", "identifier": {"type": "m.id.user", "user": user},
            "password": p.decoded_password(), "initial_device_display_name": "Codis"})
        if code == -1 or code >= 500:
            raise HTTPFailure(code, str(resp.get("error", "servidor indisponível")))
        if code != 200:
            raise AdapterFatal(f"Matrix: login falhou ({resp.get('error', code)})")
        self._token, self._user_id = resp["access_token"], resp["user_id"]

    def _session_once(self) -> None:
        self._token = ""
        self._login()
        self._since = ""
        self._mark_up(f"Matrix: conectado como {self._user_id}")
        for alias in self.p.channels:
            self.join(alias)
        fails = 0
        while not self._stop.is_set():
            params = {"filter": _FILTER, "timeout": 30000 if self._since else 0}
            if self._since:
                params["since"] = self._since
            code, resp = self._api("GET", "/sync", params=params, timeout=45)
            if code == 401:
                raise AdapterFatal("Matrix: sessão expirada ou token revogado.")
            if code != 200 or not isinstance(resp, dict):
                fails += 1
                if fails >= 3:
                    raise HTTPFailure(code, "sync falhando")
                self._stop.wait(2 * fails)
                continue
            fails = 0
            first = not self._since
            self._since = resp.get("next_batch", self._since)
            self._handle_sync(resp, first)

    def _room_label(self, rid: str, state: list[dict]) -> str:
        alias = name = ""
        for ev in state:
            t, c = ev.get("type"), ev.get("content", {})
            if t == "m.room.canonical_alias":
                alias = c.get("alias", "") or alias
            elif t == "m.room.name":
                name = c.get("name", "") or name
            elif t == "m.room.member" and c.get("displayname"):
                self._display[ev.get("state_key", "")] = c["displayname"]
        label = self._name_by_room.get(rid)
        if not label or alias or name:
            label = name or alias or label or rid
            for k in [k for k, v in self._room_by_name.items() if v == rid and k != label and k not in self.p.channels]:
                del self._room_by_name[k]
            self._name_by_room[rid] = label
            self._room_by_name[label] = rid
        return label

    def _handle_sync(self, resp: dict, first: bool) -> None:
        for rid, data in resp.get("rooms", {}).get("join", {}).items():
            state = data.get("state", {}).get("events", [])
            events = data.get("timeline", {}).get("events", [])
            label = self._room_label(rid, state + [e for e in events if "state_key" in e])
            if rid not in self._announced:
                self._announced.add(rid)
                self.ev.join(label, self._user_id)
            joined = [self._display.get(e["state_key"]) or e["state_key"].split(":")[0].lstrip("@")
                      for e in state if e.get("type") == "m.room.member"
                      and e.get("content", {}).get("membership") == "join"]
            if joined:
                self.ev.names(label, joined)
            for ev in events:
                self._on_event(label, ev)
        for rid in resp.get("rooms", {}).get("invite", {}):
            self.ev.info(SERVER_TAB, f"Convite para {rid}: use /join {rid}")

    def _nick_of(self, sender: str) -> str:
        return self._display.get(sender) or sender.split(":")[0].lstrip("@")

    def _on_event(self, label: str, ev: dict) -> None:
        t, c, sender = ev.get("type"), ev.get("content", {}), ev.get("sender", "?")
        ts = ev["origin_server_ts"] / 1000 if "origin_server_ts" in ev else None
        if t == "m.room.topic":
            self.ev.topic(label, c.get("topic", ""))
        elif t == "m.room.member":
            if c.get("displayname"):
                self._display[ev.get("state_key", "")] = c["displayname"]
            nick = self._nick_of(ev.get("state_key", sender))
            if ev.get("state_key") == self._user_id:
                return
            if c.get("membership") == "join" and ev.get("unsigned", {}).get("prev_content", {}).get("membership") != "join":
                self.ev.join(label, nick)
            elif c.get("membership") in ("leave", "ban"):
                self.ev.part(label, nick, c.get("reason", ""))
        elif t == "m.room.message":
            if ev.get("unsigned", {}).get("transaction_id") in self._txns:
                return
            kind = {"m.emote": "action", "m.notice": "notice"}.get(c.get("msgtype"), "msg")
            body = c.get("body", "")
            mt = c.get("msgtype", "")
            if mt in ("m.image", "m.file", "m.video", "m.audio") and str(c.get("url", "")).startswith("mxc://"):
                srv_media = c["url"][6:]
                body = f"[{mt[2:]}] {body} {self._base()}/_matrix/media/v3/download/{srv_media}"
            if c.get("m.relates_to", {}).get("rel_type") == "m.replace":
                return
            self.ev.message(label, self._nick_of(sender), body, kind, ts)

    def _resolve(self, alias: str) -> str:
        if alias.startswith("!"):
            return alias
        code, resp = self._api("GET", f"/directory/room/{self._q(alias)}")
        return resp.get("room_id", "") if code == 200 else ""

    def join(self, channel: str, key: str = "") -> None:
        target = channel if channel.startswith(("!", "#")) else "#" + channel
        code, resp = self._api("POST", f"/join/{self._q(target)}")
        if code != 200:
            self.ev.error(f"Matrix: não consegui entrar em {channel} ({resp.get('error', code)})")
            return
        rid = resp.get("room_id", "")
        self._room_by_name.setdefault(channel, rid)
        self._name_by_room.setdefault(rid, channel)

    def part(self, channel: str, reason: str = "") -> None:
        rid = self._room_by_name.get(channel, channel)
        self._api("POST", f"/rooms/{self._q(rid)}/leave")
        self._announced.discard(rid)
        self.ev.part(channel, self._user_id.split(":")[0].lstrip("@") or self.p.nick, "")

    def _put(self, target: str, content: dict) -> None:
        rid = self._room_by_name.get(target, target)
        txn = uuid.uuid4().hex
        self._txns.add(txn)
        if len(self._txns) > 500:
            self._txns = set(list(self._txns)[-200:])
        code, resp = self._api("PUT", f"/rooms/{self._q(rid)}/send/m.room.message/{txn}", content)
        if code != 200:
            raise HTTPFailure(code, str(resp.get("error", "falha ao enviar")))

    def _deliver(self, target: str, text: str) -> None:
        for part in split_chars(text, 30000):
            self._put(target, {"msgtype": "m.text", "body": part})

    def _deliver_action(self, target: str, text: str) -> None:
        self._put(target, {"msgtype": "m.emote", "body": text})

    @property
    def current_nick(self) -> str:
        return self._display.get(self._user_id) or self._user_id.split(":")[0].lstrip("@") or self.p.nick

    def is_dm(self, name: str) -> bool:
        return False

    def handle_command(self, cmd: str, rest: str, active: str) -> bool:
        if cmd == "TOPIC" and rest:
            rid = self._room_by_name.get(active, active)
            self._api("PUT", f"/rooms/{self._q(rid)}/state/m.room.topic", {"topic": rest})
            return True
        return False

    def commands(self) -> list[str]:
        return ["topic"]
