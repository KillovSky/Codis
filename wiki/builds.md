# 📦 Builds e Empacotamento

Esta página descreve como empacotar o Codis para distribuição.

---

## Script de Empacotamento

O Codis inclui o script `package_codis.py` na raiz do projeto para gerar distribuições.

```bash
python package_codis.py
```

---

## setup.py

O `setup.py` na raiz define os metadados do pacote Python. A versão **não é hardcoded** - é extraída dinamicamente de `APP_VERSION` em `src/constants.py` via regex, para nunca ficar dessincronizada:

```python
# setup.py (resumido)
import re
from pathlib import Path
from setuptools import setup, find_packages

def _find_version() -> str:
    """Extrai APP_VERSION de src/constants.py."""
    m = re.search(r'^APP_VERSION\s*=\s*["\']([^"\']+)["\']',
                  Path("src/constants.py").read_text(encoding="utf-8"), re.MULTILINE)
    return m.group(1)

setup(
    name="codis",
    version=_find_version(),
    python_requires=">=3.12",
    packages=find_packages(exclude=["tests*", "docs*", "wiki*", "build*", "dist*"]),
    install_requires=[],           # sem dependências obrigatórias
    extras_require={
        "recommended": ["send2trash>=1.8.0", "Pillow>=9.0.0", "markdown>=3.4.0"],
        "full": ["send2trash>=1.8.0", "Pillow>=9.0.0", "markdown>=3.4.0",
                 "py7zr>=0.20.0", "rarfile>=4.0"],
        "dev": ["pyinstaller", "pytest", "pytest-cov", "flake8", "pylint"],
    },
    entry_points={
        "console_scripts": ["codis = run:main"],
        "gui_scripts": ["codis-gui = run:main"],
    },
)
```

> `pip install .[dev]` instala as ferramentas de desenvolvimento (PyInstaller, pytest, flake8, pylint) de uma vez.

### Instalação via pip (modo desenvolvimento)

```bash
pip install -e .
```

Após isso, o comando `codis` fica disponível globalmente.

---

## Dependências

O `requirements.txt` lista pacotes opcionais que habilitam funcionalidades extras:

```
send2trash>=1.8.0    # lixeira nativa (fallback: pseudo-lixeira interna)
Pillow>=9.0.0        # imagens no K-UI
markdown>=3.4.0      # preview Markdown
py7zr>=0.20.0        # suporte a .7z
rarfile>=4.0         # suporte a .rar
```

**Nenhum deles é obrigatório.** O Codis executa com zero dependências externas.

---

## Empacotamento com PyInstaller

O Codis inclui um modo dedicado em `package_codis.py` que monta automaticamente todos os parâmetros corretos do PyInstaller - é a forma recomendada de gerar um executável, em vez de chamar `pyinstaller` manualmente:

```bash
pip install pyinstaller
python package_codis.py pyinstaller            # gera em modo --onedir (padrão)
python package_codis.py pyinstaller --onefile  # executável único em vez de pasta
python package_codis.py pyinstaller --debug    # mantém console para depuração
```

O padrão é um **executável único** (`--onefile`, mesmo modo do CI); use `--onedir` para gerar uma pasta (inicia mais rápido, pois não auto-extrai a cada execução).

Por baixo dos panos, o script monta automaticamente:

- **Dados incluídos:** os `src/locales/<idioma>.json` (14 idiomas; o `.translate_manifest.json` de desenvolvimento **não** entra), `wiki/` e `requirements.txt`
- **Tcl/Tk:** coletado pelo hook oficial do PyInstaller. **Não** adicione `--add-data` de Tcl/Tk manualmente (duplica os dados e, sem `DISPLAY`, pode embutir o prefixo inteiro do Python)
- **Imports ocultos:** todos os submódulos de `tkinter`, mais `src.plugins`, `src.ia`, `src.lsp`, `src.lint`, `src.syntax`, `src.diff`, `xmlrpc.client/server` e `email.mime.*`
- **Módulos excluídos** (reduzem o tamanho do bundle): `matplotlib`, `numpy`, `pandas`, `scipy`, `cv2`, `PyQt5/6`, `PySide2/6`, `wx`, entre outros nunca usados pelo Codis

Para nunca usar UPX (como no CI): `python package_codis.py pyinstaller --no-upx` ou `CODIS_NO_UPX=1`. O PyInstaller já ignora o UPX fora do Windows, e no executável `--onefile` final ele não reduz nada.

Tamanho de referência do executável Linux x64: **~32 MB**. O CI falha se passar de 60 MB.

> Se `send2trash` estiver instalado no ambiente de build, ele é incluído automaticamente no bundle. Sem ele, o executável final usa a lixeira do projeto (`.codis_trash/`) normalmente.

---

## Empacotamento com Nuitka (alternativa não-oficial)

> Diferente do PyInstaller, o Nuitka **não faz parte do pipeline oficial** do Codis (não há workflow de CI usando-o, nem script dedicado em `package_codis.py`). O comando abaixo é um ponto de partida não usado pelo projeto, caso prefira essa alternativa por conta própria.

```bash
pip install nuitka
python -m nuitka --onefile --enable-plugin=tk-inter \
    --include-data-dir=src/locales=src/locales \
    --include-data-dir=wiki=wiki \
    run.py
```

---

## GitHub Actions

O repositório inclui **10 workflows** em `.github/workflows/`. A regra de ouro: **cada commit passa por um único pipeline** (`pipeline.yml`), que valida, compila e publica a release **uma só vez** - os arquivos compilados são os mesmos do artefato e da release, e nada é refeito quando você lança uma versão.

### O pipeline (commit → build → release)

```text
push / tag / PR
      │
   plan ─────────────┬──► quality  (testes, lint, i18n)  ──┐
 (decide tudo        │                                      ├──► release ──► summary
  uma única vez)     └──► build    (executáveis + ZIP)    ──┘
```

| Job | O que faz |
|-----|-----------|
| `plan` | Roda `.github/scripts/plan.py`: lê `APP_VERSION`, descobre o canal de release, o que mudou desde o último build publicado e monta a matriz de plataformas |
| `quality` | Chama `_quality.yml`: sintaxe (Python 3.12/3.13), imports em Linux/Windows/macOS, Flake8 e Pylint (informativos), pytest com cobertura, i18n e consistência de locales |
| `build` | Chama `_build.yml`: executáveis PyInstaller de cada plataforma (com smoke test no Linux e limite de 60 MB) + ZIP do código-fonte. Roda **em paralelo** com `quality` |
| `release` | Só roda se `quality` e `build` passaram. Baixa os arquivos já compilados, gera `SHA256SUMS.txt` e as notas e publica a release. **Não recompila nada** |
| `summary` | Resumo da execução; falha o pipeline se alguma etapa obrigatória falhou |

### Canais de release

O canal é decidido pelo `plan`, sem nenhuma ação manual:

| Canal | Quando | Resultado |
|-------|--------|-----------|
| `stable` | Push em `main`/`master` quando `APP_VERSION` **ainda não tem tag**; ou push de tag `vX.Y.Z` | Release oficial `vX.Y.Z` (marcada como *Latest*; `-beta.N` etc. viram *pre-release*), com notas do `CHANGELOG.md` e notas automáticas por *label* |
| `nightly` | Push em `main`/`master` quando a versão **já foi lançada** | Uma única pré-release móvel chamada `nightly`, atualizada a cada commit com mudança de código (não acumula releases) |
| `dev` | Push em `develop` | Pré-release móvel `dev` (somente as 3 plataformas principais) |
| `none` | Pull request | Só valida e compila (3 plataformas principais); não publica |

Cada release traz, de uma vez: os executáveis (Windows x64, Linux x64, macOS Apple Silicon e, em `main`/tag, também Linux ARM64, Windows ARM64/x86 e macOS Intel, que são experimentais e nunca derrubam o pipeline), o **ZIP do código-fonte** (`Codis-vX.Y.Z-source.zip`), o `SHA256SUMS.txt` (usado pelo atualizador do app para conferir a integridade), as notas e o comparativo com a versão anterior.

Releases oficiais são criadas como **rascunho**, recebem todos os arquivos e só então são publicadas: nem usuários nem o atualizador enxergam uma release pela metade. Pré-releases `nightly`/`dev` **não** afetam o atualizador (ele lê apenas a última release oficial).

> **Commits só de documentação** (`*.md`, `wiki/`, `docs/`, `tools/`...) não compilam nem publicam release: o `plan` compara o commit com o último build publicado e só segue se algo que entra no binário mudou (`src/**`, `run.py`, `requirements.txt`, `package_codis.py`, `setup.py`, `pyproject.toml` ou os scripts/workflows de build).

### Todos os workflows

| Arquivo | Trigger | O que faz |
|---------|---------|-----------|
| `pipeline.yml` | Push em `main`/`master`/`develop`, tag `v*.*.*`, PR para `main`/`master`, `workflow_dispatch` (inputs `release` e `extended`) | **Porta de entrada única**: `plan` → `quality` ∥ `build` → `release` → `summary` |
| `_quality.yml` | Chamado pelo `pipeline.yml` | Verificações de código (reutilizável, sem triggers próprios) |
| `_build.yml` | Chamado pelo `pipeline.yml` | Executáveis multiplataforma + ZIP de código-fonte (reutilizável, **não publica**) |
| `docs.yml` | PR com mudanças em docs/código (`check`); push em `main` com mudanças em `docs/**`, `wiki/**`, `*.md`, `tools/**` ou `src/constants.py` (`site` + `deploy` + `wiki`); `workflow_dispatch` | PR: confere `tools/docs_facts.py --check` e `tools/build_site.py --check`. Push: gera `docs/` e publica no GitHub Pages; sincroniza `wiki/` com a GitHub Wiki **somente se `wiki/` mudou** |
| `codeql.yml` | Push/PR em `main`/`master`/`develop` com mudanças de código, e semanalmente (segunda 08:00 UTC) | Análise estática de segurança CodeQL (independente do pipeline; não bloqueia releases) |
| `dependency-review.yml` | PR com mudanças em `requirements.txt` ou `.github/dependabot.yml` | Bloqueia PRs com dependências de CVE alto/crítico e comenta o resumo no PR |
| `label.yml` | Abertura/edição/sync de PR, abertura/edição de issue | Rotulagem automática (as *labels* alimentam as notas de release por categoria) |
| `locale-sync.yml` | Somente `workflow_dispatch` (flag `apply`) | Ferramenta manual: sincroniza as chaves dos 14 locales e commita. A verificação automática já roda em `quality` |
| `artifact-cleanup.yml` | Semanalmente (segunda 07:00 UTC), ou `workflow_dispatch` (`dry_run`, `min_age_days`, `keep_last_builds`) | Remove artifacts antigos do Actions, protegendo as execuções mais recentes de `pipeline.yml` |
| `stale.yml` | Diariamente (06:00 UTC), ou `workflow_dispatch` | Marca issues/PRs inativos como stale |

As categorias das notas automáticas ficam em `.github/release.yml` (por *label*: `bug`, `melhoria`, `segurança`, `documentação`, `i18n`, `dependencies` etc.).

### Disparo manual

Em **Actions → Pipeline → Run workflow**:

- `release = auto` (padrão): comporta-se como um push no branch escolhido, ignorando o filtro de "o que mudou".
- `release = none`: valida e compila sem publicar (útil para testar o build).
- `extended = false`: só as 3 plataformas principais.
- Rodar a partir de uma **tag** (`Use workflow from: Tag: vX.Y.Z`) recompila e **atualiza os arquivos** da release dessa tag (reexecução segura, sem duplicar).

### Onde cada coisa roda (para não duplicar)

| Tarefa | Onde roda | Onde **não** roda mais |
|--------|-----------|------------------------|
| Checagem de sintaxe | `quality` (uma vez) | no build (antes havia outra) |
| ZIP do código-fonte | `build` (uma vez) | `package.yml` (removido) |
| Compilar executáveis | `build` (uma vez por commit) | `release.yml` (recompilava ao criar a tag) |
| Verificar locales | `quality` | `locale-sync.yml` em push/PR |
| Site, wiki e conferência de docs | `docs.yml` | três workflows separados |

> **Mapa do que mudou:** `ci.yml` → `_quality.yml`; `build.yml` + `package.yml` + `release.yml` → `_build.yml` + `pipeline.yml`; `docs-check.yml` + `pages.yml` + `wiki-sync.yml` → `docs.yml`. O badge de CI do README aponta para `pipeline.yml`.

---

## Testes

Os testes ficam em `tests/` - 70 arquivos, 1683 casos no total, rodando 100% headless (sem display gráfico):

```
tests/
├── conftest.py              ← Fixtures compartilhadas + stub de Tkinter
├── test_config.py           ← Testes do ConfigManager
├── test_diff_engine.py      ← Testes do DiffEngine
├── test_lsp_protocol.py     ← Testes do protocolo LSP
├── test_syntax_patterns.py  ← Testes de highlight (8 linguagens)
└── ... (mais 13 arquivos)
```

Executar todos os testes:

```bash
pytest tests/ -v
pytest tests/ --cov=src --cov-report=term-missing   # com cobertura
```

→ Veja [Testes](testes.md) para a cobertura completa por módulo, fixtures disponíveis e convenções.

---

## Distribuição como ZIP Portátil

O Codis pode ser distribuído como um ZIP portátil (sem instalação):

1. Clone o repositório.
2. Compacte o diretório: `zip -r Codis.zip .`
3. O usuário extrai e executa `python run.py` - **sem nenhum pip install**.

Para funcionalidades extras (lixeira nativa, imagens, etc.):
```bash
pip install send2trash Pillow markdown
```

---

## Versionamento

O Codis usa **Semantic Versioning** (SemVer):

- `MAJOR.MINOR.PATCH` - ex: `1.0.0`
- A versão é definida em `src/constants.py` (`APP_VERSION`)
- O codinome de cada versão é definido em `APP_CODENAME` (ex: `"Aether"`)

Para lançar uma versão **não é preciso criar tag nem rodar nada manualmente**:

1. Atualize `CHANGELOG.md` (seção `## [X.Y.Z]`) e `APP_VERSION` em `src/constants.py`.
2. Faça o commit/merge em `main`.

O `pipeline.yml` percebe que `vX.Y.Z` ainda não existe, valida, compila todas as plataformas, gera o ZIP do código-fonte e os checksums e publica a release oficial - tudo na mesma execução. Os commits seguintes (sem mudar a versão) apenas atualizam a pré-release `nightly`.

Também continua válido criar a tag manualmente (`git tag vX.Y.Z && git push origin vX.Y.Z`): ela precisa ser igual a `APP_VERSION`, e se a release já tiver sido publicada o pipeline não faz nada.

### Requisitos de runtime do executável compilado

O binário gerado por `package_codis.py pyinstaller` **não** embute Python para rodar
`pip`, `venv`, servidores LSP baseados em Python (`pylsp`, `pyright`...) nem o
code runner de Python: eles usam o Python do sistema (ver *Troubleshooting*). Ao
adicionar código que lança subprocessos Python, use `src/pyexec.py`
(`python_cmd()`, `child_env()`, `script_command()`) - **nunca `sys.executable`**,
que no executável compilado é o próprio Codis.

