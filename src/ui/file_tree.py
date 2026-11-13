"""Árvore de arquivos do projeto no Codis.

Provê ``FileTree``, o painel lateral que exibe a estrutura de diretórios
do workspace com suporte a operações de arquivo (criar, renomear, excluir,
arrastar), ícones por extensão e integração com o Git.

PERFORMANCE: optimized for minimal UI latency.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk
from typing import Any

from src.config import ConfigManager
from src.constants import (
    FONT_UI,
)
from src.editor.autocomplete import IgnoreRules
from src.i18n import _t
from src.theme.base import Theme
from src.theme.manager import ThemeManager
from src.trash_core import CrossDeviceError
from src.ui.trash_manager import TrashManager

logger = logging.getLogger("Codis")

_HIDDEN_SET: frozenset[str] = frozenset(
    {
        "__pycache__",
        ".codis_trash",
        ".git",
        "node_modules",
        ".DS_Store",
        ".mypy_cache",
        ".pytest_cache",
        ".tox",
        "dist",
        "build",
        ".venv",
        "venv",
        "env",
        ".idea",
        ".vscode",
        ".eggs",
    }
)

_WATCH_INTERVAL = 2.5
_GIT_DEBOUNCE = 4.0


class FileTree(tk.Frame):
    """
    Explorador de arquivos avançado com:
    - Ícones por tipo (unicode)
    - Filtro/busca incremental
    - Status do git (M=modified, A=added, ?=untracked, D=deleted)
    - Lazy loading eficiente (sem duplicações)
    - Drag-and-drop (básico) para mover arquivos
    - Menu de contexto completo (novo arquivo, pasta, renomear, deletar, copiar, cortar, colar)
    - Indicadores de erro lint (arquivo com problemas fica vermelho)
    - Seleção múltipla (Ctrl/Cmd + clique)
    - Atalhos de teclado (F2 renomear, Del deletar, Ctrl+C copiar, Ctrl+X cortar, Ctrl+V colar)
    - Watcher de mudanças no sistema de arquivos
    """

    ICONS = {
        "folder": "📁",
        "folder_open": "📂",
        ".py": "py",
        ".js": "🟨",
        ".ts": "🔷",
        ".jsx": "⚛ ",
        ".tsx": "⚛ ",
        ".html": "htm",
        ".css": "css",
        ".scss": "css",
        ".json": "{}",
        ".md": "md",
        ".txt": "txt",
        ".rs": "rs",
        ".go": "go",
        ".java": "java",
        ".cpp": "⚙ ",
        ".c": "⚙ ",
        ".h": "⚙ ",
        ".sh": "📜",
        ".yaml": "⚙ ",
        ".yml": "⚙ ",
        ".toml": "⚙ ",
        ".sql": "🗄 ",
        ".xml": "📰",
        ".env": "🔒",
        ".gitignore": "🔒",
        ".lock": "🔒",
        ".png": "🖼 ",
        ".jpg": "🖼 ",
        ".svg": "🖼 ",
        ".ico": "🖼 ",
        ".mp3": "🎵",
        ".mp4": "🎬",
        ".pdf": "📋",
        ".zip": "🗜 ",
        ".tar": "🗜 ",
        ".gz": "🗜 ",
        "default": "txt",
    }

    GIT_STATUS_COLORS = {
        "M": "#e2b96f",
        "A": "#73c991",
        "?": "#7fb7d4",
        "D": "#f44747",
        "R": "#569cd6",
        "U": "#e2b96f",
    }

    HIDDEN = {
        "__pycache__",
        ".codis_trash",
        ".git",
        "node_modules",
        ".DS_Store",
        ".mypy_cache",
        ".pytest_cache",
        ".tox",
        "dist",
        "build",
        ".venv",
        "venv",
        "env",
        ".idea",
        ".vscode",
        ".eggs",
        "*.egg-info",
    }

    def __init__(
        self,
        parent: tk.Widget,
        tmgr: ThemeManager,
        cfg: ConfigManager,
        on_open: Callable[[Path], None],
    ) -> None:
        t = tmgr.theme
        super().__init__(parent, bg=t.sidebar_bg)
        self._ignore_rules = None
        self._tmgr = tmgr
        self._cfg = cfg
        self._on_open = on_open
        self._root_path: Path | None = None
        self._root_missing_warned = False
        self._git_status: dict[str, str] = {}
        threading.Lock()
        self._problem_files: set = set()
        self._expanded: set = set()
        self._filter = ""
        self._watch_thread: threading.Thread | None = None
        self._watch_stop = threading.Event()
        self._last_mtime: dict[str, float] = {}
        self._clipboard_data: tuple[str, list[Path]] | None = None

        self._path_to_iid: dict[str, str] = {}

        self._refresh_job: str | None = None

        self._git_last_run: float = 0.0
        self._git_running: bool = False

        self._filter_job: str | None = None

        self._build()
        self._bind_shortcuts()

        self.bind("<<FileTreeRefresh>>", self._on_refresh_event)

    def _build(self) -> None:
        t = self._tmgr.theme

        hdr = tk.Frame(self, bg=t.sidebar_bg, height=36)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        tk.Label(
            hdr,
            text=_t("sidebar.explorer"),
            bg=t.sidebar_bg,
            fg=t.sidebar_header_fg,
            font=(FONT_UI, 9, "bold"),
        ).pack(side="left", padx=4)

        for icon, _, cmd in (
            ("⊕", _t("file.new"), self._new_file),
            ("📁", _t("filetree.new_folder"), self._new_folder),
            ("↻", "Refresh", self.refresh),
            ("⊟", "Collapse", self._collapse_all),
        ):
            btn = tk.Label(
                hdr,
                text=icon,
                bg=t.sidebar_bg,
                fg=t.sidebar_fg,
                font=(FONT_UI, 10),
                cursor="hand2",
                padx=4,
            )
            btn.pack(side="right")
            btn.bind("<Button-1>", lambda _, c=cmd: c())
            btn.bind("<Enter>", lambda _, b=btn: b.config(fg=t.fg))
            btn.bind("<Leave>", lambda _, b=btn: b.config(fg=t.sidebar_fg))

        self._trash_btn = tk.Label(
            hdr,
            text="🗑",
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 10),
            cursor="hand2",
            padx=4,
        )
        self._trash_btn.pack(side="right")
        self._trash_btn.bind("<Button-1>", lambda _: self._open_trash_panel())
        self._trash_btn.bind(
            "<Enter>", lambda _: self._trash_btn.config(fg=t.fg))
        self._trash_btn.bind(
            "<Leave>", lambda _: self._trash_btn.config(fg=t.fg_muted))
        self._update_trash_badge()

        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", self._on_search_change)
        search_frame = tk.Frame(self, bg=t.sidebar_bg)
        search_frame.pack(fill="x", padx=4, pady=(0, 4))
        tk.Label(
            search_frame, text="🔍", bg=t.sidebar_bg, fg=t.fg_muted, font=(FONT_UI, 9)
        ).pack(side="left")
        tk.Entry(
            search_frame,
            textvariable=self._search_var,
            bg=t.input_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            bd=0,
            font=(FONT_UI, 9),
            highlightthickness=1,
            highlightbackground=t.border,
        ).pack(side="left", fill="x", expand=True, padx=2)

        tree_frame = tk.Frame(self, bg=t.sidebar_bg)
        tree_frame.pack(fill="both", expand=True)

        style = ttk.Style()
        style.configure(
            "FileTree.Treeview",
            background=t.sidebar_bg,
            foreground=t.sidebar_fg,
            fieldbackground=t.sidebar_bg,
            borderwidth=0,
            rowheight=22,
            font=(FONT_UI, 9),
        )
        style.configure(
            "FileTree.Treeview.Heading",
            background=t.sidebar_bg,
            foreground=t.sidebar_fg,
        )
        style.map(
            "FileTree.Treeview",
            background=[("selected", t.sel_bg)],
            foreground=[("selected", t.sel_fg)],
        )

        self._tree = ttk.Treeview(
            tree_frame,
            selectmode="extended",
            show="tree",
            style="FileTree.Treeview",
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

        self._tree.bind("<Double-Button-1>", self._on_dbl)
        self._tree.bind("<Return>", self._on_dbl)
        self._tree.bind("<Button-3>", self._ctx_menu)
        self._tree.bind("<<TreeviewOpen>>", self._on_expand)
        self._tree.bind("<<TreeviewClose>>", self._on_collapse)
        self._tree.bind("<Button-1>", self._on_click, add=True)

    def _bind_shortcuts(self) -> None:
        self._tree.bind("<Delete>", self._delete_selected)
        self._tree.bind("<Shift-Delete>", self._delete_selected_permanent)
        self._tree.bind("<Control-z>", self._undo_delete)
        self.bind("<<TrashChanged>>", lambda _e: self._on_trash_changed(), add="+")
        self._tree.bind("<F2>", self._rename_selected)
        self._tree.bind("<Control-c>", self._copy_selected)
        self._tree.bind("<Control-x>", self._cut_selected)
        self._tree.bind("<Control-v>", self._paste)

    def set_root(self, path: Path) -> None:
        self._root_path = path
        self._root_missing_warned = False
        TrashManager.set_context(path or None, self._cfg)
        if path:
            TrashManager.purge_expired_async(
                lambda n: n and self._safe_event("<<TrashChanged>>"))
        if path:
            self._ignore_rules = IgnoreRules(path)
            self._ignore_rules.load()
        else:
            self._ignore_rules = None
        self._expanded = set()
        self._path_to_iid.clear()
        self._stop_watcher()
        self.refresh()
        self._start_watcher()

    def refresh(self) -> None:
        """Debounced refresh - coalece chamadas múltiplas em 80 ms."""
        if self._refresh_job:
            with contextlib.suppress(tk.TclError):
                self.after_cancel(self._refresh_job)
        self._refresh_job = self.after(80, self._do_refresh)

    def _do_refresh(self) -> None:
        self._refresh_job = None
        if not self._root_path:
            return
        if not self._root_path.exists():
            self._on_root_path_missing()
            return
        self._root_missing_warned = False
        self._rebuild_tree()
        self._update_git_status()

    def _on_root_path_missing(self) -> None:
        """Trata o caso em que a pasta raiz do workspace some do disco.

        Pode acontecer se o usuário (ou outro programa) apagar, mover ou
        desmontar a pasta enquanto o Codis está aberto. Evita que
        `_insert_dir` propague `FileNotFoundError`/`OSError` e deixe a árvore
        travada sem explicação.
        """
        if self._tree.get_children():
            self._tree.delete(*self._tree.get_children())
        self._path_to_iid.clear()
        logger.warning(
            "Pasta raiz do workspace não existe mais: %s", self._root_path)
        if not self._root_missing_warned:
            self._root_missing_warned = True
            with contextlib.suppress(tk.TclError):
                messagebox.showwarning(
                    _t("filetree.root_missing_title", "Pasta não encontrada"),
                    _t(
                        "filetree.root_missing_msg",
                        "A pasta do workspace '{path}' não foi encontrada. Ela pode "
                        "ter sido apagada, movida ou desmontada externamente.",
                        path=str(self._root_path),
                    ),
                    parent=self,
                )

    def _on_refresh_event(self, _e: Any) -> None:
        self.refresh()

    def _rebuild_tree(self) -> None:
        """Reconstrói a árvore de forma EFICIENTE usando batch delete."""
        if self._tree.get_children():
            self._tree.delete(*self._tree.get_children())
        self._path_to_iid.clear()
        if not self._root_path or not self._root_path.exists():
            return
        self._insert_dir(self._root_path, "")

    def _insert_dir(self, path: Path, parent: str) -> None:
        try:
            with os.scandir(path) as it:
                entries = sorted(
                    it,
                    key=lambda e: (not e.is_dir(
                        follow_symlinks=False), e.name.lower()),
                )
        except PermissionError:
            return
        except OSError as exc:
            logger.debug("Não foi possível listar %s: %s", path, exc)
            return

        show_hidden = self._cfg.get("show_hidden_files", False)
        q = self._filter.lower()

        for entry in entries:
            p = Path(entry.path)
            if not show_hidden and self._is_hidden_entry(entry):
                continue
            if q and not self._matches_filter_fast(p, q):
                continue
            self._insert_entry_fast(p, entry.is_dir(
                follow_symlinks=False), parent)

    def _insert_entry_fast(self, path: Path, is_dir: bool, parent: str) -> None:
        """Versão otimizada de _insert_entry - sem lock no hot-path crítico."""
        t = self._tmgr.theme
        icon = (
            self.ICONS["folder"]
            if is_dir
            else self.ICONS.get(path.suffix.lower(), self.ICONS["default"])
        )

        git_status_snap = self._git_status
        git_st = git_status_snap.get(str(path), "")
        git_col = self.GIT_STATUS_COLORS.get(git_st, "")
        has_prob = str(path) in self._problem_files

        fg_color = t.err_fg if has_prob else git_col if git_col else t.sidebar_fg

        label = f"{icon} {path.name}"
        if git_st:
            label += f"  {git_st}"

        item_id = self._tree.insert(
            parent,
            "end",
            text=label,
            tags=(str(path), "dir" if is_dir else "file"),
            open=str(path) in self._expanded,
        )
        if fg_color != t.sidebar_fg:
            self._tree.tag_configure(str(path), foreground=fg_color)

        self._path_to_iid[str(path)] = item_id

        if is_dir:
            self._tree.insert(
                item_id, "end", text=_t("ui.loading"), tags=("placeholder",)
            )

    def _recolor_tree(self) -> None:
        t = self._tmgr.theme
        git_status_snap = self._git_status
        for item in self._tree.get_children():
            self._recolor_item(item, t, git_status_snap)

    def _recolor_item(self, item: str, t: Theme, git_snap: dict[str, str]) -> None:
        tags = self._tree.item(item, "tags")
        if tags:
            path_str = tags[0]
            git_st = git_snap.get(path_str, "")
            git_col = self.GIT_STATUS_COLORS.get(git_st, "")
            has_prob = path_str in self._problem_files
            fg = t.err_fg if has_prob else git_col if git_col else t.sidebar_fg
            self._tree.tag_configure(path_str, foreground=fg)
        for child in self._tree.get_children(item):
            self._recolor_item(child, t, git_snap)

    def _is_hidden_entry(self, entry: os.DirEntry[str]) -> bool:
        """Versão rápida usando DirEntry (sem stat extra)."""
        name = entry.name
        if name.startswith(".") and name != ".env":
            return True
        if name in _HIDDEN_SET:
            return True
        if name.endswith(".egg-info"):
            return True
        if self._cfg.get("respect_ignore_files", True) and self._ignore_rules:
            with contextlib.suppress(Exception):
                if self._ignore_rules.is_ignored(Path(entry.path)):
                    return True
        return False

    @staticmethod
    def _matches_filter_fast(path: Path, q: str) -> bool:
        """Versão rápida: verifica nome primeiro, só faz rglob se for diretório."""
        if q in path.name.lower():
            return True
        if path.is_dir():
            try:
                for child in path.rglob("*"):
                    if child.is_file() and q in child.name.lower():
                        return True
            except (PermissionError, OSError):
                pass
        return False

    def _on_search_change(self, *_: Any) -> None:
        """Debounce de 200 ms no filtro de busca - evita rebuild por tecla."""
        if self._filter_job:
            with contextlib.suppress(tk.TclError):
                self.after_cancel(self._filter_job)
        self._filter_job = self.after(200, self._apply_filter)

    def _apply_filter(self) -> None:
        self._filter_job = None
        self._filter = self._search_var.get()
        self._rebuild_tree()

    def _on_click(self, e: tk.Event) -> None:
        item = self._tree.identify_row(e.y)
        if not item:
            return
        self._tree.focus(item)

    def _on_dbl(self, _e: Any = None) -> None:
        sel = self._tree.selection()
        if not sel:
            return
        item = sel[0]
        tags = self._tree.item(item, "tags")
        if not tags or "placeholder" in tags:
            return
        path = Path(tags[0])
        if path.is_file():
            self._on_open(path)
        else:
            if self._tree.item(item, "open"):
                self._tree.item(item, open=False)
            else:
                self._tree.item(item, open=True)

    def _on_expand(self, _e: tk.Event) -> None:
        item = self._tree.focus()
        tags = self._tree.item(item, "tags")
        if not tags:
            return
        path_str = tags[0]
        path = Path(path_str)
        if not path.is_dir():
            return

        children = self._tree.get_children(item)
        if children:
            if len(children) == 1:
                child_tags = self._tree.item(children[0], "tags")
                if child_tags and "placeholder" in child_tags:
                    self._tree.delete(children[0])
                else:
                    return
            else:
                return

        self._expanded.add(path_str)
        self._insert_dir(path, item)

    def _on_collapse(self, _e: tk.Event) -> None:
        item = self._tree.focus()
        tags = self._tree.item(item, "tags")
        if tags:
            self._expanded.discard(tags[0])

    def _ctx_menu(self, e: tk.Event) -> None:
        t = self._tmgr.theme
        item = self._tree.identify_row(e.y)
        if item and item not in self._tree.selection():
            self._tree.selection_set(item)
        selected = self._tree.selection()
        paths = []
        for sel in selected:
            tags = self._tree.item(sel, "tags")
            if tags and "placeholder" not in tags:
                paths.append(Path(tags[0]))

        menu = tk.Menu(
            self,
            tearoff=False,
            bg=t.floating_bg,
            fg=t.fg,
            activebackground=t.sel_bg,
            activeforeground=t.sel_fg,
            relief="flat",
        )

        if not paths:
            menu.add_command(label=_t("file.new"), command=self._new_file)
            menu.add_command(label=_t("filetree.new_folder"),
                             command=self._new_folder)
            menu.add_command(label=_t("filetree.paste"), command=self._paste)
        else:
            first = paths[0]
            is_file = first.is_file()
            menu.add_command(
                label=_t("filetree.open"), command=lambda: self._open_paths(paths)
            )
            if is_file:
                menu.add_command(
                    label=_t("filetree.open_with"),
                    command=lambda: self._open_with(first),
                )
            _img_exts = {
                ".png",
                ".jpg",
                ".jpeg",
                ".gif",
                ".bmp",
                ".svg",
                ".ico",
                ".webp",
            }
            _txt_exts = {
                ".txt",
                ".md",
                ".json",
                ".yaml",
                ".toml",
                ".cfg",
                ".ini",
                ".env",
                ".csv",
                ".xml",
                ".html",
                ".css",
                ".log",
            }
            if first.suffix.lower() in _img_exts:
                menu.add_command(
                    label=_t("filetree.preview_image"),
                    command=lambda: self._preview_image(first),
                )
            elif first.suffix.lower() in _txt_exts:
                menu.add_command(
                    label=_t("filetree.preview"),
                    command=lambda: self._preview_text(first),
                )
            menu.add_separator()
            menu.add_command(
                label=_t("filetree.cut"),
                command=lambda: self._copy_or_cut("cut", paths),
            )
            menu.add_command(
                label=_t("filetree.copy"),
                command=lambda: self._copy_or_cut("copy", paths),
            )
            menu.add_command(label=_t("filetree.paste"), command=self._paste)
            menu.add_separator()
            menu.add_command(
                label=_t("filetree.rename"), command=lambda: self._rename(first)
            )
            menu.add_command(
                label=_t("filetree.delete"), command=lambda: self._delete_paths(paths)
            )
            menu.add_command(
                label=_t("trash.delete_perm"),
                command=lambda: self._delete_paths(paths, permanent=True),
            )
            menu.add_separator()
            menu.add_command(
                label=_t("file.copy_path"),
                command=lambda: self._copy_paths(paths, full=True),
            )
            menu.add_command(
                label=_t("filetree.copy_rel_path"),
                command=lambda: self._copy_paths(paths, full=False),
            )
            menu.add_separator()
            menu.add_command(
                label=_t("filetree.reveal"), command=lambda: self._reveal(first)
            )

        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()

    def _preview_image(self, path: Path) -> None:
        win = tk.Toplevel(self)
        win.title(path.name)
        win.geometry("600x450")
        win.configure(bg="#1e1e1e")
        try:
            from PIL import Image, ImageTk  # type: ignore

            img = Image.open(path)
            img.thumbnail((580, 420))
            photo = ImageTk.PhotoImage(img)
            lbl = tk.Label(win, image=photo, bg="#1e1e1e")
            lbl.image = photo
            lbl.pack(fill="both", expand=True, padx=4, pady=4)
        except ImportError:
            try:
                photo = tk.PhotoImage(file=str(path))
                lbl = tk.Label(win, image=photo, bg="#1e1e1e")
                lbl.image = photo
                lbl.pack(fill="both", expand=True)
            except tk.TclError:
                tk.Label(
                    win,
                    text=_t(
                        "filetree.pillow_hint",
                        "Instale Pillow para pré-visualizar imagens:\npip install Pillow",
                    ),
                    bg="#1e1e1e",
                    fg="#cccccc",
                    font=("Segoe UI", 10),
                ).pack(expand=True)
        except Exception as exc:
            tk.Label(
                win,
                text=f"Erro ao carregar imagem:\n{exc}",
                bg="#1e1e1e",
                fg="#ff6b6b",
                font=("Segoe UI", 10),
            ).pack(expand=True)
        tk.Button(
            win,
            text=_t("filetree.close", "Fechar"),
            command=win.destroy,
            bg="#2d2d2d",
            fg="#cccccc",
            relief="flat",
        ).pack(pady=4)

    def _preview_text(self, path: Path) -> None:
        win = tk.Toplevel(self)
        win.title(path.name)
        win.geometry("700x500")
        win.configure(bg="#1e1e1e")
        frame = tk.Frame(win, bg="#1e1e1e")
        frame.pack(fill="both", expand=True, padx=4, pady=4)
        sb = tk.Scrollbar(frame)
        sb.pack(side="right", fill="y")
        txt = tk.Text(
            frame,
            bg="#1e1e1e",
            fg="#d4d4d4",
            insertbackground="#d4d4d4",
            font=("Consolas", 10),
            relief="flat",
            bd=0,
            yscrollcommand=sb.set,
            state="normal",
            wrap="none",
        )
        txt.pack(fill="both", expand=True)
        sb.config(command=txt.yview)
        try:
            text_content = path.read_text(encoding="utf-8", errors="replace")
            lines = text_content.splitlines()
            preview = "\n".join(lines[:500])
            if len(lines) > 500:
                preview += f"\n\n... ({len(lines) - 500} linhas omitidas)"
            txt.insert("1.0", preview)
        except Exception as exc:
            txt.insert("1.0", f"Erro ao ler arquivo:\n{exc}")
        txt.config(state="disabled")
        tk.Button(
            win,
            text=_t("filetree.open_in_editor", "Abrir no Editor"),
            bg="#2d2d2d",
            fg="#cccccc",
            relief="flat",
            command=lambda: (self._on_open(path), win.destroy()),
        ).pack(side="left", padx=4, pady=4)
        tk.Button(
            win,
            text=_t("filetree.close", "Fechar"),
            command=win.destroy,
            bg="#2d2d2d",
            fg="#cccccc",
            relief="flat",
        ).pack(side="right", padx=4, pady=4)

    def _open_paths(self, paths: list[Path]) -> None:
        for p in paths:
            if p.is_file():
                self._on_open(p)
            else:
                iid = self._path_to_iid.get(str(p))
                if iid:
                    self._tree.item(iid, open=True)

    def _open_with(self, path: Path) -> None:
        try:
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except OSError as e:
            messagebox.showerror(
                _t("ui.error_title", "Erro"), _t("err.file_open_tree", e=e), parent=self
            )

    @staticmethod
    def _reveal(path: Path) -> None:
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", str(path)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path.parent)])
        except OSError:
            pass

    def _copy_or_cut(self, mode: str, paths: list[Path]) -> None:
        self._clipboard_data = (mode, paths)

    def _paste(self) -> None:
        if not self._clipboard_data:
            return
        mode, src_paths = self._clipboard_data
        dest_dir = self._get_target_directory()
        if not dest_dir:
            return
        for src in src_paths:
            dest = dest_dir / src.name
            try:
                if mode == "copy":
                    if src.is_file():
                        shutil.copy2(src, dest)
                    else:
                        shutil.copytree(src, dest)
                else:
                    src.rename(dest)
            except (OSError, shutil.Error) as e:
                messagebox.showerror(
                    _t("hc.git_panel.error"), f"Failed to {mode}: {e}", parent=self
                )
        self.refresh()

    def _get_target_directory(self) -> Path | None:
        sel = self._tree.selection()
        if sel:
            tags = self._tree.item(sel[0], "tags")
            if tags:
                p = Path(tags[0])
                if p.is_dir():
                    return p
                return p.parent
        return self._root_path

    def _copy_paths(self, paths: list[Path], full: bool) -> None:
        if full:
            texts = [str(p) for p in paths]
        else:
            if self._root_path:
                texts = [str(p.relative_to(self._root_path)) for p in paths]
            else:
                texts = [p.name for p in paths]
        self.clipboard_clear()
        self.clipboard_append("\n".join(texts))

    def _find_and_select(self, item_id: str, target: Path) -> bool:
        data = self._tree.item(item_id)
        tags = data.get("tags", ())
        if tags and str(target) in tags:
            self._tree.selection_set(item_id)
            self._tree.see(item_id)
            return True
        for child in self._tree.get_children(item_id):
            if self._find_and_select(child, target):
                return True
        return False

    def _rename(self, path: Path) -> None:
        new_name = simpledialog.askstring(
            _t("filetree.rename"),
            _t("ui.rename_prompt"),
            initialvalue=path.name,
            parent=self,
        )
        if new_name and new_name != path.name:
            try:
                path.rename(path.parent / new_name)
                self.refresh()
            except OSError as e:
                messagebox.showerror(_t("hc.git_panel.error"), str(e))

    def _rename_selected(self, _e: tk.Event = None) -> None:
        sel = self._tree.selection()
        if sel:
            tags = self._tree.item(sel[0], "tags")
            if tags:
                self._rename(Path(tags[0]))

    def _delete_paths(self, paths: list[Path], *, permanent: bool = False) -> None:
        """Move para a lixeira do projeto (ou exclui de vez com ``permanent``)."""
        paths = [p for p in paths if p]
        if not paths:
            return
        if permanent or self._cfg.get("confirm_delete", True):
            key = "trash.delete_perm_confirm" if permanent else "trash.delete_confirm"
            msg = _t(f"{key}_one" if len(paths) == 1 else f"{key}_many",
                     name=paths[0].name, n=len(paths))
            if not messagebox.askyesno(
                _t("filetree.delete"), msg, icon="warning" if permanent else "question", parent=self
            ):
                return
        if permanent:
            def _work() -> None:
                for p in paths:
                    try:
                        TrashManager.delete(p, permanent=True)
                    except OSError as exc:
                        logger.warning("Exclusão definitiva falhou: %s", exc)
                self._safe_event("<<FileTreeRefresh>>")
                self._safe_event("<<TrashChanged>>")

            threading.Thread(target=_work, daemon=True, name="codis-perm-delete").start()
            return
        for p in paths:
            try:
                TrashManager.delete(p)
            except CrossDeviceError:
                messagebox.showerror(_t("hc.git_panel.error"), _t("trash.cross_device", name=p.name),
                                     parent=self)
            except (OSError, shutil.Error) as e:
                messagebox.showerror(
                    _t("hc.git_panel.error"),
                    _t("trash.delete_failed", name=p.name, err=e),
                    parent=self,
                )
        self.refresh()
        self._on_trash_changed()

    def _selected_paths(self) -> list[Path]:
        out: list[Path] = []
        for item in self._tree.selection():
            tags = self._tree.item(item, "tags")
            if tags and "placeholder" not in tags:
                out.append(Path(tags[0]))
        return out

    def _delete_selected(self, _e: tk.Event = None) -> None:
        self._delete_paths(self._selected_paths())

    def _delete_selected_permanent(self, _e: tk.Event = None) -> str:
        self._delete_paths(self._selected_paths(), permanent=True)
        return "break"

    def _undo_delete(self, _e: tk.Event = None) -> str:
        """Ctrl+Z na árvore: restaura a última exclusão desta sessão."""
        dest = TrashManager.undo_last()
        if dest is None:
            return "break"
        self.refresh()
        self._on_trash_changed()
        return "break"

    def _safe_event(self, name: str) -> None:
        """``event_generate`` tolerante (pode ser chamado de thread / após destroy)."""
        with contextlib.suppress(tk.TclError, RuntimeError):
            self.event_generate(name, when="tail")

    def _on_trash_changed(self) -> None:
        self._update_trash_badge()
        panel = getattr(self, "_trash_panel_ref", None)
        if panel is not None:
            with contextlib.suppress(tk.TclError):
                if panel.winfo_exists():
                    panel.refresh()

    def _clip_selected(self, mode: str = "copy", _e: tk.Event = None) -> None:
        """Copia (mode='copy') ou recorta (mode='cut') os itens selecionados."""
        sel = self._tree.selection()
        if sel:
            paths = [Path(self._tree.item(item, "tags")[0]) for item in sel]
            self._copy_or_cut(mode, paths)

    def _copy_selected(self, _e: tk.Event = None) -> None:
        self._clip_selected("copy")

    def _cut_selected(self, _e: tk.Event = None) -> None:
        self._clip_selected("cut")

    def _new_file(self, base: Path | None = None) -> None:
        folder = (
            base if base and base.is_dir() else base.parent if base else self._root_path
        )
        if not folder:
            return
        name = simpledialog.askstring(
            _t("file.new"), _t("ui.new_file_prompt"), parent=self
        )
        if not name:
            return

        if "." not in name:
            ans = messagebox.askyesno(
                "No extension",
                f"'{name}' has no extension. Create without extension?",
                parent=self,
            )
            if not ans:
                ext = simpledialog.askstring(
                    "Extension", "Enter extension (e.g., .txt, .py):", parent=self
                )
                if ext is None:
                    return
                if not ext.startswith("."):
                    ext = "." + ext
                name += ext

        p = folder / name
        try:
            p.touch()
            self.refresh()
            self._on_open(p)
        except OSError as e:
            messagebox.showerror(_t("hc.git_panel.error"), str(e))

    def _new_folder(self, base: Path | None = None) -> None:
        folder = (
            base if base and base.is_dir() else base.parent if base else self._root_path
        )
        if not folder:
            return
        name = simpledialog.askstring(
            _t("filetree.new_folder"), _t("ui.new_folder_prompt"), parent=self
        )
        if name:
            try:
                (folder / name).mkdir(parents=True, exist_ok=True)
                self.refresh()
            except OSError as e:
                messagebox.showerror(_t("hc.git_panel.error"), str(e))

    def _collapse_all(self) -> None:
        self._expanded.clear()
        for item in self._tree.get_children():
            self._tree.item(item, open=False)

    def _update_git_status(self) -> None:
        """Dispara git-status assíncrono com debounce de _GIT_DEBOUNCE segundos."""
        import time

        if self._git_running:
            return
        now = time.monotonic()
        if now - self._git_last_run < _GIT_DEBOUNCE:
            return
        if not self._root_path:
            return
        self._git_running = True
        self._git_last_run = now
        root = self._root_path

        def _run() -> None:
            new_status: dict[str, str] = {}
            try:
                r = subprocess.run(
                    ["git", "status", "--porcelain", "-u",
                        "--ignore-submodules=dirty"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    cwd=str(root),
                    timeout=5,
                    check=False,
                )
                for line in r.stdout.splitlines():
                    if len(line) >= 3:
                        status = line[:2].strip()
                        fname = line[3:].strip()
                        full = str(root / fname)
                        new_status[full] = status[0] if status else "?"
            except (OSError, FileNotFoundError, subprocess.TimeoutExpired):
                pass
            with contextlib.suppress(Exception):
                self.after(0, self._apply_git_status, new_status)

        t = threading.Thread(target=_run, daemon=True, name="codis-git-status")
        t.start()

    def _apply_git_status(self, new_status: dict[str, str]) -> None:
        if not self.winfo_exists():
            self._git_running = False
            return
        self._git_status = new_status
        self._git_running = False
        with contextlib.suppress(Exception):
            self._recolor_tree()

    def _start_watcher(self) -> None:
        if not self._root_path:
            return
        self._watch_stop.clear()
        self._watch_thread = threading.Thread(
            target=self._watch_loop, daemon=True, name="codis-filetree-watch"
        )
        self._watch_thread.start()

    def _stop_watcher(self) -> None:
        self._watch_stop.set()

    def _watch_loop(self) -> None:
        """Watcher leve: verifica mtime do diretório raiz a cada _WATCH_INTERVAL s."""
        while not self._watch_stop.is_set():
            if self._root_path and self._root_path.exists():
                try:
                    mtime = self._root_path.stat().st_mtime
                    key = str(self._root_path)
                    if self._last_mtime.get(key) != mtime:
                        self._last_mtime[key] = mtime
                        with contextlib.suppress(tk.TclError):
                            self.event_generate(
                                "<<FileTreeRefresh>>", when="tail")
                except OSError:
                    pass
            self._watch_stop.wait(_WATCH_INTERVAL)

    def _update_trash_badge(self) -> None:
        if not hasattr(self, "_trash_btn"):
            return
        count = TrashManager.count()
        t = self._tmgr.theme
        if count > 0:
            label = f"🗑{count}" if count < 100 else "🗑99+"
            accent = getattr(t, "error_fg", "#ff5555")
            self._trash_btn.config(text=label, fg=accent)
        else:
            self._trash_btn.config(text="🗑", fg=t.fg_muted)

    def _open_trash_panel(self) -> None:
        from src.ui.trash_manager import TrashPanel

        win = tk.Toplevel(self)
        win.title(_t("trash.title", "Lixeira"))
        win.geometry("420x480")
        win.resizable(True, True)
        t = self._tmgr.theme
        win.configure(bg=t.sidebar_bg)
        panel = TrashPanel(win, self._tmgr, self._cfg)
        panel.pack(fill="both", expand=True)
        self._trash_panel_ref = panel
        win.bind("<<TrashChanged>>", lambda _e: self._update_trash_badge(), add="+")
        win.bind("<Destroy>", lambda e: (setattr(self, "_trash_panel_ref", None),
                                         self._update_trash_badge()) if e.widget is win else None)

    def apply_theme(self, t: Theme) -> None:
        self.config(bg=t.sidebar_bg)
        self._rebuild_tree()
        self._update_trash_badge()
