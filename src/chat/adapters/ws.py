"""WebSocket genérico e servidor colaborativo do Codis (``codis``)."""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

from src.chat.adapters.base import Adapter, AdapterFatal
from src.chat.models import ChatProtocol

try:
    from websockets.asyncio.client import connect as ws_connect
    HAS_WS = True
except ImportError:  # pragma: no cover
    try:
        import websockets  # type: ignore
        import websockets.client  # type: ignore
        ws_connect = websockets.client.connect  # type: ignore[assignment]
        HAS_WS = True
    except ImportError:
        ws_connect = None  # type: ignore[assignment]
        HAS_WS = False

NO_WS_MSG = "Instale: pip install websockets"


class AsyncAdapter(Adapter):
    """Base para adaptadores asyncio: um loop por sessão e fila de saída assíncrona."""

    def __init__(self, *a: Any, **k: Any) -> None:
        super().__init__(*a, **k)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ws: Any = None

    def _session_once(self) -> None:
        if not HAS_WS:
            raise AdapterFatal(NO_WS_MSG)
        loop = asyncio.new_event_loop()
        self._loop = loop
        try:
            loop.run_until_complete(self._aio())
        finally:
            self._ws = None
            try:
                for t in asyncio.all_tasks(loop):
                    t.cancel()
                loop.run_until_complete(asyncio.sleep(0))
            except Exception:  # noqa: BLE001
                pass
            loop.close()
            self._loop = None

    async def _aio(self) -> None:
        raise NotImplementedError

    def _close(self) -> None:
        loop, ws = self._loop, self._ws
        if loop and ws and not loop.is_closed():
            try:
                asyncio.run_coroutine_threadsafe(ws.close(), loop)
            except RuntimeError:
                pass

    def _ws_send(self, payload: str, timeout: float = 10) -> None:
        loop, ws = self._loop, self._ws
        if not loop or not ws or loop.is_closed():
            raise ConnectionError("sem conexão")
        asyncio.run_coroutine_threadsafe(ws.send(payload), loop).result(timeout)


class WSAdapter(AsyncAdapter):
    learn_nicks = True
    echoes_self = True

    def __init__(self, *a: Any, **k: Any) -> None:
        super().__init__(*a, **k)
        self._room = ""
        self._peers: set[str] = set()
        self._lock = threading.Lock()

    def _uri(self) -> str:
        p = self.p
        secure = p.proto == ChatProtocol.WSS or (p.proto == ChatProtocol.CODIS and p.tls)
        scheme = "wss" if secure else "ws"
        path = "" if p.proto == ChatProtocol.CODIS else (f"/{p.room_id.strip('/')}" if p.room_id else "")
        return f"{scheme}://{p.host}:{p.port}{path}"

    @property
    def _is_codis(self) -> bool:
        return self.p.proto == ChatProtocol.CODIS

    async def _aio(self) -> None:
        p = self.p
        self._room = (p.room_id or "general") if self._is_codis else (p.room_id or "geral")
        self._peers = set()
        kw: dict[str, Any] = {"open_timeout": 10, "max_size": 4 * 1024 * 1024}
        if self._uri().startswith("wss"):
            from src.chat.util import ssl_context
            kw["ssl"] = ssl_context(p.verify_tls)
        async with ws_connect(self._uri(), **kw) as ws:
            self._ws = ws
            if self._is_codis:
                await ws.send(json.dumps({"type": "join", "room": self._room, "user": p.nick,
                                          "password": p.decoded_password()}))
            self._mark_up(f"Conectado a {p.host}:{p.port}")
            self.ev.join(self._room, p.nick)
            async for raw in ws:
                self._handle(raw if isinstance(raw, str) else raw.decode("utf-8", "replace"))

    def _handle(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except ValueError:
            self.ev.message(self._room, "server", raw, "msg", None)
            return
        if not isinstance(msg, dict):
            self.ev.message(self._room, "server", raw, "msg", None)
            return
        t = msg.get("type", "")
        room = str(msg.get("room") or self._room)
        if t == "chat":
            self.ev.message(room, str(msg.get("user", "?")), str(msg.get("msg", "")), "msg", None)
        elif t == "peers" and self._is_codis:
            users = {str(u.get("user", "")) for u in msg.get("users", []) if isinstance(u, dict)}
            for gone in self._peers - users:
                self.ev.part(self._room, gone, "")
            for new in users - self._peers:
                if new != self.p.nick:
                    self.ev.join(self._room, new)
            self._peers = users
            self.ev.names(self._room, sorted(users))
        elif t in ("peer_join", "join") and not self._is_codis:
            self.ev.join(room, str(msg.get("user", "?")))
        elif t in ("peer_leave", "leave") and not self._is_codis:
            self.ev.part(room, str(msg.get("user", "?")), "")
        elif t == "error":
            text = str(msg.get("msg", "erro"))
            if self._is_codis:
                raise AdapterFatal(f"Codis: {text}")
            self.ev.error(text)

    def _deliver(self, target: str, text: str) -> None:
        if self.p.x("ws_format", "json") == "texto" and not self._is_codis:
            self._ws_send(text)
        else:
            self._ws_send(json.dumps({"type": "chat", "room": target, "msg": text, "user": self.p.nick},
                                     ensure_ascii=False))

    def part(self, channel: str, reason: str = "") -> None:
        self.ev.part(channel, self.p.nick, "")

    def join(self, channel: str, key: str = "") -> None:
        self.ev.join(channel, self.p.nick)
