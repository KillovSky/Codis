"""Testes de :mod:`src.ui.webdev` (servidor de preview, CDP, descoberta de navegador)."""

from __future__ import annotations

import http.client
import json
import threading
import time
from pathlib import Path

import pytest

from src.ui import webdev as wd


def _get(port, path, host=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("GET", path, headers={"Host": host} if host else {})
    r = c.getresponse()
    return r.status, dict(r.getheaders()), r.read()


@pytest.fixture()
def site(tmp_path):
    (tmp_path / "index.html").write_text("<html><body><h1>oi</h1></body></html>", encoding="utf-8")
    (tmp_path / "app.js").write_text("export const a = 1;", encoding="utf-8")
    (tmp_path / "style.css").write_text("h1{color:red}", encoding="utf-8")
    (tmp_path / "doc.md").write_text("# Titulo", encoding="utf-8")
    (tmp_path.parent / "segredo.txt").write_text("nao pode vazar", encoding="utf-8")
    srv = wd.DevServer(tmp_path, md_renderer=lambda t, n: f"<html><body><p>MD:{t}</p></body></html>",
                       watch_interval=0.05)
    port = srv.start()
    yield srv, port, tmp_path
    srv.stop()


def test_serves_files_with_correct_mime_and_no_cache(site):
    _, port, _ = site
    st, h, body = _get(port, "/app.js")
    assert st == 200 and h["Content-Type"].startswith("text/javascript")
    assert h["Cache-Control"] == "no-store"
    assert _get(port, "/style.css")[1]["Content-Type"].startswith("text/css")


def test_html_gets_live_reload_script_and_index_fallback(site):
    _, port, _ = site
    for p in ("/index.html", "/"):
        st, _, body = _get(port, p)
        assert st == 200 and b"/__codis/reload.js" in body and b"<h1>oi</h1>" in body
    assert b"setInterval" in _get(port, "/__codis/reload.js")[2]


def test_markdown_rendered(site):
    _, port, _ = site
    st, h, body = _get(port, "/doc.md")
    assert st == 200 and b"MD:# Titulo" in body and h["Content-Type"].startswith("text/html")


def test_path_traversal_blocked(site):
    _, port, _ = site
    for p in ("/../segredo.txt", "/%2e%2e/segredo.txt", "/..%2fsegredo.txt"):
        st, _, body = _get(port, p)
        assert b"nao pode vazar" not in body and st in (403, 404)


def test_dns_rebinding_host_rejected(site):
    _, port, _ = site
    assert _get(port, "/index.html", host="evil.example.com")[0] == 403
    assert _get(port, "/index.html", host=f"localhost:{port}")[0] == 200


def test_404(site):
    assert _get(site[1], "/nope.html")[0] == 404


def test_overlay_serves_unsaved_buffer_and_bumps_version(site):
    srv, port, root = site
    v0 = srv.version
    srv.set_overlay(root / "index.html", "<html><body>NAO SALVO</body></html>")
    assert srv.version == v0 + 1
    assert b"NAO SALVO" in _get(port, "/index.html")[2]
    srv.set_overlay(root / "index.html", "<html><body>NAO SALVO</body></html>")
    assert srv.version == v0 + 1
    srv.set_overlay(root / "index.html", None)
    assert b"<h1>oi</h1>" in _get(port, "/index.html")[2]


def test_disk_change_detected_and_css_reported(site):
    srv, port, root = site
    v0 = srv.version
    time.sleep(0.1)
    (root / "style.css").write_text("h1{color:blue}", encoding="utf-8")
    import os
    os.utime(root / "style.css", (time.time() + 5, time.time() + 5))
    for _ in range(40):
        if srv.version > v0:
            break
        time.sleep(0.05)
    st = json.loads(_get(port, "/__codis/poll")[2])
    assert st["v"] > v0 and "style.css" in st["changed"]


def test_url_mapping_roundtrip(site):
    srv, port, root = site
    url = srv.url_for(root / "index.html")
    assert url == f"http://127.0.0.1:{port}/index.html"
    assert srv.path_for_url(url) == (root / "index.html").resolve()
    assert srv.url_for(root.parent / "segredo.txt") is None
    assert srv.path_for_url("http://example.com/x") is None


def test_find_browser_windows_and_posix(tmp_path):
    edge = tmp_path / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    edge.parent.mkdir(parents=True)
    edge.write_text("")
    env = {"ProgramFiles(x86)": str(tmp_path)}
    assert wd.find_browser(env=env, platform="win32", which=lambda n: None) == edge
    assert wd.find_browser(env={}, platform="win32", which=lambda n: None) is None
    assert wd.find_browser(env={}, platform="linux", which=lambda n: "/usr/bin/chromium" if n == "chromium" else None) == Path("/usr/bin/chromium")
    assert wd.find_browser(env={}, platform="darwin", which=lambda n: "/x") is None
    assert wd.find_browser(configured=str(edge), env={}, platform="linux", which=lambda n: None) == edge


def test_cdp_client_against_fake_devtools(tmp_path):
    """Sobe um 'Chromium' falso (HTTP /json/list + WebSocket) e confere comandos/eventos."""
    from websockets.sync.server import serve

    received: list[dict] = []
    ws_ready = threading.Event()

    def handler(ws):
        ws_ready.set()
        ws.send(json.dumps({"method": "Runtime.consoleAPICalled", "params": {
            "type": "warning", "args": [{"type": "string", "value": "cuidado"}, {"type": "number", "value": 3}],
            "stackTrace": {"callFrames": [{"url": "http://x/a.js", "lineNumber": 9, "columnNumber": 4}]}}}))
        ws.send(json.dumps({"method": "Runtime.exceptionThrown", "params": {"exceptionDetails": {
            "text": "Uncaught", "lineNumber": 1, "columnNumber": 2, "url": "http://x/b.js",
            "exception": {"description": "ReferenceError: foo is not defined\n    at b.js:2"}}}}))
        ws.send(json.dumps({"method": "Page.frameNavigated", "params": {"frame": {"url": "http://x/p.html"}}}))
        ws.send(json.dumps({"method": "Page.frameNavigated", "params": {"frame": {"url": "http://x/iframe", "parentId": "1"}}}))
        try:
            for raw in ws:
                msg = json.loads(raw)
                received.append(msg)
                ws.send(json.dumps({"id": msg["id"], "result": {"ok": msg["method"]}}))
        except Exception:
            pass

    with serve(handler, "127.0.0.1", 0) as ws_srv:
        ws_port = ws_srv.socket.getsockname()[1]
        threading.Thread(target=ws_srv.serve_forever, daemon=True).start()

        class Http(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps([{"type": "page", "webSocketDebuggerUrl": f"ws://127.0.0.1:{ws_port}/devtools/page/1"}]).encode()
                self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers()
                self.wfile.write(body)
            def log_message(self, *a): pass

        http_srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Http)
        threading.Thread(target=http_srv.serve_forever, daemon=True).start()
        (tmp_path / "DevToolsActivePort").write_text(f"{http_srv.server_address[1]}\n/devtools/browser/abc\n")

        entries, navs = [], []
        cdp = wd.CdpClient(tmp_path, entries.append, navs.append)
        cdp.connect_async(timeout=5)
        for _ in range(60):
            if cdp.connected and len(entries) >= 2 and navs:
                break
            time.sleep(0.1)
        assert cdp.connected
        assert [(e.level, e.text, e.line) for e in entries[:2]] == [
            ("warn", "cuidado 3", 10), ("error", "ReferenceError: foo is not defined", 2)]
        assert entries[1].url == "http://x/b.js"
        assert navs == ["http://x/p.html"]

        assert cdp.call("Page.reload", {"ignoreCache": True}) == {"ok": "Page.reload"}
        cdp.emulate((390, 844, 3.0, True))
        cdp.navigate("http://x/n.html")
        time.sleep(0.4)
        methods = [m["method"] for m in received]
        assert methods[:4] == ["Runtime.enable", "Log.enable", "Page.enable", "Network.enable"]
        assert "Emulation.setDeviceMetricsOverride" in methods and "Page.navigate" in methods
        cdp.close()
        http_srv.shutdown()


import http.server  # noqa: E402
