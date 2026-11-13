"""Codis - API pública de plugins: PluginManifest, PluginAPI e utilitários de suporte.

Fornece os tipos e classes usados por plugins para interagir com o editor:
:class:`OutputChannel`, :class:`PluginStorage`, :class:`ProgressHandle`,
:class:`PluginLogger` e a classe principal :class:`PluginAPI`.
"""

from __future__ import annotations

import ast
import collections
import contextlib
import json
import logging
import re
import threading
import time
import tkinter as tk
import zipfile
from dataclasses import dataclass, field, fields
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog
from typing import Any

from src.constants import (
    CONFIG_DIR,
    EXT_LANG,
    LANG_COMMENT,
    LANG_KEYWORDS,
    LANG_LABEL,
)
from src.i18n import I18n, _t
from src.syntax.patterns import LANG_RE, _pat
from src.theme.base import THEME_ORDER, THEMES
from src.theme.manager import _patch_theme

logger = logging.getLogger("Codis")


@dataclass
class PluginManifest:
    """Metadados completos de um plugin Codis."""

    name: str
    version: str = "1.0.0"
    author: str = ""
    description: str = ""
    homepage: str = ""
    repository: str = ""
    icon: str = "🔌"
    api_version: str = "4.0"
    tags: list[str] = field(default_factory=list)
    license: str = ""
    category: str = "Geral"
    min_codis: str = "1.0.0"
    trusted: bool = False
    readme: str = ""
    keywords: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    engines: dict[str, str] = field(default_factory=dict)
    changelog: str = ""
    funding: str = ""
    preview: bool = False
    isolated: bool = False


class OutputChannel:
    """Canal de saída com buffer, visível no painel Output do Codis."""

    def __init__(self, name: str, app: Any) -> None:
        """Cria um canal de saída nomeado vinculado à instância do app.

        Args:
            name: Nome do canal exibido no seletor do painel Output.
            app:  Instância de ``CodisApp`` usada para acessar o painel de output.
        """
        self._nome = name
        self._app = app
        self._linhas: list[str] = []
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        """Retorna o name do canal de saída."""
        return self._nome

    def append(self, text: str) -> None:
        """Adiciona texto (pode ter múltiplas lines) ao canal."""
        with self._lock:
            self._linhas.extend(text.splitlines())
        self._enviar_ui()

    def appendLine(self, text: str) -> None:
        """Adiciona exatamente uma linha."""
        self.append(text)

    def clear(self) -> None:
        """Limpa o canal."""
        with self._lock:
            self._linhas.clear()
        self._enviar_ui(limpar=True)

    def show(self) -> None:
        """Solicita ao UI que exiba este canal."""
        with contextlib.suppress(tk.TclError):
            output = getattr(self._app, "_output", None)
            if output and hasattr(output, "show_channel"):
                output.show_channel(self._nome)

    def get_all(self) -> str:
        """Retorna todas as mensagens do canal de saída."""
        with self._lock:
            return "\n".join(self._linhas)

    def _enviar_ui(self, *, limpar: bool = False) -> None:
        """Envia a linha mais recente do canal de saída para o painel de output da UI."""
        with contextlib.suppress(tk.TclError):
            output = getattr(self._app, "_output", None)
            if output and hasattr(output, "write_channel"):
                if limpar:
                    output.write_channel(self._nome, "", clear=True)
                else:
                    with self._lock:
                        ultima = self._linhas[-1] if self._linhas else ""
                    output.write_channel(self._nome, ultima)


class PluginStorage:
    """
    Armazenamento chave-valor persistente por plugin.

    Salvo em CONFIG_DIR/plugin_data/<plugin_id>.json.
    Escritas são agrupadas (debounce de 500 ms) para evitar thrashing.
    """

    def __init__(self, plugin_id: str) -> None:
        """Inicializa o storage do plugin, carregando dados persistidos do disco.

        Args:
            plugin_id: Identificador único do plugin; define o name do arquivo JSON.
        """
        self._plugin_id = plugin_id
        self._caminho = CONFIG_DIR / "plugin_data" / f"{plugin_id}.json"
        self._dados: dict[str, Any] = self._carregar()
        self._sujo = False
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None

    def get(self, key: str, pattern: Any = None) -> Any:
        """Retorna o valor armazenado em ``chave``, ou ``padrao`` se não existir."""
        with self._lock:
            return self._dados.get(key, pattern)

    def set(self, key: str, value: Any) -> None:
        """Armazena ``valor`` em ``chave`` e agenda gravação assíncrona no disco."""
        with self._lock:
            self._dados[key] = value
            self._sujo = True
        self._agendar_flush()

    def delete(self, key: str) -> None:
        """Exclui o item selecionado.

        Args:
            chave: Parâmetro da operação.
        """
        with self._lock:
            self._dados.pop(key, None)
            self._sujo = True
        self._agendar_flush()

    def keys(self) -> list[str]:
        """Retorna uma lista de todas as chaves armazenadas."""
        with self._lock:
            return list(self._dados.keys())

    def clear(self) -> None:
        """Remove todas as entradas do storage e agenda gravação assíncrona."""
        with self._lock:
            self._dados.clear()
            self._sujo = True
        self._agendar_flush()

    def flush(self) -> None:
        """Persiste os dados no disco imediatamente, sem aguardar o debounce."""
        with self._lock:
            if not self._sujo:
                return
            try:
                self._caminho.parent.mkdir(parents=True, exist_ok=True)
                self._caminho.write_text(
                    json.dumps(self._dados, indent=2,
                               ensure_ascii=False), "utf-8"
                )
                self._sujo = False
            except (OSError, ValueError, TypeError) as exc:
                logger.warning(
                    "PluginStorage[%s]: flush falhou: %s", self._plugin_id, exc
                )

    def _carregar(self) -> dict[str, Any]:
        """Lê e retorna os dados persistidos do arquivo JSON; retorna ``{}`` em caso de erro."""
        try:
            if self._caminho.exists():
                return json.loads(self._caminho.read_text("utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            logger.warning(
                "PluginStorage[%s]: falha ao carregar: %s", self._plugin_id, exc
            )
        return {}

    def _agendar_flush(self) -> None:
        """Cancela timer anterior e agenda novo flush com debounce de 500 ms."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(0.5, self.flush)
            self._timer.daemon = True
            self._timer.start()


class ProgressHandle:
    """Gerenciador de contexto para reportar progresso longo na status bar."""

    def __init__(self, title: str, app: Any, cancelavel: bool = False) -> None:
        """Cria um handle de progresso vinculado à status bar do app.

        Args:
            titulo:    Texto exibido na status bar durante a operação.
            app:       Instância de ``CodisApp`` com acesso à ``_status`` bar.
            cancelavel: Se ``True``, sinaliza que a operação pode ser cancelada.
        """
        self._titulo = title
        self._app = app
        self._cancelado = False
        self._ativo = False

    def __enter__(self) -> ProgressHandle:
        """Inicia o indicador de progresso na status bar."""
        self._ativo = True
        self._reportar(0, self._titulo)
        return self

    def __exit__(self, *_: Any) -> None:
        """Encerra o indicador e limpa a status bar."""
        self._ativo = False
        self._ocultar()

    def report(self, porcentagem: int, message: str = "") -> None:
        """Atualiza a status bar com a porcentagem e mensagem de progresso.

        Args:
            porcentagem: Valor de 0 a 100 indicando o progresso.
            mensagem:    Texto descritivo; usa o título do handle se omitido.
        """
        if self._ativo:
            self._reportar(porcentagem, message or self._titulo)

    @property
    def is_cancelled(self) -> bool:
        """Retorna ``True`` se :meth:`cancel` foi chamado."""
        return self._cancelado

    def cancel(self) -> None:
        """Sinaliza que a operação foi cancelada (consultável via :attr:`is_cancelled`)."""
        self._cancelado = True

    def _reportar(self, pct: int, msg: str) -> None:
        with contextlib.suppress(tk.TclError):
            status = getattr(self._app, "_status", None)
            if status:
                status.notify(f"⏳ {msg} ({pct}%)", duration=0, level="info")

    def _ocultar(self) -> None:
        with contextlib.suppress(tk.TclError):
            status = getattr(self._app, "_status", None)
            if status:
                status.notify("", duration=1, level="info")


class PluginLogger:
    """Logger estruturado com escopo por plugin, espelhado no OutputChannel."""

    def __init__(self, plugin_id: str, canal: OutputChannel | None = None) -> None:
        """Cria um logger com namespace ``Codis.plugin.<plugin_id>``.

        Args:
            plugin_id: ID do plugin; define o namespace do logger.
            canal:     Canal de output opcional para espelhar as mensagens.
        """
        self._log = logging.getLogger(f"Codis.plugin.{plugin_id}")
        self._canal = canal

    def debug(self, msg: str, *args: Any) -> None:
        """Registra mensagem de nível DEBUG no logger do plugin."""
        self._log.debug(msg, *args)

    def info(self, msg: str, *args: Any) -> None:
        """Registra mensagem de nível INFO e espelha no OutputChannel, se configurado."""
        self._log.info(msg, *args)
        if self._canal:
            self._canal.appendLine(f"[INFO] {msg % args if args else msg}")

    def warning(self, msg: str, *args: Any) -> None:
        """Registra mensagem de nível WARNING e espelha no OutputChannel, se configurado."""
        self._log.warning(msg, *args)
        if self._canal:
            self._canal.appendLine(f"[AVISO] {msg % args if args else msg}")

    def error(self, msg: str, *args: Any) -> None:
        """Registra mensagem de nível ERROR e espelha no OutputChannel, se configurado."""
        self._log.error(msg, *args)
        if self._canal:
            self._canal.appendLine(f"[ERRO] {msg % args if args else msg}")

    def exception(self, msg: str, *args: Any) -> None:
        """Registra exceção com traceback e espelha no OutputChannel, se configurado."""
        self._log.exception(msg, *args)
        if self._canal:
            self._canal.appendLine(f"[EXCEÇÃO] {msg % args if args else msg}")


class PluginAPI:
    """

    Cada plugin recebe uma instância de PluginAPI no ``activate(api)``.
    na desativação (``_teardown``), prevenindo vazamentos.

    Início rápido
    -------------
    ::

        # ~/.codis/plugins/meu_plugin/plugin.py
        from src.plugins.api import PluginManifest

        MANIFEST = PluginManifest(
            name="Meu Plugin",
            version="1.0.0",
            author="Você",
            description="Faz algo incrível.",
            icon="",
            tags=["produtividade"],
        )

        def activate(api):
            api.register_command("meu_plugin.ola", "Dizer Olá",
                                 lambda: api.show_message("Olá!"))
            api.add_keybinding("<Control-shift-h>", "meu_plugin.ola")

        def deactivate():
            pass  # limpeza feita automaticamente pela API

    Superfície da API
    -----------------
    Temas            register_theme
    Comandos         register_command
    Linguagens       register_language, register_formatter,
                     register_code_action_provider, register_symbol_provider
    Sidebar          add_sidebar_panel
    Status bar       add_status_item, update_status_item, remove_status_item
    Atalhos          add_keybinding / bind_key
    Menus            add_menu_item, add_context_menu_item
    Decoradores      register_decorator, add_gutter_icon, add_inlay_hint
    Realces          add_highlight, remove_highlight
    Eventos          on, once, off, emit
    Editor           get_active_editor, get_all_editors, get_text, set_text,
                     insert_at_cursor, get_selection, replace_selection,
                     get_cursor_position, set_cursor_position, get_line,
                     get_line_count, get_active_file, get_active_lang,
                     open_file, save_file, get_visible_range, scroll_to_line
    UI               show_message, show_error, show_info, show_warning,
                     ask_input, ask_yesno, ask_choice, open_file_dialog,
                     open_files_dialog, save_file_dialog, ask_directory,
                     with_progress
    i18n             extend_i18n, t
    Config           get_config, set_config, watch_config, migrate_config,
                     add_settings_section
    Workspace        get_workspace_folder, get_workspace_files, find_files
    Storage          get_storage
    Output           create_output_channel
    Logger           get_logger
    IPC              get_plugin, call_plugin
    Clipboard        read_clipboard, write_clipboard
    Timers           set_timeout, set_interval, clear_timer, debounce, throttle
    """

    CORE_EVENTS: frozenset[str] = frozenset(
        {
            "file_open",
            "file_save",
            "file_close",
            "text_change",
            "cursor_move",
            "theme_change",
            "lang_change",
            "app_ready",
            "app_exit",
            "before_save",
            "after_save",
            "before_format",
            "after_format",
            "lint_complete",
            "terminal_line",
            "git_commit",
            "tab_change",
            "workspace_set",
            "workspace_close",
            "plugin_loaded",
            "plugin_unloaded",
            "selection_change",
            "fold_change",
        }
    )

    def __init__(self, app: Any, plugin_id: str = "") -> None:
        """Inicializa a API de plugins com referência ao gerenciador principal.

        Args:
            manager: Instância do AppManager (app.py).
        """
        self._app = app
        self._plugin_id = plugin_id
        self._t = _t

        self._themes: dict[str, Any] = {}
        self._commands: dict[str, tuple[str, Any]] = {}
        self._languages: dict[str, Any] = {}
        self._sidebar_panels: list[tuple] = []
        self._status_items: dict[str, Any] = {}
        self._keybindings: list[tuple[str, str]] = []
        self._menu_items: list[tuple[str, str, Any]] = []
        self._context_menu_items: list[tuple] = []
        self._decorators: list[tuple[str, Any]] = []
        self._formatters: dict[str, Any] = {}
        self._code_actions: dict[str, Any] = {}
        self._symbol_providers: dict[str, Any] = {}
        self._hooks: dict[str, list[Any]] = collections.defaultdict(list)
        self._config_watchers: dict[str, list[Any]
                                    ] = collections.defaultdict(list)
        self._timers: dict[int, threading.Timer] = {}
        self._timer_counter: int = 0
        self._lock = threading.Lock()
        self._cleanup_hooks: list[Any] = []
        self._output_channels: dict[str, OutputChannel] = {}
        self._storage: PluginStorage | None = None

    def _register_cleanup(self, fn: Any) -> None:
        """Executa a operação 'register cleanup'.

        Args:
            fn: Parâmetro da operação.
        """
        self._cleanup_hooks.append(fn)

    def _teardown(self) -> None:
        """Chamado pelo PluginLoader na desativação do plugin."""
        with contextlib.suppress(tk.TclError), self._lock:
            for timer in self._timers.values():
                timer.cancel()
            self._timers.clear()
        for fn in self._cleanup_hooks:
            with contextlib.suppress(tk.TclError):
                fn()
        if self._storage is not None:
            with contextlib.suppress(tk.TclError):
                self._storage.flush()

    def register_theme(self, theme_id: str, theme: Any) -> None:
        """
        Registra um tema de cores.

        ``theme`` deve ser instância de ``src.theme.base.Theme``.
        Re-registrar o mesmo ID atualiza o tema existente.
        """
        _patch_theme(theme)
        self._themes[theme_id] = theme
        THEMES[theme_id] = theme
        if theme_id not in THEME_ORDER:
            THEME_ORDER.append(theme_id)

    def register_command(
        self,
        cmd_id: str,
        label: str,
        handler: Any,
        category: str = "Plugin",
        *,
        when: Any | None = None,
    ) -> None:
        """
        Registra um comando na Paleta de Comandos.

        ``cmd_id``   : identificador único com ponto (ex: ``meu_plugin.rodar``).
        ``label``    : texto exibido na paleta.
        ``handler``  : função sem argumentos chamada na execução.
        ``category`` : prefixo na paleta (padrão ``"Plugin"``).
        ``when``     : predicado opcional - comando só aparece quando retorna True.
        """
        rotulo = f"{category}: {label}"
        fn = handler if when is None else lambda: handler() if when() else None
        self._commands[cmd_id] = (rotulo, fn)
        self._app._command_registry[cmd_id] = (rotulo, fn)

    def register_language(
        self,
        lang_id: str,
        patterns: list[tuple[str, str]] | None = None,
        keywords: list[str] | None = None,
        label: str | None = None,
        extensions: list[str] | None = None,
        comment: str | None = None,
        comment_block: tuple[str, str] | None = None,
        *,
        indent_rules: dict[str, Any] | None = None,
        bracket_pairs: list[tuple[str, str]] | None = None,
        lsp_command: str | None = None,
    ) -> None:
        """
        Registra uma nova linguagem de programação.

        ``lang_id``      : identificador único (ex: ``"minha_lang"``).
        ``patterns``     : lista de ``(tag, regex)`` para highlight.
        ``keywords``     : palavras reservadas.
        ``label``        : name exibido no seletor de linguagem.
        ``extensions``   : extensões de arquivo (com ponto) para detecção automática.
        ``comment``      : prefixo de comentário de linha (ex: ``"//"``).
        ``comment_block``: tupla ``(abre, fecha)`` para bloco.
        ``indent_rules`` : regras de indentação automática.
        ``bracket_pairs``: pares de fechamento automático.
        ``lsp_command``  : comando shell para servidor LSP.
        """
        if patterns:
            self._languages[lang_id] = patterns
            LANG_RE[lang_id] = _pat(patterns)
        if keywords:
            LANG_KEYWORDS[lang_id] = keywords
        if label:
            LANG_LABEL[lang_id] = label
        if extensions:
            for ext in extensions:
                EXT_LANG[ext.lower()] = lang_id
        if comment:
            LANG_COMMENT[lang_id] = comment

        meta: dict[str, Any] = {}
        if comment_block:
            meta["comment_block"] = comment_block
        if indent_rules:
            meta["indent_rules"] = indent_rules
        if bracket_pairs:
            meta["bracket_pairs"] = bracket_pairs
        if lsp_command:
            meta["lsp_command"] = lsp_command
        if meta:
            self._languages.setdefault(f"{lang_id}.__meta__", meta)

    def register_formatter(
        self,
        lang_id: str,
        formatter: Any,
        *,
        priority: int = 0,
    ) -> None:
        """
        Registra um formatador de código para uma linguagem.

        ``formatter(texto) -> texto_formatado`` - lança em caso de falha.
        ``priority``     : prioridade quando múltiplos formatadores existem (maior = preferido).
        """
        self._formatters[lang_id] = (formatter, priority)
        reg = getattr(self._app, "_formatter_registry", None)
        if reg is not None:
            reg[lang_id] = formatter

    def register_code_action_provider(
        self,
        lang_id: str,
        provider: Any,
    ) -> None:
        """
        Registra um provedor de quick-fix / refatoração para uma linguagem.

        ``provider(texto, linha, col) -> list[dict]``
        Cada dict: ``{"title": str, "command": str, "arguments": [...]}``.
        """
        self._code_actions[lang_id] = provider
        reg = getattr(self._app, "_code_action_registry", None)
        if reg is not None:
            reg[lang_id] = provider

    def register_symbol_provider(
        self,
        lang_id: str,
        provider: Any,
    ) -> None:
        """
        Registra um provedor de símbolos para a visualização de esboço.

        ``provider(texto) -> list[dict]``
        Cada dict: ``{"name": str, "kind": str, "line": int, "children": [...]}``.
        """
        self._symbol_providers[lang_id] = provider
        reg = getattr(self._app, "_symbol_provider_registry", None)
        if reg is not None:
            reg[lang_id] = provider

    def add_sidebar_panel(
        self,
        panel_id: str,
        icon: str,
        factory: Any,
        tooltip: str = "",
        *,
        title: str = "",
        position: str = "bottom",
    ) -> None:
        """
        Adiciona um painel na activity bar / sidebar.

        ``factory(parent) -> tk.Widget`` - constrói o widget do painel.
        ``title``        : título exibido no cabeçalho do painel.
        ``position``     : ``"top"`` ou ``"bottom"`` na barra de atividades.
        """
        entry = (panel_id, icon, factory, tooltip, title, position)
        self._sidebar_panels.append(entry)
        with contextlib.suppress(tk.TclError):
            if hasattr(self._app, "_register_plugin_panel"):
                self._app._register_plugin_panel(
                    panel_id, icon, factory, tooltip)

    def add_status_item(
        self,
        item_id: str,
        text_fn: Any,
        click_fn: Any | None = None,
        side: str = "right",
        *,
        tooltip: str = "",
        priority: int = 0,
    ) -> None:
        """
        Adiciona um item na status bar.

        ``text_fn()`` é chamado periodicamente para atualizar o texto.
        ``click_fn()`` é chamado ao clicar no item.
        ``side``     : ``"left"`` ou ``"right"``.
        ``tooltip``  : texto exibido ao passar o mouse sobre o item.
        ``priority`` : prioridade de ordenação (maior = mais próximo da borda).
        """
        self._status_items[item_id] = (
            text_fn, click_fn, side, tooltip, priority)
        status = getattr(self._app, "_status", None)
        if status and hasattr(status, "add_plugin_item"):
            with contextlib.suppress(tk.TclError):
                status.add_plugin_item(item_id, text_fn, click_fn, side)

    def update_status_item(self, item_id: str) -> None:
        """Força a atualização visual de um item da status bar."""
        status = getattr(self._app, "_status", None)
        if status and hasattr(status, "refresh_item"):
            with contextlib.suppress(tk.TclError):
                status.refresh_item(item_id)

    def remove_status_item(self, item_id: str) -> None:
        """Remove um item previamente adicionado na status bar."""
        self._status_items.pop(item_id, None)
        status = getattr(self._app, "_status", None)
        if status and hasattr(status, "remove_plugin_item"):
            with contextlib.suppress(tk.TclError):
                status.remove_plugin_item(item_id)

    def add_keybinding(
        self,
        key_combo: str,
        cmd_id_or_fn: str | Any,
        *,
        when: Any | None = None,
    ) -> None:
        """
        Vincula um atalho de teclado a um ID de comando ou callable.

        ``key_combo``: string de evento Tkinter, ex: ``"<Control-shift-p>"``.
        ``when``     : predicado opcional - atalho só ativa quando retorna True.
        """
        if isinstance(cmd_id_or_fn, str):
            self._keybindings.append((key_combo, cmd_id_or_fn))
            with contextlib.suppress(tk.TclError):
                if hasattr(self._app, "_bind_key"):
                    self._app._bind_key(key_combo, cmd_id_or_fn)
        else:
            fn = (
                cmd_id_or_fn
                if when is None
                else lambda: cmd_id_or_fn() if when() else None
            )
            sintetico = f"__plugin_{self._plugin_id}_{id(fn)}"
            self._commands[sintetico] = (sintetico, fn)
            self._app._command_registry[sintetico] = (sintetico, fn)
            self._keybindings.append((key_combo, sintetico))
            with contextlib.suppress(tk.TclError):
                if hasattr(self._app, "_bind_key"):
                    self._app._bind_key(key_combo, sintetico)

    bind_key = add_keybinding

    def add_menu_item(
        self,
        menu_path: str,
        label: str,
        handler: Any,
        *,
        accelerator: str = "",
        before: str = "",
        after: str = "",
    ) -> None:
        """
        Adiciona um item a um menu.

        ``menu_path``  : ex. ``"Ferramentas"``, ``"Plugins/Meu Plugin"``.
        ``accelerator``: texto de atalho exibido no menu (ex: ``"Ctrl+K"``).
        ``before``     : ID de outro item - insere antes dele.
        ``after``      : ID de outro item - insere após ele.
        """
        self._menu_items.append(
            (menu_path, label, handler, accelerator, before, after))
        with contextlib.suppress(tk.TclError):
            if hasattr(self._app, "_add_plugin_menu_item"):
                self._app._add_plugin_menu_item(menu_path, label, handler)

    def add_context_menu_item(
        self,
        context: str,
        label: str,
        handler: Any,
        *,
        when: Any | None = None,
    ) -> None:
        """
        Adiciona item a menu de contexto.

        ``context``: ``"editor"``, ``"file_tree"``, ``"tab"``, ``"terminal"``.
        """
        self._context_menu_items.append((context, label, handler, ""))
        with contextlib.suppress(tk.TclError):
            if hasattr(self._app, "_add_context_menu_item"):
                self._app._add_context_menu_item(context, label, handler, when)

    def register_decorator(self, name: str, factory: Any) -> None:
        """Registra um decorador visual para o editor (anotações inline)."""
        self._decorators.append((name, factory))

    def add_gutter_icon(
        self,
        line: int,
        icon: str,
        tooltip: str = "",
        on_click: Any | None = None,
    ) -> None:
        """Adiciona ícone na calha do editor na linha ``line`` (base 1)."""
        with contextlib.suppress(tk.TclError):
            pane = self.get_active_editor()
            if pane and hasattr(pane, "add_gutter_icon"):
                pane.add_gutter_icon(line, icon, tooltip, on_click)

    def add_inlay_hint(
        self,
        line: int,
        col: int,
        text: str,
        *,
        fg: str = "#888888",
    ) -> None:
        """Adiciona hint inline (texto fantasma) após a posição (linha, col)."""
        with contextlib.suppress(tk.TclError):
            pane = self.get_active_editor()
            if pane and hasattr(pane, "add_inlay_hint"):
                pane.add_inlay_hint(line, col, text, fg=fg)

    def add_highlight(
        self,
        line_start: int,
        col_start: int,
        line_end: int,
        col_end: int,
        tag: str = "plugin_highlight",
        *,
        bg: str = "#ffff00",
        fg: str = "",
    ) -> None:
        """Realça uma região no editor ativo com uma tag colorida."""
        pane = self.get_active_editor()
        if pane:
            with contextlib.suppress(tk.TclError):
                kw: dict[str, str] = {"background": bg}
                if fg:
                    kw["foreground"] = fg
                pane.text.tag_configure(tag, **kw)
                pane.text.tag_add(
                    tag, f"{line_start}.{col_start}", f"{line_end}.{col_end}"
                )

    def remove_highlight(self, tag: str = "plugin_highlight") -> None:
        """Remove o highlight da tag especificada no editor ativo."""
        pane = self.get_active_editor()
        if pane:
            with contextlib.suppress(tk.TclError):
                pane.text.tag_remove(tag, "1.0", "end")

    def on(self, event: str, handler: Any, *, priority: int = 0) -> None:
        """
        Assina um evento do editor ou do ciclo de vida.

        Eventos do núcleo: ``file_open``, ``file_save``, ``file_close``,
        ``text_change``, ``cursor_move``, ``theme_change``, ``lang_change``,
        ``app_ready``, ``before_save``, ``after_save``, ``before_format``,
        ``after_format``, ``lint_complete``, ``terminal_line``, ``git_commit``,
        ``tab_change``, ``workspace_set``, ``plugin_loaded``, etc.

        Eventos customizados de outros plugins também são suportados.
        ``priority``: prioridade de chamada (maior = chamado primeiro).
        """

        self._hooks[event].append((priority, handler))
        self._hooks[event].sort(key=lambda x: -x[0])

        if handler not in self._app._plugin_hooks[event]:
            self._app._plugin_hooks[event].append(handler)

    def once(self, event: str, handler: Any) -> None:
        """Assina um evento exatamente uma vez; desinscreve automaticamente."""
        disparado = [False]

        def _wrapper(*args: Any, **kwargs: Any) -> None:
            if not disparado[0]:
                disparado[0] = True
                self.off(event, _wrapper)
                handler(*args, **kwargs)

        self.on(event, _wrapper)

    def off(self, event: str, handler: Any) -> None:
        """Remove o handler registrado para o evento de plugin."""

        self._hooks[event] = [
            item
            for item in self._hooks[event]
            if (item[1] if isinstance(item, tuple) else item) is not handler
        ]
        with contextlib.suppress(tk.TclError):
            self._app._plugin_hooks[event].remove(handler)

    def emit(self, event: str, *args: Any, **kwargs: Any) -> None:
        """

        Use nomes com namespace para evitar colisões: ``"meu_plugin.dados_prontos"``.
        """
        for handler in list(self._app._plugin_hooks.get(event, [])):
            with contextlib.suppress(tk.TclError):
                handler(*args, **kwargs)

    def get_active_editor(self) -> Any | None:
        """Retorna o EditorPane ativo, ou None."""
        with contextlib.suppress(tk.TclError):
            if hasattr(self._app, "_e"):
                return self._app._e()
        return getattr(self._app, "_active_pane", None)

    def get_all_editors(self) -> list[Any]:
        """Retorna a lista de todos os painéis de editor abertos."""
        with contextlib.suppress(tk.TclError):
            if hasattr(self._app, "_all_panes"):
                return list(self._app._all_panes())
        return []

    def get_text(self) -> str:
        """Retorna o texto completo do editor ativo."""
        pane = self.get_active_editor()
        if pane:
            with contextlib.suppress(tk.TclError):
                return pane.text.get("1.0", "end-1c")
        return ""

    def set_text(self, text: str) -> None:
        """Substitui todo o conteúdo do editor ativo pelo texto fornecido."""
        pane = self.get_active_editor()
        if pane:
            with contextlib.suppress(tk.TclError):
                pane.text.delete("1.0", "end")
                pane.text.insert("1.0", text)

    def insert_at_cursor(self, text: str) -> None:
        """Insere texto na posição atual do cursor."""
        pane = self.get_active_editor()
        if pane:
            with contextlib.suppress(tk.TclError):
                pane.text.insert(tk.INSERT, text)

    def get_selection(self) -> str:
        """Retorna o texto selecionado (string vazia se não houver seleção)."""
        pane = self.get_active_editor()
        if pane:
            with contextlib.suppress(tk.TclError):
                return pane.text.get(tk.SEL_FIRST, tk.SEL_LAST)
        return ""

    def replace_selection(self, text: str) -> None:
        """Substitui a seleção atual pelo texto informado."""
        pane = self.get_active_editor()
        if pane:
            with contextlib.suppress(tk.TclError, Exception):
                pane.text.delete(tk.SEL_FIRST, tk.SEL_LAST)
                pane.text.insert(tk.INSERT, text)

    def get_cursor_position(self) -> tuple[int, int]:
        """Retorna (linha, coluna) do cursor (linha base 1, coluna base 0)."""
        pane = self.get_active_editor()
        if pane:
            with contextlib.suppress(tk.TclError):
                idx = pane.text.index(tk.INSERT)
                ln, col = map(int, idx.split("."))
                return ln, col
        return (1, 0)

    def set_cursor_position(self, line: int, col: int = 0) -> None:
        """Move o cursor para (linha, coluna)."""
        pane = self.get_active_editor()
        if pane:
            with contextlib.suppress(tk.TclError):
                pane.text.mark_set(tk.INSERT, f"{line}.{col}")
                pane.text.see(tk.INSERT)

    def get_line(self, line: int) -> str:
        """Retorna o texto da linha ``line`` (base 1) no editor ativo."""
        pane = self.get_active_editor()
        if pane:
            with contextlib.suppress(tk.TclError):
                return pane.text.get(f"{line}.0", f"{line}.end")
        return ""

    def get_line_count(self) -> int:
        """Retorna o total de lines no editor ativo."""
        pane = self.get_active_editor()
        if pane:
            with contextlib.suppress(tk.TclError):
                return int(pane.text.index("end-1c").split(".")[0])
        return 0

    def get_active_file(self) -> Path | None:
        """Retorna o Path do arquivo ativo, ou None."""
        pane = self.get_active_editor()
        return getattr(pane, "path", None) if pane else None

    def get_active_lang(self) -> str:
        """Retorna o ID de linguagem do editor ativo (ex: ``"python"``)."""
        pane = self.get_active_editor()
        return getattr(pane, "lang", "text") if pane else "text"

    def open_file(self, path: str | Path) -> None:
        """Solicita ao Codis que abra um arquivo em nova aba."""
        with contextlib.suppress(tk.TclError):
            if hasattr(self._app, "_open_file"):
                self._app._open_file(Path(path))

    def save_file(self) -> None:
        """Salva o arquivo ativo."""
        with contextlib.suppress(tk.TclError):
            if hasattr(self._app, "_save"):
                self._app._save()

    def get_visible_range(self) -> tuple[int, int]:
        """Retorna (primeira_linha_visível, última_linha_visível) no editor ativo."""
        pane = self.get_active_editor()
        if pane:
            with contextlib.suppress(tk.TclError):
                top = pane.text.index("@0,0")
                base = pane.text.index(f"@0,{pane.text.winfo_height()}")
                return int(top.split(".")[0]), int(base.split(".")[0])
        return (1, 1)

    def scroll_to_line(self, line: int) -> None:
        """Rola o editor ativo até a linha ``line`` ficar visível."""
        pane = self.get_active_editor()
        if pane:
            with contextlib.suppress(tk.TclError):
                pane.text.see(f"{line}.0")

    def show_message(
        self,
        message: str,
        duration: int = 3000,
        *,
        level: str = "info",
    ) -> None:
        """
        Exibe notificação transitória na status bar.

        ``duration``: tempo em ms antes de desaparecer (0 = permanente).
        ``level``   : ``"info"`` | ``"warning"`` | ``"error"``.
        """
        status = getattr(self._app, "_status", None)
        if status:
            with contextlib.suppress(tk.TclError):
                status.notify(message, duration=duration, level=level)

    @staticmethod
    def show_error(title: str, message: str) -> None:
        """Exibe diálogo modal de erro."""
        messagebox.showerror(title, message)

    @staticmethod
    def show_info(title: str, message: str) -> None:
        """Exibe diálogo modal informativo."""
        messagebox.showinfo(title, message)

    @staticmethod
    def show_warning(title: str, message: str) -> None:
        """Exibe diálogo modal de aviso."""
        messagebox.showwarning(title, message)

    def ask_input(
        self,
        prompt: str,
        title: str = "Input",
        *,
        initial: str = "",
    ) -> str | None:
        """Diálogo de entrada de texto. Retorna string ou None."""
        return simpledialog.askstring(title, prompt, initialvalue=initial)

    @staticmethod
    def ask_yesno(question: str, title: str = "Confirmar") -> bool:
        """Diálogo sim/não. Retorna True se o usuário clicou Sim."""
        return bool(messagebox.askyesno(title, question))

    def ask_choice(
        self,
        title: str,
        prompt: str,
        choices: list[str],
    ) -> str | None:
        """
        Diálogo de escolha. Retorna o item selecionado ou None.

        Usa seletor nativo da aplicação quando disponível;
        caso contrário faz fallback para entrada de texto.
        """
        with contextlib.suppress(tk.TclError):
            if hasattr(self._app, "_show_choice_dialog"):
                return self._app._show_choice_dialog(title, prompt, choices)
        options = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(choices))
        raw = simpledialog.askstring(
            title, f"{prompt}\n\n{options}\n\nDigite o número ou name:"
        )
        if raw is None:
            return None
        raw = raw.strip()
        if raw.isdigit():
            idx = int(raw) - 1
            return choices[idx] if 0 <= idx < len(choices) else None
        return raw if raw in choices else None

    @staticmethod
    def open_file_dialog(**kwargs: Any) -> Path | None:
        """Diálogo de abrir arquivo. Retorna Path ou None."""
        p = filedialog.askopenfilename(**kwargs)
        return Path(p) if p else None

    @staticmethod
    def open_files_dialog(**kwargs: Any) -> list[Path]:
        """Diálogo de abrir múltiplos arquivos. Retorna lista de Path."""
        paths = filedialog.askopenfilenames(**kwargs)
        return [Path(p) for p in paths] if paths else []

    @staticmethod
    def save_file_dialog(**kwargs: Any) -> Path | None:
        """Diálogo de salvar arquivo. Retorna Path ou None."""
        p = filedialog.asksaveasfilename(**kwargs)
        return Path(p) if p else None

    @staticmethod
    def ask_directory(**kwargs: Any) -> Path | None:
        """Diálogo de selecionar pasta. Retorna Path ou None."""
        p = filedialog.askdirectory(**kwargs)
        return Path(p) if p else None

    def with_progress(self, title: str, cancelavel: bool = False) -> ProgressHandle:
        """Retorna gerenciador de contexto que exibe indicador de progresso."""
        return ProgressHandle(title, self._app, cancelavel=cancelavel)

    @staticmethod
    def extend_i18n(locale: str, translations: dict[str, str], label: str = "") -> None:
        """
        Adiciona traduções para um locale.

        ``locale``  : tag BCP-47 (ex: ``"pt-BR"``, ``"tl"``, ``"ja"``).
        ``translations``: ``{chave: string_traduzida}``.
        ``label``   : name legível exibido no seletor de idioma da UI
                      (ex: ``"Tagalog (Filipino)"``). Quando omitido, usa
                      o valor da chave ``"lang.name"`` nas translations,
                      ou o próprio código do locale.

        Se o locale ainda não existir, ele é automaticamente adicionado
        à lista de idiomas disponíveis no Settings.
        """
        I18n.get_instance().extend(locale, translations, label=label)

    @staticmethod
    def t(key: str, **kwargs: Any) -> str:
        """Traduz ``key`` no locale atual."""
        return _t(key, **kwargs)

    def get_config(self, key: str, default: Any = None) -> Any:
        """Lê um valor de configuração."""
        return self._app._cfg.get(key, default)

    def set_config(self, key: str, value: Any) -> None:
        """Grava um valor de configuração e persiste no disco."""
        self._app._cfg.set(key, value)
        self._app._cfg.save()

    def watch_config(self, key: str, callback: Any) -> None:
        """
        Registra callback chamado quando ``key`` muda.

        """
        self._config_watchers[key].append(callback)
        with contextlib.suppress(tk.TclError):
            if hasattr(self._app._cfg, "watch"):
                self._app._cfg.watch(key, callback)

    def migrate_config(self, migrations: dict[str, str]) -> None:
        """
        Renomeia chaves de configuração.

        ``migrations``: ``{chave_antiga: chave_nova}``.
        Útil quando o plugin é atualizado e suas chaves de config mudam.
        """
        for chave_antiga, chave_nova in migrations.items():
            _AUSENTE = object()
            value = self._app._cfg.get(chave_antiga, _AUSENTE)
            if value is not _AUSENTE:
                self._app._cfg.set(chave_nova, value)
                with contextlib.suppress(tk.TclError):
                    self._app._cfg._data.pop(chave_antiga, None)
        self._app._cfg.save()

    def add_settings_section(
        self,
        category: str,
        settings: list[Any],
    ) -> None:
        """
        Adiciona seção de configurações do plugin no painel de Settings.

        Cada entrada pode ser dict ou tupla de 8 campos:
        ``(key, label, desc, kind, default, opts, type_hint, example)``

        ``kind``: ``toggle`` | ``combo`` | ``slider`` | ``text`` | ``number`` | ``path``

        Forma dict (recomendada)::

            api.add_settings_section("Meu Plugin", [
                {
                    "key":         "meu_plugin.timeout",
                    "label":       "Timeout (segundos)",
                    "description": "Tempo máximo de espera.",
                    "kind":        "number",
                    "default":     30,
                },
            ])
        """
        from src.ui.settings_editor import SettingsEditor

        for entry in settings:
            if isinstance(entry, dict):
                entry = (
                    entry.get("key", ""),
                    f"{category} \u203a " + entry.get("label", ""),
                    entry.get("description", entry.get("desc", "")),
                    entry.get("kind", entry.get("type", "text")),
                    entry.get("default"),
                    entry.get("opts", entry.get("choices")),
                    entry.get("type_hint"),
                    entry.get("example"),
                )
            if len(entry) >= 4:
                padded = tuple(entry) + (None,) * (8 - len(entry))
                SettingsEditor._EXTRA_SCHEMA.append(padded)

        for entry in settings:
            key = (
                entry.get("key")
                if isinstance(entry, dict)
                else (entry[0] if entry else None)
            )
            pattern = (
                entry.get("default")
                if isinstance(entry, dict)
                else (entry[4] if len(entry) > 4 else None)
            )
            if key and pattern is not None:
                _AUSENTE = object()
                if self._app._cfg.get(key, _AUSENTE) is _AUSENTE:
                    with contextlib.suppress(tk.TclError):
                        self._app._cfg._data.setdefault(key, pattern)

    def get_workspace_folder(self) -> Path | None:
        """Retorna a pasta raiz do workspace atual, ou None."""
        return getattr(self._app, "_workspace", None)

    def get_workspace_files(
        self,
        pattern: str = "**/*",
        *,
        exclude: list[str] | None = None,
        max_results: int = 5000,
    ) -> list[Path]:
        """
        Retorna arquivos do workspace que correspondem ao padrão glob.

        ``exclude``: lista de padrões glob a ignorar (ex: ``["**/.git/**"]``).
        """
        root = self.get_workspace_folder()
        if root is None:
            return []
        excluir = set(exclude or [])
        resultados: list[Path] = []
        for p in root.glob(pattern):
            if not p.is_file():
                continue
            if any(p.match(ex) for ex in excluir):
                continue
            resultados.append(p)
            if len(resultados) >= max_results:
                break
        return resultados

    def find_files(
        self,
        pattern: str,
        *,
        root: Path | None = None,
        max_results: int = 1000,
    ) -> list[Path]:
        """Encontra arquivos via glob sob ``root`` (padrão: workspace)."""
        root = root or self.get_workspace_folder()
        if root is None:
            return []
        return list(root.glob(pattern))[:max_results]

    def get_storage(self) -> PluginStorage:
        """
        Retorna armazenamento persistente chave-valor para este plugin.

        Criado sob demanda; descarregado automaticamente na desativação.
        """
        if self._storage is None:
            self._storage = PluginStorage(self._plugin_id or "unknown")
        return self._storage

    def create_output_channel(self, name: str) -> OutputChannel:
        """
        Cria (ou recupera) um canal de saída nomeado.

        O canal aparece no Output Panel; texto escrito nele é preservado
        entre recarregamentos.
        """
        if name not in self._output_channels:
            canal = OutputChannel(name, self._app)
            self._output_channels[name] = canal
            with contextlib.suppress(tk.TclError):
                output = getattr(self._app, "_output", None)
                if output and hasattr(output, "register_channel"):
                    output.register_channel(name)
        return self._output_channels[name]

    def get_logger(self, *, with_channel: bool = True) -> PluginLogger:
        """
        Retorna logger estruturado para este plugin.

        Se ``with_channel=True`` (padrão), saída também vai ao OutputChannel
        com o name do plugin.
        """
        canal: OutputChannel | None = None
        if with_channel and self._plugin_id:
            canal = self.create_output_channel(self._plugin_id)
        return PluginLogger(self._plugin_id or "plugin", canal)

    def get_plugin(self, plugin_id: str) -> Any | None:
        """
        Retorna o módulo de outro plugin carregado, ou None.

        Use para comunicação entre plugins. Prefira ``call_plugin``
        para uma fronteira de API mais segura.
        """
        loader = getattr(self._app, "_plugin_loader", None)
        if loader:
            with contextlib.suppress(tk.TclError):
                return loader.plugins.get(plugin_id)
        return None

    def call_plugin(
        self,
        plugin_id: str,
        method: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """

        não estiver disponível.
        """
        mod = self.get_plugin(plugin_id)
        if mod is None:
            return None
        fn = getattr(mod, method, None)
        if callable(fn):
            return fn(*args, **kwargs)
        return None

    def read_clipboard(self) -> str:
        """Retorna o texto atual da área de transferência."""
        with contextlib.suppress(tk.TclError):
            root = (
                self._app.winfo_toplevel()
                if hasattr(self._app, "winfo_toplevel")
                else self._app
            )
            return root.clipboard_get()
        return ""

    def write_clipboard(self, text: str) -> None:
        """Define o texto da área de transferência."""
        with contextlib.suppress(tk.TclError):
            root = (
                self._app.winfo_toplevel()
                if hasattr(self._app, "winfo_toplevel")
                else self._app
            )
            root.clipboard_clear()
            root.clipboard_append(text)

    def set_timeout(self, delay_ms: int, fn: Any) -> int:
        """
        Chama ``fn()`` uma vez após ``delay_ms`` milissegundos.

        Retorna um handle para uso em ``clear_timer()``.
        """
        with self._lock:
            self._timer_counter += 1
            handle = self._timer_counter

        def _disparar() -> None:
            with self._lock:
                self._timers.pop(handle, None)
            with contextlib.suppress(tk.TclError):
                fn()

        timer = threading.Timer(delay_ms / 1000.0, _disparar)
        timer.daemon = True
        with self._lock:
            self._timers[handle] = timer
        timer.start()
        return handle

    def set_interval(self, interval_ms: int, fn: Any) -> int:
        """
        Chama ``fn()`` repetidamente a cada ``interval_ms`` milissegundos.

        Retorna um handle para uso em ``clear_timer()``.
        Para automaticamente quando o plugin é desativado.
        """
        with self._lock:
            self._timer_counter += 1
            handle = self._timer_counter
        parado = threading.Event()

        def _loop() -> None:
            if parado.is_set():
                return
            with contextlib.suppress(tk.TclError):
                fn()
            if not parado.is_set():
                t = threading.Timer(interval_ms / 1000.0, _loop)
                t.daemon = True
                with self._lock:
                    self._timers[handle] = t
                t.start()

        timer = threading.Timer(interval_ms / 1000.0, _loop)
        timer.daemon = True
        with self._lock:
            self._timers[handle] = timer
        self._register_cleanup(parado.set)
        timer.start()
        return handle

    def clear_timer(self, handle: int) -> None:
        """Cancela um timer criado com ``set_timeout`` ou ``set_interval``."""
        with self._lock:
            timer = self._timers.pop(handle, None)
        if timer is not None:
            timer.cancel()

    def debounce(self, delay_ms: int, fn: Any) -> Any:
        """
        Retorna versão debounced de ``fn``.

        Chamadas repetidas dentro de ``delay_ms`` ms reiniciam o timer;
        ``fn`` só é chamada uma vez após a última chamada.
        """
        state: dict[str, Any] = {"handle": None}
        lock = threading.Lock()

        def _debounced(*args: Any, **kwargs: Any) -> None:
            with lock:
                if state["handle"] is not None:
                    self.clear_timer(state["handle"])

                def _disparar() -> None:
                    with lock:
                        state["handle"] = None
                    fn(*args, **kwargs)

                state["handle"] = self.set_timeout(delay_ms, _disparar)

        return _debounced

    @staticmethod
    def throttle(interval_ms: int, fn: Any) -> Any:
        """
        Retorna versão throttled de ``fn``.

        ``fn`` é chamada no máximo uma vez a cada ``interval_ms`` ms,
        com os argumentos mais recentes.
        """
        ultima: dict[str, float] = {"t": 0.0}

        def _throttled(*args: Any, **kwargs: Any) -> None:
            agora = time.monotonic()
            if agora - ultima["t"] >= interval_ms / 1000.0:
                ultima["t"] = agora
                fn(*args, **kwargs)

        return _throttled


_SANDBOX_BLOCKED_MODULES: frozenset[str] = frozenset(
    {
        "ctypes",
        "ctypes.util",
        "ctypes.cdll",
        "subprocess",
        "socket",
        "ssl",
        "multiprocessing",
        "pty",
        "termios",
        "tty",
        "winreg",
        "winrm",
        "_winapi",
        "msvcrt",
        "cffi",
        "cffi.api",
        "os",
        "importlib",
        "importlib.util",
    }
)

_SANDBOX_WARN_MODULES: frozenset[str] = frozenset(
    {
        "pathlib",
        "shutil",
        "requests",
        "urllib",
        "urllib.request",
        "http.client",
        "ftplib",
        "smtplib",
        "imaplib",
        "threading",
        "concurrent",
        "asyncio",
        "pickle",
        "shelve",
        "marshal",
        "sys",
    }
)

_TRUSTED_PLUGINS_FILE: Path = CONFIG_DIR / "trusted_plugins.json"


def _load_trusted_plugins() -> dict[str, str]:
    """Carrega o mapa de plugins confiáveis (plugin_id -> sha256 do código-fonte).

    Formato: ``{"trusted": {"meu_plugin": "<sha256>", ...}}``.
    """
    with contextlib.suppress(OSError, ValueError):
        if _TRUSTED_PLUGINS_FILE.exists():
            data = json.loads(_TRUSTED_PLUGINS_FILE.read_text("utf-8"))
            raw = data.get("trusted", {})
            if isinstance(raw, dict):
                return dict(raw)
    return {}


def _save_trusted_plugins(trusted: dict[str, str]) -> None:
    """Persiste o mapa de plugins confiáveis (plugin_id -> sha256) no disco."""
    with contextlib.suppress(OSError):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _TRUSTED_PLUGINS_FILE.write_text(
            json.dumps({"trusted": dict(sorted(trusted.items()))},
                       indent=2), "utf-8"
        )


def _sandbox_check_imports(source: str) -> tuple[list[str], list[str]]:
    """
    Analisa estaticamente o código-fonte do plugin.

    Usa AST parsing para precisão; faz fallback para regex.
    Retorna ``(imports_bloqueados, imports_com_aviso)``.
    """
    encontrados: set[str] = set()

    with contextlib.suppress(SyntaxError, Exception):
        arvore = ast.parse(source, mode="exec")
        for no in ast.walk(arvore):
            if isinstance(no, ast.Import):
                for alias in no.names:
                    encontrados.add(alias.name)
            elif isinstance(no, ast.ImportFrom) and no.module:
                encontrados.add(no.module)

    import_re = re.compile(r"^\s*(?:import|from)\s+([\w\.]+)", re.MULTILINE)
    for m in import_re.finditer(source):
        encontrados.add(m.group(1))

    bloqueados: list[str] = []
    warnings: list[str] = []
    for mod in encontrados:
        root = mod.split(".")[0]
        if mod in _SANDBOX_BLOCKED_MODULES or root in _SANDBOX_BLOCKED_MODULES:
            bloqueados.append(mod)
        elif mod in _SANDBOX_WARN_MODULES or root in _SANDBOX_WARN_MODULES:
            warnings.append(mod)

    warnings = [a for a in warnings if a not in bloqueados]
    return bloqueados, warnings


def _get_plugin_source(path: Path) -> str:
    """Retorna o código-fonte do plugin para análise estática."""
    with contextlib.suppress(tk.TclError):
        if path.suffix == ".py":
            return path.read_text("utf-8", errors="replace")
        if path.suffix == ".zip":
            with zipfile.ZipFile(path) as zf:
                for name in ("plugin.py", "__init__.py"):
                    if name in zf.namelist():
                        return zf.read(name).decode("utf-8", errors="replace")
        if path.is_dir():
            py = path / "plugin.py"
            if py.exists():
                return py.read_text("utf-8", errors="replace")
    return ""


def _normalize_manifest(manifest: Any) -> PluginManifest:
    """
    Converte qualquer representação de manifesto para PluginManifest.

    Aceita: None, PluginManifest, dict, SimpleNamespace ou objeto com __dict__.
    """
    if manifest is None:
        return PluginManifest(name="")
    if isinstance(manifest, PluginManifest):
        return manifest
    chaves_validas = {f.name for f in fields(PluginManifest)}
    if isinstance(manifest, dict):
        filtrado = {k: v for k, v in manifest.items() if k in chaves_validas}
        return PluginManifest(**filtrado)
    if hasattr(manifest, "__dict__"):
        filtrado = {k: v for k, v in vars(
            manifest).items() if k in chaves_validas}
        return PluginManifest(**filtrado)
    return PluginManifest(name=str(manifest))


def _get_plugin_readme(path: Path | None, mod: Any = None) -> str:
    """
    Retorna o README do plugin em Markdown.

    Prioridade:
      1. ``MANIFEST.readme`` inline
      2. README.md / README.rst ao lado do arquivo ou dentro do ZIP
      3. Gerado a partir dos campos do MANIFEST (fallback)
    """
    if mod is not None:
        manifest = getattr(mod, "MANIFEST", None)
        if manifest and getattr(manifest, "readme", ""):
            return manifest.readme

    nomes_readme = [
        "README.md",
        "readme.md",
        "README.rst",
        "README.txt",
        "readme.txt",
        "README",
    ]

    if path is not None:
        if path.is_dir():
            for name in nomes_readme:
                f = path / name
                if f.exists():
                    with contextlib.suppress(OSError):
                        return f.read_text("utf-8", errors="replace")
        elif path.suffix == ".zip":
            with contextlib.suppress(tk.TclError), zipfile.ZipFile(path) as zf:
                for name in nomes_readme:
                    if name in zf.namelist():
                        return zf.read(name).decode("utf-8", errors="replace")
        elif path.suffix == ".py":
            for name in nomes_readme:
                f = path.parent / name
                if f.exists():
                    with contextlib.suppress(OSError):
                        return f.read_text("utf-8", errors="replace")

    if mod is not None:
        manifest = _normalize_manifest(getattr(mod, "MANIFEST", None))
        if manifest:
            lines = [f"# {manifest.name}"]
            if manifest.version:
                lines.append(f"**Versão:** {manifest.version}")
            if manifest.author:
                lines.append(f"**Autor:** {manifest.author}")
            if manifest.description:
                lines.append(f"\n{manifest.description}")
            if manifest.tags:
                lines.append("\n**Tags:** " + ", ".join(manifest.tags))
            if manifest.homepage:
                lines.append(f"\n[Homepage]({manifest.homepage})")
            if manifest.changelog:
                lines.append(f"\n---\n\n## Changelog\n\n{manifest.changelog}")
            return "\n\n".join(lines)
    return "*(Sem documentação disponível)*"
