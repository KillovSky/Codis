#!/usr/bin/env python3
"""Ponto de entrada do CLI do Codis.

Este módulo inicializa o ambiente necessário para a execução do aplicativo,
configura o tratamento de sinais do processo e normaliza subcomandos de
linha de comando para os modos standalone e para as chamadas diretas do
editor.
"""

import contextlib
import importlib
import os
import signal
import sys
import warnings
from pathlib import Path


def _setup_frozen_env() -> None:
    """Configura variáveis de ambiente para execução a partir de binários congelados.

    Quando o programa roda como um artefato PyInstaller, os diretórios de
    bibliotecas Tcl/Tk e os arquivos de configuração do fontconfig podem não
    estar disponíveis no caminho padrão. Este ajuste preserva o comportamento
    esperado em ambientes empacotados.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass is None:
        return

    base = meipass
    if base not in sys.path:
        sys.path.insert(0, base)

    for tcl_candidate in (
        os.path.join(base, "tcl"),
        os.path.join(base, "lib", "tcl8.6"),
        os.path.join(base, "_tcl_data"),
    ):
        if os.path.isdir(tcl_candidate):
            os.environ.setdefault("TCL_LIBRARY", tcl_candidate)
            break

    for tk_candidate in (
        os.path.join(base, "tk"),
        os.path.join(base, "lib", "tk8.6"),
        os.path.join(base, "_tk_data"),
    ):
        if os.path.isdir(tk_candidate):
            os.environ.setdefault("TK_LIBRARY", tk_candidate)
            break

    if "TCL_LIBRARY" not in os.environ:
        for entry in os.listdir(base):
            full_path = os.path.join(base, entry)
            if entry.startswith("tcl") and os.path.isdir(full_path):
                os.environ.setdefault("TCL_LIBRARY", full_path)
            elif entry.startswith("tk") and os.path.isdir(full_path):
                os.environ.setdefault("TK_LIBRARY", full_path)

    if sys.platform.startswith("linux"):
        for fontconfig_candidate in (
            os.path.join(base, "etc", "fonts", "fonts.conf"),
            "/etc/fonts/fonts.conf",
        ):
            if os.path.isfile(fontconfig_candidate):
                os.environ.setdefault("FONTCONFIG_FILE", fontconfig_candidate)
                break


def _warn_if_root() -> None:
    """Emite um aviso quando o programa for executado como usuário root.

    O modo root pode quebrar a integração com o X11 e impedir que o Tk
    encontre o ambiente gráfico do usuário real. Quando possível, o módulo
    tenta restaurar o diretório inicial a partir de SUDO_USER.
    """
    if sys.platform == "win32":
        return

    try:
        if os.getuid() != 0:  # type: ignore[attr-defined]
            return
    except AttributeError:
        return

    sudo_user = os.environ.get("SUDO_USER", "")
    if sudo_user:
        try:
            pwd_module = importlib.import_module("pwd")
            pw_entry = pwd_module.getpwnam(sudo_user)
            real_home = pw_entry.pw_dir
            os.environ["HOME"] = real_home
            xauthority_path = os.path.join(real_home, ".Xauthority")
            if os.path.isfile(xauthority_path):
                os.environ.setdefault("XAUTHORITY", xauthority_path)
        except (AttributeError, ImportError, KeyError, OSError):
            pass

    warnings.warn(
        "Codis está sendo executado como root. Isso pode causar problemas "
        "de exibição e permissão. Execute sem sudo sempre que possível.",
        RuntimeWarning,
        stacklevel=2,
    )

import multiprocessing  # noqa: E402

multiprocessing.freeze_support()

_setup_frozen_env()
_warn_if_root()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.pyexec import guard_startup  # noqa: E402

guard_startup(sys.argv)


def _apply_font_safety() -> None:
    """Ativa a proteção contra segfault do Tk/Xft antes de qualquer ``Tk()``."""
    try:
        from src._font_safety import apply_font_safety

        apply_font_safety()
    except (ImportError, OSError):
        pass

_apply_font_safety()


def _sigint_handler(_signum: int, _frame: object) -> None:
    """Encerra o ciclo de vida do Tk quando o processo recebe SIGINT ou SIGTERM."""
    try:
        tkinter_module = importlib.import_module("tkinter")
        root = getattr(tkinter_module, "_default_root", None)
        if root and root.winfo_exists():
            root.after(0, root.destroy)
    except (AttributeError, ImportError, RuntimeError, OSError):
        pass
    finally:
        sys.exit(0)


def _setup_signals() -> None:
    """Registra os handlers de encerramento para sinais de interrupção."""
    signal.signal(signal.SIGINT, _sigint_handler)
    with contextlib.suppress(AttributeError, OSError):
        signal.signal(signal.SIGTERM, _sigint_handler)


def _normalize_subcommand(args: list[str]) -> list[str] | None:
    """Converte um subcomando posicional em uma flag longa compatível.

    Isto permite que comandos como ``python run.py player`` sejam tratados da
    mesma forma que ``python run.py --player``. A normalização é aplicada
    apenas quando o primeiro argumento não é uma opção e não aponta para um
    caminho existente no sistema.
    """
    if not args or args[0].startswith("-") or Path(args[0]).exists():
        return None
    return [f"--{args[0].lower()}", *args[1:]]


def main() -> None:
    """Executa a inicialização do aplicativo e o dispatch do CLI."""
    _setup_signals()
    try:
        from src.standalone import dispatch

        cli_args = sys.argv[1:]
        aliased_args = _normalize_subcommand(cli_args)
        if (aliased_args is not None and dispatch(aliased_args)) or dispatch(cli_args):
            raise SystemExit(0)

        from src.main import main as app_main

        app_main()
    except KeyboardInterrupt:
        raise SystemExit(0) from None

if __name__ == "__main__":
    main()
