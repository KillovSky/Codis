"""Painel de problemas (erros e avisos) do Codis.

Provê ``ProblemsPanel``, que agrega e exibe diagnósticos LSP e de linters
em uma lista navegável, com filtragem por severidade e navegação direta
até a linha do problema no editor.
"""

from __future__ import annotations

import contextlib
import logging
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import ttk
from typing import Any

from src.constants import FONT_UI
from src.i18n import _t
from src.lint.manager import Diag
from src.theme.base import Theme
from src.theme.manager import ThemeManager

logger = logging.getLogger("Codis")


class ProblemsPanel(tk.Frame):
    """
    Painel de diagnósticos:
    - Agrupamento por arquivo com contadores
    - Filtro de severidade com botões visuais pressionados/soltos
    - Filtro textual por mensagem ou regra
    - Clique/duplo-clique para navegar ao erro no editor
    - Fix Action executável direto do painel (quando disponível)
    - Tooltip com mensagem completa ao hover
    - Contador por severidade na toolbar em tempo real
    - apply_theme correto (reconfigurar Treeview + tags + toolbar)
    """

    _SEV_META = {
        1: ("✖", "error", "err_fg"),
        2: ("⚠", "warning", "warn_fg"),
        3: ("ℹ", "info", "info_fg"),
        4: ("-", "hint", "hint_fg"),
    }

    def __init__(
        self,
        parent: tk.Widget,
        tmgr: ThemeManager,
        on_goto: Callable[[int, int], None],
        on_apply_fix: Callable[[str, Diag], None] | None = None,
        on_fix_all: Callable[[str, list[Diag]], None] | None = None,
    ) -> None:
        """Cria o painel de problemas (erros e avisos do lint/LSP).

        Args:
            parent: Widget pai Tkinter.
            on_goto: Callback(path, line) para navegar até o problema.
            on_apply_fix: Callback(file_path, diag) para aplicar um fix no editor.
            on_fix_all: Callback(file_path, diags) para aplicar todos os fixes no editor.
        """
        t = tmgr.theme
        super().__init__(parent, bg=t.panel_bg)
        self._tmgr = tmgr
        self._on_goto = on_goto
        self._on_apply_fix = on_apply_fix
        self._on_fix_all = on_fix_all
        self._diags: list[Diag] = []
        self._file = ""
        self._filter: set = {1, 2, 3, 4}
        self._sev_btns: dict[int, tk.Label] = {}
        self._tooltip_win: tk.Toplevel | None = None

        self._diags_by_file: dict[str, list[Diag]] = {}
        self._open_files_only: bool = True
        self._open_paths: set = set()
        self._build()

    def _build(self) -> None:
        """Constrói os widgets e layout interno do componente."""
        t = self._tmgr.theme

        tb = tk.Frame(self, bg=t.panel_bg, height=32)
        tb.pack(fill="x")
        tb.pack_propagate(False)

        for sev, (icon, _tag, color_attr) in self._SEV_META.items():
            _color = getattr(t, color_attr, t.fg)
            btn = tk.Label(
                tb,
                text=f" {icon} 0 ",
                bg=t.btn_active,
                fg=_color,
                font=(FONT_UI, 9, "bold"),
                cursor="hand2",
                relief="flat",
                bd=0,
                padx=4,
                pady=2,
            )
            btn.pack(side="left", padx=1, pady=4)
            btn.bind("<Button-1>", lambda _, s=sev: self._toggle_sev(s))
            self._sev_btns[sev] = btn

        tk.Frame(tb, bg=t.border, width=1).pack(
            side="left", fill="y", padx=6, pady=4)

        self._filter_var = tk.StringVar()
        self._filter_var.trace_add("write", lambda *_: self._render())
        fe = tk.Entry(
            tb,
            textvariable=self._filter_var,
            bg=t.input_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            bd=0,
            width=20,
            font=(FONT_UI, 9),
            highlightthickness=1,
            highlightbackground=t.border,
            highlightcolor=t.accent,
        )
        fe.pack(side="left", padx=2, pady=4)

        clr = tk.Label(
            tb,
            text="✕",
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
            cursor="hand2",
            padx=4,
        )
        clr.pack(side="left")
        clr.bind("<Button-1>", lambda _: self._filter_var.set(""))

        self._group_var = tk.BooleanVar(value=True)
        group_cb = tk.Checkbutton(
            tb,
            text=_t("problems.group"),
            variable=self._group_var,
            command=self._render,
            bg=t.panel_bg,
            fg=t.fg_muted,
            activebackground=t.panel_bg,
            selectcolor=t.sel_bg,
            font=(FONT_UI, 8),
        )
        group_cb.pack(side="right", padx=8)

        self._open_only_var = tk.BooleanVar(value=True)
        open_cb = tk.Checkbutton(
            tb,
            text=_t("problems.open_only"),
            variable=self._open_only_var,
            command=self._on_open_only_toggle,
            bg=t.panel_bg,
            fg=t.fg_muted,
            activebackground=t.panel_bg,
            selectcolor=t.sel_bg,
            font=(FONT_UI, 8),
        )
        open_cb.pack(side="right", padx=2)

        tk.Frame(self, bg=t.border, height=1).pack(fill="x")

        style = ttk.Style()
        style.configure(
            "Problems.Treeview",
            background=t.panel_bg,
            foreground=t.fg,
            fieldbackground=t.panel_bg,
            borderwidth=0,
            rowheight=20,
            font=(FONT_UI, 9),
        )
        style.map(
            "Problems.Treeview",
            background=[("selected", t.sel_bg)],
            foreground=[("selected", t.sel_fg)],
        )
        style.configure(
            "Problems.Treeview.Heading",
            background=t.panel_bg,
            foreground=t.fg_muted,
            relief="flat",
            font=(FONT_UI, 9, "bold"),
            borderwidth=0,
            padding=(4, 4),
        )
        style.map(
            "Problems.Treeview.Heading",
            background=[("active", t.btn_hover)],
            foreground=[("active", t.fg)],
        )

        list_frame = tk.Frame(self, bg=t.panel_bg)
        list_frame.pack(fill="both", expand=True)

        self._tree = ttk.Treeview(
            list_frame,
            columns=("message", "rule", "source"),
            show="tree headings",
            selectmode="browse",
            style="Problems.Treeview",
        )
        self._tree.heading("#0", text=_t(
            "problems.location"), command=lambda: None)
        self._tree.heading("message", text=_t("problems.message"))
        self._tree.heading("rule", text=_t("problems.rule"))
        self._tree.heading("source", text=_t("problems.source"))
        self._tree.column("#0", width=130, minwidth=80, stretch=False)
        self._tree.column("message", width=320, minwidth=150)
        self._tree.column("rule", width=100, minwidth=60)
        self._tree.column("source", width=70, minwidth=40, stretch=False)

        vsb = ttk.Scrollbar(
            list_frame,
            orient="vertical",
            command=self._tree.yview,
            style="Thin.Vertical.TScrollbar",
        )
        vsb.pack(side="right", fill="y")
        self._tree.config(yscrollcommand=vsb.set)
        self._tree.pack(fill="both", expand=True)

        self._configure_tags(t)

        self._tree.bind("<Double-Button-1>", self._on_double)
        self._tree.bind("<Return>", self._on_double)
        self._tree.bind("<Button-3>", self._on_ctx)
        self._tree.bind("<Motion>", self._on_motion)
        self._tree.bind("<Leave>", lambda _: self._hide_tooltip())

        self._diag_map: dict[str, Diag] = {}

    def _configure_tags(self, t: Theme) -> None:
        """Executa a operação 'configure tags'.

        Args:
            t: Parâmetro da operação.
        """
        self._tree.tag_configure("error", foreground=t.err_fg)
        self._tree.tag_configure("warning", foreground=t.warn_fg)
        self._tree.tag_configure("info", foreground=t.info_fg)
        self._tree.tag_configure("hint", foreground=t.hint_fg)
        self._tree.tag_configure(
            "file", foreground=t.fg, font=(FONT_UI, 9, "bold"))
        self._tree.tag_configure("fix", foreground=t.diff_add_fg)

    def set_diags(self, diags: list[Diag], file: str = "") -> None:
        """Atualiza diagnósticos de um arquivo específico."""
        if file:
            self._diags_by_file[file] = diags
        self._file = file

        self._diags = [d for ds in self._diags_by_file.values() for d in ds]
        self._render()

    def clear_file_diags(self, file: str) -> None:
        """Remove diagnósticos de um arquivo (chamado ao fechar a aba)."""
        if file and file in self._diags_by_file:
            del self._diags_by_file[file]
            self._diags = [d for ds in self._diags_by_file.values()
                           for d in ds]
            self._render()

    def set_open_files(self, open_paths: list[str]) -> None:
        """Recebe a lista de arquivos abertos para filtrar exibição."""
        self._open_paths: set = set(open_paths)
        self._render()

    def _on_open_only_toggle(self) -> None:
        """Trata o evento 'open only toggle'."""
        self._open_files_only = self._open_only_var.get()
        self._render()

    def _render(self) -> None:
        """Renderiza o conteúdo no canvas ou widget."""
        for item in self._tree.get_children():
            self._tree.delete(item)
        self._diag_map.clear()

        q = self._filter_var.get().lower()

        open_paths = getattr(self, "_open_paths", set())
        if self._open_files_only and open_paths:
            source_diags = [
                d
                for d in self._diags
                if (getattr(d, "file", self._file) or self._file) in open_paths
            ]
        else:
            source_diags = self._diags

        visible = [
            d
            for d in source_diags
            if d.severity in self._filter
            and (not q or q in d.message.lower() or q in d.rule.lower())
        ]

        t = self._tmgr.theme
        counts = {1: 0, 2: 0, 3: 0, 4: 0}
        for d in self._diags:
            counts[d.severity] = counts.get(d.severity, 0) + 1
        for sev, btn in self._sev_btns.items():
            icon, _, color_attr = self._SEV_META[sev]
            color = getattr(t, color_attr, t.fg)
            active = sev in self._filter
            btn.config(
                text=f" {icon} {counts[sev]} ",
                bg=t.btn_active if active else t.panel_bg,
                fg=color,
            )

        if self._group_var.get():
            self._render_grouped(visible)
        else:
            self._render_flat(visible)

    def _render_grouped(self, visible: list[Diag]) -> None:
        """Executa a operação 'render grouped'.

        Args:
            visible: True para habilitar, False para desabilitar.
        """
        groups: dict[str, list[Diag]] = {}
        for d in visible:
            src = getattr(d, "file", self._file) or self._file or "Unknown"
            groups.setdefault(src, []).append(d)

        for fname, diags in sorted(groups.items()):
            diags_sorted = sorted(diags, key=lambda x: (x.severity, x.line))
            err_c = sum(1 for d in diags if d.severity == 1)
            wrn_c = sum(1 for d in diags if d.severity == 2)
            badge = f"  ✖{err_c}" if err_c else ""
            if wrn_c:
                badge += f" ⚠{wrn_c}"
            short = Path(fname).name if fname else fname
            node = self._tree.insert(
                "", "end", text=f"  📄 {short}{badge}", tags=("file",), open=True
            )
            for d in diags_sorted:
                self._insert_diag(node, d)

    def _render_flat(self, visible: list[Diag]) -> None:
        """Executa a operação 'render flat'.

        Args:
            visible: True para habilitar, False para desabilitar.
        """
        for d in sorted(visible, key=lambda x: (x.severity, x.line)):
            self._insert_diag("", d)

    def _insert_diag(self, parent: str, d: Diag) -> None:
        """Executa a operação 'insert diag'.

        Args:
            parent: Widget pai Tkinter.
            d: Parâmetro da operação.
        """
        _, tag, _ = self._SEV_META.get(d.severity, ("-", "info", "info_fg"))
        icon = self._SEV_META[d.severity][0]
        loc = f"  {icon} {d.line}:{d.col + 1}"
        fix_sfx = "  [fix]" if d.fix else ""
        iid = self._tree.insert(
            parent,
            "end",
            text=loc,
            values=(d.message + fix_sfx, d.rule, d.source),
            tags=(tag, "fix") if d.fix else (tag,),
        )
        self._diag_map[iid] = d

    def _toggle_sev(self, sev: int) -> None:
        """Executa a operação 'toggle sev'.

        Args:
            sev: Parâmetro da operação.
        """
        if sev in self._filter:
            self._filter.discard(sev)
        else:
            self._filter.add(sev)
        self._render()

    def _on_double(self, _e: Any = None) -> None:
        """Trata o evento 'double'.

        Args:
            _e: Parâmetro da operação.
        """
        sel = self._tree.selection()
        if not sel:
            return
        iid = sel[0]
        d = self._diag_map.get(iid)
        if d is None:
            return

        self._on_goto(d.line, d.col)

    def _on_ctx(self, e: tk.Event) -> None:
        """Trata o evento 'ctx'.

        Args:
            e: Evento Tkinter.
        """
        item = self._tree.identify_row(e.y)
        if not item:
            return
        self._tree.selection_set(item)
        d = self._diag_map.get(item)
        if not d:
            return
        t = self._tmgr.theme
        menu = tk.Menu(
            self,
            tearoff=False,
            bg=t.floating_bg,
            fg=t.fg,
            activebackground=t.sel_bg,
            activeforeground=t.sel_fg,
            relief="flat",
        )
        menu.add_command(
            label=_t("problems.goto"), command=lambda: self._on_goto(d.line, d.col)
        )
        menu.add_command(
            label=_t("problems.copy_msg"), command=lambda: self._copy_text(d.message)
        )
        menu.add_command(
            label=_t("problems.copy_rule"), command=lambda: self._copy_text(d.rule)
        )
        if d.fix:
            menu.add_separator()
            menu.add_command(
                label=_t("problems.apply_fix"), command=lambda: self._run_fix(d)
            )
            same_rule = [
                x for x in self._diag_map.values() if x.fix and x.rule == d.rule
            ]
            if len(same_rule) > 1:
                label_rule = _t(
                    "problems.fix_all_rule",
                    default=f"Corrigir todas as falhas: {d.rule} ({len(same_rule)})",
                    rule=d.rule,
                    count=len(same_rule),
                )
                menu.add_command(
                    label=label_rule,
                    command=lambda diags=same_rule: self._run_fix_list(diags),
                )
            file_key = getattr(d, "file", self._file) or self._file
            all_fixable = [
                x for x in (self._diags_by_file.get(file_key) or self._diags) if x.fix
            ]
            if all_fixable:
                label_all = _t(
                    "problems.fix_all_file",
                    default=f"Corrigir todas as falhas do arquivo ({len(all_fixable)})",
                    count=len(all_fixable),
                )
                menu.add_command(
                    label=label_all,
                    command=lambda diags=all_fixable, f=file_key: self._run_fix_all_file(
                        f, diags
                    ),
                )
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()

    def _run_fix(self, d: Diag) -> None:
        """Aplica o fix de um diagnóstico via callback externo.

        Args:
            d: Diagnóstico com fix disponível.
        """
        if not d.fix:
            return
        file_key = getattr(d, "file", self._file) or self._file
        if self._on_apply_fix:
            with contextlib.suppress(Exception):
                self._on_apply_fix(file_key, d)

    def _run_fix_list(self, diags: list[Diag]) -> None:
        """Aplica fixes de uma lista de diagnósticos em ordem reversa (preserva posições).

        Args:
            diags: Lista de diagnósticos a corrigir.
        """
        if not diags or not self._on_apply_fix:
            return
        by_file: dict[str, list[Diag]] = {}
        for d in diags:
            if not d.fix:
                continue
            fk = getattr(d, "file", self._file) or self._file
            by_file.setdefault(fk, []).append(d)
        for fk, fdiags in by_file.items():
            self._run_fix_all_file(fk, fdiags)

    def _run_fix_all_file(self, file_key: str, diags: list[Diag]) -> None:
        """Aplica todos os fixes do arquivo via callback externo.

        Args:
            file_key: Caminho do arquivo.
            diags: Lista de diagnósticos com fix.
        """
        fixable = [d for d in diags if d.fix]
        if not fixable or not self._on_fix_all:
            return
        with contextlib.suppress(Exception):
            self._on_fix_all(file_key, fixable)

    def _copy_text(self, text: str) -> None:
        """Copia o texto para área de transferência.

        Args:
            text: Dado textual a processar.
        """
        with contextlib.suppress(tk.TclError):
            self.clipboard_clear()
            self.clipboard_append(text)

    def _on_motion(self, e: tk.Event) -> None:
        """Trata o evento 'motion'.

        Args:
            e: Evento Tkinter.
        """
        item = self._tree.identify_row(e.y)
        d = self._diag_map.get(item)
        if d:
            self._show_tooltip(e, d.message)
        else:
            self._hide_tooltip()

    def _show_tooltip(self, e: tk.Event, text: str) -> None:
        """Executa a operação 'show tooltip'.

        Args:
            e: Evento Tkinter.
            text: Dado textual a processar.
        """
        self._hide_tooltip()
        t = self._tmgr.theme
        tw = tk.Toplevel(self)
        tw.wm_overrideredirect(True)
        tw.wm_attributes("-topmost", True)
        f = tk.Frame(
            tw,
            bg=t.floating_bg,
            highlightthickness=1,
            highlightbackground=t.floating_border,
        )
        f.pack()
        tk.Label(
            f,
            text=text,
            bg=t.floating_bg,
            fg=t.fg,
            font=(FONT_UI, 9),
            padx=8,
            pady=4,
            wraplength=360,
            justify="left",
        ).pack()
        tw.geometry(f"+{e.x_root + 12}+{e.y_root + 12}")
        self._tooltip_win = tw

    def _hide_tooltip(self) -> None:
        """Oculta o tooltip de detalhe do diagnóstico."""
        if self._tooltip_win and self._tooltip_win.winfo_exists():
            with contextlib.suppress(tk.TclError):
                self._tooltip_win.destroy()
        self._tooltip_win = None

    def apply_theme(self, t: Theme) -> None:
        """Aplica o tema ao painel de problemas."""
        self.config(bg=t.panel_bg)
        style = ttk.Style()
        style.configure(
            "Problems.Treeview",
            background=t.panel_bg,
            foreground=t.fg,
            fieldbackground=t.panel_bg,
            font=(FONT_UI, 9),
        )
        style.map(
            "Problems.Treeview",
            background=[("selected", t.sel_bg)],
            foreground=[("selected", t.sel_fg)],
        )
        style.configure(
            "Problems.Treeview.Heading",
            background=t.panel_bg,
            foreground=t.fg_muted,
            relief="flat",
            font=(FONT_UI, 9, "bold"),
            borderwidth=0,
            padding=(4, 4),
        )
        style.map(
            "Problems.Treeview.Heading",
            background=[("active", t.btn_hover)],
            foreground=[("active", t.fg)],
        )
        self._configure_tags(t)
        self._render()
