"""Motor do Paint: corretude (contra implementações de referência) e orçamento de desempenho."""

import random
import time
from collections import deque

import pytest
from PIL import Image, ImageChops

from src.ui import paint_engine as pe


def doc(w=120, h=90, fill=(255, 255, 255, 255)):
    return pe.Document(Image.new("RGBA", (w, h), fill))


def stroke(d, pts, rgb=(255, 0, 0), opacity=255, size=6, aa=True):
    d.begin()
    mask = pe.new_stroke_mask(d)
    prev = pts[0]
    for p in pts:
        box = pe.stamp_segment(mask, prev[0], prev[1], p[0], p[1], size, aa)
        if box:
            pe.apply_stroke_region(d, mask, box, rgb, opacity)
        prev = p
    d.commit()


def test_undo_redo_matches_full_snapshots_for_random_operations():
    rng = random.Random(7)
    d = doc(300, 260)
    snaps = [d.image.copy()]
    for i in range(25):
        kind = rng.choice(["stroke", "rect", "fill", "spray"])
        x, y = rng.randrange(0, 300), rng.randrange(0, 260)
        col = tuple(rng.randrange(256) for _ in range(3))
        if kind == "stroke":
            stroke(d, [(x, y), (x + rng.randrange(-80, 80), y + rng.randrange(-80, 80))], col, size=rng.randrange(1, 30))
        elif kind == "rect":
            d.begin(); pe.draw_shape(d, "rect", [(x, y), (x + 60, y + 40)], col, 255, 3, rng.random() < .5); d.commit()
        elif kind == "fill":
            d.begin(); pe.flood_fill(d, x, y, col, 255, 20); d.commit()
        else:
            d.begin(); pe.spray(d, x, y, x + 10, y + 5, col, 255, 8, 20, rng); d.commit()
        snaps.append(d.image.copy())
    n = len(d.undo_stack)
    assert n >= 15, n
    for k in range(n, 0, -1):
        assert d.undo()
        assert pe.changed_bbox(d.image, snaps[k - 1]) is None, f"undo passo {k}"
    for k in range(1, n + 1):
        assert d.redo()
        assert pe.changed_bbox(d.image, snaps[k]) is None, f"redo passo {k}"
    assert not d.redo()


def test_noop_operation_does_not_pollute_history():
    d = doc()
    d.begin(); d.touch((0, 0, 50, 50)); assert d.commit() is False
    assert not d.undo_stack and not d.modified


def test_history_budget_evicts_oldest_and_keeps_recent_working():
    d = pe.Document(Image.new("RGBA", (512, 512), (255, 255, 255, 255)), history_bytes=3 * 256 * 256 * 8)
    for i in range(10):
        stroke(d, [(10, 10 + i * 30), (400, 10 + i * 30)], size=10)
    assert d.history_bytes <= 3 * 256 * 256 * 8 + 6 * 256 * 256 * 4
    assert len(d.undo_stack) < 10
    before = d.image.copy()
    d.undo(); d.redo()
    assert pe.changed_bbox(d.image, before) is None


def test_replace_image_changes_size_and_is_undoable_and_rollback_restores():
    d = doc(100, 80)
    d.replace_image(Image.new("RGBA", (40, 30), (0, 0, 255, 255)))
    assert d.size == (40, 30) and d.dirty_all
    d.undo()
    assert d.size == (100, 80)
    d.redo()
    assert d.size == (40, 30)
    d.begin(); pe.draw_shape(d, "rect", [(2, 2), (20, 20)], (0, 0, 0), 255, 2, True)
    assert d.image.getpixel((10, 10))[:3] == (0, 0, 0)
    d.rollback()
    assert d.image.getpixel((10, 10)) == (0, 0, 255, 255) and len(d.undo_stack) == 1


def test_history_keeps_only_before_tiles_until_undone():
    d = pe.Document(Image.new("RGBA", (512, 512), (255, 255, 255, 255)))
    stroke(d, [(10, 10), (500, 500)], size=40)
    one = d.history_bytes
    n_tiles = len(d.undo_stack[-1].tiles)
    assert one == n_tiles * 256 * 256 * 4
    d.undo()
    assert d.history_bytes == 2 * one


def test_modified_flag_follows_undo_and_save():
    d = doc()
    assert not d.modified
    stroke(d, [(5, 5), (30, 30)])
    assert d.modified
    d.mark_saved(); assert not d.modified
    d.undo(); assert d.modified
    d.redo(); assert not d.modified


def test_opacity_does_not_accumulate_inside_one_stroke():
    d = doc(100, 60)
    pts = [(10, 30), (90, 30), (10, 30), (90, 30), (10, 30)]
    stroke(d, pts, (0, 0, 0), opacity=128, size=10)
    v = d.image.getpixel((50, 30))[0]
    assert 120 <= v <= 135
    d2 = doc(100, 60)
    stroke(d2, pts, (0, 0, 0), opacity=255, size=10)
    assert d2.image.getpixel((50, 30))[:3] == (0, 0, 0)


def test_brush_is_antialiased_and_pixel_tool_is_hard():
    d = doc(80, 80)
    stroke(d, [(10, 10), (70, 55)], (0, 0, 0), size=9, aa=True)
    vals = {d.image.getpixel((x, y))[0] for x in range(80) for y in range(80)}
    assert any(20 < v < 235 for v in vals)
    d2 = doc(80, 80)
    stroke(d2, [(10, 10), (70, 55)], (0, 0, 0), size=9, aa=False)
    vals2 = {d2.image.getpixel((x, y))[0] for x in range(80) for y in range(80)}
    assert vals2 <= {0, 255}


def test_stroke_near_edges_and_outside_is_clipped_without_error():
    d = doc(50, 50)
    stroke(d, [(-20, -20), (70, 70)], size=12)
    stroke(d, [(200, 200), (300, 300)], size=12)
    assert d.image.size == (50, 50)


def test_spray_changes_only_near_the_cursor():
    d = doc(200, 200)
    d.begin(); pe.spray(d, 100, 100, 100, 100, (255, 0, 0), 255, 10, 60, random.Random(1)); d.commit()
    bb = ImageChops.difference(d.image.convert("RGB"), Image.new("RGB", (200, 200), (255, 255, 255))).getbbox()
    assert bb and bb[0] >= 100 - 22 and bb[2] <= 100 + 23


def ref_fill(img, x, y, tol):
    w, h = img.size
    px = img.load()
    t = px[x, y]
    dist = lambda c: sum(abs(c[i] - t[i]) for i in range(4))  # noqa: E731
    seen = {(x, y)}
    q = deque([(x, y)])
    while q:
        cx, cy = q.popleft()
        for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
            if 0 <= nx < w and 0 <= ny < h and (nx, ny) not in seen and min(dist(px[nx, ny]), 255) <= tol:
                seen.add((nx, ny))
                q.append((nx, ny))
    return seen


@pytest.mark.parametrize("seed", range(6))
@pytest.mark.parametrize("tol", [0, 30, 90])
def test_flood_fill_region_equals_reference_bfs(seed, tol):
    rng = random.Random(seed * 100 + tol)
    w, h = 40, 30
    img = Image.new("RGBA", (w, h), (255, 255, 255, 255))
    px = img.load()
    palette = [(255, 255, 255, 255), (250, 250, 250, 255), (200, 30, 30, 255), (20, 20, 200, 255), (255, 255, 255, 0)]
    for yy in range(h):
        for xx in range(w):
            if rng.random() < 0.38:
                px[xx, yy] = rng.choice(palette)
    x, y = rng.randrange(w), rng.randrange(h)
    got = pe.region_mask(img, x, y, tol)
    want = ref_fill(img, x, y, tol)
    assert got is not None
    mask, bbox = got
    mp = mask.load()
    have = {(xx, yy) for yy in range(h) for xx in range(w) if mp[xx, yy]}
    assert have == want
    xs, ys = [p[0] for p in want], [p[1] for p in want]
    assert bbox == (min(xs), min(ys), max(xs) + 1, max(ys) + 1)


def test_flood_fill_does_not_leak_through_a_closed_wall_and_respects_opacity():
    d = doc(60, 60)
    d.begin(); pe.draw_shape(d, "rect", [(10, 10), (40, 40)], (0, 0, 0), 255, 2, False, aa=False); d.commit()
    d.begin(); pe.flood_fill(d, 25, 25, (255, 0, 0), 255, 10); d.commit()
    assert d.image.getpixel((25, 25))[:3] == (255, 0, 0) and d.image.getpixel((5, 5))[:3] == (255, 255, 255)
    d.begin(); pe.flood_fill(d, 5, 5, (0, 0, 0), 128, 10); d.commit()
    assert 120 <= d.image.getpixel((5, 5))[0] <= 135 and d.image.getpixel((5, 5))[3] == 255
    assert d.image.getpixel((25, 25))[:3] == (255, 0, 0)


def test_flood_fill_out_of_bounds_and_global_mode():
    d = doc(30, 30)
    assert pe.flood_fill(d, -1, 5, (0, 0, 0), 255, 0) is None
    d.begin(); pe.draw_shape(d, "rect", [(5, 5), (24, 24)], (0, 0, 0), 255, 1, False, aa=False); d.commit()
    d.begin(); pe.flood_fill(d, 1, 1, (0, 255, 0), 255, 0, contiguous=False); d.commit()
    assert d.image.getpixel((15, 15))[:3] == (0, 255, 0)


def test_shapes_fill_and_outline():
    d = doc(80, 80)
    d.begin(); pe.draw_shape(d, "ellipse", [(10, 10), (70, 70)], (0, 0, 255), 255, 3, True); d.commit()
    assert d.image.getpixel((40, 40))[:3] == (0, 0, 255) and d.image.getpixel((2, 2))[:3] == (255, 255, 255)
    d2 = doc(80, 80)
    d2.begin(); pe.draw_shape(d2, "rect", [(70, 70), (10, 10)], (0, 0, 0), 255, 2, False, aa=False); d2.commit()
    assert d2.image.getpixel((40, 40))[:3] == (255, 255, 255) and d2.image.getpixel((10, 40))[:3] == (0, 0, 0)
    d3 = doc(80, 80)
    d3.begin(); pe.draw_shape(d3, "polygon", [(10, 70), (40, 10), (70, 70)], (255, 0, 0), 255, 2, True); d3.commit()
    assert d3.image.getpixel((40, 50))[:3] == (255, 0, 0)
    assert pe.draw_shape(d3, "polygon", [(1, 1), (2, 2)], (0, 0, 0), 255, 1, False) is None


@pytest.mark.parametrize("zoom", [0.25, 0.5, 1.0, 1.5, 3.0, 8.0, 17.0])
@pytest.mark.parametrize("transparent", [False, True])
def test_partial_render_equals_full_render(zoom, transparent):
    rng = random.Random(3)
    img = Image.new("RGBA", (64, 48))
    img.putdata([(rng.randrange(256), rng.randrange(256), rng.randrange(256), rng.choice([255, 120, 0]) if transparent else 255)
                 for _ in range(64 * 48)])
    pan = (37, -11)
    full = pe.render_rect(img, zoom, pan, (0, 0, 300, 260), (64, 64, 64), transparent)
    for rect in [(10, 20, 90, 70), (150, 0, 300, 100), (0, 120, 120, 260), (97, 3, 101, 9)]:
        part = pe.render_rect(img, zoom, pan, rect, (64, 64, 64), transparent)
        ref = full.crop(rect)
        diff = ImageChops.difference(part, ref).getbbox()
        if zoom >= 1:
            assert diff is None, (zoom, rect)
        else:
            err = sum(ImageChops.difference(part, ref).convert("L").getdata()) / max(1, part.width * part.height)
            assert err < 3, (zoom, rect, err)


def test_render_outside_image_is_background_and_grid_lines_appear_at_high_zoom():
    img = Image.new("RGBA", (10, 10), (255, 255, 255, 255))
    out = pe.render_rect(img, 10.0, (50, 50), (0, 0, 200, 200), (1, 2, 3), False)
    assert out.getpixel((5, 5)) == (1, 2, 3) and out.getpixel((55, 55)) == (255, 255, 255)
    assert out.getpixel((60, 80)) == (96, 96, 96)
    nogrid = pe.render_rect(img, 10.0, (50, 50), (0, 0, 200, 200), (1, 2, 3), False, grid=False)
    assert nogrid.getpixel((60, 80)) == (255, 255, 255)


def test_checkerboard_is_anchored_to_the_image_not_the_screen():
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    a = pe.render_rect(img, 1.0, (0, 0), (0, 0, 64, 64), (0, 0, 0), True)
    b = pe.render_rect(img, 1.0, (21, 5), (21, 5, 85, 69), (0, 0, 0), True)
    assert ImageChops.difference(a, b).getbbox() is None


def test_flatten_for_save_puts_transparency_on_white_for_jpeg():
    img = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
    assert pe.flatten_for_save(img, "jpeg").getpixel((1, 1)) == (255, 255, 255)
    assert pe.flatten_for_save(img, "png") is img


def test_large_image_performance_budgets():
    d = pe.Document(Image.new("RGBA", (4000, 3000), (255, 255, 255, 255)))
    d.begin(); mask = pe.new_stroke_mask(d)
    t0 = time.perf_counter()
    for i in range(40):
        box = pe.stamp_segment(mask, 100 + i * 8, 200 + (i % 5) * 10, 108 + i * 8, 205 + (i % 5) * 10, 24, True)
        pe.apply_stroke_region(d, mask, box, (255, 0, 0), 200)
    per_event = (time.perf_counter() - t0) / 40
    d.commit()
    assert per_event < 0.015, f"{per_event * 1000:.1f} ms por evento"
    t0 = time.perf_counter()
    d.begin(); pe.flood_fill(d, 5, 5, (0, 255, 0), 255, 20); d.commit()
    assert time.perf_counter() - t0 < 3.0
    t0 = time.perf_counter(); d.undo(); d.redo()
    assert time.perf_counter() - t0 < 1.0
    t0 = time.perf_counter()
    for _ in range(5):
        pe.render_rect(d.image, 8.0, (-12000, -9000), (0, 0, 1200, 800), (64, 64, 64), False)
    assert (time.perf_counter() - t0) / 5 < 0.08
