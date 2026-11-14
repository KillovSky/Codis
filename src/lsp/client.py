"""Codis LSP - Language Server Protocol client (LSP 3.17).

Gerencia um processo de servidor por linguagem, comunicando via JSON-RPC 2.0.

Servidores suportados (auto-detectados do PATH):
    Python      : pylsp, pyright-langserver, jedi-language-server
    JS/TS       : typescript-language-server, deno
    Rust        : rust-analyzer
    Go          : gopls
    C/C++       : clangd
    Java        : jdtls (Eclipse JDT)
    Kotlin      : kotlin-language-server
    Swift       : sourcekit-lsp
    Dart        : dart language-server
    PHP         : intelephense, phpactor
    Ruby        : ruby-lsp, solargraph
    Elixir      : elixir-ls
    Erlang      : erlang-ls
    Scala       : metals
    Haskell     : haskell-language-server-wrapper
    Lua         : lua-language-server
    Bash/Shell  : bash-language-server
    R           : r languageserver
    Julia       : LanguageServer.jl
    Clojure     : clojure-lsp
    Groovy      : groovy-language-server
    JSON        : vscode-json-languageserver
    HTML        : vscode-html-languageserver
    CSS/SCSS    : vscode-css-languageserver
    YAML        : yaml-language-server
    TOML        : taplo
    XML         : lemminx
    Markdown    : marksman
    Dockerfile  : dockerfile-language-server
    Terraform   : terraform-ls
    GraphQL     : graphql-language-service-cli
    Protobuf    : buf-language-server
    Nix         : nil, rnix-lsp
    SQL         : sqls, sql-language-server
    C#          : OmniSharp, csharp-ls
    F#          : fsautocomplete
    OCaml       : ocamllsp

Features implementadas:
    textDocument/hover -> balloon de documentação
    textDocument/completion -> autocomplete aumentado
    textDocument/definition -> ir para definição
    textDocument/declaration -> ir para declaração
    textDocument/typeDefinition -> tipo da definição
    textDocument/implementation -> implementação da interface
    textDocument/references -> todas as referências
    textDocument/signatureHelp -> hints de parâmetros
    textDocument/documentSymbol -> símbolos do documento (outline)
    workspace/symbol -> busca de símbolo no workspace
    textDocument/formatting -> formatação via LSP
    textDocument/rangeFormatting -> formatação de seleção
    textDocument/rename -> renomear símbolo
    textDocument/prepareRename -> validar renomeação
    textDocument/codeAction -> quick fixes e refactors
    textDocument/documentHighlight -> destacar ocorrências
    textDocument/foldingRange -> folding via LSP
    textDocument/inlayHint -> inlay hints via LSP
    callHierarchy/incomingCalls -> hierarquia de chamadas
    callHierarchy/outgoingCalls -> hierarquia de chamadas
    typeHierarchy/supertypes -> hierarquia de tipos
    typeHierarchy/subtypes -> hierarquia de tipos
    textDocument/semanticTokens/full -> colorização semântica
    completionItem/resolve -> documentação lazy
    workspace/executeCommand -> executar comando do servidor
    textDocument/linkedEditingRange -> edição simultânea (HTML tags)
"""

from __future__ import annotations

import contextlib
import functools
import json
import logging
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from typing import Any

from src.constants import APP_VERSION
from src.terminal.backends import child_process_env

from .protocol import (
    CallHierarchyCall,
    CallHierarchyItem,
    CodeAction,
    CompletionItem,
    DocumentHighlight,
    DocumentSymbol,
    FoldingRange,
    Hover,
    InlayHint,
    Location,
    LocationLink,
    LSPMessage,
    PrepareRenameResult,
    Range,
    SemanticTokens,
    SignatureHelp,
    TextEdit,
    TypeHierarchyItem,
    WorkspaceEdit,
)
from .protocol import (
    Diagnostic as LSPDiag,
)

logger = logging.getLogger("Codis.LSP")

_MAX_MESSAGE_BYTES = 256 * 1024 * 1024

_SERVER_CANDIDATES: dict[str, list[list[str]]] = {
    "python": [
        [sys.executable, "-m", "pylsp"],
        ["pylsp"],
        ["pyright-langserver", "--stdio"],
        [sys.executable, "-m", "pyright", "--stdio"],
        ["jedi-language-server"],
        [sys.executable, "-m", "jedi_language_server"],
        ["python-language-server"],
    ],
    "javascript": [
        ["typescript-language-server", "--stdio"],
        ["tsserver", "--stdio"],
        ["deno", "lsp"],
    ],
    "typescript": [
        ["typescript-language-server", "--stdio"],
        ["deno", "lsp"],
    ],
    "rust": [
        ["rust-analyzer"],
    ],
    "go": [
        ["gopls"],
    ],
    "c": [
        ["clangd"],
        ["ccls"],
    ],
    "cpp": [
        ["clangd"],
        ["ccls"],
    ],
    "java": [
        ["jdtls"],
        ["java-language-server"],
    ],
    "kotlin": [
        ["kotlin-language-server"],
    ],
    "swift": [
        ["sourcekit-lsp"],
    ],
    "dart": [
        ["dart", "language-server", "--client-id", "codis"],
    ],
    "php": [
        ["intelephense", "--stdio"],
        ["phpactor", "language-server"],
    ],
    "ruby": [
        ["ruby-lsp"],
        ["solargraph", "stdio"],
    ],
    "elixir": [
        ["elixir-ls"],
        ["elixir-language-server"],
    ],
    "erlang": [
        ["erlang-ls", "--transport", "stdio"],
    ],
    "scala": [
        ["metals"],
    ],
    "haskell": [
        ["haskell-language-server-wrapper", "--lsp"],
        ["haskell-language-server", "--lsp"],
    ],
    "lua": [
        ["lua-language-server"],
        ["lua-lsp"],
    ],
    "bash": [
        ["bash-language-server", "start"],
    ],
    "shell": [
        ["bash-language-server", "start"],
    ],
    "r": [
        ["R", "--slave", "-e", "languageserver::run()"],
    ],
    "julia": [
        [
            "julia",
            "--startup-file=no",
            "--history-file=no",
            "-e",
            "using LanguageServer; run()",
        ],
    ],
    "clojure": [
        ["clojure-lsp"],
    ],
    "groovy": [
        ["groovy-language-server"],
    ],
    "json": [
        ["vscode-json-languageserver", "--stdio"],
        ["vscode-json-language-server", "--stdio"],
    ],
    "html": [
        ["vscode-html-languageserver", "--stdio"],
        ["vscode-html-language-server", "--stdio"],
    ],
    "css": [
        ["vscode-css-languageserver", "--stdio"],
        ["vscode-css-language-server", "--stdio"],
    ],
    "scss": [
        ["vscode-css-languageserver", "--stdio"],
        ["vscode-css-language-server", "--stdio"],
    ],
    "less": [
        ["vscode-css-languageserver", "--stdio"],
        ["vscode-css-language-server", "--stdio"],
    ],
    "yaml": [
        ["yaml-language-server", "--stdio"],
    ],
    "toml": [
        ["taplo", "lsp", "stdio"],
    ],
    "xml": [
        ["lemminx"],
    ],
    "markdown": [
        ["marksman", "server"],
        ["zeta-note"],
    ],
    "docker": [
        ["docker-langserver", "--stdio"],
        ["dockerfile-language-server", "--stdio"],
    ],
    "hcl": [
        ["terraform-ls", "serve"],
    ],
    "graphql": [
        ["graphql-lsp", "server", "-m", "stream"],
    ],
    "protobuf": [
        ["buf", "beta", "lsp"],
        ["bufls", "serve"],
    ],
    "nix": [
        ["nil"],
        ["rnix-lsp"],
    ],
    "sql": [
        ["sqls"],
        ["sql-language-server", "up", "--method", "stdio"],
    ],
    "csharp": [
        ["OmniSharp", "-lsp"],
        ["csharp-ls"],
        ["omnisharp"],
    ],
    "fsharp": [
        ["fsautocomplete", "--adaptive-lsp-server-enabled"],
    ],
    "ocaml": [
        ["ocamllsp"],
    ],
    "perl": [
        ["perl", "-MPerl::LanguageServer", "-e", "Perl::LanguageServer::run()"],
        ["perl-language-server"],
    ],
    "powershell": [
        [
            "pwsh",
            "-NoLogo",
            "-NonInteractive",
            "-Command",
            "Import-Module PowerShellEditorServices; Start-EditorServices",
        ],
    ],
}

_LANG_ID_MAP: dict[str, str] = {
    "javascript": "javascript",
    "typescript": "typescript",
    "python": "python",
    "rust": "rust",
    "go": "go",
    "c": "c",
    "cpp": "cpp",
    "java": "java",
    "kotlin": "kotlin",
    "swift": "swift",
    "dart": "dart",
    "php": "php",
    "ruby": "ruby",
    "elixir": "elixir",
    "erlang": "erlang",
    "scala": "scala",
    "haskell": "haskell",
    "lua": "lua",
    "bash": "shellscript",
    "shell": "shellscript",
    "r": "r",
    "julia": "julia",
    "clojure": "clojure",
    "groovy": "groovy",
    "html": "html",
    "vue": "vue",
    "svelte": "svelte",
    "css": "css",
    "scss": "scss",
    "less": "less",
    "json": "json",
    "markdown": "markdown",
    "yaml": "yaml",
    "toml": "toml",
    "xml": "xml",
    "docker": "dockerfile",
    "hcl": "terraform",
    "graphql": "graphql",
    "protobuf": "proto",
    "nix": "nix",
    "sql": "sql",
    "csharp": "csharp",
    "fsharp": "fsharp",
    "ocaml": "ocaml",
    "perl": "perl",
    "powershell": "powershell",
    "bat": "bat",
    "text": "plaintext",
}

_INSTALL_HINTS: dict[str, str] = {
    "python": "pip install python-lsp-server",
    "javascript": "npm i -g typescript-language-server typescript",
    "typescript": "npm i -g typescript-language-server typescript",
    "rust": "rustup component add rust-analyzer",
    "go": "go install golang.org/x/tools/gopls@latest",
    "c": "apt install clangd  /  brew install llvm",
    "cpp": "apt install clangd  /  brew install llvm",
    "java": "brew install jdtls  (ou instale via IDE)",
    "kotlin": "brew install kotlin-language-server",
    "swift": "xcode-select --install  (inclui sourcekit-lsp)",
    "dart": "flutter/dart SDK inclui language-server",
    "php": "npm i -g intelephense",
    "ruby": "gem install ruby-lsp  /  gem install solargraph",
    "elixir": "asdf install / hex.pm/packages/elixir_ls",
    "erlang": "brew install erlang-ls",
    "scala": "coursier install metals",
    "haskell": "ghcup install hls",
    "lua": "brew install lua-language-server",
    "bash": "npm i -g bash-language-server",
    "shell": "npm i -g bash-language-server",
    "r": "install.packages('languageserver')",
    "julia": "julia -e 'using Pkg; Pkg.add(\"LanguageServer\")'",
    "clojure": "brew install clojure-lsp/brew/clojure-lsp-native",
    "html": "npm i -g vscode-langservers-extracted",
    "css": "npm i -g vscode-langservers-extracted",
    "scss": "npm i -g vscode-langservers-extracted",
    "json": "npm i -g vscode-langservers-extracted",
    "yaml": "npm i -g yaml-language-server",
    "toml": "cargo install taplo-cli  /  brew install taplo",
    "xml": "brew install lemminx",
    "markdown": "brew install marksman",
    "docker": "npm i -g dockerfile-language-server-nodejs",
    "hcl": "brew install hashicorp/tap/terraform-ls",
    "graphql": "npm i -g graphql-language-service-cli",
    "nix": "nix-env -iA nixpkgs.nil",
    "sql": "go install github.com/lighttiger2505/sqls@latest",
    "csharp": "dotnet tool install -g csharp-ls",
    "fsharp": "dotnet tool install -g fsautocomplete",
    "ocaml": "opam install ocaml-lsp-server",
}


def _extra_search_paths() -> list[str]:
    """Retorna diretórios extras a verificar além do PATH do sistema."""
    extras: list[str] = []
    if sys.platform == "win32":
        import site as _site

        with contextlib.suppress(Exception):
            for s in _site.getsitepackages():
                scripts = Path(s).parent / "Scripts"
                if scripts.is_dir():
                    extras.append(str(scripts))

        with contextlib.suppress(Exception):
            npm_bin = Path(os.environ.get("APPDATA", "")) / "npm"
            if npm_bin.is_dir():
                extras.append(str(npm_bin))

        with contextlib.suppress(Exception):
            user_scripts = Path(sys.executable).parent / "Scripts"
            if user_scripts.is_dir():
                extras.append(str(user_scripts))
    else:
        local_bin = Path.home() / ".local" / "bin"
        if local_bin.is_dir():
            extras.append(str(local_bin))

        for npm_dir in (
            "/usr/local/lib/node_modules/.bin",
            str(Path.home() / ".npm-global" / "bin"),
            "/opt/homebrew/bin",
        ):
            if Path(npm_dir).is_dir():
                extras.append(npm_dir)
    return extras


def _which_extended(exe: str) -> str | None:
    """shutil.which() com busca em diretórios extras além do PATH."""
    found = shutil.which(exe)
    if found:
        return found
    for extra in _extra_search_paths():
        candidate = Path(extra) / exe
        if candidate.is_file():
            return str(candidate)

        for ext in (".exe", ".cmd", ".bat"):
            c2 = Path(extra) / (exe + ext)
            if c2.is_file():
                return str(c2)
    return None


@functools.lru_cache(maxsize=32)
def _python_with_module(module: str) -> str | None:
    """Python que consegue importar ``module`` (ou ``None``).

    Em desenvolvimento é o próprio interpretador. Compilado, ``sys.executable``
    é o Codis; então procura o Python do sistema e pergunta a ele, em vez de só
    olhar o que veio embutido no bundle.
    """
    import importlib.util

    from src import pyexec

    if not pyexec.is_frozen():
        return sys.executable if importlib.util.find_spec(module) is not None else None
    try:
        py = pyexec.python_executable()
    except pyexec.NoPythonError:
        return None
    code = f"import importlib.util,sys;sys.exit(0 if importlib.util.find_spec({module!r}) else 1)"
    try:
        rc = subprocess.run(  # noqa: S603
            [py, "-c", code],
            capture_output=True,
            timeout=20,
            check=False,
            stdin=subprocess.DEVNULL,
            env=pyexec.child_env(),
        ).returncode
    except (OSError, subprocess.SubprocessError):
        return None
    return py if rc == 0 else None


def _find_server(lang: str) -> list[str] | None:
    """Retorna o primeiro comando de servidor disponível para a linguagem."""
    for cmd in _SERVER_CANDIDATES.get(lang, []):
        exe = cmd[0]
        if exe == sys.executable:
            try:
                m_idx = cmd.index("-m")
                mod_name = cmd[m_idx + 1].replace("-", "_").split(".")[0]
                py = _python_with_module(mod_name)
                if py is not None:
                    return [py, *cmd[1:]]
            except (ValueError, IndexError):
                pass
        else:
            found = _which_extended(exe)
            if found:
                new_cmd = [found] + cmd[1:]
                return new_cmd
    return None


def _path_to_uri(path: Path) -> str:
    s = path.as_posix()
    if sys.platform == "win32":
        s = "/" + path.as_posix()
    return "file://" + s


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """Encerra ``proc`` e todos os seus descendentes sem bloquear por muito tempo.

    Servidores de linguagem costumam ser lançados por wrappers (``npx``, shims
    ``.cmd``) que geram o processo real como filho; matar só o wrapper deixa o
    servidor vivo, segurando pipes e impedindo o encerramento limpo do app.
    """
    if proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=3,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        elif os.getpgid(proc.pid) == proc.pid:
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except (OSError, subprocess.TimeoutExpired):
        with contextlib.suppress(OSError):
            proc.kill()


class LSPClient:
    """
    Gerencia um processo de servidor LSP e sua comunicação JSON-RPC.

    Callbacks são sempre chamados na thread Tk via root.after(0, cb).
    """

    def __init__(
        self,
        lang: str,
        root_path: Path | None,
        tk_root: tk.Misc,
        on_diagnostics: Callable[[str, list], None] | None = None,
        on_log: Callable[[str], None] | None = None,
        on_apply_edit: Callable | None = None,
    ) -> None:
        """Inicializa ``LSPClient``."""
        self.lang = lang
        self.root_path = root_path
        self._tk = tk_root
        self._on_diags = on_diagnostics
        self._on_log = on_log
        self._on_apply_edit = on_apply_edit

        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._outbox: queue.Queue[bytes | None] = queue.Queue()
        self._pending_lock = threading.Lock()
        self._id_lock = threading.Lock()
        self._id = 0
        self._pending: dict[int, Callable] = {}
        self._initialized = False
        self._pre_init_queue: list[dict] = []
        self._open_docs: dict[str, int] = {}
        self._capabilities: dict = {}
        self._semantic_legend: dict = {}
        self._shutdown = False

    def start(self) -> bool:
        """Inicia o servidor. Retorna True se iniciado com sucesso."""
        if self._proc and self._proc.poll() is None:
            return True
        cmd = _find_server(self.lang)
        if not cmd:
            logger.debug("LSP: sem servidor para '%s'", self.lang)
            return False
        try:
            env = child_process_env()
            cwd = str(self.root_path) if self.root_path else None
            spawn_opts: dict[str, Any] = {}
            if sys.platform != "win32":
                spawn_opts["start_new_session"] = True
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=cwd,
                env=env,
                **spawn_opts,
            )
            self._shutdown = False
            self._outbox = queue.Queue()
            threading.Thread(
                target=self._writer_loop,
                args=(self._proc, self._outbox),
                daemon=True,
                name=f"lsp-writer-{self.lang}",
            ).start()
            self._thread = threading.Thread(
                target=self._reader_loop, daemon=True, name=f"lsp-reader-{self.lang}"
            )
            self._thread.start()
            threading.Thread(
                target=self._stderr_loop, daemon=True, name=f"lsp-stderr-{self.lang}"
            ).start()
            self._send_initialize()
            logger.info("LSP: iniciado '%s' → %s", self.lang, cmd[0])
            return True
        except (OSError, FileNotFoundError) as exc:
            logger.debug("LSP: falha ao iniciar '%s': %s", self.lang, exc)
            return False

    def stop(self, grace: float = 1.0) -> None:
        """Para o servidor: pede o encerramento e, se não sair em ``grace`` s, mata a árvore.

        O pedido de ``shutdown``/``exit`` é enfileirado para a thread escritora,
        então um servidor travado nunca bloqueia quem chamou ``stop``.

        Args:
            grace: Segundos de espera pelo encerramento voluntário.
        """
        proc = self._proc
        if not proc or proc.poll() is not None:
            return
        self._shutdown = True
        with self._pending_lock:
            self._pending.clear()
        self._send(LSPMessage.request(self._next_id(), "shutdown", None))
        self._send(LSPMessage.notification("exit", None))
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            _kill_process_tree(proc)
            with contextlib.suppress(subprocess.TimeoutExpired, OSError):
                proc.wait(timeout=2)
        self._release_pipes(proc)
        self._initialized = False
        self._open_docs.clear()

    def _release_pipes(self, proc: subprocess.Popen) -> None:
        """Fecha os pipes do servidor numa thread à parte para nunca bloquear a UI."""
        self._outbox.put(None)

        def _close() -> None:
            for pipe in (proc.stdin, proc.stdout, proc.stderr):
                if pipe is not None:
                    with contextlib.suppress(OSError, ValueError):
                        pipe.close()

        threading.Thread(target=_close, daemon=True, name="lsp-close-pipes").start()

    @property
    def running(self) -> bool:
        """``True`` se o servidor está em execução e já respondeu ao initialize."""
        return (
            self._proc is not None and self._proc.poll() is None and self._initialized
        )

    @property
    def starting(self) -> bool:
        """``True`` se o processo do servidor está ativo mas o initialize ainda não concluiu."""
        return (
            self._proc is not None
            and self._proc.poll() is None
            and not self._initialized
        )

    def wait_initialized(self, timeout: float = 8.0) -> bool:
        """Aguarda initialize (bloqueante - usar apenas em threads de background)."""
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._initialized:
                return True
            if self._proc is None or self._proc.poll() is not None:
                return False
            time.sleep(0.05)
        return self._initialized

    def has_capability(self, *keys: str) -> bool:
        """Verifica se o servidor declarou uma capability aninhada."""
        node: Any = self._capabilities
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return False
            node = node[k]
        return bool(node)

    def did_open(
        self, path: Path, lang: str, content: str, *, diagnostics: bool = True
    ) -> None:
        """Notifica o servidor que um documento foi aberto (``textDocument/didOpen``)."""
        uri = _path_to_uri(path)
        lang_id = _LANG_ID_MAP.get(lang, lang)
        notif = {
            "method": "textDocument/didOpen",
            "params": {
                "textDocument": {
                    "uri": uri,
                    "languageId": lang_id,
                    "version": 1,
                    "text": content,
                }
            },
        }
        if not self._initialized:
            self._pre_init_queue.append(notif)
            self._open_docs[uri] = 1
            return
        self._open_docs[uri] = 1
        self._send_notification(notif["method"], notif["params"])

        if not diagnostics:
            with contextlib.suppress(Exception):
                self._send_notification(
                    "textDocument/didChange",
                    {"textDocument": {"uri": uri, "version": 1}, "contentChanges": []},
                )

    def did_change(self, path: Path, content: str) -> None:
        """Notifica o servidor sobre mudança de conteúdo (``textDocument/didChange``)."""
        if not self.running:
            return
        uri = _path_to_uri(path)
        version = self._open_docs.get(uri, 0) + 1
        self._open_docs[uri] = version
        self._send_notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": version},
                "contentChanges": [{"text": content}],
            },
        )

    def did_save(self, path: Path, content: str | None = None) -> None:
        """Notifica o servidor que o documento foi salvo (``textDocument/didSave``)."""
        if not self.running:
            return
        uri = _path_to_uri(path)
        params: dict[str, Any] = {"textDocument": {"uri": uri}}
        if content is not None:
            params["text"] = content
        self._send_notification("textDocument/didSave", params)

    def did_close(self, path: Path) -> None:
        """Notifica o servidor que o documento foi fechado (``textDocument/didClose``)."""
        if not self.running:
            return
        uri = _path_to_uri(path)
        self._open_docs.pop(uri, None)
        self._send_notification("textDocument/didClose",
                                {"textDocument": {"uri": uri}})

    def hover(
        self, path: Path, line: int, col: int, callback: Callable[[Hover | None], None]
    ) -> None:
        """Solicita hover info ao servidor e entrega o resultado via ``callback``."""
        if not self.running:
            callback(None)
            return
        self._request(
            "textDocument/hover",
            {
                "textDocument": {"uri": _path_to_uri(path)},
                "position": {"line": line, "character": col},
            },
            lambda r: callback(Hover.from_dict(r) if r else None),
        )

    def completion(
        self,
        path: Path,
        line: int,
        col: int,
        trigger: str | None,
        callback: Callable[[list[CompletionItem]], None],
    ) -> None:
        """Solicita itens de completion e entrega a lista via ``callback``."""
        if not self.running:
            callback([])
            return
        ctx: dict[str, Any] = (
            {"triggerKind": 2, "triggerCharacter": trigger}
            if trigger
            else {"triggerKind": 1}
        )
        self._request(
            "textDocument/completion",
            {
                "textDocument": {"uri": _path_to_uri(path)},
                "position": {"line": line, "character": col},
                "context": ctx,
            },
            lambda r: callback(self._parse_completion(r)),
        )

    def goto_definition(
        self,
        path: Path,
        line: int,
        col: int,
        callback: Callable[[list[Location]], None],
    ) -> None:
        """Solicita ao LSP a definição do símbolo na posição dada."""
        if not self.running:
            callback([])
            return
        self._request(
            "textDocument/definition",
            {
                "textDocument": {"uri": _path_to_uri(path)},
                "position": {"line": line, "character": col},
            },
            lambda r: callback(self._parse_locations(r)),
        )

    def goto_declaration(
        self,
        path: Path,
        line: int,
        col: int,
        callback: Callable[[list[Location]], None],
    ) -> None:
        """Solicita ao LSP a declaração do símbolo na posição dada."""
        if not self.running or not self.has_capability("declarationProvider"):
            callback([])
            return
        self._request(
            "textDocument/declaration",
            {
                "textDocument": {"uri": _path_to_uri(path)},
                "position": {"line": line, "character": col},
            },
            lambda r: callback(self._parse_locations(r)),
        )

    def goto_type_definition(
        self,
        path: Path,
        line: int,
        col: int,
        callback: Callable[[list[Location]], None],
    ) -> None:
        """Solicita ao LSP o tipo do símbolo na posição dada."""
        if not self.running or not self.has_capability("typeDefinitionProvider"):
            callback([])
            return
        self._request(
            "textDocument/typeDefinition",
            {
                "textDocument": {"uri": _path_to_uri(path)},
                "position": {"line": line, "character": col},
            },
            lambda r: callback(self._parse_locations(r)),
        )

    def goto_implementation(
        self,
        path: Path,
        line: int,
        col: int,
        callback: Callable[[list[Location]], None],
    ) -> None:
        """Solicita ao LSP as implementações do símbolo na posição dada."""
        if not self.running or not self.has_capability("implementationProvider"):
            callback([])
            return
        self._request(
            "textDocument/implementation",
            {
                "textDocument": {"uri": _path_to_uri(path)},
                "position": {"line": line, "character": col},
            },
            lambda r: callback(self._parse_locations(r)),
        )

    def find_references(
        self,
        path: Path,
        line: int,
        col: int,
        callback: Callable[[list[Location]], None],
    ) -> None:
        """Solicita ao LSP todas as referências do símbolo na posição dada."""
        if not self.running:
            callback([])
            return
        self._request(
            "textDocument/references",
            {
                "textDocument": {"uri": _path_to_uri(path)},
                "position": {"line": line, "character": col},
                "context": {"includeDeclaration": True},
            },
            lambda r: callback(self._parse_locations(r)),
        )

    def signature_help(
        self,
        path: Path,
        line: int,
        col: int,
        callback: Callable[[SignatureHelp | None], None],
    ) -> None:
        """Solicita ao LSP a assinatura da função na posição do cursor."""
        if not self.running:
            callback(None)
            return
        self._request(
            "textDocument/signatureHelp",
            {
                "textDocument": {"uri": _path_to_uri(path)},
                "position": {"line": line, "character": col},
            },
            lambda r: callback(SignatureHelp.from_dict(r) if r else None),
        )

    def document_symbols(
        self, path: Path, callback: Callable[[list[DocumentSymbol]], None]
    ) -> None:
        """Solicita ao LSP todos os símbolos do documento aberto."""
        if not self.running:
            callback([])
            return

        def _parse(r: Any) -> None:
            if not r:
                callback([])
                return
            callback([DocumentSymbol.from_dict(s) for s in r])

        self._request(
            "textDocument/documentSymbol",
            {"textDocument": {"uri": _path_to_uri(path)}},
            _parse,
        )

    def workspace_symbol(
        self, query: str, callback: Callable[[list[dict]], None]
    ) -> None:
        """Pesquisa símbolos no workspace inteiro com o termo fornecido."""
        if not self.running:
            callback([])
            return
        self._request("workspace/symbol",
                      {"query": query}, lambda r: callback(r or []))

    def formatting(
        self,
        path: Path,
        tab_size: int,
        insert_spaces: bool,
        callback: Callable[[list[TextEdit]], None],
    ) -> None:
        """Solicita ao LSP a formatação completa do documento."""
        if not self.running:
            callback([])
            return

        def _parse(r: Any) -> None:
            callback([TextEdit.from_dict(e) for e in r] if r else [])

        self._request(
            "textDocument/formatting",
            {
                "textDocument": {"uri": _path_to_uri(path)},
                "options": {
                    "tabSize": tab_size,
                    "insertSpaces": insert_spaces,
                    "trimTrailingWhitespace": True,
                    "insertFinalNewline": True,
                },
            },
            _parse,
        )

    def range_formatting(
        self,
        path: Path,
        rng: Range,
        tab_size: int,
        insert_spaces: bool,
        callback: Callable[[list[TextEdit]], None],
    ) -> None:
        """Solicita ao LSP a formatação de um intervalo do documento."""
        if not self.running or not self.has_capability(
            "documentRangeFormattingProvider"
        ):
            callback([])
            return

        def _parse(r: Any) -> None:
            callback([TextEdit.from_dict(e) for e in r] if r else [])

        self._request(
            "textDocument/rangeFormatting",
            {
                "textDocument": {"uri": _path_to_uri(path)},
                "range": rng.to_dict(),
                "options": {"tabSize": tab_size, "insertSpaces": insert_spaces},
            },
            _parse,
        )

    def prepare_rename(
        self,
        path: Path,
        line: int,
        col: int,
        callback: Callable[[PrepareRenameResult | None], None],
    ) -> None:
        """Verifica se o símbolo na posição pode ser renomeado via LSP."""
        if not self.running:
            callback(None)
            return
        self._request(
            "textDocument/prepareRename",
            {
                "textDocument": {"uri": _path_to_uri(path)},
                "position": {"line": line, "character": col},
            },
            lambda r: callback(
                PrepareRenameResult.from_dict(r) if r else None),
        )

    def rename(
        self,
        path: Path,
        line: int,
        col: int,
        new_name: str,
        callback: Callable[[WorkspaceEdit | None], None],
    ) -> None:
        """Solicita ao LSP a renomeação do símbolo para o novo nome."""
        if not self.running:
            callback(None)
            return
        self._request(
            "textDocument/rename",
            {
                "textDocument": {"uri": _path_to_uri(path)},
                "position": {"line": line, "character": col},
                "newName": new_name,
            },
            lambda r: callback(WorkspaceEdit.from_dict(r) if r else None),
        )

    def code_action(
        self,
        path: Path,
        rng: Range,
        diagnostics: list[dict],
        callback: Callable[[list[CodeAction]], None],
        only: list[str] | None = None,
    ) -> None:
        """Solicita code actions (quick fix, refactor, etc.) para o range."""
        if not self.running or not self.has_capability("codeActionProvider"):
            callback([])
            return
        params: dict[str, Any] = {
            "textDocument": {"uri": _path_to_uri(path)},
            "range": rng.to_dict(),
            "context": {"diagnostics": diagnostics},
        }
        if only:
            params["context"]["only"] = only

        def _parse(r: Any) -> None:
            if not r:
                callback([])
                return
            actions: list[CodeAction] = []
            for item in r:
                if isinstance(item, dict):
                    if "command" in item and "title" in item and "kind" not in item:
                        actions.append(
                            CodeAction(
                                title=item.get("title", ""),
                                kind="",
                                command=item,
                            )
                        )
                    else:
                        actions.append(CodeAction.from_dict(item))
            callback(actions)

        self._request("textDocument/codeAction", params, _parse)

    def document_highlight(
        self,
        path: Path,
        line: int,
        col: int,
        callback: Callable[[list[DocumentHighlight]], None],
    ) -> None:
        """Destaca todas as ocorrências do símbolo sob o cursor no documento."""
        if not self.running or not self.has_capability("documentHighlightProvider"):
            callback([])
            return

        def _parse(r: Any) -> None:
            callback([DocumentHighlight.from_dict(h) for h in r] if r else [])

        self._request(
            "textDocument/documentHighlight",
            {
                "textDocument": {"uri": _path_to_uri(path)},
                "position": {"line": line, "character": col},
            },
            _parse,
        )

    def folding_ranges(
        self, path: Path, callback: Callable[[list[FoldingRange]], None]
    ) -> None:
        """Solicita folding ranges via LSP (mais preciso que heurística regex)."""
        if not self.running or not self.has_capability("foldingRangeProvider"):
            callback([])
            return

        def _parse(r: Any) -> None:
            callback([FoldingRange.from_dict(f) for f in r] if r else [])

        self._request(
            "textDocument/foldingRange",
            {"textDocument": {"uri": _path_to_uri(path)}},
            _parse,
        )

    def inlay_hints(
        self, path: Path, rng: Range, callback: Callable[[list[InlayHint]], None]
    ) -> None:
        """Solicita inlay hints para o range visível do documento."""
        if not self.running or not self.has_capability("inlayHintProvider"):
            callback([])
            return

        def _parse(r: Any) -> None:
            callback([InlayHint.from_dict(h) for h in r] if r else [])

        self._request(
            "textDocument/inlayHint",
            {
                "textDocument": {"uri": _path_to_uri(path)},
                "range": rng.to_dict(),
            },
            _parse,
        )

    def semantic_tokens(
        self, path: Path, callback: Callable[[list[dict]], None]
    ) -> None:
        """Solicita semantic tokens completos (colorização semântica)."""
        if not self.running or not self.has_capability("semanticTokensProvider"):
            callback([])
            return
        types = self._semantic_legend.get("tokenTypes", [])
        mods = self._semantic_legend.get("tokenModifiers", [])

        def _parse(r: Any) -> None:
            if not r:
                callback([])
                return
            callback(SemanticTokens.from_dict(r).decode(types, mods))

        self._request(
            "textDocument/semanticTokens/full",
            {"textDocument": {"uri": _path_to_uri(path)}},
            _parse,
        )

    def prepare_call_hierarchy(
        self,
        path: Path,
        line: int,
        col: int,
        callback: Callable[[list[CallHierarchyItem]], None],
    ) -> None:
        """Prepara a hierarquia de chamadas do símbolo na posição dada."""
        if not self.running or not self.has_capability("callHierarchyProvider"):
            callback([])
            return

        def _parse(r: Any) -> None:
            callback([CallHierarchyItem.from_dict(i) for i in r] if r else [])

        self._request(
            "textDocument/prepareCallHierarchy",
            {
                "textDocument": {"uri": _path_to_uri(path)},
                "position": {"line": line, "character": col},
            },
            _parse,
        )

    def incoming_calls(
        self,
        item: CallHierarchyItem,
        callback: Callable[[list[CallHierarchyCall]], None],
    ) -> None:
        """Obtém as chamadas entrantes na hierarquia de chamadas LSP."""
        if not self.running:
            callback([])
            return
        raw = {
            "name": item.name,
            "kind": item.kind,
            "uri": item.uri,
            "range": item.range.to_dict(),
            "selectionRange": item.selection_range.to_dict(),
        }

        def _parse(r: Any) -> None:
            callback([CallHierarchyCall.from_incoming(c)
                     for c in r] if r else [])

        self._request("callHierarchy/incomingCalls", {"item": raw}, _parse)

    def outgoing_calls(
        self,
        item: CallHierarchyItem,
        callback: Callable[[list[CallHierarchyCall]], None],
    ) -> None:
        """Obtém as chamadas saintes na hierarquia de chamadas LSP."""
        if not self.running:
            callback([])
            return
        raw = {
            "name": item.name,
            "kind": item.kind,
            "uri": item.uri,
            "range": item.range.to_dict(),
            "selectionRange": item.selection_range.to_dict(),
        }

        def _parse(r: Any) -> None:
            callback([CallHierarchyCall.from_outgoing(c)
                     for c in r] if r else [])

        self._request("callHierarchy/outgoingCalls", {"item": raw}, _parse)

    def prepare_type_hierarchy(
        self,
        path: Path,
        line: int,
        col: int,
        callback: Callable[[list[TypeHierarchyItem]], None],
    ) -> None:
        """Prepara a hierarquia de tipos do símbolo na posição dada."""
        if not self.running or not self.has_capability("typeHierarchyProvider"):
            callback([])
            return

        def _parse(r: Any) -> None:
            callback([TypeHierarchyItem.from_dict(i) for i in r] if r else [])

        self._request(
            "textDocument/prepareTypeHierarchy",
            {
                "textDocument": {"uri": _path_to_uri(path)},
                "position": {"line": line, "character": col},
            },
            _parse,
        )

    def supertypes(
        self,
        item: TypeHierarchyItem,
        callback: Callable[[list[TypeHierarchyItem]], None],
    ) -> None:
        """Obtém os supertypes do item na hierarquia de tipos LSP."""
        if not self.running:
            callback([])
            return
        raw = {
            "name": item.name,
            "kind": item.kind,
            "uri": item.uri,
            "range": item.range.to_dict(),
            "selectionRange": item.selection_range.to_dict(),
        }

        def _parse(r: Any) -> None:
            callback([TypeHierarchyItem.from_dict(i) for i in r] if r else [])

        self._request("typeHierarchy/supertypes", {"item": raw}, _parse)

    def subtypes(
        self,
        item: TypeHierarchyItem,
        callback: Callable[[list[TypeHierarchyItem]], None],
    ) -> None:
        """Obtém os subtypes do item na hierarquia de tipos LSP."""
        if not self.running:
            callback([])
            return
        raw = {
            "name": item.name,
            "kind": item.kind,
            "uri": item.uri,
            "range": item.range.to_dict(),
            "selectionRange": item.selection_range.to_dict(),
        }

        def _parse(r: Any) -> None:
            callback([TypeHierarchyItem.from_dict(i) for i in r] if r else [])

        self._request("typeHierarchy/subtypes", {"item": raw}, _parse)

    def execute_command(
        self,
        command: str,
        arguments: list | None,
        callback: Callable[[Any], None] | None = None,
    ) -> None:
        """Executa um comando arbitrário do servidor."""
        if not self.running:
            if callback:
                callback(None)
            return
        params: dict[str, Any] = {"command": command}
        if arguments is not None:
            params["arguments"] = arguments
        self._request("workspace/executeCommand", params,
                      callback or (lambda _: None))

    def _next_id(self) -> int:
        with self._id_lock:
            self._id += 1
            return self._id

    def _send(self, data: dict) -> None:
        if not self._proc or self._proc.poll() is not None:
            return
        body = json.dumps(data, ensure_ascii=False)
        body_bytes = body.encode("utf-8")
        header = f"Content-Length: {len(body_bytes)}\r\n\r\n".encode()
        self._outbox.put(header + body_bytes)

    @staticmethod
    def _writer_loop(proc: subprocess.Popen, outbox: queue.Queue) -> None:
        """Grava as mensagens enfileiradas no stdin do servidor, em ordem.

        Roda em thread própria porque ``write`` bloqueia quando o servidor para
        de ler e o pipe enche, o que congelaria a interface se fosse feito nela.
        """
        while True:
            payload = outbox.get()
            if payload is None or proc.poll() is not None:
                return
            try:
                proc.stdin.write(payload)
                proc.stdin.flush()
            except (BrokenPipeError, AttributeError, OSError, ValueError):
                return

    def _request(self, method: str, params: Any, callback: Callable) -> int:
        rid = self._next_id()
        with self._pending_lock:
            self._pending[rid] = callback
        self._send(LSPMessage.request(rid, method, params))
        return rid

    def _send_notification(self, method: str, params: Any = None) -> None:
        self._send(LSPMessage.notification(method, params))

    def _send_initialize(self) -> None:
        """Envia a mensagem initialize com capabilities completas (LSP 3.17)."""
        root_uri = _path_to_uri(self.root_path) if self.root_path else None
        rid = self._next_id()
        with self._pending_lock:
            self._pending[rid] = self._on_initialized
        self._send(
            LSPMessage.request(
                rid,
                "initialize",
                {
                    "processId": os.getpid(),
                    "clientInfo": {"name": "Codis", "version": APP_VERSION},
                    "rootUri": root_uri,
                    "workspaceFolders": (
                        [
                            {
                                "uri": root_uri,
                                "name": self.root_path.name if self.root_path else "",
                            }
                        ]
                        if root_uri
                        else None
                    ),
                    "capabilities": {
                        "general": {
                            "positionEncodings": ["utf-16"],
                        },
                        "textDocument": {
                            "synchronization": {
                                "dynamicRegistration": False,
                                "willSave": False,
                                "willSaveWaitUntil": False,
                                "didSave": True,
                            },
                            "completion": {
                                "dynamicRegistration": False,
                                "completionItem": {
                                    "snippetSupport": True,
                                    "commitCharactersSupport": True,
                                    "documentationFormat": ["markdown", "plaintext"],
                                    "deprecatedSupport": True,
                                    "preselectSupport": True,
                                    "tagSupport": {"valueSet": [1]},
                                    "insertReplaceSupport": True,
                                    "resolveSupport": {
                                        "properties": [
                                            "documentation",
                                            "detail",
                                            "additionalTextEdits",
                                        ]
                                    },
                                    "labelDetailsSupport": True,
                                },
                                "completionItemKind": {"valueSet": list(range(1, 26))},
                                "contextSupport": True,
                                "completionList": {
                                    "itemDefaults": [
                                        "editRange",
                                        "insertTextFormat",
                                        "data",
                                    ]
                                },
                            },
                            "hover": {
                                "dynamicRegistration": False,
                                "contentFormat": ["markdown", "plaintext"],
                            },
                            "signatureHelp": {
                                "dynamicRegistration": False,
                                "signatureInformation": {
                                    "documentationFormat": ["markdown", "plaintext"],
                                    "parameterInformation": {
                                        "labelOffsetSupport": True
                                    },
                                    "activeParameterSupport": True,
                                },
                                "contextSupport": True,
                            },
                            "definition": {
                                "dynamicRegistration": False,
                                "linkSupport": True,
                            },
                            "declaration": {
                                "dynamicRegistration": False,
                                "linkSupport": True,
                            },
                            "typeDefinition": {
                                "dynamicRegistration": False,
                                "linkSupport": True,
                            },
                            "implementation": {
                                "dynamicRegistration": False,
                                "linkSupport": True,
                            },
                            "references": {"dynamicRegistration": False},
                            "documentHighlight": {"dynamicRegistration": False},
                            "documentSymbol": {
                                "dynamicRegistration": False,
                                "symbolKind": {"valueSet": list(range(1, 27))},
                                "hierarchicalDocumentSymbolSupport": True,
                                "tagSupport": {"valueSet": [1]},
                                "labelSupport": True,
                            },
                            "codeAction": {
                                "dynamicRegistration": False,
                                "codeActionLiteralSupport": {
                                    "codeActionKind": {
                                        "valueSet": [
                                            "",
                                            "quickfix",
                                            "refactor",
                                            "refactor.extract",
                                            "refactor.inline",
                                            "refactor.rewrite",
                                            "source",
                                            "source.organizeImports",
                                            "source.fixAll",
                                        ]
                                    },
                                },
                                "isPreferredSupport": True,
                                "resolveSupport": {"properties": ["edit"]},
                            },
                            "formatting": {"dynamicRegistration": False},
                            "rangeFormatting": {"dynamicRegistration": False},
                            "rename": {
                                "dynamicRegistration": False,
                                "prepareSupport": True,
                                "prepareSupportDefaultBehavior": 1,
                                "honorsChangeAnnotations": False,
                            },
                            "foldingRange": {
                                "dynamicRegistration": False,
                                "rangeLimit": 5000,
                                "lineFoldingOnly": True,
                            },
                            "semanticTokens": {
                                "dynamicRegistration": False,
                                "requests": {"full": True, "range": False},
                                "tokenTypes": [
                                    "namespace",
                                    "type",
                                    "class",
                                    "enum",
                                    "interface",
                                    "struct",
                                    "typeParameter",
                                    "parameter",
                                    "variable",
                                    "property",
                                    "enumMember",
                                    "event",
                                    "function",
                                    "method",
                                    "macro",
                                    "keyword",
                                    "modifier",
                                    "comment",
                                    "string",
                                    "number",
                                    "regexp",
                                    "operator",
                                    "decorator",
                                ],
                                "tokenModifiers": [
                                    "declaration",
                                    "definition",
                                    "readonly",
                                    "static",
                                    "deprecated",
                                    "abstract",
                                    "async",
                                    "modification",
                                    "documentation",
                                    "defaultLibrary",
                                ],
                                "formats": ["relative"],
                                "overlappingTokenSupport": False,
                                "multilineTokenSupport": False,
                            },
                            "inlayHint": {
                                "dynamicRegistration": False,
                                "resolveSupport": {
                                    "properties": [
                                        "tooltip",
                                        "textEdits",
                                        "label.tooltip",
                                        "label.location",
                                    ]
                                },
                            },
                            "callHierarchy": {"dynamicRegistration": False},
                            "typeHierarchy": {"dynamicRegistration": False},
                            "linkedEditingRange": {"dynamicRegistration": False},
                            "publishDiagnostics": {
                                "relatedInformation": True,
                                "versionSupport": True,
                                "tagSupport": {"valueSet": [1, 2]},
                                "codeDescriptionSupport": True,
                                "dataSupport": True,
                            },
                        },
                        "workspace": {
                            "applyEdit": True,
                            "workspaceEdit": {
                                "documentChanges": True,
                                "resourceOperations": ["create", "rename", "delete"],
                                "failureHandling": "textOnlyTransactional",
                                "normalizesLineEndings": True,
                            },
                            "symbol": {
                                "dynamicRegistration": False,
                                "symbolKind": {"valueSet": list(range(1, 27))},
                                "tagSupport": {"valueSet": [1]},
                                "resolveSupport": {"properties": ["location.range"]},
                            },
                            "executeCommand": {"dynamicRegistration": False},
                            "configuration": True,
                            "workspaceFolders": True,
                            "semanticTokens": {"refreshSupport": True},
                            "inlayHint": {"refreshSupport": True},
                        },
                        "window": {
                            "showMessage": {
                                "messageActionItem": {
                                    "additionalPropertiesSupport": True
                                }
                            },
                            "showDocument": {"support": False},
                            "workDoneProgress": False,
                        },
                    },
                },
            )
        )

    def _on_initialized(self, result: dict) -> None:
        self._capabilities = result.get("capabilities", {})

        st_prov = self._capabilities.get("semanticTokensProvider", {})
        legend = st_prov.get("legend", {}) if isinstance(st_prov, dict) else {}
        if legend:
            self._semantic_legend = legend
        self._initialized = True
        self._send_notification("initialized", {})
        logger.debug(
            "LSP: '%s' inicializado. caps=%s",
            self.lang,
            list(self._capabilities.keys()),
        )
        for notif in self._pre_init_queue:
            self._send_notification(notif["method"], notif["params"])
        self._pre_init_queue.clear()

    def _reader_loop(self) -> None:
        buf = b""
        while not self._shutdown:
            if not self._proc or self._proc.poll() is not None:
                break
            try:
                chunk = self._proc.stdout.read1(65536)
                if not chunk:
                    break
                buf += chunk
                while True:
                    sep = buf.find(b"\r\n\r\n")
                    if sep == -1:
                        break
                    header = buf[:sep]
                    m = re.search(rb"Content-Length:\s*(\d+)", header)
                    if not m:
                        buf = buf[sep + 4:]
                        continue
                    length = int(m.group(1))
                    if length > _MAX_MESSAGE_BYTES:
                        logger.warning("LSP[%s]: mensagem de %d bytes descartada", self.lang, length)
                        self._shutdown = True
                        return
                    body_start = sep + 4
                    body_end = body_start + length
                    if len(buf) < body_end:
                        break
                    body = buf[body_start:body_end]
                    buf = buf[body_end:]
                    try:
                        msg = json.loads(body.decode(
                            "utf-8", errors="replace"))
                        self._dispatch(msg)
                    except json.JSONDecodeError:
                        pass
            except (OSError, EOFError) as exc:
                logger.debug("LSP[%s] reader encerrado: %s", self.lang, exc)
                break

    def _stderr_loop(self) -> None:
        while not self._shutdown:
            if not self._proc or self._proc.poll() is not None:
                break
            try:
                line = self._proc.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    logger.debug("LSP[%s] stderr: %s", self.lang, text)
                    if self._on_log and not self._shutdown:
                        with contextlib.suppress(tk.TclError, AttributeError):
                            self._tk.after(0, lambda t=text: self._on_log(t))
            except (OSError, EOFError) as exc:
                logger.debug("LSP[%s] stderr encerrado: %s", self.lang, exc)
                break

    def _dispatch(self, msg: dict) -> None:
        if "id" in msg and "method" not in msg:
            rid = msg["id"]
            with self._pending_lock:
                cb = self._pending.pop(rid, None)
            if cb:
                result = msg.get("result")
                error = msg.get("error")
                if error:
                    logger.debug("LSP[%s] erro %s: %s", self.lang, rid, error)
                    result = None
                if not self._shutdown:
                    with contextlib.suppress(tk.TclError):
                        self._tk.after(0, lambda r=result, c=cb: c(r))
        elif "method" in msg:
            self._handle_notification(msg)

    def _handle_notification(self, msg: dict) -> None:
        method = msg["method"]
        params = msg.get("params", {})
        req_id = msg.get("id")

        if method == "textDocument/publishDiagnostics":
            self._handle_diagnostics(params)
        elif method in ("window/logMessage", "$/logTrace"):
            text = params.get("message", "")
            if text and self._on_log and not self._shutdown:
                with contextlib.suppress(tk.TclError):
                    self._tk.after(0, lambda t=text: self._on_log(t))
        elif method == "window/showMessage":
            logger.info("LSP[%s] msg: %s", self.lang,
                        params.get("message", ""))
        elif method == "window/showMessageRequest" and req_id is not None:
            actions = params.get("actions", [])
            self._send(LSPMessage.response(
                req_id, actions[0] if actions else None))
        elif (
            method == "client/registerCapability"
            and req_id is not None
            or method == "client/unregisterCapability"
            and req_id is not None
        ):
            self._send(LSPMessage.response(req_id, {}))
        elif method == "workspace/configuration" and req_id is not None:
            items = params.get("items", [])
            self._send(LSPMessage.response(req_id, [None] * len(items)))
        elif method == "workspace/workspaceFolders" and req_id is not None:
            root_uri = _path_to_uri(self.root_path) if self.root_path else None
            folders = (
                [{"uri": root_uri, "name": self.root_path.name}] if root_uri else []
            )
            self._send(LSPMessage.response(req_id, folders))
        elif method == "workspace/applyEdit" and req_id is not None:
            edit_data = params.get("edit", {})
            applied = False
            if self._on_apply_edit and edit_data and not self._shutdown:
                try:
                    we = WorkspaceEdit.from_dict(edit_data)
                    with contextlib.suppress(tk.TclError):
                        self._tk.after(0, lambda w=we: self._on_apply_edit(w))
                    applied = True
                except Exception as exc:
                    logger.debug("workspace/applyEdit handler error: %s", exc)
            self._send(LSPMessage.response(req_id, {"applied": applied}))
        elif method in ("$/progress", "window/workDoneProgress/create"):
            if req_id is not None:
                self._send(LSPMessage.response(req_id, None))
        elif (
            method == "workspace/semanticTokens/refresh"
            and req_id is not None
            or method == "workspace/inlayHint/refresh"
            and req_id is not None
        ):
            self._send(LSPMessage.response(req_id, None))

    def _handle_diagnostics(self, params: dict) -> None:
        if not self._on_diags or self._shutdown:
            return
        uri = params.get("uri", "")
        diags = [LSPDiag.from_dict(d) for d in params.get("diagnostics", [])]
        with contextlib.suppress(tk.TclError):
            self._tk.after(0, lambda u=uri, d=diags: self._on_diags(u, d))

    @staticmethod
    def _parse_completion(result: Any) -> list[CompletionItem]:
        if result is None:
            return []
        items = result if isinstance(result, list) else result.get("items", [])
        return [CompletionItem.from_dict(i) for i in items if isinstance(i, dict)]

    @staticmethod
    def _parse_locations(result: Any) -> list[Location]:
        if result is None:
            return []
        if isinstance(result, dict):
            result = [result]
        elif not isinstance(result, list):
            return []
        out: list[Location] = []
        for item in result:
            if not isinstance(item, dict):
                continue
            if "targetUri" in item:
                link = LocationLink.from_dict(item)
                out.append(
                    Location(uri=link.target_uri,
                             range=link.target_selection_range)
                )
            else:
                out.append(Location.from_dict(item))
        return out


class LSPManager:
    """
    Gerencia múltiplos LSPClient (um por linguagem).
    API unificada usada por EditorPane e CodisApp.
    Suporta enable/disable global e por linguagem.
    """

    def __init__(
        self,
        tk_root: tk.Misc,
        root_path: Path | None = None,
        on_diagnostics: Callable[[str, str, list], None] | None = None,
        on_log: Callable[[str, str], None] | None = None,
        on_apply_edit: Callable[[WorkspaceEdit], None] | None = None,
    ) -> None:
        """Inicializa ``LSPManager``."""
        self._tk = tk_root
        self._root_path = root_path
        self._on_diags = on_diagnostics
        self._on_log = on_log
        self._on_apply_edit = on_apply_edit
        self._clients: dict[str, LSPClient] = {}
        self._enabled = True
        self._disabled_langs: set[str] = set()

    def set_root(self, path: Path | None) -> None:
        """Define o diretório raiz do workspace para todos os servidores LSP."""
        self._root_path = path
        for c in list(self._clients.values()):
            c.stop()
        self._clients.clear()

    def set_enabled(self, enabled: bool) -> None:
        """Habilita ou desabilita o cliente LSP, iniciando ou parando servidores."""
        self._enabled = enabled
        if not enabled:
            self.stop_all()

    def enable_lang(self, lang: str) -> LSPClient | None:
        """Habilita LSP manualmente para uma linguagem e inicia o servidor."""
        self._disabled_langs.discard(lang)
        return self.client_for(lang)

    def disable_lang(self, lang: str) -> None:
        """Desabilita LSP para uma linguagem específica e para o servidor."""
        self._disabled_langs.add(lang)
        if lang in self._clients:
            self._clients[lang].stop()
            del self._clients[lang]

    def is_lang_enabled(self, lang: str) -> bool:
        """Retorna ``True`` se LSP está globalmente habilitado e a linguagem não foi desabilitada."""
        return self._enabled and lang not in self._disabled_langs

    def stop_all(self, timeout: float = 3.0) -> None:
        """Para todos os servidores LSP em paralelo, com prazo total de ``timeout`` s.

        Servidores que não saírem no prazo têm a árvore de processos morta, de
        modo que o encerramento do app nunca fique preso esperando por eles.
        """
        clients = list(self._clients.items())
        self._clients.clear()

        def _stop_one(lang: str, client: LSPClient) -> None:
            try:
                client.stop(grace=min(1.0, timeout))
            except Exception as exc:
                logger.debug("LSPManager: erro ao parar '%s': %s", lang, exc)

        workers = [
            threading.Thread(target=_stop_one, args=item, daemon=True, name=f"lsp-stop-{item[0]}")
            for item in clients
        ]
        for worker in workers:
            worker.start()
        deadline = time.monotonic() + timeout
        for worker in workers:
            worker.join(max(0.0, deadline - time.monotonic()))
        for _, client in clients:
            proc = client._proc
            if proc is not None and proc.poll() is None:
                _kill_process_tree(proc)

    def restart_lang(self, lang: str) -> LSPClient | None:
        """Reinicia o servidor para uma linguagem."""
        if lang in self._clients:
            self._clients[lang].stop()
            del self._clients[lang]
        return self.client_for(lang)

    def client_for(self, lang: str) -> LSPClient | None:
        """
        Retorna o LSPClient para a linguagem, iniciando-o se necessário.
        Retorna None se LSP estiver desabilitado global ou por linguagem.
        """
        if not self._enabled or lang in self._disabled_langs:
            return None
        if lang in self._clients:
            c = self._clients[lang]
            if c._proc is not None and c._proc.poll() is None:
                return c
            c.start()
            if c._proc is not None and c._proc.poll() is None:
                return c
            return None

        def _on_diag(uri: str, diags: list) -> None:
            if self._on_diags:
                self._on_diags(lang, uri, diags)

        def _on_log_cb(msg: str) -> None:
            if self._on_log:
                self._on_log(lang, msg)

        c = LSPClient(
            lang=lang,
            root_path=self._root_path,
            tk_root=self._tk,
            on_diagnostics=_on_diag,
            on_log=_on_log_cb,
            on_apply_edit=self._on_apply_edit,
        )
        self._clients[lang] = c
        return c if c.start() else None

    @staticmethod
    def available_for(lang: str) -> bool:
        """Verifica (sem iniciar) se há servidor disponível para a linguagem."""
        return _find_server(lang) is not None

    @staticmethod
    def install_hint(lang: str) -> str:
        """Retorna o comando de instalação sugerido para o servidor da linguagem."""
        return _INSTALL_HINTS.get(lang, "")

    def status(self) -> dict[str, str]:
        """Retorna {lang: 'running'|'starting'|'stopped'|'available'|'disabled'|'unavailable'}."""
        result: dict[str, str] = {}
        for lang in _SERVER_CANDIDATES:
            if lang in self._disabled_langs:
                result[lang] = "disabled"
            elif lang in self._clients:
                c = self._clients[lang]
                if c.running:
                    result[lang] = "running"
                elif c.starting:
                    result[lang] = "starting"
                else:
                    result[lang] = "stopped"
            elif _find_server(lang):
                result[lang] = "available"
            else:
                result[lang] = "unavailable"
        return result

    def did_open(
        self, path: Path, lang: str, content: str, *, diagnostics: bool = True
    ) -> None:
        """Notifica o LSP que um documento foi aberto."""
        c = self.client_for(lang)
        if c:
            c.did_open(path, lang, content, diagnostics=diagnostics)

    def did_change(self, path: Path, lang: str, content: str) -> None:
        """Notifica o LSP sobre mudanças incrementais no documento."""
        c = self.client_for(lang)
        if c:
            c.did_change(path, content)

    def did_save(self, path: Path, lang: str, content: str | None = None) -> None:
        """Notifica o LSP que o documento foi salvo."""
        c = self.client_for(lang)
        if c:
            c.did_save(path, content)

    def did_close(self, path: Path, lang: str) -> None:
        """Notifica o LSP que o documento foi fechado."""
        c = self.client_for(lang)
        if c:
            c.did_close(path)

    def hover(
        self,
        path: Path,
        lang: str,
        line: int,
        col: int,
        callback: Callable[[Hover | None], None],
    ) -> None:
        """Solicita ao LSP informações de hover para a posição dada."""
        c = self.client_for(lang)
        if c:
            c.hover(path, line, col, callback)
        else:
            callback(None)

    def completion(
        self,
        path: Path,
        lang: str,
        line: int,
        col: int,
        trigger: str | None,
        callback: Callable[[list[CompletionItem]], None],
    ) -> None:
        """Solicita ao LSP sugestões de autocompletar na posição dada."""
        c = self.client_for(lang)
        if c:
            c.completion(path, line, col, trigger, callback)
        else:
            callback([])

    def goto_definition(
        self,
        path: Path,
        lang: str,
        line: int,
        col: int,
        callback: Callable[[list[Location]], None],
    ) -> None:
        """Solicita ao LSP a definição do símbolo na posição dada."""
        c = self.client_for(lang)
        if c:
            c.goto_definition(path, line, col, callback)
        else:
            callback([])

    def goto_declaration(
        self,
        path: Path,
        lang: str,
        line: int,
        col: int,
        callback: Callable[[list[Location]], None],
    ) -> None:
        """Solicita ao LSP a declaração do símbolo na posição dada."""
        c = self.client_for(lang)
        if c:
            c.goto_declaration(path, line, col, callback)
        else:
            callback([])

    def goto_type_definition(
        self,
        path: Path,
        lang: str,
        line: int,
        col: int,
        callback: Callable[[list[Location]], None],
    ) -> None:
        """Solicita ao LSP o tipo do símbolo na posição dada."""
        c = self.client_for(lang)
        if c:
            c.goto_type_definition(path, line, col, callback)
        else:
            callback([])

    def goto_implementation(
        self,
        path: Path,
        lang: str,
        line: int,
        col: int,
        callback: Callable[[list[Location]], None],
    ) -> None:
        """Solicita ao LSP as implementações do símbolo na posição dada."""
        c = self.client_for(lang)
        if c:
            c.goto_implementation(path, line, col, callback)
        else:
            callback([])

    def find_references(
        self,
        path: Path,
        lang: str,
        line: int,
        col: int,
        callback: Callable[[list[Location]], None],
    ) -> None:
        """Solicita ao LSP todas as referências do símbolo na posição dada."""
        c = self.client_for(lang)
        if c:
            c.find_references(path, line, col, callback)
        else:
            callback([])

    def signature_help(
        self,
        path: Path,
        lang: str,
        line: int,
        col: int,
        callback: Callable[[SignatureHelp | None], None],
    ) -> None:
        """Solicita ao LSP a assinatura da função na posição do cursor."""
        c = self.client_for(lang)
        if c:
            c.signature_help(path, line, col, callback)
        else:
            callback(None)

    def document_symbols(
        self, path: Path, lang: str, callback: Callable[[list[DocumentSymbol]], None]
    ) -> None:
        """Solicita ao LSP todos os símbolos do documento aberto."""
        c = self.client_for(lang)
        if c:
            c.document_symbols(path, callback)
        else:
            callback([])

    def workspace_symbol(
        self, query: str, callback: Callable[[list[dict]], None]
    ) -> None:
        """Pesquisa símbolos no workspace inteiro com o termo fornecido."""
        for c in self._clients.values():
            if c.running:
                c.workspace_symbol(query, callback)
                return
        callback([])

    def formatting(
        self,
        path: Path,
        lang: str,
        tab_size: int,
        insert_spaces: bool,
        callback: Callable[[list[TextEdit]], None],
    ) -> None:
        """Solicita ao LSP a formatação completa do documento."""
        c = self.client_for(lang)
        if c:
            c.formatting(path, tab_size, insert_spaces, callback)
        else:
            callback([])

    def range_formatting(
        self,
        path: Path,
        lang: str,
        rng: Range,
        tab_size: int,
        insert_spaces: bool,
        callback: Callable[[list[TextEdit]], None],
    ) -> None:
        """Solicita ao LSP a formatação de um intervalo do documento."""
        c = self.client_for(lang)
        if c:
            c.range_formatting(path, rng, tab_size, insert_spaces, callback)
        else:
            callback([])

    def prepare_rename(
        self,
        path: Path,
        lang: str,
        line: int,
        col: int,
        callback: Callable[[PrepareRenameResult | None], None],
    ) -> None:
        """Verifica se o símbolo na posição pode ser renomeado via LSP."""
        c = self.client_for(lang)
        if c:
            c.prepare_rename(path, line, col, callback)
        else:
            callback(None)

    def rename(
        self,
        path: Path,
        lang: str,
        line: int,
        col: int,
        new_name: str,
        callback: Callable[[WorkspaceEdit | None], None],
    ) -> None:
        """Solicita ao LSP a renomeação do símbolo para o novo nome."""
        c = self.client_for(lang)
        if c:
            c.rename(path, line, col, new_name, callback)
        else:
            callback(None)

    def code_action(
        self,
        path: Path,
        lang: str,
        rng: Range,
        diagnostics: list[dict],
        callback: Callable[[list[CodeAction]], None],
        only: list[str] | None = None,
    ) -> None:
        """Solicita ao LSP code actions disponíveis no intervalo dado."""
        c = self.client_for(lang)
        if c:
            c.code_action(path, rng, diagnostics, callback, only)
        else:
            callback([])

    def document_highlight(
        self,
        path: Path,
        lang: str,
        line: int,
        col: int,
        callback: Callable[[list[DocumentHighlight]], None],
    ) -> None:
        """Solicita ao LSP os realces de ocorrências do símbolo."""
        c = self.client_for(lang)
        if c:
            c.document_highlight(path, line, col, callback)
        else:
            callback([])

    def folding_ranges(
        self, path: Path, lang: str, callback: Callable[[list[FoldingRange]], None]
    ) -> None:
        """Solicita ao LSP as regiões de folding do documento."""
        c = self.client_for(lang)
        if c:
            c.folding_ranges(path, callback)
        else:
            callback([])

    def inlay_hints(
        self,
        path: Path,
        lang: str,
        rng: Range,
        callback: Callable[[list[InlayHint]], None],
    ) -> None:
        """Solicita ao LSP os inlay hints do intervalo especificado."""
        c = self.client_for(lang)
        if c:
            c.inlay_hints(path, rng, callback)
        else:
            callback([])

    def semantic_tokens(
        self, path: Path, lang: str, callback: Callable[[list[dict]], None]
    ) -> None:
        """Solicita ao LSP os tokens semânticos do documento."""
        c = self.client_for(lang)
        if c:
            c.semantic_tokens(path, callback)
        else:
            callback([])

    def prepare_call_hierarchy(
        self,
        path: Path,
        lang: str,
        line: int,
        col: int,
        callback: Callable[[list[CallHierarchyItem]], None],
    ) -> None:
        """Prepara a hierarquia de chamadas do símbolo na posição dada."""
        c = self.client_for(lang)
        if c:
            c.prepare_call_hierarchy(path, line, col, callback)
        else:
            callback([])

    def prepare_type_hierarchy(
        self,
        path: Path,
        lang: str,
        line: int,
        col: int,
        callback: Callable[[list[TypeHierarchyItem]], None],
    ) -> None:
        """Prepara a hierarquia de tipos do símbolo na posição dada."""
        c = self.client_for(lang)
        if c:
            c.prepare_type_hierarchy(path, line, col, callback)
        else:
            callback([])

    def execute_command(
        self,
        lang: str,
        command: str,
        arguments: list | None = None,
        callback: Callable[[Any], None] | None = None,
    ) -> None:
        """Executa um comando registrado no servidor LSP."""
        c = self.client_for(lang)
        if c:
            c.execute_command(command, arguments, callback)
        elif callback:
            callback(None)
