"""Codis IA - Pair programming (sem dependência de Tkinter).

Peças que fazem a IA trabalhar *com* quem está codando:

* **Menções** - ``@arquivo``, ``@src/app.py``, ``@selection``, ``@tabs``,
  ``@diagnostics``, ``@git``, ``@tree`` no texto anexam o contexto certo, com
  orçamento de tokens e a mesma proteção de caminhos do ``ToolExecutor``.
* **EditTracker** - guarda o que estava aberto no último turno da IA e, na
  próxima mensagem, informa *o que o usuário mudou desde então* (diff), para a
  IA enxergar seu trabalho ao vivo em vez de operar sobre uma foto velha.
* **Modos** - ``ask`` (só lê), ``edit`` (pair: edições cirúrgicas, respeitando o
  que está sendo digitado) e ``agent`` (autônomo), com prompts de sistema e
  conjuntos de ferramentas distintos.
"""

from __future__ import annotations

import difflib
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.ia.session import estimate_tokens, middle_truncate

logger = logging.getLogger("Codis.IA.Pair")

_KEYWORDS = ("selection", "file", "active", "tabs", "diagnostics", "git", "tree")
_MENTION_RE = re.compile(r"(?<![\w.])@(file:)?([A-Za-z0-9_./\\~-]+)")

_SENSITIVE_NAMES = frozenset({".env", ".netrc", ".pgpass", ".npmrc", ".pypirc", ".git-credentials",
                              "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa", "authorized_keys", ".gitconfig"})
_SENSITIVE_SUFFIXES = frozenset({".pem", ".key", ".p12", ".pfx", ".kdbx"})
MAX_FILE_BYTES = 400_000


def is_sensitive(path: Path) -> bool:
    n = path.name.lower()
    return n in _SENSITIVE_NAMES or n.startswith(".env.") or path.suffix.lower() in _SENSITIVE_SUFFIXES


@dataclass
class ContextSources:
    """Acesso do painel ao estado do editor. Todos opcionais/tolerantes a falha."""

    workspace: Callable[[], Path | None] = lambda: None
    active_file: Callable[[], tuple[Path | None, str, str] | None] = lambda: None
    selection: Callable[[], tuple[Path | None, str, int] | None] = lambda: None
    open_tabs: Callable[[], list[tuple[Path | None, bool]]] = lambda: []
    diagnostics: Callable[[], str] = lambda: ""
    git_diff: Callable[[], str] = lambda: ""
    file_tree: Callable[[], str] = lambda: ""
    open_buffers: Callable[[], dict[str, str]] = lambda: {}


@dataclass
class Mention:
    kind: str
    arg: str = ""
    raw: str = ""


@dataclass
class Resolved:
    """Resultado de :func:`resolve_mentions`."""

    text: str
    attachment: str = ""
    names: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def find_mentions(text: str) -> list[Mention]:
    """Extrai menções reconhecíveis (palavras-chave ou caminhos com ``/`` ou ``.``)."""
    out: list[Mention] = []
    seen: set[str] = set()
    for m in _MENTION_RE.finditer(text):
        explicit, token = m.group(1), m.group(2).rstrip(".,;:!?)")
        if not token or m.group(0) in seen:
            continue
        low = token.lower()
        if not explicit and low in _KEYWORDS:
            kind = "active" if low in ("file", "active") else low
            out.append(Mention(kind, "", m.group(0)))
        elif explicit or "/" in token or "\\" in token or "." in token:
            out.append(Mention("file", token, m.group(0)))
        else:
            continue
        seen.add(m.group(0))
    return out


def _numbered(text: str, first: int = 1) -> str:
    return "\n".join(f"{i:4d}  {ln}" for i, ln in enumerate(text.split("\n"), first))


def _block(title: str, body: str, lang: str = "", budget: int = 4000, numbered: bool = False, first: int = 1) -> str:
    body = middle_truncate(body, budget)
    if numbered:
        body = _numbered(body, first)
    return f"### {title}\n```{lang}\n{body}\n```"


def resolve_mentions(text: str, src: ContextSources, budget_tokens: int = 12_000) -> Resolved:
    """Resolve as menções de *text* em blocos de contexto dentro do orçamento."""
    mentions = find_mentions(text)
    res = Resolved(text=text)
    if not mentions:
        return res
    ws = src.workspace()
    per = max(400, budget_tokens // max(1, len(mentions)))
    blocks: list[str] = []

    for m in mentions:
        try:
            if m.kind == "active":
                af = src.active_file()
                if af:
                    path, lang, content = af
                    nm = path.name if path else "(sem nome)"
                    blocks.append(_block(f"@file: {nm} ({lang})", content, lang, per, True))
                    res.names.append(nm)
                else:
                    res.warnings.append("@file: nenhum arquivo ativo")
            elif m.kind == "selection":
                sel = src.selection()
                if sel and sel[1].strip():
                    path, body, first = sel
                    nm = f"seleção de {path.name}" if path else "seleção"
                    blocks.append(_block(f"@selection: {nm} (a partir da linha {first})", body, "", per, True, first))
                    res.names.append(nm)
                else:
                    res.warnings.append("@selection: nada selecionado")
            elif m.kind == "tabs":
                tabs = src.open_tabs()
                lines = [f"- {p.name if p else '(sem nome)'}{'  [alterações não salvas]' if mod else ''}"
                         for p, mod in tabs[:40]]
                blocks.append("### @tabs: arquivos abertos\n" + ("\n".join(lines) or "(nenhum)"))
                res.names.append("abas abertas")
            elif m.kind == "diagnostics":
                d = src.diagnostics()
                blocks.append(_block("@diagnostics: erros e avisos", d or "(sem diagnósticos)", "", per))
                res.names.append("diagnósticos")
            elif m.kind == "git":
                d = src.git_diff()
                blocks.append(_block("@git: alterações não commitadas", d or "(sem alterações)", "diff", per))
                res.names.append("git diff")
            elif m.kind == "tree":
                blocks.append(_block("@tree: estrutura do projeto", src.file_tree() or "(vazio)", "", per))
                res.names.append("árvore")
            elif m.kind == "file":
                blk = _file_block(m.arg, ws, per, src)
                if blk is None:
                    res.warnings.append(f"{m.raw}: arquivo não encontrado ou não permitido")
                else:
                    blocks.append(blk[0])
                    res.names.append(blk[1])
        except Exception as exc:  # noqa: BLE001
            logger.debug("menção %s falhou: %s", m.raw, exc, exc_info=True)
            res.warnings.append(f"{m.raw}: erro ao ler")
    res.attachment = "\n\n".join(blocks)
    if estimate_tokens(res.attachment) > budget_tokens:
        res.attachment = middle_truncate(res.attachment, budget_tokens)
    return res


def _file_block(arg: str, ws: Path | None, budget: int, src: ContextSources) -> tuple[str, str] | None:
    """Bloco de um arquivo mencionado - restrito ao workspace e sem credenciais."""
    if ws is None:
        return None
    base = ws.resolve()
    try:
        target = (base / arg.replace("\\", "/")).resolve()
    except (OSError, RuntimeError):
        return None
    if base not in target.parents and target != base:
        return None
    if not target.is_file() or is_sensitive(target):
        return None
    try:
        rel = target.relative_to(base).as_posix()
        buffers = src.open_buffers()
        content = buffers.get(str(target))
        if content is None:
            if target.stat().st_size > MAX_FILE_BYTES:
                return None
            content = target.read_text("utf-8", errors="replace")
    except OSError:
        return None
    lang = target.suffix.lstrip(".")
    return _block(f"@{rel}", content, lang, budget, True), rel


class EditTracker:
    """Detecta edições do usuário entre duas mensagens à IA."""

    def __init__(self) -> None:
        self._snap: dict[str, str] = {}

    def snapshot(self, files: dict[str, str]) -> None:
        """Registra o estado atual (chamar ao fim de cada turno da IA)."""
        self._snap = dict(files)

    def reset(self) -> None:
        self._snap = {}

    def changes_since(self, files: dict[str, str], max_chars: int = 6000, context: int = 2) -> str:
        """Diff unificado dos arquivos (já vistos) que mudaram. Vazio se nada mudou."""
        parts: list[str] = []
        for path, old in self._snap.items():
            new = files.get(path)
            if new is None or new == old:
                continue
            diff = list(difflib.unified_diff(
                old.splitlines(), new.splitlines(), fromfile=f"a/{Path(path).name}",
                tofile=f"b/{Path(path).name}", lineterm="", n=context))
            if diff:
                parts.append("\n".join(diff))
        if not parts:
            return ""
        body = "\n\n".join(parts)
        if len(body) > max_chars:
            body = body[: max_chars // 2] + "\n[… diff truncado …]\n" + body[-max_chars // 2:]
        return "### Alterações que o usuário fez desde sua última resposta\n```diff\n" + body + "\n```"


EDIT_TOOLS = frozenset({"write_file", "apply_edit", "insert_after_line", "format_code", "patch_files",
                        "create_directory", "run_tests"})

_COMMON = (
    "You are an expert software engineer working inside the Codis IDE, pair-programming with the user.\n"
    "Answer in the user's language. Be concise; show code, not essays.\n"
    "The user's editor may contain unsaved work that only exists in their buffer: never assume the "
    "disk copy is current. If a tool reports a file has unsaved changes, ask the user to save it.\n"
    "When referring to code use `path:line`. Never invent files, APIs or results: read first, then answer.\n"
)

MODE_PROMPTS: dict[str, str] = {
    "ask": _COMMON + (
        "\n## Mode: ASK (read-only)\n"
        "You can read and search the project but you CANNOT modify anything. Explain, review and suggest; "
        "put proposed code in fenced blocks so the user can apply it with the diff dialog.\n"
    ),
    "edit": _COMMON + (
        "\n## Mode: EDIT (pair programming)\n"
        "You and the user are editing this project together, live.\n"
        "- Make the smallest change that solves the request; preserve the user's style, naming and structure.\n"
        "- Prefer `apply_edit`/`patch_files` over rewriting files; use `write_file` only for new files.\n"
        "- Before editing, read the current content (the user may have changed it). One short line on what "
        "you will change, then do it; afterwards list what changed in 1-3 bullets.\n"
        "- Do not touch code outside the request. Do not reformat unrelated lines.\n"
        "- Ask ONE clarifying question when the request is genuinely ambiguous instead of guessing.\n"
        "- Every change this turn can be undone by the user with one click; still be careful.\n"
    ),
    "agent": _COMMON + (
        "\n## Mode: AGENT (autonomous)\n"
        "Break the task into steps, execute them with the available tools, verify each step "
        "(run tests/diagnostics) and keep going until the task is complete or you are blocked.\n"
        "- State the plan in a few lines first, then work; report progress briefly.\n"
        "- Prefer small verified steps; fix failures you cause. Stop and ask before anything destructive.\n"
        "- Finish with a summary: what changed, what was verified, what remains.\n"
    ),
}


def tool_name(entry: dict[str, Any]) -> str:
    return str(entry.get("function", entry).get("name", ""))


def tools_for_mode(mode: str, schema: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filtra as ferramentas conforme o modo (ask = somente leitura)."""
    from src.ia.approval_gate import ActionRisk, classify_action

    if mode == "agent":
        return list(schema)
    out = []
    for entry in schema:
        name = tool_name(entry)
        risk = classify_action(name, {})
        if risk in (ActionRisk.SAFE, ActionRisk.LOW) or (mode == "edit" and name in EDIT_TOOLS):
            out.append(entry)
    return out


def build_system_prompt(mode: str, *, user_system: str = "", skill_prompt: str = "", project_context: str = "") -> str:
    """Compõe: prompt do modo → instruções do usuário → skill → contexto do projeto."""
    parts = [MODE_PROMPTS.get(mode, MODE_PROMPTS["ask"]).rstrip()]
    if user_system.strip():
        parts.append("## User instructions\n" + user_system.strip())
    if skill_prompt.strip():
        parts.append("## Task guidance\n" + skill_prompt.strip())
    if project_context.strip():
        parts.append(project_context.strip())
    return "\n\n".join(parts)
