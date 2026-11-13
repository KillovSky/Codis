"""Codis - diálogo de criação/edição de perfis de conexão remota.

Preserva todos os campos do perfil ao editar (timeout, TLS, FTP passivo, tags),
aceita colar uma URL (``sftp://user@host:22/pasta``) e testa a conexão sem
fechar o diálogo.
"""

from __future__ import annotations

import contextlib
import threading
import tkinter as tk
from dataclasses import replace
from tkinter import filedialog, ttk
from typing import Any

from src.constants import FONT_UI
from src.i18n import _t
from src.ui.grab_safe import safe_grab_set
from src.ui.remote_adapters import (
    PROTO_DEFAULTS,
    RemoteProfile,
    make_adapter,
    parse_remote_url,
)
from src.ui.ui_scale import fit_window, px

_PROTOCOLS = ("ssh", "sftp", "ftp", "ftps", "http", "https", "webdav", "webdavs")
_TLS_PROTOCOLS = {"ftps", "https", "webdavs"}


class ProfileDialog(tk.Toplevel):
    """Diálogo modal. Após ``wait_window``, ``result`` é o perfil ou ``None``."""

    def __init__(self, parent: tk.Misc, tmgr: Any, profile: RemoteProfile | None = None) -> None:
        super().__init__(parent)
        self._t = tmgr.theme
        self._orig = profile
        self.result: RemoteProfile | None = None
        self._test_job = 0
        self.title(
            _t("rbrowser.dlg_edit", "Editar conexão")
            if profile
            else _t("rbrowser.dlg_new", "Nova conexão")
        )
        self.configure(bg=self._t.panel_bg)
        self.transient(parent.winfo_toplevel())
        self.resizable(True, True)
        self._vars: dict[str, tk.Variable] = {}
        self._rows: dict[str, list[tk.Widget]] = {}
        self._build()
        self._load(profile)
        self._sync_state()
        self.update_idletasks()
        w, h = fit_window(self, max(px(460), self.winfo_reqwidth()), self.winfo_reqheight() + px(8))
        self.geometry(f"{w}x{h}")
        self.minsize(min(px(380), w), min(px(320), h))
        self.bind("<Escape>", lambda _e: self.destroy())
        safe_grab_set(self)
        self._entries["name"].focus_set()

    def _build(self) -> None:
        t = self._t
        self._entries: dict[str, tk.Entry] = {}
        outer = tk.Frame(self, bg=t.panel_bg)
        outer.pack(fill="both", expand=True, padx=px(16), pady=(px(14), px(8)))
        outer.columnconfigure(1, weight=1)

        def var(key: str, value: Any = "") -> tk.Variable:
            v: tk.Variable = tk.BooleanVar(value=value) if isinstance(value, bool) else tk.StringVar(value=str(value))
            self._vars[key] = v
            return v

        row = 0

        def label(text: str, key: str) -> tk.Label:
            lb = tk.Label(outer, text=text, bg=t.panel_bg, fg=t.fg_muted, font=(FONT_UI, 9), anchor="w")
            lb.grid(row=row, column=0, sticky="w", padx=(0, px(10)), pady=px(4))
            self._rows.setdefault(key, []).append(lb)
            return lb

        def entry(key: str, show: str = "", span: int = 1) -> tk.Entry:
            e = tk.Entry(
                outer, textvariable=var(key), show=show, bg=t.input_bg, fg=t.fg,
                insertbackground=t.fg, relief="flat", font=(FONT_UI, 10),
                highlightthickness=1, highlightbackground=t.border, highlightcolor=t.accent,
            )  # fmt: skip
            e.grid(row=row, column=1, columnspan=span, sticky="ew", pady=px(4), ipady=px(3))
            self._entries[key] = e
            self._rows.setdefault(key, []).append(e)
            return e

        label(_t("rbrowser.f_name", "Nome"), "name")
        entry("name")
        row += 1

        label(_t("rbrowser.f_protocol", "Protocolo"), "protocol")
        cb = ttk.Combobox(outer, textvariable=var("protocol", "ssh"), values=_PROTOCOLS, state="readonly", width=12)
        cb.grid(row=row, column=1, sticky="w", pady=px(4))
        cb.bind("<<ComboboxSelected>>", self._on_protocol_change)
        row += 1

        label(_t("rbrowser.f_host", "Host / URL"), "host")
        host = entry("host")
        host.bind("<FocusOut>", self._maybe_parse_url)
        host.bind("<<Paste>>", lambda _e: self.after(30, self._maybe_parse_url))
        row += 1

        label(_t("rbrowser.f_port", "Porta"), "port")
        entry("port")
        row += 1

        label(_t("rbrowser.f_user", "Usuário"), "user")
        entry("user")
        row += 1

        label(_t("rbrowser.f_password", "Senha"), "password")
        entry("password", show="•")
        self._pw_btn = tk.Button(
            outer, text="👁", command=self._toggle_pw, bg=t.btn_bg, fg=t.btn_fg, relief="flat",
            font=(FONT_UI, 9), cursor="hand2", padx=px(6),
        )  # fmt: skip
        self._pw_btn.grid(row=row, column=2, padx=(px(6), 0))
        self._rows["password"].append(self._pw_btn)
        row += 1

        label(_t("rbrowser.f_key", "Chave privada"), "key_path")
        entry("key_path")
        kb = tk.Button(
            outer, text="…", command=self._browse_key, bg=t.btn_bg, fg=t.btn_fg, relief="flat",
            font=(FONT_UI, 9), cursor="hand2", padx=px(6),
        )  # fmt: skip
        kb.grid(row=row, column=2, padx=(px(6), 0))
        self._rows["key_path"].append(kb)
        row += 1

        label(_t("rbrowser.f_root", "Pasta inicial"), "remote_root")
        entry("remote_root")
        row += 1

        label(_t("rbrowser.f_jump", "Jump host (ssh)"), "jump_host")
        entry("jump_host")
        row += 1

        label(_t("rbrowser.f_timeout", "Timeout (s)"), "timeout")
        entry("timeout")
        row += 1

        label(_t("rbrowser.f_tags", "Etiquetas"), "tags")
        entry("tags")
        row += 1

        self._check(outer, row, "passive_ftp", _t("rbrowser.f_passive", "Modo passivo (FTP)"))
        row += 1
        self._check(outer, row, "verify_tls", _t("rbrowser.f_verify", "Verificar certificado TLS"))
        row += 1

        self._status = tk.Label(
            outer, text="", bg=t.panel_bg, fg=t.fg_muted, font=(FONT_UI, 9), anchor="w",
            justify="left", wraplength=px(420),
        )  # fmt: skip
        self._status.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(px(8), 0))

        bar = tk.Frame(self, bg=t.panel_bg)
        bar.pack(fill="x", padx=px(16), pady=(0, px(14)))
        self._mk_btn(bar, _t("rbrowser.btn_cancel", "Cancelar"), self.destroy).pack(side="right", padx=(px(6), 0))
        self._mk_btn(bar, _t("rbrowser.btn_save", "Salvar"), self._save, primary=True).pack(side="right")
        self._mk_btn(bar, _t("rbrowser.btn_test_conn", "Testar conexão"), self._test).pack(side="left")

    def _check(self, parent: tk.Frame, row: int, key: str, text: str) -> tk.Checkbutton:
        t = self._t
        v = tk.BooleanVar(value=True)
        self._vars[key] = v
        cb = tk.Checkbutton(
            parent, text=text, variable=v, bg=t.panel_bg, fg=t.fg, selectcolor=t.input_bg,
            activebackground=t.panel_bg, activeforeground=t.fg, font=(FONT_UI, 9), anchor="w",
        )  # fmt: skip
        cb.grid(row=row, column=1, sticky="w", pady=px(2))
        self._rows[key] = [cb]
        return cb

    def _mk_btn(self, parent: tk.Misc, text: str, cmd: Any, primary: bool = False) -> tk.Button:
        t = self._t
        return tk.Button(
            parent, text=text, command=cmd, relief="flat", cursor="hand2", font=(FONT_UI, 9),
            bg=t.btn_primary if primary else t.btn_bg, fg=t.btn_primary_fg if primary else t.btn_fg,
            activebackground=t.btn_hover, padx=px(12), pady=px(4),
        )  # fmt: skip

    def _load(self, p: RemoteProfile | None) -> None:
        if p is None:
            p = RemoteProfile(name="")
        v = self._vars
        v["name"].set(p.name)
        v["protocol"].set(p.protocol)
        v["host"].set(p.host)
        v["port"].set(str(p.port))
        v["user"].set(p.user)
        v["password"].set(p.decoded_password())
        v["key_path"].set(p.key_path)
        v["remote_root"].set(p.remote_root)
        v["jump_host"].set(p.jump_host)
        v["timeout"].set(str(p.timeout))
        v["tags"].set(", ".join(p.tags))
        v["passive_ftp"].set(p.passive_ftp)
        v["verify_tls"].set(p.verify_tls)

    def _on_protocol_change(self, _e: object = None) -> None:
        proto = str(self._vars["protocol"].get())
        current = str(self._vars["port"].get()).strip()
        if current in {str(n) for n in PROTO_DEFAULTS.values()} or not current:
            self._vars["port"].set(str(PROTO_DEFAULTS.get(proto, 22)))
        self._sync_state()

    def _sync_state(self) -> None:
        proto = str(self._vars["protocol"].get())
        ssh = proto in ("ssh", "sftp")
        web = proto in ("http", "https", "webdav", "webdavs")
        visible = {
            "key_path": ssh,
            "jump_host": ssh,
            "passive_ftp": proto in ("ftp", "ftps"),
            "verify_tls": proto in _TLS_PROTOCOLS,
        }
        for key, widgets in self._rows.items():
            if key in visible:
                for w in widgets:
                    if visible[key]:
                        w.grid()
                    else:
                        w.grid_remove()
        host_hint = _t("rbrowser.hint_web", "Ex.: https://servidor/pasta") if web else ""
        self._status.configure(text=host_hint, fg=self._t.fg_muted)

    def _toggle_pw(self) -> None:
        e = self._entries["password"]
        e.configure(show="" if e.cget("show") else "•")

    def _browse_key(self) -> None:
        path = filedialog.askopenfilename(parent=self, title=_t("rbrowser.pick_key", "Selecionar chave privada"))
        if path:
            self._vars["key_path"].set(path)

    def _maybe_parse_url(self, _e: object = None) -> None:
        """Se o campo Host contém uma URL completa, distribui nos campos."""
        raw = str(self._vars["host"].get()).strip()
        if "://" not in raw:
            return
        parsed = parse_remote_url(raw)
        if not parsed:
            return
        self._vars["protocol"].set(str(parsed["protocol"]))
        self._vars["host"].set(str(parsed["host"]))
        self._vars["port"].set(str(parsed["port"]))
        if parsed["user"]:
            self._vars["user"].set(str(parsed["user"]))
        if parsed["password"]:
            self._vars["password"].set(str(parsed["password"]))
        self._vars["remote_root"].set(str(parsed["remote_root"]))
        if not str(self._vars["name"].get()).strip():
            self._vars["name"].set(str(parsed["host"]))
        self._sync_state()

    def _collect(self) -> tuple[RemoteProfile | None, str]:
        v = self._vars
        name = str(v["name"].get()).strip()
        host = str(v["host"].get()).strip()
        if not name:
            return None, _t("rbrowser.err_name", "Informe um nome para a conexão.")
        if not host:
            return None, _t("rbrowser.err_host", "Informe o host.")
        try:
            port = int(str(v["port"].get()).strip())
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            return None, _t("rbrowser.err_port", "Porta inválida (1-65535).")
        try:
            timeout = max(3, min(300, int(str(v["timeout"].get()).strip() or "15")))
        except ValueError:
            return None, _t("rbrowser.err_timeout", "Timeout inválido.")
        tags = [x.strip() for x in str(v["tags"].get()).split(",") if x.strip()]
        base = self._orig or RemoteProfile(name=name)
        try:
            profile = replace(
                base,
                name=name,
                protocol=str(v["protocol"].get()),
                host=host,
                port=port,
                user=str(v["user"].get()).strip(),
                password=RemoteProfile.encode_password(str(v["password"].get())),
                key_path=str(v["key_path"].get()).strip(),
                remote_root=str(v["remote_root"].get()).strip() or "~",
                jump_host=str(v["jump_host"].get()).strip(),
                timeout=timeout,
                tags=tags,
                passive_ftp=bool(v["passive_ftp"].get()),
                verify_tls=bool(v["verify_tls"].get()),
            )
        except ValueError as exc:
            return None, str(exc)
        return profile, ""

    def _save(self) -> None:
        profile, err = self._collect()
        if profile is None:
            self._show(err, error=True)
            return
        self.result = profile
        self.destroy()

    def _show(self, text: str, *, error: bool = False, ok: bool = False) -> None:
        t = self._t
        color = t.err_fg if error else (t.ok_fg if ok else t.fg_muted)
        with contextlib.suppress(tk.TclError):
            self._status.configure(text=text, fg=color)

    def _test(self) -> None:
        profile, err = self._collect()
        if profile is None:
            self._show(err, error=True)
            return
        self._test_job += 1
        job = self._test_job
        self._show(_t("rbrowser.testing", "Testando conexão…"))

        def _work() -> None:
            adapter = make_adapter(profile)
            try:
                ok, msg = adapter.connect()
            finally:
                adapter.close()
            with contextlib.suppress(tk.TclError, RuntimeError):
                self.after(0, lambda: self._test_done(job, ok, msg))

        threading.Thread(target=_work, daemon=True).start()

    def _test_done(self, job: int, ok: bool, msg: str) -> None:
        if job != self._test_job:
            return
        self._show(("✓ " if ok else "✗ ") + msg, error=not ok, ok=ok)
