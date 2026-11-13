#!/usr/bin/env python3
"""Codis - ponto de entrada para `python -m src [subcomando]`.

Uso:
    python -m src                   # Editor completo
    python -m src player            # Zen Media Player
    python -m src paint             # Editor de Imagens
    python -m src scratch           # Bloco de Notas
    python -m src pomodoro          # Timer Pomodoro
    python -m src regex             # Regex Playground
    python -m src rest              # Cliente REST
    python -m src diff A.py B.py    # Diff Viewer
    python -m src color-picker      # Seletor de Cores
    python -m src snippets          # Gerenciador de Snippets
    python -m src sql               # SQL Studio
    python -m src tasks             # Gerenciador de Tarefas
    python -m src pip               # Gerenciador de Pacotes Pip
    python -m src git [dir]         # Painel Git
    python -m src formatter         # Formatador JSON/YAML/TOML/XML
    python -m src env               # Variáveis de Ambiente
    python -m src calendar          # Calendário com Lembretes
    python -m src clock             # Relógio / Alarmes / Cronômetro
    python -m src calculator        # Calculadora Científica
    python -m src flowchart [py]    # Gerador de Fluxograma
    python -m src todo              # Lista de Tarefas Rápidas
    python -m src output            # Painel de Output
    python -m src terminal [dir]    # Terminal Integrado
    python -m src processes         # Gerenciador de Processos
    python -m src render [file]     # Preview HTML/Markdown/SVG
    python -m src stats [dir]       # Estatísticas de Código
    python -m src plugins           # Gerenciador de Plugins
    python -m src ssh               # Conexão SSH Remota
    python -m src archive [file]    # Visualizador de Arquivo Compactado
    python -m src accessibility     # Acessibilidade
    python -m src --help            # Ajuda completa
"""

import sys
from pathlib import Path


def _normalize_subcommand(args: list[str]) -> list[str] | None:
    """Reescreve um subcomando posicional como flag longa.

    Permite que ``python -m src player`` seja tratado da mesma forma que
    ``python -m src --player``. A normalização só ocorre quando o primeiro
    argumento não é uma flag e não corresponde a um caminho existente no
    sistema de arquivos (evitando colisão com comandos como
    ``python -m src diff A.py B.py``).

    Args:
        args: Argumentos de linha de comando, sem o nome do programa.

    Returns:
        Lista de argumentos com o subcomando normalizado, ou ``None``
        quando a normalização não se aplica.
    """
    if not args or args[0].startswith("-") or Path(args[0]).exists():
        return None
    return [f"--{args[0].lower()}", *args[1:]]


if __name__ == "__main__":
    from src.pyexec import guard_startup

    guard_startup(sys.argv)

    from src._font_safety import apply_font_safety

    apply_font_safety()

    from src.standalone import dispatch

    cli_args = sys.argv[1:]
    aliased_args = _normalize_subcommand(cli_args)
    if (aliased_args is not None and dispatch(aliased_args)) or dispatch(cli_args):
        sys.exit(0)

    from src.main import main

    main()
