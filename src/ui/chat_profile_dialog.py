"""Diálogo de conexão do Chat: seletor de protocolo, campos adaptativos, teste e validação."""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk
from typing import Any

from src.chat.models import (
    PROTO_BADGE, PROTO_DEFAULTS, PROTO_FIELDS, PROTO_GROUPS, PROTO_LABELS, SECRET_EXTRA, ChatProfile,
)
from src.chat.probe import probe, validate
from src.constants import FONT_MONO, FONT_UI
from src.i18n import _t
from src.ui.grab_safe import safe_grab_set


class ChatProfileDialog(tk.Toplevel):
    def __init__(self, parent: tk.Widget, tmgr: Any, profile: ChatProfile | None = None,
                 taken_names: set[str] | None = None, protocol: str | None = None) -> None:
        super().__init__(parent)
        self._t = tmgr.theme
        self.result: ChatProfile | None = None
        self._p = profile
        self._taken = {n.casefold() for n in (taken_names or set())}
        if profile:
            self._taken.discard(profile.name.casefold())
        self._vars: dict[str, tk.Variable] = {}
        self._proto = tk.StringVar(value=(profile.protocol if profile else protocol or "irc"))
        self.title(_t("chat.profile_title", "Conexão de chat"))
        self.configure(bg=self._t.panel_bg)
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self._build()
        self.bind("<Escape>", lambda _e: self.destroy())
        self.update_idletasks()
        w, h = max(self.winfo_reqwidth(), 520), self.winfo_reqheight()
        top = parent.winfo_toplevel()
        x = top.winfo_rootx() + max(0, (top.winfo_width() - w) // 2)
        y = top.winfo_rooty() + max(0, (top.winfo_height() - h) // 3)
        self.geometry(f"{w}x{h}+{x}+{y}")
        safe_grab_set(self)

    def _build(self) -> None:
        t = self._t
        pad = tk.Frame(self, bg=t.panel_bg)
        pad.pack(fill="both", expand=True, padx=20, pady=16)
        tk.Label(pad, text=_t("chat.profile_title", "Conexão de chat"), bg=t.panel_bg, fg=t.fg,
                 font=(FONT_UI, 14, "bold")).pack(anchor="w")
        tk.Label(pad, text=_t("chat.profile_sub", "Escolha o serviço e preencha só o necessário."),
                 bg=t.panel_bg, fg=t.fg_muted, font=(FONT_UI, 9)).pack(anchor="w", pady=(0, 10))

        self._chips = tk.Frame(pad, bg=t.panel_bg)
        self._chips.pack(fill="x", pady=(0, 6))
        self._chip_widgets: dict[str, tk.Label] = {}
        for group, protos in PROTO_GROUPS.items():
            for proto in protos:
                if proto in ("ircs", "xmpps", "wss"):
                    continue
                text, color = PROTO_BADGE[proto]
                lbl = tk.Label(self._chips, text=group if proto in ("irc", "xmpp", "ws") else
                               PROTO_LABELS[proto].split(" (")[0], bg=t.btn_bg, fg=t.btn_fg,
                               font=(FONT_UI, 9), padx=9, pady=4, cursor="hand2")
                lbl.pack(side="left", padx=(0, 4), pady=2)
                lbl.bind("<Button-1>", lambda _e, p=proto: self._pick(p))
                self._chip_widgets[proto] = lbl
        self._variant_row = tk.Frame(pad, bg=t.panel_bg)
        self._variant_row.pack(fill="x")
        tk.Frame(pad, bg=t.border, height=1).pack(fill="x", pady=8)

        self._form = tk.Frame(pad, bg=t.panel_bg)
        self._form.pack(fill="x")
        self._opts = tk.Frame(pad, bg=t.panel_bg)
        self._opts.pack(fill="x", pady=(8, 0))
        self._msg = tk.Label(pad, text="", bg=t.panel_bg, fg=t.err_fg, font=(FONT_UI, 9), anchor="w",
                             justify="left", wraplength=470)
        self._msg.pack(fill="x", pady=(8, 0))
        bar = tk.Frame(pad, bg=t.panel_bg)
        bar.pack(fill="x", pady=(10, 0))
        self._btn(bar, _t("chat.save", "Salvar"), self._save, primary=True).pack(side="right")
        self._btn(bar, _t("chat.cancel", "Cancelar"), self.destroy).pack(side="right", padx=8)
        self._test_btn = self._btn(bar, _t("chat.test", "Testar conexão"), self._test)
        self._test_btn.pack(side="left")
        self._pick(self._base_proto(self._proto.get()), keep=True)

    @staticmethod
    def _base_proto(proto: str) -> str:
        return {"ircs": "irc", "xmpps": "xmpp", "wss": "ws"}.get(proto, proto)

    def _btn(self, parent: tk.Widget, text: str, cmd, primary: bool = False) -> tk.Label:
        t = self._t
        bg, fg = (t.btn_primary, t.btn_primary_fg) if primary else (t.btn_bg, t.btn_fg)
        b = tk.Label(parent, text=text, bg=bg, fg=fg, font=(FONT_UI, 10, "bold" if primary else "normal"),
                     padx=14, pady=6, cursor="hand2")
        b.bind("<Button-1>", lambda _e: cmd())
        hover = t.btn_hover if not primary else t.accent
        b.bind("<Enter>", lambda _e: b.config(bg=hover))
        b.bind("<Leave>", lambda _e: b.config(bg=bg))
        return b

    def _pick(self, proto: str, keep: bool = False) -> None:
        t = self._t
        if not keep:
            self._proto.set(proto)
        cur = self._proto.get()
        for p, lbl in self._chip_widgets.items():
            on = p == self._base_proto(cur)
            lbl.config(bg=PROTO_BADGE[p][1] if on else t.btn_bg, fg="#FFFFFF" if on else t.btn_fg)
        self._render_form()

    def _variants(self, base: str) -> list[tuple[str, str]]:
        return {"irc": [("irc", "Sem criptografia"), ("ircs", "TLS (recomendado)")],
                "xmpp": [("xmpp", "STARTTLS (porta 5222)"), ("xmpps", "TLS direto (porta 5223)")],
                "ws": [("ws", "ws://"), ("wss", "wss:// (TLS)")]}.get(base, [])

    def _render_form(self) -> None:
        t = self._t
        base = self._base_proto(self._proto.get())
        saved = {k: (v.get() if hasattr(v, "get") else None) for k, v in self._vars.items()}
        for w in (*self._form.winfo_children(), *self._opts.winfo_children(), *self._variant_row.winfo_children()):
            w.destroy()
        self._vars = {}
        var = self._variants(base)
        if var:
            tk.Label(self._variant_row, text=_t("chat.f_security", "Conexão"), bg=t.panel_bg, fg=t.fg_muted,
                     font=(FONT_UI, 9)).pack(side="left")
            for val, text in var:
                tk.Radiobutton(self._variant_row, text=text, value=val, variable=self._proto, bg=t.panel_bg,
                               fg=t.fg, selectcolor=t.input_bg, activebackground=t.panel_bg, activeforeground=t.fg,
                               font=(FONT_UI, 9), command=self._render_form, highlightthickness=0
                               ).pack(side="left", padx=(10, 0))
        proto = self._proto.get()
        p = self._p if (self._p and self._base_proto(self._p.protocol) == base) else None
        name = saved.get("name") if saved.get("name") is not None else (p.name if p else "")
        self._add_text("name", _t("chat.f_name", "Nome da conexão"), name or "", False, "ex.: Libera, Trabalho, Casa")
        for spec in PROTO_FIELDS.get(proto, PROTO_FIELDS[base]):
            label = _t(spec.label_key, spec.key.replace("_", " ").title())
            cur = self._current(spec, p, saved)
            if spec.kind == "bool":
                v = tk.BooleanVar(value=bool(cur))
                self._vars[spec.key] = v
                tk.Checkbutton(self._form, text=label + (f"  -  {spec.tip}" if spec.tip else ""), variable=v,
                               bg=t.panel_bg, fg=t.fg, selectcolor=t.input_bg, activebackground=t.panel_bg,
                               activeforeground=t.fg, font=(FONT_UI, 9), highlightthickness=0
                               ).pack(anchor="w", pady=2)
            elif spec.kind == "choice":
                v = tk.StringVar(value=str(cur or spec.default))
                self._vars[spec.key] = v
                row = tk.Frame(self._form, bg=t.panel_bg)
                row.pack(fill="x", pady=3)
                tk.Label(row, text=label, bg=t.panel_bg, fg=t.fg_muted, font=(FONT_UI, 9), width=22,
                         anchor="w").pack(side="left")
                ttk.Combobox(row, textvariable=v, values=spec.choices, state="readonly", width=12).pack(side="left")
                if spec.tip:
                    tk.Label(row, text=spec.tip, bg=t.panel_bg, fg=t.warn_fg, font=(FONT_UI, 8)).pack(side="left", padx=8)
            else:
                self._add_text(spec.key, label, str(cur or ""), spec.secret, spec.tip)
        for key, text, default in (("auto_reconnect", _t("chat.opt_reconnect", "Reconectar automaticamente"), True),
                                   ("auto_connect", _t("chat.opt_autoconnect", "Conectar ao abrir o chat"), False),
                                   ("keep_history", _t("chat.opt_history", "Salvar histórico neste computador"), True),
                                   ("verify_tls", _t("chat.opt_verify", "Verificar certificado TLS"), True)):
            v = tk.BooleanVar(value=bool(saved.get(key, getattr(p, key, default) if p else default)))
            self._vars[key] = v
            tk.Checkbutton(self._opts, text=text, variable=v, bg=t.panel_bg, fg=t.fg, selectcolor=t.input_bg,
                           activebackground=t.panel_bg, activeforeground=t.fg, font=(FONT_UI, 9),
                           highlightthickness=0).pack(anchor="w")
        if base == "discord":
            tk.Label(self._form, text="Self-bot (token de usuário) viola os Termos de Serviço do Discord.\n"
                     "Habilite 'Message Content Intent' no Developer Portal do seu bot.", bg=t.panel_bg,
                     fg=t.fg_muted, font=(FONT_UI, 8), justify="left").pack(anchor="w", pady=(4, 0))
        if base == "slack":
            tk.Label(self._form, text="Sem App Token (xapp-) o Codis usa polling a cada poucos segundos.",
                     bg=t.panel_bg, fg=t.fg_muted, font=(FONT_UI, 8)).pack(anchor="w", pady=(4, 0))
        self._msg.config(text="")

    def _current(self, spec, p: ChatProfile | None, saved: dict) -> Any:
        if spec.key in saved and saved[spec.key] is not None:
            return saved[spec.key]
        if spec.key == "channels":
            return ", ".join(p.channels) if p else spec.default
        if spec.key == "password":
            return p.decoded_password() if p else spec.default
        if spec.key == "token":
            return p.decoded_token() if p else spec.default
        if spec.extra:
            return p.x(spec.key, spec.default) if p else spec.default
        if spec.kind == "bool":
            return getattr(p, spec.key, False) if p else spec.default == "true"
        if spec.key == "port":
            return p.port if p else (spec.default or PROTO_DEFAULTS.get(self._proto.get(), ""))
        return (getattr(p, spec.key, "") or spec.default) if p else spec.default

    def _add_text(self, key: str, label: str, value: str, secret: bool, tip: str) -> None:
        t = self._t
        row = tk.Frame(self._form, bg=t.panel_bg)
        row.pack(fill="x", pady=(4, 0))
        tk.Label(row, text=label, bg=t.panel_bg, fg=t.fg_muted, font=(FONT_UI, 9), width=22, anchor="w").pack(side="left")
        v = tk.StringVar(value=value)
        self._vars[key] = v
        ent = tk.Entry(row, textvariable=v, bg=t.input_bg, fg=t.fg, insertbackground=t.cursor, relief="flat",
                       font=(FONT_MONO, 10), show="•" if secret else "", highlightthickness=1,
                       highlightbackground=t.border, highlightcolor=t.accent)
        ent.pack(side="left", fill="x", expand=True, ipady=4)
        if secret:
            show = tk.Label(row, text="mostrar", bg=t.panel_bg, fg=t.accent, font=(FONT_UI, 8), cursor="hand2")
            show.pack(side="left", padx=6)
            show.bind("<Button-1>", lambda _e: ent.config(show="" if ent.cget("show") else "•"))
        if tip:
            tk.Label(self._form, text=tip, bg=t.panel_bg, fg=t.fg_muted, font=(FONT_UI, 8)).pack(anchor="w", padx=(160, 0))
        ent.bind("<Return>", lambda _e: self._save())

    def _collect(self) -> ChatProfile:
        def s(k: str, d: str = "") -> str:
            v = self._vars.get(k)
            return v.get().strip() if isinstance(v, tk.StringVar) else d

        def b(k: str, d: bool = False) -> bool:
            v = self._vars.get(k)
            return bool(v.get()) if isinstance(v, tk.BooleanVar) else d

        proto = self._proto.get()
        try:
            port = int(s("port") or PROTO_DEFAULTS.get(proto, 0))
        except ValueError:
            port = -1
        extra: dict[str, str] = dict(self._p.extra) if self._p else {}
        for spec in PROTO_FIELDS.get(proto, []):
            if spec.extra:
                val = s(spec.key)
                if val:
                    extra[spec.key] = ChatProfile.encode(val) if spec.key in SECRET_EXTRA else val
                else:
                    extra.pop(spec.key, None)
        return ChatProfile(
            name=s("name"), protocol=proto, host=s("host"), port=port, nick=s("nick", "CodisUser") or "CodisUser",
            user=s("user", "codis") or "codis", realname=s("realname", "Codis Editor") or "Codis Editor",
            jid=s("jid"), password=ChatProfile.encode(s("password")), token=ChatProfile.encode(s("token")),
            channels=[c.strip() for c in s("channels").replace(";", ",").split(",") if c.strip()],
            tls=b("tls"), verify_tls=b("verify_tls", True), sasl=b("sasl"), room_id=s("room_id"), extra=extra,
            auto_reconnect=b("auto_reconnect", True), auto_connect=b("auto_connect"), keep_history=b("keep_history", True),
            encoding=self._p.encoding if self._p else "utf-8",
        )

    def _save(self) -> None:
        prof = self._collect()
        err = validate(prof, self._taken)
        if err:
            self._msg.config(text=err, fg=self._t.err_fg)
            return
        self.result = prof
        self.destroy()

    def _test(self) -> None:
        prof = self._collect()
        if prof.port < 0:
            self._msg.config(text="Porta inválida.", fg=self._t.err_fg)
            return
        self._msg.config(text="Testando…", fg=self._t.fg_muted)

        result: list[tuple[bool, str]] = []
        threading.Thread(target=lambda: result.append(probe(prof)), daemon=True).start()

        def poll() -> None:
            if not self.winfo_exists():
                return
            if not result:
                self.after(80, poll)
                return
            ok, text = result[0]
            self._msg.config(text=("\u2714 " if ok else "\u2716 ") + text, fg=self._t.ok_fg if ok else self._t.err_fg)
        self.after(80, poll)
