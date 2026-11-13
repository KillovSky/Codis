"""Pesquisa global em arquivos do Codis.

Implementa ``FindInFilesPanel``, painel com busca por texto ou regex em
todos os arquivos do projeto, com preview de resultados, substituição
em lote e filtros por extensão e diretório.
"""

from __future__ import annotations

import contextlib
import fnmatch
import logging
import os
import re
import threading
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any

from src.config import ConfigManager
from src.constants import (
    FONT_MONO,
    FONT_UI,
)
from src.editor.autocomplete import IgnoreRules
from src.i18n import _t
from src.theme.base import Theme
from src.theme.manager import ThemeManager
from src.ui.grab_safe import safe_grab_set
from src.ui.search_bar import _SearchEngine

logger = logging.getLogger("Codis")


class FindInFilesPanel(tk.Frame):
    """
    Busca recursiva em arquivos - high-performance rewrite:

    Backend:
      - os.scandir recursivo manual (mais rápido que os.walk)
      - Leitura mmap-like: lê arquivo uma vez, busca com regex em texto
      - Worker thread com cancelamento imediato via threading.Event
      - Batch de resultados a cada 50 hits (UI nunca trava)
      - Pré-compilação de pattern antes de iniciar thread
      - Suporte a múltiplos globs separados por vírgula (*.py,*.js)
      - Exclusão de pastas via glob negativo (ex: !node_modules)
      - IgnoreRules (.gitignore) respeitado opcionalmente
      - Replace All multi-arquivo com preview e rollback (undo por arquivo)
      - Contador de arquivos e matches em tempo real

    UX:
      - Resultado em árvore expansível: arquivo > linha > preview
      - Preview colorido com match em destaque (inline highlight)
      - Clique simples seleciona; Enter/duplo clique abre na linha
      - Atalhos: Ctrl+Enter = buscar, Escape = cancelar, F5 = rerun
      - Botão "Copiar resultados" para clipboard (como grep output)
      - Filtros rápidos: arquivo atual, pasta atual, workspace inteiro
      - Indicador de progresso (arquivos varredos / hits encontrados)
      - Sort dos resultados: por relevância, nome de arquivo, linha
    """

    _BATCH_SIZE = 50
    _HIST_MAX = 50
    SKIP_DIRS = frozenset(
        {
            "__pycache__",
            ".codis_trash",
            ".git",
            ".hg",
            ".svn",
            "node_modules",
            ".venv",
            "venv",
            "env",
            "dist",
            "build",
            ".mypy_cache",
            ".pytest_cache",
            ".tox",
            ".eggs",
            ".next",
            "out",
            "target",
            ".idea",
            ".vscode",
        }
    )

    def __init__(
        self,
        parent: tk.Widget,
        tmgr: ThemeManager,
        cfg: ConfigManager,
        get_root: Callable[[], Path | None],
        get_editor: Callable,
        on_open: Callable[[Path, int], None],
    ) -> None:
        """Inicializa o painel de pesquisa global em arquivos."""
        t = tmgr.theme
        super().__init__(parent, bg=t.sidebar_bg)
        self._tmgr = tmgr
        self._cfg = cfg
        self._get_root = get_root
        self._get_editor = get_editor
        self._on_open = on_open

        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._result_map: dict[str, tuple[Path, int]] = {}
        self._file_nodes: dict[str, str] = {}
        self._seen: set = set()
        self._total_hits = 0
        self._total_files = 0

        self._re_var = tk.BooleanVar(value=False)
        self._case_var = tk.BooleanVar(value=False)
        self._word_var = tk.BooleanVar(value=False)
        self._keep_var = tk.BooleanVar(value=False)
        self._scope_var = tk.StringVar(value="workspace")
        self._respect_ignore = tk.BooleanVar(value=True)

        self._q_history: list[str] = []
        self._r_history: list[str] = []
        self._q_hist_pos = -1
        self._r_hist_pos = -1

        self._ignore_rules: IgnoreRules | None = None

        self._replace_undo_stack: list[list[tuple]] = []

        self._build()

    def set_root(self, path: Path | None) -> None:
        """Define 'root'.

        Args:
            path: Caminho do arquivo ou diretório.
        """
        if path and path.is_dir():
            with contextlib.suppress(tk.TclError):
                self._ignore_rules = IgnoreRules(path)
        else:
            self._ignore_rules = None

    def _build(self) -> None:
        """Constrói os widgets e layout interno do componente."""
        t = self._tmgr.theme

        ekw = {
            "bg": t.input_bg,
            "fg": t.fg,
            "insertbackground": t.cursor,
            "relief": "flat",
            "bd": 0,
            "font": (FONT_UI, 9),
            "highlightthickness": 1,
            "highlightbackground": t.border,
        }

        hdr = tk.Frame(self, bg=t.sidebar_bg, height=36)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(
            hdr,
            text=_t("fif.title"),
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9, "bold"),
        ).pack(side="left", padx=4)

        exp_btn = tk.Label(
            hdr,
            text="⊞",
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 11),
            cursor="hand2",
            padx=6,
        )
        exp_btn.pack(side="right")
        exp_btn.bind("<Button-1>", lambda _: self._toggle_all_nodes(True))
        col_btn = tk.Label(
            hdr,
            text="⊟",
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 11),
            cursor="hand2",
            padx=2,
        )
        col_btn.pack(side="right")
        col_btn.bind("<Button-1>", lambda _: self._toggle_all_nodes(False))

        q_outer = tk.Frame(self, bg=t.sidebar_bg)
        q_outer.pack(fill="x", padx=6, pady=(4, 1))
        tk.Label(
            q_outer,
            text=_t("fif.search_lbl"),
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        ).pack(side="left")
        self._q_var = tk.StringVar()
        self._q_entry = tk.Entry(q_outer, textvariable=self._q_var, **ekw)
        self._q_entry.pack(side="left", fill="x",
                           expand=True, padx=(4, 0), ipady=2)
        self._q_entry.bind("<Return>", lambda _: self._start())
        self._q_entry.bind("<F5>", lambda _: self._start())
        self._q_entry.bind("<Escape>", lambda _: self._cancel_search())
        self._q_entry.bind("<Up>", self._hist_q_up)
        self._q_entry.bind("<Down>", self._hist_q_down)
        self._q_entry.bind("<Control-Return>", lambda _: self._start())

        r_outer = tk.Frame(self, bg=t.sidebar_bg)
        r_outer.pack(fill="x", padx=6, pady=(1, 1))
        tk.Label(
            r_outer,
            text=_t("fif.replace_lbl"),
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        ).pack(side="left")
        self._r_var = tk.StringVar()
        self._r_entry = tk.Entry(r_outer, textvariable=self._r_var, **ekw)
        self._r_entry.pack(side="left", fill="x",
                           expand=True, padx=(4, 0), ipady=2)
        self._r_entry.bind("<Up>", self._hist_r_up)
        self._r_entry.bind("<Down>", self._hist_r_down)

        f_outer = tk.Frame(self, bg=t.sidebar_bg)
        f_outer.pack(fill="x", padx=6, pady=(1, 2))
        tk.Label(
            f_outer,
            text=_t("fif.files_lbl"),
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        ).pack(side="left")
        self._f_var = tk.StringVar()
        self._f_entry = tk.Entry(f_outer, textvariable=self._f_var, **ekw)
        self._f_entry.pack(side="left", fill="x",
                           expand=True, padx=(4, 0), ipady=2)
        self._f_entry.insert(0, "*.py,*.js,*.ts,*.html,*.css,*.md")
        self._f_entry.bind("<Return>", lambda _: self._start())
        self._tip(
            self._f_entry,
            "Filtro de arquivos (globs separados por vírgula)\n"
            "Ex: *.py,*.js  ou  *  para todos\n"
            "Prefixe com ! para excluir: !*.pyc,!*.min.js",
        )

        opts = tk.Frame(self, bg=t.sidebar_bg)
        opts.pack(fill="x", padx=6, pady=2)

        for var, text, tip in (
            (self._re_var, ".*", "Expressão regular"),
            (self._case_var, "Aa", "Case sensitive"),
            (self._word_var, "\\b", "Palavra inteira"),
        ):
            cb = tk.Label(
                opts,
                text=text,
                bg=t.btn_bg,
                fg=t.fg_muted,
                font=(FONT_MONO, 9),
                padx=6,
                pady=1,
                cursor="hand2",
                relief="flat",
                highlightthickness=1,
                highlightbackground=t.border,
            )
            cb.pack(side="left", padx=2)
            self._tip(cb, tip)

            def _make_toggle(v=var, w=cb):
                def _toggle(_e=None):
                    v.set(not v.get())
                    t2 = self._tmgr.theme
                    w.config(
                        bg=t2.accent if v.get() else t2.btn_bg,
                        fg="#ffffff" if v.get() else t2.fg_muted,
                    )

                w.bind("<Button-1>", _toggle)

            _make_toggle()

        tk.Frame(opts, bg=t.border, width=1).pack(
            side="left", fill="y", padx=4, pady=2)

        for label, val in (
            ("🌐 Workspace", "workspace"),
            ("📁 Pasta", "folder"),
            ("📄 Arquivo", "file"),
        ):
            rb = tk.Label(
                opts,
                text=label,
                bg=t.btn_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 7),
                padx=5,
                pady=1,
                cursor="hand2",
                relief="flat",
            )
            rb.pack(side="left", padx=1)

            def _make_rb(v=val, w=rb):
                def _click(_e=None):
                    self._scope_var.set(v)
                    self._update_scope_btns()

                w.bind("<Button-1>", _click)

            _make_rb()
        [
            c
            for c in opts.winfo_children()
            if isinstance(c, tk.Label) and "scope" not in str(c)
        ]

        tk.Checkbutton(
            opts,
            text=".gitignore",
            variable=self._respect_ignore,
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            activebackground=t.sidebar_bg,
            selectcolor=t.sel_bg,
            font=(FONT_UI, 7),
        ).pack(side="right", padx=2)

        actions = tk.Frame(self, bg=t.sidebar_bg)
        actions.pack(fill="x", padx=6, pady=(2, 4))

        self._search_btn = self._abtn(
            actions,
            "🔍 Buscar",
            self._start,
            t.accent,
            tooltip=_t("hc.find_in_files.iniciar_busca_ctrl_enter"),
        )
        self._cancel_btn = self._abtn(
            actions,
            "✕ Parar",
            self._cancel_search,
            t.err_fg,
            tooltip=_t("hc.find_in_files.cancelar_busca_escape"),
        )
        self._cancel_btn.config(state="disabled")

        tk.Frame(actions, bg=t.sidebar_bg, width=6).pack(side="left")

        self._abtn(
            actions,
            "⇄ Subst. Tudo",
            self._replace_all,
            getattr(t, "warn_fg", "#f0a500"),
            tooltip=_t("hc.find_in_files.substituir_em_todos_os_arquivo"),
        )

        self._abtn(
            actions,
            "↩ Desfazer",
            self._undo_last_replace,
            t.fg,
            tooltip=_t("fif.replace_preview_undo"),
        )

        self._abtn(
            actions,
            "⎘ Copiar",
            self._copy_results,
            t.fg_muted,
            tooltip=_t("hc.find_in_files.copiar_resultados_como_texto_e"),
        )

        self._status = tk.Label(
            self, text="", bg=t.sidebar_bg, fg=t.fg_muted, font=(FONT_UI, 8), anchor="w"
        )
        self._status.pack(fill="x", padx=8, pady=(1, 2))

        self._prog = tk.Canvas(
            self, height=2, bg=t.sidebar_bg, highlightthickness=0)
        self._prog.pack(fill="x", padx=0)
        self._prog_max = 0

        tk.Frame(self, bg=t.border, height=1).pack(fill="x")

        tree_outer = tk.Frame(self, bg=t.sidebar_bg)
        tree_outer.pack(fill="both", expand=True)

        vsb = ttk.Scrollbar(
            tree_outer, orient="vertical", style="Thin.Vertical.TScrollbar"
        )
        vsb.pack(side="right", fill="y")
        hsb = ttk.Scrollbar(
            tree_outer, orient="horizontal", style="Thin.Horizontal.TScrollbar"
        )
        hsb.pack(side="bottom", fill="x")

        self._tree = ttk.Treeview(
            tree_outer,
            columns=("preview",),
            show="tree headings",
            style="CE.Treeview",
            selectmode="browse",
        )
        self._tree.heading("#0", text=_t("fif.file_line"))
        self._tree.heading("preview", text=_t("fif.preview"))
        self._tree.column("#0", width=140, minwidth=80, stretch=False)
        self._tree.column("preview", width=300, minwidth=120)
        self._tree.config(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self._tree.pack(side="left", fill="both", expand=True)
        vsb.config(command=self._tree.yview)
        hsb.config(command=self._tree.xview)

        self._tree.bind("<Double-Button-1>", self._on_activate)
        self._tree.bind("<Return>", self._on_activate)
        self._tree.bind("<Button-1>", self._on_single_click)

        self._tree.tag_configure("file_row", font=(
            FONT_UI, 9, "bold"), foreground=t.fg)
        self._tree.tag_configure(
            "hit_row", font=(FONT_MONO, 8), foreground=t.comment_fg
        )

    def _abtn(
        self, parent: tk.Widget, text: str, cmd: Callable, color: str, tooltip: str = ""
    ) -> tk.Button:
        """Cria e retorna um botão de ação estilizado.

        Args:
            parent: Widget pai Tkinter.
            text: Dado textual a processar.
            cmd: Parâmetro da operação.
            color: Parâmetro da operação.
            tooltip: Parâmetro da operação.
        """
        t = self._tmgr.theme
        b = tk.Button(
            parent,
            text=text,
            bg=t.btn_bg,
            fg=color,
            relief="flat",
            padx=8,
            pady=2,
            font=(FONT_UI, 8),
            cursor="hand2",
            highlightthickness=0,
            command=cmd,
        )
        b.pack(side="left", padx=2)
        b.bind("<Enter>", lambda _: b.config(bg=t.btn_hover))
        b.bind("<Leave>", lambda _: b.config(bg=t.btn_bg))
        if tooltip:
            self._tip(b, tooltip)
        return b

    def _tip(self, widget: tk.Widget, text: str) -> None:
        """Exibe tooltip para o widget indicado.

        Args:
            widget: Parâmetro da operação.
            text: Dado textual a processar.
        """
        _state: dict = {"win": None, "after_id": None}

        def _make(x: int, y: int) -> None:
            if _state.get("win") and _state["win"].winfo_exists():
                _state["win"].destroy()
            t = self._tmgr.theme
            tw = tk.Toplevel(widget)
            tw.wm_overrideredirect(True)
            tw.wm_attributes("-topmost", True)
            fr = tk.Frame(tw, bg=t.floating_border, bd=1)
            fr.pack()
            tk.Frame(fr, bg=t.floating_bg, padx=8, pady=4).pack()
            inner = fr.winfo_children()[-1]
            tk.Label(
                inner,
                text=text,
                bg=t.floating_bg,
                fg=t.fg,
                font=(FONT_UI, 8),
                justify="left",
                wraplength=320,
            ).pack()
            tw.geometry(f"+{x + 10}+{y + 12}")
            _state["win"] = tw

        def _destroy(_e=None):
            if _state.get("after_id"):
                with contextlib.suppress(tk.TclError):
                    widget.after_cancel(_state["after_id"])
                _state["after_id"] = None
            if _state.get("win"):
                with contextlib.suppress(tk.TclError):
                    _state["win"].destroy()
                _state["win"] = None

        def _enter(e: tk.Event):
            _state["after_id"] = widget.after(
                500, lambda: _make(e.x_root, e.y_root))

        widget.bind("<Enter>", _enter, add="+")
        widget.bind("<Leave>", _destroy, add="+")

    def _update_scope_btns(self) -> None:
        """Atualiza 'scope btns'."""
        t = self._tmgr.theme
        scope = self._scope_var.get()
        label_map = {
            "workspace": "🌐 Workspace",
            "folder": "📁 Pasta",
            "file": "📄 Arquivo",
        }
        for child in self.winfo_children():
            self._update_scope_recursive(child, scope, label_map, t)

    def _update_scope_recursive(self, w, scope, label_map, t) -> None:
        """Atualiza 'scope recursive'.

        Args:
            w: Parâmetro da operação.
            scope: Parâmetro da operação.
            label_map: Parâmetro da operação.
            t: Parâmetro da operação.
        """
        with contextlib.suppress(tk.TclError):
            txt = w.cget("text")
            if txt in label_map.values():
                key = next(k for k, v in label_map.items() if v == txt)
                w.config(
                    bg=t.accent if key == scope else t.btn_bg,
                    fg="#ffffff" if key == scope else t.fg_muted,
                )
        for c in w.winfo_children():
            self._update_scope_recursive(c, scope, label_map, t)

    def _compile_pattern(self) -> re.Pattern | None:
        """Compila e retorna o padrão regex com as opções de busca ativas."""
        q = self._q_var.get().strip()
        if not q:
            return None
        flags = 0 if self._case_var.get() else re.IGNORECASE
        try:
            if self._re_var.get():
                return re.compile(q, flags)
            if self._word_var.get():
                return re.compile(r"\b" + re.escape(q) + r"\b", flags)
            return re.compile(re.escape(q), flags)
        except re.error as e:
            self._set_status(f"⚠ Regex inválido: {e}")
            return None

    def _start(self) -> None:
        """Inicia o processo ou animação."""
        pat = self._compile_pattern()
        if pat is None:
            return

        if not self._keep_var.get():
            self._clear_results()

        scope = self._scope_var.get()
        if scope == "file":
            editor = self._get_editor()
            if not editor or not editor.path:
                self._set_status("Nenhum arquivo aberto.")
                return
            self._search_file(editor.path, pat, str(editor.path.name))
            return
        if scope == "folder":
            editor = self._get_editor()
            if editor and editor.path:
                root = editor.path.parent
            else:
                root = self._get_root()
        else:
            root = self._get_root()

        if not root or not root.is_dir():
            self._set_status("Nenhuma pasta aberta.")
            return

        self._cancel.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.2)
        self._cancel.clear()

        raw_filter = self._f_var.get().strip() or "*"
        include_globs, exclude_globs = self._parse_globs(raw_filter)

        if self._respect_ignore.get():
            with contextlib.suppress(tk.TclError):
                self._ignore_rules = IgnoreRules(root)

        self._search_btn.config(state="disabled")
        self._cancel_btn.config(state="normal")
        self._set_status("Buscando...")
        self._prog_max = 0

        self._thread = threading.Thread(
            target=self._search_worker,
            args=(root, pat, include_globs, exclude_globs, self._cancel),
            daemon=True,
        )
        self._thread.start()

    @staticmethod
    def _parse_globs(raw: str) -> tuple[list[str], list[str]]:
        """Separa globs de inclusão e exclusão (prefixo !)."""
        include, exclude = [], []
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            if part.startswith("!"):
                exclude.append(part[1:])
            else:
                include.append(part)
        if not include:
            include = ["*"]
        return include, exclude

    @staticmethod
    def _matches_globs(name: str, include: list[str], exclude: list[str]) -> bool:
        """Executa 'matches globs' e retorna o resultado.

        Args:
            name: Texto de identificação legível.
            include: Parâmetro da operação.
            exclude: Parâmetro da operação.
        """
        for pat in exclude:
            if fnmatch.fnmatch(name, pat):
                return False
        return any(pat == "*" or fnmatch.fnmatch(name, pat) for pat in include)

    def _search_file(self, fpath: Path, pat: re.Pattern, rel: str = "") -> None:
        """Search a single file synchronously (for scope=file mode)."""
        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
        except (OSError, PermissionError):
            return
        hits = []
        for lno, line in enumerate(content.split("\n"), 1):
            m = pat.search(line)
            if m:
                preview = self._make_preview(line, m.start(), m.end())
                hits.append((lno, preview))
        if hits:
            self.after(
                0,
                lambda h=hits, r=rel or fpath.name, p=fpath: self._add_file_hits(
                    r, h, p
                ),
            )
        self.after(
            0,
            lambda: self._set_status(
                f"{len(hits)} ocorrência(s) em {rel or fpath.name}"
            ),
        )

    def _search_worker(
        self,
        root: Path,
        pat: re.Pattern,
        include: list[str],
        exclude: list[str],
        cancel: threading.Event,
    ) -> None:
        """Executa a operação 'search worker'.

        Args:
            root: Widget pai Tkinter.
            pat: Parâmetro da operação.
            include: Parâmetro da operação.
            exclude: Parâmetro da operação.
            cancel: Parâmetro da operação.
        """
        total_hits = 0
        total_files = 0
        buffer: list[tuple[str, int, str, Path]] = []

        def flush():
            """Envia o buffer de resultados acumulados para a UI em lote."""
            nonlocal buffer
            if not buffer:
                return
            by_file: dict[str, list] = {}
            for rel, lno, preview, fpath in buffer:
                by_file.setdefault(rel, []).append((lno, preview, fpath))
            self.after(0, lambda bf=dict(by_file): self._add_batch(bf))
            buffer = []

        def scan(dir_path: Path) -> None:
            """Varre ``dir_path`` recursivamente em busca de correspondências."""
            nonlocal total_hits, total_files
            try:
                with os.scandir(dir_path) as it:
                    for entry in it:
                        if cancel.is_set():
                            return
                        if entry.name.startswith(".") and entry.name not in {
                            ".htaccess",
                            ".env",
                        }:
                            if entry.is_dir():
                                continue
                        if entry.is_dir(follow_symlinks=False):
                            if entry.name in self.SKIP_DIRS:
                                continue
                            scan(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            if not self._matches_globs(entry.name, include, exclude):
                                continue
                            fpath = Path(entry.path)
                            if (
                                self._ignore_rules
                                and self._respect_ignore.get()
                                and self._ignore_rules.is_ignored(fpath)
                            ):
                                continue
                            try:
                                content = fpath.read_text(
                                    encoding="utf-8", errors="replace"
                                )
                            except (OSError, PermissionError):
                                continue
                            try:
                                rel = str(fpath.relative_to(root))
                            except ValueError:
                                rel = fpath.name
                            file_hits = []
                            for lno, line in enumerate(content.split("\n"), 1):
                                if cancel.is_set():
                                    return
                                m = pat.search(line)
                                if m:
                                    key = (rel, lno)
                                    if key in self._seen:
                                        continue
                                    self._seen.add(key)
                                    preview = self._make_preview(
                                        line, m.start(), m.end()
                                    )
                                    file_hits.append((lno, preview))
                                    buffer.append((rel, lno, preview, fpath))
                                    total_hits += 1
                            if file_hits:
                                total_files += 1
                                self.after(
                                    0,
                                    lambda n=total_hits, f=total_files: self._set_status(
                                        f"{n} ocorrência(s) em {f} arquivo(s)..."
                                    ),
                                )
                            if len(buffer) >= self._BATCH_SIZE:
                                flush()
                            if total_hits >= _SearchEngine.MAX_MATCHES:
                                flush()
                                self.after(
                                    0,
                                    lambda: self._set_status(
                                        f"Limite de {_SearchEngine.MAX_MATCHES:,} atingido. "
                                        "Busca truncada."
                                    ),
                                )
                                return
            except PermissionError:
                pass

        scan(root)
        flush()

        self.after(
            0,
            lambda n=total_hits, f=total_files: (
                self._set_status(
                    f"{n} ocorrência(s) em {f} arquivo(s)"
                    if n
                    else "Nenhum resultado encontrado."
                ),
                self._search_btn.config(state="normal"),
                self._cancel_btn.config(state="disabled"),
                self._update_progress_bar(1.0),
            ),
        )

    @staticmethod
    def _make_preview(line: str, ms: int, me: int) -> str:
        """Retorna preview da linha com o match entre ►◄."""
        line = line.strip()
        MAX = 150
        prefix = line[:ms].lstrip()
        match = line[ms:me]
        suffix = line[me:]

        result = f"{prefix}►{match}◄{suffix}"
        if len(result) > MAX:
            result = result[:MAX] + "..."
        return result

    def _clear_results(self) -> None:
        """Limpa todos os resultados de busca exibidos no painel."""
        for iid in self._tree.get_children():
            self._tree.delete(iid)
        self._result_map.clear()
        self._file_nodes.clear()
        self._seen.clear()
        self._total_hits = 0
        self._total_files = 0

    def _add_file_hits(
        self, rel: str, hits: list[tuple[int, str]], fpath: Path
    ) -> None:
        """Executa a operação 'add file hits'.

        Args:
            rel: Parâmetro da operação.
            hits: Parâmetro da operação.
            fpath: Parâmetro da operação.
        """
        node = self._get_or_create_file_node(rel)
        for lno, preview in hits:
            iid = self._tree.insert(
                node, "end", text=f"  {lno}", values=(preview,), tags=("hit_row",)
            )
            self._result_map[iid] = (fpath, lno)
        count = len(self._tree.get_children(node))
        self._tree.item(node, values=(f"{count} resultado(s)",))

    def _add_batch(self, by_file: dict[str, list]) -> None:
        """Executa a operação 'add batch'.

        Args:
            by_file: Parâmetro da operação.
        """
        for rel, hit_list in by_file.items():
            node = self._get_or_create_file_node(rel)
            for lno, preview, fpath in hit_list:
                iid = self._tree.insert(
                    node, "end", text=f"  {lno}", values=(preview,), tags=("hit_row",)
                )
                self._result_map[iid] = (fpath, lno)
            count = len(self._tree.get_children(node))
            self._tree.item(node, values=(f"{count} resultado(s)",))

    def _get_or_create_file_node(self, rel: str) -> str:
        """Obtém e retorna 'or create file node'.

        Args:
            rel: Parâmetro da operação.
        """
        if rel in self._file_nodes:
            return self._file_nodes[rel]

        ext = rel.rsplit(".", 1)[-1].lower() if "." in rel else ""
        icon = {
            "py": "py",
            "js": "js",
            "ts": "ts",
            "html": "htm",
            "css": "css",
            "md": "md",
            "json": "{}",
            "yml": "yml",
            "sh": "🖥",
            "cpp": "yml",
            "c": "yml",
            "java": "java",
            "go": "go",
            "rs": "rs",
        }.get(ext, "txt")
        node = self._tree.insert(
            "",
            "end",
            text=f" {icon} {rel}",
            values=("",),
            open=True,
            tags=("file_row",),
        )
        self._file_nodes[rel] = node
        return node

    def _on_activate(self, _e: Any = None) -> None:
        """Trata o evento 'activate'.

        Args:
            _e: Parâmetro da operação.
        """
        sel = self._tree.selection()
        if sel and sel[0] in self._result_map:
            path, lno = self._result_map[sel[0]]
            self._on_open(path, lno)

    def _on_single_click(self, e: Any) -> None:
        """Trata o evento 'single click'.

        Args:
            e: Evento Tkinter.
        """
        iid = self._tree.identify_row(e.y)
        if iid and iid in self._result_map:
            self._tree.selection_set(iid)

    def _toggle_all_nodes(self, expand: bool) -> None:
        """Executa a operação 'toggle all nodes'.

        Args:
            expand: Parâmetro da operação.
        """
        for iid in self._tree.get_children():
            self._tree.item(iid, open=expand)

    def _replace_all(self) -> None:
        """Abre o dialog de preview antes de substituir.

        Mostra um diff interativo por arquivo, permite selecionar quais arquivos
        serão modificados e aplica as substituições de forma atômica com suporte
        a rollback completo via botão de desfazer.
        """
        pat = self._compile_pattern()
        repl = self._r_var.get()
        if not pat:
            return

        paths: set = {fpath for fpath, _ in self._result_map.values()}
        if not paths:
            self._set_status(_t("fif.replace_preview_none"))
            return
        previews: list[dict] = []
        for fpath in sorted(paths):
            try:
                original = fpath.read_text(encoding="utf-8", errors="replace")
                modified, n = pat.subn(repl, original)
                if n:
                    previews.append(
                        {
                            "path": fpath,
                            "original": original,
                            "modified": modified,
                            "count": n,
                        }
                    )
            except (OSError, PermissionError):
                continue
        if not previews:
            self._set_status(_t("fif.replace_preview_none"))
            return

        self._show_replace_preview(previews, pat, repl)

    def _show_replace_preview(
        self, previews: list[dict], _pat: re.Pattern, _repl: str
    ) -> None:
        """Exibe o dialog de preview diff interativo para Replace in Files.

        O dialog lista cada arquivo afetado com checkboxes de seleção
        individual, exibe o diff linha a linha com colorização de adições
        e remoções, e aplica as substituições de forma atômica apenas nos
        arquivos selecionados pelo usuário.

        Args:
            previews: Lista de dicts com path, original, modified e count.
            pat: Pattern regex compilado (usado só para exibição de info).
            repl: String de substituição.
        """
        import difflib as _difflib

        t = self._tmgr.theme

        dlg = tk.Toplevel(self)
        dlg.title(_t("fif.replace_preview_title", n=len(previews)))
        dlg.configure(bg=t.floating_bg)
        dlg.transient(self.winfo_toplevel())
        safe_grab_set(dlg)
        dlg.resizable(True, True)

        sw, sh = dlg.winfo_screenwidth(), dlg.winfo_screenheight()
        w, h = min(1100, sw - 80), min(700, sh - 100)
        x = (sw - w) // 2
        y = (sh - h) // 2
        dlg.geometry(f"{w}x{h}+{x}+{y}")
        dlg.minsize(700, 400)

        hdr = tk.Frame(dlg, bg=t.floating_bg)
        hdr.pack(fill="x", padx=10, pady=(10, 0))
        total_occ = sum(p["count"] for p in previews)
        adds = sum(
            sum(
                1
                for ln in _difflib.unified_diff(
                    p["original"].splitlines(), p["modified"].splitlines(), lineterm=""
                )
                if ln.startswith("+") and not ln.startswith("+++")
            )
            for p in previews
        )
        rems = sum(
            sum(
                1
                for ln in _difflib.unified_diff(
                    p["original"].splitlines(), p["modified"].splitlines(), lineterm=""
                )
                if ln.startswith("-") and not ln.startswith("---")
            )
            for p in previews
        )
        tk.Label(
            hdr,
            text=_t(
                "fif.replace_preview_changes",
                a=adds,
                r=rems,
                f=len(previews),
                n=total_occ,
            ),
            bg=t.floating_bg,
            fg=t.fg,
            font=(self._tmgr.theme and "Segoe UI" or "TkDefaultFont", 10),
        ).pack(side="left")

        paned = tk.PanedWindow(
            dlg, orient="horizontal", bg=t.border, sashwidth=5, sashrelief="flat", bd=0
        )
        paned.pack(fill="both", expand=True, padx=10, pady=6)

        left = tk.Frame(paned, bg=t.sidebar_bg)
        paned.add(left, minsize=200, width=280)

        tk.Label(
            left,
            text=_t("fif.preview_diff_col_file"),
            bg=t.sidebar_bg,
            fg=t.gutter_fg,
            font=("Segoe UI", 9),
        ).pack(anchor="w", padx=6, pady=(4, 0))

        list_frame = tk.Frame(left, bg=t.sidebar_bg)
        list_frame.pack(fill="both", expand=True)
        list_vsb = tk.Scrollbar(list_frame, orient="vertical")
        list_vsb.pack(side="right", fill="y")
        file_lb = tk.Listbox(
            list_frame,
            bg=t.sidebar_bg,
            fg=t.fg,
            selectbackground=t.sel_bg,
            selectforeground=t.sel_fg,
            activestyle="none",
            font=("Consolas", 9),
            yscrollcommand=list_vsb.set,
            borderwidth=0,
            highlightthickness=0,
            relief="flat",
        )
        file_lb.pack(fill="both", expand=True)
        list_vsb.config(command=file_lb.yview)

        check_vars: list[tk.BooleanVar] = []
        for p in previews:
            var = tk.BooleanVar(value=True)
            check_vars.append(var)
            label = f"{p['path'].name}  +{p['count']}"
            file_lb.insert("end", label)

        def _refresh_list():
            file_lb.delete(0, "end")
            for i, p in enumerate(previews):
                mark = "+" if check_vars[i].get() else "-"
                file_lb.insert(
                    "end", f"{mark}  {p['path'].name}  +{p['count']}")

        def _toggle_item(_e=None):
            sel = file_lb.curselection()
            if not sel:
                return
            i = sel[0]
            check_vars[i].set(not check_vars[i].get())
            _refresh_list()
            file_lb.selection_set(i)
            _show_diff(i)

        file_lb.bind("<space>", _toggle_item)
        file_lb.bind("<Return>", _toggle_item)

        right = tk.Frame(paned, bg=t.editor_bg)
        paned.add(right, minsize=300)

        diff_hdr = tk.Frame(right, bg=t.panel_bg, height=24)
        diff_hdr.pack(fill="x")
        diff_hdr.pack_propagate(False)
        diff_lbl = tk.Label(
            diff_hdr, text="", bg=t.panel_bg, fg=t.fg, font=("Segoe UI", 9), anchor="w"
        )
        diff_lbl.pack(side="left", padx=8, fill="x", expand=True)

        diff_frame = tk.Frame(right, bg=t.editor_bg)
        diff_frame.pack(fill="both", expand=True)
        diff_vsb = tk.Scrollbar(diff_frame, orient="vertical")
        diff_vsb.pack(side="right", fill="y")
        diff_hsb = tk.Scrollbar(diff_frame, orient="horizontal")
        diff_hsb.pack(side="bottom", fill="x")
        diff_txt = tk.Text(
            diff_frame,
            bg=t.editor_bg,
            fg=t.fg,
            font=("Consolas", 10),
            wrap="none",
            state="disabled",
            borderwidth=0,
            highlightthickness=0,
            relief="flat",
            yscrollcommand=diff_vsb.set,
            xscrollcommand=diff_hsb.set,
            cursor="arrow",
            insertwidth=0,
        )
        diff_txt.pack(fill="both", expand=True)
        diff_vsb.config(command=diff_txt.yview)
        diff_hsb.config(command=diff_txt.xview)

        diff_txt.tag_configure(
            "add", background="#1a3a1a", foreground="#a8ff87")
        diff_txt.tag_configure(
            "rem", background="#3a1a1a", foreground="#ff8787")
        diff_txt.tag_configure(
            "hunk", background=t.panel_bg, foreground=t.gutter_fg)
        diff_txt.tag_configure("ctx", foreground=t.fg)
        diff_txt.tag_configure(
            "lnum", foreground=t.gutter_fg, background=t.gutter_bg)
        diff_txt.tag_configure(
            "match", foreground="#ffdf87", font=("Consolas", 10, "bold")
        )
        diff_txt.tag_configure("skip", foreground=t.gutter_fg)

        def _show_diff(idx: int) -> None:
            """Renderiza o diff do arquivo idx no painel direito."""
            p = previews[idx]
            diff_lbl.config(text=str(p["path"]))
            diff_txt.config(state="normal")
            diff_txt.delete("1.0", "end")
            orig_lines = p["original"].splitlines(keepends=True)
            mod_lines = p["modified"].splitlines(keepends=True)
            hunks = list(
                _difflib.unified_diff(
                    orig_lines,
                    mod_lines,
                    fromfile="original",
                    tofile="modified",
                    n=3,
                    lineterm="",
                )
            )
            if not hunks:
                diff_txt.insert("end", "(sem diferenças)\n", "skip")
            else:
                for line in hunks:
                    if line.startswith("---") or line.startswith("+++"):
                        continue
                    if line.startswith("@@"):
                        diff_txt.insert("end", line + "\n", "hunk")
                    elif line.startswith("+"):
                        diff_txt.insert("end", line + "\n", "add")
                    elif line.startswith("-"):
                        diff_txt.insert("end", line + "\n", "rem")
                    else:
                        diff_txt.insert("end", line + "\n", "ctx")
            diff_txt.config(state="disabled")
            diff_txt.see("1.0")

        def _on_select(_e=None):
            sel = file_lb.curselection()
            if sel:
                _show_diff(sel[0])

        file_lb.bind("<<ListboxSelect>>", _on_select)

        if previews:
            file_lb.selection_set(0)
            _show_diff(0)

        btn_bar = tk.Frame(dlg, bg=t.floating_bg)
        btn_bar.pack(fill="x", padx=10, pady=(0, 10))

        def _select_all():
            for v in check_vars:
                v.set(True)
            _refresh_list()

        def _deselect_all():
            for v in check_vars:
                v.set(False)
            _refresh_list()

        def _apply():
            """Aplica as substituições de forma atômica com rollback disponível."""
            selected = [previews[i]
                        for i, v in enumerate(check_vars) if v.get()]
            if not selected:
                self._set_status(_t("fif.replace_preview_none"))
                dlg.destroy()
                return

            undo_batch: list[tuple] = []
            replaced_total = 0
            errors_list: list[str] = []

            for p in selected:
                fpath = p["path"]
                original = p["original"]
                modified = p["modified"]
                count = p["count"]
                try:
                    tmp = fpath.with_suffix(fpath.suffix + ".codis_rpl_tmp")
                    tmp.write_text(modified, encoding="utf-8")
                    tmp.replace(fpath)
                    undo_batch.append((fpath, original))
                    replaced_total += count
                except (OSError, PermissionError) as exc:
                    errors_list.append(f"{fpath.name}: {exc}")

                    with contextlib.suppress(OSError):
                        Path(str(fpath) + ".codis_rpl_tmp").unlink()

            if undo_batch:
                self._replace_undo_stack.append(undo_batch)

                if len(self._replace_undo_stack) > 10:
                    self._replace_undo_stack.pop(0)

            dlg.destroy()

            if errors_list:
                msg = _t(
                    "fif.replace_atomic_err",
                    n=len(errors_list),
                    ok=len(selected) - len(errors_list),
                )
                messagebox.showerror(
                    _t("fif.replace_title", "Substituir em Arquivos"), msg, parent=self
                )
            else:
                self._set_status(
                    _t("fif.replace_atomic_ok", n=len(
                        selected), c=replaced_total)
                )

            self.after(100, self._start)

        for txt, cmd in (
            (_t("fif.replace_preview_select_all"), _select_all),
            (_t("fif.replace_preview_deselect"), _deselect_all),
        ):
            b = tk.Button(
                btn_bar,
                text=txt,
                command=cmd,
                bg=t.btn_bg,
                fg=t.btn_fg,
                activebackground=t.btn_hover,
                activeforeground=t.btn_fg,
                relief="flat",
                bd=0,
                padx=10,
                pady=4,
                cursor="hand2",
                font=("Segoe UI", 9),
            )
            b.pack(side="left", padx=(0, 6))

        tk.Frame(btn_bar, bg=t.floating_bg).pack(side="left", expand=True)

        btn_cancel = tk.Button(
            btn_bar,
            text=_t("fif.replace_preview_cancel"),
            command=dlg.destroy,
            bg=t.btn_bg,
            fg=t.btn_fg,
            activebackground=t.btn_hover,
            activeforeground=t.btn_fg,
            relief="flat",
            bd=0,
            padx=14,
            pady=4,
            cursor="hand2",
            font=("Segoe UI", 9),
        )
        btn_cancel.pack(side="right", padx=(6, 0))

        n_selected = sum(1 for v in check_vars if v.get())
        btn_apply = tk.Button(
            btn_bar,
            text=_t("fif.replace_preview_apply", n=n_selected),
            command=_apply,
            bg=t.btn_primary,
            fg=t.btn_primary_fg,
            activebackground=t.btn_primary,
            activeforeground=t.btn_primary_fg,
            relief="flat",
            bd=0,
            padx=14,
            pady=4,
            cursor="hand2",
            font=("Segoe UI", 9, "bold"),
        )
        btn_apply.pack(side="right")

        def _update_apply_label(_e=None):
            n = sum(1 for v in check_vars if v.get())
            btn_apply.config(text=_t("fif.replace_preview_apply", n=n))

        for v in check_vars:
            v.trace_add("write", lambda *_: _update_apply_label())

        dlg.bind("<Escape>", lambda _: dlg.destroy())
        dlg.bind("<Control-Return>", lambda _: _apply())
        dlg.protocol("WM_DELETE_WINDOW", dlg.destroy)

    def _undo_last_replace(self) -> None:
        """Reverte a última operação de Replace in Files.

        operações de replace para rollback sequencial.
        """
        if not self._replace_undo_stack:
            self._set_status(_t("fif.replace_undo_none"))
            return
        batch = self._replace_undo_stack.pop()
        errors: list[str] = []
        restored = 0
        for fpath, original_content in batch:
            try:
                tmp = fpath.with_suffix(fpath.suffix + ".codis_undo_tmp")
                tmp.write_text(original_content, encoding="utf-8")
                tmp.replace(fpath)
                restored += 1
            except (OSError, PermissionError) as exc:
                errors.append(f"{fpath.name}: {exc}")
                with contextlib.suppress(OSError):
                    Path(str(fpath) + ".codis_undo_tmp").unlink()
        if errors:
            messagebox.showerror(
                "Undo Replace",
                "\n".join(errors[:10]),
                parent=self,
            )
        self._set_status(_t("fif.replace_undo_ok", n=restored))
        self.after(100, self._start)

    def _copy_results(self) -> None:
        """Copia os resultados da busca formatados para a área de transferência."""
        lines: list[str] = []
        for file_node in self._tree.get_children():
            file_label = self._tree.item(file_node, "text").strip()
            for hit_node in self._tree.get_children(file_node):
                lno_txt = self._tree.item(hit_node, "text").strip()
                preview = self._tree.set(hit_node, "preview")
                lines.append(f"{file_label}:{lno_txt}: {preview}")
        text = "\n".join(lines)
        self.clipboard_clear()
        self.clipboard_append(text)
        self._set_status(
            f"{len(lines)} resultado(s) copiado(s) para o clipboard")

    def _hist_q_up(self, _e: Any = None) -> str:
        """Executa 'hist q up' e retorna o resultado.

        Args:
            _e: Parâmetro da operação.
        """
        if not self._q_history:
            return "break"
        self._q_hist_pos = min(len(self._q_history) - 1, self._q_hist_pos + 1)
        self._q_var.set(self._q_history[-(self._q_hist_pos + 1)])
        self._q_entry.icursor("end")
        return "break"

    def _hist_q_down(self, _e: Any = None) -> str:
        """Executa 'hist q down' e retorna o resultado.

        Args:
            _e: Parâmetro da operação.
        """
        if self._q_hist_pos <= 0:
            self._q_hist_pos = -1
            return "break"
        self._q_hist_pos -= 1
        self._q_var.set(self._q_history[-(self._q_hist_pos + 1)])
        self._q_entry.icursor("end")
        return "break"

    def _hist_r_up(self, _e: Any = None) -> str:
        """Executa 'hist r up' e retorna o resultado.

        Args:
            _e: Parâmetro da operação.
        """
        if not self._r_history:
            return "break"
        self._r_hist_pos = min(len(self._r_history) - 1, self._r_hist_pos + 1)
        self._r_var.set(self._r_history[-(self._r_hist_pos + 1)])
        self._r_entry.icursor("end")
        return "break"

    def _hist_r_down(self, _e: Any = None) -> str:
        """Executa 'hist r down' e retorna o resultado.

        Args:
            _e: Parâmetro da operação.
        """
        if self._r_hist_pos <= 0:
            self._r_hist_pos = -1
            return "break"
        self._r_hist_pos -= 1
        self._r_var.set(self._r_history[-(self._r_hist_pos + 1)])
        self._r_entry.icursor("end")
        return "break"

    def _set_status(self, msg: str) -> None:
        """Define o valor de 'status'.

        Args:
            msg: Parâmetro da operação.
        """
        with contextlib.suppress(tk.TclError):
            self._status.config(text=f"  {msg}")

    def _update_progress_bar(self, ratio: float) -> None:
        """Atualiza 'progress bar'.

        Args:
            ratio: Parâmetro da operação.
        """
        with contextlib.suppress(tk.TclError):
            t = self._tmgr.theme
            w = self._prog.winfo_width() or 200
            self._prog.delete("all")
            if ratio > 0:
                self._prog.create_rectangle(
                    0, 0, int(w * min(ratio, 1.0)), 2, fill=t.accent, outline=""
                )

    def _cancel_search(self) -> None:
        """Cancela a busca em andamento incrementando a geração de controle."""
        self._cancel.set()
        self._search_btn.config(state="normal")
        self._cancel_btn.config(state="disabled")
        self._set_status("Busca cancelada.")

    def start_with_query(self, query: str) -> None:
        """Chamado por SearchBar -> abre e inicia com query pré-preenchida."""
        if query:
            self._q_var.set(query)
            if query not in self._q_history:
                self._q_history.append(query)
        self._start()

    def apply_theme(self, t: Theme) -> None:
        """Executa a operação 'apply theme'.

        Args:
            t: Parâmetro da operação.
        """
        self.config(bg=t.sidebar_bg)
        for w in self.winfo_children():
            with contextlib.suppress(tk.TclError):
                w.config(bg=t.sidebar_bg)
