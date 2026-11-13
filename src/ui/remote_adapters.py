"""Codis - adaptadores de acesso remoto (SSH/SFTP, FTP/FTPS, HTTP, WebDAV).

Camada sem Tkinter usada pelo Navegador Remoto. Cada protocolo implementa
:class:`RemoteAdapter` com a mesma interface, o que permite testar tudo contra
servidores reais (sshd, pyftpdlib, wsgidav) sem abrir janela.

Convenções:
* caminhos remotos são sempre POSIX (``/``), mesmo quando o Codis roda no
  Windows - por isso usamos ``posixpath`` e nunca ``pathlib.Path`` para eles;
* operações devolvem ``(ok, mensagem)``; ``listdir`` devolve
  ``(entradas, erro)`` e o erro NUNCA é engolido (pasta vazia e pasta ilegível
  são coisas diferentes);
* os adaptadores são thread-safe: o painel os usa a partir de threads.
"""

from __future__ import annotations

import base64
import contextlib
import ftplib
import html.parser
import logging
import os
import posixpath
import re
import shlex
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from src.constants import CONFIG_DIR
from src.i18n import _t
from src.ui._persistence_utils import load_dataclass_list, save_dataclass_list

logger = logging.getLogger("Codis.RemoteAdapters")

try:
    from defusedxml import ElementTree as _SafeET  # type: ignore[import-untyped]

    _XML_FROMSTRING = _SafeET.fromstring
except ImportError:  # pragma: no cover
    _XML_FROMSTRING = ET.fromstring

ProgressCb = Callable[[int, "int | None"], None]
Result = tuple[bool, str]

PROFILES_FILE = CONFIG_DIR / "remote_browser_profiles.json"
MAX_LISTING_BYTES = 8 * 1024 * 1024
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
_NULL_FILE = "NUL" if sys.platform == "win32" else "/dev/null"

_TEXT_EXTS: frozenset[str] = frozenset(
    ".py .pyw .pyi .js .mjs .cjs .jsx .ts .tsx .html .htm .vue .svelte .css .scss "
    ".less .json .jsonc .json5 .md .mdx .markdown .yml .yaml .xml .svg .xhtml .sql "
    ".pgsql .toml .ini .cfg .env .conf .txt .log .sh .bash .zsh .fish .ksh .csh "
    ".tcsh .ps1 .psm1 .bat .cmd .rs .go .c .h .cpp .cc .cxx .hpp .java .swift .kt "
    ".rb .erb .php .pl .pm .r .jl .hs .lua .ex .exs .erl .dart .scala .vb .cs .fs "
    ".asm .s .tf .proto .graphql .mk .gradle .properties .lock .service .rst .tex "
    ".gitignore .gitattributes .editorconfig .dockerignore .npmrc .htaccess".split()
)
_TEXT_NAMES: frozenset[str] = frozenset(
    "makefile dockerfile gemfile rakefile procfile vagrantfile jenkinsfile readme "
    "license licence changelog authors contributors notice codeowners cmakelists.txt "
    "requirements.txt .bashrc .zshrc .profile .bash_profile .vimrc .gitconfig .env".split()
)
_IMAGE_EXTS: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico", ".tiff"}
)
_BINARY_EXTS: frozenset[str] = frozenset(
    ".zip .tar .gz .tgz .bz2 .xz .7z .rar .exe .dll .so .dylib .bin .o .a .class "
    ".jar .pyc .pdf .doc .docx .xls .xlsx .ppt .pptx .mp3 .mp4 .mkv .avi .mov .wav "
    ".flac .ogg .iso .img .dmg .woff .woff2 .ttf .otf .sqlite .db".split()
)
_SPECIAL_EXTS = {".ipynb": "notebook", ".csv": "csv", ".tsv": "csv"}


def classify(name: str) -> str:
    """Classifica pelo nome: ``text``, ``image``, ``notebook``, ``csv``,
    ``binary`` (conhecido) ou ``unknown`` (decidir após inspecionar o conteúdo).
    """
    base = posixpath.basename(name).lower()
    suffix = posixpath.splitext(base)[1]
    if base in _TEXT_NAMES or base in _TEXT_EXTS or suffix in _TEXT_EXTS:
        return "text"
    if suffix in _IMAGE_EXTS:
        return "image"
    if suffix in _SPECIAL_EXTS:
        return _SPECIAL_EXTS[suffix]
    if suffix in _BINARY_EXTS:
        return "binary"
    return "unknown"


def looks_binary(path: Path, sample: int = 8192) -> bool:
    """``True`` se o início do arquivo contém byte nulo (heurística clássica)."""
    try:
        with open(path, "rb") as fh:
            return b"\x00" in fh.read(sample)
    except OSError:
        return True


def file_icon(name: str, is_dir: bool = False) -> str:
    """Ícone (emoji) para a lista de arquivos."""
    if is_dir:
        return "📁"
    suffix = posixpath.splitext(name.lower())[1]
    table = {
        "🖼": _IMAGE_EXTS,
        "🐍": {".py", ".pyw", ".pyi"},
        "📜": {".js", ".mjs", ".ts", ".jsx", ".tsx"},
        "⚙": {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".env"},
        "📝": {".md", ".mdx", ".txt", ".rst"},
        "🌐": {".html", ".htm", ".vue", ".css", ".scss"},
        "🗄": {".sql", ".db", ".sqlite"},
        "📊": {".csv", ".tsv", ".xlsx", ".xls"},
        "📓": {".ipynb"},
        "📦": {".zip", ".tar", ".gz", ".tgz", ".7z", ".bz2", ".xz", ".rar"},
        "▶": {".sh", ".bash", ".zsh", ".bat", ".cmd", ".ps1", ".exe"},
    }
    for icon, exts in table.items():
        if suffix in exts:
            return icon
    return "📄"


def format_size(size: int | None) -> str:
    """Tamanho legível (``1.4 MB``); vazio quando desconhecido."""
    if size is None:
        return ""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return ""


def parse_human_size(text: str) -> int | None:
    """Converte ``1.2K``/``340``/``-`` em bytes (``None`` se não for tamanho)."""
    text = text.strip()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([KMGT]?)i?B?", text, re.IGNORECASE)
    if not m:
        return None
    mult = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
    return int(float(m.group(1)) * mult[m.group(2).upper()])


class Protocol(StrEnum):
    SSH = "ssh"
    SFTP = "sftp"
    FTP = "ftp"
    FTPS = "ftps"
    HTTP = "http"
    HTTPS = "https"
    WEBDAV = "webdav"
    WEBDAVS = "webdavs"

    @classmethod
    def _missing_(cls, value: object) -> Protocol:
        return cls.SSH

PROTO_DEFAULTS: dict[str, int] = {
    "ssh": 22,
    "sftp": 22,
    "ftp": 21,
    "ftps": 21,
    "http": 80,
    "https": 443,
    "webdav": 80,
    "webdavs": 443,
}

_UNSAFE_TARGET_CHARS = re.compile(r"[\s\x00-\x1f\x7f]")


@dataclass
class RemoteProfile:
    name: str
    protocol: str = "ssh"
    host: str = ""
    port: int = 22
    user: str = ""
    password: str = ""
    key_path: str = ""
    remote_root: str = "~"
    passive_ftp: bool = True
    jump_host: str = ""
    timeout: int = 15
    tags: list[str] = field(default_factory=list)
    verify_tls: bool = True
    uid: str = field(default_factory=lambda: uuid4().hex[:10])

    def __post_init__(self) -> None:
        """Recusa host/usuário/jump que o ssh interpretaria como opção.

        Perfis podem vir de arquivos importados; ``-oProxyCommand=...`` viraria
        execução de comando ao ser passado ao ssh.

        Raises:
            ValueError: se algum campo for inseguro.
        """
        for label, value in (
            ("host", self.host),
            ("user", self.user),
            ("jump_host", self.jump_host),
        ):
            if value.startswith("-") or _UNSAFE_TARGET_CHARS.search(value):
                raise ValueError(f"Valor inválido em '{label}': {value!r}")

    @property
    def proto(self) -> Protocol:
        return Protocol(self.protocol)

    def decoded_password(self) -> str:
        if not self.password:
            return ""
        try:
            return base64.b64decode(self.password.encode(), validate=True).decode()
        except (ValueError, UnicodeDecodeError):
            return self.password

    @staticmethod
    def encode_password(raw: str) -> str:
        return base64.b64encode(raw.encode()).decode() if raw else ""


def load_profiles() -> list[RemoteProfile]:
    return load_dataclass_list(PROFILES_FILE, RemoteProfile, logger=logger)


def save_profiles(profiles: list[RemoteProfile]) -> None:
    save_dataclass_list(PROFILES_FILE, profiles, chmod_600=True)

_URL_SCHEMES = {"dav": "webdav", "davs": "webdavs", "sftp": "sftp", "ssh": "ssh"}


def parse_remote_url(url: str) -> dict[str, object] | None:
    """Decompõe ``sftp://user:senha@host:22/caminho`` em campos de perfil.

    Retorna ``None`` se não for uma URL de protocolo suportado.
    """
    parsed = urllib.parse.urlparse(url.strip())
    scheme = _URL_SCHEMES.get(parsed.scheme.lower(), parsed.scheme.lower())
    if scheme not in PROTO_DEFAULTS or not parsed.hostname:
        return None
    path = urllib.parse.unquote(parsed.path) or "~"
    if scheme in ("ssh", "sftp") and path.startswith("/~"):
        path = path[1:]
    return {
        "protocol": scheme,
        "host": parsed.hostname,
        "port": parsed.port or PROTO_DEFAULTS[scheme],
        "user": urllib.parse.unquote(parsed.username or ""),
        "password": urllib.parse.unquote(parsed.password or ""),
        "remote_root": path,
    }


@dataclass
class RemoteEntry:
    name: str
    is_dir: bool
    size: int | None = None
    modified: str = ""
    is_link: bool = False
    link_target: str = ""
    broken: bool = False
    mode: str = ""

    @property
    def size_text(self) -> str:
        return "" if self.is_dir else format_size(self.size)


def join_path(base: str, name: str) -> str:
    """Junta caminho remoto POSIX sem duplicar barras."""
    return posixpath.join(base or "/", name)


def parent_path(path: str) -> str:
    """Pai de um caminho remoto POSIX (``/`` é o próprio pai)."""
    if path in ("", "/", "~"):
        return path or "/"
    return posixpath.dirname(path.rstrip("/")) or "/"


def _temp_part(local: Path) -> Path:
    return local.with_name(local.name + ".codis-part")


class RemoteAdapter:
    """Contrato comum a todos os protocolos."""

    read_only = False

    def __init__(self, profile: RemoteProfile) -> None:
        self._p = profile
        self.home = "/"

    def connect(self) -> Result:
        raise NotImplementedError

    def listdir(self, path: str) -> tuple[list[RemoteEntry], str]:
        raise NotImplementedError

    def download(
        self, remote: str, local: Path, progress: ProgressCb | None = None
    ) -> Result:
        raise NotImplementedError

    def upload(
        self, local: Path, remote: str, progress: ProgressCb | None = None
    ) -> Result:
        raise NotImplementedError

    def mkdir(self, path: str) -> Result:
        return False, _t("rbrowser.op_unsupported", "Operação não suportada por este protocolo.")

    def remove(self, path: str, is_dir: bool = False) -> Result:
        return False, _t("rbrowser.op_unsupported", "Operação não suportada por este protocolo.")

    def rename(self, src: str, dst: str) -> Result:
        return False, _t("rbrowser.op_unsupported", "Operação não suportada por este protocolo.")

    def stat(self, path: str) -> RemoteEntry | None:
        """Metadados de ``path`` (via listagem do diretório pai)."""
        entries, err = self.listdir(parent_path(path))
        if err:
            return None
        name = posixpath.basename(path.rstrip("/"))
        return next((e for e in entries if e.name == name), None)

    def close(self) -> None:
        return None


def _make_ssl_context(verify: bool) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if not verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx

_LS_RE = re.compile(
    r"^(?P<mode>[-dlbcpsDC?][rwxsStTl-]{9}[+.@]?)\s+\d+\s+\S+\s+\S+\s+"
    r"(?P<size>\d+(?:,\s*\d+)?)\s+"
    r"(?P<date>\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2}(?::\d{2}(?:\.\d+)?(?:\s+[+-]\d{4})?)?)?"
    r"|[A-Za-z]{3}\s+\d{1,2}\s+(?:\d{2}:\d{2}|\d{4}))\s+"
    r"(?P<name>.+)$"
)


def parse_ls_output(text: str) -> list[RemoteEntry]:
    """Interpreta a saída de ``ls -la`` (GNU long-iso, GNU padrão, BSD, BusyBox).

    Independe do estilo de data: ancora no tamanho e reconhece a data pelos
    dois formatos possíveis, de modo que o nome (com espaços) é o resto da linha.
    """
    entries: list[RemoteEntry] = []
    for line in text.splitlines():
        m = _LS_RE.match(line.rstrip("\r"))
        if not m:
            continue
        name = m["name"]
        mode = m["mode"]
        is_link = mode[0] == "l"
        target = ""
        if is_link and " -> " in name:
            name, _, target = name.partition(" -> ")
        if name in (".", ".."):
            continue
        size_txt = m["size"]
        size = None if "," in size_txt else int(size_txt)
        entries.append(
            RemoteEntry(
                name=name,
                is_dir=mode[0] == "d",
                size=size,
                modified=re.sub(r"\s+", " ", m["date"]),
                is_link=is_link,
                link_target=target,
                mode=mode,
            )
        )
    return entries


def _find_binary(name: str) -> str | None:
    found = shutil.which(name)
    if found or sys.platform != "win32":
        return found
    for base in (
        Path(r"C:\Windows\System32\OpenSSH"),
        Path(r"C:\Program Files\Git\usr\bin"),
        Path(r"C:\Program Files\OpenSSH"),
    ):
        candidate = base / f"{name}.exe"
        if candidate.exists():
            return str(candidate)
    return None


class SSHAdapter(RemoteAdapter):
    """SSH/SFTP usando os binários ``ssh``/``sftp``/``scp`` do sistema.

    * Autenticação: chave > senha (``sshpass`` ou ``SSH_ASKPASS``, funcionando
      também no Windows) > ssh-agent. A senha NUNCA vai em argumento de processo.
    * Em sistemas POSIX reutiliza uma conexão (ControlMaster): uma única
      autenticação para listar, baixar e enviar.
    """

    def __init__(self, profile: RemoteProfile) -> None:
        super().__init__(profile)
        self._ssh = _find_binary("ssh")
        self._sftp = _find_binary("sftp")
        self._scp = _find_binary("scp")
        self._sshpass = None if sys.platform == "win32" else shutil.which("sshpass")
        self._mux = sys.platform != "win32" and not os.environ.get("CODIS_SSH_NOMUX")
        self._ctl_dir: str | None = None
        self._askpass_dir: str | None = None
        self._lock = threading.Lock()
        self.home = "~"

    def _password(self) -> str:
        return self._p.decoded_password()

    def _auth(self) -> str:
        p = self._p
        if p.key_path and Path(p.key_path).expanduser().exists():
            return "key"
        if self._password():
            return "sshpass" if self._sshpass else "askpass"
        if os.environ.get("SSH_AUTH_SOCK") or os.environ.get("SSH_AGENT_PID"):
            return "agent"
        return "none"

    def _uses_askpass(self) -> bool:
        auth = self._auth()
        return auth == "askpass" or (auth == "key" and bool(self._password()))

    def _ensure_askpass(self) -> str:
        with self._lock:
            if self._askpass_dir is None:
                self._askpass_dir = tempfile.mkdtemp(prefix="codis-ap-")
                if sys.platform == "win32":
                    script = Path(self._askpass_dir) / "askpass.cmd"
                    script.write_text(
                        "@powershell -NoProfile -NonInteractive -Command "
                        '"[Console]::Out.Write($env:CODIS_SSH_PW)"\r\n',
                        encoding="ascii",
                    )
                else:
                    script = Path(self._askpass_dir) / "askpass.sh"
                    script.write_text('#!/bin/sh\nprintf \'%s\\n\' "$CODIS_SSH_PW"\n')
                    script.chmod(0o700)
            return str(next(Path(self._askpass_dir).iterdir()))

    def _control_path(self) -> str | None:
        if not self._mux:
            return None
        with self._lock:
            if self._ctl_dir is None:
                self._ctl_dir = tempfile.mkdtemp(prefix="cx")
            return os.path.join(self._ctl_dir, "%C")

    def _target(self) -> str:
        p = self._p
        return f"{p.user}@{p.host}" if p.user else p.host

    def _common_opts(self, *, mux: bool = True) -> list[str]:
        p, auth = self._p, self._auth()
        opts = [
            "-o", f"ConnectTimeout={p.timeout}",
            "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=3",
            "-o", "LogLevel=ERROR",
        ]  # fmt: skip
        is_local = p.host.lower() in _LOCAL_HOSTS
        opts += ["-o", f"StrictHostKeyChecking={'no' if is_local else 'accept-new'}"]
        if is_local:
            opts += ["-o", f"UserKnownHostsFile={_NULL_FILE}"]
        if auth == "key":
            opts += [
                "-i", str(Path(p.key_path).expanduser()),
                "-o", "IdentitiesOnly=yes",
                "-o", "PreferredAuthentications=publickey",
            ]  # fmt: skip
            if not self._password():
                opts += ["-o", "BatchMode=yes"]
        elif auth in ("sshpass", "askpass"):
            opts += [
                "-o", "IdentitiesOnly=yes",
                "-o", f"IdentityFile={_NULL_FILE}",
                "-o", "PreferredAuthentications=password,keyboard-interactive",
                "-o", "NumberOfPasswordPrompts=1",
            ]  # fmt: skip
        else:
            opts += ["-o", "BatchMode=yes"]
            if auth == "agent":
                opts += ["-o", "PreferredAuthentications=publickey"]
        if p.jump_host:
            opts += ["-o", f"ProxyJump={p.jump_host}"]
        ctl = self._control_path() if mux else None
        if ctl:
            opts += [
                "-o", "ControlMaster=auto",
                "-o", f"ControlPath={ctl}",
                "-o", "ControlPersist=120",
            ]  # fmt: skip
        return opts

    def _argv(self, tool: str, extra: list[str] | None = None, *, mux: bool = True) -> list[str]:
        binary = {"ssh": self._ssh, "sftp": self._sftp, "scp": self._scp}[tool]
        port_flag = "-p" if tool == "ssh" else "-P"
        argv = [binary or tool]
        if self._p.port != 22:
            argv += [port_flag, str(self._p.port)]
        argv += self._common_opts(mux=mux) + (extra or [])
        return argv

    def _wrap(self, argv: list[str]) -> list[str]:
        if self._auth() == "sshpass" and self._sshpass:
            return [self._sshpass, "-e", *argv]
        return argv

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        if self._auth() == "sshpass":
            env["SSHPASS"] = self._password()
        elif self._uses_askpass():
            env["SSH_ASKPASS"] = self._ensure_askpass()
            env["SSH_ASKPASS_REQUIRE"] = "force"
            env["CODIS_SSH_PW"] = self._password()
            env.setdefault("DISPLAY", ":0")
        return env

    def _popen_kwargs(self) -> dict[str, object]:
        kw: dict[str, object] = {"env": self._env()}
        if self._uses_askpass() and sys.platform != "win32":
            kw["start_new_session"] = True
        return kw

    def _exec(
        self, argv: list[str], *, timeout: float | None, stdin_text: str | None = None
    ) -> tuple[int, str, str]:
        try:
            r = subprocess.run(
                self._wrap(argv),
                capture_output=True,
                text=True,
                input=stdin_text,
                stdin=None if stdin_text is not None else subprocess.DEVNULL,
                timeout=timeout,
                check=False,
                **self._popen_kwargs(),
            )
            return r.returncode, r.stdout, r.stderr
        except subprocess.TimeoutExpired:
            return -1, "", _t("rbrowser.timeout", "Timeout na conexão.")
        except (OSError, subprocess.SubprocessError) as exc:
            return -1, "", str(exc)

    def _run(self, remote_cmd: str, timeout: float = 30) -> tuple[int, str, str]:
        if not self._ssh:
            return -1, "", self._no_ssh_msg()
        argv = self._argv("ssh") + [self._target(), remote_cmd]
        return self._exec(argv, timeout=timeout)

    @staticmethod
    def _no_ssh_msg() -> str:
        return _t(
            "rbrowser.no_ssh",
            "Binário 'ssh' não encontrado. No Windows: Configurações → Recursos "
            "opcionais → Cliente OpenSSH.",
        )

    @staticmethod
    def _quote_remote_path(path: str) -> str:
        """Escapa um caminho para shell POSIX preservando a expansão de ``~``."""
        if not path:
            return ""
        if path == "~":
            return "~"
        if path.startswith("~/"):
            return "~/" + shlex.quote(path[2:])
        return shlex.quote(path)

    def _abs(self, path: str) -> str:
        if not path or path == "~":
            return self.home
        if path.startswith("~/") and self.home != "~":
            return posixpath.join(self.home, path[2:])
        if path.startswith("/"):
            return posixpath.normpath(path) if path != "/" else "/"
        return posixpath.join(self.home, path)

    def connect(self) -> Result:
        if not self._ssh:
            return False, self._no_ssh_msg()
        root = self._p.remote_root or "~"
        cmd = (
            "printf '__codis_ok__\\n'; "
            f"cd {self._quote_remote_path(root)} 2>/dev/null && pwd || pwd"
        )
        rc, out, err = self._run(cmd, timeout=self._p.timeout + 10)
        lines = [ln.strip() for ln in out.splitlines()]
        if rc == 0 and "__codis_ok__" in lines:
            rest = lines[lines.index("__codis_ok__") + 1 :]
            if rest and rest[0].startswith("/"):
                self.home = rest[0]
            return True, _t("rbrowser.connected", "Conectado.")
        return False, self.classify_error(err or out)

    @staticmethod
    def classify_error(err: str) -> str:
        e = err.lower()
        rules = (
            (("host key verification failed", "remote host identification has changed"),
             "rbrowser.err_hostkey",
             "A chave do servidor mudou ou é desconhecida. Remova a entrada antiga de known_hosts."),
            (("connection refused",), "rbrowser.err_refused", "Conexão recusada - o servidor SSH está em execução?"),
            (("permission denied", "authentication failed", "too many authentication"),
             "rbrowser.err_perm", "Permissão negada - verifique usuário, senha ou chave."),
            (("no route to host", "network is unreachable", "unreachable"),
             "rbrowser.err_route", "Host inacessível."),
            (("timed out", "timeout"), "rbrowser.timeout", "Timeout na conexão."),
            (("could not resolve", "name or service not known", "nodename nor servname"),
             "rbrowser.err_dns", "Host não encontrado."),
            (("no such file or directory", "not found", "no such file"),
             "rbrowser.err_nofile", "Arquivo ou pasta não encontrado."),
            (("not a directory",), "rbrowser.err_notdir", "O caminho não é uma pasta."),
        )  # fmt: skip
        for needles, key, default in rules:
            if any(n in e for n in needles):
                return _t(key, default)
        return err.strip() or _t("rbrowser.err_generic", "Falha na conexão.")

    def listdir(self, path: str) -> tuple[list[RemoteEntry], str]:
        target = self._quote_remote_path(self._abs(path))
        out, err, rc = "", "", 0
        for opts in ("ls -la --time-style=long-iso", "ls -la"):
            rc, out, err = self._run(f"LC_ALL=C {opts} {target}", timeout=30)
            if rc == 0 or not re.search(r"unrecognized option|illegal option|invalid option", err):
                break
        entries = parse_ls_output(out)
        if rc != 0 and not entries:
            return [], self.classify_error(err) if err else f"rc={rc}"
        self._resolve_links(self._abs(path), entries)
        return entries, ""

    def _resolve_links(self, directory: str, entries: list[RemoteEntry]) -> None:
        """Descobre, com uma única chamada, quais symlinks apontam para pastas."""
        links = [e for e in entries if e.is_link]
        if not links:
            return
        names = " ".join(shlex.quote(e.name) for e in links)
        script = (
            f"cd {shlex.quote(directory)} 2>/dev/null && for n in {names}; do "
            'if [ -d "$n" ]; then printf "D\\t%s\\n" "$n"; '
            'elif [ ! -e "$n" ]; then printf "B\\t%s\\n" "$n"; fi; done'
        )
        rc, out, _ = self._run(script, timeout=20)
        if rc != 0:
            return
        kinds = {}
        for line in out.splitlines():
            kind, _, name = line.partition("\t")
            kinds[name] = kind
        for e in links:
            e.is_dir = kinds.get(e.name) == "D"
            e.broken = kinds.get(e.name) == "B"

    @staticmethod
    def _sftp_quote(value: str) -> str:
        """Escapa um argumento para o lote do ``sftp`` (rejeita quebra de linha,
        que injetaria um segundo comando)."""
        if "\n" in value or "\r" in value:
            raise ValueError("invalid path: embedded newline")
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

    _SFTP_ERRORS = re.compile(
        r"no such file|not found|permission denied|couldn't|failure|"
        r"remote open|local open|connection closed|not a directory|read-only",
        re.IGNORECASE,
    )

    def _ensure_master(self) -> None:
        """Garante a conexão mestre (reautentica se o ControlPersist expirou)."""
        if self._mux and self._ssh:
            self._run("true", timeout=self._p.timeout + 10)

    def _sftp_batch(self, commands: str) -> Result:
        if not self._sftp:
            return False, _t("rbrowser.no_sftp", "Binário 'sftp' não encontrado.")
        use_batch = self._mux
        if use_batch:
            self._ensure_master()
            argv = self._argv("sftp", ["-b", "-"]) + [self._target()]
            stdin_text = commands
        else:
            argv = self._argv("sftp") + [self._target()]
            stdin_text = commands + "bye\n"
        rc, out, err = self._exec(argv, timeout=3600, stdin_text=stdin_text)
        failed = rc != 0 or (not use_batch and bool(self._SFTP_ERRORS.search(err + out)))
        if failed:
            raw = "\n".join(
                ln for ln in (err or out).splitlines() if ln.strip() and not ln.startswith("Connected to")
            )
            msg = self.classify_error(raw)
            logger.warning("SFTP falhou (rc=%d): %s", rc, raw)
            return False, msg
        return True, ""

    def download(self, remote: str, local: Path, progress: ProgressCb | None = None) -> Result:
        part = _temp_part(local)
        part.unlink(missing_ok=True)
        try:
            cmd = (
                f"get {self._sftp_quote(self._abs(remote))} "
                f"{self._sftp_quote(part.as_posix())}\n"
            )
        except ValueError as exc:
            return False, str(exc)
        ok, msg = self._sftp_batch(cmd) if self._sftp else self._scp_copy(
            f"{self._target()}:{shlex.quote(self._abs(remote))}", str(part)
        )
        if ok and part.exists():
            os.replace(part, local)
            return True, ""
        part.unlink(missing_ok=True)
        return False, msg or _t("rbrowser.dl_fail_msg", "Não foi possível baixar o arquivo.")

    def upload(self, local: Path, remote: str, progress: ProgressCb | None = None) -> Result:
        try:
            cmd = (
                f"put {self._sftp_quote(local.as_posix())} "
                f"{self._sftp_quote(self._abs(remote))}\n"
            )
        except ValueError as exc:
            return False, str(exc)
        if self._sftp:
            return self._sftp_batch(cmd)
        return self._scp_copy(str(local), f"{self._target()}:{shlex.quote(self._abs(remote))}")

    def _scp_copy(self, src: str, dst: str) -> Result:
        if not self._scp:
            return False, _t("rbrowser.no_sftp", "Binário 'sftp' não encontrado.")
        argv = self._argv("scp") + ["--", src, dst]
        rc, _out, err = self._exec(argv, timeout=3600)
        return (True, "") if rc == 0 else (False, self.classify_error(err))

    def _simple(self, cmd: str) -> Result:
        rc, _out, err = self._run(cmd, timeout=60)
        return (True, "") if rc == 0 else (False, self.classify_error(err))

    def mkdir(self, path: str) -> Result:
        return self._simple(f"mkdir -- {shlex.quote(self._abs(path))}")

    def remove(self, path: str, is_dir: bool = False) -> Result:
        flag = "-rf" if is_dir else "-f"
        return self._simple(f"rm {flag} -- {shlex.quote(self._abs(path))}")

    def rename(self, src: str, dst: str) -> Result:
        return self._simple(f"mv -n -- {shlex.quote(self._abs(src))} {shlex.quote(self._abs(dst))}")

    def start_debug_tunnel(
        self, remote_port: int = 5678, local_port: int = 5678
    ) -> subprocess.Popen | None:
        """Abre ``ssh -N -L`` para depuração remota (processo independente)."""
        if not self._ssh:
            return None
        extra = [
            "-N", "-L", f"{local_port}:localhost:{remote_port}",
            "-o", "ExitOnForwardFailure=yes",
            "-o", "ControlMaster=no", "-o", "ControlPath=none",
        ]  # fmt: skip
        argv = self._wrap(self._argv("ssh", extra, mux=False) + [self._target()])
        try:
            return subprocess.Popen(  # noqa: S603
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **self._popen_kwargs(),
            )
        except OSError as exc:
            logger.warning("Túnel de debug falhou: %s", exc)
            return None

    def close(self) -> None:
        if self._ctl_dir and self._ssh:
            ctl = os.path.join(self._ctl_dir, "%C")
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                subprocess.run(  # noqa: S603
                    [self._ssh, "-o", f"ControlPath={ctl}", "-O", "exit", self._target()],
                    capture_output=True,
                    timeout=5,
                    check=False,
                    stdin=subprocess.DEVNULL,
                )
        for attr in ("_ctl_dir", "_askpass_dir"):
            directory = getattr(self, attr)
            if directory:
                shutil.rmtree(directory, ignore_errors=True)
                setattr(self, attr, None)

_DOS_LIST_RE = re.compile(
    r"^(?P<date>\d{2}-\d{2}-\d{2,4})\s+(?P<time>\d{1,2}:\d{2}\s*[AP]M?)\s+"
    r"(?P<size>\d+|<DIR>)\s+(?P<name>.+)$",
    re.IGNORECASE,
)


def parse_ftp_list(lines: list[str]) -> list[RemoteEntry]:
    """Interpreta linhas de ``LIST`` em formato Unix ou DOS/IIS."""
    entries: list[RemoteEntry] = []
    for line in lines:
        dos = _DOS_LIST_RE.match(line.strip())
        if dos:
            is_dir = dos["size"].upper() == "<DIR>"
            entries.append(
                RemoteEntry(
                    name=dos["name"],
                    is_dir=is_dir,
                    size=None if is_dir else int(dos["size"]),
                    modified=f"{dos['date']} {dos['time']}",
                )
            )
            continue
        m = _LS_RE.match(line)
        if m:
            parsed = parse_ls_output(line)
            entries.extend(parsed)
            continue
        parts = line.split(None, 8)
        if len(parts) == 9 and parts[0][:1] in "-dl" and parts[8] not in (".", ".."):
            name, _, target = parts[8].partition(" -> ")
            entries.append(
                RemoteEntry(
                    name=name,
                    is_dir=parts[0].startswith("d"),
                    size=int(parts[4]) if parts[4].isdigit() else None,
                    modified=" ".join(parts[5:8]),
                    is_link=parts[0].startswith("l"),
                    link_target=target,
                )
            )
    return entries


class _ImplicitFTPTLS(ftplib.FTP_TLS):
    """FTPS com TLS implícito (porta 990): o socket é embrulhado ao conectar."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._raw_sock: socket.socket | None = None

    @property
    def sock(self) -> socket.socket | None:  # type: ignore[override]
        return self._raw_sock

    @sock.setter
    def sock(self, value: socket.socket | None) -> None:
        if value is not None and not isinstance(value, ssl.SSLSocket):
            value = self.context.wrap_socket(value, server_hostname=self.host)
        self._raw_sock = value


class FTPAdapter(RemoteAdapter):
    """FTP/FTPS com MLSD (quando disponível), reconexão e acesso serializado."""

    _KEEPALIVE_S = 25

    def __init__(self, profile: RemoteProfile) -> None:
        super().__init__(profile)
        self._ftp: ftplib.FTP | None = None
        self._lock = threading.RLock()
        self._last_use = 0.0

    def _new_client(self) -> ftplib.FTP:
        p = self._p
        if p.proto == Protocol.FTPS:
            ctx = _make_ssl_context(p.verify_tls)
            cls = _ImplicitFTPTLS if p.port == 990 else ftplib.FTP_TLS
            return cls(context=ctx)
        return ftplib.FTP()

    def connect(self) -> Result:
        p = self._p
        with self._lock:
            self._drop()
            try:
                ftp = self._new_client()
                ftp.connect(p.host, p.port, timeout=p.timeout)
                ftp.login(p.user or "anonymous", p.decoded_password() or "anonymous@")
                if isinstance(ftp, ftplib.FTP_TLS):
                    ftp.prot_p()
                ftp.set_pasv(bool(p.passive_ftp))
                self._ftp = ftp
                self._last_use = time.monotonic()
                root = p.remote_root if p.remote_root not in ("", "~") else ""
                if root:
                    with contextlib.suppress(ftplib.all_errors):
                        ftp.cwd(root)
                self.home = ftp.pwd() or "/"
                return True, _t("rbrowser.connected", "Conectado.")
            except (*ftplib.all_errors, ssl.SSLError, OSError) as exc:
                self._drop()
                return False, self._friendly(exc)

    @staticmethod
    def _friendly(exc: BaseException) -> str:
        text = str(exc)
        if text.startswith("530"):
            return _t("rbrowser.err_perm", "Permissão negada - verifique usuário, senha ou chave.")
        if isinstance(exc, ssl.SSLCertVerificationError):
            return _t(
                "rbrowser.err_tls",
                "Certificado TLS inválido. Desative 'Verificar certificado' se confiar no servidor.",
            )
        if isinstance(exc, socket.gaierror):
            return _t("rbrowser.err_dns", "Host não encontrado.")
        if isinstance(exc, (ConnectionRefusedError, TimeoutError)):
            return _t("rbrowser.err_refused", "Conexão recusada ou sem resposta.")
        return text or _t("rbrowser.err_generic", "Falha na conexão.")

    def _drop(self) -> None:
        if self._ftp is not None:
            with contextlib.suppress(*ftplib.all_errors, OSError):
                self._ftp.close()
            self._ftp = None

    def _client(self) -> ftplib.FTP:
        """Cliente vivo: reconecta se caiu ou ficou ocioso demais."""
        if self._ftp is not None and time.monotonic() - self._last_use > self._KEEPALIVE_S:
            try:
                self._ftp.voidcmd("NOOP")
            except (*ftplib.all_errors, OSError):
                self._drop()
        if self._ftp is None:
            ok, msg = self.connect()
            if not ok:
                raise ConnectionError(msg)
        assert self._ftp is not None
        self._last_use = time.monotonic()
        return self._ftp

    def _abs(self, path: str) -> str:
        if not path or path == "~":
            return self.home
        if path.startswith("~/"):
            return posixpath.join(self.home, path[2:])
        return path

    def listdir(self, path: str) -> tuple[list[RemoteEntry], str]:
        with self._lock:
            try:
                ftp = self._client()
                target = self._abs(path)
                try:
                    return self._mlsd(ftp, target), ""
                except ftplib.error_perm:
                    pass
                ftp.cwd(target)
                lines: list[str] = []
                ftp.retrlines("LIST", lines.append)
                return parse_ftp_list(lines), ""
            except ConnectionError as exc:
                return [], str(exc)
            except (*ftplib.all_errors, OSError) as exc:
                return [], self._friendly(exc)

    @staticmethod
    def _mlsd(ftp: ftplib.FTP, target: str) -> list[RemoteEntry]:
        entries: list[RemoteEntry] = []
        for name, facts in ftp.mlsd(target, facts=["type", "size", "modify"]):
            kind = facts.get("type", "").lower()
            if kind in ("cdir", "pdir") or name in (".", ".."):
                continue
            mod = facts.get("modify", "")
            if len(mod) >= 12:
                mod = f"{mod[0:4]}-{mod[4:6]}-{mod[6:8]} {mod[8:10]}:{mod[10:12]}"
            size = facts.get("size", "")
            entries.append(
                RemoteEntry(
                    name=name,
                    is_dir=kind == "dir",
                    size=int(size) if size.isdigit() else None,
                    modified=mod,
                )
            )
        return entries

    def download(self, remote: str, local: Path, progress: ProgressCb | None = None) -> Result:
        part = _temp_part(local)
        with self._lock:
            try:
                ftp = self._client()
                done = 0
                with open(part, "wb") as fh:

                    def _write(chunk: bytes) -> None:
                        nonlocal done
                        fh.write(chunk)
                        done += len(chunk)
                        if progress:
                            progress(done, None)

                    ftp.retrbinary(f"RETR {self._abs(remote)}", _write)
                os.replace(part, local)
                return True, ""
            except (ConnectionError, *ftplib.all_errors, OSError) as exc:
                part.unlink(missing_ok=True)
                logger.warning("FTP download: %s", exc)
                return False, self._friendly(exc)

    def upload(self, local: Path, remote: str, progress: ProgressCb | None = None) -> Result:
        with self._lock:
            try:
                ftp = self._client()
                total = local.stat().st_size
                done = 0

                def _cb(chunk: bytes) -> None:
                    nonlocal done
                    done += len(chunk)
                    if progress:
                        progress(done, total)

                with open(local, "rb") as fh:
                    ftp.storbinary(f"STOR {self._abs(remote)}", fh, callback=_cb)
                return True, ""
            except (ConnectionError, *ftplib.all_errors, OSError) as exc:
                logger.warning("FTP upload: %s", exc)
                return False, self._friendly(exc)

    def _cmd(self, fn: Callable[[ftplib.FTP], object]) -> Result:
        with self._lock:
            try:
                fn(self._client())
                return True, ""
            except (ConnectionError, *ftplib.all_errors, OSError) as exc:
                return False, self._friendly(exc)

    def mkdir(self, path: str) -> Result:
        return self._cmd(lambda f: f.mkd(self._abs(path)))

    def remove(self, path: str, is_dir: bool = False) -> Result:
        if is_dir:
            return self._cmd(lambda f: f.rmd(self._abs(path)))
        return self._cmd(lambda f: f.delete(self._abs(path)))

    def rename(self, src: str, dst: str) -> Result:
        return self._cmd(lambda f: f.rename(self._abs(src), self._abs(dst)))

    def close(self) -> None:
        with self._lock:
            if self._ftp is not None:
                with contextlib.suppress(*ftplib.all_errors, OSError):
                    self._ftp.quit()
                self._drop()

_DATE_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}|\d{2}-[A-Za-z]{3}-\d{4}\s+\d{2}:\d{2}"
)
_SIZE_RE = re.compile(r"(?:^|\s)(\d+(?:\.\d+)?[KMGT]?|-)\s*$")


class _IndexParser(html.parser.HTMLParser):
    """Extrai links e o texto que os acompanha (data/tamanho) de um índice."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[dict[str, str]] = []
        self._cur: dict[str, str] | None = None
        self._in_a = False

    def _flush(self) -> None:
        if self._cur is not None:
            self.items.append(self._cur)
            self._cur = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self._flush()
            href = dict(attrs).get("href") or ""
            self._cur = {"href": href, "label": "", "tail": ""}
            self._in_a = True
        elif tag in ("tr", "br", "hr"):
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._in_a = False
        elif tag in ("tr", "pre"):
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._cur is None:
            return
        key = "label" if self._in_a else "tail"
        self._cur[key] += data

    def close(self) -> None:
        super().close()
        self._flush()


def parse_http_index(body: str, page_url: str) -> list[RemoteEntry]:
    """Lista filhos diretos de ``page_url`` a partir de um índice Apache/nginx."""
    parser = _IndexParser()
    parser.feed(body)
    parser.close()
    base = urllib.parse.urlparse(page_url)
    base_path = base.path if base.path.endswith("/") else base.path + "/"
    entries: list[RemoteEntry] = []
    seen: set[str] = set()
    for item in parser.items:
        href = item["href"].strip()
        if not href or href.startswith(("?", "#", "javascript:", "mailto:")):
            continue
        target = urllib.parse.urlparse(urllib.parse.urljoin(page_url, href))
        if target.netloc != base.netloc or not target.path.startswith(base_path):
            continue
        rest = target.path[len(base_path):]
        child = rest.strip("/")
        if not child or "/" in child:
            continue
        name = urllib.parse.unquote(child)
        if name in seen:
            continue
        seen.add(name)
        tail = item["tail"].replace("\xa0", " ")
        date = _DATE_RE.search(tail)
        size_m = _SIZE_RE.search(tail[date.end():] if date else tail)
        is_dir = rest.endswith("/")
        entries.append(
            RemoteEntry(
                name=name,
                is_dir=is_dir,
                size=None if is_dir or not size_m else parse_human_size(size_m.group(1)),
                modified=re.sub(r"\s+", " ", date.group(0)) if date else "",
            )
        )
    return entries


class HTTPAdapter(RemoteAdapter):
    """Navegação em índices de diretório HTTP(S) (somente leitura)."""

    read_only = True
    _MAX_REDIRECTS = 5

    def __init__(self, profile: RemoteProfile) -> None:
        super().__init__(profile)
        scheme = "https" if profile.proto in (Protocol.HTTPS, Protocol.WEBDAVS) else "http"
        host = profile.host
        self._prefix = ""
        if "://" in host:
            parsed = urllib.parse.urlparse(host)
            scheme = parsed.scheme or scheme
            host = parsed.netloc
            self._prefix = parsed.path.rstrip("/")
        default = 443 if scheme == "https" else 80
        netloc = host if ":" in host.rsplit("]", 1)[-1] or profile.port == default else f"{host}:{profile.port}"
        self._base = f"{scheme}://{netloc}"
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=_make_ssl_context(profile.verify_tls))
        )
        root = profile.remote_root
        self.home = "/" if root in ("", "~") else "/" + root.strip("/")

    def _url(self, path: str) -> str:
        clean = "/" + path.lstrip("/") if path not in ("", "~") else self.home
        quoted = urllib.parse.quote(self._prefix + clean, safe="/~:@!$&'()*+,;=-._")
        return self._base + quoted

    def _headers(self) -> dict[str, str]:
        headers = {"User-Agent": "Codis-RemoteBrowser/3"}
        if self._p.user:
            raw = f"{self._p.user}:{self._p.decoded_password()}".encode()
            headers["Authorization"] = "Basic " + base64.b64encode(raw).decode()
        return headers

    def _open(
        self,
        method: str,
        url: str,
        *,
        data: object = None,
        headers: dict[str, str] | None = None,
    ):  # noqa: ANN202
        """Abre ``url`` seguindo redirecionamentos também para métodos WebDAV."""
        merged = {**self._headers(), **(headers or {})}
        for _ in range(self._MAX_REDIRECTS + 1):
            parsed = urllib.parse.urlparse(url)
            if parsed.scheme not in ("http", "https"):
                raise ValueError(f"Esquema de URL não suportado: {parsed.scheme!r}")
            req = urllib.request.Request(url, data=data, method=method, headers=merged)  # type: ignore[arg-type]
            try:
                return self._opener.open(req, timeout=self._p.timeout)
            except urllib.error.HTTPError as exc:
                location = exc.headers.get("Location") if exc.headers else None
                if exc.code in (301, 302, 307, 308) and location:
                    url = urllib.parse.urljoin(url, location)
                    continue
                raise
        raise urllib.error.URLError("too many redirects")

    @staticmethod
    def _friendly(exc: BaseException) -> str:
        if isinstance(exc, urllib.error.HTTPError):
            mapping = {
                401: ("rbrowser.http_401", "Credenciais necessárias ou inválidas (HTTP 401)."),
                403: ("rbrowser.http_403", "Acesso negado (HTTP 403)."),
                404: ("rbrowser.http_404", "Não encontrado (HTTP 404)."),
                405: ("rbrowser.http_405", "Operação não permitida pelo servidor (HTTP 405)."),
                409: ("rbrowser.http_409", "Conflito: a pasta de destino não existe (HTTP 409)."),
                507: ("rbrowser.http_507", "Sem espaço no servidor (HTTP 507)."),
            }
            if exc.code in mapping:
                return _t(*mapping[exc.code])
            return f"HTTP {exc.code} {exc.reason}"
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, ssl.SSLCertVerificationError):
            return _t(
                "rbrowser.err_tls",
                "Certificado TLS inválido. Desative 'Verificar certificado' se confiar no servidor.",
            )
        if isinstance(reason, socket.gaierror):
            return _t("rbrowser.err_dns", "Host não encontrado.")
        if isinstance(reason, (ConnectionRefusedError, TimeoutError, socket.timeout)):
            return _t("rbrowser.err_refused", "Conexão recusada ou sem resposta.")
        return str(reason) or _t("rbrowser.err_generic", "Falha na conexão.")

    def connect(self) -> Result:
        try:
            with self._open("GET", self._url(self.home)) as resp:
                resp.read(1024)
            return True, _t("rbrowser.connected", "Conectado.")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return False, self._friendly(exc)

    def listdir(self, path: str) -> tuple[list[RemoteEntry], str]:
        url = self._url(path)
        if not url.endswith("/"):
            url += "/"
        try:
            with self._open("GET", url) as resp:
                body = resp.read(MAX_LISTING_BYTES).decode("utf-8", errors="replace")
                final_url = resp.geturl()
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return [], self._friendly(exc)
        return parse_http_index(body, final_url if final_url.endswith("/") else url), ""

    def download(self, remote: str, local: Path, progress: ProgressCb | None = None) -> Result:
        part = _temp_part(local)
        try:
            with self._open("GET", self._url(remote)) as resp, open(part, "wb") as fh:
                total = int(resp.headers.get("Content-Length") or 0) or None
                done = 0
                while chunk := resp.read(64 * 1024):
                    fh.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
            os.replace(part, local)
            return True, ""
        except (urllib.error.URLError, OSError, ValueError) as exc:
            part.unlink(missing_ok=True)
            logger.warning("HTTP download %s: %s", remote, exc)
            return False, self._friendly(exc)

    def upload(self, local: Path, remote: str, progress: ProgressCb | None = None) -> Result:
        return False, _t("rbrowser.http_no_upload", "Upload não suportado via HTTP.")

_PROPFIND_BODY = (
    b'<?xml version="1.0" encoding="utf-8"?>'
    b'<d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/><d:getcontentlength/>'
    b"<d:getlastmodified/></d:prop></d:propfind>"
)
_DAV = "{DAV:}"


class WebDAVAdapter(HTTPAdapter):
    """WebDAV completo: listar, baixar, enviar, criar pasta, renomear, excluir."""

    read_only = False

    def connect(self) -> Result:
        """Valida com ``PROPFIND Depth: 0`` (um GET numa coleção WebDAV costuma
        cair no navegador HTML do servidor ou redirecionar)."""
        url = self._url(self.home).rstrip("/") + "/"
        try:
            with self._open(
                "PROPFIND",
                url,
                data=_PROPFIND_BODY,
                headers={"Depth": "0", "Content-Type": "application/xml"},
            ) as resp:
                resp.read(1024)
            return True, _t("rbrowser.connected", "Conectado.")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return False, self._friendly(exc)

    def listdir(self, path: str) -> tuple[list[RemoteEntry], str]:
        url = self._url(path)
        if not url.endswith("/"):
            url += "/"
        try:
            with self._open(
                "PROPFIND",
                url,
                data=_PROPFIND_BODY,
                headers={"Depth": "1", "Content-Type": "application/xml"},
            ) as resp:
                body = resp.read(MAX_LISTING_BYTES)
                final = urllib.parse.urlparse(resp.geturl()).path
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return [], self._friendly(exc)
        try:
            root = _XML_FROMSTRING(body)
        except ET.ParseError as exc:
            return [], f"XML inválido: {exc}"
        self_path = urllib.parse.unquote(final).rstrip("/")
        entries: list[RemoteEntry] = []
        for response in root.findall(f"{_DAV}response"):
            href_el = response.find(f"{_DAV}href")
            if href_el is None or not href_el.text:
                continue
            href_path = urllib.parse.unquote(urllib.parse.urlparse(href_el.text.strip()).path)
            if href_path.rstrip("/") == self_path:
                continue
            name = posixpath.basename(href_path.rstrip("/"))
            if not name:
                continue
            is_dir = response.find(f".//{_DAV}resourcetype/{_DAV}collection") is not None
            size_el = response.find(f".//{_DAV}getcontentlength")
            mod_el = response.find(f".//{_DAV}getlastmodified")
            size_txt = (size_el.text or "").strip() if size_el is not None else ""
            entries.append(
                RemoteEntry(
                    name=name,
                    is_dir=is_dir,
                    size=int(size_txt) if size_txt.isdigit() and not is_dir else None,
                    modified=(mod_el.text or "").strip() if mod_el is not None else "",
                )
            )
        return entries, ""

    def upload(self, local: Path, remote: str, progress: ProgressCb | None = None) -> Result:
        try:
            with open(local, "rb") as fh:
                headers = {
                    "Content-Length": str(local.stat().st_size),
                    "Content-Type": "application/octet-stream",
                }
                with self._open("PUT", self._url(remote), data=fh, headers=headers) as resp:
                    ok = resp.status in (200, 201, 204)
                    return ok, "" if ok else f"HTTP {resp.status}"
        except (urllib.error.URLError, OSError, ValueError) as exc:
            logger.warning("WebDAV PUT: %s", exc)
            return False, self._friendly(exc)

    def _method(self, method: str, url: str, headers: dict[str, str] | None = None) -> Result:
        try:
            with self._open(method, url, headers=headers) as resp:
                resp.read()
            return True, ""
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return False, self._friendly(exc)

    def mkdir(self, path: str) -> Result:
        return self._method("MKCOL", self._url(path).rstrip("/") + "/")

    def remove(self, path: str, is_dir: bool = False) -> Result:
        url = self._url(path)
        return self._method("DELETE", url.rstrip("/") + "/" if is_dir else url)

    def rename(self, src: str, dst: str) -> Result:
        return self._method(
            "MOVE", self._url(src), {"Destination": self._url(dst), "Overwrite": "F"}
        )


def make_adapter(profile: RemoteProfile) -> RemoteAdapter:
    """Cria o adaptador adequado ao protocolo do perfil."""
    proto = profile.proto
    if proto in (Protocol.FTP, Protocol.FTPS):
        return FTPAdapter(profile)
    if proto in (Protocol.WEBDAV, Protocol.WEBDAVS):
        return WebDAVAdapter(profile)
    if proto in (Protocol.HTTP, Protocol.HTTPS):
        return HTTPAdapter(profile)
    return SSHAdapter(profile)
