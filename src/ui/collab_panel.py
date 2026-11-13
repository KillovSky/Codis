"""Codis - Painel de Colaboração em Tempo Real.

Edição colaborativa estilo Live Share: hospede uma sala (servidor embutido,
WebSocket) ou entre numa sala de outra pessoa. A interface é só este painel;
toda a lógica vive em :mod:`src.collab`:

  * ``ot``          - transformação operacional (convergência provada por fuzz)
  * ``net``         - servidor/cliente WebSocket com ACK e fila de eventos
  * ``session``     - captura de edições locais + integração das remotas
  * ``tk_adapter``  - aplicação incremental no ``tk.Text`` (cursor preservado)

Pontos de projeto:
  * A sala só é criada pelo host; senha obrigatória (gerada se em branco).
  * O painel fica **preso ao editor** escolhido ao hospedar/entrar; trocar de
    aba não mistura arquivos. Ao entrar numa sala abre-se uma aba nova.
  * Nenhum callback de rede toca o Tk: tudo passa por uma fila drenada pela
    thread de interface a cada ~50 ms.

Dependência opcional: ``pip install websockets``.
"""

from __future__ import annotations

import contextlib
import gc
import logging
import secrets
import socket
import time
import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox, ttk
from typing import Any

from src.collab.net import (
    DEFAULT_PORT as _DEFAULT_PORT,
)
from src.collab.net import (
    HAS_WS as _HAS_WS,
)
from src.collab.net import (
    CollabClient,
    CollabServer,
)
from src.collab.net import (
    peer_color as _peer_color,
)
from src.collab.ot import TextOp, apply_op as _apply_op  # noqa: F401
from src.collab.session import CollabSession
from src.collab.tk_adapter import TkTextAdapter
from src.constants import FONT_MONO, FONT_UI
from src.i18n import _t
from src.ui.grab_safe import safe_grab_set

__all__ = ["CollabClient", "CollabPanel", "CollabServer", "TextOp", "_apply_op"]

logger = logging.getLogger("Codis.Collab")

_TICK_MS = 50
_CURSOR_SEND_MS = 150


def _lan_ip() -> str:
    """IP local usado para sair da máquina (sem enviar nada pela rede)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("10.255.255.255", 1))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


class _CursorOverlay:
    """Caret colorido + etiqueta com o nome de cada participante no editor."""

    def __init__(self, text_widget: tk.Text) -> None:
        self._tw = text_widget
        self._tags: dict[str, str] = {}
        self._pos: dict[str, tuple[int, int]] = {}
        self._labels: dict[str, tk.Label] = {}
        self._colors: dict[str, str] = {}

    def update(self, user: str, color: str, line: int, col: int) -> None:
        """Registra a posição do cursor de ``user`` e o redesenha."""
        self._pos[user] = (line, col)
        self._colors[user] = color
        self._draw(user)

    def _draw(self, user: str) -> None:
        tw = self._tw
        line, col = self._pos[user]
        tag = self._tags.setdefault(user, "collab_cursor_" + "".join(
            c if c.isalnum() else "_" for c in user))
        with contextlib.suppress(tk.TclError):
            tw.tag_remove(tag, "1.0", "end")
            idx = tw.index(f"{line + 1}.{col}")
            if tw.get(idx) not in ("", "\n"):
                tw.tag_add(tag, idx, f"{idx}+1c")
                tw.tag_config(tag, background=self._colors[user], foreground="#000000")
                tw.tag_raise(tag)
        self._place_label(user)

    def _place_label(self, user: str) -> None:
        tw = self._tw
        line, col = self._pos[user]
        label = self._labels.get(user)
        try:
            box = tw.bbox(tw.index(f"{line + 1}.{col}"))
        except tk.TclError:
            box = None
        if not box:
            if label is not None:
                label.place_forget()
            return
        if label is None:
            label = tk.Label(tw, text=user, font=(FONT_UI, 7, "bold"), padx=3, pady=0, bd=0)
            self._labels[user] = label
        label.configure(bg=self._colors[user], fg="#000000")
        x, y, _w, h = box
        label.place(x=x, y=max(0, y - h + 2))

    def refresh(self) -> None:
        """Reposiciona as etiquetas (rolagem/edição moveram o texto)."""
        for user in list(self._pos):
            self._place_label(user)

    def remove(self, user: str) -> None:
        """Remove o cursor de ``user``."""
        self._pos.pop(user, None)
        self._colors.pop(user, None)
        tag = self._tags.pop(user, None)
        label = self._labels.pop(user, None)
        with contextlib.suppress(tk.TclError):
            if tag:
                self._tw.tag_remove(tag, "1.0", "end")
                self._tw.tag_delete(tag)
            if label is not None:
                label.destroy()

    def clear(self) -> None:
        """Remove todos os cursores."""
        for user in list(self._pos):
            self.remove(user)

    @property
    def widget(self) -> tk.Text:
        """O editor sobre o qual o overlay desenha."""
        return self._tw


class CollabPanel(tk.Frame):
    """Painel de colaboração em tempo real do Codis.

    Args:
        get_editor_widget: devolve o ``tk.Text`` do editor ativo (usado ao
            hospedar e como alvo quando ``new_editor`` não é fornecido).
        new_editor: cria uma aba nova e devolve seu ``tk.Text``; usado ao
            entrar numa sala para não sobrescrever o arquivo aberto.
    """

    def __init__(
        self,
        parent: tk.Widget,
        tmgr: Any,
        cfg: Any,
        get_editor_widget: Callable[[], tk.Text | None] | None = None,
        new_editor: Callable[[], tk.Text | None] | None = None,
    ) -> None:
        t = tmgr.theme
        super().__init__(parent, bg=t.sidebar_bg)
        self._tmgr = tmgr
        self._cfg = cfg
        self._get_editor = get_editor_widget or (lambda: None)
        self._new_editor = new_editor

        self._server: CollabServer | None = None
        self._client: CollabClient | None = None
        self._session: CollabSession | None = None
        self._adapter: TkTextAdapter | None = None
        self._overlay: _CursorOverlay | None = None
        self._peers: list[dict] = []
        self._chat_history: list[tuple[str, str, str]] = []
        self._hosting = False
        self._room_id = ""
        self._invite = ""
        self._tick_job: str | None = None
        self._last_cursor: tuple[int, int] | None = None
        self._last_cursor_sent = 0.0
        self._last_status_user_ts = 0.0
        self._user: str = (cfg.get("collab_username", "") if cfg else "") or _default_user()

        self._build_ui(t)
        port_cfg = self._cfg.get("collab_port", _DEFAULT_PORT) if self._cfg else _DEFAULT_PORT
        if isinstance(port_cfg, int) and 1 <= port_cfg <= 65535:
            self._port_var.set(str(port_cfg))
            self._join_port_var.set(str(port_cfg))
        self.bind("<Destroy>", self._on_destroy, add="+")

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is not self:
            return
        self._teardown(notify=False)
        if self._server:
            self._server.stop()
            self._server = None
        gc.collect()

    def disconnect(self) -> None:
        """Encerra a sessão (compatível com o gerenciador de processos)."""
        self._on_leave()

    def _teardown(self, notify: bool = True) -> None:
        self._stop_tick()
        if self._session is not None:
            self._session.close()
        self._session = None
        self._client = None
        self._adapter = None
        if self._overlay is not None:
            self._overlay.clear()
            self._overlay = None
        self._last_cursor = None
        if notify:
            self._peers = []
            self._refresh_peers_ui()

    def _start_tick(self) -> None:
        self._stop_tick()
        self._tick_job = self.after(_TICK_MS, self._tick)

    def _stop_tick(self) -> None:
        if self._tick_job is not None:
            with contextlib.suppress(tk.TclError, ValueError):
                self.after_cancel(self._tick_job)
            self._tick_job = None

    def _tick(self) -> None:
        self._tick_job = None
        session = self._session
        if session is None:
            return
        try:
            if self._adapter is not None and not self._adapter.alive():
                self._set_status(_t("collab.editor_closed",
                                    "A aba compartilhada foi fechada. Sessão encerrada."),
                                 online=False)
                self._on_leave(status=False)
                return
            session.tick()
            self._send_cursor_if_changed()
            if self._overlay is not None:
                self._overlay.refresh()
        except tk.TclError:
            return
        except Exception:  # noqa: BLE001
            logger.exception("Falha no ciclo de sincronização do colab")
        if self._session is session and self.winfo_exists():
            self._tick_job = self.after(_TICK_MS, self._tick)

    def _send_cursor_if_changed(self) -> None:
        if not (self._adapter and self._client and self._session and self._session.synced):
            return
        now = time.monotonic()
        if (now - self._last_cursor_sent) * 1000 < _CURSOR_SEND_MS:
            return
        try:
            line_s, col_s = self._adapter.widget.index("insert").split(".", 1)
            cur = (int(line_s) - 1, int(col_s))
        except (tk.TclError, ValueError):
            return
        if cur != self._last_cursor:
            self._last_cursor = cur
            self._last_cursor_sent = now
            self._client.send({"type": "cursor", "line": cur[0], "col": cur[1]})

    def _on_session_event(self, ev: dict) -> None:
        kind = ev.get("type")
        if kind == "connected":
            self._set_status(_t("collab.connected", "Conectado. Sincronizando…"), online=True)
        elif kind == "synced":
            self._on_synced(ev)
        elif kind == "remote_op":
            self._on_remote_edit(str(ev.get("user", "?")))
        elif kind == "cursor":
            self._on_cursor(ev)
        elif kind == "chat":
            self._chat_history.append((str(ev.get("ts", "")), str(ev.get("user", "?")),
                                       str(ev.get("msg", ""))))
            self._refresh_chat()
        elif kind == "peers":
            self._on_peers(ev.get("users", []))
        elif kind == "error":
            self._on_error(str(ev.get("msg", "erro")))
        elif kind == "closed":
            self._on_closed(str(ev.get("msg", "")), bool(ev.get("clean", True)))

    def _on_synced(self, ev: dict) -> None:
        count = len(self._peers)
        text = _t("collab.synced", "Sincronizado - sala {rid}", rid=self._room_id)
        if count:
            text += f" · {count}"
        if ev.get("resync"):
            text = _t("collab.resynced",
                      "Documento ressincronizado com o servidor (edições pendentes descartadas).")
        self._set_status(text, online=True)

    def _on_remote_edit(self, user: str) -> None:
        now = time.monotonic()
        if now - self._last_status_user_ts > 0.5:
            self._last_status_user_ts = now
            self._set_status(_t("collab.editing", "{user} está editando...", user=user),
                             online=True)

    def _on_cursor(self, ev: dict) -> None:
        if self._adapter is None or not self._adapter.alive():
            return
        user = str(ev.get("user", "?"))
        if user == self._user or not (self._cfg.get("collab_show_cursors", True) if self._cfg else True):
            return
        if self._overlay is None or self._overlay.widget is not self._adapter.widget:
            self._overlay = _CursorOverlay(self._adapter.widget)
        with contextlib.suppress(TypeError, ValueError):
            self._overlay.update(user, str(ev.get("color", "#ffffff")),
                                 int(ev.get("line", 0)), int(ev.get("col", 0)))

    def _on_peers(self, users: list) -> None:
        previous = {p.get("user") for p in self._peers}
        self._peers = [u for u in users if isinstance(u, dict)]
        current = {p.get("user") for p in self._peers}
        stamp = time.strftime("%H:%M:%S")
        for name in sorted(current - previous - {self._user}):
            self._chat_history.append((stamp, "->", f"{name} entrou"))
        for name in sorted(previous - current - {self._user}):
            self._chat_history.append((stamp, "<-", f"{name} saiu"))
            if self._overlay is not None:
                self._overlay.remove(str(name))
        if (current - previous) or (previous - current):
            self._refresh_chat()
        self._refresh_peers_ui()

    def _on_error(self, msg: str) -> None:
        messagebox.showerror(_t("collab.connect_err", "Erro de conexão"), msg, parent=self)
        self._teardown()
        self._set_status(msg, online=False)
        self._reset_buttons()

    def _on_closed(self, msg: str, clean: bool) -> None:
        if self._session is None:
            return
        self._teardown()
        self._set_status(
            msg or _t("collab.left", "Sessão encerrada."), online=False)
        self._reset_buttons()
        if not clean and self._hosting:
            self._hosting = False
            self._host_btn.config(state="normal")
            self._stop_btn.config(state="disabled")

    def _reset_buttons(self) -> None:
        self._join_btn.config(state="normal")
        self._leave_btn.config(state="disabled")

    def _current_editor(self) -> tk.Text | None:
        try:
            editor = self._get_editor()
        except Exception:  # noqa: BLE001
            logger.debug("get_editor_widget falhou", exc_info=True)
            return None
        return editor if isinstance(editor, tk.Text) else None

    def _on_host(self) -> None:
        if not _HAS_WS:
            self._show_ws_warning()
            return
        editor = self._current_editor()
        if editor is None:
            messagebox.showwarning(
                _t("collab.title", "Colaboração"),
                _t("collab.no_editor", "Abra um arquivo (ou crie uma aba) para compartilhar."),
                parent=self)
            return
        user = self._user_var.get().strip() or "Host"
        self._user = user
        try:
            port = int(self._port_var.get())
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            messagebox.showwarning(_t("collab.title", "Colaboração"),
                                   _t("collab.bad_port", "Porta inválida (1-65535)."), parent=self)
            return
        password = self._host_pass_var.get().strip()
        if not password:
            password = secrets.token_urlsafe(6)
            self._host_pass_var.set(password)

        max_peers = self._cfg.get("collab_max_peers", 8) if self._cfg else 8
        server = CollabServer(  # noqa: S104
            host="0.0.0.0", port=port,
            max_peers=max_peers if isinstance(max_peers, int) else 8)
        ok, msg = server.start()
        if not ok:
            messagebox.showerror(_t("collab.host_err", "Erro ao hospedar"), msg, parent=self)
            return
        self._server = server
        adapter = TkTextAdapter(editor)
        self._room_id = server.create_room(password=password, initial_content=adapter.get_text())
        self._hosting = True
        ip = _lan_ip()
        self._invite = (f"Codis Colab\nHost: {ip}\nPorta: {port}\n"
                        f"Sala: {self._room_id}\nSenha: {password}")
        self._room_info_var.set(
            _t("collab.room_info", "Sala: {rid}  Porta: {port}\nHost: {ip}  Senha: {pwd}",
               rid=self._room_id, port=port, ip=ip, pwd=password))
        self._host_btn.config(state="disabled")
        self._stop_btn.config(state="normal")
        self._set_status(_t("collab.hosting", "Hospedando sala {rid}", rid=self._room_id),
                         online=True)
        self._start_session("127.0.0.1", port, self._room_id, user, password, adapter)

    def _on_stop_host(self) -> None:
        self._teardown()
        if self._server:
            self._server.stop()
            self._server = None
        self._hosting = False
        self._room_id = ""
        self._invite = ""
        self._room_info_var.set("")
        self._host_btn.config(state="normal")
        self._stop_btn.config(state="disabled")
        self._reset_buttons()
        self._set_status(_t("collab.left", "Sessão encerrada."), online=False)

    def _on_join(self) -> None:
        if not _HAS_WS:
            self._show_ws_warning()
            return
        user = self._user_var.get().strip() or "Guest"
        self._user = user
        host = self._join_host_var.get().strip() or "127.0.0.1"
        try:
            port = int(self._join_port_var.get())
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            messagebox.showwarning(_t("collab.title", "Colaboração"),
                                   _t("collab.bad_port", "Porta inválida (1-65535)."), parent=self)
            return
        room = self._join_room_var.get().strip()
        if not room:
            messagebox.showwarning(_t("collab.missing_title", "Campo obrigatório"),
                                   _t("collab.missing_room", "Informe o ID da sala."), parent=self)
            return
        editor = self._new_editor() if self._new_editor else self._current_editor()
        if editor is None:
            messagebox.showwarning(
                _t("collab.title", "Colaboração"),
                _t("collab.no_editor", "Abra um arquivo (ou crie uma aba) para compartilhar."),
                parent=self)
            return
        if not self._new_editor and editor.get("1.0", "end-1c").strip():
            if not messagebox.askyesno(
                _t("collab.title", "Colaboração"),
                _t("collab.replace_warn",
                   "Entrar na sala substituirá o conteúdo da aba atual.\nContinuar?"),
                parent=self,
            ):
                return
        self._start_session(host, port, room, user, self._join_pass_var.get(),
                            TkTextAdapter(editor))

    def _start_session(self, host: str, port: int, room: str, user: str, password: str,
                       adapter: TkTextAdapter) -> None:
        self._teardown(notify=False)
        self._room_id = room
        self._adapter = adapter
        self._client = CollabClient(host, port, room, user, password)
        self._session = CollabSession(self._client, adapter, self._on_session_event)
        self._client.connect()
        self._join_btn.config(state="disabled")
        self._leave_btn.config(state="normal")
        self._set_status(_t("collab.connecting", "Conectando a {host}:{port} sala {room}...",
                            host=host, port=port, room=room))
        self._start_tick()

    def _on_leave(self, status: bool = True) -> None:
        """Sai da sala; se for o host, também encerra o servidor."""
        if self._hosting:
            self._on_stop_host()
            return
        self._teardown()
        self._reset_buttons()
        if status:
            self._set_status(_t("collab.left", "Sessão encerrada."), online=False)

    def _on_chat_send(self, _event: tk.Event | None = None) -> None:
        msg = self._chat_var.get().strip()
        if not msg or not self._client or not self._client.connected:
            return
        self._client.send({"type": "chat", "msg": msg})
        self._chat_var.set("")

    def _build_ui(self, t: Any) -> None:
        hdr = tk.Frame(self, bg=t.activity_bg, height=48)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(
            hdr, text="🤝", bg=t.activity_bg, fg=t.accent, font=(FONT_UI, 16)
        ).pack(side="left", padx=(14, 4), pady=8)
        tk.Label(
            hdr,
            text=_t("collab.title", "Colaboração"),
            bg=t.activity_bg,
            fg=t.fg,
            font=(FONT_UI, 13, "bold"),
        ).pack(side="left", pady=8)
        self._status_badge = tk.Label(
            hdr,
            text=_t("collab.status_offline", "● offline"),
            bg=t.activity_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        )
        self._status_badge.pack(side="right", padx=12, pady=8)
        tk.Frame(self, bg=t.border, height=1).pack(fill="x")

        id_row = tk.Frame(self, bg=t.panel_bg)
        id_row.pack(fill="x", padx=10, pady=(10, 4))
        tk.Label(
            id_row,
            text=_t("collab.your_name", "Seu nome:"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
        ).pack(side="left")
        self._user_var = tk.StringVar(value=self._user)
        user_entry = tk.Entry(
            id_row,
            textvariable=self._user_var,
            bg=t.editor_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            font=(FONT_MONO, 9),
            width=18,
            bd=4,
        )
        user_entry.pack(side="left", padx=6)

        host_box = tk.LabelFrame(
            self,
            text=_t("collab.host_section", "🖥 Hospedar Sessão"),
            bg=t.panel_bg,
            fg=t.fg,
            font=(FONT_UI, 9, "bold"),
            padx=8,
            pady=6,
        )
        host_box.pack(fill="x", padx=10, pady=4)

        h1 = tk.Frame(host_box, bg=t.panel_bg)
        h1.pack(fill="x", pady=2)
        tk.Label(
            h1,
            text=_t("collab.f_port", "Porta:"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
            width=10,
        ).pack(side="left")
        self._port_var = tk.StringVar(value=str(_DEFAULT_PORT))
        tk.Entry(
            h1,
            textvariable=self._port_var,
            bg=t.editor_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            font=(FONT_MONO, 9),
            width=8,
            bd=4,
        ).pack(side="left", padx=4)

        h2 = tk.Frame(host_box, bg=t.panel_bg)
        h2.pack(fill="x", pady=2)
        tk.Label(
            h2,
            text=_t("collab.f_room_pass", "Password:"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
            width=10,
        ).pack(side="left")
        self._host_pass_var = tk.StringVar()
        tk.Entry(
            h2,
            textvariable=self._host_pass_var,
            bg=t.editor_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            show="*",
            font=(FONT_MONO, 9),
            width=18,
            bd=4,
        ).pack(side="left", padx=4)

        hb = tk.Frame(host_box, bg=t.panel_bg)
        hb.pack(fill="x", pady=(4, 0))
        self._host_btn = self._mk_btn(
            hb, _t("collab.host", "🖥 Hospedar"), self._on_host, accent=True
        )
        self._stop_btn = self._mk_btn(
            hb, _t("collab.stop", "■ Parar"), self._on_stop_host
        )
        self._stop_btn.config(state="disabled")

        self._room_info_var = tk.StringVar()
        self._room_info_lbl = tk.Label(
            host_box,
            textvariable=self._room_info_var,
            bg=t.panel_bg,
            fg=t.t_func,
            font=(FONT_MONO, 9),
        )
        self._room_info_lbl.pack(anchor="w", pady=(4, 0))

        copy_btn = tk.Label(
            host_box,
            text=_t("collab.copy_invite", "📋 Copiar convite"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            cursor="hand2",
        )
        copy_btn.pack(anchor="w")
        copy_btn.bind("<Button-1>", lambda _: self._copy_room_id())

        join_box = tk.LabelFrame(
            self,
            text=_t("collab.join_section", "🔗 Entrar em Sessão"),
            bg=t.panel_bg,
            fg=t.fg,
            font=(FONT_UI, 9, "bold"),
            padx=8,
            pady=6,
        )
        join_box.pack(fill="x", padx=10, pady=4)

        j1 = tk.Frame(join_box, bg=t.panel_bg)
        j1.pack(fill="x", pady=2)
        tk.Label(
            j1,
            text=_t("collab.f_host", "Host:"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
            width=10,
        ).pack(side="left")
        self._join_host_var = tk.StringVar(value="localhost")
        tk.Entry(
            j1,
            textvariable=self._join_host_var,
            bg=t.editor_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            font=(FONT_MONO, 9),
            width=20,
            bd=4,
        ).pack(side="left", padx=4)

        j2 = tk.Frame(join_box, bg=t.panel_bg)
        j2.pack(fill="x", pady=2)
        tk.Label(
            j2,
            text=_t("collab.f_port", "Porta:"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
            width=10,
        ).pack(side="left")
        self._join_port_var = tk.StringVar(value=str(_DEFAULT_PORT))
        tk.Entry(
            j2,
            textvariable=self._join_port_var,
            bg=t.editor_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            font=(FONT_MONO, 9),
            width=8,
            bd=4,
        ).pack(side="left", padx=4)

        j3 = tk.Frame(join_box, bg=t.panel_bg)
        j3.pack(fill="x", pady=2)
        tk.Label(
            j3,
            text=_t("collab.f_room", "ID da sala:"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
            width=10,
        ).pack(side="left")
        self._join_room_var = tk.StringVar()
        tk.Entry(
            j3,
            textvariable=self._join_room_var,
            bg=t.editor_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            font=(FONT_MONO, 9),
            width=12,
            bd=4,
        ).pack(side="left", padx=4)

        j4 = tk.Frame(join_box, bg=t.panel_bg)
        j4.pack(fill="x", pady=2)
        tk.Label(
            j4,
            text=_t("collab.f_room_pass", "Senha:"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
            width=10,
        ).pack(side="left")
        self._join_pass_var = tk.StringVar()
        tk.Entry(
            j4,
            textvariable=self._join_pass_var,
            bg=t.editor_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            show="*",
            font=(FONT_MONO, 9),
            width=18,
            bd=4,
        ).pack(side="left", padx=4)

        jb = tk.Frame(join_box, bg=t.panel_bg)
        jb.pack(fill="x", pady=(4, 0))
        self._join_btn = self._mk_btn(
            jb, _t("collab.join", "🔗 Entrar"), self._on_join, accent=True
        )
        self._leave_btn = self._mk_btn(
            jb, _t("collab.leave", "✖ Sair"), self._on_leave)
        self._leave_btn.config(state="disabled")

        peers_lbl = tk.Label(
            self,
            text=_t("collab.peers", "Participantes"),
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8, "bold"),
        )
        peers_lbl.pack(anchor="w", padx=10, pady=(8, 2))

        self._peers_frame = tk.Frame(
            self, bg=t.panel_bg, highlightthickness=1, highlightbackground=t.border
        )
        self._peers_frame.pack(fill="x", padx=10, pady=(0, 4))
        self._peers_placeholder = tk.Label(
            self._peers_frame,
            text=_t("collab.no_peers", "Nenhum participante ainda."),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        )
        self._peers_placeholder.pack(pady=8)

        tk.Label(
            self,
            text=_t("collab.chat", "Chat da Sessão"),
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8, "bold"),
        ).pack(anchor="w", padx=10, pady=(4, 2))

        chat_frame = tk.Frame(
            self, bg=t.editor_bg, highlightthickness=1, highlightbackground=t.border
        )
        chat_frame.pack(fill="both", expand=True, padx=10, pady=(0, 4))

        self._chat_text = tk.Text(
            chat_frame,
            bg=t.editor_bg,
            fg=t.fg,
            font=(FONT_MONO, 9),
            relief="flat",
            bd=4,
            state="disabled",
            wrap="word",
            height=8,
        )
        chat_vsb = ttk.Scrollbar(
            chat_frame, orient="vertical", command=self._chat_text.yview
        )
        self._chat_text.configure(yscrollcommand=chat_vsb.set)
        chat_vsb.pack(side="right", fill="y")
        self._chat_text.pack(fill="both", expand=True)
        self._chat_text.tag_config(
            "ts", foreground=t.fg_muted, font=(FONT_MONO, 7))
        self._chat_text.tag_config(
            "sys", foreground=t.t_func, font=(FONT_MONO, 8, "italic")
        )
        self._chat_text.tag_config(
            "own", foreground=t.accent, font=(FONT_MONO, 9, "bold")
        )
        self._chat_text.tag_config("peer", font=(FONT_MONO, 9, "bold"))
        self._chat_text.tag_config("msg", foreground=t.fg, font=(FONT_MONO, 9))

        chat_inp = tk.Frame(self, bg=t.panel_bg)
        chat_inp.pack(fill="x", padx=10, pady=(0, 6))
        self._chat_var = tk.StringVar()
        chat_entry = tk.Entry(
            chat_inp,
            textvariable=self._chat_var,
            bg=t.editor_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            font=(FONT_MONO, 9),
            bd=6,
        )
        chat_entry.pack(side="left", fill="x", expand=True)
        chat_entry.bind("<Return>", self._on_chat_send)
        send_lbl = tk.Label(
            chat_inp,
            text="↑",
            bg=t.accent,
            fg="#FFF",
            font=(FONT_UI, 10, "bold"),
            padx=8,
            cursor="hand2",
        )
        send_lbl.pack(side="right")
        send_lbl.bind("<Button-1>", self._on_chat_send)

        sb = tk.Frame(
            self, bg=t.sidebar_bg, highlightthickness=1, highlightbackground=t.border
        )
        sb.pack(fill="x")
        self._status_var = tk.StringVar(
            value=_t("collab.status_idle", "Sem sessão ativa.")
        )
        tk.Label(
            sb,
            textvariable=self._status_var,
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            anchor="w",
        ).pack(fill="x", padx=8, pady=4)

    def _mk_btn(
        self, parent: tk.Frame, text: str, cmd: Callable, accent: bool = False
    ) -> tk.Button:
        t = self._tmgr.theme
        bg = t.accent if accent else t.sidebar_bg
        fg = "#FFF" if accent else t.fg
        btn = tk.Button(
            parent,
            text=text,
            command=cmd,
            bg=bg,
            fg=fg,
            relief="flat",
            font=(FONT_UI, 9),
            cursor="hand2",
            padx=8,
            pady=5,
            activebackground=t.btn_hover,
        )
        btn.pack(side="left", padx=2)
        return btn

    def _set_status(self, msg: str, online: bool | None = None) -> None:
        self._status_var.set(msg)
        if online is not None:
            t = self._tmgr.theme
            if online:
                self._status_badge.config(
                    text=_t("collab.status_online", "● online"), fg=t.t_func
                )
            else:
                self._status_badge.config(
                    text=_t("collab.status_offline", "● offline"), fg=t.fg_muted
                )

    def _refresh_chat(self) -> None:
        self._chat_text.config(state="normal")
        self._chat_text.delete("1.0", "end")
        for ts, user, msg in self._chat_history[-200:]:
            self._chat_text.insert("end", f"[{ts}] ", "ts")
            if user in ("ok", "->", "<-", "!!"):
                self._chat_text.insert("end", f"{user} {msg}\n", "sys")
            elif user == self._user:
                self._chat_text.insert("end", f"{user}: ", "own")
                self._chat_text.insert("end", f"{msg}\n", "msg")
            else:
                color = _peer_color(user)
                self._chat_text.tag_config(
                    f"peer_{user}", foreground=color, font=(FONT_MONO, 9, "bold")
                )
                self._chat_text.insert("end", f"{user}: ", f"peer_{user}")
                self._chat_text.insert("end", f"{msg}\n", "msg")
        self._chat_text.see("end")
        self._chat_text.config(state="disabled")

    def _refresh_peers_ui(self) -> None:
        t = self._tmgr.theme
        for w in self._peers_frame.winfo_children():
            w.destroy()
        if not self._peers:
            tk.Label(
                self._peers_frame,
                text=_t("collab.no_peers", "Nenhum participante ainda."),
                bg=t.panel_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 8),
            ).pack(pady=6)
            return
        for peer in self._peers:
            user = peer.get("user", "?")
            color = peer.get("color", "#888")
            row = tk.Frame(self._peers_frame, bg=t.panel_bg)
            row.pack(fill="x", padx=6, pady=2)
            dot = tk.Label(row, text="●", bg=t.panel_bg,
                           fg=color, font=(FONT_UI, 10))
            dot.pack(side="left")
            you = " " + _t("collab.you",
                           "(você)") if user == self._user else ""
            tk.Label(
                row, text=user + you, bg=t.panel_bg, fg=t.fg, font=(FONT_UI, 9)
            ).pack(side="left", padx=4)

    def _copy_room_id(self) -> None:
        """Copia o convite completo (host, porta, sala e senha)."""
        if not self._room_id:
            return
        text = self._invite or self._room_id
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self._set_status(_t("collab.copied_invite", "Convite copiado: sala {rid}", rid=self._room_id))
        except tk.TclError:
            logger.debug("Falha ao copiar o convite", exc_info=True)

    def _show_ws_warning(self) -> None:
        t = self._tmgr.theme
        win = tk.Toplevel(self)
        win.title(_t("collab.no_ws_title", "websockets não instalado"))
        win.config(bg=t.panel_bg)
        win.resizable(False, False)
        safe_grab_set(win)
        tk.Label(
            win,
            text=_t(
                "collab.no_ws_warning",
                "⚠ Colaboração requer o pacote websockets.\n\n"
                "Instale com:\n  pip install websockets\n\n"
                "Depois reinicie o Codis.",
            ),
            bg=t.panel_bg,
            fg=t.fg,
            font=(FONT_UI, 10),
            justify="left",
            padx=20,
            pady=16,
        ).pack()

        def _copy_cmd() -> None:
            win.clipboard_clear()
            win.clipboard_append("pip install websockets")

        tk.Button(
            win,
            text=_t("collab.install_ws", "📋 Copiar comando pip"),
            command=_copy_cmd,
            bg=t.accent,
            fg="#FFF",
            relief="flat",
            font=(FONT_UI, 9),
            padx=12,
            pady=6,
        ).pack(pady=(0, 10))
        tk.Button(
            win,
            text=_t("collab.close", "Fechar"),
            command=win.destroy,
            bg=t.sidebar_bg,
            fg=t.fg,
            relief="flat",
            font=(FONT_UI, 9),
            padx=12,
        ).pack(pady=(0, 12))


def _default_user() -> str:
    import os

    return os.environ.get("USER", os.environ.get("USERNAME", "CodisUser"))
