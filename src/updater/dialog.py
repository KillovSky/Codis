"""Diálogo Tkinter de atualização do Codis.

Integra-se ao :class:`UpdateManager` e exibe progresso, changelog e
opções de modo de atualização com visual consistente com o KUI.
"""

from __future__ import annotations

import contextlib
import threading
import tkinter as tk
import webbrowser
from tkinter import ttk
from typing import Any

from .manager import UpdateManager
from .result import ReleaseInfo, UpdateMode, UpdateResult, UpdateStatus

try:
    from src.i18n import _t
except ImportError:

    # type: ignore[misc]
    def _t(key: str, default: str = "", **_kw: Any) -> str:
        return default or key

_MODES: list[tuple[UpdateMode, str, str]] = [
    (
        UpdateMode.SMART,
        "⚡  Inteligente",
        "Baixa apenas os arquivos modificados. Rápido e econômico.",
    ),
    (
        UpdateMode.ZIP,
        "📦  ZIP Completo",
        "Baixa o ZIP da release e sobrescreve os arquivos existentes.",
    ),
    (
        UpdateMode.REINSTALL,
        "🔄  Reinstalar",
        "Remove a instalação atual e instala do zero. Mais seguro.",
    ),
    (
        UpdateMode.FORCE,
        "🔁  Forçar Reinstalação",
        "Reinstala mesmo que já esteja na versão mais recente.",
    ),
    (
        UpdateMode.BINARY,
        "⚙️  Atualizar Executável",
        "Substitui o binário empacotado (PyInstaller) em tempo real.",
    ),
]


class UpdateDialog(tk.Toplevel):
    """Janela de atualização do Codis.

    Parâmetros
    ----------
    parent:
        Janela Tk pai.
    manager:
        Instância configurada do UpdateManager.
    auto_check:
        Se True, verifica atualizações automaticamente ao abrir.
    """

    def __init__(
        self,
        parent: tk.Misc,
        manager: UpdateManager,
        auto_check: bool = True,
        tmgr: Any | None = None,
    ) -> None:
        super().__init__(parent)
        self._manager = manager
        self._tmgr = tmgr
        self._release: ReleaseInfo | None = None
        self._selected_mode = tk.StringVar(value=UpdateMode.SMART.value)
        self._update_thread: threading.Thread | None = None

        self._setup_window()
        self._build_ui()

        if auto_check:
            self.after(100, self._start_check)

    def _setup_window(self) -> None:
        self.title("Codis — Atualizações")
        self.resizable(False, False)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        if self._tmgr is not None:
            t = self._tmgr.theme
            self._bg = t.editor_bg
            self._fg = t.fg
            self._accent = t.accent if hasattr(t, "accent") else "#89b4fa"
            self._dim = t.fg_muted
            self._success = getattr(t, "diff_add_fg", "#a6e3a1")
            self._warn = getattr(t, "diff_del_fg", "#f38ba8")
            self._panel = t.floating_bg
            self._border = t.floating_border
        else:
            self._bg = self.option_get("background", "") or "#1e1e2e"
            self._fg = self.option_get("foreground", "") or "#cdd6f4"
            self._accent = "#89b4fa"
            self._dim = "#6c7086"
            self._success = "#a6e3a1"
            self._warn = "#f38ba8"
            self._panel = "#181825"
            self._border = "#313244"

        self.configure(bg=self._bg)
        self.geometry("640x520")
        self._center()

    def _center(self) -> None:
        self.update_idletasks()
        pw, ph = self.winfo_reqwidth(), self.winfo_reqheight()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = (sw - pw) // 2
        y = (sh - ph) // 2
        self.geometry(f"+{x}+{y}")

    def _build_ui(self) -> None:
        header = tk.Frame(self, bg=self._panel, pady=14)
        header.pack(fill="x")

        tk.Label(
            header,
            text="🚀  Atualizações do Codis",
            bg=self._panel,
            fg=self._accent,
            font=("Segoe UI", 15, "bold"),
        ).pack()

        self._status_lbl = tk.Label(
            header,
            text="Verificando…",
            bg=self._panel,
            fg=self._dim,
            font=("Segoe UI", 9),
        )
        self._status_lbl.pack(pady=(2, 0))

        ver_frame = tk.Frame(self, bg=self._bg, pady=8)
        ver_frame.pack(fill="x", padx=20)

        self._cur_lbl = self._ver_badge(
            ver_frame, "Instalado", "…", side="left")
        self._new_lbl = self._ver_badge(
            ver_frame, "Disponível", "…", side="right")

        tk.Label(
            self,
            text="Novidades",
            bg=self._bg,
            fg=self._dim,
            font=("Segoe UI", 8, "bold"),
            anchor="w",
        ).pack(fill="x", padx=20, pady=(4, 0))

        cl_frame = tk.Frame(
            self,
            bg=self._panel,
            bd=0,
            highlightthickness=1,
            highlightbackground=self._border,
        )
        cl_frame.pack(fill="both", expand=True, padx=20, pady=(2, 8))

        self._changelog = tk.Text(
            cl_frame,
            bg=self._panel,
            fg=self._fg,
            font=("Consolas", 9),
            relief="flat",
            bd=0,
            wrap="word",
            state="disabled",
            padx=10,
            pady=8,
        )
        sb = tk.Scrollbar(cl_frame, command=self._changelog.yview, width=10)
        self._changelog.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._changelog.pack(fill="both", expand=True)

        self._set_changelog("Verificando disponibilidade de atualizações…")

        tk.Label(
            self,
            text="Modo de atualização",
            bg=self._bg,
            fg=self._dim,
            font=("Segoe UI", 8, "bold"),
            anchor="w",
        ).pack(fill="x", padx=20, pady=(0, 2))

        modes_frame = tk.Frame(self, bg=self._bg)
        modes_frame.pack(fill="x", padx=20, pady=(0, 8))

        for mode, label, tooltip in _MODES:
            row = tk.Frame(modes_frame, bg=self._bg, pady=1)
            row.pack(fill="x")
            rb = tk.Radiobutton(
                row,
                text=label,
                variable=self._selected_mode,
                value=mode.value,
                bg=self._bg,
                fg=self._fg,
                selectcolor=self._panel,
                activebackground=self._bg,
                activeforeground=self._accent,
                font=("Segoe UI", 9),
                anchor="w",
            )
            rb.pack(side="left")
            tk.Label(
                row,
                text=f"  {tooltip}",
                bg=self._bg,
                fg=self._dim,
                font=("Segoe UI", 8),
            ).pack(side="left")

        prog_frame = tk.Frame(self, bg=self._bg)
        prog_frame.pack(fill="x", padx=20, pady=(0, 4))

        self._prog_lbl = tk.Label(
            prog_frame,
            text="",
            bg=self._bg,
            fg=self._dim,
            font=("Segoe UI", 8),
            anchor="w",
        )
        self._prog_lbl.pack(fill="x")

        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "Codis.Horizontal.TProgressbar",
            troughcolor=self._panel,
            background=self._accent,
            bordercolor=self._border,
            lightcolor=self._accent,
            darkcolor=self._accent,
        )
        self._prog_bar = ttk.Progressbar(
            prog_frame,
            style="Codis.Horizontal.TProgressbar",
            orient="horizontal",
            mode="determinate",
            maximum=100,
        )
        self._prog_bar.pack(fill="x", pady=(2, 0))

        btn_frame = tk.Frame(self, bg=self._bg, pady=10)
        btn_frame.pack(fill="x", padx=20)

        self._check_btn = self._btn(
            btn_frame,
            "🔍  Verificar",
            self._start_check,
            accent=False,
        )
        self._check_btn.pack(side="left", padx=(0, 6))

        self._update_btn = self._btn(
            btn_frame,
            "⬇  Atualizar Agora",
            self._start_update,
            accent=True,
        )
        self._update_btn.pack(side="left", padx=(0, 6))
        self._update_btn.config(state="disabled")

        self._release_btn = self._btn(
            btn_frame,
            "🌐  Ver Release",
            self._open_release,
            accent=False,
        )
        self._release_btn.pack(side="left")
        self._release_btn.config(state="disabled")

        self._cancel_btn = self._btn(
            btn_frame,
            "✕  Cancelar",
            self._cancel_update,
            accent=False,
        )
        self._cancel_btn.pack(side="right")
        self._cancel_btn.config(state="disabled")

    def _ver_badge(
        self, parent: tk.Frame, label: str, value: str, side: str
    ) -> tk.Label:
        f = tk.Frame(
            parent,
            bg=self._panel,
            padx=10,
            pady=6,
            bd=0,
            highlightthickness=1,
            highlightbackground=self._border,
        )
        f.pack(
            side=side, fill="x", expand=True, padx=(0, 4) if side == "left" else (4, 0)
        )
        tk.Label(
            f, text=label, bg=self._panel, fg=self._dim, font=(
                "Segoe UI", 7, "bold")
        ).pack()
        lbl = tk.Label(
            f, text=value, bg=self._panel, fg=self._fg, font=(
                "Segoe UI", 13, "bold")
        )
        lbl.pack()
        return lbl

    def _btn(
        self,
        parent: tk.Frame,
        text: str,
        command: Any,
        accent: bool = False,
    ) -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=self._accent if accent else self._panel,
            fg=self._bg if accent else self._fg,
            activebackground=self._fg if accent else self._border,
            relief="flat",
            bd=0,
            padx=12,
            pady=5,
            font=("Segoe UI", 9, "bold" if accent else "normal"),
            cursor="hand2",
        )

    def _start_check(self) -> None:
        """Verifica atualizações em background."""
        self._set_buttons_busy(True)
        self._set_progress("Verificando atualizações…", 0.0)
        self._status_lbl.config(text="Conectando ao GitHub…")

        def _worker() -> None:
            result = self._manager.check()
            self.after(0, lambda: self._on_check_done(result))

        threading.Thread(target=_worker, daemon=True,
                         name="codis-check").start()

    def _on_check_done(self, result: UpdateResult) -> None:
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        self._set_buttons_busy(False)

        if result.release:
            self._release = result.release
            self._cur_lbl.config(text=f"v{result.current_version}")
            self._new_lbl.config(text=f"v{result.latest_version}")
            self._set_changelog(result.release.body or "Sem notas de release.")
            self._release_btn.config(state="normal")

        if result.status == UpdateStatus.UP_TO_DATE:
            self._status_lbl.config(
                text=f"✅  Codis está atualizado ({result.current_version})",
                fg=self._success,
            )
            self._update_btn.config(state="disabled")
            self._set_progress("Você está na versão mais recente.", 1.0)
        elif result.status == UpdateStatus.UPDATE_AVAILABLE:
            self._status_lbl.config(
                text=f"⬆  Nova versão disponível: {result.latest_version}",
                fg=self._accent,
            )
            self._update_btn.config(state="normal")
            self._set_progress(
                f"Versão {result.latest_version} disponível para download.", 0.0
            )
        elif result.status == UpdateStatus.ERROR:
            self._status_lbl.config(
                text=f"❌  Erro: {result.message}",
                fg=self._warn,
            )
            self._set_progress("", 0.0)

    def _start_update(self) -> None:
        """Inicia a atualização com o modo selecionado."""
        mode_val = self._selected_mode.get()
        mode = UpdateMode(mode_val)

        self._set_buttons_busy(True)
        self._cancel_btn.config(state="normal")
        self._status_lbl.config(
            text=f"Atualizando… modo: {mode.value}", fg=self._fg)

        def _on_progress(msg: str, pct: float) -> None:
            self.after(0, lambda m=msg, p=pct: self._set_progress(m, p))

        self._manager._on_progress = _on_progress  # pylint: disable=protected-access

        self._update_thread = self._manager.run_in_thread(
            mode=mode,
            callback=self._safe_after_update,
        )

    def _safe_after_update(self, result: UpdateResult) -> None:
        """Agenda _on_update_done apenas se o diálogo ainda existir."""
        try:
            if self.winfo_exists():
                self.after(0, lambda res=result: self._on_update_done(res))
        except tk.TclError:
            pass

    def _on_update_done(self, result: UpdateResult) -> None:
        self._manager._on_progress = None  # pylint: disable=protected-access
        self._set_buttons_busy(False)
        self._cancel_btn.config(state="disabled")

        if result.status == UpdateStatus.SUCCESS:
            self._status_lbl.config(
                text=f"✅  {result.message}",
                fg=self._success,
            )
            self._set_progress("Atualização concluída com sucesso!", 1.0)
            if result.restart_required:
                self._ask_restart()

        elif result.status == UpdateStatus.PENDING_RESTART:
            self._status_lbl.config(
                text=f"🔄  {result.message}",
                fg=self._accent,
            )
            self._ask_restart()

        elif result.status == UpdateStatus.CANCELLED:
            self._status_lbl.config(
                text="⚠  Atualização cancelada.", fg=self._warn)
            self._set_progress("", 0.0)

        elif result.status == UpdateStatus.ERROR:
            self._status_lbl.config(
                text=f"❌  {result.message}",
                fg=self._warn,
            )
            self._set_progress(
                "Falha. Tente novamente ou use outro modo.", 0.0)

    def _cancel_update(self) -> None:
        self._manager.cancel()
        self._cancel_btn.config(state="disabled")
        self._status_lbl.config(text="Cancelando…", fg=self._warn)

    def _open_release(self) -> None:
        if self._release:
            webbrowser.open(self._release.html_url)

    def _ask_restart(self) -> None:
        """Pergunta ao usuário se deseja reiniciar o Codis agora."""
        dialog = tk.Toplevel(self)
        dialog.title("Reiniciar?")
        dialog.resizable(False, False)
        dialog.grab_set()
        dialog.configure(bg=self._bg)
        dialog.geometry("380x150")

        self.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() - 380) // 2
        y = self.winfo_y() + (self.winfo_height() - 150) // 2
        dialog.geometry(f"+{x}+{y}")

        tk.Label(
            dialog,
            text="Reiniciar o Codis para aplicar a atualização?",
            bg=self._bg,
            fg=self._fg,
            font=("Segoe UI", 10),
            wraplength=340,
            justify="center",
        ).pack(pady=(20, 14))

        btn_row = tk.Frame(dialog, bg=self._bg)
        btn_row.pack()

        def _yes() -> None:
            dialog.destroy()
            self.destroy()
            UpdateManager.restart_app()

        def _no() -> None:
            dialog.destroy()

        tk.Button(
            btn_row,
            text="Reiniciar Agora",
            command=_yes,
            bg=self._accent,
            fg=self._bg,
            relief="flat",
            font=("Segoe UI", 9, "bold"),
            padx=14,
            pady=5,
        ).pack(side="left", padx=6)

        tk.Button(
            btn_row,
            text="Mais Tarde",
            command=_no,
            bg=self._panel,
            fg=self._fg,
            relief="flat",
            font=("Segoe UI", 9),
            padx=14,
            pady=5,
        ).pack(side="left", padx=6)

    def _set_changelog(self, text: str) -> None:
        self._changelog.config(state="normal")
        self._changelog.delete("1.0", "end")
        self._changelog.insert("1.0", text)
        self._changelog.config(state="disabled")

    def _set_progress(self, message: str, percent: float) -> None:
        with contextlib.suppress(tk.TclError):
            self._prog_lbl.config(text=message)
            self._prog_bar["value"] = max(0.0, min(100.0, percent * 100))

    def _set_buttons_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        with contextlib.suppress(tk.TclError):
            self._check_btn.config(state=state)
            self._update_btn.config(state=state)

    def _on_close(self) -> None:
        self._manager.cancel()
        self.destroy()


def open_update_dialog(
    parent: tk.Misc,
    current_version: str,
    github_token: str = "",
    auto_check: bool = True,
    tmgr: Any | None = None,
) -> UpdateDialog:
    """Abre o diálogo de atualização do Codis.

    Exemplo de uso em _app_tools.py ou menu_bar.py::

        from src.updater.dialog import open_update_dialog
        open_update_dialog(self, APP_VERSION, tmgr=self._tmgr)

    Parâmetros
    ----------
    parent:
        Janela Tk pai.
    current_version:
        Versão atualmente instalada.
    github_token:
        Token GitHub (opcional, eleva rate limit de 60 para 5000 req/h).
    auto_check:
        Verifica automaticamente ao abrir (default True).
    tmgr:
        ThemeManager do Codis para aplicar as cores do tema ativo ao diálogo.
    """
    manager = UpdateManager(
        current_version=current_version,
        github_token=github_token,
    )
    dlg = UpdateDialog(parent, manager, auto_check=auto_check, tmgr=tmgr)
    return dlg
