"""Mixin ``_AppKuiMixin`` - integração da interface KUI (K-UI) no Codis.

Gerencia a ativação, configuração e comunicação entre a interface tradicional
Tkinter e o engine de renderização KUI.
"""

from __future__ import annotations

import contextlib
import logging
import tkinter as tk
from pathlib import Path
from tkinter import messagebox
from typing import TYPE_CHECKING

from src.constants import FONT_UI
from src.editor.vim_mode import VimMode
from src.i18n import _t
from src.theme.base import THEMES
from src.ui.grab_safe import safe_grab_set
from src.ui.k_ui import KUI
from src.ui.zen_media import ZenMediaDialog, ZenMediaPlayer, ZenMiniOrb
from src.ui.zen_player import ZenPlayerHost

if TYPE_CHECKING:
    pass

logger = logging.getLogger("Codis")

__all__ = ["_AppKuiMixin"]


class _AppKuiMixin:
    """Mixin de Kui do CodisApp."""

    def _connect_terminal_easter_eggs(self) -> None:
        """Conecta o terminal integrado ao sistema de easter eggs.

        Deve ser chamado apos a construcao completa da UI. Vincula o handler
        de /codis e o Konami code ao widget de input do terminal.
        """
        if not self._easter_eggs:
            return
        term = getattr(self, "_terminal", None)
        if term is None:
            return
        with contextlib.suppress(AttributeError, tk.TclError):
            term.set_easter_egg_handler(
                self._easter_eggs.handle_terminal_command)

            self._easter_eggs.attach_terminal_konami(term._input)

    def _open_zen_media_config(self) -> None:
        """Abre o diálogo de configuração da mídia Zen (playlist, autoplay).

        Garante que o player exista antes de exibir o diálogo.
        Após fechar reconecta o HUD caso o player tenha sido recriado.
        """
        if not self._zen_media_player:
            self._zen_media_player = ZenMediaPlayer(tk_root=self._root)
        ZenMediaDialog(self._root, self._tmgr,
                       self._zen_media_player, self._cfg)
        self._root.after(100, self._rewire_zen_media_callbacks)

    def _zen_player_host(self) -> ZenPlayerHost | None:
        """Cria (uma vez) o host do Zen Player: janela flutuante ou dock."""
        if not self._zen_media_player:
            self._zen_media_player = ZenMediaPlayer(tk_root=self._root)
        if self._zen_player is None:
            self._zen_player = ZenPlayerHost(
                self._root,
                self._zen_media_player,
                self._cfg,
                self._tmgr,
                dock_before=self._h_paned,
                on_minimize=self._zen_media_show_orb,
            )
        return self._zen_player

    def _toggle_zen_player(self) -> None:
        """Mostra/esconde o Zen Player (menu Workspace)."""
        host = self._zen_player_host()
        if host is None:
            return
        with contextlib.suppress(tk.TclError):
            if self._zen_media_orb and self._zen_media_orb.winfo_exists():
                self._zen_media_orb.hide()
        host.toggle()

    def _rewire_zen_media_callbacks(self) -> None:
        """Exibe o player após fechar o diálogo de configuração, se houver faixas."""
        if not self._zen_media_player:
            return
        if self._zen_media_player.is_playing or self._zen_media_player.track_count > 0:
            host = self._zen_player_host()
            if host is not None:
                host.show()

    def _zen_media_autoplay(self) -> None:
        """Chamado ao entrar no modo Zen - inicia a reprodução se configurado.

        Respeita a opção ``zen_media_autoplay`` salva na configuração.
        """
        if not self._zen_media_player:
            return
        if self._cfg.get("zen_media_autoplay", False):
            self._zen_media_player.play()
        if self._zen_media_player.track_count > 0:
            host = self._zen_player_host()
            if host is not None and not host.visible:
                host.show()

    def _zen_media_stop_hud(self) -> None:
        """Esconde a bolinha ao sair do modo Zen.

        O player (janela ou dock) continua como o usuário deixou; a música
        não é interrompida.
        """
        if self._zen_media_orb and self._zen_media_orb.winfo_exists():
            self._zen_media_orb.hide()

    def _zen_media_show_orb(self) -> None:
        """Exibe a bolinha flutuante (ZenMiniOrb) quando o player é minimizado.

        Posiciona a bolinha no canto inferior direito da tela.
        Clicar nela chama :meth:`_zen_media_expand_hud`.
        """
        if not self._zen_media_player:
            return
        with contextlib.suppress(tk.TclError):
            sw = self._root.winfo_screenwidth()
            sh = self._root.winfo_screenheight()
            orb_size = 58
            ox = sw - orb_size - 12
            oy = sh - orb_size - 48
            if not self._zen_media_orb or not self._zen_media_orb.winfo_exists():
                self._zen_media_orb = ZenMiniOrb(
                    self._root,
                    self._zen_media_player,
                    on_expand=self._zen_media_expand_hud,
                    tmgr=self._tmgr,
                )
                self._zen_media_orb.move_to(ox, oy)
            self._zen_media_orb.show()

    def _zen_media_expand_hud(self) -> None:
        """Expande da bolinha de volta para o player."""
        with contextlib.suppress(tk.TclError):
            if self._zen_media_orb and self._zen_media_orb.winfo_exists():
                self._zen_media_orb.hide()
        host = self._zen_player_host()
        if host is not None:
            host.show()

    def _toggle_vim_mode(self) -> None:
        """Alterna o modo Vim."""
        if self._vim_enabled:
            self._disable_vim_mode()
        else:
            self._enable_vim_mode()

    def _enable_vim_mode(self) -> None:
        """Ativa o modo Vim no editor ativo."""
        if self._kui_enabled:
            self._disable_kui()

        ed = self._e()
        if not ed:
            self._vim_enabled = True
            self._cfg.set("vim_mode", True)
            self._status.notify(_t("kui.vim_mode_on"))
            return
        self._vim_enabled = True
        self._cfg.set("vim_mode", True)
        self._cfg.save()

        def on_mode_change(mode: str, cmd: str = "") -> None:
            """Atualiza a status bar e o K-UI com o modo Vim atual."""
            mode_icons = {
                "NORMAL": "⬛ NORMAL",
                "INSERT": "▮ INSERT",
                "VISUAL": "█ VISUAL",
                "V-LINE": "█ V-LINE",
                "REPLACE": "░ REPLACE",
                "COMMAND": f":{cmd}",
            }
            label = mode_icons.get(mode, mode)
            self._status.notify(f"⌨  Vim  {label}")
            if self._kui:
                self._kui.set_vim_mode(mode, cmd)

        self._vim_mode = VimMode(
            ed,
            on_mode_change,
            quit_cb=self._disable_vim_mode,
            save_cb=self.save_file,
        )
        self._vim_mode.enable()

        with contextlib.suppress(tk.TclError):
            if self._kui:
                self._kui.toast(
                    "⌨  Vim activated  -  i insert  Esc normal  :q quit",
                    kind="info",
                    duration_ms=4000,
                )

    def _disable_vim_mode(self) -> None:
        """Desativa o modo Vim."""
        self._vim_enabled = False
        self._cfg.set("vim_mode", False)
        self._cfg.save()
        if self._vim_mode:
            self._vim_mode.disable()
            self._vim_mode = None
        self._status.notify(_t("kui.vim_mode_off"))

    def _toggle_kui(self) -> None:
        """Alterna a interface K-UI."""
        if self._kui_enabled:
            self._disable_kui()
        else:
            self._enable_kui()

    def _enable_kui(self) -> None:
        """Ativa a interface K-UI (idempotente: a partida a agenda mais de uma vez)."""
        if self._kui is not None and getattr(self._kui, "_enabled", False):
            return
        if self._vim_enabled:
            self._disable_vim_mode()

        self._kui_enabled = True
        self._cfg.set("kui_enabled", True)
        self._cfg.save()

        commands = [
            ("📄", _t("kui.cmd.new_file"), self.new_file),
            ("📂", _t("kui.cmd.open_file"), self.open_file_dialog),
            ("🗂", _t("kui.cmd.open_folder"), self.open_folder_dialog),
            ("💾", _t("kui.cmd.save"), self.save_file),
            ("💾", _t("kui.cmd.save_as"), self.save_as),
            ("💾", _t("kui.cmd.save_all"), self.save_all),
            ("🔁", _t("kui.cmd.revert"), self.revert_file),
            ("✕", _t("kui.cmd.close_tab"), self._close_active_tab),
            ("✕", _t("kui.cmd.close_all_tabs"), self._close_all_tabs),
            ("↩", _t("edit.undo"), self._undo),
            ("↪", _t("edit.redo"), self._redo),
            ("✂", _t("edit.cut"), self._cut),
            ("📋", _t("edit.copy"), self._copy),
            ("📋", _t("edit.paste"), self._paste),
            ("🔲", _t("edit.select_all"), self._select_all),
            ("🔍", _t("kui.cmd.find"), self._show_find),
            ("🔍", _t("kui.cmd.find_replace"), self._show_replace),
            ("🔍", _t("kui.cmd.find_files"), self._show_find_files),
            ("💬", _t("edit.toggle_comment"), self._toggle_comment),
            ("📋", _t("edit.duplicate_line"), self._duplicate_line),
            ("🗑", _t("edit.delete_line"), self._delete_line),
            ("⬆", _t("edit.move_line_up"), self._move_line_up),
            ("⬇", _t("edit.move_line_down"), self._move_line_down),
            ("🔀", _t("kui.cmd.sort_asc"), lambda: self._sort_lines(False)),
            ("🔀", _t("kui.cmd.sort_desc"), lambda: self._sort_lines(True)),
            ("✂", _t("edit.trim_trailing"), self._trim_trailing),
            ("🗑", _t("edit.remove_dupes"), self._remove_duplicate_lines),
            ("📋", _t("edit.paste_history"), self._paste_history_dialog),
            ("▶", _t("run.run_file"), self._run_file),
            ("F", _t("run.format_document"), self._format_document),
            ("🔍", _t("kui.cmd.lint"), self._lint_file),
            ("🔧", _t("kui.cmd.fix_all"), self._fix_all),
            ("T", _t("kui.cmd.theme"), self._show_theme_picker),
            ("✦", _t("kui.cmd.disable"), self._disable_kui),
            ("P", _t("kui.cmd.perf"), self._show_kui_perf_picker),
            ("👁", _t("kui.cmd.word_wrap"), self._toggle_word_wrap),
            ("🗺", _t("kui.cmd.minimap"), self._toggle_minimap),
            ("🔲", _t("kui.cmd.breadcrumbs"), self._toggle_breadcrumbs),
            ("🔍", _t("kui.cmd.zoom_in"), lambda: self._zoom(+1)),
            ("🔍", _t("kui.cmd.zoom_out"), lambda: self._zoom(-1)),
            ("🔲", _t("kui.cmd.zoom_reset"), lambda: self._zoom(0)),
            ("🖥", _t("kui.cmd.fullscreen"), self._toggle_fullscreen),
            ("📐", _t("kui.cmd.split"), self._split_editor),
            ("🏠", _t("kui.cmd.welcome"), self._show_welcome),
            ("G", _t("kui.cmd.goto_line"), self._goto_line),
            ("S", _t("kui.cmd.goto_symbol"), self._goto_symbol),
            ("⚠", _t("kui.cmd.next_problem"), self._next_problem),
            ("⚠", _t("kui.cmd.prev_problem"), self._prev_problem),
            ("➡", _t("kui.cmd.next_tab"), self._next_tab),
            ("⬅", _t("kui.cmd.prev_tab"), self._prev_tab),
            ("⚠", _t("kui.cmd.problems"), lambda: self._show_panel("problems")),
            ("🖥", _t("kui.cmd.terminal"), lambda: self._show_panel("terminal")),
            ("📤", _t("kui.cmd.output"), lambda: self._show_panel("output")),
            ("📤", _t("kui.cmd.git_panel"), lambda: self._show_panel("git")),
            ("D", _t("kui.cmd.diff"), lambda: self._show_panel("diff")),
            ("🐛", _t("kui.cmd.debug"), lambda: self._show_panel("debug")),
            ("⑆", _t("kui.cmd.regex"), lambda: self._show_panel("regex")),
            ("🖥", _t("kui.cmd.new_terminal"), self._new_terminal),
            ("📁", _t("kui.cmd.explorer"), lambda: self._activity("explorer")),
            ("🔍", _t("kui.cmd.search"), lambda: self._activity("search")),
            ("🌿", _t("kui.cmd.source_ctrl"), lambda: self._activity("git")),
            ("🔌", _t("kui.cmd.extensions"), lambda: self._activity("extensions")),
            ("📝", _t("kui.cmd.outline"), lambda: self._activity("outline")),
            ("🔖", _t("kui.cmd.bookmarks"), lambda: self._activity("bookmarks")),
            ("📝", _t("kui.cmd.scratchpad"), lambda: self._activity("scratchpad")),
            ("📊", _t("kui.cmd.stats"), lambda: self._activity("codestats")),
            ("✅", _t("kui.cmd.todo"), lambda: self._activity("todo")),
            ("🌍", _t("kui.cmd.rest"), lambda: self._activity("restclient")),
            ("🌐", _t("kui.cmd.preview"), lambda: self._activity("preview")),
            ("⏱", _t("kui.cmd.pomodoro"), lambda: self._activity("pomodoro")),
            ("✂", _t("kui.cmd.snippets"), lambda: self._activity("snippets")),
            ("🔀", _t("kui.cmd.flowchart"), lambda: self._activity("flowchart")),
            ("C", _t("kui.cmd.color_picker"), self._open_color_picker),
            ("📝", _t("kui.cmd.session_notes"), self._open_session_notes),
            ("🔖", _t("kui.cmd.bookmark_add"), self._add_bookmark),
            ("🕒", _t("kui.cmd.file_timeline"), self._open_file_timeline),
            ("📊", _t("kui.cmd.word_count"), self._word_count_dialog),
            ("🔑", _t("kui.cmd.base64_enc"), self._base64_encode),
            ("🔑", _t("kui.cmd.base64_dec"), self._base64_decode),
            ("🔗", _t("kui.cmd.url_enc"), self._url_encode),
            ("🔗", _t("kui.cmd.url_dec"), self._url_decode),
            ("🔐", _t("kui.cmd.hash_md5"), lambda: self._hash_selection("md5")),
            ("🔐", _t("kui.cmd.hash_sha256"),
             lambda: self._hash_selection("sha256")),
            ("🔢", _t("kui.cmd.insert_seq"), self._insert_sequence_dialog),
            ("D", _t("kui.cmd.diff_file"), self._show_file_diff),
            ("D", _t("kui.cmd.diff_two"), self._show_diff_two_files),
            ("🎵", _t("kui.cmd.zen_media"), self._open_zen_media_config),
            ("⌨", _t("kui.cmd.vim"), self._toggle_vim_mode),
            ("🌌", _t("kui.cmd.zen"), self._toggle_zen_mode),
            ("👁", _t("kui.cmd.focus_mode"), self._toggle_focus_mode),
            ("🔒", _t("kui.cmd.read_only"), self._toggle_read_only),
            (
                "🔍",
                _t("kui.cmd.lsp_goto_def"),
                lambda: self._e().lsp_goto_definition() if self._e() else None,
            ),
            (
                "🔗",
                _t("kui.cmd.lsp_refs"),
                lambda: self._e().lsp_find_references() if self._e() else None,
            ),
            (
                "✏",
                _t("kui.cmd.lsp_rename"),
                lambda: self._e().lsp_rename_symbol() if self._e() else None,
            ),
            ("⊞", _t("kui.detach.terminal"),
             lambda: self._detach_panel("terminal")),
            ("⊞", _t("kui.detach.output"), lambda: self._detach_panel("output")),
            ("⊞", _t("kui.detach.git"), lambda: self._detach_panel("git")),
            ("⊞", _t("kui.detach.debug"), lambda: self._detach_panel("debug")),
            ("⊞", _t("kui.detach.regex"), lambda: self._detach_panel("regex")),
            ("⊞", _t("kui.detach.problems"),
             lambda: self._detach_panel("problems")),
            ("📌", _t("kui.cmd.float_sidebar"), self._kui_toggle_sidebar),
            ("⚙", _t("kui.cmd.settings"), self._show_settings),
            ("❓", _t("kui.cmd.keybindings"), self._keybindings_dialog),
            ("ℹ", _t("kui.cmd.about"), self._about_dialog),
            ("📦", _t("kui.cmd.plugins_manage"),
             lambda: self._activity("extensions")),
            ("🔄", _t("kui.cmd.plugins_reload"), self._load_plugins),
        ]

        ribbon_actions = [
            (
                "F",
                _t("kui.cmd.format"),
                lambda: self._e().format_document() if self._e() else None,
            ),
            (
                "💬",
                _t("edit.toggle_comment"),
                lambda: (
                    self._toggle_comment() if hasattr(self, "_toggle_comment") else None
                ),
            ),
            ("🔍", _t("kui.cmd.find"), self._show_find),
            ("📋", _t("edit.copy"), self._copy),
            ("📌", _t("kui.cmd.bookmark_add"), self._add_bookmark),
        ]

        with contextlib.suppress(AttributeError, tk.TclError):
            self._panel_detach.set_factories(self._build_panel_factories())
            self._panel_detach.set_dark(
                getattr(self._tmgr.theme, "dark", True))

        self._kui = KUI(
            self._root,
            self._tmgr,
            self._e,
            commands,
            ribbon_actions,
            hide_sidebar_cb=self._hide_sidebar,
            show_sidebar_cb=self._show_sidebar,
            _hide_activity_cb=lambda: [
                self._h_paned.paneconfigure(self._actbar, minsize=0, width=0),
                self._h_paned.sash_place(0, 0, 0),
            ],
            _show_activity_cb=lambda: [
                self._h_paned.paneconfigure(
                    self._actbar, minsize=48, width=48),
                self._h_paned.sash_place(0, 48, 0),
            ],
            tabbar_widget=getattr(self, "_tabbar", None),
            breadcrumb_widget=getattr(self, "_breadcrumb", None),
            statusbar_widget=getattr(self, "_status", None),
            menubar_ref=getattr(self, "_menubar", None),
            content_frame=getattr(self, "_content_frame", None),
            editor_container=getattr(self, "_editor_container", None),
            h_paned=getattr(self, "_h_paned", None),
            v_paned=getattr(self, "_v_paned", None),
            hide_bottom_cb=self._hide_bottom_panel,
            show_bottom_cb=self._show_bottom_panel,
            sidebar_dock=getattr(self, "_dock", None),
            is_sidebar_visible=lambda: self._sidebar_visible,
            show_welcome_cb=self._show_welcome,
            hide_classic_welcome_cb=self._hide_classic_welcome,
            actbar_widget=getattr(self, "_actbar", None),
            panel_factories=self._build_panel_factories(),
            perf_profile=self._cfg.get("kui_perf", "balanced"),
            detach_manager=getattr(self, "_panel_detach", None),
        )
        self._kui.enable()

        with contextlib.suppress(tk.TclError):
            if hasattr(self, "_detach_btn"):
                self._detach_btn.pack(
                    side="right", padx=(0, 2), before=self._panel_tabs.get("problems")
                )

        recent = [str(p) for p in self._cfg.get_recent_files()[:12]]
        self._kui.show_welcome(
            on_new=self.new_file,
            on_open=self.open_file_dialog,
            on_folder=self.open_folder_dialog,
            on_recent=self._open_file,
            recent_files=recent,
        )

        self._apply_kui_colors()
        self._status.notify(_t("kui.enabled"))

        self._root.bind("<<KUITabSelect>>", self._on_kui_tab_select, add=True)
        self._root.bind("<<KUITabClose>>", self._on_kui_tab_close, add=True)
        self._root.bind("<<KUITabDual>>", self._on_kui_tab_dual, add=True)

    def _apply_kui_colors(self) -> None:
        """Pinta frames com void após ativar K-UI (preserva sashwidth=4).

        Usa _pal() do K-UI, que retorna void escuro em ambos os modos
        (dark=#020209, light=#0d0d2b), em vez de ``t.bg``.
        """
        from src.ui.k_ui import _pal as _kui_pal

        t = self._tmgr.theme
        p = _kui_pal(getattr(t, "dark", True))
        void = p["void"]
        editor_bg = p["text_bg"]
        editor_fg = p["text_fg"]
        cursor = p["text_cur"]
        sel_bg = p["text_sel"]
        sel_fg = p["text_fg"]

        with contextlib.suppress(tk.TclError):
            self._root.config(bg=void)

        for pw in (getattr(self, "_h_paned", None), getattr(self, "_v_paned", None)):
            if pw:
                with contextlib.suppress(tk.TclError):
                    pw.config(bg=void)

        for attr in (
            "_content_frame",
            "_editor_container",
            "_sidebar",
            "_sidebar_stack",
            "_bottom_frame",
            "_panel_content",
        ):
            w = getattr(self, attr, None)
            if w:
                with contextlib.suppress(tk.TclError):
                    w.config(bg=void)

        for e in self._editors:
            with contextlib.suppress(tk.TclError):
                e.text.config(
                    bg=editor_bg,
                    fg=editor_fg,
                    insertbackground=cursor,
                    selectbackground=sel_bg,
                    selectforeground=sel_fg,
                )

    def _detach_panel(self, panel_key: str) -> None:
        """Destaca um painel inferior para uma janela flutuante.

        Funciona tanto no modo clássico quanto no modo K-UI,
        delegando ao ``PanelDetachManager`` compartilhado do ``CodisApp``.
        """
        with contextlib.suppress(AttributeError, tk.TclError):
            self._panel_detach.detach(panel_key)

    def _kui_toggle_sidebar(self) -> None:
        """Alterna a K-UI Dock (apenas modo K-UI)."""
        if self._kui:
            with contextlib.suppress(tk.TclError):
                self._kui.toggle_sidebar()

    def _build_panel_factories(self) -> dict:
        """Constrói fábricas para cada painel destacável.

        Importante: o ``cwd`` do terminal é resolvido dentro do lambda (no
        momento do detach), não aqui, porque esta função é chamada uma
        única vez na inicialização do app (para alimentar o
        ``PanelDetachManager`` compartilhado) - usar ``self._workspace`` no
        corpo do lambda garante que sempre reflita a pasta aberta mais
        recente, mesmo que tenha mudado depois da construção das fábricas.
        """
        factories: dict = {}

        with contextlib.suppress(tk.TclError):
            from src.ui.terminal import TerminalPanel as _TerminalPanel

            factories["terminal"] = lambda parent: _TerminalPanel(
                parent,
                self._tmgr,
                self._cfg,
                cwd=str(self._workspace) if self._workspace else str(
                    Path.home()),
            )

        with contextlib.suppress(tk.TclError):
            from src.ui.output_panel import OutputPanel as _OutputPanel

            factories["output"] = lambda parent: _OutputPanel(
                parent, self._tmgr, self._cfg
            )

        with contextlib.suppress(tk.TclError):
            from src.ui.git_panel import GitPanel as _GitPanel

            factories["git"] = lambda parent: _GitPanel(
                parent,
                self._tmgr,
                self._cfg,
                root_path=self._workspace,
                on_refresh=self._on_git_refresh,
            )

        with contextlib.suppress(tk.TclError):
            from src.ui.debug_panel import DebugPanel as _DebugPanel

            factories["debug"] = lambda parent: _DebugPanel(
                parent, self._tmgr, self._cfg, get_editor=self._active_editor
            )

        with contextlib.suppress(tk.TclError):
            from src.ui.regex_playground import RegexPlayground as _RegexPlayground

            factories["regex"] = lambda parent: _RegexPlayground(
                parent,
                self._tmgr,
                get_text=lambda: self._e().get_content() if self._e() else "",
                apply_replace=self._regex_replace,
            )

        with contextlib.suppress(tk.TclError):
            from src.ui.problems import ProblemsPanel as _ProblemsPanel

            factories["problems"] = lambda parent: _ProblemsPanel(
                parent,
                self._tmgr,
                on_goto=self._goto_problem_pos,
                on_apply_fix=self._apply_problem_fix,
                on_fix_all=self._apply_problem_fix_all,
            )

        with contextlib.suppress(tk.TclError):
            from src.editor.diff_pane import DiffPane as _DiffPane

            def _make_diff_panel(parent: tk.Widget) -> _DiffPane:
                """Cria uma DiffPane flutuante pré-carregada com o diff atual.

                O DiffPane embutido (``self._diff_pane``) é alimentado por
                chamadas externas (ex.: comparar arquivos, ver diff do git) e
                não por uma fábrica - então, ao destacar, copiamos o estado
                que ele já tem (original/modified/nomes) para a nova janela
                flutuante em vez de abrir algo vazio.
                """
                dp = _DiffPane(
                    parent,
                    self._tmgr,
                    self._cfg,
                    on_close=lambda: self._panel_detach.reattach("diff"),
                )
                src = getattr(self, "_diff_pane", None)
                if src is not None:
                    with contextlib.suppress(tk.TclError):
                        dp.show(
                            getattr(src, "_original", ""),
                            getattr(src, "_modified", ""),
                            getattr(src, "_orig_name", ""),
                            getattr(src, "_mod_name", ""),
                        )
                return dp

            factories["diff"] = _make_diff_panel

        try:
            from src.ui.sql_studio import SQLStudioPanel as _SQLStudioPanel

            factories["sql"] = lambda parent: _SQLStudioPanel(
                parent, self._tmgr, self._cfg
            )
        except Exception as exc:
            logger.debug("SQL Studio indisponível para detach: %s", exc)

        return factories

    def _disable_kui(self) -> None:
        """Desativa a interface K-UI e restaura as cores do tema atual."""
        self._kui_enabled = False
        self._cfg.set("kui_enabled", False)
        self._cfg.save()
        if self._kui:
            self._kui.disable()
            self._kui = None

        with contextlib.suppress(tk.TclError):
            self._root.unbind("<<KUITabSelect>>")
        with contextlib.suppress(tk.TclError):
            self._root.unbind("<<KUITabClose>>")
        with contextlib.suppress(tk.TclError):
            self._root.unbind("<<KUITabDual>>")

        with contextlib.suppress(Exception):
            self._on_theme_change(self._tmgr.theme)

        self._status.notify(_t("kui.disabled"))
        if not self._editors:
            self._show_welcome()

    def _on_kui_tab_select(self, _event: tk.Event) -> None:
        """Handler do evento <<KUITabSelect>> disparado pela KTabBar holográfica."""
        if not self._kui:
            return
        action = getattr(self._kui, "_last_tab_action", None)
        if not isinstance(action, tuple) or action[0] != "select":
            return
        idx = int(action[1])
        self._kui._last_tab_action = None
        open_files = getattr(self._kui, "_open_files", [])
        if not 0 <= idx < len(open_files):
            return
        target_path = open_files[idx]
        with contextlib.suppress(OSError):
            existing = self._find_editor_for_path(Path(target_path))
            if existing is not None:
                self._select_tab(existing)
            else:
                self._open_file(target_path)

    def _on_kui_tab_close(self, _event: tk.Event) -> None:
        """Handler do evento <<KUITabClose>> disparado pela KTabBar holográfica."""
        if not self._kui:
            return
        action = getattr(self._kui, "_last_tab_action", None)
        if not isinstance(action, tuple) or action[0] != "close":
            return
        idx = int(action[1])
        self._kui._last_tab_action = None
        open_files = getattr(self._kui, "_open_files", [])
        if not 0 <= idx < len(open_files):
            self._close_active_tab()
            return
        with contextlib.suppress(OSError):
            existing = self._find_editor_for_path(Path(open_files[idx]))
            if existing is not None:
                self._close_tab(existing)
            else:
                self._close_active_tab()

    def _on_kui_tab_dual(self, _event: tk.Event) -> None:
        """Handler do evento <<KUITabDual>> disparado pela KTabBar holográfica.

        Abre a aba selecionada no lado direito do modo dual.
        """
        if not self._kui:
            return
        action = getattr(self._kui, "_last_tab_action", None)
        if not isinstance(action, tuple) or action[0] != "dual":
            return
        idx = int(action[1])
        self._kui._last_tab_action = None
        open_files = getattr(self._kui, "_open_files", [])
        if 0 <= idx < len(open_files):
            path = Path(open_files[idx])
            self._split_tab_to_right(self._find_editor_for_path(path) or idx)
        else:
            self._split_editor()

    def _show_kui_perf_picker(self) -> None:
        """Abre o seletor de perfil de performance da K-UI.

        Modos ultra e quantum exibem diálogo de confirmação, pois podem
        consumir bastante CPU devido aos cálculos e renderização intensivos.
        """
        t = self._tmgr.theme
        dlg = tk.Toplevel(self._root)
        dlg.title(_t("ui.kui_perf_title"))
        dlg.configure(bg=t.floating_bg)
        dlg.transient(self._root)
        dlg.resizable(False, False)
        with contextlib.suppress(tk.TclError):
            safe_grab_set(dlg)

        current = self._cfg.get("kui_perf", "balanced")

        labels = {
            "quantum": "🌌  " + _t("kui.perf.quantum"),
            "ultra": "🌌  " + _t("kui.perf.ultra"),
            "balanced": "⚖️   " + _t("kui.perf.balanced"),
            "power_saver": "🔋  " + _t("kui.perf.power_saver"),
            "minimal": _t("kui.perf.minimal"),
        }

        tk.Label(
            dlg,
            text=_t("ui.kui_perf_header"),
            bg=t.floating_bg,
            fg=t.fg,
            font=(FONT_UI, 11, "bold"),
            anchor="w",
            pady=8,
        ).pack(fill="x", padx=12)

        var = tk.StringVar(value=current)
        for key, label in labels.items():
            rb = tk.Radiobutton(
                dlg,
                text=label,
                variable=var,
                value=key,
                bg=t.floating_bg,
                fg=t.fg,
                selectcolor=t.input_bg,
                activebackground=t.btn_hover,
                activeforeground=t.fg,
                anchor="w",
                padx=16,
                pady=4,
                font=(FONT_UI, 10),
            )
            rb.pack(fill="x")

        def _apply() -> None:
            """Aplica o perfil escolhido com confirmação obrigatória para ultra/quantum."""
            profile = var.get()

            if profile in ("ultra", "quantum"):
                msg_key = (
                    "kui.confirm.quantum_msg"
                    if profile == "quantum"
                    else "kui.confirm.ultra_msg"
                )
                confirmed = messagebox.askyesno(
                    title=_t("kui.confirm.title"),
                    message=_t(msg_key),
                    icon=messagebox.WARNING,
                    default=messagebox.NO,
                    parent=dlg,
                )
                if not confirmed:
                    return

            self._cfg.set("kui_perf", profile)
            self._cfg.save()
            if self._kui:
                self._kui.set_perf_profile(profile)
            self._status.notify(_t("ui.kui_perf_applied", profile=profile))
            dlg.destroy()

        btn_frame = tk.Frame(dlg, bg=t.floating_bg)
        btn_frame.pack(fill="x", padx=12, pady=10)
        tk.Button(
            btn_frame,
            text=_t("kui.perf.apply"),
            command=_apply,
            bg=t.btn_primary,
            fg=t.btn_primary_fg,
            relief="flat",
            padx=16,
            pady=4,
        ).pack(side="right")
        tk.Button(
            btn_frame,
            text=_t("kui.confirm.cancel"),
            command=dlg.destroy,
            bg=t.btn_bg,
            fg=t.btn_fg,
            relief="flat",
            padx=16,
            pady=4,
        ).pack(side="right", padx=8)

    def _show_theme_picker(self) -> None:
        """Seletor rápido de tema usado pelo spotlight."""
        with contextlib.suppress(tk.TclError):
            t = self._tmgr.theme
            dlg = tk.Toplevel(self._root)
            dlg.title("Theme")
            dlg.configure(bg=t.floating_bg)
            dlg.transient(self._root)
            safe_grab_set(dlg)
            for name in sorted(THEMES.keys()):
                tk.Button(
                    dlg,
                    text=name,
                    command=lambda n=name: (self._set_theme(n), dlg.destroy()),
                    bg=t.btn_bg,
                    fg=t.fg,
                    relief="flat",
                    font=("Segoe UI", 9),
                ).pack(fill="x", padx=8, pady=1)
