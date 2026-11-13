"""BgRenderer: não pode saturar um núcleo (regressão da lentidão extrema)."""

from __future__ import annotations

import pytest

pytest.importorskip("PIL")

from src.ui.k_ui._bgrenderer import BgRenderer  # noqa: E402
from src.ui.k_ui._perf import _PERF  # noqa: E402


def make(profile="balanced"):
    return BgRenderer(True, dict(_PERF[profile]))


def test_interval_follows_measured_cost_not_configured_fps():
    r = make("quantum")
    r._render_cost, r._convert_cost = 0.100, 0.020
    assert r._interval() >= 4 * 0.120 - 1e-9, "ciclo de trabalho limitado a ~20% de um núcleo"


def test_interval_never_below_configured_rate():
    r = make("power_saver")
    r._render_cost = r._convert_cost = 0.0
    assert r._interval() >= r._bg_interval


def test_interval_is_capped():
    r = make()
    r._render_cost = 10.0
    assert r._interval() == r._MAX_INTERVAL


@pytest.mark.parametrize("w,h,scale", [(800, 600, 1), (1146, 777, 2), (1920, 1080, 2),
                                       (2560, 1440, 3), (3840, 2160, 4)])
def test_scale_keeps_internal_resolution_bounded(w, h, scale):
    r = make()
    r.maybe_update(w, h, 0.1, 0.1)
    assert r._scale == scale
    assert max(w, h) // r._scale <= r._MAX_BG_DIM


def test_output_has_requested_size_and_cost_is_recorded():
    r = make()
    r._W, r._H, r._scale = 1920, 1080, 2
    r._rendering = True
    r._render(1920, 1080, 0.5, 0.3, None)
    img = r._queue.get_nowait()
    assert img.size == (1920, 1080)
    assert r._render_cost > 0
    assert not r._rendering


def test_downscaled_render_stays_close_to_full_resolution():
    from PIL import ImageChops, ImageStat

    out = {}
    for sc in (1, 2):
        r = make()
        r._scale, r._rendering = sc, True
        r._render(1280, 720, 0.9, 0.4, None)
        out[sc] = r._queue.get_nowait().convert("RGB")
    diff = ImageChops.difference(out[1], out[2])
    mean_err = sum(ImageStat.Stat(diff).mean) / 3
    assert mean_err < 2.0
