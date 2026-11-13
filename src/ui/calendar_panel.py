"""Codis - Calendário Integrado.

Funcionalidades:
  - Calendário mensal navegável com visual polido
  - Mini-mapa de eventos no grid
  - Anotações e lembretes por data
  - Lista de eventos com horário, cor e descrição
  - Pesquisa de eventos
  - Lembretes com notificação popup
"""

from __future__ import annotations

import json
import logging
import threading
import time
import tkinter as tk
from datetime import date, datetime, timedelta
from tkinter import messagebox, ttk
from typing import Any

from src.constants import CONFIG_DIR, FONT_UI
from src.i18n import I18n, _t
from src.ui._perf_utils import debounce_canvas
from src.ui.grab_safe import safe_grab_set

logger = logging.getLogger("Codis.Calendar")

_EVENTS_FILE = CONFIG_DIR / "calendar_events.json"

_MONTHS_BY_LOCALE: dict[str, list[str]] = {
    "pt": [
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
    ],
    "es": [
        "",
        "Enero",
        "Febrero",
        "Marzo",
        "Abril",
        "Mayo",
        "Junio",
        "Julio",
        "Agosto",
        "Septiembre",
        "Octubre",
        "Noviembre",
        "Diciembre",
    ],
    "fr": [
        "",
        "Janvier",
        "Février",
        "Mars",
        "Avril",
        "Mai",
        "Juin",
        "Juillet",
        "Août",
        "Septembre",
        "Octobre",
        "Novembre",
        "Décembre",
    ],
    "de": [
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
    ],
    "it": [
        "",
        "Gennaio",
        "Febbraio",
        "Marzo",
        "Aprile",
        "Maggio",
        "Giugno",
        "Luglio",
        "Agosto",
        "Settembre",
        "Ottobre",
        "Novembre",
        "Dicembre",
    ],
    "pl": [
        "",
        "Styczeń",
        "Luty",
        "Marzec",
        "Kwiecień",
        "Maj",
        "Czerwiec",
        "Lipiec",
        "Sierpień",
        "Wrzesień",
        "Październik",
        "Listopad",
        "Grudzień",
    ],
    "en": [
        "",
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ],
}
_MONTHS_SHORT_BY_LOCALE: dict[str, list[str]] = {
    "pt": [
        "",
        "Jan",
        "Fev",
        "Mar",
        "Abr",
        "Mai",
        "Jun",
        "Jul",
        "Ago",
        "Set",
        "Out",
        "Nov",
        "Dez",
    ],
    "es": [
        "",
        "Ene",
        "Feb",
        "Mar",
        "Abr",
        "May",
        "Jun",
        "Jul",
        "Ago",
        "Sep",
        "Oct",
        "Nov",
        "Dic",
    ],
    "fr": [
        "",
        "Jan",
        "Fév",
        "Mar",
        "Avr",
        "Mai",
        "Jun",
        "Jul",
        "Aoû",
        "Sep",
        "Oct",
        "Nov",
        "Déc",
    ],
    "de": [
        "",
        "Jan",
        "Feb",
        "Mär",
        "Apr",
        "Mai",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Okt",
        "Nov",
        "Dez",
    ],
    "en": [
        "",
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    ],
}
_DAYS_SHORT_BY_LOCALE: dict[str, list[str]] = {
    "pt": ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"],
    "es": ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"],
    "fr": ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"],
    "de": ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"],
    "it": ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"],
    "en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
}
_DAYS_FULL_BY_LOCALE: dict[str, list[str]] = {
    "pt": ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"],
    "es": ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"],
    "fr": ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"],
    "de": [
        "Montag",
        "Dienstag",
        "Mittwoch",
        "Donnerstag",
        "Freitag",
        "Samstag",
        "Sonntag",
    ],
    "en": [
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    ],
}


def _locale_prefix(locale: str) -> str:
    """Retorna prefixo de 2 letras do locale (pt-BR -> pt)."""
    return locale[:2].lower()


def _months(locale: str) -> list[str]:
    return _MONTHS_BY_LOCALE.get(_locale_prefix(locale), _MONTHS_BY_LOCALE["en"])


def _months_short(locale: str) -> list[str]:
    return _MONTHS_SHORT_BY_LOCALE.get(
        _locale_prefix(locale), _MONTHS_SHORT_BY_LOCALE["en"]
    )


def _days_short(locale: str) -> list[str]:
    return _DAYS_SHORT_BY_LOCALE.get(
        _locale_prefix(locale), _DAYS_SHORT_BY_LOCALE["en"]
    )


def _days_full(locale: str) -> list[str]:
    return _DAYS_FULL_BY_LOCALE.get(_locale_prefix(locale), _DAYS_FULL_BY_LOCALE["en"])


_MONTHS_PT = _MONTHS_BY_LOCALE["pt"]
_MONTHS_SHORT = _MONTHS_SHORT_BY_LOCALE["pt"]
_DAYS_PT_SHORT = _DAYS_SHORT_BY_LOCALE["pt"]
_DAYS_PT_FULL = _DAYS_FULL_BY_LOCALE["pt"]

_EVENT_COLORS = [
    "#89b4fa",
    "#a6e3a1",
    "#fab387",
    "#f38ba8",
    "#cba6f7",
    "#f9e2af",
    "#94e2d5",
    "#eba0ac",
]


def _load_events() -> dict[str, list[dict]]:
    if not _EVENTS_FILE.exists():
        return {}
    try:
        return json.loads(_EVENTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_events(events: dict[str, list[dict]]) -> None:
    _EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _EVENTS_FILE.write_text(
        json.dumps(events, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


class CalendarPanel(tk.Frame):
    """Calendário integrado ao Codis com anotações e lembretes."""

    def __init__(self, parent: tk.Widget, tmgr: Any, cfg: Any) -> None:
        """Inicializa o painel de calendário e agenda."""
        t = tmgr.theme
        super().__init__(parent, bg=t.panel_bg)
        self._tmgr = tmgr
        self._cfg = cfg
        self._events = _load_events()
        self._today = date.today()
        self._cur_month = self._today.month
        self._cur_year = self._today.year
        self._selected = self._today
        self._reminder_thread: threading.Thread | None = None
        self._alive = True
        self._build(t)
        self._start_reminder_monitor()

    def _build(self, t: Any) -> None:
        hdr = tk.Frame(self, bg=t.activity_bg, height=48)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        tk.Label(
            hdr, text="📅", bg=t.activity_bg, fg=t.accent, font=(FONT_UI, 16)
        ).pack(side="left", padx=(14, 4), pady=8)
        tk.Label(
            hdr,
            text=_t("calendar.title", "Calendário"),
            bg=t.activity_bg,
            fg=t.fg,
            font=(FONT_UI, 13, "bold"),
        ).pack(side="left", pady=8)

        search_fr = tk.Frame(
            hdr, bg=t.input_bg, highlightthickness=1, highlightbackground=t.border
        )
        search_fr.pack(side="right", padx=12, pady=10)
        tk.Label(
            search_fr, text="🔍", bg=t.input_bg, fg=t.fg_muted, font=(FONT_UI, 9)
        ).pack(side="left", padx=(6, 2))
        self._search_var = tk.StringVar()
        search_ent = tk.Entry(
            search_fr,
            textvariable=self._search_var,
            bg=t.input_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            bd=0,
            font=(FONT_UI, 9),
            width=18,
        )
        search_ent.pack(side="left", padx=(0, 6), pady=4)
        search_ent.bind("<Return>", lambda _: self._do_search())

        tk.Frame(self, bg=t.border, height=1).pack(fill="x")

        body = tk.Frame(self, bg=t.panel_bg)
        body.pack(fill="both", expand=True)

        left = tk.Frame(body, bg=t.panel_bg, width=310)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)

        tk.Frame(body, bg=t.border, width=1).pack(side="left", fill="y")

        right = tk.Frame(body, bg=t.panel_bg)
        right.pack(side="left", fill="both", expand=True)

        self._build_nav(left, t)
        self._build_weekday_header(left, t)
        self._build_grid(left, t)
        self._build_mini_agenda(left, t)
        self._build_detail(right, t)
        self._redraw_grid()
        self._show_day_detail(self._selected)

    def _build_nav(self, parent: tk.Widget, t: Any) -> None:
        nav = tk.Frame(parent, bg=t.panel_bg)
        nav.pack(fill="x", padx=10, pady=(10, 4))

        prev_btn = tk.Label(
            nav,
            text="[",
            bg=t.btn_bg,
            fg=t.fg,
            font=(FONT_UI, 18),
            cursor="hand2",
            padx=8,
            pady=2,
        )
        prev_btn.pack(side="left")
        prev_btn.bind("<Button-1>", lambda _: self._change_month(-1))
        prev_btn.bind("<Enter>", lambda _: prev_btn.config(bg=t.btn_hover))
        prev_btn.bind("<Leave>", lambda _: prev_btn.config(bg=t.btn_bg))

        self._month_lbl = tk.Label(
            nav,
            text="",
            bg=t.panel_bg,
            fg=t.fg,
            font=(FONT_UI, 13, "bold"),
            cursor="hand2",
        )
        self._month_lbl.pack(side="left", fill="x", expand=True)
        self._month_lbl.bind("<Button-1>", lambda _: self._go_today())

        today_lbl = tk.Label(
            nav,
            text=_t("calendar.btn_today", "Hoje"),
            bg=t.accent,
            fg=t.btn_primary_fg,
            font=(FONT_UI, 8),
            cursor="hand2",
            padx=8,
            pady=3,
        )
        today_lbl.pack(side="right", padx=4)
        today_lbl.bind("<Button-1>", lambda _: self._go_today())

        next_btn = tk.Label(
            nav,
            text="]",
            bg=t.btn_bg,
            fg=t.fg,
            font=(FONT_UI, 18),
            cursor="hand2",
            padx=8,
            pady=2,
        )
        next_btn.pack(side="right", padx=2)
        next_btn.bind("<Button-1>", lambda _: self._change_month(+1))
        next_btn.bind("<Enter>", lambda _: next_btn.config(bg=t.btn_hover))
        next_btn.bind("<Leave>", lambda _: next_btn.config(bg=t.btn_bg))

    def _build_weekday_header(self, parent: tk.Widget, t: Any) -> None:
        """Linha de cabeçalho dos dias da semana."""
        wk = tk.Frame(parent, bg=t.activity_bg)
        wk.pack(fill="x", padx=10, pady=(2, 0))
        locale = (
            I18n.get_instance().get_locale() if hasattr(_t, "__self__") else "pt-BR"
        )
        for i, day in enumerate(_days_short(locale)):
            fg = t.t_func if i >= 5 else t.fg_muted
            tk.Label(
                wk,
                text=day,
                bg=t.activity_bg,
                fg=fg,
                font=(FONT_UI, 8, "bold"),
                anchor="center",
                width=4,
            ).pack(side="left", expand=True)

    def _build_grid(self, parent: tk.Widget, t: Any) -> None:
        self._grid_frame = tk.Frame(parent, bg=t.panel_bg)
        self._grid_frame.pack(fill="x", padx=10, pady=(1, 0))

    def _build_mini_agenda(self, parent: tk.Widget, t: Any) -> None:
        """Mini-agenda dos próximos eventos (abaixo do grid)."""
        tk.Frame(parent, bg=t.border, height=1).pack(
            fill="x", padx=10, pady=(8, 0))

        hdr = tk.Frame(parent, bg=t.panel_bg)
        hdr.pack(fill="x", padx=10, pady=(6, 2))
        tk.Label(
            hdr,
            text=_t("calendar.upcoming", "Próximos eventos"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8, "bold"),
        ).pack(side="left")

        add_btn = tk.Label(
            hdr,
            text=_t("cal.btn_new", "+ Novo"),
            bg=t.panel_bg,
            fg=t.t_link,
            font=(FONT_UI, 8),
            cursor="hand2",
        )
        add_btn.pack(side="right")
        add_btn.bind("<Button-1>", lambda _: self._add_event_dialog())

        self._mini_agenda = tk.Frame(parent, bg=t.panel_bg)
        self._mini_agenda.pack(fill="x", padx=10, pady=(0, 8))
        self._refresh_mini_agenda()

    def _refresh_mini_agenda(self) -> None:
        t = self._tmgr.theme
        for w in self._mini_agenda.winfo_children():
            w.destroy()

        today = date.today()
        upcoming: list[tuple[date, dict]] = []
        for delta in range(30):
            d = today + timedelta(days=delta)
            iso = d.isoformat()
            for ev in self._events.get(iso, []):
                upcoming.append((d, ev))
            if len(upcoming) >= 4:
                break

        if not upcoming:
            tk.Label(
                self._mini_agenda,
                text=_t("calendar.no_events",
                        "Nenhum evento nos próximos 30 dias"),
                bg=t.panel_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 8),
            ).pack(anchor="w", pady=4)
            return

        for d, ev in upcoming[:4]:
            row = tk.Frame(self._mini_agenda, bg=t.panel_bg)
            row.pack(fill="x", pady=1)

            color = ev.get("color", t.accent)
            tk.Frame(row, bg=color, width=3).pack(
                side="left", fill="y", padx=(0, 6))

            is_today = d == today
            locale = (
                I18n.get_instance().get_locale() if hasattr(_t, "__self__") else "pt-BR"
            )
            if is_today:
                date_str = _t("calendar.today_short", "Hoje")
            elif d == today + timedelta(days=1):
                date_str = _t("calendar.tomorrow", "Amanhã")
            else:
                date_str = f"{d.day} {_months_short(locale)[d.month]}"
            tk.Label(
                row,
                text=date_str,
                bg=t.panel_bg,
                fg=t.accent if is_today else t.fg_muted,
                font=(FONT_UI, 7),
                width=6,
                anchor="w",
            ).pack(side="left")
            tk.Label(
                row,
                text=ev.get("title", ""),
                bg=t.panel_bg,
                fg=t.fg,
                font=(FONT_UI, 8),
                anchor="w",
            ).pack(side="left", fill="x", expand=True)
            if ev.get("time"):
                tk.Label(
                    row,
                    text=ev["time"],
                    bg=t.panel_bg,
                    fg=t.fg_muted,
                    font=(FONT_UI, 7),
                ).pack(side="right")

    def _build_detail(self, parent: tk.Widget, t: Any) -> None:
        """Painel de detalhe do dia selecionado."""
        detail_hdr = tk.Frame(parent, bg=t.activity_bg, height=42)
        detail_hdr.pack(fill="x")
        detail_hdr.pack_propagate(False)

        self._detail_lbl = tk.Label(
            detail_hdr,
            text="",
            bg=t.activity_bg,
            fg=t.fg,
            font=(FONT_UI, 11, "bold"),
            anchor="w",
        )
        self._detail_lbl.pack(side="left", padx=14, pady=8)

        add_btn = tk.Label(
            detail_hdr,
            text=_t("cal.btn_new_event", "+ Evento"),
            bg=t.accent,
            fg=t.btn_primary_fg,
            font=(FONT_UI, 9),
            cursor="hand2",
            padx=10,
            pady=4,
        )
        add_btn.pack(side="right", padx=10, pady=6)
        add_btn.bind("<Button-1>", lambda _: self._add_event_dialog())

        tk.Frame(parent, bg=t.border, height=1).pack(fill="x")

        events_wrap = tk.Frame(parent, bg=t.panel_bg)
        events_wrap.pack(fill="both", expand=True)

        canvas = tk.Canvas(events_wrap, bg=t.panel_bg, highlightthickness=0)
        vsb = ttk.Scrollbar(events_wrap, orient="vertical",
                            command=canvas.yview)
        vsb.pack(side="right", fill="y")
        canvas.config(yscrollcommand=vsb.set)
        canvas.pack(fill="both", expand=True)

        self._events_inner = tk.Frame(canvas, bg=t.panel_bg)
        self._events_win = canvas.create_window(
            (0, 0), window=self._events_inner, anchor="nw"
        )

        debounce_canvas(
            canvas, self._events_win, ms=40, extra_widget=self._events_inner
        )
        for ev in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            canvas.bind(
                ev,
                lambda e: canvas.yview_scroll(
                    -1 * (e.delta // 120) if e.delta else (-1 if e.num == 4 else 1),
                    "units",
                ),
            )

    def _redraw_grid(self) -> None:
        t = self._tmgr.theme
        locale = (
            I18n.get_instance().get_locale() if hasattr(_t, "__self__") else "pt-BR"
        )
        self._month_lbl.config(
            text=f"{_months(locale)[self._cur_month]}  {self._cur_year}"
        )

        for w in self._grid_frame.winfo_children():
            w.destroy()

        first = date(self._cur_year, self._cur_month, 1)
        start_wd = first.weekday()

        row = tk.Frame(self._grid_frame, bg=t.panel_bg)
        row.pack(fill="x")

        for _ in range(start_wd):
            tk.Label(row, text="", bg=t.panel_bg, width=4, pady=5).pack(
                side="left", expand=True
            )

        cur = first
        col = start_wd
        while cur.month == self._cur_month:
            if col > 0 and col % 7 == 0:
                row = tk.Frame(self._grid_frame, bg=t.panel_bg)
                row.pack(fill="x")

            iso = cur.isoformat()
            has_events = bool(self._events.get(iso))
            is_today = cur == self._today
            is_sel = cur == self._selected
            is_weekend = cur.weekday() >= 5

            if is_sel:
                cell_bg = t.accent
                cell_fg = t.btn_primary_fg
                font_w = "bold"
            elif is_today:
                cell_bg = t.sel_bg
                cell_fg = t.fg
                font_w = "bold"
            else:
                cell_bg = t.panel_bg
                cell_fg = t.t_func if is_weekend else t.fg
                font_w = "normal"

            cell = tk.Frame(row, bg=cell_bg, cursor="hand2")
            cell.pack(side="left", expand=True, padx=1, pady=1)

            day_lbl = tk.Label(
                cell,
                text=str(cur.day),
                bg=cell_bg,
                fg=cell_fg,
                font=(FONT_UI, 9, font_w),
                padx=4,
                pady=4,
                cursor="hand2",
            )
            day_lbl.pack()

            if has_events:
                dot_color = (
                    t.btn_primary_fg if is_sel else t.accent if is_today else t.accent
                )
                dot = tk.Label(
                    cell, text="•", bg=cell_bg, fg=dot_color, font=(FONT_UI, 6)
                )
                dot.pack()
                dot.bind("<Button-1>", lambda _, d=cur: self._on_day_click(d))

            for w in (cell, day_lbl):
                w.bind("<Button-1>", lambda _, d=cur: self._on_day_click(d))
                w.bind(
                    "<Enter>",
                    lambda _, c=cell, bg=cell_bg: c.config(
                        bg=t.btn_hover if bg != t.accent else bg
                    ),
                )
                w.bind("<Leave>", lambda _, c=cell,
                       bg=cell_bg: c.config(bg=bg))

            cur += timedelta(days=1)
            col += 1

    def _on_day_click(self, d: date) -> None:
        self._selected = d
        self._redraw_grid()
        self._show_day_detail(d)

    def _show_day_detail(self, d: date) -> None:
        t = self._tmgr.theme
        iso = d.isoformat()
        locale = (
            I18n.get_instance().get_locale() if hasattr(_t, "__self__") else "pt-BR"
        )
        weekday = _days_full(locale)[d.weekday()]
        month_name = _months(locale)[d.month]
        lp = _locale_prefix(locale)
        if lp in ("pt", "es", "it"):
            date_str = f"{weekday}, {d.day} de {month_name} de {d.year}"
        elif lp == "fr":
            date_str = f"{weekday}, {d.day} {month_name} {d.year}"
        elif lp == "de":
            date_str = f"{weekday}, {d.day}. {month_name} {d.year}"
        else:
            date_str = datetime(
                d.year, d.month, d.day).strftime("%A, %B %d, %Y")
        self._detail_lbl.config(text=date_str)

        for w in self._events_inner.winfo_children():
            w.destroy()

        day_events = self._events.get(iso, [])
        if not day_events:
            empty = tk.Frame(self._events_inner, bg=t.panel_bg)
            empty.pack(fill="both", expand=True, pady=40)
            tk.Label(
                empty, text="📭", bg=t.panel_bg, fg=t.fg_muted, font=(FONT_UI, 28)
            ).pack()
            tk.Label(
                empty,
                text=_t("calendar.no_events", "Nenhum evento neste dia"),
                bg=t.panel_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 10),
            ).pack(pady=4)
            tk.Label(
                empty,
                text=_t("calendar.add_hint",
                        "Clique em  + Evento  para adicionar"),
                bg=t.panel_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 8),
            ).pack()
            return

        for idx, ev in enumerate(day_events):
            color = ev.get("color", t.accent)

            ev_frame = tk.Frame(self._events_inner, bg=t.panel_bg)
            ev_frame.pack(fill="x", padx=12, pady=5)

            color_bar = tk.Frame(ev_frame, bg=color, width=4)
            color_bar.pack(side="left", fill="y")

            content = tk.Frame(
                ev_frame,
                bg=t.btn_bg,
                highlightthickness=1,
                highlightbackground=t.border,
            )
            content.pack(side="left", fill="both", expand=True, padx=(0, 0))

            title_row = tk.Frame(content, bg=t.btn_bg)
            title_row.pack(fill="x", padx=8, pady=(7, 2))

            tk.Label(
                title_row,
                text=ev.get("title", ""),
                bg=t.btn_bg,
                fg=t.fg,
                font=(FONT_UI, 11, "bold"),
                anchor="w",
            ).pack(side="left", fill="x", expand=True)

            for icon, cmd in [
                ("✏", lambda i=idx, _d=d: self._edit_event_dialog(_d, i)),
                ("🗑", lambda i=idx, _d=d: self._delete_event(_d, i)),
            ]:
                b = tk.Label(
                    title_row,
                    text=icon,
                    bg=t.btn_bg,
                    fg=t.fg_muted,
                    cursor="hand2",
                    font=(FONT_UI, 9),
                )
                b.pack(side="right", padx=2)
                b.bind("<Button-1>", lambda _, c=cmd: c())

            if ev.get("time") or ev.get("note") or ev.get("remind"):
                meta_row = tk.Frame(content, bg=t.btn_bg)
                meta_row.pack(fill="x", padx=8, pady=(0, 7))
                if ev.get("time"):
                    tk.Label(
                        meta_row,
                        text=f"🕐 {ev['time']}",
                        bg=t.btn_bg,
                        fg=t.fg_muted,
                        font=(FONT_UI, 8),
                    ).pack(side="left", padx=(0, 8))
                if ev.get("remind"):
                    tk.Label(
                        meta_row, text="🔔", bg=t.btn_bg, fg=t.accent, font=(FONT_UI, 8)
                    ).pack(side="left", padx=(0, 8))
                if ev.get("note"):
                    tk.Label(
                        meta_row,
                        text=ev["note"],
                        bg=t.btn_bg,
                        fg=t.fg_muted,
                        font=(FONT_UI, 8),
                        anchor="w",
                        wraplength=280,
                    ).pack(side="left", fill="x", expand=True)
            else:
                tk.Frame(content, bg=t.btn_bg, height=4).pack()

    def _add_event_dialog(
        self, prefill: dict | None = None, edit_idx: int | None = None
    ) -> None:
        t = self._tmgr.theme
        win = tk.Toplevel(self)
        is_edit = edit_idx is not None
        win.title(
            _t("calendar.edit_event", "Editar Evento")
            if is_edit
            else _t("calendar.new_event", "Novo Evento")
        )
        win.config(bg=t.floating_bg)
        safe_grab_set(win)
        win.resizable(False, False)

        hdr = tk.Frame(win, bg=t.activity_bg)
        hdr.pack(fill="x")
        tk.Label(
            hdr,
            text=(
                _t("cal.edit_event", "✏  Editar Evento")
                if is_edit
                else _t("cal.new_event_title", "＋  Novo Evento")
            ),
            bg=t.activity_bg,
            fg=t.fg,
            font=(FONT_UI, 11, "bold"),
        ).pack(side="left", padx=12, pady=10)
        tk.Label(
            hdr,
            text=f"📅 {self._selected.strftime('%d/%m/%Y')}",
            bg=t.activity_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
        ).pack(side="right", padx=12)

        form = tk.Frame(win, bg=t.floating_bg)
        form.pack(fill="x", padx=16, pady=12)

        def field(
            label: str, var: tk.StringVar, row_n: int, placeholder: str = ""
        ) -> None:
            tk.Label(
                form,
                text=label,
                bg=t.floating_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 8),
                anchor="w",
            ).grid(
                row=row_n * 2, column=0, columnspan=2, padx=0, pady=(8, 2), sticky="w"
            )
            ent = tk.Entry(
                form,
                textvariable=var,
                bg=t.input_bg,
                fg=t.fg,
                insertbackground=t.cursor,
                relief="flat",
                bd=0,
                highlightthickness=1,
                highlightbackground=t.border,
                font=(FONT_UI, 10),
                width=34,
            )
            ent.grid(
                row=row_n * 2 + 1,
                column=0,
                columnspan=2,
                padx=0,
                pady=0,
                sticky="ew",
                ipady=5,
            )

        pf = prefill or {}
        title_var = tk.StringVar(value=pf.get("title", ""))
        time_var = tk.StringVar(value=pf.get("time", ""))
        note_var = tk.StringVar(value=pf.get("note", ""))
        remind_var = tk.BooleanVar(value=pf.get("remind", False))
        color_var = tk.StringVar(value=pf.get("color", _EVENT_COLORS[0]))

        field(_t("calendar.ev_title", "Título *"), title_var, 0)
        field(_t("calendar.ev_time", "Horário  (ex: 14:30)"), time_var, 1)
        field(_t("calendar.ev_note", "Anotação"), note_var, 2)

        tk.Label(
            form,
            text=_t("calendar.ev_color", "Cor"),
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            anchor="w",
        ).grid(row=6, column=0, columnspan=2, pady=(8, 2), sticky="w")

        color_fr = tk.Frame(form, bg=t.floating_bg)
        color_fr.grid(row=7, column=0, columnspan=2, sticky="w")
        self._color_dots: list[tk.Label] = []

        def pick_color(c: str) -> None:
            color_var.set(c)
            for dot in self._color_dots:
                dot.config(relief="flat")
            idx = _EVENT_COLORS.index(c) if c in _EVENT_COLORS else -1
            if idx >= 0:
                self._color_dots[idx].config(relief="solid")

        for _, c in enumerate(_EVENT_COLORS):
            dot = tk.Label(
                color_fr,
                bg=c,
                width=3,
                pady=8,
                cursor="hand2",
                relief="flat",
                highlightthickness=2,
                highlightbackground=c,
            )
            dot.pack(side="left", padx=2)
            dot.bind("<Button-1>", lambda _, col=c: pick_color(col))
            self._color_dots.append(dot)
        pick_color(color_var.get())

        remind_fr = tk.Frame(form, bg=t.floating_bg)
        remind_fr.grid(row=8, column=0, columnspan=2, pady=(10, 0), sticky="w")
        tk.Checkbutton(
            remind_fr,
            text=_t("cal.enable_reminder",
                    "🔔  Ativar lembrete (1–2 min antes)"),
            variable=remind_var,
            bg=t.floating_bg,
            fg=t.fg,
            selectcolor=t.input_bg,
            activebackground=t.floating_bg,
            activeforeground=t.fg,
            font=(FONT_UI, 9),
        ).pack(side="left")

        form.columnconfigure(0, weight=1)
        form.columnconfigure(1, weight=1)

        tk.Frame(win, bg=t.border, height=1).pack(fill="x")
        btn_row = tk.Frame(win, bg=t.floating_bg)
        btn_row.pack(fill="x", padx=16, pady=10)

        def _save() -> None:
            t_val = title_var.get().strip()
            if not t_val:
                messagebox.showwarning(
                    _t("calendar.missing_title", "Campo obrigatório"),
                    _t("calendar.title_required", "O título é obrigatório."),
                    parent=win,
                )
                return
            ev = {
                "title": t_val,
                "time": time_var.get().strip(),
                "note": note_var.get().strip(),
                "remind": remind_var.get(),
                "color": color_var.get() or _EVENT_COLORS[0],
            }
            iso = self._selected.isoformat()
            day_events = self._events.setdefault(iso, [])
            if is_edit and edit_idx is not None:
                day_events[edit_idx] = ev
            else:
                day_events.append(ev)
            _save_events(self._events)
            self._redraw_grid()
            self._show_day_detail(self._selected)
            self._refresh_mini_agenda()
            win.destroy()

        cancel_b = tk.Label(
            btn_row,
            text=_t("calendar.cancel", "Cancelar"),
            bg=t.btn_bg,
            fg=t.fg,
            font=(FONT_UI, 10),
            cursor="hand2",
            padx=14,
            pady=6,
        )
        cancel_b.pack(side="left", padx=(0, 8))
        cancel_b.bind("<Button-1>", lambda _: win.destroy())

        save_b = tk.Label(
            btn_row,
            text=_t("calendar.save", "Salvar evento"),
            bg=t.accent,
            fg=t.btn_primary_fg,
            font=(FONT_UI, 10, "bold"),
            cursor="hand2",
            padx=14,
            pady=6,
        )
        save_b.pack(side="right")
        save_b.bind("<Button-1>", lambda _: _save())

        win.update_idletasks()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        ww, wh = win.winfo_reqwidth(), win.winfo_reqheight()
        win.geometry(f"{ww}x{wh}+{(sw - ww) // 2}+{(sh - wh) // 2}")

    def _edit_event_dialog(self, d: date, idx: int) -> None:
        iso = d.isoformat()
        evs = self._events.get(iso, [])
        if idx < len(evs):
            self._selected = d
            self._add_event_dialog(prefill=evs[idx], edit_idx=idx)

    def _delete_event(self, d: date, idx: int) -> None:
        iso = d.isoformat()
        evs = self._events.get(iso, [])
        if idx < len(evs):
            ev_title = evs[idx].get("title", "?")
            if messagebox.askyesno(
                "Remover Evento", f"Remover '{ev_title}'?", parent=self
            ):
                evs.pop(idx)
                if not evs:
                    self._events.pop(iso, None)
                _save_events(self._events)
                self._redraw_grid()
                self._show_day_detail(d)
                self._refresh_mini_agenda()

    def _change_month(self, delta: int) -> None:
        m = self._cur_month + delta
        y = self._cur_year
        while m > 12:
            m -= 12
            y += 1
        while m < 1:
            m += 12
            y -= 1
        self._cur_month = m
        self._cur_year = y
        self._redraw_grid()

    def _go_today(self) -> None:
        self._today = date.today()
        self._cur_month = self._today.month
        self._cur_year = self._today.year
        self._selected = self._today
        self._redraw_grid()
        self._show_day_detail(self._selected)

    def _do_search(self) -> None:
        q = self._search_var.get().strip().lower()
        if not q:
            return
        results: list[tuple[str, dict]] = []
        for iso, evs in self._events.items():
            for ev in evs:
                if q in ev.get("title", "").lower() or q in ev.get("note", "").lower():
                    results.append((iso, ev))

        t = self._tmgr.theme
        win = tk.Toplevel(self)
        win.title(f'Resultados: "{q}"')
        win.config(bg=t.floating_bg)
        win.geometry("480x380")

        tk.Label(
            win,
            text=f'🔍  {len(results)} resultado(s) para "{q}"',
            bg=t.activity_bg,
            fg=t.fg,
            font=(FONT_UI, 10, "bold"),
        ).pack(fill="x", padx=0, pady=0, ipady=10)
        tk.Frame(win, bg=t.border, height=1).pack(fill="x")

        if not results:
            tk.Label(
                win,
                text=_t("calendar.no_results", "Nenhum resultado encontrado."),
                bg=t.floating_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 10),
            ).pack(pady=30)
            return

        outer = tk.Frame(win, bg=t.floating_bg)
        outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, bg=t.floating_bg, highlightthickness=0)
        vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        vsb.pack(side="right", fill="y")
        canvas.config(yscrollcommand=vsb.set)
        canvas.pack(fill="both", expand=True)
        inner = tk.Frame(canvas, bg=t.floating_bg)
        win_ = canvas.create_window((0, 0), window=inner, anchor="nw")
        debounce_canvas(canvas, win_, ms=40, extra_widget=inner)

        for iso, ev in sorted(results):
            row = tk.Frame(inner, bg=t.floating_bg)
            row.pack(fill="x", padx=10, pady=4)

            color = ev.get("color", t.accent)
            tk.Frame(row, bg=color, width=4).pack(side="left", fill="y")

            info = tk.Frame(row, bg=t.btn_bg)
            info.pack(side="left", fill="both", expand=True, padx=1)

            tk.Label(
                info,
                text=f"📅 {iso}  {ev.get('time', '')}",
                bg=t.btn_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 7),
            ).pack(anchor="w", padx=8, pady=(5, 0))
            lbl = tk.Label(
                info,
                text=ev.get("title", ""),
                bg=t.btn_bg,
                fg=t.fg,
                font=(FONT_UI, 10, "bold"),
                anchor="w",
                cursor="hand2",
            )
            lbl.pack(anchor="w", padx=8, pady=(0, 5))
            d_obj = date.fromisoformat(iso)
            lbl.bind(
                "<Button-1>",
                lambda _, _d=d_obj: (
                    setattr(self, "_cur_month", _d.month),
                    setattr(self, "_cur_year", _d.year),
                    setattr(self, "_selected", _d),
                    self._redraw_grid(),
                    self._show_day_detail(_d),
                    win.destroy(),
                ),
            )

    def _start_reminder_monitor(self) -> None:
        def _monitor() -> None:
            while self._alive:
                try:
                    self._check_reminders()
                except Exception as exc:
                    logger.debug("Reminder check: %s", exc)
                time.sleep(60)

        self._reminder_thread = threading.Thread(
            target=_monitor, daemon=True, name="Codis-CalReminder"
        )
        self._reminder_thread.start()

    def _check_reminders(self) -> None:
        now = datetime.now()
        iso_today = now.date().isoformat()
        for ev in self._events.get(iso_today, []):
            if not ev.get("remind"):
                continue
            ev_time = ev.get("time", "").strip()
            if not ev_time:
                continue
            try:
                hh, mm = map(int, ev_time.split(":")[:2])
                ev_dt = now.replace(hour=hh, minute=mm,
                                    second=0, microsecond=0)
                diff = (ev_dt - now).total_seconds()
                if 60 <= diff <= 120:
                    title = ev.get("title", _t(
                        "calendar.event_default", "Evento"))
                    self.after(
                        0, lambda tt=title, et=ev_time: self._show_reminder(
                            tt, et)
                    )
            except (ValueError, AttributeError):
                pass

    def _show_reminder(self, title: str, ev_time: str) -> None:
        messagebox.showinfo(
            "🔔 Lembrete",
            f"Em breve: {title}\nHorário: {ev_time}",
            parent=self,
        )

    def destroy(self) -> None:
        """Cancela timers pendentes e destrói o painel."""
        self._alive = False
        super().destroy()
