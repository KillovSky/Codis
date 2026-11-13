"""Codis IA - PainelIA: Assistente de IA com controle total sobre o workspace.

Funcionalidades
---------------
* Seletor de provedor e modelo
* Contexto de código: arquivo ativo, seleção, trecho manual
* Ações rápidas via skills
* Aprovação granular de ações (read/write/exec/critical)
* Painel de aprovação bloqueante: IA pausa e aguarda decisão do usuário
* Modo agente: IA planeja e executa tarefas multi-passo autonomamente
* 38+ ferramentas (leitura, escrita, exec, git, init de projeto, install deps)
* Copiar resposta / inserir no editor com diálogo de diff
* Streaming com indicador de carregamento e cancelamento
* Configuração inline de provedores (chave, modelo, URL)
* Abrir aba de terminal diretamente da IA
"""

from __future__ import annotations

import contextlib
import inspect
import json
import logging
import threading
import time
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any

from src.constants import CONFIG_DIR, FONT_MONO, FONT_UI
from src.i18n import _t
from src.ia.actions import list_skills
from src.ia.approval_gate import (
    ActionHistoryEntry,
    ActionHistoryPanel,
    ActionRisk,
    ApprovalGate,
    _ApprovalPanel,
    classify_action,
)
from src.ia.context_builder import build_context_block
from src.ia.dialdiff import confirm_and_apply
from src.ia.pair import (
    ContextSources,
    EditTracker,
    build_system_prompt,
    resolve_mentions,
    tools_for_mode,
)
from src.ia.providers import (
    PROVIDERS,
    WARNING_LOCAL_LIB,
    BaseProvider,
    LocalLibProvider,
    OpenAICompatibleProvider,
    create_provider,
)
from src.ia.session import MODES, ChatSession, SessionStore, estimate_tokens, middle_truncate
from src.ia.toolbox import (
    TOOL_SCHEMA,
    ToolExecutor,
    extract_code_blocks,
)
from src.ui.grab_safe import safe_grab_set

logger = logging.getLogger("Codis.IA")

_SKILLS_ID_ORDER = [
    "ask",
    "generate",
    "explain",
    "fix",
    "refactor",
    "review",
    "document",
    "tests",
    "optimize",
    "translate",
    "commit",
    "security",
    "sql",
    "regex",
    "architecture",
]


def _get_quick_actions() -> list[tuple[str, str]]:
    """Retorna lista de (chave_i18n, prefixo_de_prompt) para o seletor de ação rápida."""
    skills_by_id = {s.id: s for s in list_skills()}
    items: list[tuple[str, str]] = []
    for sid in _SKILLS_ID_ORDER:
        skill = skills_by_id.get(sid)
        if skill:
            items.append((skill.i18n_key, skill.prompt_prefix))
    for skill in list_skills():
        if skill.id not in _SKILLS_ID_ORDER:
            items.append((skill.i18n_key, skill.prompt_prefix))
    return items or [("ia.skill_ask", "")]

_CONTEXT_OPTIONS: list[tuple[str, str]] = [
    ("ia.ctx_none", "none"),
    ("ia.ctx_file", "file"),
    ("ia.ctx_selection", "selection"),
    ("ia.ctx_manual", "manual"),
]

_DEFAULT_SYSTEM_PROMPT = (
    "You are an expert software engineering assistant integrated into Codis IDE. "
    "You have access to powerful tools to read, write, and execute code in the user's workspace. "
    "\n\n"
    "## Core Guidelines\n"
    "- Always read the relevant code before suggesting changes\n"
    "- Use apply_edit for surgical changes; use write_file only for new files or full rewrites\n"
    "- Use patch_files when changing multiple files simultaneously\n"
    "- Before running commands, explain what they do\n"
    "- When asked to build a project from scratch, use init_project first, then scaffold the code\n"
    "- Prefer incremental changes with clear checkpoints\n"
    "- For any destructive action (delete, reset), confirm explicitly with the user\n"
    "\n\n"
    "## Tool Usage\n"
    "- Use get_cursor_context when the user says 'here', 'this', 'at my cursor'\n"
    "- Use get_project_summary when opening a new project\n"
    "- Use read_file_tree to understand project structure\n"
    "- Use check_dependency before installing to avoid duplicates\n"
    "- Use preview_rename before any large refactor\n"
    "- Use run_tests after making code changes\n"
    "\n\n"
    "## Agent Mode\n"
    "When given a complex task, break it into steps, execute each step, and report progress. "
    "Always verify each step succeeded before moving to the next."
)


class _MessageBubble(tk.Frame):
    """Exibe uma mensagem do chat como bolha visual."""

    def __init__(
        self,
        parent: tk.Widget,
        tmgr: Any,
        role: str,
        content: str,
        on_insert: Callable[[str], None] | None = None,
        on_copy: Callable[[str], None] | None = None,
    ) -> None:
        """Cria a bolha com cabeçalho de role, corpo de texto e botões de ação.

        Args:
            role:      'user' ou 'assistant'.
            content:   Texto da mensagem.
            on_insert: Callback para inserir código no editor ativo.
            on_copy:   Callback para copiar texto para o clipboard.
        """
        t = tmgr.theme
        is_user = role == "user"
        bg = getattr(t, "sel_bg", t.input_bg) if is_user else t.panel_bg
        super().__init__(parent, bg=t.editor_bg, pady=3)
        self._tmgr = tmgr
        self._content = content

        header = tk.Frame(self, bg=t.editor_bg)
        header.pack(fill="x", padx=8)
        icon = ">" if is_user else "[AI]"
        label = _t("ia.you") if is_user else _t("ia.assistant")
        tk.Label(
            header,
            text=f"{icon} {label}",
            bg=t.editor_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8, "bold"),
        ).pack(side="left")

        if not is_user and on_copy:
            btn = tk.Label(
                header,
                text=_t("ia.copy"),
                bg=t.editor_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 7),
                cursor="hand2",
            )
            btn.pack(side="right", padx=2)
            btn.bind("<Button-1>", lambda _: on_copy(self._content))

        if not is_user and on_insert:
            btn = tk.Label(
                header,
                text=_t("ia.insert"),
                bg=t.editor_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 7),
                cursor="hand2",
            )
            btn.pack(side="right", padx=2)
            btn.bind("<Button-1>", lambda _: on_insert(self._content))

        body = tk.Frame(self, bg=bg, highlightthickness=1,
                        highlightbackground=t.border)
        body.pack(fill="x", padx=8, pady=(2, 0))
        txt = tk.Text(
            body,
            bg=bg,
            fg=t.fg,
            font=(FONT_MONO, 9),
            relief="flat",
            bd=0,
            wrap="word",
            state="normal",
            highlightthickness=0,
            padx=8,
            pady=6,
            cursor="arrow",
        )
        txt.insert("1.0", content)
        txt.config(state="disabled", height=min(content.count("\n") + 2, 30))
        txt.pack(fill="x")

    def update_content(self, new_content: str) -> None:
        """Substitui o texto exibido na bolha; usado durante streaming."""
        self._content = new_content
        for widget in self.winfo_children():
            if isinstance(widget, tk.Frame):
                for child in widget.winfo_children():
                    if isinstance(child, tk.Text):
                        child.config(state="normal")
                        child.delete("1.0", "end")
                        child.insert("1.0", new_content)
                        child.config(
                            height=min(new_content.count("\n") + 2, 30),
                            state="disabled",
                        )
                        return


class _ProviderConfigDialog(tk.Toplevel):
    """Janela modal para configurar credenciais e parâmetros de um provedor."""

    def __init__(
        self,
        parent: tk.Widget,
        tmgr: Any,
        provider_name: str,
        current_cfg: dict[str, Any],
        on_save: Callable[[dict[str, Any]], None],
    ) -> None:
        """Abre o diálogo modal de configuração de provedor e bloqueia até fechar.

        Args:
            provider_name: Nome do provedor exibido no título.
            current_cfg:   Configuração já persistida (pré-popula os campos).
            on_save:       Callback com dict de configuração ao salvar.
        """
        super().__init__(parent)
        t = tmgr.theme
        self._tmgr = tmgr
        self._on_save = on_save
        self._fields: dict[str, tk.StringVar] = {}

        self.title(_t("ia.cfg_title", name=provider_name))
        self.configure(bg=t.panel_bg)
        self.resizable(False, False)
        self.transient(parent)
        safe_grab_set(self)

        cls = next((c for c in PROVIDERS if c.name == provider_name), None)

        tk.Label(
            self,
            text=f"  {provider_name}  ",
            bg=t.panel_bg,
            fg=t.fg,
            font=(FONT_UI, 11, "bold"),
        ).pack(anchor="w", padx=12, pady=(10, 2))
        if cls:
            tk.Label(
                self,
                text=cls.description,
                bg=t.panel_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 8),
                wraplength=380,
                justify="left",
            ).pack(anchor="w", padx=12, pady=(0, 8))
        tk.Frame(self, bg=t.border, height=1).pack(fill="x")

        form = tk.Frame(self, bg=t.panel_bg, padx=12, pady=8)
        form.pack(fill="x")

        if cls == LocalLibProvider:
            fields_def = [
                ("caminho_modelo", "ia.cfg_gguf", "", False),
                ("n_gpu_layers", "ia.cfg_gpu_layers", "0", False),
                ("n_ctx", "ia.cfg_ctx", "4096", False),
                ("max_tokens", "ia.cfg_max_gen", "2048", False),
            ]
        elif cls and "Ollama" in cls.name:
            fields_def = [
                ("url", "ia.cfg_url", "http://localhost:11434/api/chat", False),
                ("modelo", "ia.cfg_model", "llama3", False),
            ]
        elif cls == OpenAICompatibleProvider:
            fields_def = [
                (
                    "url",
                    "ia.cfg_url",
                    "http://localhost:1234/v1/chat/completions",
                    False,
                ),
                ("chave_api", "ia.cfg_api_optional", "", False),
                ("modelo", "ia.cfg_model_optional", "", False),
                ("max_tokens", "ia.cfg_max_tokens", "4096", False),
                ("sistema", "ia.cfg_system", _DEFAULT_SYSTEM_PROMPT, False),
            ]
        else:
            fields_def = [
                ("chave_api", "ia.cfg_api_key", "", True),
                ("modelo", "ia.cfg_model", "", False),
                ("max_tokens", "ia.cfg_max_tokens", "4096", False),
                ("sistema", "ia.cfg_system", _DEFAULT_SYSTEM_PROMPT, False),
            ]
            if cls and "OpenAI" in cls.name:
                fields_def.insert(
                    1,
                    (
                        "url_base",
                        "ia.cfg_url_base",
                        "https://api.openai.com/v1/chat/completions",
                        False,
                    ),
                )

        for field_key, label_key, default_val, is_password in fields_def:
            tk.Label(
                form,
                text=_t(label_key) + ":",
                bg=t.panel_bg,
                fg=t.fg,
                font=(FONT_UI, 9),
                anchor="w",
            ).pack(anchor="w")
            var = tk.StringVar(value=current_cfg.get(field_key, default_val))
            self._fields[field_key] = var
            entry = tk.Entry(
                form,
                textvariable=var,
                show="*" if is_password else "",
                bg=t.input_bg,
                fg=t.fg,
                insertbackground=t.cursor,
                relief="flat",
                bd=0,
                width=46,
                font=(FONT_MONO if is_password else FONT_UI, 9),
                highlightthickness=1,
                highlightbackground=t.border,
            )
            entry.pack(fill="x", pady=(0, 6))
            if field_key == "caminho_modelo":

                def _browse(v: tk.StringVar = var) -> None:
                    path = filedialog.askopenfilename(
                        title=_t("ia.cfg_browse_title"),
                        filetypes=[("GGUF", "*.gguf"), ("Todos", "*.*")],
                        parent=self,
                    )
                    if path:
                        v.set(path)

                lbl = tk.Label(
                    form,
                    text=_t("ia.cfg_browse"),
                    bg=t.panel_bg,
                    fg=t.accent,
                    font=(FONT_UI, 8),
                    cursor="hand2",
                )
                lbl.pack(anchor="w", pady=(0, 6))
                lbl.bind("<Button-1>", lambda _, b=_browse: b())

        tk.Frame(self, bg=t.border, height=1).pack(fill="x")
        btns = tk.Frame(self, bg=t.panel_bg, padx=12, pady=8)
        btns.pack(fill="x")
        tk.Button(
            btns,
            text=_t("ia.cfg_cancel"),
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
            text=_t("ia.cfg_save"),
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
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry(f"+{pw - w // 2}+{ph - h // 2}")
        self.wait_window()

    def _save(self) -> None:
        """Coleta os valores dos campos, chama on_save e fecha o diálogo."""
        self._on_save({k: v.get() for k, v in self._fields.items()})
        self.destroy()


class _PermissionsPanel(tk.Toplevel):
    """Diálogo modal de configuração de níveis de risco aprovados automaticamente pela IA."""

    _RISK_KEY_MAP = {
        ActionRisk.SAFE: ("ia.perm_safe_label", "ia.perm_safe_desc"),
        ActionRisk.LOW: ("ia.perm_low_label", "ia.perm_low_desc"),
        ActionRisk.MEDIUM: ("ia.perm_medium_label", "ia.perm_medium_desc"),
        ActionRisk.HIGH: ("ia.perm_high_label", "ia.perm_high_desc"),
        ActionRisk.CRITICAL: ("ia.perm_critical_label", "ia.perm_critical_desc"),
    }

    def __init__(
        self,
        parent: tk.Widget,
        tmgr: Any,
        auto_levels: set[ActionRisk],
        on_save: Callable[[set[ActionRisk]], None],
    ) -> None:
        """Abre o diálogo modal de permissões e bloqueia até o usuário fechar.

        Args:
            auto_levels: Conjunto de ActionRisk aprovados automaticamente no momento.
            on_save:     Callback com o novo conjunto de níveis ao salvar.
        """
        super().__init__(parent)
        t = tmgr.theme
        self.title(_t("ia.perm_title"))
        self.configure(bg=t.panel_bg)
        self.resizable(False, False)
        self.transient(parent)
        safe_grab_set(self)

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
        for risk, (label_key, desc_key) in self._RISK_KEY_MAP.items():
            var = tk.BooleanVar(value=risk in auto_levels)
            self._vars[risk] = var
            row = tk.Frame(form, bg=t.panel_bg)
            row.pack(fill="x", pady=3)
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

        def _preset_readonly() -> None:
            for risk, var in self._vars.items():
                var.set(risk == ActionRisk.SAFE)

        def _preset_trusted() -> None:
            for risk, var in self._vars.items():
                var.set(risk in (ActionRisk.SAFE,
                        ActionRisk.LOW, ActionRisk.MEDIUM))

        def _preset_full_auto() -> None:
            for _, var in self._vars.items():
                var.set(True)

        for key, fn in [
            ("ia.perm_preset_readonly", _preset_readonly),
            ("ia.perm_preset_trusted", _preset_trusted),
            ("ia.perm_preset_full", _preset_full_auto),
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
        self._on_save = on_save
        self.wait_window()

    def _save(self) -> None:
        """Coleta checkboxes marcados, chama on_save e fecha o diálogo."""
        approved = {risk for risk, var in self._vars.items() if var.get()}
        self._on_save(approved)
        self.destroy()


class PainelIA(tk.Frame):
    """
    Painel lateral de assistente de IA integrado ao Codis.

    Integra-se a barra de atividades via ActivityBar.
    Suporta múltiplos provedores, aprovação granular de ações,
    modo agente e 38+ ferramentas.

    Uso:
        painel = PainelIA(
            parent,
            tmgr             = self._tmgr,
            cfg              = self._cfg,
            get_editor       = self._active_editor,
            on_open_new_tab  = self._criar_aba_com_texto,
            get_workspace    = lambda: self._workspace,
            on_open_terminal = self._open_terminal_tab,
        )
    """

    def __init__(
        self,
        parent: tk.Widget,
        tmgr: Any,
        cfg: Any,
        get_editor: Callable[[], Any] | None = None,
        on_open_new_tab: Callable[[str], None] | None = None,
        get_workspace: Callable[[], Any] | None = None,
        on_open_terminal: Callable[[str, str], None] | None = None,
        get_open_editors: Callable[[], Any] | None = None,
    ) -> None:
        """Inicializa o painel de IA com todas as dependências e constrói a UI.

        Args:
            tmgr:             ThemeManager.
            cfg:              ConfigManager para persistir preferências.
            get_editor:       Callable que retorna o editor ativo.
            on_open_new_tab:  Callback para abrir novo arquivo no editor.
            get_workspace:    Callable que retorna o path do workspace.
            on_open_terminal: Callback para abrir aba no terminal integrado.
            get_open_editors: Callable que retorna todos os editores abertos
                (habilita @tabs, rastreio das suas edições e proteção de
                alterações não salvas).
        """
        t = tmgr.theme
        super().__init__(parent, bg=t.panel_bg)
        self._tmgr = tmgr
        self._cfg = cfg
        self._get_editor = get_editor
        self._on_new_tab = on_open_new_tab
        self._get_workspace = get_workspace
        self._get_open_editors = get_open_editors

        _saved_mode = self._cfg.get("ia_mode", "edit")
        self._mode = tk.StringVar(value=_saved_mode if _saved_mode in MODES else "edit")
        self._store = SessionStore(CONFIG_DIR / "ia_sessions")
        self._session = ChatSession(self._ws_key(), self._mode.get())
        self._tracker = EditTracker()
        self._turn_id = 0
        self._turn_actions: list[str] = []
        self._turn_editing = False
        self._stream_shown = ""
        self._provider: BaseProvider | None = None
        self._provider_name = tk.StringVar()
        self._generating = False
        self._current_bubble: _MessageBubble | None = None
        self._stream_buffer = ""
        self._last_ai_text = ""
        self._provider_cfgs: dict[str, dict[str, Any]
                                  ] = self._load_provider_cfgs()
        self._tools_enabled = tk.BooleanVar(value=True)

        saved_auto: list[str] = self._cfg.get(
            "ia_auto_approve_levels", ["SAFE"])
        self._auto_approve_levels: set[ActionRisk] = {
            ActionRisk[n] for n in saved_auto if n in ActionRisk.__members__
        }

        self._gate = ApprovalGate(
            root=self,
            tmgr=tmgr,
            on_action_log=self._on_action_logged,
            auto_approve_safe=True,
        )

        self._tool_executor = ToolExecutor(
            get_editor=get_editor,
            get_workspace=get_workspace or (lambda: None),
            on_open_terminal=on_open_terminal,
            get_root_widget=lambda: self,
            get_open_editors=self._editors,
        )
        self._tool_executor.set_session(self._session.id)

        self._build()
        self._mode.trace_add("write", lambda *_: self._on_mode_change())
        self._select_provider(self._provider_name.get())
        self._update_meter()
        self._update_undo_btn()

    def _load_provider_cfgs(self) -> dict[str, dict[str, Any]]:
        """Carrega as configurações de todos os providers a partir do arquivo de config."""
        return self._cfg.get("ia_provedores_cfg", {})

    def _save_provider_cfgs(self) -> None:
        """Persiste as configurações de provedores no ConfigManager."""
        self._cfg.set("ia_provedores_cfg", self._provider_cfgs)
        self._cfg.save()

    def _build(self) -> None:
        """Constrói os widgets do painel de IA: cabeçalho, chat, entrada e controles."""
        t = self._tmgr.theme

        hdr = tk.Frame(self, bg=t.panel_bg, height=36)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(
            hdr,
            text="  " + _t("ia.panel_title"),
            bg=t.panel_bg,
            fg=getattr(t, "sidebar_header_fg", t.fg),
            font=(FONT_UI, 9, "bold"),
        ).pack(side="left", padx=4)

        for text, cmd in [
            (_t("ia.clear"), self._clear_chat),
            ("💬", lambda: self._open_sessions_menu()),
            ("＋", self._new_session),
        ]:
            lbl = tk.Label(
                hdr,
                text=text,
                bg=t.panel_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 8),
                cursor="hand2",
            )
            lbl.pack(side="right", padx=6)
            lbl.bind("<Button-1>", lambda _, c=cmd: c())

        perm_btn = tk.Label(
            hdr,
            text="🔐",
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 10),
            cursor="hand2",
        )
        perm_btn.pack(side="right", padx=2)
        perm_btn.bind("<Button-1>", lambda _: self._open_permissions())

        prov_row = tk.Frame(self, bg=t.panel_bg)
        prov_row.pack(fill="x", padx=6, pady=(2, 0))
        names = [cls.name for cls in PROVIDERS]
        saved = self._cfg.get("ia_provedor", names[0])
        self._provider_name.set(saved if saved in names else names[0])

        self._combo_provider = tk.OptionMenu(
            prov_row,
            self._provider_name,
            *names,
            command=lambda _: self._select_provider(self._provider_name.get()),
        )
        self._combo_provider.config(
            bg=t.input_bg,
            fg=t.fg,
            activebackground=t.sel_bg,
            activeforeground=t.sel_fg,
            highlightthickness=0,
            relief="flat",
            font=(FONT_UI, 9),
            bd=0,
            cursor="hand2",
        )
        self._combo_provider["menu"].config(
            bg=t.input_bg,
            fg=t.fg,
            activebackground=t.sel_bg,
            activeforeground=t.sel_fg,
            font=(FONT_UI, 9),
            bd=0,
            relief="flat",
        )
        self._combo_provider.pack(
            side="left", fill="x", expand=True, padx=(0, 4))

        cfg_btn = tk.Label(
            prov_row,
            text="⚙",
            bg=t.panel_bg,
            fg=t.fg,
            font=(FONT_UI, 11),
            cursor="hand2",
        )
        cfg_btn.pack(side="right")
        cfg_btn.bind("<Button-1>", lambda _: self._open_provider_config())

        self._lbl_status = tk.Label(
            self,
            text="",
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 7),
            anchor="w",
        )
        self._lbl_status.pack(fill="x", padx=8, pady=(1, 0))

        action_row = tk.Frame(self, bg=t.panel_bg)
        action_row.pack(fill="x", padx=6, pady=(3, 0))
        tk.Label(
            action_row,
            text=_t("ia.action"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        ).pack(side="left")
        _qa = _get_quick_actions()
        self._action_var = tk.StringVar(value=_t(_qa[0][0]) if _qa else "")
        _qa_labels = [_t(key) for key, _ in _qa]
        self._combo_action = tk.OptionMenu(
            action_row, self._action_var, *(_qa_labels or [""])
        )
        self._combo_action.config(
            bg=t.input_bg,
            fg=t.fg,
            activebackground=t.sel_bg,
            activeforeground=t.sel_fg,
            highlightthickness=0,
            relief="flat",
            font=(FONT_UI, 8),
            bd=0,
            cursor="hand2",
        )
        self._combo_action["menu"].config(
            bg=t.input_bg,
            fg=t.fg,
            activebackground=t.sel_bg,
            activeforeground=t.sel_fg,
            font=(FONT_UI, 8),
            bd=0,
            relief="flat",
        )
        self._combo_action.pack(side="left", fill="x",
                                expand=True, padx=(4, 0))

        ctx_row = tk.Frame(self, bg=t.panel_bg)
        ctx_row.pack(fill="x", padx=6, pady=(3, 0))
        tk.Label(
            ctx_row,
            text=_t("ia.context"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        ).pack(side="left")
        self._ctx_var = tk.StringVar(value="none")
        for label_key, value in _CONTEXT_OPTIONS:
            rb = tk.Radiobutton(
                ctx_row,
                text=_t(label_key),
                variable=self._ctx_var,
                value=value,
                bg=t.panel_bg,
                fg=t.fg,
                activebackground=t.panel_bg,
                selectcolor=t.sel_bg,
                font=(FONT_UI, 8),
                cursor="hand2",
            )
            rb.pack(side="left", padx=3)

        self._frame_manual_ctx = tk.Frame(self, bg=t.panel_bg)
        tk.Label(
            self._frame_manual_ctx,
            text=_t("ia.ctx_manual_label"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        ).pack(anchor="w", padx=6)
        self._txt_ctx = tk.Text(
            self._frame_manual_ctx,
            height=5,
            bg=t.input_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            font=(FONT_MONO, 8),
            highlightthickness=1,
            highlightbackground=t.border,
            wrap="none",
        )
        self._txt_ctx.pack(fill="x", padx=6, pady=(0, 4))
        self._ctx_var.trace_add("write", self._on_ctx_mode_change)

        tk.Frame(self, bg=t.border, height=1).pack(fill="x", pady=(4, 0))

        self._approval_panel = _ApprovalPanel(self, self._tmgr)
        self._gate.attach_panel(self._approval_panel)

        self._notebook = ttk.Notebook(self)
        self._notebook.pack(fill="both", expand=True)

        chat_outer = tk.Frame(self._notebook, bg=t.editor_bg)
        self._notebook.add(chat_outer, text=_t(
            "ia.tab_chat", default="💬 Chat"))

        scrollbar = ttk.Scrollbar(chat_outer, orient="vertical")
        scrollbar.pack(side="right", fill="y")
        self._canvas = tk.Canvas(
            chat_outer,
            bg=t.editor_bg,
            highlightthickness=0,
            yscrollcommand=scrollbar.set,
        )
        self._canvas.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self._canvas.yview)

        self._chat_frame = tk.Frame(self._canvas, bg=t.editor_bg)
        self._canvas_item = self._canvas.create_window(
            (0, 0), window=self._chat_frame, anchor="nw"
        )
        self._chat_frame.bind("<Configure>", self._on_chat_configure)
        self._canvas.bind("<Configure>", self._on_canvas_configure)
        for evt in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self._canvas.bind(evt, self._on_scroll)

        self._show_welcome()

        self._history_panel = ActionHistoryPanel(self._notebook, self._tmgr)
        self._notebook.add(
            self._history_panel, text=_t("ia.tab_actions", default="📋 Ações")
        )

        self._frame_generating = tk.Frame(self, bg=t.panel_bg)
        tk.Label(
            self._frame_generating,
            text=_t("ia.generating"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        ).pack(side="left", padx=8)
        self._agent_lbl = tk.Label(
            self._frame_generating,
            text="",
            bg=t.panel_bg,
            fg=getattr(t, "accent", "#4fc3f7"),
            font=(FONT_UI, 7, "italic"),
        )
        self._agent_lbl.pack(side="left")
        cancel_btn = tk.Label(
            self._frame_generating,
            text=_t("ia.cancel"),
            bg=t.panel_bg,
            fg=getattr(t, "err_fg", "#f44336"),
            font=(FONT_UI, 8),
            cursor="hand2",
        )
        cancel_btn.pack(side="right", padx=8)
        cancel_btn.bind("<Button-1>", lambda _: self._cancel())

        tk.Frame(self, bg=t.border, height=1).pack(fill="x")

        entry_outer = tk.Frame(
            self, bg=t.input_bg, highlightthickness=1, highlightbackground=t.border
        )
        entry_outer.pack(fill="x", padx=6, pady=4)
        self._entry = tk.Text(
            entry_outer,
            height=3,
            bg=t.input_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            bd=0,
            font=(FONT_UI, 9),
            wrap="word",
            highlightthickness=0,
        )
        self._entry.pack(fill="x", padx=6, pady=4)
        self._entry.bind("<Return>", self._on_enter)
        self._entry.bind("<Shift-Return>", lambda _e: None)

        btns_row = tk.Frame(self, bg=t.panel_bg)
        btns_row.pack(fill="x", padx=6, pady=(0, 6))

        self._btn_send = tk.Label(
            btns_row,
            text="  " + _t("ia.send") + "  ",
            bg=getattr(t, "accent", "#4fc3f7"),
            fg="#ffffff",
            font=(FONT_UI, 9, "bold"),
            cursor="hand2",
            relief="flat",
        )
        self._btn_send.pack(side="right")
        self._btn_send.bind("<Button-1>", lambda _: self._send())

        self._btn_apply = tk.Label(
            btns_row,
            text=_t("ia.apply_code"),
            bg=t.btn_bg,
            fg=t.fg,
            font=(FONT_UI, 8),
            cursor="hand2",
            relief="flat",
            padx=6,
        )
        self._btn_apply.pack(side="right", padx=4)
        self._btn_apply.bind("<Button-1>", lambda _: self._apply_last_code())

        self._btn_undo = tk.Label(
            btns_row, text=_t("ia.undo_ai", default="↶ Desfazer IA"), bg=t.btn_bg, fg=t.fg,
            font=(FONT_UI, 8), cursor="hand2", relief="flat", padx=6,
        )
        self._btn_undo.pack(side="left")
        self._btn_undo.bind("<Button-1>", lambda _: self._undo_last_ai())
        self._btn_regen = tk.Label(
            btns_row, text=_t("ia.regenerate", default="↻"), bg=t.btn_bg, fg=t.fg,
            font=(FONT_UI, 8), cursor="hand2", relief="flat", padx=6,
        )
        self._btn_regen.pack(side="left", padx=4)
        self._btn_regen.bind("<Button-1>", lambda _: self._regenerate())
        self._lbl_tokens = tk.Label(btns_row, text="", bg=t.panel_bg, fg=t.fg_muted, font=(FONT_UI, 7))
        self._lbl_tokens.pack(side="left", padx=4)

        opts_row = tk.Frame(self, bg=t.panel_bg)
        opts_row.pack(fill="x", padx=6, pady=(0, 2))

        tk.Checkbutton(
            opts_row,
            text=_t("ia.tools_toggle"),
            variable=self._tools_enabled,
            bg=t.panel_bg,
            fg=t.fg_muted,
            activebackground=t.panel_bg,
            selectcolor=t.sel_bg,
            font=(FONT_UI, 7),
            cursor="hand2",
        ).pack(side="left")

        for _mval, _mkey, _mdef in (("ask", "ia.mode_ask", "Perguntar"),
                                    ("edit", "ia.mode_edit", "Editar"),
                                    ("agent", "ia.mode_agent", "Agente")):
            tk.Radiobutton(
                opts_row,
                text=_t(_mkey, default=_mdef),
                variable=self._mode,
                value=_mval,
                bg=t.panel_bg,
                fg=t.fg_muted,
                activebackground=t.panel_bg,
                selectcolor=t.sel_bg,
                font=(FONT_UI, 7),
                cursor="hand2",
                indicatoron=False,
                padx=5,
            ).pack(side="left", padx=1)

        init_btn = tk.Label(
            opts_row,
            text=_t("ia.new_project_btn"),
            bg=t.btn_bg,
            fg=t.fg,
            font=(FONT_UI, 7),
            cursor="hand2",
            relief="flat",
            padx=4,
        )
        init_btn.pack(side="right")
        init_btn.bind(
            "<Button-1>", lambda _: self._start_init_project_dialog())

    def _on_chat_configure(self, _event: Any = None) -> None:
        """Atualiza a scrollregion do canvas quando o frame interno muda de tamanho."""
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas_configure(self, event: Any) -> None:
        """Ajusta a largura do chat_frame para preencher o canvas horizontalmente."""
        self._canvas.itemconfig(self._canvas_item, width=event.width)

    def _on_scroll(self, event: tk.Event) -> None:
        """Trata eventos de scroll do mouse no canvas do chat (Linux e Windows/macOS)."""
        if event.num == 4:
            self._canvas.yview_scroll(-1, "units")
        elif event.num == 5:
            self._canvas.yview_scroll(1, "units")
        else:
            self._canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _on_ctx_mode_change(self, *_: Any) -> None:
        """Exibe ou oculta o campo de contexto manual conforme o modo selecionado."""
        if self._ctx_var.get() == "manual":
            self._frame_manual_ctx.pack(
                fill="x", after=self._combo_action.master)
        else:
            self._frame_manual_ctx.pack_forget()

    def _select_provider(self, name: str) -> None:
        """Instancia o provedor `name`, persiste a escolha e atualiza o status."""
        self._cfg.set("ia_provedor", name)
        self._cfg.save()
        self._provider = create_provider(name)
        if self._provider:
            self._provider.configure(self._provider_cfgs.get(name, {}))
        self._update_status()

    def _open_provider_config(self) -> None:
        """Abre o diálogo de configuração do provedor atualmente selecionado."""
        prov_name = self._provider_name.get()
        cls = next((c for c in PROVIDERS if c.name == prov_name), None)
        if (
            cls
            and cls == LocalLibProvider
            and not messagebox.askyesno(
                _t("ia.heavy_warning_title"), WARNING_LOCAL_LIB, parent=self
            )
        ):
            return

        def _on_save(data: dict[str, Any]) -> None:
            self._provider_cfgs[prov_name] = data
            self._save_provider_cfgs()
            if self._provider:
                self._provider.configure(data)
            self._update_status()

        _ProviderConfigDialog(
            self,
            self._tmgr,
            prov_name,
            self._provider_cfgs.get(prov_name, {}),
            _on_save,
        )

    def _update_status(self) -> None:
        """Atualiza o label de status com o estado atual do provedor (pronto/erro)."""
        if not self._provider:
            self._lbl_status.config(
                text=_t("ia.status_unavailable"), fg="#f0a500")
            return
        error_msg = self._provider.check()
        if error_msg:
            self._lbl_status.config(
                text=_t("ia.status_error", error=error_msg), fg="#f0a500"
            )
        else:
            self._lbl_status.config(
                text=_t("ia.status_ready", name=self._provider.name),
                fg=getattr(self._tmgr.theme, "accent", "#4fc3f7"),
            )

    def _open_permissions(self) -> None:
        """Abre o diálogo de configuração de permissões automáticas da IA."""

        def _on_save(levels: set[ActionRisk]) -> None:
            self._auto_approve_levels = levels
            self._gate._auto_safe = ActionRisk.SAFE in levels
            self._cfg.set("ia_auto_approve_levels", [r.name for r in levels])
            self._cfg.save()

        _PermissionsPanel(self, self._tmgr,
                          self._auto_approve_levels, _on_save)

    def _show_welcome(self) -> None:
        """Exibe mensagem de boas-vindas e dicas no chat vazio."""
        t = self._tmgr.theme
        tk.Label(
            self._chat_frame,
            text=_t("ia.welcome"),
            bg=t.editor_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
            justify="center",
            wraplength=280,
        ).pack(pady=20)

        tips = [
            _t("ia.tip_agent"),
            "Novo Projeto cria um projeto do zero",
            _t("ia.tip_permissions"),
            _t("ia.tip_approval"),
        ]
        for tip in tips:
            tk.Label(
                self._chat_frame,
                text=tip,
                bg=t.editor_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 8),
                justify="left",
                anchor="w",
            ).pack(fill="x", padx=16, pady=1)

    def _clear_chat(self) -> None:
        """Apaga a conversa atual (inclusive do disco) e começa uma nova."""
        if self._generating:
            return
        self._store.delete(self._session.id)
        self._start_fresh_session()

    def _on_enter(self, event: tk.Event) -> str:
        """Trata Enter (envia) e Shift+Enter (nova linha) no campo de entrada."""
        if not event.state & 0x1:
            self._send()
            return "break"
        return ""

    def _ws_key(self) -> str | None:
        ws = self._get_workspace() if self._get_workspace else None
        return str(ws) if ws else None

    def _budget(self) -> int:
        try:
            return max(4000, int(self._cfg.get("ia_context_tokens", 24000) or 24000))
        except (TypeError, ValueError):
            return 24000

    def _editors(self) -> list[Any]:
        """Todos os editores abertos (ou só o ativo, se o app não informou a lista)."""
        try:
            if self._get_open_editors:
                return [e for e in self._get_open_editors() if e is not None]
            ed = self._get_editor() if self._get_editor else None
            return [ed] if ed is not None else []
        except Exception:  # noqa: BLE001
            logger.debug("listagem de editores falhou", exc_info=True)
            return []

    def _open_buffers(self) -> dict[str, str]:
        """Conteúdo (inclusive não salvo) dos arquivos abertos, por caminho absoluto."""
        out: dict[str, str] = {}
        for ed in self._editors():
            p = getattr(ed, "path", None)
            if not p:
                continue
            try:
                txt = ed.text.get("1.0", "end-1c")
                if len(txt) <= 200_000:
                    out[str(Path(p).resolve())] = txt
            except (tk.TclError, OSError, AttributeError):
                continue
        return out

    def _make_sources(self) -> ContextSources:
        ws = self._get_workspace() if self._get_workspace else None

        def active() -> tuple[Path | None, str, str] | None:
            ed = self._get_editor() if self._get_editor else None
            if ed is None:
                return None
            p = getattr(ed, "path", None)
            return (Path(p) if p else None, getattr(ed, "lang", ""), ed.text.get("1.0", "end-1c"))

        def selection() -> tuple[Path | None, str, int] | None:
            ed = self._get_editor() if self._get_editor else None
            if ed is None:
                return None
            try:
                body = ed.text.get(tk.SEL_FIRST, tk.SEL_LAST)
                first = int(str(ed.text.index(tk.SEL_FIRST)).split(".")[0])
            except tk.TclError:
                return None
            p = getattr(ed, "path", None)
            return (Path(p) if p else None, body, first)

        def tabs() -> list[tuple[Path | None, bool]]:
            return [(Path(e.path) if getattr(e, "path", None) else None, bool(getattr(e, "modified", False)))
                    for e in self._editors()]

        run = self._tool_executor.execute
        return ContextSources(
            workspace=lambda: Path(ws) if ws else None,
            active_file=active,
            selection=selection,
            open_tabs=tabs,
            diagnostics=lambda: run("read_diagnostics", {}),
            git_diff=lambda: run("read_git_diff", {}),
            file_tree=lambda: run("read_file_tree", {}),
            open_buffers=self._open_buffers,
        )

    def _skill_prefix(self) -> str:
        action_label = self._action_var.get()
        return next((p for key, p in _get_quick_actions() if _t(key) == action_label), "")

    def _build_attachment(self, user_text: str) -> tuple[str, list[str], list[str]]:
        """Contexto *fresco* desta mensagem: seletor de contexto + @menções + suas edições.

        Retorna ``(anexo, nomes, avisos)``.  Nada disso é repetido nos turnos seguintes.
        """
        budget = self._budget() // 2
        blocks: list[str] = []
        names: list[str] = []
        ctx_mode = self._ctx_var.get()
        ed = self._get_editor() if self._get_editor else None
        lang = getattr(ed, "lang", "") if ed else ""
        code, label = "", ""
        if ctx_mode == "file" and ed:
            code, label = ed.text.get("1.0", "end-1c"), _t("ia.ctx_file_name", default="arquivo ativo")
        elif ctx_mode == "selection" and ed:
            with contextlib.suppress(tk.TclError):
                code, label = ed.text.get(tk.SEL_FIRST, tk.SEL_LAST), _t("ia.ctx_sel_name", default="seleção")
        elif ctx_mode == "manual":
            code, label = self._txt_ctx.get("1.0", "end-1c").strip(), _t("ia.ctx_manual_name", default="trecho manual")
        elif ctx_mode == "none" and ed:
            with contextlib.suppress(tk.TclError, ValueError):
                row = int(str(ed.text.index("insert")).split(".")[0])
                lines = ed.text.get("1.0", "end-1c").splitlines()
                region = lines[max(0, row - 21): min(len(lines), row + 20)]
                if len(region) > 3:
                    code, label = "\n".join(region), _t("ia.ctx_cursor_name", default="trecho ao redor do cursor")
        if code:
            blocks.append(f"### {label}\n```{lang}\n{middle_truncate(code, budget)}\n```")
            names.append(label)

        res = resolve_mentions(user_text, self._make_sources(), budget_tokens=budget)
        if res.attachment:
            blocks.append(res.attachment)
            names += res.names

        changes = self._tracker.changes_since(self._open_buffers())
        if changes:
            blocks.append(changes)
            names.append(_t("ia.ctx_user_edits", default="suas edições recentes"))
        return "\n\n".join(blocks), names, res.warnings

    def _persist_session(self) -> None:
        with contextlib.suppress(OSError):
            self._store.save(self._session)

    def _start_fresh_session(self) -> None:
        self._session = ChatSession(self._ws_key(), self._mode.get())
        self._tracker.reset()
        self._tool_executor.set_session(self._session.id)
        self._last_ai_text = ""
        self._render_session()

    def _new_session(self) -> None:
        if self._generating:
            return
        self._persist_session()
        self._start_fresh_session()

    def _render_session(self) -> None:
        """Redesenha o chat a partir da sessão atual."""
        for w in self._chat_frame.winfo_children():
            w.destroy()
        if not self._session.turns:
            self._show_welcome()
        for turn in self._session.turns:
            self._add_bubble(turn.role, turn.content)
            if turn.role == "assistant":
                self._last_ai_text = turn.content
        self._update_meter()
        self._update_undo_btn()

    def _load_session(self, sid: str) -> None:
        if self._generating or sid == self._session.id:
            return
        loaded = self._store.load(sid)
        if loaded is None:
            return
        self._persist_session()
        self._session = loaded
        self._mode.set(loaded.mode if loaded.mode in MODES else self._mode.get())
        self._tracker.reset()
        self._tool_executor.set_session(loaded.id)
        self._render_session()

    def _open_sessions_menu(self, event: Any = None) -> None:
        t = self._tmgr.theme
        menu = tk.Menu(self, tearoff=False, bg=t.input_bg, fg=t.fg, activebackground=t.sel_bg,
                       activeforeground=t.sel_fg, relief="flat")
        self._persist_session()
        for info in self._store.list(self._ws_key())[:15]:
            when = time.strftime("%d/%m %H:%M", time.localtime(info.updated))
            mark = "● " if info.id == self._session.id else "   "
            menu.add_command(label=f"{mark}{info.title[:42] or '…'}  ({when})",
                             command=lambda i=info.id: self._load_session(i))
        menu.add_separator()
        menu.add_command(label=_t("ia.session_rename", default="Renomear conversa…"), command=self._rename_session)
        menu.add_command(label=_t("ia.session_new", default="Nova conversa"), command=self._new_session)
        try:
            x, y = (event.x_root, event.y_root) if event is not None else (self.winfo_pointerx(), self.winfo_pointery())
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _rename_session(self) -> None:
        name = simpledialog.askstring(_t("ia.session_rename", default="Renomear conversa…"), "",
                                      initialvalue=self._session.title, parent=self)
        if name and name.strip():
            self._session.title = name.strip()[:80]
            self._persist_session()

    def _on_mode_change(self) -> None:
        mode = self._mode.get()
        self._session.mode = mode
        self._cfg.set("ia_mode", mode)
        with contextlib.suppress(Exception):
            self._cfg.save()

    def _update_meter(self) -> None:
        try:
            tokens = self._session.messages_for_model(self._budget()).tokens
            self._lbl_tokens.config(text=f"≈ {tokens:,} / {self._budget():,} tok".replace(",", "."))
        except tk.TclError:
            pass

    def _has_undo(self) -> bool:
        cps = self._tool_executor.checkpoints
        return bool(cps and any(not c.undone for c in cps.list()))

    def _update_undo_btn(self) -> None:
        with contextlib.suppress(tk.TclError, AttributeError):
            self._btn_undo.config(fg=self._tmgr.theme.fg if self._has_undo() else self._tmgr.theme.fg_muted)

    def _set_turn_buttons(self, enabled: bool) -> None:
        state_fg = self._tmgr.theme.fg if enabled else self._tmgr.theme.fg_muted
        with contextlib.suppress(tk.TclError):
            self._btn_regen.config(fg=state_fg)
            if enabled:
                self._update_undo_btn()
            else:
                self._btn_undo.config(fg=state_fg)

    def _add_note(self, text: str) -> None:
        t = self._tmgr.theme
        tk.Label(self._chat_frame, text=text, bg=t.editor_bg, fg=t.fg_muted, font=(FONT_UI, 8, "italic"),
                 anchor="w", justify="left", wraplength=300).pack(fill="x", padx=12, pady=1)
        self._canvas.update_idletasks()
        self._on_chat_configure()
        self._canvas.yview_moveto(1.0)

    def _undo_last_ai(self) -> None:
        if self._generating or not self._has_undo():
            return
        rep = self._tool_executor.undo_turn()
        if rep is None:
            return
        if rep.conflicts and messagebox.askyesno(
            _t("ia.undo_ai", default="↶ Desfazer IA"),
            _t("ia.undo_conflict", default="Você editou estes arquivos depois da IA:\n\n{files}\n\n"
                                            "Desfazer mesmo assim e perder as suas alterações neles?",
               files="\n".join(Path(c).name for c in rep.conflicts[:10])),
            icon="warning", parent=self,
        ):
            rep2 = self._tool_executor.undo_turn(force=True)
            if rep2 is not None:
                rep = rep2
        self._add_note("↶ " + rep.summary())
        self._update_undo_btn()

    def _on_enter(self, event: tk.Event) -> str:
        """Trata Enter (envia) e Shift+Enter (nova linha) no campo de entrada."""
        if not event.state & 0x1:
            self._send()
            return "break"
        return ""

    def _send(self) -> None:
        """Valida a entrada e inicia um turno de conversa."""
        if self._generating:
            return
        user_text = self._entry.get("1.0", "end-1c").strip()
        if not user_text:
            return
        if not self._provider:
            messagebox.showwarning("IA", _t("ia.no_provider"), parent=self)
            return

        if not self._cfg.get("ia_privacy_warning_shown", False):
            msg = (
                "⚠️  Aviso de Privacidade\n\n"
                f"Provedor atual: {self._provider.name}\n\n"
                "Não envie senhas, tokens, chaves de API ou dados pessoais sensíveis.\n\n"
                "Deseja continuar?"
            )
            if not messagebox.askyesno(_t("ia.privacy_warning_title"), msg, parent=self):
                return
            self._cfg.set("ia_privacy_warning_shown", True)
            self._cfg.save()

        self._entry.delete("1.0", "end")
        attachment, names, warnings = self._build_attachment(user_text)
        sess = self._session
        sess.mode = self._mode.get()
        sess.provider = self._provider.name
        sess.model = str(getattr(self._provider, "_cfg", {}).get("modelo", ""))
        if not sess.title:
            sess.title = " ".join(user_text.split())[:60]
        sess.add_user(user_text, attachment=attachment, attached=names, lead=self._skill_prefix())
        self._add_bubble("user", user_text)
        if names:
            self._add_note("📎 " + ", ".join(names))
        for w in warnings:
            self._add_note("⚠ " + w)
        self._begin_generation(sess.mode)

    @staticmethod
    def _summarize_action(name: str, args: dict[str, Any]) -> str:
        key = next((str(args[k]) for k in ("path", "command", "pattern", "query", "src", "name") if args.get(k)), "")
        if not key and args.get("patches"):
            key = ", ".join(str(p.get("path", "")) for p in args["patches"][:3] if isinstance(p, dict))
        return f"{name} {key[:60]}".strip()

    _MAIN_THREAD_TOOLS = frozenset({"read_active_file", "get_cursor_context", "read_diagnostics", "read_symbols"})

    def _call_on_main(self, fn: Callable[[], Any]) -> Any:
        """Executa *fn* na thread da UI e espera o resultado (chamado de threads de IA)."""
        if threading.current_thread() is threading.main_thread():
            return fn()
        box: dict[str, Any] = {}
        done = threading.Event()

        def run() -> None:
            try:
                box["v"] = fn()
            except BaseException as exc:  # noqa: BLE001
                box["e"] = exc
            finally:
                done.set()

        self.after(0, run)
        if not done.wait(timeout=30):
            return "[Erro] a interface não respondeu a tempo."
        if "e" in box:
            raise box["e"]
        return box.get("v")

    def _run_tool(self, name: str, args: dict[str, Any]) -> str:
        if name in self._MAIN_THREAD_TOOLS:
            return self._call_on_main(lambda: self._tool_executor.execute(name, args))
        return self._tool_executor.execute(name, args)

    def _send_to_provider(self, msgs: list[dict[str, str]], on_chunk: Callable, on_done: Callable,
                          on_error: Callable, options: dict[str, Any]) -> None:
        """Envia com ``options`` por requisição (sem mexer na config compartilhada).

        Provedores de plugin antigos, sem o parâmetro, recebem a config temporária
        e a restauração só acontece quando a resposta termina (nunca antes, o que
        causava perda aleatória de system prompt e ferramentas).
        """
        prov = self._provider
        assert prov is not None
        try:
            params = inspect.signature(prov.send).parameters
        except (TypeError, ValueError):
            params = {}
        if "options" in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
            prov.send(msgs, on_chunk, on_done, on_error, options=options)
            return
        original = dict(getattr(prov, "_cfg", {}))
        prov.configure({**original, **options})

        def restore_then(cb: Callable) -> Callable:
            def inner(*a: Any) -> None:
                prov.configure(original)
                cb(*a)
            return inner

        prov.send(msgs, on_chunk, restore_then(on_done), restore_then(on_error))

    def _begin_generation(self, mode: str) -> None:
        """Inicia a resposta da IA para a última mensagem do usuário da sessão."""
        sess = self._session
        self._turn_id += 1
        tid = self._turn_id
        self._turn_actions = []
        self._stream_buffer = ""
        self._stream_shown = ""
        self._current_bubble = self._add_bubble("assistant", "")
        self._generating = True
        self._frame_generating.pack(fill="x", before=self._notebook)
        self._agent_lbl.config(text=_t("ia.agent_label") if mode == "agent" else "")
        self._btn_send.config(state="disabled")
        self._set_turn_buttons(False)
        self._turn_editing = False
        try:
            tools = tools_for_mode(mode, TOOL_SCHEMA) if self._tools_enabled.get() else None
            self._turn_editing = bool(tools) and mode != "ask"
            if self._turn_editing:
                self._tool_executor.set_session(sess.id)
                self._tool_executor.begin_turn(sess.last_user_text() or "")

            try:
                ws_key = self._ws_key()
                ctx_block = build_context_block(
                    get_editor=self._get_editor,
                    get_workspace=lambda: Path(ws_key) if ws_key else None,
                )
            except Exception:  # noqa: BLE001
                logger.debug("build_context_block falhou silenciosamente", exc_info=True)
                ctx_block = ""
            user_sys = str(getattr(self._provider, "_cfg", {}).get("sistema", ""))
            system = build_system_prompt(mode, user_system=user_sys, skill_prompt=self._get_action_system_prompt(),
                                         project_context=ctx_block)
            reserve = estimate_tokens(system) + (estimate_tokens(json.dumps(tools)) if tools else 0)
            view = sess.messages_for_model(self._budget(), reserve)
            if view.condensed_turns or view.truncated:
                self._add_note(_t("ia.history_condensed",
                                  default="ℹ Conversa longa: turnos antigos foram resumidos para caber no contexto."))

            def on_chunk(chunk: str) -> None:
                if tid == self._turn_id:
                    self._stream_buffer += chunk

            def on_done(complete: str) -> None:
                self.after(0, lambda: self._finish_turn(tid, complete, None))

            def on_error(msg: str) -> None:
                self.after(0, lambda: self._finish_turn(tid, None, msg))

            def on_tool_call(name: str, args: dict[str, Any]) -> str:
                """Intercepta chamada de ferramenta - aplica approval gate."""
                if tid != self._turn_id:
                    return "[Cancelado pelo usuário]"
                self.after(0, lambda: self._add_tool_bubble(name, args))
                self._turn_actions.append(self._summarize_action(name, args))
                risk = classify_action(name, args)
                if risk in self._auto_approve_levels:
                    return self._run_tool(name, args)
                return self._gate.request(name, args, lambda: self._run_tool(name, args))

            options: dict[str, Any] = {"sistema": system}
            if tools:
                options["_ferramentas"] = tools
                options["_on_tool_call"] = on_tool_call
            self._stream_tick(tid)
            self._send_to_provider(view.messages, on_chunk, on_done, on_error, options)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Falha ao iniciar geração: %s", exc, exc_info=True)
            self._finish_turn(tid, None, str(exc))

    def _stream_tick(self, tid: int) -> None:
        """Repinta a resposta em streaming a cada ~80 ms (na thread da UI)."""
        if tid != self._turn_id or not self._generating:
            return
        buf = self._stream_buffer
        if buf != self._stream_shown and self._current_bubble:
            self._current_bubble.update_content(buf)
            self._stream_shown = buf
            self._canvas.yview_moveto(1.0)
        self.after(80, lambda: self._stream_tick(tid))

    def _finish_turn(self, tid: int, complete: str | None, error: str | None) -> None:
        """Conclui o turno (thread da UI). Ignora callbacks de turnos cancelados."""
        if tid != self._turn_id or not self._generating:
            return
        sess = self._session
        cp = self._tool_executor.end_turn() if self._turn_editing else None
        self._turn_editing = False
        self._generating = False
        self._stop_indicator_keep_bubble()
        bubble = self._current_bubble
        self._current_bubble = None
        if error is not None:
            if bubble:
                bubble.update_content(f"❌ {error}")
            restored = sess.drop_last_exchange()
            if restored and not self._entry.get("1.0", "end-1c").strip():
                self._entry.insert("1.0", restored)
        else:
            text = complete if complete is not None else self._stream_buffer
            if bubble:
                bubble.update_content(text)
            sess.add_assistant(text, self._turn_actions, cp.id if cp else None)
            self._last_ai_text = text
            self._tracker.snapshot(self._open_buffers())
            if extract_code_blocks(text):
                self.after(50, self._notify_code_ready)
        self._persist_session()
        self._update_meter()
        self._set_turn_buttons(True)
        self._canvas.yview_moveto(1.0)

    def _regenerate(self) -> None:
        """Refaz a última resposta (desfazendo o que ela mudou, com contexto atualizado)."""
        if self._generating or not self._provider:
            return
        sess = self._session
        if not sess.turns or sess.turns[-1].role != "assistant":
            return
        last = sess.turns[-1]
        if last.checkpoint is not None and self._has_undo():
            rep = self._tool_executor.undo_turn(last.checkpoint)
            if rep is not None and not rep.ok:
                self._add_note("⚠ " + rep.summary())
                return
        sess.turns.pop()
        user = sess.turns[-1] if sess.turns and sess.turns[-1].role == "user" else None
        if user is None:
            return
        attachment, names, _w = self._build_attachment(user.content)
        user.attachment, user.attached, user.lead = attachment, names, self._skill_prefix()
        self._render_session()
        self._begin_generation(self._mode.get())

    def _get_action_system_prompt(self) -> str:
        """Retorna o system prompt da skill selecionada, ou string vazia se não houver."""
        action_label = self._action_var.get()
        for skill in list_skills():
            if _t(skill.i18n_key) == action_label:
                return skill.system_prompt
        return ""

    def _stop_indicator_keep_bubble(self) -> None:
        self._frame_generating.pack_forget()
        self._btn_send.config(state="normal")

    def _cancel(self) -> None:
        """Cancela a geração: guarda o texto parcial e invalida callbacks atrasados."""
        if not self._generating:
            return
        self._turn_id += 1
        if self._provider:
            self._provider.cancel()
        if self._gate._approval_panel_ref:
            self._gate._approval_panel_ref.clear_request()
        partial = self._stream_buffer
        cp = self._tool_executor.end_turn() if self._turn_editing else None
        self._turn_editing = False
        self._generating = False
        self._stop_indicator_keep_bubble()
        suffix = _t("ia.cancelled_suffix", default="_(cancelado)_")
        text = (partial + "\n\n" + suffix) if partial.strip() else suffix
        if self._current_bubble:
            self._current_bubble.update_content(text)
        self._current_bubble = None
        self._session.add_assistant(text, self._turn_actions, cp.id if cp else None)
        self._persist_session()
        self._update_meter()
        self._set_turn_buttons(True)

    def _add_bubble(self, role: str, content: str) -> _MessageBubble:
        """Cria e empacota uma nova bolha de mensagem no chat. Rola para o fim.

        Remove o label de boas-vindas na primeira mensagem.
        """
        t = self._tmgr.theme
        children = self._chat_frame.winfo_children()
        if children and isinstance(children[0], tk.Label):
            children[0].destroy()

        bubble = _MessageBubble(
            self._chat_frame,
            self._tmgr,
            role,
            content,
            on_insert=self._insert_to_editor if role == "assistant" else None,
            on_copy=self._copy_to_clipboard if role == "assistant" else None,
        )
        bubble.pack(fill="x", padx=4, pady=2)
        tk.Frame(self._chat_frame, bg=t.border, height=1).pack(
            fill="x", padx=16, pady=1
        )

        self._canvas.update_idletasks()
        self._on_chat_configure()
        self._canvas.yview_moveto(1.0)
        return bubble

    def _add_tool_bubble(self, tool_name: str, args: dict[str, Any]) -> None:
        """Exibe indicador de ferramenta em uso com nível de risco."""
        t = self._tmgr.theme
        risk = classify_action(tool_name, args)
        colors = {
            ActionRisk.SAFE: ("#4caf50", "✅"),
            ActionRisk.LOW: ("#8bc34a", "🔵"),
            ActionRisk.MEDIUM: ("#ff9800", "🟡"),
            ActionRisk.HIGH: ("#f44336", "🔴"),
            ActionRisk.CRITICAL: ("#9c27b0", "💀"),
        }
        color, icon = colors.get(risk, ("#aaa", "?"))

        frame = tk.Frame(self._chat_frame, bg=t.editor_bg, pady=2)
        frame.pack(fill="x", padx=4)
        tk.Label(
            frame,
            text=_t("ia.tool_running_label", icon=icon, name=tool_name),
            bg=t.editor_bg,
            fg=color,
            font=(FONT_UI, 8, "italic"),
            anchor="w",
        ).pack(fill="x", padx=12)
        self._canvas.update_idletasks()
        self._on_chat_configure()
        self._canvas.yview_moveto(1.0)

    def _notify_code_ready(self) -> None:
        """Exibe banner clicável no chat quando a resposta contém blocos de código."""
        t = self._tmgr.theme
        frame = tk.Frame(self._chat_frame, bg=t.editor_bg, pady=2)
        frame.pack(fill="x", padx=4)
        inner = tk.Frame(
            frame,
            bg=getattr(t, "sel_bg", t.input_bg),
            highlightthickness=1,
            highlightbackground=getattr(t, "accent", "#4fc3f7"),
        )
        inner.pack(fill="x", padx=8)
        info = tk.Label(
            inner,
            text=_t("ia.code_ready"),
            bg=getattr(t, "sel_bg", t.input_bg),
            fg=getattr(t, "accent", "#4fc3f7"),
            font=(FONT_UI, 8, "bold"),
            anchor="w",
            cursor="hand2",
        )
        info.pack(side="left", padx=4, pady=4)
        apply_btn = tk.Label(
            inner,
            text=_t("ia.apply_ellipsis"),
            bg=getattr(t, "accent", "#4fc3f7"),
            fg="#ffffff",
            font=(FONT_UI, 8, "bold"),
            cursor="hand2",
            padx=6,
            pady=3,
        )
        apply_btn.pack(side="right", padx=4, pady=4)
        for w in (apply_btn, inner, info):
            w.bind("<Button-1>", lambda _: self._apply_last_code())
        self._canvas.update_idletasks()
        self._on_chat_configure()
        self._canvas.yview_moveto(1.0)

    def _on_action_logged(self, entry: ActionHistoryEntry) -> None:
        """Processa uma nova entrada no log de ações e atualiza o painel de histórico."""
        self._history_panel.add_entry(entry)
        if entry.risk >= ActionRisk.MEDIUM:
            try:
                self._notebook.tab(
                    1, text=_t("ia.tab_actions", default="📋 Ações") + " ●"
                )
                self.after(
                    2000,
                    lambda: self._notebook.tab(
                        1, text=_t("ia.tab_actions", default="📋 Ações")
                    ),
                )
            except Exception:
                logger.debug("Suppressed error", exc_info=True)

    def _start_init_project_dialog(self) -> None:
        """Abre diálogo rápido para inicializar um novo projeto via IA."""
        t = self._tmgr.theme
        dlg = tk.Toplevel(self)
        dlg.title("Novo Projeto com IA")
        dlg.configure(bg=t.panel_bg)
        dlg.resizable(False, False)
        dlg.transient(self)
        safe_grab_set(dlg)

        tk.Label(
            dlg,
            text=_t("ia.proj_title"),
            bg=t.panel_bg,
            fg=t.fg,
            font=(FONT_UI, 11, "bold"),
        ).pack(anchor="w", padx=16, pady=(12, 4))

        form = tk.Frame(dlg, bg=t.panel_bg, padx=16, pady=8)
        form.pack(fill="x")

        tk.Label(
            form,
            text=_t("ia.proj_name_label"),
            bg=t.panel_bg,
            fg=t.fg,
            font=(FONT_UI, 9),
        ).pack(anchor="w")
        name_var = tk.StringVar(value=_t("ia.proj_name_default"))
        tk.Entry(
            form,
            textvariable=name_var,
            bg=t.input_bg,
            fg=t.fg,
            font=(FONT_UI, 9),
            relief="flat",
            highlightthickness=1,
            highlightbackground=t.border,
            insertbackground=t.cursor,
            width=36,
        ).pack(fill="x", pady=(0, 8))

        tk.Label(
            form,
            text=_t("ia.proj_template_label"),
            bg=t.panel_bg,
            fg=t.fg,
            font=(FONT_UI, 9),
        ).pack(anchor="w")
        templates = ["vazio", "node-ts", "react",
                     "python-lib", "fastapi", "rust"]
        template_var = tk.StringVar(value="vazio")
        combo = ttk.Combobox(
            form,
            textvariable=template_var,
            values=templates,
            state="readonly",
            font=(FONT_UI, 9),
            width=34,
        )
        combo.pack(fill="x", pady=(0, 8))

        tk.Label(
            form,
            text=_t("ia.proj_folder_label"),
            bg=t.panel_bg,
            fg=t.fg,
            font=(FONT_UI, 9),
        ).pack(anchor="w")
        folder_var = tk.StringVar(value=".")
        folder_row = tk.Frame(form, bg=t.panel_bg)
        folder_row.pack(fill="x", pady=(0, 8))
        tk.Entry(
            folder_row,
            textvariable=folder_var,
            bg=t.input_bg,
            fg=t.fg,
            font=(FONT_UI, 9),
            relief="flat",
            highlightthickness=1,
            highlightbackground=t.border,
            insertbackground=t.cursor,
            width=28,
        ).pack(side="left", fill="x", expand=True)

        def _browse_folder() -> None:
            path = filedialog.askdirectory(parent=dlg)
            if path:
                folder_var.set(path)

        tk.Button(
            folder_row,
            text="...",
            command=_browse_folder,
            bg=t.btn_bg,
            fg=t.fg,
            relief="flat",
            padx=4,
            font=(FONT_UI, 9),
            cursor="hand2",
        ).pack(side="right")

        git_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            form,
            text=_t("ia.proj_git_label"),
            variable=git_var,
            bg=t.panel_bg,
            fg=t.fg,
            activebackground=t.panel_bg,
            selectcolor=t.sel_bg,
            font=(FONT_UI, 9),
            cursor="hand2",
        ).pack(anchor="w")

        tk.Label(
            form,
            text=_t("ia.proj_desc_label"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        ).pack(anchor="w", pady=(8, 0))
        desc_txt = tk.Text(
            form,
            height=3,
            bg=t.input_bg,
            fg=t.fg,
            font=(FONT_MONO, 8),
            relief="flat",
            highlightthickness=1,
            highlightbackground=t.border,
            insertbackground=t.cursor,
        )
        desc_txt.pack(fill="x")

        tk.Frame(dlg, bg=t.border, height=1).pack(fill="x", pady=8)
        btns = tk.Frame(dlg, bg=t.panel_bg, padx=16, pady=8)
        btns.pack(fill="x")
        tk.Button(
            btns,
            text=_t("ia.proj_cancel"),
            command=dlg.destroy,
            bg=t.btn_bg,
            fg=t.fg,
            relief="flat",
            padx=10,
            pady=4,
            font=(FONT_UI, 9),
            cursor="hand2",
        ).pack(side="right", padx=4)

        def _create() -> None:
            proj_name = name_var.get().strip() or _t("ia.proj_name_default")
            template = template_var.get()
            folder = folder_var.get().strip() or "."
            git = git_var.get()
            extra_desc = desc_txt.get("1.0", "end-1c").strip()
            dlg.destroy()

            prompt = (
                f"Crie um novo projeto chamado '{proj_name}' usando o template '{template}'. "
                f"Pasta destino: {folder}. "
                f"{'Inicialize git.' if git else 'Não inicialize git.'} "
            )
            if extra_desc:
                prompt += f"\n\nDetalhes adicionais: {extra_desc}"

            self._entry.delete("1.0", "end")
            self._entry.insert("1.0", prompt)
            self._mode.set("agent")
            self._send()

        tk.Button(
            btns,
            text=_t("ia.proj_create_btn"),
            command=_create,
            bg=getattr(t, "accent", "#4fc3f7"),
            fg="#ffffff",
            relief="flat",
            padx=10,
            pady=4,
            font=(FONT_UI, 9, "bold"),
            cursor="hand2",
        ).pack(side="right")

        dlg.update_idletasks()
        pw = self.winfo_rootx() + self.winfo_width() // 2
        ph = self.winfo_rooty() + self.winfo_height() // 2
        dlg.geometry(
            f"+{pw - dlg.winfo_width() // 2}+{ph - dlg.winfo_height() // 2}")
        dlg.wait_window()

    def _apply_last_code(self) -> None:
        """Extrai blocos de código da última resposta e abre o diálogo de aplicação."""
        last = self._last_ai_text
        if not last:
            messagebox.showinfo("IA", _t("ia.no_code"), parent=self)
            return
        blocks = extract_code_blocks(last)
        if not blocks:
            messagebox.showinfo("IA", _t("ia.no_code_block"), parent=self)
            return
        confirm_and_apply(
            self,
            self._tmgr,
            last,
            cfg=self._cfg,
            get_editor=self._get_editor,
            on_open_new_tab=self._on_new_tab,
        )

    def _insert_to_editor(self, text: str) -> None:
        """Abre o diálogo de diff/aplicação para inserir `text` no editor ativo."""
        confirm_and_apply(
            self,
            self._tmgr,
            text,
            cfg=self._cfg,
            get_editor=self._get_editor,
            on_open_new_tab=self._on_new_tab,
        )

    def _copy_to_clipboard(self, text: str) -> None:
        """Copia `text` para o clipboard do sistema via Tkinter."""
        with contextlib.suppress(tk.TclError):
            self.clipboard_clear()
            self.clipboard_append(text)

    def apply_theme(self, t: Any) -> None:
        """
        Chamado pelo ThemeManager quando o usuário muda o tema.
        """
        self.config(bg=t.panel_bg)
        for combo in (
            getattr(self, "_combo_provider", None),
            getattr(self, "_combo_action", None),
        ):
            if combo is None:
                continue
            with contextlib.suppress(Exception):
                combo.config(
                    bg=t.input_bg,
                    fg=t.fg,
                    activebackground=t.sel_bg,
                    activeforeground=t.sel_fg,
                )
                combo["menu"].config(
                    bg=t.input_bg,
                    fg=t.fg,
                    activebackground=t.sel_bg,
                    activeforeground=t.sel_fg,
                )
        with contextlib.suppress(Exception):
            self._canvas.config(bg=t.editor_bg)
            self._chat_frame.config(bg=t.editor_bg)
