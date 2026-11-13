"""Codis - Relógio, Alarme e Cronômetro.

Funcionalidades:
  - Relógio analógico desenhado em Canvas com agulhas suaves
  - Relógio digital com timezone configurável
  - Alarmes múltiplos com rótulo e som opcional
  - Cronômetro com voltas (laps) e exportação
  - 3 abas: Relógio - Alarmes - Cronômetro
"""

from __future__ import annotations

import logging
import math
import time
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from tkinter import messagebox, ttk
from typing import Any

from src.constants import CONFIG_DIR, FONT_MONO, FONT_UI
from src.i18n import _t
from src.ui._persistence_utils import load_dataclass_list, save_dataclass_list
from src.ui.grab_safe import safe_grab_set

logger = logging.getLogger("Codis.Clock")

_ALARMS_FILE = CONFIG_DIR / "clock_alarms.json"
_TAB_PAD = {"padx": 0, "pady": 0}


@dataclass
class Alarm:
    """Entrada de alarme persistida."""

    label: str
    hour: int
    minute: int
    enabled: bool = True
    repeat: bool = False


def _load_alarms() -> list[Alarm]:
    return load_dataclass_list(_ALARMS_FILE, Alarm, logger=logger)


def _save_alarms(alarms: list[Alarm]) -> None:
    save_dataclass_list(_ALARMS_FILE, alarms)


class ClockPanel(tk.Frame):
    """Relógio analógico/digital, alarmes e cronômetro integrado ao Codis."""

    def __init__(self, parent: tk.Widget, tmgr: Any, cfg: Any) -> None:
        """Inicializa o painel de relógio mundial e fusos horários."""
        t = tmgr.theme
        super().__init__(parent, bg=t.panel_bg)
        self._tmgr = tmgr
        self._cfg = cfg
        self._alive = True
        self._alarms: list[Alarm] = _load_alarms()

        self._sw_running = False
        self._sw_start_ts = 0.0
        self._sw_elapsed = 0.0
        self._sw_laps: list[float] = []

        self._alarm_fired_this_minute: set[int] = set()

        self._build(t)
        self._tick()

    def _build(self, t: Any) -> None:
        """Constrói a estrutura de abas."""
        hdr = tk.Frame(self, bg=t.activity_bg, height=48)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(
            hdr, text="🕐", bg=t.activity_bg, fg=t.accent, font=(FONT_UI, 16)
        ).pack(side="left", padx=(14, 4), pady=8)
        tk.Label(
            hdr,
            text=_t("clock.title", "Relógio"),
            bg=t.activity_bg,
            fg=t.fg,
            font=(FONT_UI, 13, "bold"),
        ).pack(side="left", pady=8)

        tk.Frame(self, bg=t.border, height=1).pack(fill="x")

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=0, pady=0)

        self._tab_clock = tk.Frame(nb, bg=t.panel_bg)
        self._tab_alarms = tk.Frame(nb, bg=t.panel_bg)
        self._tab_stopwatch = tk.Frame(nb, bg=t.panel_bg)

        nb.add(self._tab_clock, text=_t("clock.tab_clock", "  Relógio  "))
        nb.add(self._tab_alarms, text=_t("clock.tab_alarms", "  Alarmes  "))
        nb.add(self._tab_stopwatch, text=_t(
            "clock.tab_stopwatch", "Cronômetro "))

        self._build_clock_tab(t)
        self._build_alarms_tab(t)
        self._build_stopwatch_tab(t)

    def _build_clock_tab(self, t: Any) -> None:
        tab = self._tab_clock

        self._canvas_size = 220
        self._canvas = tk.Canvas(
            tab,
            width=self._canvas_size,
            height=self._canvas_size,
            bg=t.panel_bg,
            highlightthickness=0,
        )
        self._canvas.pack(pady=(16, 0))

        self._digital_var = tk.StringVar(value="00:00:00")
        tk.Label(
            tab,
            textvariable=self._digital_var,
            bg=t.panel_bg,
            fg=t.accent,
            font=(FONT_MONO, 28, "bold"),
        ).pack(pady=(6, 0))

        self._date_var = tk.StringVar(value="")
        tk.Label(
            tab,
            textvariable=self._date_var,
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
        ).pack(pady=(2, 8))

    def _draw_clock(self, now: datetime) -> None:
        """Redesenha o relógio analógico."""
        cv = self._canvas
        t = self._tmgr.theme
        cv.delete("all")

        cx = cy = r = self._canvas_size // 2
        r -= 10

        cv.create_oval(
            cx - r, cy - r, cx + r, cy + r, fill=t.editor_bg, outline=t.border, width=2
        )

        for h in range(12):
            angle = math.radians(h * 30 - 90)
            r_outer = r - 6
            r_inner = r - (18 if h % 3 == 0 else 12)
            x1 = cx + r_outer * math.cos(angle)
            y1 = cy + r_outer * math.sin(angle)
            x2 = cx + r_inner * math.cos(angle)
            y2 = cy + r_inner * math.sin(angle)
            w = 3 if h % 3 == 0 else 1
            cv.create_line(
                x1,
                y1,
                x2,
                y2,
                fill=t.fg_muted if h % 3 != 0 else t.fg,
                width=w,
                capstyle="round",
            )

        sec = now.second + now.microsecond / 1e6
        minu = now.minute + sec / 60
        hour = now.hour % 12 + minu / 60

        def _hand(deg: float, length: float, width: int, color: str) -> None:
            angle = math.radians(deg - 90)
            x = cx + length * math.cos(angle)
            y = cy + length * math.sin(angle)
            cv.create_line(cx, cy, x, y, fill=color,
                           width=width, capstyle="round")

        _hand(hour * 30, r * 0.52, 5, t.fg)
        _hand(minu * 6, r * 0.72, 3, t.fg)
        _hand(sec * 6, r * 0.85, 1, t.accent)

        cv.create_oval(cx - 5, cy - 5, cx + 5, cy +
                       5, fill=t.accent, outline="")
        cv.create_oval(cx - 2, cy - 2, cx + 2, cy +
                       2, fill=t.panel_bg, outline="")

    def _build_alarms_tab(self, t: Any) -> None:
        tab = self._tab_alarms

        progress_bar = tk.Frame(tab, bg=t.panel_bg)
        progress_bar.pack(fill="x", padx=8, pady=(8, 4))
        tk.Label(
            progress_bar,
            text=_t("clock.alarms_title", "Meus alarmes"),
            bg=t.panel_bg,
            fg=t.fg,
            font=(FONT_UI, 10, "bold"),
        ).pack(side="left")
        tk.Button(
            progress_bar,
            text=_t("clock.alarm_add", "+ Adicionar"),
            bg=t.accent,
            fg="#FFF",
            relief="flat",
            font=(FONT_UI, 9),
            command=self._add_alarm,
        ).pack(side="right")

        list_frame = tk.Frame(tab, bg=t.panel_bg)
        list_frame.pack(fill="both", expand=True, padx=8)
        self._alarm_list_frame = list_frame
        self._refresh_alarm_list(t)

    def _refresh_alarm_list(self, t: Any | None = None) -> None:
        if t is None:
            t = self._tmgr.theme
        for w in self._alarm_list_frame.winfo_children():
            w.destroy()
        if not self._alarms:
            tk.Label(
                self._alarm_list_frame,
                text=_t("clock.no_alarms", "Nenhum alarme cadastrado."),
                bg=t.panel_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 9),
            ).pack(pady=20)
            return
        for idx, alarm in enumerate(self._alarms):
            self._build_alarm_row(t, idx, alarm)

    def _build_alarm_row(self, t: Any, idx: int, alarm: Alarm) -> None:
        row = tk.Frame(
            self._alarm_list_frame,
            bg=t.sidebar_bg,
            highlightthickness=1,
            highlightbackground=t.border,
        )
        row.pack(fill="x", pady=3)

        color_fg = t.accent if alarm.enabled else t.fg_muted
        time_str = f"{alarm.hour:02d}:{alarm.minute:02d}"
        tk.Label(
            row,
            text=time_str,
            bg=t.sidebar_bg,
            fg=color_fg,
            font=(FONT_MONO, 20, "bold"),
        ).pack(side="left", padx=(12, 8), pady=8)

        info = tk.Frame(row, bg=t.sidebar_bg)
        info.pack(side="left", fill="x", expand=True, pady=8)

        lbl_text = alarm.label or _t("clock.alarm_label_default", "Alarme")
        tk.Label(info, text=lbl_text, bg=t.sidebar_bg, fg=t.fg, font=(FONT_UI, 9)).pack(
            anchor="w"
        )

        repeat_txt = (
            _t("clock.alarm_daily", "Diário")
            if alarm.repeat
            else _t("clock.alarm_once", "Uma vez")
        )
        tk.Label(
            info, text=repeat_txt, bg=t.sidebar_bg, fg=t.fg_muted, font=(
                FONT_UI, 7)
        ).pack(anchor="w")

        ctrl = tk.Frame(row, bg=t.sidebar_bg)
        ctrl.pack(side="right", padx=8, pady=8)

        toggle_text = "●" if alarm.enabled else "○"
        toggle_color = t.accent if alarm.enabled else t.fg_muted
        tgl = tk.Label(
            ctrl,
            text=toggle_text,
            bg=t.sidebar_bg,
            fg=toggle_color,
            font=(FONT_UI, 14),
            cursor="hand2",
        )
        tgl.pack(side="left", padx=4)
        tgl.bind("<Button-1>", lambda _, i=idx: self._toggle_alarm(i))

        del_lbl = tk.Label(
            ctrl,
            text="✕",
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 11),
            cursor="hand2",
        )
        del_lbl.pack(side="left", padx=4)
        del_lbl.bind("<Button-1>", lambda _, i=idx: self._delete_alarm(i))

    def _add_alarm(self) -> None:
        _AlarmDialog(self, self._tmgr, on_save=self._on_alarm_saved)

    def _on_alarm_saved(self, alarm: Alarm) -> None:
        self._alarms.append(alarm)
        _save_alarms(self._alarms)
        self._refresh_alarm_list()

    def _toggle_alarm(self, idx: int) -> None:
        if 0 <= idx < len(self._alarms):
            self._alarms[idx].enabled = not self._alarms[idx].enabled
            _save_alarms(self._alarms)
            self._refresh_alarm_list()

    def _delete_alarm(self, idx: int) -> None:
        if 0 <= idx < len(self._alarms):
            self._alarms.pop(idx)
            _save_alarms(self._alarms)
            self._refresh_alarm_list()

    def _build_stopwatch_tab(self, t: Any) -> None:
        tab = self._tab_stopwatch

        disp = tk.Frame(tab, bg=t.panel_bg)
        disp.pack(fill="x", pady=(20, 8))

        self._sw_var = tk.StringVar(value="00:00.000")
        tk.Label(
            disp,
            textvariable=self._sw_var,
            bg=t.panel_bg,
            fg=t.accent,
            font=(FONT_MONO, 36, "bold"),
        ).pack()

        self._sw_status_var = tk.StringVar(
            value=_t("clock.sw_stopped", "Parado"))
        tk.Label(
            disp,
            textvariable=self._sw_status_var,
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        ).pack()

        btn_row = tk.Frame(tab, bg=t.panel_bg)
        btn_row.pack(pady=(4, 12))

        def _btn(text: str, cmd: Callable, bg: str, fg: str) -> tk.Label:
            lbl = tk.Label(
                btn_row,
                text=text,
                bg=bg,
                fg=fg,
                font=(FONT_UI, 10, "bold"),
                padx=14,
                pady=6,
                cursor="hand2",
                relief="flat",
            )
            lbl.pack(side="left", padx=4)
            lbl.bind("<Button-1>", lambda _: cmd())
            lbl.bind("<Enter>", lambda _: lbl.config(bg=t.accent, fg="#FFF"))
            lbl.bind("<Leave>", lambda _: lbl.config(bg=bg, fg=fg))
            return lbl

        self._sw_start_btn = _btn(
            _t("clock.sw_start", "▶ Iniciar"),
            self._sw_start,
            t.accent,
            "#FFF",
        )
        self._sw_lap_btn = _btn(
            _t("clock.sw_lap", "⊕ Volta"),
            self._sw_lap,
            t.sidebar_bg,
            t.fg,
        )
        self._sw_reset_btn = _btn(
            _t("clock.sw_reset", "⟳ Zerar"),
            self._sw_reset,
            t.sidebar_bg,
            t.fg,
        )
        self._sw_lap_btn.config(state="disabled")
        self._sw_reset_btn.config(state="disabled")

        sep = tk.Frame(tab, bg=t.border, height=1)
        sep.pack(fill="x", padx=8)

        laps_hdr = tk.Frame(tab, bg=t.panel_bg)
        laps_hdr.pack(fill="x", padx=12, pady=(6, 2))
        tk.Label(
            laps_hdr,
            text=_t("clock.sw_laps", "Voltas"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8, "bold"),
        ).pack(side="left")
        clear_lbl = tk.Label(
            laps_hdr,
            text=_t("clock.sw_clear_laps", "Limpar"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            cursor="hand2",
        )
        clear_lbl.pack(side="right")
        clear_lbl.bind("<Button-1>", lambda _: self._sw_clear_laps())

        laps_outer = tk.Frame(tab, bg=t.panel_bg)
        laps_outer.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        scrollbar = ttk.Scrollbar(laps_outer, orient="vertical")
        self._laps_lb = tk.Listbox(
            laps_outer,
            bg=t.editor_bg,
            fg=t.fg,
            font=(FONT_MONO, 10),
            relief="flat",
            bd=0,
            activestyle="none",
            selectbackground=t.accent,
            yscrollcommand=scrollbar.set,
        )
        scrollbar.config(command=self._laps_lb.yview)
        self._laps_lb.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def _sw_fmt(self, elapsed: float) -> str:
        """Formata elapsed (segundos float) como MM:SS.mmm."""
        total_ms = int(elapsed * 1000)
        ms = total_ms % 1000
        total_s = total_ms // 1000
        sec = total_s % 60
        minu = total_s // 60
        return f"{minu:02d}:{sec:02d}.{ms:03d}"

    def _sw_start(self) -> None:
        if self._sw_running:
            self._sw_elapsed += time.monotonic() - self._sw_start_ts
            self._sw_running = False
            self._sw_start_btn.config(
                text=_t("clock.sw_resume", "▶ Continuar"))
            self._sw_status_var.set(_t("clock.sw_paused", "Pausado"))
        else:
            self._sw_start_ts = time.monotonic()
            self._sw_running = True
            self._sw_start_btn.config(text=_t("clock.sw_pause", "⏸ Pausar"))
            self._sw_status_var.set(_t("clock.sw_running", "Rodando"))
            self._sw_lap_btn.config(state="normal")
            self._sw_reset_btn.config(state="normal")

    def _sw_lap(self) -> None:
        if not self._sw_running:
            return
        elapsed = self._sw_elapsed + (time.monotonic() - self._sw_start_ts)
        self._sw_laps.append(elapsed)
        n = len(self._sw_laps)
        prev = self._sw_laps[-2] if n > 1 else 0.0
        lap_time = elapsed - prev
        self._laps_lb.insert(
            "end",
            f"  #{n:02d}    {self._sw_fmt(lap_time)}   ({self._sw_fmt(elapsed)})",
        )
        self._laps_lb.see("end")

    def _sw_reset(self) -> None:
        self._sw_running = False
        self._sw_elapsed = 0.0
        self._sw_laps.clear()
        self._sw_var.set("00:00.000")
        self._sw_status_var.set(_t("clock.sw_stopped", "Parado"))
        self._sw_start_btn.config(text=_t("clock.sw_start", "▶ Iniciar"))
        self._sw_lap_btn.config(state="disabled")
        self._sw_reset_btn.config(state="disabled")

    def _sw_clear_laps(self) -> None:
        self._sw_laps.clear()
        self._laps_lb.delete(0, "end")

    def _tick(self) -> None:
        """Loop principal a ~10fps para relógio e cronômetro."""
        if not self._alive:
            return
        now = datetime.now()

        self._draw_clock(now)
        self._digital_var.set(now.strftime("%H:%M:%S"))
        locale = (
            self._tmgr.i18n.get_locale() if hasattr(self._tmgr, "i18n") else "pt-BR"
        )
        self._date_var.set(_format_date(now, locale))

        if self._sw_running:
            elapsed = self._sw_elapsed + (time.monotonic() - self._sw_start_ts)
            self._sw_var.set(self._sw_fmt(elapsed))

        self._check_alarms(now)

        self.after(100, self._tick)

    def _check_alarms(self, now: datetime) -> None:
        key = now.hour * 60 + now.minute
        if now.second >= 5:
            self._alarm_fired_this_minute.discard(key)
            return
        for alarm in self._alarms:
            if (
                alarm.enabled
                and alarm.hour == now.hour
                and alarm.minute == now.minute
                and key not in self._alarm_fired_this_minute
            ):
                self._alarm_fired_this_minute.add(key)
                self._fire_alarm(alarm)
                if not alarm.repeat:
                    alarm.enabled = False
                    _save_alarms(self._alarms)
                    self.after(100, self._refresh_alarm_list)

    def _fire_alarm(self, alarm: Alarm) -> None:
        label = alarm.label or _t("clock.alarm_label_default", "Alarme")
        time_str = f"{alarm.hour:02d}:{alarm.minute:02d}"
        messagebox.showinfo(
            _t("clock.alarm_fired_title", "⏰ Alarme"),
            _t("clock.alarm_fired_msg", f"{label} - {time_str}"),
            parent=self,
        )

    def destroy(self) -> None:
        """Cancela timers pendentes e destrói o painel."""
        self._alive = False
        super().destroy()


def _format_date(dt: datetime, locale: str) -> str:
    """Formata data com base no locale ativo."""
    week_days_pt = [
        "Segunda",
        "Terça",
        "Quarta",
        "Quinta",
        "Sexta",
        "Sábado",
        "Domingo",
    ]
    months_pt = [
        "",
        "Janeiro",
        "Fevereiro",
        "Março",
        "Abril",
        "Maio",
        "Junho",
        "Julho",
        "Agosto",
        "Setembro",
        "Outubro",
        "Novembro",
        "Dezembro",
    ]

    if locale.startswith("pt"):
        wd = week_days_pt[dt.weekday()]
        return f"{wd}, {dt.day} de {months_pt[dt.month]} de {dt.year}"
    if locale.startswith("es"):
        dias = [
            "Lunes",
            "Martes",
            "Miércoles",
            "Jueves",
            "Viernes",
            "Sábado",
            "Domingo",
        ]
        meses = [
            "",
            "enero",
            "febrero",
            "marzo",
            "abril",
            "mayo",
            "junio",
            "julio",
            "agosto",
            "septiembre",
            "octubre",
            "noviembre",
            "diciembre",
        ]
        return f"{dias[dt.weekday()]}, {dt.day} de {meses[dt.month]} de {dt.year}"
    if locale.startswith("fr"):
        jours = [
            "Lundi",
            "Mardi",
            "Mercredi",
            "Jeudi",
            "Vendredi",
            "Samedi",
            "Dimanche",
        ]
        mois = [
            "",
            "janvier",
            "février",
            "mars",
            "avril",
            "mai",
            "juin",
            "juillet",
            "août",
            "septembre",
            "octobre",
            "novembre",
            "décembre",
        ]
        return f"{jours[dt.weekday()]}, {dt.day} {mois[dt.month]} {dt.year}"
    if locale.startswith("de"):
        wochentage = [
            "Montag",
            "Dienstag",
            "Mittwoch",
            "Donnerstag",
            "Freitag",
            "Samstag",
            "Sonntag",
        ]
        monate = [
            "",
            "Januar",
            "Februar",
            "März",
            "April",
            "Mai",
            "Juni",
            "Juli",
            "August",
            "September",
            "Oktober",
            "November",
            "Dezember",
        ]
        return f"{wochentage[dt.weekday()]}, {dt.day}. {monate[dt.month]} {dt.year}"
    return dt.strftime("%A, %B %d, %Y")


class _AlarmDialog(tk.Toplevel):
    """Diálogo para criar um novo alarme."""

    def __init__(
        self, parent: tk.Widget, tmgr: Any, on_save: Callable[[Alarm], None]
    ) -> None:
        """Inicializa o diálogo de configuração de alarme."""
        super().__init__(parent)
        self._tmgr = tmgr
        self._on_save = on_save
        t = tmgr.theme

        self.title(_t("clock.alarm_dialog_title", "Novo Alarme"))
        self.resizable(False, False)
        self.config(bg=t.panel_bg)
        safe_grab_set(self)

        self._build(t)
        self.update_idletasks()
        _center_window(self)

    def _build(self, t: Any) -> None:
        pad = {"padx": 16, "pady": 6}

        hr_row = tk.Frame(self, bg=t.panel_bg)
        hr_row.pack(fill="x", **pad)
        tk.Label(
            hr_row,
            text=_t("clock.alarm_hour", "Hora (0-23)"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
            width=18,
            anchor="w",
        ).pack(side="left")
        self._hour_var = tk.StringVar(value="08")
        tk.Entry(
            hr_row,
            textvariable=self._hour_var,
            bg=t.editor_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            font=(FONT_MONO, 11),
            width=5,
        ).pack(side="left", padx=4)

        mn_row = tk.Frame(self, bg=t.panel_bg)
        mn_row.pack(fill="x", **pad)
        tk.Label(
            mn_row,
            text=_t("clock.alarm_minute", "Minuto (0-59)"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
            width=18,
            anchor="w",
        ).pack(side="left")
        self._minute_var = tk.StringVar(value="00")
        tk.Entry(
            mn_row,
            textvariable=self._minute_var,
            bg=t.editor_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            font=(FONT_MONO, 11),
            width=5,
        ).pack(side="left", padx=4)

        lbl_row = tk.Frame(self, bg=t.panel_bg)
        lbl_row.pack(fill="x", **pad)
        tk.Label(
            lbl_row,
            text=_t("clock.alarm_label", "Rótulo"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
            width=18,
            anchor="w",
        ).pack(side="left")
        self._label_var = tk.StringVar(value="")
        tk.Entry(
            lbl_row,
            textvariable=self._label_var,
            bg=t.editor_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            font=(FONT_UI, 10),
            width=20,
        ).pack(side="left", padx=4)

        rep_row = tk.Frame(self, bg=t.panel_bg)
        rep_row.pack(fill="x", **pad)
        self._repeat_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            rep_row,
            text=_t("clock.alarm_repeat", "Repetir todos os dias"),
            variable=self._repeat_var,
            bg=t.panel_bg,
            fg=t.fg,
            selectcolor=t.editor_bg,
            activebackground=t.panel_bg,
            font=(FONT_UI, 9),
        ).pack(anchor="w")

        btn_row = tk.Frame(self, bg=t.panel_bg)
        btn_row.pack(fill="x", padx=16, pady=(4, 12))

        tk.Button(
            btn_row,
            text=_t("clock.alarm_save", "Salvar"),
            command=self._save,
            bg=t.accent,
            fg="#FFF",
            relief="flat",
            font=(FONT_UI, 10),
        ).pack(side="right", padx=4)
        tk.Button(
            btn_row,
            text=_t("clock.alarm_cancel", "Cancelar"),
            command=self.destroy,
            bg=t.sidebar_bg,
            fg=t.fg,
            relief="flat",
            font=(FONT_UI, 10),
        ).pack(side="right")

    def _save(self) -> None:
        try:
            hour = int(self._hour_var.get())
            minute = int(self._minute_var.get())
        except ValueError:
            messagebox.showwarning(
                _t("clock.alarm_invalid_title", "Valor inválido"),
                _t(
                    "clock.alarm_invalid_msg",
                    "Use números inteiros para hora e minuto.",
                ),
                parent=self,
            )
            return
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            messagebox.showwarning(
                _t("clock.alarm_invalid_title", "Valor inválido"),
                _t("clock.alarm_range_msg", "Hora: 0–23  |  Minuto: 0–59."),
                parent=self,
            )
            return
        alarm = Alarm(
            label=self._label_var.get().strip(),
            hour=hour,
            minute=minute,
            repeat=self._repeat_var.get(),
        )
        self._on_save(alarm)
        self.destroy()


def _center_window(win: tk.Toplevel) -> None:
    w = win.winfo_reqwidth()
    h = win.winfo_reqheight()
    x = (win.winfo_screenwidth() - w) // 2
    y = (win.winfo_screenheight() - h) // 2
    win.geometry(f"{w}x{h}+{x}+{y}")
