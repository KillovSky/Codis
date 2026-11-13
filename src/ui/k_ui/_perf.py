"""
K-UI - Perfis de performance da animação cósmica.

Cada perfil controla FPS alvo, quantidade de objetos animados,
efeitos habilitados e configurações do renderizador PIL de fundo.

Perfis disponíveis:
  quantum      Máximo absoluto - para GPUs dedicadas / Linux.
  ultra        Alta qualidade com auto_fps ativo.
  high         Boa qualidade, adequado para a maioria dos PCs.
  balanced     Padrão seguro multi-plataforma.
  windows      Otimizado para o GDI do Windows (padrão no win32).
  cosmic_lite  Visual rico com custo mínimo de canvas calls.
               Usa batch rendering e menos primitivas por frame;
               recomendado quando o KUI trava a UI.
  low          Mínimo funcional.
  minimal      Alias de low.
  power_saver  Alias de windows (preserva bateria).
  ultra_plus   Alias de quantum.
"""

from __future__ import annotations

import collections as _collections
import concurrent.futures as _cf
import multiprocessing as _mp
import sys

_PERF: dict[str, dict] = {
    "quantum": {
        "stars": 240,
        "star_layers": 3,
        "fps": 120,
        "particles": True,
        "n_particles": 120,
        "rings": True,
        "scan": True,
        "meteors": True,
        "solar_system": True,
        "black_holes": True,
        "time_dilation": True,
        "subatomic": True,
        "n_subatomic": 50,
        "screensaver": True,
        "screensaver_idle_s": 90,
        "quantum_tooltips": True,
        "auto_fps": False,
        "bg_fps": 120,
        "bg_nebula_clouds": 6,
        "bg_nebula_blur": 50,
        "bg_aurora_bands": 7,
        "bg_aurora_blur": 18,
        "bg_rays": True,
        "vignette_steps": 14,
        "batch_stars": False,
        "canvas_item_cap": 0,
        "pulsars": True,
        "spiral_galaxies": True,
        "supernovas": True,
        "binary_stars": True,
        "aurora_curtain": True,
    },
    "ultra": {
        "stars": 200,
        "star_layers": 2,
        "fps": 60,
        "particles": True,
        "n_particles": 60,
        "rings": True,
        "scan": True,
        "meteors": True,
        "solar_system": True,
        "black_holes": True,
        "time_dilation": True,
        "subatomic": True,
        "n_subatomic": 25,
        "screensaver": True,
        "screensaver_idle_s": 150,
        "quantum_tooltips": True,
        "auto_fps": True,
        "bg_fps": 60,
        "bg_nebula_clouds": 5,
        "bg_nebula_blur": 46,
        "bg_aurora_bands": 6,
        "bg_aurora_blur": 16,
        "bg_rays": True,
        "vignette_steps": 10,
        "batch_stars": False,
        "canvas_item_cap": 0,
        "pulsars": True,
        "spiral_galaxies": True,
        "supernovas": True,
        "binary_stars": True,
        "aurora_curtain": True,
    },
    "high": {
        "stars": 120,
        "star_layers": 2,
        "fps": 45,
        "particles": True,
        "n_particles": 35,
        "rings": True,
        "scan": True,
        "meteors": True,
        "solar_system": True,
        "black_holes": True,
        "time_dilation": True,
        "subatomic": False,
        "n_subatomic": 0,
        "screensaver": False,
        "screensaver_idle_s": 300,
        "quantum_tooltips": True,
        "auto_fps": True,
        "bg_fps": 45,
        "bg_nebula_clouds": 4,
        "bg_nebula_blur": 42,
        "bg_aurora_bands": 5,
        "bg_aurora_blur": 14,
        "bg_rays": False,
        "vignette_steps": 7,
        "batch_stars": False,
        "canvas_item_cap": 0,
        "pulsars": True,
        "spiral_galaxies": True,
        "supernovas": True,
        "binary_stars": True,
        "aurora_curtain": True,
    },
    "balanced": {
        "stars": 70,
        "star_layers": 1,
        "fps": 40,
        "particles": True,
        "n_particles": 25,
        "rings": True,
        "scan": True,
        "meteors": True,
        "solar_system": True,
        "black_holes": True,
        "time_dilation": False,
        "subatomic": False,
        "n_subatomic": 0,
        "screensaver": False,
        "screensaver_idle_s": 600,
        "quantum_tooltips": True,
        "auto_fps": True,
        "bg_fps": 30,
        "bg_nebula_clouds": 3,
        "bg_nebula_blur": 40,
        "bg_aurora_bands": 4,
        "bg_aurora_blur": 12,
        "bg_rays": False,
        "vignette_steps": 0,
        "batch_stars": False,
        "canvas_item_cap": 0,
        "pulsars": True,
        "spiral_galaxies": True,
        "supernovas": True,
        "binary_stars": True,
        "aurora_curtain": True,
    },
    "windows": {
        "stars": 50,
        "star_layers": 1,
        "fps": 36,
        "particles": True,
        "n_particles": 15,
        "rings": True,
        "scan": False,
        "meteors": True,
        "solar_system": True,
        "black_holes": True,
        "time_dilation": False,
        "subatomic": False,
        "n_subatomic": 0,
        "screensaver": False,
        "screensaver_idle_s": 9999,
        "quantum_tooltips": False,
        "auto_fps": True,
        "bg_fps": 10,
        "bg_nebula_clouds": 2,
        "bg_nebula_blur": 30,
        "bg_aurora_bands": 2,
        "bg_aurora_blur": 8,
        "bg_rays": False,
        "vignette_steps": 0,
        "batch_stars": True,
        "canvas_item_cap": 180,
        "pulsars": False,
        "spiral_galaxies": False,
        "supernovas": False,
        "binary_stars": False,
        "aurora_curtain": False,
    },
    "cosmic_lite": {
        "stars": 40,
        "star_layers": 1,
        "fps": 24,
        "particles": True,
        "n_particles": 12,
        "rings": True,
        "scan": False,
        "meteors": True,
        "solar_system": True,
        "black_holes": True,
        "time_dilation": False,
        "subatomic": False,
        "n_subatomic": 0,
        "screensaver": False,
        "screensaver_idle_s": 9999,
        "quantum_tooltips": True,
        "auto_fps": True,
        "bg_fps": 8,
        "bg_nebula_clouds": 2,
        "bg_nebula_blur": 32,
        "bg_aurora_bands": 2,
        "bg_aurora_blur": 8,
        "bg_rays": False,
        "vignette_steps": 0,
        "batch_stars": True,
        "canvas_item_cap": 140,
        "pulsars": True,
        "spiral_galaxies": False,
        "supernovas": False,
        "binary_stars": True,
        "aurora_curtain": False,
    },
    "low": {
        "stars": 30,
        "star_layers": 1,
        "fps": 24,
        "particles": True,
        "n_particles": 12,
        "rings": False,
        "scan": False,
        "meteors": True,
        "solar_system": True,
        "black_holes": False,
        "time_dilation": False,
        "subatomic": False,
        "n_subatomic": 0,
        "screensaver": False,
        "screensaver_idle_s": 9999,
        "quantum_tooltips": False,
        "auto_fps": False,
        "bg_fps": 8,
        "bg_nebula_clouds": 0,
        "bg_nebula_blur": 0,
        "bg_aurora_bands": 2,
        "bg_aurora_blur": 8,
        "bg_rays": False,
        "vignette_steps": 0,
        "batch_stars": True,
        "canvas_item_cap": 100,
        "pulsars": False,
        "spiral_galaxies": False,
        "supernovas": False,
        "binary_stars": False,
        "aurora_curtain": False,
    },
}

_PERF["minimal"] = _PERF["low"]
_PERF["power_saver"] = _PERF["windows"]
_PERF["ultra_plus"] = _PERF["quantum"]

if sys.platform == "win32":
    _DEFAULT_PERF = "windows"
else:
    _DEFAULT_PERF = "balanced"

_PHYSICS_WORKERS = min(4, max(1, (_mp.cpu_count() or 2) - 1))

_physics_executor: _cf.ThreadPoolExecutor | None = None


def shutdown_physics_executor() -> None:
    """Para o executor de física. Chamado no shutdown do app."""
    global _physics_executor
    if _physics_executor is not None:
        _physics_executor.shutdown(wait=False, cancel_futures=True)
        _physics_executor = None


class FrameBudget:
    """Mede o tempo real de cada frame e calcula o wait_ms ideal.

    Mantém uma janela deslizante de N amostras e usa o percentil 90
    (em vez da média simples) para evitar que frames rápidos mascarem
    travamentos ocasionais.

    Uso::

        budget = FrameBudget(target_fps=30)
        # ... dentro do _tick():
        t0 = time.monotonic()
        render()
        wait_ms = budget.update(time.monotonic() - t0)
        widget.after(wait_ms, _tick)
    """

    _WINDOW = 20

    def __init__(self, target_fps: int = 30) -> None:
        self.target_fps = max(1, target_fps)
        self._target_ms = 1000.0 / self.target_fps
        self._samples: _collections.deque[float] = _collections.deque(
            maxlen=self._WINDOW
        )

    def update(self, frame_ms: float) -> int:
        """Registra a duração do frame atual e retorna o wait_ms para o próximo."""
        self._samples.append(frame_ms)
        if len(self._samples) < 3:
            return max(4, int(self._target_ms))

        sorted_s = sorted(self._samples)
        p90_idx = max(0, int(len(sorted_s) * 0.90) - 1)
        p90_ms = sorted_s[p90_idx]

        if p90_ms > self._target_ms * 1.2:
            ratio = min(3.0, p90_ms / max(1.0, self._target_ms))
            wait_ms = max(8, int(self._target_ms * ratio * 0.80))
        else:
            wait_ms = max(4, int(self._target_ms - frame_ms))

        return wait_ms

    def reset(self, target_fps: int) -> None:
        """Atualiza o FPS alvo e limpa o histórico."""
        self.target_fps = max(1, target_fps)
        self._target_ms = 1000.0 / self.target_fps
        self._samples.clear()
