"""Fluxos do app real com K-UI: nunca duas telas iniciais (regressão do bug).

Bug original: a WelcomeScreen clássica ficava mapeada sob a KWelcome; ao
"restaurar editor" do modo cosmos, ao fechar a última aba ou ao usar o comando
de ir à tela inicial, a tela do modo normal sobrepunha a do K-UI.
Roda o app inteiro num subprocesso com HOME isolado (não toca na config real).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def flows(tk_root):
    if not os.environ.get("DISPLAY") and sys.platform.startswith("linux"):
        pytest.skip("sem display")
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("_app_flow_runner.py")), str(ROOT)],
        capture_output=True, text=True, timeout=240, cwd=str(ROOT),
    )
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT:")), None)
    assert line, f"runner falhou:\n{proc.stdout[-800:]}\n{proc.stderr[-800:]}"
    return json.loads(line[len("RESULT:"):])


def test_enabling_kui_hides_classic_welcome(flows):
    s = flows["enabled"]
    assert s["kui_on"] and s["kwelcome"] and not s["classic"]


def test_enable_is_idempotent(flows):
    assert flows["double_enable_same_instance"] is True


def test_restore_from_cosmos_returns_to_kui_welcome(flows):
    s = flows["restored"]
    assert s["kwelcome"] and not s["classic"], "restaurar editor não pode mostrar a clássica"


def test_go_to_welcome_command_not_covered(flows):
    s = flows["command"]
    assert s["kwelcome"] and not s["classic"]


def test_closing_last_tab_shows_kui_welcome(flows):
    s = flows["closed_last"]
    assert s["kwelcome"] and not s["classic"]


def test_cosmos_mode_never_shows_classic(flows):
    assert not flows["cosmos"]["classic"]
    assert not flows["file_open"]["classic"]


def test_disabling_kui_brings_classic_back(flows):
    s = flows["disabled"]
    assert s["classic"] and not s["kwelcome"] and not s["kui_on"]


def test_never_two_sidebars(flows):
    for k in ("enabled", "cosmos", "restored", "command", "disabled"):
        assert flows[k]["sidebars"] <= 1, "a sidebar nunca pode existir em dois lugares"


def test_no_tk_errors(flows):
    assert flows["errors"] == []
