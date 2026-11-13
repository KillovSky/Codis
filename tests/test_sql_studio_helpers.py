#!/usr/bin/env python3
"""Testes para helpers de src.ui.sql_studio.

Cobre:
- ``_quote_ident``/``_quote_backtick_ident``: escape de identificador SQL.
- ``_FormRowBuilder``: helper de layout de ``_ConnectionDialog``.
"""

from __future__ import annotations

from src.ui.sql_studio import _FormRowBuilder, _quote_backtick_ident, _quote_ident


class TestQuoteIdent:
    """Testes para _quote_ident() - escape de identificador entre aspas duplas."""

    @staticmethod
    def test_plain_name_unchanged():
        """Um nome de tabela normal não deve ser alterado."""
        assert _quote_ident("users") == "users"

    @staticmethod
    def test_embedded_double_quote_is_doubled():
        """Aspas duplas embutidas devem ser dobradas (regra padrão de escape SQL)."""
        assert _quote_ident('foo"bar') == 'foo""bar'

    @staticmethod
    def test_injection_attempt_is_neutralized():
        """Um nome de tabela malicioso não deve conseguir fechar a aspa e injetar SQL."""
        malicious = 'x" ; DROP TABLE users; --'
        escaped = _quote_ident(malicious)
        assert '""' in escaped
        rebuilt = f'"{escaped}"'
        assert rebuilt.count('"') % 2 == 0

    @staticmethod
    def test_empty_string():
        """String vazia deve retornar string vazia."""
        assert _quote_ident("") == ""


class TestQuoteBacktickIdent:
    """Testes para _quote_backtick_ident() - escape para o dialeto MySQL/MariaDB."""

    @staticmethod
    def test_plain_name_unchanged():
        """Um nome de tabela normal não deve ser alterado."""
        assert _quote_backtick_ident("orders") == "orders"

    @staticmethod
    def test_embedded_backtick_is_doubled():
        """Crases embutidas devem ser dobradas."""
        assert _quote_backtick_ident("a`b") == "a``b"

    @staticmethod
    def test_double_quotes_not_affected():
        """Aspas duplas não devem ser tocadas por este helper (delimitador é a crase)."""
        assert _quote_backtick_ident('a"b') == 'a"b'


class _FakeTheme:
    panel_bg = "#1e1e2e"
    fg_muted = "#a6adc8"
    fg = "#cdd6f4"
    editor_bg = "#181825"
    cursor = "#f5e0dc"
    border = "#313244"


class TestFormRowBuilder:
    """Testes para _FormRowBuilder() - layout de formulário."""

    @staticmethod
    def test_starts_at_row_zero():
        """O contador de linha deve começar em 0."""
        fb = _FormRowBuilder(object(), _FakeTheme())
        assert fb.row == 0

    @staticmethod
    def test_lbl_advances_row_by_one():
        """Cada chamada a lbl() deve ocupar exatamente uma linha."""
        fb = _FormRowBuilder(object(), _FakeTheme())
        fb.lbl("Nome")
        assert fb.row == 1
        fb.lbl("Driver")
        assert fb.row == 2

    @staticmethod
    def test_ent_advances_row_by_one():
        """Cada chamada a ent() deve ocupar exatamente uma linha."""
        fb = _FormRowBuilder(object(), _FakeTheme())
        fb.ent(var=None)
        assert fb.row == 1

    @staticmethod
    def test_take_row_returns_current_then_increments():
        """take_row() deve retornar a linha atual e só depois incrementar."""
        fb = _FormRowBuilder(object(), _FakeTheme())
        fb.lbl("Nome")
        r = fb.take_row()
        assert r == 1
        assert fb.row == 2

    @staticmethod
    def test_mixed_sequence_matches_manual_row_counting():
        """Uma sequência lbl/ent/take_row mista deve preservar a ordem de linhas."""
        fb = _FormRowBuilder(object(), _FakeTheme())
        fb.lbl("Nome")
        fb.ent(var=None)
        combobox_row = fb.take_row()
        dyn_frame_row = fb.take_row()
        assert (combobox_row, dyn_frame_row) == (2, 3)
        assert fb.row == 4

    @staticmethod
    def test_independent_builders_do_not_share_row_counters():
        """Duas instâncias (ex.: _build vs _rebuild_dyn_fields) não devem interferir entre si."""
        fb_static = _FormRowBuilder(object(), _FakeTheme())
        fb_dynamic = _FormRowBuilder(object(), _FakeTheme())
        fb_static.lbl("Nome")
        fb_static.lbl("Driver")
        assert fb_static.row == 2
        assert fb_dynamic.row == 0
