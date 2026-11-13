#!/usr/bin/env python3
"""Testes unitários para src.ui._persistence_utils.

Cobre o helper genérico de save/load de listas de dataclasses usado por
chat_panel.py, remote_browser.py, sql_studio.py, task_manager.py e
clock_panel.py.
"""

from __future__ import annotations

import json
import logging
import stat
import sys
from dataclasses import dataclass, field

import pytest

from src.ui._persistence_utils import load_dataclass_list, save_dataclass_list


@dataclass
class _Item:
    """Dataclass simples usada nos testes."""

    name: str = ""
    value: int = 0
    tags: list[str] = field(default_factory=list)


class TestLoadDataclassList:
    """Testes para load_dataclass_list()."""

    @staticmethod
    def test_missing_file_returns_empty_list(tmp_path):
        """Arquivo inexistente deve retornar lista vazia sem erro."""
        assert load_dataclass_list(tmp_path / "nope.json", _Item) == []

    @staticmethod
    def test_roundtrip(tmp_path):
        """Itens salvos devem ser recarregados de forma equivalente."""
        path = tmp_path / "items.json"
        items = [_Item(name="a", value=1), _Item(
            name="b", value=2, tags=["x"])]
        save_dataclass_list(path, items)
        loaded = load_dataclass_list(path, _Item)
        assert loaded == items

    @staticmethod
    def test_unknown_fields_are_filtered(tmp_path):
        """Campos desconhecidos no JSON (versão antiga/nova) não devem
        quebrar o carregamento - tolerância a mudanças de schema."""
        path = tmp_path / "items.json"
        path.write_text(
            json.dumps([{"name": "a", "value": 1, "removed_field": "x"}]),
            encoding="utf-8",
        )
        loaded = load_dataclass_list(path, _Item)
        assert loaded == [_Item(name="a", value=1)]

    @staticmethod
    def test_non_dict_items_are_skipped(tmp_path):
        """Itens que não são dict na lista JSON devem ser ignorados, sem
        descartar os itens válidos ao redor."""
        path = tmp_path / "items.json"
        path.write_text(
            json.dumps([{"name": "a"}, "not-a-dict", 42, {"name": "b"}]),
            encoding="utf-8",
        )
        loaded = load_dataclass_list(path, _Item)
        assert loaded == [_Item(name="a"), _Item(name="b")]

    @staticmethod
    def test_corrupted_json_returns_empty_and_logs(tmp_path, caplog):
        """JSON corrompido deve retornar lista vazia e registrar um aviso
        (em vez de falhar silenciosamente, como no código duplicado
        original)."""
        path = tmp_path / "items.json"
        path.write_text("{not valid json", encoding="utf-8")
        logger = logging.getLogger("Codis.PersistenceTest")
        with caplog.at_level(logging.WARNING, logger="Codis.PersistenceTest"):
            loaded = load_dataclass_list(path, _Item, logger=logger)
        assert loaded == []
        assert any(
            "items.json" in r.message or "items.json" in str(r) for r in caplog.records
        )

    @staticmethod
    def test_non_list_json_returns_empty(tmp_path):
        """JSON válido mas que não é uma lista (ex.: um dict solto) deve
        retornar lista vazia em vez de lançar exceção."""
        path = tmp_path / "items.json"
        path.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
        assert load_dataclass_list(path, _Item) == []


class TestSaveDataclassList:
    """Testes para save_dataclass_list()."""

    @staticmethod
    def test_creates_parent_dir(tmp_path):
        """Diretório pai deve ser criado automaticamente se não existir."""
        path = tmp_path / "nested" / "dir" / "items.json"
        save_dataclass_list(path, [_Item(name="a")])
        assert path.exists()

    @staticmethod
    def test_writes_valid_json(tmp_path):
        """Conteúdo escrito deve ser JSON válido representando os itens."""
        path = tmp_path / "items.json"
        save_dataclass_list(path, [_Item(name="a", value=1)])
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data == [{"name": "a", "value": 1, "tags": []}]

    @staticmethod
    @pytest.mark.skipif(
        sys.platform == "win32", reason="chmod não é significativo no Windows"
    )
    def test_chmod_600_restricts_permissions(tmp_path):
        """chmod_600=True deve restringir a permissão do arquivo a 0600
        (usado para arquivos com dados sensíveis, como tokens/senhas)."""
        path = tmp_path / "secrets.json"
        save_dataclass_list(path, [_Item(name="a")], chmod_600=True)
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o600

    @staticmethod
    def test_chmod_600_false_does_not_restrict(tmp_path):
        """Sem chmod_600, o arquivo deve manter as permissões padrão do
        sistema (não deve ser forçado a 0600)."""
        path = tmp_path / "items.json"
        save_dataclass_list(path, [_Item(name="a")], chmod_600=False)
        assert path.exists()
