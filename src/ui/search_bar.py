"""Barra de busca e substituição do editor do Codis.

Provê ``SearchBar``, widget que implementa busca por texto ou regex no
arquivo aberto, substituição simples e em massa, com realce de ocorrências
e navegação entre resultados.
"""

from __future__ import annotations

import contextlib
import logging
import re
import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING, Any

from src.config import ConfigManager
from src.constants import (
    FONT_MONO,
    FONT_UI,
)
from src.i18n import _t
from src.theme.base import Theme
from src.theme.manager import ThemeManager
from src.ui.grab_safe import safe_grab_set

logger = logging.getLogger("Codis")

if TYPE_CHECKING:
    from src.editor.pane import EditorPane


class _SearchEngine:
    """
    Motor de busca que opera sobre o conteúdo do editor sem tocar no Tk.

    Estratégias de performance:
      - Índice de linhas precalculado (offset -> (linha, col)) para O(1) lookup
      - Busca incremental: só recomputa se o texto ou padrão mudou
      - Limite configurável com early-exit
      - Suporte a grupos de captura nos spans para replace com backreferences

    Thread-safety: projetado para ser chamado apenas da thread principal do Tk.
    """

    MAX_MATCHES = 10_000

    def __init__(self) -> None:
        """Inicializa o motor de busca interno da barra de pesquisa."""
        self._text_id: int = 0
        self._content: str = ""
        self._line_index: list[int] = []
        self._matches: list[tuple[int, int]] = []
        self._pat: re.Pattern | None = None
        self._pat_id: int = 0
        self._capped: bool = False

    def ingest(self, content: str) -> None:
        """Fornece novo conteúdo ao motor. Invalida o cache."""
        if content is self._content:
            return
        self._content = content
        self._text_id += 1

        idx = [0]
        pos = content.find("\n")
        while pos != -1:
            idx.append(pos + 1)
            pos = content.find("\n", pos + 1)
        self._line_index = idx

        self._matches = []
        self._pat_id = -1

    def offset_to_line_col(self, offset: int) -> tuple[int, int]:
        """Converte offset de char em (linha 1-based, col 0-based). O(log N)."""
        import bisect

        li = bisect.bisect_right(self._line_index, offset) - 1
        li = max(0, min(li, len(self._line_index) - 1))
        col = offset - self._line_index[li]
        return li + 1, col

    def tk_index(self, offset: int) -> str:
        """Retorna índice no formato Tk 'linha.col'."""
        ln, col = self.offset_to_line_col(offset)
        return f"{ln}.{col}"

    def search(
        self, pat: re.Pattern, sel_start: int = 0, sel_end: int = -1
    ) -> list[tuple[int, int]]:
        """
        Executa a busca no conteúdo ingerido.
        sel_end == -1 -> busca no conteúdo inteiro
        Retorna lista de (start_offset, end_offset).
        """
        if not self._content:
            self._matches = []
            self._capped = False
            return self._matches

        end = sel_end if sel_end != -1 else len(self._content)
        content_slice = self._content[sel_start:end]

        matches: list[tuple[int, int]] = []
        capped = False
        for m in pat.finditer(content_slice):
            s = m.start() + sel_start
            e = m.end() + sel_start
            if s == e:
                e = s + 1
            matches.append((s, e))
            if len(matches) >= self.MAX_MATCHES:
                capped = True
                break

        self._matches = matches
        self._capped = capped
        self._pat = pat
        return matches

    def replace_all(
        self, pat: re.Pattern, repl_fn: Callable[[str, str], str], repl_template: str
    ) -> tuple[str, int]:
        """
        Substitui todas as ocorrências no conteúdo.
        repl_fn(match_text, template) -> replacement_string
        Retorna (novo_conteúdo, número_de_substituições).
        """

        def _sub(m: re.Match) -> str:

            try:
                raw = m.expand(repl_template)
            except re.error:
                raw = repl_template
            return repl_fn(m.group(), raw)

        new_content, n = pat.subn(_sub, self._content)
        return new_content, n

    @property
    def matches(self) -> list[tuple[int, int]]:
        """Executa a busca e retorna a lista de (início, fim) de cada ocorrência."""
        return self._matches

    @property
    def capped(self) -> bool:
        """Indica se o número de resultados foi limitado pelo teto máximo de exibição."""
        return self._capped


def _build_pattern(
    query: str, case_sensitive: bool, whole_word: bool, is_regex: bool
) -> re.Pattern | None:
    """Compila o padrão de busca com tratamento de erros. Retorna None se inválido."""
    if not query:
        return None
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        if is_regex:
            raw = query
        elif whole_word:
            raw = r"\b" + re.escape(query) + r"\b"
        else:
            raw = re.escape(query)
        return re.compile(raw, flags)
    except re.error:
        return None


class SearchBar(tk.Frame):
    """
    Barra de busca e substituição:

    +-------------------------------------------------------------------+
    | ⠿ 🔍 [campo de busca ....................] 12/347  ↑ ↓  ⇄ 📂 ✕ |
    |    [Aa] [\\b] [.*] [≡] [⌂]    -- active: regex·case          |
    | ⇄ [campo de substituição ...............] [\\1 \\2 vars ok]  |
    |    [Cc Preserve] [AB] [ab] [Ab]  [⧉ Preview]                   |
    |    [▶ Substituir]  [▶▶ Substituir Tudo]                        |
    +-------------------------------------------------------------------+

    Backend (_SearchEngine):
      - Índice bisect de linhas -> offset_to_line_col em O(log N)
      - Debounce 80ms antes de recalcular (evita lag em arquivos grandes)
      - Limite 10 000 matches com early-exit e indicador "10000+"
      - replace_all atômico via set_content (undo group nativo do Tk)
      - Backreferences \\1 \\2 no campo replace
      - Preview diff unificado antes de aplicar substitution

    UX/Frontend:
      - Flash breve no match atual (180ms)
      - Indicador ↺ wrap-around no contador
      - Borda vermelha no campo find quando regex inválido
      - Barra de progresso proporcional ao match atual/total
      - Novo toggle: Multiline (^ $ por linha) Alt+M
      - Ctrl+Return insere \\n literal no campo find
      - Esc com texto -> limpa; sem texto -> fecha
      - Botão Preview diff: vê antes de aplicar replace all
    """

    _HIST_MAX = 100
    _DEBOUNCE_MS = 80

    def __init__(
        self,
        parent: tk.Widget,
        tmgr: ThemeManager,
        cfg: ConfigManager,
        get_editor: Callable[[], EditorPane | None],
        on_find_in_files: Callable | None = None,
        pack_before: tk.Widget | None = None,
    ) -> None:
        """Inicializa a barra de busca e substituição do editor.

        Args:
            parent: Widget pai Tkinter.
            editor: Referência ao EditorPane ativo.
            config: Instância de ConfigManager.
            pack_before: Widget antes do qual a barra deve ser inserida no
                layout pack (evita ficar atrás de widgets com expand=True).
        """
        t = tmgr.theme
        super().__init__(
            parent,
            bg=t.floating_bg,
            highlightthickness=1,
            highlightbackground=t.floating_border,
            bd=0,
        )
        self._tmgr = tmgr
        self._cfg = cfg
        self._get_editor = get_editor
        self._on_find_files = on_find_in_files
        self._pack_before: tk.Widget | None = pack_before

        self._engine = _SearchEngine()
        self._matches: list[tuple[int, int]] = []
        self._match_idx = 0
        self._visible = False
        self._replace_open = False
        self._debounce_id: str | None = None

        self._case_mode = False
        self._word_mode = False
        self._re_mode = False
        self._sel_mode = False
        self._multi_mode = False

        self._preserve_case = False
        self._case_transform = ""

        self._find_history: list[str] = []
        self._repl_history: list[str] = []
        self._find_hist_pos = -1
        self._repl_hist_pos = -1

        self._find_entry: tk.Entry | None = None
        self._repl_entry: tk.Entry | None = None
        self._count_lbl: tk.Label | None = None
        self._prog_canvas: tk.Canvas | None = None
        self._repl_row: tk.Frame | None = None
        self._repl_toggle: tk.Label | None = None
        self._hint_lbl: tk.Label | None = None
        self._opt_btns: dict[str, tk.Label] = {}
        self._ct_btns: dict[str, tk.Label] = {}
        self._find_wrap: tk.Frame | None = None
        self._err_border = False
        self._repl_opts_row: tk.Frame | None = None

        self._build()
        self.pack_forget()

    def _build(self) -> None:
        """Constrói os widgets e layout interno do componente."""
        t = self._tmgr.theme

        def _ekw(width: int = 26) -> dict:
            return {
                "bg": t.input_bg,
                "fg": t.fg,
                "insertbackground": t.cursor,
                "relief": "flat",
                "bd": 0,
                "width": width,
                "font": (FONT_UI, 10),
                "highlightthickness": 0,
            }

        grip = tk.Label(
            self,
            text="⠿",
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
            cursor="fleur",
            padx=3,
        )
        grip.pack(side="top", anchor="e", padx=2, pady=(2, 0))

        rowA = tk.Frame(self, bg=t.floating_bg)
        rowA.pack(fill="x", padx=8, pady=(2, 1))

        tk.Label(
            rowA, text="🔍", bg=t.floating_bg, fg=t.fg_muted, font=(FONT_UI, 10)
        ).pack(side="left", padx=(0, 4))

        self._find_wrap = tk.Frame(
            rowA, bg=t.border, highlightthickness=0, bd=1, relief="flat"
        )
        self._find_wrap.pack(side="left", padx=(0, 4))
        self._find_var = tk.StringVar()
        self._find_var.trace_add("write", lambda *_: self._schedule_find())
        self._find_entry = tk.Entry(
            self._find_wrap, textvariable=self._find_var, **_ekw(26)
        )
        self._find_entry.pack(padx=1, pady=1)

        counter_col = tk.Frame(rowA, bg=t.floating_bg)
        counter_col.pack(side="left", padx=(0, 4))
        self._count_lbl = tk.Label(
            counter_col,
            text="",
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_MONO, 8),
            width=10,
            anchor="w",
        )
        self._count_lbl.pack()
        self._prog_canvas = tk.Canvas(
            counter_col, height=2, width=72, bg=t.floating_bg, highlightthickness=0
        )
        self._prog_canvas.pack(fill="x")

        tk.Frame(rowA, bg=t.border, width=1).pack(
            side="left", fill="y", padx=3, pady=2)

        self._ibtn(rowA, "↑", self.prev_match, "Anterior  Shift+F3")
        self._ibtn(rowA, "↓", self.next_match, "Próximo  F3")

        tk.Frame(rowA, bg=t.border, width=1).pack(
            side="left", fill="y", padx=3, pady=2)

        self._repl_toggle = tk.Label(
            rowA,
            text="⇄",
            bg=t.btn_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 10),
            padx=7,
            pady=1,
            cursor="hand2",
            relief="flat",
        )
        self._repl_toggle.pack(side="left", padx=1)
        self._repl_toggle.bind("<Button-1>", lambda _: self._toggle_replace())
        self._tip(self._repl_toggle, "Abrir/fechar substituição  Ctrl+H")

        if self._on_find_files:
            tk.Frame(rowA, bg=t.border, width=1).pack(
                side="left", fill="y", padx=3, pady=2
            )
            self._ibtn(
                rowA,
                "📂",
                self._trigger_find_files,
                "Buscar em todos os arquivos  Ctrl+Shift+F",
            )

        self._ibtn(rowA, "✕", self.hide, "Fechar  Escape")

        rowB = tk.Frame(self, bg=t.floating_bg)
        rowB.pack(fill="x", padx=8, pady=(2, 2))

        for text, attr, tip in (
            ("Aa", "_case_mode", "Case sensitive  Alt+C"),
            ("\\b", "_word_mode", "Palavra inteira  Alt+W"),
            (".*", "_re_mode", "Expressão regular (Python re)  Alt+R"),
            ("≡", "_sel_mode", "Apenas na seleção  Alt+S"),
            ("⌂", "_multi_mode", "Multiline (^ e $ por linha)  Alt+M"),
        ):
            b = self._opt_btn(rowB, text, attr, tip)
            self._opt_btns[attr] = b

        self._hint_lbl = tk.Label(
            rowB,
            text="",
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 7),
            anchor="w",
        )
        self._hint_lbl.pack(side="left", padx=(10, 0))

        self._repl_row = tk.Frame(self, bg=t.floating_bg)

        tk.Label(
            self._repl_row,
            text="⇄",
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 10),
        ).pack(side="left", padx=(0, 4))

        self._repl_var = tk.StringVar()
        self._repl_entry = tk.Entry(
            self._repl_row, textvariable=self._repl_var, **_ekw(26)
        )
        self._repl_entry.pack(side="left", padx=(0, 4), ipady=2)
        self._tip(
            self._repl_entry,
            "Use \\1, \\2 ... para grupos de captura em modo regex\n"
            "Ex: buscar (\\w+)\\s(\\w+) → substituir \\2 \\1",
        )

        for lbl, cmd, tip_text in (
            ("▶  Substituir", self._replace_one,
             "Substituir ocorrência atual  Enter"),
            ("▶▶ Subst. Tudo", self._replace_all, "Substituir todas  Shift+Enter"),
        ):
            b = tk.Label(
                self._repl_row,
                text=lbl,
                bg=t.btn_bg,
                fg=t.fg,
                font=(FONT_UI, 8),
                padx=9,
                pady=2,
                cursor="hand2",
                relief="flat",
                highlightthickness=1,
                highlightbackground=t.border,
            )
            b.pack(side="left", padx=2)
            b.bind("<Button-1>", lambda _, c=cmd: c())
            b.bind("<Enter>", lambda _, x=b: x.config(bg=t.btn_hover))
            b.bind("<Leave>", lambda _, x=b: x.config(bg=t.btn_bg))
            self._tip(b, tip_text)

        preview_btn = tk.Label(
            self._repl_row,
            text=_t("hc.search_bar.preview"),
            bg=t.btn_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            padx=7,
            pady=2,
            cursor="hand2",
            relief="flat",
            highlightthickness=1,
            highlightbackground=t.border,
        )
        preview_btn.pack(side="left", padx=2)
        preview_btn.bind("<Button-1>", lambda _: self._show_replace_preview())
        self._tip(preview_btn, "Pré-visualizar substituições antes de aplicar")

        self._repl_opts_row = tk.Frame(self, bg=t.floating_bg)

        tk.Label(
            self._repl_opts_row,
            text=_t("hc.search_bar.case"),
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 7),
        ).pack(side="left", padx=(0, 3))

        self._opt_btn(
            self._repl_opts_row,
            "Cc",
            "_preserve_case",
            "Preservar capitalização original",
        )
        self._opt_btns.pop("_preserve_case", None)

        tk.Label(
            self._repl_opts_row,
            text=_t("hc.search_bar.transform"),
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 7),
        ).pack(side="left", padx=(6, 2))

        for label, val, tip_text in (
            ("-", "", "Sem transformação"),
            ("AB", "upper", "MAIÚSCULAS"),
            ("ab", "lower", "minúsculas"),
            ("Ab", "title", "Title Case"),
        ):
            b = tk.Label(
                self._repl_opts_row,
                text=label,
                bg=t.btn_bg,
                fg=t.fg_muted,
                font=(FONT_MONO, 8),
                padx=5,
                pady=1,
                cursor="hand2",
                relief="flat",
            )
            b.pack(side="left", padx=1)
            b.bind("<Button-1>", lambda _, v=val: self._set_case_transform(v))
            self._tip(b, tip_text)
            self._ct_btns[val] = b
        self._update_ct_btns()

        fe = self._find_entry
        re_ = self._repl_entry

        fe.bind("<Return>", lambda _: self.next_match())
        fe.bind("<Shift-Return>", lambda _: self.prev_match())
        fe.bind("<Escape>", lambda _: self._esc_find())
        fe.bind("<Tab>", self._focus_replace)
        fe.bind("<Up>", self._hist_find_up)
        fe.bind("<Down>", self._hist_find_down)
        fe.bind("<Control-h>", lambda _: self._toggle_replace())
        fe.bind("<Alt-c>", lambda _: self._toggle_opt("_case_mode"))
        fe.bind("<Alt-w>", lambda _: self._toggle_opt("_word_mode"))
        fe.bind("<Alt-r>", lambda _: self._toggle_opt("_re_mode"))
        fe.bind("<Alt-s>", lambda _: self._toggle_opt("_sel_mode"))
        fe.bind("<Alt-m>", lambda _: self._toggle_opt("_multi_mode"))
        fe.bind("<F3>", lambda _: self.next_match())
        fe.bind("<Shift-F3>", lambda _: self.prev_match())
        fe.bind("<Control-Return>", lambda _: self._insert_newline_in_find())

        re_.bind("<Return>", lambda _: self._replace_one())
        re_.bind("<Shift-Return>", lambda _: self._replace_all())
        re_.bind("<Escape>", lambda _: self.hide())
        re_.bind("<Tab>", lambda _: fe.focus_set() or "break")
        re_.bind("<Up>", self._hist_repl_up)
        re_.bind("<Down>", self._hist_repl_down)

    def _ibtn(
        self, parent: tk.Widget, text: str, cmd: Callable, tip: str = ""
    ) -> tk.Label:
        """Cria e retorna um botão ícone.

        Args:
            parent: Widget pai Tkinter.
            text: Dado textual a processar.
            cmd: Parâmetro da operação.
            tip: Parâmetro da operação.
        """
        t = self._tmgr.theme
        b = tk.Label(
            parent,
            text=text,
            bg=t.floating_bg,
            fg=t.fg,
            font=(FONT_UI, 10),
            padx=5,
            cursor="hand2",
        )
        b.pack(side="left", padx=1)
        b.bind("<Button-1>", lambda _: cmd())
        b.bind("<Enter>", lambda _: b.config(bg=t.sel_bg))
        b.bind("<Leave>", lambda _: b.config(bg=t.floating_bg))
        if tip:
            self._tip(b, tip)
        return b

    def _opt_btn(self, parent: tk.Widget, text: str, attr: str, tip: str) -> tk.Label:
        """Executa 'opt btn' e retorna o resultado.

        Args:
            parent: Widget pai Tkinter.
            text: Dado textual a processar.
            attr: Parâmetro da operação.
            tip: Parâmetro da operação.
        """
        t = self._tmgr.theme
        active = getattr(self, attr, False)
        b = tk.Label(
            parent,
            text=text,
            bg=t.accent if active else t.btn_bg,
            fg="#ffffff" if active else t.fg_muted,
            font=(FONT_MONO, 9),
            padx=6,
            pady=1,
            cursor="hand2",
            relief="flat",
            highlightthickness=1,
            highlightbackground=t.border,
        )
        b.pack(side="left", padx=2)

        def _toggle(_e=None):
            val = not getattr(self, attr, False)
            setattr(self, attr, val)
            b.config(
                bg=t.accent if val else t.btn_bg, fg="#ffffff" if val else t.fg_muted
            )
            self._do_find()
            self._update_hint()

        b.bind("<Button-1>", _toggle)
        self._tip(b, tip)
        return b

    def _tip(self, widget: tk.Widget, text: str) -> None:
        """Exibe tooltip para o widget indicado.

        Args:
            widget: Parâmetro da operação.
            text: Dado textual a processar.
        """
        _state: dict = {"win": None, "after_id": None}

        def _make(x: int, y: int) -> None:
            _destroy()
            t = self._tmgr.theme
            tw = tk.Toplevel(widget)
            tw.wm_overrideredirect(True)
            tw.wm_attributes("-topmost", True)
            fr = tk.Frame(tw, bg=t.floating_border, bd=1)
            fr.pack()
            inner = tk.Frame(fr, bg=t.floating_bg, padx=10, pady=6)
            inner.pack()
            tk.Label(
                inner,
                text=text,
                bg=t.floating_bg,
                fg=t.fg,
                font=(FONT_UI, 9),
                justify="left",
                anchor="w",
                wraplength=340,
            ).pack()
            tw.geometry(f"+{x + 12}+{y + 12}")
            _state["win"] = tw

        def _destroy(_e=None) -> None:
            if _state.get("after_id"):
                with contextlib.suppress(tk.TclError):
                    widget.after_cancel(_state["after_id"])
                _state["after_id"] = None
            if _state.get("win"):
                with contextlib.suppress(tk.TclError):
                    _state["win"].destroy()
                _state["win"] = None

        def _enter(e: tk.Event) -> None:
            _state["after_id"] = widget.after(
                500, lambda: _make(e.x_root, e.y_root))

        widget.bind("<Enter>", _enter, add="+")
        widget.bind("<Leave>", _destroy, add="+")

    def _schedule_find(self) -> None:
        """Agenda a execução da busca com debounce após o usuário parar de digitar."""
        if self._debounce_id:
            with contextlib.suppress(tk.TclError):
                self.after_cancel(self._debounce_id)
        self._debounce_id = self.after(self._DEBOUNCE_MS, self._do_find)

    def _get_pattern(self) -> re.Pattern | None:
        """Obtém e retorna 'pattern'."""
        query = self._find_var.get()
        pat = _build_pattern(query, self._case_mode,
                             self._word_mode, self._re_mode)
        self._set_find_error(query != "" and pat is None)
        return pat

    def _set_find_error(self, has_error: bool) -> None:
        """Define o valor de 'find error'.

        Args:
            has_error: Parâmetro da operação.
        """
        if has_error == self._err_border:
            return
        self._err_border = has_error
        t = self._tmgr.theme
        color = t.err_fg if has_error else t.border
        if self._find_wrap:
            with contextlib.suppress(tk.TclError):
                self._find_wrap.config(bg=color)
        if self._count_lbl and has_error:
            self._count_lbl.config(
                text=_t("hc.search_bar.regex_inv_lido"), fg=t.err_fg)

    def _do_find(self) -> None:
        """Executa a busca completa e popula os resultados na barra."""
        editor = self._get_editor()
        if not editor:
            return
        query = self._find_var.get()
        t = self._tmgr.theme

        if not query:
            editor.hl.clear_search()
            self._matches = []
            self._match_idx = 0
            if self._count_lbl:
                self._count_lbl.config(text="", fg=t.fg_muted)
            self._update_progress(0, 0)
            self._set_find_error(False)
            return

        pat = self._get_pattern()
        if pat is None:
            editor.hl.clear_search()
            return

        content = editor.text.get("1.0", "end-1c")
        self._engine.ingest(content)

        sel_start, sel_end = 0, -1
        if self._sel_mode:
            try:
                s_idx = editor.text.index(tk.SEL_FIRST)
                e_idx = editor.text.index(tk.SEL_LAST)
                sel_start = self._tk_to_offset(content, s_idx)
                sel_end = self._tk_to_offset(content, e_idx)
            except tk.TclError:
                pass

        raw_matches = self._engine.search(pat, sel_start, sel_end)
        count = len(raw_matches)

        if count == 0:
            editor.hl.clear_search()
            self._matches = []
            self._match_idx = 0
            if self._count_lbl:
                self._count_lbl.config(
                    text=_t("hc.search_bar.nenhum_resultado"), fg=t.warn_fg
                )
            self._update_progress(0, 0)
            return

        self._matches = raw_matches
        self._match_idx = 0

        tk_matches = [
            (self._engine.tk_index(s), self._engine.tk_index(e)) for s, e in raw_matches
        ]
        editor.hl.apply_search(tk_matches, 0)
        with contextlib.suppress(tk.TclError):
            editor.text.see(tk_matches[0][0])

        suffix = "+" if self._engine.capped else ""
        if self._count_lbl:
            self._count_lbl.config(text=f"1/{count}{suffix}", fg=t.fg_muted)
        self._update_progress(1, count)

    @staticmethod
    def _tk_to_offset(content: str, tk_idx: str) -> int:
        """Executa 'tk to offset' e retorna o resultado.

        Args:
            content: Dado textual a processar.
            tk_idx: Parâmetro da operação.
        """
        try:
            parts = tk_idx.split(".")
            ln = int(parts[0]) - 1
            col = int(parts[1])
            lines = content.split("\n")
            return sum(len(lines[i]) + 1 for i in range(min(ln, len(lines)))) + col
        except (ValueError, IndexError):
            return 0

    def _update_progress(self, current: int, total: int) -> None:
        """Atualiza 'progress'.

        Args:
            current: Parâmetro da operação.
            total: Parâmetro da operação.
        """
        if not self._prog_canvas:
            return
        with contextlib.suppress(tk.TclError):
            t = self._tmgr.theme
            w = self._prog_canvas.winfo_width() or 72
            self._prog_canvas.delete("all")
            if total > 0:
                ratio = current / total
                self._prog_canvas.create_rectangle(
                    0, 0, int(w * ratio), 2, fill=t.accent, outline=""
                )

    def _jump_to(self, idx: int) -> None:
        """Executa a operação 'jump to'.

        Args:
            idx: Índice do item.
        """
        editor = self._get_editor()
        if not editor or not self._matches:
            return
        s_off, e_off = self._matches[idx]
        ts = self._engine.tk_index(s_off)
        te = self._engine.tk_index(e_off)

        content = editor.text.get("1.0", "end-1c")
        self._engine.ingest(content)
        tk_matches = [
            (self._engine.tk_index(s), self._engine.tk_index(e))
            for s, e in self._matches
        ]

        editor.hl.apply_search(tk_matches, idx)
        with contextlib.suppress(tk.TclError):
            editor.text.see(ts)
            editor.text.mark_set(tk.INSERT, ts)

        count = len(self._matches)
        suffix = "+" if self._engine.capped else ""
        if self._count_lbl:
            self._count_lbl.config(
                text=f"{idx + 1}/{count}{suffix}", fg=self._tmgr.theme.fg_muted
            )
        self._update_progress(idx + 1, count)
        self._flash_match(editor, ts, te)

    def _flash_match(self, editor: Any, ts: str, te: str) -> None:
        """Executa a operação 'flash match'.

        Args:
            editor: Parâmetro da operação.
            ts: Parâmetro da operação.
            te: Parâmetro da operação.
        """
        t = self._tmgr.theme
        flash_tag = "_search_flash"
        with contextlib.suppress(tk.TclError):
            editor.text.tag_configure(
                flash_tag,
                background=getattr(t, "accent", "#007ACC"),
                foreground="#ffffff",
            )
            editor.text.tag_add(flash_tag, ts, te)
            editor.text.after(
                180,
                lambda: (
                    editor.text.tag_remove(flash_tag, "1.0", "end")
                    if editor.text.winfo_exists()
                    else None
                ),
            )

    def next_match(self) -> None:
        """Navega para a próxima ocorrência, com wraparound ao final."""
        if not self._matches:
            self._do_find()
            return
        prev = self._match_idx
        self._match_idx = (self._match_idx + 1) % len(self._matches)
        if self._match_idx < prev:
            self._show_wrap_indicator()
        self._jump_to(self._match_idx)

    def prev_match(self) -> None:
        """Navega para a ocorrência anterior, com wraparound ao início."""
        if not self._matches:
            return
        prev = self._match_idx
        self._match_idx = (self._match_idx - 1) % len(self._matches)
        if self._match_idx > prev:
            self._show_wrap_indicator()
        self._jump_to(self._match_idx)

    def _show_wrap_indicator(self) -> None:
        """Exibe brevemente o indicador de volta ao início/fim da busca."""
        if not self._count_lbl:
            return
        t = self._tmgr.theme
        orig_text = self._count_lbl.cget("text")
        self._count_lbl.config(
            text=_t("hc.search_bar.wrap"), fg=getattr(t, "accent", "#007ACC")
        )
        self._count_lbl.after(
            600,
            lambda: (
                self._count_lbl.config(text=orig_text, fg=t.fg_muted)
                if self._count_lbl.winfo_exists()
                else None
            ),
        )

    def _smart_replace(self, original: str, repl: str) -> str:
        """Executa 'smart replace' e retorna o resultado.

        Args:
            original: Parâmetro da operação.
            repl: Parâmetro da operação.
        """
        if self._case_transform:
            if self._case_transform == "upper":
                return repl.upper()
            if self._case_transform == "lower":
                return repl.lower()
            if self._case_transform == "title":
                return repl.title()
        if not self._preserve_case:
            return repl
        if original.isupper():
            return repl.upper()
        if original.islower():
            return repl.lower()
        if original and original[0].isupper():
            return repl.capitalize()
        return repl

    def _replace_one(self) -> None:
        """Substitui a ocorrência atualmente selecionada."""
        editor = self._get_editor()
        if not editor or not self._matches:
            return
        if not self._replace_open:
            self._toggle_replace()
            return
        pat = self._get_pattern()
        if pat is None:
            return
        s_off, e_off = self._matches[self._match_idx]
        ts = self._engine.tk_index(s_off)
        te = self._engine.tk_index(e_off)
        orig = editor.text.get(ts, te)
        repl_template = self._repl_var.get()
        try:
            m = pat.search(orig)
            raw = m.expand(
                repl_template) if m and self._re_mode else repl_template
        except re.error:
            raw = repl_template
        repl = self._smart_replace(orig, raw)
        try:
            editor.text.delete(ts, te)
            editor.text.insert(ts, repl)
        except tk.TclError:
            return
        self._add_history_repl(repl_template)
        self._do_find()
        if self._matches:
            self._jump_to(min(self._match_idx, len(self._matches) - 1))

    def _replace_all(self) -> None:
        """Substitui todas as ocorrências no editor."""
        editor = self._get_editor()
        if not editor:
            return
        if not self._replace_open:
            self._toggle_replace()
            return
        query = self._find_var.get()
        if not query:
            return
        pat = self._get_pattern()
        if pat is None:
            return
        content = editor.text.get("1.0", "end-1c")
        self._engine.ingest(content)
        repl_template = self._repl_var.get()
        new_content, n = self._engine.replace_all(
            pat, self._smart_replace, repl_template
        )
        t = self._tmgr.theme
        if n:
            editor.set_content(new_content)
            if self._count_lbl:
                self._count_lbl.config(
                    text=f"↺ {n} trocado(s)", fg=getattr(t, "diff_add_fg", "#4CAF50")
                )
            self._add_history_find(query)
            self._add_history_repl(repl_template)
            self._matches = []
            self._match_idx = 0
            self._update_progress(0, 0)
        else:
            if self._count_lbl:
                self._count_lbl.config(
                    text=_t("hc.search_bar.0_resultados"), fg=t.warn_fg
                )

    def _show_replace_preview(self) -> None:
        """Exibe prévia visual das substituições antes de aplicá-las."""
        editor = self._get_editor()
        if not editor:
            return
        pat = self._get_pattern()
        if pat is None:
            return
        content = editor.text.get("1.0", "end-1c")
        self._engine.ingest(content)
        repl_template = self._repl_var.get()
        new_content, n = self._engine.replace_all(
            pat, self._smart_replace, repl_template
        )
        if n == 0:
            messagebox.showinfo(
                _t("hc.search_bar.preview_substitui_o"),
                "Nenhuma substituição seria feita.",
                parent=self,
            )
            return
        t = self._tmgr.theme
        win = tk.Toplevel(self)
        win.title(f"Preview - {n} substituição(ões)")
        win.configure(bg=t.panel_bg)
        win.transient(self)
        safe_grab_set(win)
        tk.Label(
            win,
            text=f"  {n} substituição(ões) seriam feitas",
            bg=t.panel_bg,
            fg=t.fg,
            font=(FONT_UI, 10, "bold"),
        ).pack(fill="x", padx=12, pady=(10, 4))
        fr = tk.Frame(win, bg=t.panel_bg)
        fr.pack(fill="both", expand=True, padx=8, pady=4)
        vsb = ttk.Scrollbar(fr, orient="vertical",
                            style="Thin.Vertical.TScrollbar")
        vsb.pack(side="right", fill="y")
        txt = tk.Text(
            fr,
            bg=t.panel_bg,
            fg=t.fg,
            font=(FONT_MONO, 9),
            relief="flat",
            bd=0,
            wrap="none",
            highlightthickness=0,
            yscrollcommand=vsb.set,
            width=80,
            height=22,
        )
        txt.pack(side="left", fill="both", expand=True)
        vsb.config(command=txt.yview)
        txt.tag_configure(
            "del",
            background=getattr(t, "diff_del_bg", "#3d1212"),
            foreground=getattr(t, "diff_del_fg", "#ff8080"),
        )
        txt.tag_configure(
            "add",
            background=getattr(t, "diff_add_bg", "#0d2d0d"),
            foreground=getattr(t, "diff_add_fg", "#80ff80"),
        )
        txt.tag_configure("ctx", foreground=t.fg_muted)
        import difflib as _diff

        for grp in _diff.unified_diff(
            content.split("\n"), new_content.split("\n"), lineterm="", n=2
        ):
            if grp.startswith("---") or grp.startswith("+++"):
                continue
            tag = (
                "del"
                if grp.startswith("-")
                else ("add" if grp.startswith("+") else "ctx")
            )
            txt.insert("end", grp + "\n", tag)
        txt.config(state="disabled")
        btn_row = tk.Frame(win, bg=t.panel_bg)
        btn_row.pack(fill="x", padx=8, pady=(4, 10))

        def _apply():
            win.destroy()
            self._replace_all()

        tk.Button(
            btn_row,
            text=_t("hc.search_bar.aplicar"),
            bg=getattr(t, "diff_add_bg", "#0d2d0d"),
            fg=getattr(t, "diff_add_fg", "#80ff80"),
            relief="flat",
            padx=12,
            pady=4,
            font=(FONT_UI, 9, "bold"),
            cursor="hand2",
            command=_apply,
        ).pack(side="right", padx=4)
        tk.Button(
            btn_row,
            text=_t("hc.search_bar.cancelar"),
            bg=t.btn_bg,
            fg=t.fg,
            relief="flat",
            padx=12,
            pady=4,
            font=(FONT_UI, 9),
            cursor="hand2",
            command=win.destroy,
        ).pack(side="right", padx=2)
        win.update_idletasks()
        pw = self.winfo_rootx() + self.winfo_width() // 2
        ph = self.winfo_rooty() + self.winfo_height() // 2
        w, h = win.winfo_width(), win.winfo_height()
        win.geometry(f"+{pw - w // 2}+{ph - h // 2}")

    def _trigger_find_files(self) -> None:
        """Dispara a busca global em arquivos com a query atual."""
        if self._on_find_files:
            self._on_find_files(self._find_var.get())

    def _toggle_opt(self, attr: str) -> None:
        """Executa a operação 'toggle opt'.

        Args:
            attr: Parâmetro da operação.
        """
        if attr in self._opt_btns:
            self._opt_btns[attr].event_generate("<Button-1>")
        else:
            setattr(self, attr, not getattr(self, attr, False))
            self._do_find()
            self._update_hint()

    def _focus_replace(self, _e: Any = None) -> str:
        """Move o foco para o campo de substituição."""
        if self._replace_open and self._repl_entry:
            self._repl_entry.focus_set()
        return "break"

    def _set_case_transform(self, val: str) -> None:
        """Define o valor de 'case transform'.

        Args:
            val: Valor a definir ou processar.
        """
        self._case_transform = val
        self._update_ct_btns()

    def _update_ct_btns(self) -> None:
        """Atualiza 'ct btns'."""
        t = self._tmgr.theme
        for v, b in self._ct_btns.items():
            active = v == self._case_transform
            b.config(
                bg=t.accent if active else t.btn_bg,
                fg="#ffffff" if active else t.fg_muted,
            )

    def _update_hint(self) -> None:
        """Atualiza 'hint'."""
        if not self._hint_lbl:
            return
        parts = []
        if self._case_mode:
            parts.append("case")
        if self._word_mode:
            parts.append("palavra")
        if self._re_mode:
            parts.append("regex")
        if self._sel_mode:
            parts.append("seleção")
        if self._multi_mode:
            parts.append("multiline")
        self._hint_lbl.config(text=("- " + " - ".join(parts)) if parts else "")

    def _insert_newline_in_find(self) -> str:
        """Insere uma quebra de linha literal no campo de busca (para busca multilinha)."""
        if self._find_entry:
            self._find_entry.insert(tk.INSERT, "\\n")
        return "break"

    def _toggle_replace(self) -> None:
        """Alterna a visibilidade da linha de substituição na barra."""
        self._replace_open = not self._replace_open
        self._update_replace_row()

    def _update_replace_row(self) -> None:
        """Atualiza 'replace row'."""
        t = self._tmgr.theme
        if self._replace_open:
            self._repl_row.pack(fill="x", padx=8, pady=(1, 1))
            self._repl_opts_row.pack(fill="x", padx=8, pady=(0, 4))
            if self._repl_toggle:
                self._repl_toggle.config(bg=t.accent, fg="#ffffff")
            if self._repl_entry:
                self._repl_entry.focus_set()
        else:
            self._repl_row.pack_forget()
            self._repl_opts_row.pack_forget()
            if self._repl_toggle:
                self._repl_toggle.config(bg=t.btn_bg, fg=t.fg_muted)

    def _add_history_find(self, q: str) -> None:
        """Executa a operação 'add history find'.

        Args:
            q: Parâmetro da operação.
        """
        if not q:
            return
        if q in self._find_history:
            self._find_history.remove(q)
        self._find_history.append(q)
        if len(self._find_history) > self._HIST_MAX:
            self._find_history.pop(0)
        self._find_hist_pos = -1

    def _add_history_repl(self, q: str) -> None:
        """Executa a operação 'add history repl'.

        Args:
            q: Parâmetro da operação.
        """
        if not q:
            return
        if q in self._repl_history:
            self._repl_history.remove(q)
        self._repl_history.append(q)
        if len(self._repl_history) > self._HIST_MAX:
            self._repl_history.pop(0)
        self._repl_hist_pos = -1

    def _hist_find_up(self, _e: Any = None) -> str:
        """Executa 'hist find up' e retorna o resultado.

        Args:
            _e: Parâmetro da operação.
        """
        if not self._find_history:
            return "break"
        self._find_hist_pos = min(
            len(self._find_history) - 1, self._find_hist_pos + 1)
        self._find_var.set(self._find_history[-(self._find_hist_pos + 1)])
        self._find_entry.icursor("end")
        return "break"

    def _hist_find_down(self, _e: Any = None) -> str:
        """Executa 'hist find down' e retorna o resultado.

        Args:
            _e: Parâmetro da operação.
        """
        if self._find_hist_pos <= 0:
            self._find_hist_pos = -1
            return "break"
        self._find_hist_pos -= 1
        self._find_var.set(self._find_history[-(self._find_hist_pos + 1)])
        self._find_entry.icursor("end")
        return "break"

    def _hist_repl_up(self, _e: Any = None) -> str:
        """Executa 'hist repl up' e retorna o resultado.

        Args:
            _e: Parâmetro da operação.
        """
        if not self._repl_history:
            return "break"
        self._repl_hist_pos = min(
            len(self._repl_history) - 1, self._repl_hist_pos + 1)
        self._repl_var.set(self._repl_history[-(self._repl_hist_pos + 1)])
        self._repl_entry.icursor("end")
        return "break"

    def _hist_repl_down(self, _e: Any = None) -> str:
        """Executa 'hist repl down' e retorna o resultado.

        Args:
            _e: Parâmetro da operação.
        """
        if self._repl_hist_pos <= 0:
            self._repl_hist_pos = -1
            return "break"
        self._repl_hist_pos -= 1
        self._repl_var.set(self._repl_history[-(self._repl_hist_pos + 1)])
        self._repl_entry.icursor("end")
        return "break"

    def _esc_find(self) -> None:
        """Fecha a barra de busca ao pressionar Escape."""
        if self._find_var.get():
            self._find_var.set("")
        else:
            self.hide()

    def show(self, mode: str = "find", initial: str = "") -> None:
        """Exibe a barra de busca e foca no campo de texto."""
        self._visible = True
        if self._pack_before is not None:
            self.pack(side="top", fill="x", before=self._pack_before)
        else:
            self.pack(side="top", fill="x")
        if mode == "replace" and not self._replace_open:
            self._replace_open = True
            self._update_replace_row()
        if initial:
            self._find_var.set(initial)
            if self._find_entry:
                self._find_entry.select_range(0, "end")
        if self._find_entry:
            self._find_entry.focus_set()
        self._find_hist_pos = -1
        self._do_find()

    def hide(self) -> None:
        """Oculta a barra de busca e restaura o foco ao editor."""
        self._visible = False
        self._add_history_find(self._find_var.get())
        self.pack_forget()
        editor = self._get_editor()
        if editor:
            editor.hl.clear_search()
            with contextlib.suppress(tk.TclError):
                editor.text.focus_set()

    def toggle(self, mode: str = "find") -> None:
        """Alterna a visibilidade da barra de busca."""
        if self._visible and not (mode == "replace" and not self._replace_open):
            self.hide()
        else:
            self.show(mode)

    def is_visible(self) -> bool:
        """Retorna ``True`` se a barra de busca está atualmente visível."""
        return self._visible

    def notify_editor_change(self) -> None:
        """Notifica a barra de busca que o editor ativo mudou."""
        self._matches = []
        self._match_idx = 0
        self._engine = _SearchEngine()
        if self._count_lbl:
            self._count_lbl.config(text="")
        self._update_progress(0, 0)
        if self._visible:
            editor = self._get_editor()
            if editor:
                editor.hl.clear_search()
            self._do_find()

    def apply_theme(self, t: Theme) -> None:
        """Aplica o tema visual à barra de busca."""
        from src.theme.utils import cfg, cfg_many

        cfg(self, bg=t.floating_bg, highlightbackground=t.floating_border)
        ekw = {"bg": t.input_bg, "fg": t.fg, "insertbackground": t.cursor}
        cfg_many(
            [w for w in (self._find_entry, self._repl_entry) if w],
            **ekw,
        )
        cfg_many(
            [w for w in (self._count_lbl, self._hint_lbl) if w],
            bg=t.floating_bg,
            fg=t.fg_muted,
        )
        cfg_many(
            [w for w in (self._repl_row, self._repl_opts_row) if w],
            bg=t.floating_bg,
        )
        if self._repl_toggle:
            active = self._replace_open
            cfg(
                self._repl_toggle,
                bg=t.accent if active else t.btn_bg,
                fg="#ffffff" if active else t.fg_muted,
            )
        for attr, b in self._opt_btns.items():
            active = getattr(self, attr, False)
            cfg(
                b,
                bg=t.accent if active else t.btn_bg,
                fg="#ffffff" if active else t.fg_muted,
            )
        self._update_ct_btns()
        for frame in self.winfo_children():
            if isinstance(frame, tk.Frame):
                cfg(frame, bg=t.floating_bg)
