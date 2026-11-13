"""Codis Events - EventCorrelator: correlação temporal entre streams.

O correlacionador detecta padrões compostos: um conjunto de eventos de
tipos diferentes que devem ocorrer dentro de uma janela de tempo máxima.

Casos de uso no Codis
---------------------
- Detectar "edição seguida de salvar em < 500 ms" para otimizar lint.
- Correlacionar ``LSP_HOVER`` com ``EDITOR_CURSOR_MOVE`` para pré-buscar docs.
- Agrupar ``EDITOR_CHANGE`` + ``SYNTAX_LANG_CHANGE`` para re-highlight inteligente.
- Detectar sequência "cursor parado por 800 ms em palavra" para inlay hints.

Uso
---
    corr = EventCorrelator(window_ms=500)
    corr.add_trigger(EventKind.EDITOR_CHANGE)
    corr.add_trigger(EventKind.FILE_SAVE)
    corr.on_match(lambda events: print("edição + save detectados", events))
    corr.attach(bus)

    # Limpar ao encerrar
    corr.detach()
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .bus import Event, EventBus, SubscriptionToken
    from .kinds import EventKind

logger = logging.getLogger("Codis.Events.Correlator")

MatchHandler = Callable[[dict[str, "Event"]], None]


class EventCorrelator:
    """Detecta padrões compostos de eventos dentro de uma janela temporal.

    O correlacionador mantém um buffer de "slots" - um por tipo de evento
    dentro de ``window_ms`` ms, o ``match_handler`` é invocado.

    Após um match (ou timeout), os slots são limpos e o correlacionador
    reinicia automaticamente.

    Thread-safe.

    Parameters
    ----------
    window_ms:     Janela temporal máxima entre o primeiro e o último evento.
    reset_on_miss: Se True, qualquer evento fora da janela reinicia o ciclo.
    """

    def __init__(
        self,
        window_ms: int,
        match_handler: MatchHandler | None = None,
        *,
        reset_on_miss: bool = True,
    ) -> None:
        """Inicializa o correlacionador de eventos."""
        self._window_s = window_ms / 1000.0
        self._match_handler: MatchHandler | None = match_handler
        self._reset_on_miss = reset_on_miss
        self._triggers: list[str] = []
        self._slots: dict[str, Event | None] = {}
        self._first_ts: float | None = None
        self._lock = threading.Lock()
        self._tokens: list[SubscriptionToken] = []
        self._bus: EventBus | None = None

    def add_trigger(self, topic: str | EventKind) -> EventCorrelator:
        """Registra um tipo de evento como trigger obrigatório do padrão."""
        from .kinds import EventKind as EK

        key = topic.value if isinstance(topic, EK) else str(topic)
        self._triggers.append(key)
        self._slots[key] = None
        return self

    def on_match(self, handler: MatchHandler) -> EventCorrelator:
        """Define o callback invocado quando o padrão completo for detectado."""
        self._match_handler = handler
        return self

    def attach(self, bus: EventBus) -> EventCorrelator:
        """Registra o correlacionador no bus, subscrevendo cada trigger."""
        if not self._triggers:
            raise ValueError(
                "Nenhum trigger registrado - chame add_trigger() antes de attach()"
            )
        self._bus = bus
        for topic in self._triggers:
            token = bus.subscribe(topic, self._handle_event, priority=10)
            self._tokens.append(token)
        return self

    def detach(self) -> None:
        """Remove todas as subscrições do bus."""
        if self._bus:
            for token in self._tokens:
                self._bus.unsubscribe(token)
        self._tokens.clear()
        self._bus = None

    def reset(self) -> None:
        """Reinicia o estado de correlação manualmente."""
        with self._lock:
            self._first_ts = None
            for key in self._slots:
                self._slots[key] = None

    def _handle_event(self, event: Event) -> None:
        """Processa cada evento recebido e verifica se o padrão completo foi detectado."""
        topic = event.kind
        if topic not in self._slots:
            return

        now = time.monotonic()

        with self._lock:
            if self._first_ts is not None and now - self._first_ts > self._window_s:
                if self._reset_on_miss:
                    for key in self._slots:
                        self._slots[key] = None
                    self._first_ts = None

            if self._first_ts is None:
                self._first_ts = now
            self._slots[topic] = event

            if all(v is not None for v in self._slots.values()):
                matched = dict(self._slots)

                for key in self._slots:
                    self._slots[key] = None
                self._first_ts = None
            else:
                matched = {}

        if matched and self._match_handler:
            try:
                self._match_handler(matched)
            except Exception:
                logger.exception("EventCorrelator match_handler error")


class SequenceDetector:
    """Detecta uma sequência ordenada de eventos dentro de uma janela temporal.

    Diferente do ``EventCorrelator`` (que não impõe ordem), o ``SequenceDetector``
    exige que os eventos ocorram na ordem em que foram registrados via
    :meth:`then`.

    Exemplo: detectar "cursor parou -> 800 ms passaram -> usuário não digitou"
    para disparar hover/inlay hints.

    Uso
    ---
        seq = SequenceDetector(window_ms=1000)
        seq.then(EventKind.EDITOR_CURSOR_MOVE)
        seq.then(EventKind.EDITOR_FOCUS)
        seq.on_complete(lambda evts: show_hints(evts))
        seq.attach(bus)
    """

    def __init__(self, window_ms: int) -> None:
        """Cria um detector de sequência com janela temporal de ``window_ms`` ms."""
        self._window_s = window_ms / 1000.0
        self._sequence: list[str] = []
        self._idx: int = 0
        self._first_ts: float | None = None
        self._collected: list[Event] = []
        self._handler: MatchHandler | None = None
        self._lock = threading.Lock()
        self._tokens: list[SubscriptionToken] = []
        self._bus: EventBus | None = None

    def then(self, topic: str | EventKind) -> SequenceDetector:
        """Adiciona um passo à sequência."""
        from .kinds import EventKind as EK

        key = topic.value if isinstance(topic, EK) else str(topic)
        self._sequence.append(key)
        return self

    def on_complete(self, handler: Callable[[list[Event]], None]) -> SequenceDetector:
        """Define o callback invocado ao detectar a sequência completa."""
        self._handler = handler
        return self

    def attach(self, bus: EventBus) -> SequenceDetector:
        """Registra o detector no bus, subscrevendo cada tópico da sequência."""
        self._bus = bus
        for topic in set(self._sequence):
            token = bus.subscribe(topic, self._handle_event, priority=10)
            self._tokens.append(token)
        return self

    def detach(self) -> None:
        """Remove todas as subscrições do bus e para a detecção."""
        if self._bus:
            for token in self._tokens:
                self._bus.unsubscribe(token)
        self._tokens.clear()

    def _handle_event(self, event: Event) -> None:
        now = time.monotonic()
        with self._lock:
            if self._idx >= len(self._sequence):
                return

            expected = self._sequence[self._idx]
            if event.kind != expected:
                return

            if self._first_ts is not None and now - self._first_ts > self._window_s:
                self._idx = 0
                self._collected.clear()
                self._first_ts = None

            if self._idx == 0:
                self._first_ts = now

            self._collected.append(event)
            self._idx += 1

            if self._idx == len(self._sequence):
                complete = list(self._collected)
                self._idx = 0
                self._collected.clear()
                self._first_ts = None
            else:
                complete = []

        if complete and self._handler:
            try:
                self._handler(complete)
            except Exception:
                logger.exception("SequenceDetector on_complete error")
