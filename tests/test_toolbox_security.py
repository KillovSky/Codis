"""Testes de confinamento ao workspace das ferramentas da IA."""

from __future__ import annotations

import pytest

from src.ia.toolbox import ToolExecutor


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "src").mkdir()
    (root / "src" / "ok.txt").write_text("conteudo", encoding="utf-8")
    (root / ".env").write_text("TOKEN=secret", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("fora", encoding="utf-8")
    return root


@pytest.fixture
def tools(workspace):
    return ToolExecutor(get_editor=lambda: None, get_workspace=lambda: str(workspace))


def test_write_inside_workspace(tools, workspace):
    tools.execute("write_file", {"path": "src/new.txt", "content": "x"})
    assert (workspace / "src" / "new.txt").read_text(encoding="utf-8") == "x"


@pytest.mark.parametrize("path", ["../escaped.txt", "src/../../escaped.txt"])
def test_write_cannot_escape_with_dotdot(tools, workspace, path):
    result = tools.execute("write_file", {"path": path, "content": "x"})
    assert "fora do workspace" in result
    assert not (workspace.parent / "escaped.txt").exists()


def test_write_cannot_escape_with_absolute_path(tools, workspace):
    target = workspace.parent / "abs.txt"
    result = tools.execute("write_file", {"path": str(target), "content": "x"})
    assert "fora do workspace" in result
    assert not target.exists()


def test_write_cannot_escape_through_symlink(tools, workspace, make_symlink):
    link = workspace / "link"
    make_symlink(link, workspace.parent, directory=True)
    tools.execute("write_file", {"path": "link/viasymlink.txt", "content": "x"})
    assert not (workspace.parent / "viasymlink.txt").exists()


def test_write_refuses_sensitive_files(tools, workspace):
    result = tools.execute("write_file", {"path": ".env", "content": "x"})
    assert "não permitida" in result
    assert (workspace / ".env").read_text(encoding="utf-8") == "TOKEN=secret"


def test_read_inside_workspace(tools):
    assert "conteudo" in tools.execute("read_file", {"path": "src/ok.txt"})


@pytest.mark.parametrize("path", ["../outside.txt", ".env"])
def test_read_blocks_outside_and_secrets(tools, path):
    result = tools.execute("read_file", {"path": path})
    assert "fora" not in result and "secret" not in result


def test_read_blocks_absolute_outside(tools, workspace):
    result = tools.execute("read_file", {"path": str(workspace.parent / "outside.txt")})
    assert "fora" not in result


def test_delete_cannot_escape(tools, workspace):
    victim = workspace.parent / "outside.txt"
    tools.execute("delete_file", {"path": "../outside.txt"})
    tools.execute("delete_file", {"path": str(victim)})
    assert victim.exists()


def test_create_directory_cannot_escape(tools, workspace):
    tools.execute("create_directory", {"path": "../newdir"})
    assert not (workspace.parent / "newdir").exists()


def test_move_and_copy_cannot_escape(tools, workspace):
    tools.execute("copy_file", {"src": "src/ok.txt", "dst": "../copied.txt"})
    tools.execute("move_file", {"src": "src/ok.txt", "dst": "../moved.txt"})
    assert not (workspace.parent / "copied.txt").exists()
    assert not (workspace.parent / "moved.txt").exists()
    assert (workspace / "src" / "ok.txt").exists()


def test_run_command_working_dir_confined(tools, workspace):
    result = tools.execute("run_command", {"command": "pwd", "working_dir": ".."})
    assert "fora do workspace" in result


def test_install_dependency_rejects_option_injection(tools):
    result = tools.execute("install_dependency", {"package": "--index-url=http://evil", "manager": "pip"})
    assert "inválido" in result


def test_git_ref_validation(tools):
    for name in ("--orphan", "-f", "a b", ""):
        assert tools.execute("git_branch", {"action": "checkout", "name": name}) == "Nome de branch ou referência inválido."
    assert tools.execute("git_reset", {"mode": "hard", "target": "--force"}) == "Nome de branch ou referência inválido."


def test_init_project_sanitizes_name(tools, workspace):
    tools.execute(
        "init_project",
        {"template": "python-lib", "path": "proj", "name": 'x"; touch pwned; "', "git": False},
    )
    assert not (workspace / "proj" / "pwned").exists()
