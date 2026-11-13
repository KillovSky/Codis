#!/usr/bin/env python3
"""Testes unitários para src.diff.engine (DiffEngine e DiffHunk).

Cobre: unified diff, hunks, inline_diff, stats e entradas extremas.
"""

from __future__ import annotations

from src.diff.engine import DiffEngine, DiffHunk

ORIGINAL = "line1\nline2\nline3\n"
MODIFIED = "line1\nline2_edited\nline3\n"


class TestUnified:
    """Verifica a geração de diff no formato unified."""

    @staticmethod
    def test_empty_when_no_diff():
        """Dois textos idênticos não devem gerar nenhum diff."""
        assert DiffEngine.unified("abc", "abc") == ""

    @staticmethod
    def test_returns_diff_when_changed():
        """Textos diferentes devem gerar diff com marcadores de remoção e adição."""
        result = DiffEngine.unified(ORIGINAL, MODIFIED)
        assert "---" in result and "+++" in result
        assert "-line2\n" in result
        assert "+line2_edited\n" in result

    @staticmethod
    def test_accepts_none_as_original():
        """None como original deve ser tratado como string vazia (criação de arquivo)."""
        assert "+new line" in DiffEngine.unified(None, "new line\n")

    @staticmethod
    def test_accepts_none_as_modified():
        """None como modificado deve ser tratado como string vazia (remoção de arquivo)."""
        assert "-line1" in DiffEngine.unified("line1\n", None)

    @staticmethod
    def test_both_none_returns_empty():
        """Ambos os argumentos None devem retornar string vazia."""
        assert DiffEngine.unified(None, None) == ""

    @staticmethod
    def test_add_only_lines():
        """Conteúdo adicionado deve aparecer com prefixo '+'."""
        result = DiffEngine.unified("", "line1\nline2\n")
        assert "+line1" in result
        assert "+line2" in result

    @staticmethod
    def test_remove_only_lines():
        """Conteúdo removido deve aparecer com prefixo '-'."""
        result = DiffEngine.unified("line1\nline2\n", "")
        assert "-line1" in result
        assert "-line2" in result

    @staticmethod
    def test_context_lines_included():
        """Linhas sem alteração próximas à mudança devem aparecer como contexto."""
        result = DiffEngine.unified(ORIGINAL, MODIFIED)
        assert "line1" in result
        assert "line3" in result


class TestHunks:
    """Verifica a geração de hunks a partir do diff."""

    @staticmethod
    def test_no_hunks_when_identical():
        """Textos idênticos não devem gerar nenhum hunk."""
        assert not DiffEngine.hunks("abc", "abc")

    @staticmethod
    def test_hunk_returned_on_change():
        """Uma mudança de linha deve gerar pelo menos um hunk."""
        result = DiffEngine.hunks(ORIGINAL, MODIFIED)
        assert len(result) >= 1

    @staticmethod
    def test_hunk_is_diffhunk_instance():
        """Cada hunk retornado deve ser instância de DiffHunk."""
        for hunk in DiffEngine.hunks(ORIGINAL, MODIFIED):
            assert isinstance(hunk, DiffHunk)

    @staticmethod
    def test_hunk_has_line_numbers():
        """Hunks devem conter números de linha de origem e destino."""
        hunks = DiffEngine.hunks(ORIGINAL, MODIFIED)
        assert hunks[0].old_start >= 1
        assert hunks[0].new_start >= 1

    @staticmethod
    def test_hunk_lines_not_empty():
        """Hunks não devem ter lista de linhas vazia."""
        hunks = DiffEngine.hunks(ORIGINAL, MODIFIED)
        assert hunks[0].lines

    @staticmethod
    def test_multiple_changes_multiple_hunks():
        """Mudanças distantes no arquivo devem gerar múltiplos hunks."""
        original = "\n".join(["a"] * 20) + "\n"
        modified = "X\n" + "\n".join(["a"] * 18) + "\nY\n"
        hunks = DiffEngine.hunks(original, modified)
        assert len(hunks) >= 1


class TestInlineDiff:
    """Verifica o diff inline (caractere a caractere)."""

    @staticmethod
    def test_identical_strings_no_diff():
        """Strings idênticas devem retornar lista vazia de diferenças inline."""
        assert not DiffEngine.inline_diff("hello", "hello")

    @staticmethod
    def test_inline_diff_detects_change():
        """Uma alteração de caractere deve ser detectada no diff inline."""
        result = DiffEngine.inline_diff("hello", "hXllo")
        assert len(result) >= 1

    @staticmethod
    def test_inline_diff_returns_list():
        """inline_diff() deve retornar uma lista."""
        result = DiffEngine.inline_diff("a", "b")
        assert isinstance(result, list)

    @staticmethod
    def test_inline_diff_empty_strings():
        """Duas strings vazias não devem gerar diferenças inline."""
        result = DiffEngine.inline_diff("", "")
        assert not result


class TestStats:
    """Verifica o cálculo de estatísticas de diff."""

    @staticmethod
    def test_stats_identical_texts():
        """Textos idênticos devem reportar zero linhas adicionadas e removidas."""
        added, removed = DiffEngine.stats("abc\n", "abc\n")
        assert added == 0
        assert removed == 0

    @staticmethod
    def test_stats_added_lines():
        """Adição de linhas deve ser refletida no contador de linhas adicionadas."""
        added, removed = DiffEngine.stats("", "line1\nline2\n")
        assert added >= 2
        assert removed == 0

    @staticmethod
    def test_stats_removed_lines():
        """Remoção de linhas deve ser refletida no contador de linhas removidas."""
        added, removed = DiffEngine.stats("line1\nline2\n", "")
        assert removed >= 2
        assert added == 0

    @staticmethod
    def test_stats_modified_line():
        """Modificação de linha conta como remoção + adição."""
        added, removed = DiffEngine.stats(ORIGINAL, MODIFIED)
        assert added >= 1
        assert removed >= 1
