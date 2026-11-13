"""Codis - Gerenciador de Tarefas.

Funcionalidades:
  - Visão Kanban (Backlog / Em Andamento / Revisão / Concluído)
  - Visão Lista com ordenação, filtros e arquivamento
  - Prioridades: 🔴 Crítico  🟠 Alto  🟡 Médio  🟢 Baixo
  - Tags customizáveis com cores
  - Data de vencimento com badge de atraso
  - Pesquisa em tempo real
  - ⏰ Agendador de comandos: executa shell commands em horário/intervalo definidos
  - Persistência em ~/.codis/tasks.json + ~/.codis/scheduled_tasks.json
"""

from __future__ import annotations

import contextlib
import logging
import subprocess
import threading
import time
import tkinter as tk
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from tkinter import messagebox, scrolledtext, ttk
from typing import Any

from src.constants import CONFIG_DIR, FONT_MONO, FONT_UI
from src.i18n import _t
from src.theme.utils import alpha_blend_hex, make_toolbar_button
from src.ui._perf_utils import debounce_canvas
from src.ui._persistence_utils import load_dataclass_list, save_dataclass_list
from src.ui.grab_safe import safe_grab_set

logger = logging.getLogger("Codis.TaskManager")

_TASKS_FILE = CONFIG_DIR / "tasks.json"
_SCHEDULED_FILE = CONFIG_DIR / "scheduled_tasks.json"

PRIORITIES = {
    "critical": ("🔴", "#f38ba8", "Crítico"),
    "high": ("🟠", "#fab387", "Alto"),
    "medium": ("🟡", "#f9e2af", "Médio"),
    "low": ("🟢", "#a6e3a1", "Baixo"),
}

COLUMNS: list[tuple[str, str]] = [
    ("backlog", "Backlog"),
    ("doing", "Em Andamento"),
    ("review", "Revisão"),
    ("done", "Concluído"),
]

TAG_PALETTE = [
    "#89b4fa",
    "#a6e3a1",
    "#fab387",
    "#f38ba8",
    "#cba6f7",
    "#f9e2af",
    "#94e2d5",
    "#eba0ac",
    "#89dceb",
    "#b4befe",
]


@dataclass
class Task:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    title: str = ""
    desc: str = ""
    column: str = "backlog"
    priority: str = "medium"
    tags: list[str] = field(default_factory=list)
    due: str = ""
    created: str = field(default_factory=lambda: date.today().isoformat())
    archived: bool = False


def _load_tasks() -> list[Task]:
    return load_dataclass_list(_TASKS_FILE, Task, logger=logger)


def _save_tasks(tasks: list[Task]) -> None:
    save_dataclass_list(_TASKS_FILE, tasks)


def _due_status(due_str: str) -> tuple[str, str]:
    """Retorna (label, color) baseado no vencimento."""
    if not due_str:
        return ("", "")
    try:
        d = date.fromisoformat(due_str)
        today = date.today()
        delta = (d - today).days
        if delta < 0:
            return (f"⚠ {abs(delta)}d atraso", "#f38ba8")
        if delta == 0:
            return ("hoje", "#f9e2af")
        if delta <= 3:
            return (f"⏰ {delta}d", "#fab387")
        return (f"📅 {d.strftime('%d/%m')}", "#6c7086")
    except ValueError:
        return ("", "")

REPEAT_MODES = [
    ("once", "Uma vez"),
    ("minutely", "A cada N minutos"),
    ("hourly", "A cada N horas"),
    ("daily", "Todo dia"),
    ("weekly", "Toda semana"),
]

WEEKDAYS = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]


@dataclass
class ScheduledTask:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    name: str = "Nova Tarefa Agendada"
    command: str = ""
    enabled: bool = True
    mode: str = "daily"
    run_at: str = "08:00"
    interval_n: int = 30
    weekday: int = 0
    once_dt: str = ""
    last_run: str = ""
    last_status: str = ""
    next_run: str = ""


def _load_scheduled() -> list[ScheduledTask]:
    return load_dataclass_list(_SCHEDULED_FILE, ScheduledTask, logger=logger)


def _save_scheduled(tasks: list[ScheduledTask]) -> None:
    save_dataclass_list(_SCHEDULED_FILE, tasks)


def _next_run(st: ScheduledTask) -> datetime | None:
    """Calcula próxima execução baseado no modo."""
    now = datetime.now()
    try:
        if st.mode == "once":
            if st.once_dt:
                return datetime.fromisoformat(st.once_dt)
            return None
        if st.mode == "minutely":
            base = datetime.fromisoformat(st.last_run) if st.last_run else now
            return base.replace(second=0, microsecond=0) + __import__(
                "datetime"
            ).timedelta(minutes=max(1, st.interval_n))
        if st.mode == "hourly":
            base = datetime.fromisoformat(st.last_run) if st.last_run else now
            return base.replace(minute=0, second=0, microsecond=0) + __import__(
                "datetime"
            ).timedelta(hours=max(1, st.interval_n))
        if st.mode == "daily":
            h, m = map(int, st.run_at.split(":"))
            candidate = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if candidate <= now:
                import datetime as _dt

                candidate += _dt.timedelta(days=1)
            return candidate
        if st.mode == "weekly":
            import datetime as _dt

            h, m = map(int, st.run_at.split(":"))
            days_ahead = (st.weekday - now.weekday()) % 7
            if days_ahead == 0:
                candidate = now.replace(
                    hour=h, minute=m, second=0, microsecond=0)
                if candidate <= now:
                    days_ahead = 7
                else:
                    return candidate
            return (now + _dt.timedelta(days=days_ahead)).replace(
                hour=h, minute=m, second=0, microsecond=0
            )
    except Exception:
        return None

    return None


def _run_scheduled_command(
    command: str, timeout: int = 60
) -> subprocess.CompletedProcess:
    """Executa um comando de tarefa agendada/manual via shell.

    O comando é sempre executado via shell: é digitado pelo **próprio
    usuário** no Agendador de Tarefas (não é entrada de terceiros) e o campo
    é, por definição, uma linha de shell, o que cobre metacaracteres como
    crase, ``$()``, ``<``, ``*``, ``~`` e glob de forma consistente.
    """
    return subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


class Scheduler:
    """Thread daemon que verifica tarefas agendadas a cada ~10s."""

    def __init__(self, get_tasks: Callable, on_result: Callable) -> None:
        """Inicializa o agendador de tarefas recorrentes."""
        self._get_tasks = get_tasks
        self._on_result = on_result
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Inicia o loop de verificação do agendador."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Para o loop de verificação do agendador."""
        self._running = False

    def _loop(self) -> None:
        while self._running:
            try:
                tasks = self._get_tasks()
                now = datetime.now()
                for st in tasks:
                    if not st.enabled or not st.command.strip():
                        continue
                    nxt = _next_run(st)
                    if nxt is None:
                        continue
                    diff = (now - nxt).total_seconds()
                    if 0 <= diff <= 12:
                        self._run_task(st)
            except Exception as exc:
                logger.debug("Scheduler loop error: %s", exc)
            time.sleep(10)

    def _run_task(self, st: ScheduledTask) -> None:
        st.last_run = datetime.now().isoformat(timespec="seconds")
        try:
            result = _run_scheduled_command(st.command, timeout=60)
            output = (result.stdout + result.stderr).strip()[:2000]
            ok = result.returncode == 0
            st.last_status = f"{'OK' if ok else 'ERR'} código {result.returncode}"
        except subprocess.TimeoutExpired:
            output = "Timeout (60s)"
            ok = False
            st.last_status = "⏰ Timeout"
        except Exception as exc:
            output = str(exc)
            ok = False
            st.last_status = f"❌ {exc}"

        nxt = _next_run(st)
        st.next_run = nxt.isoformat(timespec="seconds") if nxt else ""

        if st.mode == "once":
            st.enabled = False

        self._on_result(st, output, ok)


class TaskManagerPanel(tk.Frame):
    """Gerenciador de Tarefas Kanban + Agendador."""

    def __init__(self, parent: tk.Widget, tmgr: Any, cfg: Any) -> None:
        """Inicializa o painel completo do gerenciador de tarefas."""
        t = tmgr.theme
        super().__init__(parent, bg=t.panel_bg)
        self._tmgr = tmgr
        self._cfg = cfg

        self._tasks: list[Task] = _load_tasks()
        self._scheduled: list[ScheduledTask] = _load_scheduled()
        self._view = "kanban"
        self._filter_q = ""
        self._filter_prio = "all"

        self._render_job: str | None = None
        self._filter_job: str | None = None

        self._list_tree: ttk.Treeview | None = None

        self._scheduler = Scheduler(
            get_tasks=lambda: list(self._scheduled),
            on_result=self._on_scheduled_result,
        )
        self._scheduler.start()

        self._build(t)

    def _on_scheduled_result(self, st: ScheduledTask, output: str, ok: bool) -> None:
        """Chamado pela scheduler thread com resultado da execução."""
        for i, s in enumerate(self._scheduled):
            if s.id == st.id:
                self._scheduled[i] = st
                break
        _save_scheduled(self._scheduled)
        with contextlib.suppress(Exception):
            self.after(0, lambda: self._on_run_complete(st, output, ok))

    def _on_run_complete(self, st: ScheduledTask, output: str, ok: bool) -> None:
        logger.info("Tarefa agendada '%s': %s", st.name, st.last_status)
        if hasattr(self, "_sched_tree"):
            self._refresh_sched_list()
        icon = "+" if ok else "x"
        logger.info("Scheduled '%s' %s: %s", st.name, icon, output[:80])

    def _build(self, t: Any) -> None:
        for child in self.winfo_children():
            with contextlib.suppress(tk.TclError):
                child.destroy()

        hdr = tk.Frame(self, bg=t.activity_bg, height=52)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(
            hdr, text="✅", bg=t.activity_bg, fg=t.accent, font=(FONT_UI, 18)
        ).pack(side="left", padx=(12, 4), pady=8)
        tk.Label(
            hdr,
            text=_t("tasks.title", "Tarefas"),
            bg=t.activity_bg,
            fg=t.fg,
            font=(FONT_UI, 13, "bold"),
        ).pack(side="left")

        vsw = tk.Frame(hdr, bg=t.activity_bg)
        vsw.pack(side="right", padx=12)
        self._view_btns: dict[str, tk.Label] = {}
        for key, icon, lbl in (
            ("kanban", "⬜", _t("tasks.view_kanban", "Kanban")),
            ("list", "☰", _t("tasks.view_list", "Lista")),
            ("schedule", "⏰", _t("tasks.view_sched", "Agendador")),
        ):
            b = tk.Label(
                vsw,
                text=f"{icon} {lbl}",
                bg=t.tab_act_bg if key == self._view else t.tabbar_bg,
                fg=t.tab_act_fg if key == self._view else t.tab_in_fg,
                font=(FONT_UI, 8),
                padx=10,
                pady=4,
                cursor="hand2",
            )
            b.pack(side="left", padx=1)
            b.bind("<Button-1>", lambda _, k=key: self._switch_view(k))
            self._view_btns[key] = b

        tk.Frame(self, bg=t.border, height=1).pack(fill="x")

        toolbar = tk.Frame(self, bg=t.sidebar_bg)
        toolbar.pack(fill="x")
        self._build_toolbar(t, toolbar)

        tk.Frame(self, bg=t.border, height=1).pack(fill="x")

        self._content = tk.Frame(self, bg=t.panel_bg)
        self._content.pack(fill="both", expand=True)
        self._render()

    def _build_toolbar(self, t: Any, toolbar: tk.Widget) -> None:
        def _btn(lbl: str, cmd: Any, accent: bool = False) -> tk.Label:
            return make_toolbar_button(
                toolbar, t, lbl, cmd, accent=accent, inner_padx=10
            )

        self._add_btn = _btn(
            _t("tasks.new_task", "✚ Nova Tarefa"), self._new_task, accent=True
        )

        tk.Label(
            toolbar, text="🔍", bg=t.sidebar_bg, fg=t.fg_muted, font=(FONT_UI, 9)
        ).pack(side="left", padx=(8, 2))
        self._q_var = tk.StringVar()
        self._q_var.trace_add("write", lambda *_: self._apply_filters())
        tk.Entry(
            toolbar,
            textvariable=self._q_var,
            bg=t.editor_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            bd=0,
            font=(FONT_UI, 8),
            width=18,
            highlightthickness=1,
            highlightbackground=t.border,
        ).pack(side="left", padx=2, ipady=3)

        tk.Label(
            toolbar,
            text=_t("tasks.filter_prio", "Prio:"),
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 7),
        ).pack(side="left", padx=(8, 2))
        self._prio_filter = tk.StringVar(value="all")
        pf = ttk.Combobox(
            toolbar,
            textvariable=self._prio_filter,
            state="readonly",
            width=10,
            font=(FONT_UI, 8),
            values=["all"] + list(PRIORITIES.keys()),
        )
        pf.pack(side="left", padx=2)
        pf.bind("<<ComboboxSelected>>", lambda _: self._apply_filters())

        self._count_lbl = tk.Label(
            toolbar, text="", bg=t.sidebar_bg, fg=t.fg_muted, font=(FONT_UI, 7)
        )
        self._count_lbl.pack(side="right", padx=12)

    def _switch_view(self, view: str) -> None:
        if view == self._view:
            return
        self._view = view
        self._list_tree = None
        t = self._tmgr.theme
        for k, b in self._view_btns.items():
            b.config(
                bg=t.tab_act_bg if k == view else t.tabbar_bg,
                fg=t.tab_act_fg if k == view else t.tab_in_fg,
            )
        if view == "schedule":
            self._add_btn.config(
                text=_t("tasks.new_scheduled", "✚ Novo Agendamento"))
        else:
            self._add_btn.config(text=_t("tasks.new_task", "✚ Nova Tarefa"))
        self._render()

    def _apply_filters(self) -> None:
        """Atualiza filtros com debounce de 80ms para evitar render a cada tecla."""
        self._filter_q = self._q_var.get().lower()
        self._filter_prio = self._prio_filter.get()
        if self._filter_job:
            with contextlib.suppress(Exception):
                self.after_cancel(self._filter_job)
        self._filter_job = self.after(80, self._render_debounced)

    def _render_debounced(self) -> None:
        """Versao debounced de _render para uso em filtros de texto."""
        self._filter_job = None
        self._render()

    def _schedule_render(self, delay: int = 0) -> None:
        """Agenda _render() com debounce minimo para agrupar mutacoes rapidas.

        Chamado por _move_task, _archive_task, _delete_task para que multiplas
        operacoes rapidas resultem em um unico rebuild de UI.
        """
        if self._render_job:
            with contextlib.suppress(Exception):
                self.after_cancel(self._render_job)
        self._render_job = self.after(delay, self._do_scheduled_render)

    def _do_scheduled_render(self) -> None:
        self._render_job = None
        self._render()

    def _visible_tasks(self) -> list[Task]:
        q = self._filter_q
        prio = self._filter_prio
        return [
            t
            for t in self._tasks
            if not t.archived
            and (
                not q
                or q in t.title.lower()
                or q in t.desc.lower()
                or any(q in tg.lower() for tg in t.tags)
            )
            and prio in ("all", t.priority)
        ]

    def _render(self) -> None:
        """Renderiza a view atual com otimizacao de reuso de widgets.

        Para a visao lista (mais usada com filtros), atualiza o Treeview
        existente em vez de destruir e recriar tudo - elimina o lag de
        digitacao no campo de busca.
        """
        if self._view == "list" and self._list_tree is not None:
            tasks = self._visible_tasks()
            self._update_list_tree(tasks)
            self._count_lbl.config(text=f"{len(tasks)} tarefa(s)")
            return

        for w in self._content.winfo_children():
            w.destroy()
        self._list_tree = None

        if self._view == "kanban":
            tasks = self._visible_tasks()
            self._render_kanban(tasks)
            self._count_lbl.config(text=f"{len(tasks)} tarefa(s)")
        elif self._view == "list":
            tasks = self._visible_tasks()
            self._render_list(tasks)
            self._count_lbl.config(text=f"{len(tasks)} tarefa(s)")
        else:
            self._render_schedule()
            self._count_lbl.config(
                text=f"{sum(1 for s in self._scheduled if s.enabled)} agendamento(s) ativo(s)"
            )

    def _new_task(self) -> None:
        if self._view == "schedule":
            self._open_sched_editor(None)
        else:
            self._open_editor(None)

    def _render_kanban(self, tasks: list[Task]) -> None:
        t = self._tmgr.theme
        canvas = tk.Canvas(self._content, bg=t.panel_bg, highlightthickness=0)
        hsb = ttk.Scrollbar(
            self._content, orient="horizontal", command=canvas.xview)
        canvas.configure(xscrollcommand=hsb.set)
        hsb.pack(side="bottom", fill="x")
        canvas.pack(fill="both", expand=True)

        inner = tk.Frame(canvas, bg=t.panel_bg)
        _kanban_win = canvas.create_window((0, 0), window=inner, anchor="nw")
        debounce_canvas(canvas, _kanban_win, ms=40, extra_widget=inner)

        for col_key, col_label in COLUMNS:
            col_tasks = [tk_ for tk_ in tasks if tk_.column == col_key]
            self._build_kanban_col(t, inner, col_key, col_label, col_tasks)

    def _build_kanban_col(
        self, t: Any, parent: tk.Widget, col_key: str, col_label: str, tasks: list[Task]
    ) -> None:
        col_colors = {
            "backlog": "#313244",
            "doing": "#1e3a5f",
            "review": "#2d2b55",
            "done": "#1a3a2a",
        }
        col_accents = {
            "backlog": "#89b4fa",
            "doing": "#89dceb",
            "review": "#cba6f7",
            "done": "#a6e3a1",
        }
        col_bg = col_colors.get(col_key, t.panel_bg)
        col_acc = col_accents.get(col_key, t.accent)

        frame = tk.Frame(parent, bg=col_bg, width=240, relief="flat", bd=0)
        frame.pack(side="left", fill="y", padx=6, pady=8)
        frame.pack_propagate(False)

        hdr = tk.Frame(frame, bg=col_acc, height=3)
        hdr.pack(fill="x")

        title_row = tk.Frame(frame, bg=col_bg)
        title_row.pack(fill="x", padx=10, pady=(8, 4))
        tk.Label(
            title_row, text=col_label, bg=col_bg, fg=t.fg, font=(
                FONT_UI, 9, "bold")
        ).pack(side="left")
        badge = tk.Label(
            title_row,
            text=str(len(tasks)),
            bg=col_acc,
            fg="#1e1e2e",
            font=(FONT_UI, 7, "bold"),
            padx=5,
            pady=1,
        )
        badge.pack(side="right")

        tk.Frame(frame, bg=t.border, height=1).pack(fill="x", padx=8)

        cards_frame = tk.Frame(frame, bg=col_bg)
        cards_frame.pack(fill="both", expand=True, padx=6, pady=4)

        for task in sorted(
            tasks, key=lambda x: list(PRIORITIES.keys()).index(x.priority)
        ):
            self._build_task_card(t, cards_frame, task, col_bg)

        add_l = tk.Label(
            cards_frame,
            text=f"＋ Adicionar em {col_label}",
            bg=col_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 7),
            cursor="hand2",
            pady=4,
        )
        add_l.pack(fill="x", pady=(4, 0))
        add_l.bind(
            "<Button-1>", lambda _, c=col_key: self._open_editor(
                None, default_col=c)
        )
        add_l.bind("<Enter>", lambda _: add_l.config(fg=t.accent))
        add_l.bind("<Leave>", lambda _: add_l.config(fg=t.fg_muted))

    def _build_task_card(
        self, t: Any, parent: tk.Widget, task: Task, col_bg: str
    ) -> None:
        prio_icon, prio_color, _ = PRIORITIES.get(
            task.priority, ("", t.fg, ""))

        card = tk.Frame(parent, bg=t.floating_bg,
                        relief="flat", bd=0, cursor="hand2")
        card.pack(fill="x", pady=3)

        strip = tk.Frame(card, bg=prio_color, width=3)
        strip.pack(side="left", fill="y")

        body = tk.Frame(card, bg=t.floating_bg)
        body.pack(fill="both", expand=True, padx=8, pady=6)

        title_row = tk.Frame(body, bg=t.floating_bg)
        title_row.pack(fill="x")
        tk.Label(
            title_row,
            text=prio_icon,
            bg=t.floating_bg,
            fg=prio_color,
            font=(FONT_UI, 9),
        ).pack(side="left")
        tk.Label(
            title_row,
            text=task.title[:30],
            bg=t.floating_bg,
            fg=t.fg,
            font=(FONT_UI, 9, "bold"),
            wraplength=180,
            anchor="w",
        ).pack(side="left", fill="x", expand=True)

        due_lbl, due_color = _due_status(task.due)
        if due_lbl:
            tk.Label(
                title_row,
                text=due_lbl,
                bg=t.floating_bg,
                fg=due_color,
                font=(FONT_UI, 7),
            ).pack(side="right")

        if task.desc:
            tk.Label(
                body,
                text=task.desc[:60] + ("..." if len(task.desc) > 60 else ""),
                bg=t.floating_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 7),
                anchor="w",
                justify="left",
                wraplength=200,
            ).pack(fill="x", pady=(2, 0))

        if task.tags:
            tag_row = tk.Frame(body, bg=t.floating_bg)
            tag_row.pack(fill="x", pady=(3, 0))
            for tg in task.tags[:4]:
                tc = _tag_color(tg)
                tk.Label(
                    tag_row,
                    text=tg,
                    bg=_alpha_blend_hex(tc, t.floating_bg, 0.2),
                    fg=tc,
                    font=(FONT_UI, 6),
                    padx=4,
                    pady=1,
                ).pack(side="left", padx=1)

        def _ctx(event: Any) -> None:
            menu = tk.Menu(card, tearoff=False, bg=t.floating_bg, fg=t.fg)
            menu.add_command(
                label=_t("tasks.edit"), command=lambda: self._open_editor(task)
            )
            menu.add_separator()
            for col_key, col_label in COLUMNS:
                if col_key != task.column:
                    menu.add_command(
                        label=f"→ {col_label}",
                        command=lambda c=col_key: self._move_task(task, c),
                    )
            menu.add_separator()
            menu.add_command(
                label=_t("tasks.archive"), command=lambda: self._archive_task(task)
            )
            menu.add_command(
                label=_t("tasks.delete"), command=lambda: self._delete_task(task)
            )
            menu.tk_popup(event.x_root, event.y_root)

        for w in [card, body, strip] + list(body.winfo_children()):
            try:
                w.bind("<Button-3>", _ctx)
                w.bind("<Double-1>", lambda _,
                       tk_=task: self._open_editor(tk_))
            except Exception:
                logger.debug(
                    "Falha ao vincular eventos ao card de tarefa.", exc_info=True
                )

    def _render_list(self, tasks: list[Task]) -> None:
        t = self._tmgr.theme
        cols = "prio", "title", "col", "due", "tags"

        tree_frame = tk.Frame(self._content, bg=t.panel_bg)
        tree_frame.pack(fill="both", expand=True)

        vsb = ttk.Scrollbar(tree_frame, orient="vertical")
        vsb.pack(side="right", fill="y")
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal")
        hsb.pack(side="bottom", fill="x")

        tree = ttk.Treeview(
            tree_frame,
            columns=cols,
            show="headings",
            selectmode="browse",
            yscrollcommand=vsb.set,
            xscrollcommand=hsb.set,
        )
        vsb.config(command=tree.yview)
        hsb.config(command=tree.xview)

        col_defs = {
            "prio": ("Prio", 60, "center"),
            "title": ("Título", 260, "w"),
            "col": ("Status", 120, "center"),
            "due": ("Vencimento", 110, "center"),
            "tags": ("Tags", 150, "w"),
        }
        for c, (label, width, anchor) in col_defs.items():
            tree.heading(
                c, text=label, command=lambda _c=c: self._sort_list(
                    tree, tasks, _c)
            )
            tree.column(
                c, width=width, minwidth=40, anchor=anchor, stretch=(c == "title")
            )

        tree.pack(fill="both", expand=True)
        self._list_tree = tree

        for task in tasks:
            prio_icon, prio_color, prio_label = PRIORITIES.get(
                task.priority, ("", t.fg, "?")
            )
            due_lbl, _ = _due_status(task.due)
            col_label = dict(COLUMNS).get(task.column, task.column)
            tree.insert(
                "",
                "end",
                iid=task.id,
                values=(
                    f"{prio_icon} {prio_label}",
                    task.title,
                    col_label,
                    due_lbl or task.due,
                    " ".join(task.tags[:3]),
                ),
                tags=(task.priority,),
            )

        for prio_key, (_, prio_color, _) in PRIORITIES.items():
            tree.tag_configure(prio_key, foreground=prio_color)

        def _on_double(_e: Any) -> None:
            sel = tree.selection()
            if sel:
                task = next((tk_ for tk_ in tasks if tk_.id == sel[0]), None)
                if task:
                    self._open_editor(task)

        tree.bind("<Double-1>", _on_double)
        tree.bind("<Return>", _on_double)
        tree.bind("<Button-3>", lambda e: self._list_context(e, tree, tasks))

        act_row = tk.Frame(self._content, bg=t.sidebar_bg)
        act_row.pack(fill="x")

        def _ab(lbl: str, cmd: Any) -> None:
            b = tk.Label(
                act_row,
                text=lbl,
                bg=t.sidebar_bg,
                fg=t.fg,
                font=(FONT_UI, 8),
                padx=8,
                pady=3,
                cursor="hand2",
            )
            b.pack(side="left", padx=2, pady=2)
            b.bind("<Button-1>", lambda _: cmd())
            b.bind("<Enter>", lambda _: b.config(bg=t.sel_bg))
            b.bind("<Leave>", lambda _: b.config(bg=t.sidebar_bg))

        _ab("✏ Editar", lambda: self._list_edit(tree, tasks))
        _ab("📦 Arquivar", lambda: self._list_archive(tree, tasks))
        _ab("🗑 Deletar", lambda: self._list_delete(tree, tasks))

        self._show_arch_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            act_row,
            text=_t("tasks.show_archived", "Mostrar arquivadas"),
            variable=self._show_arch_var,
            command=self._toggle_archived,
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            selectcolor=t.editor_bg,
            activebackground=t.sidebar_bg,
            font=(FONT_UI, 7),
        ).pack(side="right", padx=8)

    def _update_list_tree(self, tasks: list[Task]) -> None:
        """Atualiza o Treeview da lista sem destruir/recriar (O(n) em vez de O(n) widgets).

        Usa delete+insert apenas nas linhas que mudaram, mantendo a selecao
        atual e evitando o flash visual de um rebuild completo.
        """
        tree = self._list_tree
        if tree is None or not tree.winfo_exists():
            self._list_tree = None
            return

        t = self._tmgr.theme
        existing_ids = set(tree.get_children())
        new_ids = {task.id for task in tasks}

        to_remove = existing_ids - new_ids
        if to_remove:
            tree.delete(*to_remove)

        existing_now = set(tree.get_children())
        for idx, task in enumerate(tasks):
            prio_icon, _, prio_label = PRIORITIES.get(
                task.priority, ("", t.fg, "?"))
            due_lbl, _ = _due_status(task.due)
            col_label = dict(COLUMNS).get(task.column, task.column)
            values = (
                f"{prio_icon} {prio_label}",
                task.title,
                col_label,
                due_lbl or task.due,
                " ".join(task.tags[:3]),
            )
            if task.id in existing_now:
                try:
                    tree.item(task.id, values=values, tags=(task.priority,))
                    tree.move(task.id, "", idx)
                except Exception:
                    logger.debug(
                        "Falha ao atualizar item da árvore de tarefas.", exc_info=True
                    )
            else:
                with contextlib.suppress(Exception):
                    tree.insert(
                        "", idx, iid=task.id, values=values, tags=(task.priority,)
                    )

    def _sort_list(self, tree: ttk.Treeview, tasks: list[Task], col: str) -> None:
        """Ordena as tarefas da lista pela coluna *col* e re-renderiza a Treeview.

        A ordem é alternada a cada chamada (ASC -> DESC -> ASC).  O estado de
        ordenação atual é armazenado em ``self._sort_state`` como
        ``{col: bool}`` onde ``True`` significa ordem descendente.
        """
        if not hasattr(self, "_sort_state"):
            self._sort_state: dict[str, bool] = {}
        reverse = self._sort_state.get(col, False)
        self._sort_state[col] = not reverse

        key_fn: dict[str, Any] = {
            "title": lambda t: t.title.lower(),
            "priority": lambda t: list(PRIORITIES.keys()).index(t.priority),
            "due": lambda t: t.due or "",
            "status": lambda t: t.status,
        }
        sorter = key_fn.get(col, lambda t: t.title.lower())
        sorted_tasks = sorted(tasks, key=sorter, reverse=reverse)

        for item in tree.get_children():
            tree.delete(item)
        for task in sorted_tasks:
            due_txt = task.due or ""
            prio_icon = PRIORITIES.get(task.priority, {}).get("icon", "")
            tree.insert(
                "",
                "end",
                iid=task.id,
                values=(
                    task.title,
                    f"{prio_icon} {task.priority}",
                    due_txt,
                    task.status,
                ),
            )

    def _list_context(self, event: Any, tree: ttk.Treeview, tasks: list[Task]) -> None:
        iid = tree.identify_row(event.y)
        if not iid:
            return
        tree.selection_set(iid)
        task = next((tk_ for tk_ in tasks if tk_.id == iid), None)
        if not task:
            return
        t = self._tmgr.theme
        menu = tk.Menu(tree, tearoff=False, bg=t.floating_bg, fg=t.fg)
        menu.add_command(
            label=_t("tasks.edit"), command=lambda: self._open_editor(task)
        )
        menu.add_separator()
        for col_key, col_label in COLUMNS:
            if col_key != task.column:
                menu.add_command(
                    label=f"→ {col_label}",
                    command=lambda c=col_key: self._move_task(task, c),
                )
        menu.add_separator()
        menu.add_command(
            label=_t("tasks.archive"), command=lambda: self._archive_task(task)
        )
        menu.add_command(
            label=_t("tasks.delete"), command=lambda: self._delete_task(task)
        )
        menu.tk_popup(event.x_root, event.y_root)

    def _list_edit(self, tree: ttk.Treeview, tasks: list[Task]) -> None:
        sel = tree.selection()
        if sel:
            task = next((tk_ for tk_ in tasks if tk_.id == sel[0]), None)
            if task:
                self._open_editor(task)

    def _list_archive(self, tree: ttk.Treeview, tasks: list[Task]) -> None:
        sel = tree.selection()
        if sel:
            task = next((tk_ for tk_ in tasks if tk_.id == sel[0]), None)
            if task:
                self._archive_task(task)

    def _list_delete(self, tree: ttk.Treeview, tasks: list[Task]) -> None:
        sel = tree.selection()
        if sel:
            task = next((tk_ for tk_ in tasks if tk_.id == sel[0]), None)
            if task:
                self._delete_task(task)

    def _toggle_archived(self) -> None:
        self._list_tree = None
        self._schedule_render(0)

    def _render_schedule(self) -> None:
        t = self._tmgr.theme

        paned = tk.PanedWindow(
            self._content, orient="horizontal", bg=t.border, sashwidth=4
        )
        paned.pack(fill="both", expand=True)

        left = tk.Frame(paned, bg=t.panel_bg)
        paned.add(left, minsize=300, width=380)
        self._build_sched_list(t, left)

        right = tk.Frame(paned, bg=t.panel_bg)
        paned.add(right, minsize=200)
        self._build_sched_log(t, right)

    def _build_sched_list(self, t: Any, parent: tk.Widget) -> None:
        hdr = tk.Frame(parent, bg=t.sidebar_bg)
        hdr.pack(fill="x")
        tk.Label(
            hdr,
            text=_t("tasks.sched_list", "Tarefas Agendadas"),
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 7, "bold"),
        ).pack(side="left", padx=8, pady=4)

        def _btn(lbl: str, cmd: Any, accent: bool = False) -> tk.Label:
            return make_toolbar_button(
                hdr,
                t,
                lbl,
                cmd,
                accent=accent,
                side="right",
                outer_pady=3,
                inner_padx=6,
                inner_pady=3,
                font_size=7,
            )

        _btn("🗑", self._sched_delete)
        _btn("✏ Editar", self._sched_edit)
        _btn("✚ Novo", lambda: self._open_sched_editor(None), accent=True)

        cols = "enabled", "name", "mode", "next_run", "last_status"
        frame = tk.Frame(parent, bg=t.panel_bg)
        frame.pack(fill="both", expand=True)

        vsb = ttk.Scrollbar(frame, orient="vertical")
        vsb.pack(side="right", fill="y")

        self._sched_tree = ttk.Treeview(
            frame,
            columns=cols,
            show="headings",
            selectmode="browse",
            yscrollcommand=vsb.set,
        )
        vsb.config(command=self._sched_tree.yview)

        col_defs = {
            "enabled": ("+", 35, "center"),
            "name": ("Nome", 180, "w"),
            "mode": ("Modo", 90, "center"),
            "next_run": ("Próxima", 120, "center"),
            "last_status": ("Último", 100, "center"),
        }
        for c, (label, width, anchor) in col_defs.items():
            self._sched_tree.heading(c, text=label)
            self._sched_tree.column(
                c, width=width, minwidth=30, anchor=anchor, stretch=(c == "name")
            )

        self._sched_tree.pack(fill="both", expand=True)
        self._sched_tree.bind("<Double-1>", lambda _: self._sched_edit())
        self._refresh_sched_list()

    def _refresh_sched_list(self) -> None:
        self._sched_tree.delete(*self._sched_tree.get_children())
        mode_labels = dict(REPEAT_MODES)
        for st in self._scheduled:
            nxt = _next_run(st)
            nxt_str = nxt.strftime("%d/%m %H:%M") if nxt else "-"
            if st.next_run:
                with contextlib.suppress(Exception):
                    nxt_str = datetime.fromisoformat(st.next_run).strftime(
                        "%d/%m %H:%M"
                    )
            self._sched_tree.insert(
                "",
                "end",
                iid=st.id,
                values=(
                    "+" if st.enabled else "o",
                    st.name,
                    mode_labels.get(st.mode, st.mode),
                    nxt_str,
                    st.last_status or "-",
                ),
                tags=("enabled" if st.enabled else "disabled",),
            )
        self._sched_tree.tag_configure("enabled", foreground="#a6e3a1")
        self._sched_tree.tag_configure("disabled", foreground="#6c7086")

    def _build_sched_log(self, t: Any, parent: tk.Widget) -> None:
        hdr = tk.Frame(parent, bg=t.sidebar_bg)
        hdr.pack(fill="x")
        tk.Label(
            hdr,
            text=_t("tasks.sched_log", "Log de Execuções"),
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 7, "bold"),
        ).pack(side="left", padx=8, pady=4)

        def _clear() -> None:
            self._sched_log.config(state="normal")
            self._sched_log.delete("1.0", "end")
            self._sched_log.config(state="disabled")

        clr = tk.Label(
            hdr,
            text=_t("tasks.clear_log", "🗑 Limpar"),
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 7),
            padx=6,
            pady=3,
            cursor="hand2",
        )
        clr.pack(side="right", padx=4, pady=3)
        clr.bind("<Button-1>", lambda _: _clear())

        run_btn = tk.Label(
            hdr,
            text=_t("tasks.run_now", "▶ Executar agora"),
            bg=t.accent,
            fg="#FFF",
            font=(FONT_UI, 7),
            padx=8,
            pady=3,
            cursor="hand2",
        )
        run_btn.pack(side="right", padx=2, pady=3)
        run_btn.bind("<Button-1>", lambda _: self._run_sched_now())

        self._sched_log = scrolledtext.ScrolledText(
            parent,
            bg=t.editor_bg,
            fg=t.fg,
            font=(FONT_MONO, 8),
            relief="flat",
            bd=0,
            state="disabled",
            wrap="word",
        )
        self._sched_log.pack(fill="both", expand=True, padx=4, pady=4)
        self._sched_log.tag_configure("ok", foreground="#a6e3a1")
        self._sched_log.tag_configure("err", foreground="#f38ba8")
        self._sched_log.tag_configure("ts", foreground="#6c7086")

    def _run_sched_now(self) -> None:
        sel = self._sched_tree.selection()
        if not sel:
            return
        st = next((s for s in self._scheduled if s.id == sel[0]), None)
        if not st or not st.command.strip():
            return
        self._sched_log.config(state="normal")
        ts = datetime.now().strftime("%H:%M:%S")
        self._sched_log.insert("end", f"\n[{ts}] ▶ {st.name}\n", "ts")
        self._sched_log.insert("end", f"$ {st.command}\n", "ts")
        self._sched_log.config(state="disabled")

        def _work() -> None:
            self._scheduler._run_task(st)
            self.after(0, lambda: self._show_sched_result(st))

        threading.Thread(target=_work, daemon=True).start()

    def _show_sched_result(self, st: ScheduledTask) -> None:
        if not hasattr(self, "_sched_log"):
            return
        self._sched_log.config(state="normal")
        ok = st.last_status.startswith("OK")
        tag = "ok" if ok else "err"
        self._sched_log.insert("end", f"  {st.last_status}\n", tag)
        self._sched_log.see("end")
        self._sched_log.config(state="disabled")
        self._refresh_sched_list()

    def _sched_edit(self) -> None:
        sel = self._sched_tree.selection()
        if not sel:
            return
        st = next((s for s in self._scheduled if s.id == sel[0]), None)
        if st:
            self._open_sched_editor(st)

    def _sched_delete(self) -> None:
        sel = self._sched_tree.selection()
        if not sel:
            return
        st = next((s for s in self._scheduled if s.id == sel[0]), None)
        if st and messagebox.askyesno(
            _t("tasks.del_sched_title", "Remover"),
            _t("tasks.del_sched_confirm", f"Remover '{st.name}'?"),
        ):
            self._scheduled = [s for s in self._scheduled if s.id != st.id]
            _save_scheduled(self._scheduled)
            self._refresh_sched_list()

    def _open_sched_editor(self, st: ScheduledTask | None) -> None:
        _ScheduledTaskEditor(self, self._tmgr, st,
                             on_save=self._on_sched_saved)

    def _on_sched_saved(self, st: ScheduledTask) -> None:
        existing = next(
            (i for i, s in enumerate(self._scheduled) if s.id == st.id), None
        )
        if existing is not None:
            self._scheduled[existing] = st
        else:
            self._scheduled.append(st)
        _save_scheduled(self._scheduled)
        if hasattr(self, "_sched_tree"):
            self._refresh_sched_list()

    def _open_editor(self, task: Task | None, default_col: str = "backlog") -> None:
        if task is None:
            task = Task(column=default_col)
        _TaskEditor(self, self._tmgr, task, on_save=self._on_task_saved)

    def _on_task_saved(self, task: Task) -> None:
        existing = next((i for i, t in enumerate(
            self._tasks) if t.id == task.id), None)
        if existing is not None:
            self._tasks[existing] = task
        else:
            self._tasks.append(task)
        _save_tasks(self._tasks)
        self._render()

    def _move_task(self, task: Task, col: str) -> None:
        task.column = col
        _save_tasks(self._tasks)
        if self._view == "list" and self._list_tree is not None:
            self._schedule_render(0)
        else:
            self._schedule_render(0)

    def _archive_task(self, task: Task) -> None:
        task.archived = True
        _save_tasks(self._tasks)
        self._schedule_render(0)

    def _delete_task(self, task: Task) -> None:
        if messagebox.askyesno(
            _t("tasks.del_title", "Deletar"),
            _t("tasks.del_confirm", f"Deletar '{task.title}'?"),
        ):
            self._tasks = [t for t in self._tasks if t.id != task.id]
            _save_tasks(self._tasks)
            self._schedule_render(0)

    def apply_theme(self, tmgr: Any) -> None:
        """Aplica o tema ``t`` ao painel de tarefas."""
        self._tmgr = tmgr
        for w in self.winfo_children():
            w.destroy()
        self._build(tmgr.theme)

_alpha_blend_hex = alpha_blend_hex


def _tag_color(tag: str) -> str:
    idx = sum(ord(c) for c in tag) % len(TAG_PALETTE)
    return TAG_PALETTE[idx]


class _TaskEditor(tk.Toplevel):
    def __init__(
        self, parent: tk.Widget, tmgr: Any, task: Task, on_save: Callable
    ) -> None:
        """Inicializa o editor de tarefa individual."""
        super().__init__(parent)
        t = tmgr.theme
        self._tmgr = tmgr
        self._task = task
        self._on_save = on_save
        is_edit = bool(task.title)

        self.title(
            _t("tasks.edit_title", "Editar Tarefa")
            if is_edit
            else _t("tasks.new_title", "Nova Tarefa")
        )
        self.geometry("520x520")
        self.resizable(False, True)
        self.config(bg=t.panel_bg)
        safe_grab_set(self)

        self._build(t, is_edit)
        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        x = (self.winfo_screenwidth() - w) // 2
        y = (self.winfo_screenheight() - h) // 2
        self.geometry(f"520x520+{x}+{y}")

    def _build(self, t: Any, is_edit: bool) -> None:
        hdr = tk.Frame(self, bg=t.activity_bg)
        hdr.pack(fill="x")
        icon = "✏" if is_edit else "✚"
        tk.Label(
            hdr,
            text=f"{icon} {_t('tasks.edit_title', 'Editar') if is_edit else _t('tasks.new_title', 'Nova Tarefa')}",
            bg=t.activity_bg,
            fg=t.fg,
            font=(FONT_UI, 11, "bold"),
        ).pack(side="left", padx=12, pady=8)
        tk.Frame(self, bg=t.border, height=1).pack(fill="x")

        form = tk.Frame(self, bg=t.panel_bg)
        form.pack(fill="both", expand=True, padx=16, pady=8)
        form.columnconfigure(0, weight=1)

        row = 0

        def _lbl(text: str) -> None:
            nonlocal row
            tk.Label(
                form,
                text=text,
                bg=t.panel_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 8),
                anchor="w",
            ).grid(row=row, column=0, sticky="w", pady=(8, 2))
            row += 1

        def _ent(var: tk.Variable, height: int = 1) -> None:
            nonlocal row
            if height == 1:
                e = tk.Entry(
                    form,
                    textvariable=var,
                    bg=t.editor_bg,
                    fg=t.fg,
                    insertbackground=t.cursor,
                    relief="flat",
                    bd=0,
                    font=(FONT_UI, 10),
                    highlightthickness=1,
                    highlightbackground=t.border,
                )
                e.grid(row=row, column=0, sticky="ew", pady=2, ipady=5)
            else:
                txt = tk.Text(
                    form,
                    bg=t.editor_bg,
                    fg=t.fg,
                    insertbackground=t.cursor,
                    relief="flat",
                    bd=4,
                    font=(FONT_UI, 10),
                    height=height,
                    wrap="word",
                )
                txt.insert("1.0", var.get())
                txt.bind("<KeyRelease>", lambda _: var.set(
                    txt.get("1.0", "end-1c")))
                txt.grid(row=row, column=0, sticky="ew", pady=2)
            row += 1

        c = self._task
        self._title_var = tk.StringVar(value=c.title)
        self._desc_var = tk.StringVar(value=c.desc)
        self._due_var = tk.StringVar(value=c.due)
        self._tags_var = tk.StringVar(value=" ".join(c.tags))

        _lbl(_t("tasks.field_title", "Título *"))
        _ent(self._title_var)
        _lbl(_t("tasks.field_desc", "Descrição"))
        _ent(self._desc_var, height=3)
        _lbl(_t("tasks.field_due", "Vencimento (yyyy-mm-dd)"))
        _ent(self._due_var)
        _lbl(_t("tasks.field_tags", "Tags (separadas por espaço)"))
        _ent(self._tags_var)

        _lbl(_t("tasks.field_prio", "Prioridade"))
        self._prio_var = tk.StringVar(value=c.priority)
        pf = tk.Frame(form, bg=t.panel_bg)
        pf.grid(row=row, column=0, sticky="w")
        row += 1
        for key, (icon, color, label) in PRIORITIES.items():
            tk.Radiobutton(
                pf,
                text=f"{icon} {label}",
                variable=self._prio_var,
                value=key,
                bg=t.panel_bg,
                fg=color,
                selectcolor=t.editor_bg,
                activebackground=t.panel_bg,
                font=(FONT_UI, 9),
            ).pack(side="left", padx=4)

        _lbl(_t("tasks.field_col", "Status"))
        self._col_var = tk.StringVar(value=c.column)
        ttk.Combobox(
            form,
            textvariable=self._col_var,
            state="readonly",
            font=(FONT_UI, 9),
            values=[k for k, _ in COLUMNS],
        ).grid(row=row, column=0, sticky="ew", pady=2)
        row += 1

        btn_row = tk.Frame(self, bg=t.panel_bg)
        btn_row.pack(fill="x", padx=16, pady=(4, 12))

        cancel_b = tk.Label(
            btn_row,
            text=_t("tasks.cancel", "Cancelar"),
            bg=t.sidebar_bg,
            fg=t.fg,
            font=(FONT_UI, 10),
            padx=14,
            pady=6,
            cursor="hand2",
        )
        cancel_b.pack(side="right")
        cancel_b.bind("<Button-1>", lambda _: self.destroy())

        save_b = tk.Label(
            btn_row,
            text=_t("tasks.save", "💾 Salvar"),
            bg=t.accent,
            fg="#FFF",
            font=(FONT_UI, 10, "bold"),
            padx=14,
            pady=6,
            cursor="hand2",
        )
        save_b.pack(side="right", padx=4)
        save_b.bind("<Button-1>", lambda _: self._save())

    def _save(self) -> None:
        title = self._title_var.get().strip()
        if not title:
            messagebox.showwarning(
                _t("tasks.required", "Campo obrigatório"),
                _t("tasks.title_required", "O título é obrigatório."),
                parent=self,
            )
            return
        self._task.title = title
        self._task.desc = self._desc_var.get().strip()
        self._task.priority = self._prio_var.get()
        self._task.column = self._col_var.get()
        self._task.due = self._due_var.get().strip()
        self._task.tags = [tg for tg in self._tags_var.get().split() if tg]
        self._on_save(self._task)
        self.destroy()


class _ScheduledTaskEditor(tk.Toplevel):
    def __init__(
        self, parent: tk.Widget, tmgr: Any, st: ScheduledTask | None, on_save: Callable
    ) -> None:
        """Inicializa o editor de tarefa agendada."""
        super().__init__(parent)
        t = tmgr.theme
        self._tmgr = tmgr
        self._st = st or ScheduledTask()
        self._on_save = on_save
        is_edit = st is not None

        self.title(
            _t(
                "tasks.sched_editor_title",
                "Editar Agendamento" if is_edit else "Novo Agendamento",
            )
        )
        self.geometry("520x580")
        self.resizable(False, True)
        self.config(bg=t.panel_bg)
        safe_grab_set(self)

        self._build(t, is_edit)
        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        x = (self.winfo_screenwidth() - w) // 2
        y = (self.winfo_screenheight() - h) // 2
        self.geometry(f"520x580+{x}+{y}")

    def _build(self, t: Any, is_edit: bool) -> None:
        hdr = tk.Frame(self, bg=t.activity_bg)
        hdr.pack(fill="x")
        icon = "✏" if is_edit else "⏰"
        tk.Label(
            hdr,
            text=f"{icon} {self.title()}",
            bg=t.activity_bg,
            fg=t.fg,
            font=(FONT_UI, 11, "bold"),
        ).pack(side="left", padx=12, pady=8)
        tk.Frame(self, bg=t.border, height=1).pack(fill="x")

        form = tk.Frame(self, bg=t.panel_bg)
        form.pack(fill="both", expand=True, padx=16, pady=8)
        form.columnconfigure(1, weight=1)
        row = 0

        def _lbl(text: str) -> None:
            nonlocal row
            tk.Label(
                form,
                text=text,
                bg=t.panel_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 8),
                anchor="w",
            ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(10, 2))
            row += 1

        def _ent(var: tk.Variable, show: str = "", width: int = 0) -> tk.Entry:
            nonlocal row
            kw: dict = {
                "textvariable": var,
                "bg": t.editor_bg,
                "fg": t.fg,
                "insertbackground": t.cursor,
                "relief": "flat",
                "bd": 0,
                "font": (FONT_UI, 10),
                "highlightthickness": 1,
                "highlightbackground": t.border,
                "show": show,
            }
            if width:
                kw["width"] = width
            e = tk.Entry(form, **kw)
            e.grid(row=row, column=0, columnspan=2,
                   sticky="ew", pady=2, ipady=5)
            row += 1
            return e

        st = self._st

        _lbl(_t("tasks.sched_name", "Nome"))
        self._name_var = tk.StringVar(value=st.name)
        _ent(self._name_var)

        _lbl(_t("tasks.sched_cmd", "Comando Shell"))
        self._cmd_var = tk.StringVar(value=st.command)
        _ent(self._cmd_var)
        tk.Label(
            form,
            text=_t(
                "tasks.sched_cmd_hint", "Ex: python backup.py  |  echo hello > log.txt"
            ),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 7),
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 4))
        row += 1

        _lbl(_t("tasks.sched_mode", "Modo de repetição"))
        self._mode_var = tk.StringVar(value=st.mode)
        mode_cb = ttk.Combobox(
            form,
            textvariable=self._mode_var,
            state="readonly",
            font=(FONT_UI, 9),
            values=[k for k, _ in REPEAT_MODES],
        )
        mode_cb.grid(row=row, column=0, columnspan=2, sticky="ew", pady=2)
        row += 1

        self._dyn = tk.Frame(form, bg=t.panel_bg)
        self._dyn.grid(row=row, column=0, columnspan=2, sticky="ew")
        row += 1

        mode_cb.bind("<<ComboboxSelected>>", lambda _: self._rebuild_dyn(t))

        self._enabled_var = tk.BooleanVar(value=st.enabled)
        tk.Checkbutton(
            form,
            text=_t("tasks.sched_enabled", "Habilitado"),
            variable=self._enabled_var,
            bg=t.panel_bg,
            fg=t.fg,
            selectcolor=t.editor_bg,
            activebackground=t.panel_bg,
            font=(FONT_UI, 9),
        ).grid(row=row, column=0, sticky="w", pady=8)
        row += 1

        btn_row = tk.Frame(self, bg=t.panel_bg)
        btn_row.pack(fill="x", padx=16, pady=(4, 12))

        test_b = tk.Label(
            btn_row,
            text=_t("tasks.sched_test", "▶ Testar Agora"),
            bg=t.sidebar_bg,
            fg=t.fg,
            font=(FONT_UI, 9),
            padx=10,
            pady=5,
            cursor="hand2",
        )
        test_b.pack(side="left")
        test_b.bind("<Button-1>", lambda _: self._test())

        cancel_b = tk.Label(
            btn_row,
            text=_t("tasks.cancel", "Cancelar"),
            bg=t.sidebar_bg,
            fg=t.fg,
            font=(FONT_UI, 9),
            padx=10,
            pady=5,
            cursor="hand2",
        )
        cancel_b.pack(side="right")
        cancel_b.bind("<Button-1>", lambda _: self.destroy())

        save_b = tk.Label(
            btn_row,
            text=_t("tasks.save", "💾 Salvar"),
            bg=t.accent,
            fg="#FFF",
            font=(FONT_UI, 9, "bold"),
            padx=12,
            pady=5,
            cursor="hand2",
        )
        save_b.pack(side="right", padx=4)
        save_b.bind("<Button-1>", lambda _: self._save())

        self._rebuild_dyn(t)

    def _rebuild_dyn(self, t: Any) -> None:
        for w in self._dyn.winfo_children():
            w.destroy()
        f = self._dyn
        f.columnconfigure(1, weight=1)
        mode = self._mode_var.get()
        st = self._st
        row = 0

        def _lbl(text: str) -> None:
            nonlocal row
            tk.Label(
                f,
                text=text,
                bg=t.panel_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 8),
                anchor="w",
            ).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=(8, 2))
            row += 1

        def _ent(var: tk.Variable, col: int = 0, colspan: int = 2) -> tk.Entry:
            nonlocal row
            e = tk.Entry(
                f,
                textvariable=var,
                bg=t.editor_bg,
                fg=t.fg,
                insertbackground=t.cursor,
                relief="flat",
                bd=0,
                font=(FONT_UI, 10),
                highlightthickness=1,
                highlightbackground=t.border,
                width=12,
            )
            e.grid(
                row=row, column=col, columnspan=colspan, sticky="ew", pady=2, ipady=5
            )
            row += 1
            return e

        if mode == "once":
            _lbl(_t("tasks.sched_once_dt", "Data/hora (yyyy-mm-dd HH:MM)"))
            default = st.once_dt or datetime.now().strftime("%Y-%m-%d %H:%M")
            self._once_var = tk.StringVar(value=default)
            _ent(self._once_var)

        elif mode in ("minutely", "hourly"):
            unit = "minutos" if mode == "minutely" else "horas"
            _lbl(f"Intervalo ({unit})")
            self._n_var = tk.StringVar(value=str(st.interval_n))
            _ent(self._n_var)

        elif mode == "daily":
            _lbl(_t("tasks.sched_run_at", "Horário (HH:MM)"))
            self._at_var = tk.StringVar(value=st.run_at or "08:00")
            _ent(self._at_var)

        elif mode == "weekly":
            _lbl(_t("tasks.sched_weekday", "Dia da semana"))
            self._wd_var = tk.StringVar(value=WEEKDAYS[st.weekday])
            ttk.Combobox(
                f,
                textvariable=self._wd_var,
                state="readonly",
                font=(FONT_UI, 9),
                values=WEEKDAYS,
            ).grid(row=row, column=0, columnspan=2, sticky="ew", pady=2)
            row += 1
            _lbl(_t("tasks.sched_run_at", "Horário (HH:MM)"))
            self._at_var = tk.StringVar(value=st.run_at or "08:00")
            _ent(self._at_var)

    def _collect(self) -> ScheduledTask:
        st = self._st
        st.name = self._name_var.get().strip() or "Sem nome"
        st.command = self._cmd_var.get().strip()
        st.mode = self._mode_var.get()
        st.enabled = self._enabled_var.get()
        mode = st.mode
        try:
            if mode == "once":
                st.once_dt = getattr(self, "_once_var", tk.StringVar()).get()
            elif mode in ("minutely", "hourly"):
                st.interval_n = int(
                    getattr(self, "_n_var", tk.StringVar(value="30")).get()
                )
            elif mode in ("daily", "weekly"):
                st.run_at = getattr(
                    self, "_at_var", tk.StringVar(value="08:00")).get()
                if mode == "weekly":
                    wd_str = getattr(
                        self, "_wd_var", tk.StringVar(value="Seg")).get()
                    st.weekday = WEEKDAYS.index(
                        wd_str) if wd_str in WEEKDAYS else 0
        except ValueError:
            pass
        return st

    def _test(self) -> None:
        st = self._collect()
        if not st.command.strip():
            messagebox.showwarning(
                _t("tasks.sched_no_cmd", "Sem comando"),
                _t("tasks.sched_cmd_required",
                   "Digite um comando antes de testar."),
                parent=self,
            )
            return

        win = tk.Toplevel(self)
        win.title(_t("tasks.sched_test_title", "Teste de Execução"))
        win.geometry("500x300")
        win.config(bg=self._tmgr.theme.panel_bg)
        t = self._tmgr.theme

        tk.Label(
            win,
            text=f"$ {st.command}",
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_MONO, 9),
            anchor="w",
        ).pack(fill="x", padx=8, pady=4)
        out = scrolledtext.ScrolledText(
            win, bg=t.editor_bg, fg=t.fg, font=(FONT_MONO, 9), relief="flat", bd=4
        )
        out.pack(fill="both", expand=True, padx=8, pady=4)
        out.insert("end", "⏳ Executando...\n")
        out.config(state="disabled")

        def _work() -> None:
            try:
                result = _run_scheduled_command(st.command, timeout=60)
                output = (result.stdout +
                          result.stderr).strip() or "(sem saída)"
                ok = result.returncode == 0
            except Exception as exc:
                output = str(exc)
                ok = False

            def _update() -> None:
                out.config(state="normal")
                out.delete("1.0", "end")
                icon = "+" if ok else "x"
                fg = "#a6e3a1" if ok else "#f38ba8"
                out.tag_configure("result", foreground=fg)
                out.insert("end", f"{icon} ", "result")
                out.insert("end", output)
                out.config(state="disabled")

            with contextlib.suppress(Exception):
                win.after(0, _update)

        threading.Thread(target=_work, daemon=True).start()

    def _save(self) -> None:
        st = self._collect()
        if not st.command.strip():
            messagebox.showwarning(
                _t("tasks.sched_no_cmd", "Sem comando"),
                _t("tasks.sched_cmd_required", "Digite um comando."),
                parent=self,
            )
            return
        self._on_save(st)
        self.destroy()
