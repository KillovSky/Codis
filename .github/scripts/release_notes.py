#!/usr/bin/env python3
"""Gera as notas da release (Markdown) a partir do que foi realmente produzido.

Conteúdo:
* aviso do canal (nightly/dev) ou trecho do CHANGELOG.md da versão (stable);
* tabela de downloads montada com os arquivos que EXISTEM em ``ASSET_DIR``
  (plataformas experimentais que falharam simplesmente não aparecem);
* lista de commits desde a última release oficial, agrupada pelo prefixo
  convencional (feat, fix, perf...), quando o CHANGELOG não cobre a versão;
* instruções de verificação do ``SHA256SUMS.txt``.

Para releases oficiais, o GitHub ainda acrescenta, abaixo deste texto, as notas
automáticas categorizadas por *label* (ver ``.github/release.yml``).

Variáveis: VERSION, TAG, KIND (stable|nightly|dev), SHA, REPO (dono/nome),
ASSET_DIR, OUT.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

PLATFORMS = [
    ("windows-x64", "Windows x64"),
    ("windows-arm64", "Windows ARM64"),
    ("windows-x86", "Windows x86"),
    ("linux-x64", "Linux x64"),
    ("linux-arm64", "Linux ARM64"),
    ("macos-arm64", "macOS Apple Silicon"),
    ("macos-x64", "macOS Intel"),
]

SECTIONS = [
    ("feat", "✨ Novidades"),
    ("fix", "🐛 Correções"),
    ("perf", "⚡ Desempenho"),
    ("refactor", "♻️ Refatoração"),
    ("docs", "📚 Documentação"),
    ("deps", "📦 Dependências"),
    ("ci", "🔧 CI/CD e build"),
    ("build", "🔧 CI/CD e build"),
    ("test", "🧪 Testes"),
    ("chore", "🧹 Manutenção"),
]
CONVENTIONAL = re.compile(r"^(\w+)(?:\([^)]*\))?(!)?:\s*(.+)$")
MAX_COMMITS = 60


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def previous_stable(current_tag: str, include_prerelease: bool) -> str:
    """Tag oficial anterior mais recente (por versão), diferente da atual."""
    for tag in git("tag", "--list", "v[0-9]*", "--sort=-v:refname").splitlines():
        if tag == current_tag:
            continue
        if "-" in tag and not include_prerelease:
            continue
        return tag
    return ""


def changelog_section(version: str) -> str:
    path = ROOT / "CHANGELOG.md"
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    m = re.search(
        rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|\Z)", text, re.DOTALL | re.MULTILINE,
    )
    return m.group(1).strip() if m else ""


def commit_list(base: str, sha: str, repo: str) -> str:
    rng = f"{base}..{sha}" if base else sha
    args = ["log", "--no-merges", "--pretty=format:%h\t%s", rng]
    if not base:
        args.insert(1, f"-{MAX_COMMITS}")
    raw = git(*args)
    if not raw:
        return ""
    groups: dict[str, list[str]] = {}
    order = {title: i for i, (_, title) in enumerate(SECTIONS)}
    total = 0
    for line in raw.splitlines():
        short, _, subject = line.partition("\t")
        if subject.startswith("chore(i18n): sync locales") or "[skip ci]" in subject:
            continue
        total += 1
        if total > MAX_COMMITS:
            continue
        m = CONVENTIONAL.match(subject)
        title = "📌 Outras mudanças"
        text = subject
        if m:
            kind = m.group(1).lower()
            for key, sec in SECTIONS:
                if kind == key:
                    title, text = sec, m.group(3)
                    break
        link = f"[`{short}`](https://github.com/{repo}/commit/{short})" if repo else f"`{short}`"
        groups.setdefault(title, []).append(f"- {text} ({link})")
    lines: list[str] = []
    for title in sorted(groups, key=lambda t: order.get(t, 999)):
        lines += [f"#### {title}", "", *groups[title], ""]
    if total > MAX_COMMITS:
        lines.append(f"_…e mais {total - MAX_COMMITS} commit(s). Veja o comparativo completo abaixo._")
    return "\n".join(lines).strip()


def downloads_table(asset_dir: Path) -> str:
    files = sorted(p.name for p in asset_dir.iterdir() if p.is_file())
    rows = ["| Plataforma | Arquivo |", "|---|---|"]
    used: set[str] = set()
    for suffix, label in PLATFORMS:
        for name in files:
            if f"-{suffix}." in name:
                rows.append(f"| {label} | `{name}` |")
                used.add(name)
    for name in files:
        if name.endswith("-source.zip"):
            rows.append(f"| Código-fonte (ZIP) | `{name}` |")
            used.add(name)
    for name in files:
        if name.startswith("SHA256SUMS"):
            rows.append(f"| Checksums SHA-256 | `{name}` |")
            used.add(name)
    return "\n".join(rows)


def main() -> None:
    version, tag, kind = env("VERSION"), env("TAG"), env("KIND", "stable")
    sha, repo = env("SHA"), env("REPO")
    asset_dir = Path(env("ASSET_DIR", "release_files"))
    out = Path(env("OUT", "release_notes.md"))
    prerelease = "-" in version
    parts: list[str] = []

    if kind == "stable":
        body = changelog_section(version)
        parts.append(f"## Codis v{version}" + (f"\n\n{body}" if body else ""))
    else:
        label = "nightly" if kind == "nightly" else "desenvolvimento (develop)"
        parts.append(
            f"> ⚠️ **Build automático de {label}** do commit `{sha[:7]}` "
            f"(base: v{version}). Atualizado a cada commit com mudanças no código; "
            f"pode ser instável. Para uso normal, prefira a "
            f"[última release oficial](https://github.com/{repo}/releases/latest)."
        )

    parts.append("### Downloads\n\n" + downloads_table(asset_dir))
    parts.append(
        "**Verificar integridade:** `sha256sum -c SHA256SUMS.txt --ignore-missing` "
        "(no PowerShell: `Get-FileHash <arquivo>`).\n\n"
        "**Rodar pelo código-fonte:** `pip install -r requirements.txt && python run.py` "
        "(Python 3.12+ com Tkinter)."
    )

    base = previous_stable(tag, prerelease)
    if kind != "stable" or not changelog_section(version):
        commits = commit_list(base, sha, repo)
        if commits:
            heading = f"### Mudanças desde {base}" if base else "### Últimos commits"
            parts.append(f"{heading}\n\n{commits}")
    if base and repo:
        parts.append(f"**Comparativo completo:** https://github.com/{repo}/compare/{base}...{tag or sha}")

    out.write_text("\n\n".join(parts).strip() + "\n", encoding="utf-8")
    print(f"Notas geradas em {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
