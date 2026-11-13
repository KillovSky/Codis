"""Discord Gateway v10 (heartbeat com ACK, intents corretos) + REST."""

from __future__ import annotations

import asyncio
import json
import random
import re
from typing import Any

from src.chat.adapters.base import AdapterFatal
from src.chat.adapters.http import HTTPFailure, request_json
from src.chat.adapters.ws import AsyncAdapter, ws_connect
from src.chat.util import split_chars

GATEWAY = "wss://gateway.discord.gg/?v=10&encoding=json"
API = "https://discord.com/api/v10"
INTENTS = 1 | (1 << 9) | (1 << 12) | (1 << 15)
_FATAL_CLOSE = {
    4004: "token inválido",
    4010: "shard inválido", 4011: "sharding obrigatório", 4012: "versão da API inválida",
    4013: "intents inválidas",
    4014: "intent privilegiada não habilitada: ative 'Message Content Intent' no Developer Portal",
}


class DiscordAdapter(AsyncAdapter):
    learn_nicks = True

    def __init__(self, *a: Any, **k: Any) -> None:
        super().__init__(*a, **k)
        self._seq: int | None = None
        self._uid = ""
        self._me = ""
        self._label: dict[str, str] = {}
        self._dm_ids: dict[str, str] = {}
        self._user_ids: dict[str, str] = {}

    def _headers(self) -> dict[str, str]:
        tok = self.p.decoded_token()
        return {"Authorization": tok if self.p.x("token_kind", "bot") == "user" else f"Bot {tok}"}

    def _rest(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        return request_json(method, self.p.x("api_base", API).rstrip("/") + path, headers=self._headers(),
                            body=body, verify=self.p.verify_tls)

    def _visible(self, cid: str) -> bool:
        wanted = self.p.channels
        if not wanted:
            return True
        label = self._label.get(cid, "")
        return any(w == cid or w.lstrip("#").casefold() == label.lstrip("#").split(" ")[0].casefold()
                   for w in wanted)

    async def _aio(self) -> None:
        self._seq = None
        async with ws_connect(self.p.x("gateway_url", GATEWAY), open_timeout=10, max_size=8 * 1024 * 1024) as ws:
            self._ws = ws
            hello = json.loads(await asyncio.wait_for(ws.recv(), 15))
            interval = hello["d"]["heartbeat_interval"] / 1000
            acked = {"v": True}
            hb = asyncio.ensure_future(self._heartbeat(ws, interval, acked))
            await ws.send(json.dumps({"op": 2, "d": {
                "token": self.p.decoded_token(), "intents": INTENTS,
                "properties": {"os": "linux", "browser": "Codis", "device": "Codis"}}}))
            try:
                async for raw in ws:
                    ev = json.loads(raw)
                    op, t, d = ev.get("op"), ev.get("t"), ev.get("d") or {}
                    if ev.get("s") is not None:
                        self._seq = ev["s"]
                    if op == 11:
                        acked["v"] = True
                    elif op == 1:
                        await ws.send(json.dumps({"op": 1, "d": self._seq}))
                    elif op in (7, 9):
                        raise ConnectionError("Discord pediu para reconectar")
                    elif op == 0:
                        self._dispatch(t, d)
            except Exception as exc:  # noqa: BLE001
                code = getattr(getattr(exc, "rcvd", None), "code", None)
                if code in _FATAL_CLOSE:
                    raise AdapterFatal(f"Discord: {_FATAL_CLOSE[code]} (código {code})") from exc
                raise
            finally:
                hb.cancel()

    async def _heartbeat(self, ws: Any, interval: float, acked: dict) -> None:
        await asyncio.sleep(interval * random.random())
        while True:
            if not acked["v"]:
                await ws.close(code=4000, reason="sem ACK do heartbeat")
                return
            acked["v"] = False
            await ws.send(json.dumps({"op": 1, "d": self._seq}))
            await asyncio.sleep(interval)

    def _add_channels(self, d: dict) -> None:
        guild = d.get("name", "")
        for ch in d.get("channels", []):
            if ch.get("type") in (0, 5, 15):
                self._register(ch["id"], ch["name"], guild)

    def _register(self, cid: str, name: str, guild: str) -> None:
        label = f"#{name}"
        if label in self._label.values() and self._label.get(cid) != label:
            label = f"#{name} ({guild})" if guild else f"#{name}-{cid[-4:]}"
        self._label[cid] = label

    def _dispatch(self, t: str, d: dict) -> None:
        if t == "READY":
            u = d.get("user", {})
            self._uid, self._me = u.get("id", ""), u.get("username", "")
            self._mark_up(f"Discord: conectado como {self._me}")
        elif t in ("GUILD_CREATE", "CHANNEL_CREATE", "THREAD_CREATE"):
            if t == "GUILD_CREATE":
                self._add_channels(d)
                for ch in d.get("channels", []):
                    if ch["id"] in self._label and self.p.channels and self._visible(ch["id"]):
                        self.ev.join(self._label[ch["id"]], self._me)
            elif d.get("name"):
                self._register(d["id"], d["name"], "")
        elif t == "MESSAGE_CREATE":
            self._on_message(d)

    def _on_message(self, d: dict) -> None:
        author = d.get("author", {})
        if author.get("id") == self._uid:
            return
        cid = d.get("channel_id", "")
        name = author.get("global_name") or author.get("username", "?")
        if d.get("guild_id"):
            if cid not in self._label:
                self._label[cid] = f"#{cid}"
            if not self._visible(cid):
                return
            label = self._label[cid]
        else:
            label = "@" + author.get("username", cid)
            self._dm_ids[label] = cid
            self._user_ids[label] = author.get("id", "")
        text = d.get("content", "")
        for m in d.get("mentions", []):
            text = text.replace(f"<@{m['id']}>", "@" + m.get("username", "?")).replace(
                f"<@!{m['id']}>", "@" + m.get("username", "?"))
        text = re.sub(r"<#(\d+)>", lambda m: self._label.get(m.group(1), "#canal"), text)
        for att in d.get("attachments", []):
            text = f"{text} {att.get('url', '')}".strip()
        if not text:
            return
        self.ev.message(label, name, text, "msg", None)

    def _channel_id(self, target: str) -> str:
        if target in self._dm_ids:
            return self._dm_ids[target]
        for cid, label in self._label.items():
            if target in (label, cid) or target.lstrip("#") == label.lstrip("#"):
                return cid
        return target

    def _deliver(self, target: str, text: str) -> None:
        cid = self._channel_id(target)
        if target.startswith("@") and cid == target:
            uid = self._user_ids.get(target)
            if not uid:
                raise HTTPFailure(0, "responda a uma DM recebida primeiro (ID do usuário desconhecido)")
            code, ch = self._rest("POST", "/users/@me/channels", {"recipient_id": uid})
            cid = ch.get("id", "") if code == 200 else ""
            self._dm_ids[target] = cid
        for part in split_chars(text, 1990):
            code, resp = self._rest("POST", f"/channels/{cid}/messages",
                                    {"content": part, "allowed_mentions": {"parse": []}})
            if code not in (200, 201):
                raise HTTPFailure(code, str(resp.get("message", "falha ao enviar")) if isinstance(resp, dict) else "")

    def _deliver_action(self, target: str, text: str) -> None:
        self._deliver(target, f"_{self._me or self.p.nick} {text}_")

    def join(self, channel: str, key: str = "") -> None:
        self.ev.join(self._label.get(channel, channel), self._me)

    def part(self, channel: str, reason: str = "") -> None:
        self.ev.part(channel, self._me, "")

    @property
    def current_nick(self) -> str:
        return self._me or self.p.nick

    def is_dm(self, name: str) -> bool:
        return name.startswith("@")
