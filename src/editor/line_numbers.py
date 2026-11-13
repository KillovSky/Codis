"""Codis - LineNumbers (Gutter).

Princípio de layout (simples e correto):
  - A barra tem EXATAMENTE a largura necessária para caber o maior número
    possível + espaço para o breakpoint à esquerda e fold icon à direita.
  - O número é renderizado com anchor="w" a partir de x=_ZONE_BP (borda
    esquerda da zona numérica), crescendo para a DIREITA.
  - O frame pai NÃO tem width fixo nem pack_propagate(False) -
    o canvas controla sua própria largura via config(width=...).
  - Resize manual: arrastar a borda direita do canvas redimensiona o frame
    pai via _gutter_frame.config(width=...) com pack_propagate(False)
    temporário, devolvendo o controle ao canvas no reset.

Layout de zonas (esquerda -> direita):
  [_ZONE_BP px] [número left-aligned] [_ZONE_PAD px] [_ZONE_FOLD px] [_ZONE_LINT px]
"""

from __future__ import annotations

import contextlib
import logging
import tkinter as tk
import tkinter.font as tkfont
from collections.abc import Callable
from typing import Any

from src.constants import FONT_MONO, FONT_SIZE
from src.editor.kdocs import KDocsNotes, KDocsParser
from src.lint.manager import Diag
from src.theme.base import Theme

logger = logging.getLogger("Codis")

_ZONE_BP = 14
_ZONE_PAD = 6
_ZONE_FOLD = 14
_ZONE_LINT = 8


class LineNumbers(tk.Canvas):
    """Widget de gutter: números de linha, breakpoints, ícones de fold e dots de lint.

    Renderizado como ``tk.Canvas`` dimensionado para sempre caber o número de
    linha mais largo, mais zonas fixas para breakpoints (esquerda), ícones de fold
    e dots de lint (direita). Suporta redimensionamento manual arrastando a borda direita.
    """

    BP_RADIUS = 5
    _MIN_W = 40
    _MAX_W = 240

    def __init__(
        self,
        parent: tk.Widget,
        text_widget: tk.Text,
        theme: Theme,
        **kw,
    ) -> None:
        super().__init__(parent, bd=0, highlightthickness=0, **kw)

        self._txt = text_widget
        self._theme = theme
        self._font = (FONT_MONO, FONT_SIZE)
        self._after_id: str | None = None

        self._current_line = 1
        self._breakpoints: set[int] = set()
        self._lint_lines: dict[int, str] = {}
        self._kdocs_lines: dict[int, str] = {}
        self._marked_lines: set[int] = set()
        self._fold_map: dict[int, bool] = {}
        self._hover_line: int | None = None

        self._on_fold_toggle: Callable[[int], None] | None = None
        self._on_marked_change: Callable[[set[int]], None] | None = None
        self._on_breakpoint_toggle: Callable[[set[int]], None] | None = None

        self._line_geo: dict[int, tuple[int, int]] = {}

        self._desired_w: int = self._MIN_W

        self._resize_x: int | None = None
        self._manual_width: int | None = None

        self._redrawing: bool = False

        self._apply_theme(theme)
        self._update_width()

        for ev in (
            "<KeyRelease>",
            "<ButtonRelease-1>",
            "<MouseWheel>",
            "<Configure>",
            "<<Change>>",
            "<<Modified>>",
            "<FocusIn>",
        ):
            with contextlib.suppress(tk.TclError):
                self._txt.bind(ev, self._schedule_redraw, add="+")

        self.bind("<Configure>", self._schedule_redraw)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._resize_drag)
        self.bind("<ButtonRelease-1>", self._resize_end)
        self.bind("<Double-Button-1>", self._resize_reset)
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", self._on_leave)

    def _apply_theme(self, t: Theme) -> None:
        """Armazena o tema e atualiza a cor de fundo do canvas."""
        self._theme = t
        self.config(bg=t.gutter_bg)

    def update_theme(self, t: Theme) -> None:
        """Aplica um novo tema e força um redesenho imediato."""
        self._apply_theme(t)
        self.redraw()

    def update_font(self, family: str, size: int) -> None:
        """Atualiza a fonte usada nos números de linha e recalcula a largura do gutter.

        Args:
            family: Nome da família de fonte (ex.: ``"JetBrains Mono"``).
            size:   Tamanho da fonte em pontos.
        """
        self._font = family, size
        self._update_width()
        self.redraw()

    def _measure_num_width(self, digits: int) -> int:
        """Retorna a largura em pixels de um número com ``digits`` dígitos na fonte atual.

        Usa ``"8"`` repetido, que costuma ser o dígito mais largo em fontes monoespaçadas.
        Retorna ``digits * 9`` como fallback se a fonte não puder ser medida.
        """
        try:
            fnt = tkfont.Font(family=self._font[0], size=self._font[1])
            return fnt.measure("8" * digits)
        except Exception:
            return digits * 9

    def _calc_desired_w(self, total_lines: int) -> int:
        """Calcula a largura ideal do canvas para caber ``total_lines`` sem corte.

        Layout: ``[BP] [número] [PAD] [FOLD] [LINT]``, limitado a ``[_MIN_W, _MAX_W]``.

        Args:
            total_lines: Número atual de linhas do widget de texto.

        Returns:
            Largura alvo em pixels.
        """
        digits = max(len(str(total_lines)), 1)
        num_w = self._measure_num_width(digits)
        w = _ZONE_BP + num_w + _ZONE_PAD + _ZONE_FOLD + _ZONE_LINT
        return max(self._MIN_W, min(w, self._MAX_W))

    def _update_width(self) -> None:
        """Recalcula e aplica a largura do canvas, propagando ao frame pai.

        Ignora a atualização se a largura alvo já coincide com ``_desired_w``.
        No modo de redimensionamento manual, usa ``_manual_width`` em vez do valor calculado.
        """
        if self._manual_width is not None:
            target = self._manual_width
        else:
            try:
                total = max(1, int(self._txt.index("end").split(".")[0]) - 1)
            except Exception:
                total = 1
            target = self._calc_desired_w(total)

        if target == self._desired_w:
            return

        self._desired_w = target
        self.config(width=target)

        parent = self.master
        if parent and parent.winfo_exists():
            with contextlib.suppress(Exception):
                parent.config(width=target)

    def _schedule_redraw(self, e: Any = None) -> None:
        """Debounce adaptativo: 16ms para scroll/configure (suave), 50ms para conteudo.

        Scroll chama este método em alta frequência - debounce de 16ms (~60fps)
        mantém o gutter sincronizado com o editor sem sobrecarregar o event loop.
        Mudanças de conteúdo usam 50ms para agrupar keystroke rápidos.
        """
        if not self.winfo_exists():
            return
        if self._redrawing:
            try:
                if str(e.type) in ("22", "Configure"):
                    return
            except Exception:
                logger.debug(
                    "Falha ao checar tipo de evento em redraw.", exc_info=True)
        try:
            etype = str(e.type) if e is not None else ""
            delay = (
                16
                if etype
                in ("38", "22", "5", "MouseWheel", "Configure", "ButtonRelease")
                else 50
            )
        except Exception:
            delay = 50
        if self._after_id:
            with contextlib.suppress(tk.TclError):
                self.after_cancel(self._after_id)
        self._after_id = self.after(delay, self.redraw)

    def redraw(self, _e: Any = None) -> None:
        """Redesenha o gutter inteiro, protegido contra chamadas reentrantes."""
        if not self.winfo_exists():
            return
        self._redrawing = True
        try:
            self._redraw_impl()
        finally:
            self._redrawing = False

    def _redraw_impl(self) -> None:
        """Renderiza todos os elementos do gutter: separador, breakpoints, números, ícones de fold e dots de lint."""
        with contextlib.suppress(tk.TclError):
            self.delete("all")

        try:
            ins_ln = int(self._txt.index(tk.INSERT).split(".")[0])
            if ins_ln != self._current_line:
                self._current_line = ins_ln
        except (tk.TclError, ValueError):
            pass

        t = self._theme
        self._update_width()

        raw_w = self.winfo_width()
        w = raw_w if raw_w > 1 else self._desired_w
        h_canvas = max(1, self.winfo_height())

        x_bp = _ZONE_BP // 2
        x_num = _ZONE_BP
        x_fold = w - _ZONE_LINT - _ZONE_FOLD // 2
        x_lint = w - _ZONE_LINT // 2

        sep_color = getattr(t, "gutter_border", getattr(t, "border", "#444"))
        self.create_line(w - 1, 0, w - 1, h_canvas, fill=sep_color, width=1)

        try:
            total = max(1, int(self._txt.index("end").split(".")[0]) - 1)
        except Exception:
            return

        try:
            first_n = int(self._txt.index("@0,0").split(".")[0])
        except Exception:
            first_n = 1
        first_n = max(1, first_n)

        self._line_geo = {}
        fold_color_base = getattr(
            t, "gutter_fold_fg", getattr(t, "accent", "#89b4fa"))

        try:
            txt_root_y = self._txt.winfo_rooty()
            canvas_root_y = self.winfo_rooty()
            _sticky_offset = max(0, txt_root_y - canvas_root_y)
        except Exception:
            _sticky_offset = 0

        for n in range(first_n, total + 1):
            try:
                dl = self._txt.dlineinfo(f"{n}.0")
            except tk.TclError:
                break
            if dl is None:
                continue
            _x, dy, _dw, dh, _base = dl
            dy += _sticky_offset
            if dy < 0:
                continue
            if dy > h_canvas:
                break

            cy = dy + dh // 2
            self._line_geo[n] = (dy, dy + dh)

            r = self.BP_RADIUS
            if n in self._breakpoints:
                self.create_oval(
                    x_bp - r,
                    cy - r,
                    x_bp + r,
                    cy + r,
                    fill="#f87171",
                    outline="",
                    tags="bp",
                )
            else:
                self.create_oval(
                    x_bp - r,
                    cy - r,
                    x_bp + r,
                    cy + r,
                    fill="",
                    outline="",
                    tags=f"bp_{n}",
                )

            if n in self._fold_map:
                folded = self._fold_map[n]
                hovered = n == self._hover_line
                icon = "▸" if folded else "▾"
                icon_color = t.gutter_active_fg if hovered else fold_color_base
                self.create_text(
                    x_fold,
                    cy,
                    text=icon,
                    anchor="center",
                    fill=icon_color,
                    font=(self._font[0], self._font[1] - 1),
                    tags=("fold_icon", f"fi_{n}"),
                )

            dot = self._lint_lines.get(n) or self._kdocs_lines.get(n)
            if dot:
                self.create_oval(
                    x_lint - 3,
                    cy - 3,
                    x_lint + 3,
                    cy + 3,
                    fill=dot,
                    outline="",
                )

            is_cur = n == self._current_line
            is_mark = n in self._marked_lines

            if is_cur:
                fg = t.gutter_active_fg
                font = self._font[0], self._font[1], "bold"
            elif is_mark:
                fg = getattr(t, "accent", "#4fc3f7")
                font = self._font[0], self._font[1], "bold"
            else:
                fg = t.gutter_fg
                font = self._font

            self.create_text(
                x_num,
                cy,
                text=str(n),
                anchor="w",
                fill=fg,
                font=font,
            )

    def _line_at_y(self, y: int) -> int | None:
        """Retorna o número da linha cuja linha renderizada contém o pixel ``y``, ou a
        linha mais próxima se ``y`` estiver entre linhas. Retorna ``None`` se não há cache.
        """
        for lnum, (y0, y1) in self._line_geo.items():
            if y0 <= y < y1:
                return lnum
        if not self._line_geo:
            return None
        return min(
            self._line_geo.items(), key=lambda kv: abs((kv[1][0] + kv[1][1]) // 2 - y)
        )[0]

    _RESIZE_ZONE = 8

    def _in_resize_zone(self, x: int) -> bool:
        """Retorna ``True`` se ``x`` está dentro dos ``_RESIZE_ZONE`` px da borda direita."""
        raw = self.winfo_width()
        w = raw if raw > 1 else self._desired_w
        return x >= w - self._RESIZE_ZONE

    def _on_press(self, e: Any) -> None:
        """Roteia um clique com botão esquerdo para início de resize ou tratamento de clique."""
        if self._in_resize_zone(e.x):
            self._resize_x = e.x
            self.config(cursor="sb_h_double_arrow")
        else:
            self._on_click(e)

    def _resize_drag(self, e: Any) -> None:
        """Ajusta a largura do gutter enquanto o usuário arrasta a borda direita."""
        if self._resize_x is None:
            return
        delta = e.x - self._resize_x
        raw = self.winfo_width()
        cur_w = raw if raw > 1 else self._desired_w
        new_w = max(self._MIN_W, min(self._MAX_W, cur_w + delta))
        if new_w == self._desired_w:
            return

        self._manual_width = new_w
        self._desired_w = new_w
        self._resize_x = e.x

        self.config(width=new_w)

        parent = self.master
        if parent and parent.winfo_exists():
            with contextlib.suppress(Exception):
                parent.pack_propagate(False)
                parent.config(width=new_w)

        self.redraw()

    def _resize_end(self, _e: Any) -> None:
        """Clears the resize anchor and resets the cursor after a drag ends."""
        if self._resize_x is not None:
            self._resize_x = None
            self.config(cursor="")

    def _resize_reset(self, e: Any) -> None:
        """Double-click on the right border resets the gutter to automatic width."""
        if not self._in_resize_zone(e.x):
            return
        self._manual_width = None
        parent = self.master
        if parent and parent.winfo_exists():
            with contextlib.suppress(Exception):
                parent.pack_propagate(True)
        self._update_width()
        self.redraw()

    def _fold_zone_x(self) -> int:
        """Returns the left pixel boundary of the fold icon zone."""
        raw = self.winfo_width()
        w = raw if raw > 1 else self._desired_w
        return w - _ZONE_LINT - _ZONE_FOLD

    def _on_motion(self, e: Any) -> None:
        """Updates the cursor and hover highlight as the pointer moves over the gutter."""
        if self._in_resize_zone(e.x):
            self.config(cursor="sb_h_double_arrow")
            if self._hover_line is not None:
                self._hover_line = None
                self.redraw()
            return

        if not self._fold_map:
            self.config(cursor="")
            return

        line = self._line_at_y(e.y)
        in_fold_zone = (
            line is not None and line in self._fold_map and e.x >= self._fold_zone_x()
        )
        new_hover = line if in_fold_zone else None
        if new_hover != self._hover_line:
            self._hover_line = new_hover
            self.config(cursor="hand2" if in_fold_zone else "")
            self.redraw()

    def _on_leave(self, _e: Any = None) -> None:
        """Clears the hover highlight when the pointer leaves the gutter."""
        if self._hover_line is not None:
            self._hover_line = None
            self.config(cursor="")
            self.redraw()

    def _on_click(self, e: Any) -> None:
        """Handles left-button clicks: fold toggle, Alt+click line mark, or breakpoint."""
        if self._in_resize_zone(e.x):
            return
        line = self._line_at_y(e.y)
        if line is None:
            return

        if e.x >= self._fold_zone_x() and line in self._fold_map:
            if self._on_fold_toggle:
                self._on_fold_toggle(line)
            return

        if e.state & 0x8:
            if line in self._marked_lines:
                self._marked_lines.discard(line)
            else:
                self._marked_lines.add(line)
            self.redraw()
            if self._on_marked_change:
                self._on_marked_change(set(self._marked_lines))
            return

        if line in self._breakpoints:
            self._breakpoints.discard(line)
        else:
            self._breakpoints.add(line)
        self.redraw()
        if self._on_breakpoint_toggle:
            self._on_breakpoint_toggle(set(self._breakpoints))

    def set_current_line(self, line: int) -> None:
        """Updates the highlighted current-line number and schedules a redraw.

        Args:
            line: 1-based line number of the editor cursor.
        """
        if self._current_line != line:
            self._current_line = line
            self._schedule_redraw()

    def set_fold_map(self, fold_map: dict[int, bool]) -> None:
        """Replaces the fold state map and schedules a redraw.

        Args:
            fold_map: ``{start_line: folded}`` dict from the folding engine.
        """
        self._fold_map = fold_map
        self._schedule_redraw()

    def set_on_fold_toggle(self, cb: Callable[[int], None]) -> None:
        """Registers the callback invoked when the user clicks a fold icon.

        Args:
            cb: Called with the 1-based line number of the toggled region.
        """
        self._on_fold_toggle = cb

    def set_on_breakpoint_toggle(self, cb: Callable[[set[int]], None]) -> None:
        """Registers the callback invoked when a breakpoint is added/removed.

        Args:
            cb: Called with a copy of the full current breakpoint set
                (1-based line numbers) every time the user clicks the
                gutter to toggle one.
        """
        self._on_breakpoint_toggle = cb

    def set_lint_lines(self, diags: list[Diag]) -> None:
        """Updates the lint dot map from a list of diagnostics and redraws.

        Only the highest-severity diagnostic per line is shown. Severity 1 = error,
        2 = warning, 3 = info, 4 = hint.

        Args:
            diags: Diagnostic objects from the LSP manager.
        """
        self._lint_lines.clear()
        severity_map: dict[int, int] = {}
        color_table = {
            1: self._theme.err_fg,
            2: self._theme.warn_fg,
            3: self._theme.info_fg,
            4: self._theme.hint_fg,
        }
        for d in diags:
            color = color_table.get(d.severity)
            if color is None:
                continue
            if d.line not in severity_map or d.severity < severity_map[d.line]:
                severity_map[d.line] = d.severity
                self._lint_lines[d.line] = color
        self._schedule_redraw()

    def set_kdocs_lines(self, annotations: list[KDocsNotes]) -> None:
        """Updates the KDocs annotation dot map and schedules a redraw.

        Args:
            annotations: Annotation objects produced by ``KDocsParser``.
        """
        self._kdocs_lines = {a.line: KDocsParser.color(
            a.kind) for a in annotations}
        self._schedule_redraw()

    def set_on_marked_change(self, cb: Callable[[set[int]], None]) -> None:
        """Registers the callback fired when the set of Alt+clicked marked lines changes.

        Args:
            cb: Called with the updated set of marked line numbers.
        """
        self._on_marked_change = cb

    def clear_marked(self) -> None:
        """Clears all marked lines and fires the ``on_marked_change`` callback."""
        self._marked_lines.clear()
        self.redraw()
        if self._on_marked_change:
            self._on_marked_change(set())

    def get_breakpoints(self) -> set[int]:
        """Returns a copy of the current set of breakpoint line numbers."""
        return set(self._breakpoints)
