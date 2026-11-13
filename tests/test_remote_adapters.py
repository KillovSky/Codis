"""Testes dos adaptadores remotos: parsers puros + servidores reais em threads.

SSH real só roda se ``CODIS_TEST_SSH="host:porta:usuario:senha[:caminho_chave]"``
estiver definido (ver tests/manual/README). FTP/WebDAV usam pyftpdlib/wsgidav
quando instalados (``pip install pyftpdlib wsgidav cheroot``) e são pulados
caso contrário.
"""

from __future__ import annotations

import os
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from src.ui import remote_adapters as ra

enc = ra.RemoteProfile.encode_password


def prof(proto: str, port: int, **kw) -> ra.RemoteProfile:
    return ra.RemoteProfile(name="t", protocol=proto, host="127.0.0.1", port=port, **kw)


LS_LONG_ISO = """total 24
drwxr-xr-x 4 u u 4096 2026-10-23 21:50 .
drwxr-x--- 4 u u 4096 2026-10-23 21:50 ..
-rw-rw-r-- 1 u u    6 2026-10-23 21:50 a.txt
-rw-rw-r-- 1 u u    4 2026-10-23 21:50 2024
drwxrwxr-x 2 u u 4096 2026-10-23 21:50 dir with space
lrwxrwxrwx 1 u u   12 2026-10-23 21:50 broken -> /nonexistent
"""
LS_GNU_DEFAULT = """total 8
-rw-r--r-- 1 root root  120 Oct 23 21:50 notes.md
-rw-r--r-- 1 root root  999 Jan  3  2021 old file.log
"""
LS_BUSYBOX = "drwxr-xr-x    2 root     root          4096 Oct 23 21:50 etc\n"
LS_DEVICE = "brw-rw---- 1 root disk 8, 0 Oct 23 21:50 sda\n"


def test_ls_long_iso_does_not_drop_files():
    names = {e.name: e for e in ra.parse_ls_output(LS_LONG_ISO)}
    assert set(names) == {"a.txt", "2024", "dir with space", "broken"}
    assert names["a.txt"].size == 6 and not names["a.txt"].is_dir
    assert names["dir with space"].is_dir
    assert names["broken"].is_link and names["broken"].link_target == "/nonexistent"
    assert names["a.txt"].modified == "2026-10-23 21:50"


def test_ls_gnu_default_and_year_format():
    names = {e.name: e for e in ra.parse_ls_output(LS_GNU_DEFAULT)}
    assert set(names) == {"notes.md", "old file.log"}
    assert names["old file.log"].modified == "Jan 3 2021"


def test_ls_busybox_and_device_sizes():
    assert [e.name for e in ra.parse_ls_output(LS_BUSYBOX)] == ["etc"]
    dev = ra.parse_ls_output(LS_DEVICE)[0]
    assert dev.name == "sda" and dev.size is None


def test_ls_ignores_total_and_garbage():
    assert ra.parse_ls_output("total 0\nls: cannot access x\n") == []


def test_ftp_list_unix_and_dos():
    unix = ["drwxr-xr-x 2 ftp ftp 4096 Oct 23 21:50 pub", "-rw-r--r-- 1 ftp ftp 10 Oct 23 21:50 my file.txt"]
    dos = ["10-23-26  09:15AM       <DIR>          Docs", "10-23-26  09:15AM                 1234 report final.pdf"]
    u = {e.name: e for e in ra.parse_ftp_list(unix)}
    assert u["pub"].is_dir and u["my file.txt"].size == 10
    d = {e.name: e for e in ra.parse_ftp_list(dos)}
    assert d["Docs"].is_dir and d["report final.pdf"].size == 1234


APACHE = """<html><body><table>
<tr><th><a href="?C=N;O=D">Name</a></th><th><a href="?C=M;O=A">Last modified</a></th></tr>
<tr><td><a href="/pub/">Parent Directory</a></td><td>&nbsp;</td></tr>
<tr><td><a href="docs/">docs/</a></td><td align="right">2026-10-23 10:11  </td><td align="right">  - </td></tr>
<tr><td><a href="my%20file.txt">my file.txt</a></td><td align="right">2026-10-23 10:12  </td><td align="right">1.5K</td></tr>
<tr><td><a href="https://evil.example/x">x</a></td></tr>
</table></body></html>"""
NGINX = """<pre><a href="../">../</a>
<a href="src/">src/</a>                      23-Oct-2026 10:11                   -
<a href="app.py">app.py</a>                    23-Oct-2026 10:12                 340
</pre>"""


def test_http_index_apache_table():
    es = {e.name: e for e in ra.parse_http_index(APACHE, "http://h/pub/data/")}
    assert set(es) == {"docs", "my file.txt"}
    assert es["docs"].is_dir and es["my file.txt"].size == 1536
    assert es["my file.txt"].modified == "2026-10-23 10:12"


def test_http_index_nginx_pre():
    es = {e.name: e for e in ra.parse_http_index(NGINX, "http://h/code/")}
    assert set(es) == {"src", "app.py"}
    assert es["app.py"].size == 340 and es["src"].is_dir


def test_http_index_ignores_deeper_and_foreign_links():
    body = '<a href="a/b/c.txt">deep</a><a href="/other/x">other</a><a href="ok.txt">ok</a>'
    assert [e.name for e in ra.parse_http_index(body, "http://h/base/")] == ["ok.txt"]


def test_parse_remote_url():
    r = ra.parse_remote_url("sftp://ana:p%40ss@host.io:2200/var/www")
    assert r == {
        "protocol": "sftp", "host": "host.io", "port": 2200, "user": "ana",
        "password": "p@ss", "remote_root": "/var/www",
    }  # fmt: skip
    assert ra.parse_remote_url("davs://x.org/dav")["protocol"] == "webdavs"
    assert ra.parse_remote_url("ftp://h")["port"] == 21
    assert ra.parse_remote_url("mailto:a@b") is None
    assert ra.parse_remote_url("not a url") is None


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("Makefile", "text"), ("Dockerfile", "text"), (".gitignore", "text"),
        ("x.PY", "text"), ("pic.PNG", "image"), ("a.ipynb", "notebook"),
        ("d.csv", "csv"), ("a.zip", "binary"), ("noext", "unknown"),
    ],
)  # fmt: skip
def test_classify(name, kind):
    assert ra.classify(name) == kind


def test_looks_binary(tmp_path: Path):
    (tmp_path / "t").write_text("olá")
    (tmp_path / "b").write_bytes(b"ab\x00cd")
    assert not ra.looks_binary(tmp_path / "t")
    assert ra.looks_binary(tmp_path / "b")
    assert ra.looks_binary(tmp_path / "missing")


def test_paths_are_posix_regardless_of_os():
    assert ra.parent_path("/home/u/proj") == "/home/u"
    assert ra.parent_path("/home") == "/"
    assert ra.parent_path("/") == "/"
    assert ra.join_path("/a/b", "c d") == "/a/b/c d"
    assert ra.join_path("/", "x") == "/x"


def test_format_and_parse_size():
    assert ra.format_size(None) == "" and ra.format_size(512) == "512 B"
    assert ra.format_size(1536) == "1.5 KB"
    assert ra.parse_human_size("1.5K") == 1536 and ra.parse_human_size("-") is None


def test_profile_rejects_option_injection():
    for bad in ("-oProxyCommand=x", "a b", "a\nb"):
        with pytest.raises(ValueError):
            ra.RemoteProfile(name="x", host=bad)
    with pytest.raises(ValueError):
        ra.RemoteProfile(name="x", host="h", user="-x")


def test_password_roundtrip_and_legacy_plaintext():
    p = prof("ssh", 22, password=enc("s3nh@ ç"))
    assert p.decoded_password() == "s3nh@ ç"
    assert prof("ssh", 22, password="plain pw!").decoded_password() == "plain pw!"


def test_ssh_quoting_helpers():
    q = ra.SSHAdapter._quote_remote_path
    assert q("~") == "~" and q("~/a b") == "~/'a b'" and q("/x y") == "'/x y'"
    assert ra.SSHAdapter._sftp_quote('a"b\\c') == '"a\\"b\\\\c"'
    with pytest.raises(ValueError):
        ra.SSHAdapter._sftp_quote("a\nput x y")


def test_ssh_password_beats_agent_and_askpass_without_sshpass(monkeypatch):
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/x")
    a = ra.SSHAdapter(prof("ssh", 22, password=enc("pw")))
    a._sshpass = None
    assert a._auth() == "askpass" and a._uses_askpass()
    env = a._env()
    assert env["CODIS_SSH_PW"] == "pw" and env["SSH_ASKPASS_REQUIRE"] == "force"
    assert "pw" not in " ".join(a._argv("ssh"))
    a.close()


def test_ssh_agent_only_when_no_password(monkeypatch):
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/x")
    assert ra.SSHAdapter(prof("ssh", 22))._auth() == "agent"
    monkeypatch.delenv("SSH_AUTH_SOCK")
    monkeypatch.delenv("SSH_AGENT_PID", raising=False)
    assert ra.SSHAdapter(prof("ssh", 22))._auth() == "none"


def test_make_adapter_dispatch():
    assert isinstance(ra.make_adapter(prof("sftp", 22)), ra.SSHAdapter)
    assert isinstance(ra.make_adapter(prof("ftps", 21)), ra.FTPAdapter)
    assert isinstance(ra.make_adapter(prof("webdav", 80)), ra.WebDAVAdapter)
    assert isinstance(ra.make_adapter(prof("https", 443)), ra.HTTPAdapter)
    assert ra.make_adapter(prof("https", 443)).read_only


@pytest.fixture(scope="module")
def http_server(tmp_path_factory):
    root = tmp_path_factory.mktemp("www")
    (root / "docs").mkdir()
    (root / "docs" / "guia final.md").write_text("# oi ✓\n", encoding="utf-8")
    (root / "a.txt").write_text("hello\n")

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(root), **k)

        def log_message(self, *a):
            return None

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1], root
    srv.shutdown()


def test_http_list_download_and_errors(http_server, tmp_path):
    port, _root = http_server
    a = ra.HTTPAdapter(prof("http", port))
    assert a.connect()[0]
    es = {e.name: e for e in a.listdir("/")[0]}
    assert set(es) == {"docs", "a.txt"} and es["docs"].is_dir
    sub = {e.name for e in a.listdir("/docs")[0]}
    assert sub == {"guia final.md"}
    ok, _ = a.download("/docs/guia final.md", tmp_path / "g.md")
    assert ok and (tmp_path / "g.md").read_text(encoding="utf-8") == "# oi ✓\n"
    ok, msg = a.download("/nao-existe", tmp_path / "x")
    assert not ok and "404" in msg and not (tmp_path / "x").exists()
    assert not a.listdir("/nao-existe/")[1] == ""
    assert not a.upload(tmp_path / "g.md", "/x")[0]


def test_http_connect_failure_message():
    ok, msg = ra.HTTPAdapter(prof("http", 1, timeout=3)).connect()
    assert not ok and msg


def test_http_respects_remote_root(http_server):
    port, _ = http_server
    a = ra.HTTPAdapter(prof("http", port, remote_root="/docs"))
    assert a.home == "/docs"
    assert {e.name for e in a.listdir(a.home)[0]} == {"guia final.md"}


@pytest.fixture(scope="module")
def ftp_server(tmp_path_factory):
    pytest.importorskip("pyftpdlib")
    from pyftpdlib.authorizers import DummyAuthorizer
    from pyftpdlib.handlers import FTPHandler
    from pyftpdlib.servers import ThreadedFTPServer

    root = tmp_path_factory.mktemp("ftp")
    (root / "pub dir").mkdir()
    (root / "pub dir" / "my file.txt").write_text("conteúdo\n", encoding="utf-8")
    (root / "a.txt").write_text("abc")
    auth = DummyAuthorizer()
    auth.add_user("u", "pw", str(root), perm="elradfmwMT")

    class H(FTPHandler):
        authorizer = auth

    srv = ThreadedFTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.socket.getsockname()[1], root, srv
    srv.close_all()


def test_ftp_full_cycle(ftp_server, tmp_path):
    port, root, _ = ftp_server
    a = ra.FTPAdapter(prof("ftp", port, user="u", password=enc("pw")))
    assert a.connect()[0] and a.home == "/"
    es = {e.name: e for e in a.listdir("/")[0]}
    assert es["pub dir"].is_dir and es["a.txt"].size == 3
    ok, _ = a.download("/pub dir/my file.txt", tmp_path / "o.txt")
    assert ok and (tmp_path / "o.txt").read_text(encoding="utf-8") == "conteúdo\n"
    assert not list(tmp_path.glob("*.codis-part"))
    (tmp_path / "up.txt").write_text("novo")
    seen: list[int] = []
    assert a.upload(tmp_path / "up.txt", "/pub dir/up.txt", progress=lambda d, t: seen.append(d))[0]
    assert (root / "pub dir" / "up.txt").read_text() == "novo" and seen
    assert a.mkdir("/novo")[0] and (root / "novo").is_dir()
    assert a.rename("/novo", "/renomeado")[0] and (root / "renomeado").is_dir()
    assert a.remove("/renomeado", True)[0] and not (root / "renomeado").exists()
    assert a.remove("/pub dir/up.txt")[0]
    ok, msg = a.download("/nada.txt", tmp_path / "n")
    assert not ok and msg and not (tmp_path / "n").exists()
    assert a.listdir("/nao/existe")[1] != ""
    a.close()


def test_ftp_bad_login_and_refused():
    ok, msg = ra.FTPAdapter(prof("ftp", 1, timeout=2)).connect()
    assert not ok and msg


def test_ftp_wrong_password(ftp_server):
    port, _, _ = ftp_server
    ok, msg = ra.FTPAdapter(prof("ftp", port, user="u", password=enc("errada"))).connect()
    assert not ok and msg


def test_ftp_reconnects_after_connection_dropped(ftp_server):
    port, _, _ = ftp_server
    a = ra.FTPAdapter(prof("ftp", port, user="u", password=enc("pw")))
    assert a.connect()[0]
    a._ftp.sock.close()
    entries, err = a.listdir("/")
    assert not err and entries, "deveria reconectar sozinho"
    a.close()


def test_ftp_is_thread_safe(ftp_server):
    port, _, _ = ftp_server
    a = ra.FTPAdapter(prof("ftp", port, user="u", password=enc("pw")))
    assert a.connect()[0]
    errors: list[str] = []

    def work():
        for _ in range(8):
            es, err = a.listdir("/")
            if err or len(es) < 2:
                errors.append(err or "lista curta")

    ts = [threading.Thread(target=work) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    a.close()
    assert not errors, errors[:3]


@pytest.fixture(scope="module")
def dav_server(tmp_path_factory):
    pytest.importorskip("wsgidav")
    cheroot_wsgi = pytest.importorskip("cheroot.wsgi")
    from wsgidav.wsgidav_app import WsgiDAVApp

    root = tmp_path_factory.mktemp("dav")
    (root / "pasta x").mkdir()
    (root / "pasta x" / "arq 1.txt").write_text("um")
    (root / "raiz.txt").write_text("raiz!")
    app = WsgiDAVApp(
        {
            "provider_mapping": {"/dav": str(root)},
            "simple_dc": {"user_mapping": {"*": True}},
            "http_authenticator": {"domain_controller": None},
            "verbose": 0,
            "logging": {"enable": False},
        }
    )
    srv = cheroot_wsgi.Server(("127.0.0.1", 0), app)
    srv.prepare()
    threading.Thread(target=srv.serve, daemon=True).start()
    yield srv.bind_addr[1], root
    srv.stop()


def test_webdav_full_cycle(dav_server, tmp_path):
    port, root = dav_server
    a = ra.WebDAVAdapter(prof("webdav", port, remote_root="/dav"))
    assert a.home == "/dav" and a.connect()[0]
    es = {e.name: e for e in a.listdir("/dav")[0]}
    assert set(es) == {"pasta x", "raiz.txt"}, "a própria pasta não pode aparecer como filho"
    assert es["pasta x"].is_dir and es["raiz.txt"].size == 5 and es["raiz.txt"].modified
    assert {e.name for e in a.listdir("/dav/pasta x")[0]} == {"arq 1.txt"}
    ok, _ = a.download("/dav/raiz.txt", tmp_path / "r.txt")
    assert ok and (tmp_path / "r.txt").read_text() == "raiz!"
    (tmp_path / "n.txt").write_text("novo conteúdo ✓", encoding="utf-8")
    assert a.upload(tmp_path / "n.txt", "/dav/pasta x/novo arq.txt")[0]
    assert (root / "pasta x" / "novo arq.txt").read_text(encoding="utf-8") == "novo conteúdo ✓"
    assert a.mkdir("/dav/criada")[0] and (root / "criada").is_dir()
    assert a.rename("/dav/criada", "/dav/renomeada")[0] and (root / "renomeada").is_dir()
    assert a.remove("/dav/renomeada", True)[0] and not (root / "renomeada").exists()
    assert a.remove("/dav/pasta x/novo arq.txt")[0]
    ok, msg = a.download("/dav/nao.txt", tmp_path / "z")
    assert not ok and "404" in msg
    assert a.listdir("/dav/inexistente")[1] != ""


_SSH_ENV = os.environ.get("CODIS_TEST_SSH", "")


@pytest.fixture
def ssh_profile():
    if not _SSH_ENV:
        pytest.skip("defina CODIS_TEST_SSH=host:porta:usuario:senha[:chave]")
    host, port, user, password = _SSH_ENV.split(":")[:4]
    return ra.RemoteProfile(
        name="t", host=host, port=int(port), user=user,
        password=enc(password), remote_root="~/proj",
    )  # fmt: skip


def test_ssh_listing_shows_every_file(ssh_profile):
    a = ra.SSHAdapter(ssh_profile)
    assert a.connect()[0]
    entries, err = a.listdir(a.home)
    a.close()
    assert not err
    assert {"a.txt", "2024", "dir with space"} <= {e.name for e in entries}


def test_ssh_errors_are_not_swallowed(ssh_profile):
    a = ra.SSHAdapter(ssh_profile)
    assert a.connect()[0]
    assert a.listdir("/nao/existe")[1] != ""
    a.close()


def test_ssh_transfer_roundtrip_with_spaces(ssh_profile, tmp_path):
    a = ra.SSHAdapter(ssh_profile)
    assert a.connect()[0]
    src = tmp_path / "up.txt"
    src.write_text("ida e volta ✓\n", encoding="utf-8")
    remote = f"{a.home}/dir with space/rt test.txt"
    assert a.upload(src, remote)[0]
    assert a.download(remote, tmp_path / "back.txt")[0]
    assert (tmp_path / "back.txt").read_text(encoding="utf-8") == "ida e volta ✓\n"
    assert a.remove(remote)[0]
    ok, _ = a.download(f"{a.home}/nada", tmp_path / "nada")
    assert not ok and not (tmp_path / "nada").exists()
    a.close()


def test_ssh_password_login_without_sshpass(ssh_profile):
    a = ra.SSHAdapter(ssh_profile)
    a._sshpass = None
    assert a._auth() == "askpass"
    assert a.connect()[0]
    a.close()
