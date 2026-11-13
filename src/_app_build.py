"""Mixin ``_AppBuildMixin`` - construção dos painéis principais da UI do Codis.

Responsável por instanciar e conectar todos os painéis visuais (editor, terminal,
git, ferramentas IA, etc.) que compõem a janela principal da aplicação.
"""

from __future__ import annotations

import contextlib
import logging
import sys
import tkinter as tk
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.constants import (
    APP_NAME,
    FONT_UI,
    PLUGINS_DIR,
    WINDOW_MIN,
    WINDOW_SIZE,
)
from src.editor.diff_pane import DiffPane
from src.i18n import _t
from src.theme.base import THEME_ORDER
from src.ui.lazy_panel import LazyPanel
from src.ui.activity_bar import ActivityBar
from src.ui.bookmarks import BookmarksPanel
from src.ui.breadcrumb import BreadCrumb
from src.ui.code_outline import CodeOutline
from src.ui.code_stats import CodeStatsPanel
from src.ui.debug_panel import DebugPanel
from src.ui.docs_panel import DocsPanel
from src.ui.file_tree import FileTree
from src.ui.find_in_files import FindInFilesPanel
from src.ui.flowchart_panel import FlowchartPanel
from src.ui.git_panel import GitPanel
from src.ui.k_ui._dock import KDock
from src.ui.output_panel import OutputPanel
from src.ui.plugin_panel import PluginPanel
from src.ui.pomodoro_panel import PomodoroPanel
from src.ui.problems import ProblemsPanel
from src.ui.regex_playground import RegexPlayground
from src.ui.render_panel import RenderPanel
from src.ui.rest_client import RestClientPanel
from src.ui.scratchpad import ScratchpadPane
from src.ui.search_bar import SearchBar
from src.ui.settings_editor import SettingsEditor
from src.ui.snippets_panel import SnippetsPanel
from src.ui.status_bar import StatusBar
from src.ui.tab_bar import TabBar
from src.ui.terminal import TerminalPanel
from src.ui.todo_panel import TodoPanel
from src.ui.welcome import WelcomeScreen

try:
    from src.ui.sql_studio import SQLStudioPanel

    _SQL_OK = True
except Exception:
    SQLStudioPanel = None  # type: ignore[assignment,misc]
    _SQL_OK = False
try:
    from src.ia.iaspace import PainelIA

    _IA_OK = True
except Exception:
    PainelIA = None  # type: ignore[assignment,misc]
    _IA_OK = False

if TYPE_CHECKING:
    pass

logger = logging.getLogger("Codis")

__all__ = ["_AppBuildMixin"]


class _AppBuildMixin:
    """Mixin de Build do CodisApp."""

    def _show_settings_panel(self) -> None:
        """Exibe o editor de configurações clássico na barra lateral."""

        for w in self._sidebar_stack.winfo_children():
            w.pack_forget()

        self._settings_editor.pack(fill="both", expand=True)
        self._current_sidebar = "settings"

        if not self._sidebar_visible:
            self._show_sidebar()

    def _setup_window(self) -> None:
        """Configura a janela principal (título, geometria, cores)."""
        t = self._tmgr.theme
        self._root.title(APP_NAME)
        from src.ui.ui_scale import fit_window

        w, h = fit_window(self._root, *WINDOW_SIZE)
        self._root.geometry(f"{w}x{h}")
        self._root.minsize(min(WINDOW_MIN[0], w), min(WINDOW_MIN[1], h))
        self._root.config(bg=t.bg)
        self._root.protocol("WM_DELETE_WINDOW", self._on_exit)
        self._root.after(100, self._apply_titlebar_color)

    def _apply_titlebar_color(self) -> None:
        """Colorir a barra de título nativa do Windows de acordo com o tema ativo."""
        if sys.platform != "win32":
            return
        try:
            import ctypes

            t = self._tmgr.theme

            hex_col = getattr(t, "bg", "#1e1e2e").lstrip("#")
            r = int(hex_col[0:2], 16)
            g = int(hex_col[2:4], 16)
            b = int(hex_col[4:6], 16)
            colorref = (b << 16) | (g << 8) | r
            hwnd = ctypes.windll.user32.GetParent(self._root.winfo_id())
            if not hwnd:
                hwnd = self._root.winfo_id()

            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 35, ctypes.byref(ctypes.c_int(colorref)), 4
            )
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 20, ctypes.byref(ctypes.c_int(1)), 4
            )
        except Exception as exc:
            logger.debug(
                "Não foi possível aplicar cor na barra de título: %s", exc)

    def _build_layout(self) -> None:
        """Constrói toda a estrutura de painéis.

        Inclui activity bar, sidebar, área de editor e painel inferior.
        """
        t = self._tmgr.theme

        self._status = StatusBar(self._root, self._tmgr, self._cfg)
        self._status.pack(side="bottom", fill="x")
        self._status.bind_callbacks(
            git=self._toggle_git_panel,
            problems=self._show_problems,
            lang=self._pick_language,
            eol=self._pick_eol,
            encoding=self._pick_encoding,
            indent=self._pick_indent,
            zoom=self._reset_zoom,
        )

        self._h_paned = tk.PanedWindow(
            self._root,
            orient="horizontal",
            bg=t.border,
            sashwidth=6,
            sashrelief="flat",
            sashcursor="sb_h_double_arrow",
        )
        self._h_paned.pack(fill="both", expand=True)

        self._actbar = ActivityBar(
            self._h_paned, self._tmgr, self._on_activity, cfg=self._cfg
        )
        self._h_paned.add(self._actbar, width=48, minsize=48, stretch="never")

        self._sidebar = tk.Frame(self._root, bg=t.sidebar_bg)
        sw = self._cfg.get("sidebar_width", 220)
        self._h_paned.add(self._sidebar, width=sw, minsize=60, stretch="never")
        self._build_sidebar()
        self._dock = KDock(
            self._root,
            self._h_paned,
            self._sidebar,
            self._actbar,
            dark=getattr(t, "dark", True),
            get_width=lambda: self._cfg.get("sidebar_width", 260),
            set_width=lambda w: self._cfg.set("sidebar_width", w),
            get_left=lambda: 0 if getattr(self, "_zen_mode", False) else 48,
            get_top=self._dock_top,
            reduce_motion=lambda: self._cfg.get("a11y_reduce_motion", False),
            get_pinned=lambda: self._cfg.get("kui_dock_pinned", False),
            set_pinned=lambda v: self._cfg.set("kui_dock_pinned", bool(v)),
            on_change=self._on_dock_change,
            on_raise=self._on_dock_raise,
        )

        self._actbar.add_plugin_item("sql", "🗄", _t("kui.cmd.sql"))
        self._actbar.add_plugin_item("codestats", "📊", _t("kui.cmd.stats"))
        self._actbar.add_plugin_item("todo", "✅", _t("kui.cmd.todo"))
        self._actbar.add_plugin_item("restclient", "🌍", _t("kui.cmd.rest"))
        self._actbar.add_plugin_item("preview", "🌐", _t("kui.cmd.preview"))
        self._actbar.add_plugin_item("pomodoro", "⏱", _t("kui.cmd.pomodoro"))
        self._actbar.add_plugin_item(
            "flowchart", "🔀", _t("kui.cmd.flowchart"))
        self._actbar.add_plugin_item("snippets", "✂️", _t("kui.cmd.snippets"))
        self._actbar.add_plugin_item(
            "time_tracker", "⏱", _t("actbar.time_tracker"))
        self._actbar.add_plugin_item("dep_graph", "🔗", _t("actbar.dep_graph"))
        self._actbar.add_plugin_item("docs", "📚", _t("kui.cmd.outline"))

        _opt_items = [
            ("remote_ssh", "🖧", "actbar.remote_ssh", "ssh_enabled"),
            ("collab", "👥", "actbar.collab", "collab_enabled"),
            ("accessibility", "♿", "actbar.accessibility", "a11y_screen_reader"),
        ]
        _vis = set(self._actbar._visible)
        for _key, _icon, _label_key, _cfg_key in _opt_items:
            if self._cfg.get(_cfg_key, False):
                _vis.add(_key)
        self._actbar._visible = _vis

        self._content_frame = tk.Frame(self._h_paned, bg=t.bg)
        self._h_paned.add(self._content_frame, stretch="always")
        self._build_content(self._content_frame)

        self._actbar._activate("explorer")

        self._build_panel_toggle_buttons()

        self._hide_sidebar()
        self._hide_bottom_panel()

        self._setup_smooth_resize()

    def _setup_smooth_resize(self) -> None:
        """Instala resize fluido nos PanedWindows e na janela principal.

        Técnica:
          - opaqueresize=True -> os painéis acompanham o mouse em tempo real
          - Suspend/resume de minimap e syntax highlight durante o drag do
            sash e durante o resize da janela, eliminando o lag visual
          - <Configure> da raiz com debounce: overlays pesados só voltam
            quando o resize para (150 ms sem eventos)
        """

        for paned in (self._h_paned, self._v_paned):
            with contextlib.suppress(tk.TclError):
                paned.config(opaqueresize=True)

        self._resize_drag_job: str | None = None
        self._resize_dragging = False

        def _suspend_all() -> None:
            """Suspende todos os overlays pesados do editor durante operações bloqueantes."""
            for ed in self._editors:
                with contextlib.suppress(Exception):
                    ed.minimap.suspend()
                with contextlib.suppress(Exception):
                    ed.hl.suspend()
                with contextlib.suppress(Exception):
                    if ed.color_preview:
                        ed.color_preview.set_enabled(False)
                with contextlib.suppress(Exception):
                    if ed.inlay_hints:
                        ed.inlay_hints.set_enabled(False)
                with contextlib.suppress(Exception):
                    if ed.inline_blame:
                        ed.inline_blame.set_enabled(False)

        def _resume_all() -> None:
            """Retoma todos os overlays pesados do editor após operações bloqueantes."""
            for ed in self._editors:
                with contextlib.suppress(Exception):
                    ed.minimap.resume()
                with contextlib.suppress(Exception):
                    ed.hl.resume()
                with contextlib.suppress(Exception):
                    if ed.color_preview and self._cfg.get("color_preview", True):
                        ed.color_preview.set_enabled(True)
                with contextlib.suppress(Exception):
                    if ed.inlay_hints and self._cfg.get("inlay_hints", True):
                        ed.inlay_hints.set_enabled(True)
                with contextlib.suppress(Exception):
                    if ed.inline_blame and self._cfg.get("inline_blame", True):
                        ed.inline_blame.set_enabled(True)

        def _on_sash_press(_e: tk.Event) -> None:
            if self._resize_dragging:
                return
            self._resize_dragging = True
            _suspend_all()

        def _on_sash_release(_e: tk.Event) -> None:
            if not self._resize_dragging:
                return
            if self._resize_drag_job:
                with contextlib.suppress(tk.TclError):
                    self._root.after_cancel(self._resize_drag_job)
            self._resize_drag_job = self._root.after(100, _finish_resize)

        def _finish_resize() -> None:
            self._resize_drag_job = None
            self._resize_dragging = False
            _resume_all()
            with contextlib.suppress(tk.TclError):
                self._cfg.set("sidebar_width", self._sidebar.winfo_width())
            with contextlib.suppress(tk.TclError):
                self._cfg.set("panel_height",
                              self._bottom_frame.winfo_height())

        for paned in (self._h_paned, self._v_paned):
            paned.bind("<ButtonPress-1>", _on_sash_press, add=True)
            paned.bind("<ButtonRelease-1>", _on_sash_release, add=True)

        self._win_resize_job: str | None = None
        self._win_resizing = False
        self._win_last_size = (0, 0)

        def _end_window_resize() -> None:
            self._win_resize_job = None
            self._win_resizing = False
            if not self._resize_dragging:
                _resume_all()

        def _on_root_configure(e: tk.Event) -> None:
            if e.widget is not self._root:
                return
            size = (e.width, e.height)
            if size == self._win_last_size:
                return
            first = self._win_last_size == (0, 0)
            self._win_last_size = size
            if first:
                return
            if not self._win_resizing:
                self._win_resizing = True
                if not self._resize_dragging:
                    _suspend_all()
            if self._win_resize_job:
                with contextlib.suppress(tk.TclError):
                    self._root.after_cancel(self._win_resize_job)
            self._win_resize_job = self._root.after(150, _end_window_resize)

        self._root.bind("<Configure>", _on_root_configure, add="+")

    def _build_panel_toggle_buttons(self) -> None:
        """Cria pequenos botões flutuantes para alternar sidebar e painel inferior."""
        t = self._tmgr.theme

        self._sidebar_toggle_btn = tk.Label(
            self._root,
            text="▶",
            bg=t.tabbar_bg,
            fg=t.fg,
            font=(FONT_UI, 9, "bold"),
            cursor="hand2",
            padx=3,
            pady=8,
            relief="flat",
        )
        self._sidebar_toggle_btn.place(x=48, rely=0.5, anchor="w")
        self._sidebar_toggle_btn.bind(
            "<Button-1>", lambda _: self._toggle_sidebar())
        self._sidebar_toggle_btn.bind(
            "<Enter>", lambda _: self._sidebar_toggle_btn.config(bg=t.sel_bg)
        )
        self._sidebar_toggle_btn.bind(
            "<Leave>", lambda _: self._sidebar_toggle_btn.config(
                bg=t.tabbar_bg)
        )
        self._sidebar_toggle_btn.lift()

        self._panel_toggle_btn = tk.Label(
            self._root,
            text="▲",
            bg=t.tabbar_bg,
            fg=t.fg,
            font=(FONT_UI, 9, "bold"),
            cursor="hand2",
            padx=8,
            pady=3,
            relief="flat",
        )
        self._panel_toggle_btn.place(
            relx=1.0, rely=1.0, anchor="se", x=-2, y=-26)
        self._panel_toggle_btn.bind(
            "<Button-1>", lambda _: self._toggle_panel())
        self._panel_toggle_btn.bind(
            "<Enter>", lambda _: self._panel_toggle_btn.config(bg=t.sel_bg)
        )
        self._panel_toggle_btn.bind(
            "<Leave>", lambda _: self._panel_toggle_btn.config(bg=t.tabbar_bg)
        )
        self._panel_toggle_btn.lift()

    def _dock_top(self) -> int:
        """Y inicial do painel flutuante: abaixo da topbar e da tabbar K-UI."""
        kui = getattr(self, "_kui", None)
        return kui.chrome_top() if kui is not None else 0

    def _on_dock_change(self, visible: bool) -> None:
        """O dock abriu/fechou sozinho (✕, clique fora): sincroniza o estado do app."""
        self._sidebar_visible = visible
        self._update_panel_toggle_arrows()

    def _on_dock_raise(self) -> None:
        """Após elevar o painel, re-eleva as barras que devem ficar acima dele."""
        kui = getattr(self, "_kui", None)
        if kui is not None:
            kui.raise_chrome()

    def _update_panel_toggle_arrows(self) -> None:
        """Atualiza a direção das setas flutuantes conforme o estado de visibilidade."""
        with contextlib.suppress(tk.TclError):
            if hasattr(self, "_sidebar_toggle_btn"):
                actbar_w = 48 if not self._zen_mode else 0
                dock = getattr(self, "_dock", None)
                if dock is not None and dock.floating and self._sidebar_visible:
                    actbar_w = dock.right_edge()
                self._sidebar_toggle_btn.config(
                    text="▶" if not self._sidebar_visible else "◀"
                )
                self._sidebar_toggle_btn.place(
                    x=actbar_w, rely=0.5, anchor="w")
                self._sidebar_toggle_btn.lift()
        with contextlib.suppress(tk.TclError):
            if hasattr(self, "_panel_toggle_btn"):
                self._panel_toggle_btn.config(
                    text="▲" if not self._panel_visible else "▼"
                )
                self._panel_toggle_btn.lift()

    def _build_sidebar(self) -> None:
        """Constrói o painel lateral com árvore de arquivos e painéis auxiliares."""
        t = self._tmgr.theme
        self._sidebar_stack = tk.Frame(self._sidebar, bg=t.sidebar_bg)
        self._sidebar_stack.pack(fill="both", expand=True)

        self._file_tree = FileTree(
            self._sidebar_stack,
            self._tmgr,
            self._cfg,
            on_open=self._open_file,
        )

        self._find_files = FindInFilesPanel(
            self._sidebar_stack,
            self._tmgr,
            self._cfg,
            get_root=lambda: self._workspace,
            get_editor=self._active_editor,
            on_open=self._open_file_at_line,
        )

        self._git_sidebar = GitPanel(
            self._sidebar_stack,
            self._tmgr,
            self._cfg,
            root_path=None,
            on_refresh=self._on_git_refresh,
            on_show_diff=self._show_file_diff_with_content,
        )

        self._plugin_panel = PluginPanel(
            self._sidebar_stack, self._tmgr, self._plugin_loader
        )

        self._settings_editor = SettingsEditor(
            self._sidebar_stack,
            self._tmgr,
            self._cfg,
            on_change=self._on_setting_change,
        )

        self._code_outline = LazyPanel(
            lambda: CodeOutline(
                self._sidebar_stack,
                self._tmgr,
                get_editor=self._active_editor,
            ),
            '_code_outline',
        )

        self._bookmarks_panel = LazyPanel(
            lambda: BookmarksPanel(
                self._sidebar_stack,
                self._tmgr,
                mgr=self._bookmark_mgr,
                on_goto=self._open_file_at_line,
            ),
            '_bookmarks_panel',
        )

        self._scratchpad_pane = LazyPanel(
            lambda: ScratchpadPane(self._sidebar_stack, self._tmgr),
            '_scratchpad_pane',
        )

        self._snippets_panel = LazyPanel(
            lambda: SnippetsPanel(
                self._sidebar_stack,
                self._tmgr,
                self._snippets,
                get_editor=self._active_editor,
            ),
            '_snippets_panel',
        )

        self._docs_panel = LazyPanel(
            lambda: DocsPanel(
                self._sidebar_stack,
                self._tmgr,
                get_editor=self._active_editor,
                on_goto=lambda ln: (
                    self._active_editor().goto_line(ln) if self._active_editor() else None
                ),
            ),
            '_docs_panel',
        )

        self._code_stats_panel = LazyPanel(
            lambda: CodeStatsPanel(
                self._sidebar_stack, self._tmgr, self._cfg
            ),
            '_code_stats_panel',
        )

        self._todo_panel = LazyPanel(
            lambda: TodoPanel(
                self._sidebar_stack,
                self._tmgr,
                self._cfg,
                open_file_at_line=self._open_file_at_line,
            ),
            '_todo_panel',
        )

        if _SQL_OK and SQLStudioPanel is not None:
            self._sql_studio_panel: Any | None = SQLStudioPanel(
                self._sidebar_stack, self._tmgr, self._cfg
            )
        else:
            self._sql_studio_panel = None

        self._rest_client_panel = LazyPanel(
            lambda: RestClientPanel(
                self._sidebar_stack, self._tmgr, self._cfg
            ),
            '_rest_client_panel',
        )

        self._render_panel = RenderPanel(
            self._sidebar_stack, self._tmgr, self._cfg)
        self._render_panel.set_advanced_callback(self._open_web_preview)
        self._render_panel.set_selection_callback(
            lambda: self._e().text.get(tk.SEL_FIRST, tk.SEL_LAST) if self._e() else ""
        )

        self._pomodoro_panel = LazyPanel(
            lambda: PomodoroPanel(
                self._sidebar_stack,
                self._tmgr,
                on_message=lambda msg, dur=3000: self._status.notify(
                    msg, duration=dur),
            ),
            '_pomodoro_panel',
        )

        self._flowchart_panel = LazyPanel(
            lambda: FlowchartPanel(
                self._sidebar_stack, self._tmgr, self._cfg
            ),
            '_flowchart_panel',
        )

        self._sidebar_problems = ProblemsPanel(
            self._sidebar_stack,
            self._tmgr,
            on_goto=self._goto_problem_pos,
            on_apply_fix=self._apply_problem_fix,
            on_fix_all=self._apply_problem_fix_all,
        )

        if _IA_OK and PainelIA is not None:
            self._painel_ia: Any | None = PainelIA(
                self._sidebar_stack,
                self._tmgr,
                self._cfg,
                get_editor=self._active_editor,
                on_open_new_tab=self._create_tab_with_text,
                get_workspace=lambda: self._workspace,
                get_open_editors=lambda: list(self._editors),
            )
        else:
            self._painel_ia = None

        self._sidebar_placeholder = tk.Frame(
            self._sidebar_stack, bg=t.sidebar_bg)
        ph_inner = tk.Frame(self._sidebar_placeholder, bg=t.sidebar_bg)
        ph_inner.place(relx=0.5, rely=0.5, anchor="center")
        _ph_icon = tk.Label(
            ph_inner, text="📂", bg=t.sidebar_bg, fg=t.fg_muted, font=("Segoe UI", 28)
        )
        _ph_icon.pack()
        _ph_title = tk.Label(
            ph_inner,
            text=_t("sidebar.open_folder_hint"),
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
            wraplength=180,
            justify="center",
        )
        _ph_title.pack(pady=(6, 12))
        _ph_btn = tk.Label(
            ph_inner,
            text="  " + _t("file.open_folder") + "  ",
            bg=t.btn_bg,
            fg=t.fg,
            font=(FONT_UI, 9),
            cursor="hand2",
            padx=8,
            pady=5,
            relief="flat",
        )
        _ph_btn.pack()
        _ph_btn.bind("<Button-1>", lambda _: self.open_folder_dialog())
        _ph_btn.bind("<Enter>", lambda _: _ph_btn.config(bg=t.sel_bg))
        _ph_btn.bind("<Leave>", lambda _: _ph_btn.config(bg=t.btn_bg))

        if self._workspace is None:
            self._sidebar_placeholder.pack(fill="both", expand=True)
            self._sidebar_current: tk.Widget | None = self._sidebar_placeholder
        else:
            self._file_tree.pack(fill="both", expand=True)
            self._sidebar_current = self._file_tree

    def _build_content(self, parent: tk.Widget) -> None:
        """Constrói a área de conteúdo (editor + painel inferior)."""
        t = self._tmgr.theme
        self._v_paned = tk.PanedWindow(
            parent,
            orient="vertical",
            bg=t.border,
            sashwidth=6,
            sashrelief="flat",
            sashcursor="sb_v_double_arrow",
        )
        self._v_paned.pack(fill="both", expand=True)

        editor_area = tk.Frame(self._v_paned, bg=t.bg)
        self._v_paned.add(editor_area, stretch="always")
        self._build_editor_area(editor_area)

        ph = self._cfg.get("panel_height", 220)
        self._bottom_frame = tk.Frame(self._v_paned, bg=t.panel_bg)
        self._v_paned.add(self._bottom_frame, height=ph,
                          minsize=80, stretch="never")
        self._build_bottom_panel()

    def _build_editor_area(self, parent: tk.Widget) -> None:
        """Constrói a área superior (tab bar, breadcrumb, search bar, container do editor)."""
        t = self._tmgr.theme

        self._tabbar = TabBar(
            parent,
            self._tmgr,
            on_select=self._select_tab,
            on_close=self._close_tab,
            on_split_right=self._split_tab_to_right,
        )
        self._tabbar.pack(fill="x")

        self._breadcrumb = BreadCrumb(
            parent, self._tmgr, on_open=self._open_file)
        if self._cfg.get("breadcrumbs", True):
            self._breadcrumb.pack(fill="x")

        self._editor_container = tk.Frame(parent, bg=t.editor_bg)
        self._editor_container.pack(fill="both", expand=True)

        self._search_bar = SearchBar(
            parent,
            self._tmgr,
            self._cfg,
            get_editor=self._active_editor,
            pack_before=self._editor_container,
        )

        recent = [str(p) for p in self._cfg.get_recent_files()[:12]]
        self._welcome = WelcomeScreen(
            self._editor_container,
            self._tmgr,
            self._cfg,
            on_new=self.new_file,
            on_open_file=self.open_file_dialog,
            on_open_folder=self.open_folder_dialog,
            on_open_recent=self._open_file,
            recent_files=recent,
            on_clone_success=self._on_clone_success,
            on_log=lambda msg, ch, lv: self._output_panel.write(
                msg, channel=ch, level=lv, timestamp=True
            ),
        )
        self._welcome.pack(fill="both", expand=True)

    def _build_bottom_panel(self) -> None:
        """Constrói o painel inferior com abas para problems, terminal, output, etc.

        Cada aba tem menu de contexto (clique direito) com a opção de
        desplugagem para janela flutuante. O botão "⊞" ao lado das abas
        desplugga o painel atualmente ativo. Disponível tanto no modo
        clássico quanto no modo K-UI, via ``PanelDetachManager``
        compartilhado (``self._panel_detach``).
        """
        t = self._tmgr.theme

        self._panel_tabbar = tk.Frame(
            self._bottom_frame, bg=t.tabbar_bg, height=28)
        self._panel_tabbar.pack(fill="x")
        self._panel_tabbar.pack_propagate(False)

        self._panel_tabs: dict[str, tk.Label] = {}
        for key, i18n_key in (
            ("problems", "panel.problems"),
            ("terminal", "panel.terminal"),
            ("output", "panel.output"),
            ("git", "panel.git"),
            ("diff", "panel.diff"),
            ("debug", "panel.debug"),
            ("regex", "panel.regex"),
            ("sql", "panel.sql"),
        ):
            lbl = tk.Label(
                self._panel_tabbar,
                text=f"  {_t(i18n_key)}  ",
                bg=t.tabbar_bg,
                fg=t.tab_in_fg,
                font=(FONT_UI, 9),
                cursor="hand2",
            )
            lbl.pack(side="left")
            lbl.bind("<Button-1>", lambda _, k=key: self._show_panel(k))
            self._panel_tabs[key] = lbl
            self._bind_panel_tab_context_menu(key, lbl)

        self._detach_btn = tk.Label(
            self._panel_tabbar,
            text=_t("hc.app.btn_split"),
            bg=t.tabbar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
            cursor="hand2",
        )
        self._detach_btn.pack(side="right", padx=(0, 2))
        self._detach_btn.bind(
            "<Button-1>", lambda _: self._detach_current_panel())
        self._detach_btn.bind(
            "<Enter>",
            lambda _: self._detach_btn.config(
                fg=t.accent if hasattr(t, "accent") else "#7c4dff"
            ),
        )
        self._detach_btn.bind(
            "<Leave>", lambda _: self._detach_btn.config(fg=t.fg_muted)
        )

        close_btn = tk.Label(
            self._panel_tabbar,
            text=_t("hc.app.btn_close"),
            bg=t.tabbar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
            cursor="hand2",
        )
        close_btn.pack(side="right")
        close_btn.bind("<Button-1>", lambda _: self._toggle_panel())

        tk.Frame(self._bottom_frame, bg=t.border, height=1).pack(fill="x")

        self._panel_content = tk.Frame(self._bottom_frame, bg=t.panel_bg)
        self._panel_content.pack(fill="both", expand=True)

        cwd = str(self._workspace) if self._workspace else str(Path.home())

        self._problems_panel = ProblemsPanel(
            self._panel_content,
            self._tmgr,
            on_goto=self._goto_problem_pos,
            on_apply_fix=self._apply_problem_fix,
            on_fix_all=self._apply_problem_fix_all,
        )

        self._terminal = TerminalPanel(
            self._panel_content, self._tmgr, self._cfg, cwd=cwd
        )

        self._output_panel = OutputPanel(
            self._panel_content, self._tmgr, self._cfg)

        self._attach_log_handler()

        self._git_bottom = GitPanel(
            self._panel_content,
            self._tmgr,
            self._cfg,
            root_path=None,
            on_refresh=self._on_git_refresh,
        )

        self._diff_pane = DiffPane(
            self._panel_content,
            self._tmgr,
            self._cfg,
            on_close=lambda: self._show_panel("problems"),
        )

        self._debug_panel = DebugPanel(
            self._panel_content,
            self._tmgr,
            self._cfg,
            get_editor=self._active_editor,
        )

        self._regex_panel = RegexPlayground(
            self._panel_content,
            self._tmgr,
            get_text=lambda: self._e().get_content() if self._e() else "",
            apply_replace=self._regex_replace,
        )

        if _SQL_OK and SQLStudioPanel is not None:
            self._sql_bottom: Any | None = SQLStudioPanel(
                self._panel_content, self._tmgr, self._cfg
            )
        else:
            self._sql_bottom = None

        self._show_panel("problems")

    def _detach_current_panel(self) -> None:
        """Destaca o painel inferior atualmente ativo para janela flutuante.

        Funciona tanto no modo clássico quanto no modo K-UI, via
        ``PanelDetachManager`` compartilhado. Painéis sem fábrica registrada
        (ex.: diff, sql, que dependem de estado vivo do app) simplesmente
        não fazem nada - eles ainda não suportam detach.
        """
        key = getattr(self, "_current_panel", "terminal")
        if not key:
            return
        with contextlib.suppress(AttributeError, tk.TclError):
            if self._panel_detach.can_detach(key):
                self._panel_detach.detach(key)
            else:
                self._status.notify(_t("panel.detach_unsupported", panel=key))

    def _mk_menu(self, parent, label, underline=0):
        """Cria um submenu (MenuDef) e o adiciona ao parent (MenuBar ou MenuDef).

        Tanto a MenuBar de nível superior quanto qualquer MenuDef de cascade
        aceitam um MenuDef como filho - é a mesma estrutura de dados usada
        para montar os dropdowns holográficos, então não há distinção
        entre "menu de topo" e "submenu" na construção.
        """
        from src.ui.menu_bar import MenuDef

        m = MenuDef()
        parent.add_cascade(label=label, menu=m, underline=underline)
        return m

    @staticmethod
    def _mc(m, label, cmd, accel=""):
        """Adiciona um comando a um menu."""
        m.add_command(label=label, command=cmd, accelerator=accel)

    @staticmethod
    def _ms(m):
        """Adiciona separador visual ao menu."""
        m.add_separator()

    def _build_menus(self) -> None:
        """Constrói a barra de menus principal - moderna, organizada e responsiva."""
        t = self._tmgr.theme
        _mk = self._mk_menu
        _c = self._mc
        _s = self._ms
        _cb_kw = {
            "background": t.floating_bg,
            "foreground": t.fg,
            "activebackground": t.sel_bg,
            "activeforeground": t.sel_fg,
            "selectcolor": t.accent,
        }

        from src.ui.menu_bar import MenuBar

        mb_position = self._cfg.get("menubar_position", "top")
        mb = MenuBar(self._root, self._tmgr, position=mb_position)
        mb.pack_into(self._root)
        self._menubar = mb
        self._plugin_menu_cache = None

        fm = _mk(mb, _t("menu.file"))
        _c(fm, _t("file.new"), self.new_file, "Ctrl+N")
        _c(fm, _t("file.new_window"), self._new_window)
        _s(fm)
        _c(fm, _t("file.open"), self.open_file_dialog, "Ctrl+O")
        _c(fm, _t("file.open_folder"), self.open_folder_dialog)
        self._recent_menu = _mk(fm, _t("file.open_recent"))
        self._rebuild_recent_menu()
        _s(fm)
        _c(fm, _t("file.save"), self.save_file, "Ctrl+S")
        _c(fm, _t("file.save_as"), self.save_as, "Ctrl+Shift+S")
        _c(fm, _t("file.save_all"), self.save_all)
        _c(fm, _t("file.revert"), self.revert_file)
        _s(fm)
        self._autosave_var = tk.BooleanVar(
            value=self._cfg.get("autosave", False))
        fm.add_checkbutton(
            label=_t("file.autosave"),
            variable=self._autosave_var,
            command=self._toggle_autosave,
            **_cb_kw,
        )
        _s(fm)
        _c(fm, _t("file.close"), self._close_active_tab, "Ctrl+W")
        _c(fm, _t("file.close_all"), self._close_all_tabs)
        _s(fm)
        _c(fm, _t("file.restart"), self._restart_process)
        _c(fm, _t("file.exit"), self._on_exit, "Alt+F4")

        em = _mk(mb, _t("menu.edit"))
        _c(em, _t("edit.undo"), self._undo, "Ctrl+Z")
        _c(em, _t("edit.redo"), self._redo, "Ctrl+Y")
        _s(em)
        _c(em, _t("edit.cut"), self._cut, "Ctrl+X")
        _c(em, _t("edit.copy"), self._copy, "Ctrl+C")
        _c(em, _t("edit.paste"), self._paste, "Ctrl+V")
        _c(em, _t("edit.select_all"), self._select_all, "Ctrl+A")
        _s(em)
        _c(em, _t("edit.find"), self._show_find, "Ctrl+F")
        _c(em, _t("edit.find_replace"), self._show_replace, "Ctrl+H")
        _c(em, _t("edit.find_in_files"), self._show_find_files, "Ctrl+Shift+F")
        _s(em)
        lm = _mk(em, _t("hc.app.transform"))
        _c(lm, _t("edit.transform_upper"), lambda: self._transform("upper"))
        _c(lm, _t("edit.transform_lower"), lambda: self._transform("lower"))
        _c(lm, "Title Case", lambda: self._transform("title"))
        _s(lm)
        _c(lm, _t("edit.sort_lines"), lambda: self._sort_lines(False))
        _c(lm, _t("edit.sort_lines_desc"), lambda: self._sort_lines(True))
        _c(lm, _t("edit.trim_trailing"), self._trim_trailing)
        _c(lm, _t("edit.remove_dupes"), self._remove_duplicate_lines)
        _c(em, _t("edit.toggle_comment"), self._toggle_comment, "Ctrl+/")
        _c(em, _t("edit.duplicate_line"), self._duplicate_line, "Ctrl+D")
        _c(em, _t("edit.delete_line"), self._delete_line, "Ctrl+Shift+K")
        _c(em, _t("edit.move_line_up"), self._move_line_up, "Alt+Up")
        _c(em, _t("edit.move_line_down"), self._move_line_down, "Alt+Down")
        _c(em, _t("edit.join_lines"), self._join_lines)
        _s(em)
        _c(em, _t("edit.paste_history"), self._paste_history_dialog, "Ctrl+Shift+V")

        sm = _mk(mb, _t("menu.selection"))
        _c(sm, "Select Line", self._select_line, "Ctrl+L")
        _c(sm, _t("edit.copy_line_up"), self._copy_line_up)
        _c(sm, _t("edit.copy_line_down"), self._copy_line_down)

        vm = _mk(mb, _t("menu.view"))
        _c(vm, _t("view.command_palette"), self._command_palette, "Ctrl+Shift+P")
        _s(vm)
        sb_m = _mk(vm, "Barra Lateral")
        _c(sb_m, "Explorer", lambda: self._activity("explorer"))
        _c(sb_m, "Pesquisar", lambda: self._activity("search"))
        _c(sb_m, "Git", lambda: self._activity("git"))
        _c(sb_m, "Extensions", lambda: self._activity("extensions"))
        if _IA_OK:
            _c(sb_m, "Assistente IA", lambda: self._activity("ia"), "Ctrl+Shift+A")
        pn_m = _mk(vm, "Paineis")
        _c(
            pn_m,
            _t("view.problems"),
            lambda: self._show_panel("problems"),
            "Ctrl+Shift+M",
        )
        _c(pn_m, _t("view.terminal"), lambda: self._show_panel("terminal"), "Ctrl+`")
        _c(pn_m, "Output", lambda: self._show_panel("output"))
        _c(pn_m, "Debug", lambda: self._show_panel("debug"))
        _c(pn_m, "Code Outline", lambda: self._activity("outline"))
        _c(pn_m, "File Timeline", self._open_file_timeline)
        _s(vm)
        mbp_m = _mk(vm, _t("view.menubar_position"))
        self._mbp_top_var = tk.BooleanVar(
            value=self._cfg.get("menubar_position", "top") == "top"
        )
        self._mbp_bottom_var = tk.BooleanVar(
            value=self._cfg.get("menubar_position", "top") == "bottom"
        )
        mbp_m.add_checkbutton(
            label=_t("view.menubar_position_top"),
            variable=self._mbp_top_var,
            command=lambda: self._set_menubar_position("top"),
        )
        mbp_m.add_checkbutton(
            label=_t("view.menubar_position_bottom"),
            variable=self._mbp_bottom_var,
            command=lambda: self._set_menubar_position("bottom"),
        )
        _s(vm)
        t2 = self._tmgr.theme
        vm.add_command(
            label=_t("theme.open_selector", "Temas..."),
            command=self._open_theme_panel,
            background=t2.floating_bg,
            foreground=t2.fg,
            activebackground=t2.sel_bg,
            activeforeground=t2.sel_fg,
        )
        self._themes_menu = None
        _s(vm)
        self._wrap_var = tk.BooleanVar(value=self._cfg.get("word_wrap", False))
        self._minimap_var = tk.BooleanVar(value=self._cfg.get("minimap", True))
        self._bc_var = tk.BooleanVar(value=self._cfg.get("breadcrumbs", True))
        vm.add_checkbutton(
            label=_t("view.word_wrap"),
            variable=self._wrap_var,
            command=self._toggle_word_wrap,
            **_cb_kw,
        )
        vm.add_checkbutton(
            label=_t("view.minimap"),
            variable=self._minimap_var,
            command=self._toggle_minimap,
            **_cb_kw,
        )
        vm.add_checkbutton(
            label=_t("view.breadcrumbs"),
            variable=self._bc_var,
            command=self._toggle_breadcrumbs,
            **_cb_kw,
        )
        _s(vm)
        _c(vm, _t("view.zoom_in"), lambda: self._zoom(+1), "Ctrl+=")
        _c(vm, _t("view.zoom_out"), lambda: self._zoom(-1), "Ctrl+-")
        _c(vm, _t("view.zoom_reset"), lambda: self._zoom(0), "Ctrl+0")
        _s(vm)
        _c(vm, _t("view.ui_scale_up"), lambda: self._ui_scale_step(+1), "Ctrl+Alt+=")
        _c(vm, _t("view.ui_scale_down"), lambda: self._ui_scale_step(-1), "Ctrl+Alt+-")
        _c(vm, _t("view.ui_scale_auto"), self._ui_scale_auto, "Ctrl+Alt+0")
        _s(vm)
        _c(vm, _t("view.full_screen"), self._toggle_fullscreen, "F11")
        _c(vm, _t("view.split_editor"), self._split_editor, "Ctrl+\\")

        gm = _mk(mb, _t("menu.go"))
        _c(gm, _t("go.to_line"), self._goto_line, "Ctrl+G")
        _c(gm, _t("go.to_symbol"), self._goto_symbol, "Ctrl+Shift+O")
        _s(gm)
        _c(gm, _t("go.next_problem"), self._next_problem, "F8")
        _c(gm, _t("go.prev_problem"), self._prev_problem, "Shift+F8")
        _s(gm)
        _c(gm, _t("go.next_tab"), self._next_tab, "Ctrl+Tab")
        _c(gm, _t("go.prev_tab"), self._prev_tab, "Ctrl+Shift+Tab")
        _c(gm, _t("go.matching_bracket"), self._goto_bracket)

        rm = _mk(mb, _t("menu.run"))
        _c(rm, _t("run.run_file"), self._run_file, "F5")
        _c(rm, _t("run.format_document"), self._format_document, "Shift+Alt+F")
        _c(rm, _t("run.lint_file"), self._lint_file)
        _c(rm, _t("run.fix_all"), self._fix_all)

        trm = _mk(mb, _t("menu.terminal"))
        _c(trm, _t("terminal.new"), self._new_terminal, "Ctrl+Shift+`")
        _c(trm, _t("terminal.clear"), self._clear_terminal)
        _c(trm, _t("terminal.kill"), self._kill_terminal)
        _c(trm, _t("terminal.run_selection"), self._run_selection)

        pm = _mk(mb, _t("menu.plugins"))
        _c(pm, _t("menu.plugins_manage"), lambda: self._activity("extensions"))
        _c(pm, _t("menu.plugins_install"), self._install_plugin)
        _c(pm, _t("menu.plugins_folder"), lambda: self._open_extern(PLUGINS_DIR))
        self._plugin_menu_cache = pm

        xm = _mk(mb, _t("menu.tools"))

        txt_m = _mk(xm, "Texto & Arquivo")
        _c(txt_m, _t("tools.word_count"), self._word_count_dialog)
        _c(txt_m, _t("tools.insert_seq"), self._insert_sequence_dialog)
        _s(txt_m)
        _c(txt_m, _t("tools.session_notes"), self._open_session_notes)
        _c(txt_m, _t("tools.bookmark_add"), self._add_bookmark, "Ctrl+F2")
        _c(txt_m, _t("tools.bookmark_list"),
           lambda: self._activity("bookmarks"), "F2")

        enc_m = _mk(xm, "Encode & Hash")
        _c(enc_m, _t("tools.base64_enc"), self._base64_encode)
        _c(enc_m, _t("tools.base64_dec"), self._base64_decode)
        _s(enc_m)
        _c(enc_m, _t("tools.url_enc"), self._url_encode)
        _c(enc_m, _t("tools.url_dec"), self._url_decode)
        _s(enc_m)
        _c(enc_m, _t("tools.hash_md5"), lambda: self._hash_selection("md5"))
        _c(enc_m, _t("tools.hash_sha256"), lambda: self._hash_selection("sha256"))

        prod_m = _mk(xm, _t("tools.cat_productivity", "Produtividade & Notas"))
        _c(
            prod_m,
            _t("file.scratchpad"),
            lambda: self._activity("scratchpad"),
            "Ctrl+Shift+N",
        )
        _c(
            prod_m,
            _t("snippets.title", "Snippets"),
            lambda: self._activity("snippets"),
            "Ctrl+Shift+J",
        )
        _c(prod_m, _t("todo.title", "Todo Tree"), lambda: self._activity("todo"))
        _c(
            prod_m,
            _t("tools.task_manager", "✅ Tarefas"),
            self._open_task_manager,
            "Ctrl+Alt+T",
        )
        _s(prod_m)
        _c(
            prod_m,
            _t("pomodoro.title", "Pomodoro"),
            lambda: self._activity("pomodoro"),
            "Ctrl+Alt+P",
        )
        _c(
            prod_m,
            _t("tools.time_tracker", "⏱ Code Time Tracker"),
            self._open_time_tracker,
            "Ctrl+Alt+W",
        )

        dev_m = _mk(xm, _t("tools.cat_dev_utils",
                    "Utilitários de Desenvolvimento"))
        _c(dev_m, _t("tools.color_picker"),
           self._open_color_picker, "Ctrl+Shift+C")
        _c(
            dev_m,
            _t("tools.regex_playground"),
            lambda: self._show_panel("regex"),
            "Ctrl+Shift+R",
        )
        _c(dev_m, _t("sql.title"), lambda: self._show_panel("sql"), "Ctrl+Shift+Q")
        _c(
            dev_m,
            _t("restclient.title", "REST Client"),
            lambda: self._activity("restclient"),
        )
        _c(
            dev_m,
            _t("tools.diff_viewer", "⇄ Diff Viewer"),
            self._open_diff,
            "Ctrl+Alt+D",
        )
        _c(
            dev_m,
            _t("tools.formatter", "{ } Formatador"),
            self._open_formatter,
            "Ctrl+Alt+F",
        )
        _c(dev_m, _t("tools.env_manager", "⚙ Variáveis de Env"), self._open_env_manager)
        _s(dev_m)
        _c(
            dev_m,
            _t("codestats.title", "Code Stats"),
            lambda: self._activity("codestats"),
        )
        _c(
            dev_m,
            _t("flowchart.title", "Flowchart"),
            lambda: self._activity("flowchart"),
        )
        _c(
            dev_m,
            _t("tools.dep_graph", "🔗 Grafo de Deps"),
            self._open_dependency_graph,
        )

        sys_m = _mk(xm, _t("tools.cat_system", "Sistema & Processos"))
        _c(sys_m, _t("tools.pip_manager", "📦 Gerenciador Pip"), self._open_pip_manager)
        _c(
            sys_m,
            _t("tools.process_manager", "Gerenciador de Processos"),
            self._open_process_manager,
            "Ctrl+Alt+M",
        )
        _c(
            sys_m,
            _t("tools.performance_panel", "Modo Performance"),
            self._open_performance_panel,
        )
        _s(sys_m)
        _c(
            sys_m,
            _t("tools.open_paint", "Paint Integrado"),
            self._open_paint_panel,
            "Ctrl+Alt+B",
        )
        _c(sys_m, _t("preview.title", "Preview"),
           lambda: self._activity("preview"))
        _c(sys_m, _t("webpreview.menu", "Preview Web (navegador embutido)"),
           self._open_web_preview, "Ctrl+Alt+Y")

        misc_m = _mk(xm, _t("tools.cat_personal", "Calendário & Pessoal"))
        _c(
            misc_m,
            _t("tools.calculator", "🔢 Calculadora"),
            self._open_calculator,
            "Ctrl+Alt+C",
        )
        _c(misc_m, _t("tools.calendar", "📅 Calendário"), self._open_calendar)
        _c(misc_m, _t("tools.clock", "🕐 Relógio"), self._open_clock, "Ctrl+Alt+K")

        lsp_m = _mk(xm, "LSP - Navegacao de Codigo")
        _c(
            lsp_m,
            "Go to Definition",
            lambda: self._e().lsp_goto_definition() if self._e() else None,
            "F12",
        )
        _c(
            lsp_m,
            "Go to Declaration",
            lambda: self._e().lsp_goto_declaration() if self._e() else None,
        )
        _c(
            lsp_m,
            "Go to Type Definition",
            lambda: self._e().lsp_goto_type_definition() if self._e() else None,
        )
        _c(
            lsp_m,
            "Go to Implementation",
            lambda: self._e().lsp_goto_implementation() if self._e() else None,
            "Ctrl+F12",
        )
        _s(lsp_m)
        _c(
            lsp_m,
            "Find References",
            lambda: self._e().lsp_find_references() if self._e() else None,
            "Shift+F12",
        )
        _c(
            lsp_m,
            "Rename Symbol",
            lambda: self._e().lsp_rename_symbol() if self._e() else None,
            "F6",
        )
        _c(
            lsp_m,
            "Code Actions",
            lambda: self._e().lsp_code_action() if self._e() else None,
            "Ctrl+.",
        )
        _s(lsp_m)
        _c(
            lsp_m,
            "Incoming Calls",
            lambda: self._e().lsp_incoming_calls() if self._e() else None,
        )
        _c(
            lsp_m,
            "Outgoing Calls",
            lambda: self._e().lsp_outgoing_calls() if self._e() else None,
        )
        _s(lsp_m)
        _c(lsp_m, "Servidores LSP...", self._open_lsp_panel)

        ws_m = _mk(xm, "Workspace & Editor")
        _c(ws_m, _t("ws.menu_item"), self._open_workspace_switcher, "Ctrl+Shift+W")
        _c(ws_m, _t("runner.menu_item"), self._open_code_runner_config)
        _c(ws_m, _t("macro.menu_item"), self._open_macro_toolbar, "Ctrl+Shift+M")
        _c(ws_m, _t("tmpl.menu_item"), self._open_project_template)
        _c(ws_m, _t("mc.menu_item"), self._toggle_multi_cursor, "Ctrl+Alt+M")
        _s(ws_m)
        _c(ws_m, "Vim Mode", self._toggle_vim_mode, "Ctrl+Alt+V")
        _c(ws_m, "K-UI", self._toggle_kui, "Ctrl+Alt+N")
        _c(ws_m, "K-UI: Performance", self._show_kui_perf_picker)
        _c(ws_m, "Zen Player", self._toggle_zen_player, "Ctrl+Alt+P")
        _c(ws_m, "Zen Media Config...", self._open_zen_media_config)
        _s(ws_m)
        _c(ws_m, "Liberar memoria da aba", self._purge_active_tab_memory)
        _c(ws_m, "Limpar cache (GC)", self._global_gc)

        rm2 = _mk(xm, _t("menu.remote_collab", "Remoto & Colaboração"))
        _c(
            rm2,
            _t("menu.remote_ssh", "SSH Remoto..."),
            self._open_remote_ssh,
            "Ctrl+Shift+H",
        )
        _c(rm2, _t("menu.collab", "Colaboração..."), self._open_collab)
        _c(rm2, _t("menu.chat", "Chat ao Vivo..."), self._open_chat)

        _s(xm)
        _c(xm, _t("tools.zen_mode"), self._toggle_zen_mode, "Ctrl+K Z")
        _c(xm, _t("tools.focus_mode"), self._toggle_focus_mode)
        _c(xm, _t("tools.read_only"), self._toggle_read_only)

        stm = _mk(mb, _t("menu.settings", "Configurações"))
        _c(
            stm,
            _t("settings.open", "Abrir Configurações..."),
            self._open_settings_window,
            "Ctrl+,",
        )
        _c(
            stm,
            _t("menu.accessibility", "Acessibilidade..."),
            self._open_accessibility,
            "Ctrl+Shift+U",
        )
        _c(
            stm,
            _t("keyremap.menu_item", "Atalhos de Teclado..."),
            self._open_key_remapper,
        )
        _s(stm)

        rst_stm = _mk(stm, _t("settings.reset_menu", "Redefinir / Restaurar"))
        _c(
            rst_stm,
            _t("settings.reset_soft", "Soft Reset (apenas configurações)"),
            lambda: self._run_reset("soft"),
        )
        _c(
            rst_stm,
            _t("settings.reset_wizard", "Soft Reset + Forçar Setup Wizard"),
            lambda: self._run_reset("wizard"),
        )
        _c(
            rst_stm,
            _t("settings.reset_full", "Limpeza Completa"),
            lambda: self._run_reset("full"),
        )
        _s(rst_stm)
        _c(
            rst_stm,
            _t("settings.reset_by_key", "Redefinir Configuração Específica..."),
            self._reset_single_key,
        )
        _c(
            rst_stm,
            _t("settings.reset_session", "Limpar Sessão e Histórico"),
            self._reset_session_only,
        )

        _s(stm)
        adv_stm = _mk(stm, _t("settings.advanced_group", "Avançado"))
        _c(
            adv_stm,
            _t("settings.advanced", "Editar JSON Diretamente..."),
            lambda: self._on_setting_change(
                "_open_json",
                str(__import__("src.constants", fromlist=[
                    "CONFIG_FILE"]).CONFIG_FILE),
            ),
        )
        _c(
            adv_stm,
            _t("settings.open_cursor_editor", "Editar Cursor..."),
            self._open_settings_window_cursor,
        )

        hm = _mk(mb, _t("menu.help"))
        _c(hm, _t("help.welcome"), self._show_welcome)
        _c(
            hm,
            _t("help.docs"),
            lambda: webbrowser.open("https://github.com/KillovSky/Codis"),
        )
        _c(
            hm,
            _t("help.shortcuts_reference", "Referência de Atalhos"),
            self._keybindings_dialog,
        )
        _s(hm)
        _c(
            hm,
            _t("help.check_updates", "Verificar Atualizações..."),
            self._check_for_updates,
        )
        _s(hm)
        _c(hm, "About Codis", self._about_dialog)

    def _rebuild_theme_menu(self) -> None:
        """Reconstrói o submenu de temas no menu Exibir.

        após a construção inicial do menu).
        """
        themes_menu: tk.Menu | None = getattr(self, "_themes_menu", None)
        if themes_menu is None:
            return
        try:
            t = self._tmgr.theme
            themes_menu.delete(0, "end")
            themes_menu.add_command(
                label=_t("theme.open_selector", "Abrir Seletor de Temas..."),
                command=self._open_theme_panel,
                background=t.floating_bg,
                foreground=t.fg,
                activebackground=t.sel_bg,
                activeforeground=t.sel_fg,
            )
            themes_menu.add_separator()
            for name in THEME_ORDER:
                themes_menu.add_command(
                    label=name,
                    command=lambda n=name: self._apply_theme_from_menu(n),
                    background=t.floating_bg,
                    foreground=t.fg,
                    activebackground=t.sel_bg,
                    activeforeground=t.sel_fg,
                )
        except tk.TclError as exc:
            logger.debug("_rebuild_theme_menu: %s", exc)

    def _init_cursor_manager(self) -> None:
        """Inicializa o gerenciador de cursor customizável."""
        try:
            from src.ui.cursor_manager import CursorManager

            self._cursor_mgr = CursorManager(self._root, self._tmgr, self._cfg)
        except Exception as exc:
            import logging

            logging.getLogger("Codis").debug("CursorManager init: %s", exc)
            self._cursor_mgr = None

    def _bind_shortcuts(self) -> None:
        """Registra todos os atalhos de teclado globais da aplicação."""
        b = self._root.bind
        b("<Control-n>", lambda _: self.new_file())
        b("<Control-o>", lambda _: self.open_file_dialog())
        b("<Control-s>", lambda _: self.save_file())
        b("<Control-S>", lambda _: self.save_as())
        b("<Control-w>", lambda _: self._close_active_tab())
        b("<Control-z>", lambda _: self._undo())
        b("<Control-y>", lambda _: self._redo())
        b("<Control-Z>", lambda _: self._redo())
        b("<Control-a>", lambda _: self._select_all())

        b("<Control-c>", self._clipboard_copy)
        b("<Control-x>", self._clipboard_cut)
        b("<Control-v>", self._clipboard_paste)
        b("<Control-slash>", lambda _: self._toggle_comment())
        b("<Control-K>", lambda _: self._delete_line())
        b("<Alt-Up>", lambda _: self._move_line_up())
        b("<Alt-Down>", lambda _: self._move_line_down())
        b("<Control-f>", lambda _: self._show_find())
        b("<Control-h>", lambda _: self._show_replace())
        b("<Control-F>", lambda _: self._show_find_files())
        b("<F3>", lambda _: self._search_bar.next_match())
        b("<Shift-F3>", lambda _: self._search_bar.prev_match())
        b("<Escape>", self._on_escape)
        b("<Control-P>", lambda _: self._command_palette())
        b("<Control-p>", lambda _: self._quick_open())
        b("<Control-g>", lambda _: self._goto_line())
        b("<Control-grave>", lambda _: self._show_panel("terminal"))
        b("<Control-M>", lambda _: self._show_panel("problems"))
        b("<F11>", lambda _: self._toggle_fullscreen())
        b("<Control-Tab>", lambda _: self._next_tab())
        b("<Control-Shift-Tab>", lambda _: self._prev_tab())
        b("<Control-Next>", lambda _: self._next_tab())
        b("<Control-Prior>", lambda _: self._prev_tab())
        b("<Control-Shift-T>", lambda _: self._reopen_closed_tab())
        b("<Control-Alt-Key-p>", lambda _: self._toggle_zen_player())
        for _n in range(1, 10):
            b(f"<Control-Key-{_n}>", lambda _e, n=_n: self._goto_tab_number(n))
        b("<F5>", lambda _: self._run_file())
        b("<Control-Alt-y>", lambda _: self._open_web_preview())
        b("<Shift-Alt-f>", lambda _: self._format_document())
        b("<F8>", lambda _: self._next_problem())
        b("<Shift-F8>", lambda _: self._prev_problem())
        b("<Control-equal>", lambda _: self._zoom(+1))
        b("<Control-H>", lambda _: self._open_remote_ssh())
        b("<Control-U>", lambda _: self._open_accessibility())
        b("<Control-minus>", lambda _: self._zoom(-1))
        b("<Control-0>", lambda _: self._zoom(0))
        b("<Control-Alt-equal>", lambda _: self._ui_scale_step(+1))
        b("<Control-Alt-plus>", lambda _: self._ui_scale_step(+1))
        b("<Control-Alt-minus>", lambda _: self._ui_scale_step(-1))
        b("<Control-Alt-Key-0>", lambda _: self._ui_scale_auto())
        b("<F7>", lambda _: self._next_hunk())
        b("<Shift-F7>", lambda _: self._prev_hunk())
        b("<Control-comma>", lambda _: self._activity("settings"))
        b("<Control-Shift-C>", lambda _: self._open_color_picker())
        b("<Control-Shift-R>", lambda _: self._show_panel("regex"))
        b("<Control-Shift-Q>", lambda _: self._show_panel("sql"))
        b("<Control-Shift-A>", lambda _: self._activity("ia"))
        b("<Control-Shift-N>", lambda _: self._activity("scratchpad"))
        b("<Control-Shift-J>", lambda _: self._activity("snippets"))
        b("<Control-F2>", lambda _: self._add_bookmark())
        b("<F2>", lambda _: self._next_bookmark())
        b("<Shift-F2>", lambda _: self._prev_bookmark())
        b("<Insert>", lambda _: self._toggle_overwrite_mode())
        b("<Control-Shift-V>", lambda _: self._paste_history_dialog())
        b("<Control-Shift-W>", lambda _: self._open_workspace_switcher())
        b("<Control-Shift-M>", lambda _: self._open_macro_toolbar())
        b("<Control-Alt-m>", lambda _: self._toggle_multi_cursor())
        b("<Control-Alt-M>", lambda _: self._open_process_manager())
        b("<Control-backslash>", lambda _: self._toggle_dual_focus())
