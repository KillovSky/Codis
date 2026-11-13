"""Codis - Transformações de Texto para o Editor.

Todas as transformações operam sobre a seleção atual (``tk.SEL``) ou, quando não
há seleção, sobre a palavra sob o cursor. Cada função é pura (não depende de UI)
para facilitar testes.

Transformações disponíveis:
    - Conversão de case: camelCase, PascalCase, snake_case, kebab-case, CONSTANT_CASE
    - Codificação: Base64 encode/decode, URL encode/decode, HTML escape/unescape
    - Inserção: UUID v4, timestamp ISO 8601, lorem ipsum
    - Hash: MD5, SHA-1, SHA-256 (do texto selecionado)
    - Deduplicate lines, Reverse lines
"""

from __future__ import annotations

import base64
import hashlib
import re
import tkinter as tk
import urllib.parse
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from html import escape as _html_escape
from html import unescape as _html_unescape

_LOREM = (
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit. "
    "Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. "
    "Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris "
    "nisi ut aliquip ex ea commodo consequat. Duis aute irure dolor in "
    "reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla "
    "pariatur. Excepteur sint occaecat cupidatat non proident, sunt in "
    "culpa qui officia deserunt mollit anim id est laborum."
)


def _get_selection(text: tk.Text) -> tuple[str, str, str] | None:
    """Retorna (conteúdo, índice_início, índice_fim) da seleção ou None."""
    try:
        if text.tag_ranges(tk.SEL):
            return (
                text.get(tk.SEL_FIRST, tk.SEL_LAST),
                text.index(tk.SEL_FIRST),
                text.index(tk.SEL_LAST),
            )
    except tk.TclError:
        pass
    return None


def _get_word(text: tk.Text) -> tuple[str, str, str] | None:
    """Retorna (palavra, índice_início, índice_fim) da palavra sob cursor ou None."""
    try:
        pos = text.index(tk.INSERT)
        start = text.index(f"{pos} wordstart")
        end = text.index(f"{pos} wordend")
        word = text.get(start, end).strip()
        if word:
            return word, start, end
    except tk.TclError:
        pass
    return None


def _get_target(text: tk.Text) -> tuple[str, str, str] | None:
    """Retorna seleção se existir, senão palavra sob cursor."""
    return _get_selection(text) or _get_word(text)


def _replace(text: tk.Text, new_val: str, start: str, end: str) -> None:
    """Substitui o intervalo [start, end] por new_val e reseleciona."""
    try:
        text.delete(start, end)
        text.insert(start, new_val)
        text.tag_add(tk.SEL, start, f"{start}+{len(new_val)}c")
        text.mark_set(tk.INSERT, f"{start}+{len(new_val)}c")
    except tk.TclError:
        pass


def _split_words(s: str) -> list[str]:
    """Divide uma string em palavras separando por espaço, _, -, ou fronteira camel."""
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", s)
    return [w for w in re.split(r"[\s\-_]+", s) if w]


def to_camel_case(s: str) -> str:
    """Converte o texto selecionado para camelCase."""
    words = _split_words(s)
    if not words:
        return s
    return words[0].lower() + "".join(w.capitalize() for w in words[1:])


def to_pascal_case(s: str) -> str:
    """Converte o texto selecionado para PascalCase."""
    return "".join(w.capitalize() for w in _split_words(s)) or s


def to_snake_case(s: str) -> str:
    """Converte o texto selecionado para snake_case."""
    return "_".join(w.lower() for w in _split_words(s)) or s


def to_kebab_case(s: str) -> str:
    """Converte o texto selecionado para kebab-case."""
    return "-".join(w.lower() for w in _split_words(s)) or s


def to_constant_case(s: str) -> str:
    """Converte o texto selecionado para CONSTANT_CASE."""
    return "_".join(w.upper() for w in _split_words(s)) or s


def to_dot_case(s: str) -> str:
    """Converte o texto selecionado para dot.case."""
    return ".".join(w.lower() for w in _split_words(s)) or s


def to_path_case(s: str) -> str:
    """Converte o texto selecionado para path/case."""
    return "/".join(w.lower() for w in _split_words(s)) or s


def encode_base64(s: str) -> str:
    """Codifica a seleção em Base64."""
    return base64.b64encode(s.encode()).decode()


def decode_base64(s: str) -> str:
    """Decodifica a seleção de Base64 para texto."""
    try:
        padded = s + "=" * (-len(s) % 4)
        return base64.b64decode(padded).decode()
    except Exception:
        return s


def encode_url(s: str) -> str:
    """Aplica URL encoding (percent-encoding) na seleção."""
    return urllib.parse.quote(s, safe="")


def decode_url(s: str) -> str:
    """Remove URL encoding (percent-encoding) da seleção."""
    return urllib.parse.unquote(s)


def escape_html(s: str) -> str:
    """Escapa entidades HTML na seleção (<, >, &, ", ')."""
    return _html_escape(s)


def unescape_html(s: str) -> str:
    """Remove escape de entidades HTML da seleção."""
    return _html_unescape(s)


def escape_json(s: str) -> str:
    """Escapa caracteres especiais para uso dentro de uma string JSON."""
    return (
        s.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


def hash_md5(s: str) -> str:
    """Substitui a seleção pelo seu hash MD5 (hex)."""
    return hashlib.md5(s.encode(), usedforsecurity=False).hexdigest()


def hash_sha1(s: str) -> str:
    """Substitui a seleção pelo seu hash SHA-1 (hex)."""
    return hashlib.sha1(s.encode(), usedforsecurity=False).hexdigest()


def hash_sha256(s: str) -> str:
    """Substitui a seleção pelo seu hash SHA-256 (hex)."""
    return hashlib.sha256(s.encode()).hexdigest()


def insert_uuid() -> str:
    """Insere um UUID v4 aleatório na posição do cursor."""
    return str(uuid.uuid4())


def insert_timestamp(fmt: str = "iso") -> str:
    """Insere o timestamp atual em formato ISO 8601."""
    now = datetime.now(UTC)
    if fmt == "iso":
        return now.strftime("%Y-%m-%dT%H:%M:%SZ")
    if fmt == "date":
        return now.strftime("%Y-%m-%d")
    if fmt == "unix":
        return str(int(now.timestamp()))
    return now.isoformat()


def insert_lorem(sentences: int = 1) -> str:
    """Insere um parágrafo de texto Lorem Ipsum."""
    parts = _LOREM.split(". ")
    return ". ".join(parts[:sentences]) + ("." if sentences <= len(parts) else "")


def deduplicate_lines(s: str) -> str:
    """Remove linhas duplicadas mantendo a ordem de primeira ocorrência."""
    seen: set[str] = set()
    result = []
    for line in s.splitlines():
        if line not in seen:
            seen.add(line)
            result.append(line)
    return "\n".join(result)


def reverse_lines(s: str) -> str:
    """Inverte a ordem das linhas selecionadas."""
    return "\n".join(reversed(s.splitlines()))


def number_lines(s: str, start: int = 1) -> str:
    """Numera as linhas selecionadas com prefixo 1., 2., ..."""
    lines = s.splitlines()
    width = len(str(len(lines) + start - 1))
    return "\n".join(f"{i:>{width}}  {l_}" for i, l_ in enumerate(lines, start))


def remove_empty_lines(s: str) -> str:
    """Remove linhas em branco do bloco selecionado."""
    return "\n".join(l_ for l_ in s.splitlines() if l_.strip())


TRANSFORMS: dict[str, tuple[Callable[[str], str], str, str]] = {
    "camel": (to_camel_case, "camelCase", "🐪"),
    "pascal": (to_pascal_case, "PascalCase", "🅿"),
    "snake": (to_snake_case, "snake_case", "🐍"),
    "kebab": (to_kebab_case, "kebab-case", "🍢"),
    "constant": (to_constant_case, "CONSTANT_CASE", "🔠"),
    "dot": (to_dot_case, "dot.case", "."),
    "path": (to_path_case, "path/case", "📁"),
    "b64enc": (encode_base64, "Base64 Encode", "📦"),
    "b64dec": (decode_base64, "Base64 Decode", "📤"),
    "urlenc": (encode_url, "URL Encode", "🔗"),
    "urldec": (decode_url, "URL Decode", "🔓"),
    "htmlesc": (escape_html, "HTML Escape", "🏷"),
    "htmlune": (unescape_html, "HTML Unescape", "🏷"),
    "jsonesc": (escape_json, "JSON Escape", "{ }"),
    "md5": (hash_md5, "MD5 Hash", "#"),
    "sha1": (hash_sha1, "SHA-1 Hash", "#"),
    "sha256": (hash_sha256, "SHA-256 Hash", "#"),
    "dedup": (deduplicate_lines, "Remover Duplicatas", "🧹"),
    "revlines": (reverse_lines, "Inverter Linhas", "🔃"),
    "numlines": (number_lines, "Numerar Linhas", "🔢"),
    "remempty": (remove_empty_lines, "Remover Linhas Vazias", "🗑"),
}


def apply_transform(text: tk.Text, key: str) -> bool:
    """Aplica uma transformação identificada por *key* na seleção/palavra atual.

    Args:
        text: Widget tk.Text alvo.
        key:  Chave do mapa TRANSFORMS.

    Returns:
        True se a transformação foi aplicada, False se nada foi feito.
    """
    entry = TRANSFORMS.get(key)
    if not entry:
        return False
    fn, _desc, _icon = entry
    target = _get_target(text)
    if not target:
        return False
    val, start, end = target
    new_val = fn(val)
    if new_val != val:
        _replace(text, new_val, start, end)
    return True


def insert_at_cursor(text: tk.Text, key: str) -> bool:
    """Insere conteúdo gerado (UUID, timestamp, lorem) na posição do cursor.

    Args:
        text: Widget tk.Text alvo.
        key:  'uuid', 'ts_iso', 'ts_date', 'ts_unix', 'lorem1', 'lorem3'.

    Returns:
        True se algo foi inserido.
    """
    generators: dict[str, Callable[[], str]] = {
        "uuid": insert_uuid,
        "ts_iso": lambda: insert_timestamp("iso"),
        "ts_date": lambda: insert_timestamp("date"),
        "ts_unix": lambda: insert_timestamp("unix"),
        "lorem1": lambda: insert_lorem(1),
        "lorem3": lambda: insert_lorem(3),
        "lorem5": lambda: insert_lorem(5),
    }
    fn = generators.get(key)
    if not fn:
        return False
    try:
        val = fn()
        text.insert(tk.INSERT, val)
        return True
    except tk.TclError:
        return False
