"""Testes para src/ia/approval_gate.py - funções puras e enums."""

from __future__ import annotations

import time

from src.ia.approval_gate import (
    _TOOL_RISK,
    ActionRisk,
    PendingAction,
    build_action_description,
    classify_action,
)


class TestActionRisk:
    def test_all_levels_exist(self):
        levels = {r.name for r in ActionRisk}
        assert levels == {"SAFE", "LOW", "MEDIUM", "HIGH", "CRITICAL"}

    def test_ordering_by_value(self):
        """SAFE deve ter valor menor que CRITICAL."""
        assert ActionRisk.SAFE.value < ActionRisk.CRITICAL.value

    def test_enum_members_distinct(self):
        values = [r.value for r in ActionRisk]
        assert len(values) == len(set(values))


class TestToolRiskMapping:
    def test_read_tools_are_safe(self):
        safe_tools = [
            "read_file",
            "read_active_file",
            "list_files",
            "search_in_files",
            "get_project_summary",
            "read_diagnostics",
            "get_env_info",
        ]
        for tool in safe_tools:
            assert _TOOL_RISK[tool] == ActionRisk.SAFE, f"{tool} deveria ser SAFE"

    def test_shell_commands_are_high(self):
        assert _TOOL_RISK.get("run_command") in (
            ActionRisk.HIGH, ActionRisk.CRITICAL)

    def test_git_operations_are_high(self):
        high_or_critical = {ActionRisk.HIGH, ActionRisk.CRITICAL}
        git_tools = ["git_commit", "git_push"]
        for tool in git_tools:
            if tool in _TOOL_RISK:
                assert _TOOL_RISK[tool] in high_or_critical

    def test_write_tools_are_medium_or_higher(self):
        write_tools = ["write_file", "apply_edit"]
        for tool in write_tools:
            if tool in _TOOL_RISK:
                assert _TOOL_RISK[tool].value >= ActionRisk.MEDIUM.value

    def test_all_risks_are_valid_enum(self):
        for tool, risk in _TOOL_RISK.items():
            assert isinstance(
                risk, ActionRisk), f"{tool} tem risco inválido: {risk}"


class TestClassifyAction:
    def test_safe_tool_returns_safe(self):
        assert classify_action("read_file", {}) == ActionRisk.SAFE

    def test_list_files_returns_safe(self):
        assert classify_action("list_files", {}) == ActionRisk.SAFE

    def test_unknown_tool_defaults_to_medium(self):
        assert classify_action("unknown_tool_xyz", {}) == ActionRisk.MEDIUM

    def test_run_command_dangerous_becomes_critical(self):
        dangerous_cmds = [
            "rm -rf /",
            "sudo rm -rf /home",
            "git reset --hard HEAD",
            "git clean -fd",
            "dd if=/dev/zero of=/dev/sda",
            "chmod -R 777 /",
        ]
        for cmd in dangerous_cmds:
            result = classify_action("run_command", {"command": cmd})
            assert result == ActionRisk.CRITICAL, f"'{cmd}' deveria ser CRITICAL"

    def test_run_command_safe_stays_high(self):
        result = classify_action("run_command", {"command": "ls -la"})
        assert result == ActionRisk.HIGH

    def test_run_command_echo_is_not_critical(self):
        result = classify_action("run_command", {"command": "echo hello"})
        assert result != ActionRisk.CRITICAL

    def test_delete_directory_recursive_is_critical(self):
        result = classify_action(
            "delete_directory", {"recursive": True, "path": "/tmp/test"}
        )
        assert result == ActionRisk.CRITICAL

    def test_delete_directory_non_recursive_still_critical(self):
        """delete_directory tem base CRITICAL no _TOOL_RISK, independente de recursive."""
        result = classify_action(
            "delete_directory", {"recursive": False, "path": "/tmp/test"}
        )
        assert result == ActionRisk.CRITICAL

    def test_write_file_to_sensitive_path_is_critical(self):
        sensitive_paths = [
            "/home/user/.env",
            "/etc/ssl/server.pem",
            "/home/user/.ssh/id_rsa",
            "/app/secrets.json",
            "/config/credentials.yaml",
        ]
        for path in sensitive_paths:
            result = classify_action(
                "write_file", {"path": path, "content": "data"})
            assert result == ActionRisk.CRITICAL, f"'{path}' deveria ser CRITICAL"

    def test_write_file_normal_path_is_medium(self):
        result = classify_action(
            "write_file", {"path": "/home/user/code.py", "content": "x"}
        )
        assert result == ActionRisk.MEDIUM

    def test_apply_edit_sensitive_path_is_critical(self):
        result = classify_action(
            "apply_edit", {"path": "/app/.env", "edits": []})
        assert result == ActionRisk.CRITICAL

    def test_apply_edit_normal_path_is_medium(self):
        result = classify_action(
            "apply_edit", {"path": "/src/main.py", "edits": []})
        assert result == ActionRisk.MEDIUM

    def test_patch_files_sensitive_is_critical(self):
        result = classify_action(
            "patch_files", {"path": "/app/id_rsa", "patches": []})
        assert result == ActionRisk.CRITICAL


class TestBuildActionDescription:
    def test_returns_two_strings(self):
        title, detail = build_action_description(
            "read_file", {"path": "/a/b.py"})
        assert isinstance(title, str)
        assert isinstance(detail, str)

    def test_run_command_includes_command(self):
        title, detail = build_action_description(
            "run_command", {"command": "ls -la /tmp"}
        )
        assert "ls -la /tmp" in title or "ls -la /tmp" in detail

    def test_write_file_includes_path(self):
        title, detail = build_action_description(
            "write_file", {"path": "/src/app.py", "content": "x" * 100}
        )
        assert "/src/app.py" in title or "/src/app.py" in detail

    def test_delete_file_includes_path(self):
        title, detail = build_action_description(
            "delete_file", {"path": "/tmp/junk.py"}
        )
        assert "/tmp/junk.py" in title or "/tmp/junk.py" in detail

    def test_move_file_includes_src_and_dst(self):
        title, detail = build_action_description(
            "move_file", {"src": "/a.py", "dst": "/b.py"}
        )
        combined = title + detail
        assert "/a.py" in combined or "/b.py" in combined

    def test_delete_directory_mentions_recursive(self):
        _, detail = build_action_description(
            "delete_directory", {"path": "/tmp", "recursive": True}
        )
        assert (
            "True" in detail
            or "recursivo" in detail.lower()
            or "recursive" in detail.lower()
        )

    def test_create_directory_includes_path(self):
        title, detail = build_action_description(
            "create_directory", {"path": "/new/dir"}
        )
        assert "/new/dir" in title or "/new/dir" in detail

    def test_unknown_tool_returns_strings(self):
        title, detail = build_action_description("unknown_xyz", {"x": 1})
        assert isinstance(title, str)
        assert isinstance(detail, str)

    def test_run_command_truncates_long_command(self):
        long_cmd = "echo " + "x" * 200
        title, _ = build_action_description(
            "run_command", {"command": long_cmd})
        assert len(title) < 300

    def test_init_project_includes_name(self):
        title, detail = build_action_description(
            "init_project",
            {"name": "myapp", "template": "fastapi", "path": "/projects"},
        )
        assert "myapp" in title or "myapp" in detail

    def test_install_dependency_includes_package(self):
        title, detail = build_action_description(
            "install_dependency", {"package": "requests", "manager": "pip"}
        )
        assert "requests" in title or "requests" in detail


class TestPendingAction:
    def test_creation(self):
        action = PendingAction(
            id="abc123",
            tool_name="write_file",
            args={"path": "/x.py", "content": "pass"},
            risk=ActionRisk.MEDIUM,
            description="Escrever arquivo",
        )
        assert action.id == "abc123"
        assert action.tool_name == "write_file"
        assert action.risk == ActionRisk.MEDIUM
        assert action.approved is None

    def test_timestamp_set_automatically(self):
        before = time.time()
        action = PendingAction(
            id="t", tool_name="x", args={}, risk=ActionRisk.SAFE, description="test"
        )
        after = time.time()
        assert before <= action.timestamp <= after

    def test_approved_can_be_set(self):
        action = PendingAction(
            id="a", tool_name="x", args={}, risk=ActionRisk.HIGH, description="x"
        )
        action.approved = True
        assert action.approved is True

    def test_detail_defaults_empty(self):
        action = PendingAction(
            id="a", tool_name="x", args={}, risk=ActionRisk.SAFE, description="x"
        )
        assert action.detail == ""

    def test_custom_detail(self):
        action = PendingAction(
            id="a",
            tool_name="x",
            args={},
            risk=ActionRisk.SAFE,
            description="x",
            detail="extra info",
        )
        assert action.detail == "extra info"
