"""Mixin ``_AppSettingsMixin`` - gerenciamento das configurações do Codis.

Expõe métodos para abrir a janela de configurações, aplicar preferências de
fonte, tema, indentação e idioma em tempo real sem reinicialização.
"""

from __future__ import annotations

import contextlib
import logging
import re
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING, Any

from src.constants import APP_NAME, TAB_SIZE
from src.editor.pane import EditorPane
from src.i18n import _t
from src.ui.command_palette import CommandPalette

if TYPE_CHECKING:
    pass

logger = logging.getLogger("Codis")

__all__ = ["_AppSettingsMixin"]


class _AppSettingsMixin:
    """Mixin de Settings do CodisApp."""

    def _toggle_panel(self) -> None:
        """Alterna a visibilidade do painel inferior."""
        if self._panel_visible:
            self._hide_bottom_panel()
        else:
            self._show_bottom_panel()

    def _show_bottom_panel(self) -> None:
        """Exibe o painel inferior."""
        h = self._cfg.get("panel_height", 220)
        self._panel_visible = True
        self._update_panel_toggle_arrows()

        def _do_show():
            with contextlib.suppress(tk.TclError):
                self._v_paned.paneconfigure(
                    self._bottom_frame, minsize=80, height=h)
                total_h = self._v_paned.winfo_height()
                if total_h > 100:
                    self._v_paned.sash_place(0, 0, max(80, total_h - h))

        self._root.update_idletasks()
        _do_show()

        self._root.after(30, _do_show)

    def _hide_bottom_panel(self) -> None:
        """Oculta o painel inferior."""
        self._panel_visible = False
        self._update_panel_toggle_arrows()

        def _do_hide():
            with contextlib.suppress(tk.TclError):
                self._v_paned.paneconfigure(
                    self._bottom_frame, minsize=0, height=0)
                total_h = self._v_paned.winfo_height()
                if total_h > 0:
                    self._v_paned.sash_place(0, 0, total_h + 50)

        self._root.update_idletasks()
        _do_hide()
        self._root.after(30, _do_hide)

    def _show_problems(self) -> None:
        """Exibe o painel de problemas."""
        self._show_panel("problems")

    def _toggle_git_panel(self) -> None:
        """Alterna o painel Git na barra lateral."""
        self._activity("git")

    def _goto_problem_pos(self, line: int, col: int) -> None:
        """Vai para a posição (linha, coluna) no editor ativo."""
        e = self._active_editor()
        if e:
            e.goto_line(line, col)
            with contextlib.suppress(tk.TclError):
                e.text.focus_set()

    def _e(self) -> EditorPane | None:
        """Retorna o editor ativo, se existir."""
        if 0 <= self._active_idx < len(self._editors):
            return self._editors[self._active_idx]
        return None

    def _undo(self) -> None:
        """Desfaz a última ação no editor ativo."""
        e = self._e()
        if e:
            with contextlib.suppress(tk.TclError):
                e.text.edit_undo()

    def _redo(self) -> None:
        """Refaz a ação desfeita no editor ativo."""
        e = self._e()
        if e:
            with contextlib.suppress(tk.TclError):
                e.text.edit_redo()

    def _cut(self) -> None:
        """Recorta o texto selecionado no editor ativo."""
        e = self._e()
        if not e:
            return
        with contextlib.suppress(tk.TclError):
            sel = e.text.get(tk.SEL_FIRST, tk.SEL_LAST)
            if sel:
                self._record_clipboard(sel, kind="cut")
        e.text.event_generate("<<Cut>>")

    def _copy(self) -> None:
        """Copia o texto selecionado no editor ativo."""
        e = self._e()
        if not e:
            return
        with contextlib.suppress(tk.TclError):
            sel = e.text.get(tk.SEL_FIRST, tk.SEL_LAST)
            if sel:
                self._record_clipboard(sel, kind="copy")
        e.text.event_generate("<<Copy>>")

    def _paste(self) -> None:
        """Cola o conteúdo da área de transferência no editor ativo."""
        if e := self._e():
            with contextlib.suppress(tk.TclError):
                text = self._root.clipboard_get()
                if text:
                    self._record_clipboard(text, kind="copy")
            e.text.event_generate("<<Paste>>")

    def _record_clipboard(self, text: str, kind: str = "copy") -> None:
        """Adiciona o texto ao histórico global de área de transferência.

        Delega para ``_on_copy_event`` (definido em ``app.py``), que é a
        única fonte de verdade do histórico (``_copy_history``), também
        usada pelo polling do clipboard do SO e pelo diálogo de histórico
        de colagem (Ctrl+Shift+V).
        """
        if not text:
            return
        on_copy = getattr(self, "_on_copy_event", None)
        if callable(on_copy):
            on_copy(text, kind)

    def _select_all(self) -> None:
        """ """
        widget_foco = self._root.focus_get()

        if isinstance(widget_foco, (tk.Entry, ttk.Entry)):
            try:
                widget_foco.select_range(0, "end")
                widget_foco.icursor("end")
            except tk.TclError:
                pass
            return

        if isinstance(widget_foco, tk.Text):
            try:
                widget_foco.tag_add(tk.SEL, "1.0", "end")
                widget_foco.mark_set(tk.INSERT, "end")
            except tk.TclError:
                pass
            return

        if e := self._e():
            e.text.tag_add(tk.SEL, "1.0", "end")
            e.text.mark_set(tk.INSERT, "end")

    def _toggle_comment(self) -> None:
        """Alterna comentário nas linhas selecionadas."""
        if e := self._e():
            e.toggle_comment()

    def _duplicate_line(self) -> None:
        """Duplica a linha atual."""
        if e := self._e():
            e.duplicate_line()

    def _delete_line(self) -> None:
        """Apaga a linha atual."""
        if e := self._e():
            e.delete_line()

    def _move_line_up(self) -> None:
        """Move a linha atual para cima."""
        if e := self._e():
            e.move_line(-1)

    def _move_line_down(self) -> None:
        """Move a linha atual para baixo."""
        if e := self._e():
            e.move_line(+1)

    def _join_lines(self) -> None:
        """Junta a linha atual com a próxima."""
        if e := self._e():
            e.join_lines()

    def _select_line(self) -> None:
        """Seleciona a linha atual."""
        if e := self._e():
            e.select_line()

    def _transform(self, mode: str) -> None:
        """Transforma o texto selecionado para maiúsculas/minúsculas/título."""
        if e := self._e():
            e.transform_case(mode)

    def _sort_lines(self, reverse: bool) -> None:
        """Ordena as linhas selecionadas ou todo o documento, direto ou reverso."""
        if e := self._e():
            e.sort_lines(reverse)

    def _trim_trailing(self) -> None:
        """Remove espaços em branco no final das linhas."""
        if e := self._e():
            e.trim_trailing_whitespace()

    def _copy_line_up(self) -> None:
        """Copia a linha atual para cima."""
        e = self._e()
        if not e:
            return
        try:
            ln = int(e.text.index(tk.INSERT).split(".")[0])
            line = e.text.get(f"{ln}.0", f"{ln}.end")
            e.text.insert(f"{ln}.0", line + "\n")
        except tk.TclError:
            pass

    def _copy_line_down(self) -> None:
        """Copia a linha atual para baixo."""
        e = self._e()
        if not e:
            return
        try:
            ln = int(e.text.index(tk.INSERT).split(".")[0])
            line = e.text.get(f"{ln}.0", f"{ln}.end")
            e.text.insert(f"{ln}.end", "\n" + line)
        except tk.TclError:
            pass

    def _format_document(self) -> None:
        """Formata o documento atual usando o formatador adequado."""
        e = self._e()
        if e:
            self._fire_hook("before_format", e)
            e.format_document()
            self._fire_hook("after_format", e)

    def _lint_file(self) -> None:
        """Executa o lint no arquivo atual."""
        e = self._e()
        if e:
            self._lint_mgr.schedule(e.path, e.get_content(), e.lang)
            self._status.notify(_t("msg.lint_running"), duration=1500)

    def _fix_all(self) -> None:
        """Aplica correções automáticas para problemas no arquivo atual."""
        e = self._e()
        if e:
            e.fix_all()
            self._status.notify(_t("lsp.auto_fixed"), level="ok")

    def _run_file(self) -> None:
        """Executa o arquivo atual no terminal usando o Code Runner configurado."""
        e = self._e()
        if not e or not e.path:
            self._status.notify(_t("msg.save_before_run"), level="warn")
            return

        cmd_list = self._runner_store.build_command(e.lang, e.path)
        if not cmd_list:
            self._status.notify(
                _t("msg.no_formatter", lang=e.lang)
                + " - configure em Ferramentas > Code Runners",
                level="warn",
            )
            return

        use_shell = (
            self._runner_store.get_runner(e.lang).get("shell", False)
            if self._runner_store.get_runner(e.lang)
            else False
        )

        if use_shell:
            cmd_str = " ".join(cmd_list)
        else:
            cmd_str = " ".join(f'"{c}"' if " " in c else c for c in cmd_list)

        self._show_panel("terminal")
        self._terminal.run_command(cmd_str)

    def _run_selection(self) -> None:
        """Executa o trecho selecionado no terminal."""
        e = self._e()
        if not e:
            return
        try:
            sel = e.text.get(tk.SEL_FIRST, tk.SEL_LAST)
        except tk.TclError:
            sel = e.text.get(tk.INSERT + " linestart", tk.INSERT + " lineend")
        if sel.strip():
            self._show_panel("terminal")
            self._terminal.run_command(sel.strip())

    def _show_find(self) -> None:
        """Exibe a barra de busca local."""
        e = self._e()
        initial = ""
        if e:
            with contextlib.suppress(tk.TclError):
                initial = e.text.get(tk.SEL_FIRST, tk.SEL_LAST)
            if not initial:
                initial = e.get_word_at_cursor()
        self._search_bar.show("find", initial)

    def _show_replace(self) -> None:
        """Exibe a barra de busca e substituição."""
        e = self._e()
        initial = ""
        if e:
            with contextlib.suppress(tk.TclError):
                initial = e.text.get(tk.SEL_FIRST, tk.SEL_LAST)
        self._search_bar.show("replace", initial)

    def _show_find_files(self, initial_query: str = "") -> None:
        """Ativa o painel 'Buscar em Arquivos' com uma consulta inicial."""
        self._activity("search")
        if initial_query and hasattr(self, "_find_files"):
            self._find_files.start_with_query(initial_query)

    def _on_escape(self, _e: Any = None) -> None:
        """Manipula a tecla Esc: esconde a barra de busca se visível."""
        if self._search_bar.is_visible():
            self._search_bar.hide()

    def _goto_line(self) -> None:
        """Abre diálogo para ir a uma linha específica."""
        if e := self._e():
            e.goto_line_dialog()

    def _goto_symbol(self) -> None:
        """Abre diálogo para ir a um símbolo do arquivo."""
        e = self._e()
        if not e:
            return

        ac = getattr(e, "autocomplete", None)
        syms: list[str] = list(getattr(ac, "_doc_symbols", [])) if ac else []
        if not syms:
            self._status.notify(_t("msg.no_symbols"), level="info")
            return
        cmds = [
            (s, f"Symbol: {s}", lambda sym=s: self._jump_to_sym(sym)) for s in syms]
        CommandPalette.open(self._root, self._tmgr, cmds, [])

    def _jump_to_sym(self, name: str) -> None:
        """Salta para a posição de um símbolo no editor."""
        e = self._e()
        if not e:
            return
        content = e.get_content()
        m = re.search(rf"\b{re.escape(name)}\b", content)
        if m:
            idx = e.text.index(f"1.0 + {m.start()}c")
            ln, col = map(int, idx.split("."))
            e.goto_line(ln, col)

    def _goto_bracket(self) -> None:
        """Vai para o parêntese/colchete/chave correspondente."""
        if e := self._e():
            e.hl.match_brackets()

    def _next_problem(self) -> None:
        """Vai para o próximo problema (erro/aviso) no arquivo."""
        e = self._e()
        if not e or not e._diags:
            return
        try:
            cur = int(e.text.index(tk.INSERT).split(".")[0])
        except (tk.TclError, ValueError):
            cur = 0
        for d in sorted(e._diags, key=lambda x: x.line):
            if d.line > cur:
                e.goto_line(d.line, d.col)
                return
        e.goto_line(e._diags[0].line, e._diags[0].col)

    def _prev_problem(self) -> None:
        """Vai para o problema anterior no arquivo."""
        e = self._e()
        if not e or not e._diags:
            return
        try:
            cur = int(e.text.index(tk.INSERT).split(".")[0])
        except (tk.TclError, ValueError):
            cur = 99999
        for d in sorted(e._diags, key=lambda x: x.line, reverse=True):
            if d.line < cur:
                e.goto_line(d.line, d.col)
                return
        last = max(e._diags, key=lambda x: x.line)
        e.goto_line(last.line, last.col)

    def _next_hunk(self) -> None:
        """Vai para o próximo trecho de diff no painel de diff."""
        if self._diff_pane.winfo_ismapped():
            self._diff_pane.next_hunk()

    def _prev_hunk(self) -> None:
        """Vai para o trecho anterior de diff no painel de diff."""
        if self._diff_pane.winfo_ismapped():
            self._diff_pane.prev_hunk()

    def _command_palette(self) -> None:
        """Abre a paleta de comandos."""
        cmds = self._build_command_list()
        recent = [str(p) for p in self._cfg.get_recent_files()]

        def _goto(line: int) -> None:
            if e := self._e():
                e.goto_line(line)

        CommandPalette.open(
            self._root,
            self._tmgr,
            cmds,
            recent,
            goto_line_callback=_goto,
        )

    def _quick_open(self) -> None:
        """Abre a paleta de abertura rápida de arquivos."""
        recent = [str(p) for p in self._cfg.get_recent_files()]
        cmds = [(Path(f).name, f, lambda p=f: self._open_file(p))
                for f in recent]

        def _goto(line: int) -> None:
            if e := self._e():
                e.goto_line(line)

        CommandPalette.open(
            self._root,
            self._tmgr,
            cmds,
            recent,
            goto_line_callback=_goto,
        )

    def _build_command_list(self) -> list[tuple[str, str, Callable]]:
        """Constrói a lista de comandos para a paleta de comandos."""
        base: list[tuple[str, str, Callable]] = [
            (_t("file.new"), "Ctrl+N", self.new_file),
            (_t("file.open"), "Ctrl+O", self.open_file_dialog),
            (_t("file.open_folder"), "", self.open_folder_dialog),
            (_t("file.save"), "Ctrl+S", self.save_file),
            (_t("file.save_all"), "", self.save_all),
            (_t("file.revert"), "", self.revert_file),
            (_t("edit.find"), "Ctrl+F", self._show_find),
            (_t("edit.find_replace"), "Ctrl+H", self._show_replace),
            (_t("edit.find_in_files"), "Ctrl+Shift+F", self._show_find_files),
            (_t("edit.toggle_comment"), "Ctrl+/", self._toggle_comment),
            (_t("edit.duplicate_line"), "Ctrl+D", self._duplicate_line),
            (_t("edit.delete_line"), "Ctrl+Shift+K", self._delete_line),
            (_t("edit.sort_lines"), "", lambda: self._sort_lines(False)),
            (_t("edit.trim_trailing"), "", self._trim_trailing),
            (_t("view.problems"), "Ctrl+Shift+M",
             lambda: self._show_panel("problems")),
            (_t("view.terminal"), "Ctrl+`", lambda: self._show_panel("terminal")),
            (_t("view.word_wrap"), "", self._toggle_word_wrap),
            (_t("view.minimap"), "", self._toggle_minimap),
            (_t("run.run_file"), "F5", self._run_file),
            (_t("run.format_document"), "Shift+Alt+F", self._format_document),
            (_t("run.fix_all"), "", self._fix_all),
            ("Toggle Theme", "", self._tmgr.cycle),
            ("Diff: File vs Saved", "", self._show_file_diff),
            ("Diff: Two Files", "", self._show_diff_two_files),
            (_t("go.to_line"), "Ctrl+G", self._goto_line),
            (_t("go.to_symbol"), "Ctrl+Shift+O", self._goto_symbol),
            ("Zoom In", "Ctrl+=", lambda: self._zoom(+1)),
            ("Zoom Out", "Ctrl+-", lambda: self._zoom(-1)),
            ("Reset Zoom", "Ctrl+0", lambda: self._zoom(0)),
            (_t("view.ui_scale_up"), "Ctrl+Alt+=", lambda: self._ui_scale_step(+1)),
            (_t("view.ui_scale_down"), "Ctrl+Alt+-", lambda: self._ui_scale_step(-1)),
            (_t("view.ui_scale_auto"), "Ctrl+Alt+0", self._ui_scale_auto),
            ("Toggle Full Screen", "F11", self._toggle_fullscreen),
            ("Split Editor", "", self._split_editor),
            ("Welcome Screen", "", self._show_welcome),
            ("Settings", "Ctrl+,", lambda: self._activity("settings")),
            ("Change Language", "", self._pick_language),
            ("About Codis", "", self._about_dialog),
        ]

        for _, (label, handler) in self._command_registry.items():
            base.append((label, "", handler))
        return base

    def _add_plugin_menu_item(
        self, menu_path: str, label: str, handler: Callable
    ) -> None:
        """Adiciona um item de menu a partir de um plugin."""
        from src.ui.menu_bar import MenuDef

        with contextlib.suppress(tk.TclError):
            parts = [p.strip() for p in menu_path.split("/")]

            target_menu: MenuDef | None = None
            if self._plugin_menu_cache:
                if len(parts) <= 1:
                    target_menu = self._plugin_menu_cache
                else:
                    sub_label = parts[1]
                    found = False
                    end_index = self._plugin_menu_cache.index("end")
                    for i in range(end_index + 1 if end_index is not None else 0):
                        try:
                            if (
                                self._plugin_menu_cache.type(i) == "cascade"
                                and self._plugin_menu_cache.entrycget(i, "label")
                                == sub_label
                            ):
                                target_menu = self._plugin_menu_cache.entrycget(
                                    i, "menu"
                                )
                                found = True
                                break
                        except tk.TclError:
                            pass
                    if not found:
                        sub = MenuDef()
                        self._plugin_menu_cache.add_cascade(
                            label=sub_label, menu=sub)
                        target_menu = sub
            if target_menu:
                target_menu.add_command(label=label, command=handler)

    def _update_breadcrumb(self) -> None:
        """Atualiza o breadcrumb com o caminho do arquivo ativo."""
        if not self._cfg.get("breadcrumbs", True):
            return
        e = self._e()
        if e:
            self._breadcrumb.set_path(e.path)

    def _update_title(self) -> None:
        """Atualiza o título da janela com o nome do arquivo e workspace."""
        e = self._e()
        parts = [APP_NAME]
        if e and e.path:
            mod = "● " if e.modified else ""
            parts = [f"{mod}{e.path.name}", " - ", APP_NAME]
        if self._workspace:
            parts.append(f"  [{self._workspace.name}]")
        with contextlib.suppress(tk.TclError):
            self._root.title("".join(parts))

    def _update_status_bar(self) -> None:
        """Atualiza os indicadores da barra de status."""
        e = self._e()
        if e:
            self._status.set_lang(e.lang)
            self._status.set_encoding(e.encoding)
            self._status.set_eol(e.eol)
        self._status.set_indent(
            self._cfg.get("tab_size", TAB_SIZE), not self._cfg.get(
                "use_spaces", True)
        )
        self._status.set_zoom(self._zoom_pct)

    def _ui_scale_step(self, direction: int) -> None:
        """Aumenta (+1) ou diminui (-1) a escala da interface em passos de 10%."""
        pct = self._ui_scaler.step(direction)
        self._cfg.save()
        self._flash_ui_scale(pct)

    def _ui_scale_auto(self) -> None:
        """Volta a escala da interface ao modo automático (ajustado à tela)."""
        pct = self._ui_scaler.reset()
        self._cfg.save()
        self._flash_ui_scale(pct)

    def _flash_ui_scale(self, pct: int) -> None:
        """Mostra o percentual atual na barra de status, se disponível."""
        with contextlib.suppress(AttributeError, tk.TclError):
            self._status.notify(f"UI {pct}%", duration=1500)

    def _zoom(self, delta: int) -> None:
        """Aplica zoom ao editor (delta em passos de 10%)."""
        if delta == 0:
            self._zoom_pct = 100
            for e in self._editors:
                e.zoom(0)
        else:
            new_pct = max(50, min(300, self._zoom_pct + delta * 10))

            steps = (new_pct - self._zoom_pct) // 10
            for e in self._editors:
                for _ in range(abs(steps)):
                    e.zoom(1 if steps > 0 else -1)
            self._zoom_pct = new_pct
        self._status.set_zoom(self._zoom_pct)

    def _toggle_word_wrap(self) -> None:
        """Alterna a quebra de linha no editor."""
        for e in self._editors:
            e.toggle_word_wrap()
        self._cfg.set("word_wrap", not self._cfg.get("word_wrap", False))

    def _toggle_minimap(self) -> None:
        """Alterna a visibilidade do minimapa."""
        for e in self._editors:
            e.toggle_minimap()
        self._cfg.set("minimap", not self._cfg.get("minimap", True))

    def _toggle_breadcrumbs(self) -> None:
        """Alterna a exibição do breadcrumb."""
        show = not self._cfg.get("breadcrumbs", True)
        self._cfg.set("breadcrumbs", show)
        if show:
            self._breadcrumb.pack(fill="x", after=self._tabbar)
        else:
            self._breadcrumb.pack_forget()

    def _set_menubar_position(self, position: str) -> None:
        """Reancora a MenuBar holográfica em ``"top"`` ou ``"bottom"``.

        Os dois checkbuttons do submenu "Posição da Barra de Menus" agem
        como um par mutuamente exclusivo (a MenuDef não modela radiobuttons
        nativamente): ao escolher um lado, o outro é desmarcado e a barra é
        reancorada em tempo real, já trocando a direção em que os dropdowns
        abrem (para baixo no topo, para cima na base).
        """
        position = position if position in ("top", "bottom") else "top"
        menubar = getattr(self, "_menubar", None)
        if menubar is not None:
            with contextlib.suppress(tk.TclError):
                menubar.set_position(position)
        self._cfg.set("menubar_position", position)
        self._cfg.save()

        top_var = getattr(self, "_mbp_top_var", None)
        bottom_var = getattr(self, "_mbp_bottom_var", None)
        if top_var is not None:
            with contextlib.suppress(tk.TclError):
                top_var.set(position == "top")
        if bottom_var is not None:
            with contextlib.suppress(tk.TclError):
                bottom_var.set(position == "bottom")

    def _toggle_fullscreen(self) -> None:
        """Alterna o modo tela cheia."""
        cur = self._root.attributes("-fullscreen")
        self._root.attributes("-fullscreen", not cur)

    def _split_tab_to_right(self, tab_idx: int) -> None:
        """Abre a aba `tab_idx` no lado direito do modo dual, ativando o dual se necessário."""
        if not self._dual_mode:
            self._open_dual_mode()
        if 0 <= tab_idx < len(self._editors):
            path = self._editors[tab_idx].path
            if path:
                for i, e in enumerate(self._dual_right_editors):
                    if e.path == path:
                        self._dual_right_select(i)
                        return
                self._create_dual_right_editor(path)

    def _split_editor(self) -> None:
        """Alterna o modo dual (split side-by-side)."""
        if self._dual_mode:
            self._close_dual_mode()
        else:
            self._open_dual_mode()

    def _open_dual_mode(self) -> None:
        """Ativa o modo dual: divide o editor_container em dois painéis lado a lado.

        Estratégia de layout correta:
        - Esconde o conteúdo atual do _editor_container
        - Cria um PanedWindow que preenche o _editor_container
        - Lado esquerdo: frame que hospeda os editores existentes (re-pack com pai correto)
        - Lado direito: frame com tabbar e editores novos
        - Os EditorPane são sempre criados com o frame correto como pai real (não usa in_=)
        """
        if self._dual_mode:
            return
        t = self._tmgr.theme

        for e in self._editors:
            with contextlib.suppress(tk.TclError):
                e.pack_forget()
        if self._welcome.winfo_ismapped():
            self._welcome.pack_forget()

        self._dual_paned = tk.PanedWindow(
            self._editor_container,
            orient="horizontal",
            bg=t.border,
            sashwidth=5,
            sashrelief="flat",
            sashcursor="sb_h_double_arrow",
        )
        self._dual_paned.pack(fill="both", expand=True)

        self._dual_left_container = tk.Frame(self._dual_paned, bg=t.editor_bg)
        self._dual_right_container = tk.Frame(self._dual_paned, bg=t.editor_bg)

        self._dual_paned.add(self._dual_left_container, stretch="always")
        self._dual_paned.add(self._dual_right_container, stretch="always")

        if 0 <= self._active_idx < len(self._editors):
            old_e = self._editors[self._active_idx]
            old_path = old_e.path
            new_left = EditorPane(
                self._dual_left_container,
                self._tmgr,
                self._cfg,
                self._lint_mgr,
                self._snippets,
                on_change=self._on_editor_change,
                on_cursor=self._on_cursor_change,
                on_title=self._on_editor_title,
                idx=self._active_idx,
            )
            if self._lsp_mgr:
                new_left.set_lsp_manager(self._lsp_mgr)
            new_left.set_goto_callback(self._open_file_at_line)
            if old_path:
                new_left.load(old_path)
            new_left.pack(fill="both", expand=True)
            self._editors[self._active_idx] = new_left
            self._active_pane = new_left
            with contextlib.suppress(tk.TclError):
                old_e.destroy()

        self._dual_mode = True
        self._dual_right_active = False
        self._add_dual_right_tabbar(t)
        self._create_dual_right_editor()

        self._root.bind("<Control-backslash>",
                        lambda _: self._toggle_dual_focus())
        with contextlib.suppress(AttributeError):
            self._status.set_dual_mode(True)
        self._status.notify(
            _t("view.split_editor")
            + " ON - Ctrl+\\ alterna foco  -  clique em ✕ Split para fechar",
            duration=4000,
            level="ok",
        )

    def _add_dual_right_tabbar(self, t: object) -> None:
        """Adiciona tabbar + botão fechar ao container direito do modo dual."""
        from src.ui.tab_bar import TabBar as _TabBar

        top = tk.Frame(
            self._dual_right_container, bg=getattr(t, "tabbar_bg", "#1e1e2e"), height=34
        )
        top.pack(fill="x")
        top.pack_propagate(False)

        close_btn = tk.Label(
            top,
            text=_t("editor.split_close", " ✕ Split "),
            bg=getattr(t, "tabbar_bg", "#1e1e2e"),
            fg=getattr(t, "fg_muted", "#888888"),
            font=(getattr(t, "ui_font", "Segoe UI"), 9),
            cursor="hand2",
        )
        close_btn.pack(side="right", padx=(0, 4))
        _accent = getattr(t, "accent", "#f38ba8")
        _muted = getattr(t, "fg_muted", "#888888")
        close_btn.bind("<Enter>", lambda _: close_btn.config(fg=_accent))
        close_btn.bind("<Leave>", lambda _: close_btn.config(fg=_muted))
        close_btn.bind("<Button-1>", lambda _: self._close_dual_mode())

        tb = _TabBar(
            top,
            self._tmgr,
            on_select=self._dual_right_select,
            on_close=self._dual_right_close,
        )
        tb.pack(fill="both", expand=True, side="left")
        self._dual_right_tabbar = tb

        self._dual_right_editor_frame = tk.Frame(
            self._dual_right_container,
            bg=getattr(t, "editor_bg", "#1e1e1e"),
        )
        self._dual_right_editor_frame.pack(fill="both", expand=True)

    def _create_dual_right_editor(self, path: Path | None = None) -> EditorPane:
        """Cria um EditorPane no lado direito do modo dual.

        O EditorPane é criado com _dual_right_editor_frame como pai real,
        garantindo que o path de widget do Tkinter seja válido e estável.
        """
        if self._dual_right_editor_frame is None:
            raise RuntimeError("_dual_right_editor_frame não inicializado")

        e = EditorPane(
            self._dual_right_editor_frame,
            self._tmgr,
            self._cfg,
            self._lint_mgr,
            self._snippets,
            on_change=self._on_editor_change,
            on_cursor=self._on_cursor_change,
            on_title=lambda title, mod, idx=None: self._on_dual_right_title(
                title, mod),
            idx=0,
        )
        if self._lsp_mgr:
            e.set_lsp_manager(self._lsp_mgr)
        e.set_goto_callback(self._open_file_at_line)

        if path is None:
            current_e = self._e()
            if current_e and current_e.path:
                path = current_e.path

        title = path.name if path else _t("ui.tab_untitled")
        if self._dual_right_tabbar:
            self._dual_right_tabbar.add_tab(title, path)

        idx = len(self._dual_right_editors)

        if idx > 0 and 0 <= self._dual_right_idx < len(self._dual_right_editors):
            with contextlib.suppress(tk.TclError):
                self._dual_right_editors[self._dual_right_idx].pack_forget()

        self._dual_right_editors.append(e)
        self._dual_right_idx = idx

        if self._dual_right_tabbar:
            self._dual_right_tabbar.set_active(idx)

        e.pack(fill="both", expand=True)

        if path:
            e.load(path)
        with contextlib.suppress(tk.TclError):
            e.text.focus_set()
        self._dual_right_active = True
        return e

    def _dual_right_select(self, idx: int) -> None:
        """Seleciona aba no lado direito do modo dual."""
        if (
            not self._dual_right_editors
            or idx < 0
            or idx >= len(self._dual_right_editors)
        ):
            return
        if 0 <= self._dual_right_idx < len(self._dual_right_editors):
            with contextlib.suppress(tk.TclError):
                self._dual_right_editors[self._dual_right_idx].pack_forget()
        self._dual_right_idx = idx
        self._dual_right_editors[idx].pack(fill="both", expand=True)
        with contextlib.suppress(tk.TclError):
            self._dual_right_editors[idx].text.focus_set()
        self._dual_right_active = True
        if self._dual_right_tabbar:
            self._dual_right_tabbar.set_active(idx)

    def _dual_right_close(self, idx: int) -> None:
        """Fecha aba no lado direito do modo dual."""
        if (
            not self._dual_right_editors
            or idx < 0
            or idx >= len(self._dual_right_editors)
        ):
            return
        e = self._dual_right_editors.pop(idx)
        with contextlib.suppress(tk.TclError):
            e.destroy()
        if self._dual_right_tabbar:
            self._dual_right_tabbar.remove_tab(idx)
        if not self._dual_right_editors:
            self._close_dual_mode()
            return
        new_idx = min(idx, len(self._dual_right_editors) - 1)
        self._dual_right_idx = -1
        self._dual_right_select(new_idx)

    def _on_dual_right_title(self, title: str, modified: bool) -> None:
        """Atualiza título da aba ativa no lado direito."""
        if self._dual_right_tabbar and 0 <= self._dual_right_idx < len(
            self._dual_right_editors
        ):
            e = self._dual_right_editors[self._dual_right_idx]
            tab_title = e.path.name if e.path else _t("ui.tab_untitled")
            with contextlib.suppress(tk.TclError):
                self._dual_right_tabbar.update_tab(
                    self._dual_right_idx, tab_title, modified
                )

    def _toggle_dual_focus(self) -> None:
        """Alterna o foco entre lado esquerdo e direito no modo dual."""
        if not self._dual_mode:
            return
        if self._dual_right_active:
            self._dual_right_active = False
            e = self._e()
            if e:
                with contextlib.suppress(tk.TclError):
                    e.text.focus_set()
        else:
            self._dual_right_active = True
            if self._dual_right_editors and 0 <= self._dual_right_idx < len(
                self._dual_right_editors
            ):
                with contextlib.suppress(tk.TclError):
                    self._dual_right_editors[self._dual_right_idx].text.focus_set(
                    )

    def _close_dual_mode(self) -> None:
        """Desativa o modo dual e restaura o layout original."""
        if not self._dual_mode:
            return
        self._dual_mode = False
        self._dual_right_active = False

        for e in self._dual_right_editors:
            with contextlib.suppress(tk.TclError):
                e.destroy()
        self._dual_right_editors.clear()
        self._dual_right_idx = -1
        self._dual_right_tabbar = None
        self._dual_right_editor_frame = None

        if self._dual_paned:
            with contextlib.suppress(tk.TclError):
                self._dual_paned.destroy()
            self._dual_paned = None
        self._dual_left_container = None
        self._dual_right_container = None

        old_path: Path | None = None
        if 0 <= self._active_idx < len(self._editors):
            with contextlib.suppress(tk.TclError):
                old_path = self._editors[self._active_idx].path
                self._editors[self._active_idx].destroy()

        if old_path is not None:
            new_e = EditorPane(
                self._editor_container,
                self._tmgr,
                self._cfg,
                self._lint_mgr,
                self._snippets,
                on_change=self._on_editor_change,
                on_cursor=self._on_cursor_change,
                on_title=self._on_editor_title,
                idx=self._active_idx,
            )
            if self._lsp_mgr:
                new_e.set_lsp_manager(self._lsp_mgr)
            new_e.set_goto_callback(self._open_file_at_line)
            new_e.load(old_path)
            self._editors[self._active_idx] = new_e
            self._active_pane = new_e
            new_e.pack(fill="both", expand=True)
            with contextlib.suppress(tk.TclError):
                new_e.text.focus_set()
        elif not self._editors:
            self._welcome.pack(fill="both", expand=True)

        with contextlib.suppress(tk.TclError):
            self._root.unbind("<Control-backslash>")
        with contextlib.suppress(AttributeError):
            self._status.set_dual_mode(False)
        self._status.notify(_t("view.split_editor") +
                            " OFF", duration=2000, level="ok")

    def _dual_open_file(self, path: Path) -> None:
        """Abre um arquivo no lado direito (ou esquerdo) dependendo do foco."""
        if self._dual_mode and self._dual_right_active:
            for i, e in enumerate(self._dual_right_editors):
                if e.path == path:
                    self._dual_right_select(i)
                    return
            self._create_dual_right_editor(path)
        else:
            self._open_file(path)

    def _show_file_diff(self) -> None:
        """Mostra o diff entre o arquivo atual e a versão salva."""
        e = self._e()
        if not e:
            return
        self._diff_pane.load(
            e._last_saved_content,
            e.get_content(),
            f"{e.path.name if e.path else 'untitled'} (saved)",
            f"{e.path.name if e.path else 'untitled'} (current)",
        )
        self._show_panel("diff")

    def _show_diff_two_files(self) -> None:
        """Abre diálogo para selecionar dois arquivos e mostrar o diff."""
        paths = filedialog.askopenfilenames(
            title=_t("hc.app.select_2_files_to_diff"),
            parent=self._root,
        )
        if len(paths) >= 2:
            try:
                a = Path(paths[0]).read_text(
                    encoding="utf-8", errors="replace")
                b = Path(paths[1]).read_text(
                    encoding="utf-8", errors="replace")
                self._diff_pane.show(
                    a,
                    b,
                    Path(paths[0]).name,
                    Path(paths[1]).name,
                )
                self._show_panel("diff")
            except (OSError, ValueError) as exc:
                messagebox.showerror(_t("err.diff"), str(exc))

    def _new_terminal(self) -> None:
        """Abre um novo terminal."""
        self._terminal.new_shell()
        self._show_panel("terminal")

    def _clear_terminal(self) -> None:
        """Limpa o terminal ativo."""
        self._terminal.clear_active()

    def _kill_terminal(self) -> None:
        """Mata o terminal ativo."""
        self._terminal.kill_active()

    def _open_settings_window(self) -> None:
        """Abre a janela flutuante de configurações (Ctrl+,).

        Singleton robusto: mantém referência à janela e verifica winfo_exists()
        para evitar múltiplas instâncias mesmo quando o evento <Destroy> não
        dispara corretamente (ex: fechamento pelo botão engrenagem).
        """
        import contextlib

        existing: tk.Toplevel | None = getattr(self, "_settings_win_ref", None)
        if existing is not None:
            with contextlib.suppress(Exception):
                if existing.winfo_exists():
                    existing.lift()
                    existing.focus_set()
                    return
            self._settings_win_ref = None

        from src.ui.settings_window import SettingsWindow

        win = SettingsWindow(
            self._root,
            self._tmgr,
            self._cfg,
            on_change=self._on_setting_change,
            on_show_settings_panel=self._show_settings_panel,
        )
        self._settings_win_ref = win
        win._app_ref = self

        def _on_close() -> None:
            self._settings_win_ref = None

        win.protocol("WM_DELETE_WINDOW", lambda: (win.destroy(), _on_close()))
        win.bind("<Destroy>", lambda e: _on_close()
                 if e.widget is win else None)

    def _open_settings_window_cursor(self) -> None:
        """Abre a janela flutuante de configurações diretamente na aba Cursor."""
        import contextlib

        existing: tk.Toplevel | None = getattr(self, "_settings_win_ref", None)
        if existing is not None:
            with contextlib.suppress(Exception):
                if existing.winfo_exists():
                    existing.lift()
                    existing.focus_set()
                    existing._show_category("cursor")
                    return
            self._settings_win_ref = None

        from src.ui.settings_window import SettingsWindow

        win = SettingsWindow(
            self._root,
            self._tmgr,
            self._cfg,
            on_change=self._on_setting_change,
            on_show_settings_panel=self._show_settings_panel,
        )
        self._settings_win_ref = win
        win._app_ref = self
        win._show_category("cursor")

        def _on_close() -> None:
            self._settings_win_ref = None

        win.protocol("WM_DELETE_WINDOW", lambda: (win.destroy(), _on_close()))
        win.bind("<Destroy>", lambda e: _on_close()
                 if e.widget is win else None)

    def _run_reset(self, mode: str) -> None:
        """Executa um reset direto sem abrir o wizard completo.

        Args:
            mode: ``"soft"`` | ``"wizard"`` | ``"full"``
        """
        import shutil

        from src.constants import (
            CONFIG_DIR,
            DEFAULT_CONFIG,
        )

        labels = {
            "soft": "Soft Reset",
            "wizard": "Soft Reset + Wizard",
            "full": "Limpeza Completa",
        }
        label = labels.get(mode, mode)

        msg = {
            "soft": _t(
                "reset.quick_soft_msg",
                "Isso irá redefinir TODAS as configurações para os valores padrão.\n"
                "Plugins, sessão e arquivos de projeto são mantidos.\n\nContinuar?",
            ),
            "wizard": _t(
                "reset.quick_wizard_msg",
                "Redefinirá as configurações E ativará o assistente na próxima abertura.\n\nContinuar?",
            ),
            "full": _t(
                "reset.quick_full_msg",
                "Isso irá remover configurações, cache de sessão e histórico recente.\n"
                "Esta ação NÃO pode ser desfeita.\n\nContinuar?",
            ),
        }.get(mode, "Continuar?")

        if not tk.messagebox.askyesno(label, msg, parent=self._root, icon="warning"):
            return

        try:
            self._cfg._data = dict(DEFAULT_CONFIG)

            if mode == "wizard":
                self._cfg.set("_setup_wizard_pending", True)

            if mode in ("wizard", "full"):
                self._cfg.set("recent_files", [])
                self._cfg.set("session_files", [])
                self._cfg.set("last_workspace", "")

            if mode == "full":
                extras = [
                    CONFIG_DIR / "sessions",
                    CONFIG_DIR / "cache",
                    CONFIG_DIR / "history.json",
                    CONFIG_DIR / "snippets_custom.json",
                ]
                for p in extras:
                    target = Path(str(p))
                    try:
                        if target.is_dir():
                            shutil.rmtree(str(target))
                        elif target.exists():
                            target.unlink()
                    except OSError:
                        pass

            self._cfg.save()
            tk.messagebox.showinfo(
                label,
                _t(
                    "reset.quick_done_msg",
                    "{label} concluído.\nO Codis será reiniciado para aplicar as mudanças.",
                    label=label,
                ),
                parent=self._root,
            )
            self._root.after(200, self._restart_ui)

        except Exception as exc:
            logger.error("Erro no reset rápido (%s): %s",
                         mode, exc, exc_info=True)
            tk.messagebox.showerror(
                _t("reset.err_title", "Erro no Reset"),
                _t("reset.err_msg",
                   "Não foi possível completar o reset:\n{e}", e=exc),
                parent=self._root,
            )

    def _reset_single_key(self) -> None:
        """Abre diálogo para redefinir uma única chave de configuração ao padrão."""
        from src.constants import DEFAULT_CONFIG

        dlg = tk.Toplevel(self._root)
        t = self._tmgr.theme
        dlg.title(_t("reset.single_key_title",
                  "Redefinir Configuração Específica"))
        dlg.geometry("460x320")
        dlg.configure(bg=t.floating_bg)
        dlg.transient(self._root)
        from src.ui.grab_safe import safe_grab_set

        safe_grab_set(dlg)

        tk.Label(
            dlg,
            text=_t("reset.single_key_title",
                    "Redefinir Configuração Específica"),
            bg=t.floating_bg,
            fg=t.fg,
            font=("Segoe UI", 12, "bold"),
            pady=12,
            anchor="w",
            padx=16,
        ).pack(fill="x")
        tk.Label(
            dlg,
            text=_t(
                "reset.single_key_label",
                "Digite o nome da chave de configuração a ser redefinida:",
            ),
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=("Segoe UI", 9),
            anchor="w",
            padx=16,
        ).pack(fill="x")

        entry_var = tk.StringVar()
        entry = tk.Entry(
            dlg,
            textvariable=entry_var,
            bg=t.input_bg,
            fg=t.fg,
            insertbackground=t.fg,
            font=("Consolas", 10),
            relief="flat",
        )
        entry.pack(fill="x", padx=16, pady=(8, 4))
        entry.focus_set()

        keys_text = tk.Text(
            dlg,
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=("Consolas", 8),
            height=8,
            relief="flat",
            state="normal",
        )
        keys_text.pack(fill="both", expand=True, padx=16, pady=(0, 4))
        keys_text.insert("1.0", "  ".join(sorted(DEFAULT_CONFIG.keys())))
        keys_text.config(state="disabled")

        result_lbl = tk.Label(
            dlg, text="", bg=t.floating_bg, fg=t.fg_muted, font=("Segoe UI", 8), padx=16
        )
        result_lbl.pack(fill="x")

        def _do_reset():
            key = entry_var.get().strip()
            if key not in DEFAULT_CONFIG:
                result_lbl.config(
                    text=_t(
                        "reset.key_not_found", "Chave '{k}' não encontrada.", k=key
                    ),
                    fg=t.err_fg,
                )
                return
            self._cfg.set(key, DEFAULT_CONFIG[key])
            self._cfg.save()
            result_lbl.config(
                text=_t(
                    "reset.key_reset_ok",
                    "'{k}' redefinida para: {v}",
                    k=key,
                    v=repr(DEFAULT_CONFIG[key]),
                ),
                fg=t.accent,
            )

        btn_frame = tk.Frame(dlg, bg=t.floating_bg)
        btn_frame.pack(fill="x", padx=16, pady=8)
        for text, cmd, is_primary in (
            (_t("reset.btn_cancel", "Cancelar"), dlg.destroy, False),
            (_t("reset.btn_reset", "Redefinir"), _do_reset, True),
        ):
            bg = t.accent if is_primary else t.btn_bg
            b = tk.Label(
                btn_frame,
                text=text,
                bg=bg,
                fg="#ffffff" if is_primary else t.fg,
                font=("Segoe UI", 9, "bold" if is_primary else "normal"),
                cursor="hand2",
                padx=14,
                pady=5,
                relief="flat",
            )
            b.pack(side="right", padx=4)
            b.bind("<Button-1>", lambda _, c=cmd: c())

        entry.bind("<Return>", lambda _: _do_reset())

    def _reset_session_only(self) -> None:
        """Limpa apenas a sessão, recentes e histórico, sem alterar outras configs."""
        if not tk.messagebox.askyesno(
            _t("reset.session_title", "Limpar Sessão"),
            _t(
                "reset.session_msg",
                "Isso irá limpar:\n• Abas recentes\n• Arquivos recentes\n• Histórico de workspace\n\nContinuar?",
            ),
            parent=self._root,
            icon="warning",
        ):
            return

        import contextlib as _cl

        for key in (
            "recent_files",
            "session_files",
            "last_workspace",
            "recent_folders",
        ):
            with _cl.suppress(Exception):
                self._cfg.set(key, [] if key != "last_workspace" else "")

        self._cfg.save()
        tk.messagebox.showinfo(
            _t("reset.session_done_title", "Sessão Limpa"),
            _t(
                "reset.session_done_msg", "Sessão e histórico foram limpos com sucesso."
            ),
            parent=self._root,
        )
