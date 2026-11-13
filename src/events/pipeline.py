"""Codis Events - Pipeline: cadeia de estágios de transformação com backpressure.

A Pipeline processa eventos em estágios sequenciais nomeados, cada um
rodando em sua própria thread (ou compartilhando pool).  Backpressure é
controlado por filas com capacidade máxima configurável - produtores bloqueiam
ou descartam quando o consumidor está saturado.

Uso
---
    pipe = Pipeline("lint")
    pipe.add_stage("debounce",  debounce_stage)
    pipe.add_stage("run_lint",  run_lint_stage, workers=2)
    pipe.add_stage("deliver",   deliver_stage)
    pipe.start()
    pipe.push(event)
"""

from __future__ import annotations

import contextlib
import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .bus import Event

logger = logging.getLogger("Codis.Events.Pipeline")

StageFunc = Callable[["Event"], "Event | None"]


@dataclass
class StageMetrics:
    """Métricas de desempenho de um estágio da pipeline."""

    processed: int = 0
    dropped: int = 0
    errors: int = 0
    total_time_ns: int = 0

    @property
    def avg_us(self) -> float:
        """Tempo médio de processamento em microssegundos."""
        if self.processed == 0:
            return 0.0
        return (self.total_time_ns / self.processed) / 1_000

    @property
    def throughput(self) -> float:
        """Eventos processados por segundo (aproximado pelo tempo total)."""
        total_s = self.total_time_ns / 1e9
        if total_s == 0:
            return 0.0
        return self.processed / total_s


@dataclass
class Stage:
    """Estágio de processamento da pipeline.

    Parameters
    ----------
    name:       Identificador legível do estágio.
    func:       Função de transformação: recebe Event, retorna Event ou None.
                Retornar None descarta o evento (não passa para o próximo estágio).
    workers:    Número de threads paralelas para este estágio.
    capacity:   Capacidade máxima da fila de entrada (backpressure).
    drop_policy: ``"block"`` bloqueia o produtor; ``"discard"`` descarta o evento.
    """

    name: str
    func: StageFunc
    workers: int = 1
    capacity: int = 256
    drop_policy: str = "block"


class _StageRunner:
    """Executor interno de um estágio - gerencia fila, threads e métricas."""

    _SENTINEL = object()

    def __init__(self, stage: Stage, next_runner: _StageRunner | None) -> None:
        """Inicializa o runner com a fila de entrada e aponta para o próximo estágio."""
        self._stage = stage
        self._next = next_runner
        self._q: queue.Queue = queue.Queue(maxsize=stage.capacity)
        self._metrics = StageMetrics()
        self._threads: list[threading.Thread] = []
        self._stopped = False

    def start(self) -> None:
        """Inicia os workers do estágio."""
        for i in range(self._stage.workers):
            t = threading.Thread(
                target=self._worker,
                name=f"pipeline-{self._stage.name}-{i}",
                daemon=True,
            )
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        """Envia a poison pill para cada worker, sinalizando encerramento."""
        self._stopped = True
        for _ in self._threads:
            with contextlib.suppress(queue.Full):
                self._q.put_nowait(self._SENTINEL)

    def push(self, event: Event) -> bool:
        """Enfileira um evento.  Comportamento definido por ``drop_policy``."""
        if self._stopped:
            return False
        try:
            if self._stage.drop_policy == "block":
                self._q.put(event, timeout=5.0)
            else:
                self._q.put_nowait(event)
            return True
        except queue.Full:
            self._metrics.dropped += 1
            logger.debug(
                "Stage '%s' descartou evento (fila cheia)", self._stage.name)
            return False

    @property
    def metrics(self) -> StageMetrics:
        """Snapshot das métricas acumuladas do estágio."""
        return self._metrics

    @property
    def qsize(self) -> int:
        """Número de itens atualmente na fila de entrada do estágio."""
        return self._q.qsize()

    def _worker(self) -> None:
        while True:
            try:
                item = self._q.get(timeout=1.0)
            except queue.Empty:
                if self._stopped:
                    return
                continue

            if item is self._SENTINEL:
                return

            t0 = time.monotonic_ns()
            try:
                result = self._stage.func(item)
                self._metrics.processed += 1
                self._metrics.total_time_ns += time.monotonic_ns() - t0
                if result is not None and self._next is not None:
                    self._next.push(result)
            except Exception:
                self._metrics.errors += 1
                logger.exception("Erro no estágio '%s'", self._stage.name)
            finally:
                self._q.task_done()


class Pipeline:
    """Cadeia de estágios de processamento de eventos com backpressure.

    Cada estágio transforma o evento e passa para o próximo.  Retornar
    ``None`` de um estágio descarta o evento silenciosamente.

    A pipeline é thread-safe: ``push()`` pode ser chamado de qualquer thread.

    Métricas por estágio são acessíveis via :meth:`metrics`.
    """

    def __init__(self, name: str) -> None:
        """Cria uma pipeline nomeada sem estágios (use :meth:`add_stage` antes de :meth:`start`)."""
        self._name = name
        self._stages: list[Stage] = []
        self._runners: list[_StageRunner] = []
        self._started = False

    def add_stage(
        self,
        name: str,
        func: StageFunc,
        *,
        workers: int = 1,
        capacity: int = 256,
        drop_policy: str = "block",
    ) -> Pipeline:
        """Adiciona um estágio ao final da pipeline.  Deve ser chamado antes de :meth:`start`."""
        if self._started:
            raise RuntimeError(
                "Pipeline já iniciada - não é possível adicionar estágios"
            )
        self._stages.append(
            Stage(
                name=name,
                func=func,
                workers=workers,
                capacity=capacity,
                drop_policy=drop_policy,
            )
        )
        return self

    def start(self) -> None:
        """Constrói os runners em ordem reversa, encadeia-os e inicia todos."""
        if self._started:
            return
        if not self._stages:
            raise ValueError("Pipeline sem estágios")

        next_runner: _StageRunner | None = None
        for stage in reversed(self._stages):
            runner = _StageRunner(stage, next_runner)
            self._runners.insert(0, runner)
            next_runner = runner

        for runner in self._runners:
            runner.start()

        self._started = True
        logger.debug(
            "Pipeline '%s' iniciada com %d estágio(s)", self._name, len(
                self._stages)
        )

    def push(self, event: Event) -> bool:
        """Enfileira um evento no primeiro estágio.

        Returns True se enfileirado, False se descartado ou pipeline parada.
        """
        if not self._started or not self._runners:
            return False
        return self._runners[0].push(event)

    def stop(self) -> None:
        """Para todos os runners e marca a pipeline como encerrada."""
        for runner in self._runners:
            runner.stop()
        self._started = False

    def metrics(self) -> dict[str, dict[str, Any]]:
        """Retorna snapshot das métricas de todos os estágios da pipeline."""
        return {
            runner._stage.name: {
                "processed": runner.metrics.processed,
                "dropped": runner.metrics.dropped,
                "errors": runner.metrics.errors,
                "avg_us": round(runner.metrics.avg_us, 2),
                "throughput": round(runner.metrics.throughput, 1),
                "queue_size": runner.qsize,
            }
            for runner in self._runners
        }

    def __repr__(self) -> str:
        """Representação legível do pipeline com suas métricas."""
        stages = "->".join(s.name for s in self._stages)
        return f"Pipeline('{self._name}': {stages})"
