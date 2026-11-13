"""KEngine + CosmosWorld com Tk real: o cosmos precisa estar vivo e estável."""

from __future__ import annotations

import time
import tkinter as tk

import pytest

from src.ui.k_ui._engine import KEngine


def _pump(root: tk.Misc, ms: int) -> None:
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        root.update()
        time.sleep(0.003)


@pytest.fixture()
def cosmos(tk_root):
    top = tk.Toplevel(tk_root)
    top.geometry("800x500+0+0")
    eng = KEngine(top, dark=True, profile="balanced")
    eng.place(x=0, y=0, relwidth=1.0, relheight=1.0)
    eng._app_unfocused = lambda: False
    _pump(top, 250)
    yield top, eng
    eng.stop()
    top.destroy()


def _wait(top, cond, timeout=4.0):
    """Espera uma condição (frames dependem da carga da máquina)."""
    end = time.monotonic() + timeout
    while time.monotonic() < end and not cond():
        _pump(top, 30)
    return cond()


def _comet(x, y, vx, vy):
    return {"x": x, "y": y, "vx": vx, "vy": vy, "tail_len": 120, "col": "aur_a",
            "core_r": 3.0, "ph": 0.0}


def test_world_runs_without_errors(cosmos):
    top, eng = cosmos
    _pump(top, 600)
    assert eng._world_errors == 0
    assert eng._world.t > 5


def test_comet_feels_sun_and_tail_points_away(cosmos):
    top, eng = cosmos
    sx, sy = eng._W * eng._solar_cx, eng._H * eng._solar_cy
    c = _comet(sx + 220, sy - 60, -0.2, 0.0)
    eng._comets.append(c)
    assert _wait(top, lambda: "tdx" in c and c["vx"] < -0.25)
    assert "tdx" in c, "a cauda é orientada pelo mundo"
    assert (c["x"] - sx) * c["tdx"] + (c["y"] - sy) * c["tdy"] > 0
    assert c["vx"] < -0.2, "o Sol acelera o cometa"


def test_impact_chain_on_sun(cosmos):
    top, eng = cosmos
    sx, sy = eng._W * eng._solar_cx, eng._H * eng._solar_cy
    eng._comets.append(_comet(sx + 4, sy, 0.0, 0.0))
    assert _wait(top, lambda: eng._world.stats.get("impact", 0) >= 1)
    assert not any(abs(c["x"] - sx) < 6 for c in eng._comets)


def test_keystroke_becomes_solar_wind(cosmos):
    top, eng = cosmos
    before = len(eng._kp_burst)
    eng.notify_keystroke()
    assert len(eng._kp_burst) > before
    sx, sy = eng._W * eng._solar_cx, eng._H * eng._solar_cy
    newest = eng._kp_burst[-1]
    assert abs(newest["x"] - sx) < 20 and abs(newest["y"] - sy) < 20, "faíscas saem do Sol"


def test_mouse_and_click_reach_the_world(cosmos):
    top, eng = cosmos
    eng.event_generate("<Motion>", x=100, y=100)
    assert _wait(top, lambda: eng._world.cursor["on"])
    eng.event_generate("<Button-1>", x=200, y=200)
    assert _wait(top, lambda: eng._world.stats.get("ripple", 0) >= 1)


def test_editor_events_move_the_cosmos(cosmos):
    top, eng = cosmos
    eng.cosmos_event("save")
    assert eng._world.stats.get("nova", 0) == 1


def test_dt_stays_bounded(cosmos):
    top, eng = cosmos
    _pump(top, 300)
    assert 0.25 <= eng._dt <= 3.0


def test_world_failure_disables_layer_not_cosmos(cosmos):
    top, eng = cosmos

    def boom(*_a, **_k):
        raise RuntimeError("falha simulada")

    eng._world.step = boom
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and eng._world_errors < 3:
        _pump(top, 50)
    assert eng._world_errors == 3
    renders = [0]
    orig = eng._render

    def counted():
        renders[0] += 1
        orig()

    eng._render = counted
    _pump(top, 300)
    assert renders[0] > 0, "o cosmos continua animando mesmo com o mundo desligado"


def test_big_bang_does_not_loop(cosmos):
    """Antes: ao terminar, o gatilho (_singularity_start antigo + BHs) reentrava na hora."""
    top, eng = cosmos
    W, H = eng._W or 800, eng._H or 500
    calls = [0]
    orig = eng._trigger_big_bang

    def counted():
        calls[0] += 1
        orig()

    eng._trigger_big_bang = counted
    bh = {"x": 100.0, "y": 100.0, "r": 20.0, "alpha": 1.0, "growing": False, "ph": 0.0,
          "mass": 1.0, "age": 0, "freq": 1, "death_mode": None, "death_target": None,
          "death_age": 0}
    eng._black_holes.append(dict(bh))
    eng._black_holes.append(dict(bh, x=130.0))
    eng._trigger_big_bang()
    for _ in range(5000):
        if not eng._big_bang_active:
            break
        eng._draw_big_bang(W, H)
    assert not eng._big_bang_active
    assert eng._black_holes == [], "o Big Bang consome a singularidade"
    assert not eng._singularity_mode
    eng._black_holes.append(dict(bh))
    eng._anomaly_proximity = 0.95
    eng._singularity_mode = True
    eng._singularity_start = time.monotonic() - 120
    eng._tick()
    _pump(top, 200)
    assert calls[0] == 1
