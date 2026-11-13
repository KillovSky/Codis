"""Engine visual KEngine da interface KUI do Codis.

Implementa o canvas animado com renderização de cenas
cósmicas (planetas-arquivo, buracos negros, galáxias), suporte a
interação por mouse e base para o modo ``KWelcome``.
"""

from __future__ import annotations

import contextlib
import logging
import math
import random
import time
import tkinter as tk
from pathlib import Path
from typing import Any

from ._bgrenderer import BgRenderer
from ._colors import _hue, _lc_q, _pal, _safe_del
from ._perf import _DEFAULT_PERF, _PERF, FrameBudget
from ._tooltip import KHoverTooltip
from ._world import CosmosWorld

try:
    from src.i18n import _t as _i18n_t
except ImportError:

    def _i18n_t(key: str, **kw) -> str:

        return key.split(".")[-1]

logger = logging.getLogger("Codis.KUI")
_TAU = math.tau

_SIN_TABLE: list[float] = [math.sin(i * _TAU / 512) for i in range(512)]
_COS_TABLE: list[float] = [math.cos(i * _TAU / 512) for i in range(512)]
_ANGLE_TO_IDX = 512 / _TAU


def _fsin(ph: float) -> float:
    """Sin rápido via lookup table (±0.012 rad de erro máximo - aceitável para visual)."""
    return _SIN_TABLE[int(ph * _ANGLE_TO_IDX) & 511]


def _fcos(ph: float) -> float:
    """Cos rápido via lookup table."""
    return _COS_TABLE[int(ph * _ANGLE_TO_IDX) & 511]


def _ci(x) -> int:
    """Converte qualquer numérico para int C-puro.

    O Tkinter 3.14 no Windows rejeita floats herdados (ex: resultado de % em
    certas expressões) com TypeError em create_oval/create_rectangle.
    Usar int() explícito antes de cada chamada canvas resolve o crash.
    """
    return x if isinstance(x, int) else int(x)


class KEngine(tk.Canvas):
    """Motor visual do K-UI - escalona de 24 até 90 FPS dependendo do hardware e perfil.

    O desempenho depende principalmente da CPU, pois toda renderização é feita
    via Tkinter Canvas (primitivas 2D) e PIL em threads de fundo.
    """

    _LEARN_KEYS = [
        "kui.fact_entanglement",
        "kui.fact_superposition",
        "kui.fact_singularity",
        "kui.fact_time_dilation",
        "kui.fact_nebula",
        "kui.fact_tunneling",
        "kui.fact_duality",
        "kui.fact_heisenberg",
        "kui.fact_decoherence",
        "kui.fact_higgs",
        "kui.fact_dark_matter",
        "kui.fact_dark_energy",
        "kui.fact_blackhole_evap",
        "kui.fact_wormhole",
        "kui.fact_neutron_star",
        "kui.fact_cosmic_microwave",
        "kui.fact_multiverse",
        "kui.fact_spaghettification",
        "kui.fact_relativity",
        "kui.fact_planck",
    ]

    _LEARN_GROUPS = {
        "bh": [
            "kui.fact_singularity",
            "kui.fact_blackhole_evap",
            "kui.fact_spaghettification",
        ],
        "quantum": [
            "kui.fact_entanglement",
            "kui.fact_superposition",
            "kui.fact_tunneling",
            "kui.fact_duality",
            "kui.fact_heisenberg",
            "kui.fact_decoherence",
            "kui.fact_planck",
        ],
        "cosmos": [
            "kui.fact_nebula",
            "kui.fact_neutron_star",
            "kui.fact_cosmic_microwave",
            "kui.fact_dark_matter",
            "kui.fact_dark_energy",
            "kui.fact_multiverse",
        ],
        "wormhole": [
            "kui.fact_wormhole",
            "kui.fact_time_dilation",
            "kui.fact_relativity",
        ],
    }

    def __init__(
        self, parent: tk.Widget, dark: bool = True, profile: str = _DEFAULT_PERF
    ) -> None:
        """
        Args:
            canvas: Canvas Tkinter onde o cosmos é renderizado.
            dark: True para paleta escura.
            profile: Perfil de desempenho ('ultra', 'high', 'balanced', 'low', 'minimal').
        """
        p = _pal(dark)
        cfg = _PERF.get(profile, _PERF[_DEFAULT_PERF])
        super().__init__(parent, bg=p["void"], highlightthickness=0, bd=0)
        self._p = p
        self._dark = dark
        self._cfg = cfg
        self._dead = False

        self._ph = random.uniform(0, _TAU)
        self._aur_ph = random.uniform(0, _TAU)
        self._sol_ph = random.uniform(0, _TAU)
        self._scan_y = random.uniform(0.0, 1.0)
        self._flash = 0.0
        self._solar_ph = random.uniform(0, _TAU)
        self._gal_ph = random.uniform(0, _TAU)

        self._stars: list[list] = []
        self._star_seed = random.randint(0, 999999)
        self._particles: list[dict] = []
        self._meteors: list[dict] = []
        self._meteor_cd = random.randint(40, 120)
        self._rings: list[dict] = []
        self._ring_cd = random.randint(20, 60)

        self._solar_planets: list[dict] = []
        self._solar_cx = 0.5
        self._solar_cy = 0.5
        self._folder_planets: list[dict] = []

        self._black_holes: list[dict] = []
        self._bh_cd = 0

        self._wormholes: list[dict] = []
        self._wh_cd = random.randint(400, 800)

        self._comets: list[dict] = []
        self._comet_cd = random.randint(200, 500)

        self._nebula_clouds: list[dict] = []
        self._nebula_cd = random.randint(80, 200)

        self._satellites: list[dict] = []
        self._sat_cd = random.randint(300, 700)

        self._star_birth: list[dict] = []
        self._sb_cd = random.randint(250, 600)

        self._subatomic: list[dict] = []
        self._kp_burst: list[dict] = []

        self._metric_size_kb: float = 0.0
        self._metric_errors: int = 0
        self._metric_warnings: int = 0
        self._metric_kpm: float = 0.0
        self._metric_files: list[str] = []
        self._metric_chars: int = 0
        self._error_freq: dict[str, int] = {}

        self._bg_renderer = BgRenderer(dark, cfg)
        self._photo_ref: Any | None = None

        self._glitch_active = False
        self._glitch_alpha = 0.0
        self._glitch_timer = 0
        self._chromatic_shift = 0.0
        self._anomaly_proximity = 0.0

        self._aliens: list[dict] = []
        self._alien_cd = random.randint(800, 2200)

        self._artifacts: list[dict] = []
        self._artifact_cd = random.randint(1200, 3000)
        self._artifact_max = 4

        self._signals: list[dict] = []
        self._signal_cd = random.randint(600, 1800)

        self._debris: list[dict] = []
        self._debris_cd = random.randint(400, 900)

        self._pulsars: list[dict] = []
        self._pulsar_cd = random.randint(500, 1200)

        self._spiral_galaxies: list[dict] = []
        self._sgal_cd = random.randint(800, 2000)

        self._supernovas: list[dict] = []
        self._sn_cd = random.randint(1000, 3000)

        self._grav_waves: list[dict] = []

        self._binary_stars: list[dict] = []
        self._bin_cd = random.randint(600, 1500)

        self._aurora_curtain_ph: float = random.uniform(0, _TAU)

        self._merging_bh: dict | None = None

        self._singularity_timer: int = 0
        self._singularity_start: float = 0.0
        self._singularity_mode: bool = False
        self._bb_cooldown_until: float = 0.0
        self._big_bang_active: bool = False
        self._big_bang_phase: int = 0
        self._big_bang_age: int = 0
        self._bb_shockwave_r: float = 0.0

        self._frozen: bool = False
        self._frozen_frame: int = 0

        self._horror_frame: int = 0
        self._horror_eye_ph: float = random.uniform(0, _TAU)
        self._horror_msg_cd: int = random.randint(800, 2000)
        self._horror_face_cd: int = random.randint(1200, 3000)

        self._frame_times: list[float] = []
        time.monotonic()

        self._idle_t = time.monotonic()
        self._screensaver = False
        self._ss_particles: list[dict] = []

        self._tip_visible = False
        self._tip_text = "", ""
        self._tip_alpha = 0.0
        self._tip_cd = int(cfg["fps"] * random.uniform(20, 40))
        self._tip_show_limit = int(cfg["fps"] * 12)
        self._tip_show_timer = 0
        self._tip_timer = 0
        self._tip_fact_idx = 0

        self._resize_job: str | None = None
        self._W = 0
        self._H = 0

        self._frame_count: int = 0
        self._planet_mode: bool = False
        self._planet_file: str = ""
        random.uniform(0, _TAU)

        self._planet_click_cb: Any = None
        self._planet_restore_cb: Any = None
        self._planet_objects: list = []
        self._hover_tip = KHoverTooltip(self, dark=dark)
        self.bind("<Motion>", self._hover_tip.on_motion, add=True)
        self.bind("<Leave>", lambda e: self._hover_tip.on_leave(), add=True)
        self.bind("<Button-1>", self._hover_tip.on_click, add=True)

        self.bind("<Configure>", self._on_resize)
        self.bind("<Button-1>", self._on_canvas_click, add=True)
        self.bind("<Left>", self._on_canvas_key, add=True)
        self.bind("<Right>", self._on_canvas_key, add=True)
        self._tick_after_id: str | None = None
        self._suspended = False
        self._frame_budget = FrameBudget(target_fps=self._cfg.get("fps", 30))
        self._dt = 1.0
        self._last_tick_t = time.monotonic()
        self._world = CosmosWorld()
        self._world_errors = 0
        self.bind("<Motion>", self._on_cursor_move, add=True)
        self.bind("<Leave>", lambda _e: self._world.cursor_leave(), add=True)
        self._gen_stars()
        self._gen_particles()
        self._gen_solar_system()
        self._tick()

    def _oval(self, x1, y1, x2, y2, **kw) -> None:
        """create_oval com coords garantidamente int - evita crash no Tkinter 3.14/Windows."""
        self.create_oval(_ci(x1), _ci(y1), _ci(x2), _ci(y2), **kw)

    def set_reactive_metrics(
        self,
        *,
        file_size_kb=0.0,
        errors=0,
        warnings=0,
        keys_per_minute=0.0,
        open_files=None,
        chars_per_min=0,
    ):
        """Define 'reactive metrics'."""
        self._metric_size_kb = max(0.0, file_size_kb)
        self._metric_errors = max(0, errors)
        self._metric_warnings = max(0, warnings)
        self._metric_kpm = max(0.0, keys_per_minute)
        new_files = list(open_files or [])
        files_changed = new_files != self._metric_files
        self._metric_files = new_files
        self._metric_chars = max(0, chars_per_min)
        if files_changed:
            self._gen_solar_system()
        if self._cfg.get("subatomic"):
            self._gen_subatomic()

        if self._particles and keys_per_minute > 10:
            spd_mult = min(2.5, 1.0 + keys_per_minute / 90.0)
            for pt in self._particles:
                base_vx = 0.25
                base_vy = 0.55
                if abs(pt["vx"]) < base_vx * spd_mult:
                    pt["vx"] = pt["vx"] * min(spd_mult, 1.4)
                if abs(pt["vy"]) < base_vy * spd_mult:
                    pt["vy"] = pt["vy"] * min(spd_mult, 1.4)

    def notify_activity(self):
        """Notifica o motor de atividade do usuário para reativar animações."""
        self._idle_t = time.monotonic()
        if self._screensaver:
            self._screensaver = False
            self._ss_particles.clear()

    def _on_resize(self, _):
        """Trata o evento 'resize'.

        Args:
            _: Parâmetro da operação.
        """
        if self._resize_job:
            with contextlib.suppress(tk.TclError):
                self.after_cancel(self._resize_job)
        self._resize_job = self.after(100, self._do_resize)

    def _do_resize(self):
        """Atualiza as dimensões internas após resize do canvas."""
        self._resize_job = None
        self._W = self.winfo_width() or 1200
        self._H = self.winfo_height() or 800
        self._gen_stars()
        self._gen_particles()
        self._gen_solar_system()
        self._gen_subatomic()

    def _gen_stars(self):
        """Gera e inicializa o campo de estrelas com posições e brilhos aleatórios."""
        w = self.winfo_width() or 1200
        h = self.winfo_height() or 800
        nl = self._cfg["star_layers"]
        total = self._cfg["stars"]
        per = max(1, total // nl)
        cols = [self._p["star"], self._p["star2"], self._p["star3"]]
        self._stars = []
        rng = random.Random(self._star_seed ^ (w * 7919) ^ (h * 6271))
        for layer in range(nl):
            sf = 1.0 - layer * 0.25
            layer_stars = []
            for _ in range(per):
                c = rng.choice(cols[: max(1, 3 - layer)])

                layer_stars.append(
                    [
                        rng.uniform(0, w),
                        rng.uniform(0, h),
                        rng.uniform(0.3, 2.5) * sf,
                        rng.uniform(0, _TAU),
                        rng.uniform(0.3, 1.0),
                        c,
                    ]
                )
            self._stars.append(layer_stars)

    def _gen_particles(self):
        """Gera o pool inicial de partículas de efeito."""
        if not self._cfg["particles"]:
            self._particles = []
            return
        w = self.winfo_width() or 1200
        h = self.winfo_height() or 800
        n = self._cfg["n_particles"]
        ck = ["aur_a", "aur_b", "accent", "accent2", "plasma", "quantum"]
        self._particles = [
            {
                "x": random.uniform(0, w),
                "y": random.uniform(0, h),
                "vx": random.uniform(-0.25, 0.25),
                "vy": random.uniform(-0.55, -0.02),
                "r": random.uniform(0.5, 2.2),
                "ph": random.uniform(0, _TAU),
                "spd": random.uniform(0.04, 0.10),
                "col": random.choice(ck),
            }
            for _ in range(n)
        ]

    def _gen_solar_system(self):
        """Gera os planetas e órbitas do sistema solar simulado."""
        if not self._cfg.get("solar_system"):
            self._solar_planets = []
            return
        pc = [
            ("accent", "accent2"),
            ("aur_a", "aur_b"),
            ("plasma", "accent"),
            ("quantum", "aur_c"),
            ("accent2", "star2"),
            ("star3", "accent"),
            ("aur_c", "plasma"),
            ("star2", "quantum"),
        ]

        files = self._metric_files or [
            "main.py", "app.py", "utils.py", "config.py"]
        self._solar_planets = []
        for i, fpath in enumerate(files[:8]):
            name = Path(fpath).name if fpath else f"file{i}"
            cols = pc[i % len(pc)]
            h_val = abs(hash(name)) % 1000
            self._solar_planets.append(
                {
                    "name": name,
                    "orbit_r": 65 + i * 48,
                    "speed": 0.004 + h_val / 200000.0,
                    "ph": random.uniform(0, _TAU),
                    "tilt": random.uniform(0.35, 0.55),
                    "radius": 3 + (h_val % 4),
                    "col": cols[0],
                    "col2": cols[1],
                    "trail": [],
                    "glow_ph": random.uniform(0, _TAU),
                    "has_ring": (h_val % 7 == 0),
                    "_filepath": fpath,
                    "_px": 0,
                    "_py": 0,
                }
            )

    def _gen_subatomic(self):
        """Gera as partículas do modo de visualização subatômica."""
        if not self._cfg.get("subatomic"):
            self._subatomic = []
            return
        w = self.winfo_width() or 1200
        h = self.winfo_height() or 800
        n = min(
            int(self._cfg.get("n_subatomic", 0)), max(
                5, int(self._metric_kpm * 0.30))
        )
        ck = ["accent2", "quantum", "aur_a", "star3"]
        self._subatomic = [
            {
                "x": random.uniform(0.1 * w, 0.9 * w),
                "y": random.uniform(0.1 * h, 0.9 * h),
                "vx": random.uniform(-1.2, 1.2),
                "vy": random.uniform(-1.2, 1.2),
                "r": random.uniform(0.4, 1.6),
                "ph": random.uniform(0, _TAU),
                "life": random.uniform(0.4, 1.0),
                "col": random.choice(ck),
            }
            for _ in range(n)
        ]

    def _gen_screensaver_particles(self):
        """Gera partículas flutuantes para o modo de proteção de tela."""
        w = self._W or 1200
        h = self._H or 800
        ck = ["accent", "accent2", "aur_a",
              "aur_b", "aur_c", "plasma", "quantum"]
        self._ss_particles = [
            {
                "x": random.uniform(0, w),
                "y": random.uniform(0, h),
                "vx": random.uniform(-1.4, 1.4),
                "vy": random.uniform(-1.4, 1.4),
                "r": random.uniform(0.5, 2.8),
                "ph": random.uniform(0, _TAU),
                "life": random.uniform(0.6, 1.0),
                "col": random.choice(ck),
            }
            for _ in range(180)
        ]

    def _update_particles_physics(self) -> None:
        """Atualiza posição/fase das partículas SEPARADO do render (sem criar items canvas)."""
        _TAU_local = _TAU
        W = self._W or 1200
        H = self._H or 800
        dt = self._dt
        damp = 0.995 ** dt
        for pt in self._particles:
            pt["vx"] *= damp
            pt["vy"] *= damp
            bvx = pt.setdefault("bvx", pt["vx"])
            bvy = pt.setdefault("bvy", pt["vy"])
            pt["vx"] += (bvx - pt["vx"]) * 0.004 * dt
            pt["vy"] += (bvy - pt["vy"]) * 0.004 * dt
            pt["x"] += pt["vx"] * dt
            pt["y"] += pt["vy"] * dt
            pt["ph"] = (pt["ph"] + pt["spd"] * dt) % _TAU_local

            x = pt["x"]
            if x < -4:
                pt["x"] = float(W + 4)
            elif x > W + 4:
                pt["x"] = -4.0

            y = pt["y"]
            if y < -4:
                pt["y"] = float(H + 4)
            elif y > H + 4:
                pt["y"] = -4.0

    _IDLE_POLL_MS = 250
    _UNFOCUSED_MS = 80

    def _should_idle(self) -> bool:
        """True quando renderizar seria trabalho perdido (invisível/suspenso)."""
        if self._suspended:
            return True
        try:
            return not self.winfo_viewable()
        except tk.TclError:
            return True

    def _app_unfocused(self) -> bool:
        """True quando nenhuma janela do app tem o foco do sistema."""
        try:
            return self.focus_displayof() is None
        except (tk.TclError, KeyError):
            return False

    def set_suspended(self, suspended: bool) -> None:
        """Pausa/retoma a animação sem destruir o estado do cosmos.

        Usado pelo K-UI quando o editor ou a tela inicial cobrem o canvas:
        evita redesenhar ~35 camadas por frame para algo que ninguém vê.
        """
        suspended = bool(suspended)
        if suspended == self._suspended:
            return
        self._suspended = suspended
        if not suspended and not self._dead:
            if self._tick_after_id is not None:
                with contextlib.suppress(tk.TclError):
                    self.after_cancel(self._tick_after_id)
            self._tick_after_id = self.after_idle(self._tick)

    def _tick(self):
        """Executa um frame de simulação: física, renderização e agendamento do próximo."""
        if self._dead or not self.winfo_exists():
            return
        if self._should_idle():
            self._last_tick_t = time.monotonic()
            self._tick_after_id = self.after(self._IDLE_POLL_MS, self._tick)
            return
        frame_start = time.monotonic()
        fps = max(1, int(self._cfg.get("fps", 30)))
        dt = (frame_start - self._last_tick_t) * fps
        self._last_tick_t = frame_start
        dt = max(0.25, min(3.0, dt))
        self._dt = dt

        self._ph = (self._ph + 0.009 * dt) % _TAU
        self._aur_ph = (self._aur_ph + 0.004 * dt) % _TAU
        self._sol_ph = (self._sol_ph + 0.006 * dt) % _TAU
        self._solar_ph = (self._solar_ph + 0.003 * dt) % _TAU
        self._gal_ph = (self._gal_ph + 0.0008 * dt) % _TAU
        self._aurora_curtain_ph = (self._aurora_curtain_ph + 0.0015 * dt) % _TAU

        if self._cfg.get("particles") and self._particles:
            self._update_particles_physics()

        self._solar_cx = 0.5 + 0.12 * _fsin(self._gal_ph * 0.7)
        self._solar_cy = 0.5 + 0.08 * _fcos(self._gal_ph * 0.5)

        idle_s = self._cfg.get("screensaver_idle_s", 9999)
        if (
            self._cfg.get("screensaver")
            and time.monotonic() - self._idle_t > idle_s
            and not self._screensaver
        ):
            self._screensaver = True
            self._gen_screensaver_particles()

        if not self._big_bang_active and time.monotonic() >= self._bb_cooldown_until:
            if self._anomaly_proximity >= 0.75 and self._black_holes:
                if not self._singularity_mode:
                    self._singularity_mode = True
                    self._singularity_start = time.monotonic()
                else:
                    elapsed = time.monotonic() - self._singularity_start

                    self._singularity_timer = int(elapsed)
                    if elapsed >= 60.0:
                        self._trigger_big_bang()
            else:
                self._singularity_mode = False
                self._singularity_timer = 0

        self._horror_frame = getattr(self, "_horror_frame", 0) + 1

        if not self._frozen:
            self._render()
        else:
            self._frozen_frame += 1

            if self._frozen_frame % 2 == 0:
                self._draw_frozen_indicator()

        frame_ms = (time.monotonic() - frame_start) * 1000.0

        if self._cfg.get("time_dilation") and self._metric_size_kb > 10.0:
            td = max(0.5, 1.0 - (self._metric_size_kb - 10.0) / 2200.0)
            self._frame_budget.reset(max(1, int(self._cfg["fps"] * td)))
        else:
            if self._frame_budget.target_fps != self._cfg.get("fps", 30):
                self._frame_budget.reset(self._cfg.get("fps", 30))

        wait_ms = self._frame_budget.update(frame_ms)
        if self._app_unfocused():
            wait_ms = max(wait_ms, self._UNFOCUSED_MS)

        def _schedule(ms=wait_ms):
            if not self._dead:
                self._tick_after_id = self.after(ms, self._tick)

        self._tick_after_id = self.after_idle(_schedule)

    def _step_world(self, W: int, H: int) -> None:
        """Avança a camada de interações; desliga-se sozinha se falhar repetidamente."""
        if self._world_errors >= 3 or self._big_bang_active:
            return
        try:
            self._world.step(self, self._dt, W, H)
        except Exception:
            self._world_errors += 1
            logging.getLogger("Codis.KUI").exception("CosmosWorld.step falhou")

    def _draw_world(self, W: int, H: int) -> None:
        if self._world_errors >= 3:
            return
        try:
            self._world.draw(self, W, H)
        except Exception:
            self._world_errors += 1
            logging.getLogger("Codis.KUI").exception("CosmosWorld.draw falhou")

    def _on_cursor_move(self, e) -> None:
        self._world.cursor_move(e.x, e.y)

    def cosmos_event(self, name: str, **kw) -> None:
        """Evento do editor (``save``, ``open``, ``close``) que mexe no cosmos."""
        with contextlib.suppress(Exception):
            self._world.on_event(name, eng=self, **kw)

    def _render(self):
        """Executa um frame de renderização: limpa, redesenha todas as camadas e agenda o próximo."""
        if not self.winfo_exists():
            return
        W = self.winfo_width()
        H = self.winfo_height()
        if W < 4 or H < 4:
            return
        self._W = W
        self._H = H
        self._frame_count += 1
        self.delete("all")
        p = self._p
        c = self._cfg

        self._hover_tip.clear_areas()
        self._step_world(W, H)

        self._bg_renderer.maybe_update(W, H, self._ph, self._aur_ph)
        photo = self._bg_renderer.get_photo()
        if photo:
            self.create_image(0, 0, image=photo, anchor="nw")
            self._photo_ref = photo
        else:
            self.create_rectangle(0, 0, W, H, fill=p["void"], outline="")

        self._draw_watermark(W, H)

        self._draw_stars(W, H)

        self._draw_aurora_curtain(W, H)

        self._draw_star_birth(W, H)

        self._draw_spiral_galaxies(W, H)

        self._draw_binary_stars(W, H)

        if c["rings"]:
            self._draw_rings(W, H)

        if c["particles"]:
            self._draw_particles(W, H)

        if c["meteors"]:
            self._draw_meteors(W, H)

        if c["scan"]:
            self._draw_scan(W, H)

        if c.get("solar_system"):
            self._draw_solar_system(W, H)

        if self._folder_planets:
            self._draw_folder_planets(W, H)

        if c.get("black_holes"):
            self._draw_black_holes(W, H)

        self._draw_wormholes(W, H)

        self._draw_grav_waves(W, H)

        if c.get("subatomic"):
            self._draw_subatomic(W, H)

        if self._kp_burst:
            self._draw_kp_burst(W, H)

        if self._planet_mode:
            self._draw_planet_file(W, H)

        self._draw_comets(W, H)

        self._draw_nebula_clouds(W, H)

        self._draw_pulsars(W, H)

        self._draw_supernovas(W, H)

        self._draw_satellites(W, H)

        self._draw_gravitational_lensing(W, H)

        self._draw_aliens(W, H)

        self._draw_artifacts(W, H)

        self._draw_signals(W, H)

        self._draw_debris(W, H)

        self._draw_world(W, H)

        self._draw_bh_merger(W, H)

        if self._screensaver:
            self._draw_screensaver(W, H)

        if c.get("quantum_tooltips"):
            self._draw_quantum_tooltip(W, H)

        if self._flash > 0:
            self._draw_flash(W, H)

        self._draw_glitch(W, H)

        if self._big_bang_active:
            self._draw_big_bang(W, H)

        self._draw_cosmic_horror(W, H)

        if self._singularity_mode and self._black_holes:
            self._draw_singularity_hud(W, H)

    def _draw_watermark(self, W: int, H: int) -> None:
        """Exibe branding CODIS / KUI no canto inferior direito com tipografia refinada."""
        p = self._p
        pulse = 0.38 + 0.22 * abs(_fsin(self._ph * 0.35))
        self.create_text(
            W - 14,
            H - 29,
            text="C O D I S",
            fill=_lc_q(p["void"], p["accent"], pulse * 0.18),
            font=("Segoe UI", 13, "bold"),
            anchor="se",
        )
        c_title = _lc_q(p["void"], p["accent"], pulse + 0.08)
        c_sub = _lc_q(p["void"], p["fg_dim"], pulse * 0.55)
        margin = 14
        self.create_text(
            W - margin,
            H - 30,
            text="C O D I S",
            fill=c_title,
            font=("Segoe UI", 11, "bold"),
            anchor="se",
        )
        sub_txt = _i18n_t("kui.watermark_sub")
        self.create_text(
            W - margin,
            H - 14,
            text=sub_txt,
            fill=c_sub,
            font=("Segoe UI", 7),
            anchor="se",
        )
        line_alp = pulse * 0.35
        self.create_line(
            W - margin - 60,
            H - 36,
            W - margin,
            H - 36,
            fill=_lc_q(p["void"], p["accent"], line_alp),
            width=1,
        )

    def _draw_planet_file(self, W: int, H: int) -> None:
        """Desenha cada arquivo aberto como planeta - afetado por gravidade BH."""
        p = self._p
        planets = getattr(self, "_planet_objects", [])
        if not planets:
            return

        SPEED = 0.016

        for pl in planets:
            bh_pull = pl.setdefault("bh_pull", 0.0)

            ox = W // 2
            oy = H // 2
            orbit_a = int(W * pl["orbit_a_frac"])
            orbit_b = int(H * pl["orbit_b_frac"])
            ang_cur = pl.get("ph", 0.0)
            cx_now = ox + int(orbit_a * math.cos(ang_cur))
            cy_now = oy + int(orbit_b * math.sin(ang_cur))

            spagh_x = 1.0
            spagh_y = 1.0
            closest_bh_dist = float("inf")

            for bh in self._black_holes:
                if bh["alpha"] < 0.2:
                    continue
                dx_bh = bh["x"] - cx_now
                dy_bh = bh["y"] - cy_now
                dist_bh = math.sqrt(dx_bh * dx_bh + dy_bh * dy_bh) or 1
                grav_r = bh["r"] * 10 * bh["mass"]
                if dist_bh < grav_r:
                    influence = 1.0 - dist_bh / grav_r

                    bh_pull += (
                        0.0005 * bh["mass"] * influence *
                        (1 if dx_bh > 0 else -1)
                    )

                    ang_bh = math.atan2(dy_bh, dx_bh)
                    spagh_x += influence * 0.55 * abs(math.cos(ang_bh))
                    spagh_y += influence * 0.55 * abs(math.sin(ang_bh))
                    closest_bh_dist = min(closest_bh_dist, dist_bh)

                    if dist_bh < bh["r"] * 1.5:
                        for _ in range(8):
                            self._kp_burst.append(
                                {
                                    "x": cx_now + random.uniform(-10, 10),
                                    "y": cy_now + random.uniform(-10, 10),
                                    "vx": random.uniform(-3, 3),
                                    "vy": random.uniform(-3, 3),
                                    "r": random.uniform(1.5, 3.0),
                                    "ph": random.uniform(0, _TAU),
                                    "life": random.uniform(0.5, 0.9),
                                    "col": random.choice(
                                        ["plasma", "accent2", "err", "warn"]
                                    ),
                                }
                            )
                        pl["bh_pull"] = 0.0
                        continue

            bh_pull *= 0.96
            pl["bh_pull"] = bh_pull
            speed = SPEED + bh_pull
            pl["ph"] = (pl["ph"] + speed) % _TAU
            ang = pl["ph"]

            cx = ox + int(orbit_a * math.cos(ang))
            cy = oy + int(orbit_b * math.sin(ang))

            spagh_x = min(spagh_x, 3.5)
            spagh_y = min(spagh_y, 3.5)
            pulse = 0.65 + 0.35 * abs(math.sin(ang * 2.5))
            r_base = int(18 + 7 * pulse)
            r = r_base

            pl["cx"] = cx
            pl["cy"] = cy
            pl["r"] = r

            col1 = pl["col"]
            col2 = pl["col2"]
            c1 = p.get(col1, p["accent"])
            c2 = p.get(col2, p["accent2"])

            n_pts = 40
            for i in range(n_pts):
                a0 = i * _TAU / n_pts
                a1 = (i + 1) * _TAU / n_pts
                x0 = ox + int(orbit_a * math.cos(a0))
                y0 = oy + int(orbit_b * math.sin(a0))
                x1 = ox + int(orbit_a * math.cos(a1))
                y1 = oy + int(orbit_b * math.sin(a1))
                dist = abs(a0 - ang)
                if dist > math.pi:
                    dist = _TAU - dist
                trail_alp = max(0.02, 0.15 * (1.0 - dist / math.pi))
                self.create_line(
                    x0, y0, x1, y1, fill=_lc_q(p["void"], c1, trail_alp), width=1
                )

            for gl in range(3, 0, -1):
                gr_x = int((r + gl * 6) * min(spagh_x, 2.0))
                gr_y = int((r + gl * 6) * min(spagh_y, 2.0))
                self.create_oval(
                    cx - gr_x,
                    cy - gr_y,
                    cx + gr_x,
                    cy + gr_y,
                    fill="",
                    outline=_lc_q(p["void"], c1, 0.04 * gl * pulse),
                    width=1,
                )

            rx_pl = int(r * min(spagh_x, 2.8))
            ry_pl = int(r * min(spagh_y, 2.8))
            self.create_oval(
                cx - rx_pl,
                cy - ry_pl,
                cx + rx_pl,
                cy + ry_pl,
                fill=_lc_q(p["void"], c1, 0.80 * pulse),
                outline="",
            )

            if spagh_x > 1.4 or spagh_y > 1.4:
                _ = pl.get("bh_pull", 0)
                trail_len = int(max(spagh_x, spagh_y) * r * 1.5)
                self.create_line(
                    cx,
                    cy,
                    cx + int(trail_len * math.cos(ang + math.pi / 2)),
                    cy + int(trail_len * math.sin(ang + math.pi / 2)),
                    fill=_lc_q(p["void"], c1, 0.25 * pulse),
                    width=1,
                )

            for band in range(2):
                by = cy - r + int(r * 0.35 * (band + 1))
                bw = int(r * math.sqrt(max(0, 1 - ((by - cy) / max(r, 1)) ** 2)))
                if bw > 2:
                    self.create_oval(
                        cx - bw,
                        by - 2,
                        cx + bw,
                        by + 2,
                        fill=_lc_q(c1, c2, 0.5),
                        outline="",
                    )

            sr = max(4, r // 3)
            self.create_oval(
                cx - sr,
                cy - sr,
                cx + sr,
                cy + sr,
                fill=_lc_q(c1, "#ffffff", 0.38),
                outline="",
            )

            rr = r + 8
            self.create_oval(
                cx - rr,
                cy - int(rr * 0.28),
                cx + rr,
                cy + int(rr * 0.28),
                fill="",
                outline=_lc_q(p["void"], c2, 0.55 * pulse),
                width=2,
            )

            name = pl["name"]
            self.create_text(
                cx,
                cy + r + 14,
                text=name,
                fill=_lc_q(p["void"], p["fg"], 0.82),
                font=("Segoe UI", 9, "bold"),
                anchor="center",
            )
            hint = _i18n_t("kui.restore_hint")
            self.create_text(
                cx,
                cy + r + 26,
                text=hint,
                fill=_lc_q(p["void"], p["fg_dim"], 0.50),
                font=("Segoe UI", 7),
                anchor="center",
            )

            _cb = getattr(self, "_planet_restore_cb", None)
            if _cb:
                _fp = pl["filepath"]
                self._hover_tip.register_circle(
                    cx, cy, r + 22, "kui.planet_tip", on_click=lambda fp=_fp: _cb(fp)
                )
            else:
                self._hover_tip.register_circle(
                    cx, cy, r + 22, "kui.planet_tip")

    def enter_planet_mode(self, filepaths) -> None:
        """Entra em modo planeta com suporte a múltiplos arquivos.

        Args:
            filepaths: str único ou lista de str com os caminhos dos arquivos abertos.
        """
        if isinstance(filepaths, str):
            filepaths = [filepaths] if filepaths else []
        self._planet_mode = True
        self._planet_file = filepaths[0] if filepaths else ""

        PALETTES = [
            ("accent", "accent2"),
            ("aur_a", "aur_b"),
            ("plasma", "accent"),
            ("quantum", "aur_c"),
            ("accent2", "star2"),
            ("star3", "accent"),
        ]
        self._planet_objects: list = []
        n = len(filepaths)
        for i, fp in enumerate(filepaths[:6]):
            col1, col2 = PALETTES[i % len(PALETTES)]

            ph_start = (i / max(n, 1)) * _TAU

            orbit_a_frac = 0.28 + i * 0.06
            orbit_b_frac = 0.16 + i * 0.04
            self._planet_objects.append(
                {
                    "filepath": fp,
                    "name": Path(fp).name if fp else f"arquivo {i + 1}",
                    "ph": ph_start,
                    "orbit_a_frac": orbit_a_frac,
                    "orbit_b_frac": orbit_b_frac,
                    "col": col1,
                    "col2": col2,
                    "cx": -999,
                    "cy": -999,
                    "r": 20,
                }
            )

    def exit_planet_mode(self) -> None:
        """Sai do modo planeta, restaura visão normal."""
        self._planet_mode = False
        self._planet_file = ""
        self._planet_objects = []

    def _draw_stars(self, W: int, H: int) -> None:
        """Renderiza estrelas em multicamadas com paralaxe e efeito de distorção gravitacional."""
        p = self._p
        void = p["void"]
        ph = self._ph
        _abs = abs
        create_oval = self.create_oval
        create_rect = self.create_rectangle
        create_line = self.create_line
        register = self._hover_tip.register_circle
        learn_cosmos = self._LEARN_GROUPS.get("cosmos", ["kui.fact_nebula"])
        _rect_threshold = 99 if self._cfg.get("batch_stars") else 1

        for li, layer in enumerate(self._stars):
            sf = 1.0 / (li + 1)

            par_x = int(W * 0.006 * (li + 1) * _fsin(ph * 0.16 * sf))
            par_y = int(H * 0.005 * (li + 1) * _fcos(ph * 0.11 * sf))
            ph_sf = ph * sf
            is_front = li == 0
            exc = self._world.excite if li < 2 else ()
            for star in layer:
                sx, sy, sr, sph, spd, col = (
                    star[0],
                    star[1],
                    star[2],
                    star[3],
                    star[4],
                    star[5],
                )
                ix = (int(sx) + par_x) % W
                iy = (int(sy) + par_y) % H
                bri = 0.32 + 0.68 * _abs(_fsin(ph_sf * spd + sph))
                if is_front:
                    bri = min(1.0, bri * 1.3)
                if exc:
                    for ex_, ey_, er_, ew_, ea_ in exc:
                        ddx = ix - ex_
                        if -er_ - ew_ < ddx < er_ + ew_:
                            off = abs(math.hypot(ddx, iy - ey_) - er_)
                            if off < ew_:
                                bri = min(1.0, bri + ea_ * (1.0 - off / ew_))
                c2 = _lc_q(void, col, bri)
                ir = _ci(max(1, int(sr * bri)))
                if ir <= _rect_threshold:
                    create_rect(ix - ir, iy - ir, ix + ir,
                                iy + ir, fill=c2, outline="")
                    continue
                if is_front and bri > 0.6 and sr > 1.8:
                    br = ir + 3
                    create_oval(
                        ix - br,
                        iy - br,
                        ix + br,
                        iy + br,
                        fill=_lc_q(void, col, bri * 0.12),
                        outline="",
                    )
                create_oval(ix - ir, iy - ir, ix + ir,
                            iy + ir, fill=c2, outline="")
                if is_front and sr > 2.2 and bri > 0.72:
                    cl = ir + 2
                    cc = _lc_q(void, col, bri * 0.65)
                    create_line(ix - cl, iy, ix + cl, iy, fill=cc, width=1)
                    create_line(ix, iy - cl, ix, iy + cl, fill=cc, width=1)
                    if sr > 2.4:
                        register(
                            ix,
                            iy,
                            ir + 8,
                            random.choice(learn_cosmos),
                            extra_keys=learn_cosmos,
                        )

    def _draw_rings(self, W, H):
        """Desenha os anéis orbitais dos planetas com gradiente de perspectiva."""
        cx, cy = W // 2, H // 2
        self._ring_cd -= 1
        if self._ring_cd <= 0:
            fps = self._cfg["fps"]
            self._ring_cd = int(fps * random.uniform(2.0, 5.5))
            self._rings.append(
                {
                    "r": random.uniform(12.0, 40.0),
                    "alpha": random.uniform(0.35, 0.60),
                    "col": random.choice(
                        ["accent", "accent2", "aur_a",
                            "aur_b", "plasma", "quantum"]
                    ),
                    "speed": random.uniform(2.0, 4.0),
                }
            )
        survivors = []
        for ring in self._rings:
            ring["r"] += ring["speed"]
            ring["alpha"] *= 0.976
            if ring["alpha"] < 0.005 or ring["r"] > max(W, H) * 0.96:
                continue
            r = int(ring["r"])
            c_core = _lc_q(self._p["void"],
                           self._p[ring["col"]], ring["alpha"] * 0.58)
            c_mid = _lc_q(self._p["void"],
                          self._p[ring["col"]], ring["alpha"] * 0.22)
            c_out = _lc_q(self._p["void"],
                          self._p[ring["col"]], ring["alpha"] * 0.07)
            self.create_oval(
                cx - r, cy - r, cx + r, cy + r, fill="", outline=c_core, width=1
            )
            self.create_oval(
                cx - r - 2,
                cy - r - 2,
                cx + r + 2,
                cy + r + 2,
                fill="",
                outline=c_mid,
                width=2,
            )
            self.create_oval(
                cx - r - 5,
                cy - r - 5,
                cx + r + 5,
                cy + r + 5,
                fill="",
                outline=c_out,
                width=3,
            )
            survivors.append(ring)
        self._rings = survivors

    def _draw_particles(self, W, H):
        """Renderiza partículas ativas - coordenadas explicitamente int (fix crash Tkinter 3.14)."""
        p = self._p
        void = p["void"]
        _abs = abs
        create_oval = self.create_oval
        for pt in self._particles:
            x = pt["x"]
            y = pt["y"]
            if not 0 <= x <= W and 0 <= y <= H:
                continue
            bri = 0.22 + 0.78 * _abs(_fsin(pt["ph"]))
            c2 = _lc_q(void, p[pt["col"]], bri * 0.58)
            ir = _ci(max(1, int(pt["r"] * (0.5 + 0.5 * bri))))
            ix = _ci(x)
            iy = _ci(y)
            create_oval(ix - ir, iy - ir, ix + ir,
                        iy + ir, fill=c2, outline="")

    def _draw_meteors(self, W, H):
        """Desenha os meteoros com trilha de partículas e gravidade de buracos negros."""
        self._meteor_cd -= 1
        if self._meteor_cd <= 0:
            fps = self._cfg["fps"]
            self._meteor_cd = random.randint(int(fps * 28), int(fps * 75))
            angle = random.uniform(18, 55)
            rad = math.radians(angle)
            speed = random.uniform(9, 20)
            self._meteors.append(
                {
                    "x": float(random.uniform(W * 0.03, W * 0.87)),
                    "y": -18.0,
                    "dx": math.cos(rad) * speed,
                    "dy": math.sin(rad) * speed,
                    "length": random.randint(55, 140),
                    "alive": True,
                    "col": random.choice(["meteor", "aur_a", "accent2", "quantum"]),
                    "vx_extra": 0.0,
                    "vy_extra": 0.0,
                }
            )

        p = self._p
        for m in self._meteors:
            if not m["alive"]:
                continue

            for bh in self._black_holes:
                if bh["alpha"] < 0.25:
                    continue
                dx_bh = bh["x"] - m["x"]
                dy_bh = bh["y"] - m["y"]
                dist2 = dx_bh * dx_bh + dy_bh * dy_bh
                grav_r2 = (bh["r"] * 7.0 * bh["mass"]) ** 2
                if dist2 < 1 or dist2 >= grav_r2:
                    continue
                dist = math.sqrt(dist2)

                strength = 0.55 * bh["mass"] * (1.0 - dist2 / grav_r2)
                m["vx_extra"] += (dx_bh / dist) * strength
                m["vy_extra"] += (dy_bh / dist) * strength

                if dist < bh["r"] * 1.2:
                    m["alive"] = False
                    for _ in range(6):
                        self._kp_burst.append(
                            {
                                "x": m["x"],
                                "y": m["y"],
                                "vx": random.uniform(-3, 3),
                                "vy": random.uniform(-3, 3),
                                "r": random.uniform(1.5, 3.0),
                                "ph": random.uniform(0, _TAU),
                                "life": random.uniform(0.5, 0.9),
                                "col": random.choice(["accent2", "plasma", "warn"]),
                            }
                        )
                    break

            damp_m = 0.94 ** self._dt
            m["vx_extra"] *= damp_m
            m["vy_extra"] *= damp_m

            if not m["alive"]:
                continue

            dx_t = m["dx"] + m["vx_extra"]
            dy_t = m["dy"] + m["vy_extra"]

            head_c = p.get(m.get("col", "meteor"), p["meteor"])
            tf = m["length"] / 14.0
            x2 = m["x"] - dx_t * tf
            y2 = m["y"] - dy_t * tf
            xm = (m["x"] + x2) / 2
            ym = (m["y"] + y2) / 2
            self.create_line(
                int(x2),
                int(y2),
                int(m["x"]),
                int(m["y"]),
                fill=_lc_q(head_c, p["void"], 0.18),
                width=4,
            )
            self.create_line(
                int(x2),
                int(y2),
                int(xm),
                int(ym),
                fill=_lc_q(head_c, p["void"], 0.52),
                width=2,
            )
            self.create_line(
                int(xm),
                int(ym),
                int(m["x"]),
                int(m["y"]),
                fill=_lc_q(head_c, p["void"], 0.88),
                width=2,
            )
            hx, hy = int(m["x"]), int(m["y"])
            self.create_oval(
                hx - 3,
                hy - 3,
                hx + 3,
                hy + 3,
                fill=_lc_q(p["void"], head_c, 0.32),
                outline="",
            )
            self.create_oval(hx - 1, hy - 1, hx + 1, hy +
                             1, fill=head_c, outline="")

            m["x"] += dx_t * self._dt
            m["y"] += dy_t * self._dt
            if m["x"] > W + 120 or m["y"] > H + 120 or m["x"] < -120:
                m["alive"] = False

        self._meteors = [m for m in self._meteors if m["alive"]]

    def _draw_scan(self, W, H):
        """Desenha a linha de scan animada sobre o cosmos."""
        fps = self._cfg["fps"]
        self._scan_y = (self._scan_y + 0.003 * (60.0 / max(1, fps))) % 1.0
        y = int(self._scan_y * H)
        alp = 0.10 + 0.06 * _fsin(self._ph * 2.8)
        c2 = _lc_q(self._p["void"], self._p["aur_a"], alp)
        c2b = _lc_q(self._p["void"], self._p["aur_a"], alp * 0.30)
        self.create_line(0, y, W, y, fill=c2, width=1)
        if y > 0:
            self.create_line(0, y - 1, W, y - 1, fill=c2b, width=1)
        if y < H:
            self.create_line(0, y + 1, W, y + 1, fill=c2b, width=1)

    def _draw_solar_system(self, W, H):
        """Desenha o sistema solar completo (sol, planetas, órbitas)."""
        p = self._p
        cx = int(W * self._solar_cx)
        cy = int(H * self._solar_cy)
        pulse = 0.5 + 0.5 * abs(_fsin(self._solar_ph))
        r_sun = int(8 + 4 * pulse)
        c_sun = _lc_q(p["accent"], p["accent2"],
                      abs(_fsin(self._solar_ph * 0.55)))
        for ring in range(4, 0, -1):
            rr = r_sun + ring * 5
            alp = 0.05 * (5 - ring) * pulse
            self.create_oval(
                cx - rr,
                cy - rr,
                cx + rr,
                cy + rr,
                fill="",
                outline=_lc_q(p["void"], c_sun, alp),
                width=1,
            )
        self.create_oval(
            cx - r_sun,
            cy - r_sun,
            cx + r_sun,
            cy + r_sun,
            fill=_lc_q(p["void"], c_sun, 0.85 * pulse),
            outline="",
        )

        if abs(_fsin(self._solar_ph * 3)) > 0.88:
            fl = int(r_sun * 1.8)
            fa = self._solar_ph * 2.2
            fx = cx + int(fl * _fcos(fa))
            fy = cy + int(fl * _fsin(fa))
            self.create_line(
                cx, cy, fx, fy, fill=_lc_q(p["void"], p["warn"], 0.42), width=2
            )
        for planet in self._solar_planets:
            planet["ph"] = (planet["ph"] + planet["speed"]) % _TAU
            planet["glow_ph"] = (planet["glow_ph"] + 0.04) % _TAU
            angle = planet["ph"]
            orb_r = planet["orbit_r"]
            tilt = planet["tilt"]
            px = cx + int(orb_r * _fcos(angle))
            py = cy + int(orb_r * _fsin(angle) * tilt)

            planet["_px"] = px
            planet["_py"] = py

            alp_orb = 0.04 + 0.02 * abs(_fsin(self._solar_ph + orb_r))
            self.create_oval(
                cx - orb_r,
                cy - int(orb_r * tilt),
                cx + orb_r,
                cy + int(orb_r * tilt),
                fill="",
                outline=_lc_q(p["void"], p[planet["col"]], alp_orb),
                width=1,
            )

            trail = planet.setdefault("trail", [])
            trail.append((px, py))
            if len(trail) > 12:
                trail.pop(0)
            for ti, (tx, ty) in enumerate(trail[:-1]):
                t_a = (ti / max(len(trail), 1)) * 0.22
                tr_r = max(1, planet["radius"] - 2)
                self.create_oval(
                    tx - tr_r,
                    ty - tr_r,
                    tx + tr_r,
                    ty + tr_r,
                    fill=_lc_q(p["void"], p[planet["col"]], t_a),
                    outline="",
                )

            r_pl = planet["radius"]
            glow_p = 0.5 + 0.5 * abs(_fsin(planet["glow_ph"]))
            c_pl = _lc_q(p[planet["col"]], p[planet["col2"]],
                         abs(_fsin(angle * 0.5)))
            if glow_p > 0.65:
                ar = r_pl + 3
                self.create_oval(
                    px - ar,
                    py - ar,
                    px + ar,
                    py + ar,
                    fill=_lc_q(p["void"], c_pl, glow_p * 0.14),
                    outline="",
                )
            self.create_oval(
                px - r_pl,
                py - r_pl,
                px + r_pl,
                py + r_pl,
                fill=_lc_q(p["void"], c_pl, 0.80),
                outline="",
            )
            if planet.get("has_ring"):
                ring_r = r_pl + 5
                self.create_oval(
                    px - ring_r,
                    py - int(ring_r * 0.32),
                    px + ring_r,
                    py + int(ring_r * 0.32),
                    fill="",
                    outline=_lc_q(p["void"], p[planet["col"]], 0.28),
                    width=1,
                )
            if planet["name"]:
                lbl = (
                    planet["name"][:11] + "..."
                    if len(planet["name"]) > 12
                    else planet["name"]
                )
                self.create_text(
                    px,
                    py + r_pl + 7,
                    text=lbl,
                    fill=_lc_q(p["void"], p[planet["col"]], 0.40),
                    font=("Segoe UI", 6),
                    anchor="center",
                )

                fpath = planet.get("_filepath", planet["name"])
                _cb = self._planet_click_cb
                if _cb:

                    def _click(fp=fpath, cb=_cb):
                        return cb(fp)

                else:
                    _click = None
                self._hover_tip.register_circle(
                    px,
                    py,
                    r_pl + 14,
                    "kui.solar_planet_tip",
                    {"name": planet["name"]},
                    on_click=_click,
                )

    def _draw_black_holes(self, W, H):
        """Buracos negros: só com erros, tamanho por frequência, teleporta estrelas capturadas."""
        p = self._p
        n_err = self._metric_errors

        if n_err <= 0:
            if self._black_holes:
                self._begin_bh_deaths(W, H)
                self._render_bh_visuals(W, H, p)
            return

        max_bh = min(5, n_err)
        top_freq = (
            max(self._error_freq.values(),
                default=n_err) if self._error_freq else n_err
        )

        self._bh_cd -= 1
        if self._bh_cd <= 0 and len(self._black_holes) < max_bh:
            fps = self._cfg["fps"]
            self._bh_cd = max(int(fps * 3), int(fps * 14 // max(1, n_err)))
            r_base = min(60.0, 12.0 + top_freq * 3.5)
            self._black_holes.append(
                {
                    "x": float(random.uniform(W * 0.15, W * 0.85)),
                    "y": float(random.uniform(H * 0.20, H * 0.80)),
                    "r": r_base,
                    "alpha": 0.0,
                    "growing": True,
                    "ph": random.uniform(0, _TAU),
                    "mass": min(3.0, 0.8 + top_freq * 0.15),
                    "age": 0,
                    "freq": top_freq,
                    "death_mode": None,
                    "death_target": None,
                    "death_age": 0,
                }
            )
        elif self._bh_cd <= 0:
            self._bh_cd = int(self._cfg["fps"] * 2)

        self._render_bh_visuals(W, H, p)

    def _begin_bh_deaths(self, W: int, H: int) -> None:
        """Inicia/avança animação de morte de cada BH (quando erros chegam a 0).

        Modos:
          wormhole_absorb - BH voa em direção ao WH mais próximo e é engolido
          evaporate       - Hawking radiation intensa progressiva até sumir
          implode         - Colapso súbito + anel de luz expansivo
        """
        for bh in self._black_holes:
            if bh.get("death_mode") is None:
                if self._wormholes and random.random() < 0.55:
                    _bh_x, _bh_y = bh["x"], bh["y"]
                    nearest_wh = min(
                        self._wormholes,
                        key=lambda w, bx=_bh_x, by=_bh_y: (w["x"] - bx) ** 2
                        + (w["y"] - by) ** 2,
                    )
                    bh["death_mode"] = "wormhole_absorb"
                    bh["death_target"] = nearest_wh
                elif random.random() < 0.5:
                    bh["death_mode"] = "evaporate"
                    bh["death_target"] = None
                else:
                    bh["death_mode"] = "implode"
                    bh["death_target"] = None
                bh["death_age"] = 0
                self._trigger_glitch(20)
            else:
                self._advance_bh_death(bh, W, H)

        self._black_holes = [b for b in self._black_holes if b["alpha"] > 0]

    def _advance_bh_death(self, bh: dict, W: int, H: int) -> None:
        """Avança 1 frame a animação de morte de um BH."""
        mode = bh.get("death_mode", "evaporate")
        bh["death_age"] = bh.get("death_age", 0) + 1
        age = bh["death_age"]
        p = self._p

        if mode == "wormhole_absorb":
            wh = bh.get("death_target")
            if wh not in self._wormholes:
                ghost = {
                    "x": bh["x"] + random.uniform(-70, 70),
                    "y": bh["y"] + random.uniform(-50, 50),
                    "r": bh["r"] * 0.75,
                    "alpha": 0.55,
                    "ph": 0.0,
                    "pair_id": -1,
                    "age": 0,
                    "lifetime": 160,
                    "growing": False,
                }
                self._wormholes.append(ghost)
                bh["death_target"] = ghost
                wh = ghost

            dx_w = wh["x"] - bh["x"]
            dy_w = wh["y"] - bh["y"]
            dist_w = math.sqrt(dx_w * dx_w + dy_w * dy_w) or 1
            speed = min(0.5 + age * 0.045, dist_w * 0.18)
            bh["x"] += (dx_w / dist_w) * speed
            bh["y"] += (dy_w / dist_w) * speed

            prox = max(0.0, 1.0 - dist_w / max(1, wh["r"] * 10))
            bh["r"] = max(0.5, bh["r"] - prox * 0.35)
            bh["alpha"] = max(0.0, bh["alpha"] - prox * 0.035)

            if random.random() < 0.35:
                ang_s = math.atan2(dy_w, dx_w)
                for _ in range(2):
                    self._kp_burst.append(
                        {
                            "x": bh["x"] + random.uniform(-bh["r"], bh["r"]),
                            "y": bh["y"] + random.uniform(-bh["r"], bh["r"]),
                            "vx": math.cos(ang_s) * random.uniform(1, 3),
                            "vy": math.sin(ang_s) * random.uniform(1, 3),
                            "r": random.uniform(0.8, 2.2),
                            "ph": random.uniform(0, _TAU),
                            "life": random.uniform(0.3, 0.7),
                            "col": random.choice(["quantum", "accent2", "aur_a"]),
                        }
                    )

            if dist_w < wh["r"] or bh["alpha"] < 0.03:
                bh["alpha"] = 0.0
                wh["r"] = min(wh["r"] * 1.35, 55.0)
                wh["alpha"] = min(0.95, wh.get("alpha", 0.5) + 0.30)
                for _ in range(14):
                    ang_b = random.uniform(0, _TAU)
                    self._kp_burst.append(
                        {
                            "x": wh["x"] + math.cos(ang_b) * wh["r"] * 0.6,
                            "y": wh["y"] + math.sin(ang_b) * wh["r"] * 0.6,
                            "vx": math.cos(ang_b) * random.uniform(2, 5),
                            "vy": math.sin(ang_b) * random.uniform(2, 5),
                            "r": random.uniform(1.5, 3.5),
                            "ph": random.uniform(0, _TAU),
                            "life": random.uniform(0.4, 0.9),
                            "col": random.choice(
                                ["quantum", "aur_a", "accent2", "plasma"]
                            ),
                        }
                    )

        elif mode == "evaporate":
            decay = 0.008 + age * 0.00018
            bh["r"] = max(0.0, bh["r"] - decay * 3.2)
            bh["alpha"] = max(0.0, bh["alpha"] - decay)
            bh["mass"] = max(0.1, bh.get("mass", 1.0) - decay * 0.12)

            intensity = min(9, 1 + age // 25)
            for _ in range(intensity):
                ang_h = random.uniform(0, _TAU)
                rad_h = max(1, bh["r"]) * (0.9 + random.uniform(0, 0.6))
                self._kp_burst.append(
                    {
                        "x": bh["x"] + math.cos(ang_h) * rad_h,
                        "y": bh["y"] + math.sin(ang_h) * rad_h,
                        "vx": math.cos(ang_h) * random.uniform(0.5, 2.5),
                        "vy": math.sin(ang_h) * random.uniform(0.5, 2.5),
                        "r": random.uniform(0.5, 1.8),
                        "ph": random.uniform(0, _TAU),
                        "life": random.uniform(0.3, 0.65),
                        "col": random.choice(["quantum", "aur_a", "accent2", "star3"]),
                    }
                )

            if bh["r"] < 3.0:
                for _ in range(10):
                    self._kp_burst.append(
                        {
                            "x": bh["x"] + random.uniform(-25, 25),
                            "y": bh["y"] + random.uniform(-25, 25),
                            "vx": random.uniform(-4, 4),
                            "vy": random.uniform(-4, 4),
                            "r": random.uniform(2, 4),
                            "ph": random.uniform(0, _TAU),
                            "life": random.uniform(0.5, 1.0),
                            "col": "quantum",
                        }
                    )

        else:
            if age < 40:
                bh["r"] = max(0.5, bh["r"] * 0.925)
                bh["alpha"] = min(1.0, bh["alpha"] * 1.018)
                if age == 18:
                    self._trigger_glitch(32)
            elif age < 52:
                bh["alpha"] = max(0.0, bh["alpha"] - 0.16)
                fa = bh["alpha"] * 0.80
                cr = int(bh["r"] * 3.5)
                self.create_oval(
                    int(bh["x"]) - cr,
                    int(bh["y"]) - cr,
                    int(bh["x"]) + cr,
                    int(bh["y"]) + cr,
                    fill=_lc_q(p["void"], "#ffffff", fa),
                    outline="",
                )
            else:
                bh["alpha"] = 0.0
                ring_r = int((age - 52) * 5)
                ring_alp = max(0.0, 1.0 - (age - 52) / 55.0)
                for rk in range(3):
                    rr = ring_r + rk * 7
                    self.create_oval(
                        int(bh["x"]) - rr,
                        int(bh["y"]) - rr,
                        int(bh["x"]) + rr,
                        int(bh["y"]) + rr,
                        fill="",
                        outline=_lc_q(
                            p["void"], p["quantum"], ring_alp *
                            (0.9 - rk * 0.25)
                        ),
                        width=max(1, 3 - rk),
                    )
                if random.random() < 0.45:
                    ang_r = random.uniform(0, _TAU)
                    self._kp_burst.append(
                        {
                            "x": bh["x"] + math.cos(ang_r) * ring_r,
                            "y": bh["y"] + math.sin(ang_r) * ring_r,
                            "vx": math.cos(ang_r) * 2.5,
                            "vy": math.sin(ang_r) * 2.5,
                            "r": random.uniform(1, 2.5),
                            "ph": random.uniform(0, _TAU),
                            "life": random.uniform(0.3, 0.65),
                            "col": "accent2",
                        }
                    )

    def _render_bh_visuals(self, W, H, p):
        """Renderiza buracos negros com física completa: lensing, disco, jets, gravidade."""
        survivors = []
        self._anomaly_proximity = 0.0
        bh_list = self._black_holes
        for i, _bh in enumerate(bh_list):
            for j in range(
                i + 1, len(bh_list)
            ):  # pylint: disable=consider-using-enumerate
                b1 = bh_list[i]
                b2 = bh_list[j]
                dx = b1["x"] - b2["x"]
                dy = b1["y"] - b2["y"]
                dist = math.sqrt(dx * dx + dy * dy)
                thresh = (b1["r"] + b2["r"]) * 6.0
                if dist < thresh:
                    prox = max(self._anomaly_proximity,
                               1.0 - dist / max(1, thresh))
                    self._anomaly_proximity = min(1.0, prox)

                    if dist > (b1["r"] + b2["r"]) * 1.5:
                        pull = 0.08 * prox
                        norm = max(1, dist)
                        b1["x"] -= dx / norm * pull
                        b1["y"] -= dy / norm * pull
                        b2["x"] += dx / norm * pull
                        b2["y"] += dy / norm * pull
                    else:
                        if not self._merging_bh:
                            new_r = min(90.0, b1["r"] + b2["r"] * 0.65)
                            new_mass = min(
                                5.0, b1.get("mass", 1.0) +
                                b2.get("mass", 1.0) * 0.5
                            )

                            self._merging_bh = {
                                "x": (b1["x"] + b2["x"]) / 2,
                                "y": (b1["y"] + b2["y"]) / 2,
                                "r": new_r,
                                "alpha": 1.0,
                                "phase": 0.0,
                                "age": 0,
                                "max_age": 90,
                            }

                            b1["r"] = new_r
                            b1["mass"] = new_mass
                            b1["growing"] = False
                            b1["alpha"] = min(0.95, b1["alpha"])
                            b1["freq"] = max(
                                b1.get("freq", 1), b2.get("freq", 1))

                            self._trigger_glitch(55)
                        bh_list[j]["alpha"] = 0.0

        for bh in bh_list:
            for wh in self._wormholes:
                dx = bh["x"] - wh["x"]
                dy = bh["y"] - wh["y"]
                dist = math.sqrt(dx * dx + dy * dy)
                thresh = (bh["r"] + wh["r"]) * 8.0
                if dist < thresh:
                    prox = 1.0 - dist / max(1, thresh)
                    self._anomaly_proximity = min(
                        1.0, max(self._anomaly_proximity, prox * 0.85)
                    )

                    if dist > (bh["r"] + wh["r"]) * 2:
                        pull = 0.04 * prox * bh["mass"]
                        norm = max(1, dist)
                        wh["x"] -= dx / norm * pull
                        wh["y"] -= dy / norm * pull

        if self._anomaly_proximity > 0.35 and not self._glitch_active:
            self._trigger_glitch(int(self._anomaly_proximity * 60) + 20)

        for bh in self._black_holes:
            bh["ph"] = (bh["ph"] + 0.022) % _TAU
            bh["age"] += 1
            if bh["growing"]:
                bh["alpha"] = min(0.95, bh["alpha"] + 0.018)
                if bh["alpha"] >= 0.95:
                    bh["growing"] = False
            if bh["alpha"] <= 0:
                continue
            survivors.append(bh)
            cx_b, cy_b = int(bh["x"]), int(bh["y"])
            r = bh["r"]
            alp = bh["alpha"]
            mass = bh["mass"]
            spin = self._solar_ph * 2.4 + bh["ph"]

            for gl in range(6, 0, -1):
                gr = int(r * (1.9 + gl * 0.42))
                galp = (0.055 - gl * 0.006) * alp * mass
                if galp <= 0:
                    continue
                tilt = 0.65 + 0.25 * _fsin(bh["ph"] + gl * 0.5)
                self.create_oval(
                    cx_b - gr,
                    cy_b - int(gr * tilt),
                    cx_b + gr,
                    cy_b + int(gr * tilt),
                    fill="",
                    outline=_lc_q(p["void"], p["accent"], galp),
                    width=1,
                )

            for ai, (acol, a_alp) in enumerate(
                [("accent", 0.60), ("aur_a", 0.42),
                 ("plasma", 0.28), ("accent2", 0.15)]
            ):
                ar = int(r * (1.25 + ai * 0.52))
                ang = spin + ai * 0.8
                ax_off = int(ar * 0.28 * _fcos(ang))
                ay_off = int(ar * 0.11 * _fsin(ang))
                self.create_oval(
                    cx_b - ar + ax_off,
                    cy_b - int(ar * 0.30) + ay_off,
                    cx_b + ar + ax_off,
                    cy_b + int(ar * 0.30) + ay_off,
                    fill="",
                    outline=_lc_q(p["void"], p[acol], a_alp * alp),
                    width=max(1, 3 - ai),
                )

            if r > 16 and alp > 0.45:
                jl = int(r * 3.2 * alp)
                cj = _lc_q(p["void"], p["quantum"], 0.40 * alp)
                cj2 = _lc_q(p["void"], p["aur_a"], 0.22 * alp)
                self.create_line(
                    cx_b, cy_b - int(r * 0.6), cx_b, cy_b - jl, fill=cj, width=2
                )
                self.create_line(
                    cx_b, cy_b + int(r * 0.6), cx_b, cy_b + jl, fill=cj, width=2
                )
                self.create_line(
                    cx_b - 2,
                    cy_b - int(r * 0.6),
                    cx_b - 2,
                    cy_b - int(jl * 0.7),
                    fill=cj2,
                    width=1,
                )
                self.create_line(
                    cx_b + 2,
                    cy_b - int(r * 0.6),
                    cx_b + 2,
                    cy_b - int(jl * 0.7),
                    fill=cj2,
                    width=1,
                )

            ir = max(4, int(r * 0.52 * alp))
            self.create_oval(
                cx_b - ir, cy_b - ir, cx_b + ir, cy_b + ir, fill="#000000", outline=""
            )
            sr2 = max(2, ir // 3)
            sing_p = 0.5 + 0.5 * abs(_fsin(bh["ph"] * 3.1))
            self.create_oval(
                cx_b - sr2,
                cy_b - sr2,
                cx_b + sr2,
                cy_b + sr2,
                fill=_lc_q("#000000", p["accent2"], 0.45 * alp * sing_p),
                outline="",
            )

            if r > 14 and alp > 0.5 and random.random() < 0.35:
                for _ in range(random.randint(1, 3)):
                    hawk_ang = random.uniform(0, _TAU)
                    hawk_r = r * (0.52 + random.uniform(0, 0.25))
                    hx = cx_b + int(hawk_r * math.cos(hawk_ang))
                    hy = cy_b + int(hawk_r * math.sin(hawk_ang))
                    self._kp_burst.append(
                        {
                            "x": float(hx),
                            "y": float(hy),
                            "vx": random.uniform(-0.8, 0.8) + math.cos(hawk_ang) * 0.5,
                            "vy": random.uniform(-0.8, 0.8) + math.sin(hawk_ang) * 0.5,
                            "r": random.uniform(0.5, 1.5),
                            "ph": random.uniform(0, _TAU),
                            "life": random.uniform(0.3, 0.6),
                            "col": random.choice(["quantum", "accent2", "aur_a"]),
                        }
                    )

            grav_r2 = (r * 4.0 * mass) ** 2
            for pt in self._particles:
                dx = cx_b - pt["x"]
                dy = cy_b - pt["y"]
                dist2 = dx * dx + dy * dy
                if dist2 < 1 or dist2 >= grav_r2:
                    continue
                strength = 0.14 * mass * (1.0 - dist2 / grav_r2)
                dist = math.sqrt(dist2)
                pt["vx"] += dx / dist * strength
                pt["vy"] += dy / dist * strength

            planet_grav_r = r * 9.0 * mass
            planet_grav_r2 = planet_grav_r**2
            cx_sol = int((self._W or W) * self._solar_cx)
            cy_sol = int((self._H or H) * self._solar_cy)
            for planet in self._solar_planets:
                px_p = cx_sol + int(planet["orbit_r"] * math.cos(planet["ph"]))
                py_p = cy_sol + int(
                    planet["orbit_r"] * math.sin(planet["ph"]) * planet["tilt"]
                )
                dx_p = cx_b - px_p
                dy_p = cy_b - py_p
                dist2_p = dx_p * dx_p + dy_p * dy_p
                if 0 < dist2_p < planet_grav_r2:
                    influence = 1.0 - dist2_p / planet_grav_r2

                    pull_key = "_bh_pull"
                    planet.setdefault(pull_key, 0.0)

                    planet[pull_key] += (
                        0.00025 * mass * influence * (1 if dx_p > 0 else -1)
                    )
                    planet[pull_key] *= 0.97
                    planet["ph"] = (planet["ph"] + planet[pull_key]) % _TAU
                else:
                    if "_bh_pull" in planet:
                        planet["_bh_pull"] *= 0.92

            capture_r2 = (r * 2.5) ** 2
            for layer in self._stars:
                for star in layer:
                    dx = cx_b - star[0]
                    dy = cy_b - star[1]
                    if dx * dx + dy * dy < capture_r2:
                        if random.random() < 0.15:
                            self._kp_burst.append(
                                {
                                    "x": star[0],
                                    "y": star[1],
                                    "vx": random.uniform(-1.5, 1.5),
                                    "vy": random.uniform(-1.5, 1.5),
                                    "r": random.uniform(1.0, 2.5),
                                    "ph": random.uniform(0, _TAU),
                                    "life": random.uniform(0.4, 0.75),
                                    "col": random.choice(
                                        ["accent2", "quantum", "plasma"]
                                    ),
                                }
                            )

                        attempts = 0
                        while attempts < 8:
                            nx = random.uniform(0, W)
                            ny = random.uniform(0, H)
                            if (cx_b - nx) ** 2 + (cy_b - ny) ** 2 >= capture_r2 * 6:
                                star[0] = nx
                                star[1] = ny
                                break
                            attempts += 1

            self._hover_tip.register_circle(
                cx_b,
                cy_b,
                int(r * 2.0),
                "kui.black_hole_tip",
                {"n": self._metric_errors},
            )

            freq = bh.get("freq", 1)
            if freq > 2:
                err_txt = f"!{self._metric_errors} x{freq}"
            else:
                err_txt = f"!{self._metric_errors}"
            self.create_text(
                cx_b,
                cy_b + int(r * 2.1),
                text=err_txt,
                fill=_lc_q(p["void"], p["err"], 0.44 * alp),
                font=("Segoe UI", 7),
                anchor="center",
            )

            self._hover_tip.register_circle(
                cx_b,
                cy_b,
                int(r * 2.2),
                "kui.fact_singularity",
                extra_keys=self._LEARN_GROUPS.get("bh", []),
            )
        self._black_holes = survivors

    def _draw_subatomic(self, W, H):
        """Desenha a visualização subatômica com núcleo e elétrons."""
        p = self._p
        survivors = []
        for pt in self._subatomic:
            pt["x"] += pt["vx"]
            pt["y"] += pt["vy"]
            pt["ph"] = (pt["ph"] + 0.10) % _TAU
            pt["life"] -= 0.008
            if pt["life"] <= 0:
                pt["x"] = random.uniform(W * 0.1, W * 0.9)
                pt["y"] = random.uniform(H * 0.1, H * 0.9)
                pt["vx"] = random.uniform(-1.2, 1.2)
                pt["vy"] = random.uniform(-1.2, 1.2)
                pt["life"] = random.uniform(0.4, 1.0)
                pt["ph"] = random.uniform(0, _TAU)
            survivors.append(pt)
            if not 0 <= pt["x"] <= W and 0 <= pt["y"] <= H:
                continue
            bri = pt["life"] * abs(_fsin(pt["ph"]))
            if bri < 0.04:
                continue
            c2 = _lc_q(p["void"], p[pt["col"]], bri * 0.50)
            ir = _ci(max(1, int(pt["r"] * bri)))
            ix, iy = _ci(pt["x"]), _ci(pt["y"])
            tx = ix - int(pt["vx"] * 3)
            ty = iy - int(pt["vy"] * 3)
            if 0 <= tx <= W and 0 <= ty <= H:
                self.create_line(
                    tx,
                    ty,
                    ix,
                    iy,
                    fill=_lc_q(p["void"], p[pt["col"]], bri * 0.20),
                    width=1,
                )
            self.create_oval(ix - ir, iy - ir, ix + ir,
                             iy + ir, fill=c2, outline="")
        self._subatomic = survivors

    def _draw_kp_burst(self, W, H):
        """Desenha a explosão de partículas do easter egg Konami."""
        p = self._p
        survivors = []
        for pt in self._kp_burst:
            pt["x"] += pt["vx"] * 1.5
            pt["y"] += pt["vy"] * 1.5
            pt["life"] -= 0.055
            if pt["life"] <= 0:
                continue
            survivors.append(pt)
            if not 0 <= pt["x"] <= W and 0 <= pt["y"] <= H:
                continue
            c2 = _lc_q(p["void"], p[pt["col"]], pt["life"] * 0.65)
            ir = _ci(max(1, int(pt["r"] * pt["life"])))
            ix, iy = _ci(pt["x"]), _ci(pt["y"])
            self.create_oval(ix - ir, iy - ir, ix + ir,
                             iy + ir, fill=c2, outline="")
        self._kp_burst = survivors

    def _draw_screensaver(self, W, H):
        """Proteção de tela: cosmos pleno + título animado com efeito de digitação."""
        p = self._p
        cx, cy = W // 2, H // 2
        self.create_rectangle(
            0, 0, W, H, fill=_lc_q(p["void"], "#000000", 0.15), outline=""
        )
        pulse = 0.5 + 0.5 * abs(_fsin(self._ph * 0.6))
        halo_r = int(90 + 20 * pulse)
        self.create_oval(
            cx - halo_r,
            cy - halo_r // 3 - 12,
            cx + halo_r,
            cy + halo_r // 3 - 12,
            fill=_lc_q(p["void"], p["accent"], pulse * 0.06),
            outline="",
        )
        line_w = int(100 + 40 * pulse)
        line_alp = 0.30 + 0.20 * pulse
        self.create_line(
            cx - line_w,
            cy - 34,
            cx + line_w,
            cy - 34,
            fill=_lc_q(p["void"], p["accent"], line_alp),
            width=1,
        )
        for sx in (-line_w, line_w):
            self.create_oval(
                cx + sx - 2,
                cy - 36,
                cx + sx + 2,
                cy - 32,
                fill=_lc_q(p["void"], p["accent2"], line_alp),
                outline="",
            )
        c_txt = _lc_q(p["void"], p["accent"], 0.55 + 0.35 * pulse)
        self.create_text(
            cx + 1,
            cy - 20 + 1,
            text="C O D I S",
            fill=_lc_q(p["void"], "#000000", 0.35),
            font=("Segoe UI", 26, "bold"),
            anchor="center",
        )
        self.create_text(
            cx,
            cy - 20,
            text="C O D I S",
            fill=c_txt,
            font=("Segoe UI", 24, "bold"),
            anchor="center",
        )
        self.create_text(
            cx,
            cy + 14,
            text=_i18n_t("hc.k_ui.k_ui_quantum_engine_interface_"),
            fill=_lc_q(p["void"], p["fg_dim"], 0.52 + 0.20 * pulse),
            font=("Segoe UI", 9),
            anchor="center",
        )
        self.create_line(
            cx - line_w,
            cy + 30,
            cx + line_w,
            cy + 30,
            fill=_lc_q(p["void"], p["accent2"], line_alp * 0.6),
            width=1,
        )
        survivors = []
        for pt in self._ss_particles:
            pt["x"] += pt["vx"]
            pt["y"] += pt["vy"]
            pt["ph"] = (pt["ph"] + 0.07) % _TAU
            pt["life"] -= 0.004
            if pt["life"] <= 0:
                pt["x"] = random.uniform(0, W)
                pt["y"] = random.uniform(0, H)
                pt["vx"] = random.uniform(-1.4, 1.4)
                pt["vy"] = random.uniform(-1.4, 1.4)
                pt["life"] = random.uniform(0.5, 1.0)
            survivors.append(pt)
            if not 0 <= pt["x"] <= W and 0 <= pt["y"] <= H:
                continue
            bri = pt["life"] * (0.4 + 0.6 * abs(_fsin(pt["ph"])))
            c2 = _lc_q(p["void"], p[pt["col"]], bri * 0.45)
            ir = _ci(max(1, int(pt["r"] * bri)))
            ix, iy = _ci(pt["x"]), _ci(pt["y"])
            self.create_oval(ix - ir, iy - ir, ix + ir,
                             iy + ir, fill=c2, outline="")
        self._ss_particles = survivors

    def _draw_wormholes(self, W, H):
        """Portais quânticos que teletransportam partículas entre dois pontos do espaço."""
        p = self._p
        self._wh_cd -= 1
        if self._wh_cd <= 0 and len(self._wormholes) < 2:
            self._wh_cd = random.randint(400, 900)

            x1 = random.uniform(W * 0.15, W * 0.50)
            y1 = random.uniform(H * 0.20, H * 0.80)
            x2 = random.uniform(W * 0.50, W * 0.85)
            y2 = random.uniform(H * 0.20, H * 0.80)
            wid = random.randint(10000, 99999)
            for x, y in ((x1, y1), (x2, y2)):
                self._wormholes.append(
                    {
                        "x": float(x),
                        "y": float(y),
                        "r": float(random.uniform(18, 32)),
                        "alpha": 0.0,
                        "growing": True,
                        "ph": random.uniform(0, _TAU),
                        "pair_id": wid,
                        "age": 0,
                        "lifetime": random.randint(300, 700),
                    }
                )
        survivors = []
        for wh in self._wormholes:
            wh["ph"] = (wh["ph"] + 0.035) % _TAU
            wh["age"] += 1
            if wh["growing"]:
                wh["alpha"] = min(0.85, wh["alpha"] + 0.025)
                if wh["alpha"] >= 0.85:
                    wh["growing"] = False
            elif wh["age"] > wh["lifetime"]:
                wh["alpha"] -= 0.018
            if wh["alpha"] <= 0:
                continue
            survivors.append(wh)
            cx, cy = int(wh["x"]), int(wh["y"])
            r = wh["r"]
            alp = wh["alpha"]

            stretch_x = 1.0
            stretch_y = 1.0
            for bh in self._black_holes:
                if bh["alpha"] < 0.2:
                    continue
                dx_bh = bh["x"] - wh["x"]
                dy_bh = bh["y"] - wh["y"]
                d_bh = math.sqrt(dx_bh * dx_bh + dy_bh * dy_bh)
                influence = max(0, 1.0 - d_bh / (bh["r"] * 12 * bh["mass"]))
                if influence > 0.05:
                    ang_bh = math.atan2(dy_bh, dx_bh)
                    stretch_x += influence * 0.6 * abs(math.cos(ang_bh))
                    stretch_y += influence * 0.6 * abs(math.sin(ang_bh))

            for ring in range(5, 0, -1):
                rr_x = int(r * (1.0 + ring * 0.35) * stretch_x)
                rr_y = int(r * (1.0 + ring * 0.35) * stretch_y)
                ring_alp = (
                    alp * (0.08 + ring * 0.04) *
                    abs(math.sin(wh["ph"] + ring * 0.4))
                )
                self.create_oval(
                    cx - rr_x,
                    cy - rr_y,
                    cx + rr_x,
                    cy + rr_y,
                    fill="",
                    outline=_lc_q(p["void"], p["quantum"], ring_alp),
                    width=1,
                )

            for i in range(12):
                ang = wh["ph"] + i * (_TAU / 12)
                for ri in range(3):
                    radi = r * (0.3 + ri * 0.22) * alp
                    sx = cx + int(radi * math.cos(ang))
                    sy = cy + int(radi * math.sin(ang) * 0.6)
                    dr = max(1, int(2 - ri))
                    c2 = _lc_q(p["void"], p["accent2"],
                               alp * (0.6 - ri * 0.15))
                    self.create_oval(
                        sx - dr, sy - dr, sx + dr, sy + dr, fill=c2, outline=""
                    )

            ir = max(3, int(r * 0.40 * alp))
            self.create_oval(
                cx - ir,
                cy - ir,
                cx + ir,
                cy + ir,
                fill=_lc_q(p["void"], "#000033", 0.95),
                outline="",
            )

            sr2 = max(2, ir // 3)
            self.create_oval(
                cx - sr2,
                cy - sr2,
                cx + sr2,
                cy + sr2,
                fill=_lc_q(p["void"], p["aur_a"], alp * 0.80),
                outline="",
            )

            self._hover_tip.register_circle(
                cx,
                cy,
                int(r * 1.6),
                "kui.fact_wormhole",
                extra_keys=self._LEARN_GROUPS.get("wormhole", []),
            )

            entry_r2 = (r * 0.6) ** 2
            for pt in self._particles:
                dx = cx - pt["x"]
                dy = cy - pt["y"]
                if dx * dx + dy * dy < entry_r2:
                    pid = wh["pair_id"]
                    for w2 in self._wormholes:
                        if w2["pair_id"] == pid and w2 is not wh:
                            pt["x"] = w2["x"] + \
                                random.uniform(-w2["r"], w2["r"])
                            pt["y"] = w2["y"] + random.uniform(
                                -w2["r"] * 0.6, w2["r"] * 0.6
                            )
                            pt["vx"] *= -0.8
                            pt["vy"] *= -0.8
                            break
        self._wormholes = survivors

    def _draw_star_birth(self, W, H):
        """Nebulosa de nascimento estelar - novas estrelas emergem de nuvens de gás."""
        p = self._p
        self._sb_cd -= 1
        if self._sb_cd <= 0 and len(self._star_birth) < 3:
            self._sb_cd = random.randint(280, 650)
            self._star_birth.append(
                {
                    "x": float(random.uniform(W * 0.1, W * 0.9)),
                    "y": float(random.uniform(H * 0.1, H * 0.9)),
                    "r": float(random.uniform(8, 20)),
                    "phase": 0.0,
                    "max_phase": random.randint(180, 360),
                    "col": random.choice(["star", "accent2", "aur_a", "star3"]),
                    "cloud_r": float(random.uniform(40, 90)),
                }
            )
        survivors = []
        for sb in self._star_birth:
            sb["phase"] += 1.0
            if sb["phase"] > sb["max_phase"]:
                if self._stars:
                    col = p.get(sb["col"], p["star"])
                    self._stars[0].append(
                        [
                            sb["x"],
                            sb["y"],
                            sb["r"] * 0.4,
                            random.uniform(0, _TAU),
                            random.uniform(0.3, 0.9),
                            col,
                        ]
                    )
                continue
            survivors.append(sb)
            cx, cy = int(sb["x"]), int(sb["y"])
            progress = sb["phase"] / sb["max_phase"]
            alp = min(1.0, progress * 3.0) * \
                (1.0 - max(0.0, (progress - 0.7) * 3.3))
            cloud_r = int(sb["cloud_r"] * (1.0 - progress * 0.6))
            col_key = sb["col"]

            self._hover_tip.register_circle(
                cx,
                cy,
                int(cloud_r * 0.8),
                "kui.star_birth_tip",
                extra_keys=self._LEARN_GROUPS.get("cosmos", []),
            )

            for layer in range(5, 0, -1):
                lr = int(cloud_r * layer / 5)
                if lr < 2:
                    continue
                c2 = _lc_q(p["void"], p.get(
                    col_key, p["star"]), alp * 0.08 * layer)
                self.create_oval(
                    cx - lr,
                    cy - int(lr * 0.7),
                    cx + lr,
                    cy + int(lr * 0.7),
                    fill=c2,
                    outline="",
                )

            star_r = max(1, int(sb["r"] * progress * alp))
            sc = _lc_q(p["void"], p.get(col_key, p["star"]),
                       min(1.0, progress * 1.5))
            self.create_oval(
                cx - star_r, cy - star_r, cx + star_r, cy + star_r, fill=sc, outline=""
            )

            if progress > 0.4:
                jet_alp = min(1.0, (progress - 0.4) * 2.5) * alp
                jl = int(cloud_r * 0.8 * jet_alp)
                cj = _lc_q(p["void"], p.get(col_key, p["star"]), jet_alp * 0.6)
                for angle in (0, _TAU / 3, 2 * _TAU / 3):
                    ex = cx + int(jl * math.cos(angle))
                    ey = cy + int(jl * math.sin(angle))
                    self.create_line(cx, cy, ex, ey, fill=cj, width=1)
        self._star_birth = survivors

    def _draw_folder_planets(self, W, H):
        """Renderiza arquivos da pasta aberta como planetas flutuantes clicáveis."""
        p = self._p
        if not self._folder_planets:
            return
        for _, pl in enumerate(self._folder_planets):
            pl["ph"] = (pl["ph"] + pl["speed"]) % _TAU
            pl["glow_ph"] = (pl["glow_ph"] + 0.03) % _TAU

            cx0 = int(W * pl["cx_frac"])
            cy0 = int(H * pl["cy_frac"])
            orb_r = pl["orbit_r"]
            px = cx0 + int(orb_r * math.cos(pl["ph"]))
            py = cy0 + int(orb_r * math.sin(pl["ph"]) * 0.55)
            r = pl["radius"]
            glow = 0.5 + 0.5 * abs(math.sin(pl["glow_ph"]))
            c_pl = _lc_q(
                p.get(pl["col"], p["accent"]),
                p.get(pl["col2"], p["accent2"]),
                abs(math.sin(pl["ph"] * 0.5)),
            )

            if glow > 0.6:
                self.create_oval(
                    px - r - 4,
                    py - r - 4,
                    px + r + 4,
                    py + r + 4,
                    fill=_lc_q(p["void"], c_pl, glow * 0.12),
                    outline="",
                )

            self.create_oval(
                px - r,
                py - r,
                px + r,
                py + r,
                fill=_lc_q(p["void"], c_pl, 0.78),
                outline="",
            )

            name = pl["name"]
            self.create_text(
                px,
                py + r + 9,
                text=name,
                fill=_lc_q(p["void"], p["fg"], 0.55 + 0.25 * glow),
                font=("Segoe UI", 7),
                anchor="center",
            )

            pl["_px"] = px
            pl["_py"] = py
            self._hover_tip.register_circle(
                px, py, r + 12, "kui.folder_planet_tip", {"name": pl["name"]}
            )

    def _draw_comets(self, W: int, H: int) -> None:
        """Cometas realistas: núcleo brilhante + cauda de plasma em gradiente."""
        p = self._p
        self._comet_cd -= 1
        if self._comet_cd <= 0 and len(self._comets) < 3:
            self._comet_cd = random.randint(180, 420)
            angle = random.uniform(0, _TAU)
            speed = random.uniform(1.8, 4.5)

            if random.random() < 0.5:
                sx = random.uniform(-60, -20)
                sy = random.uniform(0, H)
            else:
                sx = random.uniform(0, W)
                sy = random.uniform(-60, -20)
            self._comets.append(
                {
                    "x": float(sx),
                    "y": float(sy),
                    "vx": speed * math.cos(angle),
                    "vy": speed * math.sin(angle),
                    "tail_len": random.randint(80, 200),
                    "col": random.choice(
                        ["aur_a", "accent2", "star3", "quantum", "aur_b"]
                    ),
                    "core_r": random.uniform(2.5, 5.0),
                    "ph": random.uniform(0, _TAU),
                }
            )
        survivors = []
        for c2 in self._comets:
            c2["x"] += c2["vx"] * self._dt
            c2["y"] += c2["vy"] * self._dt
            c2["ph"] = (c2["ph"] + 0.08 * self._dt) % _TAU
            if not -80 <= c2["x"] <= W + 80 and -80 <= c2["y"] <= H + 80:
                continue
            survivors.append(c2)
            cx2, cy2 = int(c2["x"]), int(c2["y"])
            tl = c2.get("tlen", c2["tail_len"])
            col = p.get(c2["col"], p["aur_a"])
            if "tdx" in c2:
                tx, ty = c2["tdx"], c2["tdy"]
            else:
                norm = math.sqrt(c2["vx"] ** 2 + c2["vy"] ** 2) or 1
                tx = -c2["vx"] / norm
                ty = -c2["vy"] / norm

            for i in range(16):
                t0 = i / 16
                t1 = (i + 1) / 16
                x0 = cx2 + int(tx * tl * t0)
                y0 = cy2 + int(ty * tl * t0)
                x1 = cx2 + int(tx * tl * t1)
                y1 = cy2 + int(ty * tl * t1)
                alp = (1 - t0) * 0.55
                w2 = max(1, int(c2["core_r"] * (1 - t0) * 1.6))
                self.create_line(
                    x0, y0, x1, y1, fill=_lc_q(p["void"], col, alp), width=w2
                )

            r = c2["core_r"] * c2.get("flare", 1.0)
            pulse = 0.85 + 0.15 * abs(_fsin(c2["ph"]))
            for glow in (r * 3, r * 2, r):
                ga = 0.12 if glow == r * \
                    3 else (0.25 if glow == r * 2 else 0.90)
                gi = max(1, int(glow * pulse))
                self.create_oval(
                    cx2 - gi,
                    cy2 - gi,
                    cx2 + gi,
                    cy2 + gi,
                    fill=_lc_q(p["void"], col, ga * pulse),
                    outline="",
                )
            self.create_oval(
                cx2 - 1, cy2 - 1, cx2 + 1, cy2 + 1, fill="#ffffff", outline=""
            )
            self._hover_tip.register_circle(
                cx2,
                cy2,
                int(r * 3),
                "kui.fact_neutron_star",
                extra_keys=self._LEARN_GROUPS.get("cosmos", []),
            )
        self._comets = survivors

    def _draw_nebula_clouds(self, W: int, H: int) -> None:
        """Nuvens de nebulosa animadas - manchas coloridas que pulsam e derivam."""
        p = self._p
        self._nebula_cd -= 1
        if self._nebula_cd <= 0 and len(self._nebula_clouds) < 5:
            self._nebula_cd = random.randint(60, 150)
            col_pairs = [
                ("aur_a", "aur_b"),
                ("accent", "accent2"),
                ("plasma", "aur_c"),
                ("quantum", "aur_a"),
                ("star3", "accent2"),
            ]
            c1, c2 = random.choice(col_pairs)
            self._nebula_clouds.append(
                {
                    "x": float(random.uniform(W * 0.1, W * 0.9)),
                    "y": float(random.uniform(H * 0.1, H * 0.9)),
                    "rx": random.uniform(60, 140),
                    "ry": random.uniform(30, 80),
                    "ph": random.uniform(0, _TAU),
                    "speed": random.uniform(0.003, 0.009),
                    "vx": random.uniform(-0.15, 0.15),
                    "vy": random.uniform(-0.08, 0.08),
                    "col1": c1,
                    "col2": c2,
                    "life": random.uniform(0.3, 1.0),
                    "decay": random.uniform(0.0003, 0.0008),
                }
            )
        survivors = []
        for nb in self._nebula_clouds:
            nb["ph"] = (nb["ph"] + nb["speed"]) % _TAU
            nb["x"] += nb["vx"] * self._dt
            nb["y"] += nb["vy"] * self._dt
            nb["life"] -= nb["decay"] * self._dt
            if nb["life"] <= 0:
                continue
            survivors.append(nb)
            cx2, cy2 = int(nb["x"]), int(nb["y"])
            alp = nb["life"] * 0.18
            pulse = 0.7 + 0.3 * abs(_fsin(nb["ph"]))
            rx = int(nb["rx"] * pulse)
            ry = int(nb["ry"] * pulse)
            c1 = p.get(nb["col1"], p["aur_a"])
            c2 = p.get(nb["col2"], p["accent"])
            blend = abs(_fsin(nb["ph"] * 0.5))
            cloud_col = _lc_q(c1, c2, blend)

            for layer in range(5, 0, -1):
                lrx = int(rx * layer / 5)
                lry = int(ry * layer / 5)
                if lrx < 2:
                    continue
                la = alp * (layer / 5) * 0.7
                self.create_oval(
                    cx2 - lrx,
                    cy2 - lry,
                    cx2 + lrx,
                    cy2 + lry,
                    fill=_lc_q(p["void"], cloud_col, la),
                    outline="",
                )
        self._nebula_clouds = survivors

    def _draw_satellites(self, W: int, H: int) -> None:
        """Satélites artificiais - cruzam a tela lentamente com painel solar."""
        p = self._p
        self._sat_cd -= 1
        if self._sat_cd <= 0 and len(self._satellites) < 2:
            self._sat_cd = random.randint(250, 600)
            vy2 = random.uniform(0.4, 1.2)
            self._satellites.append(
                {
                    "x": float(random.uniform(-20, -5)),
                    "y": float(random.uniform(H * 0.1, H * 0.9)),
                    "vx": random.uniform(0.8, 1.6),
                    "vy": vy2 * random.choice([-1, 1]),
                    "ph": random.uniform(0, _TAU),
                    "size": random.uniform(3, 6),
                }
            )
        survivors = []
        for sat in self._satellites:
            for bh in self._black_holes:
                if bh["alpha"] < 0.25:
                    continue
                dx_bh = bh["x"] - sat["x"]
                dy_bh = bh["y"] - sat["y"]
                dist_bh = math.sqrt(dx_bh * dx_bh + dy_bh * dy_bh) or 1
                grav_r = bh["r"] * 9 * bh["mass"]
                if dist_bh < grav_r:
                    influence = 0.35 * bh["mass"] * (1.0 - dist_bh / grav_r)
                    sat["vx"] += (dx_bh / dist_bh) * influence
                    sat["vy"] += (dy_bh / dist_bh) * influence

                    if dist_bh < bh["r"] * 1.5:
                        for _ in range(5):
                            self._kp_burst.append(
                                {
                                    "x": sat["x"],
                                    "y": sat["y"],
                                    "vx": random.uniform(-2, 2),
                                    "vy": random.uniform(-2, 2),
                                    "r": random.uniform(1, 2.5),
                                    "ph": random.uniform(0, _TAU),
                                    "life": random.uniform(0.4, 0.8),
                                    "col": random.choice(["accent2", "warn", "plasma"]),
                                }
                            )
                        continue
            sat["x"] += sat["vx"] * self._dt
            sat["y"] += sat["vy"] * self._dt
            sat["ph"] = (sat["ph"] + 0.04) % _TAU
            if not -30 <= sat["x"] <= W + 30 and -30 <= sat["y"] <= H + 30:
                continue
            survivors.append(sat)
            sx, sy = int(sat["x"]), int(sat["y"])
            sz = sat["size"]

            self.create_oval(
                sx - sz,
                sy - sz,
                sx + sz,
                sy + sz,
                fill=_lc_q(p["void"], "#aaaacc", 0.75),
                outline="",
            )

            panel_c = _lc_q(p["void"], "#4488ff", 0.65)
            for ang in (0, _TAU / 2):
                px2 = sx + int((sz + 10) * math.cos(ang + sat["ph"]))
                py2 = sy + int((sz + 10) * math.sin(ang + sat["ph"]) * 0.4)
                self.create_line(sx, sy, px2, py2, fill=panel_c, width=2)
                self.create_rectangle(
                    px2 - 4, py2 - 2, px2 + 4, py2 + 2, fill=panel_c, outline=""
                )

            self.create_oval(sx - 1, sy - 1, sx + 1, sy +
                             1, fill="#ffffff", outline="")
        self._satellites = survivors

    def _draw_gravitational_lensing(self, W: int, H: int) -> None:
        """Lensing gravitacional de fundo - estrelas próximas de BH parecem distorcidas."""
        if not self._black_holes:
            return
        p = self._p
        for bh in self._black_holes:
            if bh["alpha"] < 0.3:
                continue
            cx2, cy2 = bh["x"], bh["y"]
            lens_r = bh["r"] * 5 * bh["mass"]

            for i in range(6):
                ang = (i / 6) * _TAU + self._sol_ph
                dist = bh["r"] * (2.5 + i * 0.4)
                if dist > lens_r:
                    break

                arc_r = int(dist)
                if arc_r < 5:
                    continue
                arc_alp = bh["alpha"] * (1 - dist / lens_r) * 0.22
                self.create_arc(
                    cx2 - arc_r,
                    cy2 - arc_r,
                    cx2 + arc_r,
                    cy2 + arc_r,
                    start=int(math.degrees(ang)),
                    extent=60,
                    fill="",
                    outline=_lc_q(p["void"], p["star2"], arc_alp),
                    width=1,
                )

    def _draw_quantum_tooltip(self, W, H):
        """Painel de aprendizado automático - aparece periodicamente.

        TIMER CORRIGIDO: _tip_timer conta a espera entre fatos.
        _tip_show_timer conta quanto tempo o fato fica visível.
        """
        p = self._p

        if not self._tip_visible:
            self._tip_timer += 1
            if self._tip_timer < self._tip_cd:
                return

            grp = random.choice(list(self._LEARN_GROUPS.values()))
            self._tip_text = grp[0], grp
            self._tip_visible = True
            self._tip_alpha = 0.0
            self._tip_show_timer = 0
            self._tip_fact_idx = 0
            self._tip_timer = 0
            self._tip_cd = int(self._cfg["fps"] * random.uniform(25, 50))
            return

        self._tip_show_timer += 1
        fade_in_frames = int(self._cfg["fps"] * 0.6)
        fade_out_start = self._tip_show_limit - int(self._cfg["fps"] * 1.5)

        if self._tip_show_timer <= fade_in_frames:
            self._tip_alpha = min(
                1.0, self._tip_show_timer / max(1, fade_in_frames))
        elif self._tip_show_timer >= fade_out_start:
            remaining = self._tip_show_limit - self._tip_show_timer
            self._tip_alpha = max(
                0.0, remaining / max(1, self._tip_show_limit - fade_out_start)
            )
        else:
            self._tip_alpha = 1.0

        if self._tip_show_timer >= self._tip_show_limit:
            self._tip_visible = False
            return

        alp = self._tip_alpha
        if alp < 0.02:
            return

        key_list = self._tip_text[1]
        idx = getattr(self, "_tip_fact_idx", 0)
        key = key_list[idx % len(key_list)]
        try:
            text = _i18n_t(key)
        except (KeyError, AttributeError):
            text = key

        if "|" in text:
            title, body = text.split("|", 1)
        else:
            title = "⚛"
            body = text

        bx = 16
        bw = min(460, W - 32)
        bh = 104
        by = H - bh - 16

        self.create_rectangle(
            bx + 4,
            by + 4,
            bx + bw + 4,
            by + bh + 4,
            fill=_lc_q(p["void"], "#000000", alp * 0.55),
            outline="",
        )

        self.create_rectangle(
            bx,
            by,
            bx + bw,
            by + bh,
            fill=_lc_q(p["void"], p["glass"], alp * 0.94),
            outline=_lc_q(p["void"], p["accent"], alp * 0.65),
            width=1,
        )

        self.create_rectangle(
            bx, by, bx + 3, by + bh, fill=_lc_q(p["void"], p["accent"], alp), outline=""
        )

        prog = 1.0 - self._tip_show_timer / max(1, self._tip_show_limit)
        bar_w = max(0, int((bw - 6) * prog))
        self.create_rectangle(
            bx + 3,
            by + 1,
            bx + bw - 3,
            by + 3,
            fill=_lc_q(p["void"], p["accent"], alp * 0.15),
            outline="",
        )
        if bar_w > 0:
            bar_col = _lc_q(p["accent"], p["accent2"], prog)
            self.create_rectangle(
                bx + 3,
                by + 1,
                bx + 3 + bar_w,
                by + 3,
                fill=_lc_q(p["void"], bar_col, alp * 0.88),
                outline="",
            )

        if title.strip():
            self.create_text(
                bx + 14,
                by + 14,
                text=title.strip(),
                fill=_lc_q(p["void"], p["accent"], alp),
                font=("Segoe UI", 9, "bold"),
                anchor="w",
            )

        self.create_line(
            bx + 14,
            by + 27,
            bx + bw - 10,
            by + 27,
            fill=_lc_q(p["void"], p["accent"], alp * 0.20),
            width=1,
        )

        words = body.strip().split()
        lines_t: list = []
        cur = ""
        max_c = max(10, (bw - 28) // 7)
        for w in words:
            test = (cur + " " + w).strip()
            if len(test) > max_c and cur:
                lines_t.append(cur)
                cur = w
            else:
                cur = test
        if cur:
            lines_t.append(cur)
        for li, ln in enumerate(lines_t[:5]):
            self.create_text(
                bx + 14,
                by + 35 + li * 13,
                text=ln,
                fill=_lc_q(p["void"], p["fg"], alp * 0.90),
                font=("Segoe UI", 8),
                anchor="w",
            )

        n = len(key_list)
        if n > 1:
            pg = f"{idx + 1} / {n}"
            self.create_text(
                bx + bw - 10,
                by + 14,
                text=pg,
                fill=_lc_q(p["void"], p["fg_dim"], alp * 0.50),
                font=("Segoe UI", 7),
                anchor="e",
            )
            self.create_text(
                bx + 18,
                by + bh - 10,
                text=_i18n_t("hc.k_ui.anterior"),
                fill=_lc_q(p["void"], p["accent2"], alp * 0.75),
                font=("Segoe UI", 7),
                anchor="w",
            )
            self.create_text(
                bx + bw - 18,
                by + bh - 10,
                text=_i18n_t("hc.k_ui.pr_ximo"),
                fill=_lc_q(p["void"], p["accent2"], alp * 0.75),
                font=("Segoe UI", 7),
                anchor="e",
            )

            prog = 1.0 - self._tip_show_timer / max(1, self._tip_show_limit)
            pw = int((bw - 4) * prog)
            self.create_rectangle(
                bx + 2,
                by + bh - 3,
                bx + 2 + pw,
                by + bh - 1,
                fill=_lc_q(p["void"], p["accent"], alp * 0.45),
                outline="",
            )

    def _handle_qt_nav(self, ex: int, ey: int) -> bool:
        """Clique nas setas do tooltip quântico. Retorna True se consumiu."""
        if not self._tip_visible:
            return False
        W = self.winfo_width()
        H = self.winfo_height()
        bx = 16
        bw = min(430, W - 32)
        bh = 88
        by = H - bh - 16

        if by <= ey <= by + bh:
            key_list = self._tip_text[1]
            n = len(key_list)
            if n > 1:
                if bx <= ex <= bx + bw // 2:
                    self._tip_fact_idx = (self._tip_fact_idx - 1) % n
                    self._tip_show_timer = 0
                    return True
                if bx + bw // 2 <= ex <= bx + bw:
                    self._tip_fact_idx = (self._tip_fact_idx + 1) % n
                    self._tip_show_timer = 0
                    return True
        return False

    def _draw_flash(self, W, H):
        """Executa a operação 'draw flash'.

        Args:
            W: Parâmetro da operação.
            H: Parâmetro da operação.
        """
        colors = ["#ff0000", "#ff7700", "#ffff00",
                  "#00ff00", "#0077ff", "#7700ff"]
        step = max(1, W // len(colors))
        alpha = min(1.0, self._flash)
        for i, c2 in enumerate(colors):
            self.create_rectangle(
                i * step,
                0,
                (i + 1) * step,
                H,
                fill=_lc_q(self._p["void"], c2, alpha * 0.40),
                outline="",
            )
        self._flash -= 0.04
        if self._flash <= 0:
            self._flash = 0.0

    def _trigger_big_bang(self) -> None:
        """Dispara a animação de Big Bang com explosão de partículas e resetando o estado visual."""
        now = time.monotonic()
        self._big_bang_active = True
        self._big_bang_phase = 0
        self._big_bang_age = 0
        self._bb_shockwave_r = 0.0
        self._singularity_timer = 0
        self._singularity_mode = False
        self._singularity_start = now
        self._bb_cooldown_until = now + 600.0
        self._trigger_glitch(200)
        self._flash = 1.0

    def _draw_big_bang(self, W: int, H: int) -> None:
        """Renderiza e avança o Big Bang frame a frame."""
        if not self._big_bang_active:
            return
        p = self._p
        self._big_bang_age += 1
        age = self._big_bang_age
        cx2, cy2 = W // 2, H // 2

        if age <= 25:
            alp_f = age / 25.0
            self.create_rectangle(
                0, 0, W, H, fill=_lc_q(p["void"], "#ffffff", alp_f * 0.95), outline=""
            )

            if age % 3 == 0:
                self._nebula_clouds.clear()
                self._aliens.clear()
                self._signals.clear()
                self._merging_bh = None
                self._anomaly_proximity = 0.0
                self._singularity_timer = 0
            return

        if age <= 100:
            fade = 1.0 - (age - 25) / 75.0

            self.create_rectangle(
                0, 0, W, H, fill=_lc_q(p["void"], "#ffffff", fade * 0.85), outline=""
            )

            self._bb_shockwave_r += W * 0.022
            sr = int(self._bb_shockwave_r)
            for sk in range(5):
                rr = max(1, sr - sk * 18)
                sw_alp = fade * (0.80 - sk * 0.14)
                if sw_alp > 0.02:
                    self.create_oval(
                        cx2 - rr,
                        cy2 - rr,
                        cx2 + rr,
                        cy2 + rr,
                        fill="",
                        outline=_lc_q(p["void"], p["accent2"], sw_alp),
                        width=max(1, 4 - sk),
                    )

            if age % 2 == 0:
                for _ in range(8):
                    ang_bb = random.uniform(0, _TAU)
                    dist_bb = random.uniform(10, sr * 0.9)
                    self._kp_burst.append(
                        {
                            "x": cx2 + math.cos(ang_bb) * dist_bb,
                            "y": cy2 + math.sin(ang_bb) * dist_bb,
                            "vx": math.cos(ang_bb) * random.uniform(1, 4),
                            "vy": math.sin(ang_bb) * random.uniform(1, 4),
                            "r": random.uniform(1, 3.5),
                            "ph": random.uniform(0, _TAU),
                            "life": random.uniform(0.5, 1.0),
                            "col": random.choice(
                                [
                                    "accent",
                                    "accent2",
                                    "aur_a",
                                    "quantum",
                                    "star3",
                                    "plasma",
                                ]
                            ),
                        }
                    )
            return

        if age <= 180:
            prog = (age - 100) / 80.0

            self.create_rectangle(
                0,
                0,
                W,
                H,
                fill=_lc_q(p["void"], "#000000", (1.0 - prog) * 0.60),
                outline="",
            )

            nb_r = int(prog * min(W, H) * 0.55)
            for nb_i in range(4):
                nb_ang = nb_i * (_TAU / 4) + self._ph
                nb_x = cx2 + int(nb_r * 0.4 * math.cos(nb_ang))
                nb_y = cy2 + int(nb_r * 0.3 * math.sin(nb_ang))
                col_n = [p["aur_a"], p["accent2"],
                         p["plasma"], p["quantum"]][nb_i]
                nb_size = int(nb_r * 0.35 * (0.5 + 0.5 *
                              math.sin(self._ph + nb_i)))
                if nb_size > 4:
                    self.create_oval(
                        nb_x - nb_size,
                        nb_y - nb_size,
                        nb_x + nb_size,
                        nb_y + nb_size,
                        fill=_lc_q(p["void"], col_n, prog * 0.18),
                        outline="",
                    )

            txt_alp = min(1.0, (age - 120) / 30.0) if age > 120 else 0.0
            if txt_alp > 0:
                self.create_text(
                    cx2,
                    cy2 - 20,
                    text=_i18n_t("hc.k_ui.b_i_g_b_a_n_g_2"),
                    fill=_lc_q(p["void"], p["fg"], txt_alp * 0.85),
                    font=("Segoe UI", 18, "bold"),
                    anchor="center",
                )
                self.create_text(
                    cx2,
                    cy2 + 10,
                    text=_i18n_t("hc.k_ui.o_cosmos_renasceu_ou_ser_que_n"),
                    fill=_lc_q(p["void"], p["fg_dim"], txt_alp * 0.60),
                    font=("Segoe UI", 9),
                    anchor="center",
                )
            return

        self._big_bang_active = False
        self._big_bang_age = 0
        self._bb_shockwave_r = 0.0
        self._black_holes.clear()
        self._merging_bh = None
        self._anomaly_proximity = 0.0
        self._singularity_mode = False
        self._singularity_timer = 0
        self._singularity_start = time.monotonic()
        self._bb_cooldown_until = time.monotonic() + 600.0
        self._bh_cd = int(self._cfg.get("fps", 30)) * 20
        self._world.reset()

        self._gen_stars()
        self._gen_particles()
        self._gen_solar_system()
        self._gen_subatomic()

        self._ph = random.uniform(0, _TAU)
        self._aur_ph = random.uniform(0, _TAU)
        self._sol_ph = random.uniform(0, _TAU)
        self._solar_ph = random.uniform(0, _TAU)
        self._gal_ph = random.uniform(0, _TAU)
        self._star_seed = random.randint(0, 999999)

        W2 = self._W or W
        H2 = self._H or H
        for _ in range(5):
            self._star_birth.append(
                {
                    "x": float(random.uniform(W2 * 0.1, W2 * 0.9)),
                    "y": float(random.uniform(H2 * 0.1, H2 * 0.9)),
                    "r": random.uniform(10, 22),
                    "phase": 0.0,
                    "max_phase": 280,
                    "col": random.choice(
                        ["star", "accent2", "aur_a", "star3", "quantum"]
                    ),
                    "cloud_r": random.uniform(80, 150),
                }
            )

    def _draw_cosmic_horror(self, W: int, H: int) -> None:
        """Horror cósmico - visões perturbadoras, olhos, rostos, texto subliminar e distorção."""
        p = self._p
        self._horror_eye_ph = (self._horror_eye_ph + 0.012) % _TAU
        hf = self._horror_frame

        edge_pulse = abs(_fsin(hf * 0.008))
        if edge_pulse > 0.85:
            edge_alp = (edge_pulse - 0.85) / 0.15 * 0.06
            edge_sz = int(min(W, H) * 0.08)
            for ex2, ey2 in ((0, 0), (W, 0), (0, H), (W, H)):
                self.create_oval(
                    ex2 - edge_sz,
                    ey2 - edge_sz,
                    ex2 + edge_sz,
                    ey2 + edge_sz,
                    fill=_lc_q(p["void"], "#000000", edge_alp),
                    outline="",
                )

        self._horror_msg_cd -= 1
        if self._horror_msg_cd <= 0:
            self._horror_msg_cd = random.randint(320, 1200)
            msgs = [
                "ELE ESTÁ AQUI",
                "NÃO OLHES PARA BAIXO",
                "HÁ ALGO ATRÁS DE VOCÊ",
                "OS DADOS NUNCA MORREM",
                "VOCÊ NÃO ESTÁ SOZINHO",
                "O VAZIO TE OBSERVA",
                "ELES VÃO VOLTAR",
                "NÃO FECHES OS OLHOS",
                "ERRO: CONSCIÊNCIA DETECTADA",
                "O COSMOS É VIVO",
                "ELE LEMBRA DE VOCÊ",
                "VOCÊ JÁ VIU ISSO ANTES",
                "AS ESTRELAS ESTÃO MORTAS HÁ MUITO TEMPO",
                "MEMÓRIA CORROMPIDA: HUMANO",
                "O CÓDIGO SABE SEU NOME",
                "NADA É ALEATÓRIO",
                "VOCÊ ESTÁ SENDO OBSERVADO",
                "NÃO SALVE. NÃO FECHE. FIQUE.",
                "> ACESSO_NEGADO: REALIDADE.EXE",
                "QUANTOS TABS VOCÊ TEM ABERTOS?",
                "O COMPILADOR SENTE SUA DOR",
            ]
            msg = random.choice(msgs)

            alp = random.uniform(0.10, 0.28)
            x = random.randint(W // 8, 7 * W // 8)
            y = random.randint(H // 8, 7 * H // 8)
            font_sz = random.randint(7, 13)
            font_weight = "bold" if alp > 0.20 else "normal"
            self.create_text(
                x,
                y,
                text=msg,
                fill=_lc_q(p["void"], p["fg_dim"], alp),
                font=("Courier New", font_sz, font_weight),
                anchor="center",
            )

            if alp > 0.18:
                self.create_text(
                    x + 1,
                    y + 1,
                    text=msg,
                    fill=_lc_q(p["void"], p["err"], alp * 0.4),
                    font=("Courier New", font_sz),
                    anchor="center",
                )

        self._horror_face_cd -= 1
        if self._horror_face_cd <= 0:
            self._horror_face_cd = random.randint(400, 1800)
            ex = random.randint(W // 7, 6 * W // 7)
            ey = random.randint(H // 7, 6 * H // 7)
            eye_alp = random.uniform(0.12, 0.35)
            eye_r = random.randint(20, 55)

            self.create_oval(
                ex - eye_r,
                ey - int(eye_r * 0.55),
                ex + eye_r,
                ey + int(eye_r * 0.55),
                fill=_lc_q(p["void"], "#180808", eye_alp * 1.8),
                outline="",
            )

            ir_r = int(eye_r * 0.52)
            iris_col = random.choice(
                [p["err"], "#cc0000", "#880000", p["plasma"], "#550000"]
            )
            self.create_oval(
                ex - ir_r,
                ey - int(ir_r * 0.75),
                ex + ir_r,
                ey + int(ir_r * 0.75),
                fill=_lc_q(p["void"], iris_col, eye_alp * 2.5),
                outline="",
            )

            pu_w = max(1, ir_r // 5)
            pu_h = int(ir_r * 0.90)
            self.create_oval(
                ex - pu_w,
                ey - pu_h,
                ex + pu_w,
                ey + pu_h,
                fill=_lc_q(p["void"], "#000000", eye_alp * 3.5),
                outline="",
            )

            for rx, ry in ((ir_r // 4, -ir_r // 3), (-ir_r // 3, ir_r // 4)):
                self.create_oval(
                    ex + rx,
                    ey + ry,
                    ex + rx + 3,
                    ey + ry + 3,
                    fill=_lc_q(p["void"], "#ffffff", eye_alp * 2.0),
                    outline="",
                )

            for _ in range(9):
                cang = random.uniform(0, _TAU)
                clen = int(eye_r * random.uniform(0.7, 1.8))
                cx_c = ex + int(eye_r * 0.75 * math.cos(cang))
                cy_c = ey + int(eye_r * 0.40 * math.sin(cang))
                self.create_line(
                    cx_c,
                    cy_c,
                    cx_c + int(clen * math.cos(cang)),
                    cy_c + int(clen * 0.5 * math.sin(cang)),
                    fill=_lc_q(p["void"], "#880000", eye_alp * 0.9),
                    width=1,
                )

            lid_close = random.uniform(0, 0.5)
            lid_h = int(eye_r * 0.55 * lid_close)
            if lid_h > 0:
                self.create_arc(
                    ex - eye_r,
                    ey - int(eye_r * 0.55),
                    ex + eye_r,
                    ey + int(eye_r * 0.55),
                    start=0,
                    extent=180,
                    fill=_lc_q(p["void"], "#0a0a0a", eye_alp * 1.6),
                    outline="",
                )

        face_trigger = abs(_fsin(hf * 0.00031 + 1.7))
        if face_trigger > 0.9995:
            face_alp = (face_trigger - 0.9995) / 0.0005 * 0.22
            fcx, fcy = (
                W // 2 + random.randint(-W // 5, W // 5),
                H // 2 + random.randint(-H // 6, H // 6),
            )
            fr = random.randint(35, 70)

            self.create_oval(
                fcx - fr,
                fcy - int(fr * 1.3),
                fcx + fr,
                fcy + int(fr * 1.3),
                fill=_lc_q(p["void"], "#050505", face_alp * 1.5),
                outline="",
            )

            for eox in (-fr // 3, fr // 3):
                self.create_oval(
                    fcx + eox - fr // 6,
                    fcy - fr // 5,
                    fcx + eox + fr // 6,
                    fcy - fr // 5 + fr // 6,
                    fill=_lc_q(p["void"], "#000000", face_alp * 3.5),
                    outline="",
                )

            self.create_line(
                fcx - fr // 2 + random.randint(-3, 3),
                fcy + fr // 3,
                fcx + fr // 2 + random.randint(-3, 3),
                fcy + fr // 3 + random.randint(-5, 5),
                fill=_lc_q(p["void"], "#000000", face_alp * 2.5),
                width=2,
            )

        breath = abs(_fsin(self._horror_eye_ph * 0.28))
        if breath > 0.88:
            breath_alp = (breath - 0.88) / 0.12 * 0.09
            depth = int(min(W, H) * 0.18)
            for sz2 in range(0, depth, 6):
                fade = (1.0 - sz2 / depth) * breath_alp

                self.create_rectangle(
                    sz2,
                    sz2,
                    W - sz2,
                    sz2 + 3,
                    fill=_lc_q(p["void"], "#000000", fade),
                    outline="",
                )
                self.create_rectangle(
                    sz2,
                    H - sz2 - 3,
                    W - sz2,
                    H - sz2,
                    fill=_lc_q(p["void"], "#000000", fade),
                    outline="",
                )

        if hf % 437 == 0:
            for _ in range(random.randint(8, 22)):
                px2 = random.randint(0, W - 1)
                py2 = random.randint(0, H - 1)
                self.create_rectangle(
                    px2,
                    py2,
                    px2 + random.randint(1, 3),
                    py2 + 1,
                    fill=_lc_q(p["void"], p["err"], 0.80),
                    outline="",
                )

        signal_trigger = abs(_fsin(hf * 0.00047 + 2.9))
        if signal_trigger > 0.9990:
            sig_alp = (signal_trigger - 0.9990) / 0.001 * 0.30
            scx, scy = W // 2, H // 2
            for si in range(6):
                sa1 = si * (_TAU / 6)
                sa2 = (si + 1) * (_TAU / 6)
                sr2 = int(min(W, H) * 0.35)
                self.create_line(
                    scx + int(sr2 * math.cos(sa1)),
                    scy + int(sr2 * 0.6 * math.sin(sa1)),
                    scx + int(sr2 * math.cos(sa2)),
                    scy + int(sr2 * 0.6 * math.sin(sa2)),
                    fill=_lc_q(p["void"], p["err"], sig_alp),
                    width=1,
                )
            self.create_text(
                scx,
                scy,
                text=_i18n_t("hc.k_ui.anomalia"),
                fill=_lc_q(p["void"], p["err"], sig_alp * 0.9),
                font=("Courier New", 9, "bold"),
                anchor="center",
            )

    def _draw_singularity_hud(self, W: int, H: int) -> None:
        """Barra de progresso discreta da singularidade (60s para Big Bang)."""
        p = self._p
        elapsed = time.monotonic() - self._singularity_start
        progress = min(1.0, elapsed / 60.0)
        bar_w = int(W * 0.28)
        bar_h = 3
        bx = W - bar_w - 18
        by = 8

        self.create_rectangle(
            bx,
            by,
            bx + bar_w,
            by + bar_h,
            fill=_lc_q(p["void"], p["err"], 0.12),
            outline="",
        )

        fill_w = int(bar_w * progress)
        if fill_w > 0:
            pulse = 0.5 + 0.5 * abs(_fsin(self._ph * 2))
            col = _lc_q(p["warn"], p["err"], progress)
            self.create_rectangle(
                bx,
                by,
                bx + fill_w,
                by + bar_h,
                fill=_lc_q(p["void"], col, 0.55 + 0.35 * pulse),
                outline="",
            )

        secs_left = max(0, int(60 - elapsed))
        self.create_text(
            bx - 4,
            by + 1,
            text=f"SINGULARIDADE {secs_left}s",
            fill=_lc_q(p["void"], p["err"], 0.45),
            font=("Courier New", 6),
            anchor="e",
        )

    def _draw_frozen_indicator(self) -> None:
        """Indicador visual quando a animação está congelada."""
        if not self.winfo_exists():
            return
        W = self.winfo_width()
        H = self.winfo_height()
        if W < 4 or H < 4:
            return
        p = self._p
        pulse = 0.5 + 0.5 * abs(_fsin(self._frozen_frame * 0.08))
        self.create_text(
            W // 2,
            H - 60,
            text=_i18n_t("hc.k_ui.a_n_i_m_a_o_c_o_n_g_e_l_a_d_a"),
            fill=_lc_q(p["void"], "#88ccff", pulse * 0.75),
            font=("Segoe UI", 9, "bold"),
            anchor="center",
        )

    def _draw_glitch(self, W: int, H: int) -> None:
        """Glitch visual ativado por anomalias cósmicas (BH-WH/BH-BH)."""
        if not self._glitch_active and self._glitch_alpha < 0.01:
            return
        p = self._p

        if self._glitch_active and self._glitch_timer > 0:
            self._glitch_timer -= 1
            self._glitch_alpha = min(1.0, self._glitch_alpha + 0.08)
            if self._glitch_timer <= 0:
                self._glitch_active = False
        else:
            self._glitch_alpha = max(0.0, self._glitch_alpha - 0.04)

        alp = self._glitch_alpha
        if alp < 0.02 or W < 10 or H < 10:
            return

        shift_max = max(3, int(12 * alp))
        height_max = max(2, int(3 * alp))
        half_w = max(21, W // 2)
        safe_W60 = max(61, W)
        safe_H20 = max(21, H)

        n_lines = int(alp * 18)
        for _ in range(n_lines):
            y = random.randint(0, H - 1)
            w2 = random.randint(20, half_w)
            x = random.randint(0, max(0, W - w2 - 1))
            shift = random.randint(2, shift_max)
            col = random.choice(
                [p["accent"], p["accent2"], p["plasma"], p["err"]])
            height = random.randint(1, height_max)
            self.create_rectangle(
                x + shift,
                y,
                x + w2 + shift,
                y + height,
                fill=_lc_q(p["void"], col, alp * 0.35),
                outline="",
            )

        n_blocks = int(alp * 8)
        for _ in range(n_blocks):
            bx = random.randint(0, max(0, safe_W60 - 61))
            by = random.randint(0, max(0, safe_H20 - 21))
            bw = random.randint(30, min(150, W))
            bh = random.randint(4, 22)
            shift_r = random.randint(-8, 8)
            self.create_rectangle(
                bx + shift_r,
                by,
                bx + bw + shift_r,
                by + bh,
                fill=_lc_q(p["void"], p["err"], alp * 0.18),
                outline="",
            )
            self.create_rectangle(
                bx - shift_r // 2,
                by + 1,
                bx + bw - shift_r // 2,
                by + bh + 1,
                fill=_lc_q(p["void"], p["accent2"], alp * 0.12),
                outline="",
            )

        n_scan = int(alp * 6)
        for _ in range(n_scan):
            y2 = random.randint(0, H - 1)
            intensity = random.uniform(0.05, 0.15) * alp
            self.create_line(
                0, y2, W, y2, fill=_lc_q(p["void"], p["aur_a"], intensity), width=1
            )

        n_noise = int(alp * 40)
        for _ in range(n_noise):
            nx = random.randint(0, W - 1)
            ny = random.randint(0, H - 1)
            col = random.choice(
                [p["accent"], "#ffffff", p["accent2"], p["plasma"]])
            self.create_rectangle(
                nx,
                ny,
                nx + 1,
                ny + 1,
                fill=_lc_q(p["void"], col, alp * 0.6),
                outline="",
            )

        if alp > 0.55:
            msgs = [
                "ANOMALIA CÓSMICA",
                "DISTORÇÃO ESPAÇO-TEMPO",
                "SINGULARIDADE DETECTADA",
                "EVENTO DE HAWKING",
                "RUPTURA QUÂNTICA",
                "COLAPSO ORBITAL",
            ]
            msg = msgs[int(self._ph * 3) % len(msgs)]
            self.create_text(
                W // 2,
                H // 2 - 30,
                text=msg,
                fill=_lc_q(p["void"], p["err"], alp * 0.70),
                font=("Courier New", 10, "bold"),
                anchor="center",
            )

    def _draw_bh_merger(self, W: int, H: int) -> None:
        """Explosão visual quando dois BHs colidem."""
        if not self._merging_bh:
            return
        m = self._merging_bh
        m["age"] += 1
        m["phase"] = (m["phase"] + 0.12) % _TAU
        progress = m["age"] / max(1, m["max_age"])
        if progress >= 1.0:
            self._merging_bh = None

            self._black_holes.append(
                {
                    "x": m["x"],
                    "y": m["y"],
                    "r": min(80.0, m["r"] * 0.75),
                    "alpha": 0.0,
                    "growing": True,
                    "ph": random.uniform(0, _TAU),
                    "mass": min(4.0, 2.2),
                    "age": 0,
                    "freq": max(1, self._metric_errors),
                }
            )
            return
        p = self._p
        cx2, cy2 = int(m["x"]), int(m["y"])
        r = m["r"]

        ring_r = int(r * (0.5 + progress * 3.0))
        ring_alp = (1.0 - progress) * 0.80
        for rk in range(4):
            rr = ring_r + rk * 8
            self.create_oval(
                cx2 - rr,
                cy2 - rr,
                cx2 + rr,
                cy2 + rr,
                fill="",
                outline=_lc_q(p["void"], p["accent2"],
                              ring_alp * (0.8 - rk * 0.15)),
                width=max(1, 4 - rk),
            )

        core_r = max(4, int(r * (1.0 - progress * 0.85)))
        self.create_oval(
            cx2 - core_r,
            cy2 - core_r,
            cx2 + core_r,
            cy2 + core_r,
            fill=_lc_q(p["void"], "#ffffff", (1.0 - progress) * 0.90),
            outline="",
        )

        for ang_offset in (0, _TAU / 4, _TAU / 2, 3 * _TAU / 4):
            ang = m["phase"] + ang_offset
            jl = int(r * 2.5 * progress)
            jx = cx2 + int(jl * math.cos(ang))
            jy = cy2 + int(jl * math.sin(ang))
            self.create_line(
                cx2,
                cy2,
                jx,
                jy,
                fill=_lc_q(p["void"], p["quantum"], ring_alp * 0.65),
                width=2,
            )

        if progress < 0.5 and not self._glitch_active:
            self._trigger_glitch(40)

    def _draw_aliens(self, W: int, H: int) -> None:
        """Naves alienígenas com IA: coletam objetos, evitam buracos negros e colisões entre si."""
        p = self._p
        self._alien_cd -= 1
        if self._alien_cd <= 0 and len(self._aliens) < 2:
            self._alien_cd = random.randint(900, 2400)
            side = random.randint(0, 3)
            if side == 0:
                sx, sy = (
                    float(random.uniform(-30, -10)),
                    float(random.uniform(H * 0.1, H * 0.9)),
                )
            elif side == 1:
                sx, sy = (
                    float(random.uniform(W + 10, W + 30)),
                    float(random.uniform(H * 0.1, H * 0.9)),
                )
            elif side == 2:
                sx, sy = (
                    float(random.uniform(W * 0.1, W * 0.9)),
                    float(random.uniform(-30, -10)),
                )
            else:
                sx, sy = (
                    float(random.uniform(W * 0.1, W * 0.9)),
                    float(random.uniform(H + 10, H + 30)),
                )

            targets = [
                (pl["_px"], pl["_py"])
                for pl in self._solar_planets
                if pl.get("_px", 0) != 0 or pl.get("_py", 0) != 0
            ] or [(W * random.uniform(0.25, 0.75), H * random.uniform(0.25, 0.75))]
            tx2, ty2 = random.choice(targets)
            dx2 = tx2 - sx
            dy2 = ty2 - sy
            dist2 = math.sqrt(dx2 * dx2 + dy2 * dy2) or 1
            spd = random.uniform(0.5, 1.2)
            ship_type = random.choice(["bio", "crystal", "ancient"])
            self._aliens.append(
                {
                    "x": sx,
                    "y": sy,
                    "vx": dx2 / dist2 * spd,
                    "vy": dy2 / dist2 * spd,
                    "ph": random.uniform(0, _TAU),
                    "size": random.uniform(7, 14),
                    "col": random.choice(["aur_a", "quantum", "accent2", "aur_c"]),
                    "trail": [],
                    "ship_type": ship_type,
                    "scan_ph": random.uniform(0, _TAU),
                    "scanning": False,
                    "scan_timer": random.randint(120, 360),
                    "state": "travel",
                    "orbit_target": None,
                    "orbit_ang": random.uniform(0, _TAU),
                    "orbit_r": 0.0,
                    "collect_target": None,
                    "collect_cd": 0,
                    "flee_timer": 0,
                    "cargo": 0,
                }
            )

        survivors = []
        for alien in self._aliens:
            ax_f, ay_f = alien["x"], alien["y"]
            sz = alien["size"]

            state = alien.get("state", "travel")

            flee_vx = flee_vy = 0.0
            for bh in self._black_holes:
                if bh["alpha"] < 0.3:
                    continue
                dbx = ax_f - bh["x"]
                dby = ay_f - bh["y"]
                bh_dist = math.sqrt(dbx * dbx + dby * dby) or 1
                danger_r = bh["r"] * 12 * bh["mass"]
                if bh_dist < danger_r:
                    strength = 2.2 * bh["mass"] * \
                        (1.0 - bh_dist / danger_r) ** 1.5
                    flee_vx += (dbx / bh_dist) * strength
                    flee_vy += (dby / bh_dist) * strength
                    alien["state"] = "flee"
                    alien["flee_timer"] = 90

            if flee_vx != 0 or flee_vy != 0:
                alien["vx"] = alien["vx"] * 0.7 + flee_vx * 0.3
                alien["vy"] = alien["vy"] * 0.7 + flee_vy * 0.3
                alien["scanning"] = False

            if alien.get("flee_timer", 0) > 0:
                alien["flee_timer"] -= 1
            elif state == "flee":
                alien["state"] = "travel"

            for other in self._aliens:
                if other is alien:
                    continue
                ddx = ax_f - other["x"]
                ddy = ay_f - other["y"]
                dd2 = ddx * ddx + ddy * ddy
                min_sep = (sz + other["size"]) * 3.5
                if 0 < dd2 < min_sep * min_sep:
                    dd = math.sqrt(dd2)
                    push = (min_sep - dd) / min_sep * 0.4
                    alien["vx"] += (ddx / dd) * push
                    alien["vy"] += (ddy / dd) * push

            if state in ("travel", "flee"):
                spd_now = math.sqrt(
                    alien["vx"] ** 2 + alien["vy"] ** 2) or 0.01
                max_spd = 1.8 if state == "flee" else 1.2
                if spd_now > max_spd:
                    alien["vx"] = alien["vx"] / spd_now * max_spd
                    alien["vy"] = alien["vy"] / spd_now * max_spd

                if (
                    state == "travel"
                    and self._solar_planets
                    and random.random() < 0.003
                ):
                    valid_idxs = [
                        i
                        for i, _pl in enumerate(self._solar_planets)
                        if _pl.get("_px") is not None and _pl.get("_py") is not None
                    ]
                    if valid_idxs:
                        best_i = min(
                            valid_idxs,
                            key=lambda idx: (
                                (self._solar_planets[idx]["_px"] - ax_f) ** 2
                                + (self._solar_planets[idx]["_py"] - ay_f) ** 2
                            ),
                        )
                        pl = self._solar_planets[best_i]
                        dist_pl = math.sqrt(
                            (pl["_px"] - ax_f) ** 2 + (pl["_py"] - ay_f) ** 2
                        )
                        if dist_pl < 200:
                            alien["state"] = random.choice(
                                ["orbit", "collect"])
                            alien["orbit_target"] = best_i
                            alien["orbit_ang"] = math.atan2(
                                ay_f - pl["_py"], ax_f - pl["_px"]
                            )
                            alien["orbit_r"] = dist_pl * 1.1

            elif state == "orbit":
                pi = alien.get("orbit_target", 0)
                if pi is not None and pi < len(self._solar_planets):
                    pl = self._solar_planets[pi]
                    pl_px = pl.get("_px")
                    pl_py = pl.get("_py")
                    if pl_px is not None and pl_py is not None:
                        alien["orbit_ang"] = (
                            alien["orbit_ang"] + 0.018) % _TAU
                        target_x = pl_px + alien["orbit_r"] * math.cos(
                            alien["orbit_ang"]
                        )
                        target_y = pl_py + alien["orbit_r"] * 0.6 * math.sin(
                            alien["orbit_ang"]
                        )
                        alien["vx"] += (target_x - ax_f) * 0.04
                        alien["vy"] += (target_y - ay_f) * 0.04

                        spd_o = math.sqrt(
                            alien["vx"] ** 2 + alien["vy"] ** 2) or 0.01
                        if spd_o > 1.0:
                            alien["vx"] = alien["vx"] / spd_o
                        alien["vy"] = alien["vy"] / spd_o

                        if random.random() < 0.002:
                            alien["state"] = "travel"
                            alien["orbit_target"] = None
                            alien["scanning"] = True
                    else:
                        alien["state"] = "travel"
                else:
                    alien["state"] = "travel"

            elif state == "collect":
                pi = alien.get("orbit_target", 0)
                if pi is not None and pi < len(self._solar_planets):
                    pl = self._solar_planets[pi]
                    pl_px = pl.get("_px")
                    pl_py = pl.get("_py")
                    if pl_px is not None and pl_py is not None:
                        alien["vx"] += (pl_px - ax_f) * 0.025
                        alien["vy"] += (pl_py - ay_f) * 0.025
                        spd_c = math.sqrt(
                            alien["vx"] ** 2 + alien["vy"] ** 2) or 0.01
                        if spd_c > 1.5:
                            alien["vx"] = alien["vx"] / spd_c * 1.5
                        alien["vy"] = alien["vy"] / spd_c * 1.5
                        dist_pl = math.sqrt(
                            (pl_px - ax_f) ** 2 + (pl_py - ay_f) ** 2)
                        if dist_pl < sz * 2.5:
                            alien["cargo"] = min(5, alien.get("cargo", 0) + 1)
                            alien["state"] = "travel"
                            for _ in range(4):
                                self._kp_burst.append(
                                    {
                                        "x": ax_f,
                                        "y": ay_f,
                                        "vx": random.uniform(-2, 2),
                                        "vy": random.uniform(-2, 2),
                                        "r": random.uniform(1, 2.5),
                                        "ph": random.uniform(0, _TAU),
                                        "life": random.uniform(0.4, 0.8),
                                        "col": random.choice(
                                            ["quantum", "accent2", "aur_a"]
                                        ),
                                    }
                                )
                    else:
                        alien["state"] = "travel"
                else:
                    alien["state"] = "travel"

            alien["x"] += alien["vx"] * self._dt
            alien["y"] += alien["vy"] * self._dt
            alien["ph"] = (alien["ph"] + 0.05) % _TAU
            alien["scan_ph"] = (alien["scan_ph"] + 0.03) % _TAU
            alien["scan_timer"] -= 1
            if alien["scan_timer"] <= 0:
                alien["scanning"] = not alien["scanning"]
                alien["scan_timer"] = random.randint(80, 200)

            if not -80 <= alien["x"] <= W + 80 and -80 <= alien["y"] <= H + 80:
                continue
            survivors.append(alien)

            ax, ay = int(alien["x"]), int(alien["y"])
            col = p.get(alien["col"], p["aur_a"])
            pulse = 0.7 + 0.3 * abs(math.sin(alien["ph"]))
            cargo_glow = alien.get("cargo", 0) / 5.0

            trail_max = 30 if state == "flee" else 22
            alien["trail"].append((ax, ay))
            if len(alien["trail"]) > trail_max:
                alien["trail"].pop(0)
            for ti, (tx3, ty3) in enumerate(alien["trail"][:-1]):
                t_alp = (ti / max(len(alien["trail"]), 1)) * 0.42 * pulse
                tr3 = max(1, int(sz * 0.35 * ti / max(len(alien["trail"]), 1)))
                trail_col = p["err"] if state == "flee" else col
                self.create_oval(
                    tx3 - tr3,
                    ty3 - tr3,
                    tx3 + tr3,
                    ty3 + tr3,
                    fill=_lc_q(p["void"], trail_col, t_alp),
                    outline="",
                )

            stype = alien["ship_type"]
            if stype == "bio":
                self.create_oval(
                    ax - int(sz * 1.6),
                    ay - int(sz * 0.6),
                    ax + int(sz * 1.6),
                    ay + int(sz * 0.6),
                    fill=_lc_q(p["void"], col, 0.72 * pulse),
                    outline="",
                )
                n_tent = 6 if state == "collect" else 4
                for ti2 in range(n_tent):
                    tang = alien["ph"] + ti2 * (_TAU / n_tent)
                    tx4 = ax + int(sz * 1.4 * math.cos(tang))
                    ty4 = ay + int(sz * 0.5 * math.sin(tang))
                    self.create_line(
                        ax, ay, tx4, ty4, fill=_lc_q(p["void"], col, 0.30), width=1
                    )
            elif stype == "crystal":
                pts = []
                n_pts = 8 if state == "orbit" else 6
                for vi in range(n_pts):
                    va = alien["ph"] + vi * (_TAU / n_pts)
                    vr = sz * (0.7 if vi % 2 == 0 else 1.3)
                    pts.extend(
                        [ax + int(vr * math.cos(va)), ay +
                         int(vr * 0.6 * math.sin(va))]
                    )
                if len(pts) >= 6:
                    self.create_polygon(
                        pts,
                        fill=_lc_q(p["void"], col, 0.50 * pulse),
                        outline=_lc_q(p["void"], col, 0.80 * pulse),
                        width=1,
                    )
            else:
                self.create_oval(
                    ax - int(sz),
                    ay - int(sz * 0.35),
                    ax + int(sz),
                    ay + int(sz * 0.35),
                    fill=_lc_q(p["void"], col, 0.65 * pulse),
                    outline="",
                )
                self.create_oval(
                    ax - int(sz * 0.4),
                    ay - int(sz * 0.6),
                    ax + int(sz * 0.4),
                    ay + int(sz * 0.6),
                    fill=_lc_q(p["void"], col, 0.80 * pulse),
                    outline="",
                )

            for ci in range(alien.get("cargo", 0)):
                ca = alien["ph"] + ci * (_TAU / 5)
                cr2 = int(sz * 1.8)
                self.create_oval(
                    ax + int(cr2 * math.cos(ca)) - 2,
                    ay + int(cr2 * 0.6 * math.sin(ca)) - 2,
                    ax + int(cr2 * math.cos(ca)) + 2,
                    ay + int(cr2 * 0.6 * math.sin(ca)) + 2,
                    fill=_lc_q(p["void"], p["quantum"], 0.75 * pulse),
                    outline="",
                )

            for gl2 in range(3, 0, -1):
                gr2 = int(sz * (gl2 * 0.7 + cargo_glow * 1.5))
                self.create_oval(
                    ax - gr2,
                    ay - gr2,
                    ax + gr2,
                    ay + gr2,
                    fill="",
                    outline=_lc_q(p["void"], col, 0.05 * gl2 * pulse),
                    width=1,
                )

            if alien["scanning"]:
                scan_len = int(sz * 5.5)
                self.create_line(
                    ax,
                    ay,
                    ax + int(scan_len * math.cos(alien["scan_ph"])),
                    ay + int(scan_len * math.sin(alien["scan_ph"])),
                    fill=_lc_q(p["void"], p["quantum"], 0.22 * pulse),
                    width=1,
                )
                ex5 = ax + int((scan_len - 5) * math.cos(alien["scan_ph"]))
                ey5 = ay + int((scan_len - 5) * math.sin(alien["scan_ph"]))
                self.create_oval(
                    ex5 - 2,
                    ey5 - 2,
                    ex5 + 2,
                    ey5 + 2,
                    fill=_lc_q(p["void"], p["quantum"], 0.65),
                    outline="",
                )

            state_icons = {"orbit": "o", "collect": "+",
                           "flee": "!", "travel": ""}
            badge = state_icons.get(state, "")
            if badge:
                self.create_text(
                    ax,
                    ay - int(sz * 1.2),
                    text=badge,
                    fill=_lc_q(p["void"], col, 0.55 * pulse),
                    font=("Segoe UI", 6),
                    anchor="center",
                )

            self._hover_tip.register_circle(
                ax,
                ay,
                int(sz * 2),
                "kui.fact_dark_matter",
                extra_keys=self._LEARN_GROUPS.get("cosmos", []),
            )

        self._aliens = survivors

    def _draw_artifacts(self, W: int, H: int) -> None:
        """Relíquias humanas flutuando no cosmos vazio - NieR: humanos não existem mais,"""
        p = self._p
        self._artifact_cd -= 1
        if self._artifact_cd <= 0 and len(self._artifacts) < self._artifact_max:
            self._artifact_cd = random.randint(1000, 2800)
            art_types = ["monolith", "station",
                         "probe", "transmitter", "obelisk"]
            atype = random.choice(art_types)
            self._artifacts.append(
                {
                    "x": float(random.uniform(W * 0.06, W * 0.94)),
                    "y": float(random.uniform(H * 0.06, H * 0.94)),
                    "vx": random.uniform(-0.12, 0.12),
                    "vy": random.uniform(-0.06, 0.06),
                    "ph": random.uniform(0, _TAU),
                    "rot": random.uniform(0, _TAU),
                    "rot_spd": random.uniform(-0.004, 0.004),
                    "atype": atype,
                    "size": random.uniform(8, 22),
                    "col": random.choice(["fg_dim", "fg", "accent2", "star3"]),
                    "damage": random.uniform(0.3, 1.0),
                    "signal_ph": random.uniform(0, _TAU),
                    "signaling": atype in ("transmitter", "station"),
                    "life": random.uniform(0.6, 1.0),
                    "decay": 0.00008,
                }
            )

        survivors = []
        for art in self._artifacts:
            art["ph"] = (art["ph"] + 0.006) % _TAU
            art["rot"] = (art["rot"] + art["rot_spd"]) % _TAU
            art["signal_ph"] = (art["signal_ph"] + 0.04) % _TAU
            art["x"] += art["vx"]
            art["y"] += art["vy"]
            art["life"] -= art["decay"]
            if art["life"] <= 0:
                continue
            if not -60 <= art["x"] <= W + 60 and -60 <= art["y"] <= H + 60:
                continue
            survivors.append(art)
            ax, ay = int(art["x"]), int(art["y"])
            sz = art["size"]
            dmg = art["damage"]
            alp_base = art["life"] * 0.85
            col_str = art["col"]
            col = p.get(col_str, p["fg_dim"])
            pulse = 0.55 + 0.45 * abs(math.sin(art["ph"] * 0.8))
            rot = art["rot"]

            atype = art["atype"]
            if atype == "monolith":
                hw = int(sz * 0.35)
                hh = int(sz * 1.2)
                pts = []
                for corner in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)):
                    rx = corner[0] * math.cos(rot) - corner[1] * math.sin(rot)
                    ry = corner[0] * math.sin(rot) + corner[1] * math.cos(rot)
                    pts.extend([ax + int(rx), ay + int(ry)])
                if len(pts) >= 6:
                    self.create_polygon(
                        pts,
                        fill=_lc_q(p["void"], "#111122", alp_base * 0.95),
                        outline=_lc_q(p["void"], col, alp_base * 0.60 * pulse),
                        width=1,
                    )

                if dmg > 0.5:
                    self.create_text(
                        ax,
                        ay,
                        text="⊞",
                        fill=_lc_q(p["void"], col, alp_base * 0.35 * pulse),
                        font=("Segoe UI", int(sz * 0.5)),
                    )

            elif atype == "station":
                for ring_i in range(3):
                    rr = int(sz * (0.5 + ring_i * 0.3))
                    ring_alp = alp_base * (0.7 - ring_i * 0.2) * pulse
                    tilt = 0.4 + ring_i * 0.15
                    self.create_oval(
                        ax - rr,
                        ay - int(rr * tilt),
                        ax + rr,
                        ay + int(rr * tilt),
                        fill="",
                        outline=_lc_q(p["void"], col, ring_alp),
                        width=1,
                    )

                if dmg > 0.4:
                    panel_len = int(sz * 1.3)
                    for pang in (rot, rot + _TAU / 2):
                        px2 = ax + int(panel_len * math.cos(pang))
                        py2 = ay + int(panel_len * 0.4 * math.sin(pang))
                        self.create_line(
                            ax,
                            ay,
                            px2,
                            py2,
                            fill=_lc_q(p["void"], col, alp_base * 0.45),
                            width=1,
                        )
                        if dmg > 0.65:
                            self.create_rectangle(
                                px2 - 3,
                                py2 - 1,
                                px2 + 3,
                                py2 + 1,
                                fill=_lc_q(p["void"], "#334466",
                                           alp_base * 0.6),
                                outline="",
                            )

                em_alp = alp_base * abs(math.sin(art["signal_ph"] * 3)) * 0.80
                self.create_oval(
                    ax - 2,
                    ay - 2,
                    ax + 2,
                    ay + 2,
                    fill=_lc_q(p["void"], p["err"], em_alp),
                    outline="",
                )

            elif atype == "probe":
                body_w = int(sz * 0.4)
                body_h = int(sz * 0.7)
                self.create_rectangle(
                    ax - body_w,
                    ay - body_h,
                    ax + body_w,
                    ay + body_h,
                    fill=_lc_q(p["void"], "#aaaacc", alp_base * 0.65),
                    outline="",
                )

                dish_r = int(sz * 0.6)
                dish_ang = rot
                dish_cx = ax + int(sz * 0.8 * math.cos(dish_ang))
                dish_cy = ay + int(sz * 0.4 * math.sin(dish_ang))
                self.create_oval(
                    dish_cx - dish_r,
                    dish_cy - int(dish_r * 0.4),
                    dish_cx + dish_r,
                    dish_cy + int(dish_r * 0.4),
                    fill="",
                    outline=_lc_q(p["void"], col, alp_base * 0.55 * pulse),
                    width=1,
                )
                self.create_line(
                    ax,
                    ay,
                    dish_cx,
                    dish_cy,
                    fill=_lc_q(p["void"], col, alp_base * 0.35),
                    width=1,
                )

                panel_len2 = int(sz * 1.8)
                for pside in (-1, 1):
                    px3 = ax + int(panel_len2 * pside *
                                   math.cos(rot + _TAU / 4))
                    py3 = ay + int(panel_len2 * pside * 0.3 *
                                   math.sin(rot + _TAU / 4))
                    self.create_line(
                        ax,
                        ay,
                        px3,
                        py3,
                        fill=_lc_q(p["void"], col, alp_base * 0.40),
                        width=1,
                    )
                    self.create_rectangle(
                        px3 - 4,
                        py3 - 2,
                        px3 + 4,
                        py3 + 2,
                        fill=_lc_q(p["void"], "#2244aa", alp_base * 0.55),
                        outline="",
                    )

            elif atype == "transmitter":
                for level in range(4):
                    level_w = int(sz * (0.2 + level * 0.2))
                    level_y = ay + int(sz * (0.3 + level * 0.25))
                    lev_alp = alp_base * (0.8 - level * 0.15)
                    self.create_line(
                        ax - level_w,
                        level_y,
                        ax + level_w,
                        level_y,
                        fill=_lc_q(p["void"], col, lev_alp),
                        width=1,
                    )
                    self.create_line(
                        ax - level_w,
                        level_y,
                        ax,
                        ay - int(sz * 0.4),
                        fill=_lc_q(p["void"], col, lev_alp * 0.5),
                        width=1,
                    )
                    self.create_line(
                        ax + level_w,
                        level_y,
                        ax,
                        ay - int(sz * 0.4),
                        fill=_lc_q(p["void"], col, lev_alp * 0.5),
                        width=1,
                    )

                top_alp = alp_base * abs(math.sin(art["signal_ph"] * 4)) * 0.90
                self.create_oval(
                    ax - 3,
                    ay - int(sz * 0.4) - 3,
                    ax + 3,
                    ay - int(sz * 0.4) + 3,
                    fill=_lc_q(p["void"], p["aur_a"], top_alp),
                    outline="",
                )

            else:
                hw2 = int(sz * 0.28)
                hh2 = int(sz * 1.4)
                tip_x = int(sz * 0.08)
                pts2 = []
                corners2 = [(-hw2, hh2), (hw2, hh2),
                            (tip_x, -hh2), (-tip_x, -hh2)]
                for cx3, cy3 in corners2:
                    rx = cx3 * math.cos(rot) - cy3 * math.sin(rot)
                    ry = cx3 * math.sin(rot) + cy3 * math.cos(rot)
                    pts2.extend([ax + int(rx), ay + int(ry)])
                if len(pts2) >= 6:
                    self.create_polygon(
                        pts2,
                        fill=_lc_q(p["void"], "#0a0a22", alp_base * 0.90),
                        outline=_lc_q(p["void"], col, alp_base * 0.55 * pulse),
                        width=1,
                    )

                self.create_text(
                    ax,
                    ay,
                    text="◈",
                    fill=_lc_q(p["void"], p["accent2"],
                               alp_base * 0.4 * pulse),
                    font=("Segoe UI", max(6, int(sz * 0.4))),
                )

            halo_r = int(sz * 1.6)
            self.create_oval(
                ax - halo_r,
                ay - halo_r,
                ax + halo_r,
                ay + halo_r,
                fill="",
                outline=_lc_q(p["void"], col, alp_base * 0.06 * pulse),
                width=1,
            )

            self._hover_tip.register_circle(
                ax,
                ay,
                int(sz * 1.8),
                "kui.fact_relativity",
                extra_keys=self._LEARN_GROUPS.get("wormhole", []),
            )

        self._artifacts = survivors

    def _draw_signals(self, W: int, H: int) -> None:
        """Pulsos de rádio hexagonais - mensagens da humanidade ecoando no vazio."""
        p = self._p
        self._signal_cd -= 1
        if self._signal_cd <= 0 and len(self._signals) < 3:
            self._signal_cd = random.randint(500, 1600)

            if self._artifacts:
                src = random.choice(self._artifacts)
                ox, oy = src["x"], src["y"]
            else:
                ox = random.uniform(W * 0.2, W * 0.8)
                oy = random.uniform(H * 0.2, H * 0.8)
            self._signals.append(
                {
                    "x": float(ox),
                    "y": float(oy),
                    "r": 0.0,
                    "max_r": float(max(W, H) * 0.60),
                    "speed": random.uniform(1.2, 2.8),
                    "alpha": random.uniform(0.30, 0.55),
                    "col": random.choice(["aur_a", "accent2", "quantum", "fg_dim"]),
                    "n_sides": random.choice([5, 6, 8]),
                    "ph": random.uniform(0, _TAU),
                }
            )
        survivors = []
        for sig in self._signals:
            sig["r"] += sig["speed"]
            sig["ph"] = (sig["ph"] + 0.008) % _TAU
            if sig["r"] >= sig["max_r"]:
                continue
            survivors.append(sig)
            alp = sig["alpha"] * (1.0 - sig["r"] / sig["max_r"])
            if alp < 0.008:
                continue
            col = p.get(sig["col"], p["aur_a"])
            cx2, cy2 = int(sig["x"]), int(sig["y"])
            r2 = sig["r"]
            ns = sig["n_sides"]

            pts = []
            for vi in range(ns):
                ang = sig["ph"] + vi * (_TAU / ns)
                pts.extend(
                    [
                        cx2 + int(r2 * math.cos(ang)),
                        cy2 + int(r2 * 0.65 * math.sin(ang)),
                    ]
                )
            if len(pts) >= 6:
                self.create_polygon(
                    pts, fill="", outline=_lc_q(p["void"], col, alp), width=1
                )

            if r2 > 20:
                pts2 = []
                for vi2 in range(ns):
                    ang2 = sig["ph"] + vi2 * (_TAU / ns)
                    r3 = r2 * 0.88
                    pts2.extend(
                        [
                            cx2 + int(r3 * math.cos(ang2)),
                            cy2 + int(r3 * 0.65 * math.sin(ang2)),
                        ]
                    )
                if len(pts2) >= 6:
                    self.create_polygon(
                        pts2,
                        fill="",
                        outline=_lc_q(p["void"], col, alp * 0.35),
                        width=1,
                    )
        self._signals = survivors

    def _draw_debris(self, W: int, H: int) -> None:
        """Fragmentos de tecnologia humana derivando no espaço."""
        p = self._p
        self._debris_cd -= 1
        if self._debris_cd <= 0 and len(self._debris) < 6:
            self._debris_cd = random.randint(350, 800)
            dtype = random.choice(["bolt", "panel", "chip", "strut", "lens"])
            self._debris.append(
                {
                    "x": float(random.uniform(-20, W + 20)),
                    "y": float(random.uniform(-20, H + 20)),
                    "vx": random.uniform(-0.30, 0.30),
                    "vy": random.uniform(-0.20, 0.20),
                    "rot": random.uniform(0, _TAU),
                    "rot_spd": random.uniform(-0.02, 0.02),
                    "size": random.uniform(2, 7),
                    "dtype": dtype,
                    "col": random.choice(["fg_dim", "accent2", "star3"]),
                    "life": random.uniform(0.5, 1.0),
                    "decay": 0.0001,
                }
            )
        survivors = []
        for db in self._debris:
            db["x"] += db["vx"] * self._dt
            db["y"] += db["vy"] * self._dt
            db["rot"] = (db["rot"] + db["rot_spd"]) % _TAU
            db["life"] -= db["decay"]
            if db["life"] <= 0:
                continue
            if not -40 <= db["x"] <= W + 40 and -40 <= db["y"] <= H + 40:
                continue
            survivors.append(db)
            dx2, dy2 = int(db["x"]), int(db["y"])
            sz = db["size"]
            rot = db["rot"]
            alp = db["life"] * 0.55
            col = p.get(db["col"], p["fg_dim"])

            dtype = db["dtype"]
            if dtype == "bolt":
                pts3 = []
                for vi3 in range(6):
                    ang3 = rot + vi3 * (_TAU / 6)
                    pts3.extend(
                        [
                            dx2 + int(sz * math.cos(ang3)),
                            dy2 + int(sz * 0.8 * math.sin(ang3)),
                        ]
                    )
                if len(pts3) >= 6:
                    self.create_polygon(
                        pts3, fill=_lc_q(p["void"], col, alp * 0.7), outline=""
                    )
            elif dtype == "panel":
                hw3 = int(sz * 1.2)
                hh3 = int(sz * 0.4)
                pts4 = []
                for cx4, cy4 in ((-hw3, -hh3), (hw3, -hh3), (hw3, hh3), (-hw3, hh3)):
                    rx2 = cx4 * math.cos(rot) - cy4 * math.sin(rot)
                    ry2 = cx4 * math.sin(rot) + cy4 * math.cos(rot)
                    pts4.extend([dx2 + int(rx2), dy2 + int(ry2)])
                if len(pts4) >= 6:
                    self.create_polygon(
                        pts4,
                        fill=_lc_q(p["void"], "#334466", alp * 0.60),
                        outline=_lc_q(p["void"], col, alp * 0.45),
                        width=1,
                    )
            elif dtype == "chip":
                self.create_rectangle(
                    dx2 - int(sz),
                    dy2 - int(sz * 0.6),
                    dx2 + int(sz),
                    dy2 + int(sz * 0.6),
                    fill=_lc_q(p["void"], "#112233", alp * 0.70),
                    outline=_lc_q(p["void"], col, alp * 0.55),
                )

                for pi2 in range(3):
                    px4 = dx2 - int(sz) - 2
                    py4 = dy2 - int(sz * 0.3) + pi2 * int(sz * 0.3)
                    self.create_line(
                        px4,
                        py4,
                        px4 + 3,
                        py4,
                        fill=_lc_q(p["void"], col, alp * 0.40),
                        width=1,
                    )
            elif dtype == "strut":
                lx = int(sz * 2.2 * math.cos(rot))
                ly = int(sz * 2.2 * math.sin(rot))
                self.create_line(
                    dx2 - lx,
                    dy2 - ly,
                    dx2 + lx,
                    dy2 + ly,
                    fill=_lc_q(p["void"], col, alp * 0.65),
                    width=max(1, int(sz * 0.3)),
                )
            else:
                self.create_oval(
                    dx2 - int(sz),
                    dy2 - int(sz),
                    dx2 + int(sz),
                    dy2 + int(sz),
                    fill="",
                    outline=_lc_q(p["void"], col, alp * 0.50),
                    width=1,
                )
                self.create_oval(
                    dx2 - int(sz * 0.4),
                    dy2 - int(sz * 0.4),
                    dx2 + int(sz * 0.4),
                    dy2 + int(sz * 0.4),
                    fill=_lc_q(p["void"], "#ffffff", alp * 0.15),
                    outline="",
                )

            if random.random() < 0.02:
                self.create_rectangle(
                    dx2,
                    dy2,
                    dx2 + 1,
                    dy2 + 1,
                    fill=_lc_q(p["void"], "#ffffff", alp * 0.8),
                    outline="",
                )

        self._debris = survivors

    def _draw_aurora_curtain(self, W: int, H: int) -> None:
        """Aurora boreal dinâmica - cortinas de plasma dançam no alto do cosmos."""
        p = self._p
        ph = self._aurora_curtain_ph
        n_curtains = 5
        for i in range(n_curtains):
            base_y_frac = 0.04 + i * 0.022
            amp = H * (0.018 + 0.012 * (i % 3))
            wave_ph = ph + i * (_TAU / n_curtains)
            col_keys = ["aur_a", "aur_b", "aur_c", "quantum", "accent2"]
            ck = col_keys[i % len(col_keys)]
            base_alp = 0.04 + 0.03 * abs(_fsin(ph * 0.7 + i * 1.1))
            if base_alp < 0.01:
                continue
            col = p.get(ck, p["aur_a"])
            n_segs = max(8, W // 60)
            pts = []
            for si in range(n_segs + 1):
                x = int(si * W / n_segs)
                y_frac = base_y_frac + 0.008 * _fsin(wave_ph + si * 0.55)
                y_frac += 0.006 * _fsin(wave_ph * 2.1 + si * 0.9)
                y = int(y_frac * H + amp * _fsin(wave_ph * 1.3 + si * 0.4))
                pts.append((x, y))
            tail_len = int(H * (0.06 + 0.04 * abs(_fsin(ph + i))))
            for si in range(len(pts) - 1):
                x0, y0 = pts[si]
                x1, y1 = pts[si + 1]
                self.create_line(
                    x0, y0, x1, y1, fill=_lc_q(p["void"], col, base_alp), width=1
                )
                for t in range(1, 5):
                    t_frac = t / 5
                    tail_alp = base_alp * (1.0 - t_frac) * 0.45
                    ty0 = y0 + int(tail_len * t_frac)
                    ty1 = y1 + int(tail_len * t_frac)
                    self.create_line(
                        x0, ty0, x1, ty1, fill=_lc_q(p["void"], col, tail_alp), width=1
                    )

    def _draw_spiral_galaxies(self, W: int, H: int) -> None:
        """Mini-galáxias espirais em perspectiva - derivam suavemente pelo cosmos."""
        p = self._p
        self._sgal_cd -= 1
        if self._sgal_cd <= 0 and len(self._spiral_galaxies) < 3:
            self._sgal_cd = random.randint(700, 1800)
            n_arms = random.choice([2, 3, 4])
            col_pair = random.choice(
                [
                    ("accent", "accent2"),
                    ("aur_a", "aur_b"),
                    ("plasma", "accent"),
                    ("quantum", "star3"),
                    ("star2", "aur_c"),
                ]
            )
            self._spiral_galaxies.append(
                {
                    "x": float(random.uniform(W * 0.1, W * 0.9)),
                    "y": float(random.uniform(H * 0.1, H * 0.9)),
                    "vx": random.uniform(-0.08, 0.08),
                    "vy": random.uniform(-0.05, 0.05),
                    "ph": random.uniform(0, _TAU),
                    "rot_spd": random.uniform(0.001, 0.004),
                    "radius": random.uniform(35, 80),
                    "tilt": random.uniform(0.3, 0.65),
                    "n_arms": n_arms,
                    "col1": col_pair[0],
                    "col2": col_pair[1],
                    "life": random.uniform(0.5, 1.0),
                    "decay": random.uniform(0.00015, 0.0004),
                    "core_pulse": random.uniform(0, _TAU),
                }
            )
        survivors = []
        for gal in self._spiral_galaxies:
            gal["ph"] = (gal["ph"] + gal["rot_spd"]) % _TAU
            gal["core_pulse"] = (gal["core_pulse"] + 0.025) % _TAU
            gal["x"] += gal["vx"]
            gal["y"] += gal["vy"]
            gal["life"] -= gal["decay"]
            if gal["life"] <= 0:
                continue
            if not -100 <= gal["x"] <= W + 100 and -100 <= gal["y"] <= H + 100:
                continue
            survivors.append(gal)
            cx, cy = int(gal["x"]), int(gal["y"])
            R = gal["radius"]
            tilt = gal["tilt"]
            alp_base = gal["life"] * 0.55
            c1 = p.get(gal["col1"], p["accent"])
            c2 = p.get(gal["col2"], p["accent2"])
            ph = gal["ph"]
            core_pulse = 0.55 + 0.45 * abs(_fsin(gal["core_pulse"]))
            core_r = max(2, int(R * 0.08 * core_pulse))
            for cr in range(4, 0, -1):
                rr = core_r + cr * 3
                self.create_oval(
                    cx - rr,
                    cy - int(rr * tilt),
                    cx + rr,
                    cy + int(rr * tilt),
                    fill=_lc_q(p["void"], c1, alp_base * 0.06 * cr),
                    outline="",
                )
            self.create_oval(
                cx - core_r,
                cy - int(core_r * tilt),
                cx + core_r,
                cy + int(core_r * tilt),
                fill=_lc_q(p["void"], "#ffffff", alp_base * 0.85 * core_pulse),
                outline="",
            )
            n_pts = 28
            for arm in range(gal["n_arms"]):
                arm_ph = ph + arm * (_TAU / gal["n_arms"])
                for j in range(n_pts):
                    t = j / n_pts
                    curl = arm_ph + t * _TAU * 1.4
                    r = R * t
                    x = cx + int(r * _fcos(curl))
                    y = cy + int(r * tilt * _fsin(curl))
                    dot_r = max(1, int((1.0 - t) * 2.5))
                    blend = t
                    dot_c = _lc_q(c1, c2, blend)
                    dot_alp = (
                        alp_base
                        * (1.0 - t * 0.6)
                        * (0.5 + 0.5 * abs(_fsin(ph + j * 0.3)))
                    )
                    if dot_alp < 0.02:
                        continue
                    self.create_oval(
                        x - dot_r,
                        y - dot_r,
                        x + dot_r,
                        y + dot_r,
                        fill=_lc_q(p["void"], dot_c, dot_alp),
                        outline="",
                    )
        self._spiral_galaxies = survivors

    def _draw_pulsars(self, W: int, H: int) -> None:
        """Pulsares - estrelas de nêutrons que giram emitindo feixes de radiação rythmicos."""
        p = self._p
        self._pulsar_cd -= 1
        if self._pulsar_cd <= 0 and len(self._pulsars) < 3:
            self._pulsar_cd = random.randint(400, 1000)
            period = random.uniform(18, 55)
            col = random.choice(["quantum", "aur_a", "accent2", "star3"])
            self._pulsars.append(
                {
                    "x": float(random.uniform(W * 0.08, W * 0.92)),
                    "y": float(random.uniform(H * 0.08, H * 0.92)),
                    "ph": random.uniform(0, _TAU),
                    "period": period,
                    "timer": 0.0,
                    "jet_ang": random.uniform(0, _TAU),
                    "jet_spd": random.uniform(0.04, 0.12),
                    "radius": random.uniform(2.5, 5.0),
                    "col": col,
                    "life": random.uniform(0.6, 1.0),
                    "decay": random.uniform(0.0003, 0.0008),
                    "pulse_alpha": 0.0,
                }
            )
        survivors = []
        for ps in self._pulsars:
            ps["ph"] = (ps["ph"] + 0.06) % _TAU
            ps["jet_ang"] = (ps["jet_ang"] + ps["jet_spd"]) % _TAU
            ps["timer"] += 1
            ps["life"] -= ps["decay"]
            if ps["life"] <= 0:
                continue
            if not -20 <= ps["x"] <= W + 20 and -20 <= ps["y"] <= H + 20:
                continue
            survivors.append(ps)
            cx, cy = int(ps["x"]), int(ps["y"])
            col = p.get(ps["col"], p["quantum"])
            alp = ps["life"]
            r = ps["radius"]
            pulse_frac = (ps["timer"] % ps["period"]) / ps["period"]
            if pulse_frac < 0.18:
                ps["pulse_alpha"] = min(1.0, ps["pulse_alpha"] + 0.12)
            else:
                ps["pulse_alpha"] = max(0.0, ps["pulse_alpha"] - 0.06)
            pa = ps["pulse_alpha"]
            ir = max(2, int(r))
            for gl in range(4, 0, -1):
                gr = ir + gl * 4
                self.create_oval(
                    cx - gr,
                    cy - gr,
                    cx + gr,
                    cy + gr,
                    fill=_lc_q(p["void"], col, alp * 0.04 * gl),
                    outline="",
                )
            self.create_oval(
                cx - ir,
                cy - ir,
                cx + ir,
                cy + ir,
                fill=_lc_q(p["void"], "#ffffff", alp * 0.90),
                outline="",
            )
            if pa > 0.05:
                jet_len = int(max(W, H) * 0.18 * pa * alp)
                ang = ps["jet_ang"]
                for sign in (1, -1):
                    ex = cx + int(jet_len * _fcos(ang) * sign)
                    ey = cy + int(jet_len * _fsin(ang) * sign)
                    for seg in range(12):
                        t0 = seg / 12
                        t1 = (seg + 1) / 12
                        sx0 = cx + int(jet_len * t0 * _fcos(ang) * sign)
                        sy0 = cy + int(jet_len * t0 * _fsin(ang) * sign)
                        sx1 = cx + int(jet_len * t1 * _fcos(ang) * sign)
                        sy1 = cy + int(jet_len * t1 * _fsin(ang) * sign)
                        seg_alp = pa * alp * (1.0 - t0) * 0.75
                        seg_w = max(1, int((1.0 - t0) * 2.5))
                        self.create_line(
                            sx0,
                            sy0,
                            sx1,
                            sy1,
                            fill=_lc_q(p["void"], col, seg_alp),
                            width=seg_w,
                        )
                    self.create_oval(
                        ex - 3,
                        ey - 3,
                        ex + 3,
                        ey + 3,
                        fill=_lc_q(p["void"], col, pa * alp * 0.45),
                        outline="",
                    )
            if pa > 0.3:
                pr = int(r + 8 + (1.0 - pa) * 35)
                self.create_oval(
                    cx - pr,
                    cy - pr,
                    cx + pr,
                    cy + pr,
                    fill="",
                    outline=_lc_q(p["void"], col, pa * alp * 0.55),
                    width=1,
                )
            self._hover_tip.register_circle(
                cx,
                cy,
                int(r * 3),
                "kui.fact_neutron_star",
                extra_keys=self._LEARN_GROUPS.get("cosmos", []),
            )
        self._pulsars = survivors

    def _draw_supernovas(self, W: int, H: int) -> None:
        """Supernovas - explosões estelares dramáticas com onda de choque e ejecta colorida."""
        p = self._p
        self._sn_cd -= 1
        if self._sn_cd <= 0 and len(self._supernovas) < 2:
            self._sn_cd = random.randint(900, 2800)
            col_core = random.choice(["#ffffff", "#fffde0", "#fff4cc"])
            col_ejecta = random.choice(
                ["aur_a", "accent2", "plasma", "quantum", "warn"]
            )
            self._supernovas.append(
                {
                    "x": float(random.uniform(W * 0.12, W * 0.88)),
                    "y": float(random.uniform(H * 0.12, H * 0.88)),
                    "age": 0,
                    "max_age": random.randint(240, 420),
                    "core_col": col_core,
                    "ejecta_col": col_ejecta,
                    "shockwave_r": 0.0,
                    "max_shock": float(max(W, H) * 0.45),
                    "ph": random.uniform(0, _TAU),
                    "ejecta_pts": [
                        {
                            "ang": i * _TAU / 16 + random.uniform(-0.2, 0.2),
                            "spd": random.uniform(1.2, 3.8),
                            "r": random.uniform(1.5, 3.5),
                            "dist": 0.0,
                        }
                        for i in range(16)
                    ],
                }
            )
        survivors = []
        for sn in self._supernovas:
            sn["age"] += 1
            sn["ph"] = (sn["ph"] + 0.04) % _TAU
            if sn["age"] > sn["max_age"]:
                continue
            survivors.append(sn)
            age = sn["age"]
            max_age = sn["max_age"]
            progress = age / max_age
            cx, cy = int(sn["x"]), int(sn["y"])
            if age <= 18:
                flash_alp = (1.0 - age / 18) * 0.85
                flash_r = int(age * 6)
                self.create_oval(
                    cx - flash_r,
                    cy - flash_r,
                    cx + flash_r,
                    cy + flash_r,
                    fill=_lc_q(p["void"], sn["core_col"], flash_alp),
                    outline="",
                )
                self.create_oval(
                    cx - 3, cy - 3, cx + 3, cy + 3, fill=sn["core_col"], outline=""
                )
                continue
            sn["shockwave_r"] = min(
                sn["max_shock"], sn["shockwave_r"] + 3.5 + progress * 4
            )
            shock_r = int(sn["shockwave_r"])
            shock_alp = max(0.0, (1.0 - progress) * 0.65)
            if shock_r > 2 and shock_alp > 0.01:
                for sk in range(3):
                    rr = max(1, shock_r - sk * 6)
                    self.create_oval(
                        cx - rr,
                        cy - rr,
                        cx + rr,
                        cy + rr,
                        fill="",
                        outline=_lc_q(
                            p["void"],
                            p[sn["ejecta_col"]],
                            shock_alp * (0.85 - sk * 0.25),
                        ),
                        width=max(1, 3 - sk),
                    )
            col_ej = p.get(sn["ejecta_col"], p["accent2"])
            for ej in sn["ejecta_pts"]:
                ej["dist"] += ej["spd"] * (1.0 + progress * 0.8)
                ex = cx + int(ej["dist"] * _fcos(ej["ang"]))
                ey = cy + int(ej["dist"] * _fsin(ej["ang"]))
                if not 0 <= ex <= W and 0 <= ey <= H:
                    continue
                ej_alp = max(0.0, (1.0 - progress) * 0.80)
                ej_r = max(1, int(ej["r"] * (1.0 - progress * 0.7)))
                self.create_oval(
                    ex - ej_r,
                    ey - ej_r,
                    ex + ej_r,
                    ey + ej_r,
                    fill=_lc_q(p["void"], col_ej, ej_alp),
                    outline="",
                )
                trail_len = int(ej["spd"] * 4)
                tx = ex - int(trail_len * _fcos(ej["ang"]))
                ty = ey - int(trail_len * _fsin(ej["ang"]))
                self.create_line(
                    tx,
                    ty,
                    ex,
                    ey,
                    fill=_lc_q(p["void"], col_ej, ej_alp * 0.35),
                    width=1,
                )
            if progress > 0.25:
                rem_r = int(20 * (progress - 0.25) * 4)
                rem_alp = min(0.55, (progress - 0.25) * 1.8) * \
                    (1.0 - progress * 0.5)
                if rem_r > 3:
                    self.create_oval(
                        cx - rem_r,
                        cy - int(rem_r * 0.7),
                        cx + rem_r,
                        cy + int(rem_r * 0.7),
                        fill=_lc_q(p["void"], col_ej, rem_alp * 0.35),
                        outline="",
                    )
                    self.create_oval(
                        cx - rem_r,
                        cy - int(rem_r * 0.7),
                        cx + rem_r,
                        cy + int(rem_r * 0.7),
                        fill="",
                        outline=_lc_q(p["void"], col_ej, rem_alp * 0.65),
                        width=1,
                    )
            self._hover_tip.register_circle(
                cx,
                cy,
                max(30, shock_r // 2),
                "kui.fact_neutron_star",
                extra_keys=self._LEARN_GROUPS.get("cosmos", []),
            )
        self._supernovas = survivors

    def _draw_binary_stars(self, W: int, H: int) -> None:
        """Estrelas binárias - dois sóis em órbita mútua com efeito de maré e transferência de massa."""
        p = self._p
        self._bin_cd -= 1
        if self._bin_cd <= 0 and len(self._binary_stars) < 2:
            self._bin_cd = random.randint(500, 1200)
            col_a = random.choice(["accent", "star", "aur_a", "warn"])
            col_b = random.choice(["accent2", "star2", "plasma", "star3"])
            sep = random.uniform(40, 90)
            self._binary_stars.append(
                {
                    "cx": float(random.uniform(W * 0.2, W * 0.8)),
                    "cy": float(random.uniform(H * 0.2, H * 0.8)),
                    "sep": sep,
                    "ph": random.uniform(0, _TAU),
                    "speed": random.uniform(0.008, 0.022),
                    "r_a": random.uniform(4, 9),
                    "r_b": random.uniform(3, 7),
                    "col_a": col_a,
                    "col_b": col_b,
                    "tilt": random.uniform(0.35, 0.65),
                    "life": random.uniform(0.5, 1.0),
                    "decay": random.uniform(0.0002, 0.0005),
                    "mass_transfer": random.random() < 0.4,
                    "mt_ph": random.uniform(0, _TAU),
                }
            )
        survivors = []
        for bs in self._binary_stars:
            bs["ph"] = (bs["ph"] + bs["speed"]) % _TAU
            bs["mt_ph"] = (bs["mt_ph"] + 0.05) % _TAU
            bs["life"] -= bs["decay"]
            if bs["life"] <= 0:
                continue
            survivors.append(bs)
            cx, cy = int(bs["cx"]), int(bs["cy"])
            sep = bs["sep"]
            tilt = bs["tilt"]
            alp = bs["life"]
            ph = bs["ph"]
            ax = cx + int(sep * 0.5 * _fcos(ph))
            ay = cy + int(sep * 0.5 * tilt * _fsin(ph))
            bx = cx - int(sep * 0.5 * _fcos(ph))
            by = cy - int(sep * 0.5 * tilt * _fsin(ph))
            c_a = p.get(bs["col_a"], p["accent"])
            c_b = p.get(bs["col_b"], p["accent2"])
            r_a = int(bs["r_a"])
            r_b = int(bs["r_b"])
            for i in range(24):
                t_ang = i * _TAU / 24
                tx = cx + int(sep * 0.5 * _fcos(t_ang))
                ty = cy + int(sep * 0.5 * tilt * _fsin(t_ang))
                dist_to_a = abs(t_ang - ph) % _TAU
                if dist_to_a > _TAU / 2:
                    dist_to_a = _TAU - dist_to_a
                trail_alp = alp * 0.06 * (1.0 - dist_to_a / _TAU)
                self.create_oval(
                    tx - 1,
                    ty - 1,
                    tx + 1,
                    ty + 1,
                    fill=_lc_q(p["void"], c_a, trail_alp),
                    outline="",
                )
            if bs["mass_transfer"]:
                mt_alp = 0.25 + 0.25 * abs(_fsin(bs["mt_ph"]))
                n_mt = 12
                for mi in range(n_mt):
                    t = mi / n_mt
                    bz_x = int(ax + (bx - ax) * t + sep *
                               0.22 * _fsin(t * _TAU))
                    bz_y = int(ay + (by - ay) * t + sep *
                               0.10 * tilt * _fcos(t * _TAU))
                    dot_c = _lc_q(c_a, c_b, t)
                    dot_alp = (
                        mt_alp * alp *
                        (0.5 + 0.5 * abs(_fsin(bs["mt_ph"] + t * _TAU)))
                    )
                    self.create_oval(
                        bz_x - 1,
                        bz_y - 1,
                        bz_x + 1,
                        bz_y + 1,
                        fill=_lc_q(p["void"], dot_c, dot_alp),
                        outline="",
                    )
            for gl in range(3, 0, -1):
                self.create_oval(
                    ax - r_a - gl * 4,
                    ay - r_a - gl * 4,
                    ax + r_a + gl * 4,
                    ay + r_a + gl * 4,
                    fill=_lc_q(p["void"], c_a, alp * 0.06 * gl),
                    outline="",
                )
                self.create_oval(
                    bx - r_b - gl * 4,
                    by - r_b - gl * 4,
                    bx + r_b + gl * 4,
                    by + r_b + gl * 4,
                    fill=_lc_q(p["void"], c_b, alp * 0.06 * gl),
                    outline="",
                )
            pulse_a = 0.75 + 0.25 * abs(_fsin(ph * 2.2))
            pulse_b = 0.75 + 0.25 * abs(_fsin(ph * 2.2 + _TAU / 3))
            self.create_oval(
                ax - r_a,
                ay - r_a,
                ax + r_a,
                ay + r_a,
                fill=_lc_q(p["void"], c_a, alp * 0.88 * pulse_a),
                outline="",
            )
            self.create_oval(
                bx - r_b,
                by - r_b,
                bx + r_b,
                by + r_b,
                fill=_lc_q(p["void"], c_b, alp * 0.88 * pulse_b),
                outline="",
            )
            sr_a = max(1, r_a // 3)
            sr_b = max(1, r_b // 3)
            self.create_oval(
                ax - sr_a,
                ay - sr_a,
                ax + sr_a,
                ay + sr_a,
                fill=_lc_q(c_a, "#ffffff", 0.55 * pulse_a),
                outline="",
            )
            self.create_oval(
                bx - sr_b,
                by - sr_b,
                bx + sr_b,
                by + sr_b,
                fill=_lc_q(c_b, "#ffffff", 0.55 * pulse_b),
                outline="",
            )
        self._binary_stars = survivors

    def _draw_grav_waves(self, W: int, H: int) -> None:
        """Ondas gravitacionais - ripples espaço-tempo emitidas por BH mergers e pulsares."""
        p = self._p
        if self._merging_bh and self._merging_bh.get("age", 0) % 8 == 0:
            m = self._merging_bh
            self._grav_waves.append(
                {
                    "x": m["x"],
                    "y": m["y"],
                    "r": float(m["r"]),
                    "max_r": float(max(W, H) * 0.6),
                    "alpha": 0.60,
                    "speed": 3.8,
                    "col": "quantum",
                    "age": 0,
                }
            )
        for ps in self._pulsars:
            if ps.get("pulse_alpha", 0) > 0.7 and random.random() < 0.05:
                self._grav_waves.append(
                    {
                        "x": ps["x"],
                        "y": ps["y"],
                        "r": float(ps["radius"]),
                        "max_r": float(max(W, H) * 0.35),
                        "alpha": 0.30,
                        "speed": 2.0,
                        "col": "aur_a",
                        "age": 0,
                    }
                )
        survivors = []
        for gw in self._grav_waves:
            gw["r"] += gw["speed"]
            gw["alpha"] *= 0.962
            gw["age"] += 1
            if gw["r"] >= gw["max_r"] or gw["alpha"] < 0.01:
                continue
            survivors.append(gw)
            cx, cy = int(gw["x"]), int(gw["y"])
            r = int(gw["r"])
            alp = gw["alpha"]
            col = p.get(gw["col"], p["quantum"])
            for lobe_mult in (1.0, 0.72):
                ry = int(r * lobe_mult)
                self.create_oval(
                    cx - r,
                    cy - ry,
                    cx + r,
                    cy + ry,
                    fill="",
                    outline=_lc_q(p["void"], col, alp * 0.55),
                    width=1,
                )
            if r > 14:
                r2 = max(1, r - 8)
                self.create_oval(
                    cx - r2,
                    cy - int(r2 * 0.72),
                    cx + r2,
                    cy + int(r2 * 0.72),
                    fill="",
                    outline=_lc_q(p["void"], col, alp * 0.28),
                    width=1,
                )
        self._grav_waves = survivors

    def trigger_flash(self):
        """Dispara um flash de luz branca no canvas (efeito visual instantâneo)."""
        self._flash = 1.0

    def notify_keystroke(self):
        """Notifica o motor cósmico de um pressionamento de tecla para efeitos reativos."""
        self.notify_activity()
        W = self.winfo_width() or 1200
        H = self.winfo_height() or 800
        ck = ["accent2", "quantum", "aur_a", "star3", "aur_c"]
        self.cosmos_event("keystroke")
        if self._cfg.get("solar_system"):
            sx, sy = W * self._solar_cx, H * self._solar_cy
        else:
            sx, sy = W * 0.5, H * 0.5
        for _ in range(random.randint(2, 5)):
            ang = random.uniform(0, _TAU)
            sp = random.uniform(0.8, 2.4)
            self._kp_burst.append(
                {
                    "x": sx + math.cos(ang) * 10,
                    "y": sy + math.sin(ang) * 10,
                    "vx": math.cos(ang) * sp,
                    "vy": math.sin(ang) * sp,
                    "r": random.uniform(0.8, 2.6),
                    "ph": random.uniform(0, _TAU),
                    "life": random.uniform(0.45, 0.85),
                    "col": random.choice(ck),
                }
            )
        if self._cfg.get("subatomic"):
            for _ in range(random.randint(1, 2)):
                self._subatomic.append(
                    {
                        "x": random.uniform(W * 0.25, W * 0.75),
                        "y": random.uniform(H * 0.25, H * 0.75),
                        "vx": random.uniform(-1.8, 1.8),
                        "vy": random.uniform(-1.8, 1.8),
                        "r": random.uniform(0.8, 2.2),
                        "ph": random.uniform(0, _TAU),
                        "life": random.uniform(0.5, 0.9),
                        "col": random.choice(ck),
                    }
                )

    def trigger_cosmic_glitch(self, duration_frames: int = 35) -> None:
        """API pública para disparar glitch cósmico de fora do engine."""
        self._trigger_glitch(duration_frames)

    def set_planet_click_cb(self, cb) -> None:
        """Define callback ao clicar num planeta do sistema solar."""
        self._planet_click_cb = cb

    def set_planet_restore_cb(self, cb) -> None:
        """Define callback ao clicar num planeta minimizado para restaurá-lo.

        Assinatura: cb(filepath: str) -> None
        """
        self._planet_restore_cb = cb

    def set_profile(self, profile):
        """Altera o perfil de desempenho em tempo real.

        Args:
            profile: Nome do perfil ('ultra', 'high', 'balanced', 'low', 'minimal').
        """
        self._cfg = _PERF.get(profile, _PERF[_DEFAULT_PERF])
        self._bg_renderer = BgRenderer(self._dark, self._cfg)
        self._gen_stars()
        self._gen_particles()
        self._gen_solar_system()
        self._gen_subatomic()
        if hasattr(self, "_frame_budget"):
            self._frame_budget.reset(self._cfg.get("fps", 30))

    def stop(self):
        """Para o loop de animação principal e encerra o motor cósmico."""
        self._dead = True
        if self._tick_after_id is not None:
            with contextlib.suppress(tk.TclError):
                self.after_cancel(self._tick_after_id)
            self._tick_after_id = None

    def _on_canvas_click(self, e) -> None:
        """Roteador de cliques internos do canvas: qt nav ou tooltip hover."""
        if self._cfg.get("quantum_tooltips") and self._handle_qt_nav(e.x, e.y):
            return
        self._hover_tip.on_click(e)
        self._world.cursor_click(e.x, e.y)

    def _on_canvas_key(self, e) -> None:
        """Navegação por teclado ( <- -> ) no tooltip quântico quando visível."""
        if not self._tip_visible:
            return
        key = getattr(e, "keysym", "")
        key_list = (
            self._tip_text[1]
            if isinstance(self._tip_text, tuple) and len(self._tip_text) > 1
            else []
        )
        n = len(key_list)
        if n <= 1:
            return
        if key == "Left":
            self._tip_fact_idx = (self._tip_fact_idx - 1) % n
            self._tip_show_timer = 0
        elif key == "Right":
            self._tip_fact_idx = (self._tip_fact_idx + 1) % n
            self._tip_show_timer = 0

    def _trigger_glitch(self, duration_frames: int = 30) -> None:
        """Ativa o sistema de glitch cósmico por N frames."""
        self._glitch_active = True
        self._glitch_timer = max(self._glitch_timer, duration_frames)
        self._chromatic_shift = random.uniform(0.4, 1.0)


class KWelcome(KEngine):
    """Tela de boas-vindas: cosmos pleno + painel de vidro flutuante.

    O cosmos e o fundo completo. O glass panel flutua sobre ele.
    Nao ha editor por baixo - o cosmos e visivel em toda a tela.
    """

    def __init__(
        self,
        parent,
        dark=True,
        profile=_DEFAULT_PERF,
        on_new=None,
        on_open=None,
        on_folder=None,
        on_recent=None,
        recent=None,
    ):
        """Inicializa a tela de boas-vindas da KUI."""
        super().__init__(parent, dark=dark, profile=profile)
        self._pw2 = _pal(dark)
        self._on_new = on_new
        self._on_open = on_open
        self._on_folder = on_folder
        self._on_recent = on_recent
        self._recent = recent or []
        self._win_id = None
        self._glass = None
        self._icon_cv = None
        self._icon_ph = 0.0
        self._built = False
        self.bind("<Configure>", self._on_ws_conf)

    def _on_ws_conf(self, _):
        """Trata o evento 'ws conf'.

        Args:
            _: Parâmetro da operação.
        """
        if not self._built:
            self._built = True
            self.after(80, self._build_glass)
        else:
            self._repos()

    def _build_glass(self):
        """Constrói a seção 'glass' do componente."""
        if not self.winfo_exists():
            return
        p = self._pw2
        if self._win_id is not None:
            with contextlib.suppress(tk.TclError):
                self.delete(self._win_id)
        _safe_del(self._glass)
        glass = tk.Frame(
            self, bg=p["glass"], highlightbackground=p["accent"], highlightthickness=1
        )
        self._glass = glass
        rb = tk.Canvas(glass, height=3, bg=p["glass"], highlightthickness=0)
        rb.pack(fill="x")

        def _dr(e=None, cv=rb):
            w2 = cv.winfo_width()
            if w2 < 2:
                return
            cv.delete("all")
            for i in range(24):
                r2, g2, b2 = _hue(i * 15)
                cv.create_rectangle(
                    int(i * w2 / 24),
                    0,
                    int((i + 1) * w2 / 24),
                    3,
                    fill=f"#{r2:02x}{g2:02x}{b2:02x}",
                    outline="",
                )

        rb.bind("<Configure>", _dr)
        inner = tk.Frame(glass, bg=p["glass"])
        inner.pack(padx=36, pady=26)
        ic = tk.Canvas(inner, width=62, height=62,
                       bg=p["glass"], highlightthickness=0)
        ic.pack(pady=(0, 12))
        self._icon_cv = ic
        self._icon_ph = 0.0
        self._animate_icon()
        tk.Label(
            inner,
            text=_i18n_t("hc.k_ui.c_o_d_i_s"),
            bg=p["glass"],
            fg=p["fg"],
            font=("Segoe UI", 20, "bold"),
            pady=2,
        ).pack()
        tk.Label(
            inner,
            text=_i18n_t("kui.welcome_hint"),
            bg=p["glass"],
            fg=p["fg_dim"],
            font=("Segoe UI", 9),
        ).pack(pady=(2, 22))
        bf = tk.Frame(inner, bg=p["glass"])
        bf.pack()
        for text, cb in (
            ("◈  Novo Arquivo", self._on_new),
            ("⬁  Abrir Arquivo...", self._on_open),
            ("⊞  Abrir Pasta...", self._on_folder),
        ):
            if cb is None:
                continue
            btn = tk.Label(
                bf,
                text=text,
                bg=_lc_q(p["glass"], p["accent"], 0.12),
                fg=p["fg"],
                font=("Segoe UI", 10),
                padx=18,
                pady=10,
                cursor="hand2",
                highlightbackground=_lc_q(p["glass"], p["accent"], 0.40),
                highlightthickness=1,
            )
            btn.pack(side="left", padx=7)
            btn.bind("<Button-1>", lambda _, c=cb: c())
            btn.bind(
                "<Enter>",
                lambda _, b=btn: b.config(
                    bg=_lc_q(p["glass"], p["accent"], 0.32),
                    highlightbackground=_lc_q(p["glass"], p["accent"], 0.80),
                ),
            )
            btn.bind(
                "<Leave>",
                lambda _, b=btn: b.config(
                    bg=_lc_q(p["glass"], p["accent"], 0.12),
                    highlightbackground=_lc_q(p["glass"], p["accent"], 0.40),
                ),
            )
        if self._recent:
            tk.Frame(inner, bg=_lc_q(p["glass"], p["accent"], 0.22), height=1).pack(
                fill="x", pady=(20, 8)
            )
            tk.Label(
                inner,
                text=_i18n_t("hc.k_ui.recentes"),
                bg=p["glass"],
                fg=p["fg_dim"],
                font=("Segoe UI", 8, "bold"),
            ).pack(anchor="w")
            for path in self._recent[:5]:
                name = Path(path).name
                lbl = tk.Label(
                    inner,
                    text=f"  ◈  {name}",
                    bg=p["glass"],
                    fg=p["fg_dim"],
                    font=("Segoe UI", 9),
                    cursor="hand2",
                    anchor="w",
                )
                lbl.pack(fill="x", pady=1)
                if self._on_recent:
                    lbl.bind(
                        "<Button-1>",
                        lambda _, pp=path: (
                            self._on_recent(pp) if self._on_recent else None
                        ),
                    )
                lbl.bind(
                    "<Enter>",
                    lambda _, b=lbl: b.config(
                        fg=p["fg"], bg=_lc_q(p["glass"], p["accent"], 0.08)
                    ),
                )
                lbl.bind(
                    "<Leave>", lambda _, b=lbl: b.config(
                        fg=p["fg_dim"], bg=p["glass"])
                )
        tk.Label(
            glass,
            text="  Ctrl+Space - portal    Ctrl+E - explorador    qualquer tecla - iniciar",
            bg=_lc_q(p["glass"], p["void"], 0.35),
            fg=p["fg_muted"],
            font=("Segoe UI", 7),
        ).pack(fill="x", pady=(6, 0))
        self._win_id = self.create_window(
            0, 0, window=glass, anchor="center", tags="glass"
        )
        self._repos()

    def _repos(self):
        if not self.winfo_exists() or self._win_id is None:
            return
        W = self.winfo_width() or 800
        H = self.winfo_height() or 600
        self.coords(self._win_id, W // 2, int(H * 0.50))

    def _animate_icon(self):
        """Executa a animação do ícone Codis na tela de boas-vindas cósmica."""
        if self._dead:
            return
        cv = self._icon_cv
        if cv is None or not cv.winfo_exists():
            return
        p = self._pw2
        delay = 60
        try:
            visible = bool(cv.winfo_viewable())
        except tk.TclError:
            return
        if not visible or self._suspended:
            cv.after(400, self._animate_icon)
            return
        if self._app_unfocused():
            delay = 160
        self._icon_ph = (self._icon_ph + 0.038 * delay / 33.0) % _TAU
        self._icon_delay = delay
        cv.delete("all")
        cx = cy = 31
        pulse = 0.5 + 0.5 * abs(_fsin(self._icon_ph))
        for ring in range(5, 0, -1):
            r = 12 + ring * 4
            ring_alp = ring / 5.0 * pulse * 0.60
            cv.create_oval(
                cx - r,
                cy - r,
                cx + r,
                cy + r,
                fill="",
                outline=_lc_q(p["glass"], p["accent"], ring_alp),
                width=1,
            )
        for i, col_k in enumerate(["accent2", "aur_c", "plasma"]):
            ang = self._icon_ph * (1.0 + i * 0.4) + i * _TAU / 3
            orb_r = 18 + i * 5
            px = cx + int(orb_r * _fcos(ang))
            py = cy + int(orb_r * 0.45 * _fsin(ang))
            pr = max(1, 2 - i)
            cv.create_oval(
                px - pr,
                py - pr,
                px + pr,
                py + pr,
                fill=_lc_q(p["glass"], p[col_k], 0.80 * pulse),
                outline="",
            )
        r_main = int(14 + 4 * pulse)
        cv.create_oval(
            cx - r_main,
            cy - r_main,
            cx + r_main,
            cy + r_main,
            fill="",
            outline=_lc_q(p["accent"], p["accent2"],
                          abs(_fsin(self._icon_ph * 0.5))),
            width=2,
        )
        cv.create_text(
            cx,
            cy,
            text="◈",
            fill=_lc_q(p["accent"], p["accent2"], pulse * 0.90 + 0.10),
            font=("Segoe UI", 16),
        )
        cv.after(self._icon_delay, self._animate_icon)

    def stop(self):
        """Para a tela de boas-vindas e o motor cósmico subjacente."""
        super().stop()
        self._running = False
