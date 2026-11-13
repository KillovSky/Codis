"""TkTextAdapter: edição incremental fiel, inclusive com emoji e texto somente leitura."""

from __future__ import annotations

import random
import tkinter as tk

import pytest

from src.collab.ot import TextOp, apply_ops
from src.collab.tk_adapter import TkTextAdapter, tk_index

ALPHABET = "ab\n😀é字\U0001F680"


@pytest.fixture()
def text(tk_root):
    widget = tk.Text(tk_root)
    widget.pack()
    yield widget
    widget.destroy()


def _rand_ops(rng, doc):
    ops = []
    for _ in range(rng.randint(1, 4)):
        if rng.random() < 0.55 or not doc:
            op = TextOp("insert", rng.randint(0, len(doc)), text="".join(rng.choice(ALPHABET) for _ in range(rng.randint(1, 3))))
        else:
            pos = rng.randint(0, len(doc) - 1)
            op = TextOp("delete", pos, length=rng.randint(1, min(4, len(doc) - pos)))
        ops.append(op)
        doc = apply_ops(doc, [op])
    return ops


def test_fuzz_adapter_matches_pure_string_application(text):
    rng = random.Random(11)
    adapter = TkTextAdapter(text)
    doc = "".join(rng.choice(ALPHABET) for _ in range(30))
    adapter.set_text(doc)
    for _ in range(600):
        ops = _rand_ops(rng, doc)
        adapter.apply_ops(ops)
        doc = apply_ops(doc, ops)
        assert adapter.get_text() == doc


def test_tk_index_accounts_for_astral_characters():
    assert tk_index("a😀b", 2) == "1.3"
    assert tk_index("a\nb", 2) == "2.0"
    assert tk_index("abc", 99) == "1.3"


def test_incremental_apply_preserves_cursor_and_selection(text):
    adapter = TkTextAdapter(text)
    adapter.set_text("hello world")
    text.mark_set("insert", "1.8")
    text.tag_add("sel", "1.6", "1.11")
    adapter.apply_ops([TextOp("insert", 0, text=">>> ")])
    assert text.get("1.0", "end-1c") == ">>> hello world"
    assert text.index("insert") == "1.12"
    assert text.get("sel.first", "sel.last") == "world"


def test_set_text_keeps_cursor_line(text):
    adapter = TkTextAdapter(text)
    adapter.set_text("\n".join(f"l{i}" for i in range(50)))
    text.mark_set("insert", "10.1")
    adapter.set_text("\n".join(f"l{i}" for i in range(60)))
    assert text.index("insert") == "10.1"


def test_readonly_editor_is_written_and_restored(text):
    adapter = TkTextAdapter(text)
    adapter.set_text("abc")
    text.configure(state="disabled")
    adapter.apply_ops([TextOp("insert", 3, text="d")])
    assert str(text.cget("state")) == "disabled"
    text.configure(state="normal")
    assert text.get("1.0", "end-1c") == "abcd"


def test_alive_false_after_destroy(text):
    adapter = TkTextAdapter(text)
    assert adapter.alive()
    text.destroy()
    assert not adapter.alive()
    assert adapter.get_text() == ""
