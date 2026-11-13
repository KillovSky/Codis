"""Codis - Workspace Switcher.

Painel flutuante para trocar rapidamente entre workspaces recentes.
Exibe preview da estrutura de diretórios e abre com um clique.

Features
--------
- Lista de workspaces recentes com ícone de status (git, Python, JS...)
- Preview de árvore de arquivos ao passar o mouse
- Busca em tempo real
- Abrir, remover da lista, abrir no explorador
- Atalho padrão: Ctrl+Shift+W
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from typing import Any

from src.constants import FONT_MONO, FONT_UI
from src.i18n import _t
from src.ui.grab_safe import safe_grab_set


def _detect_project_type(path: Path) -> str:
    """Detecta o tipo de projeto pela presença de arquivos chave."""
    checks = [
        ("🐍", ["pyproject.toml", "setup.py", "setup.cfg", "requirements.txt"]),
        ("📦", ["package.json"]),
        ("💎", ["Gemfile"]),
        ("🦀", ["Cargo.toml"]),
        ("☕", ["pom.xml", "build.gradle"]),
        ("🐘", ["composer.json"]),
        ("🐹", ["go.mod"]),
        ("🔷", ["*.csproj", "*.sln"]),
    ]
    try:
        children = {p.name for p in path.iterdir() if path.is_dir()}
    except OSError:
        return "📁"

    for icon, markers in checks:
        for marker in markers:
            if "*" in marker:
                ext = marker.lstrip("*")
                if any(f.endswith(ext) for f in children):
                    return icon
            elif marker in children:
                return icon

    if (path / ".git").is_dir():
        return "🌿"
    return "📁"


def _get_dir_tree(path: Path, max_items: int = 12) -> list[str]:
    """Retorna as primeiras `max_items` entradas da raiz do projeto."""
    lines: list[str] = []
    try:
        entries = sorted(path.iterdir(), key=lambda p: (
            p.is_file(), p.name.lower()))
        for entry in entries[:max_items]:
            prefix = "📁 " if entry.is_dir() else "📄 "
            lines.append(f"  {prefix}{entry.name}")
        remaining = sum(1 for _ in path.iterdir()) - min(
            max_items, sum(1 for _ in path.iterdir())
        )
        if remaining > 0:
            lines.append(f"  ... +{remaining} mais")
    except OSError:
        lines.append(_t("ws.no_access"))
    return lines


class WorkspaceSwitcherDialog(tk.Toplevel):
    """Diálogo de troca rápida de workspace.

    Args:
        parent: Janela raiz do Tkinter.
        tmgr: Instância de ThemeManager.
        recent_folders: Lista de Paths com workspaces recentes.
        on_open: Callback(Path) chamado quando o usuário seleciona um workspace.
        on_remove: Callback(Path) para remover da lista de recentes.
        on_browse: Callback para abrir diálogo de seleção de pasta.
    """

    def __init__(
        self,
        parent: tk.Widget,
        tmgr: Any,
        recent_folders: list[Path],
        on_open: Callable[[Path], None],
        on_remove: Callable[[Path], None],
        on_browse: Callable[[], None],
    ) -> None:
        """Inicializa o diálogo de troca de workspace."""
        t = tmgr.theme
        super().__init__(parent)
        self.title(_t("ws.title"))
        self.configure(bg=t.floating_bg)
        self.geometry("700x480")
        self.minsize(540, 340)
        self.transient(parent)
        safe_grab_set(self)

        self._tmgr = tmgr
        self._recent = [p for p in recent_folders if isinstance(p, Path)]
        self._on_open = on_open
        self._on_remove = on_remove
        self._on_browse = on_browse
        self._filtered: list[Path] = list(self._recent)
        self._selected: Path | None = None
        self._preview_job: str | None = None

        self._build(t)
        self._populate()
        self._center(parent)
        self._search_entry.focus_set()

    def _build(self, t: Any) -> None:
        tk.Frame(self, bg=t.accent, height=2).pack(fill="x")

        hdr = tk.Frame(self, bg=t.floating_bg)
        hdr.pack(fill="x", padx=14, pady=(10, 6))
        tk.Label(
            hdr,
            text=_t("ws.window_title"),
            bg=t.floating_bg,
            fg=t.t_func,
            font=(FONT_UI, 12, "bold"),
        ).pack(side="left")

        browse_btn = tk.Label(
            hdr,
            text=_t("ws.open_folder"),
            bg=t.btn_bg,
            fg=t.fg,
            font=(FONT_UI, 9),
            cursor="hand2",
            padx=8,
            pady=3,
            relief="flat",
        )
        browse_btn.pack(side="right")
        browse_btn.bind(
            "<Button-1>", lambda _: (self.destroy(), self._on_browse()))

        sf = tk.Frame(self, bg=t.floating_bg)
        sf.pack(fill="x", padx=14, pady=(0, 6))
        tk.Label(
            sf, text="🔍", bg=t.floating_bg, fg=t.fg_muted, font=(FONT_UI, 10)
        ).pack(side="left")
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._populate())
        self._search_entry = tk.Entry(
            sf,
            textvariable=self._search_var,
            bg=t.input_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            bd=0,
            font=(FONT_UI, 10),
            highlightthickness=1,
            highlightbackground=t.border,
        )
        self._search_entry.pack(side="left", fill="x",
                                expand=True, padx=4, pady=2)

        body = tk.Frame(self, bg=t.floating_bg)
        body.pack(fill="both", expand=True, padx=14, pady=4)

        left = tk.Frame(body, bg=t.floating_bg, width=340)
        left.pack(side="left", fill="both", expand=True)
        left.pack_propagate(False)

        vsb = tk.Scrollbar(left, orient="vertical", bg=t.scroll_thumb)
        self._listbox = tk.Listbox(
            left,
            bg=t.editor_bg,
            fg=t.fg,
            selectbackground=t.sel_bg,
            selectforeground=t.sel_fg,
            font=(FONT_MONO, 9),
            relief="flat",
            bd=0,
            activestyle="none",
            highlightthickness=0,
            yscrollcommand=vsb.set,
        )
        vsb.config(command=self._listbox.yview)
        vsb.pack(side="right", fill="y")
        self._listbox.pack(fill="both", expand=True)
        self._listbox.bind("<<ListboxSelect>>", self._on_select)
        self._listbox.bind("<Double-Button-1>",
                           lambda _: self._open_selected())
        self._listbox.bind("<Return>", lambda _: self._open_selected())
        self._listbox.bind("<Button-3>", self._show_context_menu)

        sep = tk.Frame(body, bg=t.border, width=1)
        sep.pack(side="left", fill="y", padx=4)

        right = tk.Frame(body, bg=t.editor_bg, width=300)
        right.pack(side="left", fill="both", expand=True)
        right.pack_propagate(False)

        tk.Label(
            right,
            text=_t("ws.preview"),
            bg=t.tabbar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8, "bold"),
            anchor="w",
            padx=8,
            pady=3,
        ).pack(fill="x")

        self._preview_text = tk.Text(
            right,
            bg=t.editor_bg,
            fg=t.fg,
            font=(FONT_MONO, 9),
            relief="flat",
            bd=0,
            state="disabled",
            padx=6,
            pady=6,
            wrap="none",
            cursor="arrow",
        )
        self._preview_text.pack(fill="both", expand=True)

        foot = tk.Frame(self, bg=t.floating_bg)
        foot.pack(fill="x", padx=14, pady=(4, 10))

        self._status_lbl = tk.Label(
            foot,
            text=_t("ws.count", found=len(self._recent),
                    total=len(self._recent)),
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        )
        self._status_lbl.pack(side="left")

        for text, cmd, primary in (
            (_t("ws.open"), self._open_selected, True),
            (_t("ws.close"), self.destroy, False),
        ):
            btn = tk.Label(
                foot,
                text=text,
                bg=t.btn_primary if primary else t.btn_bg,
                fg=t.btn_primary_fg if primary else t.fg,
                font=(FONT_UI, 9, "bold" if primary else "normal"),
                cursor="hand2",
                padx=12,
                pady=4,
                relief="flat",
            )
            btn.pack(side="right", padx=3)
            btn.bind("<Button-1>", lambda _, c=cmd: c())

        self.bind("<Escape>", lambda _: self.destroy())
        self.bind("<Return>", lambda _: self._open_selected())

    def _populate(self) -> None:
        q = self._search_var.get().lower()
        self._filtered = [
            p
            for p in self._recent
            if not q or q in p.name.lower() or q in str(p).lower()
        ]
        self._listbox.delete(0, "end")

        for path in self._filtered:
            icon = _detect_project_type(path)
            exists = "OK" if path.is_dir() else "--"
            color_hint = "" if path.is_dir() else _t("ws.not_found_marker")
            line = f"  {icon}  {path.name:<28}  {exists}{color_hint}"
            self._listbox.insert("end", line)
            if not path.is_dir():
                self._listbox.itemconfig(
                    len(self._filtered) - 1,
                    fg="#666677",
                )

        self._status_lbl.config(
            text=_t("ws.count", found=len(self._filtered),
                    total=len(self._recent))
        )

    def _on_select(self, _e: Any = None) -> None:
        sel = self._listbox.curselection()
        if not sel or sel[0] >= len(self._filtered):
            return
        self._selected = self._filtered[sel[0]]
        if self._preview_job:
            with contextlib.suppress(tk.TclError):
                self.after_cancel(self._preview_job)
        self._preview_job = self.after(150, self._update_preview)

    def _update_preview(self) -> None:
        self._preview_job = None
        if not self._selected:
            return

        path = self._selected
        t = self._tmgr.theme

        lines: list[str] = [f"📁  {path}", ""]

        if path.is_dir():
            tree = _get_dir_tree(path)
            lines.extend(tree)

            if (path / ".git").is_dir():
                lines.append("")
                lines.append(_t("ws.git_detected"))
                try:
                    r = subprocess.run(
                        ["git", "log", "--oneline", "-3"],
                        capture_output=True,
                        text=True,
                        cwd=str(path),
                        timeout=3,
                        check=False,
                    )
                    if r.returncode == 0 and r.stdout.strip():
                        for commit_line in r.stdout.strip().splitlines():
                            lines.append(f"  {commit_line}")
                except (OSError, subprocess.TimeoutExpired):
                    pass
        else:
            lines.append(_t("ws.path_not_found"))

        self._preview_text.config(state="normal")
        self._preview_text.delete("1.0", "end")
        self._preview_text.insert("1.0", "\n".join(lines))

        self._preview_text.tag_add("path", "1.0", "1.end")
        self._preview_text.tag_configure(
            "path", foreground=t.t_func, font=(FONT_MONO, 9, "bold")
        )

        self._preview_text.config(state="disabled")

    def _open_selected(self) -> None:
        if not self._selected:
            sel = self._listbox.curselection()
            if sel and sel[0] < len(self._filtered):
                self._selected = self._filtered[sel[0]]
        if self._selected and self._selected.is_dir():
            path = self._selected
            self.destroy()
            self._on_open(path)

    def _show_context_menu(self, event: tk.Event) -> None:
        idx = self._listbox.nearest(event.y)
        if idx < 0 or idx >= len(self._filtered):
            return
        self._listbox.selection_clear(0, "end")
        self._listbox.selection_set(idx)
        self._selected = self._filtered[idx]
        path = self._selected

        t = self._tmgr.theme
        menu = tk.Menu(self, tearoff=False)
        menu.configure(bg=t.panel_bg, fg=t.fg, activebackground=t.sel_bg)

        menu.add_command(label=_t("ws.open_workspace"),
                         command=self._open_selected)
        menu.add_separator()
        menu.add_command(
            label=_t("ws.show_explorer"),
            command=lambda: _open_in_explorer(path),
        )
        menu.add_separator()
        menu.add_command(
            label=_t("ws.remove_from_list"),
            command=lambda: self._remove(path),
        )

        with contextlib.suppress(tk.TclError):
            menu.tk_popup(event.x_root, event.y_root)

    def _remove(self, path: Path) -> None:
        self._on_remove(path)
        if path in self._recent:
            self._recent.remove(path)
        self._selected = None
        self._populate()
        self._preview_text.config(state="normal")
        self._preview_text.delete("1.0", "end")
        self._preview_text.config(state="disabled")

    def _center(self, parent: tk.Widget) -> None:
        self.update_idletasks()
        try:
            px, py = parent.winfo_rootx(), parent.winfo_rooty()
            pw, ph = parent.winfo_width(), parent.winfo_height()
        except tk.TclError:
            return
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry(f"+{px + (pw - w) // 2}+{py + (ph - h) // 2}")


def _open_in_explorer(path: Path) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(str(path))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError:
        pass
