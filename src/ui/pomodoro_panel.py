"""Codis - Native Pomodoro Timer panel.

25 min work / 5 min short break / 15 min long break.
Tracks completed sessions, shows status-bar notifications.
Fully i18n-aware and theme-integrated.
"""

from __future__ import annotations

import contextlib
import threading
import time
import tkinter as tk
from collections.abc import Callable

from src.constants import FONT_MONO, FONT_UI
from src.i18n import _t
from src.theme.manager import ThemeManager

_LONG_BREAK_EVERY = 4


class PomodoroPanel(tk.Frame):
    """Native sidebar Pomodoro timer panel."""

    def __init__(
        self,
        parent: tk.Widget,
        tmgr: ThemeManager,
        on_message: Callable[[str, int], None] | None = None,
    ) -> None:
        """Cria o painel do timer Pomodoro.

        Args:
            parent: Widget pai Tkinter.
            config: Instância de ConfigManager.
        """
        t = tmgr.theme
        super().__init__(parent, bg=t.panel_bg)
        self._tmgr = tmgr
        self._on_message = on_message

        self._mode = "work"
        self._remaining = 25 * 60
        self._running = False
        self._completed = 0
        self._history: list[str] = []
        self._stop_ev = threading.Event()
        self._thread: threading.Thread | None = None

        self._work_var = tk.IntVar(value=25)
        self._break_var = tk.IntVar(value=5)

        self._build()
        self._refresh()

    def _build(self) -> None:
        """Constrói os widgets e layout interno do componente."""
        t = self._tmgr.theme

        for child in self.winfo_children():
            with contextlib.suppress(tk.TclError):
                child.destroy()

        hdr = tk.Frame(self, bg=t.panel_bg)
        hdr.pack(fill="x", padx=8, pady=(8, 0))
        tk.Label(
            hdr,
            text=f"⏱  {_t('pomodoro.title')}",
            bg=t.panel_bg,
            fg=t.fg,
            font=(FONT_UI, 11, "bold"),
        ).pack(side="left")

        self._mode_var = tk.StringVar(value=_t("pomodoro.focus"))
        self._mode_lbl = tk.Label(
            self,
            textvariable=self._mode_var,
            bg=t.panel_bg,
            fg=t.accent,
            font=(FONT_UI, 9, "bold"),
        )
        self._mode_lbl.pack(pady=(10, 0))

        self._clock_var = tk.StringVar(value="25:00")
        self._clock_lbl = tk.Label(
            self,
            textvariable=self._clock_var,
            bg=t.panel_bg,
            fg=t.accent,
            font=(FONT_MONO, 40, "bold"),
        )
        self._clock_lbl.pack(pady=(2, 4))

        prog_frame = tk.Frame(self, bg=t.panel_bg)
        prog_frame.pack(fill="x", padx=24, pady=(0, 6))
        self._prog_bg = tk.Frame(prog_frame, bg=t.border, height=6)
        self._prog_bg.pack(fill="x")
        self._prog_fill = tk.Frame(self._prog_bg, bg=t.accent, height=6)
        self._prog_fill.place(x=0, y=0, relheight=1.0, relwidth=0.0)

        dot_row = tk.Frame(self, bg=t.panel_bg)
        dot_row.pack(pady=(0, 10))
        self._dots: list[tk.Canvas] = []
        for _ in range(_LONG_BREAK_EVERY):
            c = tk.Canvas(
                dot_row, width=14, height=14, bg=t.panel_bg, highlightthickness=0
            )
            c.create_oval(2, 2, 12, 12, fill=t.border, outline="", tags="dot")
            c.pack(side="left", padx=4)
            self._dots.append(c)

        btn_row = tk.Frame(self, bg=t.panel_bg)
        btn_row.pack(pady=(0, 6))

        self._start_btn = tk.Label(
            btn_row,
            text=_t("pomodoro.start"),
            bg=t.accent,
            fg=t.panel_bg,
            font=(FONT_UI, 10, "bold"),
            cursor="hand2",
            padx=16,
            pady=6,
            relief="flat",
        )
        self._start_btn.pack(side="left", padx=4)
        self._start_btn.bind("<Button-1>", lambda _: self._toggle())

        def _make_icon_btn(parent: tk.Widget, text: str, cmd: Callable) -> tk.Label:
            b = tk.Label(
                parent,
                text=text,
                bg=t.input_bg,
                fg=t.fg,
                font=(FONT_UI, 11, "bold"),
                cursor="hand2",
                padx=10,
                pady=5,
                relief="flat",
            )
            b.pack(side="left", padx=2)
            b.bind("<Button-1>", lambda _: cmd())
            b.bind("<Enter>", lambda _, w=b: w.config(bg=t.sel_bg))
            b.bind("<Leave>", lambda _, w=b: w.config(bg=t.input_bg))
            return b

        _make_icon_btn(btn_row, _t("pomodoro.reset"), self._reset)
        _make_icon_btn(btn_row, _t("pomodoro.skip"), self._skip)

        self._stats_var = tk.StringVar(
            value=_t("pomodoro.completed_n").format(n=0))
        tk.Label(
            self,
            textvariable=self._stats_var,
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        ).pack(pady=(0, 8))

        sep = tk.Frame(self, bg=t.border, height=1)
        sep.pack(fill="x", padx=12, pady=(4, 8))

        hist_hdr = tk.Frame(self, bg=t.panel_bg)
        hist_hdr.pack(fill="x", padx=12, pady=(0, 2))
        tk.Label(
            hist_hdr,
            text=_t("pomodoro.history"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 7, "bold"),
        ).pack(side="left")
        clr_hist = tk.Label(
            hist_hdr,
            text=_t("pomodoro.clear", "limpar"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 7),
            cursor="hand2",
        )
        clr_hist.pack(side="right")
        clr_hist.bind(
            "<Button-1>", lambda e: (self._history.clear(),
                                     self._update_history_ui())
        )

        self._hist_frame = tk.Frame(self, bg=t.panel_bg)
        self._hist_frame.pack(fill="x", padx=12, pady=(0, 6))
        self._hist_labels: list[tk.Label] = []
        self._update_history_ui()

        dur_row = tk.Frame(self, bg=t.panel_bg)
        dur_row.pack(padx=12, fill="x")

        tk.Label(
            dur_row,
            text=_t("pomodoro.work_min"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        ).pack(side="left")
        tk.Spinbox(
            dur_row,
            from_=1,
            to=60,
            textvariable=self._work_var,
            font=(FONT_UI, 8),
            width=4,
            bg=t.input_bg,
            fg=t.fg,
            buttonbackground=t.border,
            relief="flat",
            command=self._on_dur_change,
        ).pack(side="left", padx=(2, 8))

        tk.Label(
            dur_row,
            text=_t("pomodoro.break_min"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        ).pack(side="left")
        tk.Spinbox(
            dur_row,
            from_=1,
            to=30,
            textvariable=self._break_var,
            font=(FONT_UI, 8),
            width=4,
            bg=t.input_bg,
            fg=t.fg,
            buttonbackground=t.border,
            relief="flat",
            command=self._on_dur_change,
        ).pack(side="left", padx=2)

    def _toggle(self) -> None:
        """Alterna o estado do componente (ativo/inativo ou visível/oculto)."""
        if self._running:
            self._pause()
        else:
            self._start()

    def _start(self) -> None:
        """Inicia ou retoma o timer Pomodoro."""
        self._running = True
        self._stop_ev.clear()
        self._start_btn.config(text=_t("pomodoro.pause"))
        self._thread = threading.Thread(target=self._tick, daemon=True)
        self._thread.start()

    def _pause(self) -> None:
        """Pausa o timer Pomodoro."""
        self._running = False
        self._stop_ev.set()
        self._start_btn.config(text=_t("pomodoro.resume"))

    def _reset(self) -> None:
        """Reinicia o timer para o início do ciclo."""
        self._stop_ev.set()
        self._running = False
        self._mode = "work"
        self._remaining = self._work_var.get() * 60
        self._start_btn.config(text=_t("pomodoro.start"))
        self._refresh()

    def _skip(self) -> None:
        """Pula a fase atual do Pomodoro e avança para a próxima."""
        self._stop_ev.set()
        self._running = False
        self._advance(completed=False)
        self._start_btn.config(text=_t("pomodoro.start"))
        self._refresh()

    def _tick(self) -> None:
        """Decrementa o timer e atualiza a exibição a cada segundo."""
        while not self._stop_ev.is_set() and self._remaining > 0:
            time.sleep(1)
            if not self._stop_ev.is_set():
                self._remaining -= 1
                self.after(0, self._refresh)
        if not self._stop_ev.is_set() and self._remaining <= 0:
            self.after(0, self._segment_done)

    def _segment_done(self) -> None:
        """Trata a conclusão de um segmento e toca um bip de notificação."""
        self._running = False
        self._start_btn.config(text=_t("pomodoro.start"))
        if self._mode == "work":
            self._completed += 1
            msg = _t("pomodoro.done_work").format(n=self._completed)
            self._history.append(f"🍅 Foco #{self._completed} concluído")
        else:
            msg = _t("pomodoro.done_break")
            self._history.append("☕ Pausa concluída")
        if len(self._history) > 20:
            self._history.pop(0)
        self._beep()
        if self._on_message:
            self._on_message(msg, 5000)
        self._advance(completed=True)
        self._refresh()
        self._update_history_ui()

    def _beep(self) -> None:
        """Toca um bip de alerta usando recursos nativos do sistema."""
        import contextlib
        import sys

        with contextlib.suppress(Exception):
            if sys.platform == "win32":
                import winsound

                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            elif sys.platform == "darwin":
                import subprocess

                subprocess.Popen(
                    ["afplay", "/System/Library/Sounds/Glass.aiff"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                import subprocess

                try:
                    subprocess.Popen(
                        ["paplay", "/usr/share/sounds/freedesktop/stereo/complete.oga"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                except FileNotFoundError:
                    print("\a", end="", flush=True)

    def _advance(self, completed: bool = True) -> None:
        if self._mode == "work":
            if (
                completed
                and self._completed % _LONG_BREAK_EVERY == 0
                and self._completed > 0
            ):
                self._mode = "long_break"
                self._remaining = self._break_var.get() * 60 * 3
            else:
                self._mode = "short_break"
                self._remaining = self._break_var.get() * 60
        else:
            self._mode = "work"
            self._remaining = self._work_var.get() * 60

    def _on_dur_change(self) -> None:
        """Trata o evento 'dur change'."""
        if not self._running:
            if self._mode == "work":
                self._remaining = self._work_var.get() * 60
            else:
                self._remaining = self._break_var.get() * 60
            self._refresh()

    _MODE_COLORS = {
        "work": "#f38ba8",
        "short_break": "#a6e3a1",
        "long_break": "#89b4fa",
    }
    _MODE_KEYS = {
        "work": "pomodoro.focus",
        "short_break": "pomodoro.short_break",
        "long_break": "pomodoro.long_break",
    }

    def _refresh(self) -> None:
        """Atualiza e redesenha o conteúdo do componente."""
        t = self._tmgr.theme
        col = self._MODE_COLORS.get(self._mode, t.accent)

        mins, secs = divmod(self._remaining, 60)
        self._clock_var.set(f"{mins:02d}:{secs:02d}")
        self._clock_lbl.config(fg=col)
        self._mode_var.set(
            _t(self._MODE_KEYS.get(self._mode, "pomodoro.focus")))
        self._mode_lbl.config(fg=col)

        total = {
            "work": self._work_var.get() * 60,
            "short_break": self._break_var.get() * 60,
            "long_break": self._break_var.get() * 60 * 3,
        }.get(self._mode, 25 * 60)
        frac = (total - self._remaining) / max(total, 1)
        self._prog_fill.config(bg=col)
        self._prog_fill.place(relwidth=min(frac, 1.0))

        pos = self._completed % _LONG_BREAK_EVERY
        for i, cv in enumerate(self._dots):
            cv.itemconfig("dot", fill=col if i < pos else t.border)

        n = self._completed
        key = "pomodoro.completed_1" if n == 1 else "pomodoro.completed_n"
        self._stats_var.set(_t(key).format(n=n))

        self._start_btn.config(bg=col, fg="#1e1e2e")

    def _update_history_ui(self) -> None:
        """Atualiza a lista de histórico de sessões no painel."""
        import contextlib

        with contextlib.suppress(tk.TclError):
            for lbl in self._hist_labels:
                lbl.destroy()
            self._hist_labels.clear()
            t = self._tmgr.theme
            entries = list(reversed(self._history[-6:]))
            for entry in entries:
                lbl = tk.Label(
                    self._hist_frame,
                    text=entry,
                    bg=t.panel_bg,
                    fg=t.fg_muted,
                    font=(FONT_UI, 7),
                    anchor="w",
                )
                lbl.pack(fill="x")
                self._hist_labels.append(lbl)
            if not entries:
                lbl = tk.Label(
                    self._hist_frame,
                    text=_t("pomodoro.no_sessions", "  Sem sessões ainda"),
                    bg=t.panel_bg,
                    fg=t.border,
                    font=(FONT_UI, 7),
                    anchor="w",
                )
                lbl.pack(fill="x")
                self._hist_labels.append(lbl)

    def destroy(self) -> None:
        """Para o timer e destrói o widget."""
        self._stop_ev.set()
        self._running = False
        super().destroy()

    def apply_theme(self, tmgr: ThemeManager) -> None:
        """Aplica o tema ao painel reconstruindo os widgets."""
        self._tmgr = tmgr

        for w in self.winfo_children():
            w.destroy()
        self._dots.clear()
        t = tmgr.theme
        self.config(bg=t.panel_bg)
        self._build()
        self._refresh()
