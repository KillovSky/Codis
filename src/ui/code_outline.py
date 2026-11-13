"""Painel de outline de código do Codis.

Implementa ``CodeOutline``, widget que exibe a estrutura hierárquica do
arquivo aberto (classes, funções, métodos) com navegação por clique,
atualizado automaticamente a cada edição.
"""

from __future__ import annotations

import logging
import re
import tkinter as tk
from collections.abc import Callable
from tkinter import ttk
from typing import Any

from src.constants import FONT_UI
from src.i18n import _t
from src.theme.base import Theme
from src.theme.manager import ThemeManager

logger = logging.getLogger("Codis")


class CodeOutline(tk.Frame):
    """
    Code Outline (Symbol Navigator):
    - Suporte a Python, JS/TS, Java, Go, Rust, C/C++, Lua, Ruby, PHP
    - Agrupa por kind: class, def/function, const, var, import, type, etc.
    - Filtro em tempo real
    - Clique / Enter navega para a linha
    - Refresh automático ao trocar de aba
    """

    PATTERNS: dict[str, list[tuple]] = {
        "python": [
            ("class", r"^(\s*)class\s+(\w+)"),
            ("def", r"^(\s*)(?:async\s+)?def\s+(\w+)"),
            ("const", r"^([A-Z_][A-Z0-9_]*)\s*="),
            ("import", r"^(?:import|from)\s+(\S+)"),
        ],
        "javascript": [
            ("class", r"^\s*(?:export\s+)?class\s+(\w+)"),
            ("function", r"^\s*(?:export\s+)?(?:async\s+)?function\s*\*?\s*(\w+)"),
            (
                "arrow",
                r"^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\(?[^)]*\)?\s*=>",
            ),
            ("const",
             r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Z_][A-Z0-9_]*)\s*="),
            ("method", r"^\s+(?:async\s+)?(\w+)\s*\("),
        ],
        "typescript": [
            ("interface", r"^\s*(?:export\s+)?interface\s+(\w+)"),
            ("type", r"^\s*(?:export\s+)?type\s+(\w+)\s*="),
            ("class", r"^\s*(?:export\s+)?(?:abstract\s+)?class\s+(\w+)"),
            ("function", r"^\s*(?:export\s+)?(?:async\s+)?function\s*\*?\s*(\w+)"),
            (
                "arrow",
                r"^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\(?[^)]*\)?\s*=>",
            ),
            ("const",
             r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Z_][A-Z0-9_]*)\s*="),
            ("enum", r"^\s*(?:export\s+)?enum\s+(\w+)"),
        ],
        "java": [
            (
                "class",
                r"^\s*(?:public\s+|private\s+|protected\s+|abstract\s+)*class\s+(\w+)",
            ),
            ("interface", r"^\s*(?:public\s+)?interface\s+(\w+)"),
            (
                "method",
                r"^\s+(?:public|private|protected|static|final|abstract)[\w\s<>?\[\]]*\s+(\w+)\s*\(",
            ),
        ],
        "go": [
            ("func", r"^func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)\s*\("),
            ("type", r"^type\s+(\w+)\s+(?:struct|interface)"),
            ("const", r"^\s*(\w+)\s*="),
            ("var", r"^var\s+(\w+)"),
        ],
        "rust": [
            ("fn", r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)"),
            ("struct", r"^\s*(?:pub\s+)?struct\s+(\w+)"),
            ("enum", r"^\s*(?:pub\s+)?enum\s+(\w+)"),
            ("trait", r"^\s*(?:pub\s+)?trait\s+(\w+)"),
            ("impl", r"^\s*impl(?:<[^>]+>)?\s+(\w+)"),
            ("const", r"^\s*(?:pub\s+)?const\s+(\w+)"),
        ],
        "c": [
            ("function", r"^[a-zA-Z_][\w\s\*]*\s+(\w+)\s*\([^;]+$"),
            ("struct", r"^\s*(?:typedef\s+)?struct\s+(\w+)"),
            ("define", r"^#define\s+(\w+)"),
        ],
        "cpp": [
            ("class", r"^\s*(?:template\s*<[^>]*>\s*)?class\s+(\w+)"),
            ("function", r"^[a-zA-Z_:~][\w\s\*&:<>]*\s+(\w+)\s*\([^;]*$"),
            ("struct", r"^\s*struct\s+(\w+)"),
            ("namespace", r"^\s*namespace\s+(\w+)"),
        ],
        "lua": [
            ("function", r"^\s*(?:local\s+)?function\s+(\w[\w.:]*)\s*\("),
            ("local", r"^\s*local\s+([A-Z]\w*)\s*="),
        ],
        "ruby": [
            ("class", r"^\s*class\s+(\w+)"),
            ("module", r"^\s*module\s+(\w+)"),
            ("def", r"^\s+def\s+(\w+[\w?!]*)"),
        ],
        "php": [
            ("class", r"^\s*(?:abstract\s+)?class\s+(\w+)"),
            (
                "function",
                r"^\s*(?:public|private|protected|static)?\s*function\s+(\w+)",
            ),
            ("const", r"^\s*(?:const|define)\s*\(?['\"]?(\w+)"),
        ],
    }

    KIND_ICONS: dict[str, str] = {
        "class": "🔷",
        "interface": "🔶",
        "struct": "🔶",
        "def": "🔹",
        "function": "🔹",
        "fn": "🔹",
        "func": "🔹",
        "method": "🔸",
        "arrow": "🔸",
        "const": "📌",
        "define": "📌",
        "type": "🔠",
        "enum": "🔠",
        "trait": "🟦",
        "impl": "🟩",
        "import": "📦",
        "namespace": "📂",
        "var": "🔡",
        "local": "🔡",
        "module": "🗂",
    }

    def __init__(
        self, parent: tk.Widget, tmgr: ThemeManager, get_editor: Callable | None = None
    ) -> None:
        """Cria o painel de outline de código (estrutura de símbolos).

        Args:
            parent: Widget pai Tkinter.
            config: Instância de ConfigManager.
            on_goto: Callback(line) para navegar até o símbolo.
        """
        t = tmgr.theme
        super().__init__(parent, bg=t.sidebar_bg)
        self._tmgr = tmgr
        self._get_editor = get_editor
        self._all_items: list[tuple] = []
        self._build()

    def _build(self) -> None:
        """Constrói os widgets e layout interno do componente."""
        t = self._tmgr.theme

        hdr = tk.Frame(self, bg=t.panel_bg, height=32)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(
            hdr,
            text=_t("outline.title", "  OUTLINE"),
            bg=t.panel_bg,
            fg=t.sidebar_header_fg,
            font=(FONT_UI, 9, "bold"),
        ).pack(side="left", padx=4)

        refresh_btn = tk.Label(
            hdr,
            text=" ↻ ",
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 10),
            cursor="hand2",
        )
        refresh_btn.pack(side="right", padx=4)
        refresh_btn.bind("<Button-1>", lambda _: self.refresh())
        refresh_btn.bind("<Enter>", lambda _: refresh_btn.config(fg=t.accent))
        refresh_btn.bind(
            "<Leave>", lambda _: refresh_btn.config(fg=t.fg_muted))

        filter_frame = tk.Frame(self, bg=t.sidebar_bg)
        filter_frame.pack(fill="x", padx=4, pady=2)
        tk.Label(
            filter_frame, text="🔍", bg=t.sidebar_bg, fg=t.fg_muted, font=(FONT_UI, 9)
        ).pack(side="left")
        self._filter_var = tk.StringVar()
        self._filter_var.trace_add("write", lambda *_: self._apply_filter())
        filter_ent = tk.Entry(
            filter_frame,
            textvariable=self._filter_var,
            bg=t.input_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            bd=0,
            font=(FONT_UI, 9),
            highlightthickness=1,
            highlightbackground=t.border,
        )
        filter_ent.pack(side="left", fill="x", expand=True, padx=4)

        tree_frame = tk.Frame(self, bg=t.sidebar_bg)
        tree_frame.pack(fill="both", expand=True)

        style = ttk.Style()
        try:
            style.configure(
                "Outline.Treeview",
                background=t.sidebar_bg,
                foreground=t.fg,
                fieldbackground=t.sidebar_bg,
                borderwidth=0,
                rowheight=22,
            )
            style.configure(
                "Outline.Treeview.Heading",
                background=t.tabbar_bg,
                foreground=t.fg_muted,
            )
            style.map(
                "Outline.Treeview",
                background=[("selected", t.sel_bg)],
                foreground=[("selected", t.sel_fg)],
            )
        except tk.TclError:
            pass

        self._tree = ttk.Treeview(
            tree_frame,
            style="Outline.Treeview",
            show="tree",
            selectmode="browse",
        )
        vsb = ttk.Scrollbar(
            tree_frame,
            orient="vertical",
            command=self._tree.yview,
            style="Thin.Vertical.TScrollbar",
        )
        vsb.pack(side="right", fill="y")
        self._tree.config(yscrollcommand=vsb.set)
        self._tree.pack(fill="both", expand=True)
        self._tree.bind("<Double-Button-1>", self._on_activate)
        self._tree.bind("<Return>", self._on_activate)

        self._count_lbl = tk.Label(
            self, text="", bg=t.sidebar_bg, fg=t.fg_muted, font=(FONT_UI, 8)
        )
        self._count_lbl.pack(anchor="e", padx=8, pady=2)

    def refresh(self) -> None:
        """Reanálisa o conteúdo do editor ativo e atualiza a árvore de símbolos."""
        editor = self._get_editor() if self._get_editor else None
        if editor is None:
            self._tree.delete(*self._tree.get_children())
            self._count_lbl.config(text=_t("ui.no_file"))
            return
        content = editor.get_content() if hasattr(editor, "get_content") else ""
        lang = getattr(editor, "lang", "plain")
        self._parse(content, lang)
        self._apply_filter()

    def _parse(self, content: str, lang: str) -> None:
        """Parseia a entrada e retorna a estrutura de dados resultante.

        Args:
            content: Dado textual a processar.
            lang: Parâmetro da operação.
        """
        patterns = self.PATTERNS.get(lang, [])
        self._all_items = []
        lines = content.splitlines()
        compiled = [(kind, re.compile(pat)) for kind, pat in patterns]
        for lineno, line in enumerate(lines, 1):
            for kind, rx in compiled:
                m = rx.search(line)
                if m:
                    groups = m.groups()
                    if len(groups) >= 2:
                        indent = len(groups[0]) if groups[0] else 0
                        name = groups[1]
                    else:
                        indent = 0
                        name = groups[0] if groups else "?"
                    self._all_items.append(
                        (kind, name.strip(), lineno, indent))
                    break

    def _apply_filter(self) -> None:
        """Filtra os itens do outline conforme o texto digitado na busca."""
        q = self._filter_var.get().lower()
        self._tree.delete(*self._tree.get_children())

        groups: dict[str, list[tuple]] = {}
        for item in self._all_items:
            kind, name, lineno, _ = item
            if q and q not in name.lower() and q not in kind:
                continue
            groups.setdefault(kind, []).append(item)

        total = 0
        for kind in sorted(groups.keys()):
            items = groups[kind]
            icon = self.KIND_ICONS.get(kind, "▸")
            parent_id = self._tree.insert(
                "",
                "end",
                text=f"  {icon} {kind.upper()}  ({len(items)})",
                open=True,
                tags=("header",),
            )
            for _, name, lineno, _ in items:
                self._tree.insert(
                    parent_id,
                    "end",
                    text=f"    {icon} {name}",
                    values=(lineno,),
                )
                total += 1

        self._count_lbl.config(
            text=f"{total} symbol{'s' if total != 1 else ''}")

        try:
            t = self._tmgr.theme
            self._tree.tag_configure(
                "header", foreground=t.accent, font=(FONT_UI, 8, "bold")
            )
        except tk.TclError:
            pass

    def _on_activate(self, _e: Any = None) -> None:
        """Trata o evento 'activate'.

        Args:
            _e: Parâmetro da operação.
        """
        sel = self._tree.selection()
        if not sel:
            return
        item = self._tree.item(sel[0])
        vals = item.get("values", [])
        if not vals:
            return
        lineno = int(vals[0])
        editor = self._get_editor() if self._get_editor else None
        if editor and hasattr(editor, "goto_line"):
            editor.goto_line(lineno)

    def apply_theme(self, t: Theme) -> None:
        """Reaplica o tema ao painel de outline e ao Treeview TTK."""
        self.config(bg=t.sidebar_bg)
        try:
            style = ttk.Style()
            style.configure(
                "Outline.Treeview",
                background=t.sidebar_bg,
                foreground=t.fg,
                fieldbackground=t.sidebar_bg,
            )
            style.map(
                "Outline.Treeview",
                background=[("selected", t.sel_bg)],
                foreground=[("selected", t.sel_fg)],
            )
        except tk.TclError:
            pass
