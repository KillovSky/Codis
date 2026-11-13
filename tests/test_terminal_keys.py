"""Tradução de teclas e mouse para sequências de terminal."""

from __future__ import annotations

import pytest

from src.terminal.keys import encode_key, encode_mouse, encode_paste


@pytest.mark.parametrize(
    ("keysym", "char", "kwargs", "expected"),
    [
        ("a", "a", {}, b"a"),
        ("A", "A", {"shift": True}, b"A"),
        ("eacute", "\u00e9", {}, "\u00e9".encode()),
        ("Return", "\r", {}, b"\r"),
        ("BackSpace", "\x08", {}, b"\x7f"),
        ("Tab", "\t", {}, b"\t"),
        ("Tab", "\t", {"shift": True}, b"\x1b[Z"),
        ("ISO_Left_Tab", "", {}, b"\x1b[Z"),
        ("Escape", "\x1b", {}, b"\x1b"),
        ("Up", "", {}, b"\x1b[A"),
        ("Up", "", {"app_cursor": True}, b"\x1bOA"),
        ("Left", "", {"ctrl": True}, b"\x1b[1;5D"),
        ("Right", "", {"alt": True, "shift": True}, b"\x1b[1;4C"),
        ("Home", "", {}, b"\x1b[H"),
        ("End", "", {"app_cursor": True}, b"\x1bOF"),
        ("Delete", "", {}, b"\x1b[3~"),
        ("Prior", "", {}, b"\x1b[5~"),
        ("Next", "", {"ctrl": True}, b"\x1b[6;5~"),
        ("F1", "", {}, b"\x1bOP"),
        ("F5", "", {}, b"\x1b[15~"),
        ("F12", "", {"shift": True}, b"\x1b[24;2~"),
        ("c", "\x03", {"ctrl": True}, b"\x03"),
        ("d", "\x04", {"ctrl": True}, b"\x04"),
        ("z", "\x1a", {"ctrl": True}, b"\x1a"),
        ("bracketleft", "\x1b", {"ctrl": True}, b"\x1b"),
        ("space", "\x00", {"ctrl": True}, b"\x00"),
        ("x", "x", {"alt": True}, b"\x1bx"),
        ("BackSpace", "", {"ctrl": True}, b"\x08"),
        ("space", " ", {}, b" "),
    ],
)
def test_key_encoding(keysym, char, kwargs, expected):
    assert encode_key(keysym, char, **kwargs) == expected


@pytest.mark.parametrize("keysym", ["Shift_L", "Control_R", "Alt_L", "Caps_Lock", "Super_L"])
def test_lone_modifier_keys_send_nothing(keysym):
    assert encode_key(keysym, "") is None


def test_paste_plain_and_bracketed():
    assert encode_paste("a\nb", False) == b"a\rb"
    assert encode_paste("a\r\nb", True) == b"\x1b[200~a\rb\x1b[201~"


def test_bracketed_paste_cannot_be_terminated_early_by_pasted_text():
    data = encode_paste("evil\x1b[201~; rm -rf ~", True)
    assert data.count(b"\x1b[201~") == 1 and data.endswith(b"\x1b[201~")


def test_mouse_sgr_and_legacy_encodings():
    assert encode_mouse(0, 10, 5, pressed=True) == b"\x1b[<0;10;5M"
    assert encode_mouse(0, 10, 5, pressed=False) == b"\x1b[<0;10;5m"
    assert encode_mouse(64, 1, 1, pressed=True, ctrl=True) == b"\x1b[<80;1;1M"
    assert encode_mouse(0, 10, 5, pressed=True, motion=True) == b"\x1b[<32;10;5M"
    assert encode_mouse(0, 3, 4, pressed=True, sgr=False) == b"\x1b[M" + bytes([32, 35, 36])
    assert encode_mouse(0, 300, 4, pressed=True, sgr=False) == b""
