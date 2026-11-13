"""Codis - interpretador Python para subprocessos e proteção contra auto-relançamento.

Problema que isto resolve
-------------------------
Em um executável compilado (PyInstaller/Nuitka) ``sys.executable`` é o **próprio
Codis**, não um Python. Código como ``[sys.executable, "-m", "pip", ...]`` ou
``[sys.executable, "-m", "venv", ...]`` então abre outra janela do Codis, que
repete a operação e assim por diante: um processo infinito (fork bomb).

Defesa em camadas
-----------------
1. :func:`python_executable` / :func:`python_cmd` devolvem um Python **real**
   (validado por execução, nunca o próprio Codis) ou levantam
   :class:`NoPythonError` com uma mensagem acionável.
2. :func:`child_env` limpa o ambiente herdado do bundle (``LD_LIBRARY_PATH``,
   ``TCL_LIBRARY``...) que quebraria um Python do sistema e marca a
   profundidade de spawn.
3. :func:`guard_startup`, chamado na entrada do app: ``freeze_support`` para
   ``multiprocessing``; recusa iniciar a interface quando a profundidade de
   spawn estoura; e, se o executável for chamado como interpretador
   (``Codis -m modulo`` / ``Codis -c codigo``), comporta-se como tal em vez de
   abrir a interface.
"""

from __future__ import annotations

import functools
import glob
import multiprocessing
import os
import runpy
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

ENV_DEPTH = "CODIS_SPAWN_DEPTH"
ENV_PYTHON = "CODIS_PYTHON"
MAX_DEPTH = 4
MIN_PYTHON = (3, 12)

_BUNDLE_PATH_VARS = ("TCL_LIBRARY", "TK_LIBRARY", "PYTHONHOME", "PYTHONPATH")
_INTERPRETER_FLAGS = {"-m", "-c", "-V", "--version"}
_SKIP_FLAGS = {"-I", "-E", "-S", "-u", "-B", "-s", "-O", "-OO", "-q"}
_PROBE_CODE = "import sys;print(sys.version_info[0],sys.version_info[1]);print(sys.executable)"


class NoPythonError(RuntimeError):
    """Nenhum interpretador Python utilizável foi encontrado."""


def is_frozen() -> bool:
    """``True`` quando o Codis roda como executável compilado."""
    return bool(getattr(sys, "frozen", False)) or "__compiled__" in globals()


def _samefile(a: str, b: str) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def is_self(path: str) -> bool:
    """``True`` se ``path`` é o próprio executável do Codis."""
    return _samefile(path, sys.executable)


def current_depth(env: Mapping[str, str] | None = None) -> int:
    """Profundidade de spawn atual (quantos Codis aninhados nos criaram)."""
    raw = (env if env is not None else os.environ).get(ENV_DEPTH, "0")
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def _under_bundle(value: str) -> bool:
    meipass = getattr(sys, "_MEIPASS", None)
    if not meipass or not value:
        return False
    root = os.path.normcase(os.path.realpath(meipass))
    return any(
        os.path.normcase(os.path.realpath(part)).startswith(root)
        for part in value.split(os.pathsep)
        if part
    )


def strip_bundle_env(env: dict[str, str]) -> dict[str, str]:
    """Remove de ``env`` o que aponta para o bundle do PyInstaller (in-place).

    Num programa congelado, ``LD_LIBRARY_PATH`` aponta para as bibliotecas
    embutidas e ``TCL_LIBRARY``/``TK_LIBRARY`` para os dados Tcl/Tk do bundle;
    um Python/pip/servidor do sistema herdando isso falha (OpenSSL, tkinter).
    Valores que o usuário definiu por conta própria são preservados.
    """
    if not is_frozen():
        return env
    env.pop("_MEIPASS2", None)
    for var in _BUNDLE_PATH_VARS:
        if _under_bundle(env.get(var, "")):
            env.pop(var)
    for var in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "LIBPATH"):
        orig = env.pop(f"{var}_ORIG", None)
        if orig is not None:
            env[var] = orig
        else:
            env.pop(var, None)
    return env


def child_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Ambiente para subprocessos Python: limpo do bundle e com profundidade +1."""
    env = dict(base if base is not None else os.environ)
    env[ENV_DEPTH] = str(current_depth(env) + 1)
    return strip_bundle_env(env)


def _fix_pythonw(path: str) -> str:
    """No Windows troca ``pythonw.exe`` por ``python.exe`` (saída capturável)."""
    if sys.platform == "win32" and path.lower().endswith("pythonw.exe"):
        candidate = path[: -len("pythonw.exe")] + "python.exe"
        if Path(candidate).exists():
            return candidate
    return path


def _probe(cmd: Sequence[str]) -> str | None:
    """Executa ``cmd`` e devolve o ``sys.executable`` real se for um Python válido."""
    env = child_env()
    env[ENV_DEPTH] = str(MAX_DEPTH)
    try:
        r = subprocess.run(  # noqa: S603
            [*cmd, "-c", _PROBE_CODE],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            stdin=subprocess.DEVNULL,
            env=env,
            **_hidden_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    lines = r.stdout.strip().splitlines()
    if r.returncode != 0 or len(lines) < 2:
        return None
    try:
        major, minor = (int(x) for x in lines[0].split())
    except ValueError:
        return None
    real = lines[1].strip()
    if (major, minor) < MIN_PYTHON or not real or is_self(real):
        return None
    return real


def _hidden_window_kwargs() -> dict:
    if sys.platform != "win32":
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    return {"startupinfo": si, "creationflags": subprocess.CREATE_NO_WINDOW}


def _is_store_stub(path: str) -> bool:
    """O alias da Microsoft Store (``WindowsApps\\python.exe``) abre a loja."""
    if sys.platform != "win32" or "windowsapps" not in path.lower():
        return False
    try:
        return os.stat(path).st_size == 0
    except OSError:
        return True


def _candidates() -> list[list[str]]:
    found: list[list[str]] = []
    override = os.environ.get(ENV_PYTHON)
    if override:
        found.append([override])
    for name in ("python3", "python"):
        path = shutil.which(name)
        if path and not is_self(path) and not _is_store_stub(path):
            found.append([path])
    if sys.platform == "win32":
        launcher = shutil.which("py")
        if launcher:
            found.append([launcher, "-3"])
        local = os.environ.get("LOCALAPPDATA", "")
        patterns = [
            os.path.join(local, "Programs", "Python", "Python3*", "python.exe"),
            r"C:\Python3*\python.exe",
            r"C:\Program Files\Python3*\python.exe",
        ]
        for pattern in patterns:
            for path in sorted(glob.glob(pattern), reverse=True):
                found.append([path])
    else:
        for path in (
            "/usr/bin/python3",
            "/usr/local/bin/python3",
            "/opt/homebrew/bin/python3",
        ):
            if os.path.exists(path) and not is_self(path):
                found.append([path])
    return found


@functools.lru_cache(maxsize=1)
def _system_python() -> str | None:
    for cmd in _candidates():
        real = _probe(cmd)
        if real:
            return real
    return None


def no_python_message() -> str:
    """Mensagem acionável para o usuário quando não há Python disponível."""
    return (
        f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ não encontrado. O Codis compilado "
        "não inclui um interpretador para executar pip/venv/LSP. Instale o Python "
        f"(python.org) ou defina a variável de ambiente {ENV_PYTHON} com o caminho "
        "do python."
    )


def python_executable(preferred: str | None = None) -> str:
    """Caminho de um Python real para subprocessos.

    Args:
        preferred: Python explícito (ex.: de um venv). Nunca é o Codis.

    Raises:
        NoPythonError: se nenhum Python utilizável existir.
    """
    if preferred:
        if is_self(preferred):
            raise NoPythonError(no_python_message())
        return _fix_pythonw(preferred)
    if not is_frozen():
        return _fix_pythonw(sys.executable)
    found = _system_python()
    if found is None:
        raise NoPythonError(no_python_message())
    return found


def python_cmd(*args: str, preferred: str | None = None) -> list[str]:
    """``[python_real, *args]``; ver :func:`python_executable`."""
    return [python_executable(preferred), *args]


_RUN_SCRIPT = (
    "import runpy,sys;sys.argv=sys.argv[1:];"
    "runpy.run_path(sys.argv[0],run_name='__main__')"
)


def script_command(script: str | Path) -> list[str]:
    """Comando que executa ``script`` com Python real, ou com o próprio Codis
    (que emula ``-c``) se não houver Python instalado."""
    try:
        return [python_executable(), str(script)]
    except NoPythonError:
        if is_frozen():
            return [sys.executable, "-c", _RUN_SCRIPT, str(script)]
        raise


def restart_command() -> list[str]:
    """Comando para reiniciar o Codis (corrige ``[exe] + sys.argv`` duplicando o exe)."""
    if is_frozen():
        return [sys.executable, *sys.argv[1:]]
    return [sys.executable, *sys.argv]


def new_window_command() -> list[str]:
    """Comando para abrir uma nova janela do Codis."""
    if is_frozen():
        return [sys.executable]
    return [sys.executable, "-m", "src"]


def _emulate_interpreter(argv: list[str]) -> None:
    """Comporta-se como ``python`` para ``-m``/``-c``/``-V`` (nunca abre a UI)."""
    args = argv[1:]
    while args and args[0] in _SKIP_FLAGS:
        args = args[1:]
    flag = args[0] if args else ""
    if flag in ("-V", "--version"):
        sys.stdout.write(f"Python {sys.version.split()[0]} (Codis embutido)\n")
        raise SystemExit(0)
    if flag == "-c" and len(args) >= 2:
        sys.argv = ["-c", *args[2:]]
        code = compile(args[1], "<string>", "exec")
        exec(code, {"__name__": "__main__", "__builtins__": __builtins__})  # noqa: S102
        raise SystemExit(0)
    if flag == "-m" and len(args) >= 2:
        module = args[1]
        sys.argv = [module, *args[2:]]
        try:
            runpy.run_module(module, run_name="__main__", alter_sys=True)
        except ImportError as exc:
            sys.stderr.write(f"{Path(sys.executable).name}: {exc}\n")
            raise SystemExit(1) from None
        raise SystemExit(0)
    sys.stderr.write("Uso de interpretador não suportado pelo Codis embutido.\n")
    raise SystemExit(2)


def guard_startup(argv: Sequence[str] | None = None) -> None:
    """Chamar no início do ponto de entrada, antes de criar qualquer janela."""
    multiprocessing.freeze_support()
    argv = list(sys.argv if argv is None else argv)
    if current_depth() >= MAX_DEPTH:
        sys.stderr.write(
            "Codis: recursão de processos detectada (profundidade "
            f"{current_depth()}); abortando para evitar uma cascata de janelas.\n"
        )
        raise SystemExit(3)
    if is_frozen() and len(argv) > 1:
        probe = list(argv[1:])
        while probe and probe[0] in _SKIP_FLAGS:
            probe = probe[1:]
        if probe and probe[0] in _INTERPRETER_FLAGS:
            _emulate_interpreter(argv)

