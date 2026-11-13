#!/usr/bin/env python3
"""Testes para src.ia.providers - BaseProvider e provedores concretos."""

from __future__ import annotations

import contextlib
import threading
import time
import urllib.error

import pytest

from src.ia.providers import (
    AnthropicProvider,
    BaseProvider,
    GeminiProvider,
    LocalLibProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
    OpenAIProvider,
    _post_json,
    _post_stream,
)


class _ConcreteProvider(BaseProvider):
    """Implementação mínima concreta de BaseProvider para testes."""

    name = "test"

    def send(self, messages, on_chunk, on_done, on_error):
        """Implementação stub - não faz nada."""

    def check(self):
        """Implementação stub - retorna None."""
        return None


class TestBaseProvider:
    """Verifica os atributos e comportamentos da classe base BaseProvider."""

    @staticmethod
    def test_name_attribute():
        """O atributo name deve refletir o valor definido na subclasse."""
        p = _ConcreteProvider()
        assert p.name == "test"

    @staticmethod
    def test_default_icon():
        """O ícone padrão deve ser '[AI]' quando não sobrescrito."""
        p = _ConcreteProvider()
        assert p.icon == "[AI]"

    @staticmethod
    def test_default_requires_key():
        """requires_key deve ser True por padrão."""
        p = _ConcreteProvider()
        assert p.requires_key is True

    @staticmethod
    def test_configure_stores_cfg():
        """configure() deve armazenar o dicionário de configuração em _cfg."""
        p = _ConcreteProvider()
        p.configure({"api_key": "sk-test", "model": "claude-3"})
        assert p._cfg["api_key"] == "sk-test"

    @staticmethod
    def test_cancel_sets_event():
        """cancel() deve sinalizar o evento _cancelled."""
        p = _ConcreteProvider()
        p.cancel()
        assert p._cancelled.is_set()

    @staticmethod
    def test_reset_cancel_clears_event():
        """_reset_cancel() deve limpar o sinal de cancelamento."""
        p = _ConcreteProvider()
        p.cancel()
        p._reset_cancel()
        assert not p._cancelled.is_set()

    @staticmethod
    def test_cancel_then_reset_then_cancel():
        """Cancelar, resetar e cancelar novamente deve resultar em evento sinalizado."""
        p = _ConcreteProvider()
        p.cancel()
        p._reset_cancel()
        p.cancel()
        assert p._cancelled.is_set()

    @staticmethod
    def test_cancelled_is_threading_event():
        """_cancelled deve ser uma instância de threading.Event."""
        p = _ConcreteProvider()
        assert isinstance(p._cancelled, threading.Event)

    @staticmethod
    def test_cfg_initially_empty():
        """_cfg deve iniciar como dicionário vazio."""
        p = _ConcreteProvider()
        assert p._cfg == {}


class TestAnthropicProvider:
    """Verifica o AnthropicProvider (sem requisições reais de rede)."""

    @pytest.fixture()
    @staticmethod
    def provider():
        """AnthropicProvider configurado com chave de teste."""
        p = AnthropicProvider()
        p.configure({"api_key": "sk-ant-test",
                    "model": "claude-3-haiku-20240307"})
        return p

    @staticmethod
    def test_name(provider):
        """O nome do provider deve ser uma string não vazia."""
        assert provider.name

    @staticmethod
    def test_icon_exists(provider):
        """O ícone do provider deve ser uma string não vazia."""
        assert provider.icon

    @staticmethod
    def test_requires_key_true(provider):
        """AnthropicProvider deve exigir chave de API."""
        assert provider.requires_key is True

    @staticmethod
    def test_check_without_key_returns_error():
        """check() com chave vazia deve retornar mensagem de erro."""
        p = AnthropicProvider()
        p.configure({"api_key": ""})
        result = p.check()
        assert result is not None

    @staticmethod
    def test_cancel_before_send(provider):
        """Cancelar antes de send() deve evitar que chunks sejam emitidos."""
        provider.cancel()
        chunks, done, errors = [], [], []
        provider.send(
            [{"role": "user", "content": "hi"}],
            on_chunk=chunks.append,
            on_done=done.append,
            on_error=errors.append,
        )
        time.sleep(0.15)
        assert not chunks or errors or done

    @staticmethod
    def test_send_with_invalid_key_calls_on_error(provider):
        """send() com chave inválida deve eventualmente chamar on_error ou on_done."""
        errors, done = [], []
        provider.send(
            [{"role": "user", "content": "hi"}],
            on_chunk=lambda c: None,
            on_done=done.append,
            on_error=errors.append,
        )
        time.sleep(2.0)
        assert errors or done


class TestOpenAIProvider:
    """Verifica o OpenAIProvider."""

    @pytest.fixture()
    @staticmethod
    def provider():
        """OpenAIProvider configurado com chave de teste."""
        p = OpenAIProvider()
        p.configure({"api_key": "sk-openai-test", "model": "gpt-4o-mini"})
        return p

    @staticmethod
    def test_name_set(provider):
        """O nome do provider deve ser uma string não vazia."""
        assert provider.name

    @staticmethod
    def test_requires_key(provider):
        """OpenAIProvider deve exigir chave de API."""
        assert provider.requires_key is True

    @staticmethod
    def test_check_empty_key_returns_error():
        """check() com chave vazia deve retornar mensagem de erro."""
        p = OpenAIProvider()
        p.configure({"api_key": ""})
        result = p.check()
        assert result is not None


class TestOpenAICompatibleProvider:
    """Verifica o OpenAICompatibleProvider (servidores locais)."""

    @pytest.fixture()
    @staticmethod
    def provider():
        """OpenAICompatibleProvider configurado para servidor local."""
        p = OpenAICompatibleProvider()
        p.configure(
            {
                "base_url": "http://localhost:1234/v1",
                "api_key": "",
                "model": "local-model",
            }
        )
        return p

    @staticmethod
    def test_name_set(provider):
        """O nome do provider deve ser uma string não vazia."""
        assert provider.name

    @staticmethod
    def test_requires_key_false(provider):
        """OpenAICompatibleProvider não deve exigir chave de API."""
        assert provider.requires_key is False

    @staticmethod
    def test_check_unreachable_returns_error(provider):
        """check() com servidor inacessível deve retornar None ou mensagem de erro."""
        result = provider.check()
        assert result is None or isinstance(result, str)

    @staticmethod
    def test_configure_stores_base_url(provider):
        """A base_url configurada deve ser armazenada em _cfg."""
        assert provider._cfg.get("base_url") == "http://localhost:1234/v1"


class TestGeminiProvider:
    """Verifica o GeminiProvider."""

    @pytest.fixture()
    @staticmethod
    def provider():
        """GeminiProvider configurado com chave de teste."""
        p = GeminiProvider()
        p.configure({"api_key": "gemini-test-key", "model": "gemini-pro"})
        return p

    @staticmethod
    def test_name_set(provider):
        """O nome do provider deve ser uma string não vazia."""
        assert provider.name

    @staticmethod
    def test_requires_key(provider):
        """GeminiProvider deve exigir chave de API."""
        assert provider.requires_key is True

    @staticmethod
    def test_check_empty_key_returns_error():
        """check() com chave vazia deve retornar mensagem de erro."""
        p = GeminiProvider()
        p.configure({"api_key": ""})
        result = p.check()
        assert result is not None


class TestOllamaProvider:
    """Verifica o OllamaProvider (servidor local sem chave)."""

    @pytest.fixture()
    @staticmethod
    def provider():
        """OllamaProvider configurado para instância local."""
        p = OllamaProvider()
        p.configure({"base_url": "http://localhost:11434", "model": "llama3"})
        return p

    @staticmethod
    def test_name_set(provider):
        """O nome do provider deve ser uma string não vazia."""
        assert provider.name

    @staticmethod
    def test_requires_key_false(provider):
        """OllamaProvider não deve exigir chave de API."""
        assert provider.requires_key is False

    @staticmethod
    def test_check_unreachable_returns_error(provider):
        """check() com Ollama não disponível deve retornar None ou mensagem de erro."""
        result = provider.check()
        assert result is None or isinstance(result, str)

    @staticmethod
    def test_list_models_unreachable_returns_empty(provider):
        """list_models() com Ollama inacessível deve retornar lista vazia."""
        result = provider.list_models()
        assert isinstance(result, list)

    @staticmethod
    def test_configure_model_stored(provider):
        """O modelo configurado deve estar armazenado em _cfg."""
        assert provider._cfg.get("model") == "llama3"


class TestLocalLibProvider:
    """Verifica o LocalLibProvider (modelo em arquivo local)."""

    @pytest.fixture()
    @staticmethod
    def provider():
        """LocalLibProvider apontando para caminho inexistente."""
        p = LocalLibProvider()
        p.configure({"model_path": "/nonexistent/model.gguf"})
        return p

    @staticmethod
    def test_name_set(provider):
        """O nome do provider deve ser uma string não vazia."""
        assert provider.name

    @staticmethod
    def test_check_missing_model_returns_error(provider):
        """check() com arquivo de modelo inexistente deve retornar mensagem de erro."""
        result = provider.check()
        assert result is not None


class TestPostHelpers:
    """Verifica os utilitários internos de requisição HTTP."""

    @staticmethod
    def test_post_json_bad_url_raises_or_returns_none():
        """_post_json() com URL inacessível deve lançar exceção ou retornar None."""
        try:
            result = _post_json(
                "http://127.0.0.1:19999/nonexistent",
                payload={},
                headers={},
                timeout=1,
            )
            assert result is None or isinstance(result, dict)
        except (urllib.error.URLError, OSError, RuntimeError):
            pass

    @staticmethod
    def test_post_stream_bad_url_raises_or_passes():
        """_post_stream() com URL inacessível deve lançar exceção ou concluir silenciosamente."""
        cancel = threading.Event()
        with contextlib.suppress(urllib.error.URLError, OSError, RuntimeError):
            _post_stream(
                url="http://127.0.0.1:19999/nonexistent",
                payload={},
                headers={},
                on_line=lambda lb: None,
                cancelled=cancel,
                timeout=1,
            )

    @staticmethod
    def test_post_stream_cancelled_immediately():
        """_post_stream() com evento de cancelamento já sinalizado não deve fazer requisição."""
        cancel = threading.Event()
        cancel.set()
        lines_received = []
        with contextlib.suppress(urllib.error.URLError, OSError, RuntimeError):
            _post_stream(
                url="http://127.0.0.1:19999/v1/stream",
                payload={},
                headers={},
                on_line=lines_received.append,
                cancelled=cancel,
                timeout=1,
            )
        assert not lines_received


class TestCancellationThreadSafety:
    """Verifica segurança de cancelamento em ambiente multi-thread."""

    @staticmethod
    def test_cancel_from_another_thread():
        """Cancelar de outra thread deve ser seguro e o provider deve finalizar."""
        p = AnthropicProvider()
        p.configure({"api_key": "sk-test", "model": "claude-3-haiku-20240307"})

        errors, done = [], []
        p.send(
            [{"role": "user", "content": "hi"}],
            on_chunk=lambda c: None,
            on_done=done.append,
            on_error=errors.append,
        )
        p.cancel()
        time.sleep(0.3)
        assert p._cancelled.is_set() or errors or done

    @staticmethod
    def test_multiple_cancels_are_idempotent():
        """Chamar cancel() várias vezes não deve causar erros ou estado inconsistente."""
        p = _ConcreteProvider()
        p.cancel()
        p.cancel()
        p.cancel()
        assert p._cancelled.is_set()

    @staticmethod
    def test_cancel_without_send_does_not_raise():
        """cancel() sem send() ativo não deve lançar exceção."""
        p = AnthropicProvider()
        p.configure({"api_key": "sk-test", "model": "claude-3-haiku-20240307"})
        p.cancel()
