"""Codis - StickyScroll: cabeçalhos de escopo fixos no topo do editor.

Exibe as linhas de definição (classe, função, if/for/with etc.) do escopo
atual como um painel fixo colado ao topo da área de edição, igual ao
recurso homônimo do VS Code. Atualiza em tempo real conforme o usuário
rola ou move o cursor.

Suporte a linguagens: Python, JavaScript/TypeScript, Rust, Go, Java,
C/C++, Ruby, PHP, Swift, Kotlin, CSS/SCSS e qualquer linguagem com
padrões de indentação reconhecíveis.
"""

from __future__ import annotations

import contextlib
import logging
import re
import tkinter as tk
from typing import TYPE_CHECKING

from src.constants import FONT_MONO, FONT_SIZE

if TYPE_CHECKING:
    from src.theme.base import Theme

logger = logging.getLogger("Codis")

_SCOPE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^(\s*)(async\s+def|def|class)\s+\w"), "python"),
    (
        re.compile(
            r"^(\s*)(function\s+\w|class\s+\w|const\s+\w+\s*=.*=>|async\s+function)"
        ),
        "js",
    ),
    (re.compile(r"^(\s*)(pub\s+)?(fn|struct|enum|impl|trait|mod)\s+\w"), "rust"),
    (re.compile(r"^(\s*)(func|type|struct|interface)\s+\w"), "go"),
    (
        re.compile(
            r"^(\s*)(public|private|protected|internal|open|override)?\s*(fun|func|class|interface|enum|object|struct)\s+\w"
        ),
        "jvm",
    ),
    (re.compile(r"^(\s*)(\w[\w\s\*&:<>]+)\s+\w+\s*\([^;]*\)\s*(\{|$)"), "c"),
    (re.compile(r"^(\s*)(def|class|module|describe|context|it)\s+\w"), "ruby"),
    (re.compile(r"^(\s*)(function|class|interface|trait)\s+\w"), "php"),
    (re.compile(r"^(\s*)([.#\w&\[\*][^{]+)\s*\{"), "css"),
    (
        re.compile(
            r"^(\s*)<(div|section|article|header|footer|nav|main|aside|form|table|ul|ol|template|component|script|style)[\s>]",
            re.IGNORECASE,
        ),
        "html",
    ),
    (re.compile(r"^(\s*)(\S.*?)(\s*[:{]\s*)$"), "generic"),
]

_MAX_STICKY_LINES = 5

_UPDATE_DEBOUNCE_MS = 80


class StickyScroll(tk.Frame):
    """Painel de Sticky Scroll fixo no topo do editor.

    Monitora a posição de rolagem e o conteúdo do widget de texto associado
    e exibe as linhas de definição do escopo atual no topo da área de edição.
    Clique em uma linha sticky navega para ela no editor.

    Args:
        parent: Widget pai (normalmente o frame do EditorPane).
        txt: Widget tk.Text do editor a ser monitorado.
        theme: Objeto Theme com as cores do tema ativo.
        font_family: Nome da família de fonte monoespaçada.
        font_size: Tamanho da fonte em pontos.
        on_goto: Callback(line_number: int) acionado ao clicar em uma linha sticky.
    """

    def __init__(
        self,
        parent: tk.Widget,
        txt: tk.Text,
        theme: Theme,
        font_family: str = FONT_MONO,
        font_size: int = FONT_SIZE,
        on_goto: callable | None = None,
    ) -> None:
        """Inicializa o StickyScroll e o conecta ao widget de texto.

        Args:
            parent: Widget pai Tkinter.
            txt: Widget tk.Text do editor.
            theme: Tema visual ativo.
            font_family: Família da fonte monoespaçada.
            font_size: Tamanho da fonte.
            on_goto: Callback(int) para navegação ao clicar.
        """
        t = theme
        super().__init__(parent, bg=t.bg, bd=0, highlightthickness=0)
        self._txt = txt
        self._theme = theme
        self._font_family = font_family
        self._font_size = font_size
        self._on_goto = on_goto
        self._after_id: str | None = None
        self._current_lines: list[tuple[int, str]] = []
        self._row_labels: list[tk.Label] = []
        self._separator: tk.Frame | None = None
        self._visible = False
        self._build()
        self._bind_events()

    def _build(self) -> None:
        """Constrói os widgets internos do painel sticky."""
        t = self._theme

        self._lines_frame = tk.Frame(self, bg=t.bg, bd=0)
        self._lines_frame.pack(fill="x", side="top")

        self._separator = tk.Frame(self, bg=t.border, height=1)
        self._separator.pack(fill="x", side="top")

        self.pack_propagate(False)
        self.config(height=0)

    def _bind_events(self) -> None:
        """Conecta os eventos do widget de texto ao handler de atualização."""
        for event in ("<KeyRelease>", "<<Modified>>", "<ButtonRelease>"):
            self._txt.bind(event, self._schedule_update, add=True)

        getattr(self._txt, "_orig_yscroll_cb", None)
        self._txt.bind("<Configure>", self._schedule_update, add=True)

    def _schedule_update(self, _event=None) -> None:
        """Agenda atualização com debounce para evitar redesenhos excessivos."""
        if self._after_id:
            with contextlib.suppress(tk.TclError):
                self.after_cancel(self._after_id)
        self._after_id = self.after(_UPDATE_DEBOUNCE_MS, self._update)

    def notify_scroll(self) -> None:
        """Notifica o StickyScroll que o editor rolou - chamado pelo EditorPane.

        Deve ser chamado sempre que a posição de rolagem vertical mudar.
        """
        self._schedule_update()

    def _update(self) -> None:
        """Recalcula e exibe as linhas de escopo para a posição atual do editor."""
        self._after_id = None
        if not self._txt.winfo_exists():
            return
        try:
            sticky = self._compute_sticky_lines()
        except (tk.TclError, ValueError, AttributeError):
            sticky = []

        if sticky == self._current_lines:
            return

        self._current_lines = sticky
        self._rebuild_labels()

    def _compute_sticky_lines(self) -> list[tuple[int, str]]:
        """Calcula quais linhas de escopo devem aparecer no sticky scroll.

        Analisa as linhas visíveis e acima do viewport para encontrar os
        ancestrais de escopo mais próximos (menor indentação acima da vista).

        Returns:
            list[tuple[int, str]]: Lista de (número_da_linha_1based, texto_da_linha).
        """
        try:
            first_vis = self._txt.index("@0,0")
            first_line = int(first_vis.split(".")[0])
        except (tk.TclError, ValueError):
            return []

        if first_line <= 1:
            return []

        try:
            above_text = self._txt.get("1.0", f"{first_line}.0")
        except tk.TclError:
            return []

        lines_above = above_text.splitlines()

        try:
            vis_line_text = self._txt.get(
                f"{first_line}.0", f"{first_line}.end")
            min_indent = len(vis_line_text) - len(vis_line_text.lstrip())
        except tk.TclError:
            min_indent = 0

        found: list[tuple[int, str]] = []
        last_indent = min_indent

        for raw_idx in range(len(lines_above) - 1, -1, -1):
            raw = lines_above[raw_idx]
            stripped = raw.lstrip()
            if not stripped or stripped.startswith(("#", "//", "/*", "*", "<!--")):
                continue
            indent = len(raw) - len(stripped)
            if indent >= last_indent:
                continue
            if self._is_scope_header(raw):
                line_num = raw_idx + 1
                found.append((line_num, raw.rstrip()))
                last_indent = indent
                if indent == 0 or len(found) >= _MAX_STICKY_LINES:
                    break

        found.reverse()
        return found

    @staticmethod
    def _is_scope_header(line: str) -> bool:
        """Verifica se uma linha é um cabeçalho de escopo reconhecível.

        Args:
            line: Texto bruto da linha a verificar.

        Returns:
            bool: True se a linha inicia um bloco de escopo.
        """
        return any(pattern.match(line) for pattern, _ in _SCOPE_PATTERNS)

    def _rebuild_labels(self) -> None:
        """Reconstrói os labels do painel sticky com as linhas calculadas."""
        t = self._theme

        for lbl in self._row_labels:
            with contextlib.suppress(tk.TclError):
                lbl.destroy()
        self._row_labels.clear()

        if not self._current_lines:
            self._hide()
            return

        for line_num, text in self._current_lines:
            row = tk.Frame(self._lines_frame, bg=t.bg, bd=0)
            row.pack(fill="x", side="top")

            num_lbl = tk.Label(
                row,
                text=f" {line_num} ",
                bg=t.bg,
                fg=t.fg_muted,
                font=(self._font_family, self._font_size - 1),
                anchor="e",
                width=5,
                height=1,
            )
            num_lbl.pack(side="left")

            code_lbl = tk.Label(
                row,
                text=text[:200],
                bg=self._sticky_bg(),
                fg=t.fg,
                font=(self._font_family, self._font_size),
                anchor="w",
                padx=4,
                cursor="hand2",
            )
            code_lbl.pack(side="left", fill="x", expand=True)

            code_lbl.bind(
                "<Enter>",
                lambda _, w=code_lbl: w.config(fg=t.accent),
            )
            code_lbl.bind(
                "<Leave>",
                lambda _, w=code_lbl: w.config(fg=t.fg),
            )

            code_lbl.bind(
                "<Button-1>",
                lambda _, ln=line_num: self._on_click(ln),
            )
            num_lbl.bind(
                "<Button-1>",
                lambda _, ln=line_num: self._on_click(ln),
            )

            self._row_labels.append(row)

        self._show()

    def _sticky_bg(self) -> str:
        """Retorna a cor de fundo do painel sticky (ligeiramente diferente do editor).

        Returns:
            str: Código hexadecimal da cor de fundo.
        """
        t = self._theme

        try:
            bg = t.bg
            tab = t.tabbar_bg

            r = (int(bg[1:3], 16) + int(tab[1:3], 16)) // 2
            g = (int(bg[3:5], 16) + int(tab[3:5], 16)) // 2
            b = (int(bg[5:7], 16) + int(tab[5:7], 16)) // 2
            return f"#{r:02x}{g:02x}{b:02x}"
        except (ValueError, AttributeError):
            return t.bg

    def _on_click(self, line_number: int) -> None:
        """Navega para a linha clicada no editor.

        Args:
            line_number: Número da linha (1-based) para onde navegar.
        """
        if self._on_goto:
            with contextlib.suppress(TypeError, AttributeError):
                self._on_goto(line_number)
        else:
            with contextlib.suppress(tk.TclError):
                self._txt.mark_set("insert", f"{line_number}.0")
                self._txt.see(f"{line_number}.0")
                self._txt.focus_set()

    def _show(self) -> None:
        """Torna o painel sticky visível sem relayout (não usa pack/pack_forget)."""
        if not self._visible:
            self._visible = True
            with contextlib.suppress(tk.TclError):
                self.pack_propagate(True)
                self.update_idletasks()

    def _hide(self) -> None:
        """Oculta o painel sticky sem relayout - colapsa a altura para 0."""
        if self._visible:
            self._visible = False
            with contextlib.suppress(tk.TclError):
                self.pack_propagate(False)
                self.config(height=0)
                self.update_idletasks()

    def apply_theme(self, theme: Theme) -> None:
        """Aplica novo tema ao painel sticky e força redesenho.

        Args:
            theme: Novo objeto Theme com as cores atualizadas.
        """
        self._theme = theme
        self.config(bg=theme.bg)
        self._lines_frame.config(bg=theme.bg)
        if self._separator:
            self._separator.config(bg=theme.border)

        self._current_lines = []
        self._schedule_update()

    def update_font(self, family: str, size: int) -> None:
        """Atualiza a fonte usada nos labels do sticky scroll.

        Args:
            family: Nome da família de fonte.
            size: Tamanho da fonte em pontos.
        """
        self._font_family = family
        self._font_size = size
        self._current_lines = []
        self._schedule_update()

    def set_enabled(self, enabled: bool) -> None:
        """Habilita ou desabilita o sticky scroll.

        Args:
            enabled: True para habilitar, False para ocultar e parar de atualizar.
        """
        if not enabled:
            self._current_lines = []
            self._hide()
        else:
            self._schedule_update()

    def destroy(self) -> None:
        """Remove o overlay de sticky scroll e cancela todos os jobs pendentes."""
        if self._after_id:
            with contextlib.suppress(tk.TclError):
                self.after_cancel(self._after_id)
        super().destroy()
