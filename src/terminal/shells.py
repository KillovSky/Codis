"""Montagem segura de comandos para os shells do terminal. Sem Tkinter.

Caminhos e valores de variáveis chegam de nomes de arquivos e de configurações,
então nunca podem ser colocados entre aspas duplas em shells POSIX (que expandem
``$(...)``, crases e ``$VAR`` ali dentro). Cada shell recebe a citação própria.
"""

from __future__ import annotations

import os
import re

_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_PS_QUOTES = "'\u2018\u2019\u201a\u201b"


def shell_kind(argv0: str) -> str:
    """Classifica o shell: ``powershell``, ``cmd``, ``fish`` ou ``posix``."""
    name = os.path.basename(argv0.replace("\\", "/")).lower()
    if name.endswith(".exe"):
        name = name[:-4]
    if name in ("powershell", "pwsh"):
        return "powershell"
    if name == "cmd":
        return "cmd"
    if name == "fish":
        return "fish"
    return "posix"


def quote_arg(kind: str, value: str) -> str:
    """Cita ``value`` como um único argumento literal para o shell ``kind``."""
    value = value.replace("\r", "").replace("\n", " ")
    if kind == "powershell":
        return "'" + "".join(c * 2 if c in _PS_QUOTES else c for c in value) + "'"
    if kind == "cmd":
        return '"' + value.replace('"', "") + '"'
    if kind == "fish":
        return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
    return "'" + value.replace("'", "'\\''") + "'"


def cd_command(kind: str, path: str) -> str:
    """Comando que muda o diretório de trabalho para ``path``."""
    if kind == "cmd":
        return f"cd /d {quote_arg(kind, path)}"
    if kind == "powershell":
        return f"Set-Location -LiteralPath {quote_arg(kind, path)}"
    return f"cd {quote_arg(kind, path)}"


def run_file_command(kind: str, interpreter: str, path: str) -> str:
    """Comando que executa ``path`` com ``interpreter`` (ou o próprio arquivo)."""
    target = quote_arg(kind, path)
    if not interpreter:
        return f"& {target}" if kind == "powershell" else target
    return f"{interpreter} {target}"


def echo_command(kind: str, text: str) -> str:
    """Comando que imprime ``text`` literalmente."""
    if kind == "cmd":
        return "echo " + re.sub(r'[&|<>^%"]', "", text)
    return f"echo {quote_arg(kind, text)}"
