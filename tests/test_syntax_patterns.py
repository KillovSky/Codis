#!/usr/bin/env python3
"""Testes para src.syntax.patterns - padrões regex de realce de sintaxe."""

from __future__ import annotations

import re

import pytest

from src.syntax.patterns import LANG_RE, _compile_all_patterns, _pat

_compile_all_patterns()


class TestCompilation:
    """Verifica a compilação dos padrões de todas as linguagens."""

    @staticmethod
    def test_lang_re_not_empty():
        """LANG_RE deve conter pelo menos um padrão."""
        assert len(LANG_RE) > 0

    @staticmethod
    def test_expected_languages_present():
        """Todas as linguagens esperadas devem estar presentes em LANG_RE."""
        expected = (
            "python",
            "javascript",
            "typescript",
            "html",
            "css",
            "json",
            "markdown",
            "yaml",
            "rust",
            "go",
            "sql",
        )
        for lang in expected:
            assert lang in LANG_RE, f"'{lang}' ausente em LANG_RE"

    @staticmethod
    def test_all_patterns_are_compiled():
        """Todos os valores de LANG_RE devem ser padrões regex compilados."""
        compiled_type = type(re.compile(""))
        for lang, pat in LANG_RE.items():
            assert isinstance(pat, compiled_type), f"{lang}: não é Pattern"

    @staticmethod
    def test_pat_helper_creates_named_groups():
        """_pat() deve criar grupos nomeados conforme os tokens fornecidos."""
        pat = _pat([("keyword", r"\bdef\b"), ("number", r"\d+")])
        assert pat.groupindex.get("keyword") is not None
        assert pat.groupindex.get("number") is not None

    @staticmethod
    def test_pat_multiline_flag():
        """Padrões gerados por _pat() devem ter a flag MULTILINE."""
        pat = _pat([("kw", r"^def")])
        assert pat.flags & re.MULTILINE


class TestPythonPattern:
    """Verifica o padrão de realce de sintaxe do Python."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        """Configura o padrão Python para uso nos métodos de instância."""
        self.re = LANG_RE["python"]

    def _match_group(self, text: str, group: str) -> bool:
        """Retorna True se o texto corresponder ao grupo especificado."""
        m = self.re.search(text)
        return bool(m and m.lastgroup == group)

    def test_keyword_def(self):
        """'def' deve ser reconhecido como keyword."""
        assert self._match_group("def foo", "keyword")

    def test_keyword_class(self):
        """'class' deve ser reconhecido como keyword."""
        assert self._match_group("class Foo", "keyword")

    def test_keyword_return(self):
        """'return' deve ser reconhecido como keyword."""
        assert self._match_group("return x", "keyword")

    def test_keyword_import(self):
        """'import' deve ser reconhecido como keyword."""
        assert self._match_group("import os", "keyword")

    def test_keyword_if(self):
        """'if' deve ser reconhecido como keyword."""
        assert self._match_group("if True:", "keyword")

    def test_keyword_for(self):
        """'for' deve ser reconhecido como keyword."""
        assert self._match_group("for x in y:", "keyword")

    def test_keyword_async(self):
        """'async' deve ser reconhecido como keyword."""
        assert self._match_group("async def f", "keyword")

    def test_keyword_await(self):
        """'await' deve ser reconhecido como keyword."""
        assert self._match_group("await coro()", "keyword")

    def test_keyword_lambda(self):
        """'lambda' deve ser reconhecido como keyword."""
        assert self._match_group("lambda x: x", "keyword")

    def test_keyword_yield(self):
        """'yield' deve ser reconhecido como keyword."""
        assert self._match_group("yield value", "keyword")

    def test_builtin_print(self):
        """'print' deve ser reconhecido como builtin."""
        assert self._match_group("print(x)", "builtin")

    def test_builtin_len(self):
        """'len' deve ser reconhecido como builtin."""
        assert self._match_group("len(x)", "builtin")

    def test_builtin_range(self):
        """'range' deve ser reconhecido como builtin."""
        assert self._match_group("range(10)", "builtin")

    def test_builtin_isinstance(self):
        """'isinstance' deve ser reconhecido como builtin."""
        assert self._match_group("isinstance(x, int)", "builtin")

    def test_builtin_none(self):
        """'None' deve ser reconhecido como builtin."""
        assert self._match_group("None", "builtin")

    def test_builtin_true(self):
        """'True' deve ser reconhecido como builtin."""
        assert self._match_group("True", "builtin")

    def test_builtin_false(self):
        """'False' deve ser reconhecido como builtin."""
        assert self._match_group("False", "builtin")

    def test_string_double_quote(self):
        """String com aspas duplas deve ser reconhecida."""
        assert self._match_group('"hello"', "string")

    def test_string_single_quote(self):
        """String com aspas simples deve ser reconhecida."""
        assert self._match_group("'hello'", "string")

    def test_string_triple_double(self):
        """String com aspas triplas duplas deve ser reconhecida."""
        assert self._match_group('"""doc"""', "string")

    def test_string_triple_single(self):
        """String com aspas triplas simples deve ser reconhecida."""
        assert self._match_group("'''doc'''", "string")

    def test_string_fstring(self):
        """f-string deve ser reconhecida como string."""
        assert self._match_group('f"hello {x}"', "string")

    def test_string_raw(self):
        """r-string deve ser reconhecida como string."""
        assert self._match_group(r'r"\n"', "string")

    def test_string_bytes(self):
        """b-string deve ser reconhecida como string."""
        assert self._match_group('b"bytes"', "string")

    def test_comment(self):
        """Comentário com '#' deve ser reconhecido como comment_line."""
        assert self._match_group("# comment here", "comment_line")

    def test_decorator(self):
        """Decorador com '@' deve ser reconhecido como decorator."""
        assert self._match_group("@property", "decorator")

    def test_decorator_with_args(self):
        """Decorador com argumentos deve ser reconhecido como decorator."""
        assert self._match_group("@pytest.fixture()", "decorator")

    def test_number_integer(self):
        """Inteiro literal deve ser reconhecido como number."""
        assert self._match_group("x = 42", "number")

    def test_number_float(self):
        """Float literal deve ser reconhecido como number."""
        assert self._match_group("x = 3.14", "number")

    def test_number_hex(self):
        """Hexadecimal deve ser reconhecido como number."""
        assert self._match_group("0xFF", "number")

    def test_number_octal(self):
        """Octal deve ser reconhecido como number."""
        assert self._match_group("0o77", "number")

    def test_number_binary(self):
        """Binário deve ser reconhecido como number."""
        assert self._match_group("0b1010", "number")

    def test_number_underscore(self):
        """Número com separador '_' deve ser reconhecido como number."""
        assert self._match_group("1_000_000", "number")


class TestJavaScriptPattern:
    """Verifica o padrão de realce de sintaxe do JavaScript."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        """Configura o padrão JavaScript para uso nos métodos de instância."""
        self.re = LANG_RE["javascript"]

    def _match_group(self, text: str, group: str) -> bool:
        """Retorna True se o texto corresponder ao grupo especificado."""
        m = self.re.search(text)
        return bool(m and m.lastgroup == group)

    def test_keyword_const(self):
        """'const' deve ser reconhecido como keyword."""
        assert self._match_group("const x = 1", "keyword")

    def test_keyword_let(self):
        """'let' deve ser reconhecido como keyword."""
        assert self._match_group("let y = 2", "keyword")

    def test_keyword_var(self):
        """'var' deve ser reconhecido como keyword."""
        assert self._match_group("var z", "keyword")

    def test_keyword_function(self):
        """'function' deve ser reconhecido como keyword."""
        assert self._match_group("function foo", "keyword")

    def test_keyword_class(self):
        """'class' deve ser reconhecido como keyword."""
        assert self._match_group("class Foo", "keyword")

    def test_keyword_return(self):
        """'return' deve ser reconhecido como keyword."""
        assert self._match_group("return x", "keyword")

    def test_keyword_async(self):
        """'async' deve ser reconhecido como keyword."""
        assert self._match_group("async function", "keyword")

    def test_keyword_await(self):
        """'await' deve ser reconhecido como keyword."""
        assert self._match_group("await fetch()", "keyword")

    def test_keyword_import(self):
        """'import' deve ser reconhecido como keyword."""
        assert self._match_group("import x from", "keyword")

    def test_keyword_export(self):
        """'export' deve ser reconhecido como keyword."""
        assert self._match_group("export default", "keyword")

    def test_builtin_console(self):
        """'console' deve ser reconhecido como builtin."""
        assert self._match_group("console.log", "builtin")

    def test_builtin_null(self):
        """'null' deve ser reconhecido como builtin."""
        assert self._match_group("null", "builtin")

    def test_builtin_undefined(self):
        """'undefined' deve ser reconhecido como builtin."""
        assert self._match_group("undefined", "builtin")

    def test_builtin_true(self):
        """'true' deve ser reconhecido como builtin."""
        assert self._match_group("true", "builtin")

    def test_builtin_false(self):
        """'false' deve ser reconhecido como builtin."""
        assert self._match_group("false", "builtin")

    def test_builtin_promise(self):
        """'Promise' deve ser reconhecido como builtin."""
        assert self._match_group("Promise.resolve", "builtin")

    def test_builtin_json(self):
        """'JSON' deve ser reconhecido como builtin."""
        assert self._match_group("JSON.parse", "builtin")

    def test_string_double(self):
        """String com aspas duplas deve ser reconhecida."""
        assert self._match_group('"hello"', "string")

    def test_string_single(self):
        """String com aspas simples deve ser reconhecida."""
        assert self._match_group("'hello'", "string")

    def test_template_literal(self):
        """Template literal com backtick deve ser reconhecida como string_tmpl."""
        assert self._match_group("`hello ${x}`", "string_tmpl")

    def test_comment_line(self):
        """Comentário de linha '//' deve ser reconhecido como comment_line."""
        assert self._match_group("// comment", "comment_line")

    def test_comment_block(self):
        """Comentário de bloco '/* */' deve ser reconhecido como comment_block."""
        assert self._match_group("/* block */", "comment_block")

    def test_number_integer(self):
        """Inteiro literal deve ser reconhecido como number."""
        assert self._match_group("42", "number")

    def test_number_float(self):
        """Float literal deve ser reconhecido como number."""
        assert self._match_group("3.14", "number")

    def test_number_hex(self):
        """Hexadecimal deve ser reconhecido como number."""
        assert self._match_group("0xFF", "number")

    def test_decorator(self):
        """Decorador TypeScript/JS com '@' deve ser reconhecido."""
        assert self._match_group("@Component", "decorator")


class TestHTMLPattern:
    """Verifica o padrão de realce de sintaxe do HTML."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        """Configura o padrão HTML para uso nos métodos de instância."""
        self.re = LANG_RE["html"]

    def test_matches_tag(self):
        """Tag de abertura deve ser reconhecida pelo padrão."""
        assert self.re.search("<div>") is not None

    def test_matches_closing_tag(self):
        """Tag de fechamento deve ser reconhecida pelo padrão."""
        assert self.re.search("</div>") is not None

    def test_matches_attribute(self):
        """Atributo HTML deve ser reconhecido pelo padrão."""
        assert self.re.search('class="container"') is not None

    def test_matches_comment(self):
        """Comentário HTML deve ser reconhecido pelo padrão."""
        assert self.re.search("<!-- comment -->") is not None

    def test_matches_doctype(self):
        """DOCTYPE deve ser reconhecido pelo padrão."""
        assert self.re.search("<!DOCTYPE html>") is not None


class TestCSSPattern:
    """Verifica o padrão de realce de sintaxe do CSS."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        """Configura o padrão CSS para uso nos métodos de instância."""
        self.re = LANG_RE["css"]

    def test_matches_property(self):
        """Propriedade CSS deve ser reconhecida pelo padrão."""
        assert self.re.search("color: red;") is not None

    def test_matches_selector(self):
        """Seletor CSS deve ser reconhecido pelo padrão."""
        assert self.re.search(".container {") is not None

    def test_matches_hex_color(self):
        """Cor hexadecimal CSS deve ser reconhecida pelo padrão."""
        assert self.re.search("#FF0000") is not None

    def test_matches_number_with_unit(self):
        """Valor com unidade CSS deve ser reconhecido pelo padrão."""
        assert self.re.search("16px") is not None

    def test_matches_comment(self):
        """Comentário CSS deve ser reconhecido pelo padrão."""
        assert self.re.search("/* comment */") is not None


class TestJSONPattern:
    """Verifica o padrão de realce de sintaxe do JSON."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        """Configura o padrão JSON para uso nos métodos de instância."""
        self.re = LANG_RE["json"]

    def test_matches_string_key(self):
        """Chave de string JSON deve ser reconhecida pelo padrão."""
        assert self.re.search('"key"') is not None

    def test_matches_number(self):
        """Número JSON deve ser reconhecido pelo padrão."""
        assert self.re.search("42") is not None

    def test_matches_boolean_true(self):
        """Booleano JSON 'true' deve ser reconhecido pelo padrão."""
        assert self.re.search("true") is not None

    def test_matches_null(self):
        """Valor 'null' do JSON deve ser reconhecido pelo padrão."""
        assert self.re.search("null") is not None


class TestSQLPattern:
    """Verifica o padrão de realce de sintaxe do SQL."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        """Configura o padrão SQL para uso nos métodos de instância."""
        self.re = LANG_RE["sql"]

    def test_select_keyword(self):
        """Cláusula SELECT deve ser reconhecida pelo padrão."""
        assert self.re.search("SELECT * FROM users") is not None

    def test_where_keyword(self):
        """Cláusula WHERE deve ser reconhecida pelo padrão."""
        assert self.re.search("WHERE id = 1") is not None

    def test_comment(self):
        """Comentário SQL '--' deve ser reconhecido pelo padrão."""
        assert self.re.search("-- inline comment") is not None


class TestYAMLPattern:
    """Verifica o padrão de realce de sintaxe do YAML."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        """Configura o padrão YAML para uso nos métodos de instância."""
        self.re = LANG_RE["yaml"]

    def test_matches_key(self):
        """Chave YAML deve ser reconhecida pelo padrão."""
        assert self.re.search("name: value") is not None

    def test_matches_comment(self):
        """Comentário YAML deve ser reconhecido pelo padrão."""
        assert self.re.search("# yaml comment") is not None

    def test_matches_list_item(self):
        """Item de lista YAML deve ser reconhecido pelo padrão."""
        assert self.re.search("- item") is not None


class TestRustPattern:
    """Verifica o padrão de realce de sintaxe do Rust."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        """Configura o padrão Rust para uso nos métodos de instância."""
        self.re = LANG_RE["rust"]

    def test_keyword_fn(self):
        """Palavra-chave 'fn' deve ser reconhecida pelo padrão."""
        assert self.re.search("fn main()") is not None

    def test_keyword_let(self):
        """Palavra-chave 'let' deve ser reconhecida pelo padrão."""
        assert self.re.search("let mut x") is not None

    def test_comment(self):
        """Comentário Rust deve ser reconhecido pelo padrão."""
        assert self.re.search("// rust comment") is not None

    def test_string(self):
        """String Rust deve ser reconhecida pelo padrão."""
        assert self.re.search('"hello"') is not None


class TestMarkdownPattern:
    """Verifica o padrão de realce de sintaxe do Markdown."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        """Configura o padrão Markdown para uso nos métodos de instância."""
        self.re = LANG_RE["markdown"]

    def test_heading(self):
        """Cabeçalho Markdown deve ser reconhecido pelo padrão."""
        assert self.re.search("# Heading 1") is not None

    def test_bold(self):
        """Texto em negrito Markdown deve ser reconhecido pelo padrão."""
        assert self.re.search("**bold**") is not None

    def test_inline_code(self):
        """Código inline Markdown deve ser reconhecido pelo padrão."""
        assert self.re.search("`code`") is not None

    def test_link(self):
        """Link Markdown deve ser reconhecido pelo padrão."""
        assert self.re.search("[text](url)") is not None
