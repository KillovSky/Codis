#!/usr/bin/env python3
"""Testes unitários para src.config.ConfigManager.

Cobre: get/set, callbacks on_change, persistência (save/load),
modo degradado, arquivos recentes e coerção de tipos.
"""

from __future__ import annotations

import json

import src.constants as _const
from src.config import ConfigManager


class TestGetSet:
    """Testa as operações básicas de leitura e escrita de configuração."""

    @staticmethod
    def test_get_returns_default_when_key_missing(cfg):
        """Deve retornar o valor padrão fornecido quando a chave não existe."""
        assert cfg.get("nonexistent_key", "fallback") == "fallback"

    @staticmethod
    def test_get_returns_value_from_default_config(cfg):
        """Deve retornar um inteiro positivo para 'font_size' do DEFAULT_CONFIG."""
        val = cfg.get("font_size")
        assert isinstance(val, int) and val > 0

    @staticmethod
    def test_set_changes_value(cfg):
        """Deve atualizar o valor armazenado após chamar set()."""
        cfg.set("font_size", 18)
        assert cfg.get("font_size") == 18

    @staticmethod
    def test_set_new_key(cfg):
        """Deve aceitar e armazenar chaves customizadas."""
        cfg.set("my_custom_key", 42)
        assert cfg.get("my_custom_key") == 42

    @staticmethod
    def test_set_none_value(cfg):
        """Deve armazenar None como valor válido."""
        cfg.set("some_key", None)
        assert cfg.get("some_key") is None


class TestOnChange:
    """Testa os callbacks de notificação de mudança de configuração."""

    @staticmethod
    def test_callback_called_when_value_changes(cfg):
        """O callback deve ser chamado com o novo valor quando a configuração muda."""
        calls = []
        cfg.on_change("font_size", calls.append)
        cfg.set("font_size", 20)
        assert calls == [20]

    @staticmethod
    def test_callback_not_called_when_value_unchanged(cfg):
        """O callback não deve ser invocado quando o valor permanece o mesmo."""
        cfg.set("font_size", 14)
        calls = []
        cfg.on_change("font_size", calls.append)
        cfg.set("font_size", 14)
        assert not calls

    @staticmethod
    def test_multiple_callbacks_same_key(cfg):
        """Múltiplos callbacks na mesma chave devem ser todos invocados."""
        results = []
        cfg.on_change("theme", lambda v: results.append(("a", v)))
        cfg.on_change("theme", lambda v: results.append(("b", v)))
        cfg.set("theme", "solarized")
        assert ("a", "solarized") in results
        assert ("b", "solarized") in results

    @staticmethod
    def test_callback_exception_does_not_propagate(cfg):
        """Callback que lança exceção não deve interromper o chamador."""

        def bad_cb(v):
            """Callback que propositalmente lança exceção."""
            raise RuntimeError("simulated error")

        cfg.on_change("font_size", bad_cb)
        cfg.set("font_size", 16)
        assert cfg.get("font_size") == 16


class TestPersistence:
    """Testa a persistência de configuração em disco."""

    @staticmethod
    def test_save_creates_file(cfg):
        """save() deve criar o arquivo de configuração e retornar True."""
        cfg.set("font_size", 22)
        ok = cfg.save()
        assert ok is True
        assert _const.CONFIG_FILE.exists()

    @staticmethod
    def test_save_and_load_roundtrip(cfg):
        """Valores salvos devem ser restaurados em uma nova instância."""
        cfg.set("font_size", 22)
        cfg.set("theme", "oceanic")
        cfg.save()
        cfg2 = ConfigManager()
        assert cfg2.get("font_size") == 22
        assert cfg2.get("theme") == "oceanic"

    @staticmethod
    def test_load_ignores_corrupt_json():
        """JSON corrompido não deve impedir a inicialização do ConfigManager."""
        _const.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _const.CONFIG_FILE.write_text("{ not valid json", encoding="utf-8")
        loaded = ConfigManager()
        assert loaded.get("font_size") is not None

    @staticmethod
    def test_load_ignores_non_object_json():
        """JSON com raiz não-objeto (ex: lista) deve ser ignorado."""
        _const.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _const.CONFIG_FILE.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        loaded = ConfigManager()
        assert loaded.get("font_size") is not None

    @staticmethod
    def test_save_atomic_no_tmp_leftover(cfg):
        """O arquivo temporário .json.tmp não deve sobrar após save bem-sucedido."""
        cfg.set("theme", "test_atomic")
        cfg.save()
        assert not _const.CONFIG_FILE.with_suffix(".json.tmp").exists()


class TestRecentFiles:
    """Testa o gerenciamento de arquivos e pastas recentes."""

    @staticmethod
    def test_add_recent_file_inserts_at_top(cfg, tmp_path):
        """O arquivo adicionado mais recentemente deve aparecer no topo da lista."""
        f1 = tmp_path / "a.py"
        f2 = tmp_path / "b.py"
        f1.touch()
        f2.touch()
        cfg.add_recent_file(f1)
        cfg.add_recent_file(f2)
        recent = cfg._data["recent_files"]
        assert recent[0] == str(f2)
        assert recent[1] == str(f1)

    @staticmethod
    def test_add_recent_file_deduplicates(cfg, tmp_path):
        """Adicionar o mesmo arquivo duas vezes não deve gerar duplicatas."""
        f = tmp_path / "x.py"
        f.touch()
        cfg.add_recent_file(f)
        cfg.add_recent_file(f)
        recent = cfg._data["recent_files"]
        assert recent.count(str(f)) == 1 and recent[0] == str(f)

    @staticmethod
    def test_get_recent_files_filters_missing(cfg, tmp_path):
        """get_recent_files() deve excluir arquivos que não existem mais no disco."""
        existing = tmp_path / "exists.py"
        existing.touch()
        cfg._data["recent_files"] = [str(existing), "/path/does/not/exist.py"]
        result = cfg.get_recent_files()
        assert len(result) == 1 and result[0] == existing

    @staticmethod
    def test_add_recent_folder(cfg, tmp_path):
        """add_recent_folder() deve registrar a pasta na lista de pastas recentes."""
        folder = tmp_path / "my_project"
        folder.mkdir()
        cfg.add_recent_folder(folder)
        assert str(folder) in cfg._data["recent_folders"]

class TestTypeCoercion:
    """Testa a coerção automática de tipos durante o carregamento da configuração."""

    @staticmethod
    def test_font_size_string_coerced_to_int():
        """'font_size' armazenado como string deve ser convertido para inteiro."""
        _const.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _const.CONFIG_FILE.write_text(json.dumps(
            {"font_size": "16"}), encoding="utf-8")
        loaded = ConfigManager()
        assert (
            isinstance(loaded.get("font_size"),
                       int) and loaded.get("font_size") == 16
        )

    @staticmethod
    def test_bool_coercion():
        """'word_wrap' armazenado como inteiro deve ser convertido para booleano."""
        _const.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _const.CONFIG_FILE.write_text(
            json.dumps({"word_wrap": 1}), encoding="utf-8")
        loaded = ConfigManager()
        assert isinstance(loaded.get("word_wrap"), bool)

    @staticmethod
    def test_list_coercion():
        """'recent_files' com valor inválido deve resultar em uma lista vazia."""
        _const.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _const.CONFIG_FILE.write_text(
            json.dumps({"recent_files": "not_a_list"}), encoding="utf-8"
        )
        loaded = ConfigManager()
        assert isinstance(loaded.get_recent_files(), list)


class TestBoolCoercionFromHandEditedJson:
    """Regressão: bool("false") era True e ligava opções desligadas."""

    @staticmethod
    def test_string_false_stays_false(cfg):
        cfg._data["autosave"] = "false"
        cfg._data["minimap"] = "0"
        cfg._data["word_wrap"] = "Off"
        cfg._coerce_types()
        assert cfg.get("autosave") is False
        assert cfg.get("minimap") is False
        assert cfg.get("word_wrap") is False

    @staticmethod
    def test_string_true_and_garbage(cfg):
        cfg._data["autosave"] = "yes"
        cfg._data["minimap"] = "talvez"
        cfg._coerce_types()
        assert cfg.get("autosave") is True
        assert cfg.get("minimap") is True
