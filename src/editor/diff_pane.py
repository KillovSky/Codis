"""Painel de diff visual do Codis.

Implementa ``DiffPane``, um widget ``tk.Frame`` que exibe duas versões de
um arquivo lado a lado com realce de hunks adicionados, removidos e
modificados, usando o motor ``DiffEngine``.
"""

from __future__ import annotations

import contextlib
import logging
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, ttk

from src.config import ConfigManager
from src.constants import FONT_MONO, FONT_SIZE, FONT_UI
from src.diff.engine import DiffEngine, DiffHunk
from src.i18n import _t
from src.theme.base import Theme
from src.theme.manager import ThemeManager

logger = logging.getLogger("Codis")


class DiffPane(tk.Frame):
    """
    Painel de visualização de diferenças (diff) entre dois textos.

    Suporta dois modos de exibição:
      - **inline**: linhas alteradas são mostradas uma após a outra, com
        realce intra‑linha (caractere a caractere) para substituições.
      - **side‑by‑side**: duas colunas (original e modificada) com linhas
        sincronizadas verticalmente.

    Funcionalidades:
      - Navegação entre hunks (blocos de diferença).
      - Estatísticas de adições, deleções e alterações.
      - Cópia do diff unificado para a área de transferência.
      - Salvamento do diff em arquivo .patch.
      - Sincronização correta de rolagem no modo side‑by‑side.
      - Aplicação dinâmica de temas.

    Attributes:
        _mode (str): Modo atual ("inline" ou "side").
        _hunks (List[DiffHunk]): Lista de hunks do diff atual.
        _hunk_idx (int): Índice do hunk atualmente destacado (para navegação).
        _original (str): Conteúdo original.
        _modified (str): Conteúdo modificado.
        _orig_name (str): Nome do arquivo original (exibido nos cabeçalhos).
        _mod_name (str): Nome do arquivo modificado.
        _syncing (bool): Flag interna para evitar recursão na sincronização de scroll.
        _inline_txt (tk.Text | None): Widget de texto do modo inline.
        _left_txt (tk.Text | None): Widget de texto esquerdo no modo side.
        _right_txt (tk.Text | None): Widget de texto direito no modo side.
    """

    def __init__(
        self,
        parent: tk.Widget,
        tmgr: ThemeManager,
        cfg: ConfigManager,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        """
        Inicializa o painel de diff.

        Args:
            parent: Widget pai (normalmente uma janela ou frame).
            tmgr: Gerenciador de temas (fornece o tema atual).
            cfg: Gerenciador de configurações.
            on_close: Callable opcional chamado quando o botão de fechar é clicado.
        """
        t = tmgr.theme
        super().__init__(parent, bg=t.panel_bg)
        self._tmgr = tmgr
        self._cfg = cfg
        self._on_close = on_close
        self._mode = "inline"
        self._hunks: list[DiffHunk] = []
        self._hunk_idx = 0
        self._original = ""
        self._modified = ""
        self._orig_name = _t("ui.diff_original")
        self._mod_name = _t("ui.diff_modified")
        self._syncing = False

        self._inline_txt: tk.Text | None = None
        self._left_txt: tk.Text | None = None
        self._right_txt: tk.Text | None = None

        self._build_ui()

    def _build_ui(self) -> None:
        """Constrói a barra de ferramentas e o contêiner principal."""
        t = self._tmgr.theme

        tb = tk.Frame(self, bg=t.panel_bg, height=32)
        tb.pack(fill="x")
        tb.pack_propagate(False)

        def _create_toolbar_button(
            text: str, command: Callable[[], None], accent: bool = False
        ) -> tk.Label:
            """Cria um botão (label simulando botão) na toolbar."""
            bg = t.accent if accent else t.btn_bg
            fg = "#000" if accent else t.fg
            lbl = tk.Label(
                tb,
                text=text,
                bg=bg,
                fg=fg,
                font=(FONT_UI, 9),
                cursor="hand2",
                padx=8,
                pady=2,
                relief="flat",
            )
            lbl.pack(side="left", padx=2, pady=4)
            lbl.bind("<Button-1>", lambda _: command())
            lbl.bind("<Enter>", lambda _: lbl.config(bg=t.btn_hover))
            lbl.bind("<Leave>", lambda _: lbl.config(bg=bg))
            return lbl

        self._inline_btn = _create_toolbar_button(
            "⌃ Inline", lambda: self.set_mode("inline"), accent=(self._mode == "inline")
        )
        self._side_btn = _create_toolbar_button(
            "⊟ Side", lambda: self.set_mode("side"), accent=(self._mode == "side")
        )

        tk.Frame(tb, bg=t.border, width=1).pack(
            side="left", fill="y", padx=4, pady=4)

        _create_toolbar_button("↑ Prev", self.prev_hunk)
        _create_toolbar_button("↓ Next", self.next_hunk)

        tk.Frame(tb, bg=t.border, width=1).pack(
            side="left", fill="y", padx=4, pady=4)

        _create_toolbar_button("📋 Patch", self.copy_patch)
        _create_toolbar_button("💾 Save", self.save_patch)

        self._stat_lbl = tk.Label(
            tb, text="", bg=t.panel_bg, fg=t.fg_muted, font=(FONT_MONO, 9)
        )
        self._stat_lbl.pack(side="left", padx=8)

        if self._on_close:
            close_btn = tk.Label(
                tb,
                text="✕",
                bg=t.panel_bg,
                fg=t.err_fg,
                font=(FONT_UI, 10),
                cursor="hand2",
                padx=6,
            )
            close_btn.pack(side="right")
            close_btn.bind("<Button-1>", lambda _: self._on_close())

        tk.Frame(self, bg=t.border, height=1).pack(fill="x")

        self._content_frame = tk.Frame(self, bg=t.panel_bg)
        self._content_frame.pack(fill="both", expand=True)
        self._build_inline_view()

    def _build_inline_view(self) -> None:
        """Constrói a visualização inline (única caixa de texto)."""
        t = self._tmgr.theme
        for w in self._content_frame.winfo_children():
            w.destroy()

        self._inline_txt = tk.Text(
            self._content_frame,
            bg=t.editor_bg,
            fg=t.fg,
            relief="flat",
            bd=0,
            font=(FONT_MONO, self._cfg.get("font_size", FONT_SIZE) - 1),
            state="disabled",
            highlightthickness=0,
            wrap="none",
            padx=8,
            pady=4,
        )
        vsb = ttk.Scrollbar(
            self._content_frame,
            orient="vertical",
            command=self._inline_txt.yview,
            style="Thin.Vertical.TScrollbar",
        )
        hsb = ttk.Scrollbar(
            self._content_frame,
            orient="horizontal",
            command=self._inline_txt.xview,
            style="Thin.Horizontal.TScrollbar",
        )
        self._inline_txt.config(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.pack(side="right", fill="y")
        hsb.pack(side="bottom", fill="x")
        self._inline_txt.pack(fill="both", expand=True)
        self._configure_inline_tags(t)

    def _configure_inline_tags(self, t) -> None:

        if self._inline_txt is None:
            return
        txt = self._inline_txt
        fs = self._cfg.get("font_size", FONT_SIZE) - 1
        txt.tag_configure("add", background=t.diff_add_bg,
                          foreground=t.diff_add_fg)
        txt.tag_configure("del", background=t.diff_del_bg,
                          foreground=t.diff_del_fg)
        txt.tag_configure(
            "hunk",
            background=t.diff_hunk_bg,
            foreground=t.diff_hunk_fg,
            font=(FONT_MONO, fs, "bold"),
        )
        txt.tag_configure("equal", foreground=t.fg_muted)
        txt.tag_configure("add_char", background="#0e5e0e",
                          foreground=t.diff_add_fg)
        txt.tag_configure("del_char", background="#5e0e0e",
                          foreground=t.diff_del_fg)
        txt.tag_configure("cur_hunk", background=t.sel_bg)
        txt.tag_configure("lineno", foreground=t.fg_dim,
                          font=(FONT_MONO, fs - 1))

    def _build_side_view(self) -> None:
        """Constrói a visualização side‑by‑side (dois textos lado a lado)."""
        t = self._tmgr.theme
        for w in self._content_frame.winfo_children():
            w.destroy()

        paned = tk.PanedWindow(
            self._content_frame,
            orient="horizontal",
            bg=t.border,
            sashwidth=4,
            sashrelief="flat",
        )
        paned.pack(fill="both", expand=True)

        def _create_side_panel(title: str) -> tuple[tk.Frame, tk.Text]:
            """Cria um painel lateral com título e texto."""
            frame = tk.Frame(paned, bg=t.panel_bg)
            hdr = tk.Label(
                frame,
                text=f"  {title}",
                bg=t.panel_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 9, "bold"),
                anchor="w",
            )
            hdr.pack(fill="x")
            tk.Frame(frame, bg=t.border, height=1).pack(fill="x")
            inner = tk.Frame(frame, bg=t.editor_bg)
            inner.pack(fill="both", expand=True)
            txt = tk.Text(
                inner,
                bg=t.editor_bg,
                fg=t.fg,
                relief="flat",
                bd=0,
                font=(FONT_MONO, self._cfg.get("font_size", FONT_SIZE) - 1),
                state="disabled",
                wrap="none",
                highlightthickness=0,
                padx=8,
                pady=4,
            )
            vsb = ttk.Scrollbar(
                inner,
                orient="vertical",
                command=txt.yview,
                style="Thin.Vertical.TScrollbar",
            )
            vsb.pack(side="right", fill="y")
            txt.pack(fill="both", expand=True)
            paned.add(frame, stretch="always")
            return frame, txt

        _, self._left_txt = _create_side_panel(self._orig_name)
        _, self._right_txt = _create_side_panel(self._mod_name)

        left_vsb = next(
            w
            for w in self._left_txt.master.winfo_children()
            if isinstance(w, ttk.Scrollbar)
        )
        right_vsb = next(
            w
            for w in self._right_txt.master.winfo_children()
            if isinstance(w, ttk.Scrollbar)
        )

        def _on_left_scroll(first: str, last: str) -> None:
            left_vsb.set(first, last)
            if not self._syncing:
                self._syncing = True
                self._right_txt.yview_moveto(float(first))
                self._syncing = False

        def _on_right_scroll(first: str, last: str) -> None:
            right_vsb.set(first, last)
            if not self._syncing:
                self._syncing = True
                self._left_txt.yview_moveto(float(first))
                self._syncing = False

        self._left_txt.config(yscrollcommand=_on_left_scroll)
        self._right_txt.config(yscrollcommand=_on_right_scroll)

        fs = self._cfg.get("font_size", FONT_SIZE) - 1
        for txt in (self._left_txt, self._right_txt):
            txt.tag_configure("add", background=t.diff_add_bg,
                              foreground=t.diff_add_fg)
            txt.tag_configure("del", background=t.diff_del_bg,
                              foreground=t.diff_del_fg)
            txt.tag_configure("equal", foreground=t.fg_muted)
            txt.tag_configure("empty", background=t.diff_hunk_bg)
            txt.tag_configure("lineno", foreground=t.fg_dim,
                              font=(FONT_MONO, fs - 1))

    def set_mode(self, mode: str) -> None:
        """
        Alterna entre os modos de visualização 'inline' e 'side'.

        Args:
            mode: "inline" ou "side".
        """
        self._mode = mode
        t = self._tmgr.theme

        if hasattr(self, "_inline_btn"):
            self._inline_btn.config(
                bg=t.accent if mode == "inline" else t.btn_bg,
                fg="#000" if mode == "inline" else t.fg,
            )
            self._side_btn.config(
                bg=t.accent if mode == "side" else t.btn_bg,
                fg="#000" if mode == "side" else t.fg,
            )

        if mode == "inline":
            self._build_inline_view()
        else:
            self._build_side_view()

        self.show(self._original, self._modified,
                  self._orig_name, self._mod_name)

    def show(
        self, original: str, modified: str, orig_name: str = "", mod_name: str = ""
    ) -> None:
        """
        Exibe um diff entre dois textos.

        Args:
            original: Texto original (pode ser vazio ou None).
            modified: Texto modificado (pode ser vazio ou None).
            orig_name: Nome do arquivo original (para cabeçalhos).
            mod_name: Nome do arquivo modificado.
        """

        self._original = original or ""
        self._modified = modified or ""
        self._orig_name = orig_name or _t("ui.diff_original")
        self._mod_name = mod_name or _t("ui.diff_modified")

        self._hunks = DiffEngine.hunks(self._original, self._modified)
        self._hunk_idx = 0

        stats = DiffEngine.stats(self._original, self._modified)
        if self._hunks:
            stat_text = f"+{stats[0]}  -{stats[1]}"
        else:
            stat_text = _t("ui.diff_no_changes")
        self._stat_lbl.config(text=stat_text)

        if self._mode == "inline":
            self._render_inline()
        else:
            self._render_side()

    def _render_inline(self) -> None:
        """Renderiza o diff no modo inline."""
        if self._inline_txt is None:
            return
        txt = self._inline_txt
        txt.config(state="normal")
        txt.delete("1.0", "end")

        if not self._hunks:
            txt.insert("end", f"\n  {_t('ui.diff_no_changes')}\n", "hunk")
        else:
            lno_left = lno_right = 1
            for _hunk in self._hunks:
                header = f"@@ -{_hunk.a_start},{_hunk.a_count} +{_hunk.b_start},{_hunk.b_count} @@\n"
                txt.insert("end", header, "hunk")
                lno_left = _hunk.a_start
                lno_right = _hunk.b_start
                i = 0
                lines = _hunk.lines
                while i < len(lines):
                    kind, line = lines[i]
                    if kind == " ":
                        self._insert_equal_line(txt, line, lno_left)
                        lno_left += 1
                        lno_right += 1
                    elif kind == "-":
                        if i + 1 < len(lines) and lines[i + 1][0] == "+":
                            add_line = lines[i + 1][1]
                            self._insert_inline_diff(
                                txt, line, add_line, lno_left, lno_right
                            )
                            lno_left += 1
                            lno_right += 1
                            i += 1
                        else:
                            self._insert_simple_line(txt, "-", line, lno_left)
                            lno_left += 1
                    elif kind == "+":
                        self._insert_simple_line(txt, "+", line, lno_right)
                        lno_right += 1
                    i += 1

        txt.config(state="disabled")

    @staticmethod
    def _insert_equal_line(
        txt: tk.Text, line: str, lno_left: int
    ) -> None:
        """Insere uma linha de contexto (igual em ambos os lados)."""
        ln = f"{lno_left:>4}  "
        txt.insert("end", ln, "lineno")
        txt.insert("end", f"  {line}\n", "equal")

    @staticmethod
    def _insert_simple_line(txt: tk.Text, prefix: str, line: str, lineno: int) -> None:
        """
        Insere uma linha simples de remoção (prefixo '-') ou adição (prefixo '+').

        Args:
            txt: Widget Text.
            prefix: '-' ou '+'.
            line: Conteúdo da linha.
            lineno: Número da linha a ser exibido.
        """
        ln = f"{lineno:>4}  "
        txt.insert("end", ln, "lineno")
        tag = "del" if prefix == "-" else "add"
        txt.insert("end", f"{prefix} {line}\n", tag)

    @staticmethod
    def _insert_inline_diff(
        txt: tk.Text, del_line: str, add_line: str, lno_left: int, lno_right: int
    ) -> None:
        """
        Insere um par de linhas (remoção e adição) com realce intra‑linha.

        Args:
            txt: Widget Text.
            del_line: Linha original (removida).
            add_line: Linha modificada (adicionada).
            lno_left: Número da linha no lado original.
            lno_right: Número da linha no lado modificado.
        """

        parts_del = DiffEngine.inline_diff(del_line, add_line)
        parts_add = DiffEngine.inline_diff(del_line, add_line)

        ln = f"{lno_left:>4}  "
        txt.insert("end", ln, "lineno")
        txt.insert("end", "- ")
        for op, text in parts_del:
            if op == "=":
                txt.insert("end", text, "del")
            elif op == "-":
                txt.insert("end", text, "del_char")

        txt.insert("end", "\n")

        ln = f"{lno_right:>4}  "
        txt.insert("end", ln, "lineno")
        txt.insert("end", "+ ")
        for op, text in parts_add:
            if op == "=":
                txt.insert("end", text, "add")
            elif op == "+":
                txt.insert("end", text, "add_char")

        txt.insert("end", "\n")

    def _render_side(self) -> None:
        """Renderiza o diff no modo side‑by‑side."""
        if self._left_txt is None or self._right_txt is None:
            return
        left = self._left_txt
        right = self._right_txt
        for txt in (left, right):
            txt.config(state="normal")
            txt.delete("1.0", "end")

        if not self._hunks:
            for txt in (left, right):
                txt.insert("end", f"\n  {_t('ui.diff_no_changes')}\n", "equal")
        else:
            for hunk in self._hunks:
                lno_l = hunk.a_start
                lno_r = hunk.b_start
                for kind, line in hunk.lines:
                    if kind == " ":
                        ln_l = f"{lno_l:>4}  "
                        ln_r = f"{lno_r:>4}  "
                        left.insert("end", ln_l, "lineno")
                        left.insert("end", f"  {line}\n", "equal")
                        right.insert("end", ln_r, "lineno")
                        right.insert("end", f"  {line}\n", "equal")
                        lno_l += 1
                        lno_r += 1
                    elif kind == "-":
                        ln_l = f"{lno_l:>4}  "
                        left.insert("end", ln_l, "lineno")
                        left.insert("end", f"- {line}\n", "del")
                        right.insert("end", " " * 6, "lineno")
                        right.insert("end", "\n", "empty")
                        lno_l += 1
                    elif kind == "+":
                        left.insert("end", " " * 6, "lineno")
                        left.insert("end", "\n", "empty")
                        ln_r = f"{lno_r:>4}  "
                        right.insert("end", ln_r, "lineno")
                        right.insert("end", f"+ {line}\n", "add")
                        lno_r += 1

        for txt in (left, right):
            txt.config(state="disabled")

    def next_hunk(self) -> None:
        """Avança para o próximo hunk (cíclico)."""
        if not self._hunks:
            return
        self._hunk_idx = (self._hunk_idx + 1) % len(self._hunks)
        self._scroll_to_hunk(self._hunk_idx)

    def prev_hunk(self) -> None:
        """Retrocede para o hunk anterior (cíclico)."""
        if not self._hunks:
            return
        self._hunk_idx = (self._hunk_idx - 1) % len(self._hunks)
        self._scroll_to_hunk(self._hunk_idx)

    def _scroll_to_hunk(self, idx: int) -> None:
        """
        Rola a visualização para o início do hunk de índice `idx`.

        Args:
            idx: Índice do hunk na lista self._hunks.
        """
        if not self._hunks or idx >= len(self._hunks):
            return
        hunk = self._hunks[idx]
        if self._mode == "inline" and self._inline_txt is not None:
            try:
                total = int(self._inline_txt.index("end").split(".", 1)[0])
                frac = max(0.0, (hunk.a_start - 3) / max(total, 1))
                self._inline_txt.yview_moveto(frac)
            except (tk.TclError, ValueError, AttributeError):
                pass
        elif (
            self._mode == "side"
            and self._left_txt is not None
            and self._right_txt is not None
        ):
            try:
                total = int(self._left_txt.index("end").split(".", 1)[0])
                frac = max(0.0, (hunk.a_start - 3) / max(total, 1))
                self._left_txt.yview_moveto(frac)
                self._right_txt.yview_moveto(frac)
            except (tk.TclError, ValueError, AttributeError):
                pass

    def copy_patch(self) -> None:
        """Copia o diff unificado para a área de transferência."""
        patch = DiffEngine.unified(
            self._original,
            self._modified,
            original_name=self._orig_name,
            modified_name=self._mod_name,
        )
        with contextlib.suppress(tk.TclError):
            self.clipboard_clear()
            self.clipboard_append(patch)

    def save_patch(self) -> None:
        """Abre diálogo para salvar o diff em um arquivo .patch."""
        path = filedialog.asksaveasfilename(
            defaultextension=".patch",
            filetypes=[("Patch files", "*.patch"), ("All files", "*.*")],
            initialfile="changes.patch",
        )
        if path:
            patch = DiffEngine.unified(
                self._original,
                self._modified,
                original_name=self._orig_name,
                modified_name=self._mod_name,
            )
            Path(path).write_text(patch, encoding="utf-8")

    def apply_theme(self, t: Theme) -> None:
        """
        Aplica um novo tema ao painel, reconstruindo a visualização atual
        sem perder o conteúdo exibido.
        """
        self.config(bg=t.panel_bg)
        had_data = bool(self._original or self._modified)
        if self._mode == "inline":
            self._build_inline_view()
        else:
            self._build_side_view()
        if had_data:
            self.show(self._original, self._modified,
                      self._orig_name, self._mod_name)
