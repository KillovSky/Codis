"""Widget de terminal: desenha a tela do emulador num ``tk.Text`` e lê o teclado.

O ``ScreenRenderer`` mantém o ``Text`` com ``histórico + linhas da tela`` e só
reescreve as linhas que mudaram, em lote por ciclo. ``TerminalView`` liga o
renderizador ao teclado, ao mouse, à seleção e ao tamanho da janela, enviando
tudo direto ao pseudo-terminal, como num terminal de verdade.
"""

from __future__ import annotations

import contextlib
import sys
import tkinter as tk
import tkinter.font as tkfont
from collections.abc import Callable
from typing import Any

from .keys import encode_key, encode_mouse, encode_paste
from .palette import ANSI_16, blend, resolve_attr
from .session import TerminalSession
from .vt import Run, VTScreen

_SHIFT, _CTRL = 0x1, 0x4
_ALT_MASK = {"win32": 0x20000, "darwin": 0x10}.get(sys.platform, 0x8)


class AttrTagger:
    """Cria sob demanda as tags do ``Text`` para cada atributo do emulador."""

    def __init__(self, widget: Any, fonts: dict[tuple[bool, bool], Any]) -> None:
        self._w = widget
        self._fonts = fonts
        self.default_fg = "#d4d4d4"
        self.default_bg = "#1e1e1e"
        self.palette = ANSI_16
        self._known: set[int] = set()

    def reset(self) -> None:
        """Descarta as tags (ao trocar tema ou fonte)."""
        for attr_id in self._known:
            with contextlib.suppress(tk.TclError):
                self._w.tag_delete(f"a{attr_id}")
        self._known.clear()

    def tag(self, attr_id: int, table: list[tuple[int, int, int]]) -> tuple[str, ...]:
        """Nome da tag para ``attr_id``; vazio para o atributo padrão."""
        if attr_id == 0:
            return ()
        name = f"a{attr_id}"
        if attr_id not in self._known:
            opts = resolve_attr(table[attr_id], self.default_fg, self.default_bg, self.palette)
            font_key = opts.pop("_font")
            if font_key != (False, False):
                opts["font"] = self._fonts[font_key]
            self._w.tag_configure(name, **opts)
            with contextlib.suppress(tk.TclError):
                self._w.tag_raise("cursor")
                self._w.tag_raise("sel")
            self._known.add(attr_id)
        return (name,)


class ScreenRenderer:
    """Mantém um widget de texto sincronizado com uma ``TerminalSession``.

    Layout: as ``sb`` primeiras linhas são o histórico de rolagem; as ``rows``
    últimas são a tela viva do emulador.
    """

    def __init__(self, widget: Any, tagger: AttrTagger, max_history: int = 10_000) -> None:
        self._w = widget
        self._tagger = tagger
        self.max_history = max_history
        self.sb = 0
        self._rows = 0
        self.max_lines_per_render = 600

    def _line_count(self) -> int:
        return int(self._w.index("end-1c").split(".")[0])

    def _args(self, runs: list[Run], table: list[tuple[int, int, int]]) -> list[Any]:
        args: list[Any] = []
        for text, attr_id in runs:
            args.append(text)
            args.append(self._tagger.tag(attr_id, table))
        return args

    def reset(self, rows: int) -> None:
        """Esvazia o widget deixando ``rows`` linhas em branco."""
        self._w.delete("1.0", "end")
        if rows > 1:
            self._w.insert("1.0", "\n" * (rows - 1))
        self.sb = 0
        self._rows = rows

    def load(self, session: TerminalSession) -> None:
        """Redesenha tudo a partir da sessão (troca de aba, tema ou fonte)."""
        screen = session.screen
        self.reset(screen.rows)
        session.take_unrendered()
        session.history_cleared = False
        lines = list(session.history)
        if lines:
            table = screen.attr_table
            args: list[Any] = []
            for runs in lines:
                args.extend(self._args(runs, table))
                args.append("\n")
                args.append(())
            self._w.insert("1.0", *args)
            self.sb = len(lines)
        screen.all_dirty = True

    def render(self, session: TerminalSession, focused: bool = True) -> bool:
        """Aplica as mudanças pendentes. Devolve ``True`` se o widget foi alterado."""
        screen = session.screen
        table = screen.attr_table
        touched = False

        if session.history_cleared:
            self.load(session)
            touched = True

        new_lines = session.take_unrendered(self.max_lines_per_render)
        if new_lines:
            args = []
            for runs in new_lines:
                args.extend(self._args(runs, table))
                args.append("\n")
                args.append(())
            self._w.insert(f"{self.sb + 1}.0", *args)
            self.sb += len(new_lines)
            touched = True
            if self.sb > self.max_history:
                extra = self.sb - self.max_history
                self._w.delete("1.0", f"{extra + 1}.0")
                self.sb -= extra

        if screen.rows != self._rows:
            self._rows = screen.rows
            screen.all_dirty = True
        target = self.sb + self._rows
        count = self._line_count()
        if count < target:
            self._w.insert("end-1c", "\n" * (target - count))
            touched = True
        elif count > target:
            self._w.delete(f"{target}.end", "end-1c")
            touched = True

        full, dirty = screen.take_dirty()
        rows = range(screen.rows) if full else sorted(dirty)
        cursor_row = screen.cy if screen.cursor_visible else -1
        for r in rows:
            if r < screen.rows:
                self._draw_row(screen, r, table, screen.cx + 1 if r == cursor_row else 0)
                touched = True
        if cursor_row >= 0 and cursor_row not in rows:
            self._pad_for_cursor(screen, cursor_row, table)
        self._draw_cursor(screen, focused)
        return touched

    def _draw_row(self, screen: VTScreen, r: int, table: list[tuple[int, int, int]], min_len: int) -> None:
        line = self.sb + r + 1
        runs = screen.row_runs(r, min_len)
        self._w.delete(f"{line}.0", f"{line}.end")
        if runs:
            self._w.insert(f"{line}.0", *self._args(runs, table))

    def _pad_for_cursor(self, screen: VTScreen, r: int, table: list[tuple[int, int, int]]) -> None:
        line = self.sb + r + 1
        have = len(self._w.get(f"{line}.0", f"{line}.end"))
        if have < screen.text_col(r, screen.cx) + 1:
            self._draw_row(screen, r, table, screen.cx + 1)

    def _draw_cursor(self, screen: VTScreen, focused: bool) -> None:
        self._w.tag_remove("cursor", "1.0", "end")
        if not screen.cursor_visible:
            return
        line = self.sb + screen.cy + 1
        col = screen.text_col(screen.cy, screen.cx)
        self._w.tag_add("cursor", f"{line}.{col}", f"{line}.{col}+1c")


class TerminalView(tk.Text):
    """Área de texto que se comporta como um terminal interativo."""

    def __init__(
        self,
        parent: tk.Widget,
        *,
        font_family: str,
        font_size: int,
        history: int = 10_000,
        on_resize: Callable[[], None] | None = None,
        on_input: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(
            parent,
            wrap="none",
            undo=False,
            relief="flat",
            bd=0,
            highlightthickness=0,
            padx=6,
            pady=4,
            insertwidth=0,
            insertontime=0,
            cursor="xterm",
            takefocus=True,
            spacing1=0,
            spacing2=0,
            spacing3=0,
            exportselection=True,
        )
        self._family = font_family
        self._fonts = {
            (b, i): tkfont.Font(
                family=font_family, size=font_size,
                weight="bold" if b else "normal", slant="italic" if i else "roman",
            )
            for b in (False, True) for i in (False, True)
        }
        self.configure(font=self._fonts[(False, False)])
        self._tagger = AttrTagger(self, self._fonts)
        self._renderer = ScreenRenderer(self, self._tagger, history)
        self.session: TerminalSession | None = None
        self._on_resize = on_resize
        self._on_input = on_input
        self._resize_job: str | None = None
        self._focused = False
        self._cursor_color = "#d4d4d4"
        self._key_observers: list[Callable[[tk.Event], Any]] = []
        self._line_filter: Callable[[bytes, str], bool] | None = None
        self._restart: Callable[[], None] | None = None
        self.tag_configure("cursor")
        self.tag_raise("cursor")
        self._bind_events()

    def apply_colors(self, *, fg: str, bg: str, cursor: str, sel_bg: str, sel_fg: str, palette: tuple[str, ...] | None = None) -> None:
        self._tagger.default_fg, self._tagger.default_bg = fg, bg
        if palette:
            self._tagger.palette = palette
        self._cursor_color = cursor
        self.configure(bg=bg, fg=fg, selectbackground=sel_bg, selectforeground=sel_fg)
        self._tagger.reset()
        self._style_cursor()
        if self.session is not None:
            self._renderer.load(self.session)

    def _style_cursor(self) -> None:
        """Cursor sólido com foco; esmaecido (mistura com o fundo) sem foco."""
        bg = self._tagger.default_bg
        if self._focused:
            self.tag_configure("cursor", background=self._cursor_color, foreground=bg)
        else:
            self.tag_configure(
                "cursor", background=blend(self._cursor_color, bg, 0.45), foreground=self._tagger.default_fg
            )

    def set_font_size(self, size: int) -> None:
        for font in self._fonts.values():
            font.configure(size=size)
        self.schedule_fit()

    def cell_size(self) -> tuple[int, int]:
        font = self._fonts[(False, False)]
        return max(1, font.measure("M")), max(1, font.metrics("linespace"))

    def grid_size(self) -> tuple[int, int]:
        """(linhas, colunas) que cabem no widget com a fonte atual."""
        cw, ch = self.cell_size()
        width = self.winfo_width() - 2 * (int(self.cget("padx")) + int(self.cget("bd")))
        height = self.winfo_height() - 2 * (int(self.cget("pady")) + int(self.cget("bd")))
        return max(1, height // ch), max(2, width // cw)

    def attach(self, session: TerminalSession | None) -> None:
        """Passa a exibir ``session`` (ou nada, se ``None``)."""
        self.session = session
        if session is None:
            self._renderer.reset(1)
            return
        self.fit()
        self._renderer.load(session)
        self.render()
        self.see("end")

    def render(self) -> None:
        """Aplica ao widget o que a sessão ativa produziu desde a última chamada."""
        session = self.session
        if session is None:
            return
        at_bottom = self.yview()[1] >= 0.999
        if self._renderer.render(session, self._focused) and at_bottom:
            self.yview_moveto(1.0)

    def schedule_fit(self) -> None:
        if self._resize_job is not None:
            with contextlib.suppress(tk.TclError):
                self.after_cancel(self._resize_job)
        with contextlib.suppress(tk.TclError):
            self._resize_job = self.after(40, self.fit)

    def fit(self) -> None:
        """Ajusta o tamanho do emulador e do PTY ao tamanho atual do widget."""
        self._resize_job = None
        if self.session is None or self.winfo_width() < 50 or self.winfo_height() < 20:
            return
        rows, cols = self.grid_size()
        self.session.resize(rows, cols)
        if self._on_resize is not None:
            self._on_resize()

    def set_line_filter(self, fn: Callable[[bytes, str], bool] | None) -> None:
        """Instala ``fn(dados, texto_digitado) -> bool``; ``True`` consome o Enter."""
        self._line_filter = fn

    def set_restart_handler(self, fn: Callable[[], None] | None) -> None:
        self._restart = fn

    def add_key_observer(self, fn: Callable[[tk.Event], Any]) -> None:
        """Registra um observador que vê toda tecla antes de ela ir ao terminal."""
        self._key_observers.append(fn)

    def _bind_events(self) -> None:
        self.bind("<Key>", self._on_key)
        self.bind("<<Paste>>", lambda _e: self.paste() or "break")
        self.bind("<<Cut>>", lambda _e: "break")
        self.bind("<<Clear>>", lambda _e: "break")
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Button-2>", self._on_middle)
        self.bind("<MouseWheel>", self._on_wheel)
        self.bind("<Button-4>", lambda e: self._on_wheel(e, 1))
        self.bind("<Button-5>", lambda e: self._on_wheel(e, -1))
        self.bind("<FocusIn>", lambda _e: self._set_focus(True))
        self.bind("<FocusOut>", lambda _e: self._set_focus(False))
        self.bind("<Configure>", lambda _e: self.schedule_fit())

    def _set_focus(self, focused: bool) -> None:
        self._focused = focused
        self._style_cursor()
        session = self.session
        if session is not None and session.screen.focus_events:
            session.send(b"\x1b[I" if focused else b"\x1b[O")

    def _send(self, data: bytes) -> None:
        if self.session is not None and data:
            self.session.type_input(data)
            if self._on_input is not None:
                self._on_input()

    def _on_key(self, event: tk.Event) -> str:
        for observer in self._key_observers:
            with contextlib.suppress(Exception):
                observer(event)
        session = self.session
        if session is None:
            return "break"
        state = int(event.state)
        shift, ctrl, alt = bool(state & _SHIFT), bool(state & _CTRL), bool(state & _ALT_MASK)
        char = event.char or ""
        if ctrl and alt and char and char >= " ":
            ctrl = alt = False
        keysym = event.keysym
        lowered = keysym.lower()

        if ctrl and (shift or sys.platform == "win32") and lowered == "c" and (shift or self.tag_ranges("sel")):
            self.copy_selection()
            return "break"
        if ctrl and lowered == "v" and (shift or sys.platform == "win32"):
            self.paste()
            return "break"
        if shift and keysym == "Insert":
            self.paste()
            return "break"
        if ctrl and keysym == "Insert":
            self.copy_selection()
            return "break"
        if shift and keysym in ("Prior", "Next") and not session.screen.alt_screen:
            self.yview_scroll(-1 if keysym == "Prior" else 1, "pages")
            return "break"

        if session.exited and keysym in ("Return", "KP_Enter") and self._restart is not None:
            self._restart()
            return "break"

        data = encode_key(
            keysym, char, shift=shift, alt=alt, ctrl=ctrl, app_cursor=session.screen.app_cursor
        )
        if data is None:
            return "break"
        if self._line_filter is not None and self._line_filter(data, keysym):
            return "break"
        self.yview_moveto(1.0)
        self._send(data)
        return "break"

    def _cell_at(self, event: tk.Event) -> tuple[int, int] | None:
        session = self.session
        if session is None:
            return None
        line, col = (int(v) for v in self.index(f"@{event.x},{event.y}").split("."))
        row = line - self._renderer.sb
        if not 1 <= row <= session.screen.rows:
            return None
        return row, min(session.screen.cols, col + 1)

    def _mouse_reporting(self, event: tk.Event) -> bool:
        session = self.session
        return bool(session and session.screen.mouse_mode and not (int(event.state) & _SHIFT))

    def _report(self, event: tk.Event, button: int, pressed: bool, motion: bool = False) -> None:
        cell = self._cell_at(event)
        if cell is None or self.session is None:
            return
        state = int(event.state)
        self.session.send(
            encode_mouse(
                button, cell[1], cell[0], pressed=pressed, motion=motion,
                shift=bool(state & _SHIFT), alt=bool(state & _ALT_MASK), ctrl=bool(state & _CTRL),
                sgr=self.session.screen.mouse_sgr,
            )
        )

    def _on_press(self, event: tk.Event) -> str | None:
        self.focus_set()
        if self._mouse_reporting(event):
            self._report(event, 0, True)
            return "break"
        return None

    def _on_drag(self, event: tk.Event) -> str | None:
        if self._mouse_reporting(event):
            if self.session and self.session.screen.mouse_mode in (1002, 1003):
                self._report(event, 0, True, motion=True)
            return "break"
        return None

    def _on_release(self, event: tk.Event) -> str | None:
        if self._mouse_reporting(event):
            self._report(event, 0, False)
            return "break"
        return None

    def _on_middle(self, event: tk.Event) -> str:
        if self._mouse_reporting(event):
            self._report(event, 1, True)
            self._report(event, 1, False)
        else:
            self.paste(primary=True)
        return "break"

    def _on_wheel(self, event: tk.Event, direction: int = 0) -> str:
        if direction == 0:
            direction = 1 if getattr(event, "delta", 0) > 0 else -1
        if int(event.state) & _CTRL:
            return "break"
        session = self.session
        if session is not None and self._mouse_reporting(event):
            self._report(event, 64 if direction > 0 else 65, True)
        elif session is not None and session.screen.alt_screen:
            prefix = b"\x1bO" if session.screen.app_cursor else b"\x1b["
            self._send((prefix + (b"A" if direction > 0 else b"B")) * 3)
        else:
            self.yview_scroll(-3 * direction, "units")
        return "break"

    def copy_selection(self) -> None:
        with contextlib.suppress(tk.TclError):
            text = self.get("sel.first", "sel.last")
            if text:
                self.clipboard_clear()
                self.clipboard_append(text.rstrip("\n"))

    def paste(self, primary: bool = False) -> None:
        try:
            text = self.selection_get(selection="PRIMARY") if primary else self.clipboard_get()
        except tk.TclError:
            return
        session = self.session
        if session is not None and text:
            self._send(encode_paste(text, session.screen.bracketed_paste))

    def select_all(self) -> None:
        self.tag_add("sel", "1.0", "end-1c")
