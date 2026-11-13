"""
K-UI - KDock: dono único do estado da barra lateral.

A **mesma** moldura de sidebar (com todos os painéis: explorer, busca, git...)
é filha da janela raiz e alterna entre dois estados, sem reparent e sem
duplicar widgets:

* ``docked``   - gerenciada pelo ``PanedWindow`` (modo clássico);
* ``floating`` - removida do paned e posicionada com ``place`` sobre o
  conteúdo (modo K-UI), com animação, auto-hide, pin e redimensionamento.

Como só existe um widget e um dono, é impossível haver dois menus ao mesmo
tempo.
"""

from __future__ import annotations

import contextlib
import logging
import time
import tkinter as tk
from collections.abc import Callable

from ._colors import _lc_q, _pal, _safe_del, _wlift
from ._tkutil import bind_add, unbind_one

try:
    from src.i18n import _t as _i18n_t
except ImportError:  # pragma: no cover

    def _i18n_t(key: str, **_kw) -> str:
        return key.split(".")[-1]

logger = logging.getLogger("Codis.KUI.Dock")

__all__ = ["KDock"]


class KDock:
    """Controlador de docked/floating da sidebar do Codis."""

    MIN_W = 240
    MAX_W = 520
    ANIM_S = 0.14
    TICK_MS = 10
    HDR_H = 30
    GRACE_S = 0.25

    def __init__(
        self,
        root: tk.Misc,
        paned: tk.PanedWindow,
        sidebar: tk.Widget,
        actbar: tk.Widget,
        *,
        dark: bool = True,
        get_width: Callable[[], int] = lambda: 260,
        set_width: Callable[[int], None] | None = None,
        get_left: Callable[[], int] = lambda: 48,
        get_top: Callable[[], int] = lambda: 58,
        get_bottom: Callable[[], int] = lambda: 24,
        reduce_motion: Callable[[], bool] = lambda: False,
        get_pinned: Callable[[], bool] | None = None,
        set_pinned: Callable[[bool], None] | None = None,
        on_change: Callable[[bool], None] | None = None,
        on_raise: Callable[[], None] | None = None,
    ) -> None:
        """
        Args:
            root: Janela raiz (a sidebar precisa ser filha dela).
            paned: ``PanedWindow`` horizontal onde a sidebar vive no modo docked.
            sidebar: Moldura da sidebar (filha da raiz, não do paned).
            actbar: Activity bar (clicar nela nunca fecha o painel flutuante).
            dark: Paleta escura/clara do K-UI.
            get_width/set_width: Largura persistida da sidebar.
            get_left/get_top/get_bottom: Margens dinâmicas (activity bar,
                barras superiores e statusbar).
            reduce_motion: Quando True, pula animações (acessibilidade).
            get_pinned/set_pinned: Persistência do pin (não auto-fechar).
            on_change: Chamado com o novo ``visible`` quando o próprio dock
                mostra/esconde (✕, clique fora) - mantém o estado do app em dia.
            on_raise: Chamado após elevar o painel, para o K-UI re-elevar as
                barras que devem ficar acima dele.
        """
        self._root = root
        self._paned = paned
        self._sb = sidebar
        self._actbar = actbar
        self._p = _pal(dark)
        self._get_width = get_width
        self._set_width = set_width or (lambda _w: None)
        self._get_left = get_left
        self._get_top = get_top
        self._get_bottom = get_bottom
        self._reduce_motion = reduce_motion
        self._get_pinned = get_pinned or (lambda: self._pinned_local)
        self._set_pinned = set_pinned or self._set_pinned_local
        self._on_change = on_change
        self._on_raise = on_raise

        self._pinned_local = False
        self._width_override: int | None = None
        self._alive = True
        self._floating = False
        self._visible = False
        self._anim: str | None = None
        self._shown_at = 0.0
        self._last_geo: tuple[int, int, int, int] | None = None
        self._exempt: list[tk.Misc] = [actbar]

        self._hdr: tk.Frame | None = None
        self._hdr_line: tk.Canvas | None = None
        self._title: tk.Label | None = None
        self._pin_lbl: tk.Label | None = None
        self._close_lbl: tk.Label | None = None
        self._grip: tk.Frame | None = None

        self._click_id = bind_add(root, "<ButtonPress-1>", self._on_root_press)

    @property
    def floating(self) -> bool:
        """True quando a sidebar está no modo flutuante (K-UI ativo)."""
        return self._floating

    @property
    def visible(self) -> bool:
        """True quando o painel flutuante está aberto."""
        return self._visible

    @property
    def widget(self) -> tk.Widget:
        """Moldura da sidebar gerenciada pelo dock."""
        return self._sb

    def width(self) -> int:
        """Largura efetiva (já limitada) do painel."""
        if self._width_override is not None:
            return self._clamp(self._width_override)
        return self._clamp(self._get_width())

    def right_edge(self) -> int:
        """Borda direita do painel (ou da activity bar se estiver fechado)."""
        left = self._get_left()
        return left + (self.width() if self._visible else 0)

    def add_exempt(self, widget: tk.Misc) -> None:
        """Cliques nestes widgets (ou filhos) não fecham o painel."""
        if widget not in self._exempt:
            self._exempt.append(widget)

    def set_dark(self, dark: bool) -> None:
        """Atualiza a paleta (tema claro/escuro) do cabeçalho e da borda."""
        self._p = _pal(dark)
        if self._hdr is not None:
            self._restyle_header()
        if self._floating:
            self._apply_chrome()

    def enter_float(self, visible: bool) -> None:
        """Passa a sidebar para o modo flutuante preservando a visibilidade."""
        if self._floating or not self._alive:
            return
        self._ensure_header()
        self._ensure_grip()
        with contextlib.suppress(tk.TclError):
            if str(self._sb) in [str(w) for w in self._paned.panes()]:
                self._paned.forget(self._sb)
        self._floating = True
        self._visible = False
        self._last_geo = None
        self._apply_chrome()
        self._pack_header()
        if visible:
            self.show(animate=False)
        else:
            with contextlib.suppress(tk.TclError):
                self._sb.place_forget()

    def leave_float(self) -> bool:
        """Devolve a sidebar ao ``PanedWindow``.

        Returns:
            Se o painel estava aberto antes de sair do modo flutuante, para o
            chamador reabrir/fechar a sidebar clássica de forma coerente.
        """
        if not self._floating:
            return self._visible
        was_visible = self._visible
        self._cancel_anim()
        with contextlib.suppress(tk.TclError):
            self._sb.place_forget()
        for w in (self._hdr, self._grip):
            if w is not None:
                with contextlib.suppress(tk.TclError):
                    w.pack_forget()
                    w.place_forget()
        with contextlib.suppress(tk.TclError):
            self._sb.configure(highlightthickness=0)
        self._floating = False
        with contextlib.suppress(tk.TclError):
            self._paned.add(
                self._sb,
                after=self._actbar,
                width=self.width(),
                minsize=60,
                stretch="never",
            )
        return was_visible

    def show(self, animate: bool = True) -> None:
        """Abre o painel (no-op se não estiver no modo flutuante)."""
        if not (self._alive and self._floating):
            return
        left, top, w, h = self._geometry()
        already = self._visible and self._is_placed()
        self._visible = True
        self._shown_at = time.monotonic()
        if already:
            self._place(left, top, w, h)
            self._raise()
            return
        self._place(left - w, top, w, h)
        self._raise()
        if animate:
            self._animate(left - w, left, None)
        else:
            self._place(left, top, w, h)
        self._notify()

    def hide(self, animate: bool = True) -> None:
        """Fecha o painel (no-op se não estiver aberto)."""
        if not (self._alive and self._floating and self._visible):
            return
        self._visible = False
        left, _top, w, _h = self._geometry()

        def _done() -> None:
            if not self._visible:
                with contextlib.suppress(tk.TclError):
                    self._sb.place_forget()
                self._last_geo = None

        if animate:
            self._animate(self._cur_x(left), left - w, _done)
        else:
            _done()
        self._notify()

    def toggle(self) -> None:
        """Alterna abrir/fechar."""
        if self._visible:
            self.hide()
        else:
            self.show()

    def relayout(self) -> None:
        """Reposiciona o painel após resize da janela ou mudança de barras."""
        if not (self._alive and self._floating and self._visible) or self._anim:
            return
        geo = self._geometry()
        if geo != self._last_geo:
            self._place(*geo)

    def raise_panel(self) -> None:
        """Eleva o painel acima do conteúdo (engine, editor, welcome)."""
        if self._floating and self._visible:
            self._raise()

    def shutdown(self) -> None:
        """Libera binds e timers; seguro chamar mais de uma vez."""
        if not self._alive:
            return
        self._alive = False
        self._cancel_anim()
        unbind_one(self._root, "<ButtonPress-1>", self._click_id)
        self._click_id = None
        _safe_del(self._hdr)
        _safe_del(self._grip)
        self._hdr = self._grip = None

    def _clamp(self, w: int) -> int:
        try:
            w = int(w)
        except (TypeError, ValueError):
            w = 260
        return max(self.MIN_W, min(self.MAX_W, w))

    def _geometry(self) -> tuple[int, int, int, int]:
        rw = self._root.winfo_width()
        rh = self._root.winfo_height()
        if rw < 2:
            rw = 1200
        if rh < 2:
            rh = 800
        left = max(0, int(self._get_left()))
        top = max(0, int(self._get_top()))
        bottom = max(0, int(self._get_bottom()))
        w = min(self.width(), max(self.MIN_W, rw - left - 80))
        h = max(120, rh - top - bottom)
        return left, top, w, h

    def _place(self, x: int, y: int, w: int, h: int) -> None:
        with contextlib.suppress(tk.TclError):
            self._sb.place(in_=self._root, x=x, y=y, width=w, height=h)
        if self._visible:
            self._last_geo = (max(0, int(self._get_left())), y, w, h)

    def _is_placed(self) -> bool:
        try:
            return self._sb.winfo_manager() == "place"
        except tk.TclError:
            return False

    def _cur_x(self, default: int) -> int:
        try:
            return int(self._sb.place_info().get("x", default))
        except (tk.TclError, ValueError):
            return default

    def _cancel_anim(self) -> None:
        if self._anim is not None:
            with contextlib.suppress(tk.TclError):
                self._root.after_cancel(self._anim)
            self._anim = None

    def _animate(self, x0: int, x1: int, done: Callable[[], None] | None) -> None:
        """Desliza o painel de ``x0`` a ``x1`` (ease-out cúbico, por tempo).

        Baseada no relógio, não em contagem de passos: a duração é constante
        mesmo se a máquina estiver ocupada (sem "câmera lenta" sob carga).
        """
        self._cancel_anim()
        if self._reduce_motion() or abs(x1 - x0) < 2:
            with contextlib.suppress(tk.TclError):
                self._sb.place_configure(x=x1)
            if done:
                done()
            return
        t0 = time.monotonic()

        def _step() -> None:
            if not self._alive:
                return
            k = min(1.0, (time.monotonic() - t0) / self.ANIM_S)
            e = 1.0 - (1.0 - k) ** 3
            with contextlib.suppress(tk.TclError):
                self._sb.place_configure(x=round(x0 + (x1 - x0) * e))
            if k < 1.0:
                self._anim = self._root.after(self.TICK_MS, _step)
            else:
                self._anim = None
                if done:
                    done()

        _step()

    def _raise(self) -> None:
        _wlift(self._sb)
        if self._on_raise is not None:
            with contextlib.suppress(Exception):
                self._on_raise()

    def _notify(self) -> None:
        if self._on_change is not None:
            with contextlib.suppress(Exception):
                self._on_change(self._visible)

    def _on_root_press(self, e: tk.Event) -> None:
        if not (self._alive and self._floating and self._visible):
            return
        if self._get_pinned():
            return
        if time.monotonic() - self._shown_at < self.GRACE_S:
            return
        w = e.widget
        if not isinstance(w, tk.Misc):
            return
        with contextlib.suppress(tk.TclError):
            if w.winfo_toplevel() is not self._root:
                return
        if self._within(w, self._sb):
            return
        if any(self._within(w, x) for x in self._exempt):
            return
        self.hide()

    @staticmethod
    def _within(w: tk.Misc | None, ancestor: tk.Misc) -> bool:
        while w is not None:
            if w is ancestor:
                return True
            w = getattr(w, "master", None)
        return False

    def _apply_chrome(self) -> None:
        p = self._p
        edge = _lc_q(p["void"], p["accent"], 0.55)
        with contextlib.suppress(tk.TclError):
            self._sb.configure(
                highlightthickness=1, highlightbackground=edge, highlightcolor=edge
            )

    def _ensure_header(self) -> None:
        if self._hdr is not None and self._hdr.winfo_exists():
            return
        p = self._p
        bg = _lc_q(p["glass"], p["void"], 0.30)
        hdr = tk.Frame(self._sb, bg=bg, height=self.HDR_H)
        hdr.pack_propagate(False)
        line = tk.Canvas(hdr, height=2, bg=bg, highlightthickness=0, bd=0)
        line.pack(side="top", fill="x")
        line.bind("<Configure>", lambda _e: self._draw_line())
        row = tk.Frame(hdr, bg=bg)
        row.pack(fill="both", expand=True)
        title = tk.Label(
            row,
            text="  " + _i18n_t("kui.sidebar.title"),
            bg=bg,
            fg=p["fg"],
            font=("Segoe UI", 8, "bold"),
            anchor="w",
        )
        title.pack(side="left", fill="y")
        close = tk.Label(
            row, text="✕", bg=bg, fg=p["fg_dim"], font=("Segoe UI", 9),
            cursor="hand2", padx=8,
        )
        close.pack(side="right", fill="y")
        close.bind("<Button-1>", lambda _e: self.hide())
        close.bind("<Enter>", lambda _e: close.configure(fg=self._p["accent"]))
        close.bind("<Leave>", lambda _e: close.configure(fg=self._p["fg_dim"]))
        pin = tk.Label(
            row, text="◇", bg=bg, fg=p["fg_dim"], font=("Segoe UI", 10),
            cursor="hand2", padx=4,
        )
        pin.pack(side="right", fill="y")
        pin.bind("<Button-1>", lambda _e: self._toggle_pin())
        self._hdr, self._hdr_line, self._title, self._pin_lbl = hdr, line, title, pin
        self._close_lbl = close
        self._restyle_header()

    def _restyle_header(self) -> None:
        if self._hdr is None:
            return
        p = self._p
        bg = _lc_q(p["glass"], p["void"], 0.30)
        with contextlib.suppress(tk.TclError):
            self._hdr.configure(bg=bg)
            if self._hdr_line is not None:
                self._hdr_line.configure(bg=bg)
            for w in self._hdr.winfo_children():
                if isinstance(w, tk.Frame):
                    w.configure(bg=bg)
                    for c in w.winfo_children():
                        c.configure(bg=bg)
            if self._title is not None:
                self._title.configure(fg=p["fg"])
        self._refresh_pin()
        self._draw_line()

    def _pack_header(self) -> None:
        if self._hdr is None:
            return
        with contextlib.suppress(tk.TclError):
            first = next(
                (w for w in self._sb.pack_slaves() if w is not self._hdr), None
            )
            if first is not None:
                self._hdr.pack(side="top", fill="x", before=first)
            else:
                self._hdr.pack(side="top", fill="x")
        if self._grip is not None:
            with contextlib.suppress(tk.TclError):
                self._grip.place(relx=1.0, x=-4, y=0, width=4, relheight=1.0)
                self._grip.lift()

    def _draw_line(self) -> None:
        cv = self._hdr_line
        if cv is None:
            return
        try:
            w = cv.winfo_width()
        except tk.TclError:
            return
        if w < 2:
            return
        p = self._p
        cv.delete("all")
        n = 28
        stops = (p["accent"], p["accent2"], p["accent3"])
        for i in range(n):
            t = i / (n - 1) * (len(stops) - 1)
            a = stops[min(int(t), len(stops) - 2)]
            b = stops[min(int(t) + 1, len(stops) - 1)]
            cv.create_rectangle(
                int(i * w / n), 0, int((i + 1) * w / n) + 1, 2,
                fill=_lc_q(a, b, t - int(t)), outline="",
            )

    def _toggle_pin(self) -> None:
        self._set_pinned(not self._get_pinned())
        self._refresh_pin()

    def _set_pinned_local(self, v: bool) -> None:
        self._pinned_local = bool(v)

    def _refresh_pin(self) -> None:
        if self._pin_lbl is None:
            return
        on = bool(self._get_pinned())
        with contextlib.suppress(tk.TclError):
            self._pin_lbl.configure(
                text="◆" if on else "◇",
                fg=self._p["accent"] if on else self._p["fg_dim"],
            )

    def _ensure_grip(self) -> None:
        if self._grip is not None and self._grip.winfo_exists():
            return
        grip = tk.Frame(self._sb, bg=_lc_q(self._p["void"], self._p["accent"], 0.0),
                        cursor="sb_h_double_arrow", width=4)
        grip.bind("<B1-Motion>", self._on_grip_drag)
        grip.bind("<ButtonRelease-1>", self._on_grip_release)
        grip.bind("<Enter>", lambda _e: grip.configure(
            bg=_lc_q(self._p["void"], self._p["accent"], 0.7)))
        grip.bind("<Leave>", lambda _e: grip.configure(
            bg=_lc_q(self._p["void"], self._p["accent"], 0.0)))
        self._grip = grip

    def _on_grip_drag(self, e: tk.Event) -> None:
        if not (self._floating and self._visible):
            return
        with contextlib.suppress(tk.TclError):
            new_w = self._clamp(e.x_root - self._sb.winfo_rootx())
            self._set_width_live(new_w)

    def _set_width_live(self, w: int) -> None:
        self._width_override = w
        left, top, w, h = self._geometry()
        self._place(left, top, w, h)

    def _on_grip_release(self, _e: tk.Event) -> None:
        with contextlib.suppress(tk.TclError):
            self._set_width(self._clamp(self._sb.winfo_width()))
        self._width_override = None

