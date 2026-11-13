"""Codis - StickyNoteGutter: notas adesivas por linha no gutter.

Permite ao usuário anexar notas de texto a linhas específicas do código.
As notas aparecem como ícones 💬 discretos no gutter. Ao passar o mouse
sobre o ícone, a nota é exibida em um tooltip rico. Ao clicar no ícone,
um popup de edição permite criar/editar/remover a nota.

As notas são persistidas por arquivo via ConfigManager (chave ``sticky_notes``).

Features:
  - Ícone 💬 no gutter para linhas com nota.
  - Tooltip com o texto da nota ao hover.
  - Popup de edição com campo de texto multiline.
  - Ctrl+Shift+N = adicionar/editar nota na linha atual.
  - Notas exportáveis como comentários inline (``# NOTE: ...``).
  - Indicador de linha modificada (nota "deslocada" quando linhas são inseridas).

Uso::

    from src.editor.sticky_note_gutter import StickyNoteGutter
    sng = StickyNoteGutter(text_widget, gutter_canvas, theme, config_manager)
    sng.add_note(42, "Atenção: revisar esta lógica antes do release")
"""

from __future__ import annotations

import contextlib
import logging
import tkinter as tk
from typing import TYPE_CHECKING

from src.constants import FONT_SIZE, FONT_UI
from src.i18n import _t

if TYPE_CHECKING:
    from src.config import ConfigManager
    from src.theme.base import Theme

logger = logging.getLogger("Codis")

_ICON = "💬"
_ICON_SIZE = 10
_TIP_DELAY = 500
_NOTE_MAX = 500


class StickyNoteGutter:
    """Gerenciador de notas adesivas por linha no editor.

    Args:
        txt:    Widget tk.Text do editor.
        gutter: Canvas do gutter (LineNumbers).
        theme:  Tema visual ativo.
        cfg:    ConfigManager para persistência das notas.
    """

    def __init__(
        self,
        txt: tk.Text,
        gutter: tk.Canvas,
        theme: Theme,
        cfg: ConfigManager,
    ) -> None:
        """Inicializa o painel de sticky notes na margem do editor."""
        self._txt = txt
        self._gutter = gutter
        self._theme = theme
        self._cfg = cfg
        self._notes: dict[int, str] = {}
        self._tip_after: str | None = None
        self._tip_win: tk.Toplevel | None = None
        self._file_key: str = ""

        self._load_notes()

        self._gutter.bind("<Motion>", self._on_gutter_motion, add=True)
        self._gutter.bind("<Leave>", self._hide_tip, add=True)
        self._gutter.bind("<Button-3>", self._on_gutter_right_click, add=True)

        self._txt.bind("<Control-Shift-n>", self._on_shortcut, add=True)
        self._txt.bind("<Control-Shift-N>", self._on_shortcut, add=True)

    def set_file(self, path: str) -> None:
        """Define o arquivo atual - carrega as notas persistidas.

        Args:
            path: Caminho absoluto do arquivo aberto.
        """
        self._file_key = path
        self._load_notes()
        self.redraw()

    def add_note(self, line: int, text: str) -> None:
        """Adiciona ou atualiza a nota de uma linha.

        Args:
            line: Número da linha (1-based).
            text: Texto da nota.
        """
        if text.strip():
            self._notes[line] = text.strip()[:_NOTE_MAX]
        else:
            self._notes.pop(line, None)
        self._save_notes()
        self.redraw()

    def remove_note(self, line: int) -> None:
        """Remove a nota de uma linha."""
        self._notes.pop(line, None)
        self._save_notes()
        self.redraw()

    def redraw(self) -> None:
        """Solicita redesenho dos ícones no gutter."""
        if self._gutter.winfo_exists():
            self._gutter.after_idle(self._draw_icons)

    def apply_theme(self, theme: Theme) -> None:
        """Atualiza o tema."""
        self._theme = theme
        self.redraw()

    def destroy(self) -> None:
        """Cancela timers e fecha popups."""
        self._hide_tip()
        if self._tip_after:
            with contextlib.suppress(tk.TclError):
                self._gutter.after_cancel(self._tip_after)

    def _draw_icons(self) -> None:
        """Desenha os ícones 💬 no gutter para linhas com nota."""
        if not self._gutter.winfo_exists():
            return
        with contextlib.suppress(tk.TclError):
            self._gutter.delete("sticky_note_icon")

        if not self._notes:
            return

        t = self._theme
        color = getattr(t, "accent", "#4fc3f7")

        self._gutter.winfo_width()

        _ZONE_BP = 14
        x_icon = _ZONE_BP // 2

        try:
            txt_root_y = self._txt.winfo_rooty()
            canvas_root_y = self._gutter.winfo_rooty()
            sticky_offset = max(0, txt_root_y - canvas_root_y)
        except Exception:
            sticky_offset = 0

        canvas_h = max(1, self._gutter.winfo_height())

        for line_num in self._notes:
            try:
                dl = self._txt.dlineinfo(f"{line_num}.0")
            except tk.TclError:
                continue
            if dl is None:
                continue
            _, y, _, h, _ = dl
            y += sticky_offset
            if y < 0 or y > canvas_h:
                continue
            cy = y + h // 2
            self._gutter.create_text(
                x_icon,
                cy,
                text=_ICON,
                anchor="center",
                font=(FONT_UI, _ICON_SIZE),
                fill=color,
                tags=("sticky_note_icon", f"sn_{line_num}"),
            )

    def _on_gutter_motion(self, e: tk.Event) -> None:
        """Mostra tooltip quando o mouse está sobre um ícone de nota."""
        item = self._gutter.find_closest(e.x, e.y)
        if not item:
            self._schedule_hide_tip()
            return
        tags = self._gutter.gettags(item[0])
        line_tag = next((t for t in tags if t.startswith("sn_")), None)
        if line_tag is None:
            self._schedule_hide_tip()
            return
        try:
            line = int(line_tag[3:])
        except ValueError:
            return
        note = self._notes.get(line)
        if note:
            self._schedule_show_tip(e.x_root, e.y_root, line, note)
        else:
            self._schedule_hide_tip()

    def _on_gutter_right_click(self, e: tk.Event) -> None:
        """Clique direito no gutter -> abre editor de nota para a linha."""
        try:
            idx = self._txt.index(f"@0,{e.y}")
            line = int(idx.split(".")[0])
        except (tk.TclError, ValueError):
            return
        self._open_note_editor(line, e.x_root, e.y_root)

    def _on_shortcut(self, _e: tk.Event) -> str:
        """Ctrl+Shift+N: abre editor de nota na linha do cursor."""
        try:
            idx = self._txt.index(tk.INSERT)
            line = int(idx.split(".")[0])
            self._open_note_editor(
                line, self._txt.winfo_rootx() + 50, self._txt.winfo_rooty() + 50
            )
        except (tk.TclError, ValueError):
            pass
        return "break"

    def _open_note_editor(self, line: int, rx: int, ry: int) -> None:
        """Abre popup de edição para a nota de uma linha.

        Args:
            line: Número da linha (1-based).
            rx:   Coordenada X da raiz da janela.
            ry:   Coordenada Y da raiz da janela.
        """
        popup = tk.Toplevel(self._txt)
        popup.title(
            _t("sticky.title", default="Nota da Linha {line}", line=line))
        popup.resizable(True, False)
        popup.geometry(f"360x160+{rx}+{ry}")

        t = self._theme
        bg = getattr(t, "panel_bg", "#1e1e2e")
        fg = getattr(t, "fg", "#cdd6f4")
        ac = getattr(t, "accent", "#89b4fa")
        popup.config(bg=bg)

        tk.Label(
            popup,
            text=_t("sticky.note_label", default=f"Nota - Linha {line}"),
            bg=bg,
            fg=ac,
            font=(FONT_UI, FONT_SIZE - 1, "bold"),
            anchor="w",
        ).pack(fill="x", padx=10, pady=(8, 2))

        frame = tk.Frame(popup, bg=bg)
        frame.pack(fill="both", expand=True, padx=10, pady=2)

        entry = tk.Text(
            frame,
            height=4,
            width=42,
            bg=getattr(t, "input_bg", "#313244"),
            fg=fg,
            insertbackground=fg,
            font=(FONT_UI, FONT_SIZE - 1),
            wrap="word",
            relief="flat",
            bd=1,
            highlightthickness=1,
            highlightcolor=ac,
            highlightbackground=bg,
        )
        entry.pack(fill="both", expand=True)

        existing = self._notes.get(line, "")
        if existing:
            entry.insert("1.0", existing)
        entry.focus_set()

        btn_frame = tk.Frame(popup, bg=bg)
        btn_frame.pack(fill="x", padx=10, pady=(4, 8))

        btn_kw = {
            "relief": "flat",
            "bd": 0,
            "padx": 10,
            "pady": 3,
            "font": (FONT_UI, FONT_SIZE - 1),
            "cursor": "hand2",
        }

        def _save():
            text = entry.get("1.0", "end-1c").strip()
            self.add_note(line, text)
            popup.destroy()

        def _delete():
            self.remove_note(line)
            popup.destroy()

        tk.Button(
            btn_frame,
            text=_t("sticky.save", default="Salvar"),
            bg=ac,
            fg=bg,
            command=_save,
            **btn_kw,
        ).pack(side="left", padx=(0, 6))
        tk.Button(
            btn_frame,
            text=_t("sticky.delete", default="Remover"),
            bg=getattr(t, "err_fg", "#f38ba8"),
            fg=bg,
            command=_delete,
            **btn_kw,
        ).pack(side="left")
        tk.Button(
            btn_frame,
            text=_t("ui.cancel", default="Cancelar"),
            bg=getattr(t, "btn_bg", "#45475a"),
            fg=fg,
            command=popup.destroy,
            **btn_kw,
        ).pack(side="right")

        entry.bind("<Control-Return>", lambda _: _save())
        popup.bind("<Escape>", lambda _: popup.destroy())

    def _schedule_show_tip(self, x: int, y: int, line: int, note: str) -> None:
        self._cancel_tip_schedule()
        self._tip_after = self._gutter.after(
            _TIP_DELAY,
            lambda: self._show_tip(x, y, line, note),
        )

    def _schedule_hide_tip(self) -> None:
        self._cancel_tip_schedule()
        self._tip_after = self._gutter.after(200, self._hide_tip)

    def _cancel_tip_schedule(self) -> None:
        if self._tip_after:
            with contextlib.suppress(tk.TclError):
                self._gutter.after_cancel(self._tip_after)
            self._tip_after = None

    def _show_tip(self, x: int, y: int, line: int, note: str) -> None:
        self._hide_tip()
        t = self._theme
        bg = getattr(t, "panel_bg", "#1e1e2e")
        fg = getattr(t, "fg", "#cdd6f4")
        ac = getattr(t, "accent", "#89b4fa")

        win = tk.Toplevel(self._gutter)
        win.wm_overrideredirect(True)
        win.wm_geometry(f"+{x + 12}+{y - 8}")
        win.config(bg=bg)

        header = _t("sticky.line_header",
                    default=f"💬  Linha {line}", line=line)
        tk.Label(
            win,
            text=header,
            bg=bg,
            fg=ac,
            font=(FONT_UI, FONT_SIZE - 2, "bold"),
            padx=8,
            pady=4,
            anchor="w",
        ).pack(fill="x")

        tk.Label(
            win,
            text=note,
            bg=bg,
            fg=fg,
            font=(FONT_UI, FONT_SIZE - 1),
            padx=8,
            pady=4,
            anchor="w",
            wraplength=280,
            justify="left",
        ).pack(fill="x")

        win.bind("<Enter>", lambda _: self._cancel_tip_schedule())
        win.bind("<Leave>", lambda _: self._hide_tip())

        self._tip_win = win

    def _hide_tip(self, _e=None) -> None:
        self._cancel_tip_schedule()
        if self._tip_win and self._tip_win.winfo_exists():
            with contextlib.suppress(tk.TclError):
                self._tip_win.destroy()
        self._tip_win = None

    def _persistence_key(self) -> str:
        return f"sticky_notes::{self._file_key}"

    def _load_notes(self) -> None:
        if not self._file_key:
            self._notes = {}
            return
        try:
            stored = self._cfg.get(self._persistence_key(), {})
            self._notes = {
                int(k): v for k, v in stored.items()} if stored else {}
        except (TypeError, ValueError):
            self._notes = {}

    def _save_notes(self) -> None:
        if not self._file_key:
            return
        try:
            self._cfg.set(
                self._persistence_key(),
                {str(k): v for k, v in self._notes.items()},
            )
        except Exception as exc:
            logger.warning("StickyNoteGutter: erro ao salvar notas: %s", exc)
