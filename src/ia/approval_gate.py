"""Codis IA - Portal de Aprovação de Ações (Approval Gate).

Pausa a execução da IA e exige confirmação explícita do usuário antes de
realizar qualquer ação destrutiva ou com efeitos colaterais.

Categorias de risco:
    SAFE     - leitura, busca, análise -> executa sem confirmação
    LOW      - análise pesada -> confirmação opcional
    MEDIUM   - escrita de arquivo, formatação -> pede aprovação
    HIGH     - shell, criar/deletar, git -> bloqueia e aguarda
    CRITICAL - delete recursivo, git reset hard -> alerta vermelho
"""

from __future__ import annotations

import contextlib
import logging
import queue
import time
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum, auto
from tkinter import ttk
from typing import Any

from src.constants import FONT_MONO, FONT_UI
from src.i18n import _t
from src.ui.grab_safe import safe_grab_set

logger = logging.getLogger(__name__)


class ActionRisk(Enum):
    """Nível de risco de uma ação da IA, do mais seguro ao mais destrutivo."""

    SAFE = auto()
    LOW = auto()
    MEDIUM = auto()
    HIGH = auto()
    CRITICAL = auto()


@dataclass
class PendingAction:
    """Ação aguardando decisão do usuário antes de ser executada."""

    id: str
    tool_name: str
    args: dict[str, Any]
    risk: ActionRisk
    description: str
    detail: str = ""
    timestamp: float = field(default_factory=time.time)
    approved: bool | None = None


_TOOL_RISK: dict[str, ActionRisk] = {
    "read_file": ActionRisk.SAFE,
    "read_active_file": ActionRisk.SAFE,
    "get_cursor_context": ActionRisk.SAFE,
    "list_files": ActionRisk.SAFE,
    "read_file_tree": ActionRisk.SAFE,
    "search_in_files": ActionRisk.SAFE,
    "find_definition": ActionRisk.SAFE,
    "find_references": ActionRisk.SAFE,
    "get_project_summary": ActionRisk.SAFE,
    "read_diagnostics": ActionRisk.SAFE,
    "read_symbols": ActionRisk.SAFE,
    "workspace_symbols": ActionRisk.SAFE,
    "read_git_diff": ActionRisk.SAFE,
    "read_git_log": ActionRisk.SAFE,
    "preview_rename": ActionRisk.SAFE,
    "get_env_info": ActionRisk.SAFE,
    "check_dependency": ActionRisk.SAFE,
    "diff_files": ActionRisk.SAFE,
    "get_clipboard": ActionRisk.SAFE,
    "write_file": ActionRisk.MEDIUM,
    "apply_edit": ActionRisk.MEDIUM,
    "insert_after_line": ActionRisk.MEDIUM,
    "format_code": ActionRisk.MEDIUM,
    "patch_files": ActionRisk.MEDIUM,
    "run_command": ActionRisk.HIGH,
    "run_script": ActionRisk.HIGH,
    "run_tests": ActionRisk.HIGH,
    "create_directory": ActionRisk.HIGH,
    "delete_file": ActionRisk.HIGH,
    "move_file": ActionRisk.HIGH,
    "copy_file": ActionRisk.HIGH,
    "init_project": ActionRisk.HIGH,
    "install_dependency": ActionRisk.HIGH,
    "git_commit": ActionRisk.HIGH,
    "git_stage": ActionRisk.HIGH,
    "git_branch": ActionRisk.HIGH,
    "open_terminal_tab": ActionRisk.HIGH,
    "delete_directory": ActionRisk.CRITICAL,
    "git_reset": ActionRisk.CRITICAL,
    "git_clean": ActionRisk.CRITICAL,
}

_RISK_ICON_KEYS = {
    ActionRisk.SAFE: "ia.approval_icon_safe",
    ActionRisk.LOW: "ia.approval_icon_low",
    ActionRisk.MEDIUM: "ia.approval_icon_medium",
    ActionRisk.HIGH: "ia.approval_icon_high",
    ActionRisk.CRITICAL: "ia.approval_icon_critical",
}
_RISK_LABEL_KEYS = {
    ActionRisk.SAFE: "ia.risk_safe",
    ActionRisk.LOW: "ia.risk_low",
    ActionRisk.MEDIUM: "ia.risk_medium",
    ActionRisk.HIGH: "ia.risk_high",
    ActionRisk.CRITICAL: "ia.risk_critical",
}
_RISK_COLORS = {
    ActionRisk.SAFE: "#4caf50",
    ActionRisk.LOW: "#8bc34a",
    ActionRisk.MEDIUM: "#ff9800",
    ActionRisk.HIGH: "#f44336",
    ActionRisk.CRITICAL: "#9c27b0",
}
_RISK_PERM_LABEL_KEYS = {
    ActionRisk.SAFE: "ia.perm_safe_label",
    ActionRisk.LOW: "ia.perm_low_label",
    ActionRisk.MEDIUM: "ia.perm_medium_label",
    ActionRisk.HIGH: "ia.perm_high_label",
    ActionRisk.CRITICAL: "ia.perm_critical_label",
}
_RISK_PERM_DESC_KEYS = {
    ActionRisk.SAFE: "ia.perm_safe_desc",
    ActionRisk.LOW: "ia.perm_low_desc",
    ActionRisk.MEDIUM: "ia.perm_medium_desc",
    ActionRisk.HIGH: "ia.perm_high_desc",
    ActionRisk.CRITICAL: "ia.perm_critical_desc",
}


def classify_action(tool_name: str, args: dict[str, Any]) -> ActionRisk:
    """Classifica o risco de uma ação baseado no nome e argumentos."""
    base = _TOOL_RISK.get(tool_name, ActionRisk.MEDIUM)
    if tool_name == "run_command":
        cmd = args.get("command", "")
        dangerous = [
            "rm -rf",
            "sudo rm",
            "format",
            "mkfs",
            ":(){",
            "dd if=",
            "chmod -R 777",
            "git reset --hard",
            "git clean -fd",
        ]
        if any(d in cmd for d in dangerous):
            return ActionRisk.CRITICAL
    if tool_name == "delete_directory" and args.get("recursive", False):
        return ActionRisk.CRITICAL
    if tool_name in ("write_file", "apply_edit", "patch_files", "insert_after_line", "format_code"):
        paths = [str(args.get("path", ""))]
        paths += [str(p.get("path", "")) for p in args.get("patches") or [] if isinstance(p, dict)]
        sensitive = (".env", ".pem", ".key", "id_rsa", "id_ed25519", "secrets", "credentials", ".netrc")
        if any(marker in path.lower() for path in paths for marker in sensitive):
            return ActionRisk.CRITICAL
    return base


def build_action_description(tool_name: str, args: dict[str, Any]) -> tuple[str, str]:
    """Gera (título, detalhe) legíveis para uma ação, usando i18n."""
    descriptions: dict[str, Callable[[dict], tuple[str, str]]] = {
        "run_command": lambda a: (
            _t("ia.tool_running_label", icon="💻",
               name=a.get("command", "")[:60]),
            f"{_t('ia.tool_running_label', icon='$', name='')}\n{a.get('command', '')}\nTimeout: {a.get('timeout', 30)}s",
        ),
        "write_file": lambda a: (
            _t("ia.tool_write_ok", path=a.get("path", ""), size="?"),
            f"{a.get('path', '')}\n{len(a.get('content', ''))} chars",
        ),
        "apply_edit": lambda a: (
            _t("ia.tool_running_label", icon="✏️", name=a.get("path", "")),
            f"{a.get('path', '')}\n{len(a.get('edits', []))} operação(ões)",
        ),
        "delete_file": lambda a: (
            _t("ia.tool_deleted_file", path=a.get("path", ""), bak="?"),
            f"⚠️ {_t('ia.tool_not_a_file', path=a.get('path', ''))}\n{a.get('path', '')}",
        ),
        "delete_directory": lambda a: (
            _t("ia.tool_deleted_dir", path=a.get("path", "")),
            f"Recursivo: {a.get('recursive', False)}\n⚠️ Todo conteúdo será removido.",
        ),
        "create_directory": lambda a: (
            _t("ia.tool_dir_created", path=a.get("path", "")),
            a.get("path", ""),
        ),
        "move_file": lambda a: (
            _t("ia.tool_moved", src=a.get("src", ""), dst=a.get("dst", "")),
            f"{a.get('src', '')} -> {a.get('dst', '')}",
        ),
        "copy_file": lambda a: (
            _t("ia.tool_copied", src=a.get("src", ""), dst=a.get("dst", "")),
            f"{a.get('src', '')} -> {a.get('dst', '')}",
        ),
        "run_script": lambda a: (
            _t(
                "ia.tool_running_label",
                icon="📜",
                name=f"script ({a.get('language', 'python')})",
            ),
            f"{len(a.get('code', ''))} chars\n{a.get('code', '')[:200]}",
        ),
        "init_project": lambda a: (
            _t(
                "ia.tool_proj_init",
                name=a.get("name", a.get("path", "?")),
                template=a.get("template", ""),
            ),
            f"Template: {a.get('template', '')}\nPasta: {a.get('path', '.')}\nGit: {a.get('git', True)}",
        ),
        "install_dependency": lambda a: (
            _t("ia.tool_running_label", icon="📦", name=a.get("package", "")),
            f"Pacote: {a.get('package', '')}\nManager: {a.get('manager', 'auto')}",
        ),
        "git_commit": lambda a: (
            _t(
                "ia.tool_running_label",
                icon="📝",
                name=f"git commit: {a.get('message', '')[:50]}",
            ),
            f"Mensagem: {a.get('message', '')}\nAdd all: {a.get('add_all', False)}",
        ),
        "git_reset": lambda a: (
            _t(
                "ia.tool_running_label",
                icon="⚠️",
                name=f"git reset --{a.get('mode', 'mixed')}",
            ),
            f"Modo: {a.get('mode', 'mixed')}\nTarget: {a.get('target', 'HEAD')}\n⚠️ Pode perder alterações!",
        ),
        "git_clean": lambda a: (
            _t(
                "ia.tool_running_label",
                icon="⚠️",
                name=f"git clean {a.get('flags', '-fd')}",
            ),
            f"Flags: {a.get('flags', '-fd')}\n⚠️ Remove arquivos não rastreados!",
        ),
        "patch_files": lambda a: (
            _t(
                "ia.tool_running_label",
                icon="🔧",
                name=f"patch {len(a.get('patches', []))} arquivo(s)",
            ),
            "\n".join(
                f"  - {p.get('path', '')}" for p in a.get("patches", [])[:6]),
        ),
        "git_stage": lambda a: (
            _t(
                "ia.tool_running_label", icon="📌", name=f"git add {a.get('path', '.')}"
            ),
            f"Caminho: {a.get('path', '.')}",
        ),
        "git_branch": lambda a: (
            _t(
                "ia.tool_running_label",
                icon="🌿",
                name=f"git branch {a.get('action', '')} {a.get('name', '')}",
            ),
            f"Ação: {a.get('action', '')}\nBranch: {a.get('name', '')}",
        ),
        "open_terminal_tab": lambda a: (
            _t("ia.tool_term_opened", name=a.get("name", "Terminal")),
            f"Comando: {a.get('command', '(nenhum)')}",
        ),
    }
    builder = descriptions.get(tool_name)
    if builder:
        try:
            return builder(args)
        except Exception:
            logger.debug("Suppressed error", exc_info=True)
    args_str = "\n".join(f"  {k}: {str(v)[:100]}" for k, v in args.items())
    return (_t("ia.tool_running_label", icon="⚙️", name=tool_name), args_str)


class ActionHistoryEntry:
    """Registro imutável de uma ação que foi aprovada ou rejeitada."""

    __slots__ = (
        "tool_name",
        "args",
        "risk",
        "description",
        "result",
        "approved",
        "timestamp",
    )

    def __init__(self, tool_name, args, risk, description, result, approved, timestamp):
        """Inicializa a entrada de ação pendente com todos os metadados necessários."""
        self.tool_name = tool_name
        self.args = args
        self.risk = risk
        self.description = description
        self.result = result
        self.approved = approved
        self.timestamp = timestamp


class ApprovalGate:
    """
    Gerencia o fluxo de aprovação de ações da IA.

    Ações SAFE são executadas diretamente; outras bloqueiam a thread da IA
    até que o usuário decida via UI Tkinter.
    """

    def __init__(
        self,
        root: tk.Misc,
        tmgr: Any,
        on_action_log: Callable[[ActionHistoryEntry], None] | None = None,
        auto_approve_safe: bool = True,
    ) -> None:
        """Inicializa o gate com referência ao widget raiz Tkinter e callback de log.

        Args:
            root:             Widget raiz usado para agendar callbacks na thread UI.
            tmgr:             ThemeManager.
            on_action_log:    Callback chamado após cada ação (aprovada ou não).
            auto_approve_safe: Se True, executa ações SAFE sem passar pelo gate.
        """
        self._root = root
        self._tmgr = tmgr
        self._on_action_log = on_action_log
        self._auto_safe = auto_approve_safe
        self._history: list[ActionHistoryEntry] = []
        self._result_queue: queue.Queue[bool] = queue.Queue(maxsize=1)
        self._approval_panel_ref: _ApprovalPanel | None = None

    def attach_panel(self, panel: _ApprovalPanel) -> None:
        """Registra o widget de aprovação que será exibido quando necessário."""
        self._approval_panel_ref = panel

    def request(
        self, tool_name: str, args: dict[str, Any], executor: Callable[[], str]
    ) -> str:
        """Executa a ação com aprovação prévia quando necessário.

        Bloqueia a thread chamante (thread da IA) até que o usuário decida,
        enquanto o event loop Tkinter continua responsivo na thread principal.
        Ações com risco em `auto_approve_levels` são executadas sem interrupção.

        Returns:
            String com o resultado da execução ou mensagem de rejeição.
        """
        risk = classify_action(tool_name, args)
        desc, detail = build_action_description(tool_name, args)

        if self._auto_safe and risk == ActionRisk.SAFE:
            result = self._safe_exec(executor)
            self._log(tool_name, args, risk, desc, result, True)
            return result

        action = PendingAction(
            id=f"{tool_name}_{int(time.time() * 1000)}",
            tool_name=tool_name,
            args=args,
            risk=risk,
            description=desc,
            detail=detail,
        )
        self._root.after(0, lambda: self._show(action))
        approved = self._result_queue.get(block=True, timeout=None)
        result = self._safe_exec(executor) if approved else _t(
            "ia.approval_rejected")
        self._log(tool_name, args, risk, desc, result, approved)
        return result

    def _safe_exec(self, fn: Callable[[], str]) -> str:
        """Chama `fn` capturando exceções e retornando-as como string."""
        try:
            return fn()
        except Exception as exc:
            return f"[Erro] {exc}"

    def _show(self, action: PendingAction) -> None:
        """Exibe o painel de aprovação com os detalhes da ação pendente."""
        if self._approval_panel_ref:
            self._approval_panel_ref.show_request(action, self._on_decision)

    def _on_decision(self, approved: bool) -> None:
        """Recebe a decisão do usuário vinda da UI e desbloqueia a thread da IA."""
        with contextlib.suppress(queue.Full):
            self._result_queue.put_nowait(approved)
        if self._approval_panel_ref:
            self._approval_panel_ref.clear_request()

    def _log(self, tool_name, args, risk, desc, result, approved) -> None:
        """Persiste uma entrada no histórico de ações da sessão atual."""
        entry = ActionHistoryEntry(
            tool_name, args, risk, desc, result, approved, time.time()
        )
        self._history.append(entry)
        if self._on_action_log:
            self._root.after(0, lambda e=entry: self._on_action_log(e))

    @property
    def history(self) -> list[ActionHistoryEntry]:
        """Cópia da lista de ações executadas nesta sessão."""
        return list(self._history)


class _ApprovalPanel(tk.Frame):
    """Painel embutido que exibe pedidos de aprovação da IA."""

    def __init__(self, parent: tk.Widget, tmgr: Any) -> None:
        """Cria o frame do painel; inicia oculto (pack_forget implícito até show_request)."""
        t = tmgr.theme
        super().__init__(parent, bg=t.panel_bg)
        self._tmgr = tmgr
        self._callback: Callable[[bool], None] | None = None
        self._build()

    def _build(self) -> None:
        """Constrói os widgets do painel de aprovação de ações da IA."""

        self._title_bar = tk.Frame(self, bg="#ff9800", height=3)
        self._title_bar.pack(fill="x")

        header = tk.Frame(self, bg="#1a1a2e")
        header.pack(fill="x")

        self._icon_lbl = tk.Label(
            header, text="🔴", bg="#1a1a2e", fg="white", font=(FONT_UI, 11)
        )
        self._icon_lbl.pack(side="left", padx=(6, 2), pady=4)

        self._title_lbl = tk.Label(
            header,
            text=_t("ia.approval_title"),
            bg="#1a1a2e",
            fg="white",
            font=(FONT_UI, 9, "bold"),
        )
        self._title_lbl.pack(side="left")

        self._risk_badge = tk.Label(
            header,
            text=_t("ia.risk_high"),
            bg="#f44336",
            fg="white",
            font=(FONT_UI, 7, "bold"),
            padx=4,
            pady=1,
        )
        self._risk_badge.pack(side="right", padx=6)

        self._desc_lbl = tk.Label(
            self,
            text="",
            bg="#1a1a2e",
            fg="white",
            font=(FONT_UI, 9),
            anchor="w",
            justify="left",
            wraplength=280,
        )
        self._desc_lbl.pack(fill="x", padx=8, pady=(4, 0))

        detail_frame = tk.Frame(
            self, bg="#0d0d1a", highlightthickness=1, highlightbackground="#333"
        )
        detail_frame.pack(fill="x", padx=8, pady=4)

        self._detail_txt = tk.Text(
            detail_frame,
            bg="#0d0d1a",
            fg="#aaaacc",
            font=(FONT_MONO, 8),
            relief="flat",
            bd=0,
            wrap="word",
            height=4,
            state="normal",
            highlightthickness=0,
            padx=6,
            pady=4,
        )
        self._detail_txt.pack(fill="x")

        btn_row = tk.Frame(self, bg="#1a1a2e")
        btn_row.pack(fill="x", padx=8, pady=(0, 6))

        self._btn_reject = tk.Button(
            btn_row,
            text=_t("ia.approval_deny"),
            bg="#c62828",
            fg="white",
            font=(FONT_UI, 9, "bold"),
            relief="flat",
            padx=10,
            pady=4,
            cursor="hand2",
            command=self._reject,
        )
        self._btn_reject.pack(side="left")

        self._btn_approve = tk.Button(
            btn_row,
            text=_t("ia.approval_allow"),
            bg="#2e7d32",
            fg="white",
            font=(FONT_UI, 9, "bold"),
            relief="flat",
            padx=10,
            pady=4,
            cursor="hand2",
            command=self._approve,
        )
        self._btn_approve.pack(side="right")

        self._btn_always = tk.Button(
            btn_row,
            text=_t("ia.approval_allow_always"),
            bg="#1565c0",
            fg="white",
            font=(FONT_UI, 8),
            relief="flat",
            padx=6,
            pady=4,
            cursor="hand2",
            command=self._approve,
        )
        self._btn_always.pack(side="right", padx=4)

        self.bind_all("<Control-Return>", lambda _: self._approve())
        self.bind_all("<Escape>", lambda _: self._reject())

    def show_request(
        self, action: PendingAction, callback: Callable[[bool], None]
    ) -> None:
        """Exibe a solicitação de aprovação e aguarda resposta do usuário via callback."""
        self._callback = callback
        color = _RISK_COLORS.get(action.risk, "#ff9800")
        icon = _t(_RISK_ICON_KEYS.get(action.risk, "ia.approval_icon_high"))
        label = _t(_RISK_LABEL_KEYS.get(action.risk, "ia.risk_high"))

        self._title_bar.config(bg=color)
        self._title_lbl.config(text=_t("ia.approval_title"))
        self._icon_lbl.config(text=icon)
        self._desc_lbl.config(text=action.description)
        self._risk_badge.config(text=label, bg=color)

        self._detail_txt.config(state="normal")
        self._detail_txt.delete("1.0", "end")
        self._detail_txt.insert(
            "1.0", action.detail or _t("ia.approval_desc_empty"))
        self._detail_txt.config(state="disabled")

        self.pack(fill="x", padx=4, pady=2)
        self.lift()

    def clear_request(self) -> None:
        """Limpa o painel de aprovação, restaurando o estado neutro."""
        self._callback = None
        self.pack_forget()

    def _approve(self) -> None:
        """Sinaliza aprovação ao gate e limpa o callback."""
        if cb := self._callback:
            self._callback = None
            cb(True)

    def _reject(self) -> None:
        """Sinaliza rejeição ao gate e limpa o callback."""
        if cb := self._callback:
            self._callback = None
            cb(False)

    def update_locale(self) -> None:
        """Atualiza textos quando o locale muda."""
        self._title_lbl.config(text=_t("ia.approval_title"))
        self._btn_approve.config(text=_t("ia.approval_allow"))
        self._btn_reject.config(text=_t("ia.approval_deny"))
        self._btn_always.config(text=_t("ia.approval_allow_always"))


class ActionHistoryPanel(tk.Frame):
    """Painel de histórico de ações executadas pela IA nesta sessão."""

    def __init__(self, parent: tk.Widget, tmgr: Any) -> None:
        """Constrói o painel de histórico com lista scrollável e botão de limpeza."""
        t = tmgr.theme
        super().__init__(parent, bg=t.panel_bg)
        self._tmgr = tmgr
        self._entries: list[ActionHistoryEntry] = []
        self._build()

    def _build(self) -> None:
        """Constrói o cabeçalho, listbox rolável e label de detalhe."""
        t = self._tmgr.theme
        hdr = tk.Frame(self, bg=t.panel_bg, height=28)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        self._hdr_lbl = tk.Label(
            hdr,
            text=_t("ia.history_title"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8, "bold"),
        )
        self._hdr_lbl.pack(side="left", padx=6)

        self._clear_lbl = tk.Label(
            hdr,
            text=_t("ia.history_clear"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 7),
            cursor="hand2",
        )
        self._clear_lbl.pack(side="right", padx=6)
        self._clear_lbl.bind("<Button-1>", lambda _: self._clear())

        scroll = ttk.Scrollbar(self, orient="vertical")
        scroll.pack(side="right", fill="y")

        self._listbox = tk.Listbox(
            self,
            bg=t.editor_bg,
            fg=t.fg,
            font=(FONT_MONO, 8),
            relief="flat",
            bd=0,
            selectbackground=t.sel_bg,
            yscrollcommand=scroll.set,
            activestyle="none",
            cursor="hand2",
        )
        self._listbox.pack(fill="both", expand=True)
        scroll.config(command=self._listbox.yview)
        self._listbox.bind("<<ListboxSelect>>", self._on_select)

        self._detail_lbl = tk.Label(
            self,
            text="",
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_MONO, 7),
            anchor="w",
            justify="left",
            wraplength=240,
        )
        self._detail_lbl.pack(fill="x", padx=6, pady=(2, 4))

    def add_entry(self, entry: ActionHistoryEntry) -> None:
        """Adiciona uma nova entrada ao histórico e atualiza a visualização."""
        self._entries.append(entry)
        icon = _t(_RISK_ICON_KEYS.get(entry.risk, "ia.approval_icon_high"))
        status = "+" if entry.approved else "x"
        ts = time.strftime("%H:%M:%S", time.localtime(entry.timestamp))
        text = f"{status} {icon} {ts}  {entry.description[:50]}"
        self._listbox.insert("end", text)
        fg = "#4caf50" if entry.approved else "#f44336"
        self._listbox.itemconfig(self._listbox.size() - 1, fg=fg)
        self._listbox.see("end")

    def _on_select(self, _: Any) -> None:
        """Exibe o resultado da entrada selecionada no label de detalhe."""
        sel = self._listbox.curselection()
        if not sel or sel[0] >= len(self._entries):
            return
        entry = self._entries[sel[0]]
        preview = entry.result[:150] + \
            ("..." if len(entry.result) > 150 else "")
        self._detail_lbl.config(text=_t("ia.history_result", result=preview))

    def _clear(self) -> None:
        """Remove todas as entradas do histórico de ações desta sessão."""
        self._entries.clear()
        self._listbox.delete(0, "end")
        self._detail_lbl.config(text="")

    def update_locale(self) -> None:
        """Atualiza textos do painel quando o locale é alterado em runtime."""
        self._hdr_lbl.config(text=_t("ia.history_title"))
        self._clear_lbl.config(text=_t("ia.history_clear"))


class _PermissionsPanel(tk.Toplevel):
    """Painel de configuração de permissões automáticas da IA."""

    def __init__(
        self,
        parent: tk.Widget,
        tmgr: Any,
        auto_levels: set[ActionRisk],
        on_save: Callable[[set[ActionRisk]], None],
    ) -> None:
        """Abre o diálogo modal de permissões; bloqueia até o usuário fechar.

        Args:
            auto_levels: Níveis de risco atualmente aprovados automaticamente.
            on_save:     Callback chamado com o novo conjunto de níveis ao salvar.
        """
        super().__init__(parent)
        t = tmgr.theme
        self.title(_t("ia.perm_title"))
        self.configure(bg=t.panel_bg)
        self.resizable(False, False)
        self.transient(parent)
        safe_grab_set(self)
        self._on_save = on_save

        tk.Label(
            self,
            text=_t("ia.perm_heading"),
            bg=t.panel_bg,
            fg=t.fg,
            font=(FONT_UI, 11, "bold"),
        ).pack(anchor="w", padx=16, pady=(12, 4))
        tk.Label(
            self,
            text=_t("ia.perm_subtitle"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            wraplength=380,
        ).pack(anchor="w", padx=16, pady=(0, 8))
        tk.Frame(self, bg=t.border, height=1).pack(fill="x")

        self._vars: dict[ActionRisk, tk.BooleanVar] = {}
        form = tk.Frame(self, bg=t.panel_bg, padx=16, pady=8)
        form.pack(fill="x")

        for risk in ActionRisk:
            var = tk.BooleanVar(value=risk in auto_levels)
            self._vars[risk] = var
            row = tk.Frame(form, bg=t.panel_bg)
            row.pack(fill="x", pady=3)
            label_key = _RISK_PERM_LABEL_KEYS[risk]
            desc_key = _RISK_PERM_DESC_KEYS[risk]
            tk.Checkbutton(
                row,
                variable=var,
                text=_t(label_key),
                bg=t.panel_bg,
                fg=t.fg,
                activebackground=t.panel_bg,
                selectcolor=t.sel_bg,
                font=(FONT_UI, 9, "bold"),
                cursor="hand2",
            ).pack(anchor="w")
            tk.Label(
                row,
                text=f"    {_t(desc_key)}",
                bg=t.panel_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 8),
                justify="left",
                wraplength=360,
            ).pack(anchor="w")

        tk.Frame(self, bg=t.border, height=1).pack(fill="x")

        preset_row = tk.Frame(self, bg=t.panel_bg, padx=16, pady=6)
        preset_row.pack(fill="x")
        tk.Label(
            preset_row,
            text=_t("ia.perm_preset"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        ).pack(side="left")

        def _preset_readonly():
            for r, v in self._vars.items():
                v.set(r == ActionRisk.SAFE)

        def _preset_trusted():
            for r, v in self._vars.items():
                v.set(r in (ActionRisk.SAFE, ActionRisk.LOW, ActionRisk.MEDIUM))

        def _preset_full():
            for v in self._vars.values():
                v.set(True)

        for key, fn in [
            ("ia.perm_preset_readonly", _preset_readonly),
            ("ia.perm_preset_trusted", _preset_trusted),
            ("ia.perm_preset_full", _preset_full),
        ]:
            tk.Button(
                preset_row,
                text=_t(key),
                command=fn,
                bg=t.btn_bg,
                fg=t.fg,
                relief="flat",
                padx=6,
                pady=2,
                font=(FONT_UI, 8),
                cursor="hand2",
            ).pack(side="left", padx=3)

        btns = tk.Frame(self, bg=t.panel_bg, padx=16, pady=8)
        btns.pack(fill="x")
        tk.Button(
            btns,
            text=_t("ia.perm_cancel"),
            command=self.destroy,
            bg=t.btn_bg,
            fg=t.fg,
            relief="flat",
            padx=10,
            pady=4,
            font=(FONT_UI, 9),
            cursor="hand2",
        ).pack(side="right", padx=4)
        tk.Button(
            btns,
            text=_t("ia.perm_save"),
            command=self._save,
            bg=getattr(t, "accent", "#4fc3f7"),
            fg="#ffffff",
            relief="flat",
            padx=10,
            pady=4,
            font=(FONT_UI, 9, "bold"),
            cursor="hand2",
        ).pack(side="right")

        self.update_idletasks()
        pw = parent.winfo_rootx() + parent.winfo_width() // 2
        ph = parent.winfo_rooty() + parent.winfo_height() // 2
        self.geometry(
            f"+{pw - self.winfo_width() // 2}+{ph - self.winfo_height() // 2}"
        )
        self.wait_window()

    def _save(self) -> None:
        """Coleta os checkboxes marcados, chama on_save e fecha o diálogo."""
        approved = {r for r, v in self._vars.items() if v.get()}
        self._on_save(approved)
        self.destroy()
