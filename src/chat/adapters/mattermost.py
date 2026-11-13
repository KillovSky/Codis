"""Mattermost API v4: REST + WebSocket de eventos."""

from __future__ import annotations

import json
from typing import Any

from src.chat.adapters.base import AdapterFatal
from src.chat.adapters.http import HTTPFailure, request_json
from src.chat.adapters.ws import HAS_WS, AsyncAdapter, ws_connect
from src.chat.util import split_chars


class MattermostAdapter(AsyncAdapter):
    learn_nicks = True

    def __init__(self, *a: Any, **k: Any) -> None:
        super().__init__(*a, **k)
        self._token = ""
        self._me = ""
        self._me_name = ""
        self._team = ""
        self._label: dict[str, str] = {}
        self._id: dict[str, str] = {}
        self._users: dict[str, str] = {}

    def _base(self) -> str:
        p = self.p
        o = p.x("api_base")
        if o:
            return o.rstrip("/")
        return f"{'https' if p.tls or p.port == 443 else 'http'}://{p.host}:{p.port}"

    def _api(self, method: str, path: str, body=None, params=None, headers=False):
        hdr = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        return request_json(method, self._base() + "/api/v4" + path, headers=hdr, body=body, params=params,
                            verify=self.p.verify_tls, with_headers=headers)

    def _session_once(self) -> None:
        if not HAS_WS:
            raise AdapterFatal("Instale: pip install websockets")
        p = self.p
        self._token = p.decoded_token()
        if not self._token:
            code, user, hdr = self._api("POST", "/users/login",
                                        {"login_id": p.jid or p.user, "password": p.decoded_password()}, headers=True)
            if code in (401, 403):
                raise AdapterFatal(f"Mattermost: login falhou ({user.get('message', code)})")
            if code != 200:
                raise HTTPFailure(code, str(user.get("message", "servidor indisponível")))
            self._token = next((v for k, v in hdr.items() if k.lower() == "token"), "")
            if not self._token:
                raise AdapterFatal("Mattermost: o servidor não devolveu token de sessão.")
        code, me = self._api("GET", "/users/me")
        if code in (401, 403):
            raise AdapterFatal("Mattermost: token inválido ou expirado.")
        if code != 200:
            raise HTTPFailure(code, "falha em /users/me")
        self._me, self._me_name = me["id"], me.get("username", "")
        self._users[self._me] = self._me_name
        _, teams = self._api("GET", "/users/me/teams")
        want = p.x("team")
        teams = teams if isinstance(teams, list) else []
        pick = next((t for t in teams if want and want in (t.get("name"), t.get("display_name"))), None) or (
            teams[0] if teams else {})
        self._team = pick.get("id", "")
        self._label.clear(); self._id.clear()
        if self._team:
            _, chs = self._api("GET", f"/users/me/teams/{self._team}/channels")
            for ch in chs if isinstance(chs, list) else []:
                self._learn_channel(ch)
        super()._session_once()

    def _learn_channel(self, ch: dict) -> str:
        cid, typ = ch.get("id", ""), ch.get("type", "O")
        if typ == "D":
            other = next((u for u in ch.get("name", "").split("__") if u and u != self._me), self._me)
            label = "@" + self._username(other)
        elif typ == "G":
            label = ch.get("display_name") or ch.get("name", cid)
        else:
            label = ch.get("name") or ch.get("display_name") or cid
        self._label[cid] = label
        self._id[label] = cid
        if ch.get("display_name"):
            self._id.setdefault(ch["display_name"], cid)
        return label

    def _username(self, uid: str) -> str:
        if uid not in self._users:
            code, u = self._api("GET", f"/users/{uid}")
            self._users[uid] = u.get("username", uid[:8]) if code == 200 and isinstance(u, dict) else uid[:8]
        return self._users[uid]

    def _chan_label(self, cid: str) -> str:
        if cid not in self._label:
            code, ch = self._api("GET", f"/channels/{cid}")
            if code == 200 and isinstance(ch, dict):
                return self._learn_channel(ch)
            self._label[cid] = cid
        return self._label[cid]

    async def _aio(self) -> None:
        base = self._base()
        url = self.p.x("ws_url") or (
            ("wss" if base.startswith("https") else "ws") + base[base.index("://"):] + "/api/v4/websocket")
        kw: dict[str, Any] = {"open_timeout": 10}
        if url.startswith("wss"):
            from src.chat.util import ssl_context
            kw["ssl"] = ssl_context(self.p.verify_tls)
        async with ws_connect(url, **kw) as ws:
            self._ws = ws
            await ws.send(json.dumps({"seq": 1, "action": "authentication_challenge", "data": {"token": self._token}}))
            async for raw in ws:
                try:
                    ev = json.loads(raw)
                except ValueError:
                    continue
                if ev.get("status") == "FAIL":
                    raise AdapterFatal("Mattermost: autenticação do WebSocket recusada.")
                if ev.get("event") == "hello" or (ev.get("status") == "OK" and not self._connected):
                    if not self._connected:
                        self._mark_up(f"Mattermost: conectado como {self._me_name}")
                        for name in self.p.channels:
                            self.join(name)
                elif ev.get("event") == "posted":
                    self._on_posted(ev)

    def _on_posted(self, ev: dict) -> None:
        data = ev.get("data", {})
        try:
            post = json.loads(data.get("post", "{}"))
        except ValueError:
            return
        if post.get("user_id") == self._me or str(post.get("type", "")).startswith("system_"):
            return
        label = self._chan_label(post.get("channel_id", ""))
        sender = (data.get("sender_name") or "").lstrip("@") or self._username(post.get("user_id", "?"))
        text = post.get("message", "")
        self.ev.message(label, sender, text, "msg", post["create_at"] / 1000 if post.get("create_at") else None)

    def join(self, channel: str, key: str = "") -> None:
        cid = self._id.get(channel) or self._id.get(channel.lstrip("#"))
        if not cid and self._team:
            code, ch = self._api("GET", f"/teams/{self._team}/channels/name/{channel.lstrip('#')}")
            if code == 200:
                cid = ch["id"]
                self._api("POST", f"/channels/{cid}/members", {"user_id": self._me})
                self._learn_channel(ch)
        if not cid:
            self.ev.error(f"Mattermost: canal '{channel}' não encontrado.")
            return
        label = self._label.get(cid, channel)
        self.ev.join(label, self._me_name)
        code, users = self._api("GET", "/users", params={"in_channel": cid, "per_page": 200})
        if code == 200 and isinstance(users, list):
            self.ev.names(label, [u.get("username", "") for u in users])

    def part(self, channel: str, reason: str = "") -> None:
        cid = self._id.get(channel)
        if cid and not channel.startswith("@"):
            self._api("DELETE", f"/channels/{cid}/members/{self._me}")
        self.ev.part(channel, self._me_name, "")

    def _deliver(self, target: str, text: str) -> None:
        cid = self._id.get(target) or target
        for part in split_chars(text, 15000):
            code, resp = self._api("POST", "/posts", {"channel_id": cid, "message": part})
            if code not in (200, 201):
                raise HTTPFailure(code, str(resp.get("message", "falha ao enviar")))

    @property
    def current_nick(self) -> str:
        return self._me_name or self.p.jid or self.p.nick

    def is_dm(self, name: str) -> bool:
        return name.startswith("@")
