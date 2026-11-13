"""Codis Events - RESA: Reactive Event Stream Architecture.

Componentes
-----------
EventBus
    Barramento central com roteamento por tópico, prioridade, wildcard,
    copy-under-lock dispatch, emit_tk() (bridge Tk), emit_batch() e
    SubscriptionGroup para gestão de ciclo de vida.

EventStream
    Stream lazy com operadores encadeáveis: filter, map, debounce (com
    leading-edge), throttle, take, skip, distinct, aggregate, window,
    idle, pauseable.

IdleStream
    Dispara apenas após N ms de silêncio - essencial para lint/hover/hints.

PauseableStream
    Pode ser pausado/retomado sem re-subscrever.

Pipeline
    Cadeia nomeada de estágios com backpressure controlado e métricas.

WindowManager / TumblingWindow / SlidingWindow
    Janelas temporais. SlidingWindow usa thread único (não Timer por step).

EventCorrelator / SequenceDetector
    Correlação e detecção de sequências ordenadas de eventos.

Uso mínimo
----------
    from src.events import bus, EventKind

    token = bus.subscribe(EventKind.EDITOR_CHANGE, my_handler)
    bus.emit(EventKind.EDITOR_CHANGE, {"path": str(path)})
    bus.unsubscribe(token)

    bus.stream(EventKind.EDITOR_CHANGE) \\
       .debounce(300, leading=True) \\
       .filter(lambda e: e.payload.get("lang") == "python") \\
       .subscribe(handler)

    # IdleStream - só dispara quando usuário parou de digitar
    bus.stream(EventKind.EDITOR_CHANGE) \\
       .idle(800) \\
       .subscribe(lambda e: run_lint(e.payload["path"]))

    # SubscriptionGroup (context manager)
    with bus.group() as g:
        g.subscribe(EventKind.EDITOR_CHANGE, handler_a)
        g.subscribe(EventKind.FILE_SAVE,     handler_b)
    # ambas canceladas ao sair
"""

from __future__ import annotations

from .bus import Event, EventBus, SubscriptionGroup, SubscriptionToken
from .correlator import EventCorrelator, SequenceDetector
from .kinds import EventKind
from .pipeline import Pipeline, Stage
from .stream import EventStream, IdleStream, PauseableStream, WindowedStream
from .window import SlidingWindow, TumblingWindow, WindowManager

bus: EventBus = EventBus()

__all__ = [
    "bus",
    "EventBus",
    "Event",
    "EventKind",
    "EventStream",
    "IdleStream",
    "PauseableStream",
    "WindowedStream",
    "Pipeline",
    "Stage",
    "SubscriptionToken",
    "SubscriptionGroup",
    "WindowManager",
    "TumblingWindow",
    "SlidingWindow",
    "EventCorrelator",
    "SequenceDetector",
]
