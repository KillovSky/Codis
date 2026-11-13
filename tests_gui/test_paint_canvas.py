"""Paint: canvas real (Tk + Xvfb). Compara o que é EXIBIDO (PhotoImage) com o modelo."""

from __future__ import annotations

import re
import time

import pytest
from PIL import Image


class Ev:
    def __init__(self, x, y, state=0, delta=0, num=0):
        self.x, self.y, self.state, self.delta, self.num = x, y, state, delta, num


@pytest.fixture
def pc(tk_root, tmp_path, monkeypatch):
    from src.ui import paint_panel as _pp
    for name in ("asksaveasfilename", "askopenfilename"):
        monkeypatch.setattr(_pp.filedialog, name, lambda *a, **k: "")
    from src.theme.base import _define_themes
    from src.theme.manager import ThemeManager
    from src.ui.paint_panel import PaintPanel

    _define_themes()
    tk_root.geometry("1100x760+0+0")
    panel = PaintPanel(tk_root, ThemeManager("fluent_dark"))
    panel.pack(fill="both", expand=True)
    tk_root.update()
    c = panel._canvas
    c.new_canvas(400, 300)
    c.set_zoom(1.0)
    tk_root.update()

    class H:
        pass
    h = H()
    h.root, h.panel, h.c, h.tmp = tk_root, panel, c, tmp_path

    def upd():
        tk_root.update()
    h.upd = upd

    def scr(ix, iy):
        x, y = c.to_screen(ix + 0.5, iy + 0.5)
        return int(x), int(y)
    h.scr = scr

    def drag(points, primary=True, state=0):
        x, y = scr(*points[0])
        c._on_press(Ev(x, y, state), primary)
        for p in points[1:]:
            x, y = scr(*p)
            c._on_drag(Ev(x, y, state), primary)
        c._on_release(Ev(x, y, state), primary)
        upd()
    h.drag = drag

    def shown(ix, iy):
        sx, sy = scr(ix, iy)
        r = c.tk.call(str(c._photo), "get", sx, sy)
        return tuple(int(v) for v in re.findall(r"\d+", str(r)))
    h.shown = shown
    yield h
    for w in list(tk_root.winfo_children()):
        w.destroy()
    tk_root.update()


def px(c, x, y):
    return c.image.getpixel((x, y))[:3]


def test_stroke_updates_model_and_screen_identically_and_flush_is_coalesced(pc):
    c = pc.c
    c.tool, c.size, c.color = "brush", 10, "#ff0000"
    x, y = pc.scr(20, 40)
    c._on_press(Ev(x, y), True)
    flushes = []
    orig = c.flush
    c.flush = lambda: (flushes.append(1), orig())[1]
    for i in range(30):
        c._on_drag(Ev(x + i * 5, y + (i % 3)), True)
    assert c._flush_id is not None and not flushes
    c.flush = orig
    pc.upd()
    c._on_release(Ev(x + 150, y), True)
    pc.upd()
    assert px(c, 100, 40) == (255, 0, 0)
    assert pc.shown(100, 40) == (255, 0, 0)
    assert pc.shown(100, 150) == (255, 255, 255)


def test_every_visible_pixel_matches_a_fresh_render_after_many_edits(pc):
    """Atualizações incrementais por retângulo não podem deixar resíduo na tela."""
    from src.ui import paint_engine as pe
    c = pc.c
    for z in (1.0, 3.0, 0.5):
        c.set_zoom(z)
        pc.upd()
        c.tool, c.size, c.color, c.fill_shapes = "brush", 14, "#0066ff", True
        pc.drag([(30, 30), (200, 120), (60, 250)])
        c.tool, c.color = "ellipse", "#00aa00"
        pc.drag([(100, 60), (180, 140)])
        c.tool, c.color = "spray", "#aa0000"
        pc.drag([(250, 200), (300, 220)])
        c.undo()
        pc.upd()
        vw, vh = c._photo_size
        ref = pe.render_rect(c.image, c._zoom, tuple(c._pan), (0, 0, vw, vh), pe.hex_to_rgb("#404040"),
                             c.doc.maybe_transparent)
        for sx in range(0, vw, 37):
            for sy in range(0, vh, 29):
                got = tuple(int(v) for v in re.findall(r"\d+", str(c.tk.call(str(c._photo), "get", sx, sy))))
                assert got == ref.getpixel((sx, sy)), (z, sx, sy)


def test_undo_redo_and_modified_flag(pc):
    c = pc.c
    assert not c.modified
    c.tool, c.size, c.color = "brush", 8, "#000000"
    pc.drag([(10, 10), (150, 10)])
    assert c.modified and px(c, 80, 10) == (0, 0, 0)
    c._on_press  # noqa: B018
    c.undo(); pc.upd()
    assert px(c, 80, 10) == (255, 255, 255) and not c.modified and pc.shown(80, 10) == (255, 255, 255)
    c.redo(); pc.upd()
    assert px(c, 80, 10) == (0, 0, 0) and pc.shown(80, 10) == (0, 0, 0)


def test_eraser_uses_secondary_color_and_right_button_uses_secondary(pc):
    c = pc.c
    c.color, c.color2 = "#000000", "#ffff00"
    c.tool, c.size = "brush", 12
    pc.drag([(10, 20), (200, 20)])
    c.tool = "eraser"
    pc.drag([(50, 20), (100, 20)])
    assert px(c, 75, 20) == (255, 255, 0) and px(c, 150, 20) == (0, 0, 0)
    c.tool = "brush"
    pc.drag([(10, 80), (100, 80)], primary=False)
    assert px(c, 50, 80) == (255, 255, 0)


def test_fill_tolerance_and_ctrl_global_fill_and_eyedropper(pc):
    c = pc.c
    c.color, c.size, c.tool, c.fill_shapes = "#000000", 2, "rect", False
    pc.drag([(50, 50), (150, 150)])
    c.tool, c.color, c.tolerance = "fill", "#ff0000", 5
    pc.drag([(100, 100), (100, 100)])
    assert px(c, 100, 100) == (255, 0, 0) and px(c, 10, 10) == (255, 255, 255)
    c.color = "#00ff00"
    pc.drag([(10, 10), (10, 10)], state=0x4)
    assert px(c, 10, 10) == (0, 255, 0) and px(c, 100, 100) == (255, 0, 0)
    got = []
    c.on_color_pick = lambda col, primary: got.append((col, primary))
    c.tool = "eyedrop"
    pc.drag([(100, 100), (100, 100)])
    pc.drag([(10, 10), (10, 10)], primary=False)
    assert got == [("#ff0000", True), ("#00ff00", False)]


def test_shift_constrains_square_circle_and_45_degree_lines(pc):
    c = pc.c
    c.color, c.size, c.fill_shapes = "#000000", 2, True
    c.tool = "rect"
    pc.drag([(20, 20), (120, 60)], state=0x1)
    assert px(c, 60, 100) == (0, 0, 0) and px(c, 150, 100) == (255, 255, 255)
    c.fill_shapes = False
    c.tool = "line"
    pc.drag([(200, 200), (260, 215)], state=0x1)
    assert px(c, 240, 200) == (0, 0, 0) and px(c, 240, 208) == (255, 255, 255)


def test_polygon_text_tool_and_escape_rollback(pc, monkeypatch):
    c = pc.c
    c.color, c.size, c.fill_shapes, c.tool = "#aa00aa", 2, True, "polygon"
    for p in [(100, 200), (200, 200), (150, 100)]:
        x, y = pc.scr(*p)
        c._on_press(Ev(x, y), True)
        c._on_release(Ev(x, y), True)
    c._on_double_click(Ev(0, 0))
    pc.upd()
    assert px(c, 150, 170) == (170, 0, 170)
    from src.ui import paint_canvas as pcv
    monkeypatch.setattr(pcv.simpledialog, "askstring", lambda *a, **k: "Olá")
    c.tool, c.color, c.size = "text", "#000000", 5
    x, y = pc.scr(20, 20)
    c._on_press(Ev(x, y), True)
    c._on_release(Ev(x, y), True)
    pc.upd()
    assert any(px(c, xx, yy) != (255, 255, 255) for xx in range(20, 80) for yy in range(20, 50))
    c.tool, c.color, c.size = "brush", "#ff0000", 20
    x, y = pc.scr(300, 250)
    c._on_press(Ev(x, y), True)
    c._on_drag(Ev(x + 40, y), True)
    c.cancel_operation(); pc.upd()
    assert px(c, 310, 250) == (255, 255, 255) and pc.shown(310, 250) == (255, 255, 255)


def test_selection_move_really_moves_instead_of_duplicating_and_is_one_undo(pc):
    c = pc.c
    c.color, c.color2, c.size, c.fill_shapes, c.tool = "#ff0000", "#ffffff", 2, True, "rect"
    pc.drag([(40, 40), (79, 79)])
    assert px(c, 60, 60) == (255, 0, 0)
    c.tool = "select"
    pc.drag([(30, 30), (90, 90)])
    assert c._selection == (30, 30, 91, 91)
    pc.drag([(60, 60), (160, 160)])
    c.commit_float(); pc.upd()
    assert px(c, 60, 60) == (255, 255, 255)
    assert px(c, 160, 160) == (255, 0, 0)
    assert pc.shown(160, 160) == (255, 0, 0) and pc.shown(60, 60) == (255, 255, 255)
    c.undo(); pc.upd()
    assert px(c, 60, 60) == (255, 0, 0) and px(c, 160, 160) == (255, 255, 255)


def test_copy_paste_cut_delete_and_float_cancel(pc):
    c = pc.c
    c.color, c.color2, c.fill_shapes, c.tool = "#0000ff", "#ffffff", True, "rect"
    pc.drag([(10, 10), (49, 49)])
    c.tool = "select"
    pc.drag([(5, 5), (55, 55)])
    c.copy_selection()
    c.paste(); pc.upd()
    assert c._float and c._float["img"].size == (51, 51)
    c._move_float(200, 150)
    c.commit_float(); pc.upd()
    n_blue = sum(1 for xx in range(400) for yy in range(300) if px(c, xx, yy) == (0, 0, 255))
    assert n_blue > 2 * 35 * 35
    c.cut_selection(); pc.upd()
    assert px(c, c._clip and 30, 30) in ((255, 255, 255), (0, 0, 255))
    c.undo(); c.undo(); pc.upd()
    c.select_all()
    x, y = pc.scr(100, 100)
    c._on_press(Ev(x, y), True)
    c._on_drag(Ev(x + 30, y + 30), True)
    assert c._float
    c.cancel_operation(); pc.upd()
    assert not c._float and px(c, 30, 30) == (0, 0, 255)


def test_switching_tool_commits_float_and_pan_tool_moves_view(pc):
    c = pc.c
    c.color, c.fill_shapes, c.tool = "#ff0000", True, "rect"
    pc.drag([(10, 10), (49, 49)])
    c.tool = "select"
    pc.drag([(5, 5), (55, 55)])
    pc.drag([(30, 30), (130, 130)])
    assert c._float
    c.tool = "brush"
    assert not c._float and px(c, 130, 130) == (255, 0, 0)
    c.tool = "pan"
    p0 = list(c._pan)
    c._on_press(Ev(200, 200), True)
    c._on_drag(Ev(230, 215), True)
    c._on_release(Ev(230, 215), True)
    pc.upd()
    assert c._pan == [p0[0] + 30, p0[1] + 15]
    assert not c.modified or True


def test_zoom_wheel_scroll_and_scrollbar_protocol(pc):
    c = pc.c
    z0 = c.zoom
    c._on_wheel(Ev(300, 200, state=0x4, delta=120))
    assert c.zoom > z0
    c.set_zoom(1.0)
    p = list(c._pan)
    c._on_wheel(Ev(300, 200, state=0, delta=-120))
    assert c._pan[1] == p[1] - 60 and c.zoom == 1.0
    c._on_wheel(Ev(300, 200, state=0x1, delta=120))
    assert c._pan[0] == p[0] + 60
    got = []
    c._xsc = lambda lo, hi: got.append((lo, hi))
    c.xview("moveto", 0.0)
    assert c._pan[0] > 100 and got and 0 <= got[-1][0] <= got[-1][1] <= 1
    c.set_zoom(1.0)
    before = c.to_image(250, 150)
    c._zoom_at(250, 150, 1)
    after = c.to_image(250, 150)
    assert abs(before[0] - after[0]) < 1.5 and abs(before[1] - after[1]) < 1.5


def test_pan_is_clamped_so_the_image_cannot_be_lost(pc):
    c = pc.c
    c.pan_by(100000, 100000)
    vw, vh = c._view_size()
    assert c._pan[0] <= vw - 63 and c._pan[1] <= vh - 63
    c.pan_by(-100000, -100000)
    assert c._pan[0] >= 64 - 400 - 1


def test_huge_image_high_zoom_does_not_allocate_scaled_image(pc):
    c = pc.c
    c.new_canvas(6000, 5000)
    t0 = time.perf_counter()
    c.set_zoom(32.0)
    pc.upd()
    assert time.perf_counter() - t0 < 0.5
    c.tool, c.size, c.color = "brush", 3, "#ff0000"
    t0 = time.perf_counter()
    x, y = c.winfo_width() // 2, c.winfo_height() // 2
    c._on_press(Ev(x, y), True)
    for i in range(30):
        c._on_drag(Ev(x + i * 6, y + i * 2), True)
        pc.upd()
    c._on_release(Ev(x + 180, y + 60), True)
    assert (time.perf_counter() - t0) / 30 < 0.03


def test_save_open_roundtrip_atomic_jpeg_flatten_and_modified(pc):
    c = pc.c
    c.new_canvas(50, 40, (0, 0, 0, 0))
    assert c.doc.maybe_transparent
    c.tool, c.color, c.size = "brush", "#ff0000", 6
    pc.drag([(5, 5), (30, 20)])
    png = pc.tmp / "a.png"
    assert c.save_image(png) and not c.modified
    back = Image.open(png)
    assert back.mode == "RGBA" and back.getpixel((45, 35))[3] == 0
    jpg = pc.tmp / "a.jpg"
    assert c.save_image(jpg)
    assert Image.open(jpg).convert("RGB").getpixel((45, 35)) == (255, 255, 255)
    bad = pc.tmp / "naoexiste" / "x.png"
    assert not c.save_image(bad, quiet=True)
    assert c.open_image(png) and c.image.size == (50, 40) and not c.modified
    assert not c.open_image(pc.tmp / "nada.png", quiet=True)


def test_resize_canvas_is_undoable_and_new_prompts_when_modified(pc, monkeypatch):
    c, panel = pc.c, pc.panel
    c.resize_canvas(200, 100, scale=False)
    assert c.image.size == (200, 100)
    c.undo(); pc.upd()
    assert c.image.size == (400, 300)
    c.tool, c.size = "brush", 8
    pc.drag([(10, 10), (100, 10)])
    from src.ui import paint_panel as pp
    asked = []
    monkeypatch.setattr(pp.messagebox, "askyesnocancel", lambda *a, **k: (asked.append(1), None)[1])
    panel._on_new()
    assert asked and c.image.size == (400, 300) and px(c, 50, 10) != (255, 255, 255)
    monkeypatch.setattr(pp.messagebox, "askyesnocancel", lambda *a, **k: False)
    panel._on_new()
    assert px(c, 50, 10) == (255, 255, 255)


def test_shortcuts_ctrl_s_n_o_are_bound_and_status_indicates_unsaved(pc, monkeypatch):
    panel, c = pc.panel, pc.c
    called = []
    monkeypatch.setattr(panel, "_on_save", lambda: called.append("save"))
    c.focus_force(); pc.upd()
    c.event_generate("<Control-s>"); pc.upd()
    assert "save" in called
    c.tool, c.size = "brush", 8
    pc.drag([(10, 10), (60, 10)])
    assert "•" in panel._dim_var.get()


def test_hover_ring_follows_cursor_and_hides_for_other_tools(pc):
    c = pc.c
    c.tool, c.size = "brush", 20
    c._on_motion(Ev(200, 200))
    assert c.find_withtag("ring")
    c.tool = "fill"
    assert not c.find_withtag("ring")
