"""Timeline de versões de arquivo do Codis.

Implementa ``FileTimeline``, um popup que exibe o histórico de snapshots
automáticos de um arquivo aberto, permitindo restaurar versões anteriores
sem depender do controle de versão Git.
"""

from __future__ import annotations

import contextlib
import datetime
import difflib
import logging
import re
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import ttk
from typing import Any

from src.constants import CONFIG_DIR, FONT_MONO, FONT_SIZE, FONT_UI
from src.i18n import _t
from src.theme.base import Theme
from src.theme.manager import ThemeManager
from src.ui.grab_safe import safe_grab_set

logger = logging.getLogger("Codis")

BACKUP_DIR = CONFIG_DIR / "backups"


class FileTimeline(tk.Toplevel):
    """
    - Salva backups automáticos com timestamp
    - Configurable max_backups e max_age_days
    - Diff visual (verde/vermelho) em relação à versão atual
    - Botão Restore para recuperar uma versão salva
    """

    def __init__(
        self,
        parent: tk.Widget,
        tmgr: ThemeManager,
        filepath: str,
        current_content: str,
        on_restore: Callable[[str], None] | None = None,
    ) -> None:
        """
        Args:
            parent: Widget pai Tkinter.
            config: Instância de ConfigManager.
            on_restore: Callback(content) para restaurar uma versão.
        """
        t = tmgr.theme
        super().__init__(parent)
        self.title(f"Timeline - {Path(filepath).name}")
        self.geometry("900x600")
        self.configure(bg=t.floating_bg)
        self.transient(parent)
        safe_grab_set(self)

        self._tmgr = tmgr
        self._path = filepath
        self._current = current_content
        self._on_restore = on_restore
        self._versions: list[Path] = []
        self._selected_content: str = ""

        self._build(t)
        self._load_versions()
        self.bind("<Escape>", lambda _: self.destroy())

    @classmethod
    def save_backup(
        cls, filepath: str, content: str, max_backups: int = 20, max_age_days: int = 30
    ) -> None:
        """Save a versioned backup. Call on every file save."""
        try:
            stem = Path(filepath).name
            bdir = BACKUP_DIR / re.sub(r"[^\w\-.]", "_", stem)
            bdir.mkdir(parents=True, exist_ok=True)

            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            dest = bdir / f"{stem}.{ts}.bak"
            dest.write_text(content, encoding="utf-8", errors="replace")

            cutoff = datetime.datetime.now() - datetime.timedelta(days=max_age_days)
            backups = sorted(bdir.glob(f"{stem}.*.bak"))
            for bak in backups:
                mtime = datetime.datetime.fromtimestamp(bak.stat().st_mtime)
                if mtime < cutoff:
                    with contextlib.suppress(OSError):
                        bak.unlink()

            backups = sorted(bdir.glob(f"{stem}.*.bak"), reverse=True)
            for bak in backups[max_backups:]:
                with contextlib.suppress(OSError):
                    bak.unlink()
        except OSError:
            pass

    def _build(self, t: Theme) -> None:
        """Constrói os widgets e layout interno do componente.

        Args:
            t: Parâmetro da operação.
        """
        hdr = tk.Frame(self, bg=t.activity_bg, height=44)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(
            hdr,
            text=f"  🕒  {_t('timeline.title')} - {Path(self._path).name}",
            bg=t.activity_bg,
            fg=t.fg,
            font=(FONT_UI, 12, "bold"),
        ).pack(side="left", padx=12, pady=8)
        tk.Label(
            hdr,
            text=_t("timeline.hint"),
            bg=t.activity_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
        ).pack(side="right", padx=16)

        tk.Frame(self, bg=t.border, height=1).pack(fill="x")

        paned = tk.PanedWindow(self, orient="horizontal",
                               bg=t.border, sashwidth=4)
        paned.pack(fill="both", expand=True)

        left = tk.Frame(paned, bg=t.sidebar_bg, width=240)
        paned.add(left, width=240, stretch="never")

        tk.Label(
            left,
            text=f"  {_t('timeline.versions')}",
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9, "bold"),
        ).pack(anchor="w", padx=4, pady=(8, 2))

        lb_frame = tk.Frame(left, bg=t.sidebar_bg)
        lb_frame.pack(fill="both", expand=True, padx=4, pady=4)
        self._lb = tk.Listbox(
            lb_frame,
            bg=t.sidebar_bg,
            fg=t.fg,
            selectbackground=t.sel_bg,
            selectforeground=t.sel_fg,
            relief="flat",
            bd=0,
            font=(FONT_UI, 9),
            activestyle="none",
            highlightthickness=1,
            highlightbackground=t.border,
        )
        lb_vsb = ttk.Scrollbar(
            lb_frame,
            orient="vertical",
            command=self._lb.yview,
            style="Thin.Vertical.TScrollbar",
        )
        lb_vsb.pack(side="right", fill="y")
        self._lb.config(yscrollcommand=lb_vsb.set)
        self._lb.pack(fill="both", expand=True)
        self._lb.bind("<<ListboxSelect>>", self._on_select)

        right = tk.Frame(paned, bg=t.editor_bg)
        paned.add(right, stretch="always")

        diff_hdr = tk.Frame(right, bg=t.tabbar_bg, height=28)
        diff_hdr.pack(fill="x")
        diff_hdr.pack_propagate(False)
        self._diff_lbl = tk.Label(
            diff_hdr,
            text=_t("timeline.select_hint"),
            bg=t.tabbar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
        )
        self._diff_lbl.pack(side="left", padx=8)

        diff_frame = tk.Frame(right, bg=t.editor_bg)
        diff_frame.pack(fill="both", expand=True)
        self._diff_text = tk.Text(
            diff_frame,
            bg=t.editor_bg,
            fg=t.fg,
            font=(FONT_MONO, FONT_SIZE - 1),
            relief="flat",
            bd=0,
            state="disabled",
            wrap="none",
            highlightthickness=0,
            padx=8,
            pady=4,
        )
        d_vsb = ttk.Scrollbar(
            diff_frame,
            orient="vertical",
            command=self._diff_text.yview,
            style="Thin.Vertical.TScrollbar",
        )
        d_hsb = ttk.Scrollbar(
            diff_frame, orient="horizontal", command=self._diff_text.xview
        )
        d_hsb.pack(side="bottom", fill="x")
        d_vsb.pack(side="right", fill="y")
        self._diff_text.config(yscrollcommand=d_vsb.set,
                               xscrollcommand=d_hsb.set)
        self._diff_text.pack(fill="both", expand=True)

        self._diff_text.tag_configure(
            "add", background="#1e3a1e", foreground="#6a9955")
        self._diff_text.tag_configure(
            "del", background="#3a1e1e", foreground="#f44747")
        self._diff_text.tag_configure("hdr", foreground="#569cd6")
        self._diff_text.tag_configure("meta", foreground="#808080")

        footer = tk.Frame(self, bg=t.floating_bg, height=44)
        footer.pack(fill="x")
        footer.pack_propagate(False)

        self._restore_btn = tk.Label(
            footer,
            text=f" ↩ {_t('timeline.restore')} ",
            bg=t.btn_primary,
            fg="#000",
            font=(FONT_UI, 9, "bold"),
            cursor="hand2",
            padx=8,
            pady=4,
        )
        self._restore_btn.pack(side="right", padx=8, pady=8)
        self._restore_btn.bind("<Button-1>", self._restore)
        self._restore_btn.config(state="normal")

        tk.Label(
            footer,
            text=f" {_t('ui.close')} ",
            bg=t.btn_bg,
            fg=t.fg,
            font=(FONT_UI, 9),
            cursor="hand2",
            padx=8,
            pady=4,
        ).pack(side="right", padx=4, pady=8)
        footer.winfo_children()[-1].bind("<Button-1>",
                                         lambda _: self.destroy())

        self._selected_content: str | None = None

    def _load_versions(self) -> None:
        """Carrega as versões do arquivo atual a partir do histórico Git."""
        stem = Path(self._path).name
        bdir = BACKUP_DIR / re.sub(r"[^\w\-.]", "_", stem)
        if not bdir.exists():
            self._lb.insert("end", "  (no backups yet)")
            return
        backups = sorted(bdir.glob(f"{stem}.*.bak"), reverse=True)
        self._versions = list(backups)
        for bak in backups:
            parts = bak.stem.split(".")
            ts_part = parts[-1] if parts else bak.stem
            try:
                dt = datetime.datetime.strptime(ts_part, "%Y%m%d_%H%M%S")
                label = dt.strftime("%Y-%m-%d  %H:%M:%S")
            except ValueError:
                label = ts_part
            self._lb.insert("end", f"  {label}")

    def _on_select(self, _e: Any = None) -> None:
        """Exibe o diff da versão selecionada.

        Args:
            e: Evento de seleção.
        """
        sel = self._lb.curselection()
        if not sel or sel[0] >= len(self._versions):
            return
        bak_path = self._versions[sel[0]]
        try:
            old = bak_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        self._selected_content = old
        self._show_diff(old, self._current)
        self._diff_lbl.config(text=f"  {bak_path.name}  <->  atual")

    def _show_diff(self, old: str, new: str) -> None:
        """Executa a operação 'show diff'.

        Args:
            old: Parâmetro da operação.
            new: Parâmetro da operação.
        """
        self._diff_text.config(state="normal")
        self._diff_text.delete("1.0", "end")
        diff = list(
            difflib.unified_diff(
                old.splitlines(keepends=True),
                new.splitlines(keepends=True),
                fromfile="backup",
                tofile="current",
                n=3,
            )
        )
        for line in diff:
            if line.startswith("+++") or line.startswith("---"):
                tag = "hdr"
            elif line.startswith("@@"):
                tag = "meta"
            elif line.startswith("+"):
                tag = "add"
            elif line.startswith("-"):
                tag = "del"
            else:
                tag = ""
            self._diff_text.insert("end", line, (tag,) if tag else ())
        if not diff:
            self._diff_text.insert(
                "end", "  (no differences - files are identical)\n", "meta"
            )
        self._diff_text.config(state="disabled")

    def _restore(self, _e: Any = None) -> None:
        if self._selected_content is None:
            return
        if self._on_restore:
            self._on_restore(self._selected_content)
        self.destroy()
