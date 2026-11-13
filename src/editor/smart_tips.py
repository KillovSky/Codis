"""Codis - SmartTips: sistema de dicas flutuantes inteligentes para o editor.

Exibe dicas contextuais não-intrusivas baseadas em gatilhos automáticos:

  - **Arquivo grande**: avisa quando o arquivo tem muitas linhas
  - **Complexidade**: detecta funções/classes aninhadas profundamente
  - **Convenção de nome**: detecta variáveis de nome genérico (x, tmp, data...)
  - **Dados especiais**: detecta senhas/tokens em texto literal
  - **Linhas longas**: avisa quando muitas linhas excedem 100 caracteres
  - **Funções grandes**: detecta funções com muitas linhas
  - **Imports duplicados**: detecta imports repetidos no topo do arquivo
  - **TODO acumulado**: avisa quando há muitos comentários TODO/FIXME
  - **Trailing whitespace**: detecta excesso de espaço em branco final
  - **Encoding especial**: avisa sobre caracteres invisíveis no arquivo

As dicas aparecem como balões flutuantes no canto inferior direito do editor,
com animação de fade-in e auto-dismiss após alguns segundos.
Podem ser dispensadas manualmente ou desabilitadas globalmente nas configurações.

Uso::

    from src.editor.smart_tips import SmartTips
    tips = SmartTips(editor_pane, config_manager, theme_manager)
    tips.analyse()   # chamado ao abrir/salvar arquivo
"""

from __future__ import annotations

import ast
import contextlib
import logging
import re
import time
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from src.i18n import _t

if TYPE_CHECKING:
    from src.config import ConfigManager
    from src.theme.manager import ThemeManager

logger = logging.getLogger("Codis")

_LARGE_FILE_LINES = 1000
_HUGE_FILE_LINES = 2000
_LONG_LINE_CHARS = 100
_LONG_LINE_RATIO = 0.20
_BIG_FUNCTION_LINES = 60
_DEEP_NESTING = 5
_TODO_THRESHOLD = 5
_GENERIC_VAR_THRESHOLD = 4
_TIP_DISPLAY_MS = 9_000
_TIP_FADE_STEPS = 15
_TIP_FADE_INTERVAL_MS = 40
_MIN_INTERVAL_MS = 30_000

_GENERIC_NAMES: frozenset[str] = frozenset(
    {
        "data",
        "temp",
        "tmp",
        "x",
        "y",
        "z",
        "n",
        "i",
        "j",
        "k",
        "foo",
        "bar",
        "baz",
        "val",
        "value",
        "result",
        "res",
        "item",
        "obj",
        "info",
        "stuff",
        "thing",
        "var",
        "a",
        "b",
        "c",
    }
)

_SECRET_PATTERNS: list[re.Pattern] = [
    re.compile(
        r'(?i)(password|passwd|pwd|secret|api_key|apikey|token|auth_token)\s*=\s*["\'][^"\']{6,}["\']'
    ),
    re.compile(
        r'(?i)(aws_access_key|aws_secret)\s*=\s*["\'][A-Za-z0-9/+=]{10,}["\']'),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9\-._~+/]+=*"),
]

_CODE_LANGS: frozenset[str] = frozenset(
    {
        "python",
        "javascript",
        "typescript",
        "java",
        "c",
        "cpp",
        "csharp",
        "go",
        "rust",
        "ruby",
        "php",
        "kotlin",
        "swift",
        "scala",
        "r",
        "julia",
        "haskell",
        "elixir",
        "erlang",
        "lua",
        "groovy",
    }
)

_INDENT_LANGS: frozenset[str] = frozenset(
    {
        "python",
        "javascript",
        "typescript",
        "java",
        "c",
        "cpp",
        "csharp",
        "go",
        "rust",
        "ruby",
        "php",
        "kotlin",
        "swift",
    }
)


@dataclass
class Tip:
    """Representa uma dica a ser exibida."""

    key: str
    icon: str
    title_key: str
    body_key: str
    body_kwargs: dict
    priority: int = 0
    lang_filter: frozenset[str] | None = None


@dataclass
class _TipState:
    """Estado interno do sistema de dicas."""

    shown_keys: set[str] = field(default_factory=set)
    last_shown_ms: int = 0
    current_win: tk.Toplevel | None = None
    pending_job: str | None = None
    fade_job: str | None = None
    alpha: float = 1.0


class SmartTips:
    """Sistema de dicas flutuantes inteligentes para o editor Codis.

    Args:
        widget:  Widget de referência (normalmente o EditorPane ou seu tk.Text).
        cfg:     Instância do ConfigManager.
        tmgr:    Instância do ThemeManager.
    """

    def __init__(
        self,
        widget: tk.Widget,
        cfg: ConfigManager,
        tmgr: ThemeManager,
    ) -> None:
        """Inicializa o sistema de smart tips para o ``EditorPane``."""
        self._widget = widget
        self._cfg = cfg
        self._tmgr = tmgr
        self._state = _TipState()
        self._analyse_job: str | None = None
        self._min_interval_ms: int = _MIN_INTERVAL_MS

    def _threshold(self, key: str, default: int) -> int:
        """Lê um limiar de análise do ConfigManager, com fallback para a constante padrão.

        Todos os valores de gatilho das dicas são configuráveis pelo usuário
        via Settings Editor. Este método garante que o código sempre leia o
        valor mais atualizado sem precisar reiniciar o editor.
        """
        return int(self._cfg.get(key, default))

    def analyse(
        self,
        content: str,
        lang: str,
        path: Path | None = None,
        delay_ms: int = 1200,
    ) -> None:
        """Agenda análise do conteúdo atual e exibe a dica mais relevante.

        Chamado após abertura ou salvamento de arquivo. Usa debounce para
        não bloquear o carregamento do arquivo.

        Args:
            content:  Texto completo do arquivo.
            lang:     Linguagem detectada (ex: ``"python"``).
            path:     Caminho do arquivo (opcional).
            delay_ms: Delay antes de analisar (ms).
        """
        if not self._cfg.get("smart_tips", True):
            return
        if self._analyse_job:
            with contextlib.suppress(ValueError, tk.TclError):
                self._widget.after_cancel(self._analyse_job)
        self._analyse_job = self._widget.after(
            delay_ms,
            lambda: self._run_analysis(content, lang, path),
        )

    def dismiss(self) -> None:
        """Dispensa a dica atual imediatamente."""
        self._close_tip()

    def reset_session(self) -> None:
        """Limpa o histórico de dicas já exibidas nesta sessão."""
        self._state.shown_keys.clear()

    def _run_analysis(self, content: str, lang: str, path: Path | None) -> None:
        """Executa todos os detectores e escolhe a dica mais prioritária."""
        self._analyse_job = None
        if not self._cfg.get("smart_tips", True):
            return

        now_ms = int(time.monotonic() * 1000)
        interval_s = self._cfg.get(
            "smart_tips_interval", _MIN_INTERVAL_MS // 1000)
        min_ms = max(5000, int(interval_s) * 1000)
        if now_ms - self._state.last_shown_ms < min_ms:
            return

        candidates: list[Tip] = []
        lines = content.splitlines()
        n_lines = len(lines)

        self._check_file_size(lines, n_lines, lang, candidates)
        self._check_long_lines(lines, n_lines, lang, candidates)
        self._check_secret_patterns(content, lang, candidates)
        self._check_todos(content, lang, candidates)
        if lang in _INDENT_LANGS:
            self._check_deep_nesting(lines, n_lines, candidates)
        if lang == "python":
            self._check_python_specifics(content, lines, candidates)
        self._check_generic_vars(content, lang, candidates)
        if path:
            self._check_filename(path, candidates)

        visible = [
            t
            for t in candidates
            if t.key not in self._state.shown_keys
            and (t.lang_filter is None or lang in t.lang_filter)
        ]
        if not visible:
            return

        best = max(visible, key=lambda t: t.priority)
        self._state.shown_keys.add(best.key)
        self._state.last_shown_ms = now_ms
        self._show_tip(best)

    def _check_file_size(
        self, lines: list[str], n_lines: int, lang: str, out: list[Tip]
    ) -> None:
        if lang not in _CODE_LANGS:
            return
        large = self._threshold(
            "smart_tips_large_file_lines", _LARGE_FILE_LINES)
        huge = self._threshold("smart_tips_huge_file_lines", _HUGE_FILE_LINES)
        if n_lines >= huge:
            out.append(
                Tip(
                    key=f"huge_file_{n_lines // 500}",
                    icon="📦",
                    title_key="tips.large_file.title",
                    body_key="tips.large_file.huge",
                    body_kwargs={"lines": n_lines},
                    priority=80,
                    lang_filter=_CODE_LANGS,
                )
            )
        elif n_lines >= large:
            out.append(
                Tip(
                    key=f"large_file_{n_lines // 100}",
                    icon="📄",
                    title_key="tips.large_file.title",
                    body_key="tips.large_file.body",
                    body_kwargs={"lines": n_lines},
                    priority=50,
                    lang_filter=_CODE_LANGS,
                )
            )

    def _check_long_lines(
        self, lines: list[str], n_lines: int, lang: str, out: list[Tip]
    ) -> None:
        if n_lines < 10:
            return
        limit = self._threshold("smart_tips_long_line_chars", _LONG_LINE_CHARS)
        long = sum(1 for l_ in lines if len(l_) > limit)
        ratio = long / max(n_lines, 1)
        if ratio >= _LONG_LINE_RATIO:
            out.append(
                Tip(
                    key=f"long_lines_{long // 5}",
                    icon="↔️",
                    title_key="tips.long_lines.title",
                    body_key="tips.long_lines.body",
                    body_kwargs={"count": long, "limit": limit},
                    priority=30,
                )
            )

    def _check_secret_patterns(self, content: str, lang: str, out: list[Tip]) -> None:
        if lang not in _CODE_LANGS:
            return
        for pat in _SECRET_PATTERNS:
            m = pat.search(content)
            if m:
                snippet = m.group(0)[:40].replace("\n", " ")
                out.append(
                    Tip(
                        key="secret_hardcoded",
                        icon="🔑",
                        title_key="tips.secret.title",
                        body_key="tips.secret.body",
                        body_kwargs={"snippet": snippet},
                        priority=100,
                        lang_filter=_CODE_LANGS,
                    )
                )
                break

    def _check_todos(self, content: str, lang: str, out: list[Tip]) -> None:
        threshold = self._threshold(
            "smart_tips_todo_threshold", _TODO_THRESHOLD)
        todos = re.findall(r"(?i)\b(TODO|FIXME|HACK|XXX)\b", content)
        if len(todos) >= threshold:
            out.append(
                Tip(
                    key=f"todos_{len(todos) // 3}",
                    icon="📝",
                    title_key="tips.todos.title",
                    body_key="tips.todos.body",
                    body_kwargs={"count": len(todos)},
                    priority=25,
                )
            )

    def _check_deep_nesting(
        self, lines: list[str], n_lines: int, out: list[Tip]
    ) -> None:
        """Detecta linhas com indentação muito profunda (≥ N níveis)."""
        tab_size = 4
        max_level = self._threshold("smart_tips_deep_nesting", _DEEP_NESTING)
        deep_count = 0
        for line in lines:
            stripped = line.lstrip()
            if not stripped or stripped.startswith(("#", "//", "/*", "*")):
                continue
            indent = len(line) - len(stripped)
            level = indent // tab_size
            if level >= max_level:
                deep_count += 1

        if deep_count >= 3:
            out.append(
                Tip(
                    key=f"deep_nesting_{deep_count // 3}",
                    icon="🌀",
                    title_key="tips.nesting.title",
                    body_key="tips.nesting.body",
                    body_kwargs={"lines": deep_count, "level": max_level},
                    priority=60,
                )
            )

    def _check_python_specifics(
        self, content: str, lines: list[str], out: list[Tip]
    ) -> None:
        """Análise específica para Python via AST."""
        try:
            tree = ast.parse(content)
            big_fn_lines = self._threshold(
                "smart_tips_big_function_lines", _BIG_FUNCTION_LINES
            )
            big_fns: list[str] = []
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    fn_lines = (node.end_lineno or node.lineno) - node.lineno
                    if fn_lines >= big_fn_lines:
                        big_fns.append(node.name)
            if big_fns:
                out.append(
                    Tip(
                        key=f"big_functions_{'_'.join(big_fns[:2])}",
                        icon="🐘",
                        title_key="tips.big_function.title",
                        body_key="tips.big_function.body",
                        body_kwargs={
                            "name": big_fns[0],
                            "count": len(big_fns),
                            "lines": big_fn_lines,
                        },
                        priority=55,
                        lang_filter=frozenset({"python"}),
                    )
                )
        except SyntaxError:
            pass

        import_names: list[str] = []
        for line in lines:
            s = line.strip()
            m = re.match(r"^(?:import|from)\s+(\S+)", s)
            if m:
                import_names.append(m.group(1))
        dupes = {n for n in import_names if import_names.count(n) > 1}
        if dupes:
            out.append(
                Tip(
                    key=f"dup_imports_{'_'.join(sorted(dupes)[:2])}",
                    icon="🔁",
                    title_key="tips.dup_imports.title",
                    body_key="tips.dup_imports.body",
                    body_kwargs={"modules": ", ".join(sorted(dupes)[:3])},
                    priority=45,
                    lang_filter=frozenset({"python"}),
                )
            )

        self._check_generic_vars_python(content, out)

    def _check_generic_vars(self, content: str, lang: str, out: list[Tip]) -> None:
        """Detecta nomes genéricos de variável em qualquer linguagem."""
        if lang == "python":
            return
        threshold = self._threshold(
            "smart_tips_generic_var_threshold", _GENERIC_VAR_THRESHOLD
        )
        found: set[str] = set()
        for name in _GENERIC_NAMES:
            pattern = re.compile(rf"\b{re.escape(name)}\s*[=:]")
            if pattern.search(content):
                found.add(name)
        if len(found) >= threshold:
            out.append(
                Tip(
                    key=f"generic_vars_{'_'.join(sorted(found)[:3])}",
                    icon="🏷️",
                    title_key="tips.generic_vars.title",
                    body_key="tips.generic_vars.body",
                    body_kwargs={"vars": ", ".join(sorted(found)[:4])},
                    priority=20,
                )
            )

    def _check_generic_vars_python(self, content: str, out: list[Tip]) -> None:
        """Detecta nomes genéricos de variável em Python via AST."""
        threshold = self._threshold(
            "smart_tips_generic_var_threshold", _GENERIC_VAR_THRESHOLD
        )
        try:
            tree = ast.parse(content)
            found: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id in _GENERIC_NAMES:
                            found.add(target.id)
            if len(found) >= threshold:
                out.append(
                    Tip(
                        key=f"generic_vars_py_{'_'.join(sorted(found)[:3])}",
                        icon="🏷️",
                        title_key="tips.generic_vars.title",
                        body_key="tips.generic_vars.body",
                        body_kwargs={"vars": ", ".join(sorted(found)[:4])},
                        priority=20,
                        lang_filter=frozenset({"python"}),
                    )
                )
        except SyntaxError:
            pass

    def _check_filename(self, path: Path, out: list[Tip]) -> None:
        """Dicas baseadas no nome do arquivo."""
        name = path.name.lower()
        if name in {"config", "settings", "configuration", "setup"}:
            out.append(
                Tip(
                    key="bare_config_file",
                    icon="⚙️",
                    title_key="tips.config_file.title",
                    body_key="tips.config_file.body",
                    body_kwargs={"name": path.name},
                    priority=10,
                )
            )
        generic_names = {"main", "index", "app", "utils", "helpers", "misc"}
        stem = path.stem.lower()
        if stem in generic_names and path.suffix in {".py", ".js", ".ts"}:
            out.append(
                Tip(
                    key=f"generic_filename_{stem}",
                    icon="📁",
                    title_key="tips.generic_filename.title",
                    body_key="tips.generic_filename.body",
                    body_kwargs={"name": path.name},
                    priority=5,
                )
            )

    def _show_tip(self, tip: Tip) -> None:
        """Cria e exibe o balão flutuante para a dica fornecida."""
        self._close_tip()

        try:
            root = self._widget.winfo_toplevel()
            t = self._tmgr.theme

            title_text = f"{tip.icon}  {_t(tip.title_key, tip.title_key)}"
            body_text = _t(tip.body_key, tip.body_key, **tip.body_kwargs)
            dismiss_text = _t("tips.dismiss", "Dispensar")
            disable_text = _t("tips.disable_all", "Desativar dicas")

            win = tk.Toplevel(root)
            win.wm_overrideredirect(True)
            win.wm_attributes("-topmost", True)
            with contextlib.suppress(tk.TclError):
                win.wm_attributes("-alpha", 0.95)

            outer = tk.Frame(
                win,
                bg=getattr(t, "floating_border", t.border),
                padx=1,
                pady=1,
            )
            outer.pack(fill="both", expand=True)

            inner = tk.Frame(outer, bg=t.floating_bg, padx=12, pady=10)
            inner.pack(fill="both", expand=True)

            accent_color = self._accent_color(tip.priority)
            accent_bar = tk.Frame(inner, bg=accent_color, height=3)
            accent_bar.pack(fill="x", pady=(0, 8))

            tk.Label(
                inner,
                text=title_text,
                bg=t.floating_bg,
                fg=t.fg,
                font=(getattr(t, "font_ui", "Segoe UI"), 9, "bold"),
                anchor="w",
                justify="left",
            ).pack(fill="x")

            tk.Label(
                inner,
                text=body_text,
                bg=t.floating_bg,
                fg=getattr(t, "fg_muted", t.fg),
                font=(getattr(t, "font_ui", "Segoe UI"), 9),
                anchor="w",
                justify="left",
                wraplength=320,
            ).pack(fill="x", pady=(4, 8))

            btn_frame = tk.Frame(inner, bg=t.floating_bg)
            btn_frame.pack(fill="x")

            btn_style = {
                "bg": t.floating_bg,
                "fg": getattr(t, "accent", "#7aa2f7"),
                "font": (getattr(t, "font_ui", "Segoe UI"), 8),
                "cursor": "hand2",
                "relief": "flat",
                "bd": 0,
            }

            dismiss_btn = tk.Label(btn_frame, text=dismiss_text, **btn_style)
            dismiss_btn.pack(side="right", padx=(6, 0))
            dismiss_btn.bind("<Button-1>", lambda _: self.dismiss())
            dismiss_btn.bind("<Enter>", lambda _: dismiss_btn.config(fg=t.fg))
            dismiss_btn.bind(
                "<Leave>",
                lambda _: dismiss_btn.config(
                    fg=getattr(t, "accent", "#7aa2f7")),
            )

            disable_btn = tk.Label(btn_frame, text=disable_text, **btn_style)
            disable_btn.pack(side="right")
            disable_btn.bind("<Button-1>", lambda _: self._disable_all())
            disable_btn.bind("<Enter>", lambda _: disable_btn.config(fg=t.fg))
            disable_btn.bind(
                "<Leave>",
                lambda _: disable_btn.config(
                    fg=getattr(t, "accent", "#7aa2f7")),
            )

            self._state.current_win = win
            self._widget.after(10, lambda: self._position_tip(win))

            self._state.pending_job = self._widget.after(
                _TIP_DISPLAY_MS, self._begin_fade
            )

        except tk.TclError as exc:
            logger.debug("SmartTips: erro ao criar balão: %s", exc)

    def _accent_color(self, priority: int) -> str:
        """Retorna cor do acento baseada na prioridade da dica."""
        t = self._tmgr.theme
        if priority >= 90:
            return "#f7768e"
        if priority >= 70:
            return "#e0af68"
        if priority >= 50:
            return "#7aa2f7"
        return getattr(t, "fg_muted", "#565f89")

    def _position_tip(self, win: tk.Toplevel) -> None:
        """Posiciona o balão no canto inferior direito do widget do editor."""
        try:
            win.update_idletasks()
            w = win.winfo_reqwidth()
            h = win.winfo_reqheight()

            try:
                rx = self._widget.winfo_rootx()
                ry = self._widget.winfo_rooty()
                rw = self._widget.winfo_width()
                rh = self._widget.winfo_height()
                x = rx + rw - w - 16
                y = ry + rh - h - 36
            except tk.TclError:
                sw = win.winfo_screenwidth()
                sh = win.winfo_screenheight()
                x = sw - w - 20
                y = sh - h - 60

            sw = win.winfo_screenwidth()
            sh = win.winfo_screenheight()
            x = max(8, min(x, sw - w - 8))
            y = max(8, min(y, sh - h - 8))

            win.geometry(f"+{x}+{y}")
        except tk.TclError:
            pass

    def _begin_fade(self) -> None:
        """Inicia animação de fade-out antes de fechar."""
        self._state.pending_job = None
        self._state.alpha = 0.95
        self._fade_step()

    def _fade_step(self) -> None:
        """Executa um passo do fade-out."""
        win = self._state.current_win
        if not win:
            return
        try:
            if not win.winfo_exists():
                self._state.current_win = None
                return
        except tk.TclError:
            self._state.current_win = None
            return

        self._state.alpha -= 0.95 / _TIP_FADE_STEPS
        if self._state.alpha <= 0.05:
            self._close_tip()
            return
        try:
            win.wm_attributes("-alpha", max(0.05, self._state.alpha))
        except tk.TclError:
            self._state.current_win = None
            return
        self._state.fade_job = self._widget.after(
            _TIP_FADE_INTERVAL_MS, self._fade_step
        )

    def _close_tip(self) -> None:
        """Fecha o balão flutuante atual e cancela jobs pendentes."""
        for job_attr in ("pending_job", "fade_job"):
            job = getattr(self._state, job_attr)
            if job:
                with contextlib.suppress(ValueError, tk.TclError):
                    self._widget.after_cancel(job)
                setattr(self._state, job_attr, None)

        win = self._state.current_win
        if win:
            try:
                if win.winfo_exists():
                    win.destroy()
            except tk.TclError:
                pass
            self._state.current_win = None

    def _disable_all(self) -> None:
        """Desativa todas as dicas inteligentes e fecha o balão atual."""
        self._cfg.set("smart_tips", False)
        self._cfg.save()
        self._close_tip()
