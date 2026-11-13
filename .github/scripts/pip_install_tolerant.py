#!/usr/bin/env python3
"""Instala requirements.txt tolerando pacotes sem wheel na plataforma.

Primeiro tenta a instalação em bloco; se falhar, instala item a item e
apenas reporta (sem falhar) os que não puderam ser instalados. Usado nas
plataformas experimentais (Windows x86/ARM64, Linux ARM64, macOS Intel), onde
alguns pacotes opcionais podem não ter wheels pré-compiladas.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _pip(*args: str) -> int:
    return subprocess.call([sys.executable, "-m", "pip", "install", *args])  # noqa: S603


def _requirements(path: Path) -> list[str]:
    items: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            items.append(line)
    return items


def main() -> int:
    """Ponto de entrada do script."""
    req = Path(sys.argv[1] if len(sys.argv) > 1 else "requirements.txt")
    if _pip("-r", str(req)) == 0:
        return 0
    print("::warning::instalacao em bloco falhou; tentando item a item")
    skipped: list[str] = []
    for item in _requirements(req):
        if _pip(item) != 0:
            skipped.append(item)
    if skipped:
        print("::warning::pacotes opcionais ignorados: " + ", ".join(skipped))
    return 0


if __name__ == "__main__":
    sys.exit(main())
