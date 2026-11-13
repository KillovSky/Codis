"""Codis - Rede do colab: servidor embutido e cliente WebSocket.

Protocolo (JSON, uma mensagem por frame)::

    cliente -> servidor
      {"type":"join",  "room":str, "user":str, "password":str}
      {"type":"op",    "rev":int,  "ops":[{op,pos,text,len}, ...]}
      {"type":"cursor","line":int, "col":int}
      {"type":"chat",  "msg":str}

    servidor -> cliente
      {"type":"sync",  "rev":int, "content":str}      documento completo
      {"type":"ack",   "rev":int}                     sua mudança foi aceita
      {"type":"op",    "rev":int, "ops":[...], "user":str}   mudança de outro
      {"type":"cursor","user":str,"color":str,"line":int,"col":int}
      {"type":"chat",  "user":str,"msg":str,"ts":str}
      {"type":"peers", "users":[{"user":str,"color":str}]}
      {"type":"error", "msg":str}

Cada mudança aceita incrementa a revisão em 1. O remetente recebe ``ack``
(nunca o ``op`` de volta). Mudanças inválidas ou baseadas em histórico já
descartado provocam um ``sync`` completo para aquele cliente.

O cliente **não** chama callbacks de interface: ele enfileira eventos em
``events`` e a thread de UI os consome com :meth:`CollabClient.drain`.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from src.collab.ot import ResyncNeeded, RoomDoc, ops_from_dicts

logger = logging.getLogger("Codis.Collab")

try:
    from websockets.asyncio.client import connect as _ws_connect
    from websockets.asyncio.server import serve as _ws_serve

    HAS_WS = True
except ImportError:  # pragma: no cover
    try:
        import websockets  # type: ignore
        import websockets.client  # type: ignore
        import websockets.server  # type: ignore

        _ws_serve = websockets.server.serve  # type: ignore[attr-defined]
        _ws_connect = websockets.client.connect  # type: ignore[attr-defined]
        HAS_WS = True
    except ImportError:
        _ws_serve = _ws_connect = None  # type: ignore[assignment]
        HAS_WS = False

DEFAULT_PORT = 9743
MAX_FRAME_BYTES = 32 * 1024 * 1024
MAX_OPS_PER_CHANGE = 10_000
MAX_DOC_CHARS = 20_000_000
MAX_ROOMS = 100
MAX_PEERS_PER_ROOM = 50
MAX_USER_LEN = 32
MAX_CHAT_LEN = 4000
CONNECT_TIMEOUT_S = 8

CURSOR_PALETTE = [
    "#f38ba8", "#fab387", "#f9e2af", "#a6e3a1",
    "#89dceb", "#74c7ec", "#b4befe", "#cba6f7",
]


def peer_color(user: str) -> str:
    """Cor estável do usuário (a mesma em qualquer máquina)."""
    h = sum(ord(c) * (i + 1) for i, c in enumerate(user))
    return CURSOR_PALETTE[h % len(CURSOR_PALETTE)]


@dataclass
class _Peer:
    user: str
    ws: Any
    color: str = ""
    line: int = 0
    col: int = 0


@dataclass
class _Room:
    room_id: str
    password: str
    doc: RoomDoc
    peers: dict[str, _Peer] = field(default_factory=dict)


class CollabServer:
    """Servidor de colaboração embutido (thread própria com event loop)."""

    def __init__(self, host: str = "0.0.0.0", port: int = DEFAULT_PORT,
                 max_peers: int = MAX_PEERS_PER_ROOM) -> None:
        self._host = host
        self._port = port
        self._max_peers = max(1, min(int(max_peers), MAX_PEERS_PER_ROOM))
        self._rooms: dict[str, _Room] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: Any = None
        self._thread: threading.Thread | None = None
        self.running = False

    @property
    def port(self) -> int:
        """Porta efetiva (útil quando iniciado com porta 0)."""
        return self._port

    def start(self) -> tuple[bool, str]:
        """Inicia o servidor. Retorna ``(sucesso, mensagem)``."""
        if not HAS_WS:
            return False, "Instale: pip install websockets"
        self._loop = asyncio.new_event_loop()
        ready = threading.Event()
        errors: list[str] = []

        async def _serve() -> None:
            try:
                self._server = await _ws_serve(
                    self._handle, self._host, self._port, max_size=MAX_FRAME_BYTES)
                sockets = getattr(self._server, "sockets", None) or []
                if sockets:
                    self._port = sockets[0].getsockname()[1]
                ready.set()
                await self._server.wait_closed()
            except OSError as exc:
                errors.append(str(exc))
                ready.set()

        def _run() -> None:
            asyncio.set_event_loop(self._loop)
            try:
                self._loop.run_until_complete(_serve())
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc))
                ready.set()
            finally:
                with contextlib.suppress(Exception):
                    self._loop.close()

        self._thread = threading.Thread(target=_run, daemon=True, name="Codis-CollabServer")
        self._thread.start()
        if not ready.wait(timeout=5):
            return False, "Tempo esgotado ao iniciar o servidor"
        if errors:
            return False, errors[0]
        self.running = True
        return True, f"Servidor iniciado em {self._host}:{self._port}"

    def stop(self) -> None:
        """Encerra o servidor e todas as conexões."""
        self.running = False
        if self._server is not None and self._loop is not None:
            with contextlib.suppress(RuntimeError):
                self._loop.call_soon_threadsafe(self._server.close)
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=3)

    def create_room(
        self, room_id: str | None = None, password: str = "", initial_content: str = ""
    ) -> str:
        """Cria uma sala (ou a recria) com conteúdo inicial e senha."""
        rid = room_id or uuid.uuid4().hex[:8].upper()
        self._rooms[rid] = _Room(
            room_id=rid, password=password,
            doc=RoomDoc(initial_content, max_chars=MAX_DOC_CHARS))
        return rid

    async def _send(self, ws: Any, payload: dict) -> None:
        await ws.send(json.dumps(payload))

    async def _reject(self, ws: Any, msg: str) -> None:
        with contextlib.suppress(Exception):
            await self._send(ws, {"type": "error", "msg": msg})

    async def _handle(self, ws: Any) -> None:
        try:
            first = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
        except Exception:  # noqa: BLE001
            return
        if not isinstance(first, dict) or first.get("type") != "join":
            await self._reject(ws, "esperado: join")
            return

        room_id = str(first.get("room", "")).strip()
        user = str(first.get("user", "")).strip()[:MAX_USER_LEN] or f"user_{uuid.uuid4().hex[:4]}"
        password = str(first.get("password", ""))

        room = self._rooms.get(room_id)
        if room is None:
            await self._reject(ws, "Sala não encontrada. Confira o ID com quem hospeda.")
            return
        if room.password and not hmac.compare_digest(
            room.password.encode("utf-8"), password.encode("utf-8")
        ):
            await self._reject(ws, "Senha incorreta.")
            return
        if user in room.peers:
            await self._reject(ws, f"O nome '{user}' já está em uso nesta sala.")
            return
        if len(room.peers) >= self._max_peers:
            await self._reject(ws, "Sala cheia.")
            return

        peer = _Peer(user=user, ws=ws, color=peer_color(user))
        room.peers[user] = peer
        try:
            await self._send(ws, {"type": "sync", "rev": room.doc.rev,
                                  "content": room.doc.content})
            await self._broadcast_peers(room)
            async for raw in ws:
                await self._dispatch(raw, room, peer)
        except Exception:  # noqa: BLE001
            logger.debug("conexão de %s encerrada", user, exc_info=True)
        finally:
            if room.peers.get(user) is peer:
                del room.peers[user]
            with contextlib.suppress(Exception):
                await self._broadcast_peers(room)

    async def _dispatch(self, raw: Any, room: _Room, peer: _Peer) -> None:
        try:
            msg = json.loads(raw)
        except (TypeError, ValueError):
            return
        if not isinstance(msg, dict):
            return
        kind = msg.get("type")

        if kind == "op":
            await self._on_op(msg, room, peer)
        elif kind == "cursor":
            try:
                peer.line, peer.col = max(0, int(msg.get("line", 0))), max(0, int(msg.get("col", 0)))
            except (TypeError, ValueError):
                return
            await self._broadcast(room, {
                "type": "cursor", "user": peer.user, "color": peer.color,
                "line": peer.line, "col": peer.col}, exclude=peer.user)
        elif kind == "chat":
            text = str(msg.get("msg", ""))[:MAX_CHAT_LEN].strip()
            if text:
                await self._broadcast(room, {
                    "type": "chat", "user": peer.user, "msg": text,
                    "ts": time.strftime("%H:%M:%S")})

    async def _on_op(self, msg: dict, room: _Room, peer: _Peer) -> None:
        try:
            raw_ops = msg.get("ops", [])
            if not isinstance(raw_ops, list) or len(raw_ops) > MAX_OPS_PER_CHANGE:
                raise ValueError("ops inválidas")
            ops = ops_from_dicts(raw_ops)
            client_rev = msg.get("rev")
            if not isinstance(client_rev, int) or isinstance(client_rev, bool):
                raise ValueError("rev inválida")
            applied = room.doc.receive(client_rev, ops)
        except (ValueError, ResyncNeeded) as exc:
            logger.debug("mudança de %s rejeitada: %s", peer.user, exc)
            await self._send(peer.ws, {"type": "sync", "rev": room.doc.rev,
                                       "content": room.doc.content})
            return
        await self._send(peer.ws, {"type": "ack", "rev": room.doc.rev})
        await self._broadcast(room, {
            "type": "op", "rev": room.doc.rev, "user": peer.user,
            "ops": [o.to_dict() for o in applied]}, exclude=peer.user)

    async def _broadcast(self, room: _Room, payload: dict, exclude: str | None = None) -> None:
        data = json.dumps(payload)
        for user, peer in list(room.peers.items()):
            if user == exclude:
                continue
            try:
                await peer.ws.send(data)
            except Exception:  # noqa: BLE001
                logger.debug("falha ao enviar para %s", user, exc_info=True)

    async def _broadcast_peers(self, room: _Room) -> None:
        await self._broadcast(room, {"type": "peers", "users": [
            {"user": p.user, "color": p.color} for p in room.peers.values()]})


class CollabClient:
    """Cliente WebSocket. Eventos chegam em ``events``; a UI chama :meth:`drain`.

    Eventos extras (além das mensagens do protocolo):
      * ``{"type": "connected"}`` - handshake TCP/WS concluído e ``join`` enviado.
      * ``{"type": "closed", "msg": str, "clean": bool}`` - conexão encerrada.
    """

    def __init__(self, host: str, port: int, room_id: str, user: str, password: str) -> None:
        self._host = host
        self._port = port
        self._room = room_id
        self._user = user
        self._password = password
        self.events: queue.SimpleQueue[dict] = queue.SimpleQueue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._out: asyncio.Queue | None = None
        self._thread: threading.Thread | None = None
        self._connected = False
        self._stopping = False
        self._rejected = False

    @property
    def connected(self) -> bool:
        """Se a conexão está estabelecida."""
        return self._connected

    def connect(self) -> None:
        """Conecta em segundo plano. O resultado chega como evento."""
        if not HAS_WS:
            self.events.put({"type": "closed", "msg": "Instale: pip install websockets",
                             "clean": False})
            return
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True, name="Codis-CollabClient")
        self._thread.start()

    def drain(self) -> list[dict]:
        """Retorna (sem bloquear) todos os eventos pendentes, em ordem."""
        items: list[dict] = []
        while True:
            try:
                items.append(self.events.get_nowait())
            except queue.Empty:
                return items

    def send(self, payload: dict) -> None:
        """Envia uma mensagem (thread-safe). Ignorada se desconectado."""
        loop, out = self._loop, self._out
        if loop is not None and out is not None and self._connected:
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(out.put_nowait, json.dumps(payload))

    def disconnect(self) -> None:
        """Fecha a conexão."""
        self._stopping = True
        self._connected = False
        loop, out = self._loop, self._out
        if loop is not None and out is not None:
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(out.put_nowait, None)

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._session())
        finally:
            with contextlib.suppress(Exception):
                self._loop.close()

    async def _session(self) -> None:
        uri = f"ws://{self._host}:{self._port}"
        self._out = asyncio.Queue()
        reason, clean = "Conexão encerrada.", True
        try:
            async with _ws_connect(
                uri, max_size=MAX_FRAME_BYTES, open_timeout=CONNECT_TIMEOUT_S
            ) as ws:
                self._connected = True
                await ws.send(json.dumps({"type": "join", "room": self._room,
                                          "user": self._user, "password": self._password}))
                self.events.put({"type": "connected"})

                async def _writer() -> None:
                    while True:
                        item = await self._out.get()
                        if item is None:
                            await ws.close()
                            return
                        await ws.send(item)

                writer = asyncio.ensure_future(_writer())
                try:
                    async for raw in ws:
                        self._on_frame(raw)
                finally:
                    writer.cancel()
        except Exception as exc:  # noqa: BLE001
            clean = False
            reason = f"Não foi possível manter a conexão: {exc}" if not self._stopping else reason
        finally:
            self._connected = False
            if not self._stopping and not self._rejected:
                self.events.put({"type": "closed", "msg": reason, "clean": clean})
            elif self._stopping:
                self.events.put({"type": "closed", "msg": "Desconectado.", "clean": True})

    def _on_frame(self, raw: Any) -> None:
        try:
            msg = json.loads(raw)
        except (TypeError, ValueError):
            return
        if not isinstance(msg, dict):
            return
        if msg.get("type") == "error":
            self._rejected = True
        self.events.put(msg)
