"""Popup de autocomplete com o novo motor (Tk real)."""

from __future__ import annotations

import tkinter as tk

import pytest

from src.editor.autocomplete import AutocompletePopup
from src.editor.suggest import SuggestionEngine

DOC = '''import os

class UserService:
    def __init__(self, repo):
        self.repo = repo
        self.cache_ttl = 60

    def get_user_by_id(self, uid):
        return self.repo.find(uid)

def compute_total(items):
    total = 0
    return total

'''


@pytest.fixture
def ac(tk_root, monkeypatch):
    from src.theme.base import _define_themes
    from src.theme.manager import ThemeManager

    _define_themes()
    monkeypatch.setattr(SuggestionEngine, "_shared_mru", {})
    monkeypatch.setattr(SuggestionEngine, "_clock", 0)
    frame = tk.Frame(tk_root)
    frame.pack(fill="both", expand=True)
    txt = tk.Text(frame, width=80, height=24)
    txt.pack(fill="both", expand=True)
    txt.insert("1.0", DOC)
    tk_root.update()
    pop = AutocompletePopup(tk_root, txt, ThemeManager("fluent_dark"))
    pop.set_lang("python")
    yield pop, txt
    pop.hide()
    frame.destroy()
    tk_root.update()


def type_at_end(txt, s):
    txt.mark_set("insert", "end-1c")
    txt.insert("insert", s)


def items(pop):
    return [i[1] for i in pop._items]


def test_defined_function_ranks_first_and_popup_shows(tk_root, ac):
    pop, txt = ac
    type_at_end(txt, "x = comp")
    pop._compute()
    tk_root.update()
    assert pop.is_visible()
    assert items(pop)[0] == "compute_total"


def test_dot_after_self_lists_members_not_keywords(tk_root, ac):
    pop, txt = ac
    txt.mark_set("insert", "end-1c")
    txt.insert("end-1c", "    def m(self):\n        self.")
    pop._compute()
    tk_root.update()
    got = items(pop)
    assert pop.is_visible()
    assert {"repo", "cache_ttl", "get_user_by_id"} <= set(got[:5])
    assert "return" not in got and "def" not in got


def test_comment_and_string_do_not_open_popup(tk_root, ac):
    pop, txt = ac
    type_at_end(txt, "x = 1  # comp")
    pop._compute()
    tk_root.update()
    assert not pop.is_visible()
    type_at_end(txt, "\ny = 'comp")
    pop._compute()
    tk_root.update()
    assert not pop.is_visible()


def test_def_name_position_is_silent(tk_root, ac):
    pop, txt = ac
    type_at_end(txt, "def comp")
    pop._compute()
    tk_root.update()
    assert not pop.is_visible()


def test_accept_replaces_prefix_and_learns(tk_root, ac):
    pop, txt = ac
    txt.insert("end-1c", "alpha_one = 1\nalpha_two = 2\n")
    type_at_end(txt, "z = alpha_")
    pop._compute()
    tk_root.update()
    assert pop.is_visible()
    first, second = items(pop)[0], items(pop)[1]
    pop._listbox.selection_clear(0, "end")
    pop._listbox.selection_set(1)
    assert pop.accept()
    assert txt.get("end-1c linestart", "end-1c") == f"z = {second}"
    txt.insert("end-1c", "\nw = alpha_")
    pop._compute()
    tk_root.update()
    assert items(pop)[0] == second and first in items(pop)


def test_fuzzy_initials_match(tk_root, ac):
    pop, txt = ac
    type_at_end(txt, "v = gubi")
    pop._compute()
    tk_root.update()
    assert "get_user_by_id" in items(pop)


def test_lsp_items_merge_into_ranking_and_stale_response_is_ignored(tk_root, ac):
    from types import SimpleNamespace

    pop, txt = ac
    calls = []

    class FakeLsp:
        def completion(self, path, lang, line, col, ctx, cb):
            calls.append(cb)

    pop.set_lsp_manager(FakeLsp(), "/x.py")
    type_at_end(txt, "q = pri")
    pop._compute()
    tk_root.update()
    ci = SimpleNamespace(label="print", insert_text="print($1)", kind_name="Function", icon="ƒ")
    calls[-1]([ci])
    tk_root.update()
    assert items(pop)[0] == "print()"
    type_at_end(txt, "nt_")
    before = list(pop._items)
    calls[-1]([SimpleNamespace(label="OUTRO", insert_text="OUTRO", kind_name="Class", icon="c")])
    tk_root.update()
    assert pop._items == before


def test_empty_prefix_without_dot_stays_closed(tk_root, ac):
    pop, txt = ac
    type_at_end(txt, "x = ")
    pop._compute()
    tk_root.update()
    assert not pop.is_visible()
