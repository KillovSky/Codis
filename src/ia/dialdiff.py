"""Codis IA - DiálogoDiff: confirma alterações geradas pela IA antes de aplicar.

Fluxo de uso
------------
1. IA gera código em um ou mais blocos markdown
2. O sistema detecta os blocos e abre este diálogo
3. Usuário vê o diff (antes/depois) com realce de adições/remoções
4. Pode aceitar tudo, rejeitar tudo, ou aceitar bloco a bloco
5. Ao aceitar, o código é inserido no editor na posição correta

Modos de inserção
-----------------
    replace_selection - substitui o texto selecionado no editor
    insert_cursor     - insere na posição atual do cursor
    new_tab           - abre em nova aba sem modificar o arquivo atual
"""

from __future__ import annotations

import contextlib
import logging
import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox
from typing import Any

from src.constants import FONT_MONO, FONT_UI
from src.diff.engine import DiffEngine
from src.editor.diff_pane import DiffPane
from src.i18n import _t
from src.ia.toolbox import extract_code_blocks
from src.ui.grab_safe import safe_grab_set

logger = logging.getLogger("Codis.IA.Diff")

_INSERTION_MODES: list[tuple[str, str]] = [
    ("replace_selection", "ia.diff_mode_selection"),
    ("replace_all", "ia.diff_mode_file"),
    ("insert_cursor", "ia.diff_mode_cursor"),
    ("new_tab", "ia.diff_mode_new_tab"),
]


class DiffDialog(tk.Toplevel):
    """
    Janela modal que exibe o diff entre o código atual e o código gerado,
    permitindo aceitar ou rejeitar antes de aplicar ao editor.

    Parâmetros
    ----------
    parent            : widget pai (janela principal).
    tmgr              : gerenciador de temas.
    blocks            : lista de (linguagem, código) extraída da resposta da IA.
    cfg               : gerenciador de configurações (repassado ao DiffPane).
    get_editor        : callable que retorna o EditorPane ativo.
    on_open_new_tab   : callable(código) que abre o código em nova aba.
    """

    def __init__(
        self,
        parent: tk.Widget,
        tmgr: Any,
        blocks: list[tuple[str, str]],
        cfg: Any = None,
        get_editor: Callable[[], Any] | None = None,
        on_open_new_tab: Callable[[str], None] | None = None,
    ) -> None:
        """Inicializa o diálogo de diff com sugestões de IA."""
        super().__init__(parent)
        t = tmgr.theme
        self._tmgr = tmgr
        self._cfg = cfg
        self._blocks = blocks
        self._get_editor = get_editor
        self._on_new_tab = on_open_new_tab
        self._current_idx = 0
        self._accepted = False
        self._mode_var = tk.StringVar(value="replace_selection")

        self._detect_default_mode()

        self.title(_t("ia.diff_title"))
        self.configure(bg=t.panel_bg)
        self.transient(parent)
        safe_grab_set(self)
        self.resizable(True, True)
        self.geometry("900x640")

        self._build()
        self._show_block(0)

        self.update_idletasks()
        px = parent.winfo_rootx() + parent.winfo_width() // 2
        py = parent.winfo_rooty() + parent.winfo_height() // 2
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry(f"+{px - w // 2}+{py - h // 2}")
        self.wait_window()

    def _build(self) -> None:
        """Constrói os widgets do diálogo de diff com áreas de texto e botões de ação."""
        t = self._tmgr.theme

        hdr = tk.Frame(self, bg=t.panel_bg, padx=12, pady=8)
        hdr.pack(fill="x")
        tk.Label(
            hdr,
            text=_t("ia.diff_header"),
            bg=t.panel_bg,
            fg=t.fg,
            font=(FONT_UI, 11, "bold"),
        ).pack(side="left")

        self._lbl_counter = tk.Label(
            hdr,
            text="",
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
        )
        self._lbl_counter.pack(side="right")

        tk.Frame(self, bg=t.border, height=1).pack(fill="x")

        toolbar = tk.Frame(self, bg=t.panel_bg, padx=10, pady=6)
        toolbar.pack(fill="x")
        tk.Label(
            toolbar,
            text=_t("ia.diff_apply_as"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
        ).pack(side="left", padx=(0, 8))

        for value, i18n_key in _INSERTION_MODES:
            rb = tk.Radiobutton(
                toolbar,
                text=_t(i18n_key),
                variable=self._mode_var,
                value=value,
                bg=t.panel_bg,
                fg=t.fg,
                activebackground=t.panel_bg,
                selectcolor=t.sel_bg,
                font=(FONT_UI, 8),
                cursor="hand2",
                command=self._refresh_diff,
            )
            rb.pack(side="left", padx=4)

        tk.Frame(self, bg=t.border, height=1).pack(fill="x")

        info_row = tk.Frame(self, bg=t.editor_bg, padx=12, pady=4)
        info_row.pack(fill="x")
        self._lbl_lang = tk.Label(
            info_row,
            text="",
            bg=t.editor_bg,
            fg=t.fg_muted,
            font=(FONT_MONO, 8),
        )
        self._lbl_lang.pack(side="left")

        self._btn_prev = tk.Label(
            info_row,
            text=_t("ia.diff_prev"),
            bg=t.editor_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            cursor="hand2",
        )
        self._btn_prev.pack(side="right", padx=4)
        self._btn_prev.bind("<Button-1>", lambda _: self._navigate(-1))

        self._btn_next = tk.Label(
            info_row,
            text=_t("ia.diff_next"),
            bg=t.editor_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            cursor="hand2",
        )
        self._btn_next.pack(side="right", padx=4)
        self._btn_next.bind("<Button-1>", lambda _: self._navigate(+1))

        self._diff = DiffPane(self, self._tmgr, self._cfg)
        self._diff.pack(fill="both", expand=True)

        tk.Frame(self, bg=t.border, height=1).pack(fill="x")

        btns = tk.Frame(self, bg=t.panel_bg, padx=12, pady=8)
        btns.pack(fill="x")

        tk.Button(
            btns,
            text=_t("ia.diff_reject"),
            command=self._reject,
            bg=t.panel_bg,
            fg=getattr(t, "err_fg", "#f44336"),
            relief="flat",
            padx=12,
            pady=5,
            font=(FONT_UI, 9),
            cursor="hand2",
            highlightthickness=1,
            highlightbackground=getattr(t, "err_fg", "#f44336"),
        ).pack(side="left")

        tk.Button(
            btns,
            text=_t("ia.diff_accept_all"),
            command=self._accept_all,
            bg=getattr(t, "accent", "#4fc3f7"),
            fg="#ffffff",
            relief="flat",
            padx=14,
            pady=5,
            font=(FONT_UI, 9, "bold"),
            cursor="hand2",
        ).pack(side="right", padx=4)

        if len(self._blocks) > 1:
            tk.Button(
                btns,
                text=_t("ia.diff_accept_one"),
                command=self._accept_current,
                bg=t.btn_bg,
                fg=t.fg,
                relief="flat",
                padx=12,
                pady=5,
                font=(FONT_UI, 9),
                cursor="hand2",
                highlightthickness=1,
                highlightbackground=t.border,
            ).pack(side="right", padx=4)

        self._lbl_stats = tk.Label(
            btns,
            text="",
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        )
        self._lbl_stats.pack(side="right", padx=8)

    def _detect_default_mode(self) -> None:
        """Define o modo de inserção padrão baseado no contexto atual do editor."""
        editor = self._get_editor() if self._get_editor else None
        if editor is None:
            self._mode_var.set("new_tab")
            return

        with contextlib.suppress(tk.TclError):
            editor.text.get(tk.SEL_FIRST, tk.SEL_LAST)
            self._mode_var.set("replace_selection")
            return

        self._mode_var.set("insert_cursor")

    def _show_block(self, idx: int) -> None:
        """Exibe o bloco de código no índice indicado."""
        if not self._blocks:
            return
        self._current_idx = idx
        lang, _code = self._blocks[idx]

        total = len(self._blocks)
        self._lbl_counter.config(
            text=_t("ia.diff_block", current=idx + 1, total=total))
        self._lbl_lang.config(text=_t("ia.diff_lang", lang=lang or "texto"))

        active_color = self._tmgr.theme.fg
        inactive_color = self._tmgr.theme.fg_muted
        self._btn_prev.config(fg=active_color if idx > 0 else inactive_color)
        self._btn_next.config(fg=active_color if idx <
                              total - 1 else inactive_color)

        self._refresh_diff()

    def _refresh_diff(self) -> None:
        """Recalcula e renderiza o diff conforme o modo de inserção selecionado."""
        if not self._blocks:
            return
        _, new_code = self._blocks[self._current_idx]
        mode = self._mode_var.get()
        editor = self._get_editor() if self._get_editor else None
        original = ""
        original_name = _t("ia.diff_orig_full")
        new_name = _t("ia.diff_generated")

        if editor and mode in ("replace_selection", "replace_all", "insert_cursor"):
            if mode == "replace_selection":
                with contextlib.suppress(tk.TclError):
                    original = editor.text.get(tk.SEL_FIRST, tk.SEL_LAST)
                if not original:
                    original = editor.text.get("1.0", "end-1c")
                    original_name = _t("ia.diff_orig_full")
                else:
                    original_name = _t("ia.diff_orig_sel")
            elif mode == "replace_all":
                original = editor.text.get("1.0", "end-1c")
                original_name = _t("ia.diff_orig_full")
            elif mode == "insert_cursor":
                original = ""
                original_name = _t("ia.diff_orig_insert")

        self._diff.show(
            original,
            new_code,
            orig_name=original_name,
            mod_name=new_name,
        )

        stats = DiffEngine.stats(original, new_code)
        self._lbl_stats.config(
            text="+" + str(stats[0]) + "  \u2212" + str(stats[1]))

    def _navigate(self, delta: int) -> None:
        """Avança ou recua entre os blocos de código."""
        new_idx = self._current_idx + delta
        if 0 <= new_idx < len(self._blocks):
            self._show_block(new_idx)

    def _accept_all(self) -> None:
        """Aceita todas as alterações propostas, substituindo o conteúdo original."""
        for idx in range(len(self._blocks)):
            self._apply_block(idx)
        self._accepted = True
        self.destroy()

    def _accept_current(self) -> None:
        """Aplica apenas o bloco atual e avança para o próximo."""
        self._apply_block(self._current_idx)
        next_idx = self._current_idx + 1
        if next_idx < len(self._blocks):
            self._show_block(next_idx)
        else:
            self._accepted = True
            self.destroy()

    def _reject(self) -> None:
        """Rejeita todas as alterações e fecha sem modificar o editor."""
        self._accepted = False
        self.destroy()

    def _apply_block(self, idx: int) -> None:
        """Aplica um bloco ao editor conforme o modo de inserção selecionado."""
        _, code = self._blocks[idx]
        mode = self._mode_var.get()
        editor = self._get_editor() if self._get_editor else None

        if mode == "new_tab":
            if self._on_new_tab:
                self._on_new_tab(code)
            return

        if editor is None:
            messagebox.showwarning(
                "IA",
                _t("ia.err_no_editor"),
                parent=self,
            )
            return

        with contextlib.suppress(tk.TclError):
            if mode == "replace_selection":
                try:
                    editor.text.delete(tk.SEL_FIRST, tk.SEL_LAST)
                    editor.text.insert(tk.INSERT, code)
                except tk.TclError:
                    editor.text.insert(tk.INSERT, code)
            elif mode == "replace_all":
                editor.text.delete("1.0", "end")
                editor.text.insert("1.0", code)
            elif mode == "insert_cursor":
                editor.text.insert(tk.INSERT, code)

    @property
    def accepted(self) -> bool:
        """True se o usuário aceitou ao menos um bloco."""
        return self._accepted


def confirm_and_apply(
    parent: tk.Widget,
    tmgr: Any,
    ai_response: str,
    cfg: Any = None,
    get_editor: Callable[[], Any] | None = None,
    on_open_new_tab: Callable[[str], None] | None = None,
) -> bool:
    """
    Extrai blocos de código da resposta da IA e abre o diálogo de confirmação.

    Retorna True se o usuário aceitou algum bloco, False se rejeitou tudo
    ou se não havia blocos de código na resposta.
    """
    blocks = extract_code_blocks(ai_response)
    if not blocks:
        return False
    dlg = DiffDialog(
        parent,
        tmgr,
        blocks,
        cfg=cfg,
        get_editor=get_editor,
        on_open_new_tab=on_open_new_tab,
    )
    return dlg.accepted
