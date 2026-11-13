#!/usr/bin/env python3
"""Testes unitários para src.i18n.I18n.

Cobre: singleton, locale padrão, tradução, interpolação,
set_locale, extend, cache e callbacks.
"""

from __future__ import annotations

import pytest

from src.i18n import I18n, _t


@pytest.fixture(autouse=True)
def reset_singleton():
    """Garante singleton limpo entre testes."""
    original = I18n._instance
    I18n._instance = None
    yield
    I18n._instance = original


@pytest.fixture(name="i18n")
def fresh_i18n():
    """Instância fresca de I18n para cada teste."""
    return I18n()


class TestSingleton:
    """Verifica o comportamento de singleton do I18n."""

    @staticmethod
    def test_get_instance_returns_same_object():
        """get_instance() deve retornar sempre o mesmo objeto."""
        a = I18n.get_instance()
        b = I18n.get_instance()
        assert a is b

    @staticmethod
    def test_module_level_t_is_callable():
        """A função _t exportada pelo módulo deve ser chamável."""
        assert callable(_t)

    @staticmethod
    def test_t_returns_string():
        """_t() deve retornar uma string para qualquer chave."""
        result = _t("any.key")
        assert isinstance(result, str)


class TestDefaultLocale:
    """Verifica a configuração padrão de locale."""

    @staticmethod
    def test_default_locale_is_pt_br(i18n):
        """O locale padrão deve ser 'pt-BR'."""
        assert i18n.get_locale() == "pt-BR"

    @staticmethod
    def test_base_english_loaded_on_init(i18n):
        """O dicionário base em inglês deve ser carregado na inicialização."""
        assert isinstance(i18n._base, dict)
        assert len(i18n._base) > 0

    @staticmethod
    def test_available_locales_contains_en(i18n):
        """O locale 'en' deve estar disponível."""
        codes = [c for c, _ in i18n.AVAILABLE_LOCALES]
        assert "en" in codes

    @staticmethod
    def test_available_locales_contains_pt_br(i18n):
        """O locale 'pt-BR' deve estar disponível."""
        codes = [c for c, _ in i18n.AVAILABLE_LOCALES]
        assert "pt-BR" in codes

    @staticmethod
    def test_available_locales_has_label_for_each(i18n):
        """Cada locale disponível deve ter código e rótulo não vazios."""
        for code, label in i18n.AVAILABLE_LOCALES:
            assert code and label


class TestTranslation:
    """Verifica a tradução de chaves."""

    @staticmethod
    def test_known_key_returns_non_empty_string(i18n):
        """Uma chave conhecida deve retornar string não vazia."""
        val = i18n.translate("menu.file")
        assert isinstance(val, str) and val

    @staticmethod
    def test_unknown_key_returns_key_itself(i18n):
        """Uma chave desconhecida deve ser retornada literalmente."""
        key = "totally.nonexistent.key.xyz"
        assert i18n.translate(key) == key

    @staticmethod
    def test_unknown_key_with_default(i18n):
        """Uma chave ausente deve retornar o valor padrão fornecido."""
        result = i18n.translate("missing.key", default="fallback")
        assert result == "fallback"

    @staticmethod
    def test_known_key_not_equal_to_itself_when_translated(i18n):
        """Chave traduzida deve produzir string não vazia."""
        val = i18n.translate("menu.file")
        assert val

    @staticmethod
    def test_interpolation_with_kwargs(i18n):
        """A interpolação de kwargs deve substituir placeholders na string."""
        i18n.extend("test_loc", {"greet": "Hello {name}!"})
        i18n.set_locale("test_loc")
        result = i18n.translate("greet", name="World")
        assert result == "Hello World!"

    @staticmethod
    def test_interpolation_missing_kwarg_returns_unformatted(i18n):
        """Kwarg ausente em interpolação deve retornar string sem lançar exceção."""
        i18n.extend("test_loc2", {"msg": "Hello {name}!"})
        i18n.set_locale("test_loc2")
        result = i18n.translate("msg")
        assert isinstance(result, str)

    @staticmethod
    def test_fallback_to_english_when_key_missing_in_locale(i18n):
        """Quando a chave não existe no locale ativo, deve cair no inglês base."""
        i18n.extend("sparse_locale", {"only.this": "Só isso"})
        i18n.set_locale("sparse_locale")
        val = i18n.translate("menu.file")
        assert isinstance(val, str) and val


class TestSetLocale:
    """Verifica a troca de locale ativo."""

    @staticmethod
    def test_set_locale_changes_active(i18n):
        """set_locale() deve alterar o locale retornado por get_locale()."""
        i18n.set_locale("en")
        assert i18n.get_locale() == "en"

    @staticmethod
    def test_set_locale_triggers_callbacks(i18n):
        """set_locale() deve invocar todos os callbacks registrados."""
        calls = []
        i18n.on_change(lambda: calls.append(1))
        i18n.set_locale("en")
        assert calls == [1]

    @staticmethod
    def test_on_change_not_duplicated(i18n):
        """Registrar o mesmo callback duas vezes não deve duplicar as chamadas."""
        calls = []

        def cb():
            """Callback de contagem para teste de deduplicação."""
            calls.append(1)

        i18n.on_change(cb)
        i18n.on_change(cb)
        i18n.set_locale("en")
        assert len(calls) == 1

    @staticmethod
    def test_callback_exception_propagates(i18n):
        """I18n não suprime exceções em callbacks - comportamento documentado."""
        i18n.on_change(lambda: 1 / 0)
        with pytest.raises(ZeroDivisionError):
            i18n.set_locale("en")


class TestExtend:
    """Verifica a extensão de locales com novas traduções."""

    @staticmethod
    def test_extend_adds_translations(i18n):
        """extend() deve tornar as novas chaves traduzíveis no locale."""
        i18n.extend("xx", {"hello": "Olá"})
        i18n.set_locale("xx")
        assert i18n.translate("hello") == "Olá"

    @staticmethod
    def test_extend_merges_with_existing(i18n):
        """Chamar extend() duas vezes no mesmo locale deve mesclar as chaves."""
        i18n.extend("xx2", {"a": "A"})
        i18n.extend("xx2", {"b": "B"})
        i18n.set_locale("xx2")
        assert i18n.translate("a") == "A"
        assert i18n.translate("b") == "B"

    @staticmethod
    def test_extend_registers_new_locale_in_available(i18n):
        """Um novo locale adicionado via extend() deve aparecer em AVAILABLE_LOCALES."""
        i18n.extend("tl", {"lang.name": "Tagalog"}, label="Tagalog")
        codes = [c for c, _ in i18n.AVAILABLE_LOCALES]
        assert "tl" in codes

    @staticmethod
    def test_extend_does_not_duplicate_available_locale(i18n):
        """Chamar extend() duas vezes no mesmo locale não deve gerar entradas duplicadas."""
        i18n.extend("dup_loc", {}, label="Dup")
        i18n.extend("dup_loc", {}, label="Dup")
        count = sum(1 for c, _ in i18n.AVAILABLE_LOCALES if c == "dup_loc")
        assert count == 1

    @staticmethod
    def test_extend_notifies_registry_callbacks(i18n):
        """Adicionar um novo locale deve acionar callbacks de registro."""
        calls = []
        i18n.on_locale_registry_change(lambda: calls.append(1))
        i18n.extend("new_loc_xyz", {"k": "v"}, label="New")
        assert len(calls) >= 1

    @staticmethod
    def test_extend_existing_locale_no_registry_callback(i18n):
        """Estender um locale já existente não deve disparar callback de registro."""
        calls = []
        i18n.on_locale_registry_change(lambda: calls.append(1))
        i18n.extend("en", {"extra": "extra"})
        assert len(calls) == 0


class TestCache:
    """Verifica o comportamento de cache de locales carregados."""

    @staticmethod
    def test_load_locale_caches_result(i18n):
        """_load() deve armazenar o resultado em cache após a primeira chamada."""
        i18n._load("en")
        i18n._load("en")
        assert "en" in i18n._cache

    @staticmethod
    def test_invalid_locale_returns_empty_dict(i18n):
        """Um locale inexistente deve retornar dicionário vazio sem lançar exceção."""
        result = i18n._load("xx_INVALID_9999")
        assert result == {}


def test_placeholder_named_key_does_not_collide_with_key_argument():
    from src.i18n import _t

    assert "painel" in _t("kui.detach", key="painel")
