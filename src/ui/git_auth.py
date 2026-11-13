"""Codis - GitAuthManager.

Gerencia autenticação Git para operações que exigem login:
- Detecção de prompt de credencial no stderr do processo
- Armazenamento ofuscado (base64) de tokens HTTPS por host, via config
- Injeção de credenciais via um helper GIT_ASKPASS estático que lê
  usuário/token de variáveis de ambiente do subprocess (nunca embutidos
  em texto de shell/batch, eliminando risco de injeção, e nunca
  persistidos em disco em texto plano)
- Suporte a SSH (delega ao ssh-agent, sem armazenar chaves)
- Diálogo visual de login dentro do Codis
"""

from __future__ import annotations

import base64
import contextlib
import logging
import os
import shlex
import stat
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from src.constants import FONT_MONO, FONT_UI
from src.i18n import _t
from src.ui.grab_safe import safe_grab_set

if TYPE_CHECKING:
    from src.config import ConfigManager
    from src.theme.base import Theme

logger = logging.getLogger("Codis")

_ASKPASS_USER_ENV = "CODIS_GIT_ASKPASS_USER"
_ASKPASS_TOKEN_ENV = "CODIS_GIT_ASKPASS_TOKEN"

_ASKPASS_HELPER_SOURCE = f'''\
"""Codis - helper GIT_ASKPASS gerado automaticamente. Não edite manualmente."""
import os
import sys

_prompt = sys.argv[1] if len(sys.argv) > 1 else ""
if "username" in _prompt.lower():
    print(os.environ.get("{_ASKPASS_USER_ENV}", ""))
else:
    print(os.environ.get("{_ASKPASS_TOKEN_ENV}", ""))
'''

_askpass_launcher_lock = threading.Lock()
_askpass_launcher_cache: str | None = None


def _ensure_askpass_launcher() -> str | None:
    """Garante a existência do script GIT_ASKPASS reutilizável e estático.

    O script nunca contém credenciais - apenas invoca o interpretador Python
    atual sobre um helper que lê usuário/token de variáveis de ambiente.
    É criado uma única vez (cacheado em memória e em disco) em vez de um
    arquivo novo por operação Git, evitando tanto o acúmulo de arquivos
    temporários quanto a interpolação insegura de credenciais em texto de
    shell/batch.

    Returns:
        str | None: Caminho do launcher, ou None em caso de falha de I/O.
    """
    global _askpass_launcher_cache
    with _askpass_launcher_lock:
        if _askpass_launcher_cache and Path(_askpass_launcher_cache).exists():
            return _askpass_launcher_cache
        try:
            base = Path(tempfile.gettempdir()) / "codis_askpass"
            base.mkdir(parents=True, exist_ok=True)
            if sys.platform != "win32":
                with contextlib.suppress(OSError):
                    os.chmod(base, stat.S_IRWXU)

            helper_py = base / "askpass_helper.py"
            helper_py.write_text(_ASKPASS_HELPER_SOURCE, encoding="utf-8")
            if sys.platform != "win32":
                os.chmod(helper_py, stat.S_IRUSR | stat.S_IWUSR)

            from src.pyexec import script_command

            parts = script_command(helper_py)
            if sys.platform == "win32":
                launcher = base / "askpass.bat"
                invoke = " ".join(f'"{p}"' for p in parts)
                launcher.write_text(f"@echo off\r\n{invoke} %1\r\n", encoding="utf-8")
            else:
                launcher = base / "askpass.sh"
                invoke = " ".join(shlex.quote(p) for p in parts)
                launcher.write_text(f'#!/bin/sh\nexec {invoke} "$1"\n', encoding="utf-8")
                os.chmod(launcher, stat.S_IRWXU)

            _askpass_launcher_cache = str(launcher)
            return _askpass_launcher_cache
        except OSError as exc:
            logger.warning("Falha ao preparar launcher GIT_ASKPASS: %s", exc)
            return None


_AUTH_SIGNALS = (
    "authentication failed",
    "could not read username",
    "could not read password",
    "permission denied",
    "terminal prompts disabled",
    "invalid credentials",
    "bad credentials",
    "403",
    "401",
    "remote: http basic: access denied",
    "repository not found",
    "fatal: repository",
)


def is_auth_error(stderr: str) -> bool:
    """Retorna ``True`` se a saída do git indica falha de autenticação."""
    low = stderr.lower()
    return any(sig in low for sig in _AUTH_SIGNALS)


class GitAuthManager:
    """Gerencia credenciais Git para operações HTTPS e SSH no Codis.

    Fluxo HTTPS:
    1. Ao executar push/pull/fetch/clone, verifica se há credenciais salvas
       para o host do remote.
    2. Se não houver, injeta GIT_ASKPASS temporário que lê de variáveis de
       ambiente controladas, e exibe diálogo visual para o usuário.
    3. As credenciais podem ser salvas (base64 ofuscado - não criptografia real,
       mas evita credenciais em plaintext imediato no JSON).

    Fluxo SSH:
    - O Codis não armazena chaves SSH. Delega completamente ao ssh-agent
      do sistema. Exibe instruções ao usuário quando detectar falha SSH.

    Args:
        cfg: Instância de ConfigManager para persistência de credenciais.
        root: Janela raiz Tkinter (necessária para diálogos modais).
    """

    _CRED_KEY = "git_credentials"

    def __init__(self, cfg: ConfigManager, root: tk.Tk) -> None:
        """Inicializa o gerenciador de autenticação Git.

        Args:
            cfg: Instância de ConfigManager.
            root: Janela raiz Tkinter.
        """
        self._cfg = cfg
        self._root = root
        self._lock = threading.Lock()

        if not self._cfg.get(self._CRED_KEY):
            self._cfg.set(self._CRED_KEY, {})

    def build_env(self, remote_url: str) -> dict[str, str]:
        """Retorna variáveis de ambiente para injetar credenciais no processo Git.

        Se credenciais estiverem salvas para o host do remote, cria um script
        GIT_ASKPASS temporário que responde automaticamente E desabilita o
        credential helper global para evitar conflito com git config --global.
        Caso contrário, retorna env sem GIT_ASKPASS (Git vai falhar, auth_error
        será detectado).

        Args:
            remote_url: URL do remote Git (ex: https://github.com/user/repo.git).

        Returns:
            dict[str, str]: Dicionário de variáveis de ambiente.
        """
        env = os.environ.copy()

        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_CONFIG_COUNT"] = str(
            int(env.get("GIT_CONFIG_COUNT", "0")) + 2)
        base_idx = int(env.get("GIT_CONFIG_COUNT", "2")) - 2
        env[f"GIT_CONFIG_KEY_{base_idx}"] = "credential.helper"
        env[f"GIT_CONFIG_VALUE_{base_idx}"] = ""
        env[f"GIT_CONFIG_KEY_{base_idx + 1}"] = "core.askPass"
        env[f"GIT_CONFIG_VALUE_{base_idx + 1}"] = ""

        creds = self._load_creds_for_url(remote_url)
        if not creds:
            return env

        user, token = creds

        if not env.get("GIT_AUTHOR_NAME"):
            env["GIT_AUTHOR_NAME"] = user
            env["GIT_COMMITTER_NAME"] = user
        if not env.get("GIT_AUTHOR_EMAIL"):
            env["GIT_AUTHOR_EMAIL"] = f"{user}@users.noreply.github.com"
            env["GIT_COMMITTER_EMAIL"] = f"{user}@users.noreply.github.com"

        askpass_path = _ensure_askpass_launcher()
        if askpass_path:
            env["GIT_ASKPASS"] = askpass_path
            env["GIT_USERNAME"] = user
            env[_ASKPASS_USER_ENV] = user
            env[_ASKPASS_TOKEN_ENV] = token

        return env

    def prompt_and_retry(
        self,
        remote_url: str,
        git_cmd: list[str],
        cwd: str,
        theme: Theme,
        on_done: Callable[[int, str, str], None],
    ) -> None:
        """Exibe diálogo de credenciais e re-executa o comando Git.

        Chamado quando `is_auth_error` retorna True após uma tentativa.
        Exibe o diálogo de login, e se o usuário confirmar, re-executa o
        comando com as novas credenciais.

        Args:
            remote_url: URL do remote Git.
            git_cmd: Comando Git a ser re-executado (lista de strings).
            cwd: Diretório de trabalho para o subprocess.
            theme: Tema visual ativo.
            on_done: Callback(returncode, stdout, stderr) chamado ao terminar.
        """

        def _show():
            dlg = _CredentialDialog(
                self._root,
                theme,
                remote_url,
                existing=self._load_creds_for_url(remote_url),
            )
            self._root.wait_window(dlg.win)
            if not dlg.confirmed:
                on_done(-1, "", _t("git.auth_cancelled",
                        "Autenticação cancelada."))
                return

            user, token, save = dlg.user, dlg.token, dlg.save
            if save:
                self._save_creds(remote_url, user, token)

            env = self.build_env(remote_url)
            if not env.get("GIT_ASKPASS"):
                askpass = _ensure_askpass_launcher()
                if askpass:
                    env["GIT_ASKPASS"] = askpass
                    env["GIT_USERNAME"] = user
            env[_ASKPASS_USER_ENV] = user
            env[_ASKPASS_TOKEN_ENV] = token

            def _run():
                try:
                    r = subprocess.run(
                        git_cmd,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        cwd=cwd,
                        timeout=60,
                        env=env,
                        check=False,
                    )
                    rc, out, err = r.returncode, r.stdout, r.stderr
                except (OSError, FileNotFoundError) as exc:
                    rc, out, err = -1, "", str(exc)
                self._root.after(0, lambda: on_done(rc, out, err))

            threading.Thread(target=_run, daemon=True).start()

        self._root.after(0, _show)

    def show_ssh_help(self, theme: Theme) -> None:
        """Exibe janela de ajuda para configuração de SSH.

        Args:
            theme: Tema visual ativo.
        """
        self._root.after(0, lambda: _SshHelpDialog(self._root, theme))

    def show_host_key_help(self, theme: Theme, host: str = "") -> None:
        """Exibe janela de ajuda para falha de verificação de host key SSH.

        Chamado quando o Git recusa a conexão porque o host remoto (ex.:
        github.com) ainda não está em ``~/.ssh/known_hosts``.

        Args:
            theme: Tema visual ativo.
            host: Nome do host remoto, quando detectável.
        """
        self._root.after(
            0, lambda: _SshHelpDialog(
                self._root, theme, kind="host_key", host=host)
        )

    def forget(self, remote_url: str) -> None:
        """Remove credenciais salvas para o host do remote.

        Args:
            remote_url: URL do remote cujas credenciais devem ser removidas.
        """
        host = _host_from_url(remote_url)
        with self._lock:
            creds: dict = self._cfg.get(self._CRED_KEY) or {}
            if host in creds:
                del creds[host]
                self._cfg.set(self._CRED_KEY, creds)
                self._cfg.save()

    def _load_creds_for_url(self, url: str) -> tuple[str, str] | None:
        """Carrega credenciais salvas para o host da URL.

        Args:
            url: URL do remote Git.

        Returns:
            tuple[str, str] | None: (usuário, token) ou None.
        """
        host = _host_from_url(url)
        with self._lock:
            creds: dict = self._cfg.get(self._CRED_KEY) or {}
            entry = creds.get(host)
            if not entry:
                return None
            try:
                user = _decode(entry.get("u", ""))
                token = _decode(entry.get("t", ""))
                return (user, token) if user and token else None
            except (ValueError, KeyError, UnicodeDecodeError):
                return None

    def _save_creds(self, url: str, user: str, token: str) -> None:
        """Salva credenciais ofuscadas para o host da URL.

        Args:
            url: URL do remote Git.
            user: Nome de usuário.
            token: Senha ou token de acesso.
        """
        host = _host_from_url(url)
        with self._lock:
            creds: dict = dict(self._cfg.get(self._CRED_KEY) or {})
            creds[host] = {"u": _encode(user), "t": _encode(token)}
            self._cfg.set(self._CRED_KEY, creds)
            self._cfg.save()


def _encode(value: str) -> str:
    """Codifica string em base64 para armazenamento ofuscado.

    Args:
        value: Texto a ser ofuscado.

    Returns:
        str: String codificada em base64.
    """
    return base64.b64encode(value.encode()).decode()


def _decode(value: str) -> str:
    """Decodifica string de base64.

    Args:
        value: String codificada em base64.

    Returns:
        str: Texto decodificado.
    """
    return base64.b64decode(value.encode()).decode()


def _host_from_url(url: str) -> str:
    """Extrai o host de uma URL Git para uso como chave de credencial.

    Suporta HTTPS e SSH (git@host:user/repo).

    Args:
        url: URL do remote Git.

    Returns:
        str: Host normalizado (ex: 'github.com').
    """
    url = url.strip()

    if url.startswith("git@"):
        try:
            return url.split("@", 1)[1].split(":")[0].lower()
        except IndexError:
            return url

    try:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        return (parsed.hostname or url).lower()
    except (ValueError, AttributeError):
        return url


class _CredentialDialog:
    """Diálogo modal de credenciais Git dentro do Codis.

    Exibe campos para usuário e token/senha com opção de salvar.

    Args:
        parent: Widget pai Tkinter.
        theme: Tema visual ativo.
        remote_url: URL do remote para exibir ao usuário.
        existing: Tupla (user, token) pré-preenchida, se disponível.
    """

    _W, _H = 460, 310

    def __init__(
        self,
        parent: tk.Widget,
        theme: Theme,
        remote_url: str,
        existing: tuple[str, str] | None = None,
    ) -> None:
        """Cria o diálogo de credenciais Git.

        Args:
            parent: Widget pai.
            theme: Tema visual.
            remote_url: URL do remote Git.
            existing: Credenciais pré-existentes (user, token).
        """
        self.confirmed = False
        self.user = ""
        self.token = ""
        self.save = False

        t = theme
        self.win = tk.Toplevel(parent)
        self.win.title(_t("git.auth_title", "Autenticação Git"))
        self.win.resizable(False, False)
        self.win.configure(bg=t.floating_bg)
        safe_grab_set(self.win)
        self.win.attributes("-topmost", True)

        parent.update_idletasks()
        px = parent.winfo_rootx()
        py = parent.winfo_rooty()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        x = px + (pw - self._W) // 2
        y = py + (ph - self._H) // 2
        self.win.geometry(f"{self._W}x{self._H}+{x}+{y}")

        outer = tk.Frame(self.win, bg=t.border, padx=1, pady=1)
        outer.pack(fill="both", expand=True)
        inner = tk.Frame(outer, bg=t.floating_bg, padx=20, pady=16)
        inner.pack(fill="both", expand=True)

        hdr = tk.Frame(inner, bg=t.floating_bg)
        hdr.pack(fill="x", pady=(0, 8))
        tk.Label(hdr, text="🔑", bg=t.floating_bg, font=(FONT_UI, 18)).pack(
            side="left", padx=(0, 8)
        )
        tf = tk.Frame(hdr, bg=t.floating_bg)
        tf.pack(side="left", fill="x", expand=True)
        tk.Label(
            tf,
            text=_t("git.auth_title", "Autenticação Git"),
            bg=t.floating_bg,
            fg=t.fg,
            font=(FONT_UI, 11, "bold"),
            anchor="w",
        ).pack(fill="x")

        host = _host_from_url(remote_url)
        tk.Label(
            tf,
            text=host,
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            anchor="w",
        ).pack(fill="x")

        note_bg = t.warn_fg if hasattr(t, "warn_fg") else "#f59e0b"
        tk.Label(
            inner,
            text=_t(
                "git.auth_token_hint",
                "⚠  Use um Token de Acesso Pessoal (PAT) como senha, não sua senha real.",
            ),
            bg=t.floating_bg,
            fg=note_bg,
            font=(FONT_UI, 8),
            anchor="w",
            wraplength=400,
            justify="left",
        ).pack(fill="x", pady=(0, 8))

        tk.Label(
            inner,
            text=_t("git.auth_user", "Usuário:"),
            bg=t.floating_bg,
            fg=t.fg,
            font=(FONT_UI, 9),
            anchor="w",
        ).pack(fill="x")
        self._user_var = tk.StringVar(value=existing[0] if existing else "")
        user_entry = tk.Entry(
            inner,
            textvariable=self._user_var,
            bg=t.input_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=t.border,
            font=(FONT_MONO, 10),
        )
        user_entry.pack(fill="x", pady=(2, 8))

        tk.Label(
            inner,
            text=_t("git.auth_token", "Token / Senha:"),
            bg=t.floating_bg,
            fg=t.fg,
            font=(FONT_UI, 9),
            anchor="w",
        ).pack(fill="x")
        self._token_var = tk.StringVar()
        self._tok_entry = tk.Entry(
            inner,
            textvariable=self._token_var,
            show="•",
            bg=t.input_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=t.border,
            font=(FONT_MONO, 10),
        )
        self._tok_entry.pack(fill="x", pady=(2, 4))

        self._show_token = False
        show_lbl = tk.Label(
            inner,
            text=_t("git.auth_show_token", "👁 Mostrar token"),
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            anchor="w",
            cursor="hand2",
        )
        show_lbl.pack(fill="x")
        show_lbl.bind("<Button-1>", lambda _: self._toggle_show(show_lbl))

        self._save_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            inner,
            text=_t("git.auth_save", "Lembrar credenciais para este host"),
            variable=self._save_var,
            bg=t.floating_bg,
            fg=t.fg,
            selectcolor=t.input_bg,
            activebackground=t.floating_bg,
            font=(FONT_UI, 9),
        ).pack(anchor="w", pady=(6, 0))

        btn_row = tk.Frame(inner, bg=t.floating_bg)
        btn_row.pack(fill="x", pady=(12, 0))

        cancel_btn = tk.Button(
            btn_row,
            text=_t("ui.cancel", "Cancelar"),
            bg=t.btn_bg,
            fg=t.btn_fg,
            activebackground=t.btn_hover,
            relief="flat",
            padx=14,
            pady=4,
            font=(FONT_UI, 9),
            command=self.win.destroy,
        )
        cancel_btn.pack(side="right", padx=(4, 0))

        ok_btn = tk.Button(
            btn_row,
            text=_t("git.auth_login", "Entrar"),
            bg=t.accent,
            fg="#000",
            activebackground=t.accent,
            relief="flat",
            padx=14,
            pady=4,
            font=(FONT_UI, 9, "bold"),
            command=self._confirm,
        )
        ok_btn.pack(side="right")

        if existing:
            self._tok_entry.focus_set()
        else:
            user_entry.focus_set()

        self.win.bind("<Return>", lambda _: self._confirm())
        self.win.bind("<Escape>", lambda _: self.win.destroy())

    def _toggle_show(self, label: tk.Label) -> None:
        """Alterna visibilidade do token.

        Args:
            label: Label de toggle para atualizar texto.
        """
        self._show_token = not self._show_token
        self._tok_entry.config(show="" if self._show_token else "•")
        label.config(
            text=(
                _t("git.auth_hide_token", "🙈 Ocultar token")
                if self._show_token
                else _t("git.auth_show_token", "👁 Mostrar token")
            )
        )

    def _confirm(self) -> None:
        """Valida e confirma as credenciais inseridas."""
        user = self._user_var.get().strip()
        token = self._token_var.get().strip()
        if not user or not token:
            return
        self.confirmed = True
        self.user = user
        self.token = token
        self.save = self._save_var.get()
        self.win.destroy()


class _SshHelpDialog:
    """Exibe instruções de configuração SSH para o usuário.

    Args:
        parent: Widget pai Tkinter.
        theme: Tema visual ativo.
    """

    _W, _H = 500, 360

    def __init__(
        self, parent: tk.Widget, theme: Theme, kind: str = "ssh_setup", host: str = ""
    ) -> None:
        """Cria o diálogo de ajuda SSH.

        Args:
            parent: Widget pai.
            theme: Tema visual.
            kind: ``"ssh_setup"`` para o guia padrão de configuração de chaves,
                ou ``"host_key"`` para o caso de falha de verificação de host key.
            host: Nome do host remoto (usado apenas quando ``kind="host_key"``).
        """
        t = theme
        win = tk.Toplevel(parent)
        title_key = (
            "git.host_key_help_title" if kind == "host_key" else "git.ssh_help_title"
        )
        title_default = (
            "Verificação de host SSH" if kind == "host_key" else "Configuração SSH"
        )
        win.title(_t(title_key, title_default))
        win.resizable(False, False)
        win.configure(bg=t.floating_bg)
        safe_grab_set(win)
        win.attributes("-topmost", True)

        parent.update_idletasks()
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        win.geometry(
            f"{self._W}x{self._H}+{px + (pw - self._W) // 2}+{py + (ph - self._H) // 2}"
        )

        outer = tk.Frame(win, bg=t.border, padx=1, pady=1)
        outer.pack(fill="both", expand=True)
        inner = tk.Frame(outer, bg=t.floating_bg, padx=20, pady=16)
        inner.pack(fill="both", expand=True)

        tk.Label(
            inner,
            text="🔐 " + _t(title_key, title_default),
            bg=t.floating_bg,
            fg=t.fg,
            font=(FONT_UI, 11, "bold"),
            anchor="w",
        ).pack(fill="x", pady=(0, 8))

        if kind == "host_key":
            host_display = host or "o host remoto"
            msg = _t(
                "git.host_key_help_body",
                "O Git recusou a conexão porque a identidade de\n"
                f"{host_display} ainda não está em ~/.ssh/known_hosts.\n\n"
                "Isso é normal na primeira vez que você se conecta a um\n"
                "host via SSH — não significa necessariamente um problema.\n\n"
                "Para resolver:\n\n"
                "1. Verifique a identidade do host manualmente:\n"
                f"   ssh -T git@{host or 'HOST'}\n\n"
                "   e responda 'yes' quando perguntado se confia na chave.\n\n"
                "2. Ou adicione o host automaticamente ao known_hosts:\n"
                f"   ssh-keyscan {host or 'HOST'} >> ~/.ssh/known_hosts\n\n"
                "Após aceitar a chave do host uma vez, as operações Git via\n"
                "SSH para este host funcionarão normalmente no Codis.",
            )
        else:
            msg = _t(
                "git.ssh_help_body",
                "O Codis não gerencia chaves SSH diretamente.\n\n"
                "Para usar SSH com Git:\n\n"
                "1. Gere um par de chaves (se ainda não tiver):\n"
                '   ssh-keygen -t ed25519 -C "seu@email.com"\n\n'
                "2. Adicione a chave pública (~/.ssh/id_ed25519.pub) no\n"
                "   GitHub / GitLab / Bitbucket → Settings → SSH Keys\n\n"
                "3. Inicie o ssh-agent e adicione sua chave:\n"
                "   eval $(ssh-agent -s)\n"
                "   ssh-add ~/.ssh/id_ed25519\n\n"
                "4. Teste a conexão:\n"
                "   ssh -T git@github.com\n\n"
                "Após configurar, as operações Git via SSH funcionarão\n"
                "automaticamente no Codis.",
            )

        txt = tk.Text(
            inner,
            bg=t.input_bg,
            fg=t.fg,
            font=(FONT_MONO, 9),
            relief="flat",
            bd=0,
            highlightthickness=0,
            wrap="word",
            padx=8,
            pady=8,
            state="normal",
        )
        txt.insert("1.0", msg)
        txt.config(state="disabled")
        txt.pack(fill="both", expand=True, pady=(0, 12))

        tk.Button(
            inner,
            text=_t("ui.close", "Fechar"),
            bg=t.btn_bg,
            fg=t.btn_fg,
            activebackground=t.btn_hover,
            relief="flat",
            padx=14,
            pady=4,
            font=(FONT_UI, 9),
            command=win.destroy,
        ).pack(side="right")
