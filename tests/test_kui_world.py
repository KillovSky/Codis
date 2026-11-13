"""CosmosWorld: as entidades do cosmos precisam se influenciar de forma causal."""

from __future__ import annotations

import math
from types import SimpleNamespace

from src.ui.k_ui._world import SUN_GM, CosmosWorld

W, H = 1200, 800
SX, SY = W * 0.5, H * 0.5


def make_eng(**over):
    eng = SimpleNamespace(
        _cfg={"solar_system": True}, _solar_cx=0.5, _solar_cy=0.5,
        _comets=[], _meteors=[], _debris=[], _satellites=[], _particles=[],
        _nebula_clouds=[], _black_holes=[], _binary_stars=[], _pulsars=[],
        _supernovas=[], _star_birth=[], _grav_waves=[], _solar_planets=[], _aliens=[],
        _metric_kpm=0.0, _metric_warnings=0, _metric_errors=0,
    )
    for k, v in over.items():
        setattr(eng, k, v)
    return eng


def comet(x, y, vx=0.0, vy=0.0, **kw):
    d = {"x": x, "y": y, "vx": vx, "vy": vy, "tail_len": 120, "col": "aur_a",
         "core_r": 3.0, "ph": 0.0}
    d.update(kw)
    return d


def run(world, eng, n, dt=1.0, integrate=True):
    for _ in range(n):
        world.step(eng, dt, W, H)
        if integrate:
            for lst in (eng._comets, eng._debris):
                for e in lst:
                    e["x"] += e["vx"] * dt
                    e["y"] += e["vy"] * dt


def test_sun_gravity_bends_comet_toward_sun():
    w, eng = CosmosWorld(1), make_eng()
    c = comet(SX + 300, SY, vy=-2.0)
    eng._comets.append(c)
    run(w, eng, 40)
    assert c["vx"] < -0.1, "a gravidade do Sol precisa puxar o cometa"


def test_gravity_is_time_based():
    a, b = make_eng(), make_eng()
    ca, cb = comet(SX + 300, SY), comet(SX + 300, SY)
    a._comets.append(ca)
    b._comets.append(cb)
    CosmosWorld(1).step(a, 1.0, W, H)
    CosmosWorld(1).step(b, 2.0, W, H)
    assert math.isclose(cb["vx"] / ca["vx"], 2.0, rel_tol=0.05)


def test_comet_tail_points_away_from_sun():
    w, eng = CosmosWorld(1), make_eng()
    c = comet(SX + 200, SY - 100, vx=-1.5, vy=0.5)
    eng._comets.append(c)
    w.step(eng, 1.0, W, H)
    away = (c["x"] - SX) * c["tdx"] + (c["y"] - SY) * c["tdy"]
    assert away > 0, "a cauda de íons aponta para longe do Sol, não contra a velocidade"
    assert c["tlen"] > 0


def test_tail_grows_near_sun():
    w, eng = CosmosWorld(1), make_eng()
    near, far = comet(SX + 90, SY), comet(SX + 520, SY)
    eng._comets += [near, far]
    w.step(eng, 1.0, W, H)
    assert near["tlen"] > far["tlen"]


def test_comet_sublimates_near_sun():
    w, eng = CosmosWorld(1), make_eng()
    c = comet(SX + 70, SY, vy=1.6)
    eng._comets.append(c)
    for _ in range(3000):
        w.step(eng, 1.0, W, H)
        for e in eng._comets:
            e["x"] += e["vx"]
            e["y"] += e["vy"]
        if not eng._comets:
            break
    assert w.stats.get("comet_sublimated", 0) + w.stats.get("impact", 0) >= 1
    assert not eng._comets


def test_meteor_hits_planet_causes_impact_chain():
    pl = {"name": "app.py", "_px": 500.0, "_py": 300.0, "radius": 5, "col": "accent"}
    w, eng = CosmosWorld(1), make_eng(_solar_planets=[pl])
    eng._meteors.append({"x": 500.0, "y": 300.0, "dx": 5, "dy": 5, "alive": True,
                         "vx_extra": 0.0, "vy_extra": 0.0})
    w.step(eng, 1.0, W, H)
    assert eng._meteors[0]["alive"] is False
    assert w.stats["impact"] == 1
    assert w.fx and w.fronts, "impacto gera faíscas, anel e onda de choque"
    assert eng._debris, "e fragmentos"


def test_sun_impact_triggers_prominence():
    w, eng = CosmosWorld(1), make_eng()
    eng._comets.append(comet(SX + 3, SY))
    w.step(eng, 1.0, W, H)
    assert w.stats["impact"] == 1
    assert any(f.kind == "flare" for f in w.fx), "impacto no Sol provoca erupção"
    assert not eng._comets


def test_black_hole_accretes_and_grows():
    bh = {"x": 300.0, "y": 300.0, "r": 20.0, "alpha": 1.0, "mass": 1.0, "death_mode": None}
    w, eng = CosmosWorld(1), make_eng(_black_holes=[bh])
    eng._comets.append(comet(300.0, 300.0))
    w.step(eng, 1.0, W, H)
    assert bh["mass"] > 1.0 and bh["r"] > 20.0
    assert not eng._comets and w.stats["accrete"] == 1
    assert any(f.kind == "streak" for f in w.fx)


def test_black_hole_burp_wave_after_feeding():
    bh = {"x": 300.0, "y": 300.0, "r": 20.0, "alpha": 1.0, "mass": 1.0, "death_mode": None}
    w, eng = CosmosWorld(1), make_eng(_black_holes=[bh])
    for _ in range(6):
        eng._debris.append({"x": 300.0, "y": 300.0, "vx": 0.0, "vy": 0.0})
        w.step(eng, 1.0, W, H)
    assert any(f.kind == "gw" for f in w.fronts)


def test_black_hole_pulls_dust_and_respawns_it():
    bh = {"x": 300.0, "y": 300.0, "r": 20.0, "alpha": 1.0, "mass": 2.0, "death_mode": None}
    pt = {"x": 340.0, "y": 300.0, "vx": 0.0, "vy": 0.0}
    w, eng = CosmosWorld(1), make_eng(_black_holes=[bh], _particles=[pt])
    w.step(eng, 1.0, W, H)
    assert pt["vx"] < 0, "poeira cai no poço"
    pt.update(x=305.0, y=300.0)
    w.step(eng, 1.0, W, H)
    assert pt["x"] in (-3.0, W + 3.0), "poeira engolida renasce na borda"


def test_supernova_shock_pushes_dust_outward():
    sn = {"x": 600.0, "y": 400.0, "shockwave_r": 200.0, "max_shock": 600.0, "age": 50}
    pt = {"x": 800.0, "y": 400.0, "vx": 0.0, "vy": 0.0}
    far = {"x": 1100.0, "y": 400.0, "vx": 0.0, "vy": 0.0}
    w, eng = CosmosWorld(1), make_eng(_supernovas=[sn], _particles=[pt, far])
    eng._cfg = {}
    w.step(eng, 1.0, W, H)
    assert pt["vx"] > 0.3 and far["vx"] == 0.0


def test_supernova_front_makes_stars_twinkle():
    sn = {"x": 600.0, "y": 400.0, "shockwave_r": 200.0, "max_shock": 600.0}
    w, eng = CosmosWorld(1), make_eng(_supernovas=[sn])
    w.step(eng, 1.0, W, H)
    assert any(abs(e[2] - 200.0) < 1 and e[4] > 0 for e in w.excite)


def test_supernova_end_leaves_remnant_and_new_star():
    sn = {"x": 400.0, "y": 300.0, "shockwave_r": 5.0, "max_shock": 500.0,
          "ejecta_col": "plasma"}
    w, eng = CosmosWorld(3), make_eng(_supernovas=[sn])
    w.step(eng, 1.0, W, H)
    assert w.stats["supernova"] == 1
    eng._supernovas.clear()
    w.step(eng, 1.0, W, H)
    assert w.stats["remnant"] == 1
    assert eng._pulsars or eng._nebula_clouds
    if eng._nebula_clouds:
        run(w, eng, 500, integrate=False)
        assert eng._star_birth


def test_shock_compresses_nebula_and_triggers_star_formation():
    neb = {"x": 700.0, "y": 400.0, "rx": 90, "ry": 50, "life": 0.4, "col1": "aur_a",
           "vx": 0.0, "vy": 0.0}
    sn = {"x": 500.0, "y": 400.0, "shockwave_r": 200.0, "max_shock": 600.0}
    w, eng = CosmosWorld(1), make_eng(_nebula_clouds=[neb], _supernovas=[sn])
    w.step(eng, 1.0, W, H)
    assert neb["life"] > 0.4 and w.stats["compression"] == 1
    run(w, eng, 160, integrate=False)
    assert eng._star_birth, "a compressão forma uma estrela"


def test_natural_star_birth_migrates_into_nebula():
    neb = {"x": 900.0, "y": 600.0, "rx": 100, "ry": 60, "life": 0.9, "col1": "aur_a",
           "vx": 0.0, "vy": 0.0}
    sb = {"x": 100.0, "y": 100.0, "r": 10.0, "phase": 0.0, "max_phase": 200,
          "col": "star", "cloud_r": 40.0}
    w, eng = CosmosWorld(1), make_eng(_nebula_clouds=[neb], _star_birth=[sb])
    w.step(eng, 1.0, W, H)
    assert abs(sb["x"] - 900) < 100 and abs(sb["y"] - 600) < 60


def test_star_born_event_when_birth_completes():
    sb = {"x": 100.0, "y": 100.0, "r": 10.0, "phase": 0.0, "max_phase": 200,
          "col": "star", "cloud_r": 40.0, "_w": True}
    w, eng = CosmosWorld(1), make_eng(_star_birth=[sb])
    w.step(eng, 1.0, W, H)
    eng._star_birth.clear()
    w.step(eng, 1.0, W, H)
    assert w.stats["star_born"] == 1 and w.fx


def test_pulsar_beam_lights_planet():
    pl = {"name": "x.py", "_px": 600.0, "_py": 300.0, "radius": 4, "col": "accent"}
    ps = {"x": 400.0, "y": 300.0, "jet_ang": 0.0, "col": "quantum"}
    w, eng = CosmosWorld(1), make_eng(_solar_planets=[pl], _pulsars=[ps])
    w.step(eng, 1.0, W, H)
    assert w.stats["lit"] == 1
    assert any(f.kind == "beam" for f in w.fx)
    w.step(eng, 1.0, W, H)
    assert w.stats["lit"] == 1, "cooldown evita piscar a cada frame"


def test_pulsar_beam_makes_alien_scan():
    al = {"x": 600.0, "y": 300.0, "vx": 0.5, "vy": 0.0, "state": "travel",
          "scanning": False, "scan_timer": 0, "size": 8}
    ps = {"x": 400.0, "y": 300.0, "jet_ang": math.pi, "col": "quantum"}
    w, eng = CosmosWorld(1), make_eng(_aliens=[al], _pulsars=[ps])
    w.step(eng, 1.0, W, H)
    assert al["scanning"] and al["scan_timer"] >= 180


def test_alien_flees_supernova():
    al = {"x": 800.0, "y": 400.0, "vx": 0.0, "vy": 0.0, "state": "travel", "size": 8}
    sn = {"x": 600.0, "y": 400.0, "shockwave_r": 190.0, "max_shock": 600.0}
    w, eng = CosmosWorld(1), make_eng(_aliens=[al], _supernovas=[sn])
    w.step(eng, 1.0, W, H)
    assert al["state"] == "flee" and al["vx"] > 0.5


def test_alien_curious_about_calm_cursor():
    al = {"x": 600.0, "y": 400.0, "vx": 0.0, "vy": 0.0, "state": "travel",
          "scanning": False, "size": 8}
    w, eng = CosmosWorld(1), make_eng(_aliens=[al])
    w.cursor_move(300, 400)
    w.step(eng, 1.0, W, H)
    assert al["vx"] < 0 and w.stats["alien_curious"] == 1


def test_busy_user_does_not_attract_aliens():
    al = {"x": 600.0, "y": 400.0, "vx": 0.0, "vy": 0.0, "state": "travel",
          "scanning": False, "size": 8}
    w, eng = CosmosWorld(1), make_eng(_aliens=[al], _metric_kpm=400.0)
    w.mood = 1.0
    w.cursor_move(100, 400)
    w.step(eng, 1.0, W, H)
    assert al["vx"] == 0.0


def test_cursor_wake_and_hover_gather_dust():
    pt = {"x": 560.0, "y": 400.0, "vx": 0.0, "vy": 0.0}
    w, eng = CosmosWorld(1), make_eng(_particles=[pt])
    eng._cfg = {}
    w.cursor_move(500, 400)
    w.cursor_move(520, 400)
    w.step(eng, 1.0, W, H)
    assert pt["vx"] > 0, "arrastar o mouse leva a poeira junto"
    w.t += 80
    w.cursor["moved"] = w.t - 60
    w.cursor["vx"] = w.cursor["vy"] = 0.0
    pt.update(vx=0.0, x=580.0)
    w.step(eng, 1.0, W, H)
    assert pt["vx"] < 0, "mouse parado vira um poço (vagalumes)"


def test_click_makes_ripple_that_pushes_dust():
    pt = {"x": 540.0, "y": 400.0, "vx": 0.0, "vy": 0.0}
    w, eng = CosmosWorld(1), make_eng(_particles=[pt])
    eng._cfg = {}
    w.cursor_click(500, 400)
    for _ in range(12):
        w.step(eng, 1.0, W, H)
    assert pt["vx"] > 0


def test_typing_raises_mood_and_flares():
    w, eng = CosmosWorld(1), make_eng(_metric_kpm=300.0)
    for _ in range(400):
        w.step(eng, 1.0, W, H)
    assert w.mood > 0.6 and w.stats["flare"] >= 3


def test_errors_raise_stress_and_storms():
    w, eng = CosmosWorld(1), make_eng(_metric_errors=4, _metric_warnings=3)
    for _ in range(1500):
        w.step(eng, 1.0, W, H)
    assert w.stress > 0.6
    assert w.stats.get("cme", 0) >= 1, "erros provocam tempestade solar"


def test_save_event_sends_nova_and_calms():
    w, eng = CosmosWorld(1), make_eng()
    w.mood = 0.8
    w.on_event("save", eng=eng)
    assert w.stats["nova"] == 1 and w.fronts and w.mood < 0.6


def test_open_event_brings_a_comet_aimed_at_sun():
    w, eng = CosmosWorld(1), make_eng()
    w.on_event("open", eng=eng)
    w.step(eng, 1.0, W, H)
    assert len(eng._comets) == 1
    c = eng._comets[0]
    to_sun = (SX - c["x"]) * c["vx"] + (SY - c["y"]) * c["vy"]
    assert to_sun > 0
    w.on_event("open", eng=eng)
    w.step(eng, 1.0, W, H)
    assert len(eng._comets) == 1


def test_everything_stays_bounded_over_long_run():
    w, eng = CosmosWorld(7), make_eng(_metric_kpm=200.0, _metric_errors=2)
    eng._black_holes.append({"x": 300.0, "y": 300.0, "r": 20.0, "alpha": 1.0, "mass": 1.0,
                             "death_mode": None})
    for i in range(6000):
        if i % 40 == 0 and len(eng._comets) < 3:
            eng._comets.append(comet(w.rng.uniform(0, W), -30, vy=2.0))
        if i % 25 == 0:
            w.cursor_move(w.rng.uniform(0, W), w.rng.uniform(0, H))
        if i % 300 == 0:
            w.on_event("save", eng=eng)
            w.cursor_click(w.rng.uniform(0, W), w.rng.uniform(0, H))
        w.step(eng, 1.0, W, H)
        for e in eng._comets + eng._debris:
            e["x"] += e["vx"]
            e["y"] += e["vy"]
        eng._comets[:] = [c for c in eng._comets if -200 < c["x"] < W + 200 and -200 < c["y"] < H + 200]
    assert len(w.fx) <= w.MAX_FX + 30
    assert len(w.fronts) <= w.MAX_FRONTS
    assert len(eng._debris) <= 12
    assert len(w.log) <= 96


def test_deterministic_with_seed():
    def go():
        w, eng = CosmosWorld(5), make_eng(_metric_kpm=250.0)
        for _ in range(300):
            w.step(eng, 1.0, W, H)
        return w.snapshot()

    assert go() == go()


def test_sun_gm_constant_gives_bound_and_unbound_comets():
    """v_escape a 150 px = sqrt(2GM/r): cometas lentos ligados, rápidos escapam."""
    v_esc = math.sqrt(2 * SUN_GM / 150.0)
    assert 1.8 < v_esc < 4.5
