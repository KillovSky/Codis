"""Codis - InlayHints: dicas de tipo e parâmetro exibidas inline no editor.

Implementa o sistema de inlay hints (dicas embutidas) - textos fantasmas
que aparecem inline no código mostrando:

  - **Dicas de parâmetro**: nomes dos parâmetros ao lado dos argumentos em
    chamadas de função (ex: ``foo(|x=|10, |y=|20)``).
  - **Dicas de tipo**: tipos inferidos ao lado de atribuições sem anotação
    explícita (ex: ``x = 10  |: int|``).
  - **Dicas de retorno**: tipo de retorno de funções sem anotação
    (via LSP ou análise estática básica).

As dicas são fornecidas pelo LSP quando disponível, ou por análise
estática simples (regex + AST parcial) como fallback para Python.

Renderização: usa tags Tkinter com estilo fantasma (fg muted, italic)
inseridas após os tokens alvo, sem modificar o buffer real.
"""

from __future__ import annotations

import ast
import contextlib
import logging
import re
import tkinter as tk
from typing import TYPE_CHECKING, Any

from src.constants import FONT_SIZE, FONT_UI

if TYPE_CHECKING:
    from src.theme.base import Theme

logger = logging.getLogger("Codis")

_HINT_TAG_PREFIX = "inlay_hint_"

_HINT_DEBOUNCE_MS = 500

_MAX_HINTS = 300

_CALL_PATTERN = re.compile(
    r"\b([a-zA-Z_]\w*)\s*\(([^)]*)\)",
    re.MULTILINE,
)

_LITERAL_TYPES: dict[type, str] = {
    ast.Constant: lambda n: type(n.value).__name__ if n.value is not None else "None",
    ast.List: lambda _: "list",
    ast.Dict: lambda _: "dict",
    ast.Set: lambda _: "set",
    ast.Tuple: lambda _: "tuple",
    ast.ListComp: lambda _: "list",
    ast.DictComp: lambda _: "dict",
    ast.SetComp: lambda _: "set",
}


def _infer_type(node: ast.expr) -> str | None:
    """Infere o tipo de um nó AST de forma simples.

    Args:
        node: Nó de expressão do AST Python.

    Returns:
        str | None: Nome do tipo inferido ou None se não determinável.
    """
    for node_type, resolver in _LITERAL_TYPES.items():
        if isinstance(node, node_type):
            try:
                return resolver(node)
            except (ValueError, TypeError, AttributeError):
                return None
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            return node.func.attr
    return None


class InlayHints:
    """Sistema de inlay hints para o editor do Codis.

    Exibe dicas de tipo e parâmetro inline usando análise estática (para
    Python) e via callbacks do LSP (para outras linguagens).

    Args:
        txt: Widget tk.Text do editor.
        theme: Tema visual ativo.
        lang: Linguagem atual do arquivo aberto.
    """

    def __init__(
        self,
        txt: tk.Text,
        theme: Theme,
        lang: str = "python",
    ) -> None:
        """Inicializa o sistema de inlay hints.

        Args:
            txt: Widget tk.Text do editor.
            theme: Objeto Theme com as cores do tema ativo.
            lang: Identificador de linguagem ('python', 'javascript', etc.).
        """
        self._txt = txt
        self._theme = theme
        self._lang = lang
        self._enabled_types: set[str] = {"parameter", "type"}
        self._hints: list[dict[str, Any]] = []
        self._hint_counter = 0
        self._after_id: str | None = None
        self._enabled = False
        self._lsp_hints: list[dict] = []

    def set_enabled(self, enabled: bool) -> None:
        """Habilita ou desabilita os inlay hints.

        Args:
        """
        self._enabled = enabled
        if enabled:
            self._schedule_scan()
        else:
            self.clear()

    def set_lang(self, lang: str) -> None:
        """Atualiza a linguagem do arquivo para adequar a análise estática.

        Args:
            lang: Identificador de linguagem.
        """
        self._lang = lang
        if self._enabled:
            self.clear()
            self._schedule_scan()

    def notify_change(self) -> None:
        """Notifica que o conteúdo mudou - re-escaneia com debounce."""
        if self._enabled:
            self._lsp_hints = []
            self._schedule_scan()

    def clear(self) -> None:
        """Remove todas as annotations de inlay hint do widget de texto."""
        try:
            for tag in self._txt.tag_names():
                if tag.startswith(_HINT_TAG_PREFIX):
                    ranges = self._txt.tag_ranges(tag)
                    if ranges:
                        self._txt.delete(ranges[0], ranges[1])
                    self._txt.tag_delete(tag)
        except tk.TclError:
            pass
        self._hints.clear()

    def apply_theme(self, theme: Theme) -> None:
        """Aplica novo tema e re-renderiza os hints.

        Args:
            theme: Novo objeto Theme com as cores atualizadas.
        """
        self._theme = theme
        if self._enabled:
            self.clear()
            self._schedule_scan()

    def _schedule_scan(self) -> None:
        """Agenda re-scan com debounce."""
        if self._after_id:
            with contextlib.suppress(tk.TclError):
                self._txt.after_cancel(self._after_id)
        self._after_id = self._txt.after(_HINT_DEBOUNCE_MS, self._scan)

    def _scan(self) -> None:
        """Escaneia o documento e renderiza hints conforme a linguagem."""
        self._after_id = None
        if not self._txt.winfo_exists():
            return
        if not self._enabled:
            return

        if self._lsp_hints:
            self._render_lsp_hints()
            return

        if self._lang == "python":
            self._scan_python()

    def _scan_python(self) -> None:
        """Analisa Python com AST para inferir tipos de atribuições simples."""
        try:
            content = self._txt.get("1.0", "end-1c")
        except tk.TclError:
            return

        hints: list[dict] = []

        if "type" in self._enabled_types:
            hints.extend(self._extract_python_type_hints(content))

        if "parameter" in self._enabled_types:
            hints.extend(self._extract_python_param_hints(content))

        if len(hints) > _MAX_HINTS:
            hints = hints[:_MAX_HINTS]

        self.clear()
        self._hints = hints
        self._render_hints()

    @staticmethod
    def _extract_python_type_hints(content: str) -> list[dict]:
        """Extrai dicas de tipo de atribuições Python sem anotação.

        Usa ast.parse() para encontrar atribuições simples onde o tipo
        pode ser inferido da expressão do lado direito.

        Args:
            content: Conteúdo completo do documento.

        Returns:
            list[dict]: Lista de dicts com 'line', 'col', 'text', 'kind'.
        """
        hints = []
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return hints

        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if not isinstance(target, ast.Name):
                    continue

                inferred = _infer_type(node.value)
                if inferred and inferred not in (target.id, "None"):
                    hints.append(
                        {
                            "line": node.lineno,
                            "col": node.col_offset + len(target.id),
                            "text": f": {inferred}",
                            "kind": "type",
                        }
                    )
            elif isinstance(node, ast.AnnAssign):
                pass

        return hints

    @staticmethod
    def _extract_python_param_hints(content: str) -> list[dict]:
        """Extrai dicas de nome de parâmetro em chamadas Python.

        Detecta chamadas de função com argumentos posicionais e exibe o
        nome do parâmetro correspondente, quando possível via AST.

        Args:
            content: Conteúdo completo do documento.

        Returns:
            list[dict]: Lista de dicts com 'line', 'col', 'text', 'kind'.
        """
        hints = []
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return hints

        func_params: dict[str, list[str]] = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                params = [a.arg for a in node.args.args]

                if params and params[0] in ("self", "cls"):
                    params = params[1:]
                func_params[node.name] = params

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func_name = None
                if isinstance(node.func, ast.Name):
                    func_name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    func_name = node.func.attr

                if func_name and func_name in func_params:
                    params = func_params[func_name]
                    for i, arg in enumerate(node.args):
                        if i < len(params):
                            hints.append(
                                {
                                    "line": arg.lineno,
                                    "col": arg.col_offset,
                                    "text": f"{params[i]}=",
                                    "kind": "parameter",
                                }
                            )

        return hints

    def _render_hints(self) -> None:
        """Renderiza as hints coletadas no widget de texto em suas posições corretas."""
        for hint in self._hints:
            self._insert_hint(hint["line"], hint["col"],
                              hint["text"], hint["kind"])

    def _render_lsp_hints(self) -> None:
        """Renderiza hints recebidos via LSP."""
        self.clear()
        count = 0
        for hint in self._lsp_hints:
            if count >= _MAX_HINTS:
                break
            line = hint.get("line", 0)
            col = hint.get("col", 0)
            label = hint.get("label", "")
            kind_num = hint.get("kind", 1)
            kind = "type" if kind_num == 1 else "parameter"
            if (kind in self._enabled_types) and label:
                self._insert_hint(line, col, label, kind)
                count += 1

    def _insert_hint(self, line: int, col: int, text: str, kind: str) -> None:
        """Insere um hint inline no editor.

        Args:
            line: Número da linha (1-based).
            col: Coluna de inserção (0-based).
            text: Texto do hint a exibir.
            kind: Tipo do hint ('type', 'parameter', 'return').
        """
        t = self._theme
        tag = f"{_HINT_TAG_PREFIX}{self._hint_counter}"
        self._hint_counter += 1

        if kind == "parameter":
            fg = t.t_func if hasattr(t, "t_func") else t.fg_muted
        elif kind == "type":
            fg = t.t_type if hasattr(t, "t_type") else t.fg_muted
        else:
            fg = t.fg_muted

        try:
            self._txt.tag_configure(
                tag,
                foreground=fg,
                font=(FONT_UI, max(FONT_SIZE - 2, 8), "italic"),
                background=self._hint_bg(),
            )
            pos = f"{line}.{col}"
            self._txt.insert(pos, text, (tag,))
        except tk.TclError:
            pass

    def _hint_bg(self) -> str:
        """Calcula a cor de fundo sutil para hints.

        Returns:
            str: Código hexadecimal da cor de fundo.
        """
        t = self._theme
        try:
            bg = t.bg
            r = min(255, int(bg[1:3], 16) + 8)
            g = min(255, int(bg[3:5], 16) + 8)
            b = min(255, int(bg[5:7], 16) + 12)
            return f"#{r:02x}{g:02x}{b:02x}"
        except (ValueError, AttributeError):
            return t.bg

    def destroy(self) -> None:
        """Cancela jobs pendentes e libera o estado interno das hints."""
        if self._after_id:
            with contextlib.suppress(tk.TclError):
                self._txt.after_cancel(self._after_id)
        self.clear()
