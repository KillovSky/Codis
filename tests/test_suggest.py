"""Testes do motor de sugestões (src/editor/suggest.py)."""

from __future__ import annotations

import time

import pytest

from src.editor import suggest as S

PY_DOC = '''import os
import sys

MAX_RETRIES = 3

class UserService:
    def __init__(self, repo):
        self.repo = repo
        self.cache_ttl = 60

    def get_user_by_id(self, uid):
        return self.repo.find(uid)

    def get_file_name(self):
        return self.cache_ttl

def compute_total(items):
    total = 0
    return total
'''


def ctx_of(before: str, lang: str = "python") -> S.LineContext:
    return S.analyze(before, lang)


def engine(doc: str = PY_DOC, lang: str = "python", line: int = 1) -> S.SuggestionEngine:
    e = S.SuggestionEngine()
    e.update_document(doc, lang, line)
    return e


def inserts(cands):
    return [c.insert for c in cands]


@pytest.mark.parametrize("before,state", [
    ("x = 1  # coment", "comment"),
    ("x = 'abc", "string"),
    ('x = "abc', "string"),
    ('x = "a" + y', "code"),
    ('"""doc\nlinha 2', "string"),
    ('"""doc"""\nx = ', "code"),
    ("# so comentario\ny = fo", "code"),
    ('s = "it\\"s ok', "string"),
    ("x = f'valor {abc", "code"),
    ("x = f'valor {abc} resto", "string"),
    ("x = f'{{literal", "string"),
    ("url = 'http://x.com'  # c", "comment"),
    ("s = 'a # nao e comentario", "string"),
])
def test_python_scan_state(before, state):
    assert S.scan_state(before, "python") == state


@pytest.mark.parametrize("before,state", [
    ("const a = 1; // nota", "comment"),
    ("/* bloco\nainda", "comment"),
    ("/* fim */ const b = ", "code"),
    ("const s = `texto ${obj", "code"),
    ("const s = `texto ${obj} fim", "string"),
    ("const s = 'x // nao", "string"),
])
def test_js_scan_state(before, state):
    assert S.scan_state(before, "javascript") == state


def test_text_langs_never_suppressed():
    assert S.scan_state("isto # nao e comentario", "markdown") == "code"


def test_analyze_fields():
    c = ctx_of("    x = get_us")
    assert c.prefix == "get_us" and c.after_def is False
    assert ctx_of("class Fo").after_def and ctx_of("def na").after_def
    assert ctx_of("    ret").statement_start
    assert not ctx_of("x = ret").statement_start
    m = ctx_of("self.repo.fi")
    assert m.member and m.qualifier == "self.repo" and m.prefix == "fi"
    assert ctx_of("x = obj.").member and ctx_of("x = obj.").prefix == ""
    assert ctx_of("from os import pa").in_import and ctx_of("import sy").in_import
    assert not ctx_of("x = import_thing").in_import


def test_suppressed_contexts_return_nothing():
    e = engine()
    assert e.suggest(ctx_of("x = 1  # usr"), "python") == []
    assert e.suggest(ctx_of("x = 'usr"), "python") == []
    assert e.suggest(ctx_of("def usr"), "python") == []


def test_match_score_ordering():
    prefix = S.match_score("get", "get_user")
    initials = S.match_score("gu", "get_user")
    subseq = S.match_score("gtusr", "get_user")
    assert prefix > initials > subseq > 0
    assert S.match_score("get", "get") is None
    assert S.match_score("zz", "get_user") is None
    assert S.match_score("GET", "getUser") > 0
    assert S.match_score("get", "getUser") > S.match_score("Get", "getUser")


def test_camel_and_snake_initials():
    assert S.match_score("gfn", "get_file_name") > 0.7
    assert S.match_score("gfn", "getFileName") > 0.7
    assert S.match_score("gfn", "HTMLParser") is None
    assert S.split_words("getHTTPResponse2") == ["get", "HTTP", "Response", "2"]


def test_shorter_wins_ties():
    e = engine("alpha_one = 1\nalpha_one_two_three = 2\nalpha_x = 3\n")
    out = inserts(e.suggest(ctx_of("al"), "python"))
    assert out.index("alpha_x") < out.index("alpha_one_two_three")


def test_defined_symbols_outrank_plain_words_and_keywords():
    e = engine()
    out = e.suggest(ctx_of("x = comp"), "python", keywords=["compile"])
    assert out[0].insert == "compute_total" and out[0].kind == "function"


def test_function_vs_class_vs_constant_kinds():
    e = engine()
    kinds = {c.insert: c.kind for c in e.suggest(ctx_of("x = "), "python") or []}
    assert kinds == {}
    kinds = {c.insert: c.kind for c in e.suggest(ctx_of("x = M"), "python")}
    assert kinds["MAX_RETRIES"] == "constant"
    assert {c.insert: c.kind for c in e.suggest(ctx_of("x = User"), "python")}["UserService"] == "class"


def test_statement_start_prefers_statement_keywords_and_snippets():
    e = engine("retry_count = 1\n")
    at_start = e.suggest(ctx_of("    re"), "python", keywords=["return", "re", "raise"], snippets=[("req", "request")])
    mid = e.suggest(ctx_of("x = re"), "python", keywords=["return", "re", "raise"], snippets=[("req", "request")])
    assert at_start[0].insert in ("return", "req")
    assert mid[0].insert != "return"


def test_proximity_prefers_recent_words():
    doc = "\n".join(["far_value = 1"] + ["pass"] * 300 + ["near_value = 2"])
    e = S.SuggestionEngine()
    e.update_document(doc, "text", 302)
    out = inserts(e.suggest(S.LineContext(prefix="n"), "text", cursor_line=302))
    assert out and "near_value" in out
    e2 = S.SuggestionEngine()
    both = "value_far = 1\n" + "\n" * 400 + "value_near = 1\n"
    e2.update_document(both, "text", 403)
    o = inserts(e2.suggest(S.LineContext(prefix="val"), "text", cursor_line=403))
    assert o.index("value_near") < o.index("value_far")


def test_frequency_boosts_common_words():
    doc = "thing_rare = 1\n" + "thing_common = 2\n" * 10
    e = engine(doc, "text")
    out = inserts(e.suggest(S.LineContext(prefix="thing_"), "text", cursor_line=6))
    assert out[:2] == ["thing_common", "thing_rare"]


def test_lsp_items_get_priority_and_dedupe():
    e = engine("print_value = 1\n")
    out = e.suggest(ctx_of("x = pri"), "python", lsp_items=[("print", "print", "function", "ƒ"),
                                                              ("print_value", "print_value", "variable", "x")])
    assert out[0].source == "lsp" and out[0].insert == "print"
    assert inserts(out).count("print_value") == 1


def test_deterministic_across_runs_and_hash_seeds():
    doc = "\n".join(f"name_{i} = {i}" for i in range(60))
    a = inserts(engine(doc, "text").suggest(S.LineContext(prefix="na"), "text"))
    b = inserts(engine(doc, "text").suggest(S.LineContext(prefix="na"), "text"))
    assert a == b and len(a) == 24


def test_self_members_from_class():
    e = engine()
    out = inserts(e.suggest(ctx_of("        self."), "python"))
    assert {"repo", "cache_ttl", "get_user_by_id", "get_file_name"} <= set(out[:6])


def test_member_prefix_filter_and_other_qualifier():
    e = engine()
    assert inserts(e.suggest(ctx_of("        self.ca"), "python"))[0] == "cache_ttl"
    out = inserts(e.suggest(ctx_of("x = self.repo."), "python"))
    assert out[0] == "find"


def test_member_context_excludes_keywords_and_snippets():
    e = engine()
    out = e.suggest(ctx_of("obj.re"), "python", keywords=["return"], snippets=[("req", "")])
    assert "return" not in inserts(out) and "req" not in inserts(out)


def test_this_falls_back_to_self_attrs_for_js_classes():
    doc = "class A {\n  constructor(){ this.items = []; this.total = 0 }\n}\n"
    e = S.SuggestionEngine()
    e.update_document(doc, "javascript")
    out = inserts(e.suggest(S.analyze("this.it", "javascript"), "javascript"))
    assert out and out[0] == "items"


def test_python_import_suggests_stdlib_modules():
    e = engine()
    out = e.suggest(ctx_of("import jso"), "python")
    assert out[0].insert == "json" and out[0].kind == "module"
    assert ctx_of("from pathl").in_import
    assert "pathlib" in inserts(e.suggest(ctx_of("from pathl"), "python"))


def test_from_import_suggests_names_inside_the_module_not_modules():
    import os  # noqa: F401

    e = engine()
    c = ctx_of("from os import pa")
    assert c.import_from == "os"
    out = inserts(e.suggest(c, "python"))
    assert "path" in out and "pathlib" not in out
    import sys

    assert S._loaded_stdlib_names("antigravity", "python") == []
    assert "antigravity" not in sys.modules
    assert S._loaded_stdlib_names("totally_not_stdlib", "python") == []
    assert S._loaded_stdlib_names("os", "javascript") == []


def test_import_context_ignores_snippets_and_keywords():
    e = engine()
    out = e.suggest(ctx_of("import jso"), "python", keywords=["json_kw"], snippets=[("json_snip", "")])
    assert "json_kw" not in inserts(out) and "json_snip" not in inserts(out)


def test_mru_learns_what_you_accept(monkeypatch):
    monkeypatch.setattr(S.SuggestionEngine, "_shared_mru", {})
    monkeypatch.setattr(S.SuggestionEngine, "_clock", 0)
    e = engine("alpha_beta = 1\nalpha_gamma = 2\n", "text")
    before = inserts(e.suggest(S.LineContext(prefix="alpha_"), "text"))
    first, second = before[0], before[1]
    S.SuggestionEngine.record_accept(second)
    after = inserts(e.suggest(S.LineContext(prefix="alpha_"), "text"))
    assert after[0] == second and first in after


def test_mru_is_bounded(monkeypatch):
    monkeypatch.setattr(S.SuggestionEngine, "_shared_mru", {})
    monkeypatch.setattr(S.SuggestionEngine, "_clock", 0)
    for i in range(700):
        S.SuggestionEngine.record_accept(f"w{i}")
    assert len(S.SuggestionEngine._shared_mru) <= 500
    assert "w699" in S.SuggestionEngine._shared_mru


def test_update_document_caches_by_content():
    e = S.SuggestionEngine()
    e.update_document("aaa_bbb = 1", "text")
    idx1 = e.index
    e.update_document("aaa_bbb = 1", "text")
    assert e.index is idx1
    e.update_document("aaa_bbb = 2", "text")
    assert e.index is not idx1


def test_huge_file_is_windowed_and_fast():
    lines = [f"var_{i} = {i}" for i in range(80_000)]
    text = "\n".join(lines)
    assert len(text) > S.MAX_INDEX_CHARS
    e = S.SuggestionEngine()
    t0 = time.perf_counter()
    e.update_document(text, "python", cursor_line=40_000)
    out = e.suggest(S.LineContext(prefix="var_399"), "python", cursor_line=40_000)
    dt = time.perf_counter() - t0
    assert dt < 1.5, dt
    assert out and all(c.insert.startswith("var_399") for c in out[:3])
    assert "var_5" not in e.index.words


def test_extract_symbols_python_avoids_comparisons():
    syms = S.extract_symbols("count = 1\nif yy == 2:\n    pass\nzed: int = 3\nclass Kk: pass\nasync def go(): pass\n", "python")
    assert "yy" not in syms and syms["count"] == "variable" and syms["zed"] == "variable"
    assert syms["Kk"] == "class" and syms["go"] == "function"


def test_extract_symbols_other_languages():
    assert S.extract_symbols("func (s *Srv) Handle() {}\ntype Conf struct{}", "go") == {"Handle": "function", "Conf": "class"}
    assert S.extract_symbols("fn main() {}\nstruct Pt;", "rust") == {"main": "function", "Pt": "class"}
    js = S.extract_symbols("const add = (a,b) => a+b\nclass Box {}\nconst MAX_SIZE = 3", "javascript")
    assert js["add"] == "function" and js["Box"] == "class" and js["MAX_SIZE"] == "constant"


def test_scan_state_performance_on_big_prefix():
    text = ("x = 'abc'  # c\n" * 20000) + "y = fo"
    t0 = time.perf_counter()
    assert S.analyze(text, "python").state == "code"
    assert time.perf_counter() - t0 < 1.0
