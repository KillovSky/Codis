#!/usr/bin/env python3
"""Testes para src.editor.snippets - Snippet e SnippetEngine."""

from __future__ import annotations

import sys

import pytest

from src.editor.snippets import Snippet, SnippetEngine


class TestSnippetDataclass:
    """Verifica o dataclass Snippet e seus campos."""

    @staticmethod
    def test_basic_fields():
        """Os campos prefix, body e description devem ser armazenados corretamente."""
        s = Snippet(
            prefix="def", body="def ${1:name}():\n    pass", description="Function"
        )
        assert s.prefix == "def"
        assert "${1:name}" in s.body
        assert s.description == "Function"

    @staticmethod
    def test_default_scope_empty():
        """O campo scope deve ser uma lista vazia quando não fornecido."""
        s = Snippet(prefix="x", body="y")
        assert not s.scope

    @staticmethod
    def test_scope_with_languages():
        """Linguagens fornecidas em scope devem ser armazenadas corretamente."""
        s = Snippet(prefix="x", body="y", scope=["python", "javascript"])
        assert "python" in s.scope

    @staticmethod
    def test_scope_not_shared():
        """Modificar o scope de um snippet não deve afetar outros snippets."""
        a = Snippet(prefix="a", body="b")
        b = Snippet(prefix="c", body="d")
        a.scope.append("python")
        assert "python" not in b.scope


class TestGetForLang:
    """Verifica a recuperação de snippets por linguagem."""

    @pytest.fixture()
    @staticmethod
    def engine():
        """Instância limpa de SnippetEngine."""
        return SnippetEngine()

    @staticmethod
    def test_python_snippets_loaded(engine):
        """Python deve ter pelo menos um snippet carregado."""
        assert engine.get_for_lang("python")

    @staticmethod
    def test_javascript_snippets_loaded(engine):
        """JavaScript deve ter pelo menos um snippet carregado."""
        assert engine.get_for_lang("javascript")

    @staticmethod
    def test_html_snippets_loaded(engine):
        """HTML deve ter pelo menos um snippet carregado."""
        assert engine.get_for_lang("html")

    @staticmethod
    def test_unknown_lang_returns_empty(engine):
        """Linguagem desconhecida deve retornar lista vazia."""
        assert engine.get_for_lang("brainfuck_xyz") == []

    @staticmethod
    def test_all_snippets_are_snippet_instances(engine):
        """Todos os snippets retornados devem ser instâncias de Snippet."""
        for lang in ("python", "javascript", "html", "css"):
            for s in engine.get_for_lang(lang):
                assert isinstance(s, Snippet)

    @staticmethod
    def test_python_has_def_snippet(engine):
        """Python deve conter snippet com prefix 'def'."""
        prefixes = [s.prefix for s in engine.get_for_lang("python")]
        assert "def" in prefixes

    @staticmethod
    def test_python_has_class_snippet(engine):
        """Python deve conter snippet com prefix 'class'."""
        prefixes = [s.prefix for s in engine.get_for_lang("python")]
        assert "class" in prefixes

    @staticmethod
    def test_python_has_for_snippet(engine):
        """Python deve conter snippet com prefix 'for'."""
        prefixes = [s.prefix for s in engine.get_for_lang("python")]
        assert "for" in prefixes

    @staticmethod
    def test_python_has_try_snippet(engine):
        """Python deve conter snippet com prefix 'try'."""
        prefixes = [s.prefix for s in engine.get_for_lang("python")]
        assert "try" in prefixes

    @staticmethod
    def test_js_has_fn_snippet(engine):
        """JavaScript deve conter snippet com prefix 'fn'."""
        prefixes = [s.prefix for s in engine.get_for_lang("javascript")]
        assert "fn" in prefixes

    @staticmethod
    def test_js_has_afn_snippet(engine):
        """JavaScript deve conter snippet com prefix 'afn'."""
        prefixes = [s.prefix for s in engine.get_for_lang("javascript")]
        assert "afn" in prefixes


class TestFindPrefix:
    """Verifica a busca de snippets por prefix exato."""

    @pytest.fixture()
    @staticmethod
    def engine():
        """Instância limpa de SnippetEngine."""
        return SnippetEngine()

    @staticmethod
    def test_finds_exact_match(engine):
        """find_prefix() deve retornar o snippet com prefix exato."""
        result = engine.find_prefix("python", "def")
        assert result is not None
        assert result.prefix == "def"

    @staticmethod
    def test_returns_none_for_unknown_prefix(engine):
        """find_prefix() com prefix desconhecido deve retornar None."""
        assert engine.find_prefix("python", "zzznotaprefix") is None

    @staticmethod
    def test_case_sensitive(engine):
        """find_prefix() deve ser sensível a maiúsculas/minúsculas."""
        assert engine.find_prefix("python", "DEF") is None

    @staticmethod
    def test_partial_prefix_no_match(engine):
        """Prefix parcial não deve fazer match com prefix completo."""
        assert engine.find_prefix("python", "de") is None

    @staticmethod
    def test_unknown_lang_returns_none(engine):
        """find_prefix() com linguagem desconhecida deve retornar None."""
        assert engine.find_prefix("brainfuck", "def") is None

    @staticmethod
    def test_js_prefix_found(engine):
        """find_prefix() deve encontrar 'fn' em JavaScript."""
        assert engine.find_prefix("javascript", "fn") is not None

    @staticmethod
    def test_html_prefix_found(engine):
        """find_prefix() deve encontrar o primeiro prefix de HTML."""
        snippets = engine.get_for_lang("html")
        if snippets:
            prefix = snippets[0].prefix
            assert engine.find_prefix("html", prefix) is not None


class TestAddRemove:
    """Verifica adição e remoção de snippets."""

    @pytest.fixture()
    @staticmethod
    def engine():
        """Instância limpa de SnippetEngine."""
        return SnippetEngine()

    @staticmethod
    def test_add_new_snippet(engine):
        """add() deve tornar o snippet localizável por find_prefix()."""
        engine.add("python", "mysnip", "print('${1:hello}')", "My snippet")
        assert engine.find_prefix("python", "mysnip") is not None

    @staticmethod
    def test_add_creates_lang_if_missing(engine):
        """add() deve criar o suporte à linguagem se ainda não existir."""
        engine.add("newlang", "snip", "body")
        assert engine.find_prefix("newlang", "snip") is not None

    @staticmethod
    def test_remove_existing(engine):
        """remove() deve excluir o snippet da engine."""
        engine.add("python", "rmsnip", "body")
        engine.remove("python", "rmsnip")
        assert engine.find_prefix("python", "rmsnip") is None

    @staticmethod
    def test_remove_nonexistent_does_not_raise(engine):
        """remove() com prefix inexistente não deve lançar exceção."""
        engine.remove("python", "totally_nonexistent_prefix_xyz")

    @staticmethod
    def test_remove_unknown_lang_does_not_raise(engine):
        """remove() com linguagem desconhecida não deve lançar exceção."""
        engine.remove("unknownlang", "prefix")

    @staticmethod
    def test_register_adds_snippet(engine):
        """register() deve tornar o snippet localizável por find_prefix()."""
        s = Snippet(prefix="regtest", body="body ${1}")
        engine.register("python", s)
        assert engine.find_prefix("python", "regtest") is not None

    @staticmethod
    def test_add_description_stored(engine):
        """A descrição fornecida em add() deve ser armazenada no snippet."""
        engine.add("python", "desctest", "body", "My Description")
        s = engine.find_prefix("python", "desctest")
        assert s.description == "My Description"


class TestExpand:
    """Verifica a expansão de snippets em widgets Text."""

    @pytest.fixture()
    @staticmethod
    def engine():
        """Instância limpa de SnippetEngine."""
        return SnippetEngine()

    @pytest.fixture()
    def mock_text(self):
        """Widget tk.Text simulado com estado mínimo para expansão."""
        tk = sys.modules["tkinter"]

        class _FakeText(tk.Text):
            """Text stub que rastreia inserções para validação dos testes."""

            def __init__(self):
                """Inicializa o Text stub com conteúdo vazio."""
                self._content = ""
                self._insert_pos = "1.0"

            def get(self, _start="1.0", _end=None):
                """Retorna o conteúdo acumulado."""
                return self._content

            def insert(self, _pos, text, *_tags):
                """Acumula texto inserido."""
                self._content += text

            def delete(self, start, _end=None):
                """Limpa o conteúdo se start corresponde à posição de inserção."""
                if start in ("1.0", self._insert_pos):
                    self._content = ""
                else:
                    prefix_len = len(self._content)
                    self._content = self._content[: max(0, prefix_len - 3)]

            def index(self, pos):
                """Calcula índice baseado no conteúdo atual."""
                lines = self._content.split("\n")
                return f"{len(lines)}.{len(lines[-1])}"

            def mark_set(self, _name, pos):
                """Atualiza a posição de inserção."""
                self._insert_pos = pos

            @staticmethod
            def mark_names():
                """Retorna marcas padrão."""
                return ["insert", "end"]

            @staticmethod
            def tag_add(*_a, **_kw):
                """Stub de tag_add."""

            @staticmethod
            def tag_remove(*_a, **_kw):
                """Stub de tag_remove."""

        return _FakeText()

    @staticmethod
    def test_expand_simple_snippet(engine, mock_text):
        """expand() com snippet válido deve retornar True."""
        s = Snippet(prefix="pr", body="print(${1})")
        result = engine.expand(s, mock_text)
        assert result is True

    @staticmethod
    def test_expand_returns_false_on_tkerror(engine):
        """expand() com widget defeituoso deve retornar False."""
        tk = sys.modules["tkinter"]

        class _BadText:
            """Text stub que lança TclError em index()."""

            @staticmethod
            def index(_pos):
                """Lança TclError simulando ambiente sem display."""
                raise tk.TclError("no display")

            @staticmethod
            def get(*_a):
                """Retorna string vazia."""
                return ""

            @staticmethod
            def insert(*_a):
                """Stub de insert."""

            @staticmethod
            def delete(*_a):
                """Stub de delete."""

            @staticmethod
            def mark_set(*_a):
                """Stub de mark_set."""

            @staticmethod
            def tag_add(*_a, **_kw):
                """Stub de tag_add."""

        s = Snippet(prefix="x", body="body")
        assert engine.expand(s, _BadText()) is False

    @staticmethod
    def test_expand_with_path(engine, mock_text, tmp_py_file):
        """expand() com path deve retornar True e inserir o nome do arquivo."""
        s = Snippet(prefix="f", body="# $TM_FILENAME\npass")
        result = engine.expand(s, mock_text, path=tmp_py_file)
        assert result is True
        assert tmp_py_file.name in mock_text._content

    @staticmethod
    def test_expand_substitutes_tm_filename(engine, mock_text, tmp_py_file):
        """$TM_FILENAME deve ser substituído pelo nome real do arquivo."""
        s = Snippet(prefix="x", body="file: $TM_FILENAME")
        engine.expand(s, mock_text, path=tmp_py_file)
        assert "sample.py" in mock_text._content

    @staticmethod
    def test_expand_no_path_uses_untitled(engine, mock_text):
        """Sem path, $TM_FILENAME deve ser substituído por 'untitled'."""
        s = Snippet(prefix="x", body="file: $TM_FILENAME")
        engine.expand(s, mock_text, path=None)
        assert "untitled" in mock_text._content


class TestBuiltinSnippetCoverage:
    """Verifica a cobertura dos snippets embutidos por linguagem."""

    @pytest.fixture()
    @staticmethod
    def engine():
        """Instância limpa de SnippetEngine."""
        return SnippetEngine()

    @pytest.mark.parametrize("lang", ["python", "javascript", "html", "css"])
    @staticmethod
    def test_lang_has_snippets(engine, lang):
        """Cada linguagem suportada deve ter pelo menos um snippet."""
        assert len(engine.get_for_lang(lang)) > 0

    @pytest.mark.parametrize("lang", ["python", "javascript", "html", "css"])
    @staticmethod
    def test_snippets_have_bodies(engine, lang):
        """Todos os snippets devem ter body não vazio."""
        for s in engine.get_for_lang(lang):
            assert s.body, f"{lang}/{s.prefix} tem body vazio"

    @pytest.mark.parametrize("lang", ["python", "javascript", "html", "css"])
    @staticmethod
    def test_snippets_have_prefixes(engine, lang):
        """Todos os snippets devem ter prefix não vazio."""
        for s in engine.get_for_lang(lang):
            assert s.prefix, f"{lang}: snippet sem prefix"

    @staticmethod
    def test_python_try_has_except(engine):
        """O snippet 'try' do Python deve conter bloco 'except'."""
        s = engine.find_prefix("python", "try")
        assert "except" in s.body

    @staticmethod
    def test_python_for_has_iteration(engine):
        """O snippet 'for' do Python deve conter a palavra 'in'."""
        s = engine.find_prefix("python", "for")
        assert "in" in s.body

    @staticmethod
    def test_python_class_has_init(engine):
        """O snippet 'class' do Python deve conter '__init__'."""
        s = engine.find_prefix("python", "class")
        assert "__init__" in s.body

    @staticmethod
    def test_js_afn_has_arrow(engine):
        """O snippet 'afn' do JavaScript deve conter '=>'."""
        s = engine.find_prefix("javascript", "afn")
        assert "=>" in s.body
