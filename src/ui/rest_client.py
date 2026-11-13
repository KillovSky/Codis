"""Codis - Native REST Client panel.

Full-featured HTTP client embedded in the editor:
  - Methods: GET POST PUT PATCH DELETE HEAD OPTIONS
  - Auth: None / Bearer / Basic / API Key
  - Headers + Body editors
  - JSON syntax-coloring in response
  - Request history (50 items, persistent per session)
  - Environment variables  ({{VAR}} substitution)
  - Response time + size display
  - Copy response button
  - Save response to file
"""

from __future__ import annotations

import base64
import contextlib
import json
import re
import threading
import time
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from src.constants import FONT_MONO, FONT_UI
from src.i18n import _t

_MAX_RESPONSE_BYTES = 20 * 1024 * 1024


def _do_request(
    method: str, url: str, headers: dict[str, str], body: str, timeout: int = 20
) -> dict:
    """Executa 'do request' e retorna o resultado.

    Args:
        method: Parâmetro da operação.
        url: Parâmetro da operação.
        headers: Parâmetro da operação.
        body: Parâmetro da operação.
        timeout: Parâmetro da operação.
    """
    t0 = time.time()
    try:
        scheme = urllib.parse.urlparse(url).scheme.lower()
        if scheme not in ("http", "https"):
            raise ValueError(
                f"Esquema de URL não suportado: {scheme!r}. Use http:// ou https://."
            )
        data = (
            body.encode("utf-8")
            if body and method in ("POST", "PUT", "PATCH")
            else None
        )
        req = urllib.request.Request(
            url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            elapsed = time.time() - t0
            raw = resp.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_RESPONSE_BYTES:
                raise ValueError("Resposta maior que o limite de 20 MB.")
            ct = resp.headers.get("Content-Type", "")
            charset = "utf-8"
            if "charset=" in ct:
                charset = ct.split("charset=")[-1].split(";")[0].strip().strip("\"'")
            try:
                text = raw.decode(charset, errors="replace")
            except LookupError:
                text = raw.decode("utf-8", errors="replace")
            return {
                "ok": True,
                "status": resp.status,
                "reason": resp.reason,
                "headers": dict(resp.headers),
                "body": text,
                "elapsed": elapsed,
                "size": len(raw),
            }
    except urllib.error.HTTPError as e:
        elapsed = time.time() - t0
        body_text = ""
        with contextlib.suppress(tk.TclError):
            body_text = e.read().decode("utf-8", errors="replace")
        return {
            "ok": False,
            "status": e.code,
            "reason": e.reason,
            "headers": dict(e.headers),
            "body": body_text,
            "elapsed": elapsed,
            "size": len(body_text.encode()),
        }
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        return {
            "ok": False,
            "status": 0,
            "reason": str(exc),
            "headers": {},
            "body": f"Error: {exc}",
            "elapsed": time.time() - t0,
            "size": 0,
        }


def _fmt_json(text: str) -> str:
    """Executa 'fmt json' e retorna o resultado.

    Args:
        text: Dado textual a processar.
    """
    try:
        return json.dumps(json.loads(text), indent=2, ensure_ascii=False)
    except (json.JSONDecodeError, ValueError):
        return text


def _fmt_size(n: int) -> str:
    """Executa 'fmt size' e retorna o resultado.

    Args:
        n: Parâmetro da operação.
    """
    if n < 1024:
        return f"{n} B"
    if n < 1024**2:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024**2:.1f} MB"


def _apply_env(text: str, env: dict[str, str]) -> str:
    """Replace {{VAR}} placeholders with env values."""

    def _sub(m: re.Match) -> str:
        return env.get(m.group(1), m.group(0))

    return re.sub(r"\{\{(\w+)\}\}", _sub, text)


MAX_HISTORY = 50
_history: list[dict] = []


class RestClientPanel(tk.Frame):
    """Embedded REST client panel."""

    def __init__(self, parent: tk.Widget, tmgr, cfg) -> None:
        """Inicializa o cliente REST integrado."""
        t = tmgr.theme
        super().__init__(parent, bg=t.panel_bg)
        self._tmgr = tmgr
        self._cfg = cfg
        self._loading = False
        self._env: dict[str, str] = {}
        self._build()

    def _build(self) -> None:
        """Constrói os widgets e layout interno do componente."""
        t = self._tmgr.theme

        for child in self.winfo_children():
            with contextlib.suppress(tk.TclError):
                child.destroy()

        hdr = tk.Frame(self, bg=t.panel_bg, height=30)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(
            hdr,
            text=f"  🌍 {_t('rest.title')}",
            bg=t.panel_bg,
            fg=t.fg,
            font=(FONT_UI, 9, "bold"),
        ).pack(side="left", padx=4)
        tk.Frame(self, bg=t.floating_border, height=1).pack(fill="x")

        url_row = tk.Frame(self, bg=t.editor_bg, pady=5)
        url_row.pack(fill="x", padx=6, pady=(6, 0))

        self._method_var = tk.StringVar(value="GET")
        methods = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
        method_box = ttk.Combobox(
            url_row,
            textvariable=self._method_var,
            values=methods,
            width=9,
            state="readonly",
            font=(FONT_UI, 9),
        )
        method_box.pack(side="left", padx=(0, 4))

        self._url_var = tk.StringVar(value="https://")
        url_entry = tk.Entry(
            url_row,
            textvariable=self._url_var,
            bg=t.input_bg,
            fg=t.fg,
            insertbackground=t.t_func,
            relief="flat",
            bd=1,
            font=(FONT_MONO, 9),
            highlightthickness=1,
            highlightbackground=t.floating_border,
        )
        url_entry.pack(side="left", fill="x", expand=True, padx=4)
        url_entry.bind("<Return>", lambda _: self._send())

        self._send_btn = tk.Button(
            url_row,
            text=f"  {_t('rest.send')}  ",
            bg=t.t_func,
            fg=t.editor_bg,
            font=(FONT_UI, 9, "bold"),
            relief="flat",
            bd=0,
            cursor="hand2",
            command=self._send,
        )
        self._send_btn.pack(side="left", padx=4)

        paned = ttk.PanedWindow(self, orient="vertical")
        paned.pack(fill="both", expand=True, padx=6, pady=4)

        req_frame = tk.Frame(paned, bg=t.panel_bg)
        resp_frame = tk.Frame(paned, bg=t.panel_bg)
        paned.add(req_frame, weight=1)
        paned.add(resp_frame, weight=2)

        req_nb = ttk.Notebook(req_frame)
        req_nb.pack(fill="both", expand=True)

        hdr_tab = tk.Frame(req_nb, bg=t.editor_bg)
        req_nb.add(hdr_tab, text=_t("hc.rest_client.headers"))
        tk.Label(
            hdr_tab,
            text=_t("hc.rest_client.format_name_value_one_per_line"),
            bg=t.editor_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        ).pack(anchor="w", padx=6, pady=2)
        self._headers_txt = self._make_text(hdr_tab, height=4)
        self._headers_txt.insert(
            "1.0", "Content-Type: application/json\nAccept: application/json"
        )

        body_tab = tk.Frame(req_nb, bg=t.editor_bg)
        req_nb.add(body_tab, text=_t("hc.rest_client.body"))
        self._body_txt = self._make_text(body_tab, height=4)

        auth_tab = tk.Frame(req_nb, bg=t.editor_bg)
        req_nb.add(auth_tab, text=_t("hc.rest_client.auth"))
        self._build_auth_tab(auth_tab)

        env_tab = tk.Frame(req_nb, bg=t.editor_bg)
        req_nb.add(env_tab, text=_t("hc.rest_client.env"))
        tk.Label(
            env_tab,
            text=_t(
                "rest.env_hint",
                "Formato: VAR=valor (uma por linha) - use {VAR} na URL/cabeçalhos",
            ),
            bg=t.editor_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        ).pack(anchor="w", padx=6, pady=2)
        self._env_txt = self._make_text(env_tab, height=4)

        hist_tab = tk.Frame(req_nb, bg=t.editor_bg)
        req_nb.add(hist_tab, text=_t("hc.rest_client.history"))
        self._hist_list = tk.Listbox(
            hist_tab,
            bg=t.editor_bg,
            fg=t.fg,
            relief="flat",
            font=(FONT_MONO, 8),
            selectbackground=t.sel_bg,
            activestyle="none",
            highlightthickness=0,
        )
        self._hist_list.pack(fill="both", expand=True, padx=6, pady=4)
        self._hist_list.bind("<Double-1>", self._load_history)

        status_row = tk.Frame(resp_frame, bg=t.panel_bg, height=26)
        status_row.pack(fill="x")
        status_row.pack_propagate(False)
        self._status_lbl = tk.Label(
            status_row,
            text=_t("rest.sending"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            anchor="w",
        )
        self._status_lbl.pack(side="left", padx=8)
        self._time_lbl = tk.Label(
            status_row, text="", bg=t.panel_bg, fg=t.fg_muted, font=(FONT_UI, 8)
        )
        self._time_lbl.pack(side="right", padx=8)

        act_row = tk.Frame(resp_frame, bg=t.panel_bg)
        act_row.pack(fill="x")
        for lbl, cmd in (
            ("📋 Copy", self._copy_response),
            ("💾 Save", self._save_response),
        ):
            lb = tk.Label(
                act_row,
                text=lbl,
                bg=t.panel_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 8),
                cursor="hand2",
                padx=6,
            )
            lb.pack(side="left")
            lb.bind("<Button-1>", lambda _, c=cmd: c())

        resp_nb = ttk.Notebook(resp_frame)
        resp_nb.pack(fill="both", expand=True)

        body_resp = tk.Frame(resp_nb, bg=t.editor_bg)
        resp_nb.add(body_resp, text=_t("hc.rest_client.response"))
        self._resp_txt = self._make_text(
            body_resp, height=10, state="disabled", wrap="none"
        )

        hdr_resp = tk.Frame(resp_nb, bg=t.editor_bg)
        resp_nb.add(hdr_resp, text=_t("hc.rest_client.headers"))
        self._resp_hdr_txt = self._make_text(
            hdr_resp, height=6, state="disabled")

        for w in (self._resp_txt, self._resp_hdr_txt):
            w.tag_configure("key", foreground=t.t_func)
            w.tag_configure("string", foreground=t.t_string)
            w.tag_configure("number", foreground=t.t_number)
            w.tag_configure("bool", foreground=t.t_keyword)
            w.tag_configure("null", foreground=t.fg_muted)

    def _make_text(self, parent, height: int = 5, **kwargs) -> tk.Text:
        """Executa 'make text' e retorna o resultado.

        Args:
            parent: Widget pai Tkinter.
            height: Parâmetro da operação.
        """
        t = self._tmgr.theme
        state = kwargs.pop("state", "normal")
        wrap = kwargs.pop("wrap", "none")
        frame = tk.Frame(parent, bg=t.editor_bg)
        frame.pack(fill="both", expand=True)
        vsb = ttk.Scrollbar(frame, orient="vertical")
        vsb.pack(side="right", fill="y")
        txt = tk.Text(
            frame,
            bg=t.editor_bg,
            fg=t.fg,
            relief="flat",
            bd=0,
            font=(FONT_MONO, 8),
            height=height,
            padx=6,
            pady=4,
            insertbackground=t.t_func,
            highlightthickness=0,
            state=state,
            wrap=wrap,
            yscrollcommand=vsb.set,
            **kwargs,
        )
        txt.pack(fill="both", expand=True)
        vsb.config(command=txt.yview)
        return txt

    def _build_auth_tab(self, parent) -> None:
        """Constrói a seção 'auth tab' do componente.

        Args:
            parent: Widget pai Tkinter.
        """
        t = self._tmgr.theme
        self._auth_type = tk.StringVar(value="None")
        row = tk.Frame(parent, bg=t.editor_bg)
        row.pack(fill="x", padx=6, pady=4)
        for kind in ("None", "Bearer", "Basic", "API Key"):
            tk.Radiobutton(
                row,
                text=kind,
                variable=self._auth_type,
                value=kind,
                bg=t.editor_bg,
                fg=t.fg,
                selectcolor=t.sel_bg,
                activebackground=t.editor_bg,
                font=(FONT_UI, 8),
            ).pack(side="left", padx=4)
        cred_frame = tk.Frame(parent, bg=t.editor_bg)
        cred_frame.pack(fill="x", padx=6, pady=2)
        tk.Label(
            cred_frame,
            text=_t("hc.rest_client.token_username"),
            bg=t.editor_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        ).pack(anchor="w")
        self._auth_token = tk.Entry(
            cred_frame,
            bg=t.input_bg,
            fg=t.fg,
            insertbackground=t.t_func,
            relief="flat",
            font=(FONT_MONO, 8),
        )
        self._auth_token.pack(fill="x", pady=2)
        tk.Label(
            cred_frame,
            text=_t("hc.rest_client.password_basic_only"),
            bg=t.editor_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        ).pack(anchor="w")
        self._auth_pass = tk.Entry(
            cred_frame,
            bg=t.input_bg,
            fg=t.fg,
            insertbackground=t.t_func,
            relief="flat",
            font=(FONT_MONO, 8),
            show="●",
        )
        self._auth_pass.pack(fill="x", pady=2)

    def _parse_headers(self) -> dict[str, str]:
        """Parseia o texto de headers em dicionário chave-valor."""
        headers: dict[str, str] = {}
        env = self._parse_env()
        for line in self._headers_txt.get("1.0", "end-1c").splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                headers[k.strip()] = _apply_env(v.strip(), env)

        auth = self._auth_type.get()
        token = self._auth_token.get().strip()
        if auth == "Bearer" and token:
            headers["Authorization"] = f"Bearer {token}"
        elif auth == "Basic" and token:
            cred = base64.b64encode(
                f"{token}:{self._auth_pass.get()}".encode()
            ).decode()
            headers["Authorization"] = f"Basic {cred}"
        elif auth == "API Key" and token:
            headers["X-API-Key"] = token
        return headers

    def _parse_env(self) -> dict[str, str]:
        """Parseia as variáveis de ambiente e substitui nos campos da requisição."""
        env: dict[str, str] = {}
        for line in self._env_txt.get("1.0", "end-1c").splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
        return env

    def _send(self) -> None:
        """Envia dados ao destino configurado."""
        if self._loading:
            return
        env = self._parse_env()
        url = _apply_env(self._url_var.get().strip(), env)
        method = self._method_var.get()
        hdrs = self._parse_headers()
        body = _apply_env(self._body_txt.get("1.0", "end-1c").strip(), env)
        if not url or url == "https://":
            self._status_lbl.config(
                text=_t("hc.rest_client.enter_a_valid_url"), fg="#f38ba8"
            )
            return
        self._loading = True
        self._send_btn.config(
            text=_t("hc.rest_client.spinner"), state="disabled")
        self._status_lbl.config(
            text=_t("hc.rest_client.sending"), fg="#f9e2af")
        threading.Thread(
            target=self._worker, args=(method, url, hdrs, body), daemon=True
        ).start()

    def _worker(self, method: str, url: str, headers: dict, body: str) -> None:
        """Worker thread que executa operação assíncrona.

        Args:
            method: Parâmetro da operação.
            url: Parâmetro da operação.
            headers: Parâmetro da operação.
            body: Parâmetro da operação.
        """
        result = _do_request(method, url, headers, body)
        self.after(0, lambda: self._show_result(result, method, url))

    def _show_result(self, r: dict, method: str, url: str) -> None:
        """Executa a operação 'show result'.

        Args:
            r: Parâmetro da operação.
            method: Parâmetro da operação.
            url: Parâmetro da operação.
        """
        self._loading = False
        self._send_btn.config(text=f"  {_t('rest.send')}  ", state="normal")
        status = r["status"]
        reason = r["reason"]
        color = (
            "#a6e3a1"
            if 200 <= status < 300
            else "#f9e2af" if 300 <= status < 400 else "#f38ba8"
        )
        self._status_lbl.config(text=f"  {status} {reason}", fg=color)
        self._time_lbl.config(
            text=f"{r['elapsed'] * 1000:.0f} ms  -  {_fmt_size(r['size'])}"
        )

        body = r.get("body", "")
        ct = r.get("headers", {}).get("Content-Type", "")
        if "json" in ct:
            body = _fmt_json(body)

        self._resp_txt.config(state="normal")
        self._resp_txt.delete("1.0", "end")
        self._resp_txt.insert("1.0", body)
        self._resp_txt.config(state="disabled")

        self._resp_hdr_txt.config(state="normal")
        self._resp_hdr_txt.delete("1.0", "end")
        for k, v in r.get("headers", {}).items():
            self._resp_hdr_txt.insert("end", f"{k}: ", ("key",))
            self._resp_hdr_txt.insert("end", f"{v}\n")
        self._resp_hdr_txt.config(state="disabled")

        _history.insert(
            0,
            {
                "method": method,
                "url": url,
                "status": status,
                "headers": self._headers_txt.get("1.0", "end-1c"),
                "body": self._body_txt.get("1.0", "end-1c"),
            },
        )
        if len(_history) > MAX_HISTORY:
            _history.pop()
        self._refresh_history()

    def _refresh_history(self) -> None:
        """Recarrega a lista de requisições do histórico persistido."""
        self._hist_list.delete(0, "end")
        for h in _history:
            self._hist_list.insert(
                "end", f"{h['method']:8} {h.get('status', '---'):3}  {h['url'][:55]}"
            )

    def _load_history(self, _: tk.Event) -> None:
        """Executa a operação 'load history'.

        Args:
            _: Parâmetro da operação.
        """
        idx = self._hist_list.curselection()
        if not idx:
            return
        h = _history[idx[0]]
        self._method_var.set(h["method"])
        self._url_var.set(h["url"])
        self._headers_txt.delete("1.0", "end")
        self._headers_txt.insert("1.0", h.get("headers", ""))
        self._body_txt.delete("1.0", "end")
        self._body_txt.insert("1.0", h.get("body", ""))

    def _copy_response(self) -> None:
        """Copia o corpo da resposta para a área de transferência."""
        text = self._resp_txt.get("1.0", "end-1c")
        self.clipboard_clear()
        self.clipboard_append(text)

    def _save_response(self) -> None:
        """Salva o corpo da resposta em arquivo escolhido pelo usuário."""
        text = self._resp_txt.get("1.0", "end-1c")
        if not text.strip():
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("Text", "*.txt"), ("All", "*")],
            title=_t("hc.rest_client.save_response"),
        )
        if path:
            try:
                Path(path).write_text(text, encoding="utf-8")
            except OSError as exc:
                messagebox.showerror(
                    _t("hc.rest_client.save_failed"), str(exc))

    def apply_theme(self, tmgr) -> None:
        """Executa a operação 'apply theme'.

        Args:
            tmgr: Parâmetro da operação.
        """
        self._tmgr = tmgr
        for w in self.winfo_children():
            w.destroy()
        self._build()
