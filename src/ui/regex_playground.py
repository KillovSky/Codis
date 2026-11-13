"""Playground de expressões regulares integrado ao Codis.

Implementa ``RegexPlayground``, painel interativo para testar regex em
tempo real sobre um texto de exemplo, com realce de grupos, flags
configurável e exibição dos grupos capturados.
"""

from __future__ import annotations

import contextlib
import logging
import re
import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

from src.constants import FONT_MONO, FONT_UI
from src.i18n import _t

logger = logging.getLogger("Codis")


class RegexPlayground(tk.Frame):
    """
    Painel inferior de regex playground com:
    - Campo de padrão com flags (i, m, s, x)
    - Texto de teste (carregado do editor ativo)
    - Realce em tempo real dos matches
    - Contador de matches
    - Campo de substituição com preview
    - Botão Apply Replace
    """

    def __init__(
        self,
        parent: tk.Widget,
        tmgr,
        *,
        get_text: Callable[[], str],
        apply_replace: Callable[[str, str, str], None],
    ) -> None:
        """Cria o painel de teste de expressões regulares.

        Args:
            parent: Widget pai Tkinter.
            config: Instância de ConfigManager.
        """
        t = tmgr.theme
        super().__init__(parent, bg=t.panel_bg)
        self._tmgr = tmgr
        self._get_text = get_text
        self._apply_replace = apply_replace
        self._after_id: str | None = None
        self._build()

    def _build(self) -> None:
        """Constrói os widgets e layout interno do componente."""
        t = self._tmgr.theme

        toolbar = tk.Frame(self, bg=t.panel_bg)
        toolbar.pack(fill="x", padx=8, pady=4)

        tk.Label(
            toolbar, text="Regex", bg=t.panel_bg, fg=t.fg, font=(FONT_UI, 9, "bold")
        ).pack(side="left", padx=(0, 8))

        tk.Label(
            toolbar,
            text=_t("ui.regex_pattern") + ":",
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
        ).pack(side="left")
        self._pattern_var = tk.StringVar()
        self._pattern_entry = tk.Entry(
            toolbar,
            textvariable=self._pattern_var,
            bg=t.input_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=t.border,
            font=(FONT_MONO, 10),
            width=28,
        )
        self._pattern_entry.pack(side="left", padx=4)

        tk.Label(
            toolbar,
            text=_t("ui.regex_flags") + ":",
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
        ).pack(side="left", padx=(8, 2))
        self._flags_var = tk.StringVar(value="")
        flags_entry = tk.Entry(
            toolbar,
            textvariable=self._flags_var,
            bg=t.input_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=t.border,
            font=(FONT_MONO, 10),
            width=6,
        )
        flags_entry.pack(side="left", padx=2)

        self._match_lbl = tk.Label(
            toolbar, text="", bg=t.panel_bg, fg=t.ok_fg, font=(FONT_UI, 9)
        )
        self._match_lbl.pack(side="left", padx=12)

        load_btn = tk.Label(
            toolbar,
            text=_t("hc.regex_playground.load_editor"),
            bg=t.btn_bg,
            fg=t.fg,
            font=(FONT_UI, 8),
            cursor="hand2",
            padx=8,
            pady=2,
            relief="flat",
        )
        load_btn.pack(side="right", padx=4)
        load_btn.bind("<Button-1>", lambda _: self._load_from_editor())
        load_btn.bind("<Enter>", lambda _,
                      w=load_btn: w.config(bg=t.btn_hover))
        load_btn.bind("<Leave>", lambda _, w=load_btn: w.config(bg=t.btn_bg))

        rep_row = tk.Frame(self, bg=t.panel_bg)
        rep_row.pack(fill="x", padx=8, pady=(0, 4))

        tk.Label(
            rep_row,
            text=_t("ui.regex_replace_with") + ":",
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
        ).pack(side="left")
        self._replace_var = tk.StringVar()
        tk.Entry(
            rep_row,
            textvariable=self._replace_var,
            bg=t.input_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=t.border,
            font=(FONT_MONO, 10),
            width=28,
        ).pack(side="left", padx=4)

        apply_btn = tk.Label(
            rep_row,
            text=_t("ui.regex_apply"),
            bg=t.btn_primary,
            fg="#000",
            font=(FONT_UI, 8, "bold"),
            cursor="hand2",
            padx=10,
            pady=2,
            relief="flat",
        )
        apply_btn.pack(side="left", padx=8)
        apply_btn.bind("<Button-1>", lambda _: self._do_replace())
        apply_btn.bind("<Enter>", lambda _, w=apply_btn: w.config(bg=t.accent))
        apply_btn.bind("<Leave>", lambda _,
                       w=apply_btn: w.config(bg=t.btn_primary))

        text_frame = tk.Frame(self, bg=t.panel_bg)
        text_frame.pack(fill="both", expand=True, padx=8, pady=(0, 4))

        self._text = tk.Text(
            text_frame,
            bg=t.editor_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            font=(FONT_MONO, 10),
            relief="flat",
            bd=0,
            padx=6,
            pady=4,
            wrap="none",
            undo=True,
        )
        vsb = ttk.Scrollbar(
            text_frame,
            orient="vertical",
            command=self._text.yview,
            style="Thin.Vertical.TScrollbar",
        )
        hsb = ttk.Scrollbar(
            text_frame,
            orient="horizontal",
            command=self._text.xview,
            style="Thin.Horizontal.TScrollbar",
        )
        vsb.pack(side="right", fill="y")
        hsb.pack(side="bottom", fill="x")
        self._text.config(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self._text.pack(fill="both", expand=True)

        self._text.tag_configure(
            "match", background=t.sel_bg, foreground=t.sel_fg)
        self._text.tag_configure(
            "match_current", background=t.accent, foreground="#000"
        )

        self._pattern_var.trace_add("write", lambda *_: self._schedule_match())
        self._flags_var.trace_add("write", lambda *_: self._schedule_match())
        self._text.bind("<KeyRelease>", lambda _: self._schedule_match())

    def _schedule_match(self) -> None:
        """Agenda a execução do teste regex com debounce."""
        if self._after_id:
            with contextlib.suppress(tk.TclError):
                self.after_cancel(self._after_id)
        self._after_id = self.after(150, self._run_match)

    def _run_match(self) -> None:
        """Executa o teste do padrão regex e destaca as ocorrências."""
        self._after_id = None
        pattern = self._pattern_var.get()
        flags_str = self._flags_var.get().lower()
        self._text.tag_remove("match", "1.0", "end")
        self._text.tag_remove("match_current", "1.0", "end")

        if not pattern:
            self._match_lbl.config(text="")
            return

        flags = 0
        if "i" in flags_str:
            flags |= re.IGNORECASE
        if "m" in flags_str:
            flags |= re.MULTILINE
        if "s" in flags_str:
            flags |= re.DOTALL
        if "x" in flags_str:
            flags |= re.VERBOSE

        try:
            rx = re.compile(pattern, flags)
        except re.error as e:
            self._match_lbl.config(text=f"⚠ {e}", fg=self._tmgr.theme.err_fg)
            return

        content = self._text.get("1.0", "end-1c")
        matches = list(rx.finditer(content))
        n = len(matches)

        if n == 0:
            self._match_lbl.config(
                text=_t("ui.regex_no_match"), fg=self._tmgr.theme.fg_muted
            )
            return

        self._match_lbl.config(
            text=_t("ui.regex_matches", n=n), fg=self._tmgr.theme.ok_fg
        )

        for i, m in enumerate(matches):
            start_idx = self._text.index(f"1.0 + {m.start()}c")
            end_idx = self._text.index(f"1.0 + {m.end()}c")
            tag = "match_current" if i == 0 else "match"
            self._text.tag_add(tag, start_idx, end_idx)

        if matches:
            self._text.see(self._text.index(f"1.0 + {matches[0].start()}c"))

    def _load_from_editor(self) -> None:
        """Carrega o texto do editor ativo para o campo de teste do playground."""
        with contextlib.suppress(tk.TclError):
            content = self._get_text()
            if content:
                self._text.delete("1.0", "end")
                self._text.insert("1.0", content)
                self._schedule_match()

    def _do_replace(self) -> None:
        """Aplica a substituição regex ao texto de teste e exibe o resultado."""
        pattern = self._pattern_var.get()
        if not pattern:
            return
        self._apply_replace(pattern, self._replace_var.get(),
                            self._flags_var.get())

    def apply_theme(self, t) -> None:
        """Aplica o tema ao playground de regex."""
        self.config(bg=t.panel_bg)
        with contextlib.suppress(tk.TclError):
            self._text.config(bg=t.editor_bg, fg=t.fg,
                              insertbackground=t.cursor)
            self._text.tag_configure(
                "match", background=t.sel_bg, foreground=t.sel_fg)
            self._text.tag_configure(
                "match_current", background=t.accent, foreground="#000"
            )
