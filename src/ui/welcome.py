#!/usr/bin/env python3
"""Tela de boas-vindas do Codis.

Implementa ``WelcomeScreen``, exibida na inicialização ou quando nenhum
arquivo está aberto, com acesso rápido a projetos recentes, templates,
documentação e novidades da versão.
"""

from __future__ import annotations

import contextlib
import logging
import random
import tkinter as tk
import webbrowser
from collections.abc import Callable
from pathlib import Path

from src.config import ConfigManager
from src.constants import APP_CODENAME, APP_NAME, APP_VERSION, FONT_UI
from src.i18n import _t
from src.ui.git_clone_dialog import launch_git_clone

logger = logging.getLogger("Codis")


class WelcomeScreen(tk.Frame):
    """Tela de boas-vindas exibida em novas instalações ou quando solicitada pelo menu."""

    _N_TIPS = 13

    def __init__(
        self,
        parent,
        tmgr,
        cfg: ConfigManager,
        on_new: Callable,
        on_open_file: Callable,
        on_open_folder: Callable,
        on_open_recent: Callable[[str], None],
        recent_files: list[str],
        on_clone_success: Callable[[Path], None] | None = None,
        on_log: Callable[[str, str, str], None] | None = None,
    ) -> None:
        """Cria a tela de boas-vindas do Codis.

        Args:
            parent: Widget pai Tkinter.
            config: Instância de ConfigManager.
            on_open_file: Callback() para abrir arquivo.
            on_open_folder: Callback() para abrir pasta.
            on_new_file: Callback() para novo arquivo.
        """
        t = tmgr.theme
        super().__init__(parent, bg=t.editor_bg)
        self._tmgr = tmgr
        self._cfg = cfg
        self._on_new = on_new
        self._on_open_file = on_open_file
        self._on_open_folder = on_open_folder
        self._on_open_recent = on_open_recent
        self._recent_files = recent_files
        self._on_clone_success = on_clone_success
        self._on_log = on_log
        self._build()

    def _build(self) -> None:
        """Constrói os widgets e layout interno do componente."""
        t = self._tmgr.theme

        for child in self.winfo_children():
            with contextlib.suppress(tk.TclError):
                child.destroy()

        center = tk.Frame(self, bg=t.editor_bg)
        center.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(
            center, text=APP_NAME, bg=t.editor_bg, fg=t.fg, font=(
                FONT_UI, 48, "bold")
        ).pack()

        codename_key = f"welcome.codename.{APP_CODENAME.lower()}"
        codename_t = _t(codename_key)
        release_lbl = _t("welcome.release",
                         codename=codename_t, version=APP_VERSION)
        tk.Label(
            center, text=release_lbl, bg=t.editor_bg, fg=t.fg_muted, font=(
                FONT_UI, 11)
        ).pack()
        tk.Label(center, text="", bg=t.editor_bg).pack(pady=8)

        cols = tk.Frame(center, bg=t.editor_bg)
        cols.pack()
        left_col = tk.Frame(cols, bg=t.editor_bg, padx=20)
        left_col.pack(side="left", anchor="n")
        right_col = tk.Frame(cols, bg=t.editor_bg, padx=20)
        right_col.pack(side="left", anchor="n")

        self._section_label(left_col, _t("welcome.start"))
        for icon, key, cmd in (
            ("txt", "welcome.new_file", self._on_new),
            ("📂", "welcome.open_file", self._on_open_file),
            ("🗂 ", "welcome.open_folder", self._on_open_folder),
            ("🌿", "welcome.clone_git", self._clone_repo),
        ):
            self._action_btn(left_col, icon, _t(key), cmd)

        tk.Label(left_col, text="", bg=t.editor_bg).pack(pady=4)

        self._section_label(left_col, _t("welcome.help"))
        for icon, key, url in (
            ("📖", "welcome.docs", "https://github.com/KillovSky/Codis"),
            ("🔧", "welcome.shortcuts", ""),
            ("💬", "welcome.community", ""),
        ):
            self._link_btn(left_col, icon, _t(key), url)

        self._section_label(right_col, _t("welcome.recent"))
        if not self._recent_files:
            tk.Label(
                right_col,
                text=_t("welcome.no_recent"),
                bg=t.editor_bg,
                fg=t.fg_dim,
                font=(FONT_UI, 9),
            ).pack(anchor="w")
        else:
            for f in self._recent_files[:12]:
                self._recent_item(right_col, f)

        tip_text = _t(f"welcome.tip.{random.randint(0, self._N_TIPS - 1)}")
        tk.Label(
            center,
            text=tip_text,
            bg=t.editor_bg,
            fg=t.fg_dim,
            font=(FONT_UI, 9, "italic"),
            wraplength=500,
        ).pack(pady=16)

        show_var = tk.BooleanVar(value=self._cfg.get("show_welcome", True))
        tk.Checkbutton(
            center,
            text=_t("welcome.show_on_startup"),
            variable=show_var,
            bg=t.editor_bg,
            fg=t.fg_muted,
            activebackground=t.editor_bg,
            selectcolor=t.sel_bg,
            font=(FONT_UI, 8),
            command=lambda: self._cfg.set("show_welcome", show_var.get()),
        ).pack()

    def _section_label(self, parent, text: str) -> None:
        """Executa a operação 'section label'.

        Args:
            parent: Widget pai Tkinter.
            text: Dado textual a processar.
        """
        t = self._tmgr.theme
        tk.Label(
            parent, text=text, bg=t.editor_bg, fg=t.fg_muted, font=(
                FONT_UI, 10, "bold")
        ).pack(anchor="w")

    def _action_btn(self, parent, icon: str, label: str, cmd: Callable) -> None:
        """Executa a operação 'action btn'.

        Args:
            parent: Widget pai Tkinter.
            icon: Parâmetro da operação.
            label: Texto de identificação legível.
            cmd: Parâmetro da operação.
        """
        t = self._tmgr.theme
        frame = tk.Frame(parent, bg=t.editor_bg, cursor="hand2")
        frame.pack(anchor="w", pady=1)
        tk.Label(frame, text=icon, bg=t.editor_bg, fg=t.fg, font=(FONT_UI, 11)).pack(
            side="left"
        )
        lbl = tk.Label(
            frame,
            text=f"  {label}",
            bg=t.editor_bg,
            fg=t.t_func,
            font=(FONT_UI, 10),
            cursor="hand2",
        )
        lbl.pack(side="left")
        for w in (frame, lbl):
            w.bind("<Button-1>", lambda _, c=cmd: c())
            w.bind("<Enter>", lambda _, fl=frame: fl.config(bg=t.sel_bg))
            w.bind("<Leave>", lambda _, fl=frame: fl.config(bg=t.editor_bg))

    def _link_btn(self, parent, icon: str, label: str, url: str) -> None:
        """Executa a operação 'link btn'.

        Args:
            parent: Widget pai Tkinter.
            icon: Parâmetro da operação.
            label: Texto de identificação legível.
            url: Parâmetro da operação.
        """
        t = self._tmgr.theme
        frame = tk.Frame(parent, bg=t.editor_bg, cursor="hand2")
        frame.pack(anchor="w", pady=1)
        tk.Label(frame, text=icon, bg=t.editor_bg, fg=t.fg, font=(FONT_UI, 9)).pack(
            side="left"
        )
        lbl = tk.Label(
            frame,
            text=f"  {label}",
            bg=t.editor_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
            cursor="hand2",
        )
        lbl.pack(side="left")
        for w in (frame, lbl):
            w.bind("<Enter>", lambda _, fl=frame: fl.config(bg=t.sel_bg))
            w.bind("<Leave>", lambda _, fl=frame: fl.config(bg=t.editor_bg))
        if url:
            for w in (frame, lbl):
                w.bind("<Button-1>", lambda _, u=url: webbrowser.open(u))

    def _recent_item(self, parent, f: str) -> None:
        """Executa a operação 'recent item'.

        Args:
            parent: Widget pai Tkinter.
            f: Parâmetro da operação.
        """
        t = self._tmgr.theme
        p = Path(f)
        icon = {
            ".py": "py",
            ".js": "🟨",
            ".ts": "🔷",
            ".html": "htm",
            ".css": "css",
            ".rs": "rs",
            ".go": "go",
            ".java": "java",
            ".cpp": "yml",
            ".c": "yml",
            ".md": "md",
            ".json": "📋",
        }.get(p.suffix.lower(), "txt")
        frame = tk.Frame(parent, bg=t.editor_bg, cursor="hand2")
        frame.pack(anchor="w", pady=1)
        tk.Label(frame, text=icon, bg=t.editor_bg, fg=t.fg, font=(FONT_UI, 9)).pack(
            side="left"
        )
        name_lbl = tk.Label(
            frame,
            text=f"  {p.name}",
            bg=t.editor_bg,
            fg=t.t_func,
            font=(FONT_UI, 10),
            cursor="hand2",
        )
        name_lbl.pack(side="left")
        dir_lbl = tk.Label(
            frame,
            text=f"  {p.parent}",
            bg=t.editor_bg,
            fg=t.fg_dim,
            font=(FONT_UI, 8),
            cursor="hand2",
        )
        dir_lbl.pack(side="left")
        for w in (frame, name_lbl, dir_lbl):
            w.bind("<Button-1>", lambda _, fn=f: self._on_open_recent(fn))
            w.bind("<Enter>", lambda _, fl=frame: fl.config(bg=t.sel_bg))
            w.bind("<Leave>", lambda _, fl=frame: fl.config(bg=t.editor_bg))

    def _clone_repo(self) -> None:
        """Abre o diálogo de clonar repositório Git."""
        with __import__("contextlib").suppress(tk.TclError, OSError):
            launch_git_clone(
                parent=self.winfo_toplevel(),
                theme=self._tmgr.theme,
                on_success=self._on_clone_success,
            )

    def apply_theme(self, theme) -> None:
        """Aplica o tema de cores à tela de boas-vindas.

        Args:
            theme: Dicionário de cores.
        """
        self.config(bg=theme.editor_bg)
        for w in self.winfo_children():
            w.destroy()
        self._build()
