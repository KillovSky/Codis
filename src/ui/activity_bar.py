"""Codis - ActivityBar com visibilidade customizável de itens.

- Visibilidade é salva na config sob a chave "actbar_visible_items"
- Um botão ⋮ no rodapé abre/fecha o painel de personalização
- Itens de plugin são adicionados após os nativos e também são alternáveis
"""

from __future__ import annotations

import contextlib
import json
import logging
import tkinter as tk
from collections.abc import Callable

from src.config import ConfigManager
from src.constants import FONT_UI
from src.i18n import _t
from src.theme.manager import ThemeManager

logger = logging.getLogger("Codis")


class ActivityBar(tk.Frame):
    """
    Vertical activity bar with:
    - Configurable item visibility (persisted to cfg)
    - Tooltip on hover
    - Badge counters
    - Active indicator
    - Customise panel (flyout) toggled by ⋮ button
    """

    ITEMS_TOP: list[tuple[str, str, str, str]] = [
        ("explorer", "📁", "actbar.explorer", "Ctrl+Shift+E"),
        ("search", "🔍", "actbar.search", "Ctrl+Shift+F"),
        ("git", "🌿", "actbar.git", "Ctrl+Shift+G"),
        ("run", "▶ ", "actbar.run", "Ctrl+Shift+D"),
        ("extensions", "🔌", "actbar.extensions", "Ctrl+Shift+X"),
        ("ia", "[IA]", "actbar.ia", "Ctrl+Shift+A"),
        ("outline", "🔷", "actbar.outline", ""),
        ("bookmarks", "🔖", "actbar.bookmarks", "F2"),
        ("scratchpad", "📝", "actbar.scratchpad", "Ctrl+Shift+N"),
        ("problems", "⚠ ", "actbar.problems", "Ctrl+Shift+M"),
        ("remote_ssh", "🗂", "actbar.remote_ssh", "Ctrl+Shift+H"),
        ("chat", "💬", "actbar.chat", "Ctrl+Shift+T"),
        ("collab", "🤝", "actbar.collab", ""),
        ("accessibility", "♿", "actbar.accessibility", "Ctrl+Shift+U"),
    ]
    ITEMS_BOTTOM: list[tuple[str, str, str, str]] = [
        ("terminal", "⊟", "actbar.terminal", "Ctrl+`"),
        ("settings", "⚙ ", "actbar.settings", "Ctrl+,"),
        ("theme", "Th", "actbar.theme", ""),
    ]

    ITEM_GROUPS: dict[str, str] = {
        "explorer": "",
        "git": "",
        "run": "",
        "extensions": "",
        "problems": "",
    }

    _DEFAULT_VISIBLE = {
        "explorer",
        "search",
        "git",
        "run",
        "terminal",
        "settings",
        "chat",
    }

    _HEIGHT_PER_ICON = 40
    _WIDE_THRESHOLD = 100

    def __init__(
        self,
        parent: tk.Widget,
        tmgr: ThemeManager,
        on_activate: Callable[[str], None],
        cfg: ConfigManager = None,
    ) -> None:
        """Cria a barra de atividades lateral do Codis.

        Args:
            parent: Widget pai Tkinter.
            config: Instância de ConfigManager.
            on_panel_change: Callback(panel_id) invocado ao mudar o painel ativo.
        """
        t = tmgr.theme
        super().__init__(parent, bg=t.actbar_bg, width=48, highlightthickness=0, bd=0)
        self.pack_propagate(False)
        self._tmgr = tmgr
        self._on_activate = on_activate
        self._cfg = cfg
        self._active = "explorer"
        self._badges: dict[str, int] = {}
        self._tooltips: dict[str, tk.Toplevel] = {}
        self._tooltip_after_id: str | None = None
        self._plugin_items: list[tuple[str, str, str]] = []
        self._customise_win: tk.Toplevel | None = None
        self._resize_job: str | None = None
        self._item_entries: dict[str, tuple[tk.Frame,
                                            tk.Label, tk.Label | None]] = {}

        self._visible: set = self._load_visible()
        self._top_frame: tk.Frame | None = None
        self._bot_frame: tk.Frame | None = None
        self._custom_vars: dict[str, tk.BooleanVar] = {}
        self._build()

        self.bind("<Configure>", self._on_configure)

    def _load_visible(self) -> set:
        """Carrega do disco quais painéis estão visíveis e em que ordem."""
        if self._cfg:
            raw = self._cfg.get("actbar_visible_items", None)
            if raw and isinstance(raw, (list, str)):
                try:
                    items = json.loads(raw) if isinstance(raw, str) else raw
                    return set(items)
                except (json.JSONDecodeError, ValueError):
                    pass
        return set(self._DEFAULT_VISIBLE)

    def _save_visible(self) -> None:
        """Persiste no disco a lista de painéis visíveis e a ordem atual."""
        if self._cfg:
            self._cfg.set("actbar_visible_items", list(self._visible))

    def _on_configure(self, _e: tk.Event) -> None:
        """Debounced: re-evaluate which icons fit after a resize."""
        if self._resize_job:
            with contextlib.suppress(tk.TclError):
                self.after_cancel(self._resize_job)
        self._resize_job = self.after(120, self._apply_resize)

    def _apply_resize(self) -> None:
        """Aplica tanto o overflow vertical quanto o deslize horizontal dos ícones."""
        self._auto_hide_overflow()
        self._apply_wide_mode()

    def _apply_wide_mode(self) -> None:
        """Exibe/oculta rótulo de texto ao lado do ícone conforme a largura da barra.

        Modo estreito (< _WIDE_THRESHOLD): ícone centralizado, sem texto.
        Modo largo (≥ _WIDE_THRESHOLD): indicador | ícone | texto, usando place
        com coordenadas fixas para garantir ordem visual correta.
        """
        try:
            self.update_idletasks()
            w = self.winfo_width()
        except tk.TclError:
            return
        if w <= 1:
            return
        wide = w >= self._WIDE_THRESHOLD
        t = self._tmgr.theme

        for key, _icon_str, label_str, _ in (*self.ITEMS_TOP, *self.ITEMS_BOTTOM):
            if key not in self._item_entries:
                continue
            frame, icon_lbl, name_lbl = self._item_entries[key]
            try:
                if not frame.winfo_exists():
                    continue
            except tk.TclError:
                continue

            if wide:
                frame.config(height=40)
                icon_lbl.place_forget()
                icon_lbl.place(x=14, rely=0.5, anchor="w")
                icon_lbl.lift()
                if name_lbl is None:
                    cur_bg = t.actbar_active_bg if key == self._active else t.actbar_bg
                    cur_fg = t.actbar_active_fg if key == self._active else t.actbar_fg
                    name_lbl = tk.Label(
                        frame,
                        text=_t(label_str),
                        bg=cur_bg,
                        fg=cur_fg,
                        font=(FONT_UI, 9),
                        cursor="hand2",
                        anchor="w",
                    )
                    name_lbl.bind(
                        "<Button-1>", lambda _e=None, k=key: self._activate(k)
                    )
                    name_lbl.bind(
                        "<Enter>",
                        lambda _e=None, k=key, fl=frame, il=icon_lbl, nl_ref=[
                            name_lbl
                        ]: (
                            (
                                fl.config(bg=t.actbar_active_bg),
                                il.config(bg=t.actbar_active_bg,
                                          fg=t.actbar_active_fg),
                                nl_ref[0].config(
                                    bg=t.actbar_active_bg, fg=t.actbar_active_fg
                                ),
                            )
                            if k != self._active
                            else None
                        ),
                    )
                    name_lbl.bind(
                        "<Leave>",
                        lambda _e=None, k=key, fl=frame, il=icon_lbl, nl_ref=[
                            name_lbl
                        ]: (
                            (
                                fl.config(bg=t.actbar_bg),
                                il.config(bg=t.actbar_bg, fg=t.actbar_fg),
                                nl_ref[0].config(
                                    bg=t.actbar_bg, fg=t.actbar_fg),
                            )
                            if k != self._active
                            else None
                        ),
                    )
                    self._item_entries[key] = (frame, icon_lbl, name_lbl)
                name_lbl.place_forget()
                name_lbl.place(x=46, rely=0.5, anchor="w")
                name_lbl.lift()
            else:
                frame.config(height=46)
                icon_lbl.place_forget()
                icon_lbl.place(relx=0.5, rely=0.5, anchor="center")
                if name_lbl is not None:
                    name_lbl.place_forget()

    def _auto_hide_overflow(self) -> None:
        """Oculta itens de menor prioridade se houver transbordamento da barra.

        Itens ocultados são agrupados num botão '+' que exibe
        um flyout com os ícones ocultos.
        """
        self._resize_job = None
        try:
            available_h = self.winfo_height()
        except tk.TclError:
            return

        bottom_count = (
            len([k for k, _, _, _ in self.ITEMS_BOTTOM if k in self._visible]) + 1
        )
        top_available = available_h - bottom_count * self._HEIGHT_PER_ICON - 8

        all_top: list[tuple[str, str, str]] = []
        for k, icon, lbl, _ in self.ITEMS_TOP:
            if k in self._visible:
                all_top.append((k, icon, _t(lbl)))
        for k, icon, lbl in self._plugin_items:
            if k in self._visible:
                all_top.append((k, icon, lbl))

        max_slots = max(1, top_available // self._HEIGHT_PER_ICON)

        overflows = all_top[max_slots:]
        if overflows:
            max_slots = max(1, max_slots - 1)
            overflows = all_top[max_slots:]

        visible_keys = {k for k, _, _ in all_top[:max_slots]}
        overflow_items = all_top[max_slots:]

        for frame in list(self._top_frame.winfo_children()):
            key = getattr(frame, "_key", None)
            if key is None:
                continue
            if key == "__overflow__":
                frame.destroy()
                continue
            if key in visible_keys:
                if not frame.winfo_ismapped():
                    frame.pack(fill="x")
            else:
                if frame.winfo_ismapped():
                    frame.pack_forget()

        if overflow_items:
            self._add_overflow_btn(overflow_items)

    def _add_overflow_btn(self, items: list[tuple[str, str, str]]) -> None:
        """Add a '⋯' overflow button to the top frame with a flyout menu."""
        t = self._tmgr.theme
        frame = tk.Frame(self._top_frame, bg=t.actbar_bg, width=48, height=40)
        frame.pack(fill="x")
        frame.pack_propagate(False)
        object.__setattr__(frame, "_key", "__overflow__")

        lbl = tk.Label(
            frame,
            text="⋯",
            bg=t.actbar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 14),
            cursor="hand2",
        )
        lbl.place(relx=0.5, rely=0.5, anchor="center")

        badge = tk.Label(
            frame,
            text=str(len(items)),
            bg=t.accent,
            fg="#fff",
            font=(FONT_UI, 6, "bold"),
            padx=2,
        )
        badge.place(relx=0.72, rely=0.15)

        def _show_flyout(_e=None):
            self._show_overflow_flyout(items)

        for w in (frame, lbl):
            w.bind("<Button-1>", _show_flyout)
            w.bind("<Enter>", lambda _, ic=lbl: ic.config(fg=t.actbar_active_fg))
            w.bind("<Leave>", lambda _, ic=lbl: ic.config(fg=t.fg_muted))

    def _show_overflow_flyout(self, items: list[tuple[str, str, str]]) -> None:
        """Show a flyout menu with the overflow icons."""
        t = self._tmgr.theme
        win = tk.Toplevel(self)
        win.wm_overrideredirect(True)
        win.wm_attributes("-topmost", True)
        win.config(bg=t.floating_bg)
        win.geometry("+9999+9999")

        outer = tk.Frame(
            win,
            bg=t.floating_bg,
            highlightthickness=1,
            highlightbackground=getattr(t, "floating_border", "#3d4455"),
        )
        outer.pack(fill="both", expand=True)

        for key, icon, label in items:
            row = tk.Frame(outer, bg=t.floating_bg, cursor="hand2")
            row.pack(fill="x", padx=2, pady=1)
            icon_lbl = tk.Label(
                row, text=icon, bg=t.floating_bg, fg=t.fg, font=(FONT_UI, 16), width=3
            )
            icon_lbl.pack(side="left", padx=4, pady=4)
            name_lbl = tk.Label(
                row,
                text=label,
                bg=t.floating_bg,
                fg=t.fg,
                font=(FONT_UI, 9),
                anchor="w",
            )
            name_lbl.pack(side="left", fill="x", expand=True, padx=4)

            def _click(_, k=key, w=win):
                w.destroy()
                self._activate(k)

            def _enter(_, r=row, il=icon_lbl, nl=name_lbl):
                r.config(bg=t.sel_bg)
                il.config(bg=t.sel_bg)
                nl.config(bg=t.sel_bg)

            def _leave(_, r=row, il=icon_lbl, nl=name_lbl):
                r.config(bg=t.floating_bg)
                il.config(bg=t.floating_bg)
                nl.config(bg=t.floating_bg)

            for w2 in (row, icon_lbl, name_lbl):
                w2.bind("<Button-1>", _click)
                w2.bind("<Enter>", _enter)
                w2.bind("<Leave>", _leave)

        win.update_idletasks()
        h_req = win.winfo_reqheight()
        bx = self.winfo_rootx() + self.winfo_width() + 2
        by = self.winfo_rooty() + self.winfo_height() - h_req
        win.geometry(f"+{bx}+{by}")
        win.bind("<FocusOut>", lambda _: win.destroy()
                 if win.winfo_exists() else None)
        win.focus_set()

    def _build(self) -> None:
        """Constrói os widgets e layout interno do componente."""
        t = self._tmgr.theme

        for child in self.winfo_children():
            with contextlib.suppress(tk.TclError):
                child.destroy()

        self._top_frame = tk.Frame(self, bg=t.actbar_bg)
        self._top_frame.pack(side="top", fill="x")

        self._bot_frame = tk.Frame(self, bg=t.actbar_bg)
        self._bot_frame.pack(side="bottom", fill="x")

        for key, icon, label, shortcut in self.ITEMS_TOP:
            if key in self._visible:
                self._add_item(self._top_frame, key, icon, label, shortcut)

        self._add_customise_btn()

        for key, icon, label, shortcut in self.ITEMS_BOTTOM:
            if key in self._visible:
                self._add_item(self._bot_frame, key, icon, label, shortcut)

        for key, icon, label in self._plugin_items:
            if key in self._visible:
                self._add_item(self._top_frame, key, icon, label, "")

    def _add_customise_btn(self) -> None:
        """Adiciona o botão de personalização ao final da barra de atividades."""
        t = self._tmgr.theme
        frame = tk.Frame(self._bot_frame, bg=t.actbar_bg, width=48, height=36)
        frame.pack(fill="x")
        frame.pack_propagate(False)
        lbl = tk.Label(
            frame,
            text="⋮",
            bg=t.actbar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 14),
            cursor="hand2",
        )
        lbl.place(relx=0.5, rely=0.5, anchor="center")
        for w in (frame, lbl):
            w.bind("<Button-1>", lambda _: self._toggle_customise())
            w.bind("<Enter>", lambda _, ic=lbl: ic.config(fg=t.actbar_active_fg))
            w.bind("<Leave>", lambda _, ic=lbl: ic.config(fg=t.fg_muted))

    def _add_item(
        self, parent: tk.Widget, key: str, icon: str, label: str, shortcut: str
    ) -> None:
        """Adiciona um item à activity bar com visual aprimorado."""
        t = self._tmgr.theme
        is_active = key == self._active

        bg = t.actbar_active_bg if is_active else t.actbar_bg
        fg = t.actbar_active_fg if is_active else t.actbar_fg

        frame = tk.Frame(parent, bg=bg, width=48, height=46)
        frame.pack(fill="x")
        frame.pack_propagate(False)

        indicator = tk.Frame(
            frame,
            bg=t.accent if is_active else t.actbar_bg,
            width=3,
        )
        indicator.place(x=0, y=6, height=34)

        lbl = tk.Label(
            frame,
            text=icon,
            bg=bg,
            fg=fg,
            font=(FONT_UI, 16),
            cursor="hand2",
        )
        lbl.place(relx=0.5, rely=0.5, anchor="center")

        badge = tk.Label(
            frame,
            text="",
            bg=t.err_fg,
            fg="#fff",
            font=(FONT_UI, 7, "bold"),
            padx=2,
            pady=0,
        )
        if key in self._badges and self._badges[key] > 0:
            badge.config(text=str(min(self._badges[key], 99)))
            badge.place(relx=0.68, rely=0.08)

        def on_click(_e=None, k=key):
            self._activate(k)

        def on_enter(_e=None, k=key, fl=frame, ic=lbl):
            if k != self._active:
                fl.config(bg=t.actbar_active_bg)
                ic.config(fg=t.actbar_active_fg, bg=t.actbar_active_bg)
            if self._tooltip_after_id:
                self.after_cancel(self._tooltip_after_id)
                self._tooltip_after_id = None
            self._tooltip_after_id = self.after(
                350, lambda: self._show_tooltip(k, label, shortcut, fl)
            )

        def on_leave(_e=None, k=key, fl=frame, ic=lbl):
            if k != self._active:
                fl.config(bg=t.actbar_bg)
                ic.config(fg=t.actbar_fg, bg=t.actbar_bg)
            if self._tooltip_after_id:
                self.after_cancel(self._tooltip_after_id)
                self._tooltip_after_id = None
            self._hide_tooltip(k)

        for w in (frame, lbl):
            w.bind("<Button-1>", on_click)
            w.bind("<Enter>", on_enter)
            w.bind("<Leave>", on_leave)

        object.__setattr__(frame, "_key", key)
        object.__setattr__(frame, "_indicator", indicator)
        object.__setattr__(frame, "_icon_lbl", lbl)
        object.__setattr__(frame, "_badge", badge)
        self._item_entries[key] = (frame, lbl, None)

    def _show_tooltip(
        self, key: str, label: str, shortcut: str, frame: tk.Widget
    ) -> None:
        """Exibe tooltip aprimorado com label + shortcut para o botão."""
        if not frame.winfo_exists():
            return
        self._hide_tooltip(key)
        t = self._tmgr.theme
        tip = tk.Toplevel(self)
        tip.wm_overrideredirect(True)
        tip.wm_attributes("-topmost", True)

        outer = tk.Frame(
            tip,
            bg=t.floating_bg,
            highlightthickness=1,
            highlightbackground=t.floating_border,
        )
        outer.pack()

        display_label = _t(label) if "." in label else label

        name_row = tk.Frame(outer, bg=t.floating_bg)
        name_row.pack(fill="x", padx=10, pady=(6, 2 if shortcut else 6))
        tk.Label(
            name_row,
            text=display_label,
            bg=t.floating_bg,
            fg=t.fg,
            font=(FONT_UI, 9, "bold"),
            anchor="w",
        ).pack(side="left")

        if shortcut:
            sc_row = tk.Frame(outer, bg=t.floating_bg)
            sc_row.pack(fill="x", padx=10, pady=(0, 6))
            sc_bg = getattr(t, "sel_bg", t.floating_bg)
            sc_label = tk.Label(
                sc_row,
                text=f"  {shortcut}  ",
                bg=sc_bg,
                fg=getattr(t, "accent", t.fg_muted),
                font=(FONT_UI, 8),
                relief="flat",
                padx=2,
            )
            sc_label.pack(side="left")

        try:
            x = self.winfo_rootx() + self.winfo_width() + 6
            y = frame.winfo_rooty() + frame.winfo_height() // 2 - 16
            tip.geometry(f"+{x}+{y}")
        except tk.TclError:
            tip.destroy()
            return
        self._tooltips[key] = tip

    def _hide_tooltip(self, key: str) -> None:
        """Oculta o tooltip atual."""
        tip = self._tooltips.pop(key, None)
        if tip and tip.winfo_exists():
            with contextlib.suppress(tk.TclError):
                tip.destroy()

    def _destroy_all_tooltips(self) -> None:
        """Remove todos os tooltips flutuantes ativos da activity bar."""
        for tip in self._tooltips.values():
            if tip and tip.winfo_exists():
                with contextlib.suppress(tk.TclError):
                    tip.destroy()
        self._tooltips.clear()

    def _activate(self, key: str) -> None:
        """Ativa o item indicado, atualizando visual de todos os itens."""
        t = self._tmgr.theme
        self._active = key
        for parent in (self._top_frame, self._bot_frame):
            if parent is None:
                continue
            for frame in parent.winfo_children():
                k = getattr(frame, "_key", None)
                ind = getattr(frame, "_indicator", None)
                ic = getattr(frame, "_icon_lbl", None)
                if not k or not ind or not ic:
                    continue
                if k == key:
                    ind.config(bg=t.accent)
                    ic.config(fg=t.actbar_active_fg, bg=t.actbar_active_bg)
                    frame.config(bg=t.actbar_active_bg)
                else:
                    ind.config(bg=t.actbar_bg)
                    ic.config(fg=t.actbar_fg, bg=t.actbar_bg)
                    frame.config(bg=t.actbar_bg)
        self._on_activate(key)

    def set_badge(self, key: str, count: int) -> None:
        """Atualiza o badge numérico de um item da activity bar."""
        self._badges[key] = count
        for parent in (self._top_frame, self._bot_frame):
            for frame in parent.winfo_children():
                if getattr(frame, "_key", None) == key:
                    badge = getattr(frame, "_badge", None)
                    if badge:
                        if count > 0:
                            badge.config(
                                text=str(min(count, 99)), bg=self._tmgr.theme.err_fg
                            )
                            badge.place(relx=0.7, rely=0.1)
                        else:
                            badge.config(text="")
                            badge.place_forget()

    def add_plugin_item(self, key: str, icon: str, label: str) -> None:
        """Adiciona um item de plugin à activity bar."""
        if any(k == key for k, _, _ in self._plugin_items):
            return
        self._plugin_items.append((key, icon, label))

        if key not in self._visible:
            self._visible.add(key)
        if key in self._visible:
            self._add_item(self._top_frame, key, icon, label, "")

    def _toggle_customise(self) -> None:
        """Alterna a visibilidade do painel de personalização da barra."""
        if self._customise_win and self._customise_win.winfo_exists():
            self._customise_win.destroy()
            self._customise_win = None
        else:
            self._open_customise()

    def _open_customise(self) -> None:

        t = self._tmgr.theme
        win = tk.Toplevel(self)
        win.wm_overrideredirect(True)
        win.wm_attributes("-topmost", True)
        win.config(bg=t.floating_bg)
        self._customise_win = win

        outer = tk.Frame(
            win,
            bg=t.floating_bg,
            highlightthickness=1,
            highlightbackground=t.floating_border,
        )
        outer.pack(fill="both", expand=True)

        hdr = tk.Frame(outer, bg=t.activity_bg)
        hdr.pack(fill="x")
        tk.Label(
            hdr,
            text="  ⚙  " + _t("ui.settings_title"),
            bg=t.activity_bg,
            fg=t.fg,
            font=(FONT_UI, 9, "bold"),
        ).pack(side="left", padx=4, pady=6)
        close_btn = tk.Label(
            hdr,
            text="✕",
            bg=t.activity_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
            cursor="hand2",
            padx=8,
        )
        close_btn.pack(side="right", pady=6)
        close_btn.bind("<Button-1>", lambda _: win.destroy())

        tk.Frame(outer, bg=t.border, height=1).pack(fill="x")

        canvas = tk.Canvas(outer, bg=t.floating_bg,
                           highlightthickness=0, width=220)
        vsb = tk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.config(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(fill="both", expand=True)

        content = tk.Frame(canvas, bg=t.floating_bg)
        win_id = canvas.create_window((0, 0), window=content, anchor="nw")

        def _on_cfg(_e=None):
            canvas.config(scrollregion=canvas.bbox("all"))
            canvas.itemconfig(win_id, width=canvas.winfo_width())

        content.bind("<Configure>", _on_cfg)
        canvas.bind("<Configure>", _on_cfg)

        def _on_wheel(e):
            delta = -1 * \
                (e.delta // 120) if e.delta else (1 if e.num == 5 else -1)
            canvas.yview_scroll(delta, "units")

        canvas.bind("<MouseWheel>", _on_wheel)
        canvas.bind("<Button-4>", _on_wheel)
        canvas.bind("<Button-5>", _on_wheel)

        all_items: list[tuple[str, str, str]] = []
        for key, icon, label, _ in self.ITEMS_TOP:
            all_items.append((key, icon, label))
        for key, icon, label in self._plugin_items:
            all_items.append((key, icon, label))
        for key, icon, label, _ in self.ITEMS_BOTTOM:
            all_items.append((key, icon, label))

        self._custom_vars = {}

        tk.Label(
            content,
            text=_t("hc.activity_bar.top_items"),
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 7, "bold"),
        ).pack(anchor="w", padx=8, pady=(8, 2))

        sections = [
            (
                _t("actbar.customise_top"),
                [(k, i, _t(ln_)) for k, i, ln_, _ in self.ITEMS_TOP],
            ),
            (_t("actbar.customise_plugins"), self._plugin_items),
            (
                _t("actbar.customise_bottom"),
                [(k, i, _t(ln_)) for k, i, ln_, _ in self.ITEMS_BOTTOM],
            ),
        ]
        for sec_title, items in sections:
            if not items:
                continue
            tk.Frame(content, bg=t.border, height=1).pack(
                fill="x", padx=8, pady=2)
            tk.Label(
                content,
                text=f"  {sec_title}",
                bg=t.floating_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 7, "bold"),
            ).pack(anchor="w", padx=8, pady=(4, 0))
            for key, icon, label in items:
                row = tk.Frame(content, bg=t.floating_bg)
                row.pack(fill="x", padx=8, pady=1)
                var = tk.BooleanVar(value=key in self._visible)
                self._custom_vars[key] = var
                cb = tk.Checkbutton(
                    row,
                    variable=var,
                    bg=t.floating_bg,
                    fg=t.fg,
                    activebackground=t.floating_bg,
                    selectcolor=t.sel_bg,
                    relief="flat",
                    command=lambda k=key, v=var: self._toggle_item(k, v.get()),
                )
                cb.pack(side="left")
                tk.Label(
                    row,
                    text=f"{icon}  {label}",
                    bg=t.floating_bg,
                    fg=t.fg,
                    font=(FONT_UI, 9),
                ).pack(side="left", padx=2)

        tk.Frame(outer, bg=t.border, height=1).pack(fill="x")
        footer = tk.Frame(outer, bg=t.floating_bg)
        footer.pack(fill="x", padx=8, pady=4)
        reset_btn = tk.Label(
            footer,
            text=_t("hc.activity_bar.reset_defaults"),
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            cursor="hand2",
        )
        reset_btn.pack(side="left")
        reset_btn.bind("<Button-1>", lambda _: self._reset_defaults())
        reset_btn.bind("<Enter>", lambda _, w=reset_btn: w.config(fg=t.accent))
        reset_btn.bind("<Leave>", lambda _,
                       w=reset_btn: w.config(fg=t.fg_muted))

        win.update_idletasks()
        try:
            x = self.winfo_rootx() + self.winfo_width() + 2
            y = self.winfo_rooty() + self.winfo_height() - win.winfo_reqheight() - 10
            y = max(y, self.winfo_rooty())
            win.geometry(f"+{x}+{y}")
        except tk.TclError:
            pass

        win.bind("<FocusOut>", lambda _: win.destroy()
                 if win.winfo_exists() else None)

    def _toggle_item(self, key: str, visible: bool) -> None:
        """Executa a operação 'toggle item'.

        Args:
            key: Chave de identificação.
            visible: True para habilitar, False para desabilitar.
        """
        if visible:
            self._visible.add(key)
        else:
            self._visible.discard(key)
        self._save_visible()
        self._rebuild()

    def _reset_defaults(self) -> None:
        """Restaura configurações padrão de fábrica."""
        self._visible = set(self._DEFAULT_VISIBLE)

        for key, _, _ in self._plugin_items:
            self._visible.add(key)
        self._save_visible()
        if self._customise_win and self._customise_win.winfo_exists():
            self._customise_win.destroy()
            self._customise_win = None
        self._rebuild()

    def _rebuild(self) -> None:
        """Reconstrói a activity bar completa refletindo o estado atual dos painéis."""
        self._destroy_all_tooltips()
        active = self._active
        plugins = list(self._plugin_items)
        self._plugin_items = []
        for child in self.winfo_children():
            child.destroy()
        self._top_frame = None
        self._bot_frame = None
        self._build()

        for key, icon, label in plugins:
            if not any(k == key for k, _, _ in self._plugin_items):
                self.add_plugin_item(key, icon, label)

        if active in self._visible:
            self._active = active
            self._activate(active)

    def apply_theme(self, tmgr: ThemeManager) -> None:
        """Aplica o tema reconstruindo a activity bar. Aceita ThemeManager ou Theme."""

        if hasattr(tmgr, "theme"):
            self._tmgr = tmgr
        else:
            pass
        self.config(bg=self._tmgr.theme.actbar_bg)
        self._destroy_all_tooltips()
        saved_plugins = list(self._plugin_items)
        self._plugin_items = []
        for child in self.winfo_children():
            child.destroy()
        self._build()
        for key, icon, label in saved_plugins:
            self.add_plugin_item(key, icon, label)
