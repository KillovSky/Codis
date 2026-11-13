"""Codis IA - Sessões de conversa (sem dependência de Tkinter).

Problemas que este módulo resolve em relação ao histórico antigo (uma lista de
``{role, content}`` que só crescia):

* **Contexto fresco, histórico enxuto.**  O código anexado (arquivo/seleção/
  menções) vale só para a mensagem em que foi enviado; nos turnos seguintes
  ele vira uma nota ("anexado: a.py") em vez de ser repetido inteiro a cada
  turno - o maior desperdício de tokens do chat.
* **Orçamento de tokens.**  :meth:`ChatSession.messages_for_model` encolhe
  blocos de código antigos, condensa os turnos mais velhos e, em último caso,
  trunca no meio, sempre preservando a mensagem atual e a alternância de
  papéis exigida pelos provedores.
* **Memória de ações.**  O que a IA *fez* (ferramentas) fica registrado no turno
  e é lembrado nas mensagens seguintes.
* **Persistência.**  Várias conversas, salvas de forma atômica.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("Codis.IA.Session")

MODES = ("ask", "edit", "agent")
KEEP_RECENT_TURNS = 6
OLD_CODE_KEEP_LINES = 4
OLD_CODE_MIN_LINES = 12
MAX_SESSIONS = 100

_FENCE = re.compile(r"```([^\n`]*)\n(.*?)```", re.S)


def estimate_tokens(text: str) -> int:
    """Estimativa barata (~3,5 caracteres por token; conservadora para código)."""
    return max(1, int(len(text) / 3.5)) if text else 0


def shrink_code_blocks(text: str, keep: int = OLD_CODE_KEEP_LINES, min_lines: int = OLD_CODE_MIN_LINES) -> str:
    """Reduz blocos de código longos a ``keep`` linhas + marcador de omissão."""

    def repl(m: re.Match[str]) -> str:
        lang, body = m.group(1), m.group(2)
        lines = body.split("\n")
        if len(lines) <= min_lines:
            return m.group(0)
        head = "\n".join(lines[:keep])
        return f"```{lang}\n{head}\n# … {len(lines) - keep} linhas omitidas do histórico …\n```"

    return _FENCE.sub(repl, text)


def middle_truncate(text: str, max_tokens: int) -> str:
    """Mantém início e fim, corta o meio (o início/fim costumam ser o mais útil)."""
    max_chars = int(max_tokens * 3.5)
    if len(text) <= max_chars or max_chars < 200:
        return text if len(text) <= max_chars else text[:max_chars]
    half = (max_chars - 40) // 2
    return f"{text[:half]}\n[… {len(text) - 2 * half} caracteres omitidos …]\n{text[-half:]}"


@dataclass
class Turn:
    """Uma mensagem da conversa."""

    role: str
    content: str
    ts: float = field(default_factory=time.time)
    actions: list[str] = field(default_factory=list)
    attached: list[str] = field(default_factory=list)
    checkpoint: int | None = None
    attachment: str = ""
    lead: str = ""

    def model_text(self, *, with_attachment: bool) -> str:
        if self.role == "user":
            parts = [(self.lead if with_attachment else "") + self.content]
            if with_attachment and self.attachment:
                parts.append("\n\n" + self.attachment)
            elif self.attached:
                parts.append(f"\n\n[anexado antes: {', '.join(self.attached)}]")
            return "".join(parts)
        if self.actions:
            return self.content + "\n\n[Ações executadas neste turno: " + "; ".join(self.actions[:25]) + "]"
        return self.content

    def to_json(self) -> dict[str, Any]:
        d: dict[str, Any] = {"role": self.role, "content": self.content, "ts": self.ts}
        if self.actions:
            d["actions"] = self.actions
        if self.attached:
            d["attached"] = self.attached
        if self.checkpoint is not None:
            d["checkpoint"] = self.checkpoint
        return d

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Turn:
        return cls(role=str(d["role"]), content=str(d.get("content", "")), ts=float(d.get("ts", 0)),
                   actions=[str(a) for a in d.get("actions", [])],
                   attached=[str(a) for a in d.get("attached", [])], checkpoint=d.get("checkpoint"))


@dataclass
class ModelView:
    """Resultado de :meth:`ChatSession.messages_for_model`."""

    messages: list[dict[str, str]]
    tokens: int
    condensed_turns: int = 0
    truncated: bool = False


class ChatSession:
    """Uma conversa com a IA."""

    def __init__(self, workspace: str | None = None, mode: str = "ask") -> None:
        self.id = uuid.uuid4().hex[:10]
        self.title = ""
        self.created = self.updated = time.time()
        self.workspace = workspace
        self.mode = mode if mode in MODES else "ask"
        self.provider = ""
        self.model = ""
        self.turns: list[Turn] = []

    def add_user(self, text: str, *, attachment: str = "", attached: list[str] | None = None,
                 lead: str = "") -> Turn:
        for t in self.turns:
            t.attachment = ""
            t.lead = ""
        turn = Turn("user", text, attachment=attachment, attached=list(attached or []), lead=lead)
        self.turns.append(turn)
        if not self.title:
            self.title = re.sub(r"\s+", " ", text).strip()[:60] or "Nova conversa"
        self._touch()
        return turn

    def add_assistant(self, text: str, actions: list[str] | None = None, checkpoint: int | None = None) -> Turn:
        turn = Turn("assistant", text, actions=list(actions or []), checkpoint=checkpoint)
        self.turns.append(turn)
        self._touch()
        return turn

    def drop_last_exchange(self) -> str | None:
        """Remove a última pergunta (e a resposta, se houver). Retorna o texto do usuário."""
        while self.turns and self.turns[-1].role == "assistant":
            self.turns.pop()
        if self.turns and self.turns[-1].role == "user":
            text = self.turns.pop().content
            self._touch()
            return text
        return None

    def last_user_text(self) -> str | None:
        return next((t.content for t in reversed(self.turns) if t.role == "user"), None)

    def clear(self) -> None:
        self.turns.clear()
        self.title = ""
        self._touch()

    def _touch(self) -> None:
        self.updated = time.time()

    def messages_for_model(self, budget_tokens: int = 24_000, reserve_tokens: int = 0) -> ModelView:
        """Mensagens dentro do orçamento, prontas para ``provider.send``.

        *reserve_tokens* desconta o que já será gasto fora do histórico
        (system prompt + contexto do projeto).
        """
        budget = max(1000, budget_tokens - reserve_tokens)
        turns = [t for t in self.turns if t.content or t.attachment]
        if not turns:
            return ModelView([], 0)
        last_idx = len(turns) - 1
        recent_from = max(0, len(turns) - KEEP_RECENT_TURNS)

        texts: list[tuple[str, str]] = []
        for i, t in enumerate(turns):
            body = t.model_text(with_attachment=(i == last_idx and t.role == "user"))
            if i < recent_from:
                body = shrink_code_blocks(body)
            texts.append((t.role, body))

        def total(items: list[tuple[str, str]]) -> int:
            return sum(estimate_tokens(b) + 4 for _, b in items)

        condensed = 0
        current = texts
        while total(current) > budget and condensed < recent_from:
            condensed += 1
            current = [("user", self._condense_note(turns[:condensed]))] + texts[condensed:]
        texts = current

        truncated = False
        guard = 0
        while total(texts) > budget and guard < 50:
            guard += 1
            idx = max(range(len(texts)), key=lambda k: len(texts[k][1]) if k != len(texts) - 1 else len(texts[k][1]) // 2)
            role, body = texts[idx]
            cur = estimate_tokens(body)
            if cur <= 200:
                break
            texts[idx] = (role, middle_truncate(body, int(cur * 0.6)))
            truncated = True

        msgs = _normalize_roles(texts)
        return ModelView(msgs, sum(estimate_tokens(m["content"]) for m in msgs), condensed, truncated)

    @staticmethod
    def _condense_note(dropped: list[Turn]) -> str:
        topics = []
        for t in dropped:
            if t.role == "user":
                first = re.sub(r"\s+", " ", t.content).strip()[:90]
                if first:
                    topics.append(f"- {first}")
        acts = [a for t in dropped for a in t.actions][:10]
        note = "[Conversa anterior condensada para economizar contexto]\nO usuário pediu antes:\n"
        note += "\n".join(topics[-12:]) or "- (sem detalhes)"
        if acts:
            note += "\nAções já realizadas: " + "; ".join(acts)
        return note

    def to_json(self) -> dict[str, Any]:
        return {"version": 1, "id": self.id, "title": self.title, "created": self.created,
                "updated": self.updated, "workspace": self.workspace, "mode": self.mode,
                "provider": self.provider, "model": self.model,
                "turns": [t.to_json() for t in self.turns]}

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> ChatSession:
        s = cls(d.get("workspace"), d.get("mode", "ask"))
        s.id = str(d["id"])
        s.title = str(d.get("title", ""))
        s.created = float(d.get("created", time.time()))
        s.updated = float(d.get("updated", s.created))
        s.provider = str(d.get("provider", ""))
        s.model = str(d.get("model", ""))
        s.turns = [Turn.from_json(t) for t in d.get("turns", []) if isinstance(t, dict) and "role" in t]
        return s


def _normalize_roles(pairs: list[tuple[str, str]]) -> list[dict[str, str]]:
    """Garante: começa com *user*, papéis alternados (funde vizinhos iguais)."""
    out: list[dict[str, str]] = []
    for role, body in pairs:
        if out and out[-1]["role"] == role:
            out[-1]["content"] += "\n\n" + body
        else:
            out.append({"role": role, "content": body})
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


@dataclass
class SessionInfo:
    id: str
    title: str
    updated: float
    workspace: str | None
    turns: int


class SessionStore:
    """Várias conversas em ``<dir>/<id>.json`` (escrita atômica)."""

    def __init__(self, directory: Path | str) -> None:
        self.dir = Path(directory)

    def _path(self, sid: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{6,32}", sid):
            raise ValueError(f"id de sessão inválido: {sid!r}")
        return self.dir / f"{sid}.json"

    def save(self, session: ChatSession) -> None:
        if not session.turns:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self._path(session.id)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(session.to_json(), ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
        self._prune()

    def load(self, sid: str) -> ChatSession | None:
        try:
            return ChatSession.from_json(json.loads(self._path(sid).read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def delete(self, sid: str) -> bool:
        try:
            self._path(sid).unlink()
            return True
        except (OSError, ValueError):
            return False

    def list(self, workspace: str | None = None) -> list[SessionInfo]:
        """Conversas mais recentes primeiro; as do *workspace* informado vêm antes."""
        infos: list[SessionInfo] = []
        for p in self.dir.glob("*.json") if self.dir.is_dir() else []:
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                infos.append(SessionInfo(str(d["id"]), str(d.get("title", "")), float(d.get("updated", 0)),
                                         d.get("workspace"), len(d.get("turns", []))))
            except (OSError, ValueError, KeyError, TypeError):
                continue
        infos.sort(key=lambda i: (workspace is not None and i.workspace != workspace, -i.updated))
        return infos

    def rename(self, sid: str, title: str) -> bool:
        s = self.load(sid)
        if s is None:
            return False
        s.title = title.strip()[:80] or s.title
        self.save(s)
        return True

    def _prune(self) -> None:
        infos = self.list()
        for info in infos[MAX_SESSIONS:]:
            with contextlib.suppress(Exception):
                self.delete(info.id)
