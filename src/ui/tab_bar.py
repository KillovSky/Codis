"""Barra de abas do editor do Codis.

Implementa ``TabBar``, o widget de gerenciamento de abas com suporte a
abertura, fechamento, reordenação por drag-and-drop, indicador de
alterações não salvas e menu de contexto.
"""

from __future__ import annotations

import contextlib
import logging
import subprocess
import sys
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from typing import Any

from src.constants import FONT_MONO, FONT_UI
from src.i18n import _t
from src.theme.base import Theme
from src.theme.manager import ThemeManager

logger = logging.getLogger("Codis")


class TabBar(tk.Frame):
    """
    Barra de abas deslizante com:
    - Ícone por tipo de arquivo
    - Indicador de modificação (- no nome)
    - Preview ao hover (conteúdo das primeiras linhas)
    - Scroll horizontal quando há muitas abas
    - Fechar com clique do meio (Button-2)
    - Menu de contexto (close, close others, close all, reveal, copy path)
    - Drag-and-drop para reordenar (visual)
    """

    ICONS = {
        ".py": "py",
        ".js": "🟨",
        ".ts": "🔷",
        ".jsx": "⚛ ",
        ".tsx": "⚛ ",
        ".html": "htm",
        ".css": "css",
        ".scss": "css",
        ".json": "{ }",
        ".md": "md",
        ".txt": "txt",
        ".rs": "rs",
        ".go": "go",
        ".java": "java",
        ".cpp": "⚙ ",
        ".c": "⚙ ",
        ".sh": "📜",
        ".yaml": "⚙ ",
        ".toml": "⚙ ",
        ".sql": "🗄 ",
        ".xml": "📰",
        ".env": "🔒",
        ".gitignore": "🔒",
    }

    def __init__(
        self,
        parent: tk.Widget,
        tmgr: ThemeManager,
        on_select: Callable[[int], None],
        on_close: Callable[[int], None],
        on_split_right: Callable[[int], None] | None = None,
    ) -> None:
        """Cria a barra de abas nativa do editor.

        Args:
            parent: Widget pai Tkinter.
            on_select: Callback(idx) ao selecionar aba.
            on_close: Callback(idx) ao fechar aba.
            on_split_right: Callback(idx) ao abrir aba no lado direito do modo dual.
        """
        t = tmgr.theme
        super().__init__(parent, bg=t.tabbar_bg, height=36)
        self.pack_propagate(False)
        self._tmgr = tmgr
        self._on_select = on_select
        self._on_close = on_close
        self._on_split_right = on_split_right
        self._tabs: list[dict] = []
        self._active = -1
        self._preview_win: tk.Toplevel | None = None
        self._preview_job: str | None = None

        self._canvas = tk.Canvas(self, bg=t.tabbar_bg,
                                 highlightthickness=0, height=36)
        self._canvas.pack(fill="both", expand=True)
        self._inner = tk.Frame(self._canvas, bg=t.tabbar_bg)
        self._canvas.create_window((0, 0), window=self._inner, anchor="nw")
        self._inner.bind("<Configure>", self._update_scroll)
        self._canvas.bind("<MouseWheel>", self._on_scroll)
        self._canvas.bind(
            "<Button-4>", lambda _: self._canvas.xview_scroll(-3, "units")
        )
        self._canvas.bind(
            "<Button-5>", lambda _: self._canvas.xview_scroll(+3, "units")
        )

    def _update_scroll(self, _e: Any = None) -> None:
        """Atualiza a região de scroll do canvas após alteração de abas."""
        self._canvas.config(scrollregion=self._canvas.bbox("all"))

    def _on_scroll(self, e: tk.Event) -> None:
        """Faz scroll horizontal com a roda do mouse sobre a tab bar."""
        self._canvas.xview_scroll(-1 if e.delta > 0 else 1, "units")

    def add_tab(self, title: str, path: Path | None = None) -> int:
        """Adiciona uma nova aba e retorna seu índice."""
        idx = len(self._tabs)
        self._tabs.append(
            {"title": title, "path": path, "modified": False, "widget": None}
        )
        self._rebuild()
        return idx

    def remove_tab(self, idx: int) -> None:
        """Remove a aba pelo índice e reconstrói a barra."""
        if 0 <= idx < len(self._tabs):
            self._tabs.pop(idx)
            self._rebuild()

    def update_tab(self, idx: int, title: str, modified: bool = False) -> None:
        """Atualiza título e estado de modificação de uma aba."""
        if 0 <= idx < len(self._tabs):
            self._tabs[idx]["title"] = title
            self._tabs[idx]["modified"] = modified
            self._rebuild()

    def set_active(self, idx: int) -> None:
        """Define a aba ativa e garante que ela esteja visível."""
        self._active = idx
        self._rebuild()
        self._scroll_to_active()

    def count(self) -> int:
        """Retorna o número total de abas abertas."""
        return len(self._tabs)

    def _scroll_to_active(self) -> None:
        """Garante que a aba ativa está visível."""
        if 0 <= self._active < len(self._tabs):
            tab = self._tabs[self._active]
            w = tab.get("widget")
            if w:
                with contextlib.suppress(tk.TclError):
                    x = w.winfo_x()
                    width = w.winfo_width()
                    canvas_w = self._canvas.winfo_width()
                    if x < 0 or x + width > canvas_w:
                        frac = x / max(self._inner.winfo_reqwidth(), 1)
                        self._canvas.xview_moveto(frac)

    def _rebuild(self) -> None:
        """Reconstroi a tab bar de forma incremental.

        Em vez de destruir e recriar todos os frames a cada mudança, mantém
        os widgets existentes e apenas atualiza cores/textos - O(1) para
        troca de aba ativa, O(delta) para add/remove.
        """
        t = self._tmgr.theme
        n_existing = len(self._inner.winfo_children())
        n_needed = len(self._tabs)

        if n_existing > n_needed:
            children = self._inner.winfo_children()
            for w in children[n_needed:]:
                with contextlib.suppress(tk.TclError):
                    w.destroy()

        children = self._inner.winfo_children()
        for i, tab in enumerate(self._tabs):
            is_active = i == self._active
            if i < len(children):
                self._update_tab(children[i], i, tab, is_active, t)
            else:
                self._build_tab(i, tab, is_active, t)

        self._update_scroll()

    def _update_tab(self, frame: tk.Frame, i: int, tab: dict, active: bool, t) -> None:
        """Atualiza visual de um frame de aba já existente - zero widget creation.

        Atualiza bg/fg do frame e de cada label filho, texto do título e
        da barra de acento, sem criar ou destruir nenhum widget.
        """
        bg = t.tab_active_bg if active else t.tab_bg
        fg = t.tab_active_fg if active else t.tab_fg
        try:
            hover_bg = t.tab_hover_bg
        except AttributeError:
            hover_bg = t.tab_active_bg

        title = tab["title"]
        if tab.get("pinned"):
            title = f"📌 {title}"
        elif tab.get("modified"):
            title = f"● {title}"

        path = tab.get("path")
        icon = ""
        if path:
            from pathlib import Path as _P

            icon = self.ICONS.get(_P(path).suffix.lower(), "txt")
        if not icon:
            icon = "txt"

        with contextlib.suppress(tk.TclError):
            frame.config(
                bg=bg,
                highlightthickness=0,
            )
            tab["widget"] = frame

        def _select(_event=None, idx=i):
            self._on_select(idx)

        def _close(_event=None, idx=i):
            self._on_close(idx)

        def _tab_hover_enter(_event=None, f=frame, _active=active, _hbg=hover_bg):
            if not _active:
                for w in f.winfo_children():
                    with contextlib.suppress(tk.TclError):
                        w.config(bg=_hbg)
                with contextlib.suppress(tk.TclError):
                    f.config(bg=_hbg)

        def _tab_hover_leave(_event=None, f=frame, _active=active, _bg=bg):
            if not _active:
                for w in f.winfo_children():
                    with contextlib.suppress(tk.TclError):
                        w.config(bg=_bg)
                with contextlib.suppress(tk.TclError):
                    f.config(bg=_bg)

        children = frame.winfo_children()
        inner_children: list = []
        if len(children) >= 2 and children[1].winfo_exists():
            inner_children = children[1].winfo_children()
        if len(children) >= 2 and len(inner_children) >= 3:
            accent_bar = children[0]
            inner = children[1]
            icon_lbl, title_lbl, close_lbl = inner_children[:3]
            with contextlib.suppress(tk.TclError):
                accent_bar.config(bg=t.accent if active else bg, height=2)
                inner.config(bg=bg)
                icon_lbl.config(bg=bg, fg=fg, text=icon)
                title_lbl.config(
                    bg=bg,
                    fg=fg,
                    text=title,
                    font=(FONT_UI, 9, "bold" if active else "normal"),
                )
                close_lbl.config(bg=bg, fg=t.fg_muted)

            for w in (frame, inner, icon_lbl, title_lbl):
                with contextlib.suppress(tk.TclError):
                    w.bind("<Button-1>", _select)
                    w.bind("<Button-2>", _close)
                    w.bind("<Button-3>", lambda e,
                           idx=i: self._ctx_menu(e, idx))
                    w.bind(
                        "<Enter>",
                        lambda e, idx=i: (
                            self._hover_preview(e, idx),
                            _tab_hover_enter(),
                        ),
                    )
                    w.bind(
                        "<Leave>",
                        lambda e: (self._hide_preview(e), _tab_hover_leave()),
                    )
            with contextlib.suppress(tk.TclError):
                close_lbl.bind("<Button-1>", _close)
                close_lbl.bind(
                    "<Enter>", lambda _e, lbl=close_lbl: lbl.config(
                        fg=t.err_fg)
                )
                close_lbl.bind(
                    "<Leave>",
                    lambda _e, lbl=close_lbl, _fg=t.fg_muted: lbl.config(
                        fg=_fg),
                )
        else:
            with contextlib.suppress(tk.TclError):
                frame.destroy()
            self._build_tab(i, tab, active, t)

    def _build_tab(self, i: int, tab: dict, active: bool, t: Theme) -> None:
        """Constrói a seção 'tab' do componente.

        Args:
            i: Índice do item.
            tab: Parâmetro da operação.
            active: True para habilitar, False para desabilitar.
            t: Parâmetro da operação.
        """
        bg = t.tab_active_bg if active else t.tab_bg
        fg = t.tab_active_fg if active else t.tab_fg
        try:
            hover_bg = t.tab_hover_bg  # type: ignore[attr-defined]
        except AttributeError:
            hover_bg = t.tab_active_bg

        frame = tk.Frame(
            self._inner,
            bg=bg,
            relief="flat",
            bd=0,
            highlightthickness=0,
        )
        frame.pack(side="left", fill="y", padx=(0, 1))
        tab["widget"] = frame

        accent_bar = tk.Frame(frame, bg=t.accent if active else bg, height=2)
        accent_bar.pack(side="top", fill="x")

        path = tab.get("path")
        icon = ""
        if path:
            icon = self.ICONS.get(Path(path).suffix.lower(), "txt")
        if not icon:
            icon = "txt"

        inner = tk.Frame(frame, bg=bg)
        inner.pack(fill="both", expand=True)

        icon_lbl = tk.Label(inner, text=icon, bg=bg, fg=fg,
                            font=(FONT_UI, 8), padx=5)
        icon_lbl.pack(side="left", pady=4)

        title = tab["title"]
        if tab.get("pinned"):
            title = f"📌 {title}"
        elif tab["modified"]:
            title = f"● {title}"

        title_lbl = tk.Label(
            inner,
            text=title,
            bg=bg,
            fg=fg,
            font=(FONT_UI, 9, "bold" if active else "normal"),
            padx=2,
            cursor="hand2",
        )
        title_lbl.pack(side="left", pady=4)

        close_lbl = tk.Label(
            inner,
            text=" ✕ ",
            bg=bg,
            fg=t.fg_muted if not active else getattr(t, "fg_muted", t.fg),
            font=(FONT_UI, 8),
            padx=0,
            cursor="hand2",
        )
        close_lbl.pack(side="left", pady=4)

        def _select(_event=None, idx=i):
            self._on_select(idx)

        def _close(_event=None, idx=i):
            self._on_close(idx)

        def _tab_hover_enter(
            _event=None,
            f=frame,
            il=icon_lbl,
            tl=title_lbl,
            cl=close_lbl,
            _active=active,
        ):
            if not _active:
                import contextlib as _ctx

                for w in (f, il, tl, cl):
                    with _ctx.suppress(tk.TclError):
                        w.config(bg=hover_bg)

        def _tab_hover_leave(
            _event=None,
            f=frame,
            il=icon_lbl,
            tl=title_lbl,
            cl=close_lbl,
            _active=active,
        ):
            if not _active:
                import contextlib as _ctx

                for w in (f, il, tl, cl):
                    with _ctx.suppress(tk.TclError):
                        w.config(bg=bg)

        def _close_hover(_event=None, lbl=close_lbl):
            lbl.config(fg=t.err_fg)

        def _close_leave(_event=None, lbl=close_lbl, _fg=t.fg_muted):
            lbl.config(fg=_fg)

        for w in (frame, inner, icon_lbl, title_lbl):
            w.bind("<Button-1>", _select)
            w.bind("<Button-2>", _close)
            w.bind("<Button-3>", lambda e, idx=i: self._ctx_menu(e, idx))
            w.bind(
                "<Enter>",
                lambda e, idx=i: (self._hover_preview(
                    e, idx), _tab_hover_enter()),
            )
            w.bind("<Leave>", lambda e: (
                self._hide_preview(e), _tab_hover_leave()))

        close_lbl.bind("<Button-1>", _close)
        close_lbl.bind("<Enter>", _close_hover)
        close_lbl.bind("<Leave>", _close_leave)

    def _ctx_menu(self, e: tk.Event, idx: int) -> None:
        """Exibe menu de contexto no ponto do evento.

        Args:
            e: Evento Tkinter.
            idx: Índice do item.
        """
        t = self._tmgr.theme
        menu = tk.Menu(
            self,
            tearoff=False,
            bg=t.floating_bg,
            fg=t.fg,
            activebackground=t.sel_bg,
            activeforeground=t.sel_fg,
            relief="flat",
        )
        menu.add_command(label=_t("ui.tab_close"),
                         command=lambda: self._on_close(idx))
        menu.add_command(
            label=_t("hc.tab_bar.close_others"), command=lambda: self._close_others(idx)
        )
        menu.add_command(label=_t("hc.tab_bar.close_all"),
                         command=self._close_all)
        menu.add_separator()
        tab = self._tabs[idx]
        pinned = tab.get("pinned", False)
        menu.add_command(
            label=("📌 Unpin Tab" if pinned else "📌 Pin Tab"),
            command=lambda: self._toggle_pin(idx),
        )
        if tab.get("path"):
            menu.add_command(
                label=_t("hc.tab_bar.copy_path"), command=lambda: self._copy_path(idx)
            )
            menu.add_command(
                label=_t("tabbar.reveal_explorer", "Revelar no Explorer"),
                command=lambda: self._reveal(idx),
            )
            menu.add_command(
                label=_t("tabbar.copy_rel_path", "Copiar Caminho Relativo"),
                command=lambda: self._copy_rel_path(idx),
            )
        menu.add_separator()
        _split_cmd = self._on_split_right
        if _split_cmd:
            menu.add_command(
                label="⬜ " + _t("view.split_editor") + " (Ctrl+\\)",
                command=lambda: _split_cmd(idx),
            )
        else:
            menu.add_command(
                label="⬜ " + _t("view.split_editor"), state="disabled")
        menu.add_command(label=_t("hc.tab_bar.keep_open"),
                         command=lambda: None)
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()

    def _close_others(self, keep_idx: int) -> None:
        """Executa a operação 'close others'.

        Args:
            keep_idx: Parâmetro da operação.
        """
        for i in range(len(self._tabs) - 1, -1, -1):
            if i != keep_idx:
                self._on_close(i)

    def _close_all(self) -> None:
        """Fecha todas as abas após confirmação do usuário."""
        for i in range(len(self._tabs) - 1, -1, -1):
            self._on_close(i)

    def _toggle_pin(self, idx: int) -> None:
        """Alterna o estado 'pinned' de uma aba."""
        if 0 <= idx < len(self._tabs):
            self._tabs[idx]["pinned"] = not self._tabs[idx].get(
                "pinned", False)
            self._rebuild()

    def _copy_path(self, idx: int, relative: bool = False) -> None:
        """Copia o caminho da aba para a área de transferência.

        Args:
            idx:      Índice da aba.
            relative: Se True, tenta copiar o caminho relativo ao cwd.
        """
        path = self._tabs[idx].get("path")
        if not path:
            return
        text = str(path)
        if relative:
            try:
                from pathlib import Path as _P

                text = str(_P(path).relative_to(_P.cwd()))
            except ValueError:
                pass
        self.clipboard_clear()
        self.clipboard_append(text)

    def _copy_rel_path(self, idx: int) -> None:
        self._copy_path(idx, relative=True)

    def _reveal(self, idx: int) -> None:
        """Abre o explorador de arquivos do SO na pasta do arquivo da aba."""
        path = self._tabs[idx].get("path")
        if path:
            try:
                p = Path(path)
                if sys.platform == "win32":
                    with subprocess.Popen(["explorer", "/select,", str(p)]):
                        pass
                elif sys.platform == "darwin":
                    with subprocess.Popen(["open", "-R", str(p)]):
                        pass
                else:
                    with subprocess.Popen(["xdg-open", str(p.parent)]):
                        pass
            except OSError:
                pass

    def _hover_preview(self, e: tk.Event, idx: int) -> None:
        """Executa a operação 'hover preview'.

        Args:
            e: Evento Tkinter.
            idx: Índice do item.
        """
        if self._preview_job:
            with contextlib.suppress(tk.TclError):
                self.after_cancel(self._preview_job)
        self._preview_job = self.after(700, lambda: self._show_preview(e, idx))

    def _show_preview(self, e: tk.Event, idx: int) -> None:
        """Executa a operação 'show preview'.

        Args:
            e: Evento Tkinter.
            idx: Índice do item.
        """
        if idx >= len(self._tabs):
            return
        tab = self._tabs[idx]
        if not tab.get("path"):
            return
        try:
            path = Path(tab["path"])
            if not path.exists():
                return
            stat = path.stat()
            size = stat.st_size
            mtime = stat.st_mtime
            content_raw = path.read_text(encoding="utf-8", errors="replace")
            all_lines = content_raw.split("\n")
            preview = "\n".join(all_lines[:18])
        except (OSError, UnicodeDecodeError):
            return

        t = self._tmgr.theme
        if self._preview_win and self._preview_win.winfo_exists():
            self._preview_win.destroy()

        self._preview_win = tk.Toplevel(self)
        self._preview_win.wm_overrideredirect(True)
        self._preview_win.wm_attributes("-topmost", True)

        frame = tk.Frame(
            self._preview_win,
            bg=t.floating_bg,
            highlightthickness=1,
            highlightbackground=t.floating_border,
        )
        frame.pack()

        hdr = tk.Frame(frame, bg=t.floating_bg)
        hdr.pack(fill="x")
        tk.Label(
            hdr,
            text=str(path),
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            anchor="w",
            padx=8,
            pady=3,
        ).pack(side="left", fill="x", expand=True)

        import datetime as _dt

        sz_str = (
            f"{size}B"
            if size < 1024
            else f"{size // 1024}KB" if size < 1048576 else f"{size // 1048576}MB"
        )
        mod_str = _dt.datetime.fromtimestamp(mtime).strftime("%d/%m %H:%M")
        meta = f"  {len(all_lines)} linhas - {sz_str} - {mod_str}"
        tk.Label(
            frame,
            text=meta,
            bg=t.floating_bg,
            fg=t.t_func,
            font=(FONT_UI, 7),
            anchor="w",
            padx=8,
            pady=0,
        ).pack(fill="x")

        tk.Frame(frame, bg=t.border, height=1).pack(fill="x")
        tk.Label(
            frame,
            text=preview,
            bg=t.editor_bg,
            fg=t.fg,
            font=(FONT_MONO, 8),
            justify="left",
            padx=8,
            pady=4,
            wraplength=420,
        ).pack(fill="x")

        tk.Label(
            frame,
            text=_t(
                "tab.middle_click_hint",
                "  Clique do meio para fechar - Botão direito para opções",
            ),
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 7),
            anchor="w",
            pady=2,
        ).pack(fill="x")

        wx = e.x_root
        wy = self.winfo_rooty() + self.winfo_height() + 2
        self._preview_win.geometry(f"+{wx}+{wy}")

    def _hide_preview(self, _e: Any = None) -> None:
        """Executa a operação 'hide preview'.

        Args:
            _e: Parâmetro da operação.
        """
        if self._preview_job:
            with contextlib.suppress(tk.TclError):
                self.after_cancel(self._preview_job)
            self._preview_job = None
        if self._preview_win and self._preview_win.winfo_exists():
            with contextlib.suppress(tk.TclError):
                self._preview_win.destroy()
            self._preview_win = None

    def apply_theme(self, t: Theme) -> None:
        """Reaplica o tema à tab bar e reconstrói todas as abas."""
        self.config(bg=t.tabbar_bg)
        self._canvas.config(bg=t.tabbar_bg)
        self._inner.config(bg=t.tabbar_bg)
        self._rebuild()
