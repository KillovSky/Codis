"""Proteção contra crashes do Tk/Xft causados por fontes problemáticas no Linux.

O Tk anterior à 8.6.13 pode encerrar com ``SIGSEGV`` ao medir ou desenhar
texto quando o fontconfig devolve fontes coloridas (bitmap COLR/CBDT, como
*Noto Color Emoji*) ou fontes bitmap não escaláveis como fallback de glifos.
O sintoma típico é um segfault dentro de ``tkinter.Label.__init__``.

Este módulo gera um arquivo de configuração do fontconfig que inclui a
configuração original do sistema e rejeita essas fontes, e o aponta via
``FONTCONFIG_FILE``. Deve ser executado ANTES de qualquer ``tk.Tk()``.

Variáveis de ambiente:
    CODIS_SAFE_FONTS=1  força a proteção em qualquer versão do Tk.
    CODIS_SAFE_FONTS=0  desativa a proteção.
"""

from __future__ import annotations

import getpass
import os
import re
import sys
import tempfile
from pathlib import Path

_FIXED_TK_VERSION = (8, 6, 13)
_SYSTEM_FONTS_CONF = "/etc/fonts/fonts.conf"
_CONF_NAME = "fontconfig-safe.conf"

_CONF_TEMPLATE = """<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig>
  <include ignore_missing="yes">{base}</include>
  <selectfont>
    <rejectfont>
      <pattern><patelt name="color"><bool>true</bool></patelt></pattern>
      <pattern><patelt name="scalable"><bool>false</bool></patelt></pattern>
    </rejectfont>
  </selectfont>
</fontconfig>
"""


def _tcl_patchlevel() -> tuple[int, int, int] | None:
    """Lê a versão do Tcl/Tk em uso sem exigir display."""
    try:
        import tkinter
    except ImportError:
        return None
    tcl_factory = getattr(tkinter, "Tcl", None)
    if tcl_factory is None:
        return None
    tcl_error = getattr(tkinter, "TclError", RuntimeError)
    try:
        raw = tcl_factory().eval("info patchlevel")
    except (tcl_error, RuntimeError, OSError):
        return None
    match = re.match(r"(\d+)\.(\d+)\.(\d+)", str(raw))
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def _should_apply() -> bool:
    """Decide se a proteção deve ser aplicada nesta execução."""
    if not sys.platform.startswith("linux"):
        return False
    override = os.environ.get("CODIS_SAFE_FONTS", "").strip().lower()
    if override in {"0", "false", "no", "off"}:
        return False
    if override in {"1", "true", "yes", "on"}:
        return True
    version = _tcl_patchlevel()
    return version is not None and version < _FIXED_TK_VERSION


def _user_tag() -> str:
    """Identificador estável do usuário atual para nomear diretórios temporários."""
    getuid = getattr(os, "getuid", None)
    if getuid is not None:
        return str(getuid())
    try:
        return getpass.getuser()
    except (OSError, KeyError, ImportError):
        return "user"


def _conf_dir() -> Path:
    """Escolhe um diretório gravável para o arquivo de configuração."""
    candidates = (
        Path.home() / ".codis" / "cache",
        Path(tempfile.gettempdir()) / f"codis-{_user_tag()}",
    )
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        if os.access(candidate, os.W_OK):
            return candidate
    return candidates[-1]


def apply_font_safety() -> bool:
    """Aplica a proteção de fontes quando necessário.

    Returns:
        bool: ``True`` se ``FONTCONFIG_FILE`` foi redirecionado.
    """
    if os.environ.get("_CODIS_FONT_SAFETY") == "1":
        return False
    if not _should_apply():
        return False

    base = os.environ.get("FONTCONFIG_FILE") or _SYSTEM_FONTS_CONF
    if not os.path.isfile(base):
        return False

    target = _conf_dir() / _CONF_NAME
    try:
        target.write_text(_CONF_TEMPLATE.format(base=base), encoding="utf-8")
    except OSError:
        return False

    os.environ["FONTCONFIG_FILE"] = str(target)
    os.environ["_CODIS_FONT_SAFETY"] = "1"
    return True
