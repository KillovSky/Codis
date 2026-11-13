#!/usr/bin/env python3
"""Planejador do pipeline do Codis.

Roda UMA vez no início de cada execução de ``pipeline.yml`` e decide, em um só
lugar, tudo o que os demais jobs precisam saber - assim nenhum job repete
cálculo e nada é executado duas vezes:

* qual versão / tag / canal de release esta execução produz;
* se há algo a validar (qualidade) e/ou compilar (build);
* quais plataformas entram na matriz;
* como os arquivos gerados serão nomeados.

Canais de release
-----------------
``stable``   push em main/master cuja versão (``APP_VERSION``) ainda não tem tag,
             ou push de tag ``vX.Y.Z``. Cria a release oficial ``vX.Y.Z``.
``nightly``  push em main/master quando a versão já foi lançada. Atualiza a
             pré-release móvel ``nightly`` (uma só; não acumula releases).
``dev``      push em develop. Atualiza a pré-release móvel ``dev``.
``none``     pull requests e demais refs: só valida/compila, não publica.

Só compila/publica quando algo que entra no binário mudou desde o último
build publicado (commits só de documentação não geram build nem release).

Entrada (variáveis de ambiente do GitHub Actions)::

    GITHUB_EVENT_NAME, GITHUB_REF, GITHUB_REF_NAME, GITHUB_SHA, GITHUB_OUTPUT,
    GITHUB_REPOSITORY, BASE_REF (PR), PR_NUMBER (PR), PR_HEAD_SHA (PR),
    INPUT_RELEASE (dispatch: auto|none), INPUT_EXTENDED (dispatch: true|false),
    GH_TOKEN (opcional; usado para consultar se a release já existe)

Uso local (simulação)::

    GITHUB_EVENT_NAME=push GITHUB_REF=refs/heads/main GITHUB_REF_NAME=main \
    GITHUB_SHA=$(git rev-parse HEAD) python .github/scripts/plan.py
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Caminhos cuja mudança exige validar o código (testes, lint, i18n...).
QUALITY_PATHS = (
    "src/", "tests/", "run.py", "requirements.txt", "sync_locales.py",
    "package_codis.py", "setup.py", "pyproject.toml", ".pylintrc",
    ".github/scripts/", ".github/workflows/pipeline.yml",
    ".github/workflows/_quality.yml", ".github/workflows/_build.yml",
)
# Caminhos que entram no binário / no ZIP de código-fonte.
BUILD_PATHS = (
    "src/", "run.py", "requirements.txt", "package_codis.py", "setup.py",
    "pyproject.toml", ".github/scripts/", ".github/workflows/pipeline.yml",
    ".github/workflows/_build.yml",
)

CORE_PLATFORMS = [
    {"label": "Linux x64", "os": "ubuntu-22.04", "platform": "linux",
     "suffix": "linux-x64", "ext": "tar.gz"},
    {"label": "Windows x64", "os": "windows-latest", "platform": "windows",
     "suffix": "windows-x64", "ext": "zip", "arch": "x64"},
    {"label": "macOS ARM64", "os": "macos-latest", "platform": "macos",
     "suffix": "macos-arm64", "ext": "zip"},
]
EXTRA_PLATFORMS = [
    {"label": "Linux ARM64", "os": "ubuntu-24.04-arm", "platform": "linux",
     "suffix": "linux-arm64", "ext": "tar.gz", "experimental": True},
    {"label": "Windows ARM64", "os": "windows-11-arm", "platform": "windows",
     "suffix": "windows-arm64", "ext": "zip", "arch": "arm64", "experimental": True},
    {"label": "Windows x86", "os": "windows-latest", "platform": "windows",
     "suffix": "windows-x86", "ext": "zip", "arch": "x86", "experimental": True},
    {"label": "macOS x64 (Intel)", "os": "macos-15-intel", "platform": "macos",
     "suffix": "macos-x64", "ext": "zip", "experimental": True},
]

MAIN_BRANCHES = ("main", "master")
ROLLING_TAGS = {"nightly": "nightly", "dev": "dev"}


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def git(*args: str) -> str | None:
    """Executa git no repositório; devolve stdout ou None se falhar."""
    try:
        res = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return res.stdout.strip()


def app_version() -> str:
    text = (ROOT / "src" / "constants.py").read_text(encoding="utf-8")
    m = re.search(r'^APP_VERSION\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    if not m:
        fail("APP_VERSION não encontrado em src/constants.py")
    return m.group(1)


def fail(msg: str) -> None:
    print(f"::error::{msg}")
    sys.exit(1)


def tag_exists(tag: str) -> bool:
    return git("rev-parse", "-q", "--verify", f"refs/tags/{tag}") is not None


def release_published(tag: str) -> bool:
    """True se já existe uma release publicada (não rascunho) para a tag."""
    if not env("GH_TOKEN"):
        return False
    try:
        res = subprocess.run(
            ["gh", "release", "view", tag, "--json", "isDraft", "--jq", ".isDraft"],
            cwd=ROOT, capture_output=True, text=True,
        )
    except OSError:
        return False
    return res.returncode == 0 and res.stdout.strip() == "false"


def stable_tags() -> list[str]:
    out = git("tag", "--list", "v[0-9]*") or ""
    return [t for t in out.splitlines() if t]


def newest_baseline(candidates: list[str]) -> str | None:
    """Entre as tags candidatas, devolve a de commit mais recente (ou None)."""
    best: tuple[int, str] | None = None
    for tag in candidates:
        ts = git("log", "-1", "--format=%ct", f"refs/tags/{tag}^{{commit}}")
        if ts and ts.isdigit() and (best is None or int(ts) > best[0]):
            best = (int(ts), tag)
    return best[1] if best else None


def changed_files(base: str, *, three_dot: bool = False) -> list[str] | None:
    spec = f"{base}...HEAD" if three_dot else f"{base}..HEAD"
    out = git("diff", "--name-only", spec)
    return None if out is None else [ln for ln in out.splitlines() if ln]


def matches(path: str, patterns: tuple[str, ...]) -> bool:
    return any(path == p or (p.endswith("/") and path.startswith(p)) for p in patterns)


def main() -> int:
    event = env("GITHUB_EVENT_NAME", "push")
    ref = env("GITHUB_REF")
    ref_name = env("GITHUB_REF_NAME")
    sha = env("PR_HEAD_SHA") or env("GITHUB_SHA") or (git("rev-parse", "HEAD") or "0" * 40)
    short = sha[:7]
    current = app_version()
    dispatch = event == "workflow_dispatch"
    is_tag = ref.startswith("refs/tags/")
    is_pr = event == "pull_request"

    release_input = env("INPUT_RELEASE", "auto").lower()
    extended_input = env("INPUT_EXTENDED", "true").lower() != "false"

    # ── 1. Canal de release ──────────────────────────────────────────────────
    version = current
    tag = ""
    kind = "none"
    reason = ""

    if is_pr:
        reason = "pull request: apenas valida e compila, sem publicar"
    elif is_tag:
        if not re.fullmatch(r"v\d+\.\d+\.\d+([-.+][0-9A-Za-z.\-+]+)?", ref_name):
            fail(f"Tag '{ref_name}' não segue o padrão vX.Y.Z")
        version = ref_name[1:]
        if version != current:
            fail(
                f"A tag {ref_name} não bate com APP_VERSION ({current}) em src/constants.py. "
                "Ajuste a versão no código ou use outra tag."
            )
        kind, tag = "stable", ref_name
        if dispatch and release_input == "none":
            kind, tag, reason = "none", "", "disparo manual com release=none"
        elif not dispatch and release_published(tag):
            kind, tag = "none", ""
            reason = f"a release {ref_name} já foi publicada: nada a fazer"
    elif ref_name in MAIN_BRANCHES:
        if dispatch and release_input == "none":
            reason = "disparo manual com release=none"
        elif not tag_exists(f"v{current}"):
            kind, tag = "stable", f"v{current}"
            reason = f"versão {current} ainda sem tag: gera a release oficial"
        else:
            kind, tag = "nightly", "nightly"
            reason = f"versão {current} já lançada: atualiza a pré-release 'nightly'"
    elif ref_name == "develop":
        if dispatch and release_input == "none":
            reason = "disparo manual com release=none"
        else:
            kind, tag = "dev", "dev"
            reason = "branch develop: atualiza a pré-release 'dev'"
    else:
        reason = f"ref '{ref_name}' não publica release"

    prerelease = kind in ROLLING_TAGS or (kind == "stable" and "-" in version)

    # ── 2. O que mudou desde o último build publicado? ───────────────────────
    force = dispatch or event == "schedule"
    baseline = ""
    files: list[str] | None = None
    if is_pr:
        files = changed_files(f"origin/{env('BASE_REF', 'main')}", three_dot=True)
        baseline = f"origin/{env('BASE_REF', 'main')}"
    elif not force:
        if kind == "dev":
            cands = ["dev"] if tag_exists("dev") else []
        elif kind in ("stable", "nightly"):
            cands = (["nightly"] if tag_exists("nightly") else []) + stable_tags()
        else:
            cands = []
        base_tag = newest_baseline(cands)
        if base_tag:
            baseline = base_tag
            files = changed_files(f"refs/tags/{base_tag}")

    if force or files is None:
        quality_changed = build_changed = True  # sem referência confiável: roda tudo
        changed_note = "todos os arquivos (sem baseline ou disparo manual)"
    else:
        quality_changed = any(matches(f, QUALITY_PATHS) for f in files)
        build_changed = any(matches(f, BUILD_PATHS) for f in files)
        changed_note = f"{len(files)} arquivo(s) desde {baseline}"

    run_quality = quality_changed
    # Release oficial sempre compila (a versão ainda não tem binários).
    run_build = build_changed or kind == "stable"
    run_release = kind != "none" and run_build

    if kind in ROLLING_TAGS and not run_build:
        kind, tag, prerelease = "none", "", False
        reason = f"nenhum arquivo de código mudou ({changed_note}): sem build nem release"
    elif not run_build and not run_quality and not reason:
        reason = f"somente documentação/metadados mudaram ({changed_note})"

    # ── 3. Nomes dos arquivos ────────────────────────────────────────────────
    if kind == "stable":
        stem = f"Codis-v{version}"
    elif kind in ROLLING_TAGS:
        stem = f"Codis-v{current}-{kind}.{short}"
    elif is_pr:
        stem = f"Codis-pr{env('PR_NUMBER', '0')}-{short}"
    else:
        safe = re.sub(r"[^0-9A-Za-z._-]+", "-", ref_name) or "ref"
        stem = f"Codis-{safe}-{short}"

    # ── 4. Matriz de plataformas ─────────────────────────────────────────────
    # Extras (ARM, x86, Intel) só em main/master/tag/disparo manual; nunca em PR/develop.
    wants_extras = (
        not is_pr and extended_input and (is_tag or dispatch or ref_name in MAIN_BRANCHES)
    )
    include = CORE_PLATFORMS + (EXTRA_PLATFORMS if wants_extras else [])

    out = {
        "version": version,
        "tag": tag,
        "kind": kind,
        "prerelease": str(prerelease).lower(),
        "stem": stem,
        "sha": sha,
        "run_quality": str(run_quality).lower(),
        "run_build": str(run_build).lower(),
        "run_release": str(run_release).lower(),
        "source": str(run_build and not is_pr).lower(),
        "retention": "7" if run_release else ("3" if is_pr else "5"),
        "matrix": json.dumps({"include": include}),
        "baseline": baseline,
        "reason": reason or "ok",
    }

    print("── Plano do pipeline ──")
    for k, v in out.items():
        print(f"{k:12} = {v if k != 'matrix' else f'{len(include)} plataforma(s)'}")
    path = env("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            for k, v in out.items():
                fh.write(f"{k}={v}\n")
    summary = env("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("## Plano\n\n| Item | Valor |\n|---|---|\n")
            fh.write(f"| Canal | `{kind}` {('→ `' + tag + '`') if tag else ''} |\n")
            fh.write(f"| Versão | `{version}` |\n")
            fh.write(f"| Validar (testes/lint) | {out['run_quality']} |\n")
            fh.write(f"| Compilar | {out['run_build']} |\n")
            fh.write(f"| Publicar release | {out['run_release']} |\n")
            fh.write(f"| Plataformas | {len(include)} |\n")
            fh.write(f"| Motivo | {out['reason']} |\n\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
