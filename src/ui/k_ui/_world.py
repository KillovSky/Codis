"""
K-UI - CosmosWorld: o "sistema nervoso" do cosmos.

Cada fenômeno do cosmos (meteoros, cometas, buracos negros, supernovas,
pulsares, aliens...) nasce por timer e se move sozinho no seu próprio loop de
desenho. Este módulo é a camada que faltava: ele roda **antes** do desenho de
cada frame, lê o estado de todas as entidades e faz com que elas se
influenciem de forma causal e coerente:

* **Gravidade unificada** - Sol, buracos negros, binárias e pulsares curvam
  cometas, meteoros, detritos e satélites (cometas ganham órbitas e
  estilingues gravitacionais).
* **Colisões com consequência** - impacto no Sol/planeta gera faíscas,
  fragmentos, anel de choque e erupção solar; buracos negros acretam massa
  (e crescem) com jatos e espirais de acreção.
* **Ondas de choque** - supernovas, ondas gravitacionais, impactos e o salvar
  de um arquivo propagam frentes que empurram partículas, detritos, cometas e
  nebulosas, assustam aliens e fazem as estrelas cintilarem.
* **Ciclo de vida estelar** - supernova -> remanescente (pulsar ou nebulosa)
  -> compressão da nuvem -> nascimento de estrela.
* **Farol de pulsar** - o feixe ilumina o que atravessa (planetas, aliens,
  cometas), e aliens "recebem o sinal".
* **Cometas realistas** - a cauda aponta para longe do Sol e cresce com a
  proximidade; perto demais, o cometa sublima e se desintegra.
* **Personalidade** - aliens ficam curiosos com o cursor quando você está
  calmo e fogem de supernovas; o Sol reage à digitação (vento solar) e aos
  avisos/erros do editor (erupções e tempestades).

O kernel não conhece Tk: opera sobre as listas de dicts do engine, o que o
torna testável sem display. O desenho dos efeitos próprios fica em
:meth:`CosmosWorld.draw`.
"""

from __future__ import annotations

import math
import random
from collections import deque
from collections.abc import Callable
from typing import Any

__all__ = ["CosmosWorld", "Fx", "Front"]

_TAU = math.tau

SUN_GM = 675.0
_SOFT2 = 625.0
_A_CAP = 0.5


class Fx:
    """Efeito visual leve pertencente ao mundo (faísca, anel, jato...)."""

    __slots__ = ("kind", "x", "y", "vx", "vy", "r", "life", "decay", "col", "a", "b", "c")

    def __init__(
        self, kind: str, x: float, y: float, *, vx: float = 0.0, vy: float = 0.0,
        r: float = 1.0, life: float = 1.0, decay: float = 0.03, col: str = "accent2",
        a: float = 0.0, b: float = 0.0, c: float = 0.0,
    ) -> None:
        self.kind = kind
        self.x, self.y, self.vx, self.vy = x, y, vx, vy
        self.r, self.life, self.decay, self.col = r, life, decay, col
        self.a, self.b, self.c = a, b, c


class Front:
    """Frente de onda expansiva (impacto, nova, tempestade solar, clique)."""

    __slots__ = ("x", "y", "r", "vr", "width", "strength", "max_r", "kind", "col")

    def __init__(
        self, x: float, y: float, strength: float, max_r: float, *,
        vr: float = 3.2, width: float = 26.0, kind: str = "wave", col: str = "accent2",
    ) -> None:
        self.x, self.y = x, y
        self.r = 4.0
        self.vr, self.width, self.strength = vr, width, strength
        self.max_r, self.kind, self.col = max_r, kind, col


class _Well:
    """Poço gravitacional / corpo de colisão."""

    __slots__ = ("x", "y", "gm", "kill", "kind", "ref")

    def __init__(self, x: float, y: float, gm: float, kill: float, kind: str, ref: Any) -> None:
        self.x, self.y, self.gm, self.kill, self.kind, self.ref = x, y, gm, kill, kind, ref


class CosmosWorld:
    """Simulação de interações do cosmos (ver docstring do módulo)."""

    MAX_FX = 240
    MAX_FRONTS = 12

    def __init__(self, seed: int | None = None) -> None:
        self.rng = random.Random(seed)
        self.t = 0.0
        self.W = 1200
        self.H = 800
        self.fx: list[Fx] = []
        self.fronts: list[Front] = []
        self.excite: list[tuple[float, float, float, float, float]] = []
        self.mood = 0.0
        self.stress = 0.0
        self.log: deque[tuple[str, dict[str, Any]]] = deque(maxlen=96)
        self.stats: dict[str, int] = {}
        self._listeners: dict[str, list[Callable[..., None]]] = {}

        self.cursor = {"x": 0.0, "y": 0.0, "vx": 0.0, "vy": 0.0, "on": False,
                       "moved": -1e9, "still": 0.0}
        self._sn_seen: dict[int, dict] = {}
        self._sb_seen: dict[int, dict] = {}
        self._pending_births: list[list[Any]] = []
        self._lit: dict[tuple[int, int], float] = {}
        self._flare_cd = 120.0
        self._open_comet_t = -1e9
        self._key_t = -1e9
        self._curious: set[int] = set()
        self._want_comet = False

    def on(self, name: str, fn: Callable[..., None]) -> None:
        """Registra um listener para um evento do cosmos (ex.: ``"impact"``)."""
        self._listeners.setdefault(name, []).append(fn)

    def emit(self, name: str, **data: Any) -> None:
        """Registra um evento no log, conta nas estatísticas e notifica listeners."""
        self.stats[name] = self.stats.get(name, 0) + 1
        self.log.append((name, data))
        for fn in self._listeners.get(name, ()):
            fn(**data)

    def reset(self) -> None:
        """Esquece todo o estado transitório (ex.: após um Big Bang)."""
        self.fx.clear()
        self.fronts.clear()
        self.excite = []
        self._sn_seen.clear()
        self._sb_seen.clear()
        self._pending_births.clear()
        self._lit.clear()
        self._curious.clear()
        self._want_comet = False
        self.emit("reset")

    def cursor_move(self, x: float, y: float) -> None:
        """Informa a posição do mouse sobre o cosmos."""
        c = self.cursor
        if c["on"]:
            c["vx"] = c["vx"] * 0.6 + (x - c["x"]) * 0.4
            c["vy"] = c["vy"] * 0.6 + (y - c["y"]) * 0.4
        c["x"], c["y"], c["on"] = float(x), float(y), True
        c["moved"] = self.t
        c["still"] = 0.0

    def cursor_leave(self) -> None:
        """O mouse saiu do cosmos."""
        self.cursor["on"] = False

    def cursor_click(self, x: float, y: float) -> None:
        """Clique no espaço vazio: ondulação que empurra poeira e partículas."""
        self._add_front(Front(x, y, 0.9, 260, vr=4.5, width=22, kind="ripple", col="accent2"))
        self._sparks(x, y, 7, 2.4, ("accent2", "aur_a", "star3"))
        self.emit("ripple", x=x, y=y)

    def on_event(self, name: str, **kw: Any) -> None:
        """Eventos do editor que viram acontecimentos no cosmos.

        ``save`` -> nova solar; ``open`` -> um cometa chega; ``close`` ->
        pequena onda; ``keystroke`` -> vento solar.
        """
        sun = self._sun_pos(kw.get("eng"))
        if name == "save":
            sx, sy = sun
            self._add_front(Front(sx, sy, 0.45, 680, vr=6.0, width=34, kind="nova", col="accent"))
            self._ring(sx, sy, 10, 2.8, "accent", life=1.0, decay=0.018, w=2)
            self._sparks(sx, sy, 14, 3.2, ("accent", "accent2", "star3"))
            self.mood *= 0.7
            self.emit("nova")
        elif name == "open":
            if self.t - self._open_comet_t > 160:
                self._open_comet_t = self.t
                self.emit("comet_arrival")
                self._want_comet = True
        elif name == "close":
            sx, sy = sun
            self._ring(sx, sy, 8, 1.8, "quantum", life=0.8, decay=0.03)
            self.emit("dim")
        elif name == "keystroke":
            if self.t - self._key_t > 2.5:
                self._key_t = self.t
                sx, sy = sun
                for _ in range(self.rng.randint(1, 2)):
                    a = self.rng.uniform(0, _TAU)
                    sp = self.rng.uniform(1.6, 2.8)
                    self._add(Fx("wind", sx + math.cos(a) * 10, sy + math.sin(a) * 10,
                                 vx=math.cos(a) * sp, vy=math.sin(a) * sp, r=1.6,
                                 life=1.0, decay=0.014, col="accent2"))

    def step(self, eng: Any, dt: float, W: int, H: int) -> None:
        """Avança as interações um frame (``dt`` em frames de referência)."""
        dt = max(0.1, min(float(dt), 3.0))
        self.t += dt
        self.W, self.H = W, H
        self.excite = []
        self._update_mood(eng, dt)
        sun = self._sun(eng, W, H)
        wells = self._wells(eng, sun)
        fronts = self._collect_fronts(eng)
        self._gravity(eng, wells, dt)
        self._solar_wind(eng, sun, dt)
        self._push_by_fronts(eng, fronts, dt)
        self._cursor_forces(eng, dt)
        self._collisions(eng, sun, wells)
        self._comet_physiology(eng, sun, dt)
        self._lifecycle(eng, fronts, dt)
        self._lighthouse(eng, dt)
        self._alien_psychology(eng, fronts, dt)
        self._flares(eng, sun, dt)
        self._spawn_requested_comet(eng, sun)
        self._advance(dt)

    def _update_mood(self, eng: Any, dt: float) -> None:
        kpm = float(getattr(eng, "_metric_kpm", 0.0))
        target = min(1.0, kpm / 260.0)
        self.mood += (target - self.mood) * min(1.0, 0.02 * dt)
        warns = int(getattr(eng, "_metric_warnings", 0))
        errs = int(getattr(eng, "_metric_errors", 0))
        st = min(1.0, warns * 0.12 + errs * 0.25)
        self.stress += (st - self.stress) * min(1.0, 0.02 * dt)

    def _sun_pos(self, eng: Any) -> tuple[float, float]:
        if eng is not None and getattr(eng, "_cfg", {}).get("solar_system"):
            return self.W * eng._solar_cx, self.H * eng._solar_cy
        return self.W * 0.5, self.H * 0.5

    def _sun(self, eng: Any, W: int, H: int) -> _Well | None:
        if not getattr(eng, "_cfg", {}).get("solar_system"):
            return None
        return _Well(W * eng._solar_cx, H * eng._solar_cy, SUN_GM, 11.0, "sun", None)

    def _wells(self, eng: Any, sun: _Well | None) -> list[_Well]:
        wells: list[_Well] = [sun] if sun else []
        for bh in getattr(eng, "_black_holes", ()):
            if bh.get("alpha", 0.0) < 0.25 or bh.get("death_mode"):
                continue
            wells.append(_Well(bh["x"], bh["y"], 55.0 * bh["r"] * bh["mass"],
                               bh["r"] * 1.3, "bh", bh))
        for b in getattr(eng, "_binary_stars", ()):
            wells.append(_Well(b["cx"], b["cy"], 45.0 * (b["r_a"] + b["r_b"]), 0.0, "binary", b))
        for ps in getattr(eng, "_pulsars", ()):
            wells.append(_Well(ps["x"], ps["y"], 70.0, 0.0, "pulsar", ps))
        return wells

    def _accel(self, e: dict, wells: list[_Well], dt: float, k: float, vx: str, vy: str,
               kinds: tuple[str, ...]) -> None:
        ex, ey = e["x"], e["y"]
        for w in wells:
            if w.kind not in kinds:
                continue
            dx, dy = w.x - ex, w.y - ey
            d2 = dx * dx + dy * dy
            if d2 > 640_000:
                continue
            d = math.sqrt(d2) or 1.0
            a = min(_A_CAP, w.gm * k / (d2 + _SOFT2)) * dt
            e[vx] += dx / d * a
            e[vy] += dy / d * a

    def _gravity(self, eng: Any, wells: list[_Well], dt: float) -> None:
        if not wells:
            return
        every = ("sun", "bh", "binary", "pulsar")
        for c in getattr(eng, "_comets", ()):
            self._accel(c, wells, dt, 1.0, "vx", "vy", every)
            sp = math.hypot(c["vx"], c["vy"])
            if sp > 9.0:
                c["vx"] *= 9.0 / sp
                c["vy"] *= 9.0 / sp
        for d in getattr(eng, "_debris", ()):
            self._accel(d, wells, dt, 1.0, "vx", "vy", every)
            sp = math.hypot(d["vx"], d["vy"])
            if sp > 3.0:
                d["vx"] *= 3.0 / sp
                d["vy"] *= 3.0 / sp
        for m in getattr(eng, "_meteors", ()):
            self._accel(m, wells, dt, 0.8, "vx_extra", "vy_extra", ("sun", "binary", "pulsar"))
        for s in getattr(eng, "_satellites", ()):
            self._accel(s, wells, dt, 0.5, "vx", "vy", ("sun", "binary", "pulsar"))
        for n in getattr(eng, "_nebula_clouds", ()):
            self._accel(n, wells, dt, 0.04, "vx", "vy", every)
        for p in getattr(eng, "_particles", ()):
            self._accel(p, wells, dt, 0.5, "vx", "vy", ("bh",))

    def _solar_wind(self, eng: Any, sun: _Well | None, dt: float) -> None:
        if sun is None:
            return
        gust = (0.5 + 1.3 * self.mood + 0.8 * self.stress) * 0.006 * dt
        for p in getattr(eng, "_particles", ()):
            dx, dy = p["x"] - sun.x, p["y"] - sun.y
            d2 = dx * dx + dy * dy
            if d2 > 90_000 or d2 < 1:
                continue
            d = math.sqrt(d2)
            f = gust * (1.0 - d / 300.0)
            p["vx"] += dx / d * f
            p["vy"] += dy / d * f
        for n in getattr(eng, "_nebula_clouds", ()):
            dx, dy = n["x"] - sun.x, n["y"] - sun.y
            d = math.hypot(dx, dy) or 1.0
            if d < 420:
                n["vx"] += dx / d * gust * 0.04
                n["vy"] += dy / d * gust * 0.04

    def _add_front(self, f: Front) -> None:
        if len(self.fronts) >= self.MAX_FRONTS:
            self.fronts.pop(0)
        self.fronts.append(f)

    def _collect_fronts(self, eng: Any) -> list[tuple[float, float, float, float, float, str]]:
        out: list[tuple[float, float, float, float, float, str]] = []
        for sn in getattr(eng, "_supernovas", ()):
            r, mx = sn.get("shockwave_r", 0.0), sn.get("max_shock", 1.0)
            if 0 < r < mx:
                s = 2.4 * (1.0 - r / mx) ** 1.2
                out.append((sn["x"], sn["y"], r, 30.0, s, "supernova"))
                self.excite.append((sn["x"], sn["y"], r, 42.0, min(1.0, s * 0.45)))
        for w in getattr(eng, "_grav_waves", ()):
            a = w.get("alpha", 0.0)
            if a > 0.05:
                out.append((w["x"], w["y"], w["r"], 22.0, 0.35 * a, "gw"))
        for f in self.fronts:
            s = f.strength * (1.0 - f.r / f.max_r)
            if s > 0.02:
                out.append((f.x, f.y, f.r, f.width, s, f.kind))
                self.excite.append((f.x, f.y, f.r, f.width * 1.4, min(1.0, s * 0.6)))
        return out

    def _push(self, ents: Any, fronts: list, k: float, dt: float, vx: str = "vx",
              vy: str = "vy") -> None:
        for e in ents:
            ex, ey = e["x"], e["y"]
            for fx, fy, fr, fw, fs, _kind in fronts:
                dx, dy = ex - fx, ey - fy
                if abs(dx) > fr + fw or abs(dy) > fr + fw:
                    continue
                d = math.hypot(dx, dy) or 1.0
                off = abs(d - fr)
                if off >= fw:
                    continue
                imp = fs * k * (1.0 - off / fw) * dt
                e[vx] += dx / d * imp
                e[vy] += dy / d * imp

    def _push_by_fronts(self, eng: Any, fronts: list, dt: float) -> None:
        if not fronts:
            return
        self._push(getattr(eng, "_particles", ()), fronts, 1.0, dt)
        self._push(getattr(eng, "_debris", ()), fronts, 0.6, dt)
        self._push(getattr(eng, "_comets", ()), fronts, 0.25, dt)
        self._push(getattr(eng, "_meteors", ()), fronts, 0.35, dt, "vx_extra", "vy_extra")
        self._push(getattr(eng, "_nebula_clouds", ()), fronts, 0.5, dt)
        self._push(getattr(eng, "_satellites", ()), fronts, 0.15, dt)
        for n in getattr(eng, "_nebula_clouds", ()):
            for fx, fy, fr, fw, _fs, kind in fronts:
                if kind != "supernova":
                    continue
                d = math.hypot(n["x"] - fx, n["y"] - fy)
                if abs(d - fr) < fw and n.get("_sf", 0.0) <= self.t:
                    n["_sf"] = self.t + 900
                    n["life"] = min(1.0, n["life"] + 0.35)
                    self._pending_births.append([110.0, n["x"], n["y"], n.get("col1", "aur_a")])
                    self.emit("compression", x=n["x"], y=n["y"])

    def _cursor_forces(self, eng: Any, dt: float) -> None:
        c = self.cursor
        if not c["on"] or self.t - c["moved"] > 360:
            return
        c["still"] = self.t - c["moved"]
        cx, cy = c["x"], c["y"]
        self.excite.append((cx, cy, 0.0, 70.0, 0.55))
        hover = c["still"] > 50
        for p in getattr(eng, "_particles", ()):
            dx, dy = p["x"] - cx, p["y"] - cy
            d2 = dx * dx + dy * dy
            if d2 > 22_500 or d2 < 1:
                continue
            d = math.sqrt(d2)
            f = 1.0 - d / 150.0
            p["vx"] += c["vx"] * 0.02 * f * dt
            p["vy"] += c["vy"] * 0.02 * f * dt
            if hover:
                a = min(0.04, 140.0 / (d2 + 900.0)) * dt
                p["vx"] -= dx / d * a
                p["vy"] -= dy / d * a
                p["vx"] *= 0.985
                p["vy"] *= 0.985
        c["vx"] *= 0.9
        c["vy"] *= 0.9

    def _add(self, fx: Fx) -> None:
        if len(self.fx) >= self.MAX_FX and fx.kind not in ("ring", "flare"):
            return
        self.fx.append(fx)

    def _sparks(self, x: float, y: float, n: int, speed: float, cols: tuple[str, ...]) -> None:
        for _ in range(n):
            a = self.rng.uniform(0, _TAU)
            sp = self.rng.uniform(0.35, 1.0) * speed
            self._add(Fx("spark", x, y, vx=math.cos(a) * sp, vy=math.sin(a) * sp,
                         r=self.rng.uniform(0.8, 1.8), life=self.rng.uniform(0.6, 1.0),
                         decay=self.rng.uniform(0.02, 0.045), col=self.rng.choice(cols)))

    def _ring(self, x: float, y: float, r0: float, grow: float, col: str, *,
              life: float = 1.0, decay: float = 0.035, w: int = 1) -> None:
        self._add(Fx("ring", x, y, r=r0, life=life, decay=decay, col=col, a=grow, b=float(w)))

    def _streaks(self, x: float, y: float, n: int, rad: float, col: str) -> None:
        for _ in range(n):
            self._add(Fx("streak", x, y, r=rad * self.rng.uniform(0.7, 1.2),
                         life=1.0, decay=0.04, col=col, a=self.rng.uniform(0, _TAU),
                         b=self.rng.choice((-1.0, 1.0)) * self.rng.uniform(0.18, 0.32)))

    def _fragments(self, eng: Any, x: float, y: float, n: int, speed: float) -> None:
        deb = getattr(eng, "_debris", None)
        if deb is None:
            return
        for _ in range(n):
            if len(deb) >= 12:
                break
            a = self.rng.uniform(0, _TAU)
            sp = self.rng.uniform(0.2, 1.0) * speed
            deb.append({
                "x": x, "y": y, "vx": math.cos(a) * sp, "vy": math.sin(a) * sp,
                "rot": self.rng.uniform(0, _TAU), "rot_spd": self.rng.uniform(-0.05, 0.05),
                "size": self.rng.uniform(1.5, 4.0),
                "dtype": self.rng.choice(("bolt", "chip", "strut")),
                "col": self.rng.choice(("fg_dim", "accent2", "star3")),
                "life": self.rng.uniform(0.5, 0.9), "decay": 0.0006,
            })

    def _feed_bh(self, bh: dict, x: float, y: float, gain: float) -> None:
        r0 = bh.setdefault("_r0", bh["r"])
        bh["mass"] = min(4.0, bh["mass"] + gain)
        bh["r"] = min(r0 * 1.25, bh["r"] + gain * 1.2)
        bh["_fed"] = bh.get("_fed", 0) + 1
        self._streaks(bh["x"], bh["y"], 5, bh["r"] * 3.2, "plasma")
        self._ring(bh["x"], bh["y"], bh["r"], 1.1, "warn", life=0.7, decay=0.05)
        self.emit("accrete", x=bh["x"], y=bh["y"], mass=bh["mass"])
        if bh["_fed"] % 6 == 0:
            self._add_front(Front(bh["x"], bh["y"], 0.5, 360, vr=4.0, width=24, kind="gw",
                                  col="quantum"))

    def _impact(self, eng: Any, x: float, y: float, what: str, power: float, col: str,
                fragments: bool = True) -> None:
        self._sparks(x, y, int(8 + 8 * power), 2.6 + power, (col, "warn", "accent2"))
        self._ring(x, y, 3, 1.5 + power, col, life=1.0, decay=0.04, w=2)
        self._add_front(Front(x, y, 0.35 + 0.35 * power, 90 + 80 * power, vr=3.2, width=18,
                              kind="impact", col=col))
        if fragments:
            self._fragments(eng, x, y, 1 + int(2 * power), 1.2)
        self.emit("impact", x=x, y=y, target=what, power=power)

    def _hit_test(self, eng: Any, e: dict, sun: _Well | None, wells: list[_Well],
                  planets: list[tuple[float, float, float, dict]], power: float,
                  gain: float) -> bool:
        """True se ``e`` colidiu com algo (e deve ser removida)."""
        ex, ey = e["x"], e["y"]
        for w in wells:
            if (w.kind == "bh" and w.kill > 0
                    and (ex - w.x) ** 2 + (ey - w.y) ** 2 < w.kill * w.kill):
                self._feed_bh(w.ref, ex, ey, gain)
                return True
        if sun is not None and (ex - sun.x) ** 2 + (ey - sun.y) ** 2 < (sun.kill + 2) ** 2:
            self._impact(eng, ex, ey, "sun", power, "warn", fragments=False)
            self._prominence(sun.x, sun.y, math.atan2(ey - sun.y, ex - sun.x), 1.4)
            return True
        for px, py, pr, pl in planets:
            if (ex - px) ** 2 + (ey - py) ** 2 < pr * pr:
                self._impact(eng, ex, ey, pl.get("name", "planet"), power,
                             pl.get("col", "accent"))
                return True
        return False

    def _collisions(self, eng: Any, sun: _Well | None, wells: list[_Well]) -> None:
        planets = [(pl["_px"], pl["_py"], pl.get("radius", 4) + 2.0, pl)
                   for pl in getattr(eng, "_solar_planets", ())
                   if pl.get("_px", 0) or pl.get("_py", 0)]
        for m in getattr(eng, "_meteors", ()):
            if m.get("alive", True) and self._hit_test(eng, m, sun, wells, planets, 0.6, 0.03):
                m["alive"] = False
        comets = getattr(eng, "_comets", None)
        if comets:
            for c in comets:
                if self._hit_test(eng, c, sun, wells, planets, 1.0, 0.08):
                    c["_dead"] = True
            comets[:] = [c for c in comets if not c.get("_dead")]
        deb = getattr(eng, "_debris", None)
        if deb:
            for d in deb:
                if self._hit_test(eng, d, sun, wells, [], 0.3, 0.015):
                    d["_dead"] = True
            deb[:] = [d for d in deb if not d.get("_dead")]
        sats = getattr(eng, "_satellites", None)
        if sats:
            for s in sats:
                if self._hit_test(eng, s, None, wells, [], 0.5, 0.02):
                    s["_dead"] = True
            sats[:] = [s for s in sats if not s.get("_dead")]
        for p in getattr(eng, "_particles", ()):
            for w in wells:
                if w.kind == "bh" and (p["x"] - w.x) ** 2 + (p["y"] - w.y) ** 2 < w.kill ** 2:
                    p["x"] = self.rng.choice((-3.0, self.W + 3.0))
                    p["y"] = self.rng.uniform(0, self.H)
                    p["vx"], p["vy"] = self.rng.uniform(-0.25, 0.25), self.rng.uniform(-0.5, -0.02)
                    if self.rng.random() < 0.15:
                        self._streaks(w.x, w.y, 1, w.ref["r"] * 2.5, "aur_a")
                    break

    def _comet_physiology(self, eng: Any, sun: _Well | None, dt: float) -> None:
        wind = 0.35 + 0.9 * self.mood + 0.7 * self.stress
        for c in getattr(eng, "_comets", ()):
            c["age"] = c.get("age", 0.0) + dt
            c.setdefault("mass", 1.0)
            vx, vy = c["vx"], c["vy"]
            vn = math.hypot(vx, vy) or 1.0
            tdx, tdy, scale = -vx / vn, -vy / vn, 1.0
            if sun is not None:
                dx, dy = c["x"] - sun.x, c["y"] - sun.y
                d = math.hypot(dx, dy) or 1.0
                w = max(0.0, min(1.0, 1.4 - d / 420.0))
                tdx = (dx / d) * w + tdx * (1.0 - w)
                tdy = (dy / d) * w + tdy * (1.0 - w)
                tn = math.hypot(tdx, tdy) or 1.0
                tdx, tdy = tdx / tn, tdy / tn
                scale = max(0.45, min(2.3, 300.0 / max(d, 70.0))) * (0.7 + 0.5 * wind)
                if d < 230:
                    c["mass"] -= 0.0007 * dt * (1.0 + (230.0 - d) / 60.0)
                    if self.rng.random() < 0.25 * dt and len(self.fx) < self.MAX_FX - 20:
                        self._add(Fx("dust", c["x"], c["y"], vx=tdx * 0.9 + self.rng.uniform(-.2, .2),
                                     vy=tdy * 0.9 + self.rng.uniform(-.2, .2), r=1.2,
                                     life=0.9, decay=0.03, col=c.get("col", "aur_a")))
            if c["age"] > 6000:
                c["mass"] -= 0.0004 * dt
            c["tdx"], c["tdy"] = tdx, tdy
            c["tlen"] = c.get("tail_len", 120) * scale * max(0.25, c["mass"])
            c["flare"] = 1.0 + max(0.0, 1.0 - c["mass"]) * 0.8 + 0.25 * max(0.0, scale - 1.0)
            if c["mass"] <= 0:
                c["_dead"] = True
                self._sparks(c["x"], c["y"], 10, 2.0, (c.get("col", "aur_a"), "star3"))
                self._ring(c["x"], c["y"], 4, 1.6, c.get("col", "aur_a"), life=0.9, decay=0.035)
                self._fragments(eng, c["x"], c["y"], 2, 0.8)
                self.emit("comet_sublimated", x=c["x"], y=c["y"])
        comets = getattr(eng, "_comets", None)
        if comets:
            comets[:] = [c for c in comets if not c.get("_dead")]

    def _spawn_requested_comet(self, eng: Any, sun: _Well | None) -> None:
        if not self._want_comet:
            return
        self._want_comet = False
        comets = getattr(eng, "_comets", None)
        if comets is None or len(comets) >= 3:
            return
        W, H = self.W, self.H
        side = self.rng.randint(0, 3)
        x = (-40.0, W + 40.0, self.rng.uniform(0, W), self.rng.uniform(0, W))[side]
        y = (self.rng.uniform(0, H), self.rng.uniform(0, H), -40.0, H + 40.0)[side]
        tx, ty = (sun.x, sun.y) if sun else (W / 2, H / 2)
        tx += self.rng.uniform(-90, 90)
        ty += self.rng.uniform(-90, 90)
        d = math.hypot(tx - x, ty - y) or 1.0
        sp = self.rng.uniform(1.8, 3.2)
        comets.append({
            "x": x, "y": y, "vx": (tx - x) / d * sp, "vy": (ty - y) / d * sp,
            "tail_len": self.rng.randint(90, 190),
            "col": self.rng.choice(("aur_a", "accent2", "star3", "quantum")),
            "core_r": self.rng.uniform(2.8, 4.8), "ph": self.rng.uniform(0, _TAU),
        })

    def _new_pulsar(self, x: float, y: float) -> dict:
        return {
            "x": x, "y": y, "ph": self.rng.uniform(0, _TAU),
            "period": self.rng.uniform(18, 40), "timer": 0.0,
            "jet_ang": self.rng.uniform(0, _TAU), "jet_spd": self.rng.uniform(0.05, 0.12),
            "radius": self.rng.uniform(2.8, 4.5),
            "col": self.rng.choice(("quantum", "aur_a", "accent2", "star3")),
            "life": 1.0, "decay": self.rng.uniform(0.0003, 0.0006), "pulse_alpha": 0.0,
        }

    def _new_nebula(self, x: float, y: float, col: str) -> dict:
        return {
            "x": x, "y": y, "rx": self.rng.uniform(70, 120), "ry": self.rng.uniform(45, 80),
            "ph": self.rng.uniform(0, _TAU), "speed": self.rng.uniform(0.003, 0.008),
            "vx": 0.0, "vy": 0.0, "col1": col, "col2": "accent2",
            "life": 1.0, "decay": 0.0006,
        }

    def _new_birth(self, x: float, y: float, col: str) -> dict:
        return {
            "x": x, "y": y, "r": float(self.rng.uniform(9, 18)), "phase": 0.0,
            "max_phase": self.rng.randint(200, 320), "col": col,
            "cloud_r": float(self.rng.uniform(55, 95)), "_w": True,
        }

    def _lifecycle(self, eng: Any, fronts: list, dt: float) -> None:
        sne = {id(s): s for s in getattr(eng, "_supernovas", ())}
        for sid, s in sne.items():
            if sid not in self._sn_seen:
                self._sn_seen[sid] = s
                self.emit("supernova", x=s["x"], y=s["y"])
        for sid in [k for k in self._sn_seen if k not in sne]:
            self._remnant(eng, self._sn_seen.pop(sid))

        births = getattr(eng, "_star_birth", None)
        if births is not None:
            neb = [n for n in getattr(eng, "_nebula_clouds", ()) if n["life"] > 0.25]
            for sb in births:
                if sb.get("_w"):
                    continue
                sb["_w"] = True
                if neb:
                    n = self.rng.choice(neb)
                    sb["x"] = n["x"] + self.rng.uniform(-0.3, 0.3) * n["rx"]
                    sb["y"] = n["y"] + self.rng.uniform(-0.3, 0.3) * n["ry"]
                    sb["cloud_r"] = max(sb.get("cloud_r", 50.0), n["rx"] * 0.6)
            cur = {id(b): b for b in births}
            for bid, b in cur.items():
                self._sb_seen.setdefault(bid, b)
            for bid in [k for k in self._sb_seen if k not in cur]:
                b = self._sb_seen.pop(bid)
                self._ring(b["x"], b["y"], 3, 2.2, b.get("col", "star"), life=1.0, decay=0.03)
                self._sparks(b["x"], b["y"], 9, 1.8, (b.get("col", "star"), "star3"))
                self.excite.append((b["x"], b["y"], 0.0, 90.0, 0.8))
                self.emit("star_born", x=b["x"], y=b["y"])
            for pend in self._pending_births[:]:
                pend[0] -= dt
                if pend[0] <= 0:
                    self._pending_births.remove(pend)
                    if len(births) < 4:
                        births.append(self._new_birth(pend[1], pend[2], "star3"))

    def _remnant(self, eng: Any, sn: dict) -> None:
        x, y = sn["x"], sn["y"]
        self._ring(x, y, 6, 2.4, sn.get("ejecta_col", "accent2"), life=1.0, decay=0.02, w=2)
        pulsars = getattr(eng, "_pulsars", None)
        neb = getattr(eng, "_nebula_clouds", None)
        if self.rng.random() < 0.55 and pulsars is not None and len(pulsars) < 4:
            pulsars.append(self._new_pulsar(x, y))
            self.emit("remnant", kind="pulsar", x=x, y=y)
        elif neb is not None and len(neb) < 6:
            neb.append(self._new_nebula(x, y, sn.get("ejecta_col", "aur_a")))
            self._pending_births.append([self.rng.uniform(220, 360), x, y, "star3"])
            self.emit("remnant", kind="nebula", x=x, y=y)

    def _lighthouse(self, eng: Any, dt: float) -> None:
        pulsars = getattr(eng, "_pulsars", ())
        if not pulsars:
            return
        targets: list[tuple[str, dict, float, float]] = []
        for pl in getattr(eng, "_solar_planets", ()):
            if pl.get("_px", 0) or pl.get("_py", 0):
                targets.append(("planet", pl, pl["_px"], pl["_py"]))
        for lst, tag in (("_aliens", "alien"), ("_comets", "comet"), ("_satellites", "sat")):
            for e in getattr(eng, lst, ()):
                targets.append((tag, e, e["x"], e["y"]))
        for key in [k for k, v in self._lit.items() if v < self.t]:
            del self._lit[key]
        for ps in pulsars:
            for tag, e, tx, ty in targets:
                dx, dy = tx - ps["x"], ty - ps["y"]
                d = math.hypot(dx, dy)
                if d < 30 or d > 420:
                    continue
                ang = math.atan2(dy, dx)
                for beam in (ps["jet_ang"], ps["jet_ang"] + math.pi):
                    diff = abs((ang - beam + math.pi) % _TAU - math.pi)
                    if diff < 0.07:
                        key = (id(ps), id(e))
                        if key in self._lit:
                            break
                        self._lit[key] = self.t + 70
                        self._add(Fx("beam", ps["x"], ps["y"], r=0.0, life=1.0, decay=0.12,
                                     col=ps.get("col", "quantum"), a=tx, b=ty))
                        self._ring(tx, ty, 3, 1.3, ps.get("col", "quantum"), life=0.9, decay=0.05)
                        if tag == "alien":
                            e["scanning"] = True
                            e["scan_timer"] = max(e.get("scan_timer", 0), 180)
                        self.emit("lit", target=tag)
                        break

    def _alien_psychology(self, eng: Any, fronts: list, dt: float) -> None:
        aliens = getattr(eng, "_aliens", ())
        if not aliens:
            return
        c = self.cursor
        curious = c["on"] and self.t - c["moved"] < 300 and self.mood < 0.5
        for a in aliens:
            for fx, fy, fr, fw, fs, kind in fronts:
                if kind not in ("supernova", "cme", "impact") or fs < 0.5:
                    continue
                dx, dy = a["x"] - fx, a["y"] - fy
                d = math.hypot(dx, dy) or 1.0
                if abs(d - fr) < fw * 2.5 and d < 520:
                    a["state"] = "flee"
                    a["flee_timer"] = 120
                    a["scanning"] = False
                    a["vx"] = a["vx"] * 0.6 + dx / d * 1.1
                    a["vy"] = a["vy"] * 0.6 + dy / d * 1.1
            if curious and a.get("state", "travel") == "travel" and not a.get("scanning"):
                dx, dy = c["x"] - a["x"], c["y"] - a["y"]
                d = math.hypot(dx, dy) or 1.0
                if 40 < d < 440:
                    k = 0.012 * dt if d > 150 else -0.004 * dt
                    a["vx"] += dx / d * k
                    a["vy"] += dy / d * k
                    if d < 160:
                        a["vx"] *= 0.97
                        a["vy"] *= 0.97
                    if id(a) not in self._curious:
                        self._curious.add(id(a))
                        self.emit("alien_curious")
                    sp = math.hypot(a["vx"], a["vy"])
                    if sp > 1.4:
                        a["vx"] *= 1.4 / sp
                        a["vy"] *= 1.4 / sp

    def _prominence(self, sx: float, sy: float, ang: float, size: float) -> None:
        col = "warn" if self.stress > 0.25 else "accent2"
        self._add(Fx("flare", sx, sy, r=11.0, life=1.0, decay=0.018, col=col,
                     a=ang, b=size * self.rng.uniform(16, 30), c=self.rng.uniform(0.5, 0.9)))

    def _flares(self, eng: Any, sun: _Well | None, dt: float) -> None:
        if sun is None:
            return
        self._flare_cd -= dt
        if self._flare_cd > 0:
            return
        self._flare_cd = max(25.0, 230.0 - 170.0 * self.mood - 130.0 * self.stress) * \
            self.rng.uniform(0.6, 1.4)
        ang = self.rng.uniform(0, _TAU)
        self._prominence(sun.x, sun.y, ang, 1.0 + self.mood + self.stress)
        for _ in range(3):
            sp = self.rng.uniform(1.2, 2.4)
            a2 = ang + self.rng.uniform(-0.25, 0.25)
            self._add(Fx("wind", sun.x + math.cos(ang) * 11, sun.y + math.sin(ang) * 11,
                         vx=math.cos(a2) * sp, vy=math.sin(a2) * sp, r=1.5, life=1.0,
                         decay=0.012, col="warn" if self.stress > 0.25 else "accent2"))
        self.emit("flare", stress=round(self.stress, 2))
        if self.stress > 0.4 and self.rng.random() < 0.18:
            self._add_front(Front(sun.x, sun.y, 0.9, 560, vr=4.2, width=30, kind="cme",
                                  col="warn"))
            self.emit("cme")

    def _advance(self, dt: float) -> None:
        for f in self.fronts:
            f.r += f.vr * dt
        self.fronts = [f for f in self.fronts if f.r < f.max_r]
        drag = 0.96 ** dt
        alive: list[Fx] = []
        for fx in self.fx:
            fx.life -= fx.decay * dt
            if fx.life <= 0:
                continue
            k = fx.kind
            if k in ("spark", "wind", "dust"):
                fx.x += fx.vx * dt
                fx.y += fx.vy * dt
                if k == "spark":
                    fx.vx *= drag
                    fx.vy *= drag
            elif k == "ring":
                fx.r += fx.a * dt
            elif k == "streak":
                fx.a += fx.b * dt
                fx.r *= 0.93 ** dt
                if fx.r < 3:
                    continue
            alive.append(fx)
        self.fx = alive

    def draw(self, eng: Any, W: int, H: int) -> None:
        """Desenha efeitos do mundo (usa o canvas e a paleta do ``eng``)."""
        from ._colors import _lc_q

        p = eng._p
        void = p["void"]
        oval, line = eng.create_oval, eng.create_line
        for f in self.fronts:
            s = f.strength * (1.0 - f.r / f.max_r)
            if s > 0.06:
                r = int(f.r)
                oval(int(f.x) - r, int(f.y) - r, int(f.x) + r, int(f.y) + r, fill="",
                     outline=_lc_q(void, p.get(f.col, p["accent2"]), min(0.5, s * 0.55)), width=1)
        cos, sin = math.cos, math.sin
        for fx in self.fx:
            col = p.get(fx.col, p["accent2"])
            al = max(0.0, min(1.0, fx.life))
            x, y = fx.x, fx.y
            k = fx.kind
            if k == "spark":
                line(int(x), int(y), int(x - fx.vx * 2.2), int(y - fx.vy * 2.2),
                     fill=_lc_q(void, col, al), width=1)
            elif k in ("wind", "dust"):
                r = max(1, int(fx.r * (0.5 + al * 0.5)))
                oval(int(x) - r, int(y) - r, int(x) + r, int(y) + r,
                     fill=_lc_q(void, col, al * (0.7 if k == "wind" else 0.4)), outline="")
            elif k == "ring":
                r = int(fx.r)
                oval(int(x) - r, int(y) - r, int(x) + r, int(y) + r, fill="",
                     outline=_lc_q(void, col, al * 0.8), width=int(fx.b))
            elif k == "streak":
                px = x + cos(fx.a) * fx.r
                py = y + sin(fx.a) * fx.r
                qa = fx.a - fx.b * 2.2
                qr = fx.r / 0.93 ** 2.2
                line(int(px), int(py), int(x + cos(qa) * qr), int(y + sin(qa) * qr),
                     fill=_lc_q(void, col, al), width=2 if al > 0.5 else 1)
            elif k == "beam":
                line(int(x), int(y), int(fx.a), int(fx.b),
                     fill=_lc_q(void, col, al * 0.7), width=2)
            elif k == "flare":
                grow = math.sin(min(1.0, (1.0 - al) * 2.2) * math.pi * 0.5) if al > 0.5 else 1.0
                span = 0.55
                pts: list[int] = []
                for i in range(9):
                    t = i / 8
                    rad = fx.r + fx.b * grow * math.sin(t * math.pi)
                    a = fx.a + (t - 0.5) * span
                    pts += [int(x + cos(a) * rad), int(y + sin(a) * rad)]
                line(*pts, fill=_lc_q(void, col, al * 0.85), width=2, smooth=True)

    def snapshot(self) -> dict[str, Any]:
        """Resumo do estado (para testes e depuração)."""
        return {"t": round(self.t, 1), "mood": round(self.mood, 3),
                "stress": round(self.stress, 3), "fx": len(self.fx),
                "fronts": len(self.fronts), "stats": dict(self.stats)}
