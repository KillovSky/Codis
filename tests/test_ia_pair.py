"""Testes de src/ia/pair.py: menções, rastreio de edições, modos."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.ia import pair as P
from src.ia.toolbox import TOOL_SCHEMA


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    w = tmp_path / "proj"
    (w / "src").mkdir(parents=True)
    (w / "src" / "app.py").write_text("def main():\n    return 1\n")
    (w / ".env").write_text("SECRET=1\n")
    (tmp_path / "fora.txt").write_text("fora do workspace")
    return w


def sources(ws, **kw):
    return P.ContextSources(workspace=lambda: ws, **kw)


def test_find_mentions_keywords_paths_and_ignores_emails():
    ms = P.find_mentions("veja @src/app.py e @selection, fale com a@b.com, @tabs @foo @file:README")
    kinds = [(m.kind, m.arg) for m in ms]
    assert ("file", "src/app.py") in kinds
    assert ("selection", "") in kinds and ("tabs", "") in kinds
    assert ("file", "README") in kinds
    assert not any(m.arg == "b.com" for m in ms)
    assert not any(m.raw == "@foo" for m in ms)


def test_trailing_punctuation_stripped():
    ms = P.find_mentions("olha @src/app.py, ok? e (@git).")
    assert [m.arg or m.kind for m in ms] == ["src/app.py", "git"]


def test_file_mention_attaches_numbered_content(ws):
    r = P.resolve_mentions("explica @src/app.py", sources(ws))
    assert "def main():" in r.attachment and "   1  def main" in r.attachment
    assert r.names == ["src/app.py"] and not r.warnings
    assert r.text == "explica @src/app.py"


def test_file_mention_blocks_traversal_symlink_and_secrets(ws, tmp_path, make_symlink):
    make_symlink(ws / "link.txt", tmp_path / "fora.txt")
    for m in ("@../fora.txt", "@link.txt", "@.env", "@file:/etc/passwd"):
        r = P.resolve_mentions(f"le {m}", sources(ws))
        assert r.attachment == "" and r.warnings, m
        assert "fora do workspace" not in r.attachment and "SECRET" not in r.attachment


def test_file_mention_prefers_unsaved_buffer(ws):
    target = str((ws / "src" / "app.py").resolve())
    r = P.resolve_mentions("@src/app.py", sources(ws, open_buffers=lambda: {target: "BUFFER NAO SALVO"}))
    assert "BUFFER NAO SALVO" in r.attachment and "def main" not in r.attachment


def test_selection_active_tabs_diagnostics_git_tree(ws):
    f = ws / "src" / "app.py"
    src = sources(ws,
                  active_file=lambda: (f, "python", "linha1\nlinha2"),
                  selection=lambda: (f, "trecho", 10),
                  open_tabs=lambda: [(f, True), (None, False)],
                  diagnostics=lambda: "app.py:2 erro X",
                  git_diff=lambda: "+nova",
                  file_tree=lambda: "src/\n  app.py")
    r = P.resolve_mentions("@file @selection @tabs @diagnostics @git @tree", src)
    a = r.attachment
    assert "linha1" in a and "  10  trecho" in a
    assert "app.py  [alterações não salvas]" in a and "(sem nome)" in a
    assert "erro X" in a and "+nova" in a and "src/" in a
    assert len(r.names) == 6


def test_empty_selection_and_no_active_file_warn(ws):
    r = P.resolve_mentions("@selection @file", sources(ws))
    assert r.attachment == "" and len(r.warnings) == 2


def test_budget_truncates_big_files(ws):
    (ws / "big.py").write_text("x = 1\n" * 50000)
    r = P.resolve_mentions("@big.py", sources(ws), budget_tokens=1500)
    assert P.estimate_tokens(r.attachment) <= 1600 and "omitidos" in r.attachment


def test_too_large_file_skipped(ws):
    (ws / "huge.bin.txt").write_bytes(b"a" * (P.MAX_FILE_BYTES + 1))
    r = P.resolve_mentions("@huge.bin.txt", sources(ws))
    assert r.attachment == "" and r.warnings


def test_no_workspace_file_mentions_warn_not_crash():
    r = P.resolve_mentions("@a/b.py", P.ContextSources())
    assert r.attachment == "" and r.warnings


def test_broken_source_does_not_break_send(ws):
    def boom():
        raise RuntimeError("tk morreu")

    r = P.resolve_mentions("@diagnostics @src/app.py", sources(ws, diagnostics=boom))
    assert "def main" in r.attachment and any("diagnostics" in w for w in r.warnings)


def test_edit_tracker_reports_only_user_changes():
    t = P.EditTracker()
    t.snapshot({"/p/a.py": "a\nb\nc\n", "/p/b.py": "same\n"})
    out = t.changes_since({"/p/a.py": "a\nB!\nc\n", "/p/b.py": "same\n", "/p/new.py": "x"})
    assert "a.py" in out and "-b" in out and "+B!" in out
    assert "b.py" not in out and "new.py" not in out


def test_edit_tracker_empty_when_nothing_changed_and_after_resnapshot():
    t = P.EditTracker()
    t.snapshot({"/p/a.py": "1"})
    assert t.changes_since({"/p/a.py": "1"}) == ""
    t.snapshot({"/p/a.py": "2"})
    assert t.changes_since({"/p/a.py": "2"}) == ""


def test_edit_tracker_truncates_huge_diff():
    t = P.EditTracker()
    t.snapshot({"/p/a.py": "\n".join(str(i) for i in range(5000))})
    out = t.changes_since({"/p/a.py": "\n".join(f"n{i}" for i in range(5000))}, max_chars=2000)
    assert len(out) < 2600 and "truncado" in out


def names(mode):
    return {P.tool_name(t) for t in P.tools_for_mode(mode, TOOL_SCHEMA)}


def test_ask_mode_is_read_only():
    n = names("ask")
    assert {"read_file", "search_in_files"} <= n
    assert not n & {"write_file", "apply_edit", "run_command", "delete_file", "git_commit", "move_file"}


def test_edit_mode_can_edit_but_not_shell_or_delete():
    n = names("edit")
    assert {"apply_edit", "write_file", "patch_files", "run_tests", "read_file"} <= n
    assert not n & {"run_command", "delete_file", "delete_directory", "git_reset", "git_commit",
                    "install_dependency", "move_file"}


def test_agent_mode_has_every_tool():
    assert names("agent") == {P.tool_name(t) for t in TOOL_SCHEMA}


def test_system_prompt_composition_order():
    s = P.build_system_prompt("edit", user_system="Use tabs.", skill_prompt="Foque em testes.",
                              project_context="## Projeto\nX")
    assert s.index("Mode: EDIT") < s.index("User instructions") < s.index("Task guidance") < s.index("## Projeto")
    assert "Mode: ASK" in P.build_system_prompt("???")
