"""Codis - Dependency Graph Visualizer.

Analisa imports de arquivos Python no workspace e renderiza um grafo
interativo de dependências no canvas do Tkinter - sem dependências externas.

Funcionalidades:
  - Parse de ``import X`` e ``from X import Y`` via ast ou regex (fallback)
  - Grafo com nós coloridos por tipo (stdlib / local / third-party)
  - Layout força-dirigida (Force-directed) implementado nativamente
  - Zoom e pan com mouse
  - Clique em nó para abrir o arquivo
  - Filtro por módulo
  - Exportação como DOT (Graphviz)
  - Ciclos de dependência destacados em vermelho
"""

from __future__ import annotations

import ast
import math
import random
import re
import sys
import threading
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox

from src.constants import FONT_UI
from src.i18n import _t

_STDLIB: frozenset[str] = (
    frozenset(sys.stdlib_module_names)
    if hasattr(sys, "stdlib_module_names")
    else frozenset(
        {
            "os",
            "sys",
            "re",
            "io",
            "math",
            "json",
            "time",
            "threading",
            "pathlib",
            "collections",
            "itertools",
            "functools",
            "typing",
            "abc",
            "enum",
            "copy",
            "hashlib",
            "hmac",
            "base64",
            "urllib",
            "http",
            "email",
            "html",
            "xml",
            "csv",
            "sqlite3",
            "logging",
            "unittest",
            "dataclasses",
            "contextlib",
            "subprocess",
            "shutil",
            "tempfile",
            "glob",
            "fnmatch",
            "stat",
            "struct",
            "socket",
            "ssl",
            "asyncio",
            "concurrent",
            "multiprocessing",
            "queue",
            "pickle",
            "shelve",
            "zipfile",
            "tarfile",
            "gzip",
            "argparse",
            "getopt",
            "tkinter",
            "datetime",
            "calendar",
            "locale",
            "gettext",
            "platform",
            "warnings",
            "traceback",
            "inspect",
            "importlib",
            "builtins",
        }
    )
)


def _classify(module: str) -> str:
    top = module.split(".")[0]
    if top in _STDLIB:
        return "stdlib"
    return "local"


def _parse_imports(filepath: Path) -> list[str]:
    """Retorna lista de módulos importados pelo arquivo."""
    try:
        source = filepath.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(filepath))
        deps: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    deps.append(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                deps.append(node.module)
        return deps
    except Exception:
        try:
            text = filepath.read_text(encoding="utf-8", errors="replace")
            deps = re.findall(
                r"^(?:import|from)\s+([\w.]+)", text, re.MULTILINE)
            return deps
        except Exception:
            return []


def _scan_workspace(root: Path) -> dict[str, list[str]]:
    """Escaneia todos .py no workspace e retorna {módulo_relativo: [deps]}."""
    graph: dict[str, list[str]] = {}
    py_files = list(root.rglob("*.py"))
    for f in py_files:
        try:
            rel = f.relative_to(root)
        except ValueError:
            rel = f
        module_name = str(rel).replace(
            "/", ".").replace("\\", ".").removesuffix(".py")
        graph[module_name] = _parse_imports(f)
    return graph


def _find_cycles(graph: dict[str, list[str]]) -> set[tuple[str, str]]:
    """Retorna conjunto de arestas (a, b) que fazem parte de ciclos."""
    visited: set[str] = set()
    rec_stack: set[str] = set()
    cycle_edges: set[tuple[str, str]] = set()

    def dfs(node: str, parent: str | None) -> None:
        visited.add(node)
        rec_stack.add(node)
        for dep in graph.get(node, []):
            dep_top = dep.split(".")[0]
            if dep_top not in graph:
                continue
            if dep_top not in visited:
                dfs(dep_top, node)
            elif dep_top in rec_stack and parent:
                cycle_edges.add((node, dep_top))

    for node in list(graph):
        if node not in visited:
            dfs(node, None)

    return cycle_edges


class _Node:
    __slots__ = ("name", "kind", "x", "y", "vx", "vy", "fx", "fy", "pinned")

    def __init__(self, name: str, kind: str) -> None:
        self.name = name
        self.kind = kind
        self.x = random.uniform(-200, 200)
        self.y = random.uniform(-200, 200)
        self.vx = 0.0
        self.vy = 0.0
        self.fx = 0.0
        self.fy = 0.0
        self.pinned = False


def _layout(
    nodes: dict[str, _Node], edges: list[tuple[str, str]], steps: int = 200
) -> None:
    """Simulação de força dirigida estável (sem NaN/overflow).

    Usa clamping de força e velocidade para garantir convergência mesmo
    com nós partindo da mesma posição ou com grafos densos.
    """
    if not nodes:
        return

    k = 80.0
    gravity = 0.02
    damping = 0.55
    max_disp = 15.0
    max_rep = 600.0

    for _ in range(steps):
        forces: dict[str, list[float]] = {n: [0.0, 0.0] for n in nodes}

        for name, n in nodes.items():
            forces[name][0] -= n.x * gravity
            forces[name][1] -= n.y * gravity

        node_list = list(nodes.items())
        for i, (na, a) in enumerate(node_list):
            for nb, b in node_list[i + 1:]:
                dx = a.x - b.x
                dy = a.y - b.y
                d2 = dx * dx + dy * dy
                if d2 < 1e-6:
                    dx = random.uniform(-1.0, 1.0)
                    dy = random.uniform(-1.0, 1.0)
                    d2 = dx * dx + dy * dy or 1e-6
                dist = math.sqrt(d2)
                rep = min(k * k / dist, max_rep)
                fx = (dx / dist) * rep
                fy = (dy / dist) * rep
                forces[na][0] += fx
                forces[na][1] += fy
                forces[nb][0] -= fx
                forces[nb][1] -= fy

        for src, dst in edges:
            a = nodes.get(src)
            b = nodes.get(dst)
            if not (a and b):
                continue
            dx = b.x - a.x
            dy = b.y - a.y
            dist = math.sqrt(dx * dx + dy * dy) or 0.01
            att = (dist / k) * min(dist, k * 2.0)
            fx = (dx / dist) * att
            fy = (dy / dist) * att
            forces[src][0] += fx
            forces[src][1] += fy
            forces[dst][0] -= fx
            forces[dst][1] -= fy

        for name, n in nodes.items():
            if n.pinned:
                continue
            n.vx = (n.vx + forces[name][0]) * damping
            n.vy = (n.vy + forces[name][1]) * damping
            speed = math.sqrt(n.vx * n.vx + n.vy * n.vy)
            if speed > max_disp:
                n.vx = n.vx / speed * max_disp
                n.vy = n.vy / speed * max_disp
            n.x += n.vx
            n.y += n.vy

_KIND_COLORS = {
    "local": ("#3b8fd4", "#cfe8ff"),
    "stdlib": ("#27ae60", "#cff0dc"),
    "third": ("#e67e22", "#fde8cc"),
    "cycle": ("#e74c3c", "#fdd"),
}


class DependencyGraphPanel(tk.Frame):
    """Painel de grafo de dependências de imports Python."""

    def __init__(
        self,
        parent: tk.Widget,
        tmgr,
        workspace: Path | None = None,
        on_open_file: Callable[[Path], None] | None = None,
    ) -> None:
        super().__init__(parent, bg=tmgr.theme.sidebar_bg)
        self._tmgr = tmgr
        self._workspace = workspace
        self._on_open = on_open_file
        self._nodes: dict[str, _Node] = {}
        self._edges: list[tuple[str, str]] = []
        self._cycle_edges: set[tuple[str, str]] = set()
        self._scale = 1.0
        self._offset_x = 0.0
        self._offset_y = 0.0
        self._drag_start: tuple[int, int] | None = None
        self._hover_node: str | None = None
        self._filter_var = tk.StringVar()
        self._show_stdlib = tk.BooleanVar(value=False)
        self._loading = False
        self._build()

    def _build(self) -> None:
        t = self._tmgr.theme

        progress_bar = tk.Frame(self, bg=t.sidebar_bg)
        progress_bar.pack(fill="x", padx=6, pady=(6, 2))

        tk.Label(
            progress_bar,
            text=_t("depgraph.title", "🔗 Grafo de Dependências"),
            bg=t.sidebar_bg,
            fg=t.accent,
            font=(FONT_UI, 10, "bold"),
        ).pack(side="left")

        btn_scan = tk.Label(
            progress_bar,
            text=_t("depgraph.scan", "↺ Escanear"),
            bg=t.btn_bg,
            fg=t.fg,
            cursor="hand2",
            font=(FONT_UI, 9),
            padx=8,
            pady=2,
        )
        btn_scan.pack(side="right", padx=2)
        btn_scan.bind("<Button-1>", lambda _: self._start_scan())
        btn_scan.bind("<Enter>", lambda _,
                      w=btn_scan: w.config(bg=t.btn_hover))
        btn_scan.bind("<Leave>", lambda _, w=btn_scan: w.config(bg=t.btn_bg))

        btn_dot = tk.Label(
            progress_bar,
            text=_t("depgraph.export_dot", "📤 DOT"),
            bg=t.btn_bg,
            fg=t.fg,
            cursor="hand2",
            font=(FONT_UI, 9),
            padx=8,
            pady=2,
        )
        btn_dot.pack(side="right", padx=2)
        btn_dot.bind("<Button-1>", lambda _: self._export_dot())
        btn_dot.bind("<Enter>", lambda _, w=btn_dot: w.config(bg=t.btn_hover))
        btn_dot.bind("<Leave>", lambda _, w=btn_dot: w.config(bg=t.btn_bg))

        fbar = tk.Frame(self, bg=t.sidebar_bg)
        fbar.pack(fill="x", padx=6, pady=2)
        tk.Label(
            fbar, text="🔍", bg=t.sidebar_bg, fg=t.fg_muted, font=(FONT_UI, 9)
        ).pack(side="left")
        flt = tk.Entry(
            fbar,
            textvariable=self._filter_var,
            bg=t.input_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            font=(FONT_UI, 9),
            highlightthickness=1,
            highlightbackground=t.border,
        )
        flt.pack(side="left", fill="x", expand=True, padx=4)
        self._filter_var.trace_add("write", lambda *_: self._redraw())

        cb = tk.Checkbutton(
            fbar,
            text=_t("depgraph.show_stdlib", "stdlib"),
            variable=self._show_stdlib,
            command=self._redraw,
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            selectcolor=t.editor_bg,
            activebackground=t.sidebar_bg,
            font=(FONT_UI, 8),
            bd=0,
        )
        cb.pack(side="right")

        leg = tk.Frame(self, bg=t.sidebar_bg)
        leg.pack(fill="x", padx=6, pady=2)
        for label, color in [
            (_t("depgraph.local", "local"), _KIND_COLORS["local"][0]),
            ("stdlib", _KIND_COLORS["stdlib"][0]),
            (_t("depgraph.cycle", "ciclo"), _KIND_COLORS["cycle"][0]),
        ]:
            tk.Label(leg, text="●", fg=color, bg=t.sidebar_bg, font=(FONT_UI, 9)).pack(
                side="left"
            )
            tk.Label(
                leg,
                text=label + "  ",
                fg=t.fg_muted,
                bg=t.sidebar_bg,
                font=(FONT_UI, 8),
            ).pack(side="left")

        self._canvas = tk.Canvas(self, bg=t.editor_bg, highlightthickness=0)
        self._canvas.pack(fill="both", expand=True, padx=6, pady=(2, 6))

        self._status_lbl = tk.Label(
            self,
            text=_t("depgraph.hint", "Clique em ↺ para escanear o workspace"),
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        )
        self._status_lbl.pack(pady=2)

        c = self._canvas
        c.bind("<ButtonPress-2>", self._on_pan_start)
        c.bind("<B2-Motion>", self._on_pan)
        c.bind("<ButtonPress-3>", self._on_pan_start)
        c.bind("<B3-Motion>", self._on_pan)
        c.bind("<MouseWheel>", self._on_zoom)
        c.bind("<Button-4>", lambda e: self._zoom_by(1.1, e))
        c.bind("<Button-5>", lambda e: self._zoom_by(0.9, e))
        c.bind("<Motion>", self._on_motion)
        c.bind("<Button-1>", self._on_click)
        c.bind("<Configure>", lambda _: self._redraw())

    def set_workspace(self, path: Path) -> None:
        self._workspace = path

    def _start_scan(self) -> None:
        if self._loading:
            return
        if not self._workspace:
            messagebox.showinfo(
                _t("depgraph.no_ws_title", "Workspace"),
                _t("depgraph.no_ws", "Nenhum workspace aberto."),
            )
            return
        self._loading = True
        self._status_lbl.config(text=_t("depgraph.scanning", "Escaneando..."))
        threading.Thread(target=self._scan_thread, daemon=True).start()

    def _scan_thread(self) -> None:
        try:
            raw = _scan_workspace(self._workspace)
            nodes: dict[str, _Node] = {}
            edges: list[tuple[str, str]] = []

            for mod, deps in raw.items():
                nodes[mod] = _Node(mod, "local")
                for dep in deps:
                    top = dep.split(".")[0]
                    kind = _classify(top)
                    if top not in nodes:
                        nodes[top] = _Node(top, kind)
                    edges.append((mod, top))

            cycle_edges = _find_cycles(
                {m: [d.split(".")[0] for d in deps] for m, deps in raw.items()}
            )
            _layout(nodes, edges, steps=120)

            self._nodes = nodes
            self._edges = edges
            self._cycle_edges = cycle_edges

            if nodes:
                xs = [n.x for n in nodes.values()]
                ys = [n.y for n in nodes.values()]
                cx = (max(xs) + min(xs)) / 2
                cy = (max(ys) + min(ys)) / 2
                for n in nodes.values():
                    n.x -= cx
                    n.y -= cy
                xs2 = [n.x for n in nodes.values()]
                ys2 = [n.y for n in nodes.values()]
                max_r = max(
                    max(abs(x) for x in xs2),
                    max(abs(y) for y in ys2),
                    1.0,
                )
                canvas_w, canvas_h = 900.0, 680.0
                margin = 120.0
                auto_scale = min(
                    (canvas_w / 2 - margin) / max_r,
                    (canvas_h / 2 - margin) / max_r,
                    4.0,
                )
            else:
                auto_scale = 1.0

            n_cycle = len({e[0] for e in cycle_edges} | {e[1]
                          for e in cycle_edges})
            self.after(0, self._scan_done, len(nodes),
                       len(edges), n_cycle, auto_scale)
        except Exception as exc:
            err_msg = str(exc)
            self.after(0, lambda m=err_msg: self._status_lbl.config(
                text=f"Erro: {m}"))
            self._loading = False

    def _scan_done(
        self, n_nodes: int, n_edges: int, n_cycle: int, auto_scale: float = 1.0
    ) -> None:
        self._loading = False
        msg = _t(
            "depgraph.result",
            "{n_nodes} módulos - {n_edges} arestas - {n_cycle} em ciclos",
            n_nodes=n_nodes,
            n_edges=n_edges,
            n_cycle=n_cycle,
        )
        self._status_lbl.config(text=msg)
        self._offset_x = 0.0
        self._offset_y = 0.0
        self._scale = max(0.1, min(auto_scale, 4.0))
        self._canvas.update_idletasks()
        self.after(80, self._redraw)

    def _world_to_canvas(self, x: float, y: float) -> tuple[float, float]:
        cw = self._canvas.winfo_width()
        ch = self._canvas.winfo_height()
        W = max(cw if cw > 10 else 900, 10) / 2
        H = max(ch if ch > 10 else 680, 10) / 2
        return (
            x * self._scale + W + self._offset_x,
            y * self._scale + H + self._offset_y,
        )

    def _canvas_to_world(self, cx: float, cy: float) -> tuple[float, float]:
        cw = self._canvas.winfo_width()
        ch = self._canvas.winfo_height()
        W = max(cw if cw > 10 else 900, 10) / 2
        H = max(ch if ch > 10 else 680, 10) / 2
        return (
            (cx - W - self._offset_x) / self._scale,
            (cy - H - self._offset_y) / self._scale,
        )

    def _redraw(self) -> None:
        c = self._canvas
        if c.winfo_width() < 10:
            self.after(100, self._redraw)
            return
        c.delete("all")
        t = self._tmgr.theme

        flt = self._filter_var.get().lower()
        show_stdlib = self._show_stdlib.get()

        visible: set[str] = set()
        for name, node in self._nodes.items():
            if not show_stdlib and node.kind == "stdlib":
                continue
            if flt and flt not in name.lower():
                continue
            visible.add(name)

        for src, dst in self._edges:
            if src not in visible or dst not in visible:
                continue
            a = self._nodes[src]
            b = self._nodes[dst]
            x1, y1 = self._world_to_canvas(a.x, a.y)
            x2, y2 = self._world_to_canvas(b.x, b.y)
            is_cycle = (src, dst) in self._cycle_edges or (
                dst,
                src,
            ) in self._cycle_edges
            color = _KIND_COLORS["cycle"][0] if is_cycle else t.border
            width = 2 if is_cycle else 1
            c.create_line(
                x1,
                y1,
                x2,
                y2,
                fill=color,
                width=width,
                arrow="last",
                arrowshape=(8, 10, 3),
            )

        r = max(5, 10 * self._scale)
        for name in visible:
            node = self._nodes[name]
            x, y = self._world_to_canvas(node.x, node.y)
            is_cycle_node = any(
                (name in (s, d)) and (s in visible and d in visible)
                for s, d in self._cycle_edges
            )
            if is_cycle_node:
                fill, outline = _KIND_COLORS["cycle"]
            else:
                fill, outline = _KIND_COLORS.get(
                    node.kind, _KIND_COLORS["local"])

            c.create_oval(
                x - r,
                y - r,
                x + r,
                y + r,
                fill=fill,
                outline=outline,
                width=1,
                tags=("node", name),
            )
            label = name.split(".")[-1]
            font_size = max(6, int(8 * self._scale))
            c.create_text(
                x,
                y + r + 2,
                text=label,
                fill=t.fg,
                font=(FONT_UI, font_size),
                anchor="n",
                tags=("node_label", name),
            )

    def _on_pan_start(self, e: tk.Event) -> None:
        self._drag_start = (e.x, e.y)

    def _on_pan(self, e: tk.Event) -> None:
        if self._drag_start:
            dx = e.x - self._drag_start[0]
            dy = e.y - self._drag_start[1]
            self._offset_x += dx
            self._offset_y += dy
            self._drag_start = (e.x, e.y)
            self._redraw()

    def _on_zoom(self, e: tk.Event) -> None:
        factor = 1.1 if e.delta > 0 else 0.9
        self._zoom_by(factor, e)

    def _zoom_by(self, factor: float, e: tk.Event) -> None:
        wx, wy = self._canvas_to_world(e.x, e.y)
        self._scale = max(0.1, min(5.0, self._scale * factor))
        cx, cy = self._world_to_canvas(wx, wy)
        self._offset_x += e.x - cx
        self._offset_y += e.y - cy
        self._redraw()

    def _on_motion(self, e: tk.Event) -> None:
        hit = self._canvas.find_closest(e.x, e.y)
        if hit:
            tags = self._canvas.gettags(hit[0])
            name = next(
                (t for t in tags if t not in ("node", "node_label", "current")), None
            )
            if name != self._hover_node:
                self._hover_node = name
                self._canvas.config(cursor="hand2" if name else "")

    def _on_click(self, e: tk.Event) -> None:
        hit = self._canvas.find_closest(e.x, e.y)
        if not hit:
            return
        tags = self._canvas.gettags(hit[0])
        name = next(
            (t for t in tags if t not in ("node", "node_label", "current")), None
        )
        if name and self._on_open and self._workspace:
            parts = name.replace(".", "/")
            for ext in (".py", "/__init__.py"):
                candidate = self._workspace / (parts + ext)
                if candidate.exists():
                    self._on_open(candidate)
                    break

    def _export_dot(self) -> None:
        if not self._nodes:
            messagebox.showinfo(
                _t("depgraph.no_data_title", "Sem dados"),
                _t("depgraph.no_data", "Escaneie o workspace primeiro."),
            )
            return
        path = filedialog.asksaveasfilename(
            title=_t("depgraph.export_dot_title", "Exportar como DOT"),
            defaultextension=".dot",
            filetypes=[("DOT/Graphviz", "*.dot"), ("Todos", "*.*")],
        )
        if not path:
            return
        lines = ["digraph dependencies {",
                 "  rankdir=LR;", "  node [shape=box];"]
        for src, dst in self._edges:
            lines.append(f'  "{src}" -> "{dst}";')
        for name, node in self._nodes.items():
            color = _KIND_COLORS.get(node.kind, _KIND_COLORS["local"])[0]
            lines.append(f'  "{name}" [color="{color}"];')
        lines.append("}")
        try:
            Path(path).write_text("\n".join(lines), encoding="utf-8")
            messagebox.showinfo(
                _t("tracker.export_ok_title", "Exportado"),
                _t("depgraph.exported", "Exportado com sucesso."),
            )
        except Exception as exc:
            messagebox.showerror(_t("err.title", "Erro"), str(exc))

    def apply_theme(self, tmgr) -> None:
        self._tmgr = tmgr
        self._redraw()
