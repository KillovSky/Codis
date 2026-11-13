#!/usr/bin/env python3
"""Testes para src.lint.manager - Diag dataclass e LintManager."""

from __future__ import annotations

import pytest

from src.lint.manager import Diag, LintManager


def _make_diag(severity: int) -> Diag:
    """Cria um Diag com campos mínimos para o severity indicado."""
    return Diag(
        line=1,
        col=0,
        end_col=5,
        severity=severity,
        message="test msg",
        rule="TEST",
        source="test",
    )


class TestDiag:
    """Verifica a dataclass Diag e suas propriedades computadas."""

    @staticmethod
    def test_fields_stored_correctly():
        """Todos os campos obrigatórios devem ser armazenados sem alteração."""
        d = Diag(
            line=10,
            col=5,
            end_col=15,
            severity=1,
            message="undefined var",
            rule="E001",
            source="pyflakes",
        )
        assert d.line == 10
        assert d.col == 5
        assert d.end_col == 15
        assert d.message == "undefined var"
        assert d.rule == "E001"
        assert d.source == "pyflakes"

    @staticmethod
    def test_default_optional_fields():
        """Campos opcionais devem ter seus valores padrão quando não fornecidos."""
        d = Diag(line=1, col=0, end_col=0, severity=2, message="warn")
        assert d.rule == ""
        assert d.source == ""
        assert d.fix is None
        assert not d.related

    @staticmethod
    def test_equality():
        """Dois Diag com mesmos campos devem ser iguais."""
        d1 = Diag(line=1, col=0, end_col=0, severity=1, message="x")
        d2 = Diag(line=1, col=0, end_col=0, severity=1, message="x")
        assert d1 == d2

    @staticmethod
    def test_inequality_different_line():
        """Diag com linhas diferentes devem ser desiguais."""
        d1 = Diag(line=1, col=0, end_col=0, severity=1, message="x")
        d2 = Diag(line=2, col=0, end_col=0, severity=1, message="x")
        assert d1 != d2


class TestPySyntax:
    """Verifica a análise de sintaxe Python pelo LintManager."""

    @pytest.fixture()
    @staticmethod
    def lm():
        """LintManager com callback de entrega vazio."""
        return LintManager(deliver=lambda diags: None)

    @staticmethod
    def test_valid_python_returns_empty(lm):
        """Código Python válido não deve gerar nenhum diagnóstico."""
        result = lm._py_syntax("x = 1\nprint(x)\n")
        assert result == []

    @staticmethod
    def test_syntax_error_returns_diag(lm):
        """Código com SyntaxError deve gerar pelo menos um diagnóstico de erro."""
        result = lm._py_syntax("def foo(:\n    pass\n")
        assert len(result) >= 1
        assert result[0].severity == 1
        assert result[0].source == "py_compile"

    @staticmethod
    def test_syntax_error_has_line_number(lm):
        """O diagnóstico de SyntaxError deve conter número de linha >= 1."""
        code = "x = 1\ny = (\nz = 3\n"
        result = lm._py_syntax(code)
        if result:
            assert result[0].line >= 1

    @staticmethod
    def test_empty_string_is_valid(lm):
        """String vazia deve ser considerada válida sem gerar diagnósticos."""
        result = lm._py_syntax("")
        assert result == []

    @staticmethod
    def test_complex_valid_code(lm):
        """Código Python complexo e válido não deve gerar diagnósticos."""
        code = (
            "\nimport os\nfrom pathlib import Path\n\n"
            "class Foo:\n    def __init__(self, x: int) -> None:\n        self.x = x\n\n"
            "    def bar(self) -> str:\n        return str(self.x)\n\n"
            "def main():\n    f = Foo(42)\n    print(f.bar())\n\n"
            'if __name__ == "__main__":\n    main()\n'
        )
        assert lm._py_syntax(code) == []

    @staticmethod
    def test_indentation_error_detected(lm):
        """IndentationError deve ser detectado como diagnóstico de sintaxe."""
        code = "if True:\npass\n"
        result = lm._py_syntax(code)
        assert len(result) >= 1


class TestFindTool:
    """Verifica o mecanismo de busca e cache de ferramentas externas."""

    @pytest.fixture()
    @staticmethod
    def lm():
        """LintManager com callback de entrega vazio."""
        return LintManager(deliver=lambda d: None)

    @staticmethod
    def test_find_existing_tool(lm):
        """Ferramenta existente no PATH deve ser encontrada ou retornar None."""
        result = lm._find_tool("python3")
        assert result is None or isinstance(result, str)

    @staticmethod
    def test_find_nonexistent_tool(lm):
        """Ferramenta inexistente deve retornar None."""
        result = lm._find_tool("tool_that_absolutely_does_not_exist_xyzabc")
        assert result is None

    @staticmethod
    def test_cache_stores_result(lm):
        """O resultado de _find_tool() deve ser armazenado em cache."""
        lm._find_tool("python3")
        assert "python3" in lm._tool_cache


class TestLintManagerLifecycle:
    """Verifica a criação e ciclo de vida do LintManager."""

    @staticmethod
    def test_create_without_cfg():
        """LintManager deve ser criado sem argumentos de configuração."""
        lm = LintManager(deliver=lambda d: None)
        assert lm is not None

    @staticmethod
    def test_create_with_cfg(cfg):
        """LintManager deve aceitar instância de ConfigManager opcional."""
        lm = LintManager(deliver=lambda d: None, cfg=cfg)
        assert lm is not None

    @staticmethod
    def test_cancel_does_not_raise():
        """cancel() sem job ativo não deve lançar exceção."""
        lm = LintManager(deliver=lambda d: None)
        lm.cancel()

    @staticmethod
    def test_set_log_callback():
        """set_log_callback() deve registrar o callback de log."""
        lm = LintManager(deliver=lambda d: None)
        lm.set_log_callback(lambda tool, msg, lvl: None)
        assert lm._log_callback is not None

    @staticmethod
    def test_schedule_none_path_does_not_crash(monkeypatch):
        """schedule() com path None não deve lançar exceção."""
        lm = LintManager(deliver=lambda d: None)
        monkeypatch.setattr(lm, "_start_thread", lambda *a, **kw: None)
        lm.schedule(None, "x = 1", "python")
