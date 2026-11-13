"""Codis - Flowchart Generator panel.

Parses a Python file with the `ast` module and renders an interactive
flowchart on a tkinter Canvas.  No external dependencies required.

Nodes:
  ▭  Rectangle   - function def / class def / assign / expr
  ◇  Diamond     - if / elif / else branches
  ⬡  Hexagon     - for / while loops (rendered as parallelogram)
  ○  Oval        - function entry / return / raise / exit
"""

from __future__ import annotations

import ast
import contextlib
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Any

from src.config import ConfigManager
from src.constants import FONT_UI
from src.i18n import _t
from src.theme.manager import ThemeManager

_BOX_W = 160
_BOX_H = 36
_DIAMOND_W = 170
_DIAMOND_H = 48
_OVAL_W = 120
_OVAL_H = 32
_H_GAP = 60
_V_GAP = 54
_INDENT = 90

_COLORS = {
    "func": ("#3b8fd4", "#cfe8ff"),
    "class": ("#9b59b6", "#e8d5f0"),
    "if": ("#e67e22", "#fde8cc"),
    "loop": ("#27ae60", "#cff0dc"),
    "return": ("#e74c3c", "#fad7d7"),
    "assign": ("#555", "#e8e8e8"),
    "expr": ("#666", "#f0f0f0"),
    "start": ("#1abc9c", "#d5f5ef"),
    "end": ("#e74c3c", "#ffd7d7"),
}
_DARK_COLORS = {
    "func": ("#5aafff", "#1a3a52"),
    "class": ("#c084fc", "#2d1a3a"),
    "if": ("#fb923c", "#3a2010"),
    "loop": ("#4ade80", "#0f2e18"),
    "return": ("#f87171", "#2d1010"),
    "assign": ("#888", "#282828"),
    "expr": ("#777", "#242424"),
    "start": ("#2dd4bf", "#0d2e2a"),
    "end": ("#f87171", "#2d1010"),
}


def _is_dark(bg_hex: str) -> bool:
    """Executa 'is dark' e retorna o resultado.

    Args:
        bg_hex: Parâmetro da operação.
    """
    bg_hex = bg_hex.lstrip("#")
    r, g, b = int(bg_hex[0:2], 16), int(bg_hex[2:4], 16), int(bg_hex[4:6], 16)
    return (0.299 * r + 0.587 * g + 0.114 * b) < 128


class FlowNode:
    """Class docstring."""

    __slots__ = (
        "kind",
        "label",
        "lineno",
        "children_true",
        "children_false",
        "children_body",
        "x",
        "y",
        "w",
        "h",
        "id",
    )
    _counter = 0

    def __init__(self, kind: str, label: str, lineno: int = 0) -> None:
        """Inicializa um nó do flowchart."""
        FlowNode._counter += 1
        self.id = FlowNode._counter
        self.kind = kind
        self.label = label
        self.lineno = lineno
        self.children_true: list[FlowNode] = []
        self.children_false: list[FlowNode] = []
        self.children_body: list[FlowNode] = []
        self.x = self.y = 0
        self.w = _BOX_W
        self.h = _BOX_H


def _truncate(s: str, n: int = 26) -> str:
    return s if len(s) <= n else s[: n - 1] + "..."


def _node_label(node: ast.AST) -> tuple[str, str]:
    """Return (kind, label) for an AST node."""
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        return "func", f"def {node.name}(...)"
    if isinstance(node, ast.ClassDef):
        return "class", f"class {node.name}"
    if isinstance(node, ast.If):
        try:
            cond = ast.unparse(node.test)
        except (ValueError, AttributeError):
            cond = "condition"
        return "if", _truncate(f"if {cond}")
    if isinstance(node, ast.For | ast.AsyncFor):
        try:
            target = ast.unparse(node.target)
            iter_ = ast.unparse(node.iter)
            label = f"for {target} in {iter_}"
        except (ValueError, AttributeError):
            label = "for"
        return "loop", _truncate(label)
    if isinstance(node, ast.While):
        try:
            cond = ast.unparse(node.test)
        except (ValueError, AttributeError):
            cond = "condition"
        return "loop", _truncate(f"while {cond}")
    if isinstance(node, ast.Return):
        try:
            val = ast.unparse(node.value) if node.value else ""
            label = f"return {val}" if val else "return"
        except (ValueError, AttributeError):
            label = "return"
        return "return", _truncate(label)
    if isinstance(node, ast.Raise):
        return "return", "raise"
    if isinstance(node, ast.Assign | ast.AugAssign | ast.AnnAssign):
        try:
            label = ast.unparse(node)
        except (ValueError, AttributeError):
            label = "assign"
        return "assign", _truncate(label)
    if isinstance(node, ast.Expr):
        try:
            label = ast.unparse(node)
        except (ValueError, AttributeError):
            label = "expr"
        return "expr", _truncate(label)
    return "expr", type(node).__name__


def _build_nodes(
    stmts: list[ast.stmt], depth: int = 0, max_depth: int = 4
) -> list[FlowNode]:
    """Recursively convert AST statements to FlowNode list."""
    nodes: list[FlowNode] = []
    if depth > max_depth:
        return nodes
    for stmt in stmts:
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            kind, label = _node_label(stmt)
            n = FlowNode(kind, label, getattr(stmt, "lineno", 0))
            body_stmts = stmt.body if hasattr(stmt, "body") else []
            entry = FlowNode("start", "Entry")
            exit_ = FlowNode("end", "Return")
            n.children_body = (
                [entry] + _build_nodes(body_stmts, depth +
                                       1, max_depth) + [exit_]
            )
            nodes.append(n)
        elif isinstance(stmt, ast.If):
            kind, label = _node_label(stmt)
            n = FlowNode(kind, label, getattr(stmt, "lineno", 0))
            n.children_true = _build_nodes(stmt.body, depth + 1, max_depth)
            n.children_false = _build_nodes(stmt.orelse, depth + 1, max_depth)
            nodes.append(n)
        elif isinstance(stmt, ast.For | ast.AsyncFor | ast.While):
            kind, label = _node_label(stmt)
            n = FlowNode(kind, label, getattr(stmt, "lineno", 0))
            n.children_body = _build_nodes(stmt.body, depth + 1, max_depth)
            nodes.append(n)
        elif isinstance(stmt, ast.Return | ast.Raise):
            kind, label = _node_label(stmt)
            nodes.append(FlowNode(kind, label, getattr(stmt, "lineno", 0)))
            break
        else:
            kind, label = _node_label(stmt)
            nodes.append(FlowNode(kind, label, getattr(stmt, "lineno", 0)))
    return nodes


class _Layout:
    """Assigns (x, y) coordinates to nodes using a simple top-down layout."""

    def __init__(self, nodes: list[FlowNode], origin_x: int, origin_y: int) -> None:
        """Inicializa o motor de layout automático do flowchart."""

    def layout(self, nodes: list[FlowNode], x0: int, y0: int) -> int:
        """Return bottom-most y after laying out nodes."""
        y = y0
        for n in nodes:
            if n.kind == "if":
                n.x, n.y = x0, y
                n.w, n.h = _DIAMOND_W, _DIAMOND_H
                y += _DIAMOND_H + _V_GAP // 2

                y_true = self.layout(
                    n.children_true, x0 - _H_GAP - _BOX_W // 2, y)

                y_false = self.layout(
                    n.children_false, x0 + _H_GAP + _BOX_W // 2, y)
                y = max(y_true, y_false) + _V_GAP
            elif n.kind in ("loop", "func", "class") and n.children_body:
                n.x, n.y = x0, y
                n.w = _BOX_W
                n.h = _BOX_H
                y += _BOX_H + _V_GAP // 2
                y = self.layout(n.children_body, x0 + _INDENT // 2, y)
                y += _V_GAP // 2
            elif n.kind in ("start", "end", "return"):
                n.x, n.y = x0, y
                n.w, n.h = _OVAL_W, _OVAL_H
                y += _OVAL_H + _V_GAP
            else:
                n.x, n.y = x0, y
                n.w, n.h = _BOX_W, _BOX_H
                y += _BOX_H + _V_GAP
        return y


class _FlowCanvas(tk.Canvas):
    """Zoomable, scrollable canvas that renders FlowNode trees."""

    def __init__(self, parent: tk.Widget, **kw: Any) -> None:
        """Inicializa o canvas de renderização do flowchart."""
        super().__init__(parent, **kw)
        self._scale = 1.0
        self._nodes: list[FlowNode] = []
        self._dark = False
        self._on_click: Callable[[int], None] | None = None
        self._pan_x = self._pan_y = 0
        self._drag_start: tuple[int, int] | None = None

        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<MouseWheel>", self._on_wheel)
        self.bind("<Button-4>", self._on_wheel)
        self.bind("<Button-5>", self._on_wheel)

    def set_nodes(self, nodes: list[FlowNode], dark: bool = False) -> None:
        """Define 'nodes'.

        Args:
            nodes: Parâmetro da operação.
            dark: True para paleta escura.
        """
        self._nodes = nodes
        self._dark = dark
        self.render()

    def render(self) -> None:
        """Renderiza e exibe o conteúdo."""
        self.delete("all")
        pal = _DARK_COLORS if self._dark else _COLORS
        s = self._scale
        px, py = self._pan_x, self._pan_y

        def sx(v: float) -> float:
            return v * s + px

        def sy(v: float) -> float:
            return v * s + py

        for n in self._flat(self._nodes):
            x, y, w, h = n.x, n.y, n.w, n.h
            border, fill = pal.get(n.kind, pal["expr"])
            fg = "#ffffff" if self._dark else "#111111"

            if n.kind == "if":
                cx, cy = sx(x + w / 2), sy(y + h / 2)
                pts = [cx, sy(y), sx(x + w), cy, cx, sy(y + h), sx(x), cy]
                self.create_polygon(
                    *pts, fill=fill, outline=border, width=2, tags=("node", f"n{n.id}")
                )
            elif n.kind in ("start", "end", "return"):
                self.create_oval(
                    sx(x),
                    sy(y),
                    sx(x + w),
                    sy(y + h),
                    fill=fill,
                    outline=border,
                    width=2,
                    tags=("node", f"n{n.id}"),
                )
            elif n.kind in ("loop",):
                off = h * s * 0.3
                pts = [
                    sx(x) + off,
                    sy(y),
                    sx(x + w),
                    sy(y),
                    sx(x + w) - off,
                    sy(y + h),
                    sx(x),
                    sy(y + h),
                ]
                self.create_polygon(
                    *pts, fill=fill, outline=border, width=2, tags=("node", f"n{n.id}")
                )
            else:
                r = 6 * s
                self.create_rectangle(
                    sx(x) + r,
                    sy(y),
                    sx(x + w) - r,
                    sy(y + h),
                    fill=fill,
                    outline="",
                    tags=f"n{n.id}",
                )
                self.create_rectangle(
                    sx(x),
                    sy(y) + r,
                    sx(x + w),
                    sy(y + h) - r,
                    fill=fill,
                    outline="",
                    tags=f"n{n.id}",
                )
                self.create_rectangle(
                    sx(x),
                    sy(y),
                    sx(x + w),
                    sy(y + h),
                    fill="",
                    outline=border,
                    width=2,
                    tags=("node", f"n{n.id}"),
                )

            self.create_text(
                sx(x + w / 2),
                sy(y + h / 2),
                text=n.label,
                fill=fg,
                font=(FONT_UI, max(7, int(9 * s))),
                tags=f"n{n.id}",
            )

            self._draw_arrows(n, s, px, py, border)

        bbox = self.bbox("all")
        if bbox:
            pad = 40
            self.config(
                scrollregion=(
                    bbox[0] - pad,
                    bbox[1] - pad,
                    bbox[2] + pad,
                    bbox[3] + pad,
                )
            )

    def _draw_arrows(
        self, n: FlowNode, s: float, px: float, py: float, color: str
    ) -> None:
        """Executa 'draw arrows' e retorna o resultado.

        Args:
            n: Parâmetro da operação.
            s: Parâmetro da operação.
            px: Parâmetro da operação.
            py: Parâmetro da operação.
            color: Parâmetro da operação.
        """

        def sx(v: float) -> float:
            return v * s + px

        def sy(v: float) -> float:
            return v * s + py

        def arrow(x1: float, y1: float, x2: float, y2: float, label: str = "") -> None:
            self.create_line(
                sx(x1),
                sy(y1),
                sx(x2),
                sy(y2),
                fill=color,
                width=max(1, int(1.5 * s)),
                arrow="last",
                arrowshape=(8 * s, 10 * s, 3 * s),
            )
            if label:
                mx, my = (x1 + x2) / 2, (y1 + y2) / 2
                self.create_text(
                    sx(mx + 6),
                    sy(my),
                    text=label,
                    fill=color,
                    font=(FONT_UI, max(6, int(7 * s))),
                    anchor="w",
                )

        bx, by, bw, bh = n.x, n.y, n.w, n.h

        if n.kind == "if":
            if n.children_true:
                c = n.children_true[0]
                arrow(bx, by + bh / 2, c.x + c.w / 2, c.y, "Yes")

            if n.children_false:
                c = n.children_false[0]
                arrow(bx + bw, by + bh / 2, c.x + c.w / 2, c.y, "No")
        elif n.children_body:
            c = n.children_body[0]
            arrow(bx + bw / 2, by + bh, c.x + c.w / 2, c.y)
            for a, b in zip(n.children_body, n.children_body[1:], strict=False):
                arrow(a.x + a.w / 2, a.y + a.h, b.x + b.w / 2, b.y)

    def _flat(self, nodes: list[FlowNode]) -> list[FlowNode]:
        out: list[FlowNode] = []
        for n in nodes:
            out.append(n)
            out.extend(self._flat(n.children_true))
            out.extend(self._flat(n.children_false))
            out.extend(self._flat(n.children_body))
        return out

    def zoom(self, factor: float) -> None:
        """Aplica zoom no canvas do flowchart."""
        self._scale = max(0.2, min(4.0, self._scale * factor))
        self.render()

    def fit(self) -> None:
        """Fit all nodes to canvas."""
        nodes = self._flat(self._nodes)
        if not nodes:
            return
        min_x = min(n.x for n in nodes)
        min_y = min(n.y for n in nodes)
        max_x = max(n.x + n.w for n in nodes)
        max_y = max(n.y + n.h for n in nodes)
        cw = self.winfo_width() or 400
        ch = self.winfo_height() or 400
        pad = 40
        content_w = max_x - min_x + pad * 2
        content_h = max_y - min_y + pad * 2
        sx = cw / content_w if content_w > 0 else 1
        sy = ch / content_h if content_h > 0 else 1
        self._scale = max(0.2, min(2.0, sx, sy))
        self._pan_x = pad - min_x * self._scale
        self._pan_y = pad - min_y * self._scale
        self.render()

    def _on_press(self, e: Any) -> None:
        """Trata o evento 'press'.

        Args:
            e: Evento Tkinter.
        """
        self._drag_start = (e.x, e.y)

    def _on_drag(self, e: Any) -> None:
        """Trata o evento 'drag'.

        Args:
            e: Evento Tkinter.
        """
        if self._drag_start:
            dx = e.x - self._drag_start[0]
            dy = e.y - self._drag_start[1]
            self._pan_x += dx
            self._pan_y += dy
            self._drag_start = (e.x, e.y)
            self.render()

    def _on_wheel(self, e: Any) -> None:
        """Trata o evento 'wheel'.

        Args:
            e: Evento Tkinter.
        """
        factor = 1.1
        if e.num == 5 or (hasattr(e, "delta") and e.delta < 0):
            factor = 1 / factor
        self.zoom(factor)

    def export_svg(self, path: str) -> None:
        """Very basic SVG export."""
        nodes = self._flat(self._nodes)
        if not nodes:
            return
        min_x = min(n.x for n in nodes) - 20
        min_y = min(n.y for n in nodes) - 20
        max_x = max(n.x + n.w for n in nodes) + 20
        max_y = max(n.y + n.h for n in nodes) + 20
        W = max_x - min_x
        H = max_y - min_y
        pal = _DARK_COLORS if self._dark else _COLORS

        lines = [
            '<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="{W}" height="{H}" viewBox="{min_x} {min_y} {W} {H}">',
            "<style>text{font-family:sans-serif;font-size:10px;}</style>",
        ]
        for n in nodes:
            border, fill = pal.get(n.kind, pal["expr"])
            cx = n.x + n.w / 2 - min_x
            cy = n.y + n.h / 2 - min_y
            x = n.x - min_x
            y = n.y - min_y
            if n.kind == "if":
                pts = f"{cx},{y} {x + n.w},{cy} {cx},{y + n.h} {x},{cy}"
                lines.append(
                    f'<polygon points="{pts}" fill="{fill}" stroke="{border}" stroke-width="2"/>'
                )
            elif n.kind in ("start", "end", "return"):
                lines.append(
                    f'<ellipse cx="{cx}" cy="{cy}" rx="{n.w / 2}" ry="{n.h / 2}" '
                    f'fill="{fill}" stroke="{border}" stroke-width="2"/>'
                )
            else:
                lines.append(
                    f'<rect x="{x}" y="{y}" width="{n.w}" height="{n.h}" rx="5" '
                    f'fill="{fill}" stroke="{border}" stroke-width="2"/>'
                )
            fg = "#ffffff" if self._dark else "#111"
            lines.append(
                f'<text x="{cx}" y="{cy + 4}" text-anchor="middle" fill="{fg}">{n.label}</text>'
            )
        lines.append("</svg>")
        Path(path).write_text("\n".join(lines), encoding="utf-8")


class FlowchartPanel(tk.Frame):
    """Sidebar panel that generates an interactive flowchart from the active file."""

    def __init__(
        self, parent: tk.Widget, tmgr: ThemeManager, cfg: ConfigManager
    ) -> None:
        """Cria o painel de geração de fluxogramas do código.

        Args:
            parent: Widget pai Tkinter.
            config: Instância de ConfigManager.
        """
        t = tmgr.theme
        super().__init__(parent, bg=t.panel_bg)
        self._tmgr = tmgr
        self._cfg = cfg
        self._current_path: str | None = None
        self._nodes: list[FlowNode] = []
        self._max_depth = tk.IntVar(value=3)
        self._build()

    def _build(self) -> None:
        """Constrói os widgets e layout interno do componente."""
        t = self._tmgr.theme

        for child in self.winfo_children():
            with contextlib.suppress(tk.TclError):
                child.destroy()

        tb = tk.Frame(self, bg=t.panel_bg)
        tb.pack(fill="x", padx=6, pady=(6, 2))

        tk.Label(
            tb,
            text=f"🔀  {_t('flowchart.title')}",
            bg=t.panel_bg,
            fg=t.fg,
            font=(FONT_UI, 10, "bold"),
        ).pack(side="left")

        for txt, cmd in (
            (_t("flowchart.generate"), self._generate),
            (_t("flowchart.export_svg"), self._export_svg),
        ):
            b = tk.Label(
                tb,
                text=txt,
                bg=t.input_bg,
                fg=t.fg,
                font=(FONT_UI, 8),
                cursor="hand2",
                padx=6,
                pady=2,
            )
            b.pack(side="right", padx=2)
            b.bind("<Button-1>", lambda _, c=cmd: c())
            b.bind("<Enter>", lambda _, w=b: w.config(bg=t.sel_bg))
            b.bind("<Leave>", lambda _, w=b: w.config(bg=t.input_bg))

        dep_row = tk.Frame(self, bg=t.panel_bg)
        dep_row.pack(fill="x", padx=8, pady=(0, 4))
        tk.Label(
            dep_row,
            text=_t("flowchart.depth"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        ).pack(side="left")
        tk.Spinbox(
            dep_row,
            from_=1,
            to=8,
            textvariable=self._max_depth,
            font=(FONT_UI, 8),
            width=3,
            bg=t.input_bg,
            fg=t.fg,
            buttonbackground=t.border,
            relief="flat",
        ).pack(side="left", padx=4)

        zoom_row = tk.Frame(self, bg=t.panel_bg)
        zoom_row.pack(fill="x", padx=8, pady=(0, 2))
        for txt, cmd in (
            (
                _t("flowchart.zoom_in"),
                lambda: self._canvas.zoom(1.2),
            ),  # pylint: disable=access-member-before-definition
            (_t("flowchart.zoom_out"), lambda: self._canvas.zoom(1 / 1.2)),
            (
                _t("flowchart.fit"),
                lambda: self._canvas.fit(),
            ),  # pylint: disable=access-member-before-definition,unnecessary-lambda
        ):
            b = tk.Label(
                zoom_row,
                text=txt,
                bg=t.input_bg,
                fg=t.fg,
                font=(FONT_UI, 8),
                cursor="hand2",
                padx=6,
                pady=2,
            )
            b.pack(side="left", padx=2)
            b.bind("<Button-1>", lambda _, c=cmd: c())
            b.bind("<Enter>", lambda _, w=b: w.config(bg=t.sel_bg))
            b.bind("<Leave>", lambda _, w=b: w.config(bg=t.input_bg))

        tk.Frame(self, bg=t.border, height=1).pack(fill="x", padx=4)

        self._status_var = tk.StringVar(value=_t("flowchart.no_file"))
        tk.Label(
            self,
            textvariable=self._status_var,
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            wraplength=220,
        ).pack(pady=4)

        canvas_frame = tk.Frame(self, bg=t.panel_bg)
        canvas_frame.pack(fill="both", expand=True)

        dark = _is_dark(t.panel_bg)
        bg_canvas = "#1a1a2e" if dark else "#f4f4f8"

        self._canvas = _FlowCanvas(
            canvas_frame,
            bg=bg_canvas,
            highlightthickness=0,
        )
        vsb = ttk.Scrollbar(
            canvas_frame,
            orient="vertical",
            command=self._canvas.yview,
            style="Thin.Vertical.TScrollbar",
        )
        hsb = ttk.Scrollbar(
            canvas_frame,
            orient="horizontal",
            command=self._canvas.xview,
            style="Thin.Horizontal.TScrollbar",
        )
        self._canvas.config(xscrollcommand=hsb.set, yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        hsb.pack(side="bottom", fill="x")
        self._canvas.pack(fill="both", expand=True)

    def notify_active_file(self, path: str | None) -> None:
        """Called when the active editor tab changes."""
        self._current_path = path
        auto = self._cfg.get("flowchart_auto", False)
        if auto and path and path.endswith(".py"):
            self._generate()
        elif path and not path.endswith(".py"):
            self._status_var.set(_t("flowchart.unsupported"))
        else:
            self._status_var.set(_t("flowchart.no_file"))

    def notify_file_saved(self, path: str) -> None:
        """Executa a operação 'notify file saved'.

        Args:
            path: Caminho do arquivo ou diretório.
        """
        auto = self._cfg.get("flowchart_auto", False)
        if auto and path.endswith(".py") and path == self._current_path:
            self._generate()

    def _generate(self) -> None:
        """Gera e retorna o conteúdo."""
        p = self._current_path
        if not p:
            self._status_var.set(_t("flowchart.no_file"))
            return
        if not p.endswith(".py"):
            self._status_var.set(_t("flowchart.unsupported"))
            return
        self._status_var.set(_t("flowchart.generating"))
        self.update_idletasks()
        try:
            source = Path(p).read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source, filename=p)
            FlowNode._counter = 0
            nodes = _build_nodes(tree.body, max_depth=self._max_depth.get())
            if not nodes:
                self._status_var.set(_t("flowchart.empty"))
                self._canvas.set_nodes([], _is_dark(self._tmgr.theme.panel_bg))
                return
            layout = _Layout(nodes, 60, 60)
            layout.layout(nodes, 60, 60)
            dark = _is_dark(self._tmgr.theme.panel_bg)
            self._nodes = nodes
            self._canvas.set_nodes(nodes, dark)
            self._status_var.set(f"{Path(p).name}")
            self.after(100, self._canvas.fit)
        except SyntaxError as e:
            self._status_var.set(_t("flowchart.error").format(err=str(e)))
        except (ValueError, OSError) as e:
            self._status_var.set(_t("flowchart.error").format(err=str(e)))

    def _export_svg(self) -> None:
        """Exporta o fluxograma como arquivo SVG."""
        if not self._nodes:
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".svg",
            filetypes=[("SVG files", "*.svg"), ("All files", "*.*")],
            initialfile="flowchart.svg",
        )
        if path:
            self._canvas.export_svg(path)

    def apply_theme(self, tmgr: ThemeManager) -> None:
        """Executa a operação 'apply theme'.

        Args:
            tmgr: Parâmetro da operação.
        """
        self._tmgr = tmgr
        t = tmgr.theme
        self.config(bg=t.panel_bg)
        for w in self.winfo_children():
            w.destroy()
        self._build()
        if self._nodes:
            dark = _is_dark(t.panel_bg)
            self._canvas.set_nodes(self._nodes, dark)
