"""Comandos de shell montados com citação correta, sem permitir injeção."""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from src.terminal.shells import (
    cd_command,
    echo_command,
    quote_arg,
    run_file_command,
    shell_kind,
)

EVIL = "/tmp/a'; touch {m}1; echo '$(touch {m}2)`touch {m}3`\"; touch {m}4; \""


@pytest.mark.parametrize(
    ("argv0", "kind"),
    [
        ("powershell.exe", "powershell"), ("C:\\Windows\\pwsh.EXE", "powershell"),
        ("cmd.exe", "cmd"), ("/usr/bin/fish", "fish"), ("bash", "posix"), ("/bin/zsh", "posix"),
        ("wsl.exe", "posix"),
    ],
)
def test_shell_kind(argv0, kind):
    assert shell_kind(argv0) == kind


def test_quoting_per_shell():
    assert quote_arg("posix", "a b") == "'a b'"
    assert quote_arg("posix", "it's") == "'it'\\''s'"
    assert quote_arg("powershell", "it's") == "'it''s'"
    assert quote_arg("powershell", "x\u2019y") == "'x\u2019\u2019y'"
    assert quote_arg("cmd", 'a"b') == '"ab"'
    assert quote_arg("fish", "it's") == "'it\\'s'"
    assert "\n" not in quote_arg("posix", "a\nb")


def test_commands_per_shell():
    assert cd_command("cmd", "C:\\a b") == 'cd /d "C:\\a b"'
    assert cd_command("powershell", "C:\\a") == "Set-Location -LiteralPath 'C:\\a'"
    assert run_file_command("posix", "python", "/x/a b.py") == "python '/x/a b.py'"
    assert run_file_command("powershell", "", "C:\\a.ps1") == "& 'C:\\a.ps1'"


def test_echo_command():
    assert echo_command("posix", "a, b") == "echo 'a, b'"
    assert echo_command("cmd", "a&b") == "echo ab"


@pytest.mark.skipif(shutil.which("sh") is None, reason="precisa de sh")
def test_hostile_values_never_execute_in_a_real_posix_shell(tmp_path):
    marker = str(tmp_path / "pwned")
    evil = EVIL.format(m=marker)
    for builder in (
        lambda: cd_command("posix", evil),
        lambda: run_file_command("posix", "true", evil),
        lambda: echo_command("posix", evil),
    ):
        subprocess.run(["sh", "-c", builder()], capture_output=True, check=False)
    assert not [f for f in os.listdir(tmp_path) if f.startswith("pwned")]
