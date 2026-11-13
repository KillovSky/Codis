"""Codis - Visualizador de Diff.

Funcionalidades:
  - Diff lado-a-lado (split) e unificado (unified)
  - Destaque de linhas adicionadas / removidas / modificadas
  - Estatísticas: linhas +/- por arquivo
  - Comparação de texto livre ou de dois arquivos
  - Copiar patch / exportar .patch
  - Integração com Git: diff do working tree ou entre commits
"""

from __future__ import annotations

import difflib
import logging
import shutil
import subprocess
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from src.constants import FONT_MONO, FONT_UI
from src.i18n import _t
from src.theme.utils import make_toolbar_button

logger = logging.getLogger("Codis.Diff")

_CHUNK_LIMIT = 3000


class DiffPanel(tk.Frame):
    """Visualizador de diff integrado ao Codis."""

    def __init__(self, parent: tk.Widget, tmgr: Any, cfg: Any) -> None:
        """Inicializa o painel de diff integrado ao editor."""
        t = tmgr.theme
        super().__init__(parent, bg=t.panel_bg)
        self._tmgr = tmgr
        self._cfg = cfg
        self._mode = "split"
        self._build(t)

    def _build(self, t: Any) -> None:
        hdr = tk.Frame(self, bg=t.activity_bg, height=52)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text="⇄", bg=t.activity_bg, fg=t.accent, font=(FONT_UI, 18)).pack(
            side="left", padx=(12, 4), pady=8
        )
        tk.Label(
            hdr,
            text=_t("diff.title", "Diff Viewer"),
            bg=t.activity_bg,
            fg=t.fg,
            font=(FONT_UI, 13, "bold"),
        ).pack(side="left")

        self._stat_lbl = tk.Label(
            hdr, text="", bg=t.activity_bg, fg=t.fg_muted, font=(FONT_UI, 8)
        )
        self._stat_lbl.pack(side="right", padx=12)

        for icon, mode in [("⬛⬛ Split", "split"), ("☰ Unified", "unified")]:
            b = tk.Label(
                hdr,
                text=icon,
                bg=t.activity_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 8),
                padx=6,
                pady=4,
                cursor="hand2",
            )
            b.pack(side="right")
            b.bind("<Button-1>", lambda _, m=mode: self._set_mode(m))

        tk.Frame(self, bg=t.border, height=1).pack(fill="x")

        sep_bar = tk.Frame(self, bg=t.sidebar_bg)
        sep_bar.pack(fill="x")

        def _btn(lbl: str, cmd: Any, accent: bool = False) -> tk.Label:
            return make_toolbar_button(
                sep_bar, t, lbl, cmd, accent=accent, inner_pady=5
            )

        _btn(_t("diff.open_left", "📂 Arquivo A"), self._open_left)
        _btn(_t("diff.open_right", "📂 Arquivo B"), self._open_right)
        _btn(_t("diff.swap", "⇄ Inverter"), self._swap_sides)
        _btn(_t("diff.git_diff", "🌿 Git diff"), self._git_diff)
        _btn(_t("diff.copy_patch", "📋 Copiar patch"), self._copy_patch)
        _btn(_t("diff.export", "💾 Exportar .patch"), self._export_patch)

        tk.Frame(self, bg=t.border, height=1).pack(fill="x")

        self._label_bar = tk.Frame(self, bg=t.panel_bg)
        self._label_bar.pack(fill="x", padx=8, pady=4)
        self._left_lbl = tk.Label(
            self._label_bar, text="A -", bg=t.panel_bg, fg=t.fg_muted, font=(FONT_UI, 8)
        )
        self._left_lbl.pack(side="left")
        self._right_lbl = tk.Label(
            self._label_bar, text="B -", bg=t.panel_bg, fg=t.fg_muted, font=(FONT_UI, 8)
        )
        self._right_lbl.pack(side="right")

        self._diff_area = tk.Frame(self, bg=t.panel_bg)
        self._diff_area.pack(fill="both", expand=True)

        self._left_text: str = ""
        self._right_text: str = ""
        self._left_name: str = "A"
        self._right_name: str = "B"
        self._last_patch: str = ""

        self._show_placeholder(t)

    def _show_placeholder(self, t: Any) -> None:
        for w in self._diff_area.winfo_children():
            w.destroy()
        tk.Label(
            self._diff_area,
            text=_t(
                "diff.placeholder",
                "Abra dois arquivos ou use  🌿 Git diff  para comparar.",
            ),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 10),
        ).pack(pady=60)

    def _open_left(self) -> None:
        path = filedialog.askopenfilename()
        if not path:
            return
        self._left_text = Path(path).read_text(
            encoding="utf-8", errors="replace")
        self._left_name = Path(path).name
        self._left_lbl.config(text=f"A - {self._left_name}")
        self._run_diff()

    def _open_right(self) -> None:
        path = filedialog.askopenfilename()
        if not path:
            return
        self._right_text = Path(path).read_text(
            encoding="utf-8", errors="replace")
        self._right_name = Path(path).name
        self._right_lbl.config(text=f"B - {self._right_name}")
        self._run_diff()

    def _swap_sides(self) -> None:
        self._left_text, self._right_text = self._right_text, self._left_text
        self._left_name, self._right_name = self._right_name, self._left_name
        self._left_lbl.config(text=f"A - {self._left_name}")
        self._right_lbl.config(text=f"B - {self._right_name}")
        self._run_diff()

    def _git_diff(self) -> None:
        if not shutil.which("git"):
            messagebox.showwarning(
                _t("diff.no_git", "Git não encontrado"),
                _t("diff.no_git_msg", "Instale o Git para usar esta função."),
            )
            return
        try:
            result = subprocess.check_output(
                ["git", "diff", "--no-color"],
                text=True,
                timeout=10,
            )
        except subprocess.CalledProcessError as e:
            result = e.output or ""
        except Exception as exc:
            messagebox.showerror("Git", str(exc))
            return
        if not result.strip():
            messagebox.showinfo(
                _t("diff.git_clean", "Sem mudanças"),
                _t("diff.git_clean_msg", "Working tree limpo - nenhuma modificação."),
            )
            return
        self._show_raw_patch(result)

    def _show_raw_patch(self, patch: str) -> None:
        """Exibe um patch unificado bruto (ex: git diff)."""
        self._last_patch = patch
        t = self._tmgr.theme
        for w in self._diff_area.winfo_children():
            w.destroy()

        txt = tk.Text(
            self._diff_area,
            bg=t.editor_bg,
            fg=t.fg,
            font=(FONT_MONO, 9),
            wrap="none",
            relief="flat",
            state="normal",
            selectbackground=t.sel_bg,
        )
        vsb = ttk.Scrollbar(
            self._diff_area, orient="vertical", command=txt.yview)
        hsb = ttk.Scrollbar(
            self._diff_area, orient="horizontal", command=txt.xview)
        txt.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.pack(side="right", fill="y")
        hsb.pack(side="bottom", fill="x")
        txt.pack(fill="both", expand=True)

        txt.tag_configure("add", background="#1a3a1a", foreground="#a6e3a1")
        txt.tag_configure("rem", background="#3a1a1a", foreground="#f38ba8")
        txt.tag_configure("hunk", background="#1e1e3a", foreground="#89b4fa")
        txt.tag_configure(
            "header",
            background="#2a2a3a",
            foreground="#cba6f7",
            font=(FONT_MONO, 9, "bold"),
        )

        adds = rems = 0
        for line in patch.splitlines():
            if line.startswith("+++") or line.startswith("---"):
                tag = "header"
            elif line.startswith("+"):
                tag = "add"
                adds += 1
            elif line.startswith("-"):
                tag = "rem"
                rems += 1
            elif line.startswith("@@"):
                tag = "hunk"
            else:
                tag = ""
            txt.insert("end", line + "\n", tag)

        txt.config(state="disabled")
        self._stat_lbl.config(text=f"+{adds}  −{rems}")

    def _copy_patch(self) -> None:
        patch = self._last_patch
        if not patch:
            return
        self.clipboard_clear()
        self.clipboard_append(patch)

    def _export_patch(self) -> None:
        patch = self._last_patch
        if not patch:
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".patch",
            filetypes=[("Patch", "*.patch"),
                       ("Text", "*.txt"), ("All", "*.*")],
        )
        if path:
            Path(path).write_text(patch, encoding="utf-8")

    def _run_diff(self) -> None:
        if not self._left_text and not self._right_text:
            return
        left_lines = self._left_text.splitlines(keepends=True)
        right_lines = self._right_text.splitlines(keepends=True)

        self._last_patch = "".join(
            difflib.unified_diff(
                left_lines,
                right_lines,
                fromfile=self._left_name,
                tofile=self._right_name,
            )
        )

        if self._mode == "split":
            self._render_split(left_lines, right_lines)
        else:
            self._show_raw_patch(self._last_patch)

    def _set_mode(self, mode: str) -> None:
        self._mode = mode
        if self._left_text or self._right_text:
            self._run_diff()

    def _render_split(self, left_lines: list[str], right_lines: list[str]) -> None:
        t = self._tmgr.theme
        for w in self._diff_area.winfo_children():
            w.destroy()

        matcher = difflib.SequenceMatcher(None, left_lines, right_lines)
        opcodes = matcher.get_opcodes()

        left_out: list[tuple[str, str]] = []
        right_out: list[tuple[str, str]] = []

        for tag, i1, i2, j1, j2 in opcodes:
            la = left_lines[i1:i2]
            ra = right_lines[j1:j2]
            max_len = max(len(la), len(ra))
            la += [""] * (max_len - len(la))
            ra += [""] * (max_len - len(ra))
            for l, r in zip(la, ra, strict=False):
                if tag == "equal":
                    left_out.append(("", l))
                    right_out.append(("", r))
                elif tag == "replace":
                    left_out.append(("rem", l))
                    right_out.append(("add", r))
                elif tag == "delete":
                    left_out.append(("rem", l))
                    right_out.append(("gap", ""))
                elif tag == "insert":
                    left_out.append(("gap", ""))
                    right_out.append(("add", r))

        if len(left_out) > _CHUNK_LIMIT:
            left_out = left_out[:_CHUNK_LIMIT]
            right_out = right_out[:_CHUNK_LIMIT]

        adds = sum(1 for tag, _ in right_out if tag == "add")
        rems = sum(1 for tag, _ in left_out if tag == "rem")
        self._stat_lbl.config(text=f"+{adds}  −{rems}")

        def _make_text(parent: tk.Widget) -> tk.Text:
            txt = tk.Text(
                parent,
                bg=t.editor_bg,
                fg=t.fg,
                font=(FONT_MONO, 9),
                wrap="none",
                relief="flat",
                state="normal",
                selectbackground=t.sel_bg,
            )
            txt.tag_configure("add", background="#1a3a1a",
                              foreground="#a6e3a1")
            txt.tag_configure("rem", background="#3a1a1a",
                              foreground="#f38ba8")
            txt.tag_configure("gap", background="#2a2a2a")
            return txt

        vsb = ttk.Scrollbar(self._diff_area, orient="vertical")
        hsb = ttk.Scrollbar(self._diff_area, orient="horizontal")

        left_txt = _make_text(self._diff_area)
        right_txt = _make_text(self._diff_area)

        def _ysync(*args: Any) -> None:
            left_txt.yview(*args)
            right_txt.yview(*args)

        def _on_yscroll(*args: Any) -> None:
            vsb.set(*args)
            right_txt.yview_moveto(args[0])

        vsb.config(command=_ysync)
        left_txt.config(yscrollcommand=_on_yscroll, xscrollcommand=hsb.set)
        right_txt.config(yscrollcommand=_on_yscroll, xscrollcommand=hsb.set)
        hsb.config(command=left_txt.xview)

        vsb.pack(side="right", fill="y")
        hsb.pack(side="bottom", fill="x")

        div = tk.Frame(self._diff_area, bg=t.border, width=2)
        div.pack(side="left", fill="y")

        left_txt.pack(side="left", fill="both", expand=True)
        right_txt.pack(side="left", fill="both", expand=True)

        def _fill(txt: tk.Text, lines: list[tuple[str, str]]) -> None:
            for lno, (tag, line) in enumerate(lines):
                num = f"{lno + 1:4d}  "
                txt.insert("end", num, "lno")
                txt.insert("end", line.rstrip("\n") + "\n", tag or None)
            txt.config(state="disabled")

        left_txt.tag_configure(
            "lno", foreground="#6c7086", font=(FONT_MONO, 8))
        right_txt.tag_configure(
            "lno", foreground="#6c7086", font=(FONT_MONO, 8))
        _fill(left_txt, left_out)
        _fill(right_txt, right_out)
