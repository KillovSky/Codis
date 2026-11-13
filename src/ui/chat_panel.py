"""Codis - Chat ao Vivo.

Interface Tkinter sobre o núcleo ``src/chat`` (adaptadores + sessão thread-safe):
barra lateral com servidores/canais, mensagens com recuo suspenso e renderização
incremental, composer multilinha com histórico/completar, busca (Ctrl+F),
alternador rápido (Ctrl+K), lista de membros e reconexão automática.

Assinatura: ``ChatPanel(parent, tmgr, cfg)``; perfis persistidos em
``chat_profiles.json``.
"""

from __future__ import annotations

import contextlib
import logging
import time
import tkinter as tk
import tkinter.font as tkfont
import webbrowser
from collections.abc import Callable
from tkinter import messagebox, ttk
from typing import Any

from src.chat.adapters import SERVER_TAB
from src.chat.models import (
    CHAT_PROFILES_FILE, PROTO_BADGE, PROTO_LABELS, ChatProfile, ChatProtocol,
)
from src.chat.session import ChatSession, Msg
from src.chat.util import nick_color
from src.constants import FONT_MONO, FONT_UI
from src.i18n import _t
from src.ui._persistence_utils import load_dataclass_list, save_dataclass_list
from src.ui.chat_profile_dialog import ChatProfileDialog
from src.ui.chat_render import blend, day_label, ellipsize, ensure_contrast, normalize_url, same_day, tokenize
from src.ui.grab_safe import safe_grab_set

logger = logging.getLogger("Codis.Chat")

__all__ = ["ChatPanel", "ChatProfile", "ChatProtocol", "_load_chat_profiles", "_save_chat_profiles"]

TICK_MS = 80
GROUP_GAP_S = 180
RENDER_TAIL = 400
MAX_LINES = 2500
SIDEBAR_W = 230
MEMBERS_W = 170


def _load_chat_profiles() -> list[ChatProfile]:
    return load_dataclass_list(CHAT_PROFILES_FILE, ChatProfile, logger=logger)


def _save_chat_profiles(profiles: list[ChatProfile]) -> None:
    save_dataclass_list(CHAT_PROFILES_FILE, profiles, chmod_600=True)


def _round_rect(cv: tk.Canvas, x1: float, y1: float, x2: float, y2: float, r: float, **kw: Any) -> int:
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2,
           x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return cv.create_polygon(pts, smooth=True, **kw)


class _Row:
    __slots__ = ("kind", "sname", "name", "y0", "y1", "data")

    def __init__(self, kind: str, sname: str, name: str, y0: int, y1: int, data: Any = None) -> None:
        self.kind, self.sname, self.name, self.y0, self.y1, self.data = kind, sname, name, y0, y1, data


class ChatPanel(tk.Frame):
    """Painel de chat universal do Codis."""

    def __init__(self, parent: tk.Widget, tmgr: Any, cfg: Any = None, on_unread: Callable[[int, int], None] | None = None,
                 session_factory: Callable[..., ChatSession] = ChatSession) -> None:
        super().__init__(parent, bg=tmgr.theme.sidebar_bg)
        self._tmgr = tmgr
        self._cfg = cfg
        self._on_unread = on_unread
        self._factory = session_factory
        self._profiles: list[ChatProfile] = _load_chat_profiles()
        self._sessions: dict[str, ChatSession] = {}
        self._cur: tuple[str, str] | None = None
        self._alive = True
        self._collapsed: set[str] = set()
        self._rows: list[_Row] = []
        self._hover: int | None = None
        self._side_sig: Any = None
        self._members_sig: Any = None
        self._header_sig: Any = None
        self._seen: dict[str, int] = {}
        self._view: dict[str, Any] = {}
        self._new_while_up = 0
        self._show_members = True
        self._show_joins = bool(self._conf("chat_show_joins", True))
        self._hist: list[str] = []
        self._hist_i = -1
        self._comp: tuple[str, list[str], int] | None = None
        self._search_hits: list[str] = []
        self._search_i = -1
        self._last_unread = (-1, -1)
        self._binds: list[tuple[str, str]] = []
        self._after_id: str | None = None
        self.bind("<Destroy>", self._on_destroy)
        self._build()
        with contextlib.suppress(Exception):
            tmgr.on_change(self._on_theme)
        for p in self._profiles:
            if p.auto_connect:
                self._ensure_session(p)
        self._after_id = self.after(TICK_MS, self._tick)

    def _conf(self, key: str, default: Any) -> Any:
        try:
            return self._cfg.get(key, default) if self._cfg is not None else default
        except Exception:  # noqa: BLE001
            return default

    def shutdown(self) -> None:
        """Desconecta tudo (chamado ao fechar a janela/serviço)."""
        self._alive = False
        for s in list(self._sessions.values()):
            with contextlib.suppress(Exception):
                s.disconnect()

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is not self:
            return
        self.shutdown()
        for seq, fid in self._binds:
            with contextlib.suppress(tk.TclError):
                self.winfo_toplevel().unbind(seq, fid)
        self._sessions.clear()

    def _on_theme(self, _theme: Any) -> None:
        if not self._alive:
            return
        with contextlib.suppress(tk.TclError):
            self.after(0, self.rebuild)

    def rebuild(self) -> None:
        """Reconstrói a interface (troca de tema) preservando as sessões."""
        if not self._alive or not self.winfo_exists():
            return
        for w in self.winfo_children():
            w.destroy()
        self.config(bg=self._tmgr.theme.sidebar_bg)
        self._view, self._side_sig, self._members_sig, self._header_sig = {}, None, None, None
        self._seen.clear()
        self._nick_tags().clear()
        self._build()

    @property
    def _sess(self) -> ChatSession | None:
        return self._sessions.get(self._cur[0]) if self._cur else None

    def unread_summary(self) -> tuple[int, int]:
        u = m = 0
        for s in self._sessions.values():
            a, b = s.unread_totals()
            u, m = u + a, m + b
        return u, m

    def _build(self) -> None:
        t = self._tmgr.theme
        self._t = t
        self._fui = (FONT_UI, 10)
        f = tkfont.Font(font=self._fui)
        self._x1 = f.measure("00:00") + 14 + f.measure("M" * 13)
        self._x2 = self._x1 + 12

        side = tk.Frame(self, bg=t.sidebar_bg, width=SIDEBAR_W)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        hdr = tk.Frame(side, bg=t.sidebar_bg)
        hdr.pack(fill="x", padx=12, pady=(12, 6))
        tk.Label(hdr, text=_t("chat.title", "Chat"), bg=t.sidebar_bg, fg=t.fg, font=(FONT_UI, 13, "bold")).pack(side="left")
        add = tk.Label(hdr, text="+", bg=t.sidebar_bg, fg=t.accent, font=(FONT_UI, 16, "bold"), cursor="hand2")
        add.pack(side="right")
        add.bind("<Button-1>", lambda _e: self._on_add())
        self._side = tk.Canvas(side, bg=t.sidebar_bg, highlightthickness=0, bd=0)
        self._side.pack(fill="both", expand=True)
        self._side.bind("<Motion>", self._side_motion)
        self._side.bind("<Leave>", lambda _e: self._side_set_hover(None))
        self._side.bind("<Button-1>", self._side_click)
        self._side.bind("<Button-3>", self._side_ctx)
        self._side.bind("<Configure>", lambda _e: self._draw_side(force=True))
        self._side.bind("<MouseWheel>", lambda e: self._side.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        self._side.bind("<Button-4>", lambda _e: self._side.yview_scroll(-1, "units"))
        self._side.bind("<Button-5>", lambda _e: self._side.yview_scroll(1, "units"))
        tk.Frame(self, bg=t.border, width=1).pack(side="left", fill="y")

        main = tk.Frame(self, bg=t.editor_bg)
        main.pack(side="left", fill="both", expand=True)
        self._main = main
        head = tk.Frame(main, bg=t.panel_bg)
        head.pack(fill="x")
        left = tk.Frame(head, bg=t.panel_bg)
        left.pack(side="left", fill="x", expand=True, padx=14, pady=8)
        self._title = tk.Label(left, text="", bg=t.panel_bg, fg=t.fg, font=(FONT_UI, 12, "bold"), anchor="w")
        self._title.pack(fill="x")
        self._topic = tk.Label(left, text="", bg=t.panel_bg, fg=t.fg_muted, font=(FONT_UI, 9), anchor="w")
        self._topic.pack(fill="x")
        right = tk.Frame(head, bg=t.panel_bg)
        right.pack(side="right", padx=10)
        self._status_pill = tk.Label(right, text="", bg=t.panel_bg, fg=t.fg_muted, font=(FONT_UI, 9))
        self._status_pill.pack(side="left", padx=(0, 8))
        self._hbtn(right, _t("chat.search", "Buscar"), self._toggle_search)
        self._hbtn(right, _t("chat.members", "Membros"), self._toggle_members)
        self._conn_btn = self._hbtn(right, "", self._toggle_connection)
        tk.Frame(main, bg=t.border, height=1).pack(fill="x")

        self._search_bar = tk.Frame(main, bg=t.panel_bg)
        self._search_var = tk.StringVar()
        se = tk.Entry(self._search_bar, textvariable=self._search_var, bg=t.input_bg, fg=t.fg, insertbackground=t.cursor,
                      relief="flat", font=self._fui, highlightthickness=1, highlightbackground=t.border,
                      highlightcolor=t.accent)
        se.pack(side="left", fill="x", expand=True, padx=(12, 6), pady=6, ipady=3)
        self._search_entry = se
        self._search_count = tk.Label(self._search_bar, text="", bg=t.panel_bg, fg=t.fg_muted, font=(FONT_UI, 9))
        self._search_count.pack(side="left", padx=4)
        self._hbtn(self._search_bar, "\u25B2", lambda: self._search_step(-1))
        self._hbtn(self._search_bar, "\u25BC", lambda: self._search_step(1))
        self._hbtn(self._search_bar, "\u2715", self._toggle_search)
        self._search_var.trace_add("write", lambda *_: self._search_run())
        se.bind("<Return>", lambda _e: self._search_step(1) or "break")
        se.bind("<Shift-Return>", lambda _e: self._search_step(-1) or "break")
        se.bind("<Escape>", lambda _e: self._toggle_search())

        body = tk.Frame(main, bg=t.editor_bg)
        body.pack(fill="both", expand=True)
        self._body = body
        self._members_frame = tk.Frame(body, bg=t.sidebar_bg, width=MEMBERS_W)
        self._members_title = tk.Label(self._members_frame, text="", bg=t.sidebar_bg, fg=t.fg_muted,
                                       font=(FONT_UI, 8, "bold"), anchor="w")
        self._members_title.pack(fill="x", padx=10, pady=(10, 4))
        self._members = tk.Listbox(self._members_frame, bg=t.sidebar_bg, fg=t.fg, font=(FONT_UI, 10), relief="flat",
                                   highlightthickness=0, bd=0, activestyle="none", selectbackground=t.sel_bg,
                                   selectforeground=t.fg, exportselection=False)
        self._members.pack(fill="both", expand=True, padx=(6, 0), pady=(0, 6))
        self._members.bind("<Double-Button-1>", self._member_dbl)
        self._members.bind("<Button-3>", self._member_ctx)
        tk.Frame(body, bg=t.border, width=1).pack(side="right", fill="y")
        self._members_frame.pack(side="right", fill="y")
        self._msg_wrap = tk.Frame(body, bg=t.editor_bg)
        self._msg_wrap.pack(side="left", fill="both", expand=True)
        self._build_text(t)

        tk.Frame(main, bg=t.border, height=1).pack(fill="x")
        comp = tk.Frame(main, bg=t.editor_bg)
        comp.pack(fill="x", padx=12, pady=10)
        box = tk.Frame(comp, bg=t.input_bg, highlightthickness=1, highlightbackground=t.border, highlightcolor=t.accent)
        box.pack(side="left", fill="x", expand=True)
        self._box = box
        self._input = tk.Text(box, height=1, wrap="word", bg=t.input_bg, fg=t.fg, insertbackground=t.cursor, relief="flat",
                              font=self._fui, undo=True, padx=10, pady=8, bd=0, highlightthickness=0)
        self._input.pack(fill="x", expand=True)
        self._ph = tk.Label(box, text="", bg=t.input_bg, fg=t.fg_muted, font=self._fui, cursor="xterm")
        self._ph.place(x=11, y=8)
        self._ph.bind("<Button-1>", lambda _e: self._input.focus_set())
        send = tk.Label(comp, text=_t("chat.send", "Enviar"), bg=t.btn_primary, fg=t.btn_primary_fg,
                        font=(FONT_UI, 10, "bold"), padx=16, pady=9, cursor="hand2")
        send.pack(side="left", padx=(8, 0), anchor="s")
        send.bind("<Button-1>", lambda _e: self._on_send())
        inp = self._input
        inp.bind("<Return>", lambda _e: self._on_send() or "break")
        inp.bind("<Shift-Return>", lambda _e: None)
        inp.bind("<Tab>", self._on_tab)
        inp.bind("<ISO_Left_Tab>", lambda _e: "break")
        inp.bind("<Up>", self._hist_up)
        inp.bind("<Down>", self._hist_down)
        inp.bind("<KeyRelease>", self._on_input_change)
        inp.bind("<<Paste>>", lambda _e: self.after(10, self._on_input_change))
        inp.bind("<FocusIn>", lambda _e: self._ph.place_forget())
        inp.bind("<FocusOut>", lambda _e: self._on_input_change())
        self._status = tk.Label(main, text="", bg=t.panel_bg, fg=t.fg_muted, font=(FONT_UI, 8), anchor="w")
        self._status.pack(fill="x", ipady=3, padx=0)
        self._build_empty(t)
        self._bind_keys()
        self._apply_members_visibility()
        self._on_input_change()
        self._draw_side(force=True)

    def _hbtn(self, parent: tk.Widget, text: str, cmd: Callable[[], Any]) -> tk.Label:
        t = self._t
        b = tk.Label(parent, text=text, bg=t.panel_bg, fg=t.fg_muted, font=(FONT_UI, 9), padx=8, pady=3, cursor="hand2")
        b.pack(side="left", padx=1)
        b.bind("<Button-1>", lambda _e: cmd())
        b.bind("<Enter>", lambda _e: b.config(fg=t.fg, bg=t.btn_bg))
        b.bind("<Leave>", lambda _e: b.config(fg=t.fg_muted, bg=t.panel_bg))
        return b

    def _build_text(self, t: Any) -> None:
        txt = tk.Text(self._msg_wrap, bg=t.editor_bg, fg=t.fg, font=self._fui, relief="flat", bd=0, highlightthickness=0,
                      wrap="word", state="disabled", cursor="arrow", padx=14, pady=10, spacing1=2, spacing3=2,
                      insertwidth=0, takefocus=0)
        sb = ttk.Scrollbar(self._msg_wrap, orient="vertical", command=txt.yview, style=self._scroll_style())
        txt.configure(yscrollcommand=lambda a, b: (sb.set(a, b), self._on_scroll()))
        sb.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)
        self._text = txt
        x1, x2 = self._x1, self._x2
        mut = t.fg_muted
        code_bg = blend(t.editor_bg, t.fg, 0.08)
        txt.tag_configure("row", lmargin1=0, lmargin2=x2, tabs=(x1, "right", x2, "left"))
        txt.tag_configure("cont", lmargin1=x2, lmargin2=x2)
        txt.tag_configure("ts", foreground=mut, font=(FONT_UI, 8))
        txt.tag_configure("body", foreground=t.fg)
        txt.tag_configure("hist", foreground=blend(t.fg, t.editor_bg, 0.35))
        txt.tag_configure("nickown", foreground=t.accent, font=(FONT_UI, 10, "bold"))
        txt.tag_configure("action", foreground=t.t_keyword, font=(FONT_UI, 10, "italic"))
        txt.tag_configure("notice", foreground=t.warn_fg)
        txt.tag_configure("sys", foreground=mut, font=(FONT_UI, 9, "italic"))
        txt.tag_configure("err", foreground=t.err_fg, font=(FONT_UI, 9))
        txt.tag_configure("topic", foreground=t.t_func, font=(FONT_UI, 9, "italic"))
        txt.tag_configure("bold", font=(FONT_UI, 10, "bold"))
        txt.tag_configure("code", font=(FONT_MONO, 10), background=code_bg)
        txt.tag_configure("codeblock", font=(FONT_MONO, 10), background=code_bg, lmargin1=x2 + 8, lmargin2=x2 + 8,
                          spacing1=0, spacing3=0)
        txt.tag_configure("url", foreground=t.t_link or t.accent, underline=True)
        txt.tag_configure("mrow", background=blend(t.editor_bg, t.accent, 0.16))
        txt.tag_configure("day", foreground=mut, font=(FONT_UI, 9, "bold"), justify="center", spacing1=10, spacing3=6,
                          lmargin1=0, lmargin2=0)
        txt.tag_configure("found", background=blend(t.editor_bg, t.warn_fg, 0.35))
        txt.tag_configure("foundcur", background=blend(t.editor_bg, t.warn_fg, 0.75), foreground="#000000")
        txt.tag_bind("url", "<Button-1>", self._on_url_click)
        txt.tag_bind("url", "<Enter>", lambda _e: txt.config(cursor="hand2"))
        txt.tag_bind("url", "<Leave>", lambda _e: txt.config(cursor="arrow"))
        txt.tag_bind("nickclick", "<Button-1>", self._on_nick_click)
        txt.tag_raise("sel")
        txt.bind("<Button-1>", lambda _e: txt.focus_set(), add="+")
        txt.bind("<Button-3>", self._text_ctx)
        txt.bind("<Control-c>", lambda _e: self._copy_sel())
        txt.bind("<Control-a>", lambda _e: (txt.tag_add("sel", "1.0", "end"), "break")[1])
        self._jump = tk.Label(self._msg_wrap, text="", bg=t.accent, fg=t.btn_primary_fg, font=(FONT_UI, 9, "bold"),
                              padx=12, pady=4, cursor="hand2")
        self._jump.bind("<Button-1>", lambda _e: self._scroll_end())

    def _scroll_style(self) -> str:
        """Barra de rolagem fina, sem setas, nas cores do tema."""
        t = self._t
        st = ttk.Style(self)
        name = "CodisChat.Vertical.TScrollbar"
        with contextlib.suppress(tk.TclError):
            st.layout(name, [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
                ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
            st.configure(name, troughcolor=t.editor_bg, background=blend(t.editor_bg, t.fg, 0.2), bordercolor=t.editor_bg,
                         lightcolor=blend(t.editor_bg, t.fg, 0.2), darkcolor=blend(t.editor_bg, t.fg, 0.2), relief="flat", width=9, borderwidth=0)
            st.map(name, background=[("active", t.accent), ("pressed", t.accent)])
        return name

    def _build_empty(self, t: Any) -> None:
        self._empty = tk.Frame(self._msg_wrap, bg=t.editor_bg)
        box = tk.Frame(self._empty, bg=t.editor_bg)
        box.place(relx=0.5, rely=0.45, anchor="center")
        self._empty_title = tk.Label(box, text="", bg=t.editor_bg, fg=t.fg, font=(FONT_UI, 16, "bold"))
        self._empty_title.pack()
        self._empty_sub = tk.Label(box, text="", bg=t.editor_bg, fg=t.fg_muted, font=(FONT_UI, 10), justify="center")
        self._empty_sub.pack(pady=(6, 14))
        self._empty_chips = tk.Frame(box, bg=t.editor_bg)
        self._empty_chips.pack()

    def _bind_keys(self) -> None:
        top = self.winfo_toplevel()
        for seq, fn in (("<Control-f>", lambda _e: self._toggle_search(True)),
                        ("<Control-k>", lambda _e: self._quick_switch()),
                        ("<Alt-Up>", lambda _e: self._step_channel(-1)),
                        ("<Alt-Down>", lambda _e: self._step_channel(1))):
            with contextlib.suppress(tk.TclError):
                self._binds.append((seq, top.bind(seq, fn, add="+")))

    def _tick(self) -> None:
        self._after_id = None
        if not self._alive:
            return
        try:
            if not self.winfo_exists():
                return
            self._refresh()
        except tk.TclError:
            return
        except Exception:  # noqa: BLE001
            logger.exception("falha ao atualizar o chat")
        self._after_id = self.after(TICK_MS, self._tick)

    def _refresh(self) -> None:
        changed = False
        cs = self._sess
        if cs and self._cur and cs.active != self._cur[1] and cs.active in cs.channels:
            self._select(self._cur[0], cs.active)
        for n, s in self._sessions.items():
            if self._seen.get(n) != s.version:
                self._seen[n] = s.version
                changed = True
        reconnecting = any(s.status == "reconnecting" for s in self._sessions.values())
        if changed or reconnecting:
            self._draw_side()
            self._update_view()
            self._update_header()
            self._update_members()
            self._notify_unread()
        self._update_empty()

    def _notify_unread(self) -> None:
        cur = self.unread_summary()
        if cur != self._last_unread:
            if cur[1] > self._last_unread[1] >= 0 and self.focus_displayof() is None and self._conf("chat_bell", False):
                with contextlib.suppress(tk.TclError):
                    self.bell()
            self._last_unread = cur
            if self._on_unread:
                with contextlib.suppress(Exception):
                    self._on_unread(*cur)

    def _status_color(self, st: str) -> str:
        t = self._t
        return {"online": t.ok_fg, "connecting": t.warn_fg, "reconnecting": t.warn_fg}.get(st, t.fg_muted)

    def _build_rows(self) -> list[_Row]:
        rows: list[_Row] = []
        y = 4
        for p in self._profiles:
            s = self._sessions.get(p.name)
            u, m = s.unread_totals() if s else (0, 0)
            collapsed = p.name in self._collapsed
            rows.append(_Row("server", p.name, p.name, y, y + 38, (p, s, u, m, collapsed)))
            y += 38
            if s and not collapsed:
                with s.lock:
                    chans = list(s.channels.values())
                order = {"server": 0, "channel": 1, "dm": 2}
                for c in sorted(chans, key=lambda c: (order.get(c.kind, 3), c.name.casefold())):
                    rows.append(_Row("chan", p.name, c.name, y, y + 28, (c.kind, c.unread, c.mentions, c.joined)))
                    y += 28
            y += 6
        return rows

    def _draw_side(self, force: bool = False) -> None:
        if not self._alive:
            return
        rows = self._build_rows()
        def _rsig(r: _Row) -> Any:
            if r.kind == "chan":
                return r.data
            p, sess, u, m, coll = r.data
            return (p.protocol, u, m, coll, sess.status if sess else None)
        sig = (tuple((r.kind, r.sname, r.name, _rsig(r)) for r in rows), self._cur, self._hover, self._side.winfo_width())
        if not force and sig == self._side_sig:
            return
        self._side_sig = sig
        self._rows = rows
        cv, t = self._side, self._t
        w = max(cv.winfo_width(), SIDEBAR_W)
        cv.delete("all")
        if not rows:
            cv.create_text(w // 2, 60, text=_t("chat.no_servers", "Nenhuma conexão ainda."), fill=t.fg_muted,
                           font=(FONT_UI, 10))
            return
        for i, r in enumerate(rows):
            hov = i == self._hover
            if r.kind == "server":
                p, s, u, m, coll = r.data
                st = s.status if s else "offline"
                if hov:
                    _round_rect(cv, 6, r.y0 + 1, w - 6, r.y1 - 1, 8, fill=blend(t.sidebar_bg, t.fg, 0.07), outline="")
                code, color = PROTO_BADGE.get(p.protocol, ("?", t.accent))
                _round_rect(cv, 14, r.y0 + 6, 46, r.y1 - 6, 7, fill=color, outline="")
                cv.create_text(30, (r.y0 + r.y1) // 2, text=code, fill="#FFFFFF", font=(FONT_UI, 8, "bold"))
                cv.create_text(56, r.y0 + 14, text=ellipsize(p.name, 20), anchor="w", fill=t.fg, font=(FONT_UI, 10, "bold"))
                label = {"online": _t("chat.st_online", "online"), "connecting": _t("chat.st_connecting", "conectando…"),
                         "reconnecting": _t("chat.st_reconnecting", "reconectando…")}.get(st, _t("chat.st_offline", "offline"))
                cv.create_oval(56, r.y0 + 25, 63, r.y0 + 32, fill=self._status_color(st), outline="")
                cv.create_text(68, r.y0 + 28, text=label, anchor="w", fill=t.fg_muted, font=(FONT_UI, 8))
                if m or u:
                    self._badge(cv, w - 34, (r.y0 + r.y1) // 2, m or u, bool(m))
                cv.create_text(w - 14, (r.y0 + r.y1) // 2, text="\u25B8" if coll else "\u25BE", fill=t.fg_muted,
                               font=(FONT_UI, 9), anchor="e")
            else:
                kind, unread, mentions, joined = r.data
                active = self._cur == (r.sname, r.name)
                if active:
                    _round_rect(cv, 10, r.y0 + 1, w - 8, r.y1 - 1, 7, fill=t.sel_bg, outline="")
                elif hov:
                    _round_rect(cv, 10, r.y0 + 1, w - 8, r.y1 - 1, 7, fill=blend(t.sidebar_bg, t.fg, 0.06), outline="")
                glyph = {"server": "\u2261", "dm": "@"}.get(kind, "#")
                strong = (unread or mentions) and not active
                fg = t.fg if (active or strong) else (t.fg_muted if joined else blend(t.fg_muted, t.sidebar_bg, 0.4))
                name = _t("chat.server_tab", "Servidor") if r.name == SERVER_TAB else r.name.lstrip("#@")
                cv.create_text(30, (r.y0 + r.y1) // 2, text=glyph, fill=t.fg_muted, font=(FONT_UI, 10, "bold"))
                cv.create_text(44, (r.y0 + r.y1) // 2, text=ellipsize(name, 22), anchor="w", fill=fg,
                               font=(FONT_UI, 10, "bold" if strong else "normal"))
                if (mentions or unread) and not active:
                    self._badge(cv, w - 24, (r.y0 + r.y1) // 2, mentions or unread, bool(mentions))
        cv.configure(scrollregion=(0, 0, w, rows[-1].y1 + 10))

    def _badge(self, cv: tk.Canvas, xr: int, cy: int, n: int, hot: bool) -> None:
        t = self._t
        txt = str(n) if n < 100 else "99+"
        w = 10 + 6 * len(txt)
        _round_rect(cv, xr - w, cy - 8, xr, cy + 8, 8, fill=t.err_fg if hot else t.accent, outline="")
        cv.create_text(xr - w / 2, cy, text=txt, fill="#FFFFFF", font=(FONT_UI, 8, "bold"))

    def _row_at(self, y: int) -> int | None:
        yy = self._side.canvasy(y)
        for i, r in enumerate(self._rows):
            if r.y0 <= yy < r.y1:
                return i
        return None

    def _side_motion(self, e: tk.Event) -> None:
        self._side_set_hover(self._row_at(e.y))

    def _side_set_hover(self, i: int | None) -> None:
        if i != self._hover:
            self._hover = i
            self._side.config(cursor="hand2" if i is not None else "")
            self._draw_side()

    def _side_click(self, e: tk.Event) -> None:
        i = self._row_at(e.y)
        if i is None:
            return
        r = self._rows[i]
        if r.kind == "server":
            w = self._side.winfo_width()
            if e.x > w - 30:
                self._collapsed ^= {r.sname}
                self._draw_side(force=True)
                return
            self._ensure_session(r.data[0])
            s = self._sessions[r.sname]
            self._select(r.sname, s.active if s.active in s.channels else SERVER_TAB)
        else:
            self._select(r.sname, r.name)

    def _side_ctx(self, e: tk.Event) -> None:
        i = self._row_at(e.y)
        if i is None:
            return
        r = self._rows[i]
        m = self._menu()
        if r.kind == "server":
            p, s = r.data[0], r.data[1]
            on = s is not None and s.status in ("online", "connecting", "reconnecting")
            m.add_command(label=_t("chat.reconnect", "Reconectar") if on else _t("chat.connect_server", "Conectar"),
                          command=lambda: self._reconnect(p))
            if on:
                m.add_command(label=_t("chat.disconnect_server", "Desconectar"), command=lambda: self._disconnect(p.name))
                m.add_command(label=_t("chat.join_channel", "Entrar em canal…"), command=lambda: self._join_dialog(p.name))
            m.add_separator()
            m.add_command(label=_t("chat.edit_server", "Editar…"), command=lambda: self._edit(p))
            m.add_command(label=_t("chat.delete_server", "Remover"), command=lambda: self._delete(p.name))
        else:
            s = self._sessions.get(r.sname)
            name = r.name
            if name != SERVER_TAB:
                m.add_command(label=_t("chat.part_channel", "Sair / fechar"),
                              command=lambda: s and s.close_channel(name))
            m.add_command(label=_t("chat.clear_ch", "Limpar mensagens"), command=lambda: s and s.clear_channel(name))
            m.add_command(label=_t("chat.mark_read", "Marcar como lido"), command=lambda: s and s.select(name)
                          if self._cur != (r.sname, name) else None)
        self._popup(m, e)

    def _menu(self) -> tk.Menu:
        t = self._t
        return tk.Menu(self, tearoff=0, bg=t.panel_bg, fg=t.fg, activebackground=t.sel_bg, activeforeground=t.fg,
                       bd=0, relief="flat", font=(FONT_UI, 9))

    def _popup(self, m: tk.Menu, e: tk.Event) -> None:
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    def _on_add(self, protocol: str | None = None) -> None:
        dlg = ChatProfileDialog(self, self._tmgr, taken_names={p.name for p in self._profiles}, protocol=protocol)
        self.wait_window(dlg)
        if dlg.result:
            self._profiles.append(dlg.result)
            _save_chat_profiles(self._profiles)
            self._ensure_session(dlg.result)
            self._select(dlg.result.name, SERVER_TAB)
            self._draw_side(force=True)

    def _edit(self, p: ChatProfile) -> None:
        dlg = ChatProfileDialog(self, self._tmgr, profile=p, taken_names={x.name for x in self._profiles})
        self.wait_window(dlg)
        if not dlg.result:
            return
        idx = self._profiles.index(p)
        if p.name in self._sessions:
            self._disconnect(p.name)
        self._profiles[idx] = dlg.result
        _save_chat_profiles(self._profiles)
        self._draw_side(force=True)
        if messagebox.askyesno(_t("chat.title", "Chat"), _t("chat.reconnect_now", "Conectar com as novas configurações agora?"),
                               parent=self):
            self._ensure_session(dlg.result)

    def _ensure_session(self, p: ChatProfile) -> ChatSession:
        s = self._sessions.get(p.name)
        if s is None:
            s = self._factory(p)
            self._sessions[p.name] = s
        if s.status in ("offline",) and not (s.adapter and s.adapter._thread and s.adapter._thread.is_alive()):
            s.adapter = None
            s.connect()
        return s

    def _reconnect(self, p: ChatProfile) -> None:
        s = self._sessions.get(p.name)
        if s is None:
            self._ensure_session(p)
        else:
            s.reconnect()

    def _disconnect(self, name: str) -> None:
        s = self._sessions.pop(name, None)
        if s:
            s.disconnect()
        if self._cur and self._cur[0] == name:
            self._cur = None
        self._side_sig = None
        self._seen.pop(name, None)

    def _delete(self, name: str) -> None:
        if not messagebox.askyesno(_t("chat.title", "Chat"), _t("chat.confirm_delete", "Remover a conexão '{n}'?", n=name),
                                   parent=self):
            return
        self._disconnect(name)
        self._profiles = [p for p in self._profiles if p.name != name]
        _save_chat_profiles(self._profiles)
        self._draw_side(force=True)

    def _toggle_connection(self) -> None:
        if not self._cur:
            return
        p = next((x for x in self._profiles if x.name == self._cur[0]), None)
        s = self._sess
        if not p:
            return
        if s and s.status in ("online", "connecting", "reconnecting"):
            s.disconnect()
        else:
            self._reconnect(p)

    def _ask(self, title: str, label: str) -> str | None:
        t = self._t
        dlg = tk.Toplevel(self)
        dlg.title(title)
        dlg.configure(bg=t.panel_bg)
        dlg.transient(self.winfo_toplevel())
        dlg.resizable(False, False)
        tk.Label(dlg, text=label, bg=t.panel_bg, fg=t.fg, font=self._fui).pack(padx=18, pady=(16, 6), anchor="w")
        var = tk.StringVar()
        ent = tk.Entry(dlg, textvariable=var, width=38, bg=t.input_bg, fg=t.fg, insertbackground=t.cursor, relief="flat",
                       font=self._fui, highlightthickness=1, highlightbackground=t.border, highlightcolor=t.accent)
        ent.pack(padx=18, ipady=5)
        out: list[str | None] = [None]

        def ok(_e: Any = None) -> None:
            out[0] = var.get().strip() or None
            dlg.destroy()
        ent.bind("<Return>", ok)
        dlg.bind("<Escape>", lambda _e: dlg.destroy())
        bar = tk.Frame(dlg, bg=t.panel_bg)
        bar.pack(fill="x", padx=18, pady=14)
        b = tk.Label(bar, text="OK", bg=t.btn_primary, fg=t.btn_primary_fg, font=(FONT_UI, 10, "bold"), padx=18, pady=5,
                     cursor="hand2")
        b.pack(side="right")
        b.bind("<Button-1>", ok)
        dlg.update_idletasks()
        top = self.winfo_toplevel()
        dlg.geometry(f"+{top.winfo_rootx() + (top.winfo_width() - dlg.winfo_reqwidth()) // 2}+{top.winfo_rooty() + 120}")
        safe_grab_set(dlg)
        ent.focus_set()
        self.wait_window(dlg)
        return out[0]

    def _join_dialog(self, sname: str | None = None) -> None:
        sname = sname or (self._cur[0] if self._cur else None)
        s = self._sessions.get(sname or "")
        if not s:
            return
        proto = s.profile.protocol
        hint = {"irc": "#canal", "ircs": "#canal", "xmpp": "sala@conference.servidor", "xmpps": "sala@conference.servidor",
                "matrix": "#alias:servidor ou !id", "mqtt": "topico/exemplo"}.get(proto, "canal")
        name = self._ask(_t("chat.join_title", "Entrar em canal"), f"Canal ({hint}):")
        if name:
            s.join_channel(name)

    def _select(self, sname: str, ch: str) -> None:
        s = self._sessions.get(sname)
        if not s:
            return
        self._cur = (sname, ch)
        s.select(ch)
        self._view = {}
        self._header_sig = self._members_sig = None
        self._side_sig = None
        self._update_view()
        self._update_header()
        self._update_members()
        self._draw_side(force=True)
        self._on_input_change()
        if self._search_bar.winfo_ismapped():
            self._search_run()
        self._input.focus_set()

    def _all_targets(self) -> list[tuple[str, str]]:
        out = []
        for p in self._profiles:
            s = self._sessions.get(p.name)
            if not s:
                continue
            with s.lock:
                out.append((p.name, SERVER_TAB))
                out.extend((p.name, c.name) for c in sorted(s.channels.values(), key=lambda c: c.name.casefold())
                           if c.kind != "server")
        return out

    def _step_channel(self, d: int) -> str:
        targets = self._all_targets()
        if targets:
            i = targets.index(self._cur) if self._cur in targets else -1
            self._select(*targets[(i + d) % len(targets)])
        return "break"

    def _quick_switch(self) -> str:
        targets = self._all_targets()
        if not targets:
            return "break"
        t = self._t
        dlg = tk.Toplevel(self)
        dlg.overrideredirect(True)
        dlg.configure(bg=t.border)
        inner = tk.Frame(dlg, bg=t.panel_bg)
        inner.pack(padx=1, pady=1)
        var = tk.StringVar()
        ent = tk.Entry(inner, textvariable=var, width=44, bg=t.input_bg, fg=t.fg, insertbackground=t.cursor, relief="flat",
                       font=(FONT_UI, 11))
        ent.pack(fill="x", padx=10, pady=10, ipady=5)
        lb = tk.Listbox(inner, height=9, bg=t.panel_bg, fg=t.fg, relief="flat", highlightthickness=0, activestyle="none",
                        selectbackground=t.sel_bg, selectforeground=t.fg, font=(FONT_UI, 10))
        lb.pack(fill="both", padx=10, pady=(0, 10))
        shown: list[tuple[str, str]] = []

        def fill(*_a: Any) -> None:
            q = var.get().casefold()
            lb.delete(0, "end")
            shown.clear()
            for sn, ch in targets:
                label = f"{ch if ch != SERVER_TAB else 'Servidor'}   ·   {sn}"
                if q in label.casefold():
                    shown.append((sn, ch))
                    lb.insert("end", label)
            if shown:
                lb.selection_set(0)

        def go(_e: Any = None) -> str:
            sel = lb.curselection()
            dlg.destroy()
            if sel:
                self._select(*shown[sel[0]])
            return "break"

        def move(d: int) -> str:
            sel = lb.curselection()
            i = max(0, min(len(shown) - 1, (sel[0] if sel else -1) + d))
            lb.selection_clear(0, "end")
            lb.selection_set(i)
            lb.see(i)
            return "break"
        var.trace_add("write", fill)
        ent.bind("<Return>", go)
        ent.bind("<Down>", lambda _e: move(1))
        ent.bind("<Up>", lambda _e: move(-1))
        ent.bind("<Escape>", lambda _e: dlg.destroy())
        ent.bind("<FocusOut>", lambda _e: self.after(120, lambda: dlg.winfo_exists() and dlg.destroy()))
        lb.bind("<Double-Button-1>", go)
        fill()
        dlg.update_idletasks()
        top = self.winfo_toplevel()
        dlg.geometry(f"+{top.winfo_rootx() + (top.winfo_width() - dlg.winfo_reqwidth()) // 2}+{top.winfo_rooty() + 90}")
        ent.focus_force()
        return "break"

    def _update_header(self) -> None:
        s = self._sess
        sig = (self._cur, s.status if s else None, int(s.retry_at) if s and s.status == "reconnecting" else 0,
               s.channels[self._cur[1]].topic if s and self._cur and self._cur[1] in s.channels else "",
               s.status_detail if s else "")
        if sig == self._header_sig:
            return
        self._header_sig = sig
        if not s or not self._cur:
            self._title.config(text=_t("chat.title", "Chat"))
            self._topic.config(text="")
            self._status_pill.config(text="")
            self._conn_btn.config(text="")
            return
        sname, ch = self._cur
        c = s.channels.get(ch)
        self._title.config(text=(s.profile.name if ch == SERVER_TAB else ch))
        topic = (c.topic if c else "") or (f"{PROTO_LABELS.get(s.profile.protocol, s.profile.protocol)} · "
                                          f"{s.profile.host or s.profile.protocol}" if ch == SERVER_TAB else "")
        self._topic.config(text=ellipsize(topic.replace("\n", " "), 160))
        st = s.status
        txt = {"online": _t("chat.online_long", "Conectado"), "connecting": _t("chat.connecting_long", "Conectando…"),
               "offline": _t("chat.offline_long", "Desconectado")}.get(st, "")
        if st == "reconnecting":
            txt = _t("chat.reconnecting_in", "Reconectando em {n}s…", n=max(0, int(s.retry_at - time.time() + 0.5)))
        dot = "\u25CF "
        self._status_pill.config(text=dot + txt, fg=self._status_color(st))
        self._conn_btn.config(text=_t("chat.disconnect_short", "Desconectar") if st != "offline"
                              else _t("chat.connect_short", "Conectar"))
        self._status.config(text=f"  {s.nick} · {s.profile.name}" + (f" · {s.status_detail}" if st != "online" and s.status_detail else ""))

    def _flash(self, msg: str) -> None:
        self._status.config(text="  " + msg)

    def _update_empty(self) -> None:
        need = None
        if not self._profiles:
            need = ("no_profiles", _t("chat.empty_title", "Converse sem sair do editor"),
                    _t("chat.empty_sub", "Conecte-se a IRC, Matrix, Discord, Slack, Telegram, XMPP, MQTT e mais.\nEscolha um serviço:"),
                    ["irc", "matrix", "discord", "slack", "telegram", "xmpp", "mattermost", "codis"])
        elif not self._cur or not self._sess:
            need = ("no_sel", _t("chat.pick_title", "Selecione uma conversa"),
                    _t("chat.pick_sub", "Clique em uma conexão na barra lateral para conectar.\nCtrl+K abre o alternador rápido."), [])
        if need is None:
            if self._empty.winfo_ismapped():
                self._empty.place_forget()
            return
        if getattr(self, "_empty_key", None) != need[0]:
            self._empty_key = need[0]
            self._empty_title.config(text=need[1])
            self._empty_sub.config(text=need[2])
            for w in self._empty_chips.winfo_children():
                w.destroy()
            for pr in need[3]:
                code, color = PROTO_BADGE[pr]
                lb = tk.Label(self._empty_chips, text=PROTO_LABELS[pr].split(" (")[0], bg=color, fg="#FFFFFF",
                              font=(FONT_UI, 9, "bold"), padx=11, pady=5, cursor="hand2")
                lb.pack(side="left", padx=3, pady=3)
                lb.bind("<Button-1>", lambda _e, p=pr: self._on_add(p))
        self._empty.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._empty.lift()

    def _at_bottom(self) -> bool:
        return self._text.yview()[1] >= 0.995

    def _on_scroll(self) -> None:
        if self._new_while_up and self._at_bottom():
            self._new_while_up = 0
            self._jump.place_forget()

    def _scroll_end(self) -> None:
        self._text.see("end")
        self._new_while_up = 0
        self._jump.place_forget()

    def _update_view(self) -> None:
        s = self._sess
        if not s or not self._cur or self._cur[1] not in s.channels:
            if self._view:
                self._clear_text()
                self._view = {}
            return
        key = self._cur
        ch = s.channels[key[1]]
        v = self._view
        if v.get("key") != key or v.get("joins") != self._show_joins or ch.base > v.get("upto", 0):
            self._clear_text()
            start_abs = max(ch.base, ch.total - RENDER_TAIL)
            self._view = v = {"key": key, "upto": start_abs, "last_nick": None, "last_ts": None, "last_hist": None,
                              "joins": self._show_joins}
            reset = True
        else:
            reset = False
        base, msgs = s.snapshot(key[1], v["upto"])
        if not msgs:
            return
        self._append(msgs, reset)
        v["upto"] = base + len(msgs)

    def _clear_text(self) -> None:
        self._text.config(state="normal")
        self._text.delete("1.0", "end")
        self._text.config(state="disabled")
        self._new_while_up = 0
        self._jump.place_forget()

    def _append(self, msgs: list[Msg], reset: bool) -> None:
        txt, v = self._text, self._view
        stick = reset or self._at_bottom()
        added = 0
        txt.config(state="normal")
        for m in msgs:
            if m.kind in ("join", "part", "nick") and not self._show_joins:
                continue
            if v["last_ts"] is None or not same_day(v["last_ts"], m.ts):
                txt.insert("end", day_label(m.ts) + "\n", ("day",))
                v["last_nick"] = None
            if v["last_hist"] and not m.history:
                txt.insert("end", "\u2014 " + _t("chat.new_msgs", "novas mensagens") + " \u2014\n", ("day",))
            v["last_hist"] = m.history
            self._insert_one(m, v)
            v["last_ts"] = m.ts
            added += 1
        lines = int(txt.index("end-1c").split(".")[0])
        if lines > MAX_LINES:
            txt.delete("1.0", f"{lines - MAX_LINES + 300}.0")
        txt.config(state="disabled")
        if stick:
            txt.see("end")
        elif added:
            self._new_while_up += sum(1 for m in msgs if m.kind in ("msg", "action", "notice"))
            if self._new_while_up:
                self._jump.config(text=f"\u2193 {self._new_while_up} " + _t("chat.new_short", "novas"))
                self._jump.place(relx=0.5, rely=1.0, y=-14, anchor="s")

    def _insert_one(self, m: Msg, v: dict) -> None:
        txt = self._text
        ts = time.strftime("%H:%M", time.localtime(m.ts))
        extra = ("mrow",) if m.mention else ()
        hist = ("hist",) if m.history else ()
        start = txt.index("end-1c")
        if m.kind in ("sys", "err", "join", "part", "nick", "topic"):
            sym = {"join": "\u2192", "part": "\u2190", "err": "!", "nick": "~", "topic": "#"}.get(m.kind, "\u2022")
            who = f"{m.nick} " if m.nick and m.kind != "topic" else ""
            txt.insert("end", ts, ("ts", "row", *extra))
            txt.insert("end", f"\t{sym}\t", ("ts", "row"))
            txt.insert("end", f"{who}{m.text}\n", (m.kind if m.kind in ("err", "topic") else "sys", "row"))
            v["last_nick"] = None
        else:
            own = m.kind == "own"
            group = (v["last_nick"] == (m.nick, own) and v["last_ts"] is not None and m.ts - v["last_ts"] < GROUP_GAP_S
                     and m.kind in ("msg", "own"))
            row = ("row", *extra)
            if group:
                txt.insert("end", "\t\t", row)
            else:
                txt.insert("end", ts, ("ts", *row))
                shown = "*" if m.kind == "action" else ellipsize(m.nick, 14)
                ntags = ("nickown",) if own else (f"nk_{m.nick}",)
                if not own:
                    if m.nick not in self._nick_tags():
                        self._text.tag_configure(f"nk_{m.nick}", foreground=self._nick_fg(m.nick), font=(FONT_UI, 10, "bold"))
                        self._nick_tags().add(m.nick)
                    ntags = (f"nk_{m.nick}", "nickclick")
                if m.kind == "action":
                    ntags = ("action",)
                txt.insert("end", "\t", row)
                txt.insert("end", shown, (*ntags, *row))
                txt.insert("end", "\t", row)
            base = ("body", *hist, *row)
            if m.kind == "action":
                base = ("action", *row)
            elif m.kind == "notice":
                base = ("notice", *row)
            body = f"{m.nick} {m.text}" if m.kind == "action" else m.text
            for seg, tags in tokenize(body):
                txt.insert("end", seg, (*base, *tags))
            txt.insert("end", "\n", base)
            v["last_nick"] = (m.nick, own) if m.kind in ("msg", "own") else None
        l0, l1 = int(start.split(".")[0]), int(txt.index("end-1c").split(".")[0])
        for ln in range(l0 + 1, l1):
            txt.tag_add("cont", f"{ln}.0", f"{ln}.end+1c")
        txt.tag_raise("cont", "row")
        txt.tag_raise("codeblock")
        txt.tag_raise("mrow", "row")
        txt.tag_raise("found")
        txt.tag_raise("foundcur")
        txt.tag_raise("sel")

    def _nick_fg(self, nick: str, bg: str | None = None) -> str:
        return ensure_contrast(nick_color(nick), bg or self._t.editor_bg)

    def _nick_tags(self) -> set[str]:
        if not hasattr(self, "_nk"):
            self._nk: set[str] = set()
        return self._nk

    def _on_url_click(self, e: tk.Event) -> None:
        txt = self._text
        rng = txt.tag_prevrange("url", f"@{e.x},{e.y}+1c")
        if not rng:
            return
        url = normalize_url(txt.get(*rng))
        if url:
            with contextlib.suppress(Exception):
                webbrowser.open(url)

    def _on_nick_click(self, e: tk.Event) -> None:
        txt = self._text
        idx = txt.index(f"@{e.x},{e.y}")
        names = [t for t in txt.tag_names(idx) if t.startswith("nk_")]
        if names:
            self._insert_mention(names[0][3:])

    def _insert_mention(self, nick: str) -> None:
        cur = self._input.get("1.0", "end-1c")
        self._input.insert("end", f"{nick}: " if not cur.strip() else f"{nick} ")
        self._input.focus_set()
        self._on_input_change()

    def _copy_sel(self) -> str:
        with contextlib.suppress(tk.TclError):
            data = self._text.get("sel.first", "sel.last")
            self.clipboard_clear()
            self.clipboard_append(data)
        return "break"

    def _text_ctx(self, e: tk.Event) -> None:
        m = self._menu()
        m.add_command(label=_t("chat.copy", "Copiar"), command=self._copy_sel)
        m.add_checkbutton(label=_t("chat.show_joins", "Mostrar entradas e saídas"), onvalue=True, offvalue=False,
                          variable=tk.BooleanVar(value=self._show_joins), command=self._toggle_joins)
        m.add_command(label=_t("chat.clear_ch", "Limpar mensagens"),
                      command=lambda: self._sess and self._cur and self._sess.clear_channel(self._cur[1]))
        self._popup(m, e)

    def _toggle_joins(self) -> None:
        self._show_joins = not self._show_joins
        with contextlib.suppress(Exception):
            if self._cfg is not None:
                self._cfg.set("chat_show_joins", self._show_joins)
        self._update_view()

    def _toggle_search(self, force_open: bool = False) -> str:
        if self._search_bar.winfo_ismapped() and not force_open:
            self._search_bar.pack_forget()
            self._text.tag_remove("found", "1.0", "end")
            self._text.tag_remove("foundcur", "1.0", "end")
            self._input.focus_set()
        else:
            self._search_bar.pack(fill="x", before=self._body)
            self._search_entry.focus_set()
            self._search_entry.select_range(0, "end")
            self._search_run()
        return "break"

    def _search_run(self) -> None:
        txt = self._text
        txt.tag_remove("found", "1.0", "end")
        txt.tag_remove("foundcur", "1.0", "end")
        self._search_hits = []
        q = self._search_var.get()
        if not q:
            self._search_count.config(text="")
            return
        idx = "1.0"
        while len(self._search_hits) < 1000:
            pos = txt.search(q, idx, "end", nocase=True)
            if not pos:
                break
            end = f"{pos}+{len(q)}c"
            txt.tag_add("found", pos, end)
            self._search_hits.append(pos)
            idx = end
        self._search_i = len(self._search_hits)
        self._search_step(-1)

    def _search_step(self, d: int) -> None:
        n = len(self._search_hits)
        if not n:
            self._search_count.config(text=_t("chat.no_results", "0 resultados") if self._search_var.get() else "")
            return
        self._search_i = (self._search_i + d) % n
        txt, q = self._text, self._search_var.get()
        txt.tag_remove("foundcur", "1.0", "end")
        pos = self._search_hits[self._search_i]
        txt.tag_add("foundcur", pos, f"{pos}+{len(q)}c")
        txt.see(pos)
        self._search_count.config(text=f"{self._search_i + 1}/{n}")

    def _toggle_members(self) -> None:
        self._show_members = not self._show_members
        self._apply_members_visibility()

    def _apply_members_visibility(self) -> None:
        if self._show_members:
            self._members_frame.pack(side="right", fill="y")
        else:
            self._members_frame.pack_forget()

    def _update_members(self) -> None:
        s = self._sess
        if not s or not self._cur:
            return
        ch = s.channels.get(self._cur[1])
        items = s.members(self._cur[1]) if ch and ch.kind == "channel" else []
        if items == self._members_sig:
            return
        self._members_sig = items
        self._members.delete(0, "end")
        for i, (nick, pfx) in enumerate(items):
            mark = {"@": "\u25CF ", "~": "\u25CF ", "&": "\u25CF ", "%": "\u25CF ", "+": "\u25CB "}.get(pfx[:1], "   ")
            self._members.insert("end", f"{mark}{nick}")
            self._members.itemconfig(i, fg=self._nick_fg(nick, self._t.sidebar_bg))
        self._members_title.config(text=(_t("chat.members_n", "MEMBROS — {n}", n=len(items)) if items else
                                         _t("chat.members_u", "MEMBROS")))

    def _member_nick(self) -> str | None:
        sel = self._members.curselection()
        return self._members.get(sel[0]).strip().lstrip("\u25CF\u25CB ").strip() if sel else None

    def _member_dbl(self, _e: tk.Event) -> None:
        nick = self._member_nick()
        if nick:
            self._insert_mention(nick)

    def _member_ctx(self, e: tk.Event) -> None:
        i = self._members.nearest(e.y)
        self._members.selection_clear(0, "end")
        self._members.selection_set(i)
        nick = self._member_nick()
        s = self._sess
        if not nick or not s:
            return
        m = self._menu()
        m.add_command(label=_t("chat.mention", "Mencionar"), command=lambda: self._insert_mention(nick))
        if s.profile.protocol in ("irc", "ircs"):
            m.add_command(label=_t("chat.private", "Conversa privada"), command=lambda: self._select_query(nick))
            m.add_command(label="Whois", command=lambda: s.send(f"/whois {nick}"))
        m.add_command(label=_t("chat.ignore", "Ignorar"), command=lambda: s.send(f"/ignore {nick}"))
        m.add_command(label=_t("chat.copy_nick", "Copiar nick"),
                      command=lambda: (self.clipboard_clear(), self.clipboard_append(nick)))
        self._popup(m, e)

    def _select_query(self, nick: str) -> None:
        s = self._sess
        if s and self._cur:
            s.open_query(nick)
            self._select(self._cur[0], nick)

    def _on_input_change(self, _e: tk.Event | None = None) -> None:
        txt = self._input
        empty = not txt.get("1.0", "end-1c")
        if empty and self.focus_get() is not txt:
            s = self._sess
            ch = self._cur[1] if self._cur else ""
            hint = (_t("chat.ph_channel", "Mensagem para {c}  ·  / para comandos", c=ch if ch != SERVER_TAB else "…")
                    if s and ch and ch != SERVER_TAB else _t("chat.ph_server", "Digite /help ou /join #canal"))
            self._ph.config(text=hint)
            self._ph.place(x=11, y=8)
        else:
            self._ph.place_forget()
        try:
            lines = txt.count("1.0", "end-1c", "displaylines")
            n = max(1, min(6, (lines[0] if lines else 0) + 1))
        except tk.TclError:
            n = 1
        if int(txt.cget("height")) != n:
            txt.config(height=n)
        val = txt.get("1.0", "end-1c")
        s = self._sess
        if val.startswith("/") and " " not in val and "\n" not in val and s:
            cmds = self._commands(s)
            m = [c for c in cmds if c.startswith(val[1:].lower())]
            if m and val != "/":
                self._flash("  ".join("/" + c for c in m[:12]))
            elif val == "/":
                self._flash("  ".join("/" + c for c in cmds[:14]) + " …")

    def _commands(self, s: ChatSession) -> list[str]:
        base = ["help", "join", "part", "close", "query", "me", "clear", "ignore", "unignore", "reconnect",
                "disconnect", "clearhistory"]
        extra = s.adapter.commands() if s.adapter else []
        return sorted(set(base) | set(extra))

    def _on_send(self) -> str:
        s = self._sess
        txt = self._input.get("1.0", "end-1c").rstrip()
        if not txt:
            return "break"
        if not s:
            self._flash(_t("chat.not_connected", "Selecione uma conexão primeiro."))
            return "break"
        n = txt.count("\n") + 1
        if n > 5 and not messagebox.askyesno(_t("chat.title", "Chat"),
                                              _t("chat.multi_confirm", "Enviar {n} linhas de uma vez?", n=n), parent=self):
            return "break"
        self._hist.append(txt)
        self._hist = self._hist[-100:]
        self._hist_i = -1
        self._input.delete("1.0", "end")
        self._comp = None
        ok = s.send(txt)
        if not ok and not txt.startswith("/"):
            pass
        self._update_view()
        self._scroll_end()
        self._on_input_change()
        return "break"

    def _hist_up(self, _e: tk.Event) -> str | None:
        if self._input.compare("insert", ">", "1.end") and self._input.get("1.0", "end-1c"):
            return None
        if not self._hist:
            return "break"
        self._hist_i = len(self._hist) - 1 if self._hist_i < 0 else max(0, self._hist_i - 1)
        self._set_input(self._hist[self._hist_i])
        return "break"

    def _hist_down(self, _e: tk.Event) -> str | None:
        if self._hist_i < 0:
            return None
        self._hist_i += 1
        self._set_input("" if self._hist_i >= len(self._hist) else self._hist[self._hist_i])
        if self._hist_i >= len(self._hist):
            self._hist_i = -1
        return "break"

    def _set_input(self, text: str) -> None:
        self._input.delete("1.0", "end")
        self._input.insert("1.0", text)
        self._on_input_change()

    def _on_tab(self, _e: tk.Event) -> str:
        s = self._sess
        if not s or not self._cur:
            return "break"
        text = self._input.get("1.0", "insert")
        if self._comp and text.endswith(self._comp[0] + self._comp[1][self._comp[2]] + self._comp_suffix):
            base, cands, i = self._comp
            i = (i + 1) % len(cands)
            cur = cands[self._comp[2]] + self._comp_suffix
            self._input.delete(f"insert-{len(cur)}c", "insert")
        else:
            word = text.split(" ")[-1].split("\n")[-1]
            at = word.startswith("@")
            prefix = word.lstrip("@")
            if text.startswith("/") and " " not in text:
                cands = ["/" + c for c in self._commands(s) if c.startswith(text[1:].lower())]
                prefix, base, self._comp_suffix = text, "", " "
                if not cands:
                    return "break"
                self._input.delete("1.0", "insert")
                self._comp = ("", cands, 0)
                self._input.insert("1.0", cands[0] + " ")
                return "break"
            nicks = [n for n, _ in s.members(self._cur[1])]
            cands = [n for n in nicks if n.casefold().startswith(prefix.casefold())] if prefix else []
            if not cands:
                return "break"
            at_start = text.strip() == word
            self._comp_suffix = ": " if at_start and not at else " "
            base, i = ("@" if at else ""), 0
            self._input.delete(f"insert-{len(word)}c", "insert")
        self._comp = (base, cands, i)
        self._input.insert("insert", base + cands[i] + self._comp_suffix)
        return "break"

    _comp_suffix = " "


_ChatProfileDialog = ChatProfileDialog
