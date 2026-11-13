"""MenuBar customizada para o Codis, com dropdowns holográficos.

Substitui o menu nativo do SO por uma barra de Frame completamente temável,
integrada ao layout da janela. Os dropdowns são Toplevel customizados (não
tk.Menu.post) para garantir:

  - Troca de menu por hover sem grab (o maior problema do menu.post).
  - Posicionamento sempre fora da área da própria barra - nunca um dropdown
    nasce sobre os botões, mesmo com a barra ancorada embaixo.
  - Barra ancorável em cima OU embaixo da janela (``position="top"/"bottom"``),
    com o dropdown sempre abrindo para o lado livre da tela.
  - Submenus funcionam com Toplevel encadeados, posicionados à direita (ou à
    esquerda, se não houver espaço).
  - Troca instantânea entre menus de nível superior: passar o mouse por cima
    de outro botão troca o dropdown ativo sem precisar fechar primeiro - e
    sem nunca deixar dois dropdowns abertos brigando pelo mesmo espaço.
  - Atalho de teclado Alt+<letra> para abrir menus.
  - apply_theme() retema tudo em tempo real, incluindo o brilho holográfico.

Arquitetura:
  MenuBar (tk.Frame)
    └─ _MenuButton (tk.Label por entrada)
         └─ _DropMenu (tk.Toplevel, criado on-demand)
               └─ _MenuItem (tk.Frame por item)
                    └─ _DropMenu (submenu, se cascade)

API compatível com ``tk.Menu``:
  ``MenuDef`` expõe a mesma superfície de API usada pelo restante do código
  (``add_command``, ``add_separator``, ``add_checkbutton``, ``add_cascade``,
  ``delete``, ``index``, ``type``, ``entrycget``) para que o código que monta
  os menus (``_app_build.py``, ``_app_settings.py`` etc.) possa tratá-lo como
  um ``tk.Menu``. Kwargs de estilo do Tk
  (``background=``, ``state=`` etc.) são aceitos e ignorados/aplicados onde
  fizer sentido, em vez de gerar ``TypeError``.
"""

from __future__ import annotations

import contextlib
import time
import logging
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass, field

from src.constants import FONT_UI

logger = logging.getLogger("Codis.MenuBar")

_SEP = object()


@dataclass
class MenuItem:
    """Representa um item de menu (comando, checkbutton, separator ou cascade)."""

    kind: str
    label: str = ""
    command: Callable | None = None
    accel: str = ""
    variable: tk.BooleanVar | None = None
    submenu: MenuDef | None = None
    state: str = "normal"


@dataclass
class MenuDef:
    """Coleção de itens que forma um menu dropdown.

    Expõe a API de ``tk.Menu`` (add_command/add_separator/add_checkbutton/
    add_cascade/delete/index/type/entrycget), sem dependência do widget nativo -
    isso permite construir os dropdowns holográficos a partir da mesma
    estrutura de dados usada para montar a MenuBar.
    """

    items: list[MenuItem] = field(default_factory=list)
    on_change: Callable[[], None] | None = None

    def _notify(self) -> None:
        if self.on_change:
            with contextlib.suppress(Exception):
                self.on_change()

    def add_command(
        self,
        label: str = "",
        command: Callable | None = None,
        accelerator: str = "",
        state: str = "normal",
        **_kw,
    ) -> None:
        """Adiciona um comando. Kwargs de estilo do Tk (background, foreground,
        activebackground etc.) são aceitos e ignorados - a aparência é
        controlada inteiramente pelo tema ativo do dropdown holográfico."""
        self.items.append(
            MenuItem("command", label, command, accelerator, state=state))
        self._notify()

    def add_separator(self, **_kw) -> None:
        self.items.append(MenuItem("separator"))
        self._notify()

    def add_checkbutton(
        self,
        label: str = "",
        variable: tk.BooleanVar | None = None,
        command: Callable | None = None,
        **_kw,
    ) -> None:
        self.items.append(MenuItem("checkbutton", label,
                          command, variable=variable))
        self._notify()

    def add_cascade(self, label: str = "", menu: MenuDef | None = None, **_kw) -> None:
        self.items.append(MenuItem("cascade", label, submenu=menu))
        self._notify()

    def delete(self, first, last=None) -> None:
        """Remove itens. Suporta os padrões:
        ``delete(0, "end")`` (limpa tudo) e índices específicos."""
        if not self.items:
            return
        if first in (0, "0") and last == "end":
            self.items.clear()
            self._notify()
            return
        try:
            start = 0 if first in ("end",) else int(first)
        except (TypeError, ValueError):
            return
        end = len(self.items) - 1 if last in (None, "end") else int(last)
        start = max(0, start)
        end = min(len(self.items) - 1, end)
        if start <= end:
            del self.items[start: end + 1]
            self._notify()

    def index(self, what) -> int | None:
        if what == "end":
            return len(self.items) - 1 if self.items else None
        with contextlib.suppress(TypeError, ValueError):
            idx = int(what)
            if 0 <= idx < len(self.items):
                return idx
        return None

    def type(self, idx: int) -> str:
        if 0 <= idx < len(self.items):
            return self.items[idx].kind
        return "separator"

    def entrycget(self, idx: int, option: str):
        if 0 <= idx < len(self.items):
            item = self.items[idx]
            if option == "menu":
                return item.submenu
            if option == "label":
                return item.label
            if option == "state":
                return item.state
        return None

    def nametowidget(self, w):
        """Para cascades, ``w`` já é o próprio MenuDef do submenu (ver
        entrycget('menu')), então só retornamos como veio."""
        return w

    def config(self, **_kw) -> None:
        pass

    def bind(self, *_a, **_kw) -> None:
        pass

    def post(self, *_a, **_kw) -> None:
        pass

    def unpost(self) -> None:
        pass


_ITEM_H = 28
_SEP_H = 9
_PAD_X = 18
_EDGE_GAP = 6


def _mix(c1: str, c2: str, t: float) -> str:
    """Interpola entre duas cores hex (#rrggbb)."""
    try:
        t = max(0.0, min(1.0, t))
        r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
        r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
        r = int(r1 + (r2 - r1) * t)
        g = int(g1 + (g2 - g1) * t)
        b = int(b1 + (b2 - b1) * t)
        return f"#{r:02x}{g:02x}{b:02x}"
    except (ValueError, TypeError, IndexError):
        return c1


class _HoloPalette:
    """Deriva uma paleta holográfica a partir do tema ativo, sem exigir
    campos extras no dataclass Theme - usa apenas atributos que já existem
    em todos os temas (bg, fg, accent, border, floating_bg) e cai para
    valores plausíveis quando algo opcional não está definido."""

    __slots__ = (
        "bg",
        "border",
        "edge_glow",
        "fg",
        "fg_muted",
        "accent",
        "hover_bg",
        "hover_glow",
        "sep",
        "scrollglow",
    )

    def __init__(self, t) -> None:
        base_bg = getattr(t, "floating_bg", t.bg)
        accent = getattr(t, "accent", "#89b4fa")
        border = getattr(t, "floating_border", "") or t.border

        self.bg = _mix(base_bg, "#000000", 0.12)
        self.border = _mix(border, accent, 0.35)
        self.edge_glow = _mix(accent, "#ffffff", 0.15)
        self.fg = t.fg
        self.fg_muted = t.fg_muted
        self.accent = accent
        self.hover_bg = _mix(base_bg, accent, 0.22)
        self.hover_glow = _mix(accent, "#ffffff", 0.25)
        self.sep = _mix(border, accent, 0.15)
        self.scrollglow = _mix(accent, base_bg, 0.7)


class _DropMenu:
    """Popup Toplevel que exibe os itens de um MenuDef com visual holográfico:
    fundo translúcido escuro, borda fina luminosa na cor de destaque do tema,
    e realce de hover com brilho lateral (uma "lâmina" de luz à esquerda do
    item ativo, como um feixe de projeção)."""

    def __init__(
        self, menubar: MenuBar, menu_def: MenuDef, parent_btn: tk.Label | None = None
    ) -> None:
        self._bar = menubar
        self._def = menu_def
        self._btn = parent_btn
        self._win: tk.Toplevel | None = None
        self._rows: list[tk.Frame] = []
        self._sub: _DropMenu | None = None
        self._sub_row: tk.Frame | None = None

    def show(
        self, x: int, y: int, *, upward: bool = False, animate: bool = True
    ) -> None:
        """Cria e exibe o popup, ancorado em (x, y) tela.

        Args:
            x, y: ponto de ancoragem (canto do botão que abriu o menu).
            upward: se True, o popup cresce para CIMA a partir de (x, y) em
                vez de para baixo - usado quando a MenuBar está ancorada na
                parte inferior da janela, para que o dropdown nunca nasça
                por baixo da própria barra (o bug clássico de "menu cobre a
                barra e trava outros menus").
            animate: se False, o popup aparece instantaneamente já opaco. Usado
                ao trocar de menu por hover, onde um fade-in deixaria um
                intervalo sem menu visível a cada troca.
        """
        self.hide()
        self._bar._last_chain_change = time.monotonic()
        t = self._bar._tmgr.theme
        pal = _HoloPalette(t)
        root = self._bar.winfo_toplevel()

        win = tk.Toplevel(root)
        win.withdraw()
        win.wm_overrideredirect(True)
        win.wm_attributes("-topmost", True)
        with contextlib.suppress(tk.TclError):
            win.wm_attributes("-alpha", 0.0 if animate else 0.97)
        self._win = win

        outer = tk.Frame(win, bg=pal.border)
        outer.pack(fill="both", expand=True)

        glow_top = tk.Frame(outer, bg=pal.edge_glow, height=1)
        glow_top.pack(fill="x", side="top")

        inner = tk.Frame(outer, bg=pal.bg, pady=4)
        inner.pack(fill="both", expand=True, padx=1, pady=(1, 1))

        self._rows = []
        for idx, item in enumerate(self._def.items):
            row = self._build_row(inner, item, idx, t, pal)
            if row:
                self._rows.append(row)

        win.update_idletasks()
        w = max(inner.winfo_reqwidth() + 2, 210)
        h = inner.winfo_reqheight() + 10

        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()

        if upward:
            y = y - h

        x = min(x, sw - w - _EDGE_GAP)
        y = min(y, sh - h - _EDGE_GAP)
        x = max(_EDGE_GAP, x)
        y = max(_EDGE_GAP, y)

        win.geometry(f"{w}x{h}+{x}+{y}")
        win.deiconify()
        win.focus_set()
        if animate:
            try:
                from src.ui.ux_enhancements import fade_in

                fade_in(win, duration_ms=110, steps=7, target_alpha=0.97)
            except Exception:
                logger.debug(
                    "Falha ao aplicar fade-in no dropdown do menu.", exc_info=True)

        win.bind("<FocusOut>", self._on_focusout)
        win.bind("<Escape>", lambda _=None: self._bar._close_all())
        win.bind("<Down>", lambda _=None: self._nav_item(+1))
        win.bind("<Up>", lambda _=None: self._nav_item(-1))
        win.bind("<Right>", lambda _=None: self._bar._nav_menu(+1))
        win.bind("<Left>", lambda _=None: self._bar._nav_menu(-1))
        win.bind("<Return>", self._activate_focused)
        self._focused_idx: int = -1

    def hide(self) -> None:
        """Esconde este dropdown e qualquer submenu aberto."""
        self._bar._last_chain_change = time.monotonic()
        if self._sub:
            self._sub.hide()
            self._sub = None
            self._sub_row = None
        if self._win:
            with contextlib.suppress(tk.TclError):
                self._win.destroy()
            self._win = None

    def _is_current(self) -> bool:
        """Indica se este popup pertence à cadeia de menus atualmente aberta."""
        active = self._bar._active
        node = active._drop if active is not None else None
        while node is not None:
            if node is self:
                return True
            node = node._sub
        return False

    def _on_focusout(self, _event: tk.Event) -> None:
        """Fecha os menus quando o foco sai para fora deles.

        A checagem é adiada: ao trocar de menu o foco passa por um estado
        intermediário sem dono, e fechar nesse instante derrubaria o menu que
        acabou de abrir. Popups que já não fazem parte da cadeia ativa (os que
        estão sendo substituídos) são ignorados.
        """
        win = self._win
        if win is None:
            return

        def _check(attempt: int = 0) -> None:
            if self._win is not win or not self._is_current():
                return
            try:
                focused = win.focus_get()
            except (tk.TclError, KeyError):
                focused = None
            if focused is not None:
                return
            if self._bar._pointer_in_menus() or self._bar._chain_changed_recently():
                self._bar._refocus_deepest()
                if attempt < 8:
                    with contextlib.suppress(tk.TclError):
                        win.after(60, _check, attempt + 1)
                return
            self._bar._close_all()

        with contextlib.suppress(tk.TclError):
            win.after(60, _check)

    def _build_row(
        self, parent: tk.Frame, item: MenuItem, idx: int, t, pal: _HoloPalette
    ) -> tk.Frame | None:
        if item.kind == "separator":
            sep = tk.Frame(parent, bg=pal.sep, height=1)
            sep.pack(fill="x", padx=_PAD_X, pady=4)
            return None

        disabled = item.state == "disabled"
        bg = pal.bg
        fg = pal.fg_muted if disabled else pal.fg
        muted = pal.fg_muted

        row = tk.Frame(parent, bg=bg, cursor="hand2" if not disabled else "")
        row.pack(fill="x", padx=0, pady=0)

        beam = tk.Frame(row, bg=bg, width=3)
        beam.pack(side="left", fill="y")

        if item.kind == "checkbutton":
            var = item.variable
            check = tk.Label(
                row,
                text="",
                bg=bg,
                fg=pal.accent,
                font=(FONT_UI, 9),
                width=2,
            )
            check.pack(side="left", padx=(6, 0))

            def _update_check(lbl=check, v=var):
                with contextlib.suppress(tk.TclError):
                    lbl.config(text="✔" if v and v.get() else "")

            if var:
                var.trace_add("write", lambda *_: _update_check())
                _update_check()

        elif item.kind == "cascade":
            tk.Label(row, text="", bg=bg, fg=fg, font=(FONT_UI, 9), width=2).pack(
                side="left", padx=(6, 0)
            )
        else:
            tk.Label(row, text="", bg=bg, fg=fg, font=(FONT_UI, 9), width=2).pack(
                side="left", padx=(6, 0)
            )

        name_lbl = tk.Label(
            row,
            text=item.label,
            bg=bg,
            fg=fg,
            font=(FONT_UI, 9),
            anchor="w",
            pady=4,
        )
        name_lbl.pack(side="left", fill="x", expand=True, padx=(4, 12))

        if item.kind == "cascade":
            tk.Label(
                row, text="›", bg=bg, fg=muted, font=(FONT_UI, 10, "bold"), padx=6
            ).pack(side="right")
        elif item.accel:
            tk.Label(
                row, text=item.accel, bg=bg, fg=muted, font=(FONT_UI, 8), padx=10
            ).pack(side="right")

        if disabled:
            return row

        all_w = [row, beam] + list(row.winfo_children())

        def _enter(_e, r=row, b=beam, i=item, ix=idx):
            self._highlight(r, b, i, ix, hover=True, pal=pal)
            if i.kind == "cascade" and i.submenu:
                self._open_sub(r, i.submenu, t)
            elif self._sub:
                self._sub.hide()
                self._sub = None
                self._sub_row = None

        def _leave(_e, r=row, b=beam, i=item, ix=idx):
            self._highlight(r, b, i, ix, hover=False, pal=pal)

        def _click(_e, i=item):
            self._activate(i)

        for w in all_w:
            w.bind("<Enter>", _enter)
            w.bind("<Leave>", _leave)
            w.bind("<Button-1>", _click)

        return row

    def _highlight(
        self,
        row: tk.Frame,
        beam: tk.Frame,
        item: MenuItem,
        _idx: int,
        hover: bool,
        pal: _HoloPalette,
    ) -> None:
        new_bg = pal.hover_bg if hover else pal.bg
        new_fg = pal.fg if hover else pal.fg
        beam_bg = pal.hover_glow if hover else pal.bg
        with contextlib.suppress(tk.TclError):
            row.config(bg=new_bg)
            beam.config(bg=beam_bg)
            for child in row.winfo_children():
                if child is beam:
                    continue
                if item.kind == "cascade" and child.cget("text") == "›":
                    child.config(bg=new_bg)
                else:
                    child.config(bg=new_bg, fg=new_fg)

    def _nav_item(self, direction: int) -> None:
        """Navega entre itens com seta Up/Down."""
        if not self._rows:
            return
        new_idx = self._focused_idx + direction
        new_idx = max(0, min(len(self._rows) - 1, new_idx))
        self._set_focus_idx(new_idx)

    def _set_focus_idx(self, idx: int) -> None:
        """Destaca o item na posição idx."""
        t = self._bar._tmgr.theme
        pal = _HoloPalette(t)
        if 0 <= self._focused_idx < len(self._rows):
            old_row = self._rows[self._focused_idx]
            with contextlib.suppress(tk.TclError):
                old_row.config(bg=pal.bg)
                for child in old_row.winfo_children():
                    child.config(bg=pal.bg)
        self._focused_idx = idx
        if 0 <= idx < len(self._rows):
            row = self._rows[idx]
            with contextlib.suppress(tk.TclError):
                row.config(bg=pal.hover_bg)
                for child in row.winfo_children():
                    child.config(bg=pal.hover_bg)

    def _activate_focused(self, _event=None) -> None:
        """Ativa o item atualmente focado (Enter)."""
        if 0 <= self._focused_idx < len(self._rows):
            row_items = [
                it for it in self._def.items if it.kind != "separator"]
            if self._focused_idx < len(row_items):
                self._activate(row_items[self._focused_idx])

    def _activate(self, item: MenuItem) -> None:
        if item.state == "disabled":
            return
        if item.kind in ("command", "checkbutton"):
            if item.kind == "checkbutton" and item.variable:
                item.variable.set(not item.variable.get())
            self._bar._close_all()
            if item.command:
                try:
                    item.command()
                except Exception:
                    logger.exception(
                        "Menu '%s': erro ao executar o comando", item.label
                    )
                    with contextlib.suppress(Exception):
                        from tkinter import messagebox

                        messagebox.showerror(
                            "Codis",
                            f"Não foi possível executar '{item.label}'.\nVeja o log para detalhes.",
                        )

    def _open_sub(self, row: tk.Frame, sub_def: MenuDef, t) -> None:
        if self._sub and self._sub_row is row:
            return
        if self._sub:
            self._sub.hide()

        sub = _DropMenu(self._bar, sub_def)

        root = self._bar.winfo_toplevel()
        sw = root.winfo_screenwidth()
        win_w = self._win.winfo_width() if self._win else row.winfo_width()
        est_w = max(win_w, 210)

        x_right = row.winfo_rootx() + row.winfo_width() + 2
        if x_right + est_w > sw - _EDGE_GAP:
            x = row.winfo_rootx() - est_w - 2
            x = max(_EDGE_GAP, x)
        else:
            x = x_right

        y = row.winfo_rooty()
        sub.show(x, y, animate=False)
        self._sub = sub
        self._sub_row = row


_HOVER_INTENT_MS = 45
_SWITCH_COOLDOWN_S = 0.08


class _MenuButton:
    """Label clicável na barra que abre um _DropMenu."""

    def __init__(self, bar: MenuBar, label: str, menu_def: MenuDef) -> None:
        self._bar = bar
        self._def = menu_def
        self._drop: _DropMenu | None = None

        t = bar._tmgr.theme
        bar_bg = getattr(t, "menubar_bg", t.actbar_bg)
        bar_fg = getattr(t, "menubar_fg", t.fg)

        self.widget = tk.Label(
            bar,
            text=f"  {label}  ",
            bg=bar_bg,
            fg=bar_fg,
            font=(FONT_UI, 9),
            cursor="hand2",
            pady=0,
        )
        self.widget.pack(side="left", fill="y")
        self.widget.bind("<Button-1>", self._on_click)
        self.widget.bind("<Enter>", self._on_enter)
        self.widget.bind("<Leave>", self._on_leave)

    @property
    def is_open(self) -> bool:
        """``True`` se o dropdown deste botão está visível."""
        return self._drop is not None and self._drop._win is not None

    def open(self, animate: bool = True) -> None:
        t = self._bar._tmgr.theme
        pal = _HoloPalette(t)
        with contextlib.suppress(tk.TclError):
            self.widget.config(bg=pal.hover_bg, fg=pal.fg)

        if self._drop is None:
            self._drop = _DropMenu(self._bar, self._def, self.widget)

        x = self.widget.winfo_rootx()
        upward = self._bar._position == "bottom"
        if upward:
            y = self.widget.winfo_rooty()
        else:
            y = self.widget.winfo_rooty() + self.widget.winfo_height()
        self._drop.show(x, y, upward=upward, animate=animate)

    def close(self) -> None:
        t = self._bar._tmgr.theme
        bar_bg = getattr(t, "menubar_bg", t.actbar_bg)
        bar_fg = getattr(t, "menubar_fg", t.fg)
        with contextlib.suppress(tk.TclError):
            self.widget.config(bg=bar_bg, fg=bar_fg)
        if self._drop:
            self._drop.hide()

    def apply_theme(self, t) -> None:
        bar_bg = getattr(t, "menubar_bg", t.actbar_bg)
        bar_fg = getattr(t, "menubar_fg", t.fg)
        with contextlib.suppress(tk.TclError):
            self.widget.config(bg=bar_bg, fg=bar_fg)
        if self._bar._active is self and self._drop is not None:
            self.open()

    def _on_click(self, _e: tk.Event) -> None:
        if self._bar._active is self:
            self._bar._close_all()
        else:
            self._bar._open(self)

    def _on_enter(self, _e: tk.Event) -> None:
        t = self._bar._tmgr.theme
        pal = _HoloPalette(t)
        with contextlib.suppress(tk.TclError):
            self.widget.config(bg=pal.hover_bg)
        if self._bar._active and self._bar._active is not self:
            self._bar._request_switch(self)

    def _on_leave(self, _e: tk.Event) -> None:
        self._bar._cancel_switch()
        if self._bar._active is not self:
            t = self._bar._tmgr.theme
            bar_bg = getattr(t, "menubar_bg", t.actbar_bg)
            with contextlib.suppress(tk.TclError):
                self.widget.config(bg=bar_bg)


class MenuBar(tk.Frame):
    """Barra de menus integrada à janela, com dropdowns Toplevel holográficos.

    Pode ser ancorada no topo ou na base da janela (``position``). Em ambos
    os casos, os dropdowns são posicionados para abrir SEMPRE para o lado
    livre da tela (para baixo quando a barra está no topo; para cima quando
    está na base), de modo que nunca cobrem a própria barra nem impedem a
    abertura de outro menu.
    """

    def __init__(self, parent: tk.Widget, tmgr, position: str = "top") -> None:
        """
        Args:
            parent:   Widget pai (normalmente ``self._root``).
            tmgr:     ThemeManager.
            position: ``"top"`` (padrão) ou ``"bottom"`` para posicionar a barra.
        """
        t = tmgr.theme
        super().__init__(
            parent,
            bg=getattr(t, "menubar_bg", t.actbar_bg),
            height=30,
            pady=0,
        )
        self.pack_propagate(False)
        self._tmgr = tmgr
        self._position = position if position in ("top", "bottom") else "top"
        self._buttons: list[_MenuButton] = []
        self._active: _MenuButton | None = None
        self._switch_job: str | None = None
        self._last_open = 0.0
        self._last_chain_change = 0.0
        self._menus_by_label: dict[str, MenuDef] = {}

        accent = getattr(t, "accent", "#89b4fa")
        self._edge = tk.Frame(self, bg=accent, height=1)
        self._edge.pack(side="bottom" if self._position ==
                        "top" else "top", fill="x")

    def pack_into(self, root: tk.Widget) -> None:
        """Empacota a barra na posição configurada e registra listeners da janela raiz.

        Usa ``before=``/``after=`` em vez de confiar na ordem de chamada
        entre ``_build_layout()`` e ``_build_menus()``. Sem isso, empacotar
        a MenuBar com apenas ``side="top"`` depois que outro widget-irmão
        já foi empacotado com ``expand=True`` faz o Tk reservar o espaço do
        topo inteiro para esse widget anterior, e a MenuBar acaba "espremida"
        entre ele e a barra de status - ou seja, visualmente embaixo, mesmo
        pedindo ``position="top"``. Ancorando explicitamente em relação ao
        primeiro/último irmão já empacotado, a posição fica correta
        independente de quando ``pack_into`` é chamada.

        O bind ``<Unmap>`` na janela raiz garante que todos os dropdowns
        abertos sejam fechados quando o usuário minimizar o Codis.
        """
        self._anchor(root)
        try:
            toplevel = root.winfo_toplevel()
            toplevel.bind("<Unmap>", lambda _e: self._close_all(), add="+")
        except tk.TclError:
            pass

    def _anchor(self, root: tk.Widget) -> None:
        """Reempacota a barra como primeiro filho (``position="top"``) ou
        último filho (``position="bottom"``) de ``root``, relativo aos
        widgets-irmãos já existentes."""
        with contextlib.suppress(tk.TclError):
            self.pack_forget()

        siblings = [w for w in root.pack_slaves() if w is not self]

        if self._position == "top":
            if siblings:
                self.pack(in_=root, side="top", fill="x", before=siblings[0])
            else:
                self.pack(in_=root, side="top", fill="x")
        else:
            if siblings:
                self.pack(in_=root, side="bottom",
                          fill="x", after=siblings[-1])
            else:
                self.pack(in_=root, side="bottom", fill="x")

    def set_position(self, position: str) -> None:
        """Reancora a barra em ``"top"`` ou ``"bottom"`` em tempo real.

        Reempacota a própria barra (na mesma master usada anteriormente,
        sempre relativa aos widgets-irmãos via ``before=``/``after=``) e
        fecha qualquer dropdown aberto, já que a direção de abertura
        (para baixo / para cima) depende da posição.
        """
        position = position if position in ("top", "bottom") else "top"
        if position == self._position:
            return
        self._close_all()
        master = self.master
        self._position = position
        with contextlib.suppress(tk.TclError):
            self._anchor(master)

        with contextlib.suppress(tk.TclError):
            self._edge.pack_forget()
            self._edge.pack(side="bottom" if position ==
                            "top" else "top", fill="x")

    @property
    def position(self) -> str:
        return self._position

    def add_cascade(self, label: str, menu: MenuDef, underline: int = 0, **_kw) -> None:
        """Adiciona uma entrada de menu de nível superior.

        Aceita e ignora kwargs extras de estilo, para manter a assinatura de
        ``tk.Menu.add_cascade``.
        """
        btn = _MenuButton(self, label, menu)
        self._buttons.append(btn)
        self._menus_by_label[label] = menu

        underline_char = label[underline].lower(
        ) if underline < len(label) else ""
        if underline_char:
            btn.widget.bind(
                f"<Alt-{underline_char}>",
                lambda _e, b=btn: self._open(b),
            )

    def _nav_menu(self, direction: int) -> None:
        """Navega Left/Right entre os menus de nível superior."""
        if not self._buttons:
            return
        if self._active is None:
            self._open(self._buttons[0])
            return
        try:
            idx = self._buttons.index(self._active)
        except ValueError:
            idx = 0
        new_idx = (idx + direction) % len(self._buttons)
        self._open(self._buttons[new_idx])

    def _request_switch(self, btn: _MenuButton) -> None:
        """Agenda a troca para ``btn`` por hover, com atraso de intenção.

        Abrir um popup cria e destrói janelas sob o ponteiro, o que gera novos
        ``<Enter>``/``<Leave>`` nos botões. Sem este atraso e a checagem de
        posição em ``_commit_switch``, esses eventos sintéticos realimentavam a
        troca e o menu oscilava entre dois botões, recriando janelas sem parar.
        """
        self._cancel_switch()
        remaining = int((_SWITCH_COOLDOWN_S - (time.monotonic() - self._last_open)) * 1000)
        delay = max(_HOVER_INTENT_MS, remaining)
        with contextlib.suppress(tk.TclError):
            self._switch_job = self.after(delay, lambda: self._commit_switch(btn))

    def _chain_windows(self) -> list[tk.Toplevel]:
        """Janelas vivas da cadeia de menus aberta (principal -> submenus)."""
        wins: list[tk.Toplevel] = []
        node = self._active._drop if self._active is not None else None
        while node is not None:
            if node._win is not None:
                wins.append(node._win)
            node = node._sub
        return wins

    def _chain_changed_recently(self, window_s: float = 0.2) -> bool:
        """Se um popup foi criado/destruído há pouco (foco em transição)."""
        return (time.monotonic() - self._last_chain_change) < window_s

    def _pointer_in_menus(self) -> bool:
        """Se o ponteiro está sobre algum popup da cadeia ou sobre a barra."""
        try:
            under = self.winfo_containing(*self.winfo_pointerxy())
        except (tk.TclError, KeyError):
            return False
        if under is None:
            return False
        path = str(under)
        if path.startswith(str(self)):
            return True
        return any(path.startswith(str(w)) for w in self._chain_windows())

    def _refocus_deepest(self) -> None:
        """Devolve o foco ao popup mais interno para manter o teclado vivo."""
        wins = self._chain_windows()
        if wins:
            with contextlib.suppress(tk.TclError):
                wins[-1].focus_set()

    def _cancel_switch(self) -> None:
        if self._switch_job is not None:
            with contextlib.suppress(tk.TclError):
                self.after_cancel(self._switch_job)
            self._switch_job = None

    def _commit_switch(self, btn: _MenuButton) -> None:
        """Efetiva a troca se o ponteiro ainda estiver sobre ``btn``."""
        self._switch_job = None
        if self._active is None or self._active is btn:
            return
        try:
            under = self.winfo_containing(*self.winfo_pointerxy())
        except (tk.TclError, KeyError):
            return
        if under is btn.widget:
            self._open(btn)

    def _open(self, btn: _MenuButton) -> None:
        """Abre o menu de ``btn``; o novo popup aparece antes de o antigo sumir."""
        self._cancel_switch()
        if self._active is btn and btn.is_open:
            return
        previous = self._active
        self._active = btn
        self._last_open = time.monotonic()
        btn.open(animate=previous is None)
        if previous is not None and previous is not btn:
            previous.close()

    def _close_all(self) -> None:
        self._cancel_switch()
        if self._active:
            self._active.close()
            self._active = None

    def apply_theme(self, t) -> None:
        bar_bg = getattr(t, "menubar_bg", t.actbar_bg)
        accent = getattr(t, "accent", "#89b4fa")
        with contextlib.suppress(tk.TclError):
            self.config(bg=bar_bg)
            self._edge.config(bg=accent)
        for btn in self._buttons:
            btn.apply_theme(t)