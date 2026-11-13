"""Codis - Code Time Tracker panel.

Rastreamento local e privado de tempo de codificação por arquivo, extensão e
projeto - sem enviar nenhum dado à internet (100% offline).

Funcionalidades:
  - Tempo por arquivo (acumulado por sessão e histórico persistente)
  - Tempo por linguagem/extensão
  - Tempo por projeto (workspace)
  - Gráfico de barras diário (últimos 7 dias)
  - Streak tracker (dias consecutivos de código)
  - Exportação CSV
  - Integração com a activity bar via evento de foco do editor
"""

from __future__ import annotations

import contextlib
import csv
import json
import logging
import threading
import time
import tkinter as tk
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from src.constants import CONFIG_DIR, FONT_MONO, FONT_UI
from src.i18n import _t

logger = logging.getLogger("Codis.TimeTracker")

_TRACKER_FILE = CONFIG_DIR / "time_tracker.json"
_IDLE_TIMEOUT = 120
_HEARTBEAT_MS = 10_000

EXT_LANG: dict[str, str] = {
    ".py": "Python",
    ".pyw": "Python",
    ".pyi": "Python",
    ".js": "JavaScript",
    ".mjs": "JavaScript",
    ".ts": "TypeScript",
    ".jsx": "JSX",
    ".tsx": "TSX",
    ".html": "HTML",
    ".htm": "HTML",
    ".css": "CSS",
    ".scss": "SCSS",
    ".json": "JSON",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".toml": "TOML",
    ".md": "Markdown",
    ".rst": "reStructuredText",
    ".txt": "Text",
    ".sh": "Shell",
    ".bash": "Shell",
    ".zsh": "Shell",
    ".bat": "Batch",
    ".ps1": "PowerShell",
    ".c": "C",
    ".h": "C",
    ".cpp": "C++",
    ".hpp": "C++",
    ".cs": "C#",
    ".java": "Java",
    ".kt": "Kotlin",
    ".go": "Go",
    ".rs": "Rust",
    ".rb": "Ruby",
    ".php": "PHP",
    ".swift": "Swift",
    ".lua": "Lua",
    ".r": "R",
    ".sql": "SQL",
    ".xml": "XML",
    ".vue": "Vue",
    ".svelte": "Svelte",
    ".ex": "Elixir",
    ".exs": "Elixir",
    ".dart": "Dart",
}


def _lang_for(path: str | Path) -> str:
    return EXT_LANG.get(Path(path).suffix.lower(), "Other")


def _today() -> str:
    return date.today().isoformat()


class TimeTrackerModel:
    """Modelo de rastreamento; thread-safe via lock."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict = self._load()
        self._active_file: str | None = None
        self._active_project: str | None = None
        self._last_activity: float = 0.0

    def _load(self) -> dict:
        try:
            if _TRACKER_FILE.exists():
                return json.loads(_TRACKER_FILE.read_text(encoding="utf-8"))
        except Exception:
            logger.warning(
                "Não foi possível ler %s; iniciando com dados de rastreamento zerados.",
                _TRACKER_FILE,
                exc_info=True,
            )
        return {
            "days": {},
            "files": {},
            "langs": {},
            "projects": {},
            "streak": 0,
            "streak_last": "",
        }

    def save(self) -> None:
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            _TRACKER_FILE.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            logger.warning(
                "Falha ao salvar dados de rastreamento de tempo em %s.",
                _TRACKER_FILE,
                exc_info=True,
            )

    def on_activity(self, filepath: str | None, project: str | None) -> None:
        """Deve ser chamado a cada heartbeat ou evento de teclado."""
        now = time.time()
        with self._lock:
            elapsed = now - self._last_activity if self._last_activity else 0
            if elapsed > _IDLE_TIMEOUT:
                elapsed = 0

            if elapsed > 0 and self._active_file:
                self._record(elapsed, self._active_file, self._active_project)

            self._last_activity = now
            self._active_file = filepath
            self._active_project = project

    def _record(self, seconds: float, filepath: str, project: str | None) -> None:
        today = _today()
        d = self._data

        d.setdefault("days", {})
        d["days"].setdefault(today, 0.0)
        d["days"][today] += seconds

        d.setdefault("files", {})
        d["files"].setdefault(filepath, {"total": 0.0, "last": today})
        d["files"][filepath]["total"] += seconds
        d["files"][filepath]["last"] = today

        lang = _lang_for(filepath)
        d.setdefault("langs", {})
        d["langs"].setdefault(lang, 0.0)
        d["langs"][lang] += seconds

        if project:
            d.setdefault("projects", {})
            d["projects"].setdefault(project, 0.0)
            d["projects"][project] += seconds

        self._update_streak(today)

    def _update_streak(self, today: str) -> None:
        d = self._data
        last = d.get("streak_last", "")
        if last == today:
            return
        if last:
            yesterday = (date.fromisoformat(today) -
                         timedelta(days=1)).isoformat()
            if last == yesterday:
                d["streak"] = d.get("streak", 0) + 1
            else:
                d["streak"] = 1
        else:
            d["streak"] = 1
        d["streak_last"] = today

    def today_seconds(self) -> float:
        with self._lock:
            return self._data.get("days", {}).get(_today(), 0.0)

    def week_days(self) -> list[tuple[str, float]]:
        """Retorna (label_dia, segundos) dos últimos 7 dias."""
        with self._lock:
            days = self._data.get("days", {})
        result = []
        for i in range(6, -1, -1):
            d = (date.today() - timedelta(days=i)).isoformat()
            label = datetime.fromisoformat(d).strftime("%a")
            result.append((label, days.get(d, 0.0)))
        return result

    def top_files(self, n: int = 10) -> list[tuple[str, float]]:
        with self._lock:
            files = self._data.get("files", {})
        ranked = sorted(
            files.items(), key=lambda x: x[1]["total"], reverse=True)
        return [(p, v["total"]) for p, v in ranked[:n]]

    def lang_totals(self) -> list[tuple[str, float]]:
        with self._lock:
            langs = self._data.get("langs", {})
        return sorted(langs.items(), key=lambda x: x[1], reverse=True)

    def project_totals(self) -> list[tuple[str, float]]:
        with self._lock:
            projects = self._data.get("projects", {})
        return sorted(projects.items(), key=lambda x: x[1], reverse=True)

    def streak(self) -> int:
        with self._lock:
            return self._data.get("streak", 0)

    def total_all_time(self) -> float:
        with self._lock:
            return sum(self._data.get("days", {}).values())

    def export_csv(self, path: str) -> None:
        with self._lock:
            days = dict(self._data.get("days", {}))
            files = dict(self._data.get("files", {}))
            langs = dict(self._data.get("langs", {}))
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["Section", "Key", "Seconds", "HH:MM:SS"])
            for day, secs in sorted(days.items()):
                w.writerow(["Day", day, f"{secs:.0f}", _fmt(secs)])
            for fp, info in sorted(
                files.items(), key=lambda x: x[1]["total"], reverse=True
            ):
                w.writerow(
                    ["File", fp, f"{info['total']:.0f}", _fmt(info["total"])])
            for lang, secs in sorted(langs.items(), key=lambda x: x[1], reverse=True):
                w.writerow(["Language", lang, f"{secs:.0f}", _fmt(secs)])

    def reset(self) -> None:
        with self._lock:
            self._data = {
                "days": {},
                "files": {},
                "langs": {},
                "projects": {},
                "streak": 0,
                "streak_last": "",
            }
        self.save()


def _fmt(seconds: float) -> str:
    """Formata segundos como HH:MM:SS."""
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, se = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {se:02d}s"
    return f"{se}s"


class TimeTrackerPanel(tk.Frame):
    """Painel de rastreamento de tempo de codificação."""

    def __init__(self, parent: tk.Widget, tmgr, model: TimeTrackerModel) -> None:
        super().__init__(parent, bg=tmgr.theme.sidebar_bg)
        self._tmgr = tmgr
        self._model = model
        self._build()
        self._refresh()

    def _build(self) -> None:
        t = self._tmgr.theme

        for child in self.winfo_children():
            with contextlib.suppress(tk.TclError):
                child.destroy()

        hdr = tk.Frame(self, bg=t.sidebar_bg)
        hdr.pack(fill="x", padx=8, pady=(8, 4))
        tk.Label(
            hdr,
            text=_t("tracker.title", "⏱  Code Time Tracker"),
            bg=t.sidebar_bg,
            fg=t.accent,
            font=(FONT_UI, 11, "bold"),
        ).pack(side="left")
        btn_ref = tk.Label(
            hdr,
            text="↻",
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 12),
            cursor="hand2",
        )
        btn_ref.pack(side="right")
        btn_ref.bind("<Button-1>", lambda _: self._refresh())

        kpi = tk.Frame(self, bg=t.sidebar_bg)
        kpi.pack(fill="x", padx=8, pady=4)
        kpi.columnconfigure((0, 1, 2), weight=1)

        self._lbl_today = self._kpi_card(
            kpi, _t("tracker.today", "Hoje"), "0s", 0)
        self._lbl_total = self._kpi_card(
            kpi, _t("tracker.total", "Total"), "0s", 1)
        self._lbl_streak = self._kpi_card(
            kpi, _t("tracker.streak", "Streak"), "0", 2)

        tk.Frame(self, bg=t.border, height=1).pack(fill="x", padx=8, pady=4)

        style = ttk.Style()
        style.configure("TT.TNotebook", background=t.sidebar_bg, borderwidth=0)
        style.configure(
            "TT.TNotebook.Tab", background=t.btn_bg, foreground=t.fg, padding=(10, 4)
        )
        style.map(
            "TT.TNotebook.Tab",
            background=[("selected", t.sel_bg)],
            foreground=[("selected", t.sel_fg)],
        )

        nb = ttk.Notebook(self, style="TT.TNotebook")
        nb.pack(fill="both", expand=True, padx=6, pady=4)

        tab_week = tk.Frame(nb, bg=t.sidebar_bg)
        nb.add(tab_week, text=_t("tracker.tab_week", "7 dias"))
        self._week_canvas = tk.Canvas(
            tab_week, bg=t.editor_bg, highlightthickness=0, height=160
        )
        self._week_canvas.pack(fill="both", expand=True, padx=6, pady=8)

        tab_lang = tk.Frame(nb, bg=t.sidebar_bg)
        nb.add(tab_lang, text=_t("tracker.tab_langs", "Linguagens"))
        self._lang_frame = tk.Frame(tab_lang, bg=t.sidebar_bg)
        self._lang_frame.pack(fill="both", expand=True, padx=6, pady=6)

        tab_files = tk.Frame(nb, bg=t.sidebar_bg)
        nb.add(tab_files, text=_t("tracker.tab_files", "Arquivos"))
        self._files_frame = tk.Frame(tab_files, bg=t.sidebar_bg)
        self._files_frame.pack(fill="both", expand=True, padx=6, pady=6)

        tab_proj = tk.Frame(nb, bg=t.sidebar_bg)
        nb.add(tab_proj, text=_t("tracker.tab_projects", "Projetos"))
        self._proj_frame = tk.Frame(tab_proj, bg=t.sidebar_bg)
        self._proj_frame.pack(fill="both", expand=True, padx=6, pady=6)

        foot = tk.Frame(self, bg=t.sidebar_bg)
        foot.pack(fill="x", padx=8, pady=(4, 8))
        self._btn(
            foot, _t("tracker.export", "📤 Exportar CSV"), self._export, side="left"
        )
        self._btn(foot, _t("tracker.reset", "🗑 Resetar"),
                  self._reset, side="right")

    def _kpi_card(
        self, parent: tk.Widget, label: str, value: str, col: int
    ) -> tk.Label:
        t = self._tmgr.theme
        card = tk.Frame(
            parent,
            bg=t.editor_bg,
            relief="flat",
            highlightthickness=1,
            highlightbackground=t.border,
        )
        card.grid(row=0, column=col, padx=3, pady=2, sticky="ew")
        tk.Label(
            card, text=label, bg=t.editor_bg, fg=t.fg_muted, font=(FONT_UI, 8)
        ).pack(pady=(6, 0))
        lbl = tk.Label(
            card, text=value, bg=t.editor_bg, fg=t.accent, font=(
                FONT_UI, 11, "bold")
        )
        lbl.pack(pady=(0, 6))
        return lbl

    def _btn(self, parent: tk.Widget, text: str, cmd, side: str = "left") -> tk.Label:
        t = self._tmgr.theme
        b = tk.Label(
            parent,
            text=text,
            bg=t.btn_bg,
            fg=t.fg,
            font=(FONT_UI, 9),
            cursor="hand2",
            padx=10,
            pady=4,
            relief="flat",
        )
        b.pack(side=side, padx=4)
        b.bind("<Button-1>", lambda _: cmd())
        b.bind("<Enter>", lambda _, w=b: w.config(bg=t.btn_hover))
        b.bind("<Leave>", lambda _, w=b: w.config(bg=t.btn_bg))
        return b

    def _refresh(self) -> None:
        m = self._model
        self._lbl_today.config(text=_fmt(m.today_seconds()))
        self._lbl_total.config(text=_fmt(m.total_all_time()))
        streak = m.streak()
        self._lbl_streak.config(text=f"{streak}" if streak else "0")

        self._draw_week()
        self._draw_list(self._lang_frame, m.lang_totals(), show_bar=True)
        self._draw_files(self._files_frame, m.top_files())
        self._draw_list(self._proj_frame, m.project_totals(), show_bar=True)

    def _draw_week(self) -> None:
        t = self._tmgr.theme
        c = self._week_canvas
        c.delete("all")
        c.update_idletasks()
        W = c.winfo_width() or 300
        H = c.winfo_height() or 160

        data = self._model.week_days()
        max_s = max((v for _, v in data), default=1) or 1
        bar_w = W // 8
        pad_x = (W - bar_w * 7) // 2
        pad_y = 12
        bar_area = H - pad_y * 2 - 24

        accent = t.accent
        dim = t.fg_muted

        for i, (label, secs) in enumerate(data):
            x = pad_x + i * bar_w + bar_w // 2
            bh = int(bar_area * secs / max_s) if secs else 2
            y1 = H - pad_y - 18 - bh
            y2 = H - pad_y - 18

            c.create_rectangle(
                x - bar_w // 3,
                y1,
                x + bar_w // 3,
                y2,
                fill=accent,
                outline="",
                tags="bar",
            )
            c.create_text(
                x, H - pad_y - 4, text=label, fill=dim, font=(FONT_UI, 8), anchor="s"
            )
            if secs:
                c.create_text(
                    x, y1 - 2, text=_fmt(secs), fill=t.fg, font=(FONT_UI, 7), anchor="s"
                )

    def _draw_list(
        self, frame: tk.Frame, items: list[tuple[str, float]], show_bar: bool = False
    ) -> None:
        t = self._tmgr.theme
        for w in frame.winfo_children():
            w.destroy()

        if not items:
            tk.Label(
                frame,
                text=_t("tracker.no_data", "Sem dados ainda"),
                bg=t.sidebar_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 9),
            ).pack(pady=20)
            return

        max_v = max(v for _, v in items) or 1
        for name, secs in items:
            row = tk.Frame(frame, bg=t.sidebar_bg)
            row.pack(fill="x", pady=1)

            tk.Label(
                row,
                text=name,
                bg=t.sidebar_bg,
                fg=t.fg,
                font=(FONT_UI, 9),
                anchor="w",
                width=18,
            ).pack(side="left")
            if show_bar:
                bar_bg = tk.Frame(row, bg=t.border, height=6)
                bar_bg.pack(side="left", fill="x", expand=True, padx=(4, 4))
                bar_bg.update_idletasks()
                bw = bar_bg.winfo_width() or 80
                fill_w = max(2, int(bw * secs / max_v))
                tk.Frame(bar_bg, bg=t.accent, width=fill_w,
                         height=6).place(x=0, y=0)

            tk.Label(
                row,
                text=_fmt(secs),
                bg=t.sidebar_bg,
                fg=t.accent,
                font=(FONT_MONO, 9),
                width=9,
                anchor="e",
            ).pack(side="right")

    def _draw_files(self, frame: tk.Frame, items: list[tuple[str, float]]) -> None:
        t = self._tmgr.theme
        for w in frame.winfo_children():
            w.destroy()

        if not items:
            tk.Label(
                frame,
                text=_t("tracker.no_data", "Sem dados ainda"),
                bg=t.sidebar_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 9),
            ).pack(pady=20)
            return

        for fp, secs in items:
            row = tk.Frame(frame, bg=t.sidebar_bg)
            row.pack(fill="x", pady=1)
            name = Path(fp).name
            tk.Label(
                row, text=name, bg=t.sidebar_bg, fg=t.fg, font=(FONT_UI, 9), anchor="w"
            ).pack(side="left")
            tk.Label(
                row,
                text=_fmt(secs),
                bg=t.sidebar_bg,
                fg=t.accent,
                font=(FONT_MONO, 9),
                width=9,
                anchor="e",
            ).pack(side="right")

    def _export(self) -> None:
        path = filedialog.asksaveasfilename(
            title=_t("tracker.export_title", "Exportar rastreamento"),
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"),
                       (_t("sql.all_files", "Todos"), "*.*")],
        )
        if path:
            self._model.export_csv(path)
            messagebox.showinfo(
                _t("tracker.export_ok_title", "Exportado"),
                _t("tracker.export_ok", "Dados exportados com sucesso."),
            )

    def _reset(self) -> None:
        if messagebox.askyesno(
            _t("tracker.reset_title", "Resetar dados"),
            _t("tracker.reset_confirm", "Apagar todo o histórico de tempo?"),
        ):
            self._model.reset()
            self._refresh()

    def apply_theme(self, tmgr) -> None:
        self._tmgr = tmgr
        for w in self.winfo_children():
            w.destroy()
        self._build()
        self._refresh()
