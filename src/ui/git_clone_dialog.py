"""Codis - GitCloneDialog.

Popup modal para clonar repositórios Git com:
- Barra de progresso real (parseia output do git --progress)
- Streaming de stdout/stderr em tempo real via thread
- Log completo enviado ao OutputPanel ("Git Clone")
- Callback on_success(dest_path) chamado ao término com sucesso
- Botão Cancel que mata o processo git
"""

from __future__ import annotations

import contextlib
import logging
import re
import subprocess
import threading
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, simpledialog, ttk

from src.constants import FONT_MONO, FONT_UI
from src.i18n import _t
from src.theme.base import Theme

logger = logging.getLogger("Codis")


def _expand_git_url(url: str) -> str:
    """Expande atalhos de URL para URLs Git completas.

    Exemplos:
        KillovSky/Iris -> https://github.com/KillovSky/Iris
        gh:KillovSky/Iris -> https://github.com/KillovSky/Iris
        gl:user/repo -> https://gitlab.com/user/repo
        bb:user/repo -> https://bitbucket.org/user/repo
        github:user/repo -> https://github.com/KillovSky/Iris
        URLs completas -> inalteradas
    """
    if not url:
        return url

    if url.startswith(("http://", "https://", "git://", "git@", "ssh://", "ftp://")):
        return url

    if url.startswith("gh:"):
        return "https://github.com/" + url[3:]
    if url.startswith(("gl:", "gitlab:")):
        return "https://gitlab.com/" + url[url.index(":") + 1:]
    if url.startswith(("bb:", "bitbucket:")):
        return "https://bitbucket.org/" + url[url.index(":") + 1:]
    if url.startswith("github:"):
        return "https://github.com/" + url[7:]

    if re.match(r"^[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+$", url):
        return "https://github.com/" + url
    return url


_PCT_RE = re.compile(r"(\d{1,3})%")

_PHASES = [
    "Enumerating objects",
    "Counting objects",
    "Compressing objects",
    "Receiving objects",
    "Resolving deltas",
    "Updating files",
]

_PHASE_WEIGHTS = [0, 5, 10, 20, 85, 95, 100]


class GitCloneDialog:
    """
    Diálogo modal de clonagem Git com progresso em tempo real.

    Parâmetros
    ----------
    parent : tk.Widget
        Janela pai.
    theme : Theme
        Tema visual ativo.
    on_success : Callable[[Path], None], opcional
        Chamado com o caminho do repo clonado em caso de sucesso.
    on_log : Callable[[str, str, str], None], opcional
        Chamado com (mensagem, canal, nível) para o OutputPanel.
    """

    _W = 520
    _H = 320

    def __init__(
        self,
        parent: tk.Widget,
        theme: Theme,
        on_success: Callable[[Path], None] | None = None,
        on_log: Callable[[str, str, str], None] | None = None,
    ) -> None:
        """Cria o diálogo de clone de repositório Git.

        Args:
            root: Janela raiz Tkinter.
            on_complete: Callback(path) invocado ao concluir o clone.
        """
        self._parent = parent
        self._t = theme
        self._on_success = on_success
        self._on_log = on_log
        self._proc: subprocess.Popen | None = None
        self._cancelled = False
        self._done = False

        url = simpledialog.askstring(
            _t("welcome.clone_title"),
            _t("welcome.clone_prompt"),
            parent=parent,
        )
        if not url or not url.strip():
            return
        url = url.strip()
        url = _expand_git_url(url)

        dest = filedialog.askdirectory(
            title=_t("welcome.clone_into"), parent=parent)
        if not dest:
            return

        repo_name = re.sub(r"\.git$", "", url.rstrip(
            "/").rsplit("/", 1)[-1]) or "repo"
        dest_path = Path(dest) / repo_name

        self._url = url
        self._dest = dest_path

        self._build_ui()
        self._start_clone()

    def _build_ui(self) -> None:
        """Constrói o layout do diálogo de clone."""
        t = self._t

        self._win = tk.Toplevel(self._parent)
        self._win.title(_t("welcome.clone_title"))
        self._win.resizable(False, False)
        self._win.attributes("-topmost", True)
        self._win.configure(bg=t.floating_bg)
        self._win.protocol("WM_DELETE_WINDOW", self._on_cancel)

        self._parent.update_idletasks()
        px = self._parent.winfo_rootx()
        py = self._parent.winfo_rooty()
        pw = self._parent.winfo_width()
        ph = self._parent.winfo_height()
        x = px + (pw - self._W) // 2
        y = py + (ph - self._H) // 2
        self._win.geometry(f"{self._W}x{self._H}+{x}+{y}")

        outer = tk.Frame(self._win, bg=t.border, padx=1, pady=1)
        outer.pack(fill="both", expand=True)
        inner = tk.Frame(outer, bg=t.floating_bg, padx=20, pady=16)
        inner.pack(fill="both", expand=True)

        hdr = tk.Frame(inner, bg=t.floating_bg)
        hdr.pack(fill="x", pady=(0, 10))
        tk.Label(hdr, text="🌿", bg=t.floating_bg, font=(FONT_UI, 18)).pack(
            side="left", padx=(0, 8)
        )
        title_frame = tk.Frame(hdr, bg=t.floating_bg)
        title_frame.pack(side="left", fill="x", expand=True)
        tk.Label(
            title_frame,
            text=_t("welcome.clone_title"),
            bg=t.floating_bg,
            fg=t.fg,
            font=(FONT_UI, 12, "bold"),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            title_frame,
            text=self._url[:60] + ("..." if len(self._url) > 60 else ""),
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            anchor="w",
        ).pack(fill="x")

        tk.Label(
            inner,
            text=f"→ {self._dest}",
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            anchor="w",
        ).pack(fill="x", pady=(0, 8))

        self._phase_var = tk.StringVar(value=_t("git.clone_starting"))
        tk.Label(
            inner,
            textvariable=self._phase_var,
            bg=t.floating_bg,
            fg=t.fg,
            font=(FONT_UI, 9, "bold"),
            anchor="w",
        ).pack(fill="x")

        style = ttk.Style()
        style.configure(
            "GitClone.Horizontal.TProgressbar",
            background=t.ok_fg,
            troughcolor=t.input_bg,
            borderwidth=0,
            thickness=8,
        )
        self._pbar = ttk.Progressbar(
            inner,
            style="GitClone.Horizontal.TProgressbar",
            mode="determinate",
            maximum=100,
            value=0,
        )
        self._pbar.pack(fill="x", pady=(4, 2))

        self._pct_var = tk.StringVar(value="0%")
        tk.Label(
            inner,
            textvariable=self._pct_var,
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            anchor="e",
        ).pack(fill="x")

        log_frame = tk.Frame(inner, bg=t.input_bg, pady=1)
        log_frame.pack(fill="both", expand=True, pady=(8, 8))
        self._log_text = tk.Text(
            log_frame,
            bg=t.input_bg,
            fg=t.fg_muted,
            font=(FONT_MONO, 8),
            height=5,
            state="disabled",
            relief="flat",
            wrap="none",
            bd=0,
        )
        self._log_text.pack(fill="both", expand=True, padx=4, pady=2)

        hsb = ttk.Scrollbar(
            log_frame, orient="horizontal", command=self._log_text.xview
        )
        hsb.pack(fill="x")
        self._log_text.configure(xscrollcommand=hsb.set)

        btn_row = tk.Frame(inner, bg=t.floating_bg)
        btn_row.pack(fill="x")
        self._cancel_btn = tk.Button(
            btn_row,
            text=_t("ui.cancel", "Cancel"),
            bg=t.btn_bg,
            fg=t.btn_fg,
            activebackground=t.btn_hover,
            relief="flat",
            padx=16,
            pady=4,
            font=(FONT_UI, 9),
            command=self._on_cancel,
        )
        self._cancel_btn.pack(side="right")

        self._win.update_idletasks()

    def _start_clone(self) -> None:
        """Valida os campos e inicia o processo de clone em thread separada."""
        self._log(_t("git.clone_starting"), "Git Clone", "info")
        self._log(
            f'git clone --progress "{self._url}" "{self._dest}"', "Git Clone", "info"
        )

        try:
            self._proc = subprocess.Popen(
                ["git", "clone", "--progress", self._url, str(self._dest)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except FileNotFoundError:
            self._finish_error(_t("err.git_not_found"))
            return
        except OSError as exc:
            self._finish_error(str(exc))
            return

        t = threading.Thread(target=self._read_output, daemon=True)
        t.start()

    def _read_output(self) -> None:
        """Lê stdout+stderr do processo git em thread separada."""
        current_phase_idx = 0
        try:
            for raw_line in self._proc.stdout:
                if self._cancelled:
                    break
                line = raw_line.rstrip()
                if not line:
                    continue

                for idx, phase in enumerate(_PHASES):
                    if phase.lower() in line.lower():
                        current_phase_idx = idx
                        break

                pct_match = _PCT_RE.search(line)
                if pct_match:
                    line_pct = int(pct_match.group(1))

                    base = _PHASE_WEIGHTS[current_phase_idx]
                    nxt = _PHASE_WEIGHTS[
                        min(current_phase_idx + 1, len(_PHASE_WEIGHTS) - 1)
                    ]
                    global_pct = int(base + (nxt - base) * line_pct / 100)
                    self._update_progress(
                        global_pct, _PHASES[current_phase_idx])

                self._log(line, "Git Clone", "info")

        except OSError as exc:
            self._log(str(exc), "Git Clone", "warn")

        with contextlib.suppress(OSError):
            self._proc.wait()

        rc = self._proc.returncode if self._proc else -1

        if self._cancelled:
            self._win_safe(self._finish_cancelled)
        elif rc == 0:
            self._win_safe(self._finish_success)
        else:
            self._win_safe(
                lambda: self._finish_error(
                    _t(
                        "git.clone_failed_code",
                        "Clone failed (exit code {code})",
                        code=rc,
                    )
                )
            )

    def _update_progress(self, pct: int, phase: str) -> None:
        """Agenda atualização da UI no thread principal."""

        def _do() -> None:
            try:
                self._pbar["value"] = pct
                self._pct_var.set(f"{pct}%")
                self._phase_var.set(phase)
            except tk.TclError:
                pass

        self._win_safe(_do)

    def _append_log_line(self, line: str) -> None:
        """Adiciona linha ao log interno do dialog (últimas N linhas)."""
        try:
            self._log_text.config(state="normal")
            self._log_text.insert("end", line + "\n")

            lines = int(self._log_text.index("end-1c").split(".", 1)[0])
            if lines > 200:
                self._log_text.delete("1.0", f"{lines - 200}.0")
            self._log_text.see("end")
            self._log_text.config(state="disabled")
        except tk.TclError:
            pass

    def _log(self, msg: str, channel: str, level: str) -> None:
        """Envia para o OutputPanel e para o log interno do dialog."""
        if self._on_log:
            try:
                self._on_log(msg, channel, level)
            except Exception:
                logger.debug("Suppressed error", exc_info=True)
        self._win_safe(lambda m=msg: self._append_log_line(m))

    def _win_safe(self, fn: Callable) -> None:
        """Agenda execução no thread principal, ignorando se a janela já foi destruída."""
        with contextlib.suppress(tk.TclError):
            self._win.after(0, fn)

    def _finish_success(self) -> None:
        """Trata a conclusão bem-sucedida do clone e notifica o callback."""
        if self._done:
            return
        self._done = True
        self._pbar["value"] = 100
        self._pct_var.set("100%")
        self._phase_var.set(_t("git.clone_success", "Clone complete!"))

        with contextlib.suppress(tk.TclError):
            ttk.Style().configure(
                "GitClone.Horizontal.TProgressbar", background=self._t.ok_fg
            )

        self._log(
            _t(
                "git.clone_success_log",
                "✔ Clone complete: {path}",
                path=str(self._dest),
            ),
            "Git Clone",
            "ok",
        )

        self._cancel_btn.config(
            text=_t("git.clone_open", _t("ui.open_folder")),
            bg=self._t.btn_primary,
            fg=self._t.btn_primary_fg,
            command=self._open_and_close,
        )

        if self._on_success and self._dest.exists():
            try:
                self._on_success(self._dest)
            except Exception:
                logger.debug("Suppressed error", exc_info=True)

    def _finish_error(self, msg: str) -> None:
        """Exibe o erro do clone e restaura a UI ao estado inicial."""
        if self._done:
            return
        self._done = True
        self._phase_var.set(_t("welcome.clone_failed"))
        with contextlib.suppress(tk.TclError):
            ttk.Style().configure(
                "GitClone.Horizontal.TProgressbar", background=self._t.err_fg
            )
        self._log(f"✖ {msg}", "Git Clone", "error")
        self._cancel_btn.config(
            text=_t("ui.close", "Close"), command=self._win.destroy)

    def _finish_cancelled(self) -> None:
        """Trata o cancelamento do clone e restaura a UI ao estado inicial."""
        if self._done:
            return
        self._done = True

        self._log(
            _t("git.clone_cancelled_log", "Clone cancelled by user."),
            "Git Clone",
            "warn",
        )
        self._cancel_btn.config(
            text=_t("ui.close", "Close"), command=self._win.destroy)

    def _on_cancel(self) -> None:
        """Cancela o clone em andamento e fecha o diálogo."""
        if self._done:
            self._win.destroy()
            return
        self._cancelled = True
        if self._proc and self._proc.poll() is None:
            with contextlib.suppress(OSError):
                self._proc.terminate()

        if self._done:
            self._win.destroy()

    def _open_and_close(self) -> None:
        """Abre o repositório clonado no workspace e fecha o diálogo."""
        self._win.destroy()


def launch_git_clone(
    parent: tk.Widget,
    theme: Theme,
    on_success: Callable[[Path], None] | None = None,
    on_log: Callable[[str, str, str], None] | None = None,
) -> None:
    """
    Ponto de entrada para iniciar o diálogo de clone Git.

    Pode ser chamado de qualquer lugar que tenha acesso ao parent e theme.
    """
    GitCloneDialog(parent, theme, on_success=on_success, on_log=on_log)
