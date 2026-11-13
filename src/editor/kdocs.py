"""Codis - KDocs  -  Universal Documentation Engine.

KDocs é um sistema de documentação universal que:
  - Cobre JSDoc e outros formatos de documentação
  - Suporta 20+ linguagens com parsers nativos (Python, Rust, Go, Ruby,
    Lua, Doxygen/C/C++, C#/XML, Kotlin, Swift, PHP, Dart, R, Julia, etc.)
    Sphinx/reST, Rust ///, Go godoc, YARD, LDoc, Doxygen, Roxygen2, Haddock
  - Implementa anotações @kdocs: semânticas em qualquer comentário de qualquer
    linguagem, incluindo HTML <!-- -->, Lua --[[-]], SQL --
  - Metadados KDocs: [!due:YYYY-MM-DD], [!ticket:ID], URLs automáticas
  - Hover balloon rico com badge de linguagem, anti-debounce e prioridades
  - Cache LRU integrado para performance em arquivos grandes
"""

from __future__ import annotations

import contextlib
import logging
import re
import textwrap
import tkinter as tk
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from src.i18n import _t

logger = logging.getLogger("Codis")

_MAX_USAGES: int = 200
_CACHE_MAX_SIZE: int = 64
_SCOPE_SCAN_LIMIT: int = 600

_DEF_PATTERNS: list[re.Pattern] = [
    re.compile(
        r"^\s*(?:export\s+)?(?:const|let|var)\s+([$\w]+)\s*(?:[:,=]|$)"),
    re.compile(
        r"^\s*(?:export\s+)?(?:async\s+)?function\s*\*?([$\w]+)\s*[\(<]"),
    re.compile(r"^\s*(?:export\s+)?(?:abstract\s+)?class\s+([$\w]+)"),
    re.compile(r"^\s*([$\w]+)\s*=\s*(?:function|class|\(|async)"),
    re.compile(r"^\s*([$\w]+)\s*:\s*[a-zA-Z]"),
    re.compile(r"^\s*(?:async\s+)?def\s+([$\w]+)\s*\("),
    re.compile(r"^\s*class\s+([$\w]+)\s*[:(]"),
    re.compile(r"^\s*([$\w]+)\s*=\s*"),
    re.compile(r"^\s*import\s+([$\w]+)\s+from"),
    re.compile(r"^\s*from\s+\S+\s+import\s+.*([$\w]+)"),
    re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+([a-zA-Z_]\w*)"),
    re.compile(
        r"^\s*(?:pub\s+)?(?:struct|enum|trait|type|union)\s+([a-zA-Z_]\w*)"),
    re.compile(r"^\s*(?:pub\s+)?(?:static|const)\s+([A-Z_][A-Z0-9_]*)"),
    re.compile(r"^\s*func\s+(?:\([^)]*\)\s+)?([a-zA-Z_]\w*)"),
    re.compile(r"^\s*type\s+([a-zA-Z_]\w*)\s+(?:struct|interface)"),
    re.compile(r"^\s*(?:var|const)\s+([a-zA-Z_]\w*)"),
    re.compile(r"^\s*def\s+([a-zA-Z_]\w*[?!]?)"),
    re.compile(r"^\s*(?:class|module)\s+([A-Z]\w*)"),
    re.compile(r"^\s*(?:local\s+)?function\s+([a-zA-Z_][\w.]*)"),
    re.compile(r"^\s*([a-zA-Z_][\w.]*)\s*=\s*function"),
]


class DocStyle(Enum):
    """Estilo de documentação de uma linguagem."""

    JSDOC = "jsdoc"
    PYTHON = "python"
    RUST = "rust"
    GO = "go"
    DOXYGEN = "doxygen"
    RDOC = "rdoc"
    LDOC = "ldoc"
    HASH = "hash"
    DASH = "dash"
    HTML = "html"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class LanguageProfile:
    """Perfil de uma linguagem: sintaxe de comentários e estilo de doc."""

    name: str
    extensions: tuple[str, ...]
    line_comments: tuple[str, ...]
    block_open: str
    block_close: str
    doc_style: DocStyle
    doc_block_open: str = "/**"
    uses_indent_scope: bool = False


def _build_lang_map() -> dict[str, LanguageProfile]:
    """Constrói a seção 'lang map' do componente."""
    profiles = [
        LanguageProfile(
            "javascript",
            (".js", ".mjs", ".cjs"),
            ("//",),
            "/*",
            "*/",
            DocStyle.JSDOC,
            "/**",
        ),
        LanguageProfile(
            "typescript",
            (".ts", ".tsx", ".d.ts"),
            ("//",),
            "/*",
            "*/",
            DocStyle.JSDOC,
            "/**",
        ),
        LanguageProfile("jsx", (".jsx",), ("//",), "/*",
                        "*/", DocStyle.JSDOC, "/**"),
        LanguageProfile(
            "python",
            (".py", ".pyw", ".pyi"),
            ("#",),
            '"""',
            '"""',
            DocStyle.PYTHON,
            '"""',
            True,
        ),
        LanguageProfile(
            "rust", (".rs",), ("///", "//!",
                               "//"), "/*", "*/", DocStyle.RUST, "/**"
        ),
        LanguageProfile("go", (".go",), ("//",), "/*",
                        "*/", DocStyle.GO, "/**"),
        LanguageProfile("java", (".java",), ("//",), "/*",
                        "*/", DocStyle.JSDOC, "/**"),
        LanguageProfile(
            "kotlin", (".kt", ".kts"), ("//",
                                        ), "/*", "*/", DocStyle.JSDOC, "/**"
        ),
        LanguageProfile(
            "swift", (".swift",), ("//",), "/*", "*/", DocStyle.JSDOC, "/**"
        ),
        LanguageProfile(
            "csharp", (".cs",), ("//",
                                 "///"), "/*", "*/", DocStyle.DOXYGEN, "///"
        ),
        LanguageProfile(
            "cpp",
            (".cpp", ".cc", ".cxx", ".hpp", ".h"),
            ("//",),
            "/*",
            "*/",
            DocStyle.DOXYGEN,
            "/**",
        ),
        LanguageProfile("c", (".c",), ("//",), "/*",
                        "*/", DocStyle.DOXYGEN, "/**"),
        LanguageProfile(
            "ruby",
            (".rb", ".rake"),
            ("#",),
            "=begin",
            "=end",
            DocStyle.RDOC,
            "##",
            True,
        ),
        LanguageProfile("lua", (".lua",), ("--",),
                        "--[[", "]]", DocStyle.LDOC, "---"),
        LanguageProfile("sql", (".sql",), ("--",), "/*",
                        "*/", DocStyle.DASH, "--"),
        LanguageProfile(
            "shell", (".sh", ".bash", ".zsh"), ("#",
                                                ), "#", "", DocStyle.HASH, "##"
        ),
        LanguageProfile("yaml", (".yml", ".yaml"), ("#",),
                        "#", "", DocStyle.HASH, "#"),
        LanguageProfile(
            "html", (".html", ".htm"), ("<!--",
                                        ), "<!--", "-->", DocStyle.HTML, "<!--"
        ),
        LanguageProfile("css", (".css",), (), "/*",
                        "*/", DocStyle.JSDOC, "/**"),
        LanguageProfile(
            "scss", (".scss", ".sass"), ("//",
                                         ), "/*", "*/", DocStyle.JSDOC, "/**"
        ),
        LanguageProfile(
            "php", (".php",), ("//", "#"), "/*", "*/", DocStyle.JSDOC, "/**"
        ),
        LanguageProfile("dart", (".dart",), ("//",), "/*",
                        "*/", DocStyle.JSDOC, "/**"),
        LanguageProfile(
            "elixir", (".ex", ".exs"), ("#",
                                        ), '"""', '"""', DocStyle.PYTHON, '"""'
        ),
        LanguageProfile("r", (".r", ".R"), ("#",),
                        "#", "", DocStyle.HASH, "#'"),
        LanguageProfile("julia", (".jl",), ("#",), "#=",
                        "=#", DocStyle.PYTHON, '"""'),
        LanguageProfile(
            "haskell", (".hs", ".lhs"), ("--",
                                         ), "{-", "-}", DocStyle.DASH, "{-|"
        ),
        LanguageProfile("toml", (".toml",), ("#",),
                        "#", "", DocStyle.HASH, "#"),
    ]
    mapping: dict[str, LanguageProfile] = {}
    for p in profiles:
        for ext in p.extensions:
            mapping[ext] = p
    return mapping

_LANG_MAP: dict[str, LanguageProfile] = _build_lang_map()
_GENERIC_PROFILE = LanguageProfile(
    "generic", (), ("//", "#", "--"), "/*", "*/", DocStyle.UNKNOWN
)


def get_language_profile(filename: str) -> LanguageProfile:
    """Retorna o perfil de linguagem para a extensão do arquivo."""
    if not filename:
        return _GENERIC_PROFILE
    dot = filename.rfind(".")
    if dot == -1:
        return _GENERIC_PROFILE
    return _LANG_MAP.get(filename[dot:].lower(), _GENERIC_PROFILE)


class _DocCache:
    """
    Cache LRU (Least Recently Used) para armazenar resultados de parsing de documentação.

    Esta classe implementa um cache simples baseado em `OrderedDict` que mantém
    os itens mais recentemente acessados no final. Quando o limite de tamanho é
    atingido, o item menos recentemente usado (no início) é removido.

    Atributos:
        _maxsize (int): Número máximo de itens no cache.
        _store (OrderedDict): Dicionário ordenado que armazena os itens.
        _hits (int): Contador de acessos com sucesso (cache hit).
        _misses (int): Contador de acessos sem sucesso (cache miss).

    Uso típico:
        cache = _DocCache(maxsize=64)
        resultado = cache.get(conteudo, "tag")
        if resultado is None:
            resultado = processar(conteudo)
            cache.put(conteudo, "tag", resultado)
    """

    __slots__ = ("_maxsize", "_store", "_hits", "_misses")

    def __init__(self, maxsize: int = _CACHE_MAX_SIZE) -> None:
        """
        Inicializa o cache com um tamanho máximo.

        Args:
            maxsize (int): Capacidade máxima do cache. Padrão: _CACHE_MAX_SIZE (64).
        """
        self._maxsize = maxsize
        self._store: OrderedDict = OrderedDict()
        self._hits = 0
        self._misses = 0

    @staticmethod
    def _key(content: str, tag: str) -> int:
        """
        Gera uma chave hash única para identificar um conteúdo no cache.

        A chave é composta pelo comprimento do conteúdo, pelos primeiros 64 caracteres,
        pelos últimos 64 caracteres e pela tag. Isso equilibra unicidade e desempenho.

        Args:
            content (str): Conteúdo do arquivo ou texto a ser cacheado.
            tag (str): Identificador adicional (ex.: tipo de parsing, linguagem).

        Returns:
            int: Valor hash que servirá como chave no dicionário.
        """
        return hash((len(content), content[:64], content[-64:], tag))

    def get(self, content: str, tag: str) -> Any | None:
        """
        Recupera um valor do cache, se existir.

        Se a chave estiver presente, o item é movido para o final do OrderedDict
        (marcado como recentemente usado) e o contador de hits é incrementado.
        Caso contrário, incrementa misses e retorna None.

        Args:
            content (str): Conteúdo associado à chave.
            tag (str): Tag associada à chave.

        Returns:
            Any | None: O valor armazenado, ou None se não encontrado.
        """
        k = self._key(content, tag)
        if k in self._store:
            self._store.move_to_end(k)
            self._hits += 1
            return self._store[k]
        self._misses += 1
        return None

    def put(self, content: str, tag: str, value: Any) -> None:
        """
        Insere ou atualiza um valor no cache.

        Se a chave já existir, o valor é substituído e movido para o final.
        Após a inserção, se o número de itens ultrapassar _maxsize, o item
        menos recentemente usado (primeiro do OrderedDict) é removido.

        Args:
            content (str): Conteúdo associado à chave.
            tag (str): Tag associada à chave.
            value (Any): Valor a ser armazenado (geralmente o resultado do parsing).
        """
        k = self._key(content, tag)
        self._store[k] = value
        self._store.move_to_end(k)
        while len(self._store) > self._maxsize:
            self._store.popitem(last=False)

_cache = _DocCache()


@dataclass
class KDocsNotes:
    """
    Representa uma anotação @kdocs: extraída do código-fonte.

    Atributos clássicos (KDocs 2.0):
        line: Número da linha (base 1).
        content: Texto principal da anotação.
        file_ref, line_ref: Referência para @kdocs:link / @kdocs:see.
        tags: Lista de tags [#tag1,tag2].
        assignee: Responsável [@usuario].
        priority: Prioridade (0=low, 1=normal, 2=high, 3=critical).
        type_name: Nome do tipo para @kdocs:type.
        symbols: Lista de símbolos vinculados [simbolo].
        scope_start, scope_end: Escopo do símbolo seguinte (base 1).
        icon, color: Calculados automaticamente.

    Atributos KDocs:
        body_lines: Linhas de continuação multi-linha.
        severity: Severidade computada (0=info, 1=low, 2=med, 3=high).
        language: Linguagem detectada.
        due_date: Prazo [!due:YYYY-MM-DD].
        ticket: ID de ticket [!ticket:ID].
        url: URL extraída automaticamente do conteúdo.
    """

    line: int
    kind: str
    content: str
    file_ref: str = ""
    line_ref: int = 0
    tags: list[str] = field(default_factory=list)
    assignee: str = ""
    priority: int = 0
    type_name: str = ""
    symbols: list[str] = field(default_factory=list)
    scope_start: int = 0
    scope_end: int = 0
    icon: str = ""
    color: str = ""
    body_lines: list[str] = field(default_factory=list)
    severity: int = 0
    language: str = ""
    due_date: str = ""
    ticket: str = ""
    url: str = ""

    def __post_init__(self) -> None:
        """Executa validação e ajustes pós-inicialização do dataclass."""
        self.icon = KDocsParser.icon(self.kind)
        self.color = KDocsParser.color(self.kind)
        self.severity = self._compute_severity()

    def _compute_severity(self) -> int:
        """Calcula e retorna o nível de severidade do bloco IrisDoc atual."""
        _sev = {
            "note": 0,
            "context": 0,
            "step": 0,
            "example": 0,
            "param": 0,
            "return": 0,
            "returns": 0,
            "type": 0,
            "link": 0,
            "see": 0,
            "symbol": 0,
            "doc": 0,
            "todo": 1,
            "question": 1,
            "debug": 1,
            "refactor": 1,
            "test": 1,
            "hack": 2,
            "perf": 2,
            "fixme": 2,
            "warn": 2,
            "deprecated": 2,
            "throws": 2,
            "review": 2,
            "important": 3,
            "security": 3,
            "breaking": 3,
            "mutex": 3,
        }
        return min(3, _sev.get(self.kind, 1) + max(0, self.priority - 1))

    @property
    def full_text(self) -> str:
        """Texto completo incluindo linhas de continuação."""
        parts = [self.content] + self.body_lines
        return "\n".join(p for p in parts if p)

    def to_dict(self) -> dict[str, Any]:
        """
        Serializa a anotação em um dicionário plano.

        Útil para persistência, exportação ou envio via API.

        Returns:
        """
        return {
            "line": self.line,
            "kind": self.kind,
            "content": self.content,
            "file_ref": self.file_ref,
            "line_ref": self.line_ref,
            "tags": self.tags,
            "assignee": self.assignee,
            "priority": self.priority,
            "severity": self.severity,
            "type_name": self.type_name,
            "symbols": self.symbols,
            "scope_start": self.scope_start,
            "scope_end": self.scope_end,
            "due_date": self.due_date,
            "ticket": self.ticket,
            "url": self.url,
            "body_lines": self.body_lines,
        }


class KDocsParser:
    """
    Analisador de anotações @kdocs: para qualquer linguagem.

    KDocs suporta:
      - Prefixos de linha: //, #, --, ---, *, <!--
      - Blocos: /* ... */, <!-- ... -->
      - Multi-linha: linhas de continuação com mesmo prefixo
      - Metadados avançados: [!due:YYYY-MM-DD], [!ticket:ID], URLs automáticas
      - Cache LRU integrado

    Exemplos válidos em qualquer linguagem:
        // @kdocs:warn [fs] Não use sync em produção! [@alice] [!high] [#nosync]
        -- @kdocs:note Consulta lenta, verificar índice
        <!-- @kdocs:deprecated Esta API será removida -->
    """

    _LINE_RE: re.Pattern = re.compile(
        r"(?:<!--|(?://+|#{1,2}|--{1,3}|\*)\s*)@kdocs:([\w]+)\s*(.*?)(?:-->)?\s*$",
        re.MULTILINE,
    )

    _BLOCK_RE: re.Pattern = re.compile(
        r"/\*+\s*@kdocs:([\w]+)\s*(.*?)\*/|<!--\s*@kdocs:([\w]+)\s*(.*?)-->",
        re.DOTALL,
    )

    _CONT_RE: re.Pattern = re.compile(
        r"^\s*(?://+|#{1,2}|--{1,3}|\*)\s{0,2}(.*)")

    _ASSIGNEE_RE: re.Pattern = re.compile(r"\[@([\w.\-]+)\]")
    _PRIORITY_RE: re.Pattern = re.compile(
        r"\[!(low|normal|high|critical)\]", re.I)
    _TAGS_RE: re.Pattern = re.compile(r"\[#([\w,.\-]+)\]")
    _SYMBOL_RE: re.Pattern = re.compile(r"\[([a-zA-Z_$][\w$]*)\]")
    _TYPE_NAME_RE: re.Pattern = re.compile(r"^([a-zA-Z_][\w]*)(?:\s+)?(.*?)$")
    _DUE_RE: re.Pattern = re.compile(r"\[!due:([\d\-]+)\]", re.I)
    _TICKET_RE: re.Pattern = re.compile(r"\[!ticket:([\w\-]+)\]", re.I)
    _URL_RE: re.Pattern = re.compile(r"https?://\S+")

    KIND_ICONS: dict[str, str] = {
        "note": "💬",
        "todo": "✅",
        "warn": "⚠️",
        "deprecated": "🚫",
        "link": "🔗",
        "see": "👁",
        "param": "📥",
        "return": "📤",
        "returns": "📤",
        "fixme": "🔧",
        "hack": "🩹",
        "perf": "⚡",
        "security": "🔒",
        "context": "📋",
        "test": "🧪",
        "debug": "🐛",
        "refactor": "♻️",
        "question": "❓",
        "important": "❗",
        "step": "🪜",
        "example": "📖",
        "throws": "💥",
        "type": "🔠",
        "symbol": "🔤",
        "api": "🌐",
        "breaking": "💔",
        "review": "👀",
        "doc": "📄",
        "measure": "📏",
        "mutex": "🔐",
        "invariant": "📐",
    }

    KIND_COLORS: dict[str, str] = {
        "note": "#89b4fa",
        "todo": "#a6e3a1",
        "warn": "#fab387",
        "deprecated": "#f38ba8",
        "link": "#7dcfff",
        "see": "#cba6f7",
        "param": "#89dceb",
        "return": "#f9e2af",
        "returns": "#f9e2af",
        "fixme": "#ff9e64",
        "hack": "#e0af68",
        "perf": "#f9e2af",
        "security": "#f38ba8",
        "context": "#cdd6f4",
        "test": "#94e2d5",
        "debug": "#f38ba8",
        "refactor": "#cba6f7",
        "question": "#f9e2af",
        "important": "#f38ba8",
        "step": "#a6e3a1",
        "example": "#89b4fa",
        "throws": "#f38ba8",
        "type": "#89dceb",
        "symbol": "#89dceb",
        "api": "#89b4fa",
        "breaking": "#f38ba8",
        "review": "#cba6f7",
        "doc": "#89b4fa",
        "measure": "#f9e2af",
        "mutex": "#f38ba8",
        "invariant": "#94e2d5",
    }

    _PRIORITY_MAP: dict[str, int] = {
        "low": 0,
        "normal": 1,
        "high": 2,
        "critical": 3,
    }

    @classmethod
    def parse(cls, content: str, language: str = "") -> list[KDocsNotes]:
        """
        Extrai todas as anotações @kdocs: do conteúdo.

        Suporta qualquer linguagem. Detecção automática de:
          - Comentários de linha (//, #, --, ---, <!--)
          - Blocos /* ... */ e <!-- ... -->
          - Continuação multi-linha (linhas seguintes com mesmo prefixo)
            [symbol], [!due:date], [!ticket:id]

        Returns:
            Lista de KDocsNotes ordenada por linha.
        """
        cache_key = f"kdocs3:{language}"
        cached = _cache.get(content, cache_key)
        if cached is not None:
            return cached

        lines = content.splitlines()
        annotations: list[KDocsNotes] = []
        seen: set[int] = set()

        i = 0
        while i < len(lines):
            raw_line = lines[i]

            bm = cls._BLOCK_RE.search(raw_line)
            if bm and (i + 1) not in seen:
                kind = (bm.group(1) or bm.group(3) or "").lower()
                text = (bm.group(2) or bm.group(4) or "").strip()
                ann = cls._build(kind, text, i + 1, lines, [], language)
                if ann:
                    annotations.append(ann)
                    seen.add(i + 1)
                i += 1
                continue

            lm = cls._LINE_RE.search(raw_line)
            if lm and (i + 1) not in seen:
                kind = lm.group(1).lower()
                text = (lm.group(2) or "").strip()

                body: list[str] = []
                j = i + 1
                while j < len(lines) and j - i < 9:
                    cm = cls._CONT_RE.match(lines[j])
                    if cm and "@kdocs:" not in lines[j] and "@" not in lines[j][:20]:
                        ct = cm.group(1).strip()
                        if ct and ct not in ("", "/", "*", "-", "--"):
                            body.append(ct)
                            j += 1
                            continue
                    break

                ann = cls._build(kind, text, i + 1, lines, body, language)
                if ann:
                    annotations.append(ann)
                    seen.add(i + 1)

            i += 1

        result = sorted(annotations, key=lambda a: a.line)
        _cache.put(content, cache_key, result)
        return result

    @classmethod
    def _build(
        cls,
        kind: str,
        raw: str,
        lineno: int,
        lines: list[str],
        body: list[str],
        lang: str,
    ) -> KDocsNotes | None:
        """Constrói KDocsNotes a partir de texto bruto."""

        raw, symbols = cls._pop(cls._SYMBOL_RE, raw, multi=True)
        kind_n, raw = cls._type_name(kind, raw)
        raw, due = cls._pop_single(cls._DUE_RE, raw)
        raw, ticket = cls._pop_single(cls._TICKET_RE, raw)
        raw, assignee = cls._pop_single(cls._ASSIGNEE_RE, raw)
        raw, priority = cls._pop_priority(raw)
        raw, tags = cls._pop_tags(raw)
        url = cls._url(raw)
        file_ref, lr = cls._link_ref(kind, raw)
        ss, se = cls._scope(lines, lineno)

        return KDocsNotes(
            line=lineno,
            kind=kind,
            content=raw,
            file_ref=file_ref,
            line_ref=lr,
            tags=tags,
            assignee=assignee,
            priority=priority,
            type_name=kind_n,
            symbols=symbols,
            scope_start=ss,
            scope_end=se,
            body_lines=body,
            language=lang,
            due_date=due,
            ticket=ticket,
            url=url,
        )

    @classmethod
    def _pop(
        cls, pattern: re.Pattern, text: str, multi: bool = False
    ) -> tuple[str, Any]:
        """Remove matches do texto; retorna (cleaned, values)."""
        if multi:
            vals: list[str] = []
            for m in pattern.finditer(text):
                v = m.group(1)
                if v not in vals:
                    vals.append(v)
            return pattern.sub("", text).strip(), vals
        m = pattern.search(text)
        if m:
            return pattern.sub("", text).strip(), m.group(1)
        return text, ""

    @classmethod
    def _pop_single(cls, pattern: re.Pattern, text: str) -> tuple[str, str]:
        """Executa 'pop single' e retorna o resultado.

        Args:
            pattern: Parâmetro da operação.
            text: Dado textual a processar.
        """
        m = pattern.search(text)
        if m:
            return pattern.sub("", text).strip(), m.group(1)
        return text, ""

    @classmethod
    def _pop_priority(cls, text: str) -> tuple[str, int]:
        """Executa 'pop priority' e retorna o resultado.

        Args:
            text: Dado textual a processar.
        """
        m = cls._PRIORITY_RE.search(text)
        if m:
            p = cls._PRIORITY_MAP.get(m.group(1).lower(), 0)
            return cls._PRIORITY_RE.sub("", text).strip(), p
        return text, 0

    @classmethod
    def _pop_tags(cls, text: str) -> tuple[str, list[str]]:
        """Executa 'pop tags' e retorna o resultado.

        Args:
            text: Dado textual a processar.
        """
        m = cls._TAGS_RE.search(text)
        if m:
            tags = [t.strip() for t in m.group(1).split(",") if t.strip()]
            return cls._TAGS_RE.sub("", text).strip(), tags
        return text, []

    @classmethod
    def _type_name(cls, kind: str, text: str) -> tuple[str, str]:
        """Executa 'type name' e retorna o resultado.

        Args:
            kind: Parâmetro da operação.
            text: Dado textual a processar.
        """
        if kind != "type":
            return "", text
        m = cls._TYPE_NAME_RE.match(text)
        if m:
            return m.group(1).strip(), m.group(2).strip()
        return "", text

    @classmethod
    def _url(cls, text: str) -> str:
        m = cls._URL_RE.search(text)
        return m.group(0) if m else ""

    @classmethod
    def _link_ref(cls, kind: str, text: str) -> tuple[str, int]:
        """Executa 'link ref' e retorna o resultado.

        Args:
            kind: Parâmetro da operação.
            text: Dado textual a processar.
        """
        if kind not in ("link", "see") or not text:
            return "", 0
        parts = text.split("#", 1)
        fr = parts[0].strip()
        lr = 0
        if len(parts) > 1:
            with contextlib.suppress(ValueError):
                lr = int(parts[1])
        return fr, lr

    @classmethod
    def _scope(cls, lines: list[str], lineno: int) -> tuple[int, int]:
        """Determina o escopo do símbolo seguinte à anotação."""
        idx = lineno
        while idx < len(lines):
            s = lines[idx].strip()
            if s and not s.startswith(("//", "#", "*", "/*", "--", "<!--")):
                break
            idx += 1
        if idx >= len(lines):
            return 0, 0
        ss = idx + 1
        se = cls._scope_end(lines, idx) + 1
        return ss, max(ss, se)

    @classmethod
    def _scope_end(cls, lines: list[str], start: int) -> int:
        """Fecha escopo por indentação (Python) ou chaves (C-family)."""
        ln = lines[start] if start < len(lines) else ""

        if ln.rstrip().endswith(":"):
            base = len(ln) - len(ln.lstrip())
            limit = min(start + _SCOPE_SCAN_LIMIT, len(lines))
            for k in range(start + 1, limit):
                row = lines[k]
                if not row.strip():
                    continue
                if len(row) - len(row.lstrip()) <= base:
                    return k - 1
            return limit - 1

        depth = found = 0
        limit = min(start + _SCOPE_SCAN_LIMIT, len(lines))
        for k in range(start, limit):
            for ch in lines[k]:
                if ch == "{":
                    depth += 1
                    found = 1
                elif ch == "}":
                    depth -= 1
                    if found and depth == 0:
                        return k
        return start

    @classmethod
    def resolve_hover(
        cls,
        word: str,
        cursor_line: int,
        annotations: list[KDocsNotes],
        _: str,
    ) -> KDocsNotes | None:
        """
        Resolve qual anotação exibir no hover.

        Prioridade:
          1. Cursor na linha da anotação
          2. Cursor na linha imediatamente após
          3. word nos símbolos vinculados E cursor no escopo
          4. Cursor dentro do escopo de qualquer anotação
        """
        if not annotations:
            return None

        for ann in annotations:
            if ann.line in (cursor_line, cursor_line - 1):
                return ann

        for ann in annotations:
            if word and word in ann.symbols and (
                ann.scope_start == 0
                or ann.scope_start <= cursor_line <= ann.scope_end
            ):
                return ann

        for ann in annotations:
            if ann.scope_start and ann.scope_end:
                if ann.scope_start <= cursor_line <= ann.scope_end:
                    return ann

        return None

    @classmethod
    def icon(cls, kind: str) -> str:
        """Retorna o emoji/ícone associado ao tipo de anotação.

        Args:

        Returns:
            str: Emoji correspondente, ou ``"📌"`` se o tipo for desconhecido.
        """
        return cls.KIND_ICONS.get(kind, "📌")

    @classmethod
    def color(cls, kind: str) -> str:
        """Retorna a cor hexadecimal associada ao tipo de anotação.

        Args:
            kind (str): Tipo da anotação (ex.: ``"security"``, ``"note"``).

        Returns:
            str: Cor no formato ``"#rrggbb"``, ou ``"#cdd6f4"`` como fallback.
        """
        return cls.KIND_COLORS.get(kind, "#cdd6f4")


class JSDocParser:
    """
    Parser universal de documentação  (KDocs).

    Suporta JSDoc/TSDoc e, adicionalmente:

      - JSDoc 3 / TSDoc completo (JS/TS/PHP/Dart/Kotlin/Swift)
      - Python docstrings: Google style, NumPy style, Sphinx/reST
      - Rust doc comments (///)
      - Go godoc (// antes de declarações)
      - Doxygen (C/C++ /** ... */ com @param / \\brief)
      - C# XML doc comments (/// <summary>)
      - YARD / RDoc (Ruby ##)
      - LDoc (Lua ---)
      - R Roxygen2 (#')
      - Haddock (Haskell {-|)

    Uso básico:
        docs = JSDocParser.parse(content)
        docs = JSDocParser.parse_file(content, "main.py")
        result = JSDocParser.resolve_hover(word, line, docs)
        sections = JSDocParser.format_sections_for_mode(name, doc, mode)
    """

    JSDOC_BLOCK: re.Pattern = re.compile(r"/\*\*([\s\S]*?)\*/", re.MULTILINE)
    TAG_RE: re.Pattern = re.compile(
        r"@([\w.\-]+)((?:\s+|\{)[^\n@]*)?", re.MULTILINE)
    TYPE_RE: re.Pattern = re.compile(r"^\{([^}]+)\}")
    OPT_TYPE_RE: re.Pattern = re.compile(r"^\{[=?]?([^}]+)[=?]?\}")
    UNION_RE: re.Pattern = re.compile(r"^\{([^}]+\|[^}]+)\}")

    _NAME_TAGS = frozenset(
        {
            "param",
            "arg",
            "argument",
            "prop",
            "property",
            "typedef",
            "callback",
            "event",
            "name",
            "alias",
            "borrows",
            "lends",
            "memberof",
            "template",
            "tparam",
        }
    )
    _TYPE_ONLY_TAGS = frozenset(
        {
            "type",
            "returns",
            "return",
            "yields",
            "yield",
            "throws",
            "exception",
            "error",
        }
    )
    _FLAG_TAGS = frozenset(
        {
            "private",
            "protected",
            "public",
            "readonly",
            "static",
            "abstract",
            "virtual",
            "override",
            "deprecated",
            "ignore",
            "hideconstructor",
            "generator",
            "async",
            "inner",
            "instance",
            "global",
            "package",
            "pure",
            "const",
            "unsafe",
            "inline",
            "final",
            "sealed",
            "nocollapse",
        }
    )

    _SYMBOL_PATTERNS: list[str] = [
        (
            r"^(?:export\s+)?(?:default\s+)?(?:declare\s+)?"
            r"(?:async\s+)?function\s*\*?([a-zA-Z_$][\w$]*)"
        ),
        r"^(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+([a-zA-Z_$][\w$]*)",
        (
            r"^(?:export\s+)?(?:declare\s+)?"
            r"(?:interface|type|enum)\s+([a-zA-Z_$][\w$]*)"
        ),
        (
            r"^(?:export\s+)?(?:declare\s+)?(?:const|let|var)\s+([a-zA-Z_$][\w$]*)"
            r"(?::[^=]*)?\s*=\s*(?:async\s+)?(?:function|\(|class)"
        ),
        r"^(?:export\s+)?(?:declare\s+)?(?:const|let|var)\s+([a-zA-Z_$][\w$]*)",
        (
            r"^(?:(?:public|private|protected|static|async|override|abstract)\s+)*"
            r"(?:async\s+)?([a-zA-Z_$][\w$]*)\s*\("
        ),
        r"^([a-zA-Z_$][\w$]*)\s*(?:=[^=]|\:)",
        r"^(?:async\s+)?def\s+([a-zA-Z_][\w]*)",
        r"^class\s+([a-zA-Z_][\w]*)",
        r"^(?:pub\s+)?(?:pub\(crate\)\s+)?(?:async\s+)?fn\s+([a-zA-Z_][\w]*)",
        r"^(?:pub\s+)?(?:struct|enum|trait|type|union|impl)\s+([a-zA-Z_][\w]*)",
        r"^(?:pub\s+)?(?:static|const)\s+([A-Z_][A-Z0-9_]*)",
        r"^func\s+(?:\([^)]*\)\s+)?([a-zA-Z_][\w]*)",
        r"^type\s+([a-zA-Z_][\w]*)\s+(?:struct|interface)",
        r"^(?:var|const)\s+([a-zA-Z_][\w]*)",
        r"^def\s+([a-zA-Z_][\w]*[?!]?)",
        r"^(?:class|module)\s+([A-Z][\w]*)",
        r"^(?:attr_reader|attr_writer|attr_accessor)\s+:([a-z_][\w]*)",
        (
            r"^(?:(?:public|private|protected|static|final|async|"
            r"override|sealed|virtual|abstract)\s+)*"
            r"[\w<>\[\],\s]+\s+([a-zA-Z_$][\w$]*)\s*\("
        ),
        (
            r"^(?:(?:static|inline|extern|virtual|explicit|constexpr)\s+)*"
            r"[\w:*&<>]+\s+([a-zA-Z_][\w]*)\s*\("
        ),
        (
            r"^(?:public|private|protected|static|abstract|final)?"
            r"\s*function\s+([a-zA-Z_][\w]*)"
        ),
        r"^(?:local\s+)?function\s+([a-zA-Z_][\w.]*)",
        r"^([a-zA-Z_][\w.]*)\s*=\s*function",
        r"^(?:function\s+)?([a-zA-Z_][\w]*)\s*\(\)",
        r"^([a-zA-Z_.][a-zA-Z0-9_.]*)\s*<-\s*function",
    ]
    _COMPILED_PATTERNS: list[re.Pattern] = [
        re.compile(p) for p in _SYMBOL_PATTERNS]

    _GOOGLE_SECT_RE: re.Pattern = re.compile(
        r"^\s{0,4}([A-Z][A-Za-z\s]+):\s*$", re.MULTILINE
    )
    _NUMPY_SECT_RE: re.Pattern = re.compile(
        r"^([A-Z][A-Za-z\s]+)\n[-=]+\s*$", re.MULTILINE
    )
    _SPHINX_PARAM_RE: re.Pattern = re.compile(
        r":param\s+(?:([^:]+)\s+)?(\w+):\s*(.*?)(?=\n\s*:|$)", re.DOTALL
    )
    _SPHINX_TYPE_RE: re.Pattern = re.compile(
        r":type\s+(\w+):\s*(.*?)(?=\n\s*:|$)", re.DOTALL
    )
    _SPHINX_RET_RE: re.Pattern = re.compile(
        r":returns?:\s*(.*?)(?=\n\s*:|$)", re.DOTALL
    )
    _SPHINX_RTYPE_RE: re.Pattern = re.compile(
        r":rtype:\s*(.*?)(?=\n\s*:|$)", re.DOTALL)
    _SPHINX_RAISE_RE: re.Pattern = re.compile(
        r":raises?\s+(\w+):\s*(.*?)(?=\n\s*:|$)", re.DOTALL
    )
    _DOXYGEN_TAG_RE: re.Pattern = re.compile(
        r"[@\\](param|tparam|returns?|throws?|brief|details|note|warning|"
        r"deprecated|since|author|version|see|pre|post|invariant|"
        r"todo|bug|attention|code|endcode)\b\s*(.*?)(?=[@\\]|\*/|$)",
        re.DOTALL,
    )
    _XML_TAG_RE: re.Pattern = re.compile(
        r"<(\w+)(?:\s+[^>]*)?>([^<]*)</\1>", re.DOTALL)
    _RUST_DOC_RE: re.Pattern = re.compile(
        r"^[ \t]*(?:///!?|//!)\s?(.*)", re.MULTILINE)

    @classmethod
    def parse(cls, content: str) -> dict[str, dict]:
        """
        Analisa documentação no estilo JSDoc/TSDoc.
        """
        cached = _cache.get(content, "jsdoc")
        if cached is not None:
            return cached
        lines = content.splitlines()
        result = cls._parse_jsdoc_file(content, lines)
        _cache.put(content, "jsdoc", result)
        return result

    @classmethod
    def parse_file(cls, content: str, filename: str) -> dict[str, dict]:
        """
        Analisa documentação detectando automaticamente o estilo
        de acordo com a extensão do arquivo.

        Suporta Python, Rust, Go, Ruby, Lua, C/C++, C#, e mais.
        Para linguagens sem suporte específico, usa JSDoc.

        Args:
            content: Conteúdo do arquivo.
            filename: Nome/caminho do arquivo para detecção de linguagem.

        Returns:
            Dicionário {symbol: doc_dict}.
        """
        profile = get_language_profile(filename)
        cache_key = f"udoc:{profile.name}"
        cached = _cache.get(content, cache_key)
        if cached is not None:
            return cached

        lines = content.splitlines()
        style = profile.doc_style

        if style == DocStyle.PYTHON:
            result = cls._parse_python_file(content, lines)
        elif style == DocStyle.RUST:
            result = cls._parse_rust_file(content, lines)
        elif style == DocStyle.GO:
            result = cls._parse_go_file(content, lines)
        elif style == DocStyle.DOXYGEN:
            result = cls._parse_doxygen_file(content, lines)
        elif style == DocStyle.RDOC:
            result = cls._parse_ruby_file(content, lines)
        elif style == DocStyle.LDOC:
            result = cls._parse_lua_file(content, lines)
        else:
            result = cls._parse_jsdoc_file(content, lines)

        for doc in result.values():
            doc.setdefault("_lang", profile.name)
            doc.setdefault("_style", style.value)

        _cache.put(content, cache_key, result)
        return result

    @classmethod
    def _parse_jsdoc_file(cls, _: str, lines: list[str]) -> dict[str, dict]:
        """Executa 'parse jsdoc file' e retorna o resultado.

        Args:
            _: Parâmetro da operação.
            lines: Parâmetro da operação.
        """
        docs: dict[str, dict] = {}
        i = 0
        while i < len(lines):
            if not lines[i].strip().startswith("/**"):
                i += 1
                continue
            block_lines: list[str] = []
            while i < len(lines):
                block_lines.append(lines[i])
                if "*/" in lines[i]:
                    break
                i += 1
            block = "\n".join(block_lines)
            symbol, sym_idx = cls._next_symbol(lines, i + 1)
            parsed = cls._parse_block(block)
            body_end = cls._body_end(lines, sym_idx)
            parsed["_symbol_line"] = sym_idx + 1
            parsed["_body_start"] = sym_idx + 1
            parsed["_body_end"] = body_end + 1
            if symbol:
                key = f"{symbol}__line_{sym_idx + 1}" if symbol in docs else symbol
                docs[key] = parsed
                for e in parsed.get("tags", {}).get("name", []):
                    alt = (e.get("description") or "").strip()
                    if alt and alt != symbol and alt not in docs:
                        docs[alt] = parsed
            else:
                docs[f"__line_{i + 1}"] = parsed
            i += 1
        return docs

    @classmethod
    def _parse_block(cls, block: str) -> dict:
        """Analisa um bloco /** ... */ em estrutura rica."""
        raw_lines = block.strip().splitlines()
        cleaned: list[str] = []
        for line in raw_lines:
            line = line.strip()
            line = re.sub(r"^/\*\*", "", line)
            line = re.sub(r"\*/$", "", line)
            line = re.sub(r"^\*\s?", "", line)
            cleaned.append(line.rstrip())

        desc_parts: list[str] = []
        tags: dict[str, list[dict]] = {}
        cur_tag: str | None = None
        cur_entry: dict | None = None

        for line in cleaned:
            if not line:
                if cur_tag is None:
                    desc_parts.append("")
                elif cur_entry is not None:
                    cur_entry["description"] += "\n"
                continue

            if line.lstrip().startswith("@"):
                m = re.match(r"@([\w.\-]+)\s*(.*)", line.lstrip())
                if not m:
                    if cur_entry is not None:
                        cur_entry["description"] += " " + line
                    continue

                tag = m.group(1).lower()
                rest = m.group(2).strip()
                if tag not in tags:
                    tags[tag] = []

                entry: dict[str, Any] = {
                    "type": "",
                    "name": "",
                    "description": "",
                    "optional": False,
                    "default": None,
                }

                if tag in cls._FLAG_TAGS:
                    entry["description"] = rest
                    tags[tag].append(entry)
                    cur_tag, cur_entry = tag, entry
                    continue

                tm = cls.OPT_TYPE_RE.match(rest)
                if tm:
                    raw_t = tm.group(0)
                    inner = tm.group(1).strip()
                    if raw_t.startswith(("{=", "{?")) or raw_t.endswith(("=}", "?}")):
                        entry["optional"] = True
                    entry["type"] = inner
                    rest = rest[len(raw_t):].strip()

                if tag in cls._NAME_TAGS:
                    om = re.match(r"\[([^\]=]+)(?:=([^\]]+))?\]", rest)
                    if om:
                        entry["name"] = om.group(1)
                        entry["optional"] = True
                        if om.group(2) is not None:
                            entry["default"] = om.group(2)
                        rest = rest[om.end():].strip()
                        rest = re.sub(r"^[-:]\s*", "", rest)
                        entry["description"] = rest
                    else:
                        parts = rest.split(None, 1)
                        if parts:
                            entry["name"] = parts[0].lstrip(".")
                            r2 = parts[1] if len(parts) > 1 else ""
                            entry["description"] = re.sub(r"^[-:]\s*", "", r2)
                elif tag in cls._TYPE_ONLY_TAGS:
                    entry["description"] = rest
                else:
                    entry["description"] = rest

                tags[tag].append(entry)
                cur_tag, cur_entry = tag, entry

            else:
                if cur_entry is not None:
                    sep = "\n" if line.startswith(" ") else " "
                    cur_entry["description"] = (
                        cur_entry["description"].rstrip() + sep + line
                    ).strip()
                else:
                    desc_parts.append(line)

        desc = "\n".join(desc_parts).strip()
        desc = re.sub(r"\n{3,}", "\n\n", desc)
        return {"description": desc, "tags": tags}

    @classmethod
    def _parse_python_file(cls, _: str, lines: list[str]) -> dict[str, dict]:
        """Analisa docstrings Python (Google, NumPy, Sphinx)."""
        docs: dict[str, dict] = {}
        i = 0
        while i < len(lines):
            stripped = lines[i].strip()
            sym_m = None
            for pat in cls._COMPILED_PATTERNS[7:9]:
                m = pat.match(stripped)
                if m:
                    sym_m = m
                    break
            if sym_m:
                symbol = sym_m.group(1)
                sym_idx = i

                j = i + 1
                while j < len(lines) and not lines[j].strip():
                    j += 1
                if j < len(lines):
                    ds = lines[j].strip()
                    quote = None
                    if ds.startswith(('"""', 'r"""')):
                        quote = '"""'
                    elif ds.startswith(("'''", "r'''")):
                        quote = "'''"
                    if quote:
                        inner, j = cls._collect_docstring(lines, j, quote)
                        parsed = cls._parse_python_docstring(inner)
                        be = cls._body_end(lines, sym_idx)
                        parsed["_symbol_line"] = sym_idx + 1
                        parsed["_body_start"] = sym_idx + 1
                        parsed["_body_end"] = be + 1
                        key = (
                            f"{symbol}__line_{sym_idx + 1}"
                            if symbol in docs
                            else symbol
                        )
                        docs[key] = parsed
            i += 1
        return docs

    @classmethod
    def _collect_docstring(
        cls, lines: list[str], start: int, quote: str
    ) -> tuple[str, int]:
        """Coleta linhas de uma docstring multi-linha."""
        first = lines[start].strip()
        after_open = first[first.index(quote) + 3:]
        j = start + 1

        if quote in after_open:
            inner = after_open[: after_open.index(quote)]
            return inner.strip(), j

        collected = [after_open]
        while j < len(lines):
            ln = lines[j]
            if quote in ln:
                collected.append(ln[: ln.index(quote)])
                j += 1
                break
            collected.append(ln)
            j += 1
        return "\n".join(collected).strip(), j

    @classmethod
    def _parse_python_docstring(cls, raw: str) -> dict:
        """Auto-detecta Google, NumPy, ou Sphinx e analisa."""
        raw = textwrap.dedent(raw).strip()
        if cls._NUMPY_SECT_RE.search(raw):
            return cls._parse_numpy(raw)
        if cls._SPHINX_PARAM_RE.search(raw):
            return cls._parse_sphinx(raw)
        if cls._GOOGLE_SECT_RE.search(raw):
            return cls._parse_google(raw)
        return {"description": raw, "tags": {}}

    @classmethod
    def _parse_google(cls, raw: str) -> dict:
        """Analisa Google-style docstring."""
        _map = {
            "Args": "param",
            "Arguments": "param",
            "Parameters": "param",
            "Params": "param",
            "Returns": "returns",
            "Return": "returns",
            "Yields": "yields",
            "Yield": "yields",
            "Raises": "throws",
            "Raise": "throws",
            "Except": "throws",
            "Attributes": "property",
            "Note": "remarks",
            "Notes": "remarks",
            "Example": "example",
            "Examples": "example",
            "Todo": "todo",
            "References": "see",
        }
        sections = cls._split_google_sections(raw)
        tags: dict[str, list[dict]] = {}
        desc = sections.pop("__desc__", "").strip()
        for name, body in sections.items():
            key = _map.get(name, name.lower())
            if key in ("example", "remarks", "see", "todo"):
                tags.setdefault(key, []).append(
                    {
                        "type": "",
                        "name": "",
                        "description": body.strip(),
                        "optional": False,
                        "default": None,
                    }
                )
                continue
            for line in body.splitlines():
                line = line.strip()
                if not line:
                    continue
                m = re.match(r"^(\w+)\s*(?:\(([^)]+)\))?\s*:\s*(.*)", line)
                if m:
                    tags.setdefault(key, []).append(
                        {
                            "type": (m.group(2) or "").strip(),
                            "name": m.group(1),
                            "description": m.group(3).strip(),
                            "optional": "optional" in (m.group(2) or "").lower(),
                            "default": None,
                        }
                    )
                elif tags.get(key):
                    tags[key][-1]["description"] += " " + line
        return {"description": desc, "tags": tags}

    @classmethod
    def _split_google_sections(cls, raw: str) -> dict[str, str]:
        """Executa 'split google sections' e retorna o resultado.

        Args:
            raw: Parâmetro da operação.
        """
        result: dict[str, str] = {"__desc__": ""}
        current, buf = "__desc__", []
        for line in raw.splitlines():
            m = re.match(r"^\s{0,4}([A-Z][A-Za-z\s]+):\s*$", line)
            if m:
                result[current] = "\n".join(buf).strip()
                current, buf = m.group(1).strip(), []
            else:
                buf.append(line)
        result[current] = "\n".join(buf).strip()
        return result

    @classmethod
    def _parse_numpy(cls, raw: str) -> dict:
        """Analisa NumPy-style docstring."""
        _map = {
            "Parameters": "param",
            "Returns": "returns",
            "Yields": "yields",
            "Raises": "throws",
            "Attributes": "property",
            "Notes": "remarks",
            "Examples": "example",
            "References": "see",
            "See Also": "see",
            "Other Parameters": "param",
        }
        sections = cls._split_numpy_sections(raw)
        tags: dict[str, list[dict]] = {}
        desc = sections.pop("__desc__", "").strip()
        for name, body in sections.items():
            key = _map.get(name, name.lower())
            lines = body.splitlines()
            i = 0
            while i < len(lines):
                line = lines[i].strip()
                if not line:
                    i += 1
                    continue
                m = re.match(r"^(\w+)\s*:\s*(.*)", line)
                if m:
                    typ = m.group(2).strip()
                    desc_l: list[str] = []
                    i += 1
                    while i < len(lines) and lines[i].startswith("    "):
                        desc_l.append(lines[i].strip())
                        i += 1
                    tags.setdefault(key, []).append(
                        {
                            "type": typ,
                            "name": m.group(1),
                            "description": " ".join(desc_l),
                            "optional": "optional" in typ.lower(),
                            "default": None,
                        }
                    )
                else:
                    if key in ("example", "remarks", "see"):
                        tags.setdefault(key, []).append(
                            {
                                "type": "",
                                "name": "",
                                "description": body.strip(),
                                "optional": False,
                                "default": None,
                            }
                        )
                        break
                    i += 1
        return {"description": desc, "tags": tags}

    @classmethod
    def _split_numpy_sections(cls, raw: str) -> dict[str, str]:
        """Executa 'split numpy sections' e retorna o resultado.

        Args:
            raw: Parâmetro da operação.
        """
        result: dict[str, str] = {"__desc__": ""}
        current, buf = "__desc__", []
        lines = raw.splitlines()
        i = 0
        while i < len(lines):
            if i + 1 < len(lines) and re.match(r"^[-=]+\s*$", lines[i + 1]):
                result[current] = "\n".join(buf).strip()
                current, buf = lines[i].strip(), []
                i += 2
                continue
            buf.append(lines[i])
            i += 1
        result[current] = "\n".join(buf).strip()
        return result

    @classmethod
    def _parse_sphinx(cls, raw: str) -> dict:
        """Analisa Sphinx/reST docstring."""
        tags: dict[str, list[dict]] = {}
        fd = re.search(r"\n\s*:", raw)
        desc = raw[: fd.start()].strip() if fd else raw

        types: dict[str, str] = {}
        for m in cls._SPHINX_TYPE_RE.finditer(raw):
            types[m.group(1)] = m.group(2).strip()

        for m in cls._SPHINX_PARAM_RE.finditer(raw):
            typ = (m.group(1) or types.get(m.group(2), "")).strip()
            tags.setdefault("param", []).append(
                {
                    "type": typ,
                    "name": m.group(2),
                    "description": m.group(3).strip(),
                    "optional": False,
                    "default": None,
                }
            )

        for m in cls._SPHINX_RET_RE.finditer(raw):
            rt = ""
            rtm = cls._SPHINX_RTYPE_RE.search(raw)
            if rtm:
                rt = rtm.group(1).strip()
            tags.setdefault("returns", []).append(
                {
                    "type": rt,
                    "name": "",
                    "description": m.group(1).strip(),
                    "optional": False,
                    "default": None,
                }
            )

        for m in cls._SPHINX_RAISE_RE.finditer(raw):
            tags.setdefault("throws", []).append(
                {
                    "type": m.group(1),
                    "name": "",
                    "description": m.group(2).strip(),
                    "optional": False,
                    "default": None,
                }
            )

        return {"description": desc, "tags": tags}

    @classmethod
    def _parse_rust_file(cls, _: str, lines: list[str]) -> dict[str, dict]:
        """Executa 'parse rust file' e retorna o resultado.

        Args:
            _: Parâmetro da operação.
            lines: Parâmetro da operação.
        """
        docs: dict[str, dict] = {}
        i = 0
        while i < len(lines):
            s = lines[i].strip()
            if not (s.startswith("///") or s.startswith("//!")):
                i += 1
                continue
            doc_lines: list[str] = []
            j = i
            while j < len(lines):
                sl = lines[j].strip()
                if sl.startswith("///") or sl.startswith("//!"):
                    doc_lines.append(re.sub(r"^///!?|^//!", "", sl).strip())
                    j += 1
                else:
                    break
            raw = "\n".join(doc_lines)
            parsed = cls._parse_rust_doc(raw)
            symbol, sym_idx = cls._next_symbol(lines, j)
            be = cls._body_end(lines, sym_idx)
            parsed["_symbol_line"] = sym_idx + 1
            parsed["_body_start"] = sym_idx + 1
            parsed["_body_end"] = be + 1
            if symbol:
                key = f"{symbol}__line_{sym_idx + 1}" if symbol in docs else symbol
                docs[key] = parsed
            else:
                docs[f"__line_{i + 1}"] = parsed
            i = j
        return docs

    @classmethod
    def _parse_rust_doc(cls, raw: str) -> dict:
        """Analisa markdown em doc comments Rust."""
        sections = cls._split_md_sections(raw)
        desc = sections.pop("__desc__", "").strip()
        tags: dict[str, list[dict]] = {}
        _map = {
            "Arguments": "param",
            "Parameters": "param",
            "Returns": "returns",
            "Return Value": "returns",
            "Errors": "throws",
            "Panics": "throws",
            "Examples": "example",
            "Example": "example",
            "Safety": "remarks",
            "Notes": "remarks",
            "Note": "remarks",
        }
        for name, body in sections.items():
            key = _map.get(name, name.lower())
            items = cls._parse_bullets(body)
            if items:
                tags.setdefault(key, []).extend(items)
            else:
                tags.setdefault(key, []).append(
                    {
                        "type": "",
                        "name": "",
                        "description": body.strip(),
                        "optional": False,
                        "default": None,
                    }
                )
        return {"description": desc, "tags": tags}

    @classmethod
    def _parse_go_file(cls, _: str, lines: list[str]) -> dict[str, dict]:
        """Executa 'parse go file' e retorna o resultado.

        Args:
            _: Parâmetro da operação.
            lines: Parâmetro da operação.
        """
        docs: dict[str, dict] = {}
        i = 0
        while i < len(lines):
            if not lines[i].strip().startswith("//"):
                i += 1
                continue
            dl: list[str] = []
            j = i
            while j < len(lines):
                s = lines[j].strip()
                if s.startswith("//") and not s.startswith("//go:"):
                    dl.append(re.sub(r"^//\s?", "", s))
                    j += 1
                elif not s:
                    j += 1
                    break
                break

            k = j
            while k < len(lines) and not lines[k].strip():
                k += 1
            symbol, sym_idx = cls._next_symbol(lines, k)
            if symbol:
                raw = "\n".join(dl)
                parsed = cls._parse_go_doc(raw)
                be = cls._body_end(lines, sym_idx)
                parsed["_symbol_line"] = sym_idx + 1
                parsed["_body_start"] = sym_idx + 1
                parsed["_body_end"] = be + 1
                key = f"{symbol}__line_{sym_idx + 1}" if symbol in docs else symbol
                docs[key] = parsed
            i = j
        return docs

    @classmethod
    def _parse_go_doc(cls, raw: str) -> dict:
        """Analisa godoc (texto puro + Deprecated:)."""
        tags: dict[str, list[dict]] = {}
        if re.search(r"^Deprecated:", raw, re.MULTILINE):
            tags["deprecated"] = [
                {
                    "type": "",
                    "name": "",
                    "description": "",
                    "optional": False,
                    "default": None,
                }
            ]
        return {"description": raw.strip(), "tags": tags}

    @classmethod
    def _parse_doxygen_file(cls, _: str, lines: list[str]) -> dict[str, dict]:
        """Executa 'parse doxygen file' e retorna o resultado.

        Args:
            _: Parâmetro da operação.
            lines: Parâmetro da operação.
        """
        docs: dict[str, dict] = {}
        i = 0
        while i < len(lines):
            s = lines[i].strip()
            if not (s.startswith("/**") or s.startswith("///")):
                i += 1
                continue
            if s.startswith("///"):
                dl: list[str] = []
                j = i
                while j < len(lines) and lines[j].strip().startswith("///"):
                    dl.append(re.sub(r"^///\s?", "", lines[j].strip()))
                    j += 1
                parsed = cls._parse_xml_doc("\n".join(dl))
            else:
                bl: list[str] = []
                while i < len(lines):
                    bl.append(lines[i])
                    if "*/" in lines[i]:
                        break
                    i += 1
                j = i + 1
                parsed = cls._parse_doxygen_block("\n".join(bl))
            symbol, sym_idx = cls._next_symbol(lines, j)
            be = cls._body_end(lines, sym_idx)
            parsed["_symbol_line"] = sym_idx + 1
            parsed["_body_start"] = sym_idx + 1
            parsed["_body_end"] = be + 1
            if symbol:
                key = f"{symbol}__line_{sym_idx + 1}" if symbol in docs else symbol
                docs[key] = parsed
            else:
                docs[f"__line_{i + 1}"] = parsed
            i = j
        return docs

    @classmethod
    def _parse_doxygen_block(cls, block: str) -> dict:
        """Executa 'parse doxygen block' e retorna o resultado.

        Args:
            block: Parâmetro da operação.
        """
        lines = block.strip().splitlines()
        cleaned: list[str] = []
        for ln in lines:
            ln = re.sub(r"^\s*/\*\*?\s?", "", ln)
            ln = re.sub(r"\*/\s*$", "", ln)
            ln = re.sub(r"^\s*\*\s?", "", ln)
            cleaned.append(ln)
        raw = "\n".join(cleaned)

        _dox_map = {
            "brief": "summary",
            "details": "remarks",
            "param": "param",
            "tparam": "template",
            "returns": "returns",
            "return": "returns",
            "throws": "throws",
            "throw": "throws",
            "note": "remarks",
            "warning": "warn",
            "deprecated": "deprecated",
            "since": "since",
            "author": "author",
            "version": "version",
            "see": "see",
            "pre": "remarks",
            "post": "remarks",
            "invariant": "remarks",
            "todo": "todo",
            "bug": "fixme",
            "attention": "important",
            "code": "example",
            "endcode": "_skip",
        }
        tags: dict[str, list[dict]] = {}
        desc_parts: list[str] = []
        last = 0

        for m in cls._DOXYGEN_TAG_RE.finditer(raw):
            if m.start() > last:
                desc_parts.append(raw[last: m.start()].strip())
            tag = m.group(1).lower()
            body = (m.group(2) or "").strip()
            mapped = _dox_map.get(tag, tag)
            last = m.end()
            if mapped == "_skip":
                continue
            entry: dict[str, Any] = {
                "type": "",
                "name": "",
                "description": body,
                "optional": False,
                "default": None,
            }
            if mapped in ("param", "template"):
                parts = body.split(None, 1)
                if parts:
                    entry["name"] = parts[0]
                    entry["description"] = parts[1] if len(parts) > 1 else ""
            tags.setdefault(mapped, []).append(entry)

        desc = "\n".join(p for p in desc_parts if p).strip()
        return {"description": desc, "tags": tags}

    @classmethod
    def _parse_xml_doc(cls, raw: str) -> dict:
        """C# XML doc comments (/// <summary> etc.)."""
        tags: dict[str, list[dict]] = {}
        desc = ""
        for m in cls._XML_TAG_RE.finditer(raw):
            tag = m.group(1).lower()
            body = m.group(2).strip()
            if tag == "summary":
                desc = body
            elif tag == "remarks":
                tags.setdefault("remarks", []).append(
                    {
                        "type": "",
                        "name": "",
                        "description": body,
                        "optional": False,
                        "default": None,
                    }
                )
            elif tag == "param":
                nm = re.search(r'name="([^"]+)"', m.group(0))
                tags.setdefault("param", []).append(
                    {
                        "type": "",
                        "name": nm.group(1) if nm else "",
                        "description": body,
                        "optional": False,
                        "default": None,
                    }
                )
            elif tag == "returns":
                tags.setdefault("returns", []).append(
                    {
                        "type": "",
                        "name": "",
                        "description": body,
                        "optional": False,
                        "default": None,
                    }
                )
            elif tag == "exception":
                cr = re.search(r'cref="([^"]+)"', m.group(0))
                tags.setdefault("throws", []).append(
                    {
                        "type": cr.group(1) if cr else "",
                        "name": "",
                        "description": body,
                        "optional": False,
                        "default": None,
                    }
                )
        return {"description": desc, "tags": tags}

    @classmethod
    def _parse_ruby_file(cls, _: str, lines: list[str]) -> dict[str, dict]:
        """Executa 'parse ruby file' e retorna o resultado.

        Args:
            _: Parâmetro da operação.
            lines: Parâmetro da operação.
        """
        docs: dict[str, dict] = {}
        i = 0
        while i < len(lines):
            if not lines[i].strip().startswith("#"):
                i += 1
                continue
            dl: list[str] = []
            j = i
            while j < len(lines) and lines[j].strip().startswith("#"):
                dl.append(re.sub(r"^##?\s?", "", lines[j].strip()))
                j += 1
            k = j
            while k < len(lines) and not lines[k].strip():
                k += 1
            symbol, sym_idx = cls._next_symbol(lines, k)
            if symbol:
                parsed = cls._parse_yard("\n".join(dl))
                be = cls._body_end(lines, sym_idx)
                parsed["_symbol_line"] = sym_idx + 1
                parsed["_body_start"] = sym_idx + 1
                parsed["_body_end"] = be + 1
                key = f"{symbol}__line_{sym_idx + 1}" if symbol in docs else symbol
                docs[key] = parsed
            i = j
        return docs

    @classmethod
    def _parse_yard(cls, raw: str) -> dict:
        """Analisa YARD (@param, @return, @raise)."""
        _map = {
            "param": "param",
            "return": "returns",
            "returns": "returns",
            "raise": "throws",
            "raises": "throws",
            "see": "see",
            "since": "since",
            "deprecated": "deprecated",
            "example": "example",
            "note": "remarks",
            "author": "author",
            "version": "version",
            "overload": "remarks",
        }
        tags: dict[str, list[dict]] = {}
        desc_lines: list[str] = []
        for line in raw.splitlines():
            m = re.match(r"@(\w+)\s*(.*)", line.strip())
            if m:
                tag = m.group(1).lower()
                rest = m.group(2).strip()
                entry: dict[str, Any] = {
                    "type": "",
                    "name": "",
                    "description": rest,
                    "optional": False,
                    "default": None,
                }
                tm = re.match(r"\[([^\]]+)\]\s*(\w+)?\s*(.*)", rest)
                if tm:
                    entry["type"] = tm.group(1)
                    entry["name"] = tm.group(2) or ""
                    entry["description"] = tm.group(3).strip()
                mapped = _map.get(tag, tag)
                tags.setdefault(mapped, []).append(entry)
            else:
                desc_lines.append(line)
        return {"description": "\n".join(desc_lines).strip(), "tags": tags}

    @classmethod
    def _parse_lua_file(cls, _: str, lines: list[str]) -> dict[str, dict]:
        """Executa 'parse lua file' e retorna o resultado.

        Args:
            _: Parâmetro da operação.
            lines: Parâmetro da operação.
        """
        docs: dict[str, dict] = {}
        i = 0
        while i < len(lines):
            if not lines[i].strip().startswith("---"):
                i += 1
                continue
            dl: list[str] = []
            j = i
            while j < len(lines):
                sl = lines[j].strip()
                if sl.startswith("---") or sl.startswith("-- ") or sl == "--":
                    dl.append(re.sub(r"^-{2,3}\s?", "", sl))
                    j += 1
                else:
                    break
            symbol, sym_idx = cls._next_symbol(lines, j)
            if symbol:
                parsed = cls._parse_ldoc("\n".join(dl))
                be = cls._body_end(lines, sym_idx)
                parsed["_symbol_line"] = sym_idx + 1
                parsed["_body_start"] = sym_idx + 1
                parsed["_body_end"] = be + 1
                key = f"{symbol}__line_{sym_idx + 1}" if symbol in docs else symbol
                docs[key] = parsed
            i = j
        return docs

    @classmethod
    def _parse_ldoc(cls, raw: str) -> dict:
        """Analisa LDoc (--- style, similar a JSDoc)."""
        _map = {
            "tparam": "param",
            "treturn": "returns",
            "return": "returns",
            "raise": "throws",
            "see": "see",
            "usage": "example",
        }
        tags: dict[str, list[dict]] = {}
        desc_lines: list[str] = []
        for line in raw.splitlines():
            m = re.match(r"@(\w+)\s*(.*)", line.strip())
            if m:
                tag, rest = m.group(1).lower(), m.group(2).strip()
                mapped = _map.get(tag, tag)
                entry: dict[str, Any] = {
                    "type": "",
                    "name": "",
                    "description": rest,
                    "optional": False,
                    "default": None,
                }
                parts = rest.split(None, 2)
                if tag in ("tparam",) and len(parts) >= 2:
                    entry["type"] = parts[0]
                    entry["name"] = parts[1]
                    entry["description"] = parts[2] if len(parts) > 2 else ""
                tags.setdefault(mapped, []).append(entry)
            else:
                desc_lines.append(line)
        return {"description": "\n".join(desc_lines).strip(), "tags": tags}

    @classmethod
    def _split_md_sections(cls, raw: str) -> dict[str, str]:
        """Divide texto markdown por # headings."""
        result: dict[str, str] = {"__desc__": ""}
        current, buf = "__desc__", []
        for line in raw.splitlines():
            m = re.match(r"^#{1,3}\s+(.*)", line)
            if m:
                result[current] = "\n".join(buf).strip()
                current, buf = m.group(1).strip(), []
            else:
                buf.append(line)
        result[current] = "\n".join(buf).strip()
        return result

    @classmethod
    def _parse_bullets(cls, content: str) -> list[dict[str, Any]]:
        """Analisa lista de bullets como itens de doc."""
        entries = []
        for line in content.splitlines():
            m = re.match(r"^[-*]\s+`?(\w+)`?\s*[-:]\s*(.*)", line.strip())
            if m:
                entries.append(
                    {
                        "type": "",
                        "name": m.group(1),
                        "description": m.group(2).strip(),
                        "optional": False,
                        "default": None,
                    }
                )
        return entries

    @classmethod
    def _next_symbol(cls, lines: list[str], start: int) -> tuple[str | None, int]:
        """Encontra o próximo símbolo após start (pula decorators)."""
        limit = min(start + 15, len(lines))
        for j in range(start, limit):
            line = lines[j].strip()
            if not line or line.startswith(("//", "#", "*", "/*", "--")):
                continue
            for pat in cls._COMPILED_PATTERNS:
                m = pat.match(line)
                if m:
                    return m.group(1), j
            if line and not line.startswith(("@", '"', "'")):
                break
        return None, start

    @classmethod
    def _body_end(cls, lines: list[str], decl_idx: int) -> int:
        """Encontra o fim do corpo de uma declaração."""
        if decl_idx >= len(lines):
            return decl_idx + 1
        decl = lines[decl_idx]

        if decl.rstrip().endswith(":"):
            base = len(decl) - len(decl.lstrip())
            limit = min(decl_idx + 300, len(lines))
            for k in range(decl_idx + 1, limit):
                ln = lines[k]
                if not ln.strip():
                    continue
                if len(ln) - len(ln.lstrip()) <= base:
                    return k - 1
            return limit - 1

        if decl.strip().endswith(";"):
            return decl_idx

        depth = found = 0
        limit = min(decl_idx + 500, len(lines))
        for k in range(decl_idx, limit):
            for ch in lines[k]:
                if ch == "{":
                    depth += 1
                    found = 1
                elif ch == "}":
                    depth -= 1
                    if found and depth == 0:
                        return k
        return decl_idx + 1

    @classmethod
    def resolve_hover(
        cls, word: str, cursor_line: int, docs: dict[str, dict]
    ) -> tuple[str, dict, str] | None:
        """
        Resolve documentação para o hover.

        Returns:
            (nome, doc_dict, modo) onde modo é "full"|"param"|"return"
        """
        if word in docs:
            return (word, docs[word], "full")

        for sym, doc in docs.items():
            base = sym.split("__line_")[0]
            if base == word:
                sl = doc.get("_symbol_line", 0)
                if sl and abs(cursor_line - sl) <= 2:
                    return (word, doc, "full")

        for sym, doc in docs.items():
            if sym.startswith("__"):
                continue
            bs = doc.get("_body_start", 0)
            be = doc.get("_body_end", 0)
            if bs and be and bs < cursor_line <= be:
                res = cls._check_param_match(word, doc, sym)
                if res:
                    return res
                return (sym, doc, "full")

        for sym, doc in docs.items():
            if sym.startswith("__"):
                continue
            sl = doc.get("_symbol_line", 0)
            if sl and abs(cursor_line - sl) <= 2:
                return (sym, doc, "full")

        for key, doc in docs.items():
            if not key.startswith("__line_"):
                continue
            with contextlib.suppress(ValueError):
                dl = int(key.split("_")[-1])
                if abs(cursor_line - dl) <= 3:
                    return (word or key, doc, "full")

        return None

    @classmethod
    def _check_param_match(
        cls, word: str, doc: dict, func_name: str
    ) -> tuple[str, dict, str] | None:
        """Executa 'check param match' e retorna o resultado.

        Args:
            word: Parâmetro da operação.
            doc: Parâmetro da operação.
            func_name: Parâmetro da operação.
        """
        tags = doc.get("tags", {})
        for key in ("param", "arg", "argument"):
            for entry in tags.get(key, []):
                if entry.get("name") == word:
                    pd = {
                        "description": "",
                        "tags": {key: [entry]},
                        "_symbol_line": doc.get("_symbol_line", 0),
                        "_body_start": doc.get("_body_start", 0),
                        "_body_end": doc.get("_body_end", 0),
                        "_param_of": func_name,
                        "_lang": doc.get("_lang", ""),
                    }
                    return (word, pd, "param")
        return None

    @classmethod
    def format_sections(
        cls, symbol: str, doc: dict, source: str = "jsdoc"
    ) -> list[dict]:
        """
        Converte doc_dict em seções para o popup.
        """
        sections: list[dict] = []
        tags = doc.get("tags", {})
        lang = doc.get("_lang", "")

        badge_source = f"{lang}" if lang else source

        ret_type = cls._ret_type(tags)
        type_tag = (tags.get("type") or [{}])[0].get("type", "")
        tpl = [e.get("name", "")
               for e in tags.get("template", []) if e.get("name")]
        mods = [
            f
            for f in (
                "static",
                "async",
                "abstract",
                "readonly",
                "private",
                "protected",
                "public",
                "unsafe",
                "final",
                "override",
            )
            if tags.get(f)
        ]

        sections.append(
            {
                "kind": "title",
                "symbol": symbol,
                "ret_type": ret_type,
                "type_tag": type_tag,
                "modifiers": mods,
                "tpl_params": tpl,
                "source": badge_source,
            }
        )

        if tags.get("deprecated"):
            msg = (tags["deprecated"][0].get("description") or "").strip()
            sections.append(
                {
                    "kind": "deprecated",
                    "lines": [f"⚠  Depreciado{(': ' + msg) if msg else ''}"],
                }
            )
            sections.append({"kind": "sep"})

        for key in ("summary", "brief", "remarks"):
            if tags.get(key):
                text = (tags[key][0].get("description") or "").strip()
                if text:
                    sections.append({"kind": "remarks", "lines": [text]})
                    sections.append({"kind": "sep"})
                    break

        desc = (doc.get("description") or "").strip()
        for k in ("desc", "description"):
            if tags.get(k):
                extra = (tags[k][0].get("description") or "").strip()
                if extra:
                    desc = (desc + "\n\n" + extra).strip() if desc else extra
        if desc:
            sections.append({"kind": "desc", "lines": cls._wrap(desc)})
            sections.append({"kind": "sep"})

        if tags.get("template") or tags.get("tparam"):
            rows = []
            for e in tags.get("template") or tags.get("tparam") or []:
                rows.append(
                    {
                        "name": e.get("name") or e.get("description") or "",
                        "type": e.get("type", ""),
                        "desc": e.get("description", ""),
                        "optional": False,
                    }
                )
            if rows:
                sections.append({"kind": "template", "rows": rows})
                sections.append({"kind": "sep"})

        param_rows = []
        for key in ("param", "arg", "argument", "property", "prop"):
            for p in tags.get(key, []):
                param_rows.append(
                    {
                        "name": p.get("name", ""),
                        "type": p.get("type", ""),
                        "desc": (p.get("description") or "").strip(),
                        "optional": p.get("optional", False),
                        "default": p.get("default"),
                    }
                )
        if param_rows:
            sections.append({"kind": "params", "rows": param_rows})
            sections.append({"kind": "sep"})

        for key in ("returns", "return", "yields", "yield"):
            if tags.get(key):
                ret = tags[key][0]
                sections.append(
                    {
                        "kind": "returns",
                        "type": (ret.get("type") or "").strip(),
                        "desc": (ret.get("description") or "").strip(),
                        "yields": key in ("yields", "yield"),
                    }
                )
                sections.append({"kind": "sep"})
                break

        throw_rows = []
        for key in ("throws", "throw", "exception", "error", "raise", "raises"):
            for e in tags.get(key, []):
                throw_rows.append(
                    {
                        "type": (e.get("type") or "").strip(),
                        "desc": (e.get("description") or "").strip(),
                    }
                )
        if throw_rows:
            sections.append({"kind": "throws", "rows": throw_rows})
            sections.append({"kind": "sep"})

        ex_lines: list[str] = []
        for ex in tags.get("example", []) + tags.get("usage", []):
            raw = ex.get("description") or ex.get("type") or ""
            ex_lines.extend(raw.splitlines())
        if ex_lines:
            sections.append({"kind": "example", "lines": ex_lines[:30]})
            sections.append({"kind": "sep"})

        if tags.get("type"):
            ts = (tags["type"][0].get("type") or "").strip()
            if ts:
                sections.append({"kind": "type", "type": ts})
                sections.append({"kind": "sep"})

        meta: list[str] = []
        for tag_key, label in (
            ("since", "Desde"),
            ("version", "Versão"),
            ("author", "Autor"),
            ("license", "Licença"),
            ("todo", "TODO"),
            ("bug", "Bug"),
            ("fixme", "FIXME"),
        ):
            if tags.get(tag_key):
                v = (
                    tags[tag_key][0].get("description")
                    or tags[tag_key][0].get("name")
                    or ""
                ).strip()
                if v:
                    meta.append(f"{label}: {v}")
        for key in ("see", "link", "tutorial"):
            for item in tags.get(key, []):
                v = (item.get("description") or item.get("name") or "").strip()
                if v:
                    meta.append(f"Veja: {v}")
        for key in ("fires", "emits", "listens"):
            for item in tags.get(key, []):
                v = (item.get("description") or item.get("name") or "").strip()
                lbl = "Emite" if key in ("fires", "emits") else "Escuta"
                if v:
                    meta.append(f"{lbl}: {v}")
        for item in tags.get("requires", []):
            v = (item.get("description") or item.get("name") or "").strip()
            if v:
                meta.append(f"Requer: {v}")
        if meta:
            sections.append({"kind": "meta", "lines": meta})
            sections.append({"kind": "sep"})

        while sections and sections[-1].get("kind") == "sep":
            sections.pop()
        return sections

    @classmethod
    def format_sections_for_mode(cls, display: str, doc: dict, mode: str) -> list[dict]:
        """
        Constrói seções do popup de acordo com o modo de hover.
        """
        if mode == "full":
            return cls.format_sections(display, doc, source="jsdoc")
        if mode == "param":
            return cls._build_param_sections(display, doc)
        return cls.format_sections(display, doc, source="jsdoc")

    @classmethod
    def _build_param_sections(cls, display: str, doc: dict) -> list[dict]:
        """Constrói a seção 'param sections' do componente.

        Args:
            display: Parâmetro da operação.
            doc: Parâmetro da operação.
        """
        tags = doc.get("tags", {})
        entry = None
        parent = doc.get("_param_of", "")
        for key in ("param", "arg", "argument"):
            for e in tags.get(key, []):
                if e.get("name") == display:
                    entry = e
                    break
            if entry:
                break

        sections: list[dict] = []
        typ = (entry.get("type") or "") if entry else ""
        desc = (entry.get("description") or "") if entry else ""
        opt = entry.get("optional", False) if entry else False

        sections.append(
            {
                "kind": "title",
                "symbol": display + ("?" if opt else ""),
                "ret_type": "",
                "type_tag": typ,
                "modifiers": ["param"],
                "tpl_params": [],
                "source": doc.get("_lang", "jsdoc"),
            }
        )
        if parent:
            sections.append({"kind": "meta", "lines": [
                            f"parâmetro de  {parent}()"]})
        if desc:
            sections.append({"kind": "sep"})
            sections.append({"kind": "desc", "lines": cls._wrap(desc[:180])})
        return sections

    @classmethod
    def _ret_type(cls, tags: dict) -> str:
        """Executa 'ret type' e retorna o resultado.

        Args:
            tags: Parâmetro da operação.
        """
        for key in ("returns", "return", "yields", "yield"):
            if tags.get(key):
                return (tags[key][0].get("type") or "").strip()
        return ""

    @classmethod
    def _wrap(cls, text: str, width: int = 72) -> list[str]:
        """Envolve ou formata o conteúdo.

        Args:
            text: Dado textual a processar.
            width: Parâmetro da operação.
        """
        result: list[str] = []
        for para in text.split("\n\n"):
            lines = textwrap.wrap(para.replace("\n", " "), width) or [""]
            result.extend(lines)
            result.append("")
        while result and not result[-1]:
            result.pop()
        return result


class KDocsBalloon:
    """
    Popup flutuante rico para documentação (KDocs + JSDoc + LSP).

      - Badge de linguagem colorido (Python, Rust, Go, etc.)
      - Seção de prazo/ticket para anotações @kdocs: avançadas
      - Renderização de URL clicável
      - Anti-flicker: mesmo símbolo+linha não redesenha
      - Scroll automático se conteúdo ultrapassar 80% da tela
      - Auto-ocultar configurável (padrão 6 s)
    """

    def __init__(self, theme: Any) -> None:
        """Balloon unificado de documentação (LSP + KDocs + JSDoc + Peek)."""
        self._theme = theme
        self._win: tk.Toplevel | None = None
        self._anchor: tk.Widget | None = None
        self._job: str | None = None
        self._last_key: tuple = ()
        self._pinned: bool = False
        self._poll_job: str | None = None

    def show(
        self,
        anchor: tk.Widget,
        screen_x: int,
        screen_y: int,
        sections: list[dict],
    ) -> None:
        """Exibe o balloon unificado.

        Regras de vida útil
        -------------------
        - Nunca fecha automaticamente; fecha só quando o mouse sai da área
          (balloon + âncora) por mais de 400 ms.
        - Ctrl pressionado -> modo interativo: remove overrideredirect, adiciona
          barra de título nativa, permite resize, não fecha pelo mouse.
        - Balloon já visível com mesmo conteúdo -> ignora (anti-flicker).
        """
        new_key = (
            screen_x // 8,
            screen_y // 8,
            len(sections),
            sections[0].get("symbol", "") if sections else "",
        )
        if new_key == self._last_key and self.is_visible():
            return
        self._last_key = new_key

        try:
            ctrl_held = bool(anchor.tk.call("set", "_codis_ctrl_held") == "1")
        except Exception:
            ctrl_held = False

        self.hide()
        self._anchor = anchor
        self._pinned = ctrl_held

        colors = self._extract_theme_colors()

        if ctrl_held:
            win = self._create_pinned_window(anchor, screen_x, screen_y)
        else:
            win = self._create_hover_window(anchor)

        self._build_content(win, sections, colors, ctrl_held)
        self._win = win

        if ctrl_held:
            self._position_window(win, screen_x, screen_y)
        else:
            self._position_window(win, screen_x, screen_y)
            self._start_mouse_poll()

    @staticmethod
    def _create_hover_window(anchor: tk.Widget) -> tk.Toplevel:
        """Toplevel sem bordas para modo hover normal."""
        try:
            root = anchor.winfo_toplevel()
        except tk.TclError:
            root = anchor
        win = tk.Toplevel(root)
        win.wm_overrideredirect(True)
        win.wm_attributes("-topmost", True)
        win.geometry("+9999+9999")
        with contextlib.suppress(tk.TclError):
            win.wm_attributes("-alpha", 0.97)
        return win

    @staticmethod
    def _create_pinned_window(anchor: tk.Widget, sx: int, sy: int) -> tk.Toplevel:
        """Toplevel com decorações nativas para modo interativo (Ctrl-pin)."""
        try:
            root = anchor.winfo_toplevel()
        except tk.TclError:
            root = anchor
        win = tk.Toplevel(root)

        win.wm_overrideredirect(False)
        win.title("📚  Documentação")
        win.wm_attributes("-topmost", True)
        win.geometry(f"500x380+{sx}+{sy}")
        win.minsize(360, 220)
        win.resizable(True, True)
        with contextlib.suppress(tk.TclError):
            win.wm_attributes("-alpha", 0.98)
        return win

    def _build_content(
        self, win: tk.Toplevel, sections: list[dict], colors: dict, pinned: bool
    ) -> None:
        """Constrói header + área scrollável com todas as seções."""
        win.configure(bg=colors["bg"])

        header = tk.Frame(win, bg=colors["bg"])
        header.pack(fill="x")

        tk.Button(
            header,
            text="✕",
            bg=colors["bg"],
            fg=colors["fg_dim"],
            relief="flat",
            bd=0,
            cursor="hand2",
            font=("Segoe UI", 8),
            command=self.hide,
        ).pack(side="right", padx=(0, 4), pady=2)

        mode_icon = "📌 Interativo" if pinned else "🔗 Hover"
        tk.Label(
            header,
            text=mode_icon,
            bg=colors["bg"],
            fg=colors["fg_dim"],
            font=("Segoe UI", 7),
        ).pack(side="left", padx=4, pady=2)

        if not pinned:
            tk.Label(
                header,
                text=_t("kdocs.ctrl_hint"),
                bg=colors["bg"],
                fg=colors["dim"],
                font=("Segoe UI", 7),
            ).pack(side="left", padx=4, pady=2)

        tk.Frame(win, bg=colors["dim"], height=1).pack(fill="x")

        outer = tk.Frame(win, bg=colors["bg"])
        outer.pack(fill="both", expand=True)

        canvas = tk.Canvas(
            outer,
            bg=colors["bg"],
            highlightthickness=0,
            width=460,
            height=min(420, 36 * len(sections) + 50),
        )
        vsb = tk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        cf = tk.Frame(canvas, bg=colors["bg"], padx=12, pady=8)

        cf_id = canvas.create_window((0, 0), window=cf, anchor="nw")

        def _on_cf(_: tk.Event) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))
            rh = cf.winfo_reqheight()
            canvas.configure(height=min(420, max(60, rh)))
            canvas.itemconfig(cf_id, width=canvas.winfo_width())

        def _on_cv(e: tk.Event) -> None:
            canvas.itemconfig(cf_id, width=e.width)

        cf.bind("<Configure>", _on_cf)
        canvas.bind("<Configure>", _on_cv)

        def _wheel(e: tk.Event) -> None:
            d = -1 * (e.delta // 120) if e.delta else (1 if e.num == 5 else -1)
            canvas.yview_scroll(d, "units")

        for w in (canvas, cf, outer, win):
            with contextlib.suppress(tk.TclError):
                w.bind("<MouseWheel>", _wheel, add=True)
                w.bind("<Button-4>", _wheel, add=True)
                w.bind("<Button-5>", _wheel, add=True)

        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.configure(yscrollcommand=vsb.set)

        self._render_all_sections(cf, sections, colors)

        win.bind("<Escape>", lambda _: self.hide())

    @staticmethod
    def _position_window(win: tk.Toplevel, screen_x: int, screen_y: int) -> None:
        """Posiciona o balloon próximo ao cursor, evitando sair da tela."""
        win.update_idletasks()
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        rw = win.winfo_reqwidth()
        rh = win.winfo_reqheight()
        x = screen_x + 12
        y = screen_y + 4
        if x + rw > sw - 10:
            x = max(4, screen_x - rw - 12)
        if y + rh > sh - 40:
            y = max(4, screen_y - rh - 4)
        win.geometry(f"+{x}+{y}")

    def _start_mouse_poll(self) -> None:
        """Inicia polling periódico para detectar quando o mouse saiu da área."""
        self._stop_mouse_poll()
        self._poll_mouse()

    def _stop_mouse_poll(self) -> None:
        if self._poll_job and self._anchor:
            with contextlib.suppress(tk.TclError):
                self._anchor.after_cancel(self._poll_job)
            self._poll_job = None

    def _poll_mouse(self) -> None:
        """Verifica a cada 120 ms se o mouse ainda está sobre o balloon ou âncora."""
        if self._pinned or not self._win or not self._anchor:
            return
        try:
            if not self._win.winfo_exists():
                return
            mx = self._win.winfo_pointerx()
            my = self._win.winfo_pointery()
            over_balloon = self._pointer_inside(self._win, mx, my)
            over_anchor = self._pointer_inside(self._anchor, mx, my)
            if over_balloon or over_anchor:
                self._cancel_job()
                self._poll_job = self._anchor.after(120, self._poll_mouse)
            else:
                self._stop_mouse_poll()
                self._job = self._anchor.after(350, self.hide)
        except (tk.TclError, AttributeError):
            pass

    @staticmethod
    def _pointer_inside(widget: tk.Widget, mx: int, my: int) -> bool:
        """True se o ponto (mx, my) está dentro do bounding box do widget."""
        try:
            wx = widget.winfo_rootx()
            wy = widget.winfo_rooty()
            ww = widget.winfo_width()
            wh = widget.winfo_height()
            return wx <= mx <= wx + ww and wy <= my <= wy + wh
        except tk.TclError:
            return False

    def _cancel_job(self) -> None:
        """Cancela o after-job pendente de fechamento automático do balloon."""
        if self._job and self._anchor:
            with contextlib.suppress(tk.TclError):
                self._anchor.after_cancel(self._job)
            self._job = None

    def _schedule_hide(self, delay: int = 500) -> None:
        """Agenda fechamento após delay ms."""
        self._cancel_job()
        if self._anchor:
            self._job = self._anchor.after(delay, self.hide)

    def _extract_theme_colors(self) -> dict[str, Any]:
        """Extrai e retorna as cores do tema ativo para uso no balloon de documentação."""
        t = self._theme

        def _g(names, default):
            for n in [names] if isinstance(names, str) else names:
                v = getattr(t, n, None)
                if isinstance(v, str):
                    return v
            return default

        colors = {
            "bg": _g(["floating_bg", "editor_bg"], "#1e1e2e"),
            "fg": _g("fg", "#cdd6f4"),
            "muted": _g("fg_muted", "#a6adc8"),
            "dim": _g("fg_dim", "#6c7086"),
            "sel": _g("sel_bg", "#313244"),
            "acc": _g("t_func", "#89b4fa"),
            "kw": _g("t_keyword", "#cba6f7"),
            "typ": _g("t_type", "#89dceb"),
            "str_": _g("t_string", "#a6e3a1"),
            "mono": _g("font_mono", "Consolas"),
            "ui": _g("font_ui", "Segoe UI"),
        }

        colors["fg_dim"] = colors["dim"]
        colors["vp_borda"] = colors["sel"]
        return colors

    def _render_all_sections(
        self, parent: tk.Widget, sections: list[dict], colors: dict
    ) -> None:
        """Executa a operação 'render all sections'.

        Args:
            parent: Widget pai Tkinter.
            sections: Parâmetro da operação.
            colors: Dicionário de cores do tema.
        """
        renderers = {
            "title": self._render_title,
            "type_definition": self._render_type_definition,
            "deprecated": self._render_badge,
            "desc": self._render_desc,
            "remarks": self._render_remarks,
            "params": self._render_params,
            "template": self._render_template,
            "returns": self._render_returns,
            "throws": self._render_throws,
            "example": self._render_example,
            "type": self._render_type,
            "meta": self._render_meta,
            "kdocs_title": self._render_kdocs_title,
            "sep": self._render_separator,
            "source_label": self._render_source_label,
        }
        for section in sections:
            fn = renderers.get(section.get("kind", ""))
            if fn:
                fn(parent, section, colors)

    @staticmethod
    def _render_title(parent: tk.Widget, section: dict, colors: dict) -> None:
        """Executa a operação 'render title'.

        Args:
            parent: Widget pai Tkinter.
            section: Parâmetro da operação.
            colors: Dicionário de cores do tema.
        """
        row = tk.Frame(parent, bg=colors["bg"])
        row.pack(fill="x", pady=(0, 4))

        src = section.get("source", "jsdoc")

        _lang_colors: dict[str, str] = {
            "python": "#3572a5",
            "rust": "#dea584",
            "go": "#00add8",
            "javascript": "#f7df1e",
            "typescript": "#3178c6",
            "ruby": "#cc342d",
            "kotlin": "#a97bff",
            "swift": "#f05138",
            "java": "#b07219",
            "lua": "#000080",
            "php": "#4f5d95",
            "dart": "#00b4ab",
            "elixir": "#6e4a7e",
            "csharp": "#178600",
            "cpp": "#f34b7d",
            "c": "#555555",
            "r": "#198ce7",
            "haskell": "#5e5086",
            "julia": "#a270ba",
            "lsp": "#89b4fa",
            "jsdoc": "#cba6f7",
        }
        src_lower = src.lower()
        src_color = _lang_colors.get(src_lower, "#cba6f7")
        label = (
            src.upper()[:6]
            if src not in ("lsp", "jsdoc")
            else ("LSP" if src == "lsp" else "Doc")
        )
        tk.Label(
            row,
            text=label,
            bg=src_color,
            fg="#1e1e2e",
            font=(colors["ui"], 7, "bold"),
            padx=4,
            pady=1,
        ).pack(side="left", padx=(0, 6))

        for mod in section.get("modifiers", []):
            tk.Label(
                row,
                text=mod,
                bg=colors["sel"],
                fg=colors["muted"],
                font=(colors["ui"], 8, "italic"),
                padx=3,
            ).pack(side="left", padx=2)

        tk.Label(
            row,
            text=section.get("symbol", ""),
            bg=colors["bg"],
            fg=colors["acc"],
            font=(colors["mono"], 10, "bold"),
        ).pack(side="left")

        tpl = section.get("tpl_params", [])
        if tpl:
            tk.Label(
                row,
                text=f"<{', '.join(tpl)}>",
                bg=colors["bg"],
                fg=colors["typ"],
                font=(colors["mono"], 9),
            ).pack(side="left")

        ret_type = section.get("ret_type", "")
        type_tag = section.get("type_tag", "")
        if ret_type:
            tk.Label(
                row,
                text=_t("hc.kdocs.arrow_sep"),
                bg=colors["bg"],
                fg=colors["dim"],
                font=(colors["mono"], 9),
            ).pack(side="left")
            tk.Label(
                row,
                text=ret_type,
                bg=colors["bg"],
                fg=colors["typ"],
                font=(colors["mono"], 9),
            ).pack(side="left")
        elif type_tag:
            tk.Label(
                row,
                text=f": {type_tag}",
                bg=colors["bg"],
                fg=colors["typ"],
                font=(colors["mono"], 9),
            ).pack(side="left")

    @staticmethod
    def _render_type_definition(parent: tk.Widget, section: dict, colors: dict) -> None:
        """Executa a operação 'render type definition'.

        Args:
            parent: Widget pai Tkinter.
            section: Parâmetro da operação.
            colors: Dicionário de cores do tema.
        """
        frame = tk.Frame(parent, bg=colors["bg"])
        frame.pack(fill="x", pady=(0, 4))
        badge = tk.Frame(frame, bg=colors["typ"], padx=4, pady=1)
        badge.pack(side="left", padx=(0, 6))
        tk.Label(
            badge,
            text=_t("hc.kdocs.tipo"),
            bg=colors["typ"],
            fg=colors["bg"],
            font=(colors["ui"], 7, "bold"),
        ).pack()
        tk.Label(
            frame,
            text=section.get("type_name", ""),
            bg=colors["bg"],
            fg=colors["acc"],
            font=(colors["mono"], 9, "bold"),
        ).pack(side="left", padx=(0, 6))
        desc = section.get("description", "")
        if desc:
            tk.Label(
                frame,
                text=desc,
                bg=colors["bg"],
                fg=colors["fg"],
                font=(colors["ui"], 9),
                anchor="w",
                wraplength=350,
            ).pack(side="left")

    def _render_kdocs_title(
        self, parent: tk.Widget, section: dict, colors: dict
    ) -> None:
        """Executa a operação 'render kdocs title'.

        Args:
            parent: Widget pai Tkinter.
            section: Parâmetro da operação.
            colors: Dicionário de cores do tema.
        """
        row = tk.Frame(parent, bg=colors["bg"])
        row.pack(fill="x", pady=(0, 6))

        badge_color = section.get("color", "#89b4fa")
        badge = tk.Frame(row, bg=badge_color, padx=6, pady=2)
        badge.pack(side="left", padx=(0, 8))
        lbl_text = (
            f"{section.get('icon', '📌')}  @kdocs:{section.get('kind_name', 'note')}"
        )
        tk.Label(
            badge,
            text=lbl_text,
            bg=badge_color,
            fg="#1e1e2e",
            font=(colors["mono"], 9, "bold"),
        ).pack()

        priority = section.get("priority", 0)
        if priority >= 3:
            self._priority_badge(row, "CRÍTICA", "#f38ba8", colors)
        elif priority == 2:
            self._priority_badge(row, "ALTA", "#fab387", colors)
        elif priority == 1:
            self._priority_badge(row, "NORMAL", "#f9e2af", colors)

        assignee = section.get("assignee", "")
        if assignee:
            tk.Label(
                row,
                text=f"@{assignee}",
                bg=colors["bg"],
                fg=colors["acc"],
                font=(colors["ui"], 8),
            ).pack(side="left", padx=(0, 4))

        for tag in section.get("tags", [])[:4]:
            tb = tk.Frame(row, bg=colors["sel"], padx=3)
            tb.pack(side="left", padx=2)
            tk.Label(
                tb,
                text=f"#{tag}",
                bg=colors["sel"],
                fg=colors["muted"],
                font=(colors["ui"], 7),
            ).pack()

        due = section.get("due_date", "")
        if due:
            tk.Label(
                row,
                text=f"📅 {due}",
                bg=colors["bg"],
                fg=colors["dim"],
                font=(colors["ui"], 7),
            ).pack(side="left", padx=(6, 0))
        ticket = section.get("ticket", "")
        if ticket:
            tk.Label(
                row,
                text=f"🎫 {ticket}",
                bg=colors["bg"],
                fg=colors["dim"],
                font=(colors["ui"], 7),
            ).pack(side="left", padx=(4, 0))

    @staticmethod
    def _priority_badge(parent: tk.Frame, text: str, color: str, colors: dict) -> None:
        """Executa a operação 'priority badge'.

        Args:
            parent: Widget pai Tkinter.
            text: Dado textual a processar.
            color: Parâmetro da operação.
            colors: Dicionário de cores do tema.
        """
        pb = tk.Frame(parent, bg=color, padx=4, pady=1)
        pb.pack(side="left", padx=(0, 4))
        tk.Label(
            pb, text=text, bg=color, fg="#1e1e2e", font=(colors["ui"], 7, "bold")
        ).pack()

    @staticmethod
    def _render_badge(parent: tk.Widget, section: dict, colors: dict) -> None:
        """Executa a operação 'render badge'.

        Args:
            parent: Widget pai Tkinter.
            section: Parâmetro da operação.
            colors: Dicionário de cores do tema.
        """
        for line in section.get("lines", []):
            f = tk.Frame(parent, bg="#3d2228", pady=2, padx=4)
            f.pack(fill="x", pady=2)
            tk.Label(
                f,
                text=line,
                bg="#3d2228",
                fg="#f38ba8",
                font=(colors["ui"], 9, "bold"),
                anchor="w",
            ).pack(anchor="w")

    @staticmethod
    def _render_desc(parent: tk.Widget, section: dict, colors: dict) -> None:
        """Executa a operação 'render desc'.

        Args:
            parent: Widget pai Tkinter.
            section: Parâmetro da operação.
            colors: Dicionário de cores do tema.
        """
        text = "\n".join(section.get("lines", []))
        tk.Label(
            parent,
            text=text,
            bg=colors["bg"],
            fg=colors["fg"],
            font=(colors["ui"], 9),
            justify="left",
            anchor="w",
            wraplength=440,
        ).pack(anchor="w", pady=(0, 2))

    @staticmethod
    def _render_remarks(parent: tk.Widget, section: dict, colors: dict) -> None:
        """Executa a operação 'render remarks'.

        Args:
            parent: Widget pai Tkinter.
            section: Parâmetro da operação.
            colors: Dicionário de cores do tema.
        """
        text = "\n".join(section.get("lines", []))
        tk.Label(
            parent,
            text=text,
            bg=colors["sel"],
            fg=colors["muted"],
            font=(colors["ui"], 9, "italic"),
            justify="left",
            anchor="w",
            wraplength=440,
            padx=6,
            pady=3,
        ).pack(fill="x", pady=(0, 2))

    @staticmethod
    def _render_params(parent: tk.Widget, section: dict, colors: dict) -> None:
        """Executa a operação 'render params'.

        Args:
            parent: Widget pai Tkinter.
            section: Parâmetro da operação.
            colors: Dicionário de cores do tema.
        """
        hdr = tk.Frame(parent, bg=colors["bg"])
        hdr.pack(fill="x", pady=(0, 2))
        tk.Label(
            hdr,
            text=_t("hc.kdocs.par_metros"),
            bg=colors["bg"],
            fg=colors["muted"],
            font=(colors["ui"], 8, "bold"),
            anchor="w",
        ).pack(anchor="w")

        for row in section.get("rows", []):
            r = tk.Frame(parent, bg=colors["bg"])
            r.pack(fill="x", padx=4, pady=1)

            name = row.get("name", "")
            if row.get("optional"):
                name += "?"
            tk.Label(
                r,
                text=name,
                bg=colors["bg"],
                fg=colors["acc"],
                font=(colors["mono"], 9),
                width=14,
                anchor="w",
            ).pack(side="left")

            typ = row.get("type", "")
            if typ:
                pill = tk.Frame(r, bg=colors["sel"], padx=4)
                pill.pack(side="left", padx=(0, 6))
                tk.Label(
                    pill,
                    text=typ[:32],
                    bg=colors["sel"],
                    fg=colors["typ"],
                    font=(colors["mono"], 8),
                ).pack()

            desc = row.get("desc", "")
            if row.get("default") is not None:
                desc += f" (padrão: {row['default']})"
            tk.Label(
                r,
                text=desc,
                bg=colors["bg"],
                fg=colors["fg"],
                font=(colors["ui"], 9),
                anchor="w",
                wraplength=270,
                justify="left",
            ).pack(side="left", fill="x")

    @staticmethod
    def _render_template(parent: tk.Widget, section: dict, colors: dict) -> None:
        """Executa a operação 'render template'.

        Args:
            parent: Widget pai Tkinter.
            section: Parâmetro da operação.
            colors: Dicionário de cores do tema.
        """
        hdr = tk.Frame(parent, bg=colors["bg"])
        hdr.pack(fill="x", pady=(0, 2))
        tk.Label(
            hdr,
            text=_t("hc.kdocs.par_metros_de_tipo"),
            bg=colors["bg"],
            fg=colors["muted"],
            font=(colors["ui"], 8, "bold"),
            anchor="w",
        ).pack(anchor="w")
        for row in section.get("rows", []):
            r = tk.Frame(parent, bg=colors["bg"])
            r.pack(fill="x", padx=4, pady=1)
            tk.Label(
                r,
                text=row.get("name", ""),
                bg=colors["bg"],
                fg=colors["kw"],
                font=(colors["mono"], 9),
                width=14,
                anchor="w",
            ).pack(side="left")
            tk.Label(
                r,
                text=row.get("desc", ""),
                bg=colors["bg"],
                fg=colors["fg"],
                font=(colors["ui"], 9),
                anchor="w",
            ).pack(side="left")

    @staticmethod
    def _render_returns(parent: tk.Widget, section: dict, colors: dict) -> None:
        """Executa a operação 'render returns'.

        Args:
            parent: Widget pai Tkinter.
            section: Parâmetro da operação.
            colors: Dicionário de cores do tema.
        """
        label = _t("hc.kdocs.produz") if section.get("yields") else "Retorna"
        typ = section.get("type", "")
        desc = section.get("desc", "")
        r = tk.Frame(parent, bg=colors["bg"])
        r.pack(fill="x", pady=(0, 2))
        tk.Label(
            r,
            text=label,
            bg=colors["bg"],
            fg=colors["muted"],
            font=(colors["ui"], 8, "bold"),
            width=10,
            anchor="w",
        ).pack(side="left")
        if typ:
            pill = tk.Frame(r, bg=colors["sel"], padx=4)
            pill.pack(side="left", padx=(0, 6))
            tk.Label(
                pill,
                text=typ[:40],
                bg=colors["sel"],
                fg=colors["typ"],
                font=(colors["mono"], 8),
            ).pack()
        if desc:
            tk.Label(
                r,
                text=desc,
                bg=colors["bg"],
                fg=colors["fg"],
                font=(colors["ui"], 9),
                anchor="w",
                wraplength=290,
                justify="left",
            ).pack(side="left")

    @staticmethod
    def _render_throws(parent: tk.Widget, section: dict, colors: dict) -> None:
        """Executa a operação 'render throws'.

        Args:
            parent: Widget pai Tkinter.
            section: Parâmetro da operação.
            colors: Dicionário de cores do tema.
        """
        hdr = tk.Frame(parent, bg=colors["bg"])
        hdr.pack(fill="x", pady=(0, 2))
        tk.Label(
            hdr,
            text=_t("hc.kdocs.lan_amentos"),
            bg=colors["bg"],
            fg="#f38ba8",
            font=(colors["ui"], 8, "bold"),
            anchor="w",
        ).pack(anchor="w")
        for row in section.get("rows", []):
            r = tk.Frame(parent, bg=colors["bg"])
            r.pack(fill="x", padx=4, pady=1)
            typ = row.get("type", "")
            if typ:
                pill = tk.Frame(r, bg="#3d2228", padx=4)
                pill.pack(side="left", padx=(0, 6))
                tk.Label(
                    pill,
                    text=typ[:30],
                    bg="#3d2228",
                    fg="#f38ba8",
                    font=(colors["mono"], 8),
                ).pack()
            tk.Label(
                r,
                text=row.get("desc", ""),
                bg=colors["bg"],
                fg=colors["fg"],
                font=(colors["ui"], 9),
                anchor="w",
                wraplength=310,
                justify="left",
            ).pack(side="left")

    def _render_example(self, parent: tk.Widget, section: dict, colors: dict) -> None:
        """Executa a operação 'render example'.

        Args:
            parent: Widget pai Tkinter.
            section: Parâmetro da operação.
            colors: Dicionário de cores do tema.
        """
        hdr = tk.Frame(parent, bg=colors["bg"])
        hdr.pack(fill="x", pady=(0, 2))
        tk.Label(
            hdr,
            text=_t("hc.kdocs.exemplo"),
            bg=colors["bg"],
            fg=colors["muted"],
            font=(colors["ui"], 8, "bold"),
            anchor="w",
        ).pack(anchor="w")
        code_bg = getattr(self._theme, "gutter_bg", "#181825")
        code = tk.Frame(parent, bg=code_bg, padx=8, pady=4)
        code.pack(fill="x", pady=2)
        text = "\n".join(section.get("lines", [])[:25])
        tk.Label(
            code,
            text=text,
            bg=code_bg,
            fg=colors["str_"],
            font=(colors["mono"], 8),
            justify="left",
            anchor="w",
        ).pack(anchor="w")

    @staticmethod
    def _render_type(parent: tk.Widget, section: dict, colors: dict) -> None:
        """Executa a operação 'render type'.

        Args:
            parent: Widget pai Tkinter.
            section: Parâmetro da operação.
            colors: Dicionário de cores do tema.
        """
        r = tk.Frame(parent, bg=colors["bg"])
        r.pack(fill="x", pady=(0, 2))
        tk.Label(
            r,
            text=_t("hc.kdocs.tipo_2"),
            bg=colors["bg"],
            fg=colors["muted"],
            font=(colors["ui"], 8, "bold"),
        ).pack(side="left")
        pill = tk.Frame(r, bg=colors["sel"], padx=4)
        pill.pack(side="left")
        tk.Label(
            pill,
            text=section.get("type", "")[:50],
            bg=colors["sel"],
            fg=colors["typ"],
            font=(colors["mono"], 9),
        ).pack()

    @staticmethod
    def _render_meta(parent: tk.Widget, section: dict, colors: dict) -> None:
        """Executa a operação 'render meta'.

        Args:
            parent: Widget pai Tkinter.
            section: Parâmetro da operação.
            colors: Dicionário de cores do tema.
        """
        for line in section.get("lines", []):
            tk.Label(
                parent,
                text=line,
                bg=colors["bg"],
                fg=colors["dim"],
                font=(colors["ui"], 8),
                anchor="w",
            ).pack(anchor="w")

    @staticmethod
    @staticmethod
    def _render_source_label(parent: tk.Widget, section: dict, colors: dict) -> None:
        """Renderiza um rótulo de separação de fonte (LSP / KDocs / JSDoc).

        Exibido como uma linha fina + badge colorido indicando a origem
        das seções que se seguem - permite identificar visualmente de onde
        vem cada bloco de documentação no balloon unificado.
        """
        label = section.get("label", "")
        if not label:
            return

        source_colors: dict[str, str] = {
            "LSP": "#7c4dff",
            "KDocs": "#ff6d00",
            "JSDoc": "#00bfa5",
        }
        badge_color = source_colors.get(label, colors["acc"])

        frame = tk.Frame(parent, bg=colors["bg"])
        frame.pack(fill="x", pady=(6, 2))

        tk.Frame(frame, bg=colors["dim"], height=1, width=12).pack(
            side="left", anchor="center", padx=(0, 4), pady=4
        )

        badge = tk.Label(
            frame,
            text=f" {label} ",
            bg=badge_color,
            fg="#ffffff",
            font=("Segoe UI", 7, "bold"),
            padx=3,
            pady=1,
        )
        badge.pack(side="left")

        tk.Frame(frame, bg=colors["dim"], height=1).pack(
            side="left", fill="x", expand=True, anchor="center", pady=4, padx=(4, 0)
        )

    @staticmethod
    def _render_separator(parent: tk.Widget, _: dict, colors: dict) -> None:
        """Executa a operação 'render separator'.

        Args:
            parent: Widget pai Tkinter.
            _: Parâmetro da operação.
            colors: Dicionário de cores do tema.
        """
        tk.Frame(parent, bg=colors["dim"], height=1).pack(fill="x", pady=4)

    def hide(self) -> None:
        """Oculta o balloon e cancela jobs/polls pendentes."""
        self._stop_mouse_poll()
        self._cancel_job()
        self._pinned = False
        if self._job and self._anchor:
            with contextlib.suppress(tk.TclError):
                self._anchor.after_cancel(self._job)
        self._job = None
        self._last_key = ()
        if self._win:
            with contextlib.suppress(tk.TclError):
                if self._win.winfo_exists():
                    self._win.destroy()
        self._win = None

    def is_visible(self) -> bool:
        """Retorna ``True`` se o popup estiver criado e visível na tela."""
        return bool(self._win) and self._win.winfo_exists()


def kdocs_annotation_to_sections(ann: KDocsNotes) -> list[dict]:
    """
    Converte KDocsNotes em seções para o popup.
    Inclui os campos KDocs (due_date, ticket, url, body_lines).
    """
    sections: list[dict] = []

    sections.append(
        {
            "kind": "kdocs_title",
            "icon": ann.icon,
            "kind_name": ann.kind,
            "color": ann.color,
            "priority": ann.priority,
            "assignee": ann.assignee,
            "tags": ann.tags,
            "due_date": ann.due_date,
            "ticket": ann.ticket,
        }
    )

    body = ann.full_text.strip()
    if body:
        wrapped = textwrap.wrap(body, 72) or [body]
        sections.append({"kind": "desc", "lines": wrapped})

    if ann.kind == "link" and ann.file_ref:
        ref = ann.file_ref + (f":{ann.line_ref}" if ann.line_ref else "")
        sections.append({"kind": "meta", "lines": [f"→ {ref}"]})

    elif ann.kind == "see" and body:
        sections.append({"kind": "meta", "lines": [f"Veja também: {body}"]})

    elif ann.kind == "type" and ann.type_name:
        sections.append({"kind": "type", "type": ann.type_name})

    elif ann.kind == "param":
        alt = _parse_kdocs_param(ann, body)
        if alt:
            return alt

    elif ann.kind in ("return", "returns"):
        alt = _parse_kdocs_return(ann, body)
        if alt:
            return alt

    if ann.url:
        sections.append({"kind": "meta", "lines": [f"🔗 {ann.url}"]})

    meta = _build_kdocs_meta(ann)
    if meta:
        if sections and sections[-1].get("kind") != "sep":
            sections.append({"kind": "sep"})
        sections.append({"kind": "meta", "lines": meta})

    return sections


def _parse_kdocs_param(ann: KDocsNotes, body: str) -> list[dict] | None:
    """Executa 'parse kdocs param' e retorna o resultado.

    Args:
        ann: Parâmetro da operação.
        body: Parâmetro da operação.
    """
    m = re.match(r"^\{([^}]+)\}\s*(\S+)?\s*[-–]?\s*(.*)", body)
    if m:
        return [
            {
                "kind": "kdocs_title",
                "icon": ann.icon,
                "kind_name": ann.kind,
                "color": ann.color,
                "priority": ann.priority,
                "assignee": ann.assignee,
                "tags": ann.tags,
                "due_date": ann.due_date,
                "ticket": ann.ticket,
            },
            {
                "kind": "params",
                "rows": [
                    {
                        "name": m.group(2) or "",
                        "type": m.group(1) or "",
                        "desc": (m.group(3) or "").strip(),
                        "optional": False,
                        "default": None,
                    }
                ],
            },
        ]
    return None


def _parse_kdocs_return(ann: KDocsNotes, body: str) -> list[dict] | None:
    """Executa 'parse kdocs return' e retorna o resultado.

    Args:
        ann: Parâmetro da operação.
        body: Parâmetro da operação.
    """
    m = re.match(r"^\{([^}]+)\}\s*(.*)", body)
    if m:
        return [
            {
                "kind": "kdocs_title",
                "icon": ann.icon,
                "kind_name": ann.kind,
                "color": ann.color,
                "priority": ann.priority,
                "assignee": ann.assignee,
                "tags": ann.tags,
                "due_date": ann.due_date,
                "ticket": ann.ticket,
            },
            {
                "kind": "returns",
                "type": m.group(1).strip(),
                "desc": m.group(2).strip(),
                "yields": False,
            },
        ]
    return None


def _build_kdocs_meta(ann: KDocsNotes) -> list[str]:
    """Constrói a seção 'kdocs meta' do componente.

    Args:
        ann: Parâmetro da operação.
    """
    meta: list[str] = []
    if ann.assignee:
        meta.append(f"Responsável: @{ann.assignee}")
    if ann.tags:
        meta.append(f"Tags: {', '.join(ann.tags)}")
    if ann.priority == 3:
        meta.append("Prioridade: ⚠ CRÍTICA")
    elif ann.priority == 2:
        meta.append("Prioridade: ALTA")
    elif ann.priority == 1:
        meta.append("Prioridade: NORMAL")
    if ann.due_date:
        meta.append(f"Prazo: {ann.due_date}")
    if ann.ticket:
        meta.append(f"Ticket: {ann.ticket}")
    return meta


def lsp_hover_to_sections(symbol: str, markdown: str) -> list[dict]:
    """
    Converte markdown do LSP em seções para o popup.
    """
    sections: list[dict] = [
        {
            "kind": "title",
            "symbol": symbol,
            "ret_type": "",
            "type_tag": "",
            "modifiers": [],
            "tpl_params": [],
            "source": "lsp",
        }
    ]

    if not markdown:
        return sections

    fence_re = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)
    last_end = 0

    for match in fence_re.finditer(markdown):
        text_before = markdown[last_end: match.start()].strip()
        if text_before:
            lines = textwrap.wrap(text_before.replace(
                "\n", " "), 72) or [text_before]
            sections.append({"kind": "desc", "lines": lines})
            sections.append({"kind": "sep"})
        sections.append(
            {"kind": "example", "lines": match.group(2).splitlines()})
        sections.append({"kind": "sep"})
        last_end = match.end()

    tail = markdown[last_end:].strip()
    if tail:
        lines = textwrap.wrap(tail.replace("\n", " "), 72) or [tail]
        sections.append({"kind": "desc", "lines": lines})

    while sections and sections[-1].get("kind") == "sep":
        sections.pop()

    return sections


def find_definition_line(word: str, content: str, current_line: int) -> int | None:
    """
    Encontra a linha de definição de um símbolo.
    """
    if not word or not content:
        return None
    lines = content.splitlines()
    candidates: list[int] = []
    for i, line in enumerate(lines):
        if i + 1 == current_line:
            continue
        for pattern in _DEF_PATTERNS:
            if m := pattern.match(line):
                for g in m.groups():
                    if g == word:
                        candidates.append(i + 1)
                        break
    if not candidates:
        return None
    before = [c for c in candidates if c < current_line]
    after = [c for c in candidates if c > current_line]
    if before:
        return max(before)
    return min(after) if after else None


def find_all_usages(word: str, content: str) -> list[int]:
    """
    Encontra todas as ocorrências de um símbolo.
    """
    if not word or not content:
        return []
    pattern = re.compile(r"(?<![a-zA-Z0-9_$])" +
                         re.escape(word) + r"(?![a-zA-Z0-9_$])")
    results: list[int] = []
    for i, line in enumerate(content.splitlines()):
        if pattern.search(line):
            results.append(i + 1)
            if len(results) >= _MAX_USAGES:
                break
    return results
