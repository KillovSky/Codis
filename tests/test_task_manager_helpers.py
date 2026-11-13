#!/usr/bin/env python3
"""Testes para src.ui.task_manager._run_scheduled_command.

``_run_scheduled_command`` sempre executa via shell, de forma consistente e
previsível, independentemente dos metacaracteres presentes no comando.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from src.ui.task_manager import _run_scheduled_command


class TestRunScheduledCommand:
    def test_always_uses_shell_true(self, monkeypatch):
        """Deve sempre passar shell=True ao subprocess.run, independente do conteúdo."""
        captured = {}

        def _fake_run(command, **kwargs):
            captured["command"] = command
            captured.update(kwargs)
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", _fake_run)
        _run_scheduled_command("echo simple")
        assert captured["shell"] is True
        assert captured["command"] == "echo simple"

    def test_shell_true_even_without_shell_metacharacters(self, monkeypatch):
        """Um comando 'simples' (sem | & ; >) também roda com shell=True."""
        captured = {}
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda command, **kw: (
                captured.update(kw) or subprocess.CompletedProcess(command, 0)
            ),
        )
        _run_scheduled_command("ls -la")
        assert captured["shell"] is True

    def test_shell_true_with_shell_metacharacters(self, monkeypatch):
        """Um comando com metacaracteres de shell também deve rodar com shell=True."""
        captured = {}
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda command, **kw: (
                captured.update(kw) or subprocess.CompletedProcess(command, 0)
            ),
        )
        _run_scheduled_command("echo a | grep a && echo done")
        assert captured["shell"] is True

    def test_passes_through_timeout(self, monkeypatch):
        """O timeout customizado deve ser repassado ao subprocess.run."""
        captured = {}
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda command, **kw: (
                captured.update(kw) or subprocess.CompletedProcess(command, 0)
            ),
        )
        _run_scheduled_command("echo hi", timeout=5)
        assert captured["timeout"] == 5

    @pytest.mark.skipif(sys.platform == "win32", reason="comando shell POSIX")
    def test_real_execution_returns_completed_process(self):
        """Execução real (sem mocks) de um comando trivial deve funcionar via shell."""
        result = _run_scheduled_command("echo hello-codis")
        assert isinstance(result, subprocess.CompletedProcess)
        assert result.returncode == 0
        assert "hello-codis" in result.stdout

    @pytest.mark.skipif(
        sys.platform == "win32", reason="comando shell POSIX com metacaractere"
    )
    def test_real_execution_supports_shell_features(self):
        """Um pipe (metacaractere de shell) deve funcionar de fato, provando shell=True real."""
        result = _run_scheduled_command("echo hello | tr a-z A-Z")
        assert result.returncode == 0
        assert "HELLO" in result.stdout
