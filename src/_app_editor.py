"""Mixin ``_AppEditorMixin`` - operações do editor de texto do Codis.

Gerencia abertura, salvamento, fechamento e ciclo de vida dos arquivos no editor,
bem como tabs, histórico recente e integração com o sistema de arquivos.
"""

from __future__ import annotations

import contextlib
import logging
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING, Any

from src.constants import EXT_LANG
from src.editor.multi_cursor import MultiCursorManager
from src.editor.pane import EditorPane
from src.i18n import _t
from src.theme.base import THEME_ORDER
from src.ui.code_runner import CodeRunnerDialog
from src.ui.key_remapper import KeyRemapperDialog, KeyRemapperManager
from src.ui.macro_recorder import MacroToolbar
from src.ui.project_templates import ProjectTemplateDialog
from src.ui.settings_window import SettingsWindow
from src.ui.workspace_switcher import WorkspaceSwitcherDialog
from src.ui.workspace_trust import WorkspaceTrust
from src.workspace_state import set_restricted

try:
    from src.editor.smart_tips import SmartTips as _SmartTips

    _SMART_TIPS_AVAILABLE = True
except ImportError:
    _SmartTips = None  # type: ignore[assignment,misc]
    _SMART_TIPS_AVAILABLE = False

if TYPE_CHECKING:
    pass

logger = logging.getLogger("Codis")

__all__ = ["_AppEditorMixin"]


def _widget_exists(widget: tk.Widget | None) -> bool:
    """Retorna True se o widget Tkinter ainda existe e não foi destruído."""
    if widget is None:
        return False
    try:
        return bool(widget.winfo_exists())
    except tk.TclError:
        return False


class _AppEditorMixin:
    """Mixin de Editor do CodisApp."""

    def _clipboard_copy(self, _e: tk.Event) -> str | None:
        """
        Manipulador global de Ctrl+C.

        Copia o texto selecionado no widget com foco ou, se nenhum widget de texto
        tiver foco, delega ao editor ativo.
        """
        w = self._root.focus_get()
        if isinstance(w, tk.Text):
            try:
                sel = w.selection_get()
                if sel:
                    self._record_clipboard(sel)
            except tk.TclError:
                pass
            return None
        if isinstance(w, (tk.Entry, ttk.Entry)):
            try:
                sel = w.selection_get()
                self._root.clipboard_clear()
                self._root.clipboard_append(sel)
                self._record_clipboard(sel)
            except tk.TclError:
                pass
            return "break"

        self._copy()
        return "break"

    def _clipboard_cut(self, _e: tk.Event) -> str | None:
        """
        Manipulador global de Ctrl+X.

        Recorta o texto selecionado no widget com foco ou, se nenhum widget de texto
        tiver foco, delega ao editor ativo.
        """
        w = self._root.focus_get()
        if isinstance(w, tk.Text):
            try:
                sel = w.selection_get()
                if sel:
                    self._record_clipboard(sel)
            except tk.TclError:
                pass
            return None
        if isinstance(w, (tk.Entry, ttk.Entry)):
            try:
                sel = w.selection_get()
                self._root.clipboard_clear()
                self._root.clipboard_append(sel)
                self._record_clipboard(sel)
                w.delete(tk.SEL_FIRST, tk.SEL_LAST)
            except tk.TclError:
                pass
            return "break"
        self._cut()
        return "break"

    def _clipboard_paste(self, _e: tk.Event) -> str | None:
        """
        Manipulador global de Ctrl+V.

        Cola o conteúdo da área de transferência no widget com foco ou,
        se nenhum widget de texto tiver foco, delega ao editor ativo.
        """
        w = self._root.focus_get()
        if isinstance(w, tk.Text):
            return None
        if isinstance(w, (tk.Entry, ttk.Entry)):
            try:
                text = self._root.clipboard_get()
                w.insert(tk.INSERT, text)
            except tk.TclError:
                pass
            return "break"
        self._paste()
        return "break"

    def _setup_key_manager(self) -> None:
        """Inicializa o KeyRemapperManager e registra todos os handlers de comando."""
        self._key_mgr = KeyRemapperManager(self._root, self._key_store)

        handlers: dict[str, Any] = {
            "file.new": self.new_file,
            "file.open": self.open_file_dialog,
            "file.save": self.save_file,
            "file.save_as": self.save_as,
            "file.save_all": self.save_all,
            "file.close_tab": self._close_active_tab,
            "file.exit": self._on_exit,
            "edit.undo": self._undo,
            "edit.redo": self._redo,
            "edit.cut": self._cut,
            "edit.copy": self._copy,
            "edit.paste": self._paste,
            "edit.select_all": self._select_all,
            "edit.find": self._show_find,
            "edit.replace": self._show_replace,
            "edit.find_files": self._show_find_files,
            "edit.comment": self._toggle_comment,
            "edit.duplicate": self._duplicate_line,
            "edit.delete_line": self._delete_line,
            "edit.move_up": self._move_line_up,
            "edit.move_down": self._move_line_down,
            "edit.paste_history": self._paste_history_dialog,
            "view.command_palette": self._command_palette,
            "view.quick_open": self._quick_open,
            "view.terminal": lambda: self._show_panel("terminal"),
            "view.problems": lambda: self._show_panel("problems"),
            "view.zoom_in": lambda: self._zoom(+1),
            "view.zoom_out": lambda: self._zoom(-1),
            "view.zoom_reset": lambda: self._zoom(0),
            "view.fullscreen": self._toggle_fullscreen,
            "view.split": self._split_editor,
            "view.settings": lambda: self._activity("settings"),
            "view.color_picker": self._open_color_picker,
            "view.regex": lambda: self._show_panel("regex"),
            "view.ia": lambda: self._activity("ia"),
            "view.scratchpad": lambda: self._activity("scratchpad"),
            "view.snippets": lambda: self._activity("snippets"),
            "go.line": self._goto_line,
            "go.next_tab": self._next_tab,
            "go.prev_tab": self._prev_tab,
            "go.next_problem": self._next_problem,
            "go.prev_problem": self._prev_problem,
            "run.run_file": self._run_file,
            "run.format": self._format_document,
            "tools.bookmark_add": self._add_bookmark,
            "tools.bookmark_next": self._next_bookmark,
            "tools.zen_mode": self._toggle_zen_mode,
            "tools.vim_mode": self._toggle_vim_mode,
            "tools.kui": self._toggle_kui,
            "tools.pomodoro": lambda: self._activity("pomodoro"),
            "tools.macro": self._open_macro_toolbar,
            "tools.multi_cursor": self._toggle_multi_cursor,
            "tools.template": self._open_project_template,
        }

        for bid, handler in handlers.items():
            self._key_mgr.register(bid, handler)

        self._key_mgr.apply_all()

    def _open_key_remapper(self) -> None:
        """Abre o diálogo de remapeamento de atalhos de teclado."""

        def _on_apply() -> None:
            if self._key_mgr:
                self._key_mgr.apply_all()
            self._status.notify(_t("keyremap.applied"), level="ok")

        KeyRemapperDialog(self._root, self._tmgr, self._key_store, _on_apply)

    def _open_workspace_switcher(self) -> None:
        """Abre o Workspace Switcher para trocar rapidamente de projeto."""
        recent_folders = [Path(str(p))
                          for p in self._cfg.get("recent_folders", [])]
        if not recent_folders:
            self._status.notify(_t("ws.no_recent"), level="info")
            return

        def _on_open(path: Path) -> None:
            self._set_workspace(path)

        def _on_remove(path: Path) -> None:
            folders = self._cfg.get("recent_folders", [])
            folders = [f for f in folders if str(f) != str(path)]
            self._cfg.set("recent_folders", folders)
            self._cfg.save()

        WorkspaceSwitcherDialog(
            self._root,
            self._tmgr,
            recent_folders,
            on_open=_on_open,
            on_remove=_on_remove,
            on_browse=self.open_folder_dialog,
        )

    def _open_code_runner_config(self) -> None:
        """Abre o diálogo de configuração dos Code Runners."""
        CodeRunnerDialog(
            self._root,
            self._tmgr,
            self._runner_store,
            on_change=lambda: self._status.notify(
                _t("runner.updated"), level="ok"),
        )

    def _open_macro_toolbar(self) -> None:
        """Abre o Macro Recorder (toolbar flutuante)."""
        if self._macro_toolbar and self._macro_toolbar.winfo_exists():
            self._macro_toolbar.lift()
            return

        def _get_text() -> tk.Text | None:
            e = self._e()
            return e.text if e else None

        self._macro_toolbar = MacroToolbar(
            self._root,
            self._tmgr,
            self._macro_recorder,
            self._macro_store,
            _get_text,
        )

    def _open_project_template(self) -> None:
        """Abre o diálogo de criação de projeto a partir de template."""
        ProjectTemplateDialog(
            self._root,
            self._tmgr,
            self._template_store,
            on_open=self._set_workspace,
            cfg=self._cfg,
        )

    def _toggle_multi_cursor(self) -> None:
        """Liga/desliga o Multi-Cursor no editor ativo."""
        e = self._e()
        if not e:
            return
        text = e.text
        if (
            self._multi_cursor
            and self._multi_cursor._text is text
            and self._multi_cursor._active
        ):
            self._multi_cursor.deactivate()
            self._multi_cursor = None
            self._status.notify(_t("mc.status_inactive"), level="info")
        else:
            if self._multi_cursor:
                self._multi_cursor.deactivate()
            self._multi_cursor = MultiCursorManager(text)
            self._multi_cursor.activate()
            self._status.notify(
                _t("mc.status_active"),
                level="ok",
            )

    def _bind_key(self, key: str, cmd_id: str) -> None:
        """
        Registra um atalho de teclado para um comando de plugin.

        Chamado pelo PluginAPI.
        """
        cmd_info = self._command_registry.get(cmd_id)
        if cmd_info:
            _, handler = cmd_info
            with contextlib.suppress(tk.TclError):
                self._root.bind(key, lambda _, h=handler: h())

    def _show_settings(self) -> None:
        """Abre a janela flutuante de configurações."""
        win = SettingsWindow(
            self._root,
            self._tmgr,
            self._cfg,
            on_change=self._on_setting_change,
            on_show_settings_panel=self._show_settings_panel,
        )
        win._app_ref = self

    def _on_activity(self, key: str) -> None:
        """Chamado quando um botão da barra de atividades é clicado."""
        if key == "theme":
            if not THEME_ORDER:
                return
            try:
                idx = THEME_ORDER.index(self._tmgr.theme.name)
            except ValueError:
                idx = -1
            next_name = THEME_ORDER[(idx + 1) % len(THEME_ORDER)]
            self._set_theme(next_name)
            return
        if key == "settings":
            self._show_settings()
            return

        if key == "remote_ssh":
            self._open_remote_ssh()
            return

        if key == "chat":
            self._open_chat()
            return

        if key == "accessibility":
            self._open_accessibility()
            return

        if key == "collab":
            self._open_collab()
            return

        if key == "time_tracker":
            self._open_time_tracker()
            return

        if key == "dep_graph":
            self._open_dependency_graph()
            return

        if key == "terminal":
            self._show_panel("terminal")
            return
        if key == "run":
            self._show_panel("debug")
            return

        if key == self._current_sidebar and self._sidebar_visible:
            self._toggle_sidebar()
            return
        self._current_sidebar = key
        if not self._sidebar_visible:
            self._show_sidebar()

        panel_map: dict[str, tk.Widget | None] = {
            "explorer": (
                self._sidebar_placeholder
                if self._workspace is None
                else self._file_tree
            ),
            "search": self._find_files,
            "git": self._git_sidebar,
            "extensions": self._plugin_panel,
            "settings": self._settings_editor,
            "outline": self._code_outline,
            "bookmarks": self._bookmarks_panel,
            "scratchpad": self._scratchpad_pane,
            "snippets": self._snippets_panel,
            "docs": self._docs_panel,
            "codestats": self._code_stats_panel,
            "todo": self._todo_panel,
            "restclient": self._rest_client_panel,
            "preview": self._render_panel,
            "pomodoro": self._pomodoro_panel,
            "flowchart": self._flowchart_panel,
            "problems": self._sidebar_problems,
            "ia": getattr(self, "_painel_ia", None),
            "sql": getattr(self, "_sql_studio_panel", None),
            "terminal": None,
            "run": None,
        }

        if hasattr(self, "_plugin_panels"):
            panel_map.update(self._plugin_panels)

        for w in self._sidebar_stack.winfo_children():
            w.pack_forget()

        panel = panel_map.get(key)
        if panel is not None:
            panel.pack(fill="both", expand=True)
        elif key == "terminal":
            self._show_panel("terminal")
        elif key == "run":
            self._show_panel("debug")

    def _activity(self, key: str) -> None:
        """Ativa programaticamente um item da barra de atividades."""
        self._actbar._activate(key)

    def _toggle_sidebar(self) -> None:
        """Alterna a visibilidade da barra lateral."""
        if self._sidebar_visible:
            self._hide_sidebar()
        else:
            self._show_sidebar()

    def _animate_sidebar(self, target_x: int, on_done: Callable[[], None]) -> None:
        """Move o sash da sidebar até ``target_x`` com ease-out (~90 ms).

        Respeita ``a11y_reduce_motion``: nesse caso aplica o destino direto.
        """
        job = getattr(self, "_sidebar_anim_job", None)
        if job is not None:
            with contextlib.suppress(tk.TclError):
                self._root.after_cancel(job)
            self._sidebar_anim_job = None

        try:
            start_x = int(self._h_paned.sash_coord(1)[0])
        except (tk.TclError, IndexError):
            start_x = target_x
        steps = 6
        if self._cfg.get("a11y_reduce_motion", False) or start_x == target_x:
            steps = 1

        def _step(i: int) -> None:
            progress = i / steps
            eased = 1 - (1 - progress) ** 3
            x = round(start_x + (target_x - start_x) * eased)
            with contextlib.suppress(tk.TclError):
                self._h_paned.sash_place(1, x, 0)
            if i < steps:
                self._sidebar_anim_job = self._root.after(15, _step, i + 1)
            else:
                self._sidebar_anim_job = None
                on_done()

        _step(1)

    def _show_sidebar(self) -> None:
        """Exibe a barra lateral (com transição suave)."""
        dock = getattr(self, "_dock", None)
        if dock is not None and dock.floating:
            self._sidebar_visible = True
            self._update_panel_toggle_arrows()
            dock.show()
            return
        w = self._cfg.get("sidebar_width", 220)
        self._sidebar_visible = True
        self._update_panel_toggle_arrows()

        with contextlib.suppress(tk.TclError):
            self._h_paned.paneconfigure(self._sidebar, minsize=60)

        def _settle() -> None:
            with contextlib.suppress(tk.TclError):
                self._h_paned.paneconfigure(self._sidebar, minsize=60, width=w)
                self._h_paned.sash_place(1, 48 + w, 0)

        self._root.update_idletasks()
        self._animate_sidebar(48 + w, _settle)

    def _hide_sidebar(self) -> None:
        """Oculta a barra lateral (com transição suave)."""
        dock = getattr(self, "_dock", None)
        if dock is not None and dock.floating:
            self._sidebar_visible = False
            self._update_panel_toggle_arrows()
            dock.hide()
            return
        self._sidebar_visible = False
        self._update_panel_toggle_arrows()

        def _settle() -> None:
            with contextlib.suppress(tk.TclError):
                self._h_paned.paneconfigure(self._sidebar, minsize=0, width=0)
                self._h_paned.sash_place(1, 48, 0)

        self._root.update_idletasks()
        with contextlib.suppress(tk.TclError):
            self._h_paned.paneconfigure(self._sidebar, minsize=0)
        self._animate_sidebar(48, _settle)

    def _register_plugin_panel(
        self, panel_id: str, icon: str, factory: Callable, tooltip: str
    ) -> None:
        """Registra um painel de plugin na barra de atividades e na sidebar."""
        with contextlib.suppress(tk.TclError):
            widget = factory(self._sidebar_stack)

            self._actbar.add_plugin_item(panel_id, icon, tooltip or panel_id)

            if not hasattr(self, "_plugin_panels"):
                self._plugin_panels: dict[str, tk.Widget] = {}
            self._plugin_panels[panel_id] = widget

    def _active_editor(self) -> EditorPane | None:
        """Alias estável de ``_e()`` exposto como callable para componentes externos.

        Usado por SearchBar, IA, plugins e outros que recebem ``get_editor=``
        como parâmetro de construção.  Não remover - é a API pública interna.
        """
        return self._e()

    def _select_tab(self, idx: int) -> None:
        """Seleciona a aba de índice `idx`, tornando seu editor visível."""
        if idx < 0 or idx >= len(self._editors):
            return
        same = idx == self._active_idx
        if not same and 0 <= self._active_idx < len(self._editors):
            with contextlib.suppress(tk.TclError):
                self._editors[self._active_idx].pack_forget()
        with contextlib.suppress(tk.TclError):
            if self._welcome.winfo_ismapped():
                self._welcome.pack_forget()

        if self._kui and getattr(self._kui, "_enabled", False):
            with contextlib.suppress(tk.TclError):
                self._kui.hide_welcome()
                self._kui.ensure_editor_visible()

        self._active_idx = idx
        e = self._editors[idx]
        self._active_pane = e
        if not same:
            dual_mode = getattr(self, "_dual_mode", False)
            dual_container = getattr(self, "_dual_left_container", None)
            dual_ok = (
                dual_mode
                and dual_container is not None
                and _widget_exists(dual_container)
            )
            editor_ok = _widget_exists(e)
            if not editor_ok:
                container = dual_container if dual_ok else self._editor_container
                new_e = EditorPane(
                    container,
                    self._tmgr,
                    self._cfg,
                    self._lint_mgr,
                    self._snippets,
                    on_change=self._on_editor_change,
                    on_cursor=self._on_cursor_change,
                    on_title=self._on_editor_title,
                    idx=idx,
                )
                if self._lsp_mgr:
                    new_e.set_lsp_manager(self._lsp_mgr)
                new_e.set_goto_callback(self._open_file_at_line)
                self._wire_breakpoint_sync(new_e)
                old_path = getattr(e, "path", None)
                if old_path:
                    new_e.load(old_path)
                self._editors[idx] = new_e
                self._active_pane = new_e
                e = new_e
                editor_ok = True
            if editor_ok:
                if dual_ok:
                    with contextlib.suppress(tk.TclError):
                        e.pack(fill="both", expand=True, in_=dual_container)
                else:
                    with contextlib.suppress(tk.TclError):
                        e.pack(fill="both", expand=True,
                               in_=self._editor_container)
            self._tabbar.set_active(idx)
            with contextlib.suppress(tk.TclError):
                e.text.focus_set()
        self._update_breadcrumb()
        self._update_title()
        if e.lang == "text" and e.path:
            from src.constants import EXT_LANG

            inferred = EXT_LANG.get(e.path.suffix.lower(), "text")
            if inferred != "text":
                e.lang = inferred
                with __import__("contextlib").suppress(Exception):
                    e.hl.set_lang(inferred)
        self._status.set_lang(e.lang)
        self._status.set_encoding(e.encoding)
        self._status.set_eol(e.eol)
        if e._diags:
            self._status.set_diags(e._diags)

        if not same and hasattr(self, "_search_bar"):
            with contextlib.suppress(tk.TclError):
                self._search_bar.notify_editor_change()

        if e.path:
            self._lint_mgr.schedule(e.path, e.get_content(), e.lang)

        self._fire_hook("tab_change", idx)

        self._notify_active_panels(e)
        self._refresh_debug_breakpoints()

        if self._kui:
            with contextlib.suppress(tk.TclError):
                open_paths = [str(ed.path) for ed in self._editors if ed.path]
                self._kui.notify_open_files(open_paths)

        with contextlib.suppress(tk.TclError):
            self._snippets_panel.notify_active_lang(e.lang)

        with contextlib.suppress(tk.TclError):
            if self._vim_enabled and (
                self._vim_mode is None or self._vim_mode.editor_pane is not e
            ):
                if self._vim_mode is not None:
                    self._vim_mode.disable()
                    self._vim_mode = None
                self._enable_vim_mode()

    def _notify_active_panels(self, e: EditorPane) -> None:
        """Notifica todos os painéis registrados sobre a mudança de editor ativo."""
        path = e.path if (e and e.path) else None
        path_str = str(path) if path else None
        with contextlib.suppress(tk.TclError):
            self._render_panel.notify_active_file(path)
        with contextlib.suppress(Exception):
            self._web_preview_follow(path)
        with contextlib.suppress(tk.TclError):
            self._flowchart_panel.notify_active_file(path_str)
        with contextlib.suppress(tk.TclError):
            if hasattr(self, "_pomodoro_panel"):
                pass

        with contextlib.suppress(tk.TclError):
            if hasattr(self, "_code_outline") and e:
                self._code_outline.refresh()
        with contextlib.suppress(tk.TclError):
            if self._current_sidebar == "docs":
                self._docs_panel.refresh()

        with contextlib.suppress(Exception):
            if hasattr(self, "_tracker_model") and path_str:
                ws = str(self._workspace) if self._workspace else None
                self._tracker_model.on_activity(path_str, ws)

        if _SMART_TIPS_AVAILABLE and e and e.path:
            with contextlib.suppress(Exception):
                if self._smart_tips is None:
                    self._smart_tips = _SmartTips(
                        e.text, self._cfg, self._tmgr)
                else:
                    self._smart_tips._widget = e.text
                    self._smart_tips._cfg = self._cfg
                    self._smart_tips._tmgr = self._tmgr
                interval_sec = self._cfg.get("smart_tips_interval", 30)
                self._smart_tips._min_interval_ms = int(interval_sec) * 1000
                self._smart_tips.analyse(
                    e.get_content(), e.lang, e.path, delay_ms=1500)

    def _close_tab(self, idx: int) -> None:
        """Fecha a aba de índice `idx`, perguntando para salvar se necessário."""
        if idx < 0 or idx >= len(self._editors):
            return
        e = self._editors[idx]
        if e.modified:
            name = e.path.name if e.path else _t("ui.tab_untitled")
            ans = messagebox.askyesnocancel(
                _t("file.save"),
                _t("ui.confirm_close") + f"\n\n{name}",
                parent=self._root,
            )
            if ans is None:
                return
            if ans:
                e.save()

        self._fire_hook("file_close", e.path)
        closed_path = str(e.path) if e.path else ""

        if e.path and hasattr(self, "_lsp_mgr") and self._lsp_mgr:
            with contextlib.suppress(Exception):
                self._lsp_mgr.did_close(e.path, getattr(e, "lang", ""))

        e.pack_forget()
        with contextlib.suppress(Exception):
            e.destroy()
        self._editors.pop(idx)
        self._tabbar.remove_tab(idx)

        if closed_path:
            self._closed_tabs.append(closed_path)
            del self._closed_tabs[:-20]
        self._schedule_gc()

        if closed_path:
            self._problems_panel.clear_file_diags(closed_path)
            with contextlib.suppress(tk.TclError):
                self._sidebar_problems.clear_file_diags(closed_path)

        open_paths = [str(ed.path) for ed in self._editors if ed.path]
        self._problems_panel.set_open_files(open_paths)
        with contextlib.suppress(tk.TclError):
            self._sidebar_problems.set_open_files(open_paths)
        if self._kui:
            with contextlib.suppress(tk.TclError):
                self._kui.notify_open_files(open_paths)

        if not self._editors:
            self._active_idx = -1
            self._active_pane = None
            self._present_start_screen()
            self._update_title()
            self._status.set_lang("text")
            with contextlib.suppress(tk.TclError):
                self._breadcrumb.set_path(None)
            return

        new_idx = min(idx, len(self._editors) - 1)
        self._active_idx = -1
        self._select_tab(new_idx)

    def _close_active_tab(self) -> None:
        """Fecha a aba ativa."""
        if self._active_idx >= 0:
            self._close_tab(self._active_idx)

    def _close_all_tabs(self) -> None:
        """Fecha todas as abas abertas."""
        while self._editors:
            self._close_tab(0)

    def _schedule_gc(self, delay_ms: int = 400) -> None:
        """Agenda um ``gc.collect()`` fora do caminho crítico da interface.

        Fechar várias abas em sequência dispara apenas uma coleta, evitando
        pausas perceptíveis (50-200 ms) a cada fechamento.
        """
        if self._gc_job is not None:
            with contextlib.suppress(tk.TclError):
                self._root.after_cancel(self._gc_job)

        def _run() -> None:
            import gc

            self._gc_job = None
            gc.collect()

        self._gc_job = self._root.after(delay_ms, _run)

    def _goto_tab_number(self, number: int) -> None:
        """Vai para a aba ``number`` (1-8); 9 sempre seleciona a última aba."""
        if not self._editors:
            return
        idx = len(self._editors) - 1 if number >= 9 else number - 1
        self._select_tab(idx)

    def _reopen_closed_tab(self) -> None:
        """Reabre o último arquivo fechado que ainda existe no disco."""
        while self._closed_tabs:
            path = Path(self._closed_tabs.pop())
            if not path.is_file():
                continue
            existing = self._find_editor_for_path(path)
            if existing is not None:
                self._select_tab(existing)
            else:
                self.open_file(path)
            return

    def _next_tab(self) -> None:
        """Ativa a próxima aba (direita)."""
        if self._editors:
            self._select_tab((self._active_idx + 1) % len(self._editors))

    def _prev_tab(self) -> None:
        """Ativa a aba anterior (esquerda)."""
        if self._editors:
            self._select_tab((self._active_idx - 1) % len(self._editors))

    def _find_editor_for_path(self, path: Path) -> int | None:
        """Retorna o índice do editor que contém o arquivo `path`, se existir."""
        for i, e in enumerate(self._editors):
            if e.path and e.path.resolve() == path.resolve():
                return i
        return None

    def _show_file_diff_with_content(
        self, original: str, modified: str, orig_name: str, mod_name: str
    ) -> None:
        """Exibe o diff entre dois conteúdos no painel de diff."""
        self._diff_pane.show(
            original or "", modified or "", orig_name, mod_name)
        self._show_panel("diff")

    def new_file(self) -> EditorPane:
        """Cria um novo arquivo (aba em branco)."""
        return self._create_editor()

    def _create_tab_with_text(self, text: str) -> None:
        """Cria nova aba com conteúdo de texto pré-preenchido (usado pela IA)."""
        editor = self._create_editor()
        with contextlib.suppress(tk.TclError):
            editor.text.insert("1.0", text)

    def _create_editor(self, path: Path | None = None) -> EditorPane:
        """
        Cria um novo editor (aba) e o adiciona à lista.

        Se `path` for fornecido, tenta carregar o arquivo.
        Retorna o editor criado.
        """
        if path:
            existing = self._find_editor_for_path(path)
            if existing is not None:
                self._select_tab(existing)
                return self._editors[existing]

        e = EditorPane(
            self._editor_container,
            self._tmgr,
            self._cfg,
            self._lint_mgr,
            self._snippets,
            on_change=self._on_editor_change,
            on_cursor=self._on_cursor_change,
            on_title=self._on_editor_title,
            on_copy=self._on_copy_event,
            idx=len(self._editors),
        )

        if self._lsp_mgr:
            e.set_lsp_manager(self._lsp_mgr)
        e.set_goto_callback(self._open_file_at_line)
        self._wire_breakpoint_sync(e)
        self._editors.append(e)
        tab_idx = len(self._editors) - 1
        title = path.name if path else _t("ui.tab_untitled")
        self._tabbar.add_tab(title, path)

        if self._kui:
            with contextlib.suppress(tk.TclError):
                open_paths = [str(ed.path) for ed in self._editors if ed.path]
                self._kui.notify_open_files(open_paths)

        if self._welcome.winfo_ismapped():
            self._welcome.pack_forget()

        if self._kui and getattr(self._kui, "_enabled", False):
            with contextlib.suppress(tk.TclError):
                self._kui.hide_welcome()
                self._kui.ensure_editor_visible()
        if 0 <= self._active_idx < len(self._editors) - 1:
            self._editors[self._active_idx].pack_forget()

        self._active_idx = tab_idx
        self._active_pane = e
        _dual_mode = getattr(self, "_dual_mode", False)
        _dual_c = getattr(self, "_dual_left_container", None)
        if _dual_mode and _widget_exists(_dual_c):
            with contextlib.suppress(tk.TclError):
                e.pack(fill="both", expand=True, in_=_dual_c)
        else:
            with contextlib.suppress(tk.TclError):
                e.pack(fill="both", expand=True, in_=self._editor_container)
        self._tabbar.set_active(tab_idx)
        with contextlib.suppress(tk.TclError):
            e.text.focus_set()
        self._refresh_debug_breakpoints()
        return e

    def _wire_breakpoint_sync(self, editor: EditorPane) -> None:
        """Conecta o gutter de um editor ao painel de debug.

        Sempre que um breakpoint é adicionado/removido clicando no gutter,
        a lista é empurrada para ``DebugPanel.set_breakpoints`` - que, por
        sua vez, repassa ao vivo a uma sessão DAP ativa para o mesmo
        arquivo, se houver. Sem esta conexão, breakpoints marcados na UI
        nunca chegavam ao depurador de fato.
        """
        debug_panel = getattr(self, "_debug_panel", None)
        if debug_panel is None:
            return

        def _on_toggle(lines: set[int], ed: EditorPane = editor) -> None:
            if ed is not self._active_editor():
                return
            path_str = str(ed.path) if ed.path else ""
            with contextlib.suppress(Exception):
                debug_panel.set_breakpoints(sorted(lines), path_str)

        with contextlib.suppress(Exception):
            editor.gutter.set_on_breakpoint_toggle(_on_toggle)

    def _refresh_debug_breakpoints(self) -> None:
        """Reenvia ao painel de debug os breakpoints do editor ativo.

        Chamado ao trocar de aba/editor para que o painel sempre reflita
        os breakpoints do arquivo atualmente em foco, e não os da aba
        anterior.
        """
        debug_panel = getattr(self, "_debug_panel", None)
        editor = self._active_editor()
        if debug_panel is None or editor is None:
            return
        with contextlib.suppress(Exception):
            lines = sorted(editor.gutter.get_breakpoints())
            path_str = str(editor.path) if editor.path else ""
            debug_panel.set_breakpoints(lines, path_str)

    def _open_file(self, path_or_str: Any, *, adopt_workspace: bool = True) -> None:
        """Abre um arquivo (Path ou string) no editor.

        ``adopt_workspace=False`` impede que um arquivo fora do workspace troque
        a pasta aberta (usado por arquivos baixados de servidores remotos, cuja
        pasta é temporária).

        Se o modo dual estiver ativo E o foco estiver no painel direito,
        abre no lado direito via _dual_open_file para evitar que todos os
        arquivos abertos no modo split caiam sempre no painel esquerdo.
        """
        path = (
            Path(str(path_or_str)) if not isinstance(
                path_or_str, Path) else path_or_str
        )
        if not path.exists():
            messagebox.showerror(
                _t("ui.file_not_found"),
                _t("ui.could_not_open", path=str(path)),
                parent=self._root,
            )
            return

        if getattr(self, "_dual_mode", False) and getattr(
            self, "_dual_right_active", False
        ):
            self._dual_open_file(path)
            return

        editor = self._create_editor(path)
        if editor.load(path):
            self._cfg.add_recent_file(path)
            self._cfg.save()
            self._rebuild_recent_menu()
            if adopt_workspace and (
                self._workspace is None
                or (
                    not str(path).startswith(str(self._workspace))
                    and path.parent != self._workspace
                )
            ):
                self._set_workspace(path.parent)
            self._update_breadcrumb()
            self._fire_hook("file_open", path)
            self._status.notify(
                f"Opened: {path.name}", duration=2000, level="ok")
            self._output_panel.write(
                f"📂 Aberto: {path}", "Codis", "info", timestamp=True
            )

            self._status.set_lang(editor.lang)
            self._status.set_encoding(editor.encoding)
            self._status.set_eol(editor.eol)
            self._notify_active_panels(editor)

    def _open_file_at_line(self, path: Path, line: int) -> None:
        """Abre um arquivo e posiciona o cursor na linha especificada."""
        self._open_file(path)
        e = self._active_editor()
        if e:
            e.goto_line(line)

    def open_file_dialog(self) -> None:
        """Abre um diálogo para selecionar arquivos e abri-los."""
        paths = filedialog.askopenfilenames(
            title=_t("file.open"),
            filetypes=[
                ("All supported", " ".join(f"*{ext}" for ext in EXT_LANG)),
                ("Python", "*.py *.pyw"),
                ("JavaScript", "*.js *.mjs *.jsx"),
                ("TypeScript", "*.ts *.tsx"),
                ("Web", "*.html *.css *.scss"),
                ("Data", "*.json *.yaml *.toml *.csv"),
                ("All files", "*.*"),
            ],
            parent=self._root,
        )
        for p in paths:
            self._open_file(Path(p))

    def open_folder_dialog(self) -> None:
        """Abre um diálogo para selecionar uma pasta e definir como workspace."""
        folder = filedialog.askdirectory(
            title=_t("file.open_folder"), parent=self._root
        )
        if folder:
            self._set_workspace(Path(folder))

    def _apply_workspace_trust(self, path: Path) -> None:
        """Pergunta se a pasta é confiável e restringe execução de código se não for.

        Em modo restrito o LSP é desligado e as ferramentas de IA que executam
        comandos ficam bloqueadas. Se a opção ``workspace_trust`` estiver
        desligada, nenhuma restrição é aplicada.
        """
        if not self._cfg.get("workspace_trust", True):
            set_restricted(False)
            return
        trust = WorkspaceTrust()
        try:
            trusted = trust.prompt_if_needed(self._root, path, self._tmgr.theme)
        except tk.TclError:
            trusted = trust.is_trusted(path)
        set_restricted(not trusted)
        if self._lsp_mgr:
            self._lsp_mgr.set_enabled(trusted and bool(self._cfg.get("lsp_enabled", True)))
        if not trusted:
            logger.info("Workspace em modo restrito: %s", path)

    def _set_workspace(self, path: Path) -> None:
        """Define o workspace atual e notifica os componentes."""
        self._workspace = path
        self._apply_workspace_trust(path)
        self._file_tree.set_root(path)

        with contextlib.suppress(tk.TclError):
            if (
                self._sidebar_current is self._sidebar_placeholder
                or self._current_sidebar == "explorer"
            ):
                self._sidebar_placeholder.pack_forget()
                self._file_tree.pack(fill="both", expand=True)
                self._sidebar_current = self._file_tree
                self._current_sidebar = "explorer"

        if not self._sidebar_visible:
            self._show_sidebar()
        self._git_sidebar.set_root(path)
        self._git_bottom.set_root(path)
        with contextlib.suppress(AttributeError):
            self._terminal.set_cwd(str(path))

        with contextlib.suppress(tk.TclError):
            self._code_stats_panel.set_workspace(path)
        with contextlib.suppress(tk.TclError):
            self._todo_panel.set_workspace(path)
        with contextlib.suppress(Exception):
            dw = getattr(self, "_depgraph_win", None)
            if dw is not None and dw.winfo_exists():
                for w in dw.winfo_children():
                    if hasattr(w, "set_workspace"):
                        w.set_workspace(path)
                        break

        with contextlib.suppress(tk.TclError):
            if self._kui:
                self._kui.notify_workspace_opened()

        if self._lsp_mgr:
            with contextlib.suppress(tk.TclError):
                self._lsp_mgr.set_root(path)
        self._cfg.add_recent_folder(path)
        self._cfg.save()
        self._update_title()
        self._output_panel.write(
            f"Workspace: {path}", "Codis", "info", timestamp=True)
        if self._bus is not None:
            self._bus.emit(
                "workspace.open",
                {"path": str(path)},
                source="CodisApp",
            )

    def _attach_log_handler(self) -> None:
        """Conecta um handler de logging para exibir mensagens no painel de saída."""
        panel = self._output_panel

        class _PanelHandler(logging.Handler):
            LEVEL_MAP = {
                logging.DEBUG: "info",
                logging.INFO: "info",
                logging.WARNING: "warn",
                logging.ERROR: "error",
                logging.CRITICAL: "error",
            }

            def emit(self, record: logging.LogRecord) -> None:
                """Formata e escreve o log record no painel de output, agendando no Tk thread."""
                try:
                    msg = self.format(record)
                    lvl = self.LEVEL_MAP.get(record.levelno, "info")

                    panel.write(msg, "Codis", lvl, timestamp=True)
                except Exception:
                    logger.debug(
                        "_PanelHandler: falha ao escrever no painel de output",
                        exc_info=True,
                    )

        handler = _PanelHandler()
        handler.setLevel(logging.WARNING)
        handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
        logging.getLogger("Codis").addHandler(handler)

        root_handler = _PanelHandler()
        root_handler.setLevel(logging.ERROR)
        root_handler.setFormatter(logging.Formatter("[%(name)s] %(message)s"))
        logging.getLogger().addHandler(root_handler)

    def save_file(self) -> bool:
        """Salva o arquivo ativo. Retorna True se bem‑sucedido."""
        e = self._active_editor()
        if e:
            ok = e.save()
            if ok:
                self._fire_hook("file_save", e.path)
                self._fire_hook("after_save", e.path)
                if self._bus is not None and e.path:
                    self._bus.emit(
                        "editor.save",
                        {"path": str(e.path), "lang": getattr(e, "lang", "")},
                        source="CodisApp",
                    )

                    self._bus.emit(
                        "file.save",
                        {"path": str(e.path)},
                        source="CodisApp",
                    )

                if e.path:
                    with contextlib.suppress(tk.TclError):
                        self._todo_panel.notify_file_saved(e.path)
                    with contextlib.suppress(tk.TclError):
                        self._render_panel.notify_file_saved(e.path)
                    with contextlib.suppress(Exception):
                        self._web_preview_on_saved(e.path)
                    with contextlib.suppress(tk.TclError):
                        self._flowchart_panel.notify_file_saved(str(e.path))
                if _SMART_TIPS_AVAILABLE and self._smart_tips is not None:
                    with contextlib.suppress(Exception):
                        self._smart_tips._widget = e.text
                        self._smart_tips._state.last_shown_ms = 0
                        self._smart_tips.analyse(
                            e.get_content(), e.lang, e.path, delay_ms=2000
                        )
            return ok
        return False

    def save_as(self) -> bool:
        """Abre diálogo para salvar o arquivo ativo com novo nome e caminho."""
        e = self._active_editor()
        return e.save_as() if e else False

    def save_all(self) -> None:
        """Salva todos os arquivos abertos com modificações pendentes."""
        failed: list[str] = []
        for e in self._editors:
            if e.modified:
                try:
                    ok = e.save() is not False
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Falha ao salvar '%s': %s", e.path, exc)
                    ok = False
                if not ok:
                    failed.append(e.path.name if e.path else _t("ui.tab_untitled"))
        if failed:
            self._status.notify(_t("msg.autosave_failed", names=", ".join(failed),
                                   default="Falha ao salvar: {names}"), level="warn")
        else:
            self._status.notify(_t("msg.all_files_saved"), level="ok")

    def revert_file(self) -> None:
        """Reverte o arquivo ativo para a versão salva em disco."""
        e = self._active_editor()
        if e:
            e.revert()

    def _rebuild_recent_menu(self) -> None:
        """Reconstrói o menu de arquivos recentes."""
        self._recent_menu.delete(0, "end")
        files = self._cfg.get_recent_files()
        for p in files[:20]:
            self._recent_menu.add_command(
                label=str(p), command=lambda f=p: self._open_file(f)
            )
        if not files:
            self._recent_menu.add_command(
                label=_t("hc.app.no_recent_files"), state="disabled"
            )
        self._recent_menu.add_separator()
        self._recent_menu.add_command(
            label=_t("hc.app.clear_recent"), command=self._clear_recent
        )

    def _clear_recent(self) -> None:
        """Limpa a lista de arquivos recentes."""
        self._cfg.set("recent_files", [])
        self._cfg.save()
        self._rebuild_recent_menu()
