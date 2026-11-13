"""Codis IA - Construtor de contexto automático para o assistente.

Responsabilidade única: reunir tudo que a IA precisa saber sobre o projeto
e o estado atual do editor, empacotando num bloco de contexto injetado
no system prompt antes de cada requisição.

Com esse módulo ativo, a IA não precisa perguntar onde está o workspace,
qual arquivo está aberto, qual linguagem é usada, ou como o projeto se
estrutura. Ela recebe tudo como contexto implícito - permitindo que até
tratamento de erro aqui" sejam compreendidos sem orientação extra.

Informações coletadas (todas opcionais/seguras para falhar)
-----------------------------------------------------------
Nível de projeto:
    - Nome, caminho e tipo de projeto (detectado via arquivos de config)
    - Linguagens detectadas com contagem de arquivos
    - Árvore de arquivos relevante (limitada, filtrada, com tamanhos)
    - Branch git atual, último commit e status de arquivos modificados
    - Dependências declaradas (primeiras entradas do arquivo de deps)
    - Resumo do README (primeiras linhas não-vazias)

Nível de arquivo ativo:
    - Caminho relativo e linguagem
    - Imports/includes do arquivo (para entender dependências internas)
    - Conteúdo da região ao redor do cursor (janela de +/-N linhas)
    - Diagnósticos ativos (erros/avisos do linter/LSP)
    - Estilo de código (indentação, encoding)
    - Seleção de texto ativa (quando há texto selecionado)

Uso típico (em iaspace.py)
--------------------------
    from src.ia.context_builder import build_context_block

    system_base = cfg.get("sistema", _DEFAULT_SYSTEM)
    system_completo = system_base + build_context_block(
        get_editor    = self._get_editor,
        get_workspace = self._get_workspace,
    )
"""

from __future__ import annotations

import contextlib
import logging
import re
import subprocess
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger("Codis.IA.ContextBuilder")

_MAX_CONTEXT_CHARS: int = 10_000

_MAX_TREE_LINES: int = 80

_MAX_README_LINES: int = 25

_MAX_SYMBOLS: int = 60

_CURSOR_WINDOW_LINES: int = 30

_MAX_IMPORTS: int = 20

_MAX_TODOS: int = 8

_IGNORED_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".pyc",
        ".pyo",
        ".pyd",
        ".so",
        ".dll",
        ".dylib",
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".svg",
        ".ico",
        ".mp3",
        ".mp4",
        ".wav",
        ".ogg",
        ".flac",
        ".zip",
        ".tar",
        ".gz",
        ".bz2",
        ".xz",
        ".7z",
        ".pdf",
        ".docx",
        ".xlsx",
        ".pptx",
        ".lock",
        ".sum",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".bin",
        ".exe",
        ".o",
        ".a",
    }
)

_IGNORED_DIRS: frozenset[str] = frozenset(
    {
        ".codis_trash",
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "node_modules",
        ".yarn",
        ".pnp",
        "venv",
        ".venv",
        "env",
        ".env",
        "dist",
        "build",
        "_build",
        "site",
        "out",
        ".idea",
        ".vscode",
        ".eclipse",
        "coverage",
        ".coverage",
        ".tox",
        ".nox",
    }
)

_EXT_TO_LANG: dict[str, str] = {
    ".py": "Python",
    ".pyx": "Cython",
    ".js": "JavaScript",
    ".mjs": "JavaScript (ESM)",
    ".ts": "TypeScript",
    ".jsx": "React/JSX",
    ".tsx": "React/TSX",
    ".java": "Java",
    ".kt": "Kotlin",
    ".rs": "Rust",
    ".go": "Go",
    ".rb": "Ruby",
    ".php": "PHP",
    ".cs": "C#",
    ".cpp": "C++",
    ".cc": "C++",
    ".cxx": "C++",
    ".c": "C",
    ".h": "C/C++ header",
    ".hpp": "C++ header",
    ".swift": "Swift",
    ".dart": "Dart",
    ".lua": "Lua",
    ".sh": "Shell",
    ".bash": "Bash",
    ".zsh": "Zsh",
    ".fish": "Fish",
    ".ps1": "PowerShell",
    ".html": "HTML",
    ".htm": "HTML",
    ".css": "CSS",
    ".scss": "SCSS/Sass",
    ".less": "Less",
    ".sql": "SQL",
    ".json": "JSON",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".toml": "TOML",
    ".xml": "XML",
    ".md": "Markdown",
    ".rst": "reStructuredText",
    ".r": "R",
    ".jl": "Julia",
    ".ex": "Elixir",
    ".exs": "Elixir Script",
    ".erl": "Erlang",
    ".hs": "Haskell",
    ".elm": "Elm",
    ".clj": "Clojure",
    ".vue": "Vue",
    ".svelte": "Svelte",
    ".tf": "Terraform",
    ".proto": "Protobuf",
    ".graphql": "GraphQL",
    ".gql": "GraphQL",
}

_DEPENDENCY_FILES: list[str] = [
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "setup.py",
    "setup.cfg",
    "Pipfile",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "Gemfile",
    "composer.json",
    "pubspec.yaml",
    "mix.exs",
]

_PROJECT_TYPE_MARKERS: dict[str, str] = {
    "pyproject.toml": "Python (pyproject)",
    "setup.py": "Python (setuptools)",
    "requirements.txt": "Python",
    "Pipfile": "Python (Pipenv)",
    "package.json": "Node.js / JavaScript",
    "Cargo.toml": "Rust",
    "go.mod": "Go",
    "pom.xml": "Java (Maven)",
    "build.gradle": "Java/Kotlin (Gradle)",
    "Gemfile": "Ruby",
    "composer.json": "PHP",
    "pubspec.yaml": "Dart / Flutter",
    "mix.exs": "Elixir",
    "CMakeLists.txt": "C/C++ (CMake)",
    "Makefile": "C/C++ (Make)",
}


def build_context_block(
    get_editor: Callable[[], Any] | None = None,
    get_workspace: Callable[[], Any] | None = None,
) -> str:
    """Constrói e retorna o bloco de contexto do projeto/editor.

    Retorna string vazia se não houver nada relevante a informar.
    Nunca levanta exceção - todas as coletas são envolvidas em try/except.

    Parameters
    ----------
    get_editor:
        Callable que retorna o EditorPane ativo, ou None.
    get_workspace:
        Callable que retorna o Path do workspace, ou None.
    """
    builder = _ContextBuilder(get_editor, get_workspace)
    return builder.build()


class _ContextBuilder:
    """Coleta e monta o bloco de contexto de forma controlada e segura."""

    def __init__(
        self,
        get_editor: Callable[[], Any] | None,
        get_workspace: Callable[[], Any] | None,
    ) -> None:
        """Inicializa o construtor de contexto de IA."""
        self._get_editor = get_editor
        self._get_workspace = get_workspace
        self._parts: list[str] = []
        self._chars_used: int = 0

    def build(self) -> str:
        """Constrói e retorna o bloco de contexto completo."""
        workspace = self._safe_workspace()
        editor = self._safe_editor()

        self._collect_project_header(workspace)
        self._collect_git_info(workspace)
        self._collect_project_tree(workspace)
        self._collect_readme(workspace)
        self._collect_dependencies(workspace)
        self._collect_active_file(editor, workspace)

        if not self._parts:
            return ""

        body = "\n\n".join(self._parts)
        return f"\n\n---\n## Context\n\n{body}\n---\n"

    def _collect_project_header(self, workspace: Path | None) -> None:
        """Registra nome, caminho, tipo e linguagens detectadas do workspace."""
        if workspace is None:
            self._add(
                "*Nenhum workspace aberto. Respondendo sem contexto de projeto.*")
            return

        proj_type = "desconhecido"
        for marker, label in _PROJECT_TYPE_MARKERS.items():
            if (workspace / marker).exists():
                proj_type = label
                break

        langs = self._detect_project_languages(workspace)
        langs_label = (
            ", ".join(
                f"{lang} ({count} arquivo{'s' if count != 1 else ''})"
                for lang, count in langs[:5]
            )
            if langs
            else "indeterminado"
        )

        self._add(
            f"**Projeto:** `{workspace.name}`  (`{workspace}`)  -  {proj_type}\n"
            f"**Linguagens:** {langs_label}"
        )

    def _collect_git_info(self, workspace: Path | None) -> None:
        """Branch atual, último commit e lista de arquivos modificados."""
        if workspace is None:
            return
        try:
            branch = self._run_git(
                ["git", "branch", "--show-current"], workspace)
            if not branch:
                return

            commit = self._run_git(
                ["git", "log", "-1", "--pretty=format:%h - %s"],
                workspace,
            )

            git_parts = [f"**Git:** branch `{branch}`"]
            if commit:
                git_parts.append(f"último commit: `{commit}`")

            git_status_raw = self._run_git(
                ["git", "status", "--short", "--porcelain"], workspace
            )
            if git_status_raw:
                modified = [
                    line[3:].strip()
                    for line in git_status_raw.splitlines()
                    if line.strip()
                ][:8]
                if modified:
                    git_parts.append(
                        "arquivos modificados: " +
                        ", ".join(f"`{f}`" for f in modified)
                    )

            self._add("  |  ".join(git_parts))
        except (OSError, FileNotFoundError):
            pass

    def _collect_project_tree(self, workspace: Path | None) -> None:
        """Lista hierárquica compacta dos arquivos relevantes do workspace."""
        if workspace is None or self._over_budget():
            return
        lines: list[str] = []
        self._walk_tree(workspace, workspace, 0, lines)
        if not lines:
            return
        tree_text = "\n".join(lines[:_MAX_TREE_LINES])
        self._add("### Estrutura do projeto\n```\n" + tree_text + "\n```")

    def _walk_tree(
        self,
        root: Path,
        target: Path,
        depth: int,
        out: list[str],
    ) -> None:
        """Percorre a árvore de diretórios de forma recursiva e limitada."""
        if depth > 4 or len(out) >= _MAX_TREE_LINES:
            return
        indent = "  " * depth
        try:
            entries = sorted(
                target.iterdir(),
                key=lambda p: (p.is_file(), p.name.lower()),
            )
        except OSError:
            return

        for entry in entries:
            if len(out) >= _MAX_TREE_LINES:
                break
            if entry.name.startswith(".") and entry.name not in {".env.example"}:
                continue
            if entry.is_dir():
                if entry.name in _IGNORED_DIRS:
                    continue
                out.append(f"{indent}{entry.name}/")
                self._walk_tree(root, entry, depth + 1, out)
            elif entry.is_file():
                if entry.suffix.lower() in _IGNORED_EXTENSIONS:
                    continue
                size = entry.stat().st_size
                size_label = f"{size // 1024} KB" if size >= 1024 else f"{size} B"
                out.append(f"{indent}{entry.name}  ({size_label})")

    def _collect_readme(self, workspace: Path | None) -> None:
        """Inclui as primeiras linhas não-triviais do README."""
        if workspace is None or self._over_budget():
            return
        for name in ("README.md", "README.rst", "README.txt", "README"):
            candidate = workspace / name
            if candidate.is_file():
                try:
                    lines = candidate.read_text(
                        "utf-8", errors="replace").splitlines()

                    meaningful = [
                        l_
                        for l_ in lines
                        if l_.strip() and not l_.strip().startswith("[![")
                    ][:_MAX_README_LINES]
                    if meaningful:
                        snippet = "\n".join(meaningful)
                        self._add(f"### README (resumo)\n```\n{snippet}\n```")
                except OSError:
                    pass
                return

    def _collect_dependencies(self, workspace: Path | None) -> None:
        """Informa arquivo de dependências e lista os pacotes principais."""
        if workspace is None or self._over_budget():
            return
        for dep_file in _DEPENDENCY_FILES:
            path = workspace / dep_file
            if not path.is_file():
                continue
            try:
                content = path.read_text("utf-8", errors="replace")
                lines = [
                    l_.strip()
                    for l_ in content.splitlines()
                    if l_.strip() and not l_.strip().startswith(("#", "//", ";", "["))
                ]
                preview = "\n".join(lines[:15])
                self._add(
                    f"### Dependências (`{dep_file}`)\n```\n{preview}\n```")
            except OSError:
                pass
            return

    def _collect_active_file(
        self,
        editor: Any,
        workspace: Path | None,
    ) -> None:
        """Informações detalhadas sobre o arquivo aberto no editor."""
        if editor is None:
            self._add("*Nenhum arquivo aberto no editor.*")
            return

        file_path: Path | None = getattr(editor, "path", None)
        lang: str = getattr(editor, "lang", "")
        lang_label = _EXT_TO_LANG.get(f".{lang}", lang or "desconhecida")

        if file_path:
            if workspace and file_path.is_relative_to(workspace):
                rel_path = str(file_path.relative_to(workspace))
            else:
                rel_path = str(file_path)
        else:
            rel_path = "(arquivo sem nome - ainda não salvo)"

        self._add(f"### Arquivo ativo: `{rel_path}`  -  {lang_label}")

        content: str | None = None
        with contextlib.suppress(tk.TclError):
            content = editor.text.get("1.0", "end-1c")
        if content is None and file_path:
            with contextlib.suppress(OSError):
                content = file_path.read_text("utf-8", errors="replace")

        cursor_line = self._collect_cursor_position(editor)

        if content:
            self._collect_imports(content, lang)

        if content:
            self._collect_symbols(content, lang)

        if content and cursor_line > 0:
            self._collect_cursor_region(content, cursor_line, lang)

        self._collect_selection(editor, lang)

        if content:
            self._collect_todos(content)

        self._collect_diagnostics(editor)

        if content:
            self._collect_style_hints(content)

    def _collect_cursor_position(self, editor: Any) -> int:
        """Linha e coluna atual do cursor. Retorna número da linha (1-based)."""
        try:
            index = editor.text.index("insert")
            row, col = index.split(".")
            row_int = int(row)
            self._add(f"**Cursor:** linha {row_int}, coluna {int(col) + 1}")
            return row_int
        except (tk.TclError, ValueError):
            return 0

    def _collect_imports(self, content: str, lang: str) -> None:
        """Lista os imports/includes do arquivo para orientar sobre dependências."""
        if self._over_budget():
            return
        lines = content.splitlines()
        imports: list[str] = []

        if lang == "python":
            for line in lines:
                stripped = line.strip()
                if stripped.startswith(("import ", "from ")):
                    imports.append(stripped)
                elif stripped and not stripped.startswith("#") and imports:
                    if not stripped.startswith(('"""', "'''", "#")):
                        break

        elif lang in ("javascript", "typescript", "jsx", "tsx"):
            import_re = re.compile(r"^(?:import|require|export)\s+")
            for line in lines[:50]:
                if import_re.match(line.strip()):
                    imports.append(line.strip())

        elif lang in ("java", "kotlin", "cs"):
            import_re = re.compile(r"^(?:import|using)\s+")
            for line in lines[:60]:
                if import_re.match(line.strip()):
                    imports.append(line.strip())

        elif lang == "go":
            in_import = False
            for line in lines[:40]:
                stripped = line.strip()
                if stripped.startswith("import ("):
                    in_import = True
                elif in_import and stripped == ")":
                    break
                elif in_import or stripped.startswith("import "):
                    imports.append(stripped)

        if imports:
            preview = "\n".join(imports[:_MAX_IMPORTS])
            self._add(f"**Imports:**\n```\n{preview}\n```")

    def _collect_symbols(self, content: str, lang: str) -> None:
        """Extrai símbolos (funções, classes, variáveis) do conteúdo por linguagem."""
        if self._over_budget():
            return
        symbols = _extract_symbols(content, lang)
        if not symbols:
            return
        lines_out = [f"  - {s}" for s in symbols[:_MAX_SYMBOLS]]
        self._add("**Símbolos definidos:**\n" + "\n".join(lines_out))

    def _collect_cursor_region(
        self,
        content: str,
        cursor_line: int,
        lang: str,
    ) -> None:
        """Exibe as linhas ao redor do cursor - região de maior relevância."""
        if self._over_budget():
            return
        lines = content.splitlines()
        total = len(lines)
        if total == 0:
            return

        start = max(0, cursor_line - _CURSOR_WINDOW_LINES - 1)
        end = min(total, cursor_line + _CURSOR_WINDOW_LINES)
        region = lines[start:end]

        numbered = "\n".join(
            f"{start + i + 1:4d}  {line}" for i, line in enumerate(region)
        )
        line_start = start + 1
        line_end = start + len(region)
        self._add(
            f"**Região do cursor (linhas {line_start}–{line_end}):**\n```{lang}\n{numbered}\n```"
        )

    def _collect_selection(self, editor: Any, lang: str) -> None:
        """Texto atualmente selecionado no editor (quando há seleção ativa)."""
        if self._over_budget():
            return
        try:
            selected = editor.text.get(tk.SEL_FIRST, tk.SEL_LAST)
            if selected and selected.strip():
                preview = selected[:800]
                if len(selected) > 800:
                    preview += "\n... (truncado)"
                self._add(f"**Texto selecionado:**\n```{lang}\n{preview}\n```")
        except tk.TclError:
            pass

    def _collect_todos(self, content: str) -> None:
        """Coleta comentários TODO/FIXME/HACK do conteúdo para enriquecer o contexto."""
        if self._over_budget():
            return
        todo_re = re.compile(
            r"(?:TODO|FIXME|HACK|XXX|BUG)\s*[:\-]?\s*(.+)", re.IGNORECASE
        )
        found: list[str] = []
        for i, line in enumerate(content.splitlines(), 1):
            m = todo_re.search(line)
            if m:
                found.append(f"  linha {i}: {line.strip()[:100]}")
                if len(found) >= _MAX_TODOS:
                    break
        if found:
            self._add("**TODOs/FIXMEs:**\n" + "\n".join(found))

    def _collect_diagnostics(self, editor: Any) -> None:
        """Erros e avisos do linter/LSP do arquivo ativo."""
        try:
            diags = getattr(editor, "_diags", []) or []
            if not diags:
                self._add("**Diagnósticos:** ✅ Nenhum erro ou aviso")
                return
            lines = []
            for d in diags[:10]:
                level = getattr(d, "level", "?").upper()
                lineno = getattr(d, "line", 0)
                msg = getattr(d, "message", str(d))
                source = getattr(d, "source", "")
                entry = f"  [{level}] linha {lineno}: {msg}"
                if source:
                    entry += f"  ({source})"
                lines.append(entry)
            self._add("**Diagnósticos:**\n" + "\n".join(lines))
        except AttributeError:
            pass

    def _collect_style_hints(self, content: str) -> None:
        """Indentação detectada - para manter consistência ao gerar código."""
        try:
            if not content:
                return
            uses_tabs = "\t" in content
            indent_char = "tabs" if uses_tabs else "espaços"
            indent_size: int | None = None
            for line in content.splitlines():
                if line.startswith("    "):
                    indent_size = 4
                    break
                if line.startswith("  "):
                    indent_size = 2
                    break
            size_label = f" ({indent_size} por nível)" if indent_size else ""
            self._add(f"**Estilo:** indentação com {indent_char}{size_label}")
        except (ValueError, AttributeError):
            pass

    @staticmethod
    def _detect_project_languages(workspace: Path) -> list[tuple[str, int]]:
        """Retorna as linguagens mais presentes no workspace com contagem."""
        counts: dict[str, int] = {}
        try:
            for p in workspace.rglob("*"):
                if p.is_file() and p.suffix.lower() in _EXT_TO_LANG:
                    if any(part in _IGNORED_DIRS for part in p.parts):
                        continue
                    lang = _EXT_TO_LANG[p.suffix.lower()]
                    counts[lang] = counts.get(lang, 0) + 1
        except OSError:
            pass
        return sorted(counts.items(), key=lambda x: -x[1])[:6]

    def _add(self, text: str) -> None:
        """Adiciona um trecho ao bloco, respeitando o orçamento de caracteres."""
        if self._chars_used + len(text) > _MAX_CONTEXT_CHARS:
            return
        self._parts.append(text)
        self._chars_used += len(text)

    def _over_budget(self) -> bool:
        return self._chars_used >= _MAX_CONTEXT_CHARS

    def _safe_editor(self) -> Any:
        """Retorna o editor ativo sem levantar exceção."""
        try:
            return self._get_editor() if self._get_editor else None
        except (AttributeError, RuntimeError):
            return None

    def _safe_workspace(self) -> Path | None:
        """Retorna o workspace Path sem levantar exceção."""
        try:
            return self._get_workspace() if self._get_workspace else None
        except (AttributeError, RuntimeError):
            return None

    @staticmethod
    def _run_git(args: list[str], cwd: Path) -> str:
        """Executa um comando git e retorna stdout limpo, ou string vazia."""
        try:
            result = subprocess.run(
                args,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            return result.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return ""


def _extract_symbols(content: str, lang: str) -> list[str]:
    """Extrai símbolos (classes, funções, etc.) de um arquivo de código fonte.

    Retorna lista de strings no formato ``"tipo NomeDoSímbolo (linha N)"``.
    Suporta as linguagens mais comuns com regexes leves - sem AST real.

    Args:
        content: Conteúdo completo do arquivo como texto.
        lang:    Identificador de linguagem no formato do Codis (ex: ``"python"``).
    """
    symbols: list[str] = []
    lines = content.splitlines()

    extractor = _SYMBOL_EXTRACTORS.get(lang) or _SYMBOL_EXTRACTORS.get(
        _LANG_ALIASES.get(lang, "")
    )
    if extractor is None:
        extractor = _extract_symbols_generic
    extractor(lines, symbols)
    return symbols

_LANG_ALIASES: dict[str, str] = {
    "jsx": "javascript",
    "tsx": "typescript",
    "c++": "cpp",
    "kotlin": "java",
}


def _extract_symbols_python(lines: list[str], out: list[str]) -> None:
    """Extrai classes, funções e funções assíncronas de Python."""
    pattern = re.compile(r"^(\s*)(class|def|async def)\s+(\w+)")
    for i, line in enumerate(lines, 1):
        m = pattern.match(line)
        if m:
            indent = len(m.group(1))
            prefix = "  " if indent > 0 else ""
            out.append(f"{prefix}{m.group(2)} {m.group(3)}  (linha {i})")


def _extract_symbols_js(lines: list[str], out: list[str]) -> None:
    """Extrai classes, funções e arrow functions de JavaScript/TypeScript."""
    cls_pat = re.compile(r"^(?:export\s+)?(?:abstract\s+)?class\s+(\w+)")
    fn_pat = re.compile(
        r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*(\w+)"
    )
    arrow_pat = re.compile(
        r"^(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?"
        r"(?:function|\(|[a-zA-Z_$][\w$]*\s*=>)"
    )
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if m := cls_pat.match(stripped):
            out.append(f"class {m.group(1)}  (linha {i})")
        elif m := fn_pat.match(stripped):
            out.append(f"function {m.group(1)}  (linha {i})")
        elif m := arrow_pat.match(stripped):
            out.append(f"const {m.group(1)} (arrow/fn)  (linha {i})")


def _extract_symbols_java(lines: list[str], out: list[str]) -> None:
    """Extrai classes, interfaces e métodos de Java/Kotlin."""
    cls_pat = re.compile(
        r"(?:public|private|protected|internal|open|data|abstract|sealed)?\s*"
        r"(?:class|interface|object|enum)\s+(\w+)"
    )
    fn_pat = re.compile(
        r"(?:public|private|protected|internal|override|suspend|fun|void)\s+(\w+)\s*\("
    )
    for i, line in enumerate(lines, 1):
        if line.strip().startswith("//"):
            continue
        if m := cls_pat.search(line):
            out.append(f"class {m.group(1)}  (linha {i})")
        elif m := fn_pat.search(line):
            out.append(f"  fun {m.group(1)}  (linha {i})")


def _extract_symbols_rust(lines: list[str], out: list[str]) -> None:
    """Extrai fn, struct, enum, trait, impl, mod e type de Rust."""
    _RUST_KINDS = {"fn", "struct", "enum", "trait", "impl", "mod", "type"}
    pat = re.compile(
        r"^(?:pub(?:\([^)]*\))?\s+)?(?:fn|struct|enum|trait|impl|mod|type)\s+(\w+)"
    )
    for i, line in enumerate(lines, 1):
        m = pat.match(line)
        if m:
            kind = next((t for t in line.strip().split()
                        if t in _RUST_KINDS), "fn")
            out.append(f"{kind} {m.group(1)}  (linha {i})")


def _extract_symbols_go(lines: list[str], out: list[str]) -> None:
    """Extrai func e type struct/interface de Go."""
    fn_pat = re.compile(r"^func\s+(?:\(.*?\)\s+)?(\w+)")
    typ_pat = re.compile(r"^type\s+(\w+)\s+(?:struct|interface)")
    for i, line in enumerate(lines, 1):
        if m := fn_pat.match(line):
            out.append(f"func {m.group(1)}  (linha {i})")
        elif m := typ_pat.match(line):
            out.append(f"type {m.group(1)}  (linha {i})")


def _extract_symbols_ruby(lines: list[str], out: list[str]) -> None:
    """Extrai def, class e module de Ruby."""
    pat = re.compile(r"^\s*(?:def|class|module)\s+([\w.]+)")
    for i, line in enumerate(lines, 1):
        m = pat.match(line)
        if m:
            kind = line.strip().split()[0]
            indent = "  " if line.startswith("  ") else ""
            out.append(f"{indent}{kind} {m.group(1)}  (linha {i})")


def _extract_symbols_php(lines: list[str], out: list[str]) -> None:
    """Extrai function, class, interface, trait e enum de PHP."""
    pat = re.compile(
        r"(?:function|class|interface|trait|enum)\s+(\w+)", re.IGNORECASE)
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("*"):
            continue
        m = pat.search(line)
        if m:
            kind = m.group(0).strip().split()[0].lower()
            out.append(f"{kind} {m.group(1)}  (linha {i})")


def _extract_symbols_c(lines: list[str], out: list[str]) -> None:
    """Extrai class, struct, namespace, enum e funções de C/C++."""
    cls_pat = re.compile(r"^(?:class|struct|namespace|enum)\s+(\w+)")
    fn_pat = re.compile(
        r"^(?:[\w:*&<>\s]+?)\s+(\w+)\s*\([^;]*\)\s*(?:const\s*)?(?:\{|$)"
    )
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("#"):
            continue
        if m := cls_pat.match(stripped):
            kind = stripped.split()[0]
            out.append(f"{kind} {m.group(1)}  (linha {i})")
        elif m := fn_pat.match(line):
            out.append(f"fn {m.group(1)}  (linha {i})")


def _extract_symbols_generic(lines: list[str], out: list[str]) -> None:
    """Extrai símbolos de linguagens não mapeadas usando padrão genérico."""
    pat = re.compile(
        r"^\s*(?:function|func|fn|def|proc|procedure|sub)\s+(\w+)",
        re.IGNORECASE,
    )
    for i, line in enumerate(lines, 1):
        m = pat.match(line)
        if m:
            out.append(f"function {m.group(1)}  (linha {i})")

_SymExtractor = Callable[[list[str], list[str]], None]
_SYMBOL_EXTRACTORS: dict[str, _SymExtractor] = {
    "python": _extract_symbols_python,
    "javascript": _extract_symbols_js,
    "typescript": _extract_symbols_js,
    "java": _extract_symbols_java,
    "rust": _extract_symbols_rust,
    "go": _extract_symbols_go,
    "ruby": _extract_symbols_ruby,
    "php": _extract_symbols_php,
    "c": _extract_symbols_c,
    "cpp": _extract_symbols_c,
}


class ContextBuilder:
    """Constrói um dicionário de contexto para envio ao assistente de IA.

    Ao contrário de ``_ContextBuilder`` (que coleta dados automaticamente
    do editor e workspace via callbacks), esta classe aceita dados fornecidos
    explicitamente pelo chamador - ideal para testes unitários e integrações
    programáticas.

    Suporta encadeamento fluente::

        ctx = (
            ContextBuilder()
            .set_code(\"def foo(): pass\")
            .set_language(\"python\")
            .set_file_path(\"/tmp/foo.py\")
            .set_cursor_position(line=1, col=0)
            .build()
        )

    Uso típico em código de produção
    ---------------------------------
        from src.ia.context_builder import ContextBuilder

        ctx = ContextBuilder().set_code(editor.get_text()).set_language(lang).build()
    """

    def __init__(self) -> None:
        """Inicializa o builder com estado vazio."""
        self._data: dict = {}

    def set_code(self, code: str) -> ContextBuilder:
        """Define o código-fonte a ser incluído no contexto."""
        self._data["code"] = code
        return self

    def set_language(self, language: str) -> ContextBuilder:
        """Define a linguagem do arquivo ativo (ex: ``\"python\"``)."""
        self._data["language"] = language
        return self

    def set_file_path(self, path: str) -> ContextBuilder:
        """Define o caminho do arquivo ativo."""
        self._data["file_path"] = path
        return self

    def set_selection(self, text: str) -> ContextBuilder:
        """Define o texto atualmente selecionado no editor."""
        self._data["selection"] = text
        return self

    def set_cursor_position(self, *, line: int, col: int) -> ContextBuilder:
        """Define a posição do cursor (linha e coluna, ambas 0-based ou 1-based)."""
        self._data["cursor"] = {"line": line, "col": col}
        return self

    def set_diagnostics(self, diagnostics: list) -> ContextBuilder:
        """Define a lista de diagnósticos do linter/LSP."""
        self._data["diagnostics"] = diagnostics
        return self

    def reset(self) -> ContextBuilder:
        """Limpa todos os dados acumulados, permitindo reutilização do builder."""
        self._data = {}
        return self

    def build(self) -> dict:
        """Retorna uma cópia do dicionário de contexto acumulado.

        Chamadas repetidas sem alterações intermediárias retornam dicionários
        equivalentes (idempotência). Modificar o retorno não afeta o estado
        interno do builder.
        """
        return dict(self._data)
