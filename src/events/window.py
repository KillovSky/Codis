"""Codis Events - WindowManager: janelas temporais sobre streams."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .bus import Event

logger = logging.getLogger("Codis.Events.Window")
WindowHandler = Callable[[list["Event"]], None]


class TumblingWindow:
    """Janela temporal fixa sem sobreposição.

    Coleta eventos durante ``size_ms`` ms e entrega em lote ao fechar.
    Janelas vazias ou abaixo de ``min_events`` são silenciosamente ignoradas.
    """

    def __init__(
        self, size_ms: int, on_close: WindowHandler, *, min_events: int = 1
    ) -> None:
        """Cria a janela e inicia o timer automático de fechamento.

        Args:
            size_ms:    Duração da janela em milissegundos.
            on_close:   Callback chamado com a lista de eventos ao fechar.
            min_events: Número mínimo de eventos para invocar ``on_close``.
        """
        self._size_ms = size_ms
        self._on_close = on_close
        self._min_events = max(1, min_events)
        self._buffer: list[Event] = []
        self._lock = threading.Lock()
        self._stopped = False
        self._timer: threading.Timer | None = None
        self._start_timer()

    def push(self, event: Event) -> None:
        """Adiciona ``event`` ao buffer da janela atual."""
        with self._lock:
            if not self._stopped:
                self._buffer.append(event)

    def flush(self) -> None:
        """Fecha a janela imediatamente e entrega o batch, sem aguardar o timer."""
        self._close()

    def stop(self) -> None:
        """Para a janela, cancela o timer e impede novos recebimentos."""
        with self._lock:
            self._stopped = True
            if self._timer:
                self._timer.cancel()
                self._timer = None

    def _start_timer(self) -> None:
        t = threading.Timer(self._size_ms / 1000.0, self._close)
        t.daemon = True
        t.start()
        with self._lock:
            self._timer = t

    def _close(self) -> None:
        with self._lock:
            if self._stopped:
                return
            batch = list(self._buffer)
            self._buffer.clear()
        if len(batch) >= self._min_events:
            try:
                self._on_close(batch)
            except Exception:
                logger.exception("TumblingWindow on_close error")
        self._start_timer()


class SlidingWindow:
    """Janela temporal deslizante com sobreposição - thread único.

    Usa um único thread de background que dorme com
    ``threading.Event.wait(timeout)`` e acorda no step_ms - nenhuma thread
    extra é criada durante a operação normal.

    Parameters
    ----------
    size_ms:    Duração da janela em milissegundos.
    step_ms:    Intervalo entre avaliações em milissegundos.
    on_close:   Callback chamado a cada step com a lista de eventos da janela.
    min_events: Número mínimo de eventos para disparar o callback.
    """

    def __init__(
        self,
        size_ms: int,
        step_ms: int,
        on_close: WindowHandler,
        *,
        min_events: int = 1,
    ) -> None:
        """Inicializa a janela deslizante de eventos."""
        self._size_s = size_ms / 1000.0
        self._step_s = step_ms / 1000.0
        self._on_close = on_close
        self._min_events = max(1, min_events)
        self._buffer: deque[tuple[float, Event]] = deque()
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="codis-sliding-window",
            daemon=True,
        )
        self._thread.start()

    def push(self, event: Event) -> None:
        """Adiciona ``event`` ao buffer deslizante com timestamp atual."""
        ts = time.monotonic()
        with self._lock:
            self._buffer.append((ts, event))

    def stop(self) -> None:
        """Sinaliza o thread de avaliação para encerrar."""
        self._stop_event.set()

    def _run(self) -> None:
        """Thread de background: avalia a janela a cada step_s."""
        while not self._stop_event.wait(timeout=self._step_s):
            self._evaluate()

    def _evaluate(self) -> None:
        now = time.monotonic()
        cutoff = now - self._size_s
        with self._lock:
            while self._buffer and self._buffer[0][0] < cutoff:
                self._buffer.popleft()
            batch = [ev for _, ev in self._buffer]
        if len(batch) >= self._min_events:
            try:
                self._on_close(batch)
            except Exception:
                logger.exception("SlidingWindow on_close error")


class WindowManager:
    """Gerenciador centralizado de janelas temporais nomeadas."""

    def __init__(self) -> None:
        """Inicializa o gerenciador de janelas deslizantes de eventos."""
        self._windows: dict[str, TumblingWindow | SlidingWindow] = {}
        self._lock = threading.Lock()

    def tumbling(
        self, name: str, size_ms: int, on_close: WindowHandler, *, min_events: int = 1
    ) -> TumblingWindow:
        """Cria e registra uma :class:`TumblingWindow` com o nome dado, substituindo a anterior."""
        w = TumblingWindow(size_ms, on_close, min_events=min_events)
        with self._lock:
            self._stop_one(name)
            self._windows[name] = w
        return w

    def sliding(
        self,
        name: str,
        size_ms: int,
        step_ms: int,
        on_close: WindowHandler,
        *,
        min_events: int = 1,
    ) -> SlidingWindow:
        """Cria e registra uma :class:`SlidingWindow` com o nome dado, substituindo a anterior."""
        w = SlidingWindow(size_ms, step_ms, on_close, min_events=min_events)
        with self._lock:
            self._stop_one(name)
            self._windows[name] = w
        return w

    def push(self, name: str, event: Event) -> None:
        """Entrega ``event`` para a janela registrada sob ``name`` (sem efeito se não existir)."""
        with self._lock:
            w = self._windows.get(name)
        if w:
            w.push(event)

    def stop(self, name: str) -> None:
        """Para e remove a janela registrada sob ``name``."""
        with self._lock:
            self._stop_one(name)

    def stop_all(self) -> None:
        """Para e remove todas as janelas registradas."""
        with self._lock:
            for name in list(self._windows):
                self._stop_one(name)

    def _stop_one(self, name: str) -> None:
        w = self._windows.pop(name, None)
        if w:
            w.stop()
