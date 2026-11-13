"""Motor de sugestões do editor (sem dependência de Tkinter).

Ranking de sugestões, em vez de um simples filtro por prefixo:

* **Contexto** - não sugere dentro de comentários, strings (exceto expressões de
  f-string / ``${}``) nem ao nomear algo novo (``def |``, ``class |``); após ``.``
  sugere *membros* (atributos já vistos naquele objeto, ``self.``/``this.`` da
  classe); em ``import`` sugere módulos; no começo da linha prioriza palavras-chave
  de instrução e snippets.
* **Casamento** - prefixo exato > iniciais de ``snake_case``/``camelCase``
  (``gfn`` → ``get_file_name``) > subsequência (``gtflnm``), com bônus de caixa.
* **Sinais** - fonte (LSP > símbolos definidos > snippets > palavras), frequência
  no arquivo, proximidade do cursor e aprendizado do que *você* costuma aceitar.
* **Determinismo** - mesma entrada, mesma ordem.
"""

from __future__ import annotations

import math
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

_IDENT = re.compile(r"[A-Za-z_$][\w$]*")
_WORD_SPLIT = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")
MAX_INDEX_CHARS = 600_000
WINDOW_LINES = 400

_HASH_COMMENT = frozenset({"python", "ruby", "shell", "bash", "sh", "yaml", "toml", "perl", "r", "makefile",
                           "dockerfile", "powershell", "elixir", "nim"})
_DASH_COMMENT = frozenset({"sql", "lua", "haskell"})
_SLASH_COMMENT = frozenset({"javascript", "typescript", "java", "c", "cpp", "csharp", "go", "rust", "php",
                            "kotlin", "swift", "scala", "dart", "jsx", "tsx", "vue", "svelte", "zig", "groovy"})
_STATEMENT_KW = frozenset({
    "def", "class", "if", "elif", "else", "for", "while", "try", "except", "finally", "with", "return", "import",
    "from", "async", "await", "yield", "raise", "assert", "lambda", "pass", "break", "continue", "match", "case",
    "function", "const", "let", "var", "switch", "do", "throw", "catch", "export", "interface", "type", "enum",
    "struct", "impl", "fn", "func", "package", "public", "private", "static", "void", "int", "using", "namespace",
})


@dataclass
class LineContext:
    """O que está acontecendo exatamente na posição do cursor."""

    state: str = "code"
    prefix: str = ""
    qualifier: str | None = None
    statement_start: bool = False
    after_def: bool = False
    in_import: bool = False
    import_from: str | None = None
    member: bool = False

    @property
    def suppress(self) -> bool:
        return self.state != "code" or self.after_def


def scan_state(text: str, lang: str) -> str:
    """Estado léxico no fim de *text*: ``code``, ``comment`` ou ``string``.

    Aproximação de um lexer: aspas simples/duplas/triplas, crases (JS), escapes,
    comentários de linha e expressões em f-string / ``${}`` (contam como código).
    """
    hash_c, dash_c, slash_c = lang in _HASH_COMMENT, lang in _DASH_COMMENT, lang in _SLASH_COMMENT
    i, n = 0, len(text)
    in_str: str | None = None
    fstr = False
    depth = 0
    triple = False
    while i < n:
        ch = text[i]
        if in_str is None:
            if ch == "\n":
                i += 1
                continue
            two = text[i:i + 2]
            if (hash_c and ch == "#") or (dash_c and two == "--") or (slash_c and two == "//"):
                j = text.find("\n", i)
                if j == -1:
                    return "comment"
                i = j
                continue
            if slash_c and two == "/*":
                j = text.find("*/", i + 2)
                if j == -1:
                    return "comment"
                i = j + 2
                continue
            if ch in "\"'`":
                trip = text[i:i + 3]
                is_tri = lang == "python" and trip in ('"""', "'''")
                prev = text[max(0, i - 2):i].lower()
                fstr = (lang == "python" and bool(re.search(r"(?:^|[^\w])(?:f|rf|fr)$", prev))) or ch == "`"
                in_str, triple, depth = (trip if is_tri else ch), is_tri, 0
                i += 3 if is_tri else 1
                continue
            i += 1
            continue
        if depth > 0:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            elif ch in "\"'" and ch != in_str[0]:
                j = text.find(ch, i + 1)
                if j == -1:
                    return "code"
                i = j
            i += 1
            continue
        if ch == "\\":
            i += 2
            continue
        if fstr and ch == "{" and text[i:i + 2] != "{{":
            depth = 1
            i += 1
            continue
        if fstr and text[i:i + 2] in ("{{", "}}"):
            i += 2
            continue
        if fstr and in_str == "`" and text[i:i + 2] == "${":
            depth = 1
            i += 2
            continue
        if in_str and text.startswith(in_str, i):
            i += len(in_str)
            in_str, fstr, triple = None, False, False
            continue
        if ch == "\n" and not triple and in_str != "`":
            in_str, fstr = None, False
        i += 1
    if in_str is not None:
        return "code" if depth > 0 else "string"
    return "code"


_AFTER_DEF = re.compile(r"\b(?:def|class|function|fn|func|struct|enum|trait|interface|namespace|package|"
                        r"const|let|var|type)\s+$")
_IMPORT_LINE = re.compile(r"^\s*(?:from\s+[\w.]+\s+import\s+[\w\s,()]*|import\s+[\w\s,.]*|"
                          r"(?:import|export)\s*\{[^}]*|using\s+[\w.]*)$")


def analyze(text_before_cursor: str, lang: str, *, scan_limit_lines: int = WINDOW_LINES) -> LineContext:
    """Analisa o texto do início do arquivo (ou janela) até o cursor."""
    lines = text_before_cursor.split("\n")
    if len(lines) > scan_limit_lines * 2 and lang != "python":
        text_for_scan = "\n".join(lines[-scan_limit_lines:])
    else:
        text_for_scan = text_before_cursor
    line = lines[-1]
    ctx = LineContext(state=scan_state(text_for_scan, lang))
    m = re.search(r"[\w$]*$", line)
    ctx.prefix = m.group() if m else ""
    head = line[: len(line) - len(ctx.prefix)]
    ctx.statement_start = not head.strip()
    ctx.after_def = bool(_AFTER_DEF.search(head)) and lang != "css"
    ctx.in_import = bool(_IMPORT_LINE.match(line)) or bool(re.match(r"^\s*from\s+[\w.]*$", line))
    fm = re.match(r"^\s*from\s+([\w.]+)\s+import\s", line)
    ctx.import_from = fm.group(1) if fm else None
    if head.endswith("."):
        qm = re.search(r"([\w$]+(?:\.[\w$]+)*)\.$", head)
        ctx.member = True
        ctx.qualifier = qm.group(1) if qm else ""
    return ctx


def split_words(word: str) -> list[str]:
    return _WORD_SPLIT.findall(word.replace("$", "_")) or [word]


def match_score(prefix: str, word: str) -> float | None:
    """Qualidade do casamento de *prefix* com *word* (``None`` = não casa).

    1.0+ prefixo · 0.8 iniciais · 0.45–0.7 subsequência.  Pequena penalidade por
    tamanho para preferir nomes mais curtos em empate.
    """
    if not prefix:
        return 0.5
    p, w = prefix.lower(), word.lower()
    if w == p:
        return None
    pen = 0.002 * min(len(word), 40)
    if w.startswith(p):
        return 1.0 + (0.1 if word.startswith(prefix) else 0.0) - pen
    if len(p) >= 2:
        segs = split_words(word)
        initials = "".join(s[0].lower() for s in segs)
        if initials.startswith(p):
            return 0.8 - pen
        j = 0
        for s in segs:
            sl = s.lower()
            k = 0
            while j < len(p) and k < len(sl) and p[j] == sl[k]:
                j += 1
                k += 1
            if j == len(p):
                return 0.7 - pen
            if k == 0:
                break
        if p[0] == w[0]:
            pos = -1
            span = 0
            for ch in p:
                nxt = w.find(ch, pos + 1)
                if nxt == -1:
                    return None
                span += nxt - pos
                pos = nxt
            compact = len(p) / max(span, 1)
            return 0.45 + 0.2 * compact - pen
    return None


_SYMBOL_PATTERNS: dict[str, list[tuple[str, str]]] = {
    "python": [(r"^\s*(?:async\s+)?def\s+(\w+)", "function"), (r"^\s*class\s+(\w+)", "class"),
               (r"^\s*([A-Z][A-Z0-9_]{2,})\s*(?::[^=\n]+)?=(?!=)", "constant"),
               (r"^\s*([A-Za-z_]\w*)\s*(?::[^=\n]+)?=(?!=)", "variable")],
    "javascript": [(r"function\*?\s+(\w+)", "function"), (r"class\s+(\w+)", "class"),
                   (r"(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?(?:function|\([^)]*\)\s*=>|\w+\s*=>)", "function"),
                   (r"const\s+([A-Z][A-Z0-9_]{2,})\b", "constant"), (r"(?:const|let|var)\s+(\w+)", "variable")],
    "typescript": [(r"function\*?\s+(\w+)", "function"), (r"(?:class|interface|enum)\s+(\w+)", "class"),
                   (r"type\s+(\w+)\s*[=<]", "class"),
                   (r"(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?(?:function|\([^)]*\)\s*=>|\w+\s*=>)", "function"),
                   (r"(?:const|let|var)\s+(\w+)", "variable")],
    "go": [(r"^func\s+(?:\([^)]*\)\s*)?(\w+)", "function"), (r"^type\s+(\w+)", "class"),
           (r"^\s*(?:var|const)\s+(\w+)", "variable")],
    "rust": [(r"\bfn\s+(\w+)", "function"), (r"\b(?:struct|enum|trait)\s+(\w+)", "class"),
             (r"\b(?:let|const|static)\s+(?:mut\s+)?(\w+)", "variable")],
    "java": [(r"\bclass\s+(\w+)", "class"), (r"\b(?:public|private|protected|static)[\w\s<>\[\],]*\s(\w+)\s*\(", "function")],
}
_SYMBOL_PATTERNS["jsx"] = _SYMBOL_PATTERNS["javascript"]
_SYMBOL_PATTERNS["tsx"] = _SYMBOL_PATTERNS["typescript"]
_SYMBOL_PATTERNS["csharp"] = _SYMBOL_PATTERNS["java"]


def extract_symbols(text: str, lang: str) -> dict[str, str]:
    """Nome → tipo (function/class/constant/variable) das definições do arquivo."""
    out: dict[str, str] = {}
    for pat, kind in _SYMBOL_PATTERNS.get(lang, []):
        for m in re.finditer(pat, text, re.M):
            name = m.group(1)
            if len(name) > 1 and name not in out:
                out[name] = kind
    return out


@dataclass
class WordInfo:
    count: int = 0
    last_line: int = 0


@dataclass
class DocIndex:
    words: dict[str, WordInfo] = field(default_factory=dict)
    symbols: dict[str, str] = field(default_factory=dict)
    attrs: dict[str, Counter[str]] = field(default_factory=dict)
    import_words: Counter[str] = field(default_factory=Counter)


_ATTR = re.compile(r"([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\.([A-Za-z_$][\w$]*)")
_SELF_DEF = re.compile(r"^\s*(?:async\s+)?def\s+(\w+)\s*\(\s*self\b", re.M)


def build_index(text: str, lang: str, cursor_line: int = 1) -> DocIndex:
    """Indexa palavras, símbolos e atributos. Arquivos enormes: só uma janela em torno do cursor."""
    if len(text) > MAX_INDEX_CHARS:
        lines = text.split("\n")
        lo = max(0, cursor_line - 1 - WINDOW_LINES)
        text = "\n".join(lines[lo: cursor_line - 1 + WINDOW_LINES])
        base = lo
    else:
        base = 0
    idx = DocIndex(symbols=extract_symbols(text, lang))
    for ln, line in enumerate(text.split("\n"), 1 + base):
        for m in _IDENT.finditer(line):
            w = m.group()
            if len(w) < 3:
                continue
            info = idx.words.get(w)
            if info is None:
                idx.words[w] = WordInfo(1, ln)
            else:
                info.count += 1
                info.last_line = ln
        for m in _ATTR.finditer(line):
            idx.attrs.setdefault(m.group(1), Counter())[m.group(2)] += 1
        if re.match(r"^\s*(?:from|import)\b", line):
            for w in _IDENT.findall(line):
                if w not in ("from", "import", "as"):
                    idx.import_words[w] += 1
    for m in _SELF_DEF.finditer(text):
        idx.attrs.setdefault("self", Counter())[m.group(1)] += 2
    return idx


def _loaded_stdlib_names(module: str, lang: str) -> list[str]:
    """Nomes públicos de um módulo da stdlib já carregado (nunca o importa)."""
    if lang != "python":
        return []
    top = module.split(".")[0]
    if top not in getattr(sys, "stdlib_module_names", ()):
        return []
    mod = sys.modules.get(module)
    if mod is None:
        return []
    try:
        return [n for n in dir(mod) if not n.startswith("_")]
    except Exception:  # noqa: BLE001
        return []


@dataclass
class Candidate:
    label: str
    insert: str
    kind: str
    source: str
    score: float = 0.0
    detail: str = ""


_KIND_BONUS = {"lsp": 0.30, "member": 0.0, "symbol": 0.22, "snippet": 0.08, "keyword": 0.0, "word": 0.0,
               "module": 0.12}
_SYMBOL_BONUS = {"function": 0.22, "class": 0.22, "constant": 0.20, "variable": 0.08}
_KEYWORD_STATEMENT_BOOST = 0.30


class SuggestionEngine:
    """Ranking de sugestões para um documento. Compartilha o aprendizado (MRU) entre instâncias."""

    _shared_mru: dict[str, int] = {}
    _clock = 0

    def __init__(self, max_items: int = 24) -> None:
        self.max_items = max_items
        self._index = DocIndex()
        self._key: tuple[int, str] | None = None

    @classmethod
    def record_accept(cls, insert: str) -> None:
        cls._clock += 1
        cls._shared_mru[insert] = cls._clock
        if len(cls._shared_mru) > 500:
            keep = sorted(cls._shared_mru.items(), key=lambda kv: kv[1], reverse=True)[:300]
            cls._shared_mru = dict(keep)

    @classmethod
    def _mru_bonus(cls, insert: str) -> float:
        stamp = cls._shared_mru.get(insert)
        if stamp is None:
            return 0.0
        age = cls._clock - stamp
        return 0.25 * (0.93 ** age)

    def update_document(self, text: str, lang: str, cursor_line: int = 1) -> None:
        """Reindexa só se o conteúdo/linguagem mudou (barato em re-chamadas)."""
        key = (hash(text), lang)
        if key == self._key:
            return
        self._key = key
        self._index = build_index(text, lang, cursor_line)

    @property
    def index(self) -> DocIndex:
        return self._index

    def suggest(self, ctx: LineContext, lang: str, *, cursor_line: int = 1, keywords: Any = (),
                snippets: Any = (), lsp_items: Any = (), icons: dict[str, str] | None = None) -> list[Candidate]:
        """Lista ordenada de candidatos para *ctx*.

        *snippets*: iterável de ``(prefix, description)``; *lsp_items*: de
        ``(label, insert, kind_name, icon)``.
        """
        if ctx.suppress:
            return []
        if not ctx.prefix and not ctx.member:
            return []
        icons = icons or {}
        kwset = frozenset(keywords)
        idx = self._index
        pool: dict[str, Candidate] = {}

        def add(label: str, insert: str, kind: str, source: str, extra: float = 0.0, detail: str = "") -> None:
            ms = match_score(ctx.prefix, insert)
            if ms is None:
                return
            bonus = _SYMBOL_BONUS.get(kind, 0.22) if source == "symbol" else _KIND_BONUS.get(source, 0.0)
            score = ms + bonus + extra + self._mru_bonus(insert)
            key = insert.lower()
            cur = pool.get(key)
            if cur is None or score > cur.score:
                pool[key] = Candidate(label, insert, kind, source, score, detail)

        def icon(kind: str) -> str:
            return icons.get(kind, "")

        if ctx.member:
            q = ctx.qualifier or ""
            seen = idx.attrs.get(q, {})
            if not seen and q in ("this",):
                seen = idx.attrs.get("self", {})
            for attr, n in seen.items():
                add(f"{icon('property')}{attr}", attr, "property", "member", 0.5 + min(0.15, 0.04 * math.log2(n + 1)),
                    "membro")
            for ci in lsp_items:
                lab, ins, kname, ic = ci
                add(f"{ic} {lab}".strip(), ins, kname, "lsp")
            for w, info in idx.words.items():
                if w not in kwset:
                    add(f"{icon('variable')}{w}", w, "variable", "word", -0.2 + self._prox(info, cursor_line))
            return self._finish(pool)

        if ctx.in_import:
            if ctx.import_from:
                for name in _loaded_stdlib_names(ctx.import_from, lang):
                    add(f"{icon('variable')}{name}", name, "variable", "module", 0.1)
            elif lang == "python":
                for mod in getattr(sys, "stdlib_module_names", ()):
                    if not mod.startswith("_"):
                        add(f"{icon('module')}{mod}", mod, "module", "module")
            for w in idx.import_words:
                if w != ctx.import_from:
                    add(f"{icon('module')}{w}", w, "module", "module", 0.1)
            for ci in lsp_items:
                lab, ins, kname, ic = ci
                add(f"{ic} {lab}".strip(), ins, kname, "lsp")
            return self._finish(pool)

        for ci in lsp_items:
            lab, ins, kname, ic = ci
            add(f"{ic} {lab}".strip(), ins, kname, "lsp")
        for name, kind in idx.symbols.items():
            info = idx.words.get(name)
            add(f"{icon(kind)}{name}", name, kind, "symbol", self._prox(info, cursor_line) if info else 0.0,
                {"function": "função", "class": "classe", "constant": "constante", "variable": "variável"}.get(kind, ""))
        for prefix_s, desc in snippets:
            add(f"{icon('snippet')}{prefix_s}" + (f"  ({desc})" if desc else ""), prefix_s, "snippet", "snippet",
                0.15 if ctx.statement_start else 0.0)
        for kw in keywords:
            boost = _KEYWORD_STATEMENT_BOOST if (ctx.statement_start and kw in _STATEMENT_KW) else (-0.05 if kw in _STATEMENT_KW else 0.0)
            add(f"{icon('keyword')}{kw}", kw, "keyword", "keyword", boost)
        for w, info in idx.words.items():
            if w in kwset:
                continue
            freq = min(0.15, 0.04 * math.log2(info.count + 1))
            add(f"{icon('variable')}{w}", w, "variable", "word", freq + self._prox(info, cursor_line))
        return self._finish(pool)

    @staticmethod
    def _prox(info: WordInfo | None, cursor_line: int) -> float:
        """Bônus por quão perto do cursor a palavra apareceu pela última vez."""
        if info is None:
            return 0.0
        return 0.12 * math.exp(-abs(cursor_line - info.last_line) / 80.0)

    def _finish(self, pool: dict[str, Candidate]) -> list[Candidate]:
        out = sorted(pool.values(), key=lambda c: (-round(c.score, 6), len(c.insert), c.insert.lower(), c.insert))
        return out[: self.max_items]
