#!/usr/bin/env python3
"""Testes para src.theme.base e src.theme.manager."""

from __future__ import annotations

import pytest

from src.theme.base import THEME_ORDER, THEMES, _define_themes
from src.theme.manager import ThemeManager, _patch_all_themes, _patch_theme


@pytest.fixture(autouse=True)
def ensure_themes():
    """Garante que os temas estão definidos antes de cada teste."""
    if not THEMES:
        _define_themes()
    _patch_all_themes()


class TestThemeDataclass:
    """Verifica os campos obrigatórios e tipos dos temas."""

    @staticmethod
    def test_theme_has_required_fields():
        """O primeiro tema deve conter name, dark, bg e fg não vazios."""
        t = THEMES[THEME_ORDER[0]]
        assert t.name
        assert isinstance(t.dark, bool)
        assert t.bg
        assert t.fg

    @staticmethod
    def test_theme_name_matches_key():
        """O campo name de cada tema deve ser igual à sua chave em THEMES."""
        for key, theme in THEMES.items():
            assert theme.name == key

    @staticmethod
    def test_all_themes_have_editor_bg():
        """Todos os temas devem ter o atributo editor_bg preenchido."""
        for t in THEMES.values():
            assert hasattr(t, "editor_bg") and t.editor_bg

    @staticmethod
    def test_all_themes_have_syntax_colors():
        """Todos os temas devem conter os atributos de cor de sintaxe obrigatórios."""
        for t in THEMES.values():
            assert hasattr(t, "t_keyword")
            assert hasattr(t, "t_string")
            assert hasattr(t, "t_comment")
            assert hasattr(t, "t_number")

    @staticmethod
    def test_dark_themes_have_dark_flag():
        """Temas com 'dark' ou 'night' no nome devem ter dark=True."""
        dark_themes = [
            t for t in THEMES.values() if "dark" in t.name or "night" in t.name
        ]
        for t in dark_themes:
            assert t.dark is True

    @staticmethod
    def test_theme_colors_are_hex_strings():
        """As cores principais do primeiro tema devem estar em formato hexadecimal."""
        t = list(THEMES.values())[0]
        for attr in ("bg", "fg", "editor_bg", "t_keyword", "t_string"):
            val = getattr(t, attr, None)
            if val:
                assert val.startswith("#"), f"{attr}={val!r} não é hex"
                assert len(val) in (
                    4, 7), f"{attr}={val!r} tem tamanho inválido"


class TestThemeOrder:
    """Verifica a lista de ordenação dos temas."""

    @staticmethod
    def test_theme_order_not_empty():
        """THEME_ORDER deve conter pelo menos um tema."""
        assert len(THEME_ORDER) > 0

    @staticmethod
    def test_all_order_entries_in_themes():
        """Todos os nomes em THEME_ORDER devem existir em THEMES."""
        for name in THEME_ORDER:
            assert name in THEMES, f"'{name}' em THEME_ORDER mas não em THEMES"

    @staticmethod
    def test_minimum_theme_count():
        """O aplicativo deve ter ao menos 5 temas cadastrados."""
        assert len(THEMES) >= 5


class TestPatchTheme:
    """Verifica a função _patch_theme que preenche atributos opcionais."""

    @staticmethod
    def test_patch_fills_missing_optional_attrs():
        """_patch_theme() deve garantir que fg_muted existe após a chamada."""
        if not THEMES:
            _define_themes()
        for t in list(THEMES.values())[:3]:
            _patch_theme(t)
            assert hasattr(t, "fg_muted")

    @staticmethod
    def test_patch_does_not_overwrite_existing():
        """_patch_theme() não deve sobrescrever o valor de bg já definido."""
        t = list(THEMES.values())[0]
        original_bg = t.bg
        _patch_theme(t)
        assert t.bg == original_bg


class TestThemeManager:
    """Verifica a classe ThemeManager."""

    @pytest.fixture()
    @staticmethod
    def tmgr():
        """ThemeManager inicializado com o primeiro tema."""
        return ThemeManager(THEME_ORDER[0])

    @staticmethod
    def test_initial_theme_set(tmgr):
        """O tema inicial deve ser o fornecido ao construtor."""
        assert tmgr.theme is not None
        assert tmgr.theme.name == THEME_ORDER[0]

    @staticmethod
    def test_set_valid_theme(tmgr):
        """set() com tema válido deve alterar o tema ativo."""
        second = THEME_ORDER[1] if len(THEME_ORDER) > 1 else THEME_ORDER[0]
        tmgr.set(second)
        assert tmgr.theme.name == second

    @staticmethod
    def test_set_invalid_theme_keeps_current(tmgr):
        """set() com tema inexistente não deve alterar o tema atual."""
        original = tmgr.theme.name
        tmgr.set("nonexistent_theme_xyz")
        assert tmgr.theme.name == original

    @staticmethod
    def test_on_change_callback_called(tmgr):
        """on_change() deve registrar callback invocado na troca de tema."""
        calls = []
        tmgr.on_change(lambda t: calls.append(t.name))
        second = THEME_ORDER[1] if len(THEME_ORDER) > 1 else THEME_ORDER[0]
        tmgr.set(second)
        if second != THEME_ORDER[0]:
            assert len(calls) == 1

    @staticmethod
    def test_cycle_advances_theme(tmgr):
        """cycle() deve avançar para um tema válido em THEME_ORDER."""
        tmgr.cycle()
        assert tmgr.theme.name in THEME_ORDER

    @staticmethod
    def test_cycle_wraps_around(tmgr):
        """cycle() chamado mais vezes que o número de temas deve continuar válido."""
        for _ in range(len(THEME_ORDER) + 1):
            tmgr.cycle()
        assert tmgr.theme.name in THEME_ORDER

    @staticmethod
    def test_token_map_returns_dict(tmgr):
        """token_map() deve retornar um dicionário não vazio."""
        tmap = tmgr.token_map()
        assert isinstance(tmap, dict)
        assert "keyword" in tmap or len(tmap) > 0

    @staticmethod
    def test_fallback_to_first_theme_when_invalid_initial():
        """Tema inicial inválido deve cair no fallback para um tema existente."""
        tmgr = ThemeManager("this_theme_does_not_exist")
        assert tmgr.theme is not None
        assert tmgr.theme.name in THEMES

    @staticmethod
    def test_multiple_callbacks(tmgr):
        """Múltiplos callbacks registrados devem ser todos invocados na troca."""
        results = []
        tmgr.on_change(lambda t: results.append("a"))
        tmgr.on_change(lambda t: results.append("b"))
        second = THEME_ORDER[1] if len(THEME_ORDER) > 1 else THEME_ORDER[0]
        tmgr.set(second)
        if second != THEME_ORDER[0]:
            assert "a" in results and "b" in results

    @staticmethod
    def test_theme_is_accessible_after_set(tmgr):
        """Após set(), o atributo theme deve ser acessível e válido."""
        second = THEME_ORDER[1] if len(THEME_ORDER) > 1 else THEME_ORDER[0]
        tmgr.set(second)
        assert tmgr.theme.bg is not None
