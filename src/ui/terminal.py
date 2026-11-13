"""Terminal integrado do Codis.

``TerminalPanel`` é um terminal em tempo real: cada aba é um shell rodando num
pseudo-terminal, com emulação VT100/xterm (cursor, cores, tela alternativa,
mouse), de modo que ``vim``, ``less``, ``htop``, o histórico e o Tab do próprio
shell funcionam como no VS Code. A área de texto recebe as teclas diretamente.

A lógica pesada fica em ``src.terminal``: emulador (``vt``), backends de PTY
(``backends``), sessões (``session``) e a visão Tk (``view``).
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Any

from src.config import ConfigManager
from src._shells import SHELL_OPTIONS, fast_which
from src.constants import (
    FONT_MAX,
    FONT_MIN,
    FONT_MONO,
    FONT_SIZE,
    FONT_UI,
)
from src.terminal.session import DEFAULT_HISTORY, TerminalSession
from src.terminal.shells import (
    cd_command,
    echo_command,
    run_file_command,
    shell_kind,
)
from src.terminal.view import TerminalView
from src.theme.base import Theme
from src.theme.manager import ThemeManager
from src.ui.dropdown import DropDown

logger = logging.getLogger("Codis")

_EGG_PREFIX = "/codis "
_EGG_HINT = "happybirthday, 1123581321, 20250101, opensesame"
_INTERPRETERS = {
    ".py": "python", ".js": "node", ".mjs": "node", ".sh": "bash",
    ".rb": "ruby", ".lua": "lua", ".ts": "ts-node",
}


class _KeyTap:
    """Fachada do widget do terminal para quem só quer observar o teclado.

    O terminal consome todas as teclas (``break``), o que impediria bindings
    ``add=True`` comuns de rodarem. Pedidos para ``<Key>`` viram observadores
    chamados antes do envio ao shell; o resto é repassado ao widget.
    """

    def __init__(self, view: TerminalView) -> None:
        self._view = view

    def bind(self, sequence: str | None = None, func: Any = None, add: Any = None) -> Any:
        if sequence == "<Key>" and func is not None:
            self._view.add_key_observer(func)
            return ""
        return self._view.bind(sequence, func, add)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._view, name)


class TerminalPanel(tk.Frame):
    """Painel de terminal com abas, uma sessão PTY por aba."""

    def __init__(
        self,
        parent: tk.Widget,
        tmgr: ThemeManager,
        cfg: ConfigManager,
        cwd: str | None = None,
    ) -> None:
        t = tmgr.theme
        super().__init__(parent, bg=t.terminal_bg)
        self._tmgr = tmgr
        self._cfg = cfg
        self._cwd = cwd or str(Path.home())
        self._sessions: list[TerminalSession] = []
        self._commands: list[list[str]] = []
        self._active = -1
        self._alive = True
        self._counter = 0
        self._font_size: int = int(cfg.get("terminal_font_size", FONT_SIZE - 1))
        self._easter_egg_handler: Any = None
        self._typed = ""
        self._typed_valid = True
        self._tick_job: str | None = None
        self._last_activity = time.monotonic()
        self._tab_signature: tuple = ()
        self._needs_render = False
        self._build()
        self.new_shell()
        self._schedule_tick(1)

    def _build(self) -> None:
        t = self._tmgr.theme
        self._tabbar = tk.Frame(self, bg=t.tabbar_bg, height=28)
        self._tabbar.pack(fill="x", side="top")
        self._tabbar.pack_propagate(False)

        self._shell_var = tk.StringVar()
        avail = [lbl for lbl, cmd in SHELL_OPTIONS.items() if fast_which(cmd[0])]
        if not avail:
            avail = list(SHELL_OPTIONS.keys())[:1]
        self._shell_var.set(avail[0] if avail else "")
        DropDown(
            self._tabbar,
            textvariable=self._shell_var,
            values=avail,
            width=16,
            font=(FONT_UI, 8),
            state="readonly",
        ).pack(side="left", padx=4, pady=3)

        self._tabs_frame = tk.Frame(self._tabbar, bg=t.tabbar_bg)
        self._tabs_frame.pack(side="left", fill="y")

        self._font_lbl = tk.Label(
            self._tabbar, text=f"  {self._font_size}px", bg=t.tabbar_bg,
            fg=t.fg_muted, font=(FONT_UI, 8),
        )
        self._font_lbl.pack(side="right", padx=4)

        self._toolbar_buttons: list[tk.Label] = []
        for text, command in (
            ("\uff0b", self.new_shell),
            ("\u2715", self.kill_active),
            ("\u232b", self.clear_active),
        ):
            btn = tk.Label(
                self._tabbar, text=text, bg=t.tabbar_bg, fg=t.fg,
                font=(FONT_UI, 10), cursor="hand2", padx=6,
            )
            btn.pack(side="right")
            btn.bind("<Button-1>", lambda _e, c=command: c())
            btn.bind("<Enter>", lambda _e, b=btn: b.config(bg=self._tmgr.theme.btn_hover))
            btn.bind("<Leave>", lambda _e, b=btn: b.config(bg=self._tmgr.theme.tabbar_bg))
            self._toolbar_buttons.append(btn)

        self._sep = tk.Frame(self, bg=t.border, height=1)
        self._sep.pack(fill="x", side="top")

        body = tk.Frame(self, bg=t.terminal_bg)
        body.pack(fill="both", expand=True, side="top")
        self._body = body
        self._view = TerminalView(
            body,
            font_family=FONT_MONO,
            font_size=self._font_size,
            history=int(self._cfg.get("terminal_scrollback", DEFAULT_HISTORY)),
            on_resize=self._wake,
            on_input=self._wake,
        )
        vsb = ttk.Scrollbar(
            body, orient="vertical", command=self._view.yview,
            style="Thin.Vertical.TScrollbar",
        )
        vsb.pack(side="right", fill="y")
        self._view.config(yscrollcommand=vsb.set)
        self._view.pack(side="left", fill="both", expand=True)
        self._view.set_line_filter(self._filter_input)
        self._view.set_restart_handler(self._restart_active)
        self._input = _KeyTap(self._view)
        self._apply_view_theme(t)

        self._view.bind("<Control-MouseWheel>", self._on_scroll_zoom)
        self._view.bind("<Control-Button-4>", lambda e: self._on_scroll_zoom(e, +1))
        self._view.bind("<Control-Button-5>", lambda e: self._on_scroll_zoom(e, -1))
        self._view.bind("<Button-3>", self._show_menu)

        self._menu = tk.Menu(self._view, tearoff=0)
        self._menu.add_command(label="Copiar", command=self._view.copy_selection)
        self._menu.add_command(label="Colar", command=self._view.paste)
        self._menu.add_command(label="Selecionar tudo", command=self._view.select_all)
        self._menu.add_separator()
        self._menu.add_command(label="Limpar", command=self.clear_active)
        self._menu.add_command(label="Novo terminal", command=self.new_shell)

    def _apply_view_theme(self, t: Theme) -> None:
        self._view.apply_colors(
            fg=t.terminal_fg, bg=t.terminal_bg, cursor=t.cursor,
            sel_bg=t.sel_bg, sel_fg=t.sel_fg,
        )

    def _show_menu(self, event: tk.Event) -> str:
        self._view.focus_set()
        with contextlib.suppress(tk.TclError):
            self._menu.tk_popup(event.x_root, event.y_root)
        return "break"

    def _on_scroll_zoom(self, event: Any, direction: int = 0) -> str:
        """Zoom da fonte com Ctrl+rolagem; o PTY é redimensionado para o novo tamanho."""
        if direction == 0:
            direction = 1 if getattr(event, "delta", 0) > 0 else -1
        self._font_size = max(FONT_MIN, min(FONT_MAX, self._font_size + direction))
        self._view.set_font_size(self._font_size)
        self._font_lbl.config(text=f"  {self._font_size}px")
        self._cfg.set("terminal_font_size", self._font_size)
        return "break"

    def _schedule_tick(self, delay: int) -> None:
        if not self._alive:
            return
        if self._tick_job is not None:
            with contextlib.suppress(tk.TclError):
                self.after_cancel(self._tick_job)
        with contextlib.suppress(tk.TclError):
            self._tick_job = self.after(delay, self._tick)

    def _wake(self) -> None:
        """Acelera o próximo ciclo (chamado ao digitar) para o eco aparecer na hora."""
        self._last_activity = time.monotonic()
        self._schedule_tick(2)

    def _tick(self) -> None:
        self._tick_job = None
        if not self._alive:
            return
        try:
            if not self.winfo_exists():
                return
            visible = bool(self.winfo_viewable())
        except tk.TclError:
            return

        active = self._current()
        activity = pending = False
        for session in self._sessions:
            if session.pump(budget=0.003 if session is active else 0.001):
                activity = True
            pending = pending or session.pending or session.backlog > 0
        self._needs_render = self._needs_render or activity
        if visible and active is not None and (self._needs_render or active.backlog):
            self._needs_render = False
            self._view.render()
        self._refresh_tabs_if_changed()

        now = time.monotonic()
        if activity or pending:
            self._last_activity = now
        if pending:
            delay = 2
        elif now - self._last_activity < 0.5:
            delay = 8
        else:
            delay = 40 if visible else 120
        self._schedule_tick(delay)

    def _current(self) -> TerminalSession | None:
        if 0 <= self._active < len(self._sessions):
            return self._sessions[self._active]
        return None

    def _command_for(self, label: str) -> list[str]:
        cmd = SHELL_OPTIONS.get(label, [])
        if cmd:
            return list(cmd)
        fallback = os.environ.get("COMSPEC" if sys.platform == "win32" else "SHELL")
        return [fallback or ("cmd.exe" if sys.platform == "win32" else "/bin/sh")]

    def _make_session(self, label: str, cmd: list[str]) -> TerminalSession:
        rows, cols = (24, 80)
        if self._view.winfo_width() > 100:
            rows, cols = self._view.grid_size()
        self._counter += 1
        return TerminalSession(
            f"{label} {self._counter}",
            cmd,
            self._cwd,
            rows,
            cols,
            load_rc=bool(self._cfg.get("terminal_load_shell_rc", True)),
            prefer_conpty=bool(self._cfg.get("terminal_conpty", True)),
            history=int(self._cfg.get("terminal_scrollback", DEFAULT_HISTORY)),
        )

    def new_shell(self) -> None:
        """Abre uma nova aba com o shell selecionado."""
        cmd = self._command_for(self._shell_var.get())
        session = self._make_session(self._shell_var.get() or Path(cmd[0]).name, cmd)
        self._sessions.append(session)
        self._commands.append(cmd)
        self._switch_shell(len(self._sessions) - 1)

    def _restart_active(self) -> None:
        """Reinicia o shell da aba ativa depois que ele terminou."""
        idx = self._active
        if not 0 <= idx < len(self._sessions):
            return
        old = self._sessions[idx]
        label = old.label.rsplit(" ", 1)[0]
        old.terminate()
        self._sessions[idx] = self._make_session(label, self._commands[idx])
        self._switch_shell(idx)

    def _switch_shell(self, idx: int) -> None:
        self._active = idx
        session = self._current()
        self._view.attach(session)
        self._rebuild_tabs()
        self._typed, self._typed_valid = "", True
        with contextlib.suppress(tk.TclError):
            self._view.focus_set()
        self._wake()

    def kill_active(self) -> None:
        """Encerra o shell da aba ativa e remove a aba."""
        session = self._current()
        if session is None:
            return
        session.terminate()
        idx = self._active
        self._sessions.pop(idx)
        self._commands.pop(idx)
        self._active = min(idx, len(self._sessions) - 1)
        self._view.attach(self._current())
        self._rebuild_tabs()

    def clear_active(self) -> None:
        """Limpa a tela e o histórico da aba ativa, sem encerrar o shell."""
        session = self._current()
        if session is None:
            return
        session.clear()
        self._view.attach(session)
        self._wake()

    def _tab_title(self, session: TerminalSession) -> str:
        title = session.screen.title.strip()
        return f"{session.label.rsplit(' ', 1)[0]}: {title[:24]}" if title else session.label

    def _refresh_tabs_if_changed(self) -> None:
        signature = tuple((s.label, s.screen.title, s.exited) for s in self._sessions) + (self._active,)
        if signature != self._tab_signature:
            self._rebuild_tabs()

    def _rebuild_tabs(self) -> None:
        t = self._tmgr.theme
        self._tab_signature = tuple(
            (s.label, s.screen.title, s.exited) for s in self._sessions
        ) + (self._active,)
        for w in self._tabs_frame.winfo_children():
            w.destroy()
        for i, session in enumerate(self._sessions):
            active = i == self._active
            dot = "\u25cf " if not session.exited else "\u25cb "
            lbl = tk.Label(
                self._tabs_frame,
                text=f"  {dot}{self._tab_title(session)}  ",
                bg=t.tab_act_bg if active else t.tab_in_bg,
                fg=t.tab_act_fg if active else t.tab_in_fg,
                font=(FONT_UI, 8), cursor="hand2", relief="flat",
            )
            lbl.pack(side="left")
            lbl.bind("<Button-1>", lambda _e, idx=i: self._switch_shell(idx))
            lbl.bind("<Button-2>", lambda _e, idx=i: self._close_tab(idx))

    def _close_tab(self, idx: int) -> None:
        if 0 <= idx < len(self._sessions):
            self._active = idx
            self.kill_active()

    def set_easter_egg_handler(self, handler: Any) -> None:
        """Define o callable que intercepta easter eggs digitados no terminal.

        O handler recebe a linha digitada e devolve ``True`` se foi um easter
        egg (a linha então não é enviada ao shell).
        """
        self._easter_egg_handler = handler

    def _filter_input(self, data: bytes, keysym: str) -> bool:
        """Acompanha a linha digitada; consome o Enter de comandos ``/codis``."""
        if keysym in ("Return", "KP_Enter"):
            line, valid = self._typed.strip(), self._typed_valid
            self._typed, self._typed_valid = "", True
            if valid and self._easter_egg_handler and line.lower().startswith(_EGG_PREFIX):
                self._run_easter_egg(line)
                return True
            return False
        if keysym == "BackSpace":
            self._typed = self._typed[:-1]
        elif data in (b"\x03", b"\x15", b"\x04", b"\x1b"):
            self._typed, self._typed_valid = "", True
        elif data.startswith(b"\x1b") or data == b"\t":
            self._typed_valid = False
        else:
            self._typed += data.decode("utf-8", errors="ignore")
        return False

    def _run_easter_egg(self, line: str) -> None:
        session = self._current()
        handled = False
        with contextlib.suppress(AttributeError, RuntimeError):
            handled = bool(self._easter_egg_handler(line))
        if session is None:
            return
        kind = shell_kind(session.cmd[0]) if session.cmd else "posix"
        if handled:
            session.type_input(b"\x15")
        else:
            session.type_input(b"\x15" + echo_command(kind, _EGG_HINT).encode() + b"\r")

    def run_command(self, cmd: str) -> None:
        """Digita ``cmd`` no shell ativo e pressiona Enter."""
        session = self._current()
        if session is None or session.exited:
            return
        if self._easter_egg_handler and cmd.strip().lower().startswith(_EGG_PREFIX):
            self._typed = cmd
            self._typed_valid = True
            self._filter_input(b"\r", "Return")
            return
        text = cmd.replace("\r\n", "\n").replace("\n", "\r")
        session.type_input(text.encode("utf-8", errors="replace") + b"\r")
        self._wake()

    def set_cwd(self, path: str) -> None:
        """Define o diretório de trabalho de novas abas e faz ``cd`` no shell ativo."""
        self._cwd = path
        session = self._current()
        if session is not None and not session.exited and session.cmd:
            self.run_command(cd_command(shell_kind(session.cmd[0]), path))

    def run_file(self, filepath: str, interpreter: str = "") -> None:
        """Executa um arquivo, escolhendo o interpretador pela extensão."""
        interpreter = interpreter or _INTERPRETERS.get(Path(filepath).suffix.lower(), "")
        session = self._current()
        kind = shell_kind(session.cmd[0]) if session and session.cmd else "posix"
        self.run_command(run_file_command(kind, interpreter, filepath))

    def kill_all(self) -> None:
        """Encerra todos os shells abertos."""
        for session in list(self._sessions):
            session.terminate()

    def apply_theme(self, t: Theme) -> None:
        """Aplica o tema ao painel e ao terminal."""
        from src.theme.utils import recolor

        recolor(
            {
                self: {"bg": t.terminal_bg},
                self._body: {"bg": t.terminal_bg},
                self._tabbar: {"bg": t.tabbar_bg},
                self._tabs_frame: {"bg": t.tabbar_bg},
                self._font_lbl: {"bg": t.tabbar_bg, "fg": t.fg_muted},
                self._sep: {"bg": t.border},
            }
        )
        for btn in self._toolbar_buttons:
            btn.config(bg=t.tabbar_bg, fg=t.fg)
        self._apply_view_theme(t)
        self._rebuild_tabs()

    def destroy(self) -> None:
        """Encerra todos os shells e libera os recursos."""
        self._alive = False
        if self._tick_job is not None:
            with contextlib.suppress(tk.TclError):
                self.after_cancel(self._tick_job)
            self._tick_job = None
        for session in self._sessions:
            session.terminate()
        super().destroy()
