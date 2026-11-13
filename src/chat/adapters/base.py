"""Base dos adaptadores: ciclo de vida com reconexão, fila de envio e eventos."""

from __future__ import annotations

import logging
import queue
import random
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from src.chat.models import ChatProfile
from src.chat.util import clean_line, split_utf8

logger = logging.getLogger("Codis.Chat")

SERVER_TAB = "*server*"


def _noop(*_a: Any, **_k: Any) -> None:
    return None


@dataclass
class Events:
    """Callbacks que o adaptador dispara (de qualquer thread). Todos são opcionais."""

    message: Callable[..., None] = _noop
    own: Callable[..., None] = _noop
    join: Callable[..., None] = _noop
    part: Callable[..., None] = _noop
    nick: Callable[..., None] = _noop
    topic: Callable[..., None] = _noop
    names: Callable[..., None] = _noop
    connect: Callable[..., None] = _noop
    error: Callable[..., None] = _noop
    info: Callable[..., None] = _noop
    disconnect: Callable[..., None] = _noop
    reconnecting: Callable[..., None] = _noop
    raw: Callable[..., None] = _noop


class AdapterFatal(Exception):
    """Erro que não adianta repetir (senha errada, token inválido): não reconecta."""


@dataclass
class Backoff:
    base: float = 1.0
    cap: float = 60.0
    attempt: int = 0
    _rng: random.Random = field(default_factory=random.Random, repr=False)

    def next(self) -> float:
        d = min(self.cap, self.base * (2**self.attempt))
        self.attempt += 1
        return d * (0.85 + self._rng.random() * 0.3)

    def reset(self) -> None:
        self.attempt = 0


class Adapter:
    """Interface comum. Subclasses implementam ``_session_once`` e ``_deliver``.

    * ``_session_once`` bloqueia enquanto a conexão viver; retorna/levanta ao cair.
    * ``_deliver`` envia UMA linha (já saneada/dividida) e levanta em caso de falha.
    """

    max_bytes = 0
    echoes_self = False
    multiline = True
    learn_nicks = False
    backoff_base = 1.0

    def __init__(self, profile: ChatProfile, events: Events | None = None) -> None:
        self.p = profile
        self.ev = events or Events()
        self._stop = threading.Event()
        self._connected = False
        self._was_up = False
        self._outq: queue.Queue[tuple[str, str, str] | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._worker: threading.Thread | None = None

    def connect(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._supervise, daemon=True,
                                        name=f"Codis-{type(self).__name__}")
        self._thread.start()
        self._worker = threading.Thread(target=self._send_loop, daemon=True,
                                        name=f"Codis-{type(self).__name__}-tx")
        self._worker.start()

    def _supervise(self) -> None:
        backoff = Backoff(base=self.backoff_base)
        while not self._stop.is_set():
            self._was_up = False
            reason = ""
            try:
                self._session_once()
            except AdapterFatal as exc:
                self._connected = False
                self.ev.error(str(exc))
                break
            except Exception as exc:  # noqa: BLE001
                if not self._stop.is_set():
                    logger.debug("%s caiu", type(self).__name__, exc_info=True)
                    reason = str(exc) or type(exc).__name__
            self._connected = False
            if self._stop.is_set():
                break
            if self._was_up:
                backoff.reset()
            self.ev.disconnect(reason or "conexão encerrada")
            if not self.p.auto_reconnect:
                break
            delay = backoff.next()
            self.ev.reconnecting(delay)
            if self._stop.wait(delay):
                break
        self._connected = False

    def _mark_up(self, msg: str) -> None:
        self._connected = True
        self._was_up = True
        self.ev.connect(msg)

    def _session_once(self) -> None:
        raise NotImplementedError

    def _close(self) -> None:
        """Interrompe a I/O bloqueante da sessão atual (fecha socket, loop...)."""

    def quit(self, reason: str = "Codis") -> None:
        self._stop.set()
        self._connected = False
        self._outq.put(None)
        try:
            self._close()
        except Exception:  # noqa: BLE001
            logger.debug("falha ao fechar", exc_info=True)

    def send(self, target: str, text: str) -> None:
        self._enqueue(target, text, "msg")

    def send_action(self, target: str, text: str) -> None:
        self._enqueue(target, text, "action")

    def _enqueue(self, target: str, text: str, kind: str) -> None:
        if not self._connected:
            self.ev.error("Não conectado: mensagem não enviada.")
            return
        chunks = text.split("\n") if not self.multiline else [text]
        for chunk in chunks:
            chunk = clean_line(chunk) if not self.multiline else chunk.replace("\x00", "")
            if not chunk.strip():
                continue
            parts = (split_utf8(chunk, max(16, self.max_bytes - len(target.encode())))
                     if self.max_bytes else [chunk])
            for part in parts:
                self._outq.put((target, part, kind))

    def _send_loop(self) -> None:
        while True:
            item = self._outq.get()
            if item is None or self._stop.is_set():
                return
            target, text, kind = item
            try:
                if kind == "action":
                    self._deliver_action(target, text)
                else:
                    self._deliver(target, text)
            except Exception as exc:  # noqa: BLE001
                self.ev.error(f"Falha ao enviar para {target}: {exc}")

    def _deliver(self, target: str, text: str) -> None:
        raise NotImplementedError

    def _deliver_action(self, target: str, text: str) -> None:
        self._deliver(target, f"_{self.current_nick} {text}_")

    def join(self, channel: str, key: str = "") -> None:
        self.ev.join(channel, self.current_nick)

    def part(self, channel: str, reason: str = "") -> None:
        self.ev.part(channel, self.current_nick, reason)

    def handle_command(self, cmd: str, rest: str, active: str) -> bool:
        """True se ``/cmd rest`` foi tratado pelo adaptador (``cmd`` em MAIÚSCULAS)."""
        return False

    def commands(self) -> list[str]:
        return []

    def is_dm(self, name: str) -> bool:
        return name.startswith("@")

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def current_nick(self) -> str:
        return self.p.nick
