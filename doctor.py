#!/usr/bin/env python3
"""Diagnóstico de ambiente para desenvolvimento do Codis.

Verifica versão do Python, presença do Tkinter, dependências opcionais,
ferramentas de build e configurações de display, reportando exatamente
o que falta e como corrigir.

Uso:
    python doctor.py
    python doctor.py --verbose
    python doctor.py --json
"""

from __future__ import annotations

import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

MIN_PYTHON = (3, 12)

_USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text


def _ok(text: str) -> str:
    return _c("32", text)


def _warn(text: str) -> str:
    return _c("33", text)


def _err(text: str) -> str:
    return _c("31", text)


def _bold(text: str) -> str:
    return _c("1", text)


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str
    fix: str | None = None


@dataclass
class Report:
    checks: list[CheckResult] = field(default_factory=list)

    def add(self, name: str, status: str, detail: str, fix: str | None = None) -> None:
        self.checks.append(CheckResult(name, status, detail, fix))

    @property
    def has_errors(self) -> bool:
        return any(c.status == "error" for c in self.checks)

    @property
    def has_warnings(self) -> bool:
        return any(c.status == "warn" for c in self.checks)


def check_python_version(report: Report) -> None:
    version = sys.version_info[:3]
    version_str = ".".join(str(v) for v in version)
    if sys.version_info[:2] >= MIN_PYTHON:
        report.add(
            "Python", "ok", f"{version_str} (mínimo: {'.'.join(map(str, MIN_PYTHON))})"
        )
    else:
        report.add(
            "Python",
            "error",
            f"{version_str} é menor que o mínimo exigido ({'.'.join(map(str, MIN_PYTHON))})",
            fix="Instale Python 3.12+ em https://www.python.org/downloads/",
        )


def check_tkinter(report: Report) -> None:
    try:
        import tkinter

        tk_version = tkinter.TkVersion
        report.add("Tkinter", "ok", f"Tcl/Tk {tk_version}")
    except ImportError:
        fix = {
            "Linux": "sudo apt install python3-tk  (Debian/Ubuntu)\n"
            "         sudo dnf install python3-tkinter  (Fedora)\n"
            "         sudo pacman -S tk  (Arch)",
            "Darwin": "brew install python-tk@3.12",
            "Windows": "Reinstale o Python marcando 'tcl/tk and IDLE'",
        }.get(platform.system(), "Instale o pacote tkinter para seu sistema")
        report.add("Tkinter", "error", "não encontrado", fix=fix)


def check_optional_dependency(
    report: Report, module: str, package: str, feature: str
) -> None:
    spec = importlib.util.find_spec(module)
    if spec is not None:
        try:
            mod = importlib.import_module(module)
            version = getattr(mod, "__version__", "desconhecida")
            report.add(package, "ok", f"v{version} — {feature}")
        except Exception:
            report.add(package, "ok", f"instalado — {feature}")
    else:
        report.add(
            package,
            "warn",
            f"ausente — {feature} ficará desativado",
            fix=f"pip install {package}",
        )


def check_git(report: Report) -> None:
    git_path = shutil.which("git")
    if git_path:
        try:
            result = subprocess.run(
                ["git", "--version"], capture_output=True, text=True, timeout=5
            )
            report.add("Git", "ok", result.stdout.strip())
        except Exception:
            report.add("Git", "warn", "encontrado mas falhou ao executar")
    else:
        report.add(
            "Git",
            "warn",
            "não encontrado no PATH — git panel ficará desativado",
            fix="https://git-scm.com/downloads",
        )


def check_display(report: Report) -> None:
    system = platform.system()
    if system == "Linux":
        display = os.environ.get("DISPLAY")
        wayland = os.environ.get("WAYLAND_DISPLAY")
        if display:
            report.add("Display (X11)", "ok", f"DISPLAY={display}")
        elif wayland:
            report.add(
                "Display (Wayland)",
                "warn",
                f"WAYLAND_DISPLAY={wayland} sem DISPLAY — Tkinter requer XWayland",
                fix="Verifique se XWayland está instalado e ativo, ou rode com GDK_BACKEND=x11",
            )
        else:
            report.add(
                "Display",
                "error",
                "nenhuma variável DISPLAY ou WAYLAND_DISPLAY detectada",
                fix="Certifique-se de estar em uma sessão gráfica (não SSH headless sem X forwarding)",
            )
    else:
        report.add("Display", "ok", f"{system} gerencia display nativamente")


def check_pyinstaller(report: Report) -> None:
    spec = importlib.util.find_spec("PyInstaller")
    if spec is not None:
        try:
            import PyInstaller

            report.add(
                "PyInstaller",
                "ok",
                f"v{PyInstaller.__version__} — build de binário disponível",
            )
        except Exception:
            report.add("PyInstaller", "ok", "instalado")
    else:
        report.add(
            "PyInstaller",
            "warn",
            "ausente — necessário apenas para gerar binários (package_codis.py)",
            fix="pip install pyinstaller",
        )


def check_locales(report: Report) -> None:
    locales_dir = Path(__file__).parent / "src" / "locales"
    if not locales_dir.exists():
        report.add("Locales", "warn", "pasta src/locales/ não encontrada")
        return
    files = list(locales_dir.glob("*.json"))
    if files:
        report.add("Locales", "ok", f"{len(files)} idioma(s) encontrado(s)")
    else:
        report.add("Locales", "warn",
                   "nenhum arquivo .json encontrado em src/locales/")


def check_venv(report: Report) -> None:
    in_venv = sys.prefix != sys.base_prefix
    if in_venv:
        report.add("Ambiente virtual", "ok", f"ativo em {sys.prefix}")
    else:
        report.add(
            "Ambiente virtual",
            "warn",
            "não detectado — recomendado para desenvolvimento",
            fix="python -m venv .venv && source .venv/bin/activate  (Linux/macOS)\n"
            "         python -m venv .venv && .venv\\Scripts\\activate     (Windows)",
        )


def run_all_checks() -> Report:
    report = Report()

    check_python_version(report)
    check_tkinter(report)
    check_venv(report)
    check_git(report)
    check_display(report)
    check_pyinstaller(report)
    check_locales(report)

    optional_deps = [
        ("PIL", "Pillow", "renderização de imagens, paint panel"),
        ("send2trash", "send2trash", "lixeira nativa do SO"),
        ("markdown", "markdown", "preview de Markdown"),
        ("psutil", "psutil", "monitor de CPU/RAM"),
        ("websockets", "websockets", "colaboração em tempo real"),
        ("pygments", "Pygments", "exportação HTML com highlight"),
        ("charset_normalizer", "charset-normalizer", "detecção de encoding"),
    ]
    for module, package, feature in optional_deps:
        check_optional_dependency(report, module, package, feature)

    if platform.system() == "Windows":
        check_optional_dependency(
            report, "winpty", "pywinpty", "terminal com PTY completo"
        )

    return report


def print_report(report: Report, verbose: bool = False) -> None:
    print(_bold("\n🩺 Codis Doctor — Diagnóstico de Ambiente\n"))
    print(f"  Sistema: {platform.platform()}")
    print(f"  Python:  {sys.executable}\n")

    icons = {"ok": _ok("✓"), "warn": _warn("⚠"), "error": _err("✗")}

    for check in report.checks:
        icon = icons[check.status]
        print(f"  {icon}  {_bold(check.name):<28} {check.detail}")
        if check.fix and (verbose or check.status != "ok"):
            for line in check.fix.split("\n"):
                print(f"       {_c('2', '→')} {line.strip()}")

    print()
    if report.has_errors:
        print(_err("✗ Encontrados problemas críticos. O Codis pode não iniciar."))
    elif report.has_warnings:
        print(
            _warn(
                "⚠ Tudo funcional, mas algumas funcionalidades opcionais estão desativadas."
            )
        )
    else:
        print(_ok("✓ Ambiente perfeito! Tudo pronto para rodar o Codis."))
    print()


def main() -> int:
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    as_json = "--json" in sys.argv

    report = run_all_checks()

    if as_json:
        payload = {
            "checks": [
                {"name": c.name, "status": c.status,
                    "detail": c.detail, "fix": c.fix}
                for c in report.checks
            ],
            "has_errors": report.has_errors,
            "has_warnings": report.has_warnings,
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print_report(report, verbose=verbose)

    return 1 if report.has_errors else 0


if __name__ == "__main__":
    sys.exit(main())
