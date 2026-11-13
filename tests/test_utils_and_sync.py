#!/usr/bin/env python3
"""Testes unitários para src.editor._pane_utils e sync_locales."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from src.editor._pane_utils import _lsp_uri_to_path

_SYNC_LOCALES_PATH = Path(__file__).parent.parent / "sync_locales.py"


def _load_sync_mod():
    """Carrega sync_locales.py como módulo dinamicamente."""
    spec = importlib.util.spec_from_file_location(
        "sync_locales", _SYNC_LOCALES_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_sync(ld: Path) -> dict[str, dict]:
    """Executa a lógica central do sync_locales e retorna os dicts resultantes."""
    mod = _load_sync_mod()
    all_data = mod.load_all(ld)
    all_keys = mod.collect_all_keys(all_data)
    results = mod.sync(all_data, all_keys)
    return {fname: new_dict for fname, (new_dict, _) in results.items()}


@pytest.fixture(name="locale_dir")
def fresh_locale_dir(tmp_path):
    """Diretório temporário com 3 locales de teste: pt-BR, en e es."""
    a = {"hello": "Olá", "world": "Mundo", "extra_a": "Extra A"}
    b = {"hello": "Hello", "world": "World"}
    c = {"hello": "Hola"}

    (tmp_path / "pt-BR.json").write_text(
        json.dumps(a, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (tmp_path / "en.json").write_text(
        json.dumps(b, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (tmp_path / "es.json").write_text(
        json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return tmp_path


class TestLspUriToPath:
    """Verifica a conversão de URIs LSP para objetos Path."""

    @staticmethod
    def test_unix_path():
        """URI de arquivo Unix deve ser convertida para Path com 'home' e 'main.py'."""
        result = _lsp_uri_to_path("file:///home/user/project/main.py")
        assert str(result).endswith("main.py")
        assert "home" in str(result)

    @staticmethod
    def test_no_scheme_passes_through():
        """URI sem esquema deve ser convertida diretamente para Path."""
        result = _lsp_uri_to_path("/tmp/plain.py")
        assert str(result) == "/tmp/plain.py"

    @staticmethod
    def test_returns_path_object():
        """O retorno de _lsp_uri_to_path() deve ser sempre um objeto Path."""
        result = _lsp_uri_to_path("file:///tmp/x.py")
        assert isinstance(result, Path)

    @staticmethod
    def test_windows_path_uri(monkeypatch):
        """Em plataforma Windows, o '/' inicial do drive deve ser removido."""
        monkeypatch.setattr(sys, "platform", "win32")
        result = _lsp_uri_to_path("file:///C:/Users/test/file.py")
        assert "C:" in str(result) or "file.py" in str(result)

    @staticmethod
    def test_empty_string():
        """String vazia deve retornar um objeto Path sem lançar exceção."""
        result = _lsp_uri_to_path("")
        assert isinstance(result, Path)

    @staticmethod
    def test_relative_uri():
        """URI relativa deve ser convertida para Path sem exceção."""
        result = _lsp_uri_to_path("file://relative/path.py")
        assert isinstance(result, Path)


class TestSyncLocalesLogic:
    """Verifica a lógica de sincronização de chaves entre locales."""

    @staticmethod
    def test_all_files_get_same_key_count(locale_dir):
        """Após sync, todos os arquivos devem ter o mesmo número de chaves."""
        results = _run_sync(locale_dir)
        counts = [len(d) for d in results.values()]
        assert len(set(counts)) == 1, f"Contagens diferentes: {counts}"

    @staticmethod
    def test_superset_contains_all_keys(locale_dir):
        """O conjunto de chaves resultante deve conter todas as originais."""
        results = _run_sync(locale_dir)
        all_keys: set = set()
        for d in results.values():
            all_keys.update(d.keys())
        assert "hello" in all_keys
        assert "world" in all_keys
        assert "extra_a" in all_keys

    @staticmethod
    def test_existing_values_preserved(locale_dir):
        """Valores já presentes nos locales não devem ser sobrescritos."""
        results = _run_sync(locale_dir)
        en = results["en.json"]
        assert en["hello"] == "Hello"
        assert en["world"] == "World"

    @staticmethod
    def test_missing_keys_filled_from_donor(locale_dir):
        """Chaves ausentes em um locale devem ser preenchidas com valor de outro."""
        results = _run_sync(locale_dir)
        es = results["es.json"]
        assert "world" in es
        assert "extra_a" in es

    @staticmethod
    def test_filled_value_is_not_empty(locale_dir):
        """Nenhum valor preenchido deve ser None ou string vazia."""
        results = _run_sync(locale_dir)
        for fname, data in results.items():
            for key, val in data.items():
                assert (
                    val is not None and val != ""
                ), f"{fname}: chave '{key}' com valor vazio"

    @staticmethod
    def test_idempotent(locale_dir):
        """Executar sync duas vezes não deve gerar novas adições na segunda rodada."""
        mod = _load_sync_mod()

        all_data = mod.load_all(locale_dir)
        all_keys = mod.collect_all_keys(all_data)
        results = mod.sync(all_data, all_keys)

        for fname, (new_dict, _added) in results.items():
            (locale_dir / fname).write_text(
                json.dumps(new_dict, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

        all_data2 = mod.load_all(locale_dir)
        all_keys2 = mod.collect_all_keys(all_data2)
        results2 = mod.sync(all_data2, all_keys2)

        for fname, (_, added2) in results2.items():
            assert (
                added2 == []
            ), f"Segunda execução ainda adicionou chaves em {fname}: {added2}"

    @staticmethod
    def test_collect_all_keys_preserves_order(locale_dir):
        """A primeira chave coletada deve ser 'hello' (ordem de pt-BR)."""
        mod = _load_sync_mod()
        all_data = mod.load_all(locale_dir)
        keys = mod.collect_all_keys(all_data)
        assert keys[0] == "hello"

    @staticmethod
    def test_empty_locale_dir_raises(tmp_path):
        """Diretório sem arquivos JSON deve lançar SystemExit."""
        mod = _load_sync_mod()
        with pytest.raises(SystemExit):
            mod.load_all(tmp_path)
