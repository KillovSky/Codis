"""Codis - Gerenciador de Processos.

Funcionalidades:
  - Aba "Sistema": lista todos os processos do SO com CPU, memória, status, usuário
  - Aba "Codis": lista painéis abertos, threads internas e subprocessos do editor
  - Gráficos de uso de CPU e memória em tempo real
  - Painel de informações do sistema (RAM total, núcleos, uptime)
  - Filtro em tempo real por nome ou PID
  - Encerrar processo (com confirmação) e encerrar árvore de processos
  - Detalhes expandidos: linha de comando, threads, arquivos abertos
  - Auto-atualização configurável (1s / 2s / 5s)
  - Requer psutil (pip install psutil); degrada graciosamente sem ele
"""

from __future__ import annotations

import contextlib
import logging
import os
import platform
import sys
import threading
import tkinter as tk
from collections import deque
from tkinter import messagebox, ttk
from typing import Any

from src.constants import FONT_MONO, FONT_UI
from src.i18n import _t
from src.ui.grab_safe import safe_grab_set

logger = logging.getLogger("Codis.ProcessManager")


def _widget_alive(widget: tk.Widget) -> bool:
    """Retorna True se o widget ainda existe e não foi destruído."""
    try:
        return bool(widget.winfo_exists())
    except Exception:
        return False


try:
    import psutil  # type: ignore

    _PSUTIL = True
except ImportError:
    psutil = None  # type: ignore
    _PSUTIL = False

_STATUS_MAP = {
    "running": ("procmgr.status_running", "Rodando"),
    "sleeping": ("procmgr.status_sleeping", "Inativo"),
    "stopped": ("procmgr.status_stopped", "Parado"),
    "zombie": ("procmgr.status_zombie", "Zumbi"),
}

_HISTORY_LEN = 60


def _fmt_bytes(n: float) -> str:
    """Formata bytes em string legível (KB/MB/GB)."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _status_label(status: str) -> str:
    key, fallback = _STATUS_MAP.get(
        status, ("procmgr.status_unknown", "Desconhecido"))
    return _t(key, fallback)


class _DataCollector(threading.Thread):
    """Coleta dados de processos em background."""

    def __init__(self) -> None:
        """Inicializa o coletor de dados de sistema (CPU, RAM, processos)."""
        super().__init__(daemon=True, name="ProcMgr-Collector")
        self._interval = 2.0
        self._stop_evt = threading.Event()
        self._lock = threading.Lock()
        self._data: list[dict] = []
        self._cpu_history: deque[float] = deque(
            [0.0] * _HISTORY_LEN, maxlen=_HISTORY_LEN
        )
        self._mem_history: deque[float] = deque(
            [0.0] * _HISTORY_LEN, maxlen=_HISTORY_LEN
        )
        self._sys_info: dict = {}
        self._callbacks: list = []

    def set_interval(self, secs: float) -> None:
        """Define o intervalo de coleta de métricas em segundos."""
        self._interval = max(0.5, secs)

    def add_callback(self, cb: Any) -> None:
        """Registra callback chamado a cada atualização de métricas."""
        self._callbacks.append(cb)

    def get_data(self) -> list[dict]:
        """Retorna os dados mais recentes coletados."""
        with self._lock:
            return list(self._data)

    def get_cpu_history(self) -> list[float]:
        """Retorna o histórico de uso de CPU como lista de floats."""
        with self._lock:
            return list(self._cpu_history)

    def get_mem_history(self) -> list[float]:
        """Retorna o histórico de uso de memória como lista de floats."""
        with self._lock:
            return list(self._mem_history)

    def get_sys_info(self) -> dict:
        """Retorna informações estáticas do sistema (OS, CPU, RAM total)."""
        with self._lock:
            return dict(self._sys_info)

    def stop(self) -> None:
        """Interrompe a thread de coleta de métricas."""
        self._stop_evt.set()

    def run(self) -> None:
        """Loop principal da thread de coleta de métricas."""
        if not _PSUTIL:
            return
        psutil.cpu_percent(interval=None)
        while not self._stop_evt.wait(self._interval):
            try:
                self._collect()
                for cb in self._callbacks:
                    with contextlib.suppress(Exception):
                        cb()
            except Exception as exc:
                logger.debug("DataCollector error: %s", exc)

    def _collect(self) -> None:
        procs = []
        for proc in psutil.process_iter(
            [
                "pid",
                "name",
                "status",
                "username",
                "cpu_percent",
                "memory_info",
                "num_threads",
                "cmdline",
                "create_time",
            ]
        ):
            try:
                info = proc.info
                mem = info.get("memory_info")
                mem_bytes = mem.rss if mem else 0
                procs.append(
                    {
                        "pid": info["pid"],
                        "name": info.get("name") or "",
                        "status": info.get("status") or "unknown",
                        "user": info.get("username") or "",
                        "cpu": info.get("cpu_percent") or 0.0,
                        "mem": mem_bytes,
                        "mem_str": _fmt_bytes(mem_bytes),
                        "threads": info.get("num_threads") or 0,
                        "cmdline": " ".join(info.get("cmdline") or []),
                        "created": info.get("create_time") or 0,
                    }
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        cpu_total = psutil.cpu_percent(interval=None)
        mem_info = psutil.virtual_memory()
        mem_pct = mem_info.percent

        with self._lock:
            self._data = procs
            self._cpu_history.append(cpu_total)
            self._mem_history.append(mem_pct)
            self._sys_info = {
                "cpu_count": psutil.cpu_count(logical=True),
                "cpu_pct": cpu_total,
                "ram_total": _fmt_bytes(mem_info.total),
                "ram_used": _fmt_bytes(mem_info.used),
                "ram_pct": mem_pct,
                "proc_count": len(procs),
                "platform": platform.system(),
                "python": sys.version.split()[0],
            }


class _MiniGraph(tk.Canvas):
    """Gráfico de linha leve usando Canvas do Tkinter."""

    def __init__(
        self, parent: tk.Widget, label: str, color: str, bg: str, fg: str, **kw: Any
    ) -> None:
        """Inicializa o mini-gráfico de histórico de uso."""
        super().__init__(parent, bg=bg, highlightthickness=0, height=60, **kw)
        self._label = label
        self._color = color
        self._fg = fg
        self._bg = bg
        self._values: list[float] = [0.0] * _HISTORY_LEN
        self.bind("<Configure>", self._redraw)

    def update_values(self, values: list[float]) -> None:
        """Atualiza os valores exibidos no mini-gráfico."""
        if not _widget_alive(self):
            return
        self._values = list(values)
        self._redraw()

    def _redraw(self, _event: Any = None) -> None:
        if not _widget_alive(self):
            return
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 4 or h < 4:
            return

        vals = self._values[-w:] if len(self._values) > w else self._values
        n = len(vals)
        if n < 2:
            return

        step = w / (n - 1)
        pts = []
        for i, v in enumerate(vals):
            x = i * step
            y = h - (v / 100.0) * (h - 4) - 2
            pts.extend([x, y])

        fill_pts = [0, h] + pts + [w, h]
        self.create_polygon(fill_pts, fill=self._color,
                            outline="", stipple="gray50")
        self.create_line(pts, fill=self._color, width=2, smooth=True)
        cur = f"{vals[-1]:.1f}%"
        self.create_text(
            6, 6, text=self._label, anchor="nw", font=(FONT_UI, 7), fill=self._fg
        )
        self.create_text(
            w - 4,
            6,
            text=cur,
            anchor="ne",
            font=(FONT_MONO, 7, "bold"),
            fill=self._color,
        )


class _SystemTab(tk.Frame):
    """Aba que exibe processos do SO."""

    _COLS = [
        ("pid", "PID", 60, "e"),
        ("name", "Nome", 200, "w"),
        ("cpu", "CPU %", 70, "e"),
        ("mem", "Memória", 90, "e"),
        ("threads", "Threads", 65, "e"),
        ("status", "Status", 90, "w"),
        ("user", "Usuário", 120, "w"),
    ]

    def __init__(self, parent: tk.Widget, tmgr: Any, collector: _DataCollector) -> None:
        """Inicializa a aba de métricas de sistema."""
        t = tmgr.theme
        super().__init__(parent, bg=t.panel_bg)
        self._tmgr = tmgr
        self._collector = collector
        self._filter_var = tk.StringVar()
        self._filter_job: str | None = None
        self._all_data: list[dict] = []
        self._sort_col = "cpu"
        self._sort_rev = True

        self._build(t)
        collector.add_callback(self._schedule_refresh)

    def _build(self, t: Any) -> None:
        toolbar = tk.Frame(self, bg=t.panel_bg)
        toolbar.pack(fill="x", padx=8, pady=(6, 4))

        tk.Label(
            toolbar, text="🔍", bg=t.panel_bg, fg=t.fg_muted, font=(FONT_UI, 10)
        ).pack(side="left")
        self._search = tk.Entry(
            toolbar,
            textvariable=self._filter_var,
            bg=t.editor_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            font=(FONT_MONO, 9),
            width=26,
        )
        self._search.pack(side="left", padx=(2, 8))
        self._search.insert(
            0, _t("procmgr.search_placeholder", "Filtrar processos..."))
        self._search.bind("<FocusIn>", self._on_search_focus)
        self._search.bind("<FocusOut>", self._on_search_blur)
        self._filter_var.trace_add("write", lambda *_: self._schedule_filter())

        def _btn(text: str, cmd: Any, accent: bool = False) -> tk.Label:
            bg = t.accent if accent else t.sidebar_bg
            fg = "#FFF" if accent else t.fg
            lbl = tk.Label(
                toolbar,
                text=text,
                bg=bg,
                fg=fg,
                font=(FONT_UI, 8),
                cursor="hand2",
                padx=7,
                pady=4,
                relief="flat",
            )
            lbl.pack(side="left", padx=2)
            lbl.bind("<Button-1>", lambda _: cmd())
            lbl.bind("<Enter>", lambda _: lbl.config(bg=t.btn_hover))
            lbl.bind("<Leave>", lambda _: lbl.config(bg=bg))
            return lbl

        _btn(_t("procmgr.btn_kill", "⛔ Encerrar"), self._kill_selected)
        _btn(_t("procmgr.btn_kill_tree", "💀 Encerrar árvore"), self._kill_tree)
        _btn(_t("procmgr.btn_details", "🔍 Detalhes"), self._show_details)

        tree_frame = tk.Frame(self, bg=t.panel_bg)
        tree_frame.pack(fill="both", expand=True, padx=6, pady=(0, 4))

        cols = [c[0] for c in self._COLS]
        self._tree = ttk.Treeview(
            tree_frame, columns=cols, show="headings", selectmode="browse"
        )
        for col_id, col_label, col_w, anchor in self._COLS:
            self._tree.heading(
                col_id, text=col_label, command=lambda c=col_id: self._sort_by(
                    c)
            )
            self._tree.column(
                col_id,
                width=col_w,
                anchor=anchor,
                minwidth=40,
                stretch=(col_id == "name"),
            )

        vsb = ttk.Scrollbar(tree_frame, orient="vertical",
                            command=self._tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal",
                            command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.pack(side="right", fill="y")
        hsb.pack(side="bottom", fill="x")
        self._tree.pack(fill="both", expand=True)
        self._tree.bind("<Double-1>", lambda _: self._show_details())

        self._status_var = tk.StringVar(value="")
        tk.Label(
            self,
            textvariable=self._status_var,
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            anchor="w",
        ).pack(fill="x", padx=8, pady=(0, 4))

    def _on_search_focus(self, _e: Any = None) -> None:
        placeholder = _t("procmgr.search_placeholder", "Filtrar processos...")
        if self._search.get() == placeholder:
            self._search.delete(0, "end")

    def _on_search_blur(self, _e: Any = None) -> None:
        if not self._search.get():
            placeholder = _t("procmgr.search_placeholder",
                             "Filtrar processos...")
            self._search.insert(0, placeholder)

    def _sort_by(self, col: str) -> None:
        if self._sort_col == col:
            self._sort_rev = not self._sort_rev
        else:
            self._sort_col = col
            self._sort_rev = col in ("cpu", "mem", "threads")
        self._render(self._all_data)

    def _schedule_refresh(self) -> None:
        if not _widget_alive(self):
            return
        with contextlib.suppress(Exception):
            self.after(0, self._refresh)

    def _refresh(self) -> None:
        if not _widget_alive(self):
            return
        self._all_data = self._collector.get_data()
        self._apply_filter()

    def _schedule_filter(self) -> None:
        """Debounce de 60ms no filtro de busca para nao chamar psutil a cada tecla."""
        if not _widget_alive(self):
            return
        if self._filter_job:
            with contextlib.suppress(Exception):
                self.after_cancel(self._filter_job)
        with contextlib.suppress(Exception):
            self._filter_job = self.after(60, self._apply_filter)

    def _apply_filter(self) -> None:
        if not _widget_alive(self) or not _widget_alive(self._tree):
            return
        placeholder = _t("procmgr.search_placeholder", "Filtrar processos...")
        q = self._filter_var.get().strip().lower()
        if q == placeholder.lower():
            q = ""
        if q:
            data = [
                p
                for p in self._all_data
                if q in p["name"].lower()
                or q in str(p["pid"])
                or q in p.get("user", "").lower()
            ]
        else:
            data = self._all_data
        self._render(data)

    def _render(self, data: list[dict]) -> None:
        if not _widget_alive(self) or not _widget_alive(self._tree):
            return
        num_cols = {"pid", "cpu", "mem", "threads"}

        def _sort_key(p: dict) -> Any:
            val = p.get(self._sort_col, 0)
            if self._sort_col in num_cols:
                return float(val) if val else 0.0
            return str(val).lower()

        data = sorted(data, key=_sort_key, reverse=self._sort_rev)

        sel = self._tree.selection() if _widget_alive(self._tree) else ()
        sel_pid: int | None = None
        if sel:
            with contextlib.suppress(IndexError, ValueError):
                sel_pid = int(self._tree.item(sel[0], "values")[0])

        if not _widget_alive(self._tree):
            return
        new_sel = None
        new_pids = {p["pid"] for p in data}
        existing = {
            int(self._tree.item(iid, "values")[0]): iid
            for iid in self._tree.get_children()
            if self._tree.exists(iid)
        }

        pids_to_remove = set(existing.keys()) - new_pids
        if pids_to_remove:
            self._tree.delete(*[existing[pid] for pid in pids_to_remove])

        for idx, p in enumerate(data):
            pid = p["pid"]
            status_str = _status_label(p.get("status", ""))
            values = (
                pid,
                p["name"],
                f"{p['cpu']:.1f}",
                p["mem_str"],
                p["threads"],
                status_str,
                p.get("user", ""),
            )
            if pid in existing:
                iid = existing[pid]
                self._tree.item(iid, values=values, tags=(str(pid),))
                self._tree.move(iid, "", idx)
            else:
                iid = self._tree.insert(
                    "", idx, iid=str(pid), values=values, tags=(str(pid),)
                )
            if pid == sel_pid:
                new_sel = iid

        if new_sel and _widget_alive(self._tree):
            self._tree.selection_set(new_sel)
            self._tree.see(new_sel)

        if not _widget_alive(self):
            return
        sys_info = self._collector.get_sys_info()
        self._status_var.set(
            _t("procmgr.processes_total", f"Total de processos: {len(data)}").format(
                n=len(data)
            )
            + f"  |  CPU: {sys_info.get('cpu_pct', 0):.1f}%"
            + f"  |  RAM: {sys_info.get('ram_pct', 0):.1f}%"
        )

    def _selected_pid(self) -> int | None:
        sel = self._tree.selection()
        if not sel:
            return None
        try:
            return int(self._tree.item(sel[0], "values")[0])
        except (IndexError, ValueError):
            return None

    def _selected_name(self) -> str:
        sel = self._tree.selection()
        if not sel:
            return ""
        try:
            return str(self._tree.item(sel[0], "values")[1])
        except IndexError:
            return ""

    def _kill_selected(self) -> None:
        if not _PSUTIL:
            messagebox.showwarning(
                "psutil", _t("procmgr.no_psutil", "psutil não instalado.")
            )
            return
        pid = self._selected_pid()
        if pid is None:
            messagebox.showinfo(
                "", _t("procmgr.no_selection",
                       "Selecione um processo primeiro.")
            )
            return
        name = self._selected_name()
        if not messagebox.askyesno(
            _t("procmgr.kill_confirm_title", "Encerrar processo?"),
            _t(
                "procmgr.kill_confirm_msg", f"Encerrar o processo {name} (PID {pid})?"
            ).format(name=name, PID=pid),
        ):
            return
        try:
            psutil.Process(pid).terminate()
            messagebox.showinfo(
                "",
                _t("procmgr.kill_success", f"Processo {pid} encerrado.").format(
                    pid=pid
                ),
            )
        except Exception as exc:
            messagebox.showerror(
                "",
                _t("procmgr.kill_failed", f"Falha ao encerrar processo: {exc}").format(
                    err=exc
                ),
            )

    def _kill_tree(self) -> None:
        if not _PSUTIL:
            messagebox.showwarning(
                "psutil", _t("procmgr.no_psutil", "psutil não instalado.")
            )
            return
        pid = self._selected_pid()
        if pid is None:
            messagebox.showinfo(
                "", _t("procmgr.no_selection",
                       "Selecione um processo primeiro.")
            )
            return
        name = self._selected_name()
        if not messagebox.askyesno(
            _t("procmgr.kill_confirm_title", "Encerrar processo?"),
            _t(
                "procmgr.kill_confirm_msg", f"Encerrar o processo {name} (PID {pid})?"
            ).format(name=name, PID=pid),
        ):
            return
        try:
            proc = psutil.Process(pid)
            children = proc.children(recursive=True)
            for ch in children:
                with contextlib.suppress(psutil.NoSuchProcess):
                    ch.terminate()
            proc.terminate()
        except Exception as exc:
            messagebox.showerror(
                "",
                _t("procmgr.kill_failed", f"Falha ao encerrar processo: {exc}").format(
                    err=exc
                ),
            )

    def _show_details(self) -> None:
        if not _PSUTIL:
            return
        pid = self._selected_pid()
        if pid is None:
            return
        try:
            proc = psutil.Process(pid)
            info = proc.as_dict(
                attrs=[
                    "pid",
                    "name",
                    "status",
                    "username",
                    "cpu_percent",
                    "memory_info",
                    "num_threads",
                    "cmdline",
                    "create_time",
                    "exe",
                    "cwd",
                    "open_files",
                ]
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
            messagebox.showwarning("", str(exc))
            return

        t = self._tmgr.theme
        dlg = tk.Toplevel(self)
        dlg.title(_t("procmgr.details_title", "Detalhes do processo"))
        dlg.geometry("620x440")
        dlg.config(bg=t.panel_bg)
        safe_grab_set(dlg)

        txt = tk.Text(
            dlg,
            bg=t.editor_bg,
            fg=t.fg,
            font=(FONT_MONO, 9),
            wrap="word",
            relief="flat",
            padx=10,
            pady=10,
        )
        vsb = ttk.Scrollbar(dlg, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        txt.pack(fill="both", expand=True)

        mem = info.get("memory_info")
        lines = [
            f"PID:         {info['pid']}",
            f"Nome:        {info['name']}",
            f"Status:      {info.get('status', '?')}",
            f"Usuário:     {info.get('username', '?')}",
            f"CPU:         {info.get('cpu_percent', 0):.2f}%",
            f"RAM (RSS):   {_fmt_bytes(mem.rss) if mem else '?'}",
            f"RAM (VMS):   {_fmt_bytes(mem.vms) if mem else '?'}",
            f"Threads:     {info.get('num_threads', '?')}",
            f"Executável:  {info.get('exe', '?')}",
            f"Dir atual:   {info.get('cwd', '?')}",
            f"Linha cmd:   {' '.join(info.get('cmdline') or [])}",
        ]
        files = info.get("open_files") or []
        if files:
            lines.append(f"\nArquivos abertos ({len(files)}):")
            for f in files[:30]:
                lines.append(f"  {f.path}")
            if len(files) > 30:
                lines.append(f"  ... (+{len(files) - 30})")

        txt.insert("1.0", "\n".join(lines))
        txt.config(state="disabled")

        tk.Button(
            dlg,
            text=_t("ui.close", "Fechar"),
            command=dlg.destroy,
            bg=t.accent,
            fg="#FFF",
            relief="flat",
            font=(FONT_UI, 9),
        ).pack(pady=8)


class _CodisTab(tk.Frame):
    """Aba que exibe componentes internos do Codis com controle de serviços."""

    _SERVICES: list[tuple[str, str, str]] = [
        ("lsp", "🔌 Conexão LSP", "Servidor de linguagem (autocompletar, erros)"),
        ("chat", "💬 Chat", "Chat multi-protocolo (ChatPanel)"),
        ("ia", "🤖 Motor de IA", "Painel de IA / chat com modelos"),
        ("terminal", "⊟ Terminal", "Todas as abas do terminal integrado"),
        ("git", "🌿 Git Sidebar", "Painel de controle de versão"),
        ("build", "🔨 Build Runner", "Processos de build em segundo plano"),
        ("collab", "🤝 Colaboração", "Conexão de colaboração em tempo real"),
        ("kui", "✨ K-UI (animação)", "Engine de animação cósmica (K-UI)"),
    ]

    def __init__(
        self, parent: tk.Widget, tmgr: Any, app_ref: Any | None = None
    ) -> None:
        """Inicializa a aba de métricas específicas do Codis."""
        t = tmgr.theme
        super().__init__(parent, bg=t.panel_bg)
        self._tmgr = tmgr
        self._app = app_ref
        self._svc_status: dict[str, str] = (
            {}
        )

        self._build(t)
        self._refresh()

    def _build(self, t: Any) -> None:
        hdr = tk.Frame(self, bg=t.panel_bg)
        hdr.pack(fill="x", padx=8, pady=(8, 4))
        tk.Label(
            hdr,
            text=_t("procmgr.codis_panels", "Serviços internos do Codis"),
            bg=t.panel_bg,
            fg=t.fg,
            font=(FONT_UI, 10, "bold"),
        ).pack(side="left")
        btn = tk.Label(
            hdr,
            text=_t("procmgr.btn_refresh", "⟳ Atualizar"),
            bg=t.sidebar_bg,
            fg=t.fg,
            font=(FONT_UI, 8),
            cursor="hand2",
            padx=7,
            pady=4,
        )
        btn.pack(side="right")
        btn.bind("<Button-1>", lambda _: self._refresh())

        svc_frame = tk.Frame(self, bg=t.panel_bg)
        svc_frame.pack(fill="both", expand=True, padx=6, pady=(0, 2))

        cols = ("desc", "status")
        self._svc_tree = ttk.Treeview(
            svc_frame,
            columns=cols,
            show="tree headings",
            selectmode="browse",
            height=len(self._SERVICES),
        )
        self._svc_tree.heading("#0", text=_t("procmgr.col_service", "Serviço"))
        self._svc_tree.heading("desc", text=_t(
            "procmgr.col_desc", "Descrição"))
        self._svc_tree.heading("status", text=_t(
            "procmgr.col_status", "Status"))
        self._svc_tree.column("#0", width=170, minwidth=120)
        self._svc_tree.column("desc", width=210, minwidth=100, stretch=True)
        self._svc_tree.column("status", width=90, minwidth=70, anchor="center")
        vsb_s = ttk.Scrollbar(
            svc_frame, orient="vertical", command=self._svc_tree.yview
        )
        self._svc_tree.configure(yscrollcommand=vsb_s.set)
        vsb_s.pack(side="right", fill="y")
        self._svc_tree.pack(fill="both", expand=True)

        act_bar = tk.Frame(self, bg=t.panel_bg)
        act_bar.pack(fill="x", padx=6, pady=(0, 4))

        def _act_btn(text: str, cmd: Any, danger: bool = False) -> tk.Label:
            bg0 = t.err_fg if danger else t.sidebar_bg
            lbl = tk.Label(
                act_bar,
                text=text,
                bg=bg0,
                fg="#fff" if danger else t.fg,
                font=(FONT_UI, 8),
                cursor="hand2",
                padx=8,
                pady=4,
                relief="flat",
            )
            lbl.pack(side="left", padx=(0, 4))
            lbl.bind("<Button-1>", lambda _: cmd())
            lbl.bind("<Enter>", lambda _: lbl.config(bg=t.btn_hover))
            lbl.bind("<Leave>", lambda _: lbl.config(bg=bg0))
            return lbl

        _act_btn("⛔ Parar serviço", self._stop_selected_service, danger=True)
        _act_btn("🔄 Reiniciar serviço", self._restart_selected_service)
        _act_btn("📋 Detalhes", self._show_service_details)

        tk.Frame(self, bg=self._tmgr.theme.border, height=1).pack(
            fill="x", padx=6, pady=(2, 4)
        )

        wins_hdr = tk.Frame(self, bg=t.panel_bg)
        wins_hdr.pack(fill="x", padx=8, pady=(0, 2))
        tk.Label(
            wins_hdr,
            text=_t("procmgr.open_windows", "Janelas abertas"),
            bg=t.panel_bg,
            fg=t.fg,
            font=(FONT_UI, 9, "bold"),
        ).pack(side="left")

        wins_frame = tk.Frame(self, bg=t.panel_bg)
        wins_frame.pack(fill="both", expand=True, padx=6, pady=(0, 2))

        self._wins_tree = ttk.Treeview(
            wins_frame,
            columns=("type", "status"),
            show="tree headings",
            selectmode="browse",
            height=5,
        )
        self._wins_tree.heading("#0", text=_t("procmgr.col_panel", "Janela"))
        self._wins_tree.heading("type", text=_t("procmgr.col_type", "Tipo"))
        self._wins_tree.heading("status", text=_t(
            "procmgr.col_status", "Status"))
        self._wins_tree.column("#0", width=200, minwidth=100)
        self._wins_tree.column("type", width=100, minwidth=60)
        self._wins_tree.column(
            "status", width=80, minwidth=60, anchor="center")
        vsb_w = ttk.Scrollbar(
            wins_frame, orient="vertical", command=self._wins_tree.yview
        )
        self._wins_tree.configure(yscrollcommand=vsb_w.set)
        vsb_w.pack(side="right", fill="y")
        self._wins_tree.pack(fill="both", expand=True)

        tk.Frame(self, bg=self._tmgr.theme.border, height=1).pack(
            fill="x", padx=6, pady=(2, 4)
        )

        tf = tk.Frame(self, bg=t.panel_bg)
        tf.pack(fill="both", expand=True, padx=6, pady=(0, 4))

        tk.Label(
            tf,
            text=_t("procmgr.codis_threads", "Threads internas"),
            bg=t.panel_bg,
            fg=t.fg,
            font=(FONT_UI, 9, "bold"),
        ).pack(anchor="w")

        self._thread_lb = tk.Listbox(
            tf,
            bg=t.editor_bg,
            fg=t.fg,
            font=(FONT_MONO, 8),
            relief="flat",
            height=5,
            selectmode="browse",
        )
        vsb2 = ttk.Scrollbar(tf, orient="vertical",
                             command=self._thread_lb.yview)
        self._thread_lb.configure(yscrollcommand=vsb2.set)
        vsb2.pack(side="right", fill="y")
        self._thread_lb.pack(fill="both", expand=True)

        self._info_var = tk.StringVar()
        tk.Label(
            self,
            textvariable=self._info_var,
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            anchor="w",
        ).pack(fill="x", padx=8, pady=(0, 4))

    def _refresh(self) -> None:
        if not _widget_alive(self):
            return
        self._refresh_services()
        self._refresh_windows()
        self._refresh_threads()
        self._refresh_info()

    def _probe_service_status(self, key: str) -> str:
        """Verifica se um serviço interno está ativo, retorna string legível."""
        app = self._app
        if app is None:
            return "Desconhecido"
        try:
            if key == "lsp":
                mgr = getattr(app, "_lsp_mgr", None)
                if mgr is None:
                    return "Inativo"
                clients = getattr(mgr, "_clients", {})
                active = [
                    lang
                    for lang, c in clients.items()
                    if getattr(c, "_proc", None) and c._proc.poll() is None
                ]
                return f"Ativo ({', '.join(active)})" if active else "Inativo"

            elif key == "chat":
                win = getattr(app, "_chat_win", None)
                if win is None:
                    return "Não iniciado"
                try:
                    return "Ativo" if win.winfo_exists() else "Fechado"
                except Exception:
                    return "Fechado"

            elif key == "ia":
                ia_attr = getattr(app, "_ia_panel", None) or getattr(
                    app, "_painel_ia", None
                )
                if ia_attr is not None:
                    try:
                        return "Ativo" if ia_attr.winfo_exists() else "Inativo"
                    except Exception:
                        return "Inativo"
                return "Não iniciado"

            elif key == "terminal":
                term = getattr(app, "_terminal", None)
                if term is None:
                    return "Inativo"
                tabs = getattr(term, "_tabs", []) or getattr(
                    term, "_sessions", [])
                n = len(tabs)
                return (
                    f"Ativo ({n} sessão{'ões' if n != 1 else ''})"
                    if n
                    else "Sem sessões"
                )

            elif key == "git":
                git = getattr(app, "_git_sidebar", None)
                if git is None:
                    return "Inativo"
                try:
                    return "Ativo" if git.winfo_exists() else "Inativo"
                except Exception:
                    return "Inativo"

            elif key == "build":
                runners = getattr(app, "_build_runner", None) or getattr(
                    app, "_code_runners", []
                )
                if not runners:
                    return "Inativo"
                return "Ativo"

            elif key == "collab":
                collab = getattr(app, "_collab_panel", None)
                if collab is None:
                    return "Inativo"
                try:
                    return "Ativo" if collab.winfo_exists() else "Inativo"
                except Exception:
                    return "Inativo"

            elif key == "kui":
                kui = getattr(app, "_kui", None)
                if kui is None:
                    return "Inativo"
                dead = getattr(kui, "_dead", True)
                return "Inativo" if dead else "Ativo"

        except Exception:
            logger.debug("Falha ao checar status do K-UI.", exc_info=True)
        return "Desconhecido"

    def _refresh_services(self) -> None:
        if not _widget_alive(self._svc_tree):
            return
        existing_iids = set(self._svc_tree.get_children())
        for key, name, desc in self._SERVICES:
            status = self._probe_service_status(key)
            self._svc_status[key] = status
            tag = "active" if "Ativo" in status else "inactive"
            if key in existing_iids:
                self._svc_tree.item(key, text=name, values=(
                    desc, status), tags=(tag,))
            else:
                self._svc_tree.insert(
                    "", "end", iid=key, text=name, values=(desc, status), tags=(tag,)
                )
        t = self._tmgr.theme
        self._svc_tree.tag_configure("active", foreground=t.accent)
        self._svc_tree.tag_configure("inactive", foreground=t.fg_muted)

    def _refresh_windows(self) -> None:
        if not _widget_alive(self._wins_tree):
            return
        self._wins_tree.delete(*self._wins_tree.get_children())
        try:
            import tkinter as tk_mod

            root = tk_mod._default_root
            if root and root.winfo_exists():
                for w in root.winfo_children():
                    if isinstance(w, tk_mod.Toplevel) and w.winfo_exists():
                        title = w.title() or "(sem título)"
                        self._wins_tree.insert(
                            "", "end", text=title, values=("Toplevel", "Aberta")
                        )
                for w in root.winfo_children():
                    if not isinstance(w, tk_mod.Toplevel) and w.winfo_exists():
                        cls = type(w).__name__
                        self._wins_tree.insert(
                            "", "end", text=cls, values=("Widget", "Ativo")
                        )
        except Exception:
            logger.debug(
                "Falha ao listar janelas/widgets Tk ativos.", exc_info=True)
        if _PSUTIL:
            try:
                me = psutil.Process(os.getpid())
                for ch in me.children(recursive=False):
                    with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
                        self._wins_tree.insert(
                            "",
                            "end",
                            text=ch.name(),
                            values=("Subprocesso", _status_label(ch.status())),
                        )
            except Exception:
                logger.debug(
                    "Falha ao listar subprocessos filhos.", exc_info=True)

    def _refresh_threads(self) -> None:
        if not _widget_alive(self._thread_lb):
            return
        import threading as thr

        self._thread_lb.delete(0, "end")
        for t in sorted(thr.enumerate(), key=lambda x: x.name):
            daemon_flag = " [daemon]" if t.daemon else ""
            alive_flag = " ✓" if t.is_alive() else " ✗"
            self._thread_lb.insert("end", f"{t.name}{daemon_flag}{alive_flag}")

    def _refresh_info(self) -> None:
        if not _widget_alive(self):
            return
        if not _PSUTIL:
            self._info_var.set(
                _t("procmgr.no_psutil", "psutil não instalado."))
            return
        try:
            me = psutil.Process(os.getpid())
            mem = me.memory_info()
            cpu = me.cpu_percent(interval=None)
            self._info_var.set(
                f"Codis PID: {me.pid}  |  CPU: {cpu:.1f}%  |  "
                f"RAM: {_fmt_bytes(mem.rss)}  |  Threads: {me.num_threads()}"
            )
        except Exception:
            self._info_var.set("")

    def _selected_service_key(self) -> str | None:
        sel = self._svc_tree.selection()
        return sel[0] if sel else None

    def _stop_selected_service(self) -> None:
        key = self._selected_service_key()
        if not key:
            messagebox.showinfo("", "Selecione um serviço primeiro.")
            return
        name = next((n for k, n, _ in self._SERVICES if k == key), key)
        if not messagebox.askyesno(
            "Parar serviço",
            f"Deseja realmente parar o serviço:\n\n{name}\n\n"
            "Isso pode afetar funcionalidades do Codis. Continuar?",
        ):
            return
        try:
            self._do_stop_service(key)
            messagebox.showinfo("", f"Serviço '{name}' encerrado com sucesso.")
        except Exception as exc:
            messagebox.showerror(
                "Erro", f"Não foi possível parar o serviço:\n{exc}")
        self._refresh_services()

    def _restart_selected_service(self) -> None:
        key = self._selected_service_key()
        if not key:
            messagebox.showinfo("", "Selecione um serviço primeiro.")
            return
        name = next((n for k, n, _ in self._SERVICES if k == key), key)
        try:
            self._do_stop_service(key)
            self._do_start_service(key)
            messagebox.showinfo("", f"Serviço '{name}' reiniciado.")
        except Exception as exc:
            messagebox.showerror("Erro", f"Erro ao reiniciar serviço:\n{exc}")
        self._refresh_services()

    def _do_stop_service(self, key: str) -> None:
        """Para o serviço interno identificado por key."""
        app = self._app
        if app is None:
            raise RuntimeError("Referência ao app não disponível.")

        if key == "lsp":
            mgr = getattr(app, "_lsp_mgr", None)
            if mgr:
                mgr.stop_all()

        elif key == "chat":
            win = getattr(app, "_chat_win", None)
            if win:
                with contextlib.suppress(Exception):
                    panel = getattr(app, "_chat_panel", None)
                    if panel is not None:
                        panel.shutdown()
                try:
                    win.destroy()
                except Exception:
                    logger.debug(
                        "Falha ao destruir janela de chat ao encerrar serviço.",
                        exc_info=True,
                    )
                app._chat_win = None

        elif key == "ia":
            ia_attr = getattr(app, "_ia_panel", None) or getattr(
                app, "_painel_ia", None
            )
            if ia_attr:
                with contextlib.suppress(Exception):
                    (
                        ia_attr._cancel_stream()
                        if hasattr(ia_attr, "_cancel_stream")
                        else None
                    )
                with contextlib.suppress(Exception):
                    ia_attr.pack_forget()
                with contextlib.suppress(Exception):
                    ia_attr.destroy()

        elif key == "terminal":
            term = getattr(app, "_terminal", None)
            if term:
                with contextlib.suppress(Exception):
                    term.kill_all()

        elif key == "git":
            git = getattr(app, "_git_sidebar", None)
            if git:
                with contextlib.suppress(Exception):
                    for attr in ("_fetch_job", "_diff_job", "_log_job"):
                        job = getattr(git, attr, None)
                        if job:
                            try:
                                git.after_cancel(job)
                            except Exception:
                                logger.debug(
                                    "Falha ao cancelar job git pendente.", exc_info=True
                                )

        elif key == "build":
            runner = getattr(app, "_build_runner", None)
            if runner:
                with contextlib.suppress(Exception):
                    runner.stop()

        elif key == "collab":
            collab = getattr(app, "_collab_panel", None)
            if collab:
                with contextlib.suppress(Exception):
                    collab.disconnect() if hasattr(collab, "disconnect") else None
                with contextlib.suppress(Exception):
                    collab.destroy()

        elif key == "kui":
            kui = getattr(app, "_kui", None)
            if kui:
                with contextlib.suppress(Exception):
                    kui.stop()

        else:
            raise RuntimeError(
                f"Serviço '{key}' não tem método de parada conhecido.")

    def _do_start_service(self, key: str) -> None:
        """Reinicia o serviço identificado por key (best-effort)."""
        app = self._app
        if app is None:
            return

        if key == "lsp":
            mgr = getattr(app, "_lsp_mgr", None)
            if mgr:
                with contextlib.suppress(Exception):
                    editors = list(getattr(app, "_editors", []))
                    for editor in editors:
                        lang = getattr(editor, "_lang_id", None)
                        if lang:
                            mgr.client_for(lang)

        elif key == "kui":
            kui = getattr(app, "_kui", None)
            if kui:
                with contextlib.suppress(Exception):
                    kui._dead = False
                    kui._tick()

    def _show_service_details(self) -> None:
        key = self._selected_service_key()
        if not key:
            messagebox.showinfo("", "Selecione um serviço primeiro.")
            return
        name = next((n for k, n, _ in self._SERVICES if k == key), key)
        desc = next((d for k, _, d in self._SERVICES if k == key), "")
        status = self._svc_status.get(key, "?")

        t = self._tmgr.theme
        dlg = tk.Toplevel(self)
        dlg.title(f"Detalhes — {name}")
        dlg.geometry("480x320")
        dlg.config(bg=t.panel_bg)
        safe_grab_set(dlg)

        txt = tk.Text(
            dlg,
            bg=t.editor_bg,
            fg=t.fg,
            font=(FONT_MONO, 9),
            wrap="word",
            relief="flat",
            padx=12,
            pady=10,
        )
        vsb = ttk.Scrollbar(dlg, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        txt.pack(fill="both", expand=True)

        app = self._app
        lines = [
            f"Serviço:    {name}",
            f"Chave:      {key}",
            f"Descrição:  {desc}",
            f"Status:     {status}",
            "",
        ]

        if app and key == "lsp":
            mgr = getattr(app, "_lsp_mgr", None)
            if mgr:
                clients = getattr(mgr, "_clients", {})
                lines.append(f"Clientes LSP ativos: {len(clients)}")
                for lang, c in clients.items():
                    proc = getattr(c, "_proc", None)
                    pid = proc.pid if proc else "N/A"
                    poll = proc.poll() if proc else "N/A"
                    alive = "rodando" if poll is None else f"encerrado ({poll})"
                    lines.append(f"  {lang}: PID={pid}  [{alive}]")
        elif app and key == "terminal":
            term = getattr(app, "_terminal", None)
            if term:
                tabs = getattr(term, "_tabs", []) or getattr(
                    term, "_sessions", [])
                lines.append(f"Sessões abertas: {len(tabs)}")
        elif app and key == "kui":
            kui = getattr(app, "_kui", None)
            if kui:
                cfg = getattr(kui, "_cfg", {})
                lines.append(f"Perfil: {cfg.get('fps', '?')} fps")
                lines.append(f"Estrelas: {cfg.get('stars', '?')}")
                lines.append(f"Partículas: {cfg.get('n_particles', '?')}")
                lines.append(f"Motor parado: {getattr(kui, '_dead', '?')}")

        txt.insert("1.0", "\n".join(lines))
        txt.config(state="disabled")

        tk.Button(
            dlg,
            text="Fechar",
            command=dlg.destroy,
            bg=t.accent,
            fg="#FFF",
            relief="flat",
            font=(FONT_UI, 9),
        ).pack(pady=8)


class _SysInfoBar(tk.Frame):
    """Barra com gráficos de CPU/RAM e info do sistema."""

    def __init__(self, parent: tk.Widget, tmgr: Any, collector: _DataCollector) -> None:
        """Inicializa a barra de informações estáticas do sistema."""
        t = tmgr.theme
        super().__init__(parent, bg=t.sidebar_bg, width=170)
        self.pack_propagate(False)
        self._tmgr = tmgr
        self._collector = collector

        self._build(t)
        collector.add_callback(self._schedule_refresh)

    def _build(self, t: Any) -> None:
        tk.Label(
            self,
            text=_t("procmgr.system_info", "Info do Sistema"),
            bg=t.sidebar_bg,
            fg=t.fg,
            font=(FONT_UI, 9, "bold"),
        ).pack(anchor="w", padx=8, pady=(8, 4))

        self._cpu_graph = _MiniGraph(
            self,
            label=_t("procmgr.graph_cpu", "CPU %"),
            color="#89b4fa",
            bg=t.sidebar_bg,
            fg=t.fg,
        )
        self._cpu_graph.pack(fill="x", padx=6, pady=2)

        self._mem_graph = _MiniGraph(
            self,
            label=_t("procmgr.graph_mem", "Memória %"),
            color="#a6e3a1",
            bg=t.sidebar_bg,
            fg=t.fg,
        )
        self._mem_graph.pack(fill="x", padx=6, pady=2)

        tk.Frame(self, bg=t.border, height=1).pack(fill="x", padx=6, pady=6)

        self._info_labels: list[tk.Label] = []
        for _ in range(6):
            lbl = tk.Label(
                self,
                text="",
                bg=t.sidebar_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 7),
                anchor="w",
                wraplength=155,
            )
            lbl.pack(fill="x", padx=8, pady=1)
            self._info_labels.append(lbl)

    def _schedule_refresh(self) -> None:
        if not _widget_alive(self):
            return
        with contextlib.suppress(Exception):
            self.after(0, self._refresh)

    def _refresh(self) -> None:
        if not _widget_alive(self):
            return
        if not _widget_alive(self._cpu_graph):
            return
        if not _widget_alive(self._mem_graph):
            return
        self._cpu_graph.update_values(self._collector.get_cpu_history())
        self._mem_graph.update_values(self._collector.get_mem_history())

        si = self._collector.get_sys_info()
        texts = [
            _t("procmgr.cpu_cores", "Núcleos CPU: {n}").format(
                n=si.get("cpu_count", "?")
            ),
            _t("procmgr.ram_total", "RAM Total: {v}").format(
                v=si.get("ram_total", "?")
            ),
            _t("procmgr.ram_used", "RAM Usada: {v} ({pct}%)").format(
                v=si.get("ram_used", "?"), pct=f"{si.get('ram_pct', 0):.1f}"
            ),
            _t("procmgr.processes_total", "Total de processos: {n}").format(
                n=si.get("proc_count", "?")
            ),
            f"SO: {si.get('platform', '?')}",
            f"Python: {si.get('python', '?')}",
        ]
        for lbl, txt in zip(self._info_labels, texts, strict=False):
            if _widget_alive(lbl):
                lbl.config(text=txt)


class ProcessManagerPanel(tk.Frame):
    """Janela completa do Gerenciador de Processos.

    Layout:
      +------------------------------------------+----------------+
      │  Notebook: [Sistema] [Codis]              │  Gráficos +    │
      │  (Treeview de processos)                  │  Info sistema  │
      +------------------------------------------+----------------+
      │  Toolbar: auto-refresh [1s/2s/5s] | status total          │
      +----------------------------------------------------------+
    """

    def __init__(
        self, parent: tk.Widget, tmgr: Any, cfg: Any, app_ref: Any | None = None
    ) -> None:
        """Inicializa o painel de gerenciamento de processos."""
        t = tmgr.theme
        super().__init__(parent, bg=t.panel_bg)
        self._tmgr = tmgr
        self._cfg = cfg
        self._app = app_ref

        self._collector = _DataCollector()
        self._auto_interval = 2.0

        self._build(t)
        self._collector.start()

        self._schedule_codis_refresh()

    def _build(self, t: Any) -> None:
        hdr = tk.Frame(self, bg=t.activity_bg, height=48)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text="P", bg=t.activity_bg, fg=t.accent, font=(FONT_UI, 16)).pack(
            side="left", padx=(14, 4), pady=8
        )
        tk.Label(
            hdr,
            text=_t("procmgr.title", "Gerenciador de Processos"),
            bg=t.activity_bg,
            fg=t.fg,
            font=(FONT_UI, 13, "bold"),
        ).pack(side="left", pady=8)

        ar_frame = tk.Frame(hdr, bg=t.activity_bg)
        ar_frame.pack(side="right", padx=12)
        tk.Label(
            ar_frame,
            text=_t("procmgr.btn_auto_refresh", "Auto-atualizar:"),
            bg=t.activity_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 7),
        ).pack(side="left")
        for label, secs in (("1s", 1.0), ("2s", 2.0), ("5s", 5.0)):
            btn = tk.Label(
                ar_frame,
                text=label,
                bg=t.sidebar_bg,
                fg=t.fg,
                font=(FONT_UI, 7),
                cursor="hand2",
                padx=5,
                pady=2,
                relief="flat",
            )
            btn.pack(side="left", padx=1)
            btn.bind("<Button-1>", lambda _, s=secs,
                     b=btn: self._set_interval(s, b))

        tk.Frame(self, bg=t.border, height=1).pack(fill="x")

        body = tk.Frame(self, bg=t.panel_bg)
        body.pack(fill="both", expand=True)

        self._sysbar = _SysInfoBar(body, self._tmgr, self._collector)
        self._sysbar.pack(side="right", fill="y")

        tk.Frame(body, bg=t.border, width=1).pack(side="right", fill="y")

        nb_frame = tk.Frame(body, bg=t.panel_bg)
        nb_frame.pack(side="left", fill="both", expand=True)

        self._nb = ttk.Notebook(nb_frame)
        self._nb.pack(fill="both", expand=True)

        self._sys_tab = _SystemTab(self._nb, self._tmgr, self._collector)
        self._nb.add(
            self._sys_tab, text=_t("procmgr.tab_system",
                                   "  Processos do Sistema  ")
        )

        self._codis_tab = _CodisTab(self._nb, self._tmgr, self._app)
        self._nb.add(
            self._codis_tab, text=_t(
                "procmgr.tab_codis", "  Processos do Codis  ")
        )

        if not _PSUTIL:
            warn = tk.Frame(self, bg="#f38ba8")
            warn.pack(fill="x")
            tk.Label(
                warn,
                text=_t(
                    "procmgr.no_psutil",
                    "⚠  psutil não instalado. Execute: pip install psutil",
                ),
                bg="#f38ba8",
                fg="#1e1e2e",
                font=(FONT_UI, 8),
                anchor="w",
            ).pack(fill="x", padx=8, pady=4)

    def _set_interval(self, secs: float, _btn: tk.Label) -> None:
        self._auto_interval = secs
        self._collector.set_interval(secs)

    def _schedule_codis_refresh(self) -> None:
        """Atualiza a aba Codis periodicamente."""
        if not _widget_alive(self):
            return
        with contextlib.suppress(Exception):
            self._codis_tab._refresh()
        if _widget_alive(self):
            with contextlib.suppress(Exception):
                self.after(
                    int(self._auto_interval * 1000), self._schedule_codis_refresh
                )

    def destroy(self) -> None:
        """Encerra o coletor de dados e destrói o painel."""
        self._collector.stop()
        super().destroy()
