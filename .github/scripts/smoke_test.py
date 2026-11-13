#!/usr/bin/env python3
"""Smoke test multiplataforma do binário do Codis.

Inicia o executável com um HOME temporário, mantém-no rodando por alguns
segundos e valida que ele continua vivo e sem tracebacks no log. Em caso de
morte por sinal (ex.: SIGSEGV = -11) o código do sinal é reportado.

Uso: python smoke_test.py <binario> [--seconds N]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BAD_PATTERNS = re.compile(
    r"Traceback|ModuleNotFoundError|ImportError|Fatal Python error|Segmentation fault"
)


def _describe_exit(code: int) -> str:
    """Traduz o código de saída em texto legível (incluindo sinais POSIX)."""
    if code < 0:
        return f"morto pelo sinal {-code}"
    if code > 128 and os.name != "nt":
        return f"código {code} (provável sinal {code - 128})"
    return f"código {code}"


def run(binary: Path, seconds: int) -> int:
    """Executa o binário e devolve 0 em caso de sucesso."""
    home = Path(tempfile.mkdtemp(prefix="codis-smoke-"))
    config_dir = home / ".codis"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(
        json.dumps({"first_run_done": True}), encoding="utf-8"
    )

    env = dict(os.environ)
    env.update(
        HOME=str(home),
        USERPROFILE=str(home),
        PYTHONUNBUFFERED="1",
        PYTHONFAULTHANDLER="1",
        PYTHONIOENCODING="utf-8",
    )

    log_path = Path("smoke.log")
    with log_path.open("wb") as log:
        proc = subprocess.Popen(  # noqa: S603
            [str(binary)], stdout=log, stderr=subprocess.STDOUT, env=env
        )
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            time.sleep(0.25)
        alive = proc.poll() is None
        if alive:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        exit_code = proc.returncode

    text = log_path.read_text(encoding="utf-8", errors="replace")
    print(f"--- smoke.log ({_describe_exit(exit_code)}) ---")
    print(text)
    print("--- fim do smoke.log ---")

    if not alive:
        print(f"ERRO: o binario encerrou sozinho: {_describe_exit(exit_code)}")
        return 1
    if BAD_PATTERNS.search(text):
        print("ERRO: excecoes no log do smoke test")
        return 1
    print("Smoke test OK")
    return 0


def main() -> int:
    """Ponto de entrada do script."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument("--seconds", type=int, default=20)
    args = parser.parse_args()
    if not args.binary.exists():
        print(f"ERRO: binario nao encontrado: {args.binary}")
        return 2
    return run(args.binary, args.seconds)


if __name__ == "__main__":
    sys.exit(main())
