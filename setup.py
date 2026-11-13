"""Configuração de instalação do pacote Codis via setuptools."""

from __future__ import annotations

import re
from pathlib import Path

from setuptools import find_packages, setup


def _read(filename: str) -> str:
    """Lê o conteúdo de um arquivo de texto em UTF-8."""
    return Path(filename).read_text(encoding="utf-8")


def _find_version() -> str:
    """Extrai a versão declarada em src/constants.py sem importar o pacote."""
    match = re.search(
        r'^APP_VERSION\s*=\s*["\']([^"\']+)["\']',
        _read("src/constants.py"),
        re.MULTILINE,
    )
    if match:
        return match.group(1)
    raise RuntimeError("APP_VERSION não encontrado em src/constants.py")


VERSION = _find_version()
LONG_DESCRIPTION = _read("README.md")
DESCRIPTION = (
    "Editor de código moderno, 100% Python + Tkinter - "
    "sem Electron, sem Node, sem dependências obrigatórias."
)

setup(
    name="codis",
    version=VERSION,
    description=DESCRIPTION,
    long_description=LONG_DESCRIPTION,
    long_description_content_type="text/markdown",
    author="KillovSky",
    author_email="",
    license="MIT",
    url="https://github.com/KillovSky/Codis",
    project_urls={
        "Bug Tracker": "https://github.com/KillovSky/Codis/issues",
        "Changelog": "https://github.com/KillovSky/Codis/blob/main/CHANGELOG.md",
        "Wiki": "https://github.com/KillovSky/Codis/wiki",
    },
    packages=find_packages(
        exclude=["tests*", "docs*", "wiki*", "build*", "dist*"]),
    include_package_data=True,
    python_requires=">=3.12",
    install_requires=[],
    extras_require={
        "recommended": [
            "send2trash>=1.8.0",
            "Pillow>=9.0.0",
            "markdown>=3.4.0",
        ],
        "full": [
            "send2trash>=1.8.0",
            "Pillow>=9.0.0",
            "markdown>=3.4.0",
            "py7zr>=0.20.0",
            "rarfile>=4.0",
        ],
        "dev": [
            "pyinstaller",
            "pytest",
            "pytest-cov",
            "flake8",
            "pylint",
        ],
    },
    entry_points={
        "console_scripts": [
            "codis = run:main",
        ],
        "gui_scripts": [
            "codis-gui = run:main",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Environment :: X11 Applications",
        "Environment :: Win32 (MS Windows)",
        "Environment :: MacOS X",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Natural Language :: Portuguese (Brazilian)",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Programming Language :: Python :: 3 :: Only",
        "Topic :: Software Development",
        "Topic :: Text Editors",
    ],
    keywords=[
        "editor",
        "code",
        "ide",
        "tkinter",
        "python",
        "codis",
        "text-editor",
        "syntax-highlighting",
        "lsp",
    ],
    package_data={
        "": [
            "src/locales/*.json",
            "wiki/*.md",
            "assets/*",
        ],
    },
)
