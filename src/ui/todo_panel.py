"""
and presents them in a filterable, navigable tree view with live-update
support when files are saved.
"""

from __future__ import annotations

import contextlib
import re
import threading
import tkinter as tk
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from tkinter import ttk

from src.constants import FONT_UI
from src.i18n import _t

TAGS: dict[str, dict] = {
    "TODO": {"icon": "☐", "color": "#89b4fa"},
    "FIXME": {"icon": "✖", "color": "#f38ba8"},
    "BUG": {"icon": "🐛", "color": "#f38ba8"},
    "HACK": {"icon": "!", "color": "#fab387"},
    "WARN": {"icon": "⚠", "color": "#f9e2af"},
    "NOTE": {"icon": "📌", "color": "#a6e3a1"},
    "XXX": {"icon": "❓", "color": "#cba6f7"},
    "OPTIMIZE": {"icon": "~", "color": "#89dceb"},
    "REVIEW": {"icon": "👁", "color": "#cba6f7"},
    "DEPRECATED": {"icon": "🗑", "color": "#6c7086"},
}

_TAG_PAT = re.compile(
    r"(?://|#|<!--|;|--|\'\'\'|\"\"\"|\*)\s*("
    + "|".join(TAGS.keys())
    + r")[:\s]\s*(.+?)(?:\s*-->|\s*$)",
    re.IGNORECASE,
)

SKIP_DIRS = {
    ".codis_trash",
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    ".next",
    "coverage",
    ".pytest_cache",
    ".mypy_cache",
    "target",
    ".cargo",
}
SKIP_EXTS = {
    ".pyc",
    ".pyo",
    ".min.js",
    ".min.css",
    ".map",
    ".lock",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".svg",
    ".woff",
    ".ttf",
    ".eot",
    ".pdf",
    ".zip",
    ".so",
    ".dll",
    ".exe",
}
MAX_SIZE = 2 * 1024 * 1024


def _scan_file(path: Path) -> list[tuple[str, int, str]]:
    """Executa 'scan file' e retorna o resultado.

    Args:
        path: Caminho do arquivo ou diretório.
    """
    results: list[tuple[str, int, str]] = []
    try:
        if path.stat().st_size > MAX_SIZE:
            return results
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            m = _TAG_PAT.search(line)
            if m:
                tag = m.group(1).upper()
                msg = m.group(2).strip()[:140]
                results.append((tag, lineno, msg))
    except (OSError, UnicodeDecodeError):
        pass
    return results


def _scan_workspace(root: Path) -> dict[str, list[tuple[Path, int, str]]]:
    """Executa 'scan workspace' e retorna o resultado.

    Args:
        root: Widget pai Tkinter.
    """
    results: dict[str, list] = defaultdict(list)
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in SKIP_EXTS:
            continue
        for tag, lineno, text in _scan_file(path):
            results[tag].append((path, lineno, text))
    return results


class TodoPanel(tk.Frame):
    """Painel de TODOs que varre o workspace por anotações de código pendentes."""

    def __init__(
        self, parent: tk.Widget, tmgr, cfg, open_file_at_line: Callable | None = None
    ) -> None:
        """
        Args:
            parent: Widget pai Tkinter.
            config: Instância de ConfigManager.
        """
        t = tmgr.theme
        super().__init__(parent, bg=t.panel_bg)
        self._tmgr = tmgr
        self._cfg = cfg
        self._open_file_at_line = open_file_at_line
        self._results: dict[str, list] = {}
        self._scanning = False
        self._ws: Path | None = None
        self._build()

    def set_workspace(self, path: Path | None) -> None:
        """Define o diretório raiz do workspace para scan."""
        self._ws = path

    def _build(self) -> None:
        """Constrói os widgets e layout interno do componente."""
        t = self._tmgr.theme

        for child in self.winfo_children():
            with contextlib.suppress(tk.TclError):
                child.destroy()

        hdr = tk.Frame(self, bg=t.panel_bg, height=32)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(
            hdr,
            text=f"  ✅ {_t('todo.title')}",
            bg=t.panel_bg,
            fg=t.fg,
            font=(FONT_UI, 9, "bold"),
        ).pack(side="left", padx=4)
        self._scan_btn = tk.Label(
            hdr,
            text=_t("todo.refresh"),
            bg=t.panel_bg,
            fg=t.t_func,
            font=(FONT_UI, 8),
            cursor="hand2",
            padx=6,
        )
        self._scan_btn.pack(side="right", padx=4)
        self._scan_btn.bind("<Button-1>", lambda _: self._start_scan())
        self._status_lbl = tk.Label(
            hdr, text="", bg=t.panel_bg, fg=t.fg_muted, font=(FONT_UI, 8)
        )
        self._status_lbl.pack(side="right")
        tk.Frame(self, bg=t.floating_border, height=1).pack(fill="x")

        flt = tk.Frame(self, bg=t.panel_bg, pady=2)
        flt.pack(fill="x")
        tk.Label(
            flt,
            text=_t("hc.todo_panel.search_icon"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
        ).pack(side="left")
        self._filter_var = tk.StringVar()
        self._filter_var.trace_add("write", lambda *_: self._apply_filter())
        tk.Entry(
            flt,
            textvariable=self._filter_var,
            bg=t.input_bg,
            fg=t.fg,
            insertbackground=t.t_func,
            relief="flat",
            bd=2,
            font=(FONT_UI, 8),
            width=24,
        ).pack(side="left", fill="x", expand=True, padx=4, pady=2)

        self._tag_vars: dict[str, tk.BooleanVar] = {}
        tag_frame = tk.Frame(self, bg=t.panel_bg)
        tag_frame.pack(fill="x", padx=4, pady=2)
        for tag, info in TAGS.items():
            var = tk.BooleanVar(value=True)
            self._tag_vars[tag] = var
            cb = tk.Checkbutton(
                tag_frame,
                text=f"{info['icon']}{tag[:3]}",
                variable=var,
                bg=t.panel_bg,
                fg=info["color"],
                activebackground=t.panel_bg,
                selectcolor=t.sel_bg,
                font=(FONT_UI, 7),
                command=self._apply_filter,
            )
            cb.pack(side="left")

        tree_frame = tk.Frame(self, bg=t.panel_bg)
        tree_frame.pack(fill="both", expand=True)

        vsb = ttk.Scrollbar(tree_frame, orient="vertical")
        vsb.pack(side="right", fill="y")
        self._tree = ttk.Treeview(
            tree_frame,
            columns=("loc", "text"),
            show="tree headings",
            selectmode="browse",
        )
        self._tree.heading("#0", text=_t("hc.todo_panel.tag_file"))
        self._tree.heading("loc", text=_t("todo.col_line", "Linha"))
        self._tree.heading("text", text=_t("todo.col_message", "Mensagem"))
        self._tree.column("#0", width=140, stretch=False, minwidth=80)
        self._tree.column("loc", width=55, stretch=False, anchor="center")
        self._tree.column("text", width=280, stretch=True)
        self._tree.config(yscrollcommand=vsb.set)
        vsb.config(command=self._tree.yview)
        self._tree.pack(fill="both", expand=True)
        self._tree.bind("<Double-1>", self._on_double_click)
        self._tree.bind("<Return>", self._on_double_click)

        for tag, info in TAGS.items():
            self._tree.tag_configure(f"tag_{tag}", foreground=info["color"])

        self._count_lbl = tk.Label(
            self,
            text="",
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            anchor="w",
            padx=8,
        )
        self._count_lbl.pack(fill="x")

    def _start_scan(self) -> None:
        """Inicia a varredura assíncrona por TODOs no workspace atual."""
        if self._scanning:
            return
        ws = self._ws
        if ws is None:
            self._status_lbl.config(text=_t("todo.no_workspace"))
            return
        self._scanning = True
        self._scan_btn.config(text=_t("todo.scanning"))
        self._status_lbl.config(text="")
        self._count_lbl.config(text="")
        threading.Thread(target=self._scan_worker,
                         args=(ws,), daemon=True).start()

    def _scan_worker(self, root: Path) -> None:
        """
        Args:
            folder: Pasta a escanear.
            gen: Geração do scan (para cancelamento).
        """
        results = _scan_workspace(root)
        self.after(0, lambda: self._populate(results))

    def _populate(self, results: dict) -> None:
        """Popula a lista ou árvore com dados.

        Args:
            results: Parâmetro da operação.
        """
        self._results = results
        self._scanning = False
        total = sum(len(v) for v in results.values())
        self._scan_btn.config(text=_t("todo.refresh"))
        self._status_lbl.config(
            text=f"{total} item{'s' if total != 1 else ''}")
        self._apply_filter()

    def _apply_filter(self) -> None:
        """Filtra os TODOs exibidos conforme tipo (TODO/FIXME/HACK) e busca textual."""
        q = self._filter_var.get().lower()
        self._tree.delete(*self._tree.get_children())
        total = 0

        for tag, tag_info in TAGS.items():
            if not self._tag_vars.get(tag, tk.BooleanVar(value=True)).get():
                continue
            items = self._results.get(tag, [])
            if not items:
                continue
            info = tag_info
            by_file: dict[Path, list] = defaultdict(list)
            for path, lineno, text in items:
                if q and q not in text.lower() and q not in path.name.lower():
                    continue
                by_file[path].append((lineno, text))
            if not by_file:
                continue

            count = sum(len(v) for v in by_file.values())
            tag_id = self._tree.insert(
                "",
                "end",
                text=f"{info['icon']} {tag} ({count})",
                tags=(f"tag_{tag}",),
                open=True,
            )
            for path, entries in sorted(by_file.items(), key=lambda x: x[0].name):
                file_id = self._tree.insert(
                    tag_id,
                    "end",
                    text=f"  📄 {path.name}",
                    values=("", ""),
                    tags=(f"tag_{tag}",),
                )
                self._tree.item(file_id, open=True)
                for lineno, text in sorted(entries, key=lambda x: x[0]):
                    self._tree.insert(
                        file_id,
                        "end",
                        text="",
                        values=(f"L{lineno}", text),
                        tags=(str(path), str(lineno), f"tag_{tag}"),
                    )
                    total += 1

        self._count_lbl.config(
            text=f"  {total} item{'s' if total != 1 else ''} found"
            + (" (filtered)" if q else "")
        )

    def _on_double_click(self, _event: tk.Event) -> None:
        """Trata o evento 'double click'.

        Args:
            _event: Parâmetro da operação.
        """
        sel = self._tree.selection()
        if not sel:
            return
        item = sel[0]
        tags = self._tree.item(item, "tags")

        if len(tags) >= 2 and self._open_file_at_line:
            try:
                path = Path(tags[0])
                line = int(tags[1])
                if path.exists():
                    self._open_file_at_line(path, line)
            except (ValueError, IndexError):
                pass

    def notify_file_saved(self, path: Path) -> None:
        """Call after a file is saved to rescan only that file."""
        if not self._ws:
            return
        try:
            rel_items = _scan_file(path)

            for tag in list(self._results.keys()):
                self._results[tag] = [
                    (p, l_, t) for p, l_, t in self._results[tag] if p != path
                ]

            for tag, lineno, text in rel_items:
                self._results.setdefault(tag, []).append((path, lineno, text))
            self.after(0, self._apply_filter)
        except (OSError, ValueError):
            pass

    def refresh(self) -> None:
        """Reinicia o scan do workspace."""
        self._start_scan()

    def apply_theme(self, tmgr) -> None:
        """Aplica o tema ao painel reconstruindo os widgets."""
        self._tmgr = tmgr
        for w in self.winfo_children():
            w.destroy()
        self._build()
