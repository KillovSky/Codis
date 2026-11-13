"""Codis Events - EventStream: stream reativo com operadores encadeáveis."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .bus import Event, EventBus, SubscriptionToken
    from .kinds import EventKind

Handler = Callable[["Event"], None]
Predicate = Callable[["Event"], bool]
Transform = Callable[["Event"], "Event"]


class EventStream:
    """Stream reativo lazy - transformações encadeadas sem threads adicionais."""

    def __init__(self, bus: EventBus, topic: str | EventKind) -> None:
        """Inicializa o stream de eventos reativos."""
        from .kinds import EventKind as EK

        self._bus = bus
        self._topic = topic.value if isinstance(topic, EK) else str(topic)
        self._ops: list[tuple[str, Any]] = []
        self._token: SubscriptionToken | None = None
        self._lock = threading.Lock()

        self._debounce_ms: int | None = None
        self._debounce_leading: bool = False
        self._debounce_timer: threading.Timer | None = None
        self._debounce_pending: Event | None = None
        self._debounce_fired_leading: bool = False

        self._throttle_ms: int | None = None
        self._throttle_last: float = 0.0

        self._take_n: int | None = None
        self._take_count: int = 0
        self._skip_n: int | None = None
        self._skip_count: int = 0

        self._distinct_last: dict | None = None

        self._agg_fn: Callable | None = None
        self._agg_state: Any = None

    def filter(self, predicate: Predicate) -> EventStream:
        """Descarta eventos para os quais ``predicate(event)`` retorna ``False``."""
        self._ops.append(("filter", predicate))
        return self

    def map(self, transform: Transform) -> EventStream:
        """Aplica ``transform`` em cada evento, substituindo-o pelo resultado."""
        self._ops.append(("map", transform))
        return self

    def distinct(self) -> EventStream:
        """Emite apenas quando o payload difere do evento anterior."""
        self._ops.append(("distinct", None))
        return self

    def aggregate(
        self, fn: Callable[[Any, Event], Any], initial: Any = None
    ) -> EventStream:
        """Redução acumulada: ``state = fn(state, event)`` a cada evento.

        O evento é substituído por um sintético com ``payload={\"value\": state}``.
        Útil para contadores, médias móveis, listas acumuladas, etc.
        """
        self._agg_fn = fn
        self._agg_state = initial
        self._ops.append(("aggregate", None))
        return self

    def debounce(self, ms: int, *, leading: bool = False) -> EventStream:
        """Suprime eventos até que ``ms`` ms passem sem novo evento.

        Usa um único timer reciclado - não cria thread por evento.

        Parameters
        ----------
        ms:      Tempo de espera após o último evento.
        leading: Se True, dispara imediatamente no PRIMEIRO evento da rajada
                 E no último (comportamento leading + trailing).
        """
        self._debounce_ms = ms
        self._debounce_leading = leading
        return self

    def throttle(self, ms: int) -> EventStream:
        """Limita a taxa a no máximo 1 evento a cada ``ms`` ms."""
        self._throttle_ms = ms
        return self

    def take(self, n: int) -> EventStream:
        """Cancela a subscrição automaticamente após receber ``n`` eventos."""
        self._take_n = n
        return self

    def skip(self, n: int) -> EventStream:
        """Ignora os primeiros ``n`` eventos antes de começar a entregá-los."""
        self._skip_n = n
        return self

    def window(self, size_ms: int) -> WindowedStream:
        """Agrupa eventos em janelas temporais de ``size_ms`` ms (tumbling window)."""
        return WindowedStream(self, size_ms)

    def idle(self, idle_ms: int) -> IdleStream:
        """Retorna um IdleStream que dispara apenas após ``idle_ms`` ms de silêncio."""
        return IdleStream(self, idle_ms)

    def pauseable(self) -> PauseableStream:
        """Retorna um PauseableStream que pode ser pausado/retomado."""
        return PauseableStream(self)

    def subscribe(self, handler: Handler) -> SubscriptionToken:
        """Ativa o stream e registra ``handler`` como destino final."""

        def _pipeline(event: Event) -> None:
            if self._skip_n is not None:
                with self._lock:
                    if self._skip_count < self._skip_n:
                        self._skip_count += 1
                        return

            current = event
            for op_type, op_fn in self._ops:
                if op_type == "filter":
                    if not op_fn(current):
                        return
                elif op_type == "map":
                    current = op_fn(current)
                elif op_type == "distinct":
                    with self._lock:
                        if current.payload == self._distinct_last:
                            return
                        self._distinct_last = current.payload
                elif op_type == "aggregate" and self._agg_fn is not None:
                    with self._lock:
                        self._agg_state = self._agg_fn(
                            self._agg_state, current)
                        state_snap = self._agg_state
                    import dataclasses

                    current = dataclasses.replace(
                        current, payload={"value": state_snap}
                    )

            if self._throttle_ms is not None:
                now = time.monotonic()
                with self._lock:
                    if (now - self._throttle_last) * 1000 < self._throttle_ms:
                        return
                    self._throttle_last = now

            if self._debounce_ms is not None:
                self._debounce_handle(current, handler)
                return

            if self._take_n is not None:
                with self._lock:
                    self._take_count += 1
                    count = self._take_count
                if count > self._take_n:
                    if self._token:
                        self._bus.unsubscribe(self._token)
                    return

            handler(current)

        self._token = self._bus.subscribe(self._topic, _pipeline)
        return self._token

    def dispose(self) -> None:
        """Cancela o timer de debounce pendente e remove a subscrição do bus."""
        with self._lock:
            if self._debounce_timer:
                self._debounce_timer.cancel()
                self._debounce_timer = None
        if self._token:
            self._bus.unsubscribe(self._token)
            self._token = None

    def _debounce_handle(self, event: Event, handler: Handler) -> None:
        """Lógica de debounce com timer reciclado - nunca cria mais de 1 thread."""
        ms = self._debounce_ms or 0
        with self._lock:
            is_first = self._debounce_pending is None
            self._debounce_pending = event

            if self._debounce_timer is not None:
                self._debounce_timer.cancel()
                self._debounce_timer = None

            fire_leading = (
                self._debounce_leading and is_first and not self._debounce_fired_leading
            )
            if fire_leading:
                self._debounce_fired_leading = True

        if fire_leading:
            try:
                handler(event)
            except Exception:
                import logging

                logging.getLogger("Codis.Events").exception(
                    "debounce leading handler error"
                )

        def _fire() -> None:
            with self._lock:
                ev = self._debounce_pending
                self._debounce_pending = None
                self._debounce_timer = None
                self._debounce_fired_leading = False
            if ev is not None:
                try:
                    handler(ev)
                except Exception:
                    import logging

                    logging.getLogger("Codis.Events").exception(
                        "debounce trailing handler error"
                    )

        with self._lock:
            t = threading.Timer(ms / 1000.0, _fire)
            t.daemon = True
            self._debounce_timer = t
        t.start()


class WindowedStream:
    """Agrupa eventos em janelas temporais fixas (tumbling window)."""

    def __init__(self, source: EventStream, size_ms: int) -> None:
        """Cria uma janela tumbling de ``size_ms`` ms sobre o stream fonte."""
        self._source = source
        self._size_ms = size_ms
        self._buffer: list[Event] = []
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._handler: Callable[[list[Event]], None] | None = None
        self._token: SubscriptionToken | None = None

    def subscribe(self, handler: Callable[[list[Event]], None]) -> SubscriptionToken:
        """Ativa a janela e registra ``handler`` para receber lotes de eventos."""
        self._handler = handler
        self._start_timer()

        def _collect(event: Event) -> None:
            with self._lock:
                self._buffer.append(event)

        self._token = self._source.subscribe(_collect)
        return self._token

    def _start_timer(self) -> None:
        t = threading.Timer(self._size_ms / 1000.0, self._flush)
        t.daemon = True
        t.start()
        with self._lock:
            self._timer = t

    def _flush(self) -> None:
        with self._lock:
            batch = list(self._buffer)
            self._buffer.clear()
        if batch and self._handler:
            try:
                self._handler(batch)
            except Exception:
                import logging

                logging.getLogger("Codis.Events").exception(
                    "WindowedStream handler error"
                )
        self._start_timer()

    def dispose(self) -> None:
        """Cancela o timer e remove a subscrição do stream fonte."""
        with self._lock:
            if self._timer:
                self._timer.cancel()
        if self._token:
            self._source._bus.unsubscribe(self._token)


class IdleStream:
    """Dispara apenas quando nenhum evento chegou por ``idle_ms`` ms.

    Indispensável em IDEs: lint, hover e inlay hints só devem processar
    quando o usuário parou de digitar.  Cada evento recebido reinicia o
    timer; o handler é chamado com o ÚLTIMO evento recebido após o silêncio.

    Exemplo::

        bus.stream(EventKind.EDITOR_CHANGE)
           .filter(lambda e: e.payload.get(\"lang\") == \"python\")
           .idle(800)
           .subscribe(lambda e: run_lint(e.payload[\"path\"]))
    """

    def __init__(self, source: EventStream, idle_ms: int) -> None:
        """Inicializa um stream idle que emite eventos em intervalo fixo."""
        self._source = source
        self._idle_ms = idle_ms
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._last: Event | None = None
        self._handler: Handler | None = None
        self._token: SubscriptionToken | None = None

    def subscribe(self, handler: Handler) -> SubscriptionToken:
        """Ativa o stream e registra ``handler`` para disparar após o período de silêncio."""
        self._handler = handler

        def _on_event(event: Event) -> None:
            with self._lock:
                self._last = event
                if self._timer is not None:
                    self._timer.cancel()
                t = threading.Timer(self._idle_ms / 1000.0, self._fire)
                t.daemon = True
                self._timer = t
            t.start()

        self._token = self._source.subscribe(_on_event)
        return self._token

    def _fire(self) -> None:
        with self._lock:
            ev = self._last
            self._last = None
            self._timer = None
        if ev is not None and self._handler is not None:
            try:
                self._handler(ev)
            except Exception:
                import logging

                logging.getLogger("Codis.Events").exception(
                    "IdleStream handler error")

    def dispose(self) -> None:
        """Cancela o timer de idle e remove a subscrição do stream fonte."""
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None
        if self._token:
            self._source._bus.unsubscribe(self._token)


class PauseableStream:
    """Stream que pode ser pausado e retomado sem re-subscrever ao bus.

    Durante a pausa os eventos são descartados (não bufferizados).
    Útil para desligar temporariamente processamento caro (ex: durante
    carregamento de arquivo grande).

    Exemplo::

        ps = bus.stream(EventKind.EDITOR_CHANGE).pauseable()
        ps.subscribe(handler)
        ps.pause()    # handler não é chamado enquanto pausado
        ps.resume()   # retoma normalmente
        ps.dispose()  # cancela subscrição
    """

    def __init__(self, source: EventStream) -> None:
        """Inicializa um stream pausável."""
        self._source = source
        self._paused = False
        self._lock = threading.Lock()
        self._token: SubscriptionToken | None = None
        self._handler: Handler | None = None

    def subscribe(self, handler: Handler) -> SubscriptionToken:
        """Ativa o stream e registra ``handler``, que será ignorado enquanto pausado."""
        self._handler = handler

        def _guarded(event: Event) -> None:
            with self._lock:
                if self._paused:
                    return
            if self._handler:
                self._handler(event)

        self._token = self._source.subscribe(_guarded)
        return self._token

    def pause(self) -> None:
        """Pausa o stream: eventos chegam mas ``handler`` não é chamado."""
        with self._lock:
            self._paused = True

    def resume(self) -> None:
        """Retoma a entrega de eventos ao handler registrado."""
        with self._lock:
            self._paused = False

    @property
    def paused(self) -> bool:
        """Retorna ``True`` se o stream está atualmente pausado."""
        with self._lock:
            return self._paused

    def dispose(self) -> None:
        """Cancela a subscrição e libera recursos."""
        if self._token:
            self._source._bus.unsubscribe(self._token)
            self._token = None
