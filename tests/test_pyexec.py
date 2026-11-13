"""Testes da proteção contra auto-relançamento do Codis compilado (src/pyexec.py)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from src import pyexec


@pytest.fixture
def frozen(monkeypatch, tmp_path):
    """Simula o Codis compilado: sys.frozen e sys.executable = 'Codis.exe' falso."""
    fake_exe = tmp_path / "Codis"
    fake_exe.write_text("#!/bin/sh\necho GUI-STARTED >> \"$CODIS_FAKE_LOG\"\n")
    fake_exe.chmod(0o755)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe))
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "bundle"), raising=False)
    monkeypatch.delenv(pyexec.ENV_DEPTH, raising=False)
    monkeypatch.delenv(pyexec.ENV_PYTHON, raising=False)
    pyexec._system_python.cache_clear()
    yield fake_exe
    pyexec._system_python.cache_clear()


REAL_PYTHON = Path(sys.executable).resolve()


def _use_real_python(monkeypatch):
    """Faz o 'sistema' ter só o Python real do teste (o exe falso fica de fora)."""
    monkeypatch.setattr(pyexec, "_candidates", lambda: [[str(REAL_PYTHON)]])
    pyexec._system_python.cache_clear()


def test_not_frozen_uses_sys_executable():
    assert not pyexec.is_frozen()
    assert pyexec.python_executable() == sys.executable
    assert pyexec.python_cmd("-m", "pip")[1:] == ["-m", "pip"]


def test_frozen_never_returns_codis_itself(frozen, monkeypatch):
    _use_real_python(monkeypatch)
    py = pyexec.python_executable()
    assert Path(py).resolve() == REAL_PYTHON
    assert not pyexec.is_self(py)
    assert pyexec.python_cmd("-m", "venv", "x") == [py, "-m", "venv", "x"]


def test_frozen_without_python_raises_actionable_error(frozen, monkeypatch):
    monkeypatch.setattr(pyexec, "_candidates", lambda: [])
    pyexec._system_python.cache_clear()
    with pytest.raises(pyexec.NoPythonError, match=pyexec.ENV_PYTHON):
        pyexec.python_executable()


def test_codis_itself_is_rejected_even_if_listed_as_candidate(frozen, monkeypatch):
    monkeypatch.setattr(pyexec, "_candidates", lambda: [[str(frozen)]])
    pyexec._system_python.cache_clear()
    with pytest.raises(pyexec.NoPythonError):
        pyexec.python_executable()


def test_preferred_venv_python_is_used_but_codis_is_refused(frozen):
    assert pyexec.python_executable("/some/venv/bin/python") == "/some/venv/bin/python"
    with pytest.raises(pyexec.NoPythonError):
        pyexec.python_executable(str(frozen))


def test_probe_rejects_non_python_and_old_versions(monkeypatch, tmp_path):
    liar = tmp_path / "liar"
    liar.write_text("#!/bin/sh\necho hello\n")
    liar.chmod(0o755)
    assert pyexec._probe([str(liar)]) is None
    assert pyexec._probe([str(tmp_path / "nao-existe")]) is None
    monkeypatch.setattr(pyexec, "MIN_PYTHON", (99, 0))
    assert pyexec._probe([str(REAL_PYTHON)]) is None


def test_env_override_wins(frozen, monkeypatch):
    monkeypatch.setenv(pyexec.ENV_PYTHON, str(REAL_PYTHON))
    pyexec._system_python.cache_clear()
    assert Path(pyexec._candidates()[0][0]) == REAL_PYTHON


def test_child_env_increments_depth(monkeypatch):
    monkeypatch.delenv(pyexec.ENV_DEPTH, raising=False)
    assert pyexec.child_env()[pyexec.ENV_DEPTH] == "1"
    assert pyexec.child_env({pyexec.ENV_DEPTH: "2"})[pyexec.ENV_DEPTH] == "3"
    assert pyexec.current_depth({pyexec.ENV_DEPTH: "lixo"}) == 0


def test_child_env_strips_bundle_vars_only_when_frozen(frozen, tmp_path):
    bundle = tmp_path / "bundle"
    base = {
        "TCL_LIBRARY": str(bundle / "tcl8.6"),
        "TK_LIBRARY": "/usr/share/tk8.6",
        "LD_LIBRARY_PATH": str(bundle),
        "LD_LIBRARY_PATH_ORIG": "/opt/lib",
        "_MEIPASS2": str(bundle),
        "PATH": "/usr/bin",
    }
    env = pyexec.child_env(base)
    assert "TCL_LIBRARY" not in env and "_MEIPASS2" not in env
    assert env["TK_LIBRARY"] == "/usr/share/tk8.6"
    assert env["LD_LIBRARY_PATH"] == "/opt/lib" and "LD_LIBRARY_PATH_ORIG" not in env
    assert env["PATH"] == "/usr/bin"


def test_child_env_without_original_drops_bundle_ld_path(frozen, tmp_path):
    env = pyexec.child_env({"LD_LIBRARY_PATH": str(tmp_path / "bundle")})
    assert "LD_LIBRARY_PATH" not in env


def test_terminal_child_env_also_cleans_bundle_tcl(frozen, tmp_path):
    from src.terminal.backends import child_process_env

    env = child_process_env({"TCL_LIBRARY": str(tmp_path / "bundle" / "tcl"), "HOME": "/h"})
    assert "TCL_LIBRARY" not in env and env["HOME"] == "/h"


def test_restart_and_new_window_commands(frozen, monkeypatch):
    monkeypatch.setattr(sys, "argv", [str(frozen), "/proj/a.py"])
    assert pyexec.restart_command() == [str(frozen), "/proj/a.py"], "não pode duplicar o exe"
    assert pyexec.new_window_command() == [str(frozen)]


def test_restart_and_new_window_commands_from_source(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["run.py", "x.py"])
    assert pyexec.restart_command() == [sys.executable, "run.py", "x.py"]
    assert pyexec.new_window_command() == [sys.executable, "-m", "src"]


def test_script_command_falls_back_to_codis_c_and_the_snippet_really_works(
    frozen, monkeypatch, tmp_path
):
    monkeypatch.setattr(pyexec, "_candidates", lambda: [])
    pyexec._system_python.cache_clear()
    script = tmp_path / "helper.py"
    script.write_text("import sys; print('ARGS', sys.argv[1:])\n")
    cmd = pyexec.script_command(script)
    assert cmd[:2] == [str(frozen), "-c"]
    real = [str(REAL_PYTHON), "-c", pyexec._RUN_SCRIPT, str(script), "pedido"]
    out = subprocess.run(real, capture_output=True, text=True, check=True).stdout
    assert "ARGS ['pedido']" in out


def test_guard_aborts_when_depth_exceeded(frozen, monkeypatch, capsys):
    monkeypatch.setenv(pyexec.ENV_DEPTH, str(pyexec.MAX_DEPTH))
    with pytest.raises(SystemExit) as exc:
        pyexec.guard_startup(["Codis"])
    assert exc.value.code == 3
    assert "recursão" in capsys.readouterr().err


def test_guard_allows_normal_launch_and_file_argument(frozen):
    pyexec.guard_startup(["Codis"])
    pyexec.guard_startup(["Codis", "/proj/arquivo.py"])
    pyexec.guard_startup(["Codis", "--player"])


def test_frozen_codis_acts_as_python_dash_c(frozen, capsys):
    with pytest.raises(SystemExit) as exc:
        pyexec.guard_startup(["Codis", "-c", "print(40 + 2)"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == "42"


def test_frozen_codis_dash_m_runs_bundled_module(frozen, capsys):
    with pytest.raises(SystemExit) as exc:
        pyexec.guard_startup(["Codis", "-m", "platform"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip()


def test_frozen_codis_dash_m_missing_module_fails_instead_of_opening_gui(frozen, capsys):
    """O caso do bug: `Codis -m pip` não pode abrir outra janela do Codis."""
    with pytest.raises(SystemExit) as exc:
        pyexec.guard_startup(["Codis", "-m", "modulo_que_nao_existe_xyz", "list"])
    assert exc.value.code == 1
    assert "No module named" in capsys.readouterr().err


def test_frozen_codis_version_and_skip_flags(frozen, capsys):
    with pytest.raises(SystemExit) as exc:
        pyexec.guard_startup(["Codis", "-I", "-m", "modulo_que_nao_existe_xyz"])
    assert exc.value.code == 1
    with pytest.raises(SystemExit) as exc:
        pyexec.guard_startup(["Codis", "--version"])
    assert exc.value.code == 0 and "Python" in capsys.readouterr().out


def test_not_frozen_never_emulates_interpreter():
    pyexec.guard_startup(["python", "-m", "pip"])


def test_pip_manager_never_builds_command_with_codis_exe(frozen, monkeypatch):
    from src.ui import pip_manager as pm

    _use_real_python(monkeypatch)
    cmd = pm._pip()
    assert Path(cmd[0]).resolve() == REAL_PYTHON and cmd[1:] == ["-m", "pip"]
    assert str(frozen) not in cmd


def test_pip_manager_reports_missing_python_instead_of_empty_list(frozen, monkeypatch):
    from src.ui import pip_manager as pm

    monkeypatch.setattr(pyexec, "_candidates", lambda: [])
    pyexec._system_python.cache_clear()
    with pytest.raises(pyexec.NoPythonError):
        pm._list_installed()
    with pytest.raises(pyexec.NoPythonError):
        pm._list_outdated()
    with pytest.raises(pyexec.NoPythonError):
        pm._show_info("x")


def test_pip_manager_run_pip_reports_error_in_log_queue(frozen, monkeypatch):
    import queue

    from src.ui import pip_manager as pm

    monkeypatch.setattr(pyexec, "_candidates", lambda: [])
    pyexec._system_python.cache_clear()
    q: queue.Queue = queue.Queue()
    assert pm._run_pip(["list"], q) == -1
    first = q.get_nowait()
    assert "Python" in first and q.get_nowait() is None


def test_pip_subprocess_kwargs_carry_clean_env(frozen):
    from src.ui import pip_manager as pm

    env = pm._make_popen_kwargs()["env"]
    assert env[pyexec.ENV_DEPTH] == "1"


def test_pip_manager_pip_list_really_runs_with_real_python(frozen, monkeypatch):
    from src.ui import pip_manager as pm

    _use_real_python(monkeypatch)
    pkgs = pm._list_installed()
    assert isinstance(pkgs, list)


def test_code_runner_resolves_python_at_run_time(frozen, monkeypatch, tmp_path):
    from src.ui import code_runner as cr

    _use_real_python(monkeypatch)
    assert Path(cr._resolve_cmd("{python}")).resolve() == REAL_PYTHON
    assert cr._resolve_cmd("node") == "node"
    monkeypatch.setattr(pyexec, "_candidates", lambda: [])
    pyexec._system_python.cache_clear()
    assert cr._resolve_cmd("{python}") == ""


def test_lsp_finds_module_in_system_python_not_only_bundle(frozen, monkeypatch):
    from src.lsp import client

    _use_real_python(monkeypatch)
    client._python_with_module.cache_clear()
    assert Path(client._python_with_module("json")).resolve() == REAL_PYTHON
    assert client._python_with_module("modulo_que_nao_existe_xyz") is None
    monkeypatch.setattr(pyexec, "_candidates", lambda: [])
    pyexec._system_python.cache_clear()
    client._python_with_module.cache_clear()
    assert client._python_with_module("json") is None
    client._python_with_module.cache_clear()
