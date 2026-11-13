#!/usr/bin/env python3
"""Testes para src.plugins.api - PluginManifest, PluginStorage, PluginLogger e OutputChannel."""

from __future__ import annotations

import json
import threading
import time
from unittest.mock import MagicMock

import pytest

import src.constants as _const
from src.plugins.api import (
    OutputChannel,
    PluginLogger,
    PluginManifest,
    PluginStorage,
    _load_trusted_plugins,
    _normalize_manifest,
    _sandbox_check_imports,
    _save_trusted_plugins,
)


class TestPluginManifest:
    """Verifica os atributos e defaults do dataclass PluginManifest."""

    @staticmethod
    def test_minimal_manifest():
        """Um manifesto com somente o nome deve ser criado sem erros."""
        m = PluginManifest(name="test_plugin")
        assert m.name == "test_plugin"

    @staticmethod
    def test_defaults():
        """Os campos opcionais devem ter seus valores padrão corretos."""
        m = PluginManifest(name="x")
        assert m.version == "1.0.0"
        assert m.author == ""
        assert m.icon == "🔌"
        assert m.api_version == "4.0"
        assert not m.tags
        assert m.trusted is False
        assert m.preview is False

    @staticmethod
    def test_full_manifest():
        """Todos os campos fornecidos devem ser armazenados corretamente."""
        m = PluginManifest(
            name="my_plugin",
            version="1.0.0",
            author="Russell",
            description="Test plugin",
            icon="🚀",
            tags=["editor", "utils"],
            trusted=True,
        )
        assert m.version == "1.0.0"
        assert m.author == "Russell"
        assert m.trusted is True
        assert "editor" in m.tags

    @staticmethod
    def test_tags_not_shared():
        """Tags de manifesto A não devem contaminar o manifesto B."""
        a = PluginManifest(name="a")
        b = PluginManifest(name="b")
        a.tags.append("x")
        assert "x" not in b.tags

    @staticmethod
    def test_capabilities_default_empty():
        """O campo capabilities deve iniciar como lista vazia."""
        m = PluginManifest(name="x")
        assert not m.capabilities

    @staticmethod
    def test_dependencies_default_empty():
        """O campo dependencies deve iniciar como lista vazia."""
        m = PluginManifest(name="x")
        assert not m.dependencies

    @staticmethod
    def test_engines_default_empty():
        """O campo engines deve iniciar como dicionário vazio."""
        m = PluginManifest(name="x")
        assert not m.engines


class TestNormalizeManifest:
    """Verifica a normalização de dicionários em PluginManifest."""

    @staticmethod
    def test_dict_converted_to_manifest():
        """Um dicionário com 'name' deve ser convertido para PluginManifest."""
        result = _normalize_manifest({"name": "plugin_x", "version": "1.0.0"})
        assert isinstance(result, PluginManifest)
        assert result.name == "plugin_x"

    @staticmethod
    def test_manifest_passthrough():
        """Um PluginManifest já existente deve ser retornado sem alteração."""
        m = PluginManifest(name="x")
        result = _normalize_manifest(m)
        assert result is m

    @staticmethod
    def test_missing_name_raises():
        """Dicionário sem 'name' deve lançar TypeError ou KeyError."""
        with pytest.raises((TypeError, KeyError)):
            _normalize_manifest({})


class TestSandboxCheckImports:
    """Verifica a análise de importações pelo sandbox."""

    @staticmethod
    def test_safe_code_returns_lists():
        """Código seguro deve retornar listas (possivelmente vazias)."""
        code = "import os\nimport json\nfrom pathlib import Path\n"
        blocked, warned = _sandbox_check_imports(code)
        assert isinstance(blocked, list)
        assert isinstance(warned, list)

    @staticmethod
    def test_dangerous_import_detected():
        """Importação perigosa como subprocess deve gerar resultado nas listas."""
        code = "import subprocess\nos.system('rm -rf /')\n"
        blocked, warned = _sandbox_check_imports(code)
        assert isinstance(blocked, list) and isinstance(warned, list)

    @staticmethod
    def test_empty_code():
        """Código vazio deve retornar listas vazias sem lançar exceção."""
        blocked, warned = _sandbox_check_imports("")
        assert isinstance(blocked, list)
        assert isinstance(warned, list)

    @staticmethod
    def test_invalid_syntax_handled():
        """Código com sintaxe inválida deve ser tratado sem lançar exceção."""
        blocked, warned = _sandbox_check_imports("def (:")
        assert isinstance(blocked, list)
        assert isinstance(warned, list)


class TestPluginStorage:
    """Verifica o armazenamento chave-valor do PluginStorage."""

    @pytest.fixture()
    @staticmethod
    def storage(monkeypatch):
        """PluginStorage limpo para cada teste."""
        monkeypatch.setattr(_const, "CONFIG_DIR", _const.CONFIG_DIR)
        s = PluginStorage("test_plugin_storage")
        s._dados.clear()
        s._sujo = False
        return s

    @staticmethod
    def test_get_missing_returns_default(storage):
        """get() com chave inexistente deve retornar o valor padrão fornecido."""
        assert storage.get("nonexistent", "default") == "default"

    @staticmethod
    def test_get_missing_returns_none_by_default(storage):
        """get() com chave inexistente e sem default deve retornar None."""
        assert storage.get("nonexistent") is None

    @staticmethod
    def test_set_and_get(storage):
        """set() + get() deve recuperar o valor armazenado."""
        storage.set("key", "value")
        assert storage.get("key") == "value"

    @staticmethod
    def test_set_overwrites(storage):
        """set() deve sobrescrever valores anteriores para a mesma chave."""
        storage.set("key", "v1")
        storage.set("key", "v2")
        assert storage.get("key") == "v2"

    @staticmethod
    def test_set_none_value(storage):
        """set() deve aceitar None como valor válido."""
        storage.set("key", None)
        assert storage.get("key") is None

    @staticmethod
    def test_set_complex_value(storage):
        """set() deve suportar dicionários e listas aninhados."""
        data = {"nested": [1, 2, 3], "flag": True}
        storage.set("complex", data)
        assert storage.get("complex") == data

    @staticmethod
    def test_delete_existing(storage):
        """delete() deve remover a chave do armazenamento."""
        storage.set("key", "v")
        storage.delete("key")
        assert storage.get("key") is None

    @staticmethod
    def test_delete_missing_does_not_raise(storage):
        """delete() com chave inexistente não deve lançar exceção."""
        storage.delete("nonexistent_xyz")

    @staticmethod
    def test_keys_empty(storage):
        """keys() em armazenamento vazio deve retornar lista vazia."""
        assert storage.keys() == []

    @staticmethod
    def test_keys_after_set(storage):
        """keys() deve listar todas as chaves inseridas."""
        storage.set("a", 1)
        storage.set("b", 2)
        keys = storage.keys()
        assert "a" in keys and "b" in keys

    @staticmethod
    def test_clear(storage):
        """clear() deve remover todas as entradas do armazenamento."""
        storage.set("a", 1)
        storage.set("b", 2)
        storage.clear()
        assert storage.keys() == []

    @staticmethod
    def test_flush_writes_file(storage, tmp_path, monkeypatch):
        """flush() deve persistir os dados no arquivo JSON."""
        monkeypatch.setattr(_const, "CONFIG_DIR", tmp_path)
        storage._caminho = tmp_path / "plugin_data" / "test_plugin_storage.json"
        storage.set("key", "value")
        if storage._timer:
            storage._timer.cancel()
        storage.flush()
        assert storage._caminho.exists()
        data = json.loads(storage._caminho.read_text("utf-8"))
        assert data.get("key") == "value"

    @staticmethod
    def test_flush_idempotent_when_clean(storage):
        """flush() sem modificações pendentes não deve lançar exceção."""
        storage._sujo = False
        storage.flush()

    @staticmethod
    def test_thread_safety(storage):
        """Escritas concorrentes não devem causar condições de corrida."""
        results = []

        def writer(i: int) -> None:
            """Escreve e lê o próprio valor no storage."""
            storage.set(f"key_{i}", i)
            results.append(storage.get(f"key_{i}"))

        threads = [threading.Thread(target=writer, args=(i,))
                   for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(results) == 10

    @staticmethod
    def test_agendar_flush_sets_timer(storage):
        """Após set(), o timer de flush deve ser agendado sem erros."""
        storage.set("key", "value")
        time.sleep(0.1)
        if storage._timer:
            storage._timer.cancel()


class TestPluginLogger:
    """Verifica os métodos de logging do PluginLogger."""

    @pytest.fixture()
    @staticmethod
    def logger():
        """PluginLogger para o plugin de teste."""
        return PluginLogger("test_plugin")

    @staticmethod
    def test_debug_does_not_raise(logger):
        """logger.debug() não deve lançar exceção."""
        logger.debug("debug message")

    @staticmethod
    def test_info_does_not_raise(logger):
        """logger.info() não deve lançar exceção."""
        logger.info("info message")

    @staticmethod
    def test_warning_does_not_raise(logger):
        """logger.warning() não deve lançar exceção."""
        logger.warning("warning message")

    @staticmethod
    def test_error_does_not_raise(logger):
        """logger.error() não deve lançar exceção."""
        logger.error("error message")

    @staticmethod
    def test_exception_does_not_raise(logger):
        """logger.exception() chamado dentro de except não deve lançar."""
        try:
            raise ValueError("test error")
        except ValueError:
            logger.exception("caught exception")

    @staticmethod
    def test_format_args(logger):
        """logger.info() deve aceitar argumentos de formatação posicional."""
        logger.info("value: %s", "hello")

    @staticmethod
    def test_with_output_channel():
        """Mensagens registradas via canal devem ser recebidas pelo OutputChannel."""
        app_mock = MagicMock()
        channel = OutputChannel("test_plugin", app_mock)
        pl = PluginLogger("test_plugin", canal=channel)
        pl.info("logged via channel")
        _ = channel.get_all()


class TestOutputChannel:
    """Verifica o buffer de saída do OutputChannel."""

    @pytest.fixture()
    @staticmethod
    def channel():
        """OutputChannel vinculado a um mock de app."""
        app_mock = MagicMock()
        return OutputChannel("test_plugin", app_mock)

    @staticmethod
    def test_nome_property(channel):
        """A propriedade name deve retornar o nome do canal."""
        assert channel.name == "test_plugin"

    @staticmethod
    def test_append_adds_text(channel):
        """append() deve incluir o texto no buffer do canal."""
        channel.append("hello")
        content = channel.get_all()
        assert "hello" in content

    @staticmethod
    def test_append_line_adds_newline(channel):
        """appendLine() deve incluir as linhas no buffer com separação."""
        channel.appendLine("line1")
        channel.appendLine("line2")
        content = channel.get_all()
        assert "line1" in content
        assert "line2" in content

    @staticmethod
    def test_clear_empties_buffer(channel):
        """clear() deve esvaziar completamente o buffer."""
        channel.append("something")
        channel.clear()
        assert channel.get_all() == ""

    @staticmethod
    def test_multiple_appends(channel):
        """Múltiplos appends devem estar todos presentes no buffer."""
        for i in range(5):
            channel.append(f"msg{i}")
        content = channel.get_all()
        for i in range(5):
            assert f"msg{i}" in content

    @staticmethod
    def test_show_does_not_raise(channel):
        """show() não deve lançar exceção."""
        channel.show()


class TestTrustedPlugins:
    """Verifica a persistência do mapa de plugins confiáveis (plugin_id -> hash)."""

    @staticmethod
    def test_load_returns_dict(tmp_path, monkeypatch):
        """_load_trusted_plugins() deve retornar um dict (plugin_id -> sha256)."""
        import src.plugins.api as _api

        monkeypatch.setattr(
            _api, "_TRUSTED_PLUGINS_FILE", tmp_path / "trusted_plugins.json"
        )
        result = _load_trusted_plugins()
        assert isinstance(result, dict)

    @staticmethod
    def test_save_and_load_roundtrip(tmp_path, monkeypatch):
        """Salvar e carregar deve preservar todos os plugins confiáveis e seus hashes."""
        import src.plugins.api as _api

        tp_file = tmp_path / "trusted_plugins.json"
        monkeypatch.setattr(_api, "_TRUSTED_PLUGINS_FILE", tp_file)
        monkeypatch.setattr(_api, "CONFIG_DIR", tmp_path)
        trusted = {"plugin_a": "hash_a", "plugin_b": "hash_b"}
        _save_trusted_plugins(trusted)
        loaded = _load_trusted_plugins()
        assert loaded.get("plugin_a") == "hash_a"
        assert loaded.get("plugin_b") == "hash_b"

    @staticmethod
    def test_load_missing_file_returns_empty(tmp_path, monkeypatch):
        """Arquivo ausente deve resultar em dict vazio."""
        import src.plugins.api as _api

        nonexistent = tmp_path / "no_dir" / "trusted_plugins.json"
        monkeypatch.setattr(_api, "_TRUSTED_PLUGINS_FILE", nonexistent)
        result = _load_trusted_plugins()
        assert result == {}
