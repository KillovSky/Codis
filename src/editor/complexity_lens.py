"""Codis - ComplexityLens: badge flutuante de complexidade ciclomática.

Exibe em tempo real a complexidade ciclomática de funções e métodos Python
à medida que o cursor se move pelo código. O badge aparece discretamente no
canto superior direito do editor - mostrando nível, nome da função e um
indicador visual de cor (verde/amarelo/vermelho) proporcional à complexidade.

Complexidade ciclomática (McCabe):
  - 1-5   : Simples -> verde
  - 6-10  : Moderada -> amarelo
  - 11-15 : Alta -> laranja
  - 16+   : Muito alta -> vermelho

A análise é feita via AST e nunca bloqueia a UI - é agendada com debounce
de 300ms após cada movimento de cursor.

Uso::

    from src.editor.complexity_lens import ComplexityLens
    lens = ComplexityLens(text_widget, theme)
    lens.on_cursor_move()   # chame nos eventos de cursor
    lens.destroy()
"""

from __future__ import annotations

import ast
import contextlib
import logging
import tkinter as tk
from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.i18n import _t

if TYPE_CHECKING:
    from src.theme.base import Theme

logger = logging.getLogger("Codis")

_CC_LOW = 5
_CC_MEDIUM = 10
_CC_HIGH = 15

_DEBOUNCE_MS = 300
_BADGE_PAD_X = 10
_BADGE_PAD_Y = 8


def _cyclomatic_complexity(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Calcula a complexidade ciclomática de McCabe para um nó função/método.

    Conta 1 (base) + 1 para cada ponto de decisão: if/elif/for/while/except/
    with/assert/comprehension/ternary/bool-op (and/or).

    Args:
        node: Nó AST de função ou método.

    Returns:
        int: Complexidade ciclomática (mínimo 1).
    """
    complexity = 1
    for child in ast.walk(node):
        if isinstance(
            child,
            (
                ast.If,
                ast.For,
                ast.While,
                ast.ExceptHandler,
                ast.With,
                ast.Assert,
                ast.comprehension,
            ),
        ):
            complexity += 1
        elif isinstance(child, ast.BoolOp):
            complexity += len(child.values) - 1
        elif isinstance(child, ast.IfExp):
            complexity += 1
    return complexity


@dataclass
class _FuncInfo:
    name: str
    start: int
    end: int
    complexity: int
    is_method: bool = False


def _analyse_functions(source: str) -> list[_FuncInfo]:
    """Extrai todas as funções/métodos e suas complexidades via AST.

    Args:
        source: Código-fonte Python.

    Returns:
        Lista de _FuncInfo, ordenada por linha de início.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    results: list[_FuncInfo] = []
    method_nodes: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in ast.walk(node):
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    method_nodes.add(id(item))

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(node, "end_lineno", node.lineno)
            cc = _cyclomatic_complexity(node)
            results.append(
                _FuncInfo(
                    name=node.name,
                    start=node.lineno,
                    end=end,
                    complexity=cc,
                    is_method=id(node) in method_nodes,
                )
            )

    return sorted(results, key=lambda f: f.start)


class ComplexityLens:
    """Badge flutuante de complexidade ciclomática para o editor.

    Analisa o arquivo atual com AST e mostra, para a função sob o cursor,
    um badge colorido com nome e nível de complexidade.

    Args:
        txt:   Widget tk.Text do editor.
        theme: Tema visual ativo.
    """

    def __init__(self, txt: tk.Text, theme: Theme) -> None:
        """Inicializa o lens de complexidade ciclomática no editor."""
        self._txt = txt
        self._theme = theme
        self._after_id: str | None = None
        self._badge: tk.Label | None = None
        self._funcs: list[_FuncInfo] = []
        self._enabled = True

        self._txt.bind("<KeyRelease>", self._schedule, add=True)
        self._txt.bind("<ButtonRelease>", self._schedule, add=True)
        self._txt.bind("<FocusIn>", self._schedule, add=True)

    def apply_theme(self, theme: Theme) -> None:
        """Atualiza o tema e redesenha o badge se visível."""
        self._theme = theme
        if self._badge and self._badge.winfo_exists():
            self._badge.destroy()
            self._badge = None

    def set_enabled(self, value: bool) -> None:
        """Ativa ou desativa o complexity lens."""
        self._enabled = value
        if not value:
            self._hide_badge()

    def destroy(self) -> None:
        """Cancela timers e destrói o badge."""
        if self._after_id:
            with contextlib.suppress(tk.TclError):
                self._txt.after_cancel(self._after_id)
        self._hide_badge()

    def _schedule(self, _e=None) -> None:
        if not self._enabled:
            return
        if self._after_id:
            with contextlib.suppress(tk.TclError):
                self._txt.after_cancel(self._after_id)
        self._after_id = self._txt.after(_DEBOUNCE_MS, self._analyse_and_show)

    def _analyse_and_show(self) -> None:
        self._after_id = None
        if not self._txt.winfo_exists():
            return
        try:
            source = self._txt.get("1.0", "end-1c")
            cursor = self._txt.index(tk.INSERT)
            cur_line = int(cursor.split(".")[0])
        except tk.TclError:
            return

        self._funcs = _analyse_functions(source)

        func = self._func_at_line(cur_line)
        if func is None:
            self._hide_badge()
            return

        self._show_badge(func)

    def _func_at_line(self, line: int) -> _FuncInfo | None:
        """Retorna a função mais específica (mais aninhada) que contém a linha."""
        candidates = [f for f in self._funcs if f.start <= line <= f.end]
        if not candidates:
            return None
        return min(candidates, key=lambda f: f.end - f.start)

    def _cc_color(self, cc: int) -> str:
        if cc <= _CC_LOW:
            return "#4ade80"
        if cc <= _CC_MEDIUM:
            return "#facc15"
        if cc <= _CC_HIGH:
            return "#fb923c"
        return "#f87171"

    def _cc_label(self, cc: int) -> str:
        if cc <= _CC_LOW:
            return _t("complexity.low", default="Simples")
        if cc <= _CC_MEDIUM:
            return _t("complexity.medium", default="Moderada")
        if cc <= _CC_HIGH:
            return _t("complexity.high", default="Alta")
        return _t("complexity.very_high", default="Muito alta")

    def _show_badge(self, func: _FuncInfo) -> None:
        """Cria/atualiza o badge flutuante com dados da função."""
        cc = func.complexity
        color = self._cc_color(cc)
        label = self._cc_label(cc)
        kind = (
            _t("complexity.method", default="método")
            if func.is_method
            else _t("complexity.func", default="função")
        )
        cc_meter = self._cc_meter(cc)

        text = f"  {cc_meter}  {func.name} ({kind})  -  CC {cc}  ({label})  "

        parent = self._txt.master if self._txt.master else self._txt

        if self._badge is None or not self._badge.winfo_exists():
            self._badge = tk.Label(
                parent,
                text=text,
                font=("TkFixedFont", 9),
                relief="flat",
                bd=0,
                padx=4,
                pady=2,
            )

        t = self._theme
        bg = getattr(t, "panel_bg", "#1e1e2e")
        fg = color

        self._badge.config(
            text=text,
            bg=bg,
            fg=fg,
        )

        try:
            x = self._txt.winfo_width() - _BADGE_PAD_X
            y = _BADGE_PAD_Y
            self._badge.place(in_=self._txt, x=x, y=y, anchor="ne")
            self._badge.lift()
        except tk.TclError:
            pass

    def _hide_badge(self) -> None:
        if self._badge and self._badge.winfo_exists():
            with contextlib.suppress(tk.TclError):
                self._badge.place_forget()

    @staticmethod
    def _cc_meter(cc: int) -> str:
        """Retorna uma mini barra Unicode representando o nível de complexidade."""
        blocks = ["▁", "▂", "▃", "▄", "▅", "▆", "▇", "█"]
        idx = min(len(blocks) - 1, max(0, (cc - 1) * len(blocks) // 20))
        return blocks[idx]
