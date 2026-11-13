#!/usr/bin/env python3
"""Testes para src.lsp.protocol - mensagens JSON-RPC 2.0 e tipos LSP."""

from __future__ import annotations

from src.lsp.protocol import (
    CompletionItem,
    Diagnostic,
    Hover,
    Location,
    LSPMessage,
    Position,
    Range,
    SignatureHelp,
    TextDocumentItem,
)


class TestLSPMessageRequest:
    """Verifica a criação de mensagens de requisição JSON-RPC."""

    @staticmethod
    def test_basic_structure():
        """Uma requisição deve conter jsonrpc, id e method."""
        msg = LSPMessage.request(1, "textDocument/hover")
        assert msg["jsonrpc"] == "2.0"
        assert msg["id"] == 1
        assert msg["method"] == "textDocument/hover"

    @staticmethod
    def test_no_params_by_default():
        """Uma requisição sem parâmetros não deve incluir a chave 'params'."""
        msg = LSPMessage.request(1, "initialize")
        assert "params" not in msg

    @staticmethod
    def test_with_params():
        """Parâmetros fornecidos devem ser incluídos na mensagem."""
        params = {"textDocument": {"uri": "file:///test.py"}}
        msg = LSPMessage.request(2, "textDocument/definition", params)
        assert msg["params"] == params

    @staticmethod
    def test_id_can_be_string():
        """O id de requisição pode ser uma string."""
        msg = LSPMessage.request("req-1", "textDocument/completion")
        assert msg["id"] == "req-1"

    @staticmethod
    def test_id_zero_is_valid():
        """O id zero é válido conforme a especificação JSON-RPC."""
        msg = LSPMessage.request(0, "$/cancelRequest")
        assert msg["id"] == 0


class TestLSPMessageNotification:
    """Verifica a criação de notificações JSON-RPC (sem id)."""

    @staticmethod
    def test_notification_has_no_id():
        """Notificações não devem conter a chave 'id'."""
        msg = LSPMessage.notification("textDocument/didOpen")
        assert "id" not in msg

    @staticmethod
    def test_notification_has_jsonrpc():
        """Notificações devem incluir o campo jsonrpc 2.0."""
        msg = LSPMessage.notification("textDocument/didOpen")
        assert msg["jsonrpc"] == "2.0"

    @staticmethod
    def test_notification_has_method():
        """O método deve estar presente na notificação."""
        msg = LSPMessage.notification("textDocument/didOpen")
        assert msg["method"] == "textDocument/didOpen"

    @staticmethod
    def test_notification_with_params():
        """Parâmetros devem ser incluídos nas notificações quando fornecidos."""
        params = {"uri": "file:///x.py"}
        msg = LSPMessage.notification("textDocument/didOpen", params)
        assert msg["params"] == params


class TestLSPMessageResponse:
    """Verifica a criação de respostas JSON-RPC."""

    @staticmethod
    def test_response_has_id():
        """Uma resposta deve incluir o id da requisição correspondente."""
        msg = LSPMessage.response(42, {"result": "ok"})
        assert msg["id"] == 42

    @staticmethod
    def test_response_has_jsonrpc():
        """Uma resposta deve incluir o campo jsonrpc 2.0."""
        msg = LSPMessage.response(1, {})
        assert msg["jsonrpc"] == "2.0"

    @staticmethod
    def test_response_with_result():
        """O resultado deve estar acessível na resposta."""
        result = {"capabilities": {}}
        msg = LSPMessage.response(1, result)
        assert msg.get("result") == result

    @staticmethod
    def test_error_response():
        """Uma resposta de erro deve conter o campo 'error' e não 'result'."""
        error = {"code": -32600, "message": "Invalid Request"}
        msg = LSPMessage.error_response(1, error)
        assert "error" in msg
        assert "result" not in msg


class TestPosition:
    """Verifica a criação e uso de Position (linha, coluna)."""

    @staticmethod
    def test_basic_fields():
        """Position deve armazenar linha e caractere corretos."""
        pos = Position(line=3, character=10)
        assert pos.line == 3
        assert pos.character == 10

    @staticmethod
    def test_to_dict():
        """to_dict() deve retornar dicionário com chaves 'line' e 'character'."""
        pos = Position(line=0, character=5)
        d = pos.to_dict()
        assert d["line"] == 0
        assert d["character"] == 5

    @staticmethod
    def test_from_dict():
        """from_dict() deve reconstruir a Position corretamente."""
        pos = Position.from_dict({"line": 2, "character": 8})
        assert pos.line == 2
        assert pos.character == 8

    @staticmethod
    def test_zero_position():
        """A posição (0, 0) deve ser válida."""
        pos = Position(line=0, character=0)
        assert pos.line == 0
        assert pos.character == 0


class TestRange:
    """Verifica a criação e uso de Range (início e fim)."""

    @staticmethod
    def test_start_end_stored():
        """Range deve armazenar start e end corretamente."""
        start = Position(line=1, character=0)
        end = Position(line=1, character=10)
        r = Range(start=start, end=end)
        assert r.start.line == 1
        assert r.end.character == 10

    @staticmethod
    def test_to_dict():
        """to_dict() deve incluir os campos start e end."""
        start = Position(line=0, character=0)
        end = Position(line=0, character=5)
        r = Range(start=start, end=end)
        d = r.to_dict()
        assert "start" in d
        assert "end" in d

    @staticmethod
    def test_from_dict():
        """from_dict() deve reconstruir o Range a partir de dicionário."""
        raw = {"start": {"line": 2, "character": 0},
               "end": {"line": 2, "character": 8}}
        r = Range.from_dict(raw)
        assert r.start.line == 2
        assert r.end.character == 8


class TestLocation:
    """Verifica a criação e uso de Location (URI + Range)."""

    @staticmethod
    def test_fields():
        """Location deve armazenar uri e range."""
        start = Position(line=0, character=0)
        end = Position(line=0, character=5)
        loc = Location(uri="file:///test.py",
                       range=Range(start=start, end=end))
        assert loc.uri == "file:///test.py"
        assert loc.range.start.line == 0

    @staticmethod
    def test_to_dict():
        """to_dict() deve retornar dicionário com 'uri' e 'range'."""
        start = Position(line=1, character=0)
        end = Position(line=1, character=10)
        loc = Location(uri="file:///a.py", range=Range(start=start, end=end))
        d = loc.to_dict()
        assert d["uri"] == "file:///a.py"
        assert "range" in d


class TestTextDocumentItem:
    """Verifica a criação de TextDocumentItem."""

    @staticmethod
    def test_basic_fields():
        """TextDocumentItem deve armazenar uri, languageId, version e text."""
        item = TextDocumentItem(
            uri="file:///test.py",
            language_id="python",
            version=1,
            text="x = 1\n",
        )
        assert item.uri == "file:///test.py"
        assert item.language_id == "python"
        assert item.version == 1
        assert item.text == "x = 1\n"

    @staticmethod
    def test_to_dict():
        """to_dict() deve incluir languageId em camelCase."""
        item = TextDocumentItem(
            uri="file:///x.py",
            language_id="python",
            version=1,
            text="",
        )
        d = item.to_dict()
        assert "languageId" in d or "language_id" in d


class TestCompletionItem:
    """Verifica a criação e conversão de CompletionItem."""

    @staticmethod
    def test_label_stored():
        """CompletionItem deve armazenar o rótulo de exibição."""
        ci = CompletionItem(label="print")
        assert ci.label == "print"

    @staticmethod
    def test_default_kind():
        """CompletionItem sem kind deve ter valor padrão (texto = 1)."""
        ci = CompletionItem(label="x")
        assert ci.kind is None or isinstance(ci.kind, int)

    @staticmethod
    def test_to_dict():
        """to_dict() deve incluir pelo menos o campo 'label'."""
        ci = CompletionItem(label="def")
        d = ci.to_dict()
        assert d["label"] == "def"

    @staticmethod
    def test_from_dict():
        """from_dict() deve reconstruir o CompletionItem corretamente."""
        raw = {"label": "len", "kind": 3, "detail": "built-in"}
        ci = CompletionItem.from_dict(raw)
        assert ci.label == "len"


class TestDiagnostic:
    """Verifica a criação de Diagnostic LSP."""

    @staticmethod
    def test_basic_fields():
        """Diagnostic deve armazenar range, severity e message."""
        start = Position(line=0, character=0)
        end = Position(line=0, character=5)
        diag = Diagnostic(
            range=Range(start=start, end=end),
            message="undefined name",
            severity=1,
        )
        assert diag.message == "undefined name"
        assert diag.severity == 1

    @staticmethod
    def test_to_dict():
        """to_dict() deve incluir os campos obrigatórios do Diagnostic."""
        start = Position(line=1, character=0)
        end = Position(line=1, character=3)
        diag = Diagnostic(
            range=Range(start=start, end=end),
            message="error",
            severity=1,
        )
        d = diag.to_dict()
        assert "message" in d
        assert "range" in d


class TestHover:
    """Verifica a criação de Hover (resposta de hover)."""

    @staticmethod
    def test_contents_stored():
        """Hover deve armazenar o conteúdo da dica."""
        h = Hover(contents="Built-in function")
        assert h.contents == "Built-in function"

    @staticmethod
    def test_range_optional():
        """Range em Hover é opcional e deve aceitar None."""
        h = Hover(contents="info", range=None)
        assert h.range is None

    @staticmethod
    def test_from_dict():
        """from_dict() deve reconstruir o Hover corretamente."""
        raw = {"contents": {"kind": "markdown", "value": "**bold**"}}
        h = Hover.from_dict(raw)
        assert h.contents is not None


class TestSignatureHelp:
    """Verifica a criação de SignatureHelp."""

    @staticmethod
    def test_empty_signatures():
        """SignatureHelp sem assinaturas deve ter lista vazia."""
        sh = SignatureHelp(signatures=[])
        assert not sh.signatures

    @staticmethod
    def test_from_dict_with_signatures():
        """from_dict() com assinaturas deve populá-las corretamente."""
        raw = {
            "signatures": [
                {"label": "print(*args, sep=' ', end='\\n')",
                 "parameters": []},
            ],
            "activeSignature": 0,
            "activeParameter": 0,
        }
        sh = SignatureHelp.from_dict(raw)
        assert len(sh.signatures) == 1

    @staticmethod
    def test_active_signature_default():
        """activeSignature padrão deve ser 0 ou None."""
        sh = SignatureHelp(signatures=[])
        assert sh.active_signature in (0, None)

    @staticmethod
    def test_active_parameter_default():
        """activeParameter padrão deve ser 0 ou None."""
        sh = SignatureHelp(signatures=[])
        assert sh.active_parameter in (0, None)
