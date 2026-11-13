"""
K-UI - Sistema de Easter Eggs com detecção inteligente de contexto.

REGRA DE ATIVAÇÃO:
  - Modo Cosmos (planet_active=True): easter eggs SEMPRE ativos.
    O canvas rouba o foco do teclado automaticamente, isolando o editor.
  - Modo Normal: ativo apenas se o widget com foco NÃO for um editor de código.
    Widgets internos do K-UI (portal, sidebar) são marcados com
    o atributo ``_kui_internal = True`` e permitem easter eggs.

COMO USAR:
    eggs = KUEasterEggs(root, get_engine_cb, toast_cb, get_planet_active_cb)
    # Bindar no root:
    root.bind("<Key>", eggs.on_key, add=True)
    # Sinalizar modo planeta:
    eggs.set_planet_active(True)
"""

from __future__ import annotations

import logging
import math
import random
import time
import tkinter as tk
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("Codis")

_TAU = math.tau


class KUEasterEggs:
    """Detector de sequências de teclado com easter eggs cósmicos.

    Completamente desacoplado do KUI - recebe callbacks em vez de referências
    diretas, facilitando testes e reutilização.
    """

    _SEQS = {
        "konami": 6,
        "codis": 5,
        "space": 5,
        "nova": 4,
        "alien": 5,
        "nier": 4,
        "glitch": 6,
        "reset": 5,
        "freeze": 6,
        "horror": 6,
    }

    _TIMEOUTS = {
        "konami": 4.0,
        "codis": 3.5,
        "space": 3.5,
        "nova": 3.0,
        "alien": 4.0,
        "nier": 3.5,
        "glitch": 4.5,
        "reset": 4.0,
        "freeze": 5.0,
        "horror": 5.0,
    }

    def __init__(
        self,
        root: tk.Misc,
        get_engine: Callable[[], Any],
        toast: Callable[[str, str, int], None],
        get_planet_active: Callable[[], bool],
        spawn_supernova: Callable[[], None],
        spawn_meteor_shower: Callable[[], None],
        spawn_star_birth: Callable[[], None],
        spawn_nova_burst: Callable[[], None],
    ) -> None:
        """Inicializa o controlador de easter eggs da KUI."""
        self._root = root
        self._get_engine = get_engine
        self._toast = toast
        self._get_planet_active = get_planet_active
        self._spawn_supernova = spawn_supernova
        self._spawn_meteor = spawn_meteor_shower
        self._spawn_star_birth = spawn_star_birth
        self._spawn_nova_burst = spawn_nova_burst

        self._bufs: dict[str, str] = dict.fromkeys(self._SEQS, "")
        self._times: dict[str, float] = dict.fromkeys(self._SEQS, 0.0)

    def on_key(self, e: tk.Event) -> None:
        """Processa teclas pressionadas para detectar sequências de easter egg."""
        if not self._should_process(e):
            return
        char = getattr(e, "char", "") or ""
        if not char or not char.isprintable():
            return
        self._dispatch(char.lower())

    def _should_process(self, e: tk.Event) -> bool:
        """Decide se o evento deve ser processado.

        Regras (em ordem de prioridade):
        1. Modo cosmos ativo -> sempre processa (canvas tem foco exclusivo).
        2. Widget com foco é marcado como _kui_internal -> processa.
        3. Widget com foco é tk.Text ou tk.Entry -> BLOQUEIA (editor de código).
        4. Qualquer outro widget -> processa.
        """
        if self._get_planet_active():
            return True

        focused: tk.Misc | None = None
        with __import__("contextlib").suppress(tk.TclError):
            focused = self._root.focus_get()

        if focused is None:
            return True

        if getattr(focused, "_kui_internal", False):
            return True

        return not isinstance(focused, (tk.Text, tk.Entry))

    def _dispatch(self, cl: str) -> None:
        """Atualiza buffers e dispara easter eggs detectados."""
        now = time.time()
        for name, length in self._SEQS.items():
            timeout = self._TIMEOUTS[name]
            if now - self._times[name] > timeout:
                self._bufs[name] = ""
            self._times[name] = now
            self._bufs[name] = (self._bufs[name] + cl)[-length:]
            if self._bufs[name] == name:
                self._bufs[name] = ""
                self._fire(name)
                break

    def _fire(self, name: str) -> None:
        """Dispara o efeito correspondente à sequência detectada."""
        eng = self._get_engine()
        handler = getattr(self, f"_egg_{name}", None)
        if handler:
            handler(eng)

    def _egg_konami(self, eng: Any) -> None:
        """Executa a operação 'egg konami'.

        Args:
            eng: Parâmetro da operação.
        """
        if not eng:
            return
        for i in range(5):
            self._root.after(i * 150, eng.trigger_flash)
        self._root.after(200, self._spawn_supernova)
        eng._screensaver = True
        eng._gen_screensaver_particles()
        self._root.after(
            8000,
            lambda: (
                setattr(eng, "_screensaver", False)
                if eng and eng.winfo_exists()
                else None
            ),
        )
        self._toast("🌌 K O N A M I - SUPERNOVA CÓSMICA!", "info", 5000)

    def _egg_codis(self, eng: Any) -> None:
        """Executa a operação 'egg codis'.

        Args:
            eng: Parâmetro da operação.
        """
        if not eng:
            return
        for i in range(3):
            self._root.after(i * 200, eng.trigger_flash)
        self._root.after(100, self._spawn_meteor)
        self._toast("⚛ C O D I S - Motor Quântico Ativo!", "info", 4000)

    def _egg_space(self, eng: Any) -> None:
        """Executa a operação 'egg space'.

        Args:
            eng: Parâmetro da operação.
        """
        self._root.after(0, self._spawn_star_birth)
        self._toast("✨ S P A C E - Nascimento Estelar!", "info", 3500)

    def _egg_nova(self, eng: Any) -> None:
        """Executa a operação 'egg nova'.

        Args:
            eng: Parâmetro da operação.
        """
        if eng:
            eng.trigger_flash()
            self._root.after(100, eng.trigger_flash)
            self._root.after(250, eng.trigger_flash)
        self._root.after(0, self._spawn_nova_burst)
        self._toast("💥 N O V A - Explosão Estelar!", "info", 3500)

    def _egg_alien(self, eng: Any) -> None:
        """Executa a operação 'egg alien'.

        Args:
            eng: Parâmetro da operação.
        """
        if not eng:
            return
        W2 = eng.winfo_width() or 1200
        H2 = eng.winfo_height() or 800
        for _ in range(4):
            sx = random.choice([-30.0, float(W2 + 30)])
            sy = float(random.uniform(H2 * 0.1, H2 * 0.9))
            tx = W2 * random.uniform(0.3, 0.7)
            ty = H2 * random.uniform(0.3, 0.7)
            dxx = tx - sx
            dyy = ty - sy
            dist = math.sqrt(dxx * dxx + dyy * dyy) or 1
            spd = random.uniform(0.8, 1.6)
            eng._aliens.append(
                {
                    "x": float(sx),
                    "y": float(sy),
                    "vx": dxx / dist * spd,
                    "vy": dyy / dist * spd,
                    "ph": random.uniform(0, _TAU),
                    "size": random.uniform(10, 18),
                    "col": random.choice(["quantum", "aur_c", "accent2"]),
                    "trail": [],
                    "ship_type": random.choice(["bio", "crystal", "ancient"]),
                    "scan_ph": random.uniform(0, _TAU),
                    "scanning": True,
                    "scan_timer": random.randint(80, 200),
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
        eng._alien_cd = 300
        self._toast("👽 A L I E N - Contato Extraterrestre!", "info", 5000)

    def _egg_nier(self, eng: Any) -> None:
        """Executa a operação 'egg nier'.

        Args:
            eng: Parâmetro da operação.
        """
        if not eng:
            return
        W2 = eng.winfo_width() or 1200
        H2 = eng.winfo_height() or 800
        for atype in ("monolith", "station", "probe", "transmitter", "obelisk"):
            eng._artifacts.append(
                {
                    "x": float(random.uniform(W2 * 0.1, W2 * 0.9)),
                    "y": float(random.uniform(H2 * 0.1, H2 * 0.9)),
                    "vx": random.uniform(-0.08, 0.08),
                    "vy": random.uniform(-0.04, 0.04),
                    "ph": random.uniform(0, _TAU),
                    "rot": random.uniform(0, _TAU),
                    "rot_spd": random.uniform(-0.003, 0.003),
                    "atype": atype,
                    "size": random.uniform(14, 30),
                    "col": "fg",
                    "damage": random.uniform(0.5, 0.95),
                    "signal_ph": random.uniform(0, _TAU),
                    "signaling": atype in ("transmitter", "station"),
                    "life": 1.0,
                    "decay": 0.00005,
                }
            )
        eng._signals.append(
            {
                "x": float(W2 // 2),
                "y": float(H2 // 2),
                "r": 0.0,
                "max_r": float(max(W2, H2) * 0.75),
                "speed": 2.0,
                "alpha": 0.5,
                "col": "fg_dim",
                "n_sides": 8,
                "ph": random.uniform(0, _TAU),
            }
        )
        eng._trigger_glitch(25)
        self._toast("🏚 N I E R - Legado da Humanidade", "info", 6000)

    def _egg_glitch(self, eng: Any) -> None:
        """Executa a operação 'egg glitch'.

        Args:
            eng: Parâmetro da operação.
        """
        if eng:
            eng.trigger_cosmic_glitch(120)
        self._toast("⚡ G L I T C H - Anomalia Forçada!", "info", 3000)

    def _egg_reset(self, eng: Any) -> None:
        """Executa a operação 'egg reset'.

        Args:
            eng: Parâmetro da operação.
        """
        if eng:
            eng._trigger_big_bang()
        self._toast("💥 R E S E T - Big Bang Manual!", "info", 4000)

    def _egg_freeze(self, eng: Any) -> None:
        """Executa a operação 'egg freeze'.

        Args:
            eng: Parâmetro da operação.
        """
        if not eng:
            return
        eng._frozen = not eng._frozen
        if eng._frozen:
            self._toast("❄ F R E E Z E - Animação Pausada", "warn", 0)
        else:
            self._toast("▶ F R E E Z E - Animação Retomada", "info", 2000)

    def _egg_horror(self, eng: Any) -> None:
        """Executa a operação 'egg horror'.

        Args:
            eng: Parâmetro da operação.
        """
        if not eng:
            return
        eng._horror_msg_cd = 1
        eng._horror_face_cd = 1
        eng.trigger_cosmic_glitch(80)
        self._toast("👁 H O R R O R - Vazio Absoluto", "err", 5000)
