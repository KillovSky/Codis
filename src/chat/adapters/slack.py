"""Slack: Socket Mode (xapp-), RTM (apps clássicos) ou polling (fallback universal)."""

from __future__ import annotations

import json
import re
import time
from typing import Any

from src.chat.adapters.base import AdapterFatal
from src.chat.adapters.http import HTTPFailure, request_json
from src.chat.adapters.ws import HAS_WS, AsyncAdapter, ws_connect
from src.chat.util import split_chars

_FATAL = {"invalid_auth", "not_authed", "token_revoked", "account_inactive", "token_expired", "invalid_token"}
_SHOW_SUBTYPES = {None, "me_message", "bot_message", "thread_broadcast", "file_share"}


def slack_unescape(text: str, user_name=lambda uid: uid) -> str:
    text = re.sub(r"<@([UW][A-Z0-9]+)(?:\|[^>]*)?>", lambda m: "@" + user_name(m.group(1)), text)
    text = re.sub(r"<#[A-Z0-9]+\|([^>]+)>", r"#\1", text)
    text = re.sub(r"<!(here|channel|everyone)[^>]*>", r"@\1", text)
    text = re.sub(r"<(https?://[^|>]+)\|([^>]+)>", r"\2 (\1)", text)
    text = re.sub(r"<((?:https?|mailto):[^>]+)>", r"\1", text)
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


class SlackAdapter(AsyncAdapter):
    learn_nicks = True

    def __init__(self, *a: Any, **k: Any) -> None:
        super().__init__(*a, **k)
        self._uid = ""
        self._me = ""
        self._chan_name: dict[str, str] = {}
        self._chan_id: dict[str, str] = {}
        self._users: dict[str, str] = {}
        self.poll_interval = 4.0

    def _base(self) -> str:
        return self.p.x("api_base", "https://slack.com").rstrip("/")

    def _api(self, method: str, params: dict | None = None, token: str | None = None) -> dict:
        status, data = request_json("POST", f"{self._base()}/api/{method}",
                                    headers={"Authorization": f"Bearer {token or self.p.decoded_token()}"},
                                    body=params or {}, verify=self.p.verify_tls)
        if not isinstance(data, dict) or status >= 500 or status == -1:
            raise HTTPFailure(status, f"Slack {method}")
        return data

    def _check(self, data: dict, what: str) -> dict:
        if not data.get("ok"):
            err = data.get("error", "erro")
            if err in _FATAL:
                raise AdapterFatal(f"Slack: {err} ({what}) - confira o token.")
            raise HTTPFailure(0, f"Slack {what}: {err}")
        return data

    def _session_once(self) -> None:
        auth = self._check(self._api("auth.test"), "auth.test")
        self._uid, self._me = auth.get("user_id", ""), auth.get("user", "")
        self._users[self._uid] = self._me
        self._chan_name.clear(); self._chan_id.clear()
        cursor = ""
        while True:
            data = self._api("conversations.list", {"types": "public_channel,private_channel",
                                                    "limit": 200, "exclude_archived": True, "cursor": cursor})
            if not data.get("ok"):
                break
            for c in data.get("channels", []):
                self._remember(c["id"], c.get("name", c["id"]))
            cursor = data.get("response_metadata", {}).get("next_cursor", "")
            if not cursor:
                break
        self._mark_up(f"Slack: conectado como {self._me}")
        for ch in self.p.channels:
            self.join(ch)
        app = self.p.x("app_token")
        url, enveloped = "", False
        if app:
            d = self._check(self._api("apps.connections.open", token=app), "apps.connections.open")
            url, enveloped = d["url"], True
        else:
            d = self._api("rtm.connect")
            if d.get("ok"):
                url = d["url"]
            else:
                self.ev.info("*server*", f"Slack: RTM indisponível ({d.get('error')}); usando polling a cada "
                                         f"{int(self.poll_interval)}s. Para tempo real use um App Token (xapp-).")
        if url and HAS_WS:
            self._url, self._env = url, enveloped
            super()._session_once()
        else:
            self._poll()

    def _remember(self, cid: str, name: str) -> None:
        self._chan_name[cid] = name
        self._chan_id[name] = cid
        self._chan_id[cid] = cid

    def _label(self, cid: str) -> str:
        if cid not in self._chan_name:
            d = self._api("conversations.info", {"channel": cid})
            ch = d.get("channel", {}) if d.get("ok") else {}
            if ch.get("is_im"):
                name = "@" + self._user(ch.get("user", cid))
            else:
                name = ch.get("name", cid)
            self._remember(cid, name)
        return self._chan_name[cid]

    def _user(self, uid: str) -> str:
        if uid not in self._users:
            d = self._api("users.info", {"user": uid})
            u = d.get("user", {}) if d.get("ok") else {}
            prof = u.get("profile", {})
            self._users[uid] = prof.get("display_name") or u.get("name") or uid
        return self._users[uid]

    async def _aio(self) -> None:
        async with ws_connect(self._url, open_timeout=10) as ws:
            self._ws = ws
            async for raw in ws:
                try:
                    ev = json.loads(raw)
                except ValueError:
                    continue
                if self._env:
                    if ev.get("envelope_id"):
                        await ws.send(json.dumps({"envelope_id": ev["envelope_id"]}))
                    if ev.get("type") == "disconnect":
                        raise ConnectionError("Slack pediu reconexão")
                    ev = ev.get("payload", {}).get("event", {}) if ev.get("type") == "events_api" else {}
                self._on_event(ev)

    def _on_event(self, ev: dict) -> None:
        if ev.get("type") != "message" or ev.get("subtype") not in _SHOW_SUBTYPES:
            return
        uid = ev.get("user", "")
        if uid == self._uid or not ev.get("text"):
            return
        cid = ev.get("channel", "")
        nick = self._user(uid) if uid else ev.get("username", ev.get("bot_profile", {}).get("name", "bot"))
        text = slack_unescape(ev["text"], self._user)
        kind = "action" if ev.get("subtype") == "me_message" else "msg"
        try:
            ts = float(ev.get("ts", ""))
        except ValueError:
            ts = None
        self.ev.message(self._label(cid), nick, text, kind, ts)

    def _poll(self) -> None:
        last: dict[str, str] = {}
        start = f"{time.time():.6f}"
        while not self._stop.is_set():
            ids = [self._chan_id[c.lstrip("#")] for c in self.p.channels if c.lstrip("#") in self._chan_id]
            for ch in ids:
                d = self._api("conversations.history", {"channel": ch, "oldest": last.get(ch, start), "limit": 50})
                if not d.get("ok"):
                    if d.get("error") in _FATAL:
                        raise AdapterFatal(f"Slack: {d['error']}")
                    continue
                for m in reversed(d.get("messages", [])):
                    last[ch] = max(last.get(ch, "0"), m.get("ts", "0"))
                    m["channel"] = ch
                    m.setdefault("type", "message")
                    self._on_event(m)
            self._stop.wait(self.poll_interval)

    def join(self, channel: str, key: str = "") -> None:
        name = channel.lstrip("#")
        cid = self._chan_id.get(name)
        if not cid:
            self.ev.error(f"Slack: canal '{channel}' não encontrado.")
            return
        self._api("conversations.join", {"channel": cid})
        self.ev.join(name, self._me)

    def part(self, channel: str, reason: str = "") -> None:
        cid = self._chan_id.get(channel.lstrip("#"))
        if cid and not channel.startswith("@"):
            self._api("conversations.leave", {"channel": cid})
        self.ev.part(channel, self._me, "")

    def _post(self, method: str, target: str, text: str) -> None:
        cid = self._chan_id.get(target.lstrip("#")) or target
        d = self._api(method, {"channel": cid, "text": text})
        if not d.get("ok"):
            raise HTTPFailure(0, f"Slack: {d.get('error', 'falha ao enviar')}")

    def _deliver(self, target: str, text: str) -> None:
        for part in split_chars(text, 3900):
            self._post("chat.postMessage", target, part)

    def _deliver_action(self, target: str, text: str) -> None:
        self._post("chat.meMessage", target, text)

    @property
    def current_nick(self) -> str:
        return self._me or self.p.nick

    def is_dm(self, name: str) -> bool:
        return name.startswith("@")
