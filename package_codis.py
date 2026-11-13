#!/usr/bin/env python3
"""Script de empacotamento do Codis para distribuição.

Modos disponíveis:
    zip        - Cria arquivo .zip com o código-fonte (padrão)
    pyinstaller - Gera executável nativo via PyInstaller (Windows/Linux/macOS)

Uso:
    python package_codis.py            # modo zip
    python package_codis.py zip
    python package_codis.py pyinstaller           # --onefile (padrão)
    python package_codis.py pyinstaller --onedir  # pasta em vez de binário único
    python package_codis.py pyinstaller --debug
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
OUTPUT_DIR = PROJECT_ROOT / "dist"
APP_NAME = "Codis"
ENTRY_POINT = PROJECT_ROOT / "run.py"


def _read_app_version() -> str:
    """Lê APP_VERSION de src/constants.py sem importar o módulo.

    Usa regex para evitar dependências de Tkinter no script de build.
    Aceita também a variável de ambiente CODIS_RELEASE_VERSION (usada pelo CI).

    Returns:
        str: Versão do Codis.
    """
    env_ver = os.environ.get("CODIS_RELEASE_VERSION", "").strip()
    if env_ver:
        return env_ver
    constants = PROJECT_ROOT / "src" / "constants.py"
    if constants.exists():
        m = re.search(
            r'APP_VERSION\s*=\s*["\']([^"\']+)["\']',
            constants.read_text(encoding="utf-8"),
        )
        if m:
            return m.group(1)
    return "1.0.0"


APP_VERSION = _read_app_version()

_snap_name = os.environ.get("CODIS_SNAPSHOT_NAME", "").strip()
ZIP_NAME = (
    _snap_name
    if _snap_name
    else f"Codis-v{APP_VERSION}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
)

EXCLUDE_DIRS: set[str] = {
    "__pycache__",
    ".git",
    ".venv",
    "venv",
    ".idea",
    ".vscode",
    "dist",
    "build",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}

EXCLUDE_EXTENSIONS: set[str] = {".pyc", ".pyo", ".pyd", ".so", ".dll"}

EXCLUDE_FILES: set[str] = {
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",
    ".coverage",
    ".env",
    ".gitignore",
}

PYINSTALLER_DATA: list[tuple[str, str]] = [
    ("wiki", "wiki"),
    ("requirements.txt", "."),
]

PYINSTALLER_HIDDEN_IMPORTS: list[str] = [
    "tkinter",
    "tkinter.ttk",
    "tkinter.messagebox",
    "tkinter.filedialog",
    "tkinter.colorchooser",
    "tkinter.font",
    "tkinter.simpledialog",
    "src.plugins",
    "src.ia",
    "src.updater",
    "src.updater.manager",
    "src.updater.channel",
    "src.updater.dialog",
    "src.updater.backup",
    "src.updater.result",
    "src.lsp",
    "src.lint",
    "src.syntax",
    "src.diff",
    "xmlrpc.client",
    "xmlrpc.server",
    "email.mime.text",
    "email.mime.multipart",
]

PYINSTALLER_EXCLUDES: list[str] = [
    "matplotlib",
    "numpy",
    "pandas",
    "scipy",
    "cv2",
    "PyQt5",
    "PyQt6",
    "PySide2",
    "PySide6",
    "wx",
    "IPython",
    "jupyter",
    "notebook",
    "test",
    "unittest",
    "pylsp",
    "rope",
    "autopep8",
    "black",
    "isort",
    "pycodestyle",
    "pyflakes",
    "mccabe",
    "python_lsp_server",
    "python_lsp_jsonrpc",
    "docstring_to_markdown",
    "winpty",
    "mpv",
    "vlc",
    "tkinter.test",
    "lib2to3",
]


def _should_exclude(path: Path, root: Path) -> bool:
    rel = path.relative_to(root)
    for part in rel.parts:
        if part in EXCLUDE_DIRS:
            return True
    if path.suffix in EXCLUDE_EXTENSIONS:
        return True
    return path.name in EXCLUDE_FILES


def _remove_pycache(root: Path) -> int:
    """Remove pastas __pycache__ recursivamente. Retorna o total removido."""
    count = 0
    for cache_dir in root.rglob("__pycache__"):
        if cache_dir.is_dir():
            shutil.rmtree(cache_dir, ignore_errors=True)
            count += 1
    return count


def _create_zip(root: Path, dest: Path) -> int:
    """Cria ZIP de distribuição. Retorna o número de arquivos incluídos."""
    added = 0
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for file_path in sorted(root.rglob("*")):
            if file_path.is_file() and not _should_exclude(file_path, root):
                zf.write(file_path, file_path.relative_to(root))
                added += 1
    return added


def mode_zip() -> None:
    """Empacota o projeto como ZIP de código-fonte."""
    print(f"\n📦 Empacotamento ZIP - {APP_NAME} v{APP_VERSION}")
    print(f"   Projeto : {PROJECT_ROOT}")
    print(f"   Saída   : {OUTPUT_DIR / ZIP_NAME}")

    OUTPUT_DIR.mkdir(exist_ok=True)

    removed = _remove_pycache(PROJECT_ROOT)
    print(f"   __pycache__ removidas: {removed}")

    dest = OUTPUT_DIR / ZIP_NAME
    added = _create_zip(PROJECT_ROOT, dest)
    size_mb = dest.stat().st_size / (1024 * 1024)

    print(f"\n✅ ZIP criado: {dest}")
    print(f"   {added} arquivos  |  {size_mb:.2f} MB")


def _check_pyinstaller() -> bool:
    """Verifica se PyInstaller está instalado; oferece instalar se não estiver."""
    import importlib.util

    if importlib.util.find_spec("PyInstaller") is not None:
        return True
    print("⚠  PyInstaller não encontrado.")
    if not sys.stdin.isatty():
        print("   Execução não interativa: instale com 'pip install pyinstaller'.")
        return False
    answer = (
        input(
            "   Instalar agora? (pip install pyinstaller) [s/N] ").strip().lower()
    )
    if answer in ("s", "sim", "y", "yes"):
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "pyinstaller"],
            check=False,
        )
        return result.returncode == 0
    return False


def _find_upx() -> str | None:
    """Localiza o executável UPX para compressão do bundle.

    Returns:
        Caminho absoluto do UPX, ou None se não encontrado.
    """
    return shutil.which("upx") or shutil.which("upx-ucl")


def mode_pyinstaller(
    one_file: bool = True, debug: bool = False, use_upx: bool = True
) -> None:
    """Gera executável nativo via PyInstaller.

    Padrão é ``--onefile``: ``--onedir`` já causou segmentation fault ao
    iniciar o executável em alguns ambientes Linux (resolução incorreta de
    .so soltos ao lado do binário). Veja o cabeçalho de
    ``.github/workflows/build.yml`` para o mesmo racional aplicado ao CI.
    """
    if not ENTRY_POINT.exists():
        print(f"❌ Ponto de entrada não encontrado: {ENTRY_POINT}")
        sys.exit(1)

    if not _check_pyinstaller():
        print("❌ PyInstaller necessário para este modo.")
        sys.exit(1)

    print(f"\n🔨 Build PyInstaller - {APP_NAME} v{APP_VERSION}")
    print(f"   Modo     : {'--onefile' if one_file else '--onedir'}")
    print(f"   Plataforma: {sys.platform}")

    OUTPUT_DIR.mkdir(exist_ok=True)

    args: list[str] = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--name",
        APP_NAME,
        "--distpath",
        str(OUTPUT_DIR),
        "--workpath",
        str(PROJECT_ROOT / "build" / "pyinstaller_work"),
        "--specpath",
        str(PROJECT_ROOT / "build"),
        "--noconfirm",
        "--clean",
        "--collect-all",
        "tkinter",
        "--optimize",
        "2",
    ]

    upx_path = _find_upx() if use_upx else None
    if upx_path:
        upx_dir = str(Path(upx_path).parent)
        args += ["--upx-dir", upx_dir]
        print(f"   UPX     : {upx_path} (compressão ativada)")
    else:
        args.append("--noupx")
        print("   UPX     : desativado" if not use_upx else "   UPX     : não encontrado (compressão desativada)")

    if sys.platform.startswith("linux") and os.environ.get("CODIS_PYI_STRIP") == "1":
        args.append("--strip")

    if one_file:
        args.append("--onefile")
    else:
        args.append("--onedir")

    if not debug:
        args.append(
            "--noconsole" if sys.platform in ("win32",
                                              "darwin") else "--console"
        )

    icon_candidates = [
        PROJECT_ROOT / "assets" / "icon.ico",
        PROJECT_ROOT / "assets" / "icon.icns",
        PROJECT_ROOT / "assets" / "icon.png",
    ]
    for icon in icon_candidates:
        if icon.exists():
            args += ["--icon", str(icon)]
            break

    sep = ";" if sys.platform == "win32" else ":"
    for src_glob, dest_dir in PYINSTALLER_DATA:
        src_path = PROJECT_ROOT / src_glob
        if src_path.exists():
            args += ["--add-data", f"{src_path}{sep}{dest_dir}"]
    locales_dir = PROJECT_ROOT / "src" / "locales"
    for locale_file in sorted(locales_dir.glob("*.json")):
        if locale_file.name.startswith("."):
            continue
        args += ["--add-data", f"{locale_file}{sep}src/locales"]

    for hidden in PYINSTALLER_HIDDEN_IMPORTS:
        args += ["--hidden-import", hidden]

    for exc_mod in PYINSTALLER_EXCLUDES:
        args += ["--exclude-module", exc_mod]

    args.append(str(ENTRY_POINT))

    print(f"\n   Executando: pyinstaller {' '.join(args[2:5])} ...\n")
    result = subprocess.run(args, cwd=PROJECT_ROOT, check=False)

    if result.returncode == 0:
        output_path = OUTPUT_DIR / APP_NAME
        if one_file:
            ext = ".exe" if sys.platform == "win32" else ""
            output_path = OUTPUT_DIR / f"{APP_NAME}{ext}"
        print(f"\n✅ Build concluído: {output_path}")
    else:
        print(f"\n❌ PyInstaller falhou (código {result.returncode}).")
        print("   Verifique os logs acima para detalhes.")
        sys.exit(result.returncode)


def _build_parser() -> argparse.ArgumentParser:
    """Constrói o parser de argumentos da linha de comando."""
    parser = argparse.ArgumentParser(
        description=f"Empacotador do {APP_NAME} v{APP_VERSION}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "mode",
        nargs="?",
        choices=["zip", "pyinstaller"],
        default="zip",
        help="Modo de empacotamento (padrão: zip)",
    )
    parser.add_argument(
        "--onedir",
        action="store_true",
        help=(
            "[PyInstaller] Gera uma pasta em vez de um único executável "
            "(padrão é --onefile; --onedir pode causar segmentation fault "
            "ao iniciar em alguns ambientes Linux)"
        ),
    )
    parser.add_argument(
        "--no-upx",
        action="store_true",
        help="[PyInstaller] Nunca usa UPX (também via CODIS_NO_UPX=1; usado no CI)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="[PyInstaller] Mantém janela de console para depuração",
    )
    return parser


def main() -> None:
    """Ponto de entrada principal do empacotador."""
    if not ENTRY_POINT.exists():
        print("❌ Execute este script a partir da raiz do projeto Codis.")
        print(f"   run.py não encontrado em: {PROJECT_ROOT}")
        sys.exit(1)

    parser = _build_parser()
    args = parser.parse_args()

    print("=" * 60)
    print(f"  📦 Empacotador Codis v{APP_VERSION}")
    print("=" * 60)

    if args.mode == "pyinstaller":
        no_upx = args.no_upx or os.environ.get("CODIS_NO_UPX", "").strip() not in ("", "0")
        mode_pyinstaller(one_file=not args.onedir, debug=args.debug, use_upx=not no_upx)
    else:
        mode_zip()


if __name__ == "__main__":
    main()
