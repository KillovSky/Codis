"""Codis - EditHeatmap: mapa de calor de edições por linha.

Funcionalidade exclusiva do Codis - não existe em nenhum outro editor.

Rastreia quantas vezes cada linha foi editada durante a sessão e exibe
um gradiente de calor no gutter (azul frio -> vermelho quente). Ajuda o
desenvolvedor a identificar:

- Linhas problemáticas que precisam de refatoração constante.
- Hot spots de complexidade onde bugs tendem a se concentrar.
- Padrões de edição que revelam dívida técnica.

Integração
----------
O EditHeatmap recebe eventos do EventBus (``editor.change`` + payload
de linha) e acumula contagens por linha. O gutter do editor chama
:meth:`get_line_color` para colorir cada linha conforme a intensidade.

Uso
---
    hm = EditHeatmap()
    hm.record_edit(line_number)
    color = hm.get_line_color(line_number)   # '#rrggbb' ou None
    hm.reset()
"""

from __future__ import annotations

import colorsys
import logging
import threading
import time
from collections import defaultdict
from collections.abc import Callable

logger = logging.getLogger("Codis.Heatmap")


class EditHeatmap:
    """Acumula contagens de edições por linha e gera cores de calor.

    Thread-safe - pode receber chamadas de múltiplas threads (eventos
    do EventBus rodando em pool assíncrono).

    Parameters
    ----------
    decay_s:      Tempo de decaimento em segundos. Edições antigas
                  têm peso reduzido exponencialmente. 0 = sem decaimento.
    max_heat:     Número de edições que satura a escala (cor máxima).
    on_update:    Callback chamado quando o mapa muda (para re-desenhar o gutter).
    """

    _COLD = (0.60, 0.70, 0.85)
    _WARM = (0.15, 0.85, 0.95)
    _HOT = (0.00, 1.00, 1.00)

    def __init__(
        self,
        decay_s: float = 0.0,
        max_heat: int = 20,
        on_update: Callable[[], None] | None = None,
    ) -> None:
        """Inicializa o heatmap de edições para o widget ``txt``."""
        self._decay_s = decay_s
        self._max_heat = max(1, max_heat)
        self._on_update = on_update
        self._lock = threading.Lock()

        self._edits: dict[int, list[float]] = defaultdict(list)

    def record_edit(self, line: int) -> None:
        """Registra uma edição na linha ``line`` (1-based)."""
        if line < 1:
            return
        with self._lock:
            self._edits[line].append(time.monotonic())
        self._notify()

    def get_line_color(self, line: int) -> str | None:
        """Retorna a cor de calor para ``line`` ou None se não editada.

        Returns '#rrggbb' na escala azul -> laranja -> vermelho.
        """
        heat = self._heat(line)
        if heat <= 0:
            return None
        return self._heat_to_color(heat)

    def reset(self) -> None:
        """Zera o mapa de calor."""
        with self._lock:
            self._edits.clear()
        self._notify()

    def summary(self) -> dict[int, int]:
        """Retorna dict linha -> contagem de edições (sem decaimento)."""
        with self._lock:
            return {ln: len(ts) for ln, ts in self._edits.items()}

    def _notify(self) -> None:
        """Aciona o callback ``on_update``, se configurado, suprimindo falhas.

        Falhas no callback do consumidor (ex.: redesenho de gutter) não
        devem propagar e interromper o fluxo de edição do usuário.
        """
        if not self._on_update:
            return
        try:
            self._on_update()
        except Exception:
            logger.debug("Suppressed error", exc_info=True)

    def _heat(self, line: int) -> float:
        """Calcula o calor acumulado (com decaimento se configurado)."""
        with self._lock:
            timestamps = list(self._edits.get(line, []))
        if not timestamps:
            return 0.0
        if self._decay_s <= 0:
            return float(len(timestamps))
        now = time.monotonic()
        heat = 0.0
        for ts in timestamps:
            age = now - ts
            heat += max(0.0, 1.0 - age / self._decay_s)
        return heat

    def _heat_to_color(self, heat: float) -> str:
        """Converte valor de calor para hex RGB."""
        t = min(1.0, heat / self._max_heat)
        if t <= 0.5:
            ratio = t * 2
            h = self._COLD[0] + (self._WARM[0] - self._COLD[0]) * ratio
            s = self._COLD[1] + (self._WARM[1] - self._COLD[1]) * ratio
            v = self._COLD[2] + (self._WARM[2] - self._COLD[2]) * ratio
        else:
            ratio = (t - 0.5) * 2
            h = self._WARM[0] + (self._HOT[0] - self._WARM[0]) * ratio
            s = self._WARM[1] + (self._HOT[1] - self._WARM[1]) * ratio
            v = self._WARM[2] + (self._HOT[2] - self._WARM[2]) * ratio
        r, g, b = colorsys.hsv_to_rgb(h, s, v)
        return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"
