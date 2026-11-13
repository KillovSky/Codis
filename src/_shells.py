"""Descoberta rápida e preguiçosa de executáveis/shells.

Por que existe: ``shutil.which`` consulta cada diretório do PATH × cada
extensão do PATHEXT com ``stat``. No Windows (principalmente no Git Bash, cujo
PATH é enorme, com antivírus varrendo cada acesso) ~90 chamadas no import dos
``constants`` custavam segundos. Aqui o PATH é listado **uma vez** (um
``scandir`` por diretório) e as consultas viram buscas em memória.
"""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Iterator, MutableMapping

_LOCK = threading.RLock()
_INDEX: list[tuple[str, dict[str, str]]] | None = None
_WIN = sys.platform == "win32"


def _build_index() -> list[tuple[str, dict[str, str]]]:
    index: list[tuple[str, dict[str, str]]] = []
    seen: set[str] = set()
    for raw in os.environ.get("PATH", "").split(os.pathsep):
        directory = raw.strip().strip('"')
        key = os.path.normcase(directory)
        if not directory or key in seen:
            continue
        seen.add(key)
        try:
            with os.scandir(directory) as it:
                names = {e.name.lower() if _WIN else e.name: e.name for e in it}
        except OSError:
            continue
        index.append((directory, names))
    return index


def warm() -> None:
    """Constrói o índice do PATH (seguro chamar em thread de fundo)."""
    global _INDEX
    with _LOCK:
        if _INDEX is None:
            _INDEX = _build_index()


def refresh() -> None:
    """Descarta o índice (ex.: após instalar algo e alterar o PATH)."""
    global _INDEX
    with _LOCK:
        _INDEX = None


def fast_which(name: str) -> str | None:
    """Equivalente barato de :func:`shutil.which` baseado no índice do PATH."""
    if not name:
        return None
    if os.path.dirname(name):
        import shutil

        return shutil.which(name)
    warm()
    assert _INDEX is not None
    if _WIN:
        exts = [e.lower() for e in os.environ.get("PATHEXT", ".EXE;.CMD;.BAT;.COM").split(";") if e]
        low = name.lower()
        wanted = [low] if any(low.endswith(e) for e in exts) else [low + e for e in exts]
        for directory, names in _INDEX:
            for cand in wanted:
                real = names.get(cand)
                if real:
                    return os.path.join(directory, real)
        return None
    for directory, names in _INDEX:
        if name in names:
            full = os.path.join(directory, name)
            if os.access(full, os.X_OK) and not os.path.isdir(full):
                return full
    return None


def _shell_table() -> dict[str, list[str]]:
    w = fast_which
    out: dict[str, list[str]] = {}
    if _WIN:
        ps_args = ["-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "RemoteSigned"]
        if w("powershell.exe"):
            out["PowerShell"] = ["powershell.exe", *ps_args]
        if w("cmd.exe"):
            out["CMD"] = ["cmd.exe"]
        if w("pwsh.exe"):
            out["PowerShell Core"] = ["pwsh.exe", *ps_args]
        if w("bash.exe"):
            out["Bash (Git/Cygwin)"] = ["bash.exe", "--login", "-i"]
        if w("wsl.exe"):
            out["WSL (default)"] = ["wsl.exe"]
        if w("busybox.exe"):
            out["BusyBox ash"] = ["busybox.exe", "ash"]
        for exe, label in (("dash", "Dash"), ("ksh", "Korn Shell"), ("tcsh", "Tcsh"),
                           ("csh", "Csh"), ("elvish", "Elvish"), ("nu", "Nushell"),
                           ("xonsh", "Xonsh"), ("osh", "Oil")):
            if w(exe + ".exe"):
                out[label] = [exe + ".exe"]
    else:
        for exe, label, args in (
            ("bash", "Bash", ["--norc"]), ("zsh", "Zsh", ["--no-rcs"]),
            ("fish", "Fish", ["--no-config"]), ("dash", "Dash", []),
            ("ksh", "Korn Shell", []), ("tcsh", "Tcsh", []), ("csh", "Csh", []),
            ("ash", "Ash", []), ("elvish", "Elvish", []), ("nu", "Nushell", []),
            ("xonsh", "Xonsh", []), ("osh", "Oil", []),
        ):
            if w(exe):
                out[label] = [exe, *args]
        sh = os.environ.get("SHELL", "/bin/sh")
        if sh not in [c for lst in out.values() for c in lst]:
            out["sh (default)"] = [sh]
    py = w("python3") or w("python")
    if py:
        out["Python REPL"] = [py, "-i", "-u"]
    for exe, label, args in (
        ("ipython", "IPython", []), ("bpython", "BPython", []), ("ptpython", "PTPython", []),
        ("node", "Node REPL", ["--interactive"]), ("irb", "Ruby (irb)", []),
        ("perl", "Perl debugger", ["-de", "1"]), ("php", "PHP (interactive)", ["-a"]),
        ("lua", "Lua REPL", []), ("R", "R REPL", ["--quiet"]), ("julia", "Julia REPL", []),
        ("iex", "Elixir (iex)", []), ("erl", "Erlang", []), ("clojure", "Clojure REPL", []),
        ("scala", "Scala REPL", []), ("groovysh", "Groovy (groovysh)", []),
        ("tclsh", "Tcl", []), ("gosh", "Gauche Scheme", []), ("guile", "Guile", []),
        ("racket", "Racket", ["-i"]), ("swipl", "SWI-Prolog", []),
    ):
        if w(exe):
            out[label] = [exe, *args]
    if not out:
        out["CMD" if _WIN else "sh"] = ["cmd.exe"] if _WIN else ["/bin/sh"]
    return out


class LazyShellOptions(MutableMapping):  # type: ignore[type-arg]
    """Mapeamento ``rótulo -> comando`` calculado no primeiro acesso."""

    def __init__(self) -> None:
        self._data: dict[str, list[str]] | None = None
        self._lock = threading.RLock()

    def _d(self) -> dict[str, list[str]]:
        with self._lock:
            if self._data is None:
                self._data = _shell_table()
            return self._data

    def __getitem__(self, k: str) -> list[str]:
        return self._d()[k]

    def __setitem__(self, k: str, v: list[str]) -> None:
        self._d()[k] = v

    def __delitem__(self, k: str) -> None:
        del self._d()[k]

    def __iter__(self) -> Iterator[str]:
        return iter(self._d())

    def __len__(self) -> int:
        return len(self._d())


SHELL_OPTIONS: LazyShellOptions = LazyShellOptions()


def get_default_shell() -> str:
    """Primeiro shell disponível (avaliado só quando necessário)."""
    return next(iter(SHELL_OPTIONS))
