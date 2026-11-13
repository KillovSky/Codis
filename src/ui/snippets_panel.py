#!/usr/bin/env python3
"""Codis - SnippetsPanel (sidebar panel para snippets).

Features:
- i18n via _t()
- Language selector as Listbox (no Combobox)
- Prefix/Code split view with proper separator + scrollbars
- Inline edit dialog (edit existing snippets)
- Zero threading bugs
"""

from __future__ import annotations

import contextlib
import logging
import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox
from typing import TYPE_CHECKING

from src.constants import FONT_MONO, FONT_UI
from src.i18n import _t
from src.theme.manager import ThemeManager

if TYPE_CHECKING:
    from src.editor.pane import EditorPane
from src.editor.snippets import SnippetEngine
from src.ui.grab_safe import safe_grab_set

logger = logging.getLogger("Codis")


class SnippetsPanel(tk.Frame):
    """Painel lateral para navegar, pesquisar, visualizar e inserir snippets."""

    def __init__(
        self,
        parent: tk.Widget,
        tmgr: ThemeManager,
        snippets: SnippetEngine,
        get_editor: Callable[[], EditorPane | None],
    ) -> None:
        """Cria o painel de gerenciamento de snippets.

        Args:
            parent: Widget pai Tkinter.
            config: Instância de ConfigManager.
            on_insert: Callback(snippet_body) para inserir snippet no editor.
        """
        t = tmgr.theme
        super().__init__(parent, bg=t.sidebar_bg)
        self._tmgr = tmgr
        self._snippets = snippets
        self._get_editor = get_editor
        self._current_lang = "python"
        self._filtered_snips: list = []
        self._prefix_lbl: tk.Label | None = None
        self._desc_lbl: tk.Label | None = None
        self._preview: tk.Text | None = None
        self._build()

    def _build(self) -> None:
        """Constrói os widgets e layout interno do componente."""
        t = self._tmgr.theme

        for child in self.winfo_children():
            with contextlib.suppress(tk.TclError):
                child.destroy()

        hdr = tk.Frame(self, bg=t.activity_bg)
        hdr.pack(fill="x")
        tk.Label(
            hdr,
            text=f"  \u2702\ufe0f  {_t('snippets.title')}",
            bg=t.activity_bg,
            fg=t.fg,
            font=(FONT_UI, 10, "bold"),
            anchor="w",
            height=2,
        ).pack(fill="x")

        lang_outer = tk.Frame(self, bg=t.sidebar_bg)
        lang_outer.pack(fill="x", padx=6, pady=(6, 0))

        tk.Label(
            lang_outer,
            text=_t("snippets.language"),
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8, "bold"),
        ).pack(anchor="w", padx=2)

        lang_list_frame = tk.Frame(lang_outer, bg=t.sidebar_bg)
        lang_list_frame.pack(fill="x", pady=(2, 0))

        lang_vsb = tk.Scrollbar(
            lang_list_frame,
            orient="vertical",
            bg=t.scroll_thumb,
            troughcolor=t.scroll_bg,
            relief="flat",
            bd=0,
            width=4,
        )
        lang_vsb.pack(side="right", fill="y")

        self._lang_lb = tk.Listbox(
            lang_list_frame,
            bg=t.editor_bg,
            fg=t.fg,
            selectbackground=t.accent,
            selectforeground="#fff",
            relief="flat",
            bd=0,
            font=(FONT_UI, 9),
            activestyle="none",
            highlightthickness=1,
            highlightbackground=t.border,
            yscrollcommand=lang_vsb.set,
            height=5,
            exportselection=False,
        )
        lang_vsb.config(command=self._lang_lb.yview)
        self._lang_lb.pack(fill="x", expand=True)
        self._lang_lb.bind("<<ListboxSelect>>", self._on_lang_select)

        self._populate_langs()

        search_row = tk.Frame(self, bg=t.sidebar_bg)
        search_row.pack(fill="x", padx=6, pady=(6, 2))
        tk.Label(
            search_row,
            text="\U0001f50d",
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
        ).pack(side="left")
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._refresh_list())
        tk.Entry(
            search_row,
            textvariable=self._search_var,
            bg=t.input_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=t.border,
            font=(FONT_UI, 9),
        ).pack(side="left", fill="x", expand=True, padx=(4, 0))

        snip_frame = tk.Frame(self, bg=t.sidebar_bg)
        snip_frame.pack(fill="x", padx=6, pady=(2, 0))

        snip_vsb = tk.Scrollbar(
            snip_frame,
            orient="vertical",
            bg=t.scroll_thumb,
            troughcolor=t.scroll_bg,
            relief="flat",
            bd=0,
            width=4,
        )
        snip_vsb.pack(side="right", fill="y")

        self._listbox = tk.Listbox(
            snip_frame,
            bg=t.sidebar_bg,
            fg=t.fg,
            selectbackground=t.sel_bg,
            selectforeground=t.sel_fg,
            relief="flat",
            bd=0,
            font=(FONT_UI, 9),
            activestyle="none",
            highlightthickness=1,
            highlightbackground=t.border,
            yscrollcommand=snip_vsb.set,
            height=7,
            exportselection=False,
        )
        snip_vsb.config(command=self._listbox.yview)
        self._listbox.pack(fill="both", expand=True)
        self._listbox.bind("<<ListboxSelect>>", self._on_select)
        self._listbox.bind("<Double-Button-1>", lambda _: self._insert())
        self._listbox.bind("<Return>", lambda _: self._insert())

        tk.Frame(self, bg=t.border, height=1).pack(
            fill="x", padx=6, pady=(8, 4))

        self._build_detail(t)

        btn_row = tk.Frame(self, bg=t.sidebar_bg)
        btn_row.pack(fill="x", padx=6, pady=6)

        self._make_btn(btn_row, _t("snippets.insert"), self._insert, primary=True).pack(
            side="left", padx=(0, 3)
        )
        self._make_btn(btn_row, _t("snippets.new"), self._new_snippet).pack(
            side="left", padx=(0, 3)
        )
        self._make_btn(btn_row, _t("snippets.edit"), self._edit_snippet).pack(
            side="left", padx=(0, 3)
        )
        self._make_btn(btn_row, _t("snippets.delete"), self._delete_snippet).pack(
            side="left"
        )

        self._refresh_list()

    def _build_detail(self, t) -> None:
        """Constrói a seção de detalhes do snippet (prefix, descrição, preview)."""
        detail = tk.Frame(self, bg=t.sidebar_bg)
        detail.pack(fill="both", expand=True, padx=6, pady=0)

        prow = tk.Frame(detail, bg=t.sidebar_bg)
        prow.pack(fill="x", pady=(0, 2))
        tk.Label(
            prow,
            text=_t("snippets.prefix") + ":",
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8, "bold"),
            width=11,
            anchor="w",
        ).pack(side="left")
        self._prefix_lbl = tk.Label(
            prow,
            text="",
            bg=t.editor_bg,
            fg=t.t_func,
            font=(FONT_MONO, 9),
            anchor="w",
            relief="flat",
            padx=4,
            pady=1,
        )
        self._prefix_lbl.pack(side="left", fill="x", expand=True)

        drow = tk.Frame(detail, bg=t.sidebar_bg)
        drow.pack(fill="x", pady=(0, 4))
        tk.Label(
            drow,
            text=_t("snippets.description") + ":",
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8, "bold"),
            width=11,
            anchor="w",
        ).pack(side="left")
        self._desc_lbl = tk.Label(
            drow, text="", bg=t.sidebar_bg, fg=t.fg_muted, font=(FONT_UI, 8), anchor="w"
        )
        self._desc_lbl.pack(side="left", fill="x", expand=True)

        tk.Label(
            detail,
            text=_t("snippets.body") + ":",
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8, "bold"),
            anchor="w",
        ).pack(fill="x")

        code_frame = tk.Frame(detail, bg=t.sidebar_bg)
        code_frame.pack(fill="both", expand=True)

        code_vsb = tk.Scrollbar(
            code_frame,
            orient="vertical",
            bg=t.scroll_thumb,
            troughcolor=t.scroll_bg,
            relief="flat",
            bd=0,
            width=4,
        )
        code_vsb.pack(side="right", fill="y")
        code_hsb = tk.Scrollbar(
            code_frame,
            orient="horizontal",
            bg=t.scroll_thumb,
            troughcolor=t.scroll_bg,
            relief="flat",
            bd=0,
            width=4,
        )
        code_hsb.pack(side="bottom", fill="x")

        self._preview = tk.Text(
            code_frame,
            bg=t.editor_bg,
            fg=t.fg,
            font=(FONT_MONO, 9),
            relief="flat",
            bd=0,
            height=6,
            state="disabled",
            padx=6,
            pady=4,
            wrap="none",
            highlightthickness=1,
            highlightbackground=t.border,
            yscrollcommand=code_vsb.set,
            xscrollcommand=code_hsb.set,
        )
        code_vsb.config(command=self._preview.yview)
        code_hsb.config(command=self._preview.xview)
        self._preview.pack(fill="both", expand=True)

    def _make_btn(self, parent, text, cmd, primary=False):
        """Executa 'make btn' e retorna o resultado.

        Args:
            parent: Widget pai Tkinter.
            text: Dado textual a processar.
            cmd: Parâmetro da operação.
            primary: Parâmetro da operação.
        """
        t = self._tmgr.theme
        b = tk.Label(
            parent,
            text=text,
            bg=t.btn_primary if primary else t.btn_bg,
            fg="#000" if primary else t.fg,
            font=(FONT_UI, 8),
            padx=7,
            pady=3,
            cursor="hand2",
            relief="flat",
        )
        b.bind("<Button-1>", lambda _, c=cmd: c())
        b.bind(
            "<Enter>",
            lambda _, w=b, p=primary: w.config(
                bg=t.accent if p else t.btn_hover),
        )
        b.bind(
            "<Leave>",
            lambda _, w=b, p=primary: w.config(
                bg=t.btn_primary if p else t.btn_bg),
        )
        return b

    def _populate_langs(self) -> None:
        """Preenche o combo de linguagens com as opções disponíveis."""
        langs = sorted(getattr(self._snippets, "_snippets", {}).keys()) or [
            "python"]
        self._lang_lb.delete(0, "end")
        sel_idx = 0
        for i, lang in enumerate(langs):
            self._lang_lb.insert("end", f"  {lang}")
            if lang == self._current_lang:
                sel_idx = i
        self._lang_lb.selection_set(sel_idx)
        self._lang_lb.see(sel_idx)

    def _selected_lang(self) -> str:
        """Retorna a linguagem atualmente selecionada no combo."""
        sel = self._lang_lb.curselection()
        if not sel:
            return self._current_lang
        return self._lang_lb.get(sel[0]).strip()

    def _on_lang_select(self, _e=None) -> None:
        """Trata o evento 'lang select'.

        Args:
            _e: Parâmetro da operação.
        """
        self._current_lang = self._selected_lang()
        self._refresh_list()

    def _refresh_list(self) -> None:
        """Recarrega e exibe os snippets da linguagem selecionada."""
        lang = self._selected_lang()
        q = self._search_var.get().lower()

        self._filtered_snips = [
            s
            for s in self._snippets.get_for_lang(lang)
            if not q
            or q in s.prefix.lower()
            or q in (s.description or "").lower()
            or q in s.body.lower()
        ]

        self._listbox.delete(0, "end")
        if not self._filtered_snips:
            self._listbox.insert("end", f"  {_t('snippets.no_snippets')}")
        else:
            for snip in self._filtered_snips:
                desc = f"  \u2014  {snip.description}" if snip.description else ""
                self._listbox.insert("end", f"  {snip.prefix}{desc}")

        self._clear_preview()

    def _on_select(self, _e=None) -> None:
        """Exibe o corpo do snippet selecionado.

        Args:
            e: Evento de seleção.
        """
        snip = self._get_selected_snip()
        if snip is None:
            return
        self._prefix_lbl.config(text=snip.prefix)
        self._desc_lbl.config(text=snip.description or "")
        self._preview.config(state="normal")
        self._preview.delete("1.0", "end")
        self._preview.insert("1.0", snip.body)
        self._preview.config(state="disabled")

    def _clear_preview(self) -> None:
        """Limpa o campo de preview do corpo do snippet."""
        self._prefix_lbl.config(text="")
        self._desc_lbl.config(text="")
        self._preview.config(state="normal")
        self._preview.delete("1.0", "end")
        self._preview.config(state="disabled")

    def _get_selected_snip(self):
        """Obtém e retorna 'selected snip'."""
        sel = self._listbox.curselection()
        if not sel or not self._filtered_snips:
            return None
        idx = sel[0]
        if idx >= len(self._filtered_snips):
            return None
        return self._filtered_snips[idx]

    def _insert(self) -> None:
        """Insere elemento na posição indicada."""
        snip = self._get_selected_snip()
        if snip is None:
            return
        e = self._get_editor()
        if e:
            with contextlib.suppress(tk.TclError):
                self._snippets.expand(snip, e.text, e.path)

    def _new_snippet(self) -> None:
        """Abre o diálogo para criar um novo snippet."""
        self._open_snippet_dialog(None)

    def _edit_snippet(self) -> None:
        """Abre o diálogo para editar o snippet selecionado."""
        snip = self._get_selected_snip()
        if snip is None:
            return
        self._open_snippet_dialog(snip)

    def _delete_snippet(self) -> None:
        """Remove o snippet selecionado após confirmação do usuário."""
        snip = self._get_selected_snip()
        if snip is None:
            return
        msg = _t("snippets.confirm_delete", prefix=snip.prefix)
        if messagebox.askyesno(_t("snippets.delete"), msg, parent=self):
            self._snippets.remove(self._selected_lang(), snip.prefix)
            self._refresh_list()

    def _open_snippet_dialog(self, snip) -> None:
        """Abre o formulário de criação/edição de snippet."""
        t = self._tmgr.theme
        lang = self._selected_lang()
        edit = snip is not None
        title = _t("snippets.edit_title") if edit else _t("snippets.new_title")

        dlg = tk.Toplevel(self)
        dlg.title(title)
        dlg.configure(bg=t.floating_bg)
        dlg.transient(self.winfo_toplevel())
        safe_grab_set(dlg)
        dlg.resizable(True, True)
        dlg.minsize(340, 300)

        tk.Label(
            dlg, text=title, bg=t.floating_bg, fg=t.fg, font=(
                FONT_UI, 11, "bold")
        ).pack(padx=16, pady=(12, 6))

        def labeled_entry(label_key, init=""):
            """Cria um campo de entrada com rótulo traduzido e valor inicial opcional."""
            row = tk.Frame(dlg, bg=t.floating_bg)
            row.pack(fill="x", padx=16, pady=3)
            tk.Label(
                row,
                text=_t(label_key) + ":",
                bg=t.floating_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 9),
                width=12,
                anchor="w",
            ).pack(side="left")
            e = tk.Entry(
                row,
                bg=t.input_bg,
                fg=t.fg,
                insertbackground=t.cursor,
                relief="flat",
                bd=0,
                highlightthickness=1,
                highlightbackground=t.border,
                font=(FONT_UI, 9),
            )
            e.pack(side="left", fill="x", expand=True)
            if init:
                e.insert(0, init)
            return e

        prefix_e = labeled_entry(
            "snippets.prefix", init=snip.prefix if edit else "")
        desc_e = labeled_entry(
            "snippets.description", init=(snip.description or "") if edit else ""
        )

        tk.Label(
            dlg,
            text=_t("snippets.body") + ":",
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
            anchor="w",
        ).pack(anchor="w", padx=16)

        body_frame = tk.Frame(dlg, bg=t.floating_bg)
        body_frame.pack(fill="both", expand=True, padx=16, pady=(2, 6))

        body_vsb = tk.Scrollbar(
            body_frame,
            orient="vertical",
            bg=t.scroll_thumb,
            troughcolor=t.scroll_bg,
            relief="flat",
            bd=0,
            width=4,
        )
        body_vsb.pack(side="right", fill="y")

        body_t = tk.Text(
            body_frame,
            bg=t.input_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=t.border,
            font=(FONT_MONO, 9),
            width=38,
            height=8,
            wrap="none",
            yscrollcommand=body_vsb.set,
        )
        body_vsb.config(command=body_t.yview)
        body_t.pack(fill="both", expand=True)

        if edit and snip.body:
            body_t.insert("1.0", snip.body)

        btn_row = tk.Frame(dlg, bg=t.floating_bg)
        btn_row.pack(pady=(0, 10))

        def _save():
            prefix = prefix_e.get().strip()
            desc = desc_e.get().strip()
            body = body_t.get("1.0", "end-1c")
            if not prefix or not body:
                return
            if edit:
                with contextlib.suppress(tk.TclError):
                    self._snippets.remove(lang, snip.prefix)
            self._snippets.add(lang, prefix, body, desc)
            self._populate_langs()
            self._refresh_list()
            dlg.destroy()

        for text, cmd, is_primary in (
            (_t("snippets.save"), _save, True),
            (_t("snippets.cancel"), dlg.destroy, False),
        ):
            b = tk.Label(
                btn_row,
                text=text,
                bg=t.btn_primary if is_primary else t.btn_bg,
                fg="#000" if is_primary else t.fg,
                font=(FONT_UI, 9),
                padx=14,
                pady=4,
                cursor="hand2",
            )
            b.pack(side="left", padx=5)
            b.bind("<Button-1>", lambda _, c=cmd: c())

        prefix_e.focus_set()
        dlg.bind("<Escape>", lambda _: dlg.destroy())

    def notify_active_lang(self, lang: str) -> None:
        """Called by CodisApp when active editor language changes."""
        self._current_lang = lang
        self._populate_langs()
        self._refresh_list()

    def apply_theme(self, t) -> None:
        """Aplica o tema ao painel reconstruindo os widgets."""
        for w in self.winfo_children():
            with contextlib.suppress(tk.TclError):
                w.destroy()
        self.config(bg=t.sidebar_bg)
        self._build()
