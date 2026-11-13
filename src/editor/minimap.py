"""Codis - MiniMap: mapa em miniatura do conteúdo do editor.

Renderiza tokens reais por linha usando tokenizador regex simples,
com:
  - Segmentos coloridos por tipo de token (keyword, string, número, comentário)
  - Destaque da linha do cursor
  - Indicador de viewport arrastável
  - Marcadores de lint e resultados de busca na borda direita
  - Redimensionamento e escala automáticos

Performance:
  - Renderização incremental: apenas linhas *dirty* são re-tokenizadas/redesenhadas
  - Zero delete("all"): o canvas é mantido entre frames; items são movidos/recolorados
  - Tags numéricas por linha: cada linha tem seus próprios canvas-items reusáveis
  - Color cache por tema: _build_color_cache só recalcula quando o tema muda
  - Dirty-line tracking por hash de string: só re-tokeniza linhas modificadas
  - Debounce inteligente: 80ms para conteúdo, 16ms para viewport (≈60fps)
  - Viewport rectangle: coordenadas atualizadas com coords() - sem recriação
  - LRU simples no token cache para limitar memória
"""

from __future__ import annotations

import contextlib
import logging
import re
import tkinter as tk
from typing import Any

from src.constants import MINIMAP_WIDTH
from src.lint.manager import Diag
from src.theme.utils import alpha_blend_hex

logger = logging.getLogger("Codis")

_RE_COMMENT_LINE = re.compile(r"^\s*(#|//|--|/\*|\*)")
_RE_STR_DOUBLE = re.compile(r'f?"(?:[^"\\\\]|\\\\.)*"')
_RE_STR_SINGLE = re.compile(r"f?'(?:[^'\\\\]|\\\\.)*'")
_RE_STR_BACK = re.compile(r"`(?:[^`\\\\]|\\\\.)*`")
_RE_NUMBER = re.compile(
    r"\b(?:0x[0-9a-fA-F]+|0b[01]+|0o[0-7]+|\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)\b"
)
_RE_DECORATOR = re.compile(r"@[A-Za-z_]\w*")
_RE_FUNC_DEF = re.compile(
    r"\b(?:def|function|fn|func|sub|constructor|method)\s+([A-Za-z_]\w*)",
)
_RE_CLASS_DEF = re.compile(
    r"\b(?:class|struct|interface|trait|enum|type|protocol)\s+([A-Za-z_]\w*)",
)
_RE_WORD = re.compile(r"\b[A-Za-z_]\w*\b")

_KEYWORDS: frozenset[str] = frozenset(
    {
        "and",
        "as",
        "assert",
        "async",
        "await",
        "break",
        "class",
        "continue",
        "def",
        "del",
        "elif",
        "else",
        "except",
        "finally",
        "for",
        "from",
        "global",
        "if",
        "import",
        "in",
        "is",
        "lambda",
        "nonlocal",
        "not",
        "or",
        "pass",
        "raise",
        "return",
        "try",
        "while",
        "with",
        "yield",
        "True",
        "False",
        "None",
        "self",
        "cls",
        "const",
        "let",
        "var",
        "function",
        "new",
        "this",
        "typeof",
        "instanceof",
        "export",
        "default",
        "extends",
        "implements",
        "interface",
        "enum",
        "type",
        "abstract",
        "override",
        "readonly",
        "public",
        "private",
        "protected",
        "static",
        "switch",
        "case",
        "throw",
        "catch",
        "of",
        "fn",
        "pub",
        "use",
        "mod",
        "impl",
        "trait",
        "struct",
        "match",
        "mut",
        "ref",
        "where",
        "crate",
        "dyn",
        "loop",
        "move",
        "unsafe",
        "extern",
        "package",
        "func",
        "go",
        "chan",
        "defer",
        "range",
        "select",
        "val",
        "fun",
        "when",
        "then",
        "unless",
        "until",
        "rescue",
        "ensure",
        "require",
        "include",
        "module",
        "proc",
        "begin",
        "end",
        "nil",
        "true",
        "false",
        "null",
        "undefined",
        "void",
        "bool",
        "int",
        "float",
        "string",
        "char",
        "byte",
        "rune",
        "any",
        "object",
        "unit",
        "never",
        "unknown",
    }
)


class MiniMap(tk.Canvas):
    """
    Mapa em miniatura do arquivo exibido à direita do editor.

    Mantém canvas-items persistentes por linha e só recria/move o que mudou,
    eliminando o flickering causado por delete("all") + redraw completo.
    """

    CHAR_W = 2
    LINE_H = 2
    MAX_CHARS = 120
    PADDING = 3
    GUTTER_W = 4

    def __init__(self, parent: tk.Widget, text_widget: tk.Text, tmgr) -> None:
        super().__init__(parent, width=MINIMAP_WIDTH, bd=0, highlightthickness=0)
        self._txt_ref = text_widget
        self._tmgr = tmgr
        self._suspended = False

        self._job: str | None = None
        self._vp_job: str | None = None
        self._drag_y: int | None = None

        self._lint_lines: dict[int, str] = {}
        self._search_lines: set[int] = set()

        self._token_cache: dict[int, tuple[int, list[str]]] = {}
        self._TOKEN_CACHE_MAX = 3000

        self._line_items: dict[int, list[int]] = {}

        self._bg_item: int | None = None
        self._cursor_items: dict[int, int] = {}
        self._gutter_items: dict[int, int] = {}
        self._vp_item: int | None = None

        self._color_cache: dict[str, str] = {}
        self._color_cache_theme_id: int = -1

        self._prev_lines: list[str] = []
        self._prev_total: int = 0
        self._prev_cursor_ln: int = -1
        self._prev_first_vis: float = -1.0
        self._prev_last_vis: float = -1.0
        self._prev_cw: int = 0
        self._prev_ch: int = 0
        self._prev_lh: int = 0

        self._apply_theme()
        self.bind("<Button-1>", self._on_click)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>",
                  lambda _: setattr(self, "_drag_y", None))
        self.bind("<Configure>", self._on_resize)

        for ev in ("<MouseWheel>", "<Configure>"):
            with contextlib.suppress(tk.TclError):
                self._txt_ref.bind(ev, self._schedule_viewport_update, add="+")

        for ev in ("<<Modified>>", "<KeyRelease>"):
            with contextlib.suppress(tk.TclError):
                self._txt_ref.bind(ev, self._schedule_redraw, add="+")

    def _apply_theme(self) -> None:
        t = self._tmgr.theme
        self.config(bg=t.minimap_bg)

    def update_theme(self) -> None:
        self._apply_theme()
        self._color_cache_theme_id = -1
        self._token_cache.clear()
        self._prev_lines = []
        self._invalidate_all_canvas_items()
        self._schedule_redraw()

    def _get_colors(self) -> dict[str, str]:
        """Retorna cache de cores, recalculando apenas quando o tema mudou."""
        t = self._tmgr.theme
        tid = id(t)
        if tid != self._color_cache_theme_id:
            vp = getattr(t, "minimap_viewport", t.sel_bg)
            bg = getattr(t, "minimap_bg", t.editor_bg)
            self._color_cache = {
                "bg": bg,
                "viewport": vp,
                "vp_borda": getattr(t, "scroll_hover", t.accent),
                "cursor_hl": _alpha_blend(vp, bg, 0.5),
                "comment": getattr(t, "t_comment", "#6a9955"),
                "string": getattr(t, "t_string", "#a6d4a6"),
                "number": getattr(t, "t_number", "#e8a07a"),
                "keyword": getattr(t, "t_keyword", "#9b9bef"),
                "func": getattr(t, "t_func", "#78b4fa"),
                "klass": getattr(t, "t_type", "#f38b8b"),
                "decorator": getattr(t, "t_decorator", "#f9e2af"),
                "normal": getattr(t, "fg_dim", "#4a4a6a"),
                "ws": bg,
            }
            self._color_cache_theme_id = tid
        return self._color_cache

    def _schedule_redraw(self, _e: Any = None) -> None:
        if not self.winfo_exists() or self._suspended:
            return
        if self._job:
            with contextlib.suppress(tk.TclError):
                self.after_cancel(self._job)
        self._job = self.after(80, self._incremental_redraw)

    def _schedule_viewport_update(self, _e: Any = None) -> None:
        if not self.winfo_exists() or self._suspended:
            return
        if self._vp_job:
            with contextlib.suppress(tk.TclError):
                self.after_cancel(self._vp_job)
        self._vp_job = self.after(16, self._update_viewport)

    def _on_resize(self, _e: Any = None) -> None:
        """Resize força redraw completo pois lh e escala mudam."""
        if not self.winfo_exists() or self._suspended:
            return
        self._prev_cw = 0
        self._schedule_redraw()

    def suspend(self) -> None:
        self._suspended = True
        for job_attr in ("_job", "_vp_job"):
            job = getattr(self, job_attr, None)
            if job:
                with contextlib.suppress(tk.TclError):
                    self.after_cancel(job)
                setattr(self, job_attr, None)

    def resume(self) -> None:
        self._suspended = False
        self._schedule_redraw()

    def set_lint_lines(self, diags: list[Diag]) -> None:
        self._lint_lines = {}
        for d in diags:
            color = {
                1: self._tmgr.theme.minimap_err,
                2: self._tmgr.theme.minimap_warn,
            }.get(d.severity)
            if color:
                self._lint_lines[d.line] = color
        self._schedule_redraw()

    def _invalidate_all_canvas_items(self) -> None:
        """Remove todos os canvas-items gerenciados (sem delete("all"))."""
        with contextlib.suppress(tk.TclError):
            for items in self._line_items.values():
                for iid in items:
                    self.delete(iid)
            for iid in self._cursor_items.values():
                self.delete(iid)
            for iid in self._gutter_items.values():
                self.delete(iid)
            if self._bg_item is not None:
                self.delete(self._bg_item)
            if self._vp_item is not None:
                self.delete(self._vp_item)
        self._line_items.clear()
        self._cursor_items.clear()
        self._gutter_items.clear()
        self._bg_item = None
        self._vp_item = None

    def _incremental_redraw(self, _e: Any = None) -> None:
        """
        Redesenho incremental: detecta dirty lines e só re-renderiza o necessário.

        Não usa delete("all") - mantém items existentes e os atualiza.
        Elimina o flickering completamente.
        """
        self._job = None
        if not self.winfo_exists():
            return

        color = self._get_colors()
        cw = self.winfo_width()
        ch = self.winfo_height()
        if cw < 6 or ch < 6:
            return

        try:
            content = self._txt_ref.get("1.0", "end-1c")
            first_vis = float(self._txt_ref.yview()[0])
            last_vis = float(self._txt_ref.yview()[1])
            cursor_ln = int(self._txt_ref.index("insert").split(".")[0])
        except tk.TclError:
            return

        lines = content.split("\n")
        total_linhas = max(len(lines), 1)
        espaco = ch - self.PADDING * 2
        lh = max(1, min(self.LINE_H, espaco // total_linhas))
        util_w = cw - self.GUTTER_W - self.PADDING * 2

        geometry_changed = (
            cw != self._prev_cw or ch != self._prev_ch or lh != self._prev_lh
        )
        if geometry_changed:
            self._invalidate_all_canvas_items()
            self._prev_lines = []
            self._prev_cursor_ln = -1

        if self._bg_item is None:
            self._bg_item = self.create_rectangle(
                0, 0, cw, ch, fill=color["bg"], outline="", tags="bg"
            )
            self.tag_lower("bg")
        else:
            with contextlib.suppress(tk.TclError):
                self.coords(self._bg_item, 0, 0, cw, ch)
                self.itemconfig(self._bg_item, fill=color["bg"])

        prev = self._prev_lines
        dirty: set[int] = set()

        if len(lines) != len(prev):
            dirty = set(range(1, total_linhas + 1))
            for n in list(self._line_items):
                if n > total_linhas:
                    for iid in self._line_items.pop(n):
                        with contextlib.suppress(tk.TclError):
                            self.delete(iid)
            for n in list(self._cursor_items):
                if n > total_linhas:
                    with contextlib.suppress(tk.TclError):
                        self.delete(self._cursor_items.pop(n))
            for n in list(self._gutter_items):
                if n > total_linhas:
                    with contextlib.suppress(tk.TclError):
                        self.delete(self._gutter_items.pop(n))
        else:
            for i, (cur, prv) in enumerate(zip(lines, prev, strict=False)):
                if cur != prv:
                    dirty.add(i + 1)

        if cursor_ln != self._prev_cursor_ln:
            dirty.add(self._prev_cursor_ln)
            dirty.add(cursor_ln)

        self._prev_lines = lines
        self._prev_total = total_linhas
        self._prev_cursor_ln = cursor_ln
        self._prev_cw = cw
        self._prev_ch = ch
        self._prev_lh = lh

        for i, linha_texto in enumerate(lines):
            y = self.PADDING + i * lh
            n_linha = i + 1
            if y > ch:
                if n_linha in self._line_items:
                    for iid in self._line_items.pop(n_linha):
                        with contextlib.suppress(tk.TclError):
                            self.delete(iid)
                continue

            if n_linha not in dirty:
                continue

            gutter_color = self._lint_lines.get(n_linha) or (
                self._tmgr.theme.minimap_find if n_linha in self._search_lines else None
            )
            if gutter_color:
                if n_linha in self._gutter_items:
                    with contextlib.suppress(tk.TclError):
                        self.coords(
                            self._gutter_items[n_linha],
                            cw - self.GUTTER_W,
                            y,
                            cw,
                            y + max(1, lh),
                        )
                        self.itemconfig(
                            self._gutter_items[n_linha], fill=gutter_color)
                else:
                    self._gutter_items[n_linha] = self.create_rectangle(
                        cw - self.GUTTER_W,
                        y,
                        cw,
                        y + max(1, lh),
                        fill=gutter_color,
                        outline="",
                    )
            else:
                if n_linha in self._gutter_items:
                    with contextlib.suppress(tk.TclError):
                        self.delete(self._gutter_items.pop(n_linha))

            if n_linha == cursor_ln:
                hl_color = color["cursor_hl"]
                if n_linha in self._cursor_items:
                    with contextlib.suppress(tk.TclError):
                        self.coords(
                            self._cursor_items[n_linha],
                            0,
                            y,
                            cw - self.GUTTER_W,
                            y + max(2, lh),
                        )
                        self.itemconfig(
                            self._cursor_items[n_linha], fill=hl_color)
                else:
                    self._cursor_items[n_linha] = self.create_rectangle(
                        0,
                        y,
                        cw - self.GUTTER_W,
                        y + max(2, lh),
                        fill=hl_color,
                        outline="",
                    )
            else:
                if n_linha in self._cursor_items:
                    with contextlib.suppress(tk.TclError):
                        self.delete(self._cursor_items.pop(n_linha))

            if n_linha in self._line_items:
                for iid in self._line_items.pop(n_linha):
                    with contextlib.suppress(tk.TclError):
                        self.delete(iid)

            if not linha_texto.strip():
                self._line_items[n_linha] = []
                continue

            colors = self._tokenize(linha_texto, n_linha, color)

            new_items = self._draw_linha(
                colors, linha_texto, y, lh, util_w, cw, color)
            self._line_items[n_linha] = new_items

        self._draw_or_update_viewport(
            first_vis, last_vis, cw, ch, total_linhas, lh, color
        )

        self._prev_first_vis = first_vis
        self._prev_last_vis = last_vis

    def _update_viewport(self) -> None:
        """Atualização ultra-leve: apenas reposiciona o retângulo de viewport."""
        self._vp_job = None
        if not self.winfo_exists():
            return
        try:
            first_vis = float(self._txt_ref.yview()[0])
            last_vis = float(self._txt_ref.yview()[1])
        except tk.TclError:
            return

        cw = self.winfo_width()
        ch = self.winfo_height()
        if cw < 6 or ch < 6:
            return

        if self._bg_item is None:
            self._schedule_redraw()
            return

        try:
            total_linhas = max(self._prev_total, 1)
        except tk.TclError:
            return

        espaco = ch - self.PADDING * 2
        lh = max(1, min(self.LINE_H, espaco // total_linhas))
        color = self._get_colors()

        self._draw_or_update_viewport(
            first_vis, last_vis, cw, ch, total_linhas, lh, color
        )
        self._prev_first_vis = first_vis
        self._prev_last_vis = last_vis

    def _draw_or_update_viewport(
        self,
        first_vis: float,
        last_vis: float,
        cw: int,
        ch: int,
        total_linhas: int,
        lh: int,
        color: dict[str, str],
    ) -> None:
        content_h = min(ch, self.PADDING + total_linhas * lh)
        vy1 = max(0, int(first_vis * content_h))
        vy2 = min(content_h, int(last_vis * content_h))
        vy2 = max(vy2, vy1 + 4)

        with contextlib.suppress(tk.TclError):
            if self._vp_item is not None and self.find_withtag(self._vp_item):
                self.coords(self._vp_item, 0, vy1, cw - self.GUTTER_W, vy2)
                self.itemconfig(
                    self._vp_item,
                    fill=color["viewport"],
                    stipple="gray50",
                    outline=color["vp_borda"],
                )
            else:
                self._vp_item = self.create_rectangle(
                    0,
                    vy1,
                    cw - self.GUTTER_W,
                    vy2,
                    fill=color["viewport"],
                    outline=color["vp_borda"],
                    width=1,
                    stipple="gray50",
                    tags="viewport",
                )
            self.tag_raise(self._vp_item)

    def _tokenize(
        self,
        line: str,
        n_linha: int,
        color: dict[str, str],
    ) -> list[str]:
        """Tokeniza uma linha e retorna lista de cores por caractere, com cache.

        Usa slice-assignment em vez de loops por caractere para minimizar
        o custo de atribuição em linhas longas.
        """
        n = min(len(line), self.MAX_CHARS)
        lhash = hash(line)

        cached = self._token_cache.get(n_linha)
        if cached and cached[0] == lhash:
            return cached[1]

        norm_color = color["normal"]
        colors = [norm_color] * n

        if _RE_COMMENT_LINE.match(line):
            c = color["comment"]
            colors[:] = [c] * n
        else:
            str_color = color["string"]
            for regex in (_RE_STR_DOUBLE, _RE_STR_SINGLE, _RE_STR_BACK):
                for m in regex.finditer(line[:n]):
                    a, b = m.start(), min(m.end(), n)
                    colors[a:b] = [str_color] * (b - a)

            num_color = color["number"]
            for m in _RE_NUMBER.finditer(line[:n]):
                a, b = m.start(), min(m.end(), n)
                if colors[a] != str_color:
                    colors[a:b] = [num_color] * (b - a)

            dec_color = color["decorator"]
            for m in _RE_DECORATOR.finditer(line[:n]):
                a, b = m.start(), min(m.end(), n)
                if colors[a] == norm_color:
                    colors[a:b] = [dec_color] * (b - a)

            fn_color = color["func"]
            for m in _RE_FUNC_DEF.finditer(line[:n]):
                a, b = m.start(), min(m.end(), n)
                if colors[a] == norm_color:
                    colors[a:b] = [fn_color] * (b - a)

            cls_color = color["klass"]
            for m in _RE_CLASS_DEF.finditer(line[:n]):
                a, b = m.start(), min(m.end(), n)
                if colors[a] == norm_color:
                    colors[a:b] = [cls_color] * (b - a)

            kw_color = color["keyword"]
            for m in _RE_WORD.finditer(line[:n]):
                if m.group() in _KEYWORDS:
                    a, b = m.start(), min(m.end(), n)
                    if colors[a] == norm_color:
                        colors[a:b] = [kw_color] * (b - a)

        ws_color = color["ws"]
        for k, ch_char in enumerate(line[:n]):
            if ch_char in (" ", "\t"):
                colors[k] = ws_color

        if len(self._token_cache) >= self._TOKEN_CACHE_MAX:
            oldest = next(iter(self._token_cache))
            del self._token_cache[oldest]
        self._token_cache[n_linha] = (lhash, colors)
        return colors

    def _draw_linha(
        self,
        colors: list[str],
        line: str,
        y: int,
        lh: int,
        util_w: int,
        cw: int,
        color: dict[str, str],
    ) -> list[int]:
        """Desenha runs de mesma cor para uma linha; retorna lista de item-ids."""
        n = min(len(line), self.MAX_CHARS)
        height = max(1, lh - 1)
        scale = util_w / max(self.MAX_CHARS * self.CHAR_W, 1)
        ws_c = color["ws"]
        items: list[int] = []

        k = 0
        while k < n:
            c_cur = colors[k]
            start = k
            while k < n and colors[k] == c_cur:
                k += 1
            if c_cur == ws_c:
                continue
            x1 = self.PADDING + int(start * self.CHAR_W * scale)
            x2 = self.PADDING + int(k * self.CHAR_W * scale)
            x2 = min(x2, cw - self.GUTTER_W - 2)
            if x2 <= x1:
                x2 = x1 + 1
            iid = self.create_rectangle(
                x1, y, x2, y + height, fill=c_cur, outline="")
            items.append(iid)

        return items

    def redraw(self, _e: Any = None) -> None:
        """Alias público; delega para incremental_redraw."""
        self._prev_lines = []
        self._invalidate_all_canvas_items()
        self._schedule_redraw()

    def _on_click(self, e: Any) -> None:
        self._drag_y = e.y
        self._scroll_to(e.y)

    def _on_drag(self, e: Any) -> None:
        self._scroll_to(e.y)
        self._drag_y = e.y

    def _scroll_to(self, y: int) -> None:
        ch = self.winfo_height()
        if ch <= 0:
            return
        frac = max(0.0, min(1.0, y / ch))
        with contextlib.suppress(tk.TclError):
            self._txt_ref.yview_moveto(frac)

_alpha_blend = alpha_blend_hex
