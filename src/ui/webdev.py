"""Codis - infraestrutura do Preview Web (sem API key, sem serviços externos).

Três peças independentes de Tk (testáveis sozinhas):

* :func:`find_browser` - localiza um navegador Chromium instalado
  (Edge, Chrome, Chromium, Brave...). No Windows o Edge já vem no sistema.
* :class:`DevServer` - servidor HTTP **local (127.0.0.1)** para a pasta do
  projeto, com *live reload* (troca CSS sem recarregar a página), preview de
  Markdown, MIME correto para ``.js``/``.mjs``/``.wasm`` e **buffers não
  salvos** do editor (você vê o que digita antes de salvar).
* :class:`CdpClient` - cliente mínimo do *Chrome DevTools Protocol*: navegar,
  recarregar, emular dispositivo e receber console/erros/requisições
  falhas para mostrar no Codis.

O navegador roda como processo próprio e sua janela é incorporada ao editor
por :mod:`src.ui.window_embed`; por isso HTML, CSS e JavaScript têm o suporte
**completo** de um navegador de verdade (flexbox, grid, módulos ES, fetch,
WebGL, DevTools com F12...), e não de um renderizador parcial.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import sys
import threading
import time
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

logger = logging.getLogger("Codis.Web")

__all__ = [
    "CdpClient",
    "ConsoleEntry",
    "DevServer",
    "DEVICE_PRESETS",
    "find_browser",
    "is_previewable",
]

PREVIEW_EXTS = frozenset({".html", ".htm", ".xhtml", ".md", ".markdown", ".svg"})


def is_previewable(path: str | os.PathLike[str] | None) -> bool:
    """True se ``path`` é HTML/Markdown/SVG."""
    return bool(path) and Path(str(path)).suffix.lower() in PREVIEW_EXTS


_WIN_CANDIDATES = (
    (r"Microsoft\Edge\Application\msedge.exe", "edge"),
    (r"Google\Chrome\Application\chrome.exe", "chrome"),
    (r"BraveSoftware\Brave-Browser\Application\brave.exe", "brave"),
    (r"Chromium\Application\chrome.exe", "chromium"),
    (r"Vivaldi\Application\vivaldi.exe", "vivaldi"),
)
_POSIX_NAMES = (
    "google-chrome-stable", "google-chrome", "chromium", "chromium-browser",
    "microsoft-edge-stable", "microsoft-edge", "brave-browser", "brave", "vivaldi",
)


def find_browser(
    configured: str | None = None,
    env: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
    platform: str | None = None,
) -> Path | None:
    """Localiza um navegador baseado em Chromium (necessário para ``--app`` e CDP).

    Ordem: caminho configurado (``web_browser_path`` / ``CODIS_WEB_BROWSER``)
    -> instalações padrão (Windows) -> PATH. Firefox/Safari não servem aqui
    (não falam CDP nem têm modo ``--app``).
    """
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    for explicit in (configured, env.get("CODIS_WEB_BROWSER")):
        if explicit and Path(explicit).expanduser().is_file():
            return Path(explicit).expanduser()
    if platform == "win32":
        bases = [env.get(k) for k in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
        for rel, _name in _WIN_CANDIDATES:
            for base in filter(None, bases):
                cand = Path(base, *rel.split("\\"))
                if cand.is_file():
                    return cand
        for exe in ("msedge", "chrome", "brave"):
            found = which(exe)
            if found:
                return Path(found)
        return None
    if platform == "darwin":
        return None
    for name in _POSIX_NAMES:
        found = which(name)
        if found:
            return Path(found)
    return None


_MIME = {
    ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
    ".cjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8", ".htm": "text/html; charset=utf-8",
    ".xhtml": "application/xhtml+xml", ".svg": "image/svg+xml", ".json": "application/json",
    ".map": "application/json", ".wasm": "application/wasm", ".webmanifest": "application/manifest+json",
    ".md": "text/markdown; charset=utf-8", ".txt": "text/plain; charset=utf-8",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
    ".webp": "image/webp", ".avif": "image/avif", ".ico": "image/x-icon",
    ".woff": "font/woff", ".woff2": "font/woff2", ".ttf": "font/ttf", ".otf": "font/otf",
    ".mp4": "video/mp4", ".webm": "video/webm", ".mp3": "audio/mpeg", ".ogg": "audio/ogg",
    ".wav": "audio/wav", ".pdf": "application/pdf", ".xml": "application/xml",
}
_WATCH_EXTS = frozenset(
    {".html", ".htm", ".css", ".js", ".mjs", ".cjs", ".json", ".svg", ".md", ".markdown",
     ".xhtml", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".woff", ".woff2", ".wasm"}
)
_SKIP_DIRS = frozenset({".git", "node_modules", "__pycache__", ".venv", "venv", ".idea", ".vscode", "dist-cache"})
_MAX_WATCH_FILES = 4000

_RELOAD_JS = r"""(function(){
  var v=null, busy=false;
  function swapCss(){
    var links=document.querySelectorAll('link[rel~="stylesheet"]'), t=Date.now();
    links.forEach(function(l){
      try{var u=new URL(l.href,location.href); if(u.origin!==location.origin) return;
        u.searchParams.set('__codis',t);
        var n=l.cloneNode(); n.href=u.toString();
        n.onload=function(){l.remove()}; n.onerror=function(){n.remove()}; l.after(n);}catch(e){}
    });
  }
  function tick(){
    if(busy) return; busy=true;
    fetch('/__codis/poll?v='+(v===null?'':v),{cache:'no-store'}).then(function(r){return r.json()})
    .then(function(d){
      if(v!==null && d.v!==v){
        var onlyCss=d.changed.length>0 && d.changed.every(function(p){return /\.css$/i.test(p)});
        if(onlyCss) swapCss(); else location.reload();
      }
      v=d.v;
    }).catch(function(){}).then(function(){busy=false});
  }
  setInterval(tick,500); tick();
})();"""


class DevServer:
    """Servidor local de preview para uma pasta (``root``).

    Args:
        root: Pasta servida (nada fora dela é acessível).
        md_renderer: ``(markdown_text, title) -> html`` para arquivos ``.md``.
        live_reload: Injeta o script de recarga automática em páginas HTML.
        watch_interval: Segundos entre varreduras de mtime.
    """

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        md_renderer: Callable[[str, str], str] | None = None,
        live_reload: bool = True,
        watch_interval: float = 0.4,
    ) -> None:
        """Prepara o servidor (use :meth:`start`)."""
        self.root = Path(root).resolve()
        self._md = md_renderer
        self._live = live_reload
        self._interval = watch_interval
        self._overlay: dict[Path, str] = {}
        self._lock = threading.Lock()
        self._version = 0
        self._changed: list[str] = []
        self._mtimes: dict[Path, float] = {}
        self._httpd: ThreadingHTTPServer | None = None
        self._stop = threading.Event()
        self.port = 0

    def start(self) -> int:
        """Inicia o servidor em ``127.0.0.1:<porta livre>``; devolve a porta."""
        if self._httpd is not None:
            return self.port
        server = self

        class Handler(_Handler):
            dev = server

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._httpd.daemon_threads = True
        self.port = int(self._httpd.server_address[1])
        self._scan(initial=True)
        threading.Thread(target=self._httpd.serve_forever, name="CodisDevHTTP", daemon=True).start()
        threading.Thread(target=self._watch, name="CodisDevWatch", daemon=True).start()
        return self.port

    def stop(self) -> None:
        """Encerra o servidor e a varredura."""
        self._stop.set()
        httpd, self._httpd = self._httpd, None
        if httpd is not None:
            with contextlib.suppress(OSError):
                httpd.shutdown()
                httpd.server_close()

    def url_for(self, path: str | os.PathLike[str]) -> str | None:
        """URL http do arquivo (``None`` se estiver fora de ``root``)."""
        try:
            rel = Path(path).resolve().relative_to(self.root)
        except (ValueError, OSError):
            return None
        from urllib.parse import quote

        return f"http://127.0.0.1:{self.port}/{quote(rel.as_posix())}"

    def path_for_url(self, url: str) -> Path | None:
        """Arquivo local correspondente a uma URL deste servidor (para "ir ao erro")."""
        parts = urlsplit(url)
        if parts.hostname not in ("127.0.0.1", "localhost") or parts.port != self.port:
            return None
        return self._safe_path(parts.path)

    def _safe_path(self, url_path: str) -> Path | None:
        rel = unquote(url_path).lstrip("/")
        try:
            target = (self.root / rel).resolve()
            target.relative_to(self.root)
        except (ValueError, OSError):
            return None
        return target

    def set_overlay(self, path: str | os.PathLike[str], text: str | None) -> None:
        """Serve ``text`` no lugar do arquivo em disco (``None`` remove o overlay)."""
        p = Path(path).resolve()
        with self._lock:
            if text is None:
                if self._overlay.pop(p, None) is None:
                    return
            elif self._overlay.get(p) == text:
                return
            else:
                self._overlay[p] = text
            self._bump([self._rel(p)])

    def _rel(self, p: Path) -> str:
        try:
            return p.relative_to(self.root).as_posix()
        except ValueError:
            return p.name

    def overlay_for(self, p: Path) -> str | None:
        """Texto em memória para ``p`` (ou ``None``)."""
        with self._lock:
            return self._overlay.get(p)

    @property
    def version(self) -> int:
        """Contador incrementado a cada mudança observada."""
        return self._version

    def state(self) -> dict[str, Any]:
        """Estado enviado ao script de recarga."""
        with self._lock:
            return {"v": self._version, "changed": list(self._changed)}

    def _bump(self, changed: list[str]) -> None:
        self._version += 1
        self._changed = changed[:50]

    def _scan(self, *, initial: bool = False) -> None:
        seen: dict[Path, float] = {}
        count = 0
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
            for name in filenames:
                if os.path.splitext(name)[1].lower() not in _WATCH_EXTS:
                    continue
                full = Path(dirpath, name)
                try:
                    seen[full] = full.stat().st_mtime
                except OSError:
                    continue
                count += 1
                if count >= _MAX_WATCH_FILES:
                    break
            if count >= _MAX_WATCH_FILES:
                break
        if not initial:
            changed = [self._rel(p) for p, m in seen.items() if self._mtimes.get(p) != m]
            changed += [self._rel(p) for p in self._mtimes if p not in seen]
            if changed:
                with self._lock:
                    self._bump(changed)
        self._mtimes = seen

    def _watch(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self._scan()
            except Exception as exc:  # noqa: BLE001
                logger.debug("varredura falhou: %s", exc)

    def render(self, path: Path) -> tuple[bytes, str] | None:
        """Corpo e Content-Type de ``path`` (overlay > disco; Markdown -> HTML)."""
        suffix = path.suffix.lower()
        text = self.overlay_for(path)
        if text is None:
            if not path.is_file():
                return None
            if suffix not in (".md", ".markdown", ".html", ".htm", ".xhtml"):
                return path.read_bytes(), _MIME.get(suffix, "application/octet-stream")
            text = path.read_text(encoding="utf-8", errors="replace")
        if suffix in (".md", ".markdown"):
            html = self._md(text, path.stem) if self._md else f"<pre>{_esc(text)}</pre>"
            return self._inject(html).encode("utf-8"), _MIME[".html"]
        if suffix in (".html", ".htm", ".xhtml"):
            return self._inject(text).encode("utf-8"), _MIME[suffix]
        return text.encode("utf-8"), _MIME.get(suffix, "text/plain; charset=utf-8")

    def _inject(self, html: str) -> str:
        if not self._live:
            return html
        tag = '<script src="/__codis/reload.js"></script>'
        low = html.lower()
        i = low.rfind("</body>")
        return html[:i] + tag + html[i:] if i != -1 else html + tag


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class _Handler(BaseHTTPRequestHandler):
    """Handler GET/HEAD: somente leitura, somente loopback."""

    dev: DevServer
    server_version = "CodisDev/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, *_: Any) -> None:
        """Sem logs no console."""

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        return host in ("127.0.0.1", "localhost", "[::1]")

    def _send(self, code: int, body: bytes, ctype: str, head: bool = False) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def do_HEAD(self) -> None:  # noqa: N802
        """HEAD = GET sem corpo."""
        self._serve(head=True)

    def do_GET(self) -> None:  # noqa: N802
        """Serve arquivos, o script de recarga e o estado de versão."""
        self._serve(head=False)

    def _serve(self, *, head: bool) -> None:
        if not self._host_ok():
            self._send(403, b"forbidden", "text/plain", head)
            return
        parts = urlsplit(self.path)
        if parts.path == "/__codis/reload.js":
            self._send(200, _RELOAD_JS.encode(), "text/javascript; charset=utf-8", head)
            return
        if parts.path == "/__codis/poll":
            parse_qs(parts.query)
            self._send(200, json.dumps(self.dev.state()).encode(), "application/json", head)
            return
        target = self.dev._safe_path(parts.path)
        if target is None:
            self._send(403, b"forbidden", "text/plain", head)
            return
        if target.is_dir():
            idx = next((target / n for n in ("index.html", "index.htm") if (target / n).is_file()), None)
            if idx is None:
                self._send(200, _listing(self.dev, target).encode(), _MIME[".html"], head)
                return
            target = idx
        try:
            out = self.dev.render(target)
        except OSError:
            out = None
        if out is None:
            self._send(404, b"404 - nao encontrado", "text/plain; charset=utf-8", head)
            return
        self._send(200, out[0], out[1], head)


def _listing(dev: DevServer, folder: Path) -> str:
    from urllib.parse import quote

    rel = folder.relative_to(dev.root).as_posix()
    rows = []
    try:
        entries = sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError:
        entries = []
    for p in entries:
        if p.name.startswith("."):
            continue
        href = quote((Path(rel) / p.name).as_posix() if rel != "." else p.name) + ("/" if p.is_dir() else "")
        rows.append(f'<li><a href="/{href}">{_esc(p.name)}{"/" if p.is_dir() else ""}</a></li>')
    title = _esc("/" + rel if rel != "." else "/")
    return (
        f"<!doctype html><meta charset=utf-8><title>{title}</title>"
        "<style>body{font:15px system-ui;margin:2rem}li{line-height:1.8}</style>"
        f"<h3>{title}</h3><ul>{''.join(rows)}</ul>"
    )


DEVICE_PRESETS: dict[str, tuple[int, int, float, bool] | None] = {
    "Responsivo (janela)": None,
    "Desktop 1440": (1440, 900, 1.0, False),
    "Laptop 1280": (1280, 720, 1.0, False),
    "Tablet 820": (820, 1180, 2.0, True),
    "Mobile 390": (390, 844, 3.0, True),
    "Mobile pequeno 320": (320, 568, 2.0, True),
}


@dataclass
class ConsoleEntry:
    """Mensagem de console/erro vinda do navegador."""

    level: str
    text: str
    url: str = ""
    line: int = 0
    col: int = 0
    ts: float = field(default_factory=time.time)


class CdpClient:
    """Cliente CDP mínimo sobre WebSocket (biblioteca ``websockets``, já dependência do Codis).

    Todos os callbacks são chamados em **thread de fundo**: quem usa Tk deve
    repassar à thread principal (o painel faz isso com uma fila + ``after``).

    Args:
        profile_dir: ``--user-data-dir`` usado no lançamento (contém ``DevToolsActivePort``).
        on_console: callback de :class:`ConsoleEntry`.
        on_navigated: callback ``(url)`` quando o frame principal navega.
    """

    def __init__(
        self,
        profile_dir: Path,
        on_console: Callable[[ConsoleEntry], None] | None = None,
        on_navigated: Callable[[str], None] | None = None,
    ) -> None:
        """Cria o cliente (use :meth:`connect_async`)."""
        self.profile_dir = Path(profile_dir)
        self._on_console = on_console
        self._on_navigated = on_navigated
        self._ws: Any = None
        self._next_id = 0
        self._pending: dict[int, tuple[threading.Event, list[Any]]] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._connected = threading.Event()
        self.port = 0

    @property
    def connected(self) -> bool:
        """True com o WebSocket aberto."""
        return self._connected.is_set()

    def connect_async(self, timeout: float = 20.0) -> None:
        """Aguarda o navegador publicar a porta e conecta (em thread de fundo)."""
        threading.Thread(target=self._run, args=(timeout,), name="CodisCDP", daemon=True).start()

    def close(self) -> None:
        """Fecha a conexão."""
        self._stop.set()
        ws, self._ws = self._ws, None
        if ws is not None:
            with contextlib.suppress(Exception):
                ws.close()
        self._connected.clear()

    def navigate(self, url: str) -> None:
        """Navega a aba."""
        self.send("Page.navigate", {"url": url})

    def reload(self, *, ignore_cache: bool = True) -> None:
        """Recarrega a página."""
        self.send("Page.reload", {"ignoreCache": ignore_cache})

    def back(self) -> None:
        """Voltar no histórico."""
        self.send("Runtime.evaluate", {"expression": "history.back()"})

    def forward(self) -> None:
        """Avançar no histórico."""
        self.send("Runtime.evaluate", {"expression": "history.forward()"})

    def evaluate(self, expression: str) -> None:
        """Executa JavaScript na página."""
        self.send("Runtime.evaluate", {"expression": expression, "userGesture": True})

    def emulate(self, preset: tuple[int, int, float, bool] | None) -> None:
        """Aplica (ou remove, com ``None``) a emulação de dispositivo."""
        if preset is None:
            self.send("Emulation.clearDeviceMetricsOverride", {})
            self.send("Emulation.setTouchEmulationEnabled", {"enabled": False})
            return
        w, h, scale, mobile = preset
        self.send("Emulation.setDeviceMetricsOverride",
                  {"width": w, "height": h, "deviceScaleFactor": scale, "mobile": mobile})
        self.send("Emulation.setTouchEmulationEnabled", {"enabled": mobile})

    def send(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Envia comando sem esperar resposta (ignora se desconectado)."""
        ws = self._ws
        if ws is None:
            return
        with self._lock:
            self._next_id += 1
            msg = {"id": self._next_id, "method": method, "params": params or {}}
        with contextlib.suppress(Exception):
            ws.send(json.dumps(msg))

    def call(self, method: str, params: dict[str, Any] | None = None, timeout: float = 5.0) -> Any:
        """Envia e espera a resposta (``None`` em erro/timeout)."""
        ws = self._ws
        if ws is None:
            return None
        ev, box = threading.Event(), []
        with self._lock:
            self._next_id += 1
            mid = self._next_id
            self._pending[mid] = (ev, box)
        try:
            ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        except Exception:  # noqa: BLE001
            self._pending.pop(mid, None)
            return None
        ev.wait(timeout)
        self._pending.pop(mid, None)
        return box[0] if box else None

    def _discover_ws_url(self, deadline: float) -> str | None:
        port_file = self.profile_dir / "DevToolsActivePort"
        while time.monotonic() < deadline and not self._stop.is_set():
            try:
                self.port = int(port_file.read_text().splitlines()[0])
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/list", timeout=2) as r:  # noqa: S310
                    targets = json.loads(r.read().decode("utf-8"))
                for tgt in targets:
                    if tgt.get("type") == "page" and tgt.get("webSocketDebuggerUrl"):
                        return str(tgt["webSocketDebuggerUrl"])
            except (OSError, ValueError, IndexError):
                pass
            time.sleep(0.25)
        return None

    def _run(self, timeout: float) -> None:
        try:
            from websockets.sync.client import connect
        except ImportError:
            logger.warning("websockets>=11 ausente: console/navegação do preview indisponíveis")
            return
        url = self._discover_ws_url(time.monotonic() + timeout)
        if url is None:
            logger.info("CDP: porta de depuração não apareceu")
            return
        try:
            with connect(url, max_size=None, open_timeout=5, ping_interval=None) as ws:
                self._ws = ws
                self._connected.set()
                for m in ("Runtime.enable", "Log.enable", "Page.enable", "Network.enable"):
                    self.send(m)
                while not self._stop.is_set():
                    self._dispatch(json.loads(ws.recv()))
        except Exception as exc:  # noqa: BLE001
            logger.debug("CDP encerrado: %s", exc)
        finally:
            self._ws = None
            self._connected.clear()

    def _dispatch(self, msg: dict[str, Any]) -> None:
        mid = msg.get("id")
        if mid is not None:
            pend = self._pending.get(mid)
            if pend:
                pend[1].append(msg.get("result"))
                pend[0].set()
            return
        method, p = msg.get("method", ""), msg.get("params", {})
        if method == "Runtime.consoleAPICalled" and self._on_console:
            level = {"warning": "warn", "assert": "error", "trace": "debug"}.get(p.get("type", "log"), p.get("type", "log"))
            text = " ".join(_remote_str(a) for a in p.get("args", []))
            frame = (p.get("stackTrace", {}).get("callFrames") or [{}])[0]
            self._on_console(ConsoleEntry(level, text, frame.get("url", ""), frame.get("lineNumber", 0) + 1,
                                          frame.get("columnNumber", 0) + 1))
        elif method == "Runtime.exceptionThrown" and self._on_console:
            d = p.get("exceptionDetails", {})
            text = (d.get("exception", {}) or {}).get("description") or d.get("text", "Erro")
            self._on_console(ConsoleEntry("error", text.split("\n")[0], d.get("url", ""),
                                          d.get("lineNumber", 0) + 1, d.get("columnNumber", 0) + 1))
        elif method == "Log.entryAdded" and self._on_console:
            e = p.get("entry", {})
            lvl = {"warning": "warn", "verbose": "debug"}.get(e.get("level", "info"), e.get("level", "info"))
            self._on_console(ConsoleEntry(lvl, e.get("text", ""), e.get("url", ""), e.get("lineNumber", 0)))
        elif method == "Network.loadingFailed" and self._on_console and not p.get("canceled"):
            self._on_console(ConsoleEntry("error", f"Falha ao carregar recurso: {p.get('errorText', '')}"))
        elif method == "Page.frameNavigated" and self._on_navigated:
            fr = p.get("frame", {})
            if not fr.get("parentId"):
                self._on_navigated(fr.get("url", ""))


def _remote_str(arg: dict[str, Any]) -> str:
    if "value" in arg:
        v = arg["value"]
        return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return str(arg.get("description") or arg.get("unserializableValue") or arg.get("type", ""))
