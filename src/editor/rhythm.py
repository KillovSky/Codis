"""Codis - CodeRhythm: análise de ritmo de digitação em tempo real.

Funcionalidade exclusiva do Codis.

Rastreia o ritmo de digitação do desenvolvedor e exibe estatísticas
de produtividade em tempo real:

- WPM (words per minute) de código.
- "Flow state" detector: identifica quando o desenvolvedor está em estado
  de fluxo (digitação regular, sem longas pausas).
- Mapa de padrões de pausa (onde o desenvolvedor hesita).
- Sequências de edições repetitivas (refatoração em loop).
- Estimativa de tempo restante para completar o arquivo baseado no ritmo.

Integração
----------
O CodeRhythm recebe keystroke events via :meth:`record_keystroke` e expõe
métricas via :meth:`metrics`. Integrado ao StatusBar e ao EventBus.

Uso
---
    rhythm = CodeRhythm()
    rhythm.record_keystroke()           # chamado a cada KeyPress no editor
    m = rhythm.metrics()
    print(m["wpm"], m["flow_state"])    # ex: 42.3, "flow"
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_WPM_WINDOW_S = 60.0
_CHARS_PER_WORD = 5
_FLOW_BREAK_S = 3.0
_SESSION_RESET_S = 120.0
_MIN_KEYSTROKES = 10


@dataclass
class RhythmMetrics:
    """Snapshot das métricas de ritmo em um dado momento."""

    wpm: float = 0.0
    cpm: float = 0.0
    flow_state: str = "idle"
    session_chars: int = 0
    session_s: float = 0.0
    pause_count: int = 0
    streak_s: float = 0.0
    last_pause_s: float = 0.0


class CodeRhythm:
    """Rastreador de ritmo de digitação com detecção de flow state.

    Thread-safe: ``record_keystroke()`` pode ser chamado da thread da UI.
    ``metrics()`` pode ser chamado de qualquer thread.
    """

    def __init__(
        self,
        on_flow_change: Callable[[str], None] | None = None,
    ) -> None:
        """Inicializa o rastreador de ritmo.

        Parameters
        ----------
        on_flow_change:
            Callback chamado quando o flow state muda.
            Recebe a nova string de estado: ``"idle"``, ``"warming"``,
            ``"flow"`` ou ``"deep_flow"``.
        """
        self._lock = threading.Lock()
        self._on_flow_change = on_flow_change

        self._timestamps: deque[float] = deque()

        self._session_start: float | None = None
        self._session_chars: int = 0
        self._pause_count: int = 0
        self._last_ks: float | None = None
        self._streak_start: float | None = None
        self._last_pause_s: float = 0.0
        self._flow_state: str = "idle"

    def record_keystroke(self, char: str = "") -> None:
        """Registra um keystroke.  Deve ser chamado a cada tecla no editor."""
        if char and not char.isprintable():
            return
        now = time.monotonic()
        with self._lock:
            cutoff = now - _WPM_WINDOW_S
            while self._timestamps and self._timestamps[0] < cutoff:
                self._timestamps.popleft()
            self._timestamps.append(now)

            if self._last_ks is not None:
                gap = now - self._last_ks
                if gap >= _FLOW_BREAK_S:
                    self._pause_count += 1
                    self._last_pause_s = gap
                    self._streak_start = now
                if gap >= _SESSION_RESET_S:
                    self._session_chars = 0
                    self._session_start = now
                    self._pause_count = 0
            else:
                self._session_start = now
                self._streak_start = now

            self._last_ks = now
            if self._session_start is None:
                self._session_start = now
            self._session_chars += 1

            new_state = self._compute_flow(now)
            old_state = self._flow_state
            self._flow_state = new_state

        if new_state != old_state and self._on_flow_change:
            try:
                self._on_flow_change(new_state)
            except Exception:
                logger.debug("Suppressed error", exc_info=True)

    def _window_cpm(self) -> float:
        """Calcula caracteres por minuto a partir da janela de timestamps atual.

        Deve ser chamado com ``self._lock`` já adquirido. Usado tanto por
        :meth:`metrics` quanto por :meth:`_compute_flow`, que derivam suas
        respectivas taxas (CPM/WPM) da mesma janela deslizante de
        keystrokes.

        Returns:
            Caracteres por minuto, ou ``0.0`` se não houver amostras
            suficientes para estimar uma taxa.
        """
        if len(self._timestamps) < 2:
            return 0.0
        window_s = self._timestamps[-1] - self._timestamps[0]
        if window_s <= 0:
            return 0.0
        return (len(self._timestamps) / window_s) * 60.0

    def metrics(self) -> RhythmMetrics:
        """Retorna um snapshot das métricas de ritmo atuais."""
        now = time.monotonic()
        with self._lock:
            if len(self._timestamps) < 2:
                return RhythmMetrics(flow_state=self._flow_state)

            cpm = self._window_cpm()
            wpm = cpm / _CHARS_PER_WORD
            session_s = (
                now - self._session_start) if self._session_start else 0.0
            streak_s = (
                now - self._streak_start) if self._streak_start else 0.0
            return RhythmMetrics(
                wpm=round(wpm, 1),
                cpm=round(cpm, 1),
                flow_state=self._flow_state,
                session_chars=self._session_chars,
                session_s=round(session_s, 1),
                pause_count=self._pause_count,
                streak_s=round(streak_s, 1),
                last_pause_s=round(self._last_pause_s, 1),
            )

    def reset(self) -> None:
        """Zera todas as métricas."""
        with self._lock:
            self._timestamps.clear()
            self._session_start = None
            self._session_chars = 0
            self._pause_count = 0
            self._last_ks = None
            self._streak_start = None
            self._last_pause_s = 0.0
            self._flow_state = "idle"

    def _compute_flow(self, now: float) -> str:
        """Determina o flow state atual (deve ser chamado com lock)."""
        if len(self._timestamps) < _MIN_KEYSTROKES:
            return "idle"

        cpm = self._window_cpm()
        if cpm == 0.0:
            return "idle"
        wpm = cpm / _CHARS_PER_WORD

        streak_s = (now - self._streak_start) if self._streak_start else 0.0

        if wpm < 5:
            return "idle"
        if wpm < 15 or streak_s < 30:
            return "warming"
        if wpm >= 25 and streak_s >= 120:
            return "deep_flow"
        return "flow"
