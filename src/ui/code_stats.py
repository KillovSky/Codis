"""Codis - Native CodeStats panel.

Replaces the CodeStats plugin with a richer native implementation:
  - Tracks all languages by file extension
  - Counts total/code/blank/comment lines per language
  - Shows distribution bar chart in themed monospace widget
  - Tracks project complexity heuristic (functions, classes, nesting)
  - Shows top contributors by code volume
  - Full async scanning so UI never blocks
"""

from __future__ import annotations

import contextlib
import re
import threading
import time
import tkinter as tk
from collections import defaultdict
from pathlib import Path
from tkinter import filedialog, ttk
from tkinter import messagebox as mb

from src.constants import FONT_MONO, FONT_UI
from src.i18n import _t

EXT_LANG: dict[str, str] = {
    ".py": "Python",
    ".pyw": "Python",
    ".pyi": "Python",
    ".js": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".ts": "TypeScript",
    ".mts": "TypeScript",
    ".jsx": "JSX",
    ".tsx": "TSX",
    ".html": "HTML",
    ".htm": "HTML",
    ".xhtml": "HTML",
    ".css": "CSS",
    ".scss": "SCSS",
    ".sass": "Sass",
    ".less": "Less",
    ".json": "JSON",
    ".jsonc": "JSON",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".toml": "TOML",
    ".ini": "INI",
    ".cfg": "Config",
    ".env": "Env",
    ".md": "Markdown",
    ".mdx": "Markdown",
    ".rst": "reStructuredText",
    ".txt": "Text",
    ".log": "Text",
    ".sh": "Shell",
    ".bash": "Shell",
    ".zsh": "Shell",
    ".fish": "Shell",
    ".bat": "Batch",
    ".cmd": "Batch",
    ".ps1": "PowerShell",
    ".c": "C",
    ".h": "C",
    ".cpp": "C++",
    ".cxx": "C++",
    ".cc": "C++",
    ".hpp": "C++",
    ".cs": "C#",
    ".java": "Java",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".go": "Go",
    ".rs": "Rust",
    ".rb": "Ruby",
    ".php": "PHP",
    ".swift": "Swift",
    ".m": "Objective-C",
    ".mm": "Objective-C",
    ".lua": "Lua",
    ".r": "R",
    ".sql": "SQL",
    ".xml": "XML",
    ".vue": "Vue",
    ".svelte": "Svelte",
    ".graphql": "GraphQL",
    ".ex": "Elixir",
    ".exs": "Elixir",
    ".erl": "Erlang",
    ".hs": "Haskell",
    ".elm": "Elm",
    ".dart": "Dart",
    ".jl": "Julia",
    ".nim": "Nim",
    ".zig": "Zig",
    ".dockerfile": "Docker",
    ".tf": "Terraform",
    ".hcl": "HCL",
    ".proto": "Protobuf",
}

COMMENT_CHARS: dict[str, tuple[str, ...]] = {
    "Python": ("#",),
    "Ruby": ("#",),
    "Shell": ("#",),
    "YAML": ("#",),
    "TOML": ("#",),
    "R": ("#",),
    "Elixir": ("#",),
    "Nim": ("#",),
    "Julia": ("#",),
    "Terraform": ("#",),
    "HCL": ("#",),
    "JavaScript": ("//",),
    "TypeScript": ("//",),
    "JSX": ("//",),
    "TSX": ("//",),
    "C": ("//",),
    "C++": ("//",),
    "C#": ("//",),
    "Java": ("//",),
    "Kotlin": ("//",),
    "Go": ("//",),
    "Rust": ("//",),
    "Swift": ("//",),
    "Dart": ("//",),
    "Zig": ("//",),
    "PHP": ("//",),
    "Scala": ("//",),
    "Groovy": ("//",),
    "CSS": ("/*",),
    "SCSS": ("//",),
    "Sass": ("//",),
    "Less": ("//",),
    "HTML": ("<!--",),
    "XML": ("<!--",),
    "Lua": ("--",),
    "Haskell": ("--",),
    "Elm": ("--",),
    "SQL": ("--",),
    "Ada": ("--",),
    "Erlang": ("%",),
    "Batch": ("REM ", "::"),
    "PowerShell": ("#",),
}

COMPLEXITY_PATTERNS = {
    "Python": (r"\bdef \b", r"\bclass \b", r"\bif \b", r"\bfor \b", r"\bwhile \b"),
    "JavaScript": (r"\bfunction\b", r"\bclass\b", r"=>", r"\bif\b", r"\bfor\b"),
    "TypeScript": (r"\bfunction\b", r"\bclass\b", r"=>", r"\bif\b", r"\bfor\b"),
    "Go": (r"\bfunc\b", r"\bstruct\b", r"\bif\b", r"\bfor\b"),
    "Rust": (r"\bfn\b", r"\bstruct\b", r"\btrait\b", r"\bif\b", r"\bfor\b"),
    "Java": (r"\bclass\b", r"\bvoid\b", r"\bpublic\b", r"\bprivate\b"),
    "C": (r"\bvoid\b", r"^\w+\s+\w+\s*\(", r"\bif\b", r"\bfor\b"),
    "C++": (r"\bvoid\b", r"\bclass\b", r"\btemplate\b", r"\bif\b"),
}

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
    ".ruff_cache",
    "target",
    ".cargo",
    ".gradle",
}
BINARY_EXTS = {
    ".pyc",
    ".pyo",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".otf",
    ".pdf",
    ".zip",
    ".tar",
    ".gz",
    ".bz2",
    ".xz",
    ".7z",
    ".rar",
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".a",
    ".o",
    ".obj",
    ".pdb",
    ".map",
    ".min.js",
    ".min.css",
    ".lock",
}
MAX_FILE_SIZE = 2 * 1024 * 1024


def _count_file(path: Path, lang: str) -> tuple[int, int, int, int]:
    """Conta e retorna métricas de linhas (total, código, branco, comentário) do arquivo."""
    try:
        if path.stat().st_size > MAX_FILE_SIZE:
            return 0, 0, 0, 0
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        prefixes = COMMENT_CHARS.get(lang, ("#",))
        total = len(lines)
        blank = sum(1 for l_ in lines if not l_.strip())
        comment = sum(
            1 for l_ in lines if any(l_.strip().startswith(p) for p in prefixes)
        )
        code = max(total - blank - comment, 0)
        return total, code, blank, comment
    except (OSError, UnicodeDecodeError):
        return 0, 0, 0, 0


def _scan(root: Path) -> dict:
    by_lang: dict[str, dict] = defaultdict(
        lambda: {
            "files": 0,
            "total": 0,
            "code": 0,
            "blank": 0,
            "comment": 0,
            "size": 0,
            "complexity": 0,
        }
    )
    file_count = 0
    total_size = 0

    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        suffix = path.suffix.lower()
        if suffix in BINARY_EXTS:
            continue
        lang = EXT_LANG.get(suffix, "Other")
        try:
            size = path.stat().st_size
        except OSError:
            continue

        total, code, blank, comment = _count_file(path, lang)
        if total == 0 and size > 0:
            continue

        rec = by_lang[lang]
        rec["files"] += 1
        rec["total"] += total
        rec["code"] += code
        rec["blank"] += blank
        rec["comment"] += comment
        rec["size"] += size

        if lang in COMPLEXITY_PATTERNS and size < 512 * 1024:
            try:
                src = path.read_text(encoding="utf-8", errors="replace")
                cx = sum(len(re.findall(p, src))
                         for p in COMPLEXITY_PATTERNS[lang])
                rec["complexity"] += cx
            except (OSError, UnicodeDecodeError):
                pass

        file_count += 1
        total_size += size

    return {
        "by_lang": dict(by_lang),
        "file_count": file_count,
        "total_size": total_size,
        "total_lines": sum(v["total"] for v in by_lang.values()),
        "code_lines": sum(v["code"] for v in by_lang.values()),
        "blank_lines": sum(v["blank"] for v in by_lang.values()),
        "comment_lines": sum(v["comment"] for v in by_lang.values()),
        "total_complexity": sum(v["complexity"] for v in by_lang.values()),
    }


def _fmt_size(n: int) -> str:
    """Executa 'fmt size' e retorna o resultado.

    Args:
        n: Parâmetro da operação.
    """
    if n < 1024:
        return f"{n} B"
    if n < 1024**2:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024**2:.1f} MB"


def _bar(pct: float, width: int = 22) -> str:
    filled = max(0, min(width, round(pct / 100 * width)))
    return "█" * filled + "░" * (width - filled)


class CodeStatsPanel(tk.Frame):
    """Native CodeStats panel - full project analysis dashboard."""

    def __init__(self, parent: tk.Widget, tmgr, cfg) -> None:
        """Inicializa o painel de estatísticas de código."""
        t = tmgr.theme
        super().__init__(parent, bg=t.panel_bg)
        self._tmgr = tmgr
        self._cfg = cfg
        self._scanning = False
        self._ws: Path | None = None
        self._last_data: dict = {}
        self._last_root: Path | None = None
        self._last_elapsed: float = 0.0
        self._build()

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
            text=f"  📊 {_t('codestats.title')}",
            bg=t.panel_bg,
            fg=t.fg,
            font=(FONT_UI, 9, "bold"),
        ).pack(side="left", padx=4)
        self._btn = tk.Label(
            hdr,
            text=_t("codestats.refresh"),
            bg=t.panel_bg,
            fg=t.t_func,
            font=(FONT_UI, 8),
            cursor="hand2",
            padx=6,
        )
        self._btn.pack(side="right", padx=4)
        self._btn.bind("<Button-1>", lambda _: self._start())
        self._export_btn = tk.Label(
            hdr,
            text=_t("codestats.export"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            cursor="hand2",
            padx=4,
        )
        self._export_btn.pack(side="right", padx=2)
        self._export_btn.bind("<Button-1>", lambda _: self._export())
        tk.Frame(self, bg=t.floating_border, height=1).pack(fill="x")

        out_frame = tk.Frame(self, bg=t.panel_bg)
        out_frame.pack(fill="both", expand=True)

        self._txt = tk.Text(
            out_frame,
            bg=t.editor_bg,
            fg=t.fg,
            relief="flat",
            bd=0,
            font=(FONT_MONO, 8),
            state="disabled",
            wrap="none",
            padx=8,
            pady=6,
            highlightthickness=0,
        )
        vsb = ttk.Scrollbar(out_frame, orient="vertical",
                            command=self._txt.yview)
        vsb.pack(side="right", fill="y")
        hsb = ttk.Scrollbar(out_frame, orient="horizontal",
                            command=self._txt.xview)
        hsb.pack(side="bottom", fill="x")
        self._txt.config(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self._txt.pack(fill="both", expand=True)

        self._txt.tag_configure(
            "header", foreground=t.t_func, font=(FONT_UI, 9, "bold")
        )
        self._txt.tag_configure("accent", foreground=t.t_string)
        self._txt.tag_configure("dim", foreground=t.fg_muted)
        self._txt.tag_configure("num", foreground=t.t_number)
        self._txt.tag_configure("bar_py", foreground=t.t_func)
        self._txt.tag_configure("bar_js", foreground=t.t_keyword)
        self._txt.tag_configure("bar_rs", foreground=t.t_type)
        self._txt.tag_configure("bar_gen", foreground=t.t_string)
        self._txt.tag_configure("warn", foreground=t.warn_fg)
        self._txt.tag_configure("good", foreground=t.t_string)
        self._txt.tag_configure("bad", foreground=t.err_fg)

        self._write("\n  " + _t("ui.no_workspace_stats") + "\n", "dim")

    def set_workspace(self, path: Path | None) -> None:
        """Define o diretório raiz do workspace para análise."""
        self._ws = path

    def _start(self) -> None:
        """Inicia o processo ou animação."""
        if self._scanning:
            return
        ws = self._ws
        if ws is None:
            self._write_replace(
                "\n  " + _t("ui.no_workspace_warn") + "\n", "warn")
            return
        self._scanning = True
        self._btn.config(text=_t("codestats.scanning"))
        self._write_replace(f"\n  Scanning {ws.name}...\n", "dim")
        threading.Thread(target=self._worker, args=(ws,), daemon=True).start()

    def _worker(self, root: Path) -> None:
        """Worker thread que executa operação assíncrona.

        Args:
            root: Widget pai Tkinter.
        """
        t0 = time.time()
        data = _scan(root)
        elapsed = time.time() - t0
        self.after(0, lambda: self._render(data, root, elapsed))

    def _write(self, text: str, tag: str = "") -> None:
        """Escreve dados no destino configurado.

        Args:
            text: Dado textual a processar.
            tag: Parâmetro da operação.
        """
        self._txt.config(state="normal")
        if tag:
            self._txt.insert("end", text, (tag,))
        else:
            self._txt.insert("end", text)
        self._txt.config(state="disabled")

    def _write_replace(self, text: str, tag: str = "") -> None:
        """Executa a operação 'write replace'.

        Args:
            text: Dado textual a processar.
            tag: Parâmetro da operação.
        """
        self._txt.config(state="normal")
        self._txt.delete("1.0", "end")
        if tag:
            self._txt.insert("end", text, (tag,))
        else:
            self._txt.insert("end", text)
        self._txt.config(state="disabled")

    def _render(self, data: dict, root: Path, elapsed: float) -> None:
        """Renderiza o conteúdo no canvas ou widget.

        Args:
            data: Dado textual a processar.
            root: Widget pai Tkinter.
            elapsed: Parâmetro da operação.
        """
        self._scanning = False
        self._btn.config(text=_t("codestats.refresh"))
        self._txt.config(state="normal")
        self._txt.delete("1.0", "end")
        self._txt.config(state="disabled")

        total_code = max(data["code_lines"], 1)
        by_lang = sorted(data["by_lang"].items(), key=lambda x: -x[1]["code"])

        self._write(f"\n  📊 Code Stats - {root.name}\n", "header")
        self._write(f"  Scanned in {elapsed:.2f}s\n\n", "dim")

        self._write("  -- Summary --\n", "dim")
        items = [
            ("Files", f"{data['file_count']:,}", "num"),
            ("Total lines", f"{data['total_lines']:,}", "num"),
            ("Code lines", f"{data['code_lines']:,}", "accent"),
            ("Comment lines", f"{data['comment_lines']:,}", "dim"),
            ("Blank lines", f"{data['blank_lines']:,}", "dim"),
            ("Total size", f"{_fmt_size(data['total_size'])}", "num"),
            ("Languages", f"{len(data['by_lang'])}", "num"),
            ("Complexity", f"{data['total_complexity']:,}", "warn"),
        ]
        for name, val, tag in items:
            self._write(f"  {name:<16}: ", "dim")
            self._write(f"{val}\n", tag)
        self._write("\n")

        self._write(
            f"  {'Language':<14} {'Files':>5} {'Code':>8} {'Total':>8}  Distribution\n",
            "dim",
        )
        self._write("  " + "-" * 68 + "\n", "dim")

        for lang, info in by_lang[:25]:
            pct = info["code"] / total_code * 100
            dist_bar = _bar(pct, 20)
            bar_tag = (
                "bar_py"
                if lang in ("Python",)
                else (
                    "bar_js"
                    if lang in ("JavaScript", "TypeScript", "JSX", "TSX")
                    else "bar_rs" if lang in ("Rust", "Go", "C", "C++") else "bar_gen"
                )
            )
            self._write(f"  {lang:<14} ", "dim")
            self._write(f"{info['files']:>5} ", "num")
            self._write(f"{info['code']:>8,} ", "accent")
            self._write(f"{info['total']:>8,}  ", "dim")
            self._write(dist_bar, bar_tag)
            self._write(f" {pct:4.1f}%\n", "dim")

        if len(by_lang) > 25:
            self._write(
                f"  ... and {len(by_lang) - 25} more language(s)\n", "dim")
        self._write("\n")

        self._write("  -- Top 5 by code lines --\n", "dim")
        for lang, info in by_lang[:5]:
            pct = info["code"] / total_code * 100
            self._write(f"  {lang}: ", "accent")
            self._write(f"{info['code']:,} lines ", "num")
            self._write(f"({pct:.0f}%)\n", "dim")

        self._write("\n")
        self._write("  -- Top 5 by complexity --\n", "dim")
        cx_sorted = sorted(
            [(l_, v) for l_, v in by_lang if v["complexity"] > 0],
            key=lambda x: -x[1]["complexity"],
        )
        for lang, info in cx_sorted[:5]:
            self._write(f"  {lang}: ", "accent")
            self._write(f"{info['complexity']:,} constructs\n", "warn")

        self._txt.see("1.0")

        self._last_data = data
        self._last_root = root
        self._last_elapsed = elapsed

    def _export(self) -> None:
        """Exporta o conteúdo do painel para arquivo de texto."""
        if not self._last_data:
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text", "*.txt"), ("Markdown", "*.md"), ("All", "*")],
            title=_t("codestats.export_title", "Exportar Estatísticas"),
        )
        if not path:
            return
        try:
            content = self._txt.get("1.0", "end")
            Path(path).write_text(content, encoding="utf-8")
        except OSError as exc:
            mb.showerror("Export failed", str(exc))

    def refresh(self) -> None:
        """Reinicia a análise do workspace."""
        self._start()

    def apply_theme(self, tmgr) -> None:
        """Aplica o tema ao painel reconstruindo os widgets."""
        self._tmgr = tmgr
        for w in self.winfo_children():
            w.destroy()
        self._build()
