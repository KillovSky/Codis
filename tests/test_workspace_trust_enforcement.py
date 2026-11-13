"""Garante que o modo restrito bloqueia as ferramentas de IA que executam código."""

from __future__ import annotations

import pytest

from src import workspace_state
from src.ia.toolbox import ToolExecutor


@pytest.fixture(autouse=True)
def _reset_state():
    workspace_state.set_restricted(False)
    yield
    workspace_state.set_restricted(False)


@pytest.fixture
def tools(tmp_path):
    return ToolExecutor(get_editor=lambda: None, get_workspace=lambda: str(tmp_path))


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("run_command", {"command": "echo hi"}),
        ("run_script", {"code": "print(1)"}),
        ("install_dependency", {"package": "requests"}),
        ("init_project", {"template": "vazio"}),
    ],
)
def test_execution_tools_blocked_when_restricted(tools, tool, args):
    workspace_state.set_restricted(True)
    assert "modo restrito" in tools.execute(tool, args)


def test_execution_tools_allowed_when_trusted(tools):
    result = tools.execute("run_command", {"command": "echo hi"})
    assert "modo restrito" not in result and "hi" in result


def test_read_tools_still_work_when_restricted(tools, tmp_path):
    (tmp_path / "a.txt").write_text("ok", encoding="utf-8")
    workspace_state.set_restricted(True)
    assert "ok" in tools.execute("read_file", {"path": "a.txt"})
