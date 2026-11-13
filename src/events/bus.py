"""Codis Events - EventBus: barramento central de eventos."""

from __future__ import annotations

import collections
import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, NamedTuple

from src.events.stream import EventStream

if TYPE_CHECKING:
    import tkinter as tk

from .kinds import EventKind

logger = logging.getLogger("Codis.Events")
Handler = Callable[["Event"], None]


@dataclass(frozen=True, slots=True)
class Event:
    """Unidade de dados transportada pelo barramento."""

    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    source: str = ""
    timestamp: float = field(default_factory=time.monotonic)
    seq: int = 0
    batch_id: int = 0


class SubscriptionToken(NamedTuple):
    """Handle opaco retornado por ``subscribe``, usado para cancelar a subscrição."""

    topic: str
    handler_id: int


@dataclass(slots=True)
class _Subscription:
    topic: str
    handler: Handler
    priority: int
    handler_id: int
    once: bool = False


class _TopicMetrics:
    """Acumulador de métricas de latência e erros por tópico."""

    _BUCKETS = 128

    def __init__(self) -> None:
        """Inicializa as métricas de um tópico do barramento de eventos."""
        self.emit_count: int = 0
        self.handler_errors: int = 0
        self._total_ns: int = 0
        self._samples: collections.deque[int] = collections.deque(
            maxlen=self._BUCKETS)

    def record(self, elapsed_ns: int, errors: int) -> None:
        """Registra a latência de um dispatch e incrementa contadores."""
        self.emit_count += 1
        self.handler_errors += errors
        self._total_ns += elapsed_ns
        self._samples.append(elapsed_ns)

    @property
    def avg_us(self) -> float:
        """Tempo médio de dispatch em microssegundos."""
        return (self._total_ns / self.emit_count / 1_000) if self.emit_count else 0.0

    def _pct(self, p: float) -> float:
        """Calcula o percentil ``p`` (0.0-1.0) em microssegundos, sem re-sortear."""
        if not self._samples:
            return 0.0
        s = sorted(self._samples)
        idx = min(int(len(s) * p), len(s) - 1)
        return s[idx] / 1_000

    @property
    def p50_us(self) -> float:
        """Percentil 50 de latência em microssegundos (mediana)."""
        return self._pct(0.50)

    @property
    def p95_us(self) -> float:
        """Percentil 95 de latência em microssegundos."""
        return self._pct(0.95)


class SubscriptionGroup:
    """Gerencia múltiplas subscrições com dispose() atômico.

    Uso como context manager::

        with bus.group() as g:
            g.subscribe(EventKind.EDITOR_CHANGE, handler_a)
            g.subscribe(EventKind.FILE_SAVE,     handler_b)
        # ambas canceladas ao sair do bloco
    """

    def __init__(self, bus: EventBus) -> None:
        """Inicializa um grupo de subscrições com gerenciamento de ciclo de vida."""
        self._bus = bus
        self._tokens: list[SubscriptionToken] = []

    def subscribe(
        self,
        topic: str | EventKind,
        handler: Handler,
        *,
        priority: int = 50,
        once: bool = False,
    ) -> SubscriptionToken:
        """Subscreve ao bus e rastreia o token para cancelamento em grupo."""
        token = self._bus.subscribe(
            topic, handler, priority=priority, once=once)
        self._tokens.append(token)
        return token

    def dispose(self) -> None:
        """Cancela todas as subscrições do grupo de uma vez."""
        for token in self._tokens:
            self._bus.unsubscribe(token)
        self._tokens.clear()

    def __enter__(self) -> SubscriptionGroup:
        """Entra no contexto do grupo de subscrições."""
        return self

    def __exit__(self, *_: Any) -> None:
        """Sai do contexto e cancela todas as subscrições do grupo."""
        self.dispose()


class EventBus:
    """Barramento de eventos thread-safe - RESA."""

    def __init__(self, max_workers: int = 4) -> None:
        """Inicializa o barramento de eventos da aplicação."""
        self._lock = threading.RLock()
        self._subs: dict[str, list[_Subscription]
                         ] = collections.defaultdict(list)
        self._seq = 0
        self._batch_seq = 0
        self._metrics: dict[str, _TopicMetrics] = collections.defaultdict(
            _TopicMetrics)
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="codis-event"
        )
        self._handler_id = 0
        self._emitting = threading.local()
        self._pending: collections.deque[Event] = collections.deque()
        self._tk_root: tk.Tk | None = None

    def set_tk_root(self, root: tk.Tk) -> None:
        """Registra o widget root do Tk para uso em emit_tk()."""
        self._tk_root = root

    def subscribe(
        self,
        topic: str | EventKind,
        handler: Handler,
        *,
        priority: int = 50,
        once: bool = False,
    ) -> SubscriptionToken:
        """Subscreve ``handler`` ao tópico, inserindo-o na posição correta por prioridade.

        Args:
            topic:    Tópico (``EventKind`` ou string). Suporta wildcard ``"*"``.
            handler:  Callable invocado com o ``Event`` quando o tópico é emitido.
            priority: Ordem de invocação - menor valor = executado primeiro (padrão 50).
            once:     Se ``True``, cancela automaticamente após o primeiro disparo.

        Returns:
            Token de subscrição usado em :meth:`unsubscribe`.
        """
        key = topic.value if isinstance(topic, EventKind) else str(topic)
        with self._lock:
            self._handler_id += 1
            hid = self._handler_id
            sub = _Subscription(
                topic=key, handler=handler, priority=priority, handler_id=hid, once=once
            )
            bucket = self._subs[key]
            pos = 0
            while pos < len(bucket) and bucket[pos].priority <= priority:
                pos += 1
            bucket.insert(pos, sub)
        return SubscriptionToken(topic=key, handler_id=hid)

    def unsubscribe(self, token: SubscriptionToken) -> bool:
        """Remove a subscrição identificada por ``token``.

        Returns:
            ``True`` se encontrado e removido; ``False`` se já não existia.
        """
        with self._lock:
            bucket = self._subs.get(token.topic, [])
            for i, sub in enumerate(bucket):
                if sub.handler_id == token.handler_id:
                    bucket.pop(i)
                    return True
        return False

    def once(
        self, topic: str | EventKind, handler: Handler, *, priority: int = 50
    ) -> SubscriptionToken:
        """Subscreve ``handler`` para ser invocado apenas uma vez, depois cancela automaticamente."""
        return self.subscribe(topic, handler, priority=priority, once=True)

    def group(self) -> SubscriptionGroup:
        """Retorna um SubscriptionGroup vinculado a este bus."""
        return SubscriptionGroup(self)

    def emit(
        self,
        kind: str | EventKind,
        payload: dict[str, Any] | None = None,
        *,
        source: str = "",
        async_: bool = False,
    ) -> None:
        """
        Args:
            kind:    Tipo do evento (``EventKind`` ou string).
            payload: Dados adicionais entregues ao handler em ``event.payload``.
            source:  Identificador do módulo emissor (opcional, para rastreabilidade).
            async_:  Se ``True``, entrega via ``ThreadPoolExecutor`` sem bloquear.
        """
        key = kind.value if isinstance(kind, EventKind) else str(kind)
        with self._lock:
            self._seq += 1
            seq = self._seq
        event = Event(
            kind=key,
            payload=payload or {},
            source=source,
            timestamp=time.monotonic(),
            seq=seq,
        )
        if async_:
            self._pool.submit(self._deliver, event)
        else:
            self._deliver(event)

    def emit_tk(
        self,
        kind: str | EventKind,
        payload: dict[str, Any] | None = None,
        *,
        source: str = "",
        callback: Handler | None = None,
    ) -> None:
        """Publica evento e entrega handlers no Tk main thread via root.after(0).

        Essencial para handlers de background que precisam atualizar widgets.
        O ``callback`` extra é executado exclusivamente no Tk thread.
        """
        if self._tk_root is None:
            logger.warning("emit_tk() sem tk_root - usando emit() normal")
            self.emit(kind, payload, source=source)
            return
        key = kind.value if isinstance(kind, EventKind) else str(kind)
        with self._lock:
            self._seq += 1
            seq = self._seq
        event = Event(
            kind=key,
            payload=payload or {},
            source=source,
            timestamp=time.monotonic(),
            seq=seq,
        )

        def _tk_deliver() -> None:
            self._deliver(event)
            if callback is not None:
                try:
                    callback(event)
                except Exception:
                    logger.exception("emit_tk callback error (kind=%s)", key)

        try:
            self._tk_root.after(0, _tk_deliver)
        except Exception:
            self._deliver(event)

    def emit_batch(
        self,
        events: list[tuple[str | EventKind, dict[str, Any]]],
        *,
        source: str = "",
        async_: bool = False,
    ) -> None:
        """Emite múltiplos eventos com batch_id compartilhado e seqs contíguos."""
        if not events:
            return
        with self._lock:
            self._batch_seq += 1
            bid = self._batch_seq
            base = self._seq + 1
            self._seq += len(events)

        built: list[Event] = []
        now = time.monotonic()
        for i, (kind, payload) in enumerate(events):
            key = kind.value if isinstance(kind, EventKind) else str(kind)
            built.append(
                Event(
                    kind=key,
                    payload=payload or {},
                    source=source,
                    timestamp=now,
                    seq=base + i,
                    batch_id=bid,
                )
            )
        if async_:
            for ev in built:
                self._pool.submit(self._deliver, ev)
        else:
            for ev in built:
                self._deliver(ev)

    def stream(self, topic: str | EventKind) -> EventStream:
        """Retorna um ``EventStream`` lazy para o tópico, permitindo encadear operadores."""
        return EventStream(self, topic)

    def metrics(self) -> dict[str, dict]:
        """Retorna estatísticas de emissão por tópico: contagem, avg/p50/p95 em µs e erros."""
        with self._lock:
            return {
                topic: {
                    "emit_count": m.emit_count,
                    "avg_us": round(m.avg_us, 2),
                    "p50_us": round(m.p50_us, 2),
                    "p95_us": round(m.p95_us, 2),
                    "handler_errors": m.handler_errors,
                }
                for topic, m in self._metrics.items()
            }

    def shutdown(self) -> None:
        """Encerra o pool de threads, cancelando futures pendentes."""
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _deliver(self, event: Event) -> None:
        """Entrega o evento, enfileirando-o se já estiver num dispatch recursivo."""
        if getattr(self._emitting, "active", False):
            self._pending.append(event)
            return
        self._emitting.active = True
        try:
            self._dispatch(event)
            while self._pending:
                self._dispatch(self._pending.popleft())
        finally:
            self._emitting.active = False

    def _dispatch(self, event: Event) -> None:
        """Copy-under-lock: coleta handlers com lock brevíssimo, entrega fora.

        Otimizações aplicadas:
        - Prefixos wildcard pré-computados a partir do kind do evento.
        - Sort omitido quando só há um bucket (caso mais comum).
        - Remoção de ``once`` em único passo sobre os buckets afetados.
        """
        t0 = time.monotonic_ns()
        key = event.kind

        parts = key.split(".")
        prefixes = [".".join(parts[:d]) + ".*" for d in range(1, len(parts))]

        handlers: list[_Subscription] = []
        once_ids: list[tuple[str, int]] = []
        seen: set[int] = set()

        with self._lock:
            for sub in self._subs.get(key, ()):
                if sub.handler_id not in seen:
                    handlers.append(sub)
                    seen.add(sub.handler_id)

            for pfx in prefixes:
                for sub in self._subs.get(pfx, ()):
                    if sub.handler_id not in seen:
                        handlers.append(sub)
                        seen.add(sub.handler_id)

            for sub in self._subs.get("*", ()):
                if sub.handler_id not in seen:
                    handlers.append(sub)
                    seen.add(sub.handler_id)

            for sub in handlers:
                if sub.once:
                    once_ids.append((sub.topic, sub.handler_id))
            if once_ids:
                remove_set: dict[str, set[int]] = {}
                for topic, hid in once_ids:
                    remove_set.setdefault(topic, set()).add(hid)
                for topic, hids in remove_set.items():
                    self._subs[topic] = [
                        s for s in self._subs.get(topic, []) if s.handler_id not in hids
                    ]

        if len(handlers) > 1:
            handlers.sort(key=lambda s: s.priority)

        errors = 0
        for sub in handlers:
            try:
                sub.handler(event)
            except Exception:
                errors += 1
                logger.exception(
                    "Handler error tópico '%s' handler_id=%d", key, sub.handler_id
                )

        elapsed = time.monotonic_ns() - t0
        with self._lock:
            self._metrics[key].record(elapsed, errors)
