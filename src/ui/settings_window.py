"""Janela de configurações do Codis.

Implementa ``SettingsWindow``, a janela principal de preferências do
usuário, organizada em categorias (editor, aparência, atalhos, IA, etc.)
com aplicação em tempo real das mudanças.
"""

from __future__ import annotations

import contextlib
import logging
import tkinter as tk
from collections.abc import Callable
from tkinter import ttk
from typing import Any

from src.config import ConfigManager
from src.constants import CONFIG_FILE, FONT_MONO, FONT_UI
from src.i18n import _t
from src.theme.base import Theme
from src.theme.manager import ThemeManager
from src.ui._perf_utils import debounce_canvas
from src.ui.dropdown import DropDown
from src.ui.settings_editor import SettingsEditor

logger = logging.getLogger("Codis")


class SettingsWindow(tk.Toplevel):
    """Janela flutuante de configurações do Codis.

    Layout: sidebar de categorias à esquerda + painel de conteúdo scrollável
    à direita. Suporta busca em tempo real e salva imediatamente ao alterar.

    Args:
        parent: Janela pai Tkinter.
        tmgr: Instância de ThemeManager.
        cfg: Instância de ConfigManager.
        on_change: Callback(key, value) acionado a cada alteração.
        on_show_settings_panel: Callback para abrir o modo avançado inline.
    """

    _CATEGORIES_DEF: list = [
        ("editor", "✏", "setting.cat_editor"),
        ("advanced_editor", "-", "setting.cat_advanced_editor"),
        ("autocomplete", "💬", "setting.cat_autocomplete"),
        ("appearance", "A", "setting.cat_appearance"),
        ("cursor", "🖱", "setting.cat_cursor"),
        ("layout", "🖥", "setting.cat_ui"),
        ("save", "💾", "setting.cat_save"),
        ("lint", "🔍", "setting.cat_lint"),
        ("lsp", "🔤", "setting.cat_lsp"),
        ("git", "🌿", "setting.cat_git"),
        ("terminal", "💻", "setting.cat_terminal"),
        ("filetree", "📁", "setting.cat_filetree"),
        ("render", "🌐", "setting.cat_render"),
        ("timeline", "🕒", "setting.cat_timeline"),
        ("flowchart", "🔀", "setting.cat_flowchart"),
        ("pomodoro", "⏱", "setting.cat_pomodoro"),
        ("perf", "P", "setting.cat_perf"),
        ("security", "🔒", "setting.cat_security"),
        ("advanced", "⚙", "setting.cat_advanced"),
        ("accessibility", "♿", "setting.cat_accessibility"),
        ("ssh", "🖧", "setting.cat_ssh"),
        ("collab", "👥", "setting.cat_collab"),
        ("plugins_cfg", "🔌", "setting.cat_plugins_cfg"),
        ("custom_syntax", "🎨", "setting.cat_custom_syntax"),
    ]

    _CAT_KEYS: dict[str, list[str]] = {
        "editor": [
            "font_family",
            "font_size",
            "font_ligatures",
            "ligature_mode",
            "tab_size",
            "use_spaces",
            "word_wrap",
            "cursor_style",
            "cursor_blinking",
            "smooth_scroll",
            "scroll_speed",
            "multi_cursor",
            "multicursor_lines",
            "render_whitespace",
            "indent_guides",
            "bracket_matching",
            "highlight_current_line",
            "line_numbers",
            "relative_numbers",
            "breadcrumbs",
            "minimap",
            "encoding",
            "eol",
        ],
        "advanced_editor": [
            "sticky_scroll",
            "code_folding",
            "inline_blame",
            "inline_blame_mode",
            "color_preview",
            "inlay_hints",
            "smart_tips",
            "smart_tips_interval",
            "smart_tips_large_file_lines",
            "smart_tips_huge_file_lines",
            "smart_tips_big_function_lines",
            "smart_tips_long_line_chars",
            "smart_tips_deep_nesting",
            "smart_tips_todo_threshold",
            "smart_tips_generic_var_threshold",
        ],
        "autocomplete": [
            "autocomplete",
            "autocomplete_delay",
            "snippets",
            "parameter_hints",
            "hover_docs",
        ],
        "cursor": [],
        "appearance": [
            "theme",
            "language",
            "ui_font_family",
            "ui_scale",
            "ui_font_size",
            "activity_bar",
            "show_welcome",
        ],
        "layout": [
            "sidebar_width",
            "panel_height",
            "minimap_width",
            "minimap_scale",
            "window_width",
            "window_height",
            "max_recent",
        ],
        "save": [
            "autosave",
            "autosave_delay",
            "format_on_save",
            "trim_trailing_whitespace",
            "insert_final_newline",
            "session_restore",
            "exit_save_mode",
            "session_save_on_exit",
            "confirm_delete",
            "trash_backend",
            "trash_retention_days",
            "trash_max_mb",
        ],
        "lint": [
            "lint_enabled",
            "lint_python",
            "lint_js",
            "lint_html",
            "lint_delay",
            "eslint_path",
            "pylint_path",
            "flake8_path",
            "mypy_path",
            "prettier_path",
        ],
        "lsp": [
            "lsp_enabled",
            "lsp_hover_enabled",
            "lsp_format_enabled",
            "lsp_diagnostics",
        ],
        "git": [
            "git_enabled",
            "git_decorations",
            "git_auto_fetch",
            "git_fetch_interval",
            "updater_url",
        ],
        "terminal": [
            "default_shell",
            "terminal_font_size",
            "terminal_max_lines",
        ],
        "filetree": [
            "show_hidden_files",
            "respect_ignore_files",
        ],
        "render": [
            "render_internal",
        ],
        "timeline": [
            "timeline_max_backups",
            "timeline_max_age_days",
        ],
        "flowchart": [
            "flowchart_auto",
            "flowchart_max_depth",
        ],
        "pomodoro": [
            "pomodoro_work_min",
            "pomodoro_break_min",
            "pomodoro_long_break_min",
        ],
        "perf": [
            "kui_perf",
            "hl_idle_ms",
            "perf_auto_mode",
            "perf_freeze_threshold_ms",
            "perf_mode",
            "perf_native_bindings",
            "perf_kui_fallback",
            "perf_keep_minimap",
            "perf_keep_sticky_scroll",
            "perf_keep_heatmap",
            "perf_keep_rhythm",
            "perf_keep_lint",
            "perf_keep_kui_anim",
            "autocomplete_min_chars",
            "autocomplete_max_items",
            "large_file_mb",
            "search_max_results",
        ],
        "security": [
            "workspace_trust",
        ],
        "advanced": [
            "font_min",
            "font_max",
        ],
        "accessibility": [
            "a11y_high_contrast",
            "a11y_colorblind_mode",
            "a11y_screen_reader",
            "a11y_keyboard_nav",
            "a11y_large_ui_font",
            "a11y_reduce_motion",
        ],
        "ssh": [
            "ssh_default_port",
            "ssh_connect_timeout",
            "ssh_keepalive_interval",
            "ssh_agent_forwarding",
            "ssh_strict_host_key",
            "ssh_sftp_buffer_kb",
            "ssh_open_in_editor",
            "debug_dap_enabled",
            "debug_dap_port",
        ],
        "collab": [
            "collab_enabled",
            "collab_port",
            "collab_max_peers",
            "collab_show_cursors",
            "collab_show_chat",
            "collab_autosave_sync",
        ],
        "plugins_cfg": [
            "plugin_isolation",
            "plugin_timeout_s",
            "plugin_heartbeat_s",
            "plugin_max_restarts",
            "plugin_sandbox_network",
        ],
    }

    @property
    def _categories(self):
        """Retorna categorias com nomes traduzidos.

        Returns:
            list: Lista de (id, icone, nome_traduzido).
        """
        return [
            (cid, icon, _t(key, cid.replace("_", " ").title()))
            for cid, icon, key in self._CATEGORIES_DEF
        ]

    def __init__(
        self,
        parent: tk.Tk,
        tmgr: ThemeManager,
        cfg: ConfigManager,
        on_change: Callable | None = None,
        on_show_settings_panel: Callable | None = None,
    ) -> None:
        """Cria a janela de configurações do Codis.

        Args:
            parent: Janela pai Tkinter.
            tmgr: Instância de ThemeManager.
            cfg: Instância de ConfigManager.
            on_change: Callback(key, value) acionado a cada alteração.
            on_show_settings_panel: Callback para abrir o modo avançado inline.
        """
        t = tmgr.theme
        super().__init__(parent)
        self.title(_t("hc.settings_window.settings", "Configurações - Codis"))
        self.geometry("980x680")
        self.minsize(640, 480)
        self.config(bg=t.floating_bg)
        self.transient(parent)
        self.focus_set()
        self.resizable(True, True)
        try:
            self.wm_attributes("-zoomed", False)
        except Exception:
            logger.debug(
                "wm_attributes('-zoomed') não suportado nesta plataforma.",
                exc_info=True,
            )

        self._tmgr = tmgr
        self._cfg = cfg
        self._on_change = on_change
        self._on_show_settings_panel = on_show_settings_panel
        self._schema_map: dict[str, tuple] = {}
        self._current_cat = "editor"
        self._cat_frame_cache: dict[str, tk.Frame] = {}
        self._search_after_id: Any = None
        self._search_trace_name: str | None = None
        self._destroyed: bool = False

        for schema in SettingsEditor._get_schema():
            key = schema[0]
            if not key.startswith("__"):
                self._schema_map[key] = schema

        self._build()
        self.bind("<Escape>", lambda _: self.destroy())
        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = 980, 680
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")
        self.update_idletasks()
        self._show_category("editor")

    def destroy(self) -> None:
        """Override: cancela afters e remove traces antes de destruir."""
        if self._destroyed:
            return
        self._destroyed = True
        if self._search_after_id:
            with contextlib.suppress(Exception):
                self.after_cancel(self._search_after_id)
            self._search_after_id = None
        if self._search_trace_name is not None:
            with contextlib.suppress(Exception):
                self._search_var.trace_remove("write", self._search_trace_name)
            self._search_trace_name = None
        with contextlib.suppress(Exception):
            super().destroy()

    def _build(self) -> None:
        """Constrói todos os widgets da janela de configurações."""
        t = self._tmgr.theme

        titlebar = tk.Frame(self, bg=t.activity_bg, height=52)
        titlebar.pack(fill="x")
        titlebar.pack_propagate(False)
        tk.Label(
            titlebar,
            text=_t("hc.settings_window.settings", "Configurações"),
            bg=t.activity_bg,
            fg=t.fg,
            font=(FONT_UI, 15, "bold"),
        ).pack(side="left", padx=16, pady=10)

        search_frame = tk.Frame(titlebar, bg=t.activity_bg)
        search_frame.pack(side="right", padx=16, pady=10)
        tk.Label(
            search_frame, text="🔍", bg=t.activity_bg, fg=t.fg_muted, font=(FONT_UI, 10)
        ).pack(side="left")
        self._search_var = tk.StringVar()
        self._search_after_id: Any = None

        def _debounced_search(*_: Any) -> None:
            """Agenda busca com 300ms de debounce para evitar lag ao digitar."""
            if self._destroyed:
                return
            if self._search_after_id:
                with contextlib.suppress(Exception):
                    self.after_cancel(self._search_after_id)
            with contextlib.suppress(tk.TclError):
                self._search_after_id = self.after(300, self._on_search)

        self._search_trace_name = self._search_var.trace_add(
            "write", _debounced_search)
        search_ent = tk.Entry(
            search_frame,
            textvariable=self._search_var,
            bg=t.input_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            bd=0,
            font=(FONT_UI, 10),
            highlightthickness=1,
            highlightbackground=t.border,
            width=28,
        )
        search_ent.pack(side="left", padx=6)

        def _esc_search(_):
            self._search_var.set("")
            return "break"

        search_ent.bind("<Escape>", _esc_search)
        self.bind(
            "<Control-f>",
            lambda _: (search_ent.focus_set(),
                       search_ent.select_range(0, "end")),
        )

        tk.Frame(self, bg=t.border, height=1).pack(fill="x")

        body = tk.Frame(self, bg=t.floating_bg)
        body.pack(fill="both", expand=True)

        sidebar_outer = tk.Frame(body, bg=t.activity_bg, width=220)
        sidebar_outer.pack(side="left", fill="y")
        sidebar_outer.pack_propagate(False)
        tk.Frame(body, bg=t.border, width=1).pack(side="left", fill="y")

        _sb_canvas = tk.Canvas(
            sidebar_outer, bg=t.activity_bg, highlightthickness=0, width=218
        )
        _sb_vsb = ttk.Scrollbar(
            sidebar_outer,
            orient="vertical",
            command=_sb_canvas.yview,
            style="Thin.Vertical.TScrollbar",
        )
        _sb_vsb.pack(side="right", fill="y")
        _sb_canvas.config(yscrollcommand=_sb_vsb.set)
        _sb_canvas.pack(side="left", fill="both", expand=True)

        _sb_inner = tk.Frame(_sb_canvas, bg=t.activity_bg)
        _sb_win = _sb_canvas.create_window(
            (0, 0), window=_sb_inner, anchor="nw")

        debounce_canvas(_sb_canvas, _sb_win, ms=40, extra_widget=_sb_inner)

        def _sb_wheel(e):
            speed = self._cfg.get("scroll_speed", 3)
            steps = (
                -1 * (e.delta // 120) * speed
                if e.delta
                else (speed if e.num == 5 else -speed)
            )
            _sb_canvas.yview_scroll(int(steps), "units")

        _sb_canvas.bind("<MouseWheel>", _sb_wheel)
        _sb_canvas.bind("<Button-4>", _sb_wheel)
        _sb_canvas.bind("<Button-5>", _sb_wheel)
        _sb_inner.bind("<MouseWheel>", _sb_wheel)
        _sb_inner.bind("<Button-4>", _sb_wheel)
        _sb_inner.bind("<Button-5>", _sb_wheel)

        def _bind_sb_children_scroll(w=_sb_inner):
            """Binda scroll em todos os filhos do sidebar para que o hover funcione."""
            for child in w.winfo_children():
                try:
                    child.bind("<MouseWheel>", _sb_wheel, add=True)
                    child.bind("<Button-4>", _sb_wheel, add=True)
                    child.bind("<Button-5>", _sb_wheel, add=True)
                    _bind_sb_children_scroll(child)
                except Exception:
                    logger.debug(
                        "Falha ao vincular scroll a widget filho.", exc_info=True
                    )

        self._bind_sb_children_scroll = _bind_sb_children_scroll

        self._sidebar = _sb_inner

        content_wrap = tk.Frame(body, bg=t.floating_bg)
        content_wrap.pack(side="left", fill="both", expand=True)

        canvas = tk.Canvas(content_wrap, bg=t.floating_bg,
                           highlightthickness=0)
        vsb = ttk.Scrollbar(
            content_wrap,
            orient="vertical",
            command=canvas.yview,
            style="Thin.Vertical.TScrollbar",
        )
        vsb.pack(side="right", fill="y")
        canvas.config(yscrollcommand=vsb.set)
        canvas.pack(fill="both", expand=True)

        self._content_outer = tk.Frame(canvas, bg=t.floating_bg)
        self._canvas_window = canvas.create_window(
            (0, 0), window=self._content_outer, anchor="nw"
        )

        debounce_canvas(
            canvas, self._canvas_window, ms=40, extra_widget=self._content_outer
        )
        self._canvas = canvas

        def _on_wheel(e: Any) -> None:
            speed = self._cfg.get("scroll_speed", 3)
            if e.delta:
                steps = -1 * (e.delta // 120) * speed
            else:
                steps = speed if e.num == 5 else -speed
            canvas.yview_scroll(int(steps), "units")

        def _bind_scroll(widget: tk.Widget) -> None:
            with contextlib.suppress(tk.TclError):
                widget.bind("<MouseWheel>", _on_wheel, add=True)
                widget.bind("<Button-4>", _on_wheel, add=True)
                widget.bind("<Button-5>", _on_wheel, add=True)
            for child in widget.winfo_children():
                _bind_scroll(child)

        canvas.bind("<MouseWheel>", _on_wheel)
        canvas.bind("<Button-4>", _on_wheel)
        canvas.bind("<Button-5>", _on_wheel)
        self._content_outer.bind("<MouseWheel>", _on_wheel)
        self._content_outer.bind(
            "<Map>", lambda _: _bind_scroll(self._content_outer), add=True
        )
        self._bind_scroll_fn = _bind_scroll

        self.bind("<Alt-Up>", lambda _: self._nav_category(-1))
        self.bind("<Alt-Down>", lambda _: self._nav_category(+1))

        tk.Frame(self, bg=t.border, height=1).pack(fill="x")
        footer = tk.Frame(self, bg=t.activity_bg, height=44)
        footer.pack(fill="x")
        footer.pack_propagate(False)

        edit_json_btn = tk.Label(
            footer,
            text=_t("hc.settings_window.edit_settings_json",
                    "Editar settings.json"),
            bg=t.activity_bg,
            fg=t.t_link,
            font=(FONT_UI, 9),
            cursor="hand2",
        )
        edit_json_btn.pack(side="left", padx=8, pady=8)
        edit_json_btn.bind("<Button-1>", lambda _: self._open_json())

        classic_btn = tk.Label(
            footer,
            text=_t("hc.settings_window.advanced_view", "Modo Avançado"),
            bg=t.activity_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
            cursor="hand2",
        )
        classic_btn.pack(side="left", padx=4, pady=8)
        classic_btn.bind(
            "<Button-1>",
            lambda _: (
                self.destroy(),
                (
                    self._on_show_settings_panel()
                    if self._on_show_settings_panel
                    else None
                ),
            ),
        )

        reset_all_btn = tk.Label(
            footer,
            text=_t("hc.settings_window.reset_all", "Restaurar Padrões"),
            bg=t.activity_bg,
            fg=t.warn_fg if hasattr(t, "warn_fg") else t.fg_muted,
            font=(FONT_UI, 9),
            cursor="hand2",
        )
        reset_all_btn.pack(side="left", padx=4, pady=8)
        reset_all_btn.bind("<Button-1>", lambda _: self._reset_all())

        self._count_lbl = tk.Label(
            footer,
            text="",
            bg=t.activity_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        )
        self._count_lbl.pack(side="left", padx=12)

        close_btn = tk.Label(
            footer,
            text=_t("hc.settings_window.close", "Fechar"),
            bg=t.btn_primary,
            fg="#000",
            font=(FONT_UI, 9, "bold"),
            cursor="hand2",
            padx=10,
            pady=4,
        )
        close_btn.pack(side="right", padx=12, pady=8)
        close_btn.bind("<Button-1>", lambda _: self.destroy())

        self._cat_btns: dict[str, tk.Frame] = {}

        _CAT_GROUPS = [
            ("Editor", ["editor", "advanced_editor", "autocomplete"]),
            ("Interface", ["appearance", "cursor", "layout"]),
            ("Arquivos", ["save", "filetree", "timeline"]),
            ("Análise", ["lint", "lsp"]),
            ("Ferramentas", ["git", "terminal",
             "render", "flowchart", "pomodoro"]),
            ("Sistema", ["perf", "security", "advanced", "accessibility"]),
            ("Rede / Remoto", ["ssh", "collab", "plugins_cfg"]),
        ]
        _cat_to_group = {}
        for grp, ids in _CAT_GROUPS:
            for cid in ids:
                _cat_to_group[cid] = grp

        current_group = None
        for cat_id, icon, name in self._categories:
            grp = _cat_to_group.get(cat_id, "")
            if grp and grp != current_group:
                current_group = grp
                tk.Frame(self._sidebar, bg=t.border, height=1).pack(
                    fill="x", padx=8, pady=(6, 0)
                )
                tk.Label(
                    self._sidebar,
                    text=f"  {grp.upper()}",
                    bg=t.activity_bg,
                    fg=t.fg_muted,
                    font=(FONT_UI, 7, "bold"),
                    anchor="w",
                ).pack(fill="x", padx=4, pady=(2, 0))

            inner = tk.Frame(self._sidebar, bg=t.activity_bg, cursor="hand2")
            inner.pack(fill="x", padx=4, pady=0)

            indicator = tk.Frame(inner, bg=t.activity_bg, width=3)
            indicator.pack(side="left", fill="y", pady=4)

            icon_lbl = tk.Label(
                inner,
                text=f" {icon} ",
                bg=t.activity_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 9),
                width=3,
                anchor="center",
            )
            icon_lbl.pack(side="left", pady=4)

            name_lbl = tk.Label(
                inner,
                text=name,
                bg=t.activity_bg,
                fg=t.fg,
                font=(FONT_UI, 9),
                anchor="w",
            )
            name_lbl.pack(side="left", fill="x", expand=True, padx=4, pady=4)

            def _enter(_e, f=inner, ic=icon_lbl, nl=name_lbl, ind=indicator):
                hover_bg = getattr(t, "btn_hover", t.sel_bg)
                for w2 in (f, ic, nl, ind):
                    with contextlib.suppress(Exception):
                        w2.config(bg=hover_bg)

            def _leave(
                _e, cid=cat_id, f=inner, ic=icon_lbl, nl=name_lbl, ind=indicator
            ):
                is_sel = self._current_cat == cid
                bg2 = t.sel_bg if is_sel else t.activity_bg
                acc = getattr(
                    t, "accent", t.sel_bg) if is_sel else t.activity_bg
                try:
                    f.config(bg=bg2)
                    ic.config(bg=bg2)
                    nl.config(bg=bg2)
                    ind.config(bg=acc)
                except Exception:
                    logger.debug(
                        "Falha ao aplicar cor de hover na categoria.", exc_info=True
                    )

            for w in (inner, indicator, icon_lbl, name_lbl):
                w.bind("<Button-1>", lambda _,
                       cid=cat_id: self._show_category(cid))
                w.bind("<Enter>", _enter)
                w.bind("<Leave>", _leave)

            self._cat_btns[cat_id] = inner

        if hasattr(self, "_bind_sb_children_scroll"):

            def _safe_bind_scroll():
                with contextlib.suppress(tk.TclError):
                    if self.winfo_exists():
                        self._bind_sb_children_scroll()

            self.after(50, _safe_bind_scroll)

    def _nav_category(self, direction: int) -> None:
        """Navega entre categorias com Alt+Up/Down."""
        cats = [c[0] for c in self._CATEGORIES_DEF]
        try:
            idx = cats.index(self._current_cat)
        except ValueError:
            idx = 0
        new_idx = max(0, min(len(cats) - 1, idx + direction))
        self._show_category(cats[new_idx])

    def _show_category(self, cat_id: str) -> None:
        """Exibe as configurações de uma categoria - usa cache para evitar rebuild.

        Args:
            cat_id: Identificador da categoria a exibir.
        """
        if self._destroyed or not self.winfo_exists():
            return
        t = self._tmgr.theme
        self._current_cat = cat_id

        accent = getattr(t, "accent", t.sel_bg)
        for cid, btn in self._cat_btns.items():
            is_sel = cid == cat_id
            bg = t.sel_bg if is_sel else t.activity_bg
            try:
                if not btn.winfo_exists():
                    continue
                btn.config(bg=bg)
                children = btn.winfo_children()
                for i, child in enumerate(children):
                    if i == 0:
                        child.config(bg=accent if is_sel else t.activity_bg)
                    else:
                        try:
                            child.config(bg=bg)
                        except tk.TclError:
                            continue
            except tk.TclError:
                continue

        for cached_frame in self._cat_frame_cache.values():
            with contextlib.suppress(tk.TclError):
                cached_frame.pack_forget()

        if cat_id in self._cat_frame_cache:
            frame = self._cat_frame_cache[cat_id]
            with contextlib.suppress(tk.TclError):
                if not frame.winfo_exists():
                    del self._cat_frame_cache[cat_id]
                else:
                    frame.pack(fill="both", expand=True)
                    self._update_canvas_scroll()
                    keys = self._CAT_KEYS.get(cat_id, [])
                    valid = sum(1 for k in keys if k in self._schema_map)
                    with contextlib.suppress(tk.TclError):
                        self._count_lbl.config(
                            text=f"{valid} configuração(ões)")
                    return

        frame = tk.Frame(self._content_outer, bg=t.floating_bg)
        self._cat_frame_cache[cat_id] = frame
        frame.pack(fill="both", expand=True)

        if cat_id == "custom_syntax":
            from src.ui.custom_keywords_panel import CustomKeywordsPanel

            CustomKeywordsPanel(
                frame, self._cfg, self._tmgr, on_change=self._on_change
            ).pack(fill="both", expand=True)
            return

        if cat_id == "cursor":
            from src.ui.cursor_manager import CursorPickerPanel

            app = getattr(self, "_app_ref", None)
            cursor_mgr = getattr(app, "_cursor_mgr", None) if app else None
            if cursor_mgr is None:
                tk.Label(
                    frame,
                    text="  🖱  Cursor Manager não disponível nesta janela.",
                    bg=t.floating_bg,
                    fg=t.fg_muted,
                    font=(FONT_UI, 10),
                    pady=20,
                    anchor="w",
                ).pack(fill="x", padx=20)
            else:
                CursorPickerPanel(frame, cursor_mgr, self._tmgr).pack(
                    fill="both", expand=True
                )
            return

        if cat_id == "git":
            self._build_git_panel(frame, t)
            return

        keys = self._CAT_KEYS.get(cat_id, [])
        icon_name = next(
            ((ic, nm) for cid, ic, nm in self._categories if cid == cat_id),
            ("⚙", cat_id),
        )

        hdr = tk.Frame(frame, bg=t.floating_bg)
        hdr.pack(fill="x", padx=20, pady=(16, 4))
        tk.Label(
            hdr,
            text=f"{icon_name[0]}  {icon_name[1]}",
            bg=t.floating_bg,
            fg=t.fg,
            font=(FONT_UI, 14, "bold"),
        ).pack(anchor="w")

        valid_keys = [k for k in keys if k in self._schema_map]
        tk.Label(
            hdr,
            text=_t("setting.cat_items",
                    "{n} configurações", n=len(valid_keys)),
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        ).pack(anchor="w")
        tk.Frame(frame, bg=t.border, height=1).pack(
            fill="x", padx=20, pady=(0, 8))

        for key in keys:
            schema = self._schema_map.get(key)
            if schema is None:
                continue
            skey, label, desc, kind, default, opts = (
                schema[0],
                schema[1],
                schema[2],
                schema[3],
                schema[4],
                schema[5],
            )
            type_hint = schema[6] if len(schema) > 6 else None
            example = schema[7] if len(schema) > 7 else None
            lk, dk = f"setting.{skey}", f"setting.desc.{skey}"
            label = _t(lk) if _t(lk) != lk else label
            desc = _t(dk) if _t(dk) != dk else desc
            self._build_setting_row(
                skey,
                label,
                desc,
                kind,
                default,
                opts,
                type_hint,
                example,
                t,
                parent=frame,
            )

        with contextlib.suppress(tk.TclError):
            self._bind_scroll_fn(frame)

        self._update_canvas_scroll()
        with contextlib.suppress(tk.TclError):
            self._count_lbl.config(text=f"{len(valid_keys)} configuração(ões)")

    def _update_canvas_scroll(self) -> None:
        """Atualiza o scrollregion e volta ao topo."""
        if self._destroyed:
            return
        with contextlib.suppress(tk.TclError):
            self._content_outer.update_idletasks()
            self._canvas.config(scrollregion=self._canvas.bbox("all"))
            self._canvas.itemconfig(
                self._canvas_window, width=self._canvas.winfo_width()
            )
            self._canvas.yview_moveto(0)

    def _build_setting_row(
        self,
        key: str,
        label: str,
        desc: str,
        kind: str,
        default: Any,
        opts: Any,
        type_hint: str | None,
        example: str | None,
        t: Theme,
        parent: tk.Widget | None = None,
    ) -> None:
        """Constrói uma linha de configuração individual no painel de conteúdo.

        Args:
            key: Chave de configuração.
            label: Rótulo traduzido.
            desc: Descrição traduzida.
            kind: Tipo de controle ('toggle', 'slider', 'combo', 'entry').
            default: Valor padrão.
            opts: Opções do controle (lista ou tupla).
            type_hint: Texto descritivo do tipo de valor.
            example: Exemplo de valor.
            t: Tema visual ativo.
            parent: Frame pai (usa ``self._content_outer`` se None).
        """
        container = parent if parent is not None else self._content_outer
        row = tk.Frame(container, bg=t.floating_bg)
        row.pack(fill="x", padx=20, pady=6)

        right = tk.Frame(row, bg=t.floating_bg, width=240)
        right.pack(side="right", fill="y")
        right.pack_propagate(False)
        left = tk.Frame(row, bg=t.floating_bg)
        left.pack(side="left", fill="x", expand=True)

        tk.Label(
            left,
            text=label,
            bg=t.floating_bg,
            fg=t.fg,
            font=(FONT_UI, 10, "bold"),
            anchor="w",
        ).pack(anchor="w")
        if desc:
            tk.Label(
                left,
                text=desc,
                bg=t.floating_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 8),
                anchor="w",
                wraplength=400,
            ).pack(anchor="w")

        meta: list[str] = []
        if type_hint:
            meta.append(f"Type: {type_hint}")
        if example is not None:
            meta.append(f"Ex: {example}")
        if default is not None:
            meta.append(f"Default: {default}")
        if meta:
            tk.Label(
                left,
                text="  " + "  |  ".join(meta),
                bg=t.floating_bg,
                fg=t.t_comment,
                font=(FONT_UI, 7),
                anchor="w",
            ).pack(anchor="w")

        cur_val = self._cfg.get(key, default)

        if kind == "toggle":
            var = tk.BooleanVar(value=bool(cur_val))
            sw_f = tk.Frame(right, bg=t.floating_bg)
            sw_f.pack(anchor="e", pady=4)
            state_lbl = tk.Label(
                sw_f,
                text=_t("ui.on", "ON") if var.get() else _t("ui.off", "OFF"),
                bg=t.floating_bg,
                fg=t.accent if var.get() else t.fg_muted,
                font=(FONT_UI, 8, "bold"),
            )
            state_lbl.pack(side="left", padx=4)
            toggle_bg = t.accent if var.get() else t.border
            toggle = tk.Label(
                sw_f,
                text="● " if var.get() else "○ ",
                bg=toggle_bg,
                fg="#fff" if var.get() else t.fg_muted,
                font=(FONT_UI, 10),
                cursor="hand2",
                relief="flat",
                padx=4,
                width=3,
            )
            toggle.pack(side="left")

            def _toggle(k=key, v=var, s=state_lbl, tb=toggle):
                """Alterna o valor do toggle e atualiza o visual."""
                v.set(not v.get())
                val = v.get()
                s.config(
                    text=_t("ui.on", "ON") if val else _t("ui.off", "OFF"),
                    fg=t.accent if val else t.fg_muted,
                )
                tb.config(
                    text="● " if val else "○ ",
                    bg=t.accent if val else t.border,
                    fg="#fff" if val else t.fg_muted,
                )
                self._save(k, val)

            toggle.bind("<Button-1>", lambda _: _toggle())
            state_lbl.bind("<Button-1>", lambda _: _toggle())

        elif kind == "slider":
            lo, hi = opts
            var = tk.IntVar(value=int(cur_val))
            sf = tk.Frame(right, bg=t.floating_bg)
            sf.pack(anchor="e", pady=4)
            val_lbl = tk.Label(
                sf,
                text=str(int(cur_val)),
                bg=t.floating_bg,
                fg=t.fg,
                font=(FONT_MONO, 9),
                width=6,
            )
            val_lbl.pack(side="right")
            ttk.Scale(
                sf,
                from_=lo,
                to=hi,
                orient="horizontal",
                variable=var,
                length=160,
                command=lambda v, k=key, lbl=val_lbl: (
                    lbl.config(text=str(int(float(v)))),
                    self._save(k, int(float(v))),
                ),
            ).pack(side="left")

        elif kind == "combo":
            var = tk.StringVar(value=str(cur_val))
            cb = DropDown(
                right,
                textvariable=var,
                values=opts,
                width=24,
                font=(FONT_UI, 9),
                state="readonly",
            )
            cb.pack(anchor="e", pady=4)
            cb.bind(
                "<<ComboboxSelected>>", lambda _, k=key, v=var: self._save(
                    k, v.get())
            )

        elif kind == "entry":
            var = tk.StringVar(value=str(cur_val))
            ent = tk.Entry(
                right,
                textvariable=var,
                bg=t.input_bg,
                fg=t.fg,
                insertbackground=t.cursor,
                relief="flat",
                bd=0,
                font=(FONT_UI, 9),
                highlightthickness=1,
                highlightbackground=t.border,
                width=26,
            )
            ent.pack(anchor="e", pady=4)
            ent.bind("<FocusOut>", lambda _, k=key,
                     v=var: self._save(k, v.get()))
            ent.bind("<Return>", lambda _, k=key,
                     v=var: self._save(k, v.get()))

        reset = tk.Label(
            right,
            text="↺ reset",
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 7),
            cursor="hand2",
        )
        reset.pack(anchor="e")
        reset.bind("<Button-1>", lambda _, k=key, d=default: self._reset(k, d))
        reset.bind("<Enter>", lambda _, w=reset: w.config(fg=t.accent))
        reset.bind("<Leave>", lambda _, w=reset: w.config(fg=t.fg_muted))

        tk.Frame(container, bg=t.border, height=1).pack(fill="x", padx=20)

    def _build_git_panel(self, frame: tk.Frame, t) -> None:
        """Constrói o painel Git: configurações padrão + credenciais + URL updater."""
        hdr = tk.Frame(frame, bg=t.floating_bg)
        hdr.pack(fill="x", padx=20, pady=(16, 4))
        tk.Label(
            hdr,
            text="🌿  " + _t("setting.cat_git", "Git"),
            bg=t.floating_bg,
            fg=t.fg,
            font=(FONT_UI, 14, "bold"),
        ).pack(anchor="w")
        tk.Frame(frame, bg=t.border, height=1).pack(
            fill="x", padx=20, pady=(0, 8))

        std_keys = [
            "git_enabled",
            "git_decorations",
            "git_auto_fetch",
            "git_fetch_interval",
        ]
        for key in std_keys:
            schema = self._schema_map.get(key)
            if schema is None:
                continue
            skey, label, desc, kind, default, opts = (
                schema[0],
                schema[1],
                schema[2],
                schema[3],
                schema[4],
                schema[5],
            )
            type_hint = schema[6] if len(schema) > 6 else None
            example = schema[7] if len(schema) > 7 else None
            lk, dk = f"setting.{skey}", f"setting.desc.{skey}"
            label = _t(lk) if _t(lk) != lk else label
            desc = _t(dk) if _t(dk) != dk else desc
            self._build_setting_row(
                skey,
                label,
                desc,
                kind,
                default,
                opts,
                type_hint,
                example,
                t,
                parent=frame,
            )

        tk.Frame(frame, bg=t.border, height=1).pack(
            fill="x", padx=20, pady=(8, 4))
        sec_upd = tk.Frame(frame, bg=t.floating_bg)
        sec_upd.pack(fill="x", padx=20, pady=(4, 8))
        tk.Label(
            sec_upd,
            text="⬆  "
            + _t("setting.updater_url", "URL do Repositório de Atualizações"),
            bg=t.floating_bg,
            fg=t.fg,
            font=(FONT_UI, 10, "bold"),
            anchor="w",
        ).pack(anchor="w")
        tk.Label(
            sec_upd,
            text=_t(
                "setting.updater_url_desc",
                "Owner/repo do GitHub para checagem de atualizações. Padrão: KillovSky/Codis",
            ),
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            anchor="w",
            wraplength=500,
        ).pack(anchor="w")

        url_row = tk.Frame(sec_upd, bg=t.floating_bg)
        url_row.pack(fill="x", pady=(6, 0))
        cur_url = self._cfg.get("updater_url", "KillovSky/Codis")
        url_var = tk.StringVar(value=str(cur_url))
        url_ent = tk.Entry(
            url_row,
            textvariable=url_var,
            bg=t.input_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            bd=0,
            font=(FONT_UI, 9),
            highlightthickness=1,
            highlightbackground=t.border,
            width=36,
        )
        url_ent.pack(side="left", padx=(0, 8))

        def _save_url():
            val = url_var.get().strip() or "KillovSky/Codis"
            url_var.set(val)
            self._cfg.set("updater_url", val)
            if self._on_change:
                self._on_change("updater_url", val)

        tk.Label(
            url_row,
            text="Salvar",
            bg=t.accent,
            fg="#000",
            font=(FONT_UI, 9, "bold"),
            cursor="hand2",
            padx=10,
            pady=3,
            relief="flat",
        ).pack(side="left")
        url_row.winfo_children()[-1].bind("<Button-1>", lambda _: _save_url())
        url_ent.bind("<Return>", lambda _: _save_url())
        url_ent.bind("<FocusOut>", lambda _: _save_url())

        reset_url = tk.Label(
            url_row,
            text="↺ reset",
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 7),
            cursor="hand2",
        )
        reset_url.pack(side="left", padx=8)
        reset_url.bind(
            "<Button-1>", lambda _: (url_var.set("KillovSky/Codis"),
                                     _save_url())
        )
        reset_url.bind("<Enter>", lambda _, w=reset_url: w.config(fg=t.accent))
        reset_url.bind("<Leave>", lambda _,
                       w=reset_url: w.config(fg=t.fg_muted))

        tk.Frame(frame, bg=t.border, height=1).pack(
            fill="x", padx=20, pady=(12, 4))
        sec_cred = tk.Frame(frame, bg=t.floating_bg)
        sec_cred.pack(fill="x", padx=20, pady=(4, 4))

        tk.Label(
            sec_cred,
            text="🔑  " + _t("git.manage_creds", "Credenciais Git (HTTPS)"),
            bg=t.floating_bg,
            fg=t.fg,
            font=(FONT_UI, 10, "bold"),
            anchor="w",
        ).pack(anchor="w")
        tk.Label(
            sec_cred,
            text=_t(
                "git.creds_desc",
                "Gerencie os tokens de acesso pessoal (PAT) salvos para repositórios HTTPS.",
            ),
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            anchor="w",
            wraplength=500,
        ).pack(anchor="w", pady=(0, 8))

        creds_frame = tk.Frame(sec_cred, bg=t.floating_bg)
        creds_frame.pack(fill="x")

        def _refresh_creds():
            """Reconstrói a lista de credenciais salvas."""
            for child in creds_frame.winfo_children():
                child.destroy()
            creds_now: dict = self._cfg.get("git_credentials") or {}
            if not creds_now:
                tk.Label(
                    creds_frame,
                    text=_t("git.no_creds_saved", "Nenhuma credencial salva."),
                    bg=t.floating_bg,
                    fg=t.fg_muted,
                    font=(FONT_UI, 9),
                ).pack(anchor="w", pady=4)
            else:
                for host in list(creds_now.keys()):
                    row = tk.Frame(
                        creds_frame,
                        bg=t.sidebar_bg,
                        highlightthickness=1,
                        highlightbackground=t.border,
                    )
                    row.pack(fill="x", pady=2, ipady=4)
                    tk.Label(
                        row,
                        text=f"🌐  {host}",
                        bg=t.sidebar_bg,
                        fg=t.fg,
                        font=(FONT_UI, 9),
                        anchor="w",
                    ).pack(side="left", padx=12, fill="x", expand=True)

                    def _forget(h=host):
                        c: dict = dict(self._cfg.get("git_credentials") or {})
                        c.pop(h, None)
                        self._cfg.set("git_credentials", c)
                        self._cfg.save()
                        _refresh_creds()

                    tk.Label(
                        row,
                        text=_t("git.forget_creds", "Remover"),
                        bg=t.btn_bg,
                        fg=t.fg,
                        font=(FONT_UI, 8),
                        cursor="hand2",
                        padx=8,
                        pady=2,
                        relief="flat",
                    ).pack(side="right", padx=8)
                    row.winfo_children()[-1].bind(
                        "<Button-1>", lambda _, fn=_forget: fn()
                    )

        _refresh_creds()

        add_row = tk.Frame(sec_cred, bg=t.floating_bg)
        add_row.pack(fill="x", pady=(10, 4))

        def _add_creds():
            """Abre diálogo para inserir novas credenciais."""
            from src.ui.git_auth import _CredentialDialog

            host_win = tk.Toplevel(self)
            host_win.title(_t("git.add_creds", "Adicionar Credenciais"))
            host_win.resizable(False, False)
            host_win.configure(bg=t.floating_bg)
            host_win.transient(self)
            host_win.geometry("380x140")
            fw = tk.Frame(host_win, bg=t.floating_bg, padx=20, pady=16)
            fw.pack(fill="both", expand=True)
            tk.Label(
                fw,
                text=_t("git.host_label", "Host (ex: github.com):"),
                bg=t.floating_bg,
                fg=t.fg,
                font=(FONT_UI, 9),
                anchor="w",
            ).pack(fill="x")
            host_var = tk.StringVar(value="github.com")
            host_ent = tk.Entry(
                fw,
                textvariable=host_var,
                bg=t.input_bg,
                fg=t.fg,
                insertbackground=t.cursor,
                relief="flat",
                bd=0,
                font=(FONT_UI, 10),
                highlightthickness=1,
                highlightbackground=t.border,
            )
            host_ent.pack(fill="x", pady=(4, 10))
            btn_row = tk.Frame(fw, bg=t.floating_bg)
            btn_row.pack(fill="x")

            def _proceed():
                host = host_var.get().strip()
                if not host:
                    return
                host_win.destroy()
                url = f"https://{host}"
                dlg = _CredentialDialog(self, t, url, existing=None)
                self.wait_window(dlg.win)
                if dlg.confirmed:
                    import base64 as _b64

                    c: dict = dict(self._cfg.get("git_credentials") or {})
                    c[host] = {
                        "u": _b64.b64encode(dlg.user.encode()).decode(),
                        "t": _b64.b64encode(dlg.token.encode()).decode(),
                    }
                    self._cfg.set("git_credentials", c)
                    self._cfg.save()
                    _refresh_creds()

            tk.Label(
                btn_row,
                text="Continuar",
                bg=t.accent,
                fg="#000",
                font=(FONT_UI, 9, "bold"),
                cursor="hand2",
                padx=12,
                pady=4,
                relief="flat",
            ).pack(side="right")
            btn_row.winfo_children(
            )[-1].bind("<Button-1>", lambda _: _proceed())
            tk.Label(
                btn_row,
                text=_t("ui.cancel", "Cancelar"),
                bg=t.btn_bg,
                fg=t.fg,
                font=(FONT_UI, 9),
                cursor="hand2",
                padx=12,
                pady=4,
                relief="flat",
            ).pack(side="right", padx=6)
            btn_row.winfo_children()[-1].bind(
                "<Button-1>", lambda _: host_win.destroy()
            )
            host_ent.bind("<Return>", lambda _: _proceed())
            host_ent.focus_set()

        add_btn = tk.Label(
            add_row,
            text=_t("git.add_creds", "＋ Adicionar / Atualizar credenciais"),
            bg=t.accent,
            fg="#000",
            font=(FONT_UI, 9, "bold"),
            cursor="hand2",
            padx=12,
            pady=5,
            relief="flat",
        )
        add_btn.pack(side="left")
        add_btn.bind("<Button-1>", lambda _: _add_creds())

        with contextlib.suppress(tk.TclError):
            self._bind_scroll_fn(frame)
        self._update_canvas_scroll()

    def _on_search(self) -> None:
        """Filtra e exibe configurações de todas as categorias que correspondam à busca."""
        if self._destroyed or not self.winfo_exists():
            return
        q = self._search_var.get().strip().lower()
        if not q:
            self._show_category(
                self._current_cat if self._current_cat != "__search__" else "editor"
            )
            return

        t = self._tmgr.theme
        self._current_cat = "__search__"

        for cached_frame in self._cat_frame_cache.values():
            with contextlib.suppress(tk.TclError):
                cached_frame.pack_forget()

        if "__search__" in self._cat_frame_cache:
            with contextlib.suppress(tk.TclError):
                self._cat_frame_cache["__search__"].destroy()
        search_frame = tk.Frame(self._content_outer, bg=t.floating_bg)
        self._cat_frame_cache["__search__"] = search_frame
        search_frame.pack(fill="both", expand=True)

        tk.Label(
            search_frame,
            text=f'🔍  {_t("setting.search_results", "Resultados para")} "{q}"',
            bg=t.floating_bg,
            fg=t.fg,
            font=(FONT_UI, 12, "bold"),
        ).pack(anchor="w", padx=20, pady=(16, 4))
        tk.Frame(search_frame, bg=t.border, height=1).pack(
            fill="x", padx=20, pady=(0, 8)
        )

        found = 0
        for schema in SettingsEditor._get_schema():
            key = schema[0]
            if key.startswith("__"):
                continue
            label, desc, kind = schema[1], schema[2], schema[3]
            default, opts = schema[4], schema[5]
            type_hint = schema[6] if len(schema) > 6 else None
            example = schema[7] if len(schema) > 7 else None
            lk, dk = f"setting.{key}", f"setting.desc.{key}"
            label = _t(lk) if _t(lk) != lk else label
            desc = _t(dk) if _t(dk) != dk else desc
            if q in key or q in label.lower() or q in desc.lower():
                self._build_setting_row(
                    key,
                    label,
                    desc,
                    kind,
                    default,
                    opts,
                    type_hint,
                    example,
                    t,
                    parent=search_frame,
                )
                found += 1

        if found == 0:
            tk.Label(
                search_frame,
                text=_t(
                    "hc.settings_window.no_settings_found",
                    "Nenhuma configuração encontrada.",
                ),
                bg=t.floating_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 10),
            ).pack(padx=20, pady=20)
        else:
            tk.Label(
                search_frame,
                text=_t("setting.search_count", "{n} resultado(s)", n=found),
                bg=t.floating_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 8),
            ).pack(anchor="w", padx=20, pady=(0, 8))
            with contextlib.suppress(tk.TclError):
                self._count_lbl.config(text=f"{found} resultado(s)")

        with contextlib.suppress(tk.TclError):
            self._bind_scroll_fn(search_frame)
        self._update_canvas_scroll()

    def _save(self, key: str, value: Any) -> None:
        """Salva uma configuração e notifica o callback.

        Args:
            key: Chave de configuração.
            value: Novo valor a salvar.
        """
        self._cfg.set(key, value)
        if self._on_change:
            self._on_change(key, value)

    def _reset(self, key: str, default: Any) -> None:
        """Restaura uma configuração ao valor padrão e recarrega a categoria atual.

        Args:
            key: Chave de configuração a resetar.
            default: Valor padrão a aplicar.
        """
        self._cfg.set(key, default)
        if self._on_change:
            self._on_change(key, default)
        cat = self._current_cat
        if cat in self._cat_frame_cache:
            with contextlib.suppress(tk.TclError):
                self._cat_frame_cache.pop(cat).destroy()
        self._show_category(cat)

    def _reset_all(self) -> None:
        """Restaura todas as configurações da categoria atual aos valores padrão."""
        import tkinter.messagebox as mb

        cat = self._current_cat
        if cat in ("__search__",):
            return
        keys = self._CAT_KEYS.get(cat, [])
        if not keys:
            return
        cat_name = next(
            (nm for cid, _, nm in self._categories if cid == cat), cat)
        if not mb.askyesno(
            _t("setting.reset_all_title", "Restaurar Padrões"),
            _t(
                "setting.reset_all_msg",
                "Restaurar todas as configurações de '{cat}' aos valores padrão?",
                cat=cat_name,
            ),
            parent=self,
        ):
            return
        for key in keys:
            schema = self._schema_map.get(key)
            if schema:
                default = schema[4]
                self._cfg.set(key, default)
                if self._on_change:
                    self._on_change(key, default)
        if cat in self._cat_frame_cache:
            with contextlib.suppress(tk.TclError):
                self._cat_frame_cache.pop(cat).destroy()
        self._show_category(cat)

    def _open_json(self) -> None:
        """Abre o config.json no editor para edição manual."""
        if self._on_change:
            self._on_change("_open_json", str(CONFIG_FILE))
