"""Codis - orquestrador principal.

Integração com o barramento de eventos (RESA)
---------------------------------------------
O ``CodisApp`` é o ponto de convergência de toda a arquitetura de eventos.
Ele inicializa o ``EventBus`` global e registra os handlers internos que

Eventos emitidos por este módulo
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
``editor.change``       Conteúdo do editor foi modificado.
``editor.cursor_move``  Cursor moveu-se; payload: ``{line, col, path}``.
``editor.active``       Aba ativa mudou; payload: ``{path, lang, idx}``.
``editor.open``         Arquivo aberto numa aba; payload: ``{path}``.
``editor.save``         Arquivo salvo; payload: ``{path}``.
``editor.close``        Aba fechada; payload: ``{path}``.
``file.save``           Emitido junto com ``editor.save``.
``workspace.open``      Workspace definido; payload: ``{path}``.
``workspace.close``     Workspace fechado; payload: ``{}``.
``config.change``       Qualquer chave de configuração mudou; payload: ``{key, value}``.
``theme.change``        Tema visual alterado; payload: ``{theme}``.
``plugin.hook``         Hook de plugin disparado; payload: ``{event, data}``.
``session.save``        Sessão salva; payload: ``{}``.
``session.restore``     Sessão restaurada; payload: ``{files}``.
``ui.panel_show``       Painel inferior exibido; payload: ``{panel}``.
``ui.sidebar_show``     Sidebar exibida; payload: ``{sidebar}``.
``perf.metric``         Métrica de desempenho do bus; payload: métricas por tópico.
"""

from __future__ import annotations

import collections
import contextlib
import logging
import sys
import time
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src._app_build import _AppBuildMixin
from src._app_editor import _AppEditorMixin
from src._app_kui import _AppKuiMixin
from src._app_webpreview import _AppWebPreviewMixin
from src._app_lsp import _AppLspMixin
from src._app_settings import _AppSettingsMixin
from src._app_tools import _AppToolsMixin
from src.config import ConfigManager
from src.constants import (
    FONT_UI,
    LANG_LABEL,
    TAB_SIZE,
)
from src.editor.multi_cursor import MultiCursorManager
from src.editor.pane import EditorPane
from src.editor.snippets import SnippetEngine
from src.editor.vim_mode import VimMode
from src.i18n import I18n, _t
from src.lint.manager import LintManager
from src.plugins.api import PluginAPI
from src.plugins.loader import PluginLoader
from src.theme.base import THEME_ORDER, THEMES, Theme, _define_themes
from src.theme.manager import (
    ThemeManager,
    _apply_statusbar_patches,
    _patch_all_themes,
    _patch_theme,
)
from src.ui.bookmarks import BookmarkManager
from src.ui.code_runner import CodeRunnerStore
from src.ui.easter_eggs import EasterEggManager
from src.ui.grab_safe import safe_grab_set
from src.ui.k_ui import KUI, build_kui_theme
from src.ui.key_remapper import KeyRemapperManager, KeyRemapperStore
from src.ui.macro_recorder import MacroRecorder, MacroStore, MacroToolbar
from src.ui.panel_detach import PanelDetachManager
from src.ui.project_templates import TemplateStore
from src.ui.theme_preview_panel import ThemePreviewPanel
from src.ui.zen_media import ZenMediaPlayer, ZenMiniOrb
from src.ui.zen_player import ZenPlayerHost
from src.workspace_state import is_restricted


@dataclass
class CopyEntry:
    """Entrada no histórico de cópia/recorte."""

    text: str
    kind: str
    timestamp: float = field(default_factory=time.time)


try:
    from src.lsp.client import LSPManager

    _LSP_OK = True
except ImportError:
    LSPManager = None
    _LSP_OK = False

try:
    from src.events import bus as _event_bus
except Exception:
    _event_bus = None

logger = logging.getLogger("Codis")


class CodisApp(
    _AppBuildMixin,
    _AppEditorMixin,
    _AppLspMixin,
    _AppSettingsMixin,
    _AppToolsMixin,
    _AppKuiMixin,
    _AppWebPreviewMixin,
):
    """
    Orquestrador principal do Codis

    Responsável por construir toda a interface, gerenciar abas, painéis,
    configurações, plugins e os recursos exclusivos do editor.
    """

    def __init__(self, root: tk.Tk) -> None:
        """Inicializa a aplicação, carregando configurações, temas, plugins
        e construindo a interface principal.

        Args:
            root: Janela raiz do Tkinter.
        """
        self._root: tk.Tk = root

        self._bus = _event_bus

        if self._bus is not None:
            self._bus.set_tk_root(root)

        self._hook_to_kind: dict[str, str] = {
            "file_open": "file.open",
            "file_close": "file.close",
            "file_save": "file.save",
            "after_save": "file.save",
            "tab_change": "editor.active",
            "text_change": "editor.change",
            "cursor_move": "editor.cursor_move",
            "lint_complete": "lint.result",
            "before_format": "editor.save",
            "after_format": "editor.save",
            "theme_change": "theme.change",
            "lang_change": "syntax.lang_change",
        }

        self._cfg = ConfigManager()
        if not THEMES:
            _define_themes()
            _patch_all_themes()
        self._tmgr = ThemeManager(self._cfg.get("theme", "fluent_dark"))
        _patch_theme(self._tmgr.theme)
        _apply_statusbar_patches()
        from src.ui.ui_scale import UIScaler

        self._ui_scaler = UIScaler(self._root, self._cfg)
        self._ui_scaler.start()
        try:
            I18n.get_instance().set_locale(self._cfg.get("language", "pt-BR"))
        except Exception as exc:
            logger.warning(
                "Falha ao definir locale da configuração; usando padrão pt-BR: %s", exc
            )

        self._snippets = SnippetEngine()
        self._lint_mgr = LintManager(self._on_lint_result, self._cfg)

        self._lsp_mgr: LSPManager | None = None
        if _LSP_OK and self._cfg.get("lsp_enabled", True):
            try:
                self._lsp_mgr = LSPManager(
                    tk_root=self._root,
                    on_diagnostics=self._on_lsp_diagnostics,
                    on_log=self._on_lsp_log,
                    on_apply_edit=self._on_lsp_apply_edit,
                )
                logger.info("Gerenciador LSP iniciado")
            except (OSError, ImportError, RuntimeError) as exc:
                logger.warning("LSP indisponível: %s", exc)

        self._command_registry: dict[str, tuple[str, Callable]] = {}
        self._plugin_hooks: dict[str, list[Callable]
                                 ] = collections.defaultdict(list)
        self._plugin_api = PluginAPI(self)
        self._plugin_loader = PluginLoader(self._plugin_api)

        self._editors: list[EditorPane] = []
        self._closed_tabs: list[str] = []
        self._gc_job: str | None = None
        self._active_idx: int = -1
        self._workspace: Path | None = None

        self._dual_mode: bool = False
        self._dual_paned: tk.PanedWindow | None = None
        self._dual_left_container: tk.Frame | None = None
        self._dual_right_container: tk.Frame | None = None
        self._dual_right_editor_frame: tk.Frame | None = None
        self._dual_right_editors: list[EditorPane] = []
        self._dual_right_idx: int = -1
        self._dual_right_tabbar: object | None = None
        self._dual_right_active: bool = False
        self._autosave_job: str | None = None
        self._zoom_pct: int = 100
        self._sidebar_visible = False
        self._panel_visible = False
        self._current_panel = "problems"
        self._current_sidebar = "explorer"

        self._active_pane: EditorPane | None = None
        self._smart_tips: object | None = None

        self._zen_mode = False
        self._focus_mode = False
        self._copy_history: list[CopyEntry] = []
        self._theme_debounce_job: str | None = None
        self._clipboard_poll_last: str = ""
        self._clipboard_poll_job: str | None = None
        self._bookmark_mgr = BookmarkManager()

        self._zen_media_player: ZenMediaPlayer | None = None
        self._zen_player: ZenPlayerHost | None = None
        self._zen_media_orb: ZenMiniOrb | None = None
        self._vim_mode: VimMode | None = None
        self._vim_enabled: bool = False
        self._kui: KUI | None = None
        self._kui_enabled: bool = False
        self._panel_detach = PanelDetachManager(
            self._root, dark=getattr(self._tmgr.theme, "dark", True)
        )
        self._easter_eggs: EasterEggManager | None = None

        self._key_store = KeyRemapperStore()
        self._key_mgr: KeyRemapperManager | None = None
        self._runner_store = CodeRunnerStore()
        self._macro_store = MacroStore()
        self._macro_recorder = MacroRecorder(self._macro_store)
        self._macro_toolbar: MacroToolbar | None = None
        self._template_store = TemplateStore()
        self._multi_cursor: MultiCursorManager | None = None

        try:
            from src.ui.time_tracker import TimeTrackerModel

            self._tracker_model: TimeTrackerModel = TimeTrackerModel()
        except Exception as _tracker_err:
            logger.warning("TimeTracker init: %s", _tracker_err)

        self._setup_window()
        self._build_layout()
        with contextlib.suppress(AttributeError, tk.TclError):
            self._panel_detach.set_factories(self._build_panel_factories())
        self._build_menus()
        self._init_cursor_manager()
        self._bind_shortcuts()
        self._setup_key_manager()

        self._tmgr.on_change(self._on_theme_change)
        I18n.get_instance().on_change(self._on_locale_change)

        self._cfg.on_change("theme", self._set_theme)
        self._tmgr.apply_ttk()
        self._lint_mgr.set_log_callback(self._output_panel.write)

        self._lint_mgr.attach(self._root)
        self._load_plugins()
        self._rebuild_theme_menu()
        self._restore_session_or_welcome()
        self._setup_autosave()
        self._setup_tracker_heartbeat()
        self._update_title()
        self._update_status_bar()
        self._init_exclusive_features()
        if self._bus is not None:
            self._root.after(30_000, self._emit_bus_metrics)

        self._root.after(2000, self._start_clipboard_polling)

        if self._cfg.get("auto_check_updates", True):
            self._root.after(5000, self._silent_update_check)

        self._root.bind("<Unmap>", self._dismiss_active_menus, add="+")

    def _dismiss_active_menus(self, _event: tk.Event | None = None) -> None:
        """Fecha menus flutuantes abertos ao minimizar ou perder foco."""
        if _event is not None and _event.widget is not self._root:
            return
        with contextlib.suppress(tk.TclError):
            self._root.tk.call("tk", "menuSetFocus", "")
        for widget in list(self._root.winfo_children()):
            if isinstance(widget, tk.Menu):
                with contextlib.suppress(tk.TclError):
                    widget.unpost()

    def _on_editor_change(self) -> None:
        """Chamado quando o conteúdo do editor é modificado."""
        self._update_breadcrumb()
        e = self._active_editor()
        if e:
            self._fire_hook("text_change", e)
            self._web_preview_on_edit()
            if self._bus is not None:
                self._bus.emit(
                    "editor.change",
                    {
                        "path": str(e.path) if e.path else "",
                        "lang": getattr(e, "lang", ""),
                    },
                    source="CodisApp",
                )

    def _on_copy_event(self, text: str, kind: str) -> None:
        """Registra uma entrada no histórico global de cópia/recorte."""
        entry = CopyEntry(text=text, kind=kind)
        self._copy_history.insert(0, entry)
        if len(self._copy_history) > 100:
            self._copy_history.pop()

    def _start_clipboard_polling(self) -> None:
        """Inicia polling periódico do clipboard do sistema (a cada 500ms).

        Captura cópias feitas em qualquer aplicativo ou pelo menu de contexto
        do SO - não apenas via Ctrl+C dentro do editor.
        Compara com o último valor registrado; só adiciona ao histórico se
        o conteúdo mudou.
        """
        self._clipboard_poll_last: str = ""
        self._clipboard_poll_job: str | None = None
        self._clipboard_poll_tick()

    def _clipboard_poll_tick(self) -> None:
        """Tick do polling de clipboard - executa a cada 500ms."""
        try:
            current = self._root.clipboard_get()
        except tk.TclError:
            current = ""

        if current and current != self._clipboard_poll_last:
            self._clipboard_poll_last = current
            if not self._copy_history or self._copy_history[0].text != current:
                entry = CopyEntry(text=current, kind="copy")
                self._copy_history.insert(0, entry)
                if len(self._copy_history) > 100:
                    self._copy_history.pop()

        if self._root.winfo_exists():
            self._clipboard_poll_job = self._root.after(
                500, self._clipboard_poll_tick)

    def _silent_update_check(self) -> None:
        """Verifica atualizações em background e exibe badge discreto se houver."""
        import contextlib
        import threading

        from src.updater import UpdateManager
        from src.updater.result import UpdateStatus

        token = self._cfg.get("github_token", "")
        _upd_url = (
            str(self._cfg.get("updater_url", "KillovSky/Codis")).strip()
            or "KillovSky/Codis"
        )
        _parts = _upd_url.split("/", 1)
        _upd_owner = _parts[0] if len(_parts) == 2 else "KillovSky"
        _upd_repo = _parts[1] if len(_parts) == 2 else "Codis"
        from src.updater.channel import UpdateChannel as _UC

        mgr = UpdateManager(
            current_version=__import__(
                "src.constants", fromlist=["APP_VERSION"]
            ).APP_VERSION,
            github_token=token,
            channel=_UC(owner=_upd_owner, repo=_upd_repo, token=token),
        )

        def _worker() -> None:
            with contextlib.suppress(Exception):
                result = mgr.check()
                if result.status == UpdateStatus.UPDATE_AVAILABLE:

                    def _notify() -> None:
                        with contextlib.suppress(Exception):
                            self._status.notify(
                                f"⬆  Codis {result.latest_version} disponível — Ajuda › Verificar Atualizações",
                            )

                    self._root.after(0, _notify)

        threading.Thread(target=_worker, daemon=True,
                         name="codis-update-check").start()

    def _bind_panel_tab_context_menu(self, key: str, lbl: tk.Label) -> None:
        """Vincula menu de contexto (clique direito) à aba de painel.

        Exibe a opção de desplugagem (detach) tanto no modo clássico quanto
        no modo K-UI, via ``PanelDetachManager`` compartilhado. Painéis
        sem fábrica registrada (ex.: diff, sql) não recebem a opção, pois
        ainda dependem do estado vivo do painel embutido.

        Args:
            key: Chave do painel.
            lbl: Widget de rótulo da aba.
        """

        def _show_menu(event: tk.Event) -> None:
            if not self._panel_detach.can_detach(key):
                return
            menu = tk.Menu(lbl, tearoff=False)
            t = self._tmgr.theme
            menu.configure(bg=t.panel_bg, fg=t.fg, activebackground=t.sel_bg)
            menu.add_command(
                label=_t("kui.detach", key=key),
                command=lambda: self._panel_detach.detach(key),
            )
            if self._panel_detach.is_detached(key):
                menu.add_command(
                    label=_t("kui.reattach", key=key),
                    command=lambda: self._panel_detach.reattach(key),
                )
            with contextlib.suppress(tk.TclError):
                menu.tk_popup(event.x_root, event.y_root)

        lbl.bind("<Button-3>", _show_menu)

    def _make_theme_callbacks(self) -> tuple:
        """Cria e retorna os callbacks (on_full, on_fast, on_cancel) para o painel de temas.

        Centraliza a lógica compartilhada entre ``_open_theme_panel`` e
        ``_apply_theme_from_menu``, eliminando duplicação de código.
        """

        def _do_full(name: str) -> None:
            self._theme_panel_open = False
            self._tmgr.set(name)
            self._cfg.set("theme", name)
            self._cfg.save()
            self._restart_ui()

        def _do_fast(name: str) -> None:
            self._theme_panel_open = False
            self._tmgr.set(name)
            self._cfg.set("theme", name)
            self._cfg.save()
            self._on_theme_change(self._tmgr.theme)

        def _do_cancel() -> None:
            self._theme_panel_open = False

        return _do_full, _do_fast, _do_cancel

    def _open_theme_panel(self) -> None:
        """Abre o painel wizard de seleção de temas com preview ao vivo."""
        if getattr(self, "_theme_panel_open", False):
            return
        self._theme_panel_open = True
        _do_full, _do_fast, _do_cancel = self._make_theme_callbacks()
        try:
            ThemePreviewPanel(
                self._root,
                current_theme=self._tmgr.theme,
                on_full=_do_full,
                on_fast=_do_fast,
                on_cancel=_do_cancel,
            )
        except Exception:
            logger.warning(
                "Falha ao criar ThemePreviewPanel, aplicando fallback direto",
                exc_info=True,
            )
            self._theme_panel_open = False
            self._root.after(80, self._restart_ui)

    def _apply_theme_from_menu(self, name: str) -> None:
        """Chamado ao clicar diretamente no nome de um tema no menu.

        Abre o seletor de temas com o tema pré-selecionado, dando ao usuário
        a opção de aplicar completo (com restart da UI) ou rápido - evitando
        o bug de cores corrompidas que ocorre na aplicação direta pelo menu.
        """
        if getattr(self, "_theme_panel_open", False):
            return
        self._theme_panel_open = True
        _do_full, _do_fast, _do_cancel = self._make_theme_callbacks()
        try:
            ThemePreviewPanel(
                self._root,
                current_theme=self._tmgr.theme,
                on_full=_do_full,
                on_fast=_do_fast,
                on_cancel=_do_cancel,
            )
        except Exception:
            self._theme_panel_open = False
            self._set_theme(name)

    def _set_theme(self, name: str) -> None:
        """Aplica tema direto (chamado pelo config watcher) sem abrir o painel.

        Usa debounce de 300ms para evitar aplicações parciais quando o tema
        é trocado rapidamente (ex: clicar várias vezes no menu de temas),
        o que causava cores corrompidas e misturadas na UI.
        """
        if getattr(self, "_theme_debounce_job", None):
            with contextlib.suppress(tk.TclError):
                self._root.after_cancel(self._theme_debounce_job)

        def _apply() -> None:
            self._theme_debounce_job = None
            self._tmgr.set(name)
            self._cfg.set("theme", name)
            self._cfg.save()
            self._on_theme_change(self._tmgr.theme)

        self._theme_debounce_job = self._root.after(300, _apply)

    def _restart_ui(self) -> None:
        """
        Destrói e recria toda a interface mantendo estado (workspace, arquivos).
        Garante que NENHUM widget fique com cores do tema anterior.
        Restaura o estado do painel de temas ao terminar.
        """
        workspace = self._workspace
        active_idx = self._active_idx
        open_files: list = []
        for e in self._editors:
            try:
                content_str = e.text.get("1.0", "end-1c")
                lang = getattr(e, "lang", "text")
                open_files.append(
                    (getattr(e, "path", None), content_str, lang))
            except Exception as exc:
                logger.debug(
                    "Não foi possível salvar estado do editor no restart: %s", exc
                )

        try:
            if self._autosave_job:
                self._root.after_cancel(self._autosave_job)
        except Exception as exc:
            logger.debug("Erro ao cancelar autosave no restart: %s", exc)
        try:
            self._lint_mgr.cancel()
        except Exception as exc:
            logger.debug("Erro ao cancelar lint no restart: %s", exc)
        try:
            if self._lsp_mgr:
                self._lsp_mgr.stop_all()
        except Exception as exc:
            logger.debug("Erro ao parar LSP no restart: %s", exc)
        try:
            from src.ui.k_ui._perf import shutdown_physics_executor

            shutdown_physics_executor()
        except Exception as exc:
            logger.debug(
                "Erro ao encerrar physics executor no restart: %s", exc)

        for child in list(self._root.winfo_children()):
            with contextlib.suppress(tk.TclError):
                child.destroy()

        t = self._tmgr.theme
        _patch_theme(t)
        self._tmgr.apply_ttk()
        self._root.config(bg=t.bg)

        self._editors = []
        self._active_idx = -1
        self._workspace = None
        self._autosave_job = None
        self._zoom_pct = 100
        self._sidebar_visible = False
        self._panel_visible = False
        self._current_panel = "problems"
        self._current_sidebar = "explorer"
        self._active_pane = None
        self._plugin_hooks = collections.defaultdict(list)

        self._cfg._change_callbacks["theme"] = []
        self._tmgr._change_callbacks.clear()

        self._setup_window()
        self._build_layout()
        with contextlib.suppress(AttributeError, tk.TclError):
            self._panel_detach.set_factories(self._build_panel_factories())
            self._panel_detach.set_dark(getattr(t, "dark", True))
        self._build_menus()
        self._bind_shortcuts()
        self._setup_key_manager()

        self._tmgr.on_change(self._on_theme_change)
        I18n.get_instance().on_change(self._on_locale_change)
        self._cfg.on_change("theme", self._set_theme)
        self._tmgr.apply_ttk()
        self._lint_mgr.set_log_callback(
            lambda msg, channel="Codis", level="info": self._output_panel.write(
                msg, channel, level
            )
        )
        self._lint_mgr.attach(self._root)
        self._load_plugins()
        self._rebuild_theme_menu()

        if workspace:
            with contextlib.suppress(tk.TclError):
                self._set_workspace(workspace)

        for path, content, lang in open_files:
            try:
                if path and path.exists():
                    self._open_file(path)
                else:
                    self.new_file()
                    if self._editors:
                        e = self._editors[-1]
                        e.text.delete("1.0", "end")
                        e.text.insert("1.0", content)
                        e.language = lang
            except (tk.TclError, AttributeError):
                pass

        if 0 <= active_idx < len(self._editors):
            with contextlib.suppress(tk.TclError):
                self._select_tab(active_idx)
        elif self._editors:
            with contextlib.suppress(tk.TclError):
                self._select_tab(0)

        self._setup_autosave()
        self._update_title()
        self._update_status_bar()

        if self._cfg.get("kui_enabled", False):
            self._kui_enabled = False
            self._kui = None
            self._root.after(300, self._enable_kui)
        if self._cfg.get("vim_mode", False):
            self._vim_enabled = False
            self._vim_mode = None
            self._root.after(450, self._enable_vim_mode)

        self._theme_panel_open = False

    def _on_theme_change(self, t: Theme) -> None:
        """Aplica o tema a todos os componentes da janela principal."""
        from src.theme.utils import cfg, recolor

        _patch_theme(t)
        self._root.config(bg=t.bg)
        self._tmgr.apply_ttk()
        with contextlib.suppress(AttributeError, tk.TclError):
            self._ui_scaler.reapply_styles()

        with contextlib.suppress(AttributeError, tk.TclError):
            self._panel_detach.set_dark(getattr(t, "dark", True))

        with contextlib.suppress(Exception):
            self._menubar.apply_theme(t)

        for e in self._editors:
            with contextlib.suppress(tk.TclError):
                e.apply_theme(t)

        for widget in (
            self._status,
            self._breadcrumb,
            self._tabbar,
            self._file_tree,
            self._find_files,
            self._git_sidebar,
            self._plugin_panel,
            self._settings_editor,
            self._problems_panel,
            self._terminal,
            self._output_panel,
            self._git_bottom,
            self._diff_pane,
            self._debug_panel,
            self._code_outline,
            self._snippets_panel,
            self._scratchpad_pane,
            self._bookmarks_panel,
            self._regex_panel,
            self._sidebar_problems,
            self._search_bar,
        ):
            with contextlib.suppress(AttributeError, tk.TclError):
                widget.apply_theme(t)

        for widget in (
            self._actbar,
            self._code_stats_panel,
            self._todo_panel,
            self._rest_client_panel,
            self._render_panel,
            self._pomodoro_panel,
            self._flowchart_panel,
            self._docs_panel,
            getattr(self, "_sql_studio_panel", None),
            getattr(self, "_sql_bottom", None),
        ):
            with contextlib.suppress(AttributeError, tk.TclError):
                widget.apply_theme(self._tmgr)

        recolor(
            {
                **{
                    getattr(self, a): {"bg": bg}
                    for a, bg in {
                        "_sidebar": t.sidebar_bg,
                        "_sidebar_stack": t.sidebar_bg,
                        "_content_frame": t.bg,
                        "_panel_content": t.panel_bg,
                        "_bottom_frame": t.panel_bg,
                    }.items()
                    if getattr(self, a, None) is not None
                },
            }
        )

        for pw_attr in ("_h_paned", "_v_paned"):
            with contextlib.suppress(AttributeError, tk.TclError):
                getattr(self, pw_attr).config(bg=t.border)

        cfg(
            getattr(self, "_sidebar_toggle_btn", None) or tk.Frame(),
            bg=t.tabbar_bg,
            fg=t.fg,
        )
        cfg(
            getattr(self, "_panel_toggle_btn", None) or tk.Frame(),
            bg=t.tabbar_bg,
            fg=t.fg,
        )
        cfg(getattr(self, "_panel_tabbar", None) or tk.Frame(), bg=t.tabbar_bg)

        for k, lbl in self._panel_tabs.items():
            active = k == self._current_panel
            cfg(
                lbl,
                bg=t.tab_act_bg if active else t.tabbar_bg,
                fg=t.tab_act_fg if active else t.tab_in_fg,
            )

        try:
            self._apply_titlebar_color()
        except Exception as exc:
            logger.debug(
                "Não foi possível aplicar cor na barra de título: %s", exc)

        if self._kui:
            with contextlib.suppress(tk.TclError):
                self._kui.apply_theme(t)

            with contextlib.suppress(tk.TclError):
                self._apply_kui_colors()

        self._fire_hook("theme_change", t)

    _CONSTANTS_KEY_MAP: dict[str, str | None] = {
        "autocomplete_delay": "AUTOCOMPLETE_DELAY",
        "autocomplete_min_chars": "AUTOCOMPLETE_MIN_CHARS",
        "autocomplete_max_items": "AUTOCOMPLETE_MAX_ITEMS",
        "lint_delay": "LINT_DELAY",
        "hl_idle_ms": "HL_IDLE_MS",
        "large_file_mb": None,
        "search_max_results": "SEARCH_MAX_RESULTS",
        "font_min": "FONT_MIN",
        "font_max": "FONT_MAX",
        "minimap_width": "MINIMAP_WIDTH",
        "minimap_scale": "MINIMAP_SCALE",
        "max_recent": "MAX_RECENT",
        "autosave_default": "AUTOSAVE_DEFAULT",
    }

    def _patch_constant(self, key: str, value: Any) -> None:
        """Atualiza a constante correspondente em ``src.constants`` em tempo real."""
        try:
            mod = sys.modules.get("src.constants")
            if not mod:
                return
            const = self._CONSTANTS_KEY_MAP.get(key)
            if const:
                setattr(mod, const, int(value))
            elif key == "large_file_mb":
                mod.LARGE_FILE_BYTES = int(value) * 1024 * 1024
        except (ValueError, AttributeError):
            pass

    def _apply_a11y_setting(self, key: str, value: Any) -> None:
        """Aplica configuração de acessibilidade ao módulo _a11y em tempo real."""
        a11y = getattr(self, "_a11y_mgr", None)
        if not a11y:
            return
        if key == "a11y_high_contrast":
            if value:
                a11y.enable_high_contrast(save=False)
            else:
                a11y.disable_high_contrast(save=False)
        elif key == "a11y_colorblind_mode":
            if value:
                a11y.enable_colorblind_mode(str(value), save=False)
            else:
                a11y.disable_colorblind_mode(save=False)
        elif key == "a11y_screen_reader":
            a11y._screen_reader_enabled = bool(value)
        elif key == "a11y_large_ui_font":
            a11y._large_ui_font = bool(value)
        elif key == "a11y_reduce_motion":
            a11y._reduce_motion = bool(value)

    def _on_setting_change(self, key: str, value: Any) -> None:
        """Aplica mudanças de configuração em tempo real sem reiniciar a aplicação."""
        if key == "theme":
            self._set_theme(value)
        elif key == "language":
            I18n.get_instance().set_locale(value)
            self._cfg.set("language", value)
        elif key == "ui_scale":
            if getattr(self, "_ui_scale_job", None):
                with contextlib.suppress(tk.TclError):
                    self._root.after_cancel(self._ui_scale_job)

            def _apply_scale(v: Any = value) -> None:
                self._ui_scale_job = None
                self._ui_scaler.on_config_change(v)

            self._ui_scale_job = self._root.after(80, _apply_scale)
        elif key == "kui_perf":
            if self._kui:
                self._kui.set_perf_profile(str(value))
        elif key in ("font_family", "font_size", "tab_size"):
            for e in self._editors:
                e.apply_config(self._cfg)
            if key == "tab_size":
                self._status.set_indent(
                    value, not self._cfg.get("use_spaces", True))
        elif key == "word_wrap":
            for e in self._editors:
                e.set_word_wrap(bool(value))
        elif key == "minimap":
            for e in self._editors:
                e.set_minimap(bool(value))
        elif key in ("autosave", "autosave_delay"):
            self._setup_autosave()
        elif key == "lsp_enabled":
            if self._lsp_mgr:
                self._lsp_mgr.set_enabled(bool(value) and not is_restricted())
        elif key in self._CONSTANTS_KEY_MAP:
            self._patch_constant(key, value)
        elif key == "_open_json":
            self._open_file(Path(value))
        elif key == "ui_cursor":
            try:
                from src.theme.manager import apply_custom_cursor

                apply_custom_cursor(self._root, str(value))
            except Exception as exc:
                logger.debug("Custom cursor: %s", exc)
        elif key.startswith("a11y_"):
            self._apply_a11y_setting(key, value)
        elif key == "custom_keywords":
            for e in self._editors:
                with contextlib.suppress(Exception):
                    e.refresh_custom_keywords()
        self._cfg.save()

    def _on_locale_change(self) -> None:
        """Reconstrói os menus quando o idioma muda."""
        with contextlib.suppress(tk.TclError):
            self._menubar.destroy()
        self._build_menus()

    def _pick_language(self) -> None:
        """Abre diálogo para selecionar o modo de linguagem do arquivo."""
        t = self._tmgr.theme
        dlg = tk.Toplevel(self._root)
        dlg.title("Select Language")
        dlg.configure(bg=t.floating_bg)
        dlg.transient(self._root)
        safe_grab_set(dlg)
        dlg.resizable(False, False)

        tk.Label(
            dlg,
            text=_t("hc.app.select_language_mode"),
            bg=t.floating_bg,
            fg=t.fg,
            font=(FONT_UI, 11, "bold"),
        ).pack(padx=16, pady=(12, 4))

        search_var = tk.StringVar()
        sf = tk.Frame(dlg, bg=t.floating_bg)
        sf.pack(fill="x", padx=12, pady=(0, 4))
        tk.Label(
            sf, text="🔍", bg=t.floating_bg, fg=t.fg_muted, font=(FONT_UI, 10)
        ).pack(side="left")
        search_entry = tk.Entry(
            sf,
            textvariable=search_var,
            bg=t.input_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            bd=0,
            font=(FONT_UI, 10),
            highlightthickness=1,
            highlightbackground=t.border,
        )
        search_entry.pack(side="left", fill="x", expand=True, padx=4, pady=2)

        lf = tk.Frame(dlg, bg=t.floating_bg)
        lf.pack(fill="both", expand=True, padx=12, pady=4)
        lb = tk.Listbox(
            lf,
            bg=t.sidebar_bg,
            fg=t.fg,
            selectbackground=t.sel_bg,
            selectforeground=t.sel_fg,
            relief="flat",
            bd=0,
            font=(FONT_UI, 10),
            activestyle="none",
            highlightthickness=1,
            highlightbackground=t.border,
            width=40,
            height=16,
        )
        vsb = tk.Scrollbar(
            lf,
            command=lb.yview,
            bg=t.scroll_thumb,
            troughcolor=t.scroll_bg,
            relief="flat",
            bd=0,
            width=6,
        )
        vsb.pack(side="right", fill="y")
        lb.config(yscrollcommand=vsb.set)
        lb.pack(fill="both", expand=True)

        all_langs = sorted(LANG_LABEL.items(), key=lambda x: x[1])

        def _populate(filter_str: str = "") -> None:
            lb.delete(0, "end")
            q = filter_str.lower()
            for lang_id, lang_name in all_langs:
                if not q or q in lang_name.lower() or q in lang_id:
                    lb.insert("end", f"  {lang_name}")
            if lb.size() > 0:
                lb.selection_set(0)

        _populate()

        def _on_search(*_):
            _populate(search_var.get())

        search_var.trace_add("write", _on_search)

        def _apply(lang_id: str) -> None:
            e = self._e()
            if e:
                old_lang = e.lang
                e.lang = lang_id
                e.hl.set_lang(lang_id)
                e.hl.highlight()
                e.autocomplete.set_lang(lang_id)
                self._status.set_lang(lang_id)
                self._status.notify(
                    _t("msg.lang_changed", lang=LANG_LABEL.get(lang_id, lang_id))
                )
                self._fire_hook("lang_change", lang_id)
                with contextlib.suppress(tk.TclError):
                    self._snippets_panel.notify_active_lang(lang_id)

                with contextlib.suppress(Exception):
                    if e.path:
                        content = e.text.get("1.0", "end-1c")
                        self._lint_mgr.schedule(e.path, content, lang_id)

                if self._lsp_mgr and e.path:
                    with contextlib.suppress(Exception):
                        if old_lang != lang_id:
                            self._lsp_mgr.did_close(e.path, old_lang)

                        content = e.text.get("1.0", "end-1c")
                        self._lsp_mgr.did_open(e.path, lang_id, content)
                        e.autocomplete.set_lsp_manager(self._lsp_mgr, e.path)

                        if lang_id not in (
                            "text",
                            "plaintext",
                            "",
                        ) and not self._lsp_mgr.available_for(lang_id):
                            hint = self._lsp_mgr.install_hint(lang_id)
                            if hint:
                                self._output_panel.write(
                                    f"LSP [{lang_id}]: sem servidor. Instale: {hint}",
                                    "LSP",
                                    "info",
                                    timestamp=False,
                                )
            dlg.destroy()

        def _on_ok(*_):
            sel = lb.curselection()
            if not sel:
                return
            q = search_var.get().lower()
            matches = [
                (lid, ln)
                for lid, ln in all_langs
                if not q or q in ln.lower() or q in lid
            ]
            if sel[0] < len(matches):
                _apply(matches[sel[0]][0])

        lb.bind("<Double-Button-1>", _on_ok)
        lb.bind("<Return>", _on_ok)

        btn_frame = tk.Frame(dlg, bg=t.floating_bg)
        btn_frame.pack(fill="x", padx=12, pady=(4, 12))
        for text, cmd in (("OK", _on_ok), ("Cancel", dlg.destroy)):
            b = tk.Label(
                btn_frame,
                text=text,
                bg=t.btn_bg,
                fg=t.fg,
                font=(FONT_UI, 9),
                cursor="hand2",
                padx=14,
                pady=4,
                relief="flat",
            )
            b.pack(side="right", padx=4)
            b.bind("<Button-1>", lambda _, c=cmd: c())
            b.bind("<Enter>", lambda _, w=b: w.config(bg=t.btn_hover))
            b.bind("<Leave>", lambda _, w=b: w.config(bg=t.btn_bg))

        search_entry.focus_set()

        dlg.update_idletasks()
        dlg.lift()
        dlg.focus_force()

        x = (
            self._root.winfo_rootx()
            + (self._root.winfo_width() - dlg.winfo_width()) // 2
        )
        y = (
            self._root.winfo_rooty()
            + (self._root.winfo_height() - dlg.winfo_height()) // 2
        )
        dlg.geometry(f"+{x}+{y}")

    def _pick_eol(self) -> None:
        """Abre diálogo para escolher o tipo de fim de linha."""
        t = self._tmgr.theme
        dlg = tk.Toplevel(self._root)
        dlg.title("Select End of Line")
        dlg.configure(bg=t.floating_bg)
        dlg.transient(self._root)
        safe_grab_set(dlg)
        dlg.resizable(False, False)

        tk.Label(
            dlg,
            text=_t("hc.app.end_of_line_sequence"),
            bg=t.floating_bg,
            fg=t.fg,
            font=(FONT_UI, 11, "bold"),
        ).pack(padx=20, pady=(14, 6))

        eol_options = [
            ("LF", "\n", "Unix/macOS (\\n)"),
            ("CRLF", "\r\n", "Windows (\\r\\n)"),
            ("CR", "\r", "Legacy Mac (\\r)"),
        ]

        var = tk.StringVar(value="LF")
        for code, _, desc in eol_options:
            fr = tk.Frame(dlg, bg=t.floating_bg, cursor="hand2")
            fr.pack(fill="x", padx=20, pady=2)
            rb = tk.Radiobutton(
                fr,
                text=f"{code}  -  {desc}",
                variable=var,
                value=code,
                bg=t.floating_bg,
                fg=t.fg,
                activebackground=t.floating_bg,
                selectcolor=t.input_bg,
                font=(FONT_UI, 10),
            )
            rb.pack(anchor="w")

        def _apply(*_):
            eol_map = {"LF": "\n", "CRLF": "\r\n", "CR": "\r"}
            eol = eol_map.get(var.get())
            if eol:
                e = self._e()
                if e:
                    e.eol = eol
                    self._status.set_eol(eol)
            dlg.destroy()

        btn_frame = tk.Frame(dlg, bg=t.floating_bg)
        btn_frame.pack(fill="x", padx=16, pady=(8, 14))
        for text, cmd in (("OK", _apply), ("Cancel", dlg.destroy)):
            b = tk.Label(
                btn_frame,
                text=text,
                bg=t.btn_bg,
                fg=t.fg,
                font=(FONT_UI, 9),
                cursor="hand2",
                padx=14,
                pady=4,
                relief="flat",
            )
            b.pack(side="right", padx=4)
            b.bind("<Button-1>", lambda _, c=cmd: c())
            b.bind("<Enter>", lambda _, w=b: w.config(bg=t.btn_hover))
            b.bind("<Leave>", lambda _, w=b: w.config(bg=t.btn_bg))

        dlg.update_idletasks()
        x = (
            self._root.winfo_rootx()
            + (self._root.winfo_width() - dlg.winfo_width()) // 2
        )
        y = (
            self._root.winfo_rooty()
            + (self._root.winfo_height() - dlg.winfo_height()) // 2
        )
        dlg.geometry(f"+{x}+{y}")

    def _pick_encoding(self) -> None:
        """Abre diálogo para escolher a codificação do arquivo."""
        t = self._tmgr.theme
        dlg = tk.Toplevel(self._root)
        dlg.title("Select Encoding")
        dlg.configure(bg=t.floating_bg)
        dlg.transient(self._root)
        safe_grab_set(dlg)
        dlg.resizable(False, False)

        tk.Label(
            dlg,
            text=_t("hc.app.file_encoding"),
            bg=t.floating_bg,
            fg=t.fg,
            font=(FONT_UI, 11, "bold"),
        ).pack(padx=20, pady=(14, 6))

        encodings = [
            ("UTF-8", "Unicode, all languages (recommended)"),
            ("UTF-8-BOM", "UTF-8 with BOM (Windows compatibility)"),
            ("UTF-16", "16-bit Unicode"),
            ("UTF-16-LE", "UTF-16 Little Endian"),
            ("UTF-16-BE", "UTF-16 Big Endian"),
            ("LATIN-1", "ISO-8859-1, Western European"),
            ("CP1252", "Windows Western European"),
            ("CP1251", "Windows Cyrillic"),
            ("GB2312", "Simplified Chinese"),
            ("BIG5", "Traditional Chinese"),
            ("SHIFT-JIS", "Japanese"),
            ("EUC-KR", "Korean"),
        ]

        cur_enc = (
            self._e().encoding
            if self._e() and hasattr(self._e(), "encoding")
            else "UTF-8"
        ).upper()

        _ = tk.StringVar(value=cur_enc)

        lf = tk.Frame(dlg, bg=t.floating_bg)
        lf.pack(fill="both", expand=True, padx=12, pady=4)
        lb = tk.Listbox(
            lf,
            bg=t.sidebar_bg,
            fg=t.fg,
            selectbackground=t.sel_bg,
            selectforeground=t.sel_fg,
            relief="flat",
            bd=0,
            font=(FONT_UI, 10),
            activestyle="none",
            highlightthickness=1,
            highlightbackground=t.border,
            width=48,
            height=12,
        )
        vsb = tk.Scrollbar(
            lf,
            command=lb.yview,
            bg=t.scroll_thumb,
            troughcolor=t.scroll_bg,
            relief="flat",
            bd=0,
            width=6,
        )
        vsb.pack(side="right", fill="y")
        lb.config(yscrollcommand=vsb.set)
        lb.pack(fill="both", expand=True)

        for enc, desc in encodings:
            lb.insert("end", f"  {enc:<14} {desc}")

        for i, (enc, _) in enumerate(encodings):
            if enc == cur_enc:
                lb.selection_set(i)
                lb.see(i)
                break

        def _apply(*_):
            sel = lb.curselection()
            if sel:
                enc = encodings[sel[0]][0]
                e = self._e()
                if e:
                    e.encoding = enc
                    self._status.set_encoding(enc)
            dlg.destroy()

        lb.bind("<Double-Button-1>", _apply)
        lb.bind("<Return>", _apply)

        bf = tk.Frame(dlg, bg=t.floating_bg)
        bf.pack(fill="x", padx=12, pady=(4, 14))
        for text, cmd in (("OK", _apply), ("Cancel", dlg.destroy)):
            b = tk.Label(
                bf,
                text=text,
                bg=t.btn_bg,
                fg=t.fg,
                font=(FONT_UI, 9),
                cursor="hand2",
                padx=14,
                pady=4,
                relief="flat",
            )
            b.pack(side="right", padx=3)
            b.bind("<Button-1>", lambda _, c=cmd: c())
            b.bind("<Enter>", lambda _, w=b: w.config(bg=t.btn_hover))
            b.bind("<Leave>", lambda _, w=b: w.config(bg=t.btn_bg))

        dlg.update_idletasks()
        x = (
            self._root.winfo_rootx()
            + (self._root.winfo_width() - dlg.winfo_width()) // 2
        )
        y = (
            self._root.winfo_rooty()
            + (self._root.winfo_height() - dlg.winfo_height()) // 2
        )
        dlg.geometry(f"+{x}+{y}")

    def _pick_indent(self) -> None:
        """Abre diálogo para configurar indentação (tabs vs espaços, tamanho)."""
        t = self._tmgr.theme
        dlg = tk.Toplevel(self._root)
        dlg.title("Indentation")
        dlg.configure(bg=t.floating_bg)
        dlg.transient(self._root)
        safe_grab_set(dlg)
        dlg.resizable(False, False)

        tk.Label(
            dlg,
            text=_t("hc.app.indentation_settings"),
            bg=t.floating_bg,
            fg=t.fg,
            font=(FONT_UI, 11, "bold"),
        ).pack(padx=20, pady=(14, 6))

        use_spaces = self._cfg.get("use_spaces", True)
        indent_var = tk.StringVar(value="spaces" if use_spaces else "tabs")
        for val, lbl in (("spaces", "Spaces"), ("tabs", "Tabs")):
            tk.Radiobutton(
                dlg,
                text=lbl,
                variable=indent_var,
                value=val,
                bg=t.floating_bg,
                fg=t.fg,
                activebackground=t.floating_bg,
                selectcolor=t.input_bg,
                font=(FONT_UI, 10),
            ).pack(anchor="w", padx=20, pady=2)

        tk.Label(
            dlg,
            text=_t("hc.app.tab_size"),
            bg=t.floating_bg,
            fg=t.fg,
            font=(FONT_UI, 10),
        ).pack(anchor="w", padx=20, pady=(8, 2))
        size_var = tk.IntVar(value=self._cfg.get("tab_size", TAB_SIZE))
        sf = tk.Frame(dlg, bg=t.floating_bg)
        sf.pack(padx=20, pady=2)
        for sz in (2, 4, 8):
            tk.Radiobutton(
                sf,
                text=str(sz),
                variable=size_var,
                value=sz,
                bg=t.floating_bg,
                fg=t.fg,
                activebackground=t.floating_bg,
                selectcolor=t.input_bg,
                font=(FONT_UI, 10),
            ).pack(side="left", padx=8)

        def _apply(*_):
            use_sp = indent_var.get() == "spaces"
            size = size_var.get()
            self._cfg.set("use_spaces", use_sp)
            self._cfg.set("tab_size", size)
            for e in self._editors:
                e.apply_config(self._cfg)
            self._status.set_indent(size, not use_sp)
            dlg.destroy()

        btn_frame = tk.Frame(dlg, bg=t.floating_bg)
        btn_frame.pack(fill="x", padx=16, pady=(10, 14))
        for text, cmd in (("OK", _apply), ("Cancel", dlg.destroy)):
            b = tk.Label(
                btn_frame,
                text=text,
                bg=t.btn_bg,
                fg=t.fg,
                font=(FONT_UI, 9),
                cursor="hand2",
                padx=14,
                pady=4,
            )
            b.pack(side="right", padx=4)
            b.bind("<Button-1>", lambda _, c=cmd: c())
            b.bind("<Enter>", lambda _, w=b: w.config(bg=t.btn_hover))
            b.bind("<Leave>", lambda _, w=b: w.config(bg=t.btn_bg))

        dlg.update_idletasks()
        x = (
            self._root.winfo_rootx()
            + (self._root.winfo_width() - dlg.winfo_width()) // 2
        )
        y = (
            self._root.winfo_rooty()
            + (self._root.winfo_height() - dlg.winfo_height()) // 2
        )
        dlg.geometry(f"+{x}+{y}")

    def _reset_zoom(self) -> None:
        """Abre diálogo para escolher o nível de zoom."""
        t = self._tmgr.theme
        dlg = tk.Toplevel(self._root)
        dlg.title("Zoom")
        dlg.configure(bg=t.floating_bg)
        dlg.transient(self._root)
        safe_grab_set(dlg)
        dlg.resizable(False, False)

        tk.Label(
            dlg,
            text=_t("hc.app.editor_zoom"),
            bg=t.floating_bg,
            fg=t.fg,
            font=(FONT_UI, 11, "bold"),
        ).pack(padx=20, pady=(14, 6))

        cur = self._zoom_pct
        var = tk.IntVar(value=cur)
        levels = [50, 75, 90, 100, 110, 125, 150, 175, 200]

        for pct in levels:
            row = tk.Frame(dlg, bg=t.floating_bg, cursor="hand2")
            row.pack(fill="x", padx=20, pady=1)
            rb = tk.Radiobutton(
                row,
                text=f"{pct}%",
                variable=var,
                value=pct,
                bg=t.floating_bg,
                fg=t.fg,
                activebackground=t.floating_bg,
                selectcolor=t.input_bg,
                font=(FONT_UI, 10),
            )
            rb.pack(anchor="w")
            if pct == cur:
                rb.config(fg=t.accent)

        def _apply(*_):
            target = var.get()

            self._zoom_pct = 100
            for e in self._editors:
                e.zoom(0)
            steps = (target - 100) // 10
            for e in self._editors:
                for _ in range(abs(steps)):
                    e.zoom(1 if steps > 0 else -1)
            self._zoom_pct = target
            self._status.set_zoom(self._zoom_pct)
            dlg.destroy()

        bf = tk.Frame(dlg, bg=t.floating_bg)
        bf.pack(fill="x", padx=16, pady=(8, 14))
        for text, cmd in (
            ("Reset (100%)", lambda: (var.set(100), _apply())),
            ("OK", _apply),
            ("Cancel", dlg.destroy),
        ):
            b = tk.Label(
                bf,
                text=text,
                bg=t.btn_bg,
                fg=t.fg,
                font=(FONT_UI, 9),
                cursor="hand2",
                padx=12,
                pady=4,
                relief="flat",
            )
            b.pack(side="right", padx=3)
            b.bind("<Button-1>", lambda _, c=cmd: c())
            b.bind("<Enter>", lambda _, w=b: w.config(bg=t.btn_hover))
            b.bind("<Leave>", lambda _, w=b: w.config(bg=t.btn_bg))

        dlg.update_idletasks()
        x = (
            self._root.winfo_rootx()
            + (self._root.winfo_width() - dlg.winfo_width()) // 2
        )
        y = (
            self._root.winfo_rooty()
            + (self._root.winfo_height() - dlg.winfo_height()) // 2
        )
        dlg.geometry(f"+{x}+{y}")

    def _init_exclusive_features(self) -> None:
        """Inicializa funcionalidades exclusivas que dependem de janela e bus prontos."""
        self._zen_media_player = ZenMediaPlayer(tk_root=self._root)

        _playlist_salva = self._cfg.get("zen_media_playlist", [])
        if _playlist_salva:
            self._zen_media_player.set_playlist(_playlist_salva)

        if "kui" not in THEMES:
            ng_theme = build_kui_theme()
            _patch_theme(ng_theme)
            THEMES["kui"] = ng_theme
            if "kui" not in THEME_ORDER:
                THEME_ORDER.append("kui")

        if self._cfg.get("kui_enabled", False):
            self._root.after(400, self._enable_kui)

        if self._cfg.get("vim_mode", False):
            self._root.after(500, self._enable_vim_mode)

        self._easter_eggs = EasterEggManager(
            self._root,
            self._tmgr,
            self._e,
            self._status.notify,
            editor_container_getter=lambda: getattr(
                self, "_editor_container", None),
            cfg=getattr(self, "_cfg", None),
        )

        self._root.after(100, self._connect_terminal_easter_eggs)

        self._root.bind("<Control-Alt-v>",
                        lambda _: self._toggle_vim_mode(), add=True)
        self._root.bind("<Control-Alt-V>",
                        lambda _: self._toggle_vim_mode(), add=True)
        self._root.bind(
            "<Control-Alt-n>", lambda _: self._toggle_kui(), add=True
        )
        self._root.bind(
            "<Control-Alt-N>", lambda _: self._toggle_kui(), add=True
        )

        self._root.after(800, self._init_accessibility)
        self._root.after(1500, self._init_screen_reader_global)
        self._root.after(1200, self._init_performance_monitor)
