"""Codis - Calculadora Integrada.

Funcionalidades:
  - Calculadora básica e científica com display elegante
  - Histórico persistente de cálculos
  - Anotações livres por cálculo
  - Conversão de unidades (comprimento, peso, temperatura)
  - Expressões Python completas no campo de entrada
"""

from __future__ import annotations

import ast
import json
import logging
import math
import operator
import tkinter as tk
from tkinter import ttk
from typing import Any

from src.constants import CONFIG_DIR, FONT_MONO, FONT_UI
from src.i18n import _t

logger = logging.getLogger("Codis.Calculator")

_HISTORY_FILE = CONFIG_DIR / "calc_history.json"
_MAX_HISTORY = 200


def _load_history() -> list[dict]:
    if not _HISTORY_FILE.exists():
        return []
    try:
        return json.loads(_HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_history(items: list[dict]) -> None:
    _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    _HISTORY_FILE.write_text(
        json.dumps(items[-_MAX_HISTORY:], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.BitAnd: operator.and_,
    ast.BitOr: operator.or_,
    ast.BitXor: operator.xor,
    ast.LShift: operator.lshift,
    ast.RShift: operator.rshift,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg, ast.Invert: operator.invert}
_MAX_EXPONENT_BITS = 100_000
_MAX_FACTORIAL = 5_000


def _check_size(op: type, left: Any, right: Any) -> None:
    """Recusa operações inteiras cujo resultado levaria tempo ou memória excessivos."""
    if not (isinstance(left, int) and isinstance(right, int)):
        return
    if op is ast.Pow and right > 0:
        if abs(left).bit_length() * right > _MAX_EXPONENT_BITS:
            raise OverflowError("expoente muito grande")
    elif op is ast.LShift and right > _MAX_EXPONENT_BITS:
        raise OverflowError("deslocamento muito grande")


def _safe_eval(expression: str, names: dict[str, Any]) -> Any:
    """Avalia uma expressão aritmética sem executar código arbitrário.

    Só aceita números, operadores aritméticos e bit a bit, nomes presentes em
    ``names`` e chamadas posicionais a funções desse mesmo dicionário.

    Raises:
        ValueError: para qualquer construção não permitida.
        OverflowError: se uma operação inteira for grande demais.
    """

    def walk(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.Name) and node.id in names:
            return names[node.id]
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
            left, right = walk(node.left), walk(node.right)
            _check_size(type(node.op), left, right)
            return _BIN_OPS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
            return _UNARY_OPS[type(node.op)](walk(node.operand))
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and callable(names.get(node.func.id))
            and not node.keywords
        ):
            args = [walk(arg) for arg in node.args]
            if node.func.id == "factorial" and args and args[0] > _MAX_FACTORIAL:
                raise OverflowError("fatorial muito grande")
            if node.func.id == "pow" and len(args) == 2:
                _check_size(ast.Pow, *args)
            return names[node.func.id](*args)
        raise ValueError(f"construção não permitida: {type(node).__name__}")

    return walk(ast.parse(expression, mode="eval"))


def _decimal_commas_to_points(expression: str) -> str:
    """Converte vírgula decimal em ponto, preservando vírgulas de argumentos.

    Só vírgulas fora de parênteses e entre dígitos são convertidas, então
    ``3,5 + 2`` vira ``3.5 + 2`` e ``max(1,2)`` permanece intacto.
    """
    out: list[str] = []
    depth = 0
    for index, char in enumerate(expression):
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        is_decimal = (
            char == ","
            and depth == 0
            and 0 < index < len(expression) - 1
            and expression[index - 1].isdigit()
            and expression[index + 1].isdigit()
        )
        out.append("." if is_decimal else char)
    return "".join(out)


def _btn(
    parent: tk.Widget,
    text: str,
    cmd: Any,
    bg: str,
    fg: str,
    font: tuple,
    active_bg: str = "",
    active_fg: str = "",
) -> tk.Label:
    """Label estilizado como botão com hover."""
    lbl = tk.Label(
        parent, text=text, bg=bg, fg=fg, font=font, cursor="hand2", relief="flat"
    )
    lbl.bind("<Button-1>", lambda _: cmd())
    if active_bg:
        lbl.bind("<Enter>", lambda _: lbl.config(
            bg=active_bg, fg=active_fg or fg))
        lbl.bind("<Leave>", lambda _: lbl.config(bg=bg, fg=fg))
    return lbl


class CalculatorPanel(tk.Frame):
    """Calculadora integrada ao Codis com histórico e anotações."""

    _SAFE_BUILTINS = {
        "abs": abs,
        "round": round,
        "min": min,
        "max": max,
        "sum": sum,
        "pow": pow,
        "int": int,
        "float": float,
        "hex": hex,
        "oct": oct,
        "bin": bin,
        "pi": math.pi,
        "e": math.e,
        "tau": math.tau,
        "sqrt": math.sqrt,
        "cbrt": lambda x: x ** (1 / 3),
        "sin": math.sin,
        "cos": math.cos,
        "tan": math.tan,
        "asin": math.asin,
        "acos": math.acos,
        "atan": math.atan,
        "atan2": math.atan2,
        "sinh": math.sinh,
        "cosh": math.cosh,
        "tanh": math.tanh,
        "log": math.log,
        "log2": math.log2,
        "log10": math.log10,
        "exp": math.exp,
        "ceil": math.ceil,
        "floor": math.floor,
        "factorial": math.factorial,
        "gcd": math.gcd,
        "lcm": getattr(math, "lcm", None),
        "degrees": math.degrees,
        "radians": math.radians,
        "inf": math.inf,
        "nan": math.nan,
    }

    def __init__(self, parent: tk.Widget, tmgr: Any, cfg: Any) -> None:
        """Inicializa o painel de calculadora científica."""
        t = tmgr.theme
        super().__init__(parent, bg=t.panel_bg)
        self._tmgr = tmgr
        self._cfg = cfg
        self._history: list[dict] = _load_history()
        self._memory: float = 0.0
        self._last_result: float | None = None
        self._history_idx = -1
        self._mode = tk.StringVar(value="basic")
        self._build(t)

    def _build(self, t: Any) -> None:
        hdr = tk.Frame(self, bg=t.activity_bg, height=48)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        tk.Label(
            hdr, text="🔢", bg=t.activity_bg, fg=t.accent, font=(FONT_UI, 16)
        ).pack(side="left", padx=(14, 4), pady=8)
        tk.Label(
            hdr,
            text=_t("calc.title", "Calculadora"),
            bg=t.activity_bg,
            fg=t.fg,
            font=(FONT_UI, 13, "bold"),
        ).pack(side="left", pady=8)

        tab_fr = tk.Frame(hdr, bg=t.activity_bg)
        tab_fr.pack(side="right", padx=10)
        self._mode_btns: dict[str, tk.Label] = {}
        for key, val in [
            ("calc.mode_basic", "basic"),
            ("calc.mode_sci", "sci"),
            ("calc.mode_conv", "conv"),
        ]:
            b = tk.Label(
                tab_fr,
                text=_t(key),
                bg=t.activity_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 8),
                cursor="hand2",
                padx=8,
                pady=4,
            )
            b.pack(side="left")
            b.bind("<Button-1>", lambda _, v=val: self._switch_mode(v))
            self._mode_btns[val] = b

        tk.Frame(self, bg=t.border, height=1).pack(fill="x")

        body = tk.Frame(self, bg=t.panel_bg)
        body.pack(fill="both", expand=True)

        self._left = tk.Frame(body, bg=t.panel_bg, width=260)
        self._left.pack(side="left", fill="y")
        self._left.pack_propagate(False)

        tk.Frame(body, bg=t.border, width=1).pack(side="left", fill="y")

        right = tk.Frame(body, bg=t.panel_bg)
        right.pack(side="left", fill="both", expand=True)

        self._build_display(self._left, t)
        self._build_basic_buttons(self._left, t)
        self._build_history(right, t)

        self._sci_frame = tk.Frame(self._left, bg=t.panel_bg)
        self._conv_frame = tk.Frame(self._left, bg=t.panel_bg)
        self._build_sci_buttons(self._sci_frame, t)
        self._build_conv(self._conv_frame, t)

    def _build_display(self, parent: tk.Widget, t: Any) -> None:
        """Display com expressão + resultado + memória."""
        disp = tk.Frame(
            parent, bg=t.editor_bg, highlightthickness=1, highlightbackground=t.border
        )
        disp.pack(fill="x", padx=10, pady=(10, 4))

        top_row = tk.Frame(disp, bg=t.editor_bg)
        top_row.pack(fill="x", padx=8, pady=(6, 0))
        self._memory_lbl = tk.Label(
            top_row,
            text="M: 0",
            bg=t.editor_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 7),
            anchor="w",
        )
        self._memory_lbl.pack(side="left")
        self._mode_badge = tk.Label(
            top_row,
            text="RAD",
            bg=t.accent,
            fg=t.btn_primary_fg,
            font=(FONT_UI, 7),
            padx=4,
            pady=1,
        )
        self._mode_badge.pack(side="right")
        self._mode_badge.pack_forget()

        self._expr_var = tk.StringVar()
        self._expr_entry = tk.Entry(
            disp,
            textvariable=self._expr_var,
            bg=t.editor_bg,
            fg=t.fg_muted,
            font=(FONT_MONO, 12),
            insertbackground=t.cursor,
            relief="flat",
            bd=0,
            justify="right",
        )
        self._expr_entry.pack(fill="x", padx=8, pady=(2, 0))
        self._expr_entry.bind("<Return>", lambda _: self._evaluate())
        self._expr_entry.bind("<Up>", self._history_up)
        self._expr_entry.bind("<Down>", self._history_down)
        self._expr_entry.bind("<Escape>", lambda _: self._btn_action("c"))
        self._expr_entry.focus_set()

        self._result_var = tk.StringVar(value="0")
        tk.Label(
            disp,
            textvariable=self._result_var,
            bg=t.editor_bg,
            fg=t.accent,
            font=(FONT_MONO, 28, "bold"),
            anchor="e",
        ).pack(fill="x", padx=8, pady=(2, 8))

        mem_row = tk.Frame(parent, bg=t.panel_bg)
        mem_row.pack(fill="x", padx=10, pady=(0, 2))
        for lbl, cmd in [
            ("MC", self._mem_clear),
            ("MR", self._mem_recall),
            ("M+", self._mem_add),
            ("M−", self._mem_sub),
            ("MS", self._mem_store),
        ]:
            b = tk.Label(
                mem_row,
                text=lbl,
                bg=t.btn_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 8),
                cursor="hand2",
                padx=6,
                pady=3,
                relief="flat",
            )
            b.pack(side="left", fill="x", expand=True, padx=1)
            b.bind("<Button-1>", lambda _, c=cmd: c())
            b.bind("<Enter>", lambda e, w=b: w.config(bg=t.btn_hover))
            b.bind("<Leave>", lambda e, w=b, bg=t.btn_bg: w.config(bg=bg))

    def _build_basic_buttons(self, parent: tk.Widget, t: Any) -> None:
        """Grade de botões principais 4×5 usando grid."""
        self._btn_frame = tk.Frame(parent, bg=t.panel_bg)
        self._btn_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        layout = [
            ("CE", "ce", "clear"),
            ("C", "c", "clear"),
            ("⌫", "bs", "clear"),
            ("÷", "/", "op"),
            ("7", "7", None),
            ("8", "8", None),
            ("9", "9", None),
            ("×", "*", "op"),
            ("4", "4", None),
            ("5", "5", None),
            ("6", "6", None),
            ("−", "-", "op"),
            ("1", "1", None),
            ("2", "2", None),
            ("3", "3", None),
            ("+", "+", "op"),
            ("±", "neg", None),
            ("0", "0", None),
            (".", ".", None),
            ("=", "=", "eq"),
        ]

        col_count = 4
        for i, (text, action, kind) in enumerate(layout):
            row_i, col_i = divmod(i, col_count)
            if kind == "clear":
                bg = t.border
                fg = t.fg
            elif kind == "op":
                bg = t.btn_hover
                fg = t.t_func
            elif kind == "eq":
                bg = t.accent
                fg = t.btn_primary_fg
            else:
                bg = t.btn_bg
                fg = t.fg

            lbl = tk.Label(
                self._btn_frame,
                text=text,
                bg=bg,
                fg=fg,
                font=(FONT_UI, 14, "bold" if kind == "eq" else "normal"),
                cursor="hand2",
                relief="flat",
                pady=9,
            )
            lbl.grid(row=row_i, column=col_i, sticky="nsew", padx=1, pady=1)
            lbl.bind("<Button-1>", lambda _, a=action: self._btn_action(a))
            hover = t.sel_bg
            lbl.bind("<Enter>", lambda e, w=lbl, h=hover: w.config(bg=h))
            lbl.bind("<Leave>", lambda e, w=lbl, b=bg: w.config(bg=b))

        for c in range(col_count):
            self._btn_frame.columnconfigure(c, weight=1)
        for r in range(5):
            self._btn_frame.rowconfigure(r, weight=1)

    def _build_sci_buttons(self, parent: tk.Widget, t: Any) -> None:
        """Botões científicos extras."""
        sci_fns = [
            ("sin", "sin("),
            ("cos", "cos("),
            ("tan", "tan("),
            ("π", "pi"),
            ("asin", "asin("),
            ("acos", "acos("),
            ("atan", "atan("),
            ("e", "e"),
            ("√", "sqrt("),
            ("x²", "**2"),
            ("x³", "**3"),
            ("^", "**"),
            ("log", "log("),
            ("ln", "log("),
            ("(", "("),
            (")", ")"),
            ("1/x", "1/("),
            ("n!", "factorial("),
            ("ceil", "ceil("),
            ("floor", "floor("),
        ]
        for i, (lbl, snippet) in enumerate(sci_fns):
            r, c = divmod(i, 4)
            b = tk.Label(
                parent,
                text=lbl,
                bg=t.sidebar_bg,
                fg=t.t_func,
                font=(FONT_UI, 9),
                cursor="hand2",
                relief="flat",
                pady=7,
                padx=4,
            )
            b.grid(row=r, column=c, sticky="nsew", padx=1, pady=1)
            b.bind("<Button-1>", lambda _, s=snippet: self._insert_snippet(s))
            b.bind("<Enter>", lambda e, w=b: w.config(bg=t.btn_hover))
            b.bind("<Leave>", lambda e, w=b: w.config(bg=t.sidebar_bg))
        for c in range(4):
            parent.columnconfigure(c, weight=1)

    def _build_conv(self, parent: tk.Widget, t: Any) -> None:
        """Painel de conversão de unidades."""
        tk.Label(
            parent,
            text=_t("calc.conv_title", "Converter"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9, "bold"),
        ).pack(anchor="w", padx=8, pady=(8, 2))

        self._conv_val = tk.StringVar(value="1")
        self._conv_from = tk.StringVar(value="m")
        self._conv_to = tk.StringVar(value="cm")
        self._conv_res = tk.StringVar(value="")

        val_f = tk.Frame(parent, bg=t.panel_bg)
        val_f.pack(fill="x", padx=8, pady=2)
        tk.Label(
            val_f,
            text=_t("calc.conv_value", "Valor"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            width=6,
            anchor="w",
        ).pack(side="left")
        tk.Entry(
            val_f,
            textvariable=self._conv_val,
            bg=t.input_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=t.border,
            font=(FONT_MONO, 10),
            width=14,
        ).pack(side="left", padx=4)

        from_f = tk.Frame(parent, bg=t.panel_bg)
        from_f.pack(fill="x", padx=8, pady=2)
        tk.Label(
            from_f,
            text=_t("calc.conv_from", "De"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            width=6,
            anchor="w",
        ).pack(side="left")
        tk.Entry(
            from_f,
            textvariable=self._conv_from,
            bg=t.input_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=t.border,
            font=(FONT_MONO, 10),
            width=8,
        ).pack(side="left", padx=4)

        to_f = tk.Frame(parent, bg=t.panel_bg)
        to_f.pack(fill="x", padx=8, pady=2)
        tk.Label(
            to_f,
            text=_t("calc.conv_to", "Para"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            width=6,
            anchor="w",
        ).pack(side="left")
        tk.Entry(
            to_f,
            textvariable=self._conv_to,
            bg=t.input_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=t.border,
            font=(FONT_MONO, 10),
            width=8,
        ).pack(side="left", padx=4)

        conv_btn = tk.Label(
            parent,
            text=_t("calc.conv_btn", "Converter →"),
            bg=t.accent,
            fg=t.btn_primary_fg,
            font=(FONT_UI, 10),
            cursor="hand2",
            pady=8,
        )
        conv_btn.pack(fill="x", padx=8, pady=6)
        conv_btn.bind("<Button-1>", lambda _: self._do_convert())

        tk.Label(
            parent,
            textvariable=self._conv_res,
            bg=t.editor_bg,
            fg=t.accent,
            font=(FONT_MONO, 14, "bold"),
            anchor="center",
            pady=10,
        ).pack(fill="x", padx=8, pady=2)

        tk.Label(
            parent,
            text=_t("calc.shortcuts", "Atalhos"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8, "bold"),
        ).pack(anchor="w", padx=8, pady=(8, 2))
        shortcuts = [
            ("°C → °F", "°C", "°F"),
            ("°F → °C", "°F", "°C"),
            ("km → mi", "km", "mi"),
            ("mi → km", "mi", "km"),
            ("kg → lb", "kg", "lb"),
            ("lb → kg", "lb", "kg"),
        ]
        sc_f = tk.Frame(parent, bg=t.panel_bg)
        sc_f.pack(fill="x", padx=8)
        for i, (label, frm, to) in enumerate(shortcuts):
            r, c = divmod(i, 3)
            b = tk.Label(
                sc_f,
                text=label,
                bg=t.btn_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 7),
                cursor="hand2",
                pady=4,
                padx=3,
            )
            b.grid(row=r, column=c, sticky="nsew", padx=1, pady=1)
            b.bind(
                "<Button-1>",
                lambda _, f=frm, to_=to: (
                    self._conv_from.set(f),
                    self._conv_to.set(to_),
                ),
            )
            b.bind("<Enter>", lambda e, w=b: w.config(bg=t.btn_hover))
            b.bind("<Leave>", lambda e, w=b: w.config(bg=t.btn_bg))
        for c in range(3):
            sc_f.columnconfigure(c, weight=1)

    def _do_convert(self) -> None:
        """Realiza conversão entre unidades."""
        try:
            val = float(self._conv_val.get().replace(",", "."))
            frm = self._conv_from.get().strip().lower()
            to = self._conv_to.get().strip().lower()

            to_base: dict[str, float] = {
                "km": 1000,
                "m": 1,
                "cm": 0.01,
                "mm": 0.001,
                "mi": 1609.34,
                "ft": 0.3048,
                "in": 0.0254,
                "yd": 0.9144,
                "kg": 1,
                "g": 0.001,
                "lb": 0.453592,
                "oz": 0.0283495,
                "t": 1000,
                "mg": 0.000001,
            }

            def to_celsius(v: float, unit: str) -> float:
                if unit in ("°c", "c", "celsius"):
                    return v
                if unit in ("°f", "f", "fahrenheit"):
                    return (v - 32) * 5 / 9
                if unit in ("k", "kelvin"):
                    return v - 273.15
                raise ValueError(f"Unidade desconhecida: {unit}")

            def from_celsius(v: float, unit: str) -> float:
                if unit in ("°c", "c", "celsius"):
                    return v
                if unit in ("°f", "f", "fahrenheit"):
                    return v * 9 / 5 + 32
                if unit in ("k", "kelvin"):
                    return v + 273.15
                raise ValueError(f"Unidade desconhecida: {unit}")

            temp_units = {"°c", "c", "°f", "f", "k",
                          "celsius", "fahrenheit", "kelvin"}
            if frm in temp_units or to in temp_units:
                celsius = to_celsius(val, frm)
                result = from_celsius(celsius, to)
            elif frm in to_base and to in to_base:
                result = val * to_base[frm] / to_base[to]
            else:
                self._conv_res.set(
                    _t("calc.conv_unknown", "Unidade não reconhecida"))
                return

            if result == int(result):
                self._conv_res.set(f"{int(result)} {self._conv_to.get()}")
            else:
                self._conv_res.set(f"{result:.6g} {self._conv_to.get()}")
        except Exception as exc:
            self._conv_res.set(f"Erro: {exc}")

    def _build_history(self, parent: tk.Widget, t: Any) -> None:
        """Painel de histórico de cálculos."""
        hdr = tk.Frame(parent, bg=t.panel_bg)
        hdr.pack(fill="x", padx=10, pady=(10, 0))

        tk.Label(
            hdr,
            text=_t("calc.history", "Histórico"),
            bg=t.panel_bg,
            fg=t.fg,
            font=(FONT_UI, 10, "bold"),
        ).pack(side="left")

        clear_lbl = tk.Label(
            hdr,
            text=_t("calc.clear_hist", "Limpar"),
            bg=t.panel_bg,
            fg=t.t_link,
            font=(FONT_UI, 8),
            cursor="hand2",
        )
        clear_lbl.pack(side="right")
        clear_lbl.bind("<Button-1>", lambda _: self._clear_history())

        tk.Frame(parent, bg=t.border, height=1).pack(fill="x", padx=10, pady=4)

        wrap = tk.Frame(parent, bg=t.panel_bg)
        wrap.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self._hist_canvas = tk.Canvas(
            wrap, bg=t.panel_bg, highlightthickness=0)
        vsb = ttk.Scrollbar(wrap, orient="vertical",
                            command=self._hist_canvas.yview)
        vsb.pack(side="right", fill="y")
        self._hist_canvas.config(yscrollcommand=vsb.set)
        self._hist_canvas.pack(fill="both", expand=True)

        self._hist_inner = tk.Frame(self._hist_canvas, bg=t.panel_bg)
        self._hist_win = self._hist_canvas.create_window(
            (0, 0), window=self._hist_inner, anchor="nw"
        )
        self._hist_inner.bind("<Configure>", self._on_hist_configure)
        self._hist_canvas.bind("<Configure>", self._on_hist_configure)

        for ev in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self._hist_canvas.bind(ev, self._on_scroll)
            self._hist_inner.bind(ev, self._on_scroll)

        self._refresh_history()

    def _switch_mode(self, mode: str) -> None:
        self._mode.set(mode)
        self._sci_frame.pack_forget()
        self._conv_frame.pack_forget()
        if mode == "sci":
            self._btn_frame.pack_forget()
            self._sci_frame.pack(fill="both", expand=True,
                                 padx=10, pady=(0, 10))
            self._build_basic_buttons(self._left, self._tmgr.theme)
            self._sci_frame.pack(fill="x", padx=10, pady=(0, 10))
            self._mode_badge.pack()
        elif mode == "conv":
            self._btn_frame.pack_forget()
            self._conv_frame.pack(fill="both", expand=True)
            self._mode_badge.pack_forget()
        else:
            self._btn_frame.pack(fill="both", expand=True,
                                 padx=10, pady=(0, 10))
            self._mode_badge.pack_forget()

    def _on_hist_configure(self, _e: Any = None) -> None:
        self._hist_canvas.config(scrollregion=self._hist_canvas.bbox("all"))
        self._hist_canvas.itemconfig(
            self._hist_win, width=self._hist_canvas.winfo_width()
        )

    def _on_scroll(self, e: Any) -> None:
        if e.delta:
            self._hist_canvas.yview_scroll(-1 * (e.delta // 120), "units")
        elif e.num == 4:
            self._hist_canvas.yview_scroll(-1, "units")
        else:
            self._hist_canvas.yview_scroll(1, "units")

    def _refresh_history(self) -> None:
        t = self._tmgr.theme
        for w in self._hist_inner.winfo_children():
            w.destroy()

        items = list(reversed(self._history[-50:]))
        if not items:
            tk.Label(
                self._hist_inner,
                text=_t(
                    "calc.hist_empty",
                    "Nenhum cálculo ainda.\nDigite uma expressão e pressione Enter.",
                ),
                bg=t.panel_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 9),
                justify="center",
            ).pack(pady=30)
            return

        for item in items:
            row = tk.Frame(
                self._hist_inner,
                bg=t.panel_bg,
                highlightthickness=1,
                highlightbackground=t.border,
            )
            row.pack(fill="x", pady=2, padx=1)

            tk.Frame(row, bg=t.accent, width=3).pack(side="left", fill="y")

            content = tk.Frame(row, bg=t.panel_bg)
            content.pack(side="left", fill="both", expand=True)

            expr_lbl = tk.Label(
                content,
                text=item.get("expr", ""),
                bg=t.panel_bg,
                fg=t.fg_muted,
                font=(FONT_MONO, 8),
                anchor="w",
                cursor="hand2",
            )
            expr_lbl.pack(fill="x", padx=6, pady=(5, 0))
            expr_lbl.bind(
                "<Button-1>",
                lambda _, e=item["expr"]: (
                    self._expr_var.set(e),
                    self._expr_entry.focus_set(),
                ),
            )

            res_lbl = tk.Label(
                content,
                text=f"= {item.get('result', '')}",
                bg=t.panel_bg,
                fg=t.fg,
                font=(FONT_MONO, 11, "bold"),
                anchor="e",
            )
            res_lbl.pack(fill="x", padx=6, pady=(0, 5))

            for w in (row, content, expr_lbl, res_lbl):
                w.bind(
                    "<Enter>", lambda e, r=row: r.config(
                        highlightbackground=t.accent)
                )
                w.bind(
                    "<Leave>", lambda e, r=row: r.config(
                        highlightbackground=t.border)
                )

    def _evaluate(self) -> None:
        expr = self._expr_var.get().strip()
        if not expr:
            return

        safe_expr = (
            expr.replace("×", "*")
            .replace("÷", "/")
            .replace("^", "**")
            .replace("√", "sqrt")
        )
        safe_expr = _decimal_commas_to_points(safe_expr)

        ns = dict(self._SAFE_BUILTINS)
        if self._last_result is not None:
            ns["ans"] = self._last_result
            ns["_"] = self._last_result

        try:
            result = _safe_eval(safe_expr, ns)
            result_str = self._format_result(result)
            self._last_result = (
                float(result) if isinstance(result, (int, float)) else None
            )
            self._result_var.set(result_str)

            entry: dict = {"expr": expr, "result": result_str, "note": ""}
            self._history.append(entry)
            _save_history(self._history)
            self._refresh_history()
            self._history_idx = -1

        except ZeroDivisionError:
            self._result_var.set(
                _t("calc.div_zero", "Divisão por zero")
            )
        except (ValueError, TypeError, OverflowError, ArithmeticError, SyntaxError) as exc:
            self._result_var.set(f"Erro: {type(exc).__name__}")

    def _format_result(self, result: Any) -> str:
        if isinstance(result, float):
            if (
                result == int(result)
                and not math.isinf(result)
                and not math.isnan(result)
            ) and abs(result) < 1e15:
                return str(int(result))
            return f"{result:.10g}"
        return str(result)

    def _btn_action(self, action: str) -> None:
        cur = self._expr_var.get()
        if action == "=":
            self._evaluate()
        elif action == "c":
            self._expr_var.set("")
            self._result_var.set("0")
        elif action == "ce":
            self._result_var.set("0")
        elif action == "bs":
            self._expr_var.set(cur[:-1])
        elif action == "neg":
            try:
                val = float(self._result_var.get().replace(",", "."))
                self._expr_var.set(str(-val))
            except ValueError:
                self._expr_var.set(f"-({cur})" if cur else "")
        else:
            self._expr_var.set(cur + action)
            self._expr_entry.icursor("end")

    def _insert_snippet(self, snippet: str) -> None:
        cur = self._expr_var.get()
        if snippet in ("pi", "e"):
            self._expr_var.set(cur + snippet)
        elif "x" in snippet:
            x_val = self._result_var.get() if self._result_var.get() != "0" else cur
            filled = snippet.replace("x", f"({x_val})")
            self._expr_var.set(filled)
        else:
            self._expr_var.set(cur + snippet)
        self._expr_entry.icursor("end")
        self._expr_entry.focus_set()

    def _history_up(self, _e: Any = None) -> None:
        calc_hist = [h["expr"] for h in self._history]
        if not calc_hist:
            return
        self._history_idx = max(
            0, (self._history_idx if self._history_idx >=
                0 else len(calc_hist)) - 1
        )
        self._expr_var.set(calc_hist[self._history_idx])

    def _history_down(self, _e: Any = None) -> None:
        calc_hist = [h["expr"] for h in self._history]
        if self._history_idx < 0:
            return
        self._history_idx += 1
        if self._history_idx >= len(calc_hist):
            self._history_idx = -1
            self._expr_var.set("")
        else:
            self._expr_var.set(calc_hist[self._history_idx])

    def _clear_history(self) -> None:
        self._history.clear()
        _save_history(self._history)
        self._refresh_history()

    def _mem_update_label(self) -> None:
        self._memory_lbl.config(text=f"M: {self._format_result(self._memory)}")

    def _mem_clear(self) -> None:
        self._memory = 0.0
        self._mem_update_label()

    def _mem_recall(self) -> None:
        self._expr_var.set(self._expr_var.get() +
                           self._format_result(self._memory))

    def _mem_add(self) -> None:
        try:
            self._memory += float(self._result_var.get())
            self._mem_update_label()
        except ValueError:
            pass

    def _mem_sub(self) -> None:
        try:
            self._memory -= float(self._result_var.get())
            self._mem_update_label()
        except ValueError:
            pass

    def _mem_store(self) -> None:
        try:
            self._memory = float(self._result_var.get())
            self._mem_update_label()
        except ValueError:
            pass
