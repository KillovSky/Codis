#!/usr/bin/env python3
"""Testes para src.ia.context_builder - construção de contexto para IA."""

from __future__ import annotations

from src.ia.context_builder import ContextBuilder


class TestContextBuilderBasic:
    """Verifica a construção básica de contexto."""

    @staticmethod
    def test_build_returns_dict():
        """build() deve retornar um dicionário."""
        cb = ContextBuilder()
        ctx = cb.build()
        assert isinstance(ctx, dict)

    @staticmethod
    def test_empty_builder_has_no_code():
        """ContextBuilder vazio não deve incluir código no contexto."""
        cb = ContextBuilder()
        ctx = cb.build()
        assert ctx.get("code", "") == "" or "code" not in ctx

    @staticmethod
    def test_set_code_stored():
        """set_code() deve incluir o código no contexto."""
        cb = ContextBuilder()
        cb.set_code("x = 1\n")
        ctx = cb.build()
        assert ctx["code"] == "x = 1\n"

    @staticmethod
    def test_set_language_stored():
        """set_language() deve incluir a linguagem no contexto."""
        cb = ContextBuilder()
        cb.set_language("python")
        ctx = cb.build()
        assert ctx["language"] == "python"

    @staticmethod
    def test_set_file_path_stored():
        """set_file_path() deve incluir o caminho do arquivo no contexto."""
        cb = ContextBuilder()
        cb.set_file_path("/home/user/main.py")
        ctx = cb.build()
        assert ctx["file_path"] == "/home/user/main.py"

    @staticmethod
    def test_set_selection_stored():
        """set_selection() deve incluir o texto selecionado no contexto."""
        cb = ContextBuilder()
        cb.set_selection("selected code")
        ctx = cb.build()
        assert ctx["selection"] == "selected code"

    @staticmethod
    def test_set_cursor_position_stored():
        """set_cursor_position() deve armazenar a posição (linha, coluna) no contexto."""
        cb = ContextBuilder()
        cb.set_cursor_position(line=5, col=12)
        ctx = cb.build()
        assert ctx["cursor"]["line"] == 5
        assert ctx["cursor"]["col"] == 12

    @staticmethod
    def test_set_diagnostics_stored():
        """set_diagnostics() deve incluir os diagnósticos no contexto."""
        diags = [{"line": 1, "message": "undefined name"}]
        cb = ContextBuilder()
        cb.set_diagnostics(diags)
        ctx = cb.build()
        assert ctx["diagnostics"] == diags

class TestContextBuilderChaining:
    """Verifica que os setters suportam encadeamento fluente."""

    @staticmethod
    def test_chaining_returns_self():
        """Cada setter deve retornar a própria instância (para encadeamento)."""
        cb = ContextBuilder()
        result = cb.set_code("pass").set_language(
            "python").set_file_path("/tmp/x.py")
        assert result is cb

    @staticmethod
    def test_full_chain_builds_correctly():
        """Encadeamento completo deve produzir contexto com todos os campos."""
        ctx = (
            ContextBuilder()
            .set_code("def foo(): pass")
            .set_language("python")
            .set_file_path("/tmp/foo.py")
            .set_selection("def foo(): pass")
            .set_cursor_position(line=1, col=0)
            .build()
        )
        assert ctx["code"] == "def foo(): pass"
        assert ctx["language"] == "python"
        assert ctx["file_path"] == "/tmp/foo.py"
        assert ctx["selection"] == "def foo(): pass"
        assert ctx["cursor"]["line"] == 1


class TestContextBuilderReset:
    """Verifica o comportamento de reset do builder."""

    @staticmethod
    def test_reset_clears_code():
        """reset() deve limpar o código armazenado."""
        cb = ContextBuilder()
        cb.set_code("x = 1")
        cb.reset()
        ctx = cb.build()
        assert ctx.get("code", "") == "" or "code" not in ctx

    @staticmethod
    def test_reset_clears_language():
        """reset() deve limpar a linguagem armazenada."""
        cb = ContextBuilder()
        cb.set_language("python")
        cb.reset()
        ctx = cb.build()
        assert ctx.get("language", "") in ("", None, "plaintext")

    @staticmethod
    def test_reset_allows_reuse():
        """Após reset(), o builder deve aceitar novos valores normalmente."""
        cb = ContextBuilder()
        cb.set_code("old code")
        cb.reset()
        cb.set_code("new code")
        ctx = cb.build()
        assert ctx["code"] == "new code"


class TestContextBuilderEdgeCases:
    """Verifica casos extremos na construção de contexto."""

    @staticmethod
    def test_empty_code_string():
        """Código vazio deve ser armazenado como string vazia sem erros."""
        cb = ContextBuilder()
        cb.set_code("")
        ctx = cb.build()
        assert ctx.get("code", "") == ""

    @staticmethod
    def test_none_selection_not_required():
        """Sem set_selection(), o campo deve estar ausente ou ser None."""
        cb = ContextBuilder()
        ctx = cb.build()
        assert ctx.get("selection") in (None, "", [])

    @staticmethod
    def test_large_code_handled():
        """Código de 10.000 linhas deve ser armazenado sem truncamento."""
        large = "\n".join(f"x{i} = {i}" for i in range(10_000))
        cb = ContextBuilder()
        cb.set_code(large)
        ctx = cb.build()
        assert len(ctx["code"]) == len(large)

    @staticmethod
    def test_unicode_code_preserved():
        """Código com caracteres Unicode deve ser preservado integralmente."""
        code = "# -*- coding: utf-8 -*-\nprint('olá mundo 🐍')\n"
        cb = ContextBuilder()
        cb.set_code(code)
        ctx = cb.build()
        assert "🐍" in ctx["code"]

    @staticmethod
    def test_build_idempotent():
        """Chamadas repetidas a build() devem retornar contextos equivalentes."""
        cb = ContextBuilder()
        cb.set_code("x = 1")
        ctx1 = cb.build()
        ctx2 = cb.build()
        assert ctx1 == ctx2

    @staticmethod
    def test_build_returns_copy():
        """Modificar o dicionário retornado por build() não deve afetar o estado interno."""
        cb = ContextBuilder()
        cb.set_code("original")
        ctx = cb.build()
        ctx["code"] = "tampered"
        assert cb.build()["code"] == "original"
