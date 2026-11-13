"""Emulador VT: comportamento esperado de xterm para as sequências mais usadas."""

from __future__ import annotations

from src.terminal.palette import color_to_hex, resolve_attr
from src.terminal.vt import (
    FLAG_BOLD,
    FLAG_REVERSE,
    VTScreen,
    char_width,
)


def screen(data="", rows=6, cols=20):
    s = VTScreen(rows, cols)
    s.feed(data)
    return s


def test_plain_text_and_newlines():
    s = screen("ab\r\ncd")
    assert s.line_text(0) == "ab" and s.line_text(1) == "cd"


def test_autowrap_is_deferred_until_next_character():
    s = screen("x" * 20, cols=20)
    assert (s.cx, s.cy) == (19, 0)
    s.feed("y")
    assert s.line_text(1) == "y"


def test_cursor_movement_and_absolute_positioning():
    s = screen("\x1b[3;5Hx\x1b[1Ay\x1b[2Dz")
    assert s.line_text(2) == "    x"
    assert s.line_text(1) == "    zy"


def test_erase_in_line_and_display():
    s = screen("abcdef\x1b[1;4H\x1b[K")
    assert s.line_text(0) == "abc"
    s = screen("aa\r\nbb\r\ncc\x1b[2;1H\x1b[J")
    assert s.text().split("\n")[:3] == ["aa", "", ""]


def test_insert_and_delete_lines_and_chars():
    s = screen("1\r\n2\r\n3\x1b[2;1H\x1b[L")
    assert [s.line_text(i) for i in range(4)] == ["1", "", "2", "3"]
    s = screen("abcdef\x1b[1;3H\x1b[2P")
    assert s.line_text(0) == "abef"
    s = screen("abcdef\x1b[1;3H\x1b[2@")
    assert s.line_text(0) == "ab  cdef"


def test_scroll_region_confines_scrolling():
    s = screen("top\x1b[2;4r\x1b[2;1H" + "\r\n".join(f"r{i}" for i in range(8)), rows=6)
    assert s.line_text(0) == "top"
    assert s.take_scrolled() == []


def test_lines_leaving_the_top_are_reported_for_history():
    s = screen("\r\n".join(str(i) for i in range(10)), rows=4)
    gone = ["".join(c).rstrip() for c, _ in s.take_scrolled()]
    assert gone == ["0", "1", "2", "3", "4", "5"]
    assert s.take_scrolled() == []


def test_alternate_screen_saves_and_restores_main():
    s = screen("main\r\nline2")
    s.feed("\x1b[?1049h\x1b[2J\x1b[HALT")
    assert s.alt_screen and s.line_text(0) == "ALT"
    s.feed("\x1b[?1049l")
    assert not s.alt_screen
    assert s.line_text(0) == "main" and s.line_text(1) == "line2"
    assert (s.cx, s.cy) == (5, 1)


def test_save_and_restore_cursor():
    s = screen("ab\x1b7cd\x1b8XY")
    assert s.line_text(0) == "abXY"


def test_sequences_split_across_chunks_are_reassembled():
    s = VTScreen(4, 20)
    for chunk in ("ab\x1b", "[1;5Hq", "\x1b[3", "1mcolored\x1b[0m", "\x1b]0;ti", "tle\x07!"):
        s.feed(chunk)
    assert s.line_text(0).startswith("ab") and "q" in s.line_text(0)
    assert "colored" in s.line_text(0)
    assert s.title == "title"


def test_sgr_colors_and_flags():
    s = screen("\x1b[1;31mA\x1b[0mB\x1b[38;5;200mC\x1b[48;2;1;2;3mD\x1b[38:2::9:8:7mE")
    fg, bg, flags = s.attr_table[s.grid[0].attrs[0]]
    assert fg == 1 and flags & FLAG_BOLD
    assert s.grid[0].attrs[1] == 0
    assert s.attr_table[s.grid[0].attrs[2]][0] == 200
    assert s.attr_table[s.grid[0].attrs[3]][1] == 0x1000000 | (1 << 16) | (2 << 8) | 3
    assert s.attr_table[s.grid[0].attrs[4]][0] == 0x1000000 | (9 << 16) | (8 << 8) | 7


def test_runs_merge_equal_attributes_and_trim_blank_tail():
    s = screen("\x1b[31mred\x1b[0m plain   ")
    runs = s.row_runs(0)
    assert [t for t, _ in runs] == ["red", " plain"]


def test_device_status_and_attribute_queries_get_answers():
    s = screen("\x1b[3;7H\x1b[6n\x1b[5n\x1b[c")
    assert s.take_responses() == b"\x1b[3;7R\x1b[0n\x1b[?62;c"
    assert s.take_responses() == b""


def test_wide_characters_take_two_cells_and_combining_marks_none():
    assert char_width("\u4e2d") == 2 and char_width("a") == 1 and char_width("\u0301") == 0
    s = screen("\u4e2dx")
    assert s.line_text(0) == "\u4e2dx" and s.cx == 3
    s = screen("e\u0301x")
    assert s.line_text(0) == "e\u0301x" and s.cx == 2
    assert s.text_col(0, 2) == 3


def test_dec_special_graphics_draws_boxes():
    s = screen("\x1b(0lqk\x1b(B")
    assert s.line_text(0) == "\u250c\u2500\u2510"


def test_modes_are_tracked():
    s = screen("\x1b[?1h\x1b[?25l\x1b[?2004h\x1b[?1000h\x1b[?1006h")
    assert s.app_cursor and not s.cursor_visible and s.bracketed_paste
    assert s.mouse_mode == 1000 and s.mouse_sgr
    s.feed("\x1b[?1l\x1b[?25h\x1b[?2004l\x1b[?1000l")
    assert not s.app_cursor and s.cursor_visible and not s.bracketed_paste and s.mouse_mode == 0


def test_osc7_updates_cwd_and_title_is_limited():
    s = screen("\x1b]7;file://host/home/u/my%20dir\x07")
    assert s.cwd == "/home/u/my dir"
    s.feed("\x1b]0;" + "x" * 1000 + "\x07")
    assert len(s.title) == 256


def test_resize_preserves_content_and_clamps_cursor():
    s = screen("abc\r\ndef\r\nghi", rows=3, cols=10)
    s.resize(5, 6)
    assert s.line_text(0) == "abc" and (s.rows, s.cols) == (5, 6)
    s.resize(2, 4)
    assert s.rows == 2 and s.cy < 2 and s.cx < 4
    assert len(s.grid) == 2 and all(len(r.chars) == 4 for r in s.grid)


def test_resize_with_cursor_at_bottom_moves_top_rows_to_history():
    s = screen("\r\n".join("L%d" % i for i in range(5)), rows=5, cols=10)
    s.resize(3, 10)
    gone = ["".join(c).rstrip() for c, _ in s.take_scrolled()]
    assert gone == ["L0", "L1"]
    assert s.line_text(2) == "L4"


def test_reset_clears_everything():
    s = screen("hello\x1b[?25l\x1b[?1049h")
    s.feed("\x1bc")
    assert s.text().strip() == "" and s.cursor_visible and not s.alt_screen


def test_malformed_and_huge_sequences_do_not_hang_or_grow_forever():
    s = VTScreen(4, 20)
    s.feed("\x1b[" + "9" * 20000)
    s.feed("\x1b]" + "x" * 20000)
    s.feed("\x1b[999999999999;999999999999H\x1b[99999999A\x1b[?9999h text")
    assert "text" in s.text()
    assert len(s._pending) <= 8192


def test_scrolled_buffer_is_capped():
    s = VTScreen(2, 10)
    s.feed("\r\n".join("x" for _ in range(20000)))
    assert len(s.take_scrolled()) <= 5000


def test_palette_colors():
    assert color_to_hex(-1) is None and color_to_hex(1) == "#f44747"
    assert color_to_hex(16) == "#000000" and color_to_hex(231) == "#ffffff"
    assert color_to_hex(244) == "#808080"
    assert color_to_hex(0x1000000 | 0x102030) == "#102030"


def test_resolve_attr_handles_reverse_bold_and_defaults():
    opts = resolve_attr((-1, -1, FLAG_REVERSE), "#ffffff", "#000000")
    assert opts["foreground"] == "#000000" and opts["background"] == "#ffffff"
    opts = resolve_attr((1, -1, FLAG_BOLD), "#ffffff", "#000000")
    assert opts["foreground"] == "#f44747" and opts["_font"] == (True, False)
    assert "background" not in resolve_attr((-1, -1, 0), "#ffffff", "#000000")
