"""Codis - Transformação Operacional (OT) para edição colaborativa.

Modelo
------
Um documento é uma string. Uma *operação* (:class:`TextOp`) é um ``insert``
ou ``delete`` de caracteres. Uma *mudança* é uma **lista** de operações
aplicáveis em sequência (cada uma sobre o resultado da anterior).

Convergência
------------
:func:`transform` implementa a propriedade TP1: dadas duas mudanças ``A`` e
``B`` feitas concorrentemente sobre o mesmo estado, ``A' = transform(A, B)``
aplicada depois de ``B`` produz o mesmo documento que ``B'`` aplicada depois
de ``A``. A única fonte de ambiguidade é o empate de inserções na mesma
posição, resolvido pelo parâmetro ``a_after`` (quem é ordenado depois).
Uma deleção que engloba uma inserção concorrente é *dividida em duas*, de
modo que o texto inserido por outro usuário nunca é apagado em silêncio.

:class:`ClientOT` é a máquina de estados do cliente (protocolo Jupiter):
no máximo uma mudança em voo (aguardando ACK) e as demais em buffer.
"""

from __future__ import annotations

from dataclasses import dataclass

MAX_OP_TEXT = 1_000_000


@dataclass
class TextOp:
    """Operação atômica de texto."""

    op: str
    pos: int
    text: str = ""
    length: int = 0

    def to_dict(self) -> dict:
        """Serializa para o formato do protocolo."""
        return {"op": self.op, "pos": self.pos, "text": self.text, "len": self.length}

    @staticmethod
    def from_dict(d: dict) -> TextOp:
        """Cria uma operação a partir de dados recebidos da rede.

        Raises:
            ValueError: se a operação, a posição, o tamanho ou o texto forem inválidos.
        """
        if not isinstance(d, dict):
            raise ValueError("operação malformada")
        kind, pos = d.get("op"), d.get("pos")
        text, length = d.get("text", ""), d.get("len", 0)
        if kind not in ("insert", "delete"):
            raise ValueError(f"tipo de operação inválido: {kind!r}")
        if not (isinstance(pos, int) and isinstance(length, int) and isinstance(text, str)):
            raise ValueError("campos de operação com tipo inválido")
        if isinstance(pos, bool) or isinstance(length, bool) or pos < 0 or length < 0:
            raise ValueError("posição e tamanho não podem ser negativos")
        if len(text) > MAX_OP_TEXT:
            raise ValueError("texto da operação grande demais")
        return TextOp(op=kind, pos=pos, text=text, length=length)

    def size(self) -> int:
        """Número de caracteres afetados."""
        return len(self.text) if self.op == "insert" else self.length


def ops_from_dicts(items: list) -> list[TextOp]:
    """Converte uma lista de dicts em ``TextOp`` validando cada item."""
    if not isinstance(items, list):
        raise ValueError("ops deve ser uma lista")
    return [TextOp.from_dict(o) for o in items]


def is_valid(text_len: int, op: TextOp) -> bool:
    """Se ``op`` cabe num documento de ``text_len`` caracteres (sem clamp)."""
    if op.op == "insert":
        return 0 <= op.pos <= text_len and bool(op.text)
    return op.length > 0 and 0 <= op.pos and op.pos + op.length <= text_len


def apply_op(text: str, op: TextOp) -> str:
    """Aplica uma operação. Posições fora do documento são limitadas (clamp)."""
    pos = max(0, min(op.pos, len(text)))
    if op.op == "insert":
        return text[:pos] + op.text + text[pos:]
    if op.op == "delete":
        end = min(pos + op.length, len(text))
        return text[:pos] + text[end:]
    return text


def apply_ops(text: str, ops: list[TextOp]) -> str:
    """Aplica uma mudança (lista de operações) em sequência."""
    for op in ops:
        text = apply_op(text, op)
    return text


def validate_change(text: str, ops: list[TextOp]) -> bool:
    """Valida, sem clamp, que a mudança é aplicável a ``text``."""
    length = len(text)
    for op in ops:
        if not is_valid(length, op):
            return False
        length += len(op.text) if op.op == "insert" else -op.length
    return True


def diff_change(old: str, new: str) -> list[TextOp]:
    """Menor mudança contígua que leva ``old`` a ``new``.

    Usa prefixo/sufixo comuns (O(n), sem difflib): para digitação, colar,
    apagar e desfazer isso gera no máximo uma deleção e uma inserção.
    """
    if old == new:
        return []
    limit = min(len(old), len(new))
    start = 0
    while start < limit and old[start] == new[start]:
        start += 1
    end = 0
    while end < limit - start and old[-1 - end] == new[-1 - end]:
        end += 1
    removed = old[start:len(old) - end]
    added = new[start:len(new) - end]
    ops: list[TextOp] = []
    if removed:
        ops.append(TextOp("delete", start, length=len(removed)))
    if added:
        ops.append(TextOp("insert", start, text=added))
    return ops


def xform(a: TextOp, b: TextOp, a_after: bool) -> list[TextOp]:
    """Transforma ``a`` para valer *depois* de ``b`` (ambas do mesmo estado).

    Args:
        a_after: em empate de inserções na mesma posição, ``a`` é ordenada
            depois de ``b`` (desloca) quando ``True``.

    Returns:
        Lista de 0, 1 ou 2 operações (2 quando uma deleção é dividida).
    """
    if a.op == "insert" and b.op == "insert":
        pos = a.pos
        if a.pos > b.pos or (a.pos == b.pos and a_after):
            pos += len(b.text)
        return [TextOp("insert", pos, text=a.text)]

    if a.op == "insert":
        b_start, b_end = b.pos, b.pos + b.length
        if a.pos <= b_start:
            pos = a.pos
        elif a.pos >= b_end:
            pos = a.pos - b.length
        else:
            pos = b_start
        return [TextOp("insert", pos, text=a.text)]

    if b.op == "insert":
        a_start, a_end = a.pos, a.pos + a.length
        blen = len(b.text)
        if b.pos <= a_start:
            return [TextOp("delete", a.pos + blen, length=a.length)]
        if b.pos >= a_end:
            return [TextOp("delete", a.pos, length=a.length)]
        upper = TextOp("delete", b.pos + blen, length=a_end - b.pos)
        lower = TextOp("delete", a_start, length=b.pos - a_start)
        return [upper, lower]

    a_start, a_end = a.pos, a.pos + a.length
    b_start, b_end = b.pos, b.pos + b.length
    overlap = max(0, min(a_end, b_end) - max(a_start, b_start))
    length = a.length - overlap
    if length <= 0:
        return []
    if a_start >= b_end:
        pos = a_start - b.length
    elif a_start >= b_start:
        pos = b_start
    else:
        pos = a_start
    return [TextOp("delete", pos, length=length)]


def transform(
    a_ops: list[TextOp], b_ops: list[TextOp], a_after: bool
) -> tuple[list[TextOp], list[TextOp]]:
    """Transforma duas mudanças concorrentes uma contra a outra.

    Returns:
        ``(a', b')``: ``a'`` vale depois de ``b`` e ``b'`` vale depois de ``a``;
        ``apply(apply(s, a), b') == apply(apply(s, b), a')``.
    """
    if not a_ops or not b_ops:
        return list(a_ops), list(b_ops)
    if len(a_ops) == 1 and len(b_ops) == 1:
        a, b = a_ops[0], b_ops[0]
        return xform(a, b, a_after), xform(b, a, not a_after)
    if len(a_ops) > 1:
        first, rest = a_ops[:1], a_ops[1:]
        first_t, b_mid = transform(first, b_ops, a_after)
        rest_t, b_out = transform(rest, b_mid, a_after)
        return first_t + rest_t, b_out
    b_first, b_rest = b_ops[:1], b_ops[1:]
    a_mid, b_first_t = transform(a_ops, b_first, a_after)
    a_out, b_rest_t = transform(a_mid, b_rest, a_after)
    return a_out, b_first_t + b_rest_t


class ResyncNeeded(Exception):
    """O cliente precisa receber o documento completo novamente."""


class RoomDoc:
    """Documento autoritativo: uma revisão por mudança aceita."""

    MAX_HISTORY = 5000
    TRIM_TO = 2000

    def __init__(self, content: str = "", max_chars: int = 20_000_000) -> None:
        self.content = content
        self.max_chars = max_chars
        self.history: list[list[TextOp]] = []
        self.offset = 0

    @property
    def rev(self) -> int:
        """Revisão atual (número de mudanças aceitas desde a criação)."""
        return self.offset + len(self.history)

    def receive(self, client_rev: int, ops: list[TextOp]) -> list[TextOp]:
        """Aceita uma mudança baseada na revisão ``client_rev``.

        Returns:
            A mudança já transformada, como foi aplicada ao documento.

        Raises:
            ResyncNeeded: revisão inválida/descartada ou mudança inaplicável.
        """
        if client_rev > self.rev or client_rev < self.offset:
            raise ResyncNeeded("revisão fora do histórico")
        change = list(ops)
        for past in self.history[client_rev - self.offset:]:
            change, _ = transform(change, past, a_after=True)
        if not validate_change(self.content, change):
            raise ResyncNeeded("mudança inaplicável")
        new_content = apply_ops(self.content, change)
        if len(new_content) > self.max_chars:
            raise ResyncNeeded("documento grande demais")
        self.content = new_content
        self.history.append(change)
        if len(self.history) > self.MAX_HISTORY:
            drop = len(self.history) - self.TRIM_TO
            self.history = self.history[drop:]
            self.offset += drop
        return change


class ClientOT:
    """Estado OT de um cliente: revisão, mudança em voo e buffer.

    Todas as chamadas devem ocorrer numa única thread (a da interface).
    """

    def __init__(self, rev: int = 0) -> None:
        self.rev = rev
        self.inflight: list[TextOp] | None = None
        self.buffer: list[TextOp] = []

    def reset(self, rev: int) -> None:
        """Descarta o estado pendente (usado ao receber um ``sync`` completo)."""
        self.rev = rev
        self.inflight = None
        self.buffer = []

    @property
    def pending(self) -> bool:
        """Se há edições locais ainda não confirmadas pelo servidor."""
        return self.inflight is not None

    def local(self, ops: list[TextOp]) -> tuple[int, list[TextOp]] | None:
        """Registra uma edição local; devolve ``(rev, ops)`` se deve enviar já."""
        if not ops:
            return None
        if self.inflight is None:
            self.inflight = list(ops)
            return self.rev, list(ops)
        self.buffer.extend(ops)
        return None

    def remote(self, ops: list[TextOp], rev: int) -> list[TextOp]:
        """Integra uma mudança do servidor; devolve o que aplicar ao editor."""
        incoming = list(ops)
        if self.inflight is not None:
            self.inflight, incoming = transform(self.inflight, incoming, a_after=True)
            if self.buffer:
                self.buffer, incoming = transform(self.buffer, incoming, a_after=True)
        self.rev = rev
        return incoming

    def ack(self, rev: int) -> tuple[int, list[TextOp]] | None:
        """Servidor aceitou a mudança em voo; devolve a próxima a enviar."""
        self.rev = rev
        if self.buffer:
            self.inflight, self.buffer = self.buffer, []
            return self.rev, list(self.inflight)
        self.inflight = None
        return None
