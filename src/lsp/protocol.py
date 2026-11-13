#!/usr/bin/env python3
"""Codis LSP - Protocol data structures (LSP 3.17).

Completion, Hover, Diagnostics, SignatureHelp, DocumentSymbol,
Location, Range, Position, TextEdit, WorkspaceEdit,
CodeAction, DocumentHighlight, InlayHint, FoldingRange,
CallHierarchy, TypeHierarchy, SemanticTokens,
PrepareRename, LinkedEditingRanges.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class LSPMessage:
    """Constrói mensagens JSON-RPC 2.0."""

    @staticmethod
    def request(req_id: int, method: str, params: Any = None) -> dict:
        """Constrói uma mensagem de request JSON-RPC 2.0 com id e method."""
        msg: dict[str, Any] = {"jsonrpc": "2.0",
                               "id": req_id, "method": method}
        if params is not None:
            msg["params"] = params
        return msg

    @staticmethod
    def notification(method: str, params: Any = None) -> dict:
        """Constrói uma notificação JSON-RPC 2.0 (sem id, sem resposta esperada)."""
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        return msg

    @staticmethod
    def response(req_id: int, result: Any) -> dict:
        """Constrói uma resposta bem-sucedida JSON-RPC 2.0."""
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    @staticmethod
    def error_response(
        req_id: int, code_or_error: int | dict, message: str = ""
    ) -> dict:
        """Constrói uma resposta de erro JSON-RPC 2.0.

        Aceita duas formas de chamada::

            LSPMessage.error_response(1, {"code": -32600, "message": "..."})
            LSPMessage.error_response(1, -32600, "...")
        """
        if isinstance(code_or_error, dict):
            error = code_or_error
        else:
            error = {"code": code_or_error, "message": message}
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": error,
        }


@dataclass
class Position:
    """Posição (linha, caractere) base-0."""

    line: int
    character: int

    def to_dict(self) -> dict:
        """Serializa para dicionário compatível com o protocolo LSP."""
        return {"line": self.line, "character": self.character}

    @classmethod
    def from_dict(cls, d: dict) -> Position:
        """Desserializa de dicionário LSP, com fallback ``0`` para campos ausentes."""
        return cls(line=d.get("line", 0), character=d.get("character", 0))


@dataclass
class Range:
    """Intervalo [start, end) no documento."""

    start: Position
    end: Position

    def to_dict(self) -> dict:
        """Serializa ``Range`` em dicionário compatível com o protocolo LSP."""
        return {"start": self.start.to_dict(), "end": self.end.to_dict()}

    @classmethod
    def from_dict(cls, d: dict) -> Range:
        """Desserializa um dicionário LSP em ``Range``."""
        return cls(
            start=Position.from_dict(d.get("start", {})),
            end=Position.from_dict(d.get("end", {})),
        )


@dataclass
class Location:
    """URI + Range."""

    uri: str
    range: Range

    @classmethod
    def from_dict(cls, d: dict) -> Location:
        """Desserializa um dicionário LSP em ``Location``."""
        return cls(uri=d.get("uri", ""), range=Range.from_dict(d.get("range", {})))

    def to_dict(self) -> dict:
        """Serializa ``Location`` em dicionário compatível com o protocolo LSP."""
        return {"uri": self.uri, "range": self.range.to_dict()}


@dataclass
class LocationLink:
    """Link de localização com range de origem e destino."""

    origin_selection_range: Range | None
    target_uri: str
    target_range: Range
    target_selection_range: Range

    @classmethod
    def from_dict(cls, d: dict) -> LocationLink:
        """Desserializa um dicionário LSP em ``LocationLink``."""
        osr = d.get("originSelectionRange")
        return cls(
            origin_selection_range=Range.from_dict(osr) if osr else None,
            target_uri=d.get("targetUri", ""),
            target_range=Range.from_dict(d.get("targetRange", {})),
            target_selection_range=Range.from_dict(
                d.get("targetSelectionRange", d.get("targetRange", {}))
            ),
        )


@dataclass
class TextEdit:
    """Edição textual aplicável a um documento."""

    range: Range
    new_text: str

    @classmethod
    def from_dict(cls, d: dict) -> TextEdit:
        """Desserializa um dicionário LSP em ``TextEdit``."""
        return cls(
            range=Range.from_dict(d.get("range", {})), new_text=d.get("newText", "")
        )


@dataclass
class WorkspaceEdit:
    """Conjunto de edições em múltiplos documentos."""

    changes: dict[str, list[TextEdit]] = field(default_factory=dict)
    document_changes: list[dict] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> WorkspaceEdit:
        """Desserializa um dicionário LSP em ``WorkspaceEdit``."""
        if not d:
            return cls()
        changes: dict[str, list[TextEdit]] = {}
        for uri, edits in (d.get("changes") or {}).items():
            changes[uri] = [TextEdit.from_dict(e) for e in edits]
        return cls(changes=changes, document_changes=d.get("documentChanges", []))


@dataclass
class TextDocumentItem:
    """Representa um documento de texto com URI, languageId, versão e conteúdo."""

    uri: str
    language_id: str
    version: int
    text: str

    def to_dict(self) -> dict:
        """Serializa ``TextDocumentItem`` em dicionário compatível com o protocolo LSP."""
        return {
            "uri": self.uri,
            "languageId": self.language_id,
            "version": self.version,
            "text": self.text,
        }


COMPLETION_KINDS = {
    1: "text",
    2: "method",
    3: "function",
    4: "constructor",
    5: "field",
    6: "variable",
    7: "class",
    8: "interface",
    9: "module",
    10: "property",
    11: "unit",
    12: "value",
    13: "enum",
    14: "keyword",
    15: "snippet",
    16: "color",
    17: "file",
    18: "reference",
    19: "folder",
    20: "enum_member",
    21: "constant",
    22: "struct",
    23: "event",
    24: "operator",
    25: "type_parameter",
}

KIND_ICONS = {
    "text": "📄",
    "method": "⚙",
    "function": "ƒ",
    "constructor": "new",
    "field": "▪",
    "variable": "𝑥",
    "class": "◆",
    "interface": "𝕀",
    "module": "mod",
    "property": "p",
    "unit": "#",
    "value": "𝑣",
    "enum": "en",
    "keyword": "kw",
    "snippet": "sn",
    "color": "col",
    "file": "f",
    "reference": "🔗",
    "folder": "📁",
    "enum_member": "e",
    "constant": "π",
    "struct": "📐",
    "event": "ev",
    "operator": "±",
    "type_parameter": "T",
}


@dataclass
class CompletionItem:
    """Item de autocompletar retornado por ``textDocument/completion``."""

    label: str
    kind: int = 1
    detail: str = ""
    documentation: str = ""
    insert_text: str = ""
    insert_text_format: int = 1
    sort_text: str = ""
    filter_text: str = ""
    deprecated: bool = False
    preselect: bool = False
    tags: list[int] = field(default_factory=list)
    text_edit: TextEdit | None = None
    additional_text_edits: list[TextEdit] = field(default_factory=list)
    commit_characters: list[str] = field(default_factory=list)
    command: dict | None = None
    data: Any = None

    @property
    def kind_name(self) -> str:
        """Retorna o nome textual do ``kind`` numérico."""
        return COMPLETION_KINDS.get(self.kind, "text")

    @property
    def icon(self) -> str:
        """Retorna o ícone Unicode associado ao ``kind``."""
        return KIND_ICONS.get(self.kind_name, "?")

    @classmethod
    def from_dict(cls, d: dict) -> CompletionItem:
        """Desserializa um dicionário LSP em ``CompletionItem``."""
        doc = d.get("documentation", "")
        if isinstance(doc, dict):
            doc = doc.get("value", "")
        te = d.get("textEdit")
        return cls(
            label=d.get("label", ""),
            kind=d.get("kind", 1),
            detail=d.get("detail", ""),
            documentation=doc,
            insert_text=d.get("insertText", d.get("label", "")),
            insert_text_format=d.get("insertTextFormat", 1),
            sort_text=d.get("sortText", d.get("label", "")),
            filter_text=d.get("filterText", d.get("label", "")),
            deprecated=d.get("deprecated", False),
            preselect=d.get("preselect", False),
            tags=d.get("tags", []),
            text_edit=TextEdit.from_dict(te) if te else None,
            additional_text_edits=[
                TextEdit.from_dict(e) for e in d.get("additionalTextEdits", [])
            ],
            commit_characters=d.get("commitCharacters", []),
            command=d.get("command"),
            data=d.get("data"),
        )

    def to_dict(self) -> dict:
        """Serializa ``CompletionItem`` em dicionário compatível com o protocolo LSP."""
        d: dict = {"label": self.label, "kind": self.kind}
        if self.detail:
            d["detail"] = self.detail
        if self.documentation:
            d["documentation"] = self.documentation
        if self.insert_text:
            d["insertText"] = self.insert_text
        return d


@dataclass
class Hover:
    """Resultado de ``textDocument/hover``: conteúdo e range opcional."""

    contents: str | dict | list
    range: Range | None = None

    @property
    def markdown(self) -> str:
        """Formata o conteúdo do hover como Markdown puro."""
        c = self.contents
        if isinstance(c, str):
            return c
        if isinstance(c, dict):
            return c.get("value", c.get("language", ""))
        if isinstance(c, list):
            parts: list[str] = []
            for item in c:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    lang = item.get("language", "")
                    val = item.get("value", "")
                    parts.append(f"```{lang}\n{val}\n```" if lang else val)
            return "\n\n".join(parts)
        return str(c)

    @classmethod
    def from_dict(cls, d: dict) -> Hover:
        """Desserializa um dicionário LSP em ``Hover``."""
        r = d.get("range")
        return cls(
            contents=d.get("contents", ""), range=Range.from_dict(r) if r else None
        )


SEVERITY_LABELS = {1: "error", 2: "warning", 3: "information", 4: "hint"}


@dataclass
class DiagnosticRelated:
    """Localização relacionada a um diagnóstico (referência secundária)."""

    location: Location
    message: str

    @classmethod
    def from_dict(cls, d: dict) -> DiagnosticRelated:
        """Desserializa um dicionário LSP em ``DiagnosticRelated``."""
        return cls(
            location=Location.from_dict(d.get("location", {})),
            message=d.get("message", ""),
        )


@dataclass
class Diagnostic:
    """Diagnóstico LSP: posição, severidade, mensagem e informações opcionais."""

    range: Range
    severity: int
    message: str
    source: str = ""
    code: str | int | None = None
    code_description: str = ""
    tags: list[int] = field(default_factory=list)
    related: list[DiagnosticRelated] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> Diagnostic:
        """Desserializa um dicionário LSP em ``Diagnostic``."""
        cd = d.get("codeDescription", {})
        return cls(
            range=Range.from_dict(d.get("range", {})),
            severity=d.get("severity", 1),
            message=d.get("message", ""),
            source=d.get("source", ""),
            code=d.get("code"),
            code_description=cd.get(
                "href", "") if isinstance(cd, dict) else "",
            tags=d.get("tags", []),
            related=[
                DiagnosticRelated.from_dict(r) for r in d.get("relatedInformation", [])
            ],
        )

    def to_dict(self) -> dict:
        """Serializa ``Diagnostic`` em dicionário compatível com o protocolo LSP."""
        d: dict = {
            "range": self.range.to_dict(),
            "severity": self.severity,
            "message": self.message,
        }
        if self.source:
            d["source"] = self.source
        if self.code is not None:
            d["code"] = self.code
        return d


@dataclass
class SignatureParameter:
    """Parâmetro individual de uma assinatura de função."""

    label: str | list[int]
    documentation: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> SignatureParameter:
        """Desserializa um dicionário LSP em ``SignatureParameter``."""
        doc = d.get("documentation", "")
        if isinstance(doc, dict):
            doc = doc.get("value", "")
        return cls(label=d.get("label", ""), documentation=doc)


@dataclass
class SignatureInfo:
    """Assinatura de função com lista de parâmetros."""

    label: str
    documentation: str = ""
    parameters: list[SignatureParameter] = field(default_factory=list)
    active_parameter: int = 0

    @classmethod
    def from_dict(cls, d: dict) -> SignatureInfo:
        """Desserializa um dicionário LSP em ``SignatureInfo``."""
        doc = d.get("documentation", "")
        if isinstance(doc, dict):
            doc = doc.get("value", "")
        return cls(
            label=d.get("label", ""),
            documentation=doc,
            parameters=[
                SignatureParameter.from_dict(p) for p in d.get("parameters", [])
            ],
            active_parameter=d.get("activeParameter", 0),
        )


@dataclass
class SignatureHelp:
    """Resultado de ``textDocument/signatureHelp``: assinaturas e índice ativo."""

    signatures: list[SignatureInfo]
    active_signature: int = 0
    active_parameter: int = 0

    @classmethod
    def from_dict(cls, d: dict) -> SignatureHelp:
        """Desserializa um dicionário LSP em ``SignatureHelp``."""
        if not d:
            return cls(signatures=[])
        return cls(
            signatures=[SignatureInfo.from_dict(
                s) for s in d.get("signatures", [])],
            active_signature=d.get("activeSignature", 0),
            active_parameter=d.get("activeParameter", 0),
        )


SYMBOL_KINDS = {
    1: "file",
    2: "module",
    3: "namespace",
    4: "package",
    5: "class",
    6: "method",
    7: "property",
    8: "field",
    9: "constructor",
    10: "enum",
    11: "interface",
    12: "function",
    13: "variable",
    14: "constant",
    15: "string",
    16: "number",
    17: "boolean",
    18: "array",
    19: "object",
    20: "key",
    21: "null",
    22: "enum_member",
    23: "struct",
    24: "event",
    25: "operator",
    26: "type_parameter",
}

SYMBOL_ICONS = {
    "class": "◆",
    "method": "⚙",
    "function": "ƒ",
    "variable": "𝑥",
    "constant": "π",
    "interface": "𝕀",
    "enum": "en",
    "struct": "📐",
    "namespace": "ns",
    "module": "mod",
    "property": "p",
    "field": "f",
    "constructor": "new",
    "enum_member": "e",
    "type_parameter": "T",
    "event": "ev",
    "operator": "±",
    "file": "f",
}


@dataclass
class DocumentSymbol:
    """Símbolo do documento (classe, função, variável) com range e hierarquia."""

    name: str
    kind: int
    range: Range
    selection_range: Range
    detail: str = ""
    children: list[DocumentSymbol] = field(default_factory=list)
    deprecated: bool = False

    @property
    def kind_name(self) -> str:
        """Retorna o nome textual do ``kind`` numérico."""
        return SYMBOL_KINDS.get(self.kind, "variable")

    @property
    def icon(self) -> str:
        """Retorna o ícone Unicode associado ao ``kind``."""
        return SYMBOL_ICONS.get(self.kind_name, "?")

    @classmethod
    def from_dict(cls, d: dict) -> DocumentSymbol:
        """Desserializa um dicionário LSP em ``DocumentSymbol``."""
        rng = d.get("range") or d.get("location", {}).get("range", {})
        sel = d.get("selectionRange") or rng
        return cls(
            name=d.get("name", ""),
            kind=d.get("kind", 13),
            range=Range.from_dict(rng),
            selection_range=Range.from_dict(sel),
            detail=d.get("detail", ""),
            children=[DocumentSymbol.from_dict(c)
                      for c in d.get("children", [])],
            deprecated=d.get("deprecated", False),
        )


CODE_ACTION_KINDS = {
    "quickfix": "🔧 Quick Fix",
    "refactor": "🔄 Refactor",
    "refactor.extract": "📤 Extract",
    "refactor.inline": "📥 Inline",
    "refactor.rewrite": "✏️ Rewrite",
    "source": "📎 Source Action",
    "source.organizeImports": "📋 Organize Imports",
    "source.fixAll": "🛠 Fix All",
}


@dataclass
class CodeAction:
    """Ação de código (quick fix, refactor) retornada por ``textDocument/codeAction``."""

    title: str
    kind: str = ""
    diagnostics: list[Diagnostic] = field(default_factory=list)
    is_preferred: bool = False
    edit: WorkspaceEdit | None = None
    command: dict | None = None
    data: Any = None

    @property
    def kind_label(self) -> str:
        """Retorna o rótulo legível do tipo de code action."""
        return CODE_ACTION_KINDS.get(self.kind, self.kind or "Action")

    @classmethod
    def from_dict(cls, d: dict) -> CodeAction:
        """Desserializa um dicionário LSP em ``CodeAction``."""
        edit = d.get("edit")
        return cls(
            title=d.get("title", ""),
            kind=d.get("kind", ""),
            diagnostics=[Diagnostic.from_dict(x)
                         for x in d.get("diagnostics", [])],
            is_preferred=d.get("isPreferred", False),
            edit=WorkspaceEdit.from_dict(edit) if edit else None,
            command=d.get("command"),
            data=d.get("data"),
        )


HIGHLIGHT_KINDS = {1: "text", 2: "read", 3: "write"}


@dataclass
class DocumentHighlight:
    """Destaque de ocorrência no documento com range e tipo (text/read/write)."""

    range: Range
    kind: int = 1

    @property
    def kind_name(self) -> str:
        """Retorna o nome textual do ``kind`` numérico."""
        return HIGHLIGHT_KINDS.get(self.kind, "text")

    @classmethod
    def from_dict(cls, d: dict) -> DocumentHighlight:
        """Desserializa um dicionário LSP em ``DocumentHighlight``."""
        return cls(range=Range.from_dict(d.get("range", {})), kind=d.get("kind", 1))


@dataclass
class FoldingRange:
    """Intervalo de folding de código com linhas de início e fim."""

    start_line: int
    end_line: int
    start_character: int | None = None
    end_character: int | None = None
    kind: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> FoldingRange:
        """Desserializa um dicionário LSP em ``FoldingRange``."""
        return cls(
            start_line=d.get("startLine", 0),
            end_line=d.get("endLine", 0),
            start_character=d.get("startCharacter"),
            end_character=d.get("endCharacter"),
            kind=d.get("kind", ""),
        )


INLAY_HINT_KINDS = {1: "type", 2: "parameter"}


@dataclass
class InlayHintPart:
    """Parte individual de um inlay hint (rótulo ou tooltip)."""

    value: str
    tooltip: str = ""
    location: Location | None = None

    @classmethod
    def from_dict(cls, d: dict) -> InlayHintPart:
        """Desserializa um dicionário LSP em ``InlayHintPart``."""
        loc = d.get("location")
        tt = d.get("tooltip", "")
        if isinstance(tt, dict):
            tt = tt.get("value", "")
        return cls(
            value=d.get("value", ""),
            tooltip=tt,
            location=Location.from_dict(loc) if loc else None,
        )


@dataclass
class InlayHint:
    """Hint inline exibido no editor com posição, rótulo e tipo opcional."""

    position: Position
    label: str | list[InlayHintPart]
    kind: int = 0
    padding_left: bool = False
    padding_right: bool = False
    tooltip: str = ""

    @property
    def label_str(self) -> str:
        """Retorna o label do inlay hint como string simples."""
        if isinstance(self.label, str):
            return self.label
        return "".join(p.value for p in self.label)

    @property
    def kind_name(self) -> str:
        """Retorna o nome textual do ``kind`` numérico."""
        return INLAY_HINT_KINDS.get(self.kind, "")

    @classmethod
    def from_dict(cls, d: dict) -> InlayHint:
        """Desserializa um dicionário LSP em ``InlayHint``."""
        lbl = d.get("label", "")
        if isinstance(lbl, list):
            lbl = [
                (
                    InlayHintPart.from_dict(p)
                    if isinstance(p, dict)
                    else InlayHintPart(value=str(p))
                )
                for p in lbl
            ]
        tt = d.get("tooltip", "")
        if isinstance(tt, dict):
            tt = tt.get("value", "")
        return cls(
            position=Position.from_dict(d.get("position", {})),
            label=lbl,
            kind=d.get("kind", 0),
            padding_left=d.get("paddingLeft", False),
            padding_right=d.get("paddingRight", False),
            tooltip=tt,
        )


@dataclass
class CallHierarchyItem:
    """Item de hierarquia de chamadas retornado pelo servidor LSP."""

    name: str
    kind: int
    uri: str
    range: Range
    selection_range: Range
    detail: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> CallHierarchyItem:
        """Desserializa um dicionário LSP em ``CallHierarchyItem``."""
        return cls(
            name=d.get("name", ""),
            kind=d.get("kind", 12),
            uri=d.get("uri", ""),
            range=Range.from_dict(d.get("range", {})),
            selection_range=Range.from_dict(d.get("selectionRange", {})),
            detail=d.get("detail", ""),
        )


@dataclass
class CallHierarchyCall:
    """Chamada de entrada ou saída na hierarquia, com ranges de referência."""

    item: CallHierarchyItem
    from_ranges: list[Range]

    @classmethod
    def from_incoming(cls, d: dict) -> CallHierarchyCall:
        """Constrói ``CallHierarchyCall`` a partir de uma chamada de hierarquia *incoming*."""
        return cls(
            item=CallHierarchyItem.from_dict(d.get("from", {})),
            from_ranges=[Range.from_dict(r) for r in d.get("fromRanges", [])],
        )

    @classmethod
    def from_outgoing(cls, d: dict) -> CallHierarchyCall:
        """Constrói ``CallHierarchyCall`` a partir de uma chamada de hierarquia *outgoing*."""
        return cls(
            item=CallHierarchyItem.from_dict(d.get("to", {})),
            from_ranges=[Range.from_dict(r) for r in d.get("fromRanges", [])],
        )


@dataclass
class TypeHierarchyItem:
    """Item da hierarquia de tipos (supertipo ou subtipo)."""

    name: str
    kind: int
    uri: str
    range: Range
    selection_range: Range
    detail: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> TypeHierarchyItem:
        """Desserializa um dicionário LSP em ``TypeHierarchyItem``."""
        return cls(
            name=d.get("name", ""),
            kind=d.get("kind", 5),
            uri=d.get("uri", ""),
            range=Range.from_dict(d.get("range", {})),
            selection_range=Range.from_dict(d.get("selectionRange", {})),
            detail=d.get("detail", ""),
        )


SEMANTIC_TOKEN_TYPES = [
    "namespace",
    "type",
    "class",
    "enum",
    "interface",
    "struct",
    "typeParameter",
    "parameter",
    "variable",
    "property",
    "enumMember",
    "event",
    "function",
    "method",
    "macro",
    "keyword",
    "modifier",
    "comment",
    "string",
    "number",
    "regexp",
    "operator",
    "decorator",
]

SEMANTIC_TOKEN_MODIFIERS = [
    "declaration",
    "definition",
    "readonly",
    "static",
    "deprecated",
    "abstract",
    "async",
    "modification",
    "documentation",
    "defaultLibrary",
]


@dataclass
class SemanticTokens:
    """Tokens semânticos codificados do documento para colorização avançada."""

    result_id: str | None
    data: list[int]

    @classmethod
    def from_dict(cls, d: dict) -> SemanticTokens:
        """Desserializa um dicionário LSP em ``SemanticTokens``."""
        if not d:
            return cls(result_id=None, data=[])
        return cls(result_id=d.get("resultId"), data=d.get("data", []))

    def decode(self, legend_types: list[str], legend_mods: list[str]) -> list[dict]:
        """Decodifica o encoding delta dos semantic tokens em lista de dicts."""
        tokens: list[dict] = []
        line = char = 0
        data = self.data
        i = 0
        while i + 4 < len(data):
            dl, dc, length, token_type, token_mods = data[i: i + 5]
            if dl:
                line += dl
                char = dc
            else:
                char += dc
            t_name = (
                legend_types[token_type]
                if token_type < len(legend_types)
                else "unknown"
            )
            mods = [
                legend_mods[b]
                for b in range(len(legend_mods))
                if (token_mods & (1 << b)) and b < len(legend_mods)
            ]
            tokens.append(
                {
                    "line": line,
                    "start": char,
                    "length": length,
                    "type": t_name,
                    "modifiers": mods,
                }
            )
            i += 5
        return tokens


@dataclass
class PrepareRenameResult:
    """Resultado de ``textDocument/prepareRename``: range e placeholder."""

    range: Range
    placeholder: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> PrepareRenameResult:
        """Desserializa um dicionário LSP em ``PrepareRenameResult``."""
        if not d:
            return cls(range=Range(Position(0, 0), Position(0, 0)))
        if "range" in d:
            return cls(
                range=Range.from_dict(d["range"]), placeholder=d.get("placeholder", "")
            )
        return cls(range=Range.from_dict(d))


@dataclass
class LinkedEditingRanges:
    """Ranges vinculados para edição simultânea (ex: tags HTML abertura/fechamento)."""

    ranges: list[Range]
    word_pattern: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> LinkedEditingRanges:
        """Desserializa um dicionário LSP em ``LinkedEditingRanges``."""
        if not d:
            return cls(ranges=[])
        return cls(
            ranges=[Range.from_dict(r) for r in d.get("ranges", [])],
            word_pattern=d.get("wordPattern"),
        )
