"""
K-UI - Pacote de engine cósmica Codis
=====================================
Estrutura do pacote:
  _colors.py      - paletas e utilitários de cor/widget
  _perf.py        - perfis de performance (quantum/ultra/balanced/...)
  _easter.py      - KUEasterEggs: detecção inteligente de sequências
  _tooltip.py     - KHoverTooltip: painel de aprendizado contextual
  _bgrenderer.py  - BgRenderer: nebulosa/aurora em thread PIL
  _engine.py      - KEngine + KWelcome
  _dock.py        - KDock: dono único da sidebar (docked/flutuante)
  _tkutil.py      - bind/unbind seguros
  _widgets.py     - KGlassPane, KTopBar, KTabBar, KStatusBar,
                    KPortal, KFloatWindow, KToast
  __init__.py     - KUI: controlador principal + re-exports públicos
"""

from __future__ import annotations

import contextlib
import logging
import math
import random
import time
import tkinter as tk
from pathlib import Path

from src.theme.base import Theme

from ._bgrenderer import _PIL, BgRenderer
from ._colors import _hex2rgb, _hue, _lc_q, _lerp, _pal, _safe_del, _wlift, _wlower
from ._dock import KDock
from ._easter import KUEasterEggs
from ._engine import KEngine, KWelcome
from ._perf import _DEFAULT_PERF, _PERF
from ._tkutil import unbind_one
from ._tooltip import KHoverTooltip
from ._widgets import (
    KFloatWindow,
    KGlassPane,
    KPortal,
    KStatusBar,
    KTabBar,
    KToast,
    KTopBar,
)

try:
    from src.i18n import _t as _i18n_t
except ImportError:

    def _i18n_t(key: str, **_kw) -> str:

        return key.split(".")[-1]

logger = logging.getLogger("Codis.KUI")
_TAU = math.tau

__all__ = [
    "KUI",
    "build_kui_theme",
    "KDock",
    "KEngine",
    "KWelcome",
    "KGlassPane",
    "KTopBar",
    "KTabBar",
    "KStatusBar",
    "KPortal",
    "KFloatWindow",
    "KToast",
    "KHoverTooltip",
    "BgRenderer",
    "KUEasterEggs",
    "_pal",
    "_lc_q",
    "_lerp",
    "_hue",
    "_safe_del",
    "_wlift",
    "_wlower",
    "_hex2rgb",
    "_PIL",
    "_PERF",
    "_DEFAULT_PERF",
]


class KUI:
    """Controlador principal do K-UI.

    - KGlassPane: editor flutua sobre cosmos com borda luminosa
    - BgRenderer: nebulosa/aurora em thread PIL - sem lag
    - apply_theme() robusto - reconstroi tudo corretamente
    - _show_welcome interceptado para usar KWelcome cosmica
    - FPS real reportado na statusbar
    """

    def __init__(
        self,
        root,
        tmgr,
        get_editor,
        commands,
        _ribbon_actions,
        hide_sidebar_cb=None,
        show_sidebar_cb=None,
        _hide_activity_cb=None,
        _show_activity_cb=None,
        hide_bottom_cb=None,
        show_bottom_cb=None,
        tabbar_widget=None,
        breadcrumb_widget=None,
        statusbar_widget=None,
        menubar_ref=None,
        content_frame=None,
        editor_container=None,
        h_paned=None,
        v_paned=None,
        sidebar_dock=None,
        is_sidebar_visible=None,
        show_welcome_cb=None,
        hide_classic_welcome_cb=None,
        actbar_widget=None,
        panel_factories=None,
        perf_profile=_DEFAULT_PERF,
        detach_manager=None,
    ):
        """
        Args:
            root: Janela raiz Tkinter.
            config: Instância de ConfigManager.
            editor_getter: Callable que retorna o EditorPane ativo.
            dark: True para modo escuro.
            detach_manager: ``PanelDetachManager`` compartilhado com o
                ``CodisApp``. Quando fornecido, ``detach_panel``/
                ``reattach_panel`` delegam a ele em vez de manter estado
                próprio - isso permite que painéis destacados sobrevivam
                a alternâncias do modo K-UI ligado/desligado, e que o
                modo clássico também consiga destacar painéis. Se omitido,
                o estado fica em ``_detached``.
        """
        self._root = root
        self._tmgr = tmgr
        self._get_ed = get_editor
        self._commands = commands
        self._hide_sb = hide_sidebar_cb
        self._show_sb = show_sidebar_cb
        self._tabbar = tabbar_widget
        self._breadcrumb = breadcrumb_widget
        self._menubar = menubar_ref
        self._cf = content_frame
        self._ec = editor_container
        self._hpaned = h_paned
        self._vpaned = v_paned
        self._dock = sidebar_dock
        self._is_sb_visible = is_sidebar_visible or (lambda: False)
        self._show_welcome_cb = show_welcome_cb
        self._hide_classic_welcome_cb = hide_classic_welcome_cb
        self._actbar = actbar_widget
        self._factories = panel_factories or {}
        self._profile = perf_profile
        self._enabled = False
        self._dark = True
        self._has_ws = False
        self._engine = None
        self._topbar = None
        self._tabbar_w = None
        self._stbar = None
        self._portal = None
        self._welcome = None
        self._detached = {}
        self._detach_manager = detach_manager
        self._glass_pane: KGlassPane | None = None

        self._easter: KUEasterEggs | None = None
        self._diag_errors = 0
        self._diag_warnings = 0
        self._open_files = []
        self._kpm_window = []
        self._char_window = []
        self._metric_job = None
        self._last_tab_action = None
        self._fps_report_job = None

        self._minimize_btn: tk.Label | None = None

        self._root_bind_ids: dict[str, list[str]] = {}
        self._planet_active: bool = False
        self._activity_job: str | None = None

    def _paint_frames_void(self) -> None:
        """Pinta os frames de layout com a cor void para integração visual.

        Exclui recursivamente a activity sep_bar e o tree_parent (sidebar_stack)
        para não sobrescrever as cores de tema de PainelIA e demais painéis.
        """
        p = _pal(self._dark)
        void = p["void"]
        _SKIP = (
            tk.Text,
            tk.Listbox,
            tk.Entry,
            tk.Scrollbar,
            tk.Button,
            tk.PanedWindow,
            tk.Canvas,
        )
        _skip: set[str] = set()

        def _collect(w: tk.Misc) -> None:
            _skip.add(str(w))
            for ch in w.winfo_children():
                _collect(ch)

        if self._actbar and self._actbar.winfo_exists():
            _collect(self._actbar)

        if self._dock is not None and self._dock.widget.winfo_exists():
            _collect(self._dock.widget)

        def _paint(w: tk.Misc, depth: int = 0) -> None:
            if depth > 16 or str(w) in _skip:
                return
            with contextlib.suppress(tk.TclError):
                if not isinstance(w, _SKIP):
                    w.configure(bg=void)
            for ch in w.winfo_children():
                _paint(ch, depth + 1)

        _paint(self._root)
        for pw in (self._hpaned, self._vpaned):
            if pw:
                with contextlib.suppress(tk.TclError):
                    pw.config(bg=void, sashwidth=4,
                              sashrelief="flat", sashpad=0)
        chain: list[tk.Misc | None] = [self._cf, self._ec]
        if self._ec:
            with contextlib.suppress(tk.TclError):
                ea = self._ec.nametowidget(self._ec.winfo_parent())
                if ea and not isinstance(ea, (tk.PanedWindow, tk.Tk)):
                    chain.append(ea)
        for w in chain:
            if w:
                with contextlib.suppress(tk.TclError):
                    w.configure(bg=void)
                for ch in w.winfo_children():
                    with contextlib.suppress(tk.TclError):
                        if not isinstance(
                            ch, (tk.Text, tk.Canvas, tk.Listbox,
                                 tk.Entry, tk.Scrollbar)
                        ):
                            ch.configure(bg=void)

    def _make_engine(self):
        """Instancia e configura o KEngine com o perfil atual."""
        engine_parent = self._cf or self._ec or self._root
        self._engine = KEngine(engine_parent, self._dark, self._profile)
        self._engine.place(x=0, y=0, relwidth=1.0, relheight=1.0)
        _wlower(self._engine)

        self._engine.set_planet_click_cb(self._on_solar_planet_click)

    def _setup_glass_pane(self) -> None:
        """Conecta o KGlassPane ao editor_container.

        Nenhum Frame com place() é criado no _cf; o glass pane é aplicado
        diretamente no _ec:
        - highlightbackground animado (borda luminosa 2px)
        - faixa espectral 2px dentro do _ec via place()
        Isso não interfere com o layout do cosmos nem do editor.
        TÁTICA #6: margens dinâmicas obtidas das alturas reais das barras.
        """
        if not self._ec:
            return

        if self._glass_pane:
            self._glass_pane.destroy()
            self._glass_pane = None
        self._glass_pane = KGlassPane(self._dark)

        def _attach():
            if self._glass_pane and self._ec and self._ec.winfo_exists():
                self._glass_pane.attach(self._ec)

        self._root.after(100, _attach)

    def chrome_top(self) -> int:
        """Y logo abaixo da topbar e (se visível) da tabbar K-UI."""
        top = 22
        tb = self._tabbar_w
        if tb is not None:
            with contextlib.suppress(tk.TclError):
                if tb.winfo_exists() and tb.winfo_ismapped():
                    top += 36
        return top

    def raise_chrome(self) -> None:
        """Re-eleva as barras K-UI acima da sidebar flutuante (z-order único)."""
        for w in (self._topbar, self._tabbar_w, self._stbar,
                  self._minimize_btn, self._portal):
            if w is not None:
                with contextlib.suppress(tk.TclError):
                    _wlift(w)

    def update_engine_activity(self) -> None:
        """Suspende o engine quando o editor ou a welcome o cobrem totalmente."""
        eng = self._engine
        if eng is None:
            return
        covered = False
        if not self._planet_active:
            w = self._welcome
            with contextlib.suppress(tk.TclError):
                if w is not None and w.winfo_exists() and w.winfo_ismapped():
                    covered = True
            if not covered:
                ed = self._get_ed()
                with contextlib.suppress(tk.TclError, AttributeError):
                    covered = bool(ed is not None and ed.winfo_ismapped())
        with contextlib.suppress(tk.TclError):
            eng.set_suspended(covered)

    def _deferred_engine_activity(self) -> None:
        self._activity_job = None
        if self._enabled:
            self.update_engine_activity()

    def sync_layers(self) -> None:
        """Garante a ordem de camadas: conteúdo < sidebar flutuante < barras.

        Chamado após cada troca de modo (cosmos/editor/welcome), pois elevar o
        engine ou a welcome não pode nunca encobrir o painel lateral.
        """
        if self._dock is not None:
            self._dock.raise_panel()
        self.raise_chrome()
        if self._hide_classic_welcome_cb is not None:
            self._hide_classic_welcome_cb()
        self.update_engine_activity()
        if self._activity_job is not None:
            with contextlib.suppress(tk.TclError):
                self._root.after_cancel(self._activity_job)
        with contextlib.suppress(tk.TclError):
            self._activity_job = self._root.after(
                120, self._deferred_engine_activity)

    def ensure_editor_visible(self) -> None:
        """Sai do modo cosmos se necessário (ex.: arquivo aberto pela sidebar)."""
        if self._enabled and self._planet_active:
            self.restore_from_planet()

    def _dock_relayout(self) -> None:
        if self._dock is not None:
            self._dock.relayout()

    def _place_bars(self) -> None:
        """Posiciona topbar, statusbar e tabbar após a sidebar/activity sep_bar."""
        try:
            if self._cf and self._cf.winfo_exists():
                x_off = self._cf.winfo_rootx() - self._root.winfo_rootx()
                x_off = max(0, x_off)
                bar_w = self._root.winfo_width() - x_off
            else:
                x_off = 0
                bar_w = self._root.winfo_width() or 1200
            bar_w = max(200, bar_w)
        except tk.TclError:
            x_off = 0
            bar_w = self._root.winfo_width() or 1200
        if self._topbar and self._topbar.winfo_exists():
            self._topbar.place(x=x_off, y=0, width=bar_w, height=22)
            _wlift(self._topbar)
        if self._stbar and self._stbar.winfo_exists():
            self._stbar.place(x=x_off, rely=1.0, anchor="sw",
                              width=bar_w, height=24)
            _wlift(self._stbar)

    def enable(self):
        """Ativa o componente."""
        if self._enabled:
            return
        self._enabled = True
        t = self._tmgr.theme if self._tmgr else None
        self._dark = getattr(t, "dark", True)
        p = _pal(self._dark)
        for w in (self._tabbar, self._breadcrumb):
            if w:
                with contextlib.suppress(tk.TclError):
                    w.pack_forget()
        if self._menubar is not None:
            with contextlib.suppress(tk.TclError):
                self._menubar.pack_forget()
        self._paint_frames_void()
        self._make_engine()

        if self._engine:
            self._engine.bind("<Button-1>", self._on_engine_click, add=True)

        self._build_minimize_btn(p)

        if not _PIL:
            self._root.after(
                1500,
                lambda: self.toast(
                    _i18n_t("kui.pillow_warn"), kind="warn", duration_ms=6000
                ),
            )
        self._setup_glass_pane()

        self._topbar = KTopBar(
            self._root, self._open_portal, self._get_ed, self._dark, self._profile
        )
        self._tabbar_w = KTabBar(
            self._root, self._on_tab_select, self._on_tab_close, self._dark
        )
        self._tabbar_w.set_callbacks(
            minimize_planet=self._minimize_tab_to_planet,
            dual_mode=self._open_tab_dual_mode,
        )
        self._stbar = KStatusBar(self._root, self._get_ed, self._dark)
        self._place_bars()
        _wlift(self._topbar)
        _wlift(self._stbar)
        self._refresh_tabs()
        if self._dock is not None:
            self._dock.set_dark(self._dark)
            if self._minimize_btn:
                self._dock.add_exempt(self._minimize_btn)
            self._dock.enter_float(visible=bool(self._is_sb_visible()))

        self._easter = KUEasterEggs(
            root=self._root,
            get_engine=lambda: self._engine,
            toast=lambda msg, kind, ms: self.toast(
                msg, kind=kind, duration_ms=ms),
            get_planet_active=lambda: self._planet_active,
            spawn_supernova=self._engine_spawn_supernova,
            spawn_meteor_shower=self._engine_spawn_meteor_shower,
            spawn_star_birth=self._engine_spawn_star_birth,
            spawn_nova_burst=self._engine_spawn_nova_burst,
        )

        _bi: dict[str, list[str]] = {}

        def _b(ev: str, fn) -> None:
            fid = self._root.bind(ev, fn, add=True)
            _bi.setdefault(ev, []).append(fid)

        _b("<Control-space>", lambda _: self._open_portal())
        _b("<Control-grave>", lambda _: self._open_portal())
        _b("<Control-e>", lambda _: self._toggle_side())
        _b("<Control-E>", lambda _: self._toggle_side())
        _b("<Key>", self._easter.on_key)
        _b("<Key>", self._track_keystroke)
        _b("<Button-1>", self._on_click_activity)
        _b("<Configure>", lambda _: (self._place_bars(), self._refresh_tabs(),
                                     self._dock_relayout()))
        self._root_bind_ids = _bi
        self._poll_metrics()
        self._start_fps_report()
        ed = self._get_ed()
        if ed:
            with contextlib.suppress(tk.TclError):
                ed.text.config(
                    bg=p["text_bg"],
                    fg=p["text_fg"],
                    insertbackground=p["text_cur"],
                    selectbackground=p["text_sel"],
                )

    def _build_minimize_btn(self, p: dict) -> None:
        """Cria (ou recria) o botão ⊙ Cosmos sobre a barra de abas.

        Parent é sempre self._root para sobreviver mudanças de layout.
        Usa i18n para o texto. Reconstrói automaticamente se sumir.
        """
        _safe_del(self._minimize_btn)
        self._minimize_btn = None
        label = "⊙ " + _i18n_t("kui.cosmos_mode")
        btn = tk.Label(
            self._root,
            text=label,
            bg=p["void"],
            fg=p["accent"],
            font=("Segoe UI", 8, "bold"),
            cursor="hand2",
            padx=8,
            pady=3,
            relief="flat",
        )

        btn.place(relx=1.0, y=26, anchor="ne", x=-6)
        btn.bind("<Button-1>", lambda _: self.minimize_to_planet())
        btn.bind(
            "<Enter>",
            lambda _: btn.config(
                fg=p["accent2"], bg=_lc_q(p["void"], p["accent"], 0.15)
            ),
        )
        btn.bind("<Leave>", lambda _: btn.config(fg=p["accent"], bg=p["void"]))
        self._minimize_btn = btn
        _wlift(btn)

    def _on_engine_click(self, _e=None) -> None:
        """Clique no cosmos: NADA acontece.

        A restauração é feita apenas via:
        - Clique direto no planeta (gerenciado pelo hover_tip.on_click)
        - Botão ⊙ Restaurar Editor
        Desta forma cliques fora dos planetas não interrompem tooltips.
        """

        if self._engine:
            with contextlib.suppress(tk.TclError):
                self._engine.notify_activity()

    def minimize_to_planet(self) -> None:
        """Modo planeta: eleva o engine sobre o editor via z-order puro.

        NÃO oculta o _ec - apenas coloca o canvas cosmos por cima dele.
        Isso evita qualquer problema de re-layout ao restaurar.
        """
        if not self._enabled or not self._engine:
            return
        self._planet_active = True

        filepaths = list(self._open_files) if self._open_files else []
        if not filepaths:
            ed = self._get_ed()
            if ed:
                fp = str(
                    getattr(ed, "filepath", None) or getattr(
                        ed, "path", None) or ""
                )
                if fp:
                    filepaths = [fp]

        self._engine.set_planet_restore_cb(self._on_planet_clicked)

        self._engine.enter_planet_mode(filepaths)

        self._engine.place(x=0, y=0, relwidth=1.0, relheight=1.0)

        _wlift(self._engine)
        with contextlib.suppress(tk.TclError):
            self._engine.focus_set()

        if self._glass_pane:
            self._glass_pane.destroy()
            self._glass_pane = None

        if self._welcome and self._welcome.winfo_exists():
            with contextlib.suppress(tk.TclError):
                self._welcome.place_forget()

        p = _pal(self._dark)
        if not self._minimize_btn or not self._minimize_btn.winfo_exists():
            self._build_minimize_btn(p)
        if self._minimize_btn and self._minimize_btn.winfo_exists():
            self._minimize_btn.config(
                text="⊙ " + _i18n_t("kui.restore_editor"),
                fg=p["ok"],
                bg=_lc_q(p["void"], p["ok"], 0.12),
            )
            self._minimize_btn.unbind("<Button-1>")
            self._minimize_btn.bind(
                "<Button-1>", lambda _: self.restore_from_planet())
            _wlift(self._minimize_btn)
        self.sync_layers()

        self.toast(
            "⊙ " + _i18n_t("kui.cosmos_mode") + " - " +
            _i18n_t("kui.restore_hint"),
            kind="info",
            duration_ms=3000,
        )

    def restore_from_planet(self) -> None:
        """Restaura editor do modo planeta rebaixando o engine (z-order puro).

        NÃO toca no layout do _ec - apenas abaixa o canvas cosmos.
        """
        if not self._enabled:
            return
        self._planet_active = False

        if self._engine:
            self._engine.exit_planet_mode()

        if self._engine:
            _wlower(self._engine)

        ed_restore = self._get_ed()
        if ed_restore and hasattr(ed_restore, "text"):
            with contextlib.suppress(tk.TclError):
                ed_restore.text.focus_set()

        no_editor = self._get_ed() is None
        if no_editor and self._show_welcome_cb is not None:
            with contextlib.suppress(tk.TclError):
                self._show_welcome_cb()
        elif self._welcome and self._welcome.winfo_exists():
            with contextlib.suppress(tk.TclError):
                self._welcome.place_forget()

        self._setup_glass_pane()

        for sep_bar in (self._topbar, self._tabbar_w, self._stbar):
            if sep_bar and sep_bar.winfo_exists():
                _wlift(sep_bar)
        self.sync_layers()

        p = _pal(self._dark)
        if not self._minimize_btn or not self._minimize_btn.winfo_exists():
            self._build_minimize_btn(p)
        if self._minimize_btn and self._minimize_btn.winfo_exists():
            self._minimize_btn.config(
                text="⊙ " + _i18n_t("kui.cosmos_mode"), fg=p["accent"], bg=p["void"]
            )
            self._minimize_btn.unbind("<Button-1>")
            self._minimize_btn.bind(
                "<Button-1>", lambda _: self.minimize_to_planet())
            _wlift(self._minimize_btn)

    def disable(self):
        """Desativa o componente."""
        if not self._enabled:
            return
        self._enabled = False
        for w, kw in ((self._tabbar, {"fill": "x"}), (self._breadcrumb, {"fill": "x"})):
            if w:
                with contextlib.suppress(tk.TclError):
                    w.pack(**kw)
        if self._menubar is not None:
            with contextlib.suppress(tk.TclError):
                self._menubar.pack_into(self._root)
        t = self._tmgr.theme if self._tmgr else None
        t_bg = getattr(t, "bg", "#1e1e1e") if t else "#1e1e1e"
        t_brd = getattr(t, "border", "#333") if t else "#333"
        for pw in (self._hpaned, self._vpaned):
            if pw:
                with contextlib.suppress(tk.TclError):
                    pw.config(bg=t_brd, sashwidth=6, sashrelief="flat")
        for w2 in (self._root, self._cf, self._ec):
            if w2:
                with contextlib.suppress(tk.TclError):
                    w2.configure(bg=t_bg)
        for attr in ("_engine", "_topbar", "_tabbar_w", "_stbar"):
            obj = getattr(self, attr, None)
            if obj:
                with contextlib.suppress(tk.TclError):
                    obj.stop()
                _safe_del(obj)
                setattr(self, attr, None)
        if self._glass_pane:
            self._glass_pane.destroy()
        self._glass_pane = None
        if self._dock is not None:
            was_open = self._dock.leave_float()
            cb = self._show_sb if was_open else self._hide_sb
            if cb:
                self._root.after_idle(cb)
        _safe_del(self._portal)
        self._portal = None
        _safe_del(self._welcome)
        self._welcome = None
        _safe_del(self._minimize_btn)
        self._minimize_btn = None
        if self._detach_manager is None:
            for win in list(self._detached.values()):
                _safe_del(win)
            self._detached.clear()

        for ev, fids in self._root_bind_ids.items():
            for fid in fids:
                unbind_one(self._root, ev, fid)
        self._root_bind_ids.clear()
        self._easter = None
        if self._metric_job:
            with contextlib.suppress(tk.TclError):
                self._root.after_cancel(self._metric_job)
            self._metric_job = None
        if self._fps_report_job:
            with contextlib.suppress(tk.TclError):
                self._root.after_cancel(self._fps_report_job)
            self._fps_report_job = None

    def apply_theme(self, t):
        """Executa a operação 'apply theme'.

        Args:
            t: Parâmetro da operação.
        """
        if not self._enabled:
            return
        self._dark = getattr(t, "dark", True)
        p = _pal(self._dark)
        self._paint_frames_void()
        if self._engine:
            self._engine.stop()
        _safe_del(self._engine)
        self._engine = None
        if self._glass_pane:
            self._glass_pane.destroy()
        self._glass_pane = None
        self._make_engine()
        self._setup_glass_pane()

        if self._engine:
            self._engine.bind("<Button-1>", self._on_engine_click, add=True)
            self._engine.set_planet_click_cb(self._on_solar_planet_click)
            self._engine.set_planet_restore_cb(self._on_planet_clicked)

        self._build_minimize_btn(p)

        if self._planet_active and self._engine:
            filepath = getattr(self._engine, "_planet_file", "")
            self._engine.enter_planet_mode(filepath)
        for sep_bar in (self._topbar, self._tabbar_w, self._stbar):
            if sep_bar:
                _wlift(sep_bar)
        if self._minimize_btn:
            _wlift(self._minimize_btn)
        ed = self._get_ed()
        if ed:
            with contextlib.suppress(tk.TclError):
                ed.text.config(
                    bg=p["text_bg"],
                    fg=p["text_fg"],
                    insertbackground=p["text_cur"],
                    selectbackground=p["text_sel"],
                )
        if self._welcome:
            self._welcome.stop()
        _safe_del(self._welcome)
        self._welcome = None

    def _start_fps_report(self):
        """Inicia o timer periódico de reporte de FPS para a barra de status."""

        def _report():
            if not self._enabled:
                return
            if self._engine and self._stbar:
                with contextlib.suppress(tk.TclError):
                    ft = self._engine._frame_times
                    if ft:
                        avg_ms = sum(ft) / len(ft)
                        fps = min(999, 1000.0 / max(0.1, avg_ms))
                        self._stbar.set_fps(fps)
            self._fps_report_job = self._root.after(2000, _report)

        self._fps_report_job = self._root.after(2000, _report)

    def _open_portal(self, _=None):
        """Executa a operação 'open portal'.

        Args:
            _: Parâmetro da operação.
        """
        if not self._enabled:
            return
        _safe_del(self._portal)
        self._portal = KPortal(self._root, self._commands, self._dark)

    def toggle_sidebar(self) -> None:
        """Alterna a visibilidade da barra lateral (API pública)."""
        self._toggle_side()

    def _toggle_side(self, _=None):
        """Executa a operação 'toggle side'.

        Args:
            _: Parâmetro da operação.
        """
        if not self._enabled or self._dock is None:
            return
        cb = self._hide_sb if self._dock.visible else self._show_sb
        if cb:
            cb()

    def _on_click_activity(self, _=None):
        """Trata o evento 'click activity'.

        Args:
            _: Parâmetro da operação.
        """
        if self._engine:
            with contextlib.suppress(tk.TclError):
                self._engine.notify_activity()

    def _refresh_tabs(self):
        """Sincroniza a barra de abas K-UI com as abas abertas no editor."""
        if not self._tabbar_w or not self._tabbar_w.winfo_exists():
            return
        tabs = []
        ed = self._get_ed()
        if self._open_files:
            active_path = ""
            if ed:
                active_path = str(
                    getattr(ed, "filepath", None) or getattr(
                        ed, "path", None) or ""
                )
            for fpath in self._open_files:
                tabs.append(
                    {
                        "name": Path(fpath).name if fpath else "Sem título",
                        "modified": False,
                        "active": (fpath == active_path),
                    }
                )
        elif ed:
            path = getattr(ed, "filepath", None) or getattr(ed, "path", None)
            name = Path(path).name if path else "Sem título"
            modified = (
                getattr(ed, "modified", False)
                or getattr(ed, "is_modified", lambda: False)()
            )
            tabs = [{"name": name, "modified": modified, "active": True}]
        self._tabbar_w.set_tabs(tabs)
        self._place_bars()
        if tabs:
            try:
                x_off = 0
                if self._cf and self._cf.winfo_exists():
                    x_off = max(0, self._cf.winfo_rootx() -
                                self._root.winfo_rootx())
                bar_w = max(200, self._root.winfo_width() - x_off)
            except tk.TclError:
                x_off = 0
                bar_w = max(200, self._root.winfo_width() or 1200)
            self._tabbar_w.place(x=x_off, y=22, width=bar_w, height=36)
            _wlift(self._tabbar_w)
        else:
            self._tabbar_w.place_forget()

    def _on_planet_clicked(self, filepath: str) -> None:
        """Chamado quando o usuário clica num planeta minimizado.

        Restaura o editor e gera evento para o app abrir o arquivo.
        """
        if not self._enabled:
            return

        self.restore_from_planet()

        if filepath:
            self._last_tab_action = "open_planet", filepath
            with contextlib.suppress(tk.TclError):
                self._root.event_generate("<<KUIPlanetRestore>>", when="tail")

    def _on_solar_planet_click(self, filepath: str) -> None:
        """Abre o arquivo associado a um planeta do sistema solar clicado."""
        if not self._enabled or not filepath:
            return
        self._last_tab_action = "solar_planet", filepath
        with contextlib.suppress(tk.TclError):
            self._root.event_generate("<<KUISolarPlanetClick>>", when="tail")

    def _on_tab_select(self, idx):
        """Trata o evento 'tab select'.

        Args:
            idx: Índice do item.
        """
        if not (0 <= idx < len(self._open_files)):
            return
        self._last_tab_action = "select", idx
        with contextlib.suppress(tk.TclError):
            self._root.event_generate("<<KUITabSelect>>", when="tail")

    def _on_tab_close(self, idx):
        """Fecha aba: gera evento se há lista de arquivos, senão busca comando."""
        if 0 <= idx < len(self._open_files):
            self._last_tab_action = "close", idx
            with contextlib.suppress(tk.TclError):
                self._root.event_generate("<<KUITabClose>>", when="tail")
            return

        for _, label, cb in self._commands:
            if any(
                k in label.lower()
                for k in ("fechar aba", "fechar editor", "file.close")
            ):
                with contextlib.suppress(tk.TclError):
                    cb()
                return

        self._last_tab_action = ("close", max(0, idx))
        with contextlib.suppress(tk.TclError):
            self._root.event_generate("<<KUITabClose>>", when="tail")

    def _minimize_tab_to_planet(self, _idx: int) -> None:
        """
        arquivos de _open_files são convertidos.
        """
        if not self._enabled or not self._engine:
            return

        self.minimize_to_planet()

    def _open_tab_dual_mode(self, idx: int) -> None:
        """Registra par de arquivos para modo dual e notifica o app.

        O app deve ouvir <<KUITabDual>> e ler last_tab_action = ("dual", idx).
        """
        if not self._enabled:
            return
        if 0 <= idx < len(self._open_files):
            self._last_tab_action = "dual", idx
            with contextlib.suppress(tk.TclError):
                self._root.event_generate("<<KUITabDual>>", when="tail")
            filepath = self._open_files[idx]
            name = Path(filepath).name if filepath else f"aba {idx + 1}"
            self.toast(
                f"⊞ {_i18n_t('kui.dual_mode')}: {name}", kind="info", duration_ms=2500
            )
        else:
            self._last_tab_action = "dual", 0
            with contextlib.suppress(tk.TclError):
                self._root.event_generate("<<KUITabDual>>", when="tail")

    def _engine_spawn_supernova(self) -> None:
        """Spawn visual de supernova: muitos meteoros radiais + nascimento estelar."""
        if not self._engine:
            return
        eng = self._engine
        W = eng.winfo_width() or 1200
        H = eng.winfo_height() or 800
        cx2 = W // 2
        cy2 = H // 2
        for _ in range(30):
            ang = random.uniform(0, _TAU)
            spd = random.uniform(6, 14)
            eng._meteors.append(
                {
                    "x": float(cx2 + random.uniform(-20, 20)),
                    "y": float(cy2 + random.uniform(-20, 20)),
                    "dx": spd * math.cos(ang),
                    "dy": spd * math.sin(ang),
                    "length": random.randint(60, 130),
                    "alive": True,
                    "vx_extra": 0.0,
                    "vy_extra": 0.0,
                    "col": random.choice(
                        ["accent", "accent2", "aur_a",
                            "plasma", "quantum", "star3"]
                    ),
                }
            )
        eng._star_birth.append(
            {
                "x": float(cx2),
                "y": float(cy2),
                "r": random.uniform(12, 24),
                "phase": 0.0,
                "max_phase": 220,
                "col": "star",
                "cloud_r": random.uniform(100, 160),
            }
        )

    def _engine_spawn_meteor_shower(self) -> None:
        """Chuva de meteoros comemorativa."""
        if not self._engine:
            return
        eng = self._engine
        W = eng.winfo_width() or 1200
        for _ in range(20):
            ang = math.radians(random.uniform(30, 60))
            spd = random.uniform(8, 16)
            eng._meteors.append(
                {
                    "x": float(random.uniform(W * 0.03, W * 0.87)),
                    "y": -18.0,
                    "dx": math.cos(ang) * spd,
                    "dy": math.sin(ang) * spd,
                    "length": random.randint(55, 120),
                    "alive": True,
                    "vx_extra": 0.0,
                    "vy_extra": 0.0,
                    "col": random.choice(["accent", "aur_a", "star3", "accent2"]),
                }
            )

    def _engine_spawn_star_birth(self) -> None:
        """Força nascimento estelar no centro."""
        if not self._engine:
            return
        eng = self._engine
        W = eng.winfo_width() or 1200
        H = eng.winfo_height() or 800
        eng._star_birth.append(
            {
                "x": float(W // 2 + random.randint(-100, 100)),
                "y": float(H // 2 + random.randint(-80, 80)),
                "r": random.uniform(10, 22),
                "phase": 0.0,
                "max_phase": 300,
                "col": random.choice(["star", "accent2", "aur_a"]),
                "cloud_r": random.uniform(80, 140),
            }
        )

    def _engine_spawn_nova_burst(self) -> None:
        """Nova burst: anel expansivo de meteoros + star birth + kp burst denso."""
        if not self._engine:
            return
        eng = self._engine
        W = eng.winfo_width() or 1200
        H = eng.winfo_height() or 800
        cx2 = W // 2
        cy2 = H // 2

        for i in range(16):
            ang = i * (_TAU / 16)
            spd = random.uniform(4, 10)
            eng._meteors.append(
                {
                    "x": float(cx2),
                    "y": float(cy2),
                    "dx": spd * math.cos(ang),
                    "dy": spd * math.sin(ang),
                    "length": random.randint(50, 100),
                    "alive": True,
                    "vx_extra": 0.0,
                    "vy_extra": 0.0,
                    "col": random.choice(["warn", "accent2", "plasma", "star3"]),
                }
            )
        eng._star_birth.append(
            {
                "x": float(cx2),
                "y": float(cy2),
                "r": random.uniform(8, 18),
                "phase": 0.0,
                "max_phase": 180,
                "col": "accent2",
                "cloud_r": random.uniform(70, 120),
            }
        )
        ck = ["accent2", "quantum", "aur_a", "plasma"]
        for _ in range(30):
            eng._kp_burst.append(
                {
                    "x": float(cx2 + random.uniform(-40, 40)),
                    "y": float(cy2 + random.uniform(-40, 40)),
                    "vx": random.uniform(-4.0, 4.0),
                    "vy": random.uniform(-4.0, 4.0),
                    "r": random.uniform(1.0, 3.5),
                    "ph": random.uniform(0, _TAU),
                    "life": random.uniform(0.55, 1.0),
                    "col": random.choice(ck),
                }
            )

    def _track_keystroke(self, e):
        """Rastreia teclas para métricas de KPM e notifica o engine."""
        char = getattr(e, "char", "") or ""
        if not char:
            return
        now = time.time()
        self._kpm_window.append(now)
        if char.isprintable():
            self._char_window.append(now)
        cutoff = now - 60.0
        self._kpm_window = [t for t in self._kpm_window if t >= cutoff]
        self._char_window = [t for t in self._char_window if t >= cutoff]
        if self._engine:
            with contextlib.suppress(tk.TclError):
                self._engine.notify_keystroke()

    def _poll_metrics(self):
        """Consulta métricas de uso (FPS, memória) e atualiza os indicadores."""
        if not self._enabled or not self._engine:
            return
        self.update_engine_activity()
        with contextlib.suppress(tk.TclError):
            ed = self._get_ed()
            size_kb = 0.0
            if ed:
                path = getattr(ed, "filepath", None) or getattr(
                    ed, "path", None)
                if path:
                    with contextlib.suppress(OSError):
                        size_kb = Path(path).stat().st_size / 1024.0
            now = time.time()
            cutoff = now - 60.0
            self._kpm_window = [t for t in self._kpm_window if t >= cutoff]
            self._char_window = [t for t in self._char_window if t >= cutoff]
            self._engine.set_reactive_metrics(
                file_size_kb=size_kb,
                errors=self._diag_errors,
                warnings=self._diag_warnings,
                keys_per_minute=float(len(self._kpm_window)),
                open_files=self._open_files,
                chars_per_min=len(self._char_window),
            )
        self._metric_job = self._root.after(5000, self._poll_metrics)

    def set_vim_mode(self, mode, cmd=""):
        """Define 'vim mode'.

        Args:
            mode: Parâmetro da operação.
            cmd: Parâmetro da operação.
        """
        if self._stbar and self._stbar.winfo_exists():
            self._stbar.set_vim_mode(mode, cmd)

    def notify_diagnostics(self, errors, warnings):
        """Executa a operação 'notify diagnostics'.

        Args:
            errors: Parâmetro da operação.
            warnings: Parâmetro da operação.
        """
        self._diag_errors = max(0, errors)
        self._diag_warnings = max(0, warnings)
        if self._topbar:
            with contextlib.suppress(tk.TclError):
                self._topbar.set_diagnostics(errors, warnings)

    _COSMOS_EVENTS = {"file_save": "save", "file_open": "open", "file_close": "close"}

    def on_app_event(self, event, data=None):
        """Traduz eventos do editor (salvar/abrir/fechar) em acontecimentos no cosmos."""
        kind = self._COSMOS_EVENTS.get(event)
        if kind and self._enabled and self._engine is not None:
            self._engine.cosmos_event(kind)

    def notify_open_files(self, paths):
        """Executa a operação 'notify open files'.

        Args:
            paths: Parâmetro da operação.
        """
        self._open_files = list(paths)
        self._refresh_tabs()
        self._root.after_idle(self.sync_layers)

    def toast(self, message, kind="info", duration_ms=3000):
        """Exibe uma notificação toast temporária na interface KUI."""
        if not self._enabled:
            return
        with contextlib.suppress(tk.TclError):
            KToast(self._root, message, kind, duration_ms, self._dark)

    def detach_panel(self, panel_key):
        """Destaca o painel em janela flutuante; se já destacado, traz ao foco.

        Args:
            panel_key: Chave do painel registrado em ``_factories``.
        """
        if self._detach_manager is not None:
            self._detach_manager.detach(panel_key)
            return

        if panel_key in self._detached:
            with contextlib.suppress(tk.TclError):
                self._detached[panel_key].lift()
            return
        factory = self._factories.get(panel_key)
        if not factory:
            logger.warning("detach_panel: sem factory para %r", panel_key)
            return

        def _on_close(k=panel_key):
            self._detached.pop(k, None)

        self._detached[panel_key] = KFloatWindow(
            self._root,
            panel_key.replace("_", " ").title(),
            factory,
            _on_close,
            self._dark,
        )

    def reattach_panel(self, panel_key):
        """Executa a operação 'reattach panel'.

        Args:
            panel_key: Parâmetro da operação.
        """
        if self._detach_manager is not None:
            self._detach_manager.reattach(panel_key)
            return

        win = self._detached.pop(panel_key, None)
        if win:
            _safe_del(win)

    def notify_workspace_opened(self):
        """Marca o KUI como tendo um workspace aberto."""
        self._has_ws = True

    def show_welcome(
        self,
        on_new=None,
        on_open=None,
        on_folder=None,
        on_recent=None,
        recent_files=None,
    ):
        """Executa a operação 'show welcome'.

        Args:
            on_new: Parâmetro da operação.
            on_open: Parâmetro da operação.
            on_folder: Parâmetro da operação.
            on_recent: Parâmetro da operação.
            recent_files: Parâmetro da operação.
        """
        if not self._enabled:
            return
        _safe_del(self._welcome)
        container = self._ec or self._cf or self._root
        with contextlib.suppress(tk.TclError):
            self._welcome = KWelcome(
                container,
                dark=self._dark,
                profile=self._profile,
                on_new=on_new,
                on_open=on_open,
                on_folder=on_folder,
                on_recent=on_recent,
                recent=recent_files,
            )
            self._welcome.place(x=0, y=0, relwidth=1.0, relheight=1.0)
        self.sync_layers()

    def hide_welcome(self):
        """Oculta a tela de boas-vindas cósmica e exibe o canvas de edição."""
        if self._welcome:
            with contextlib.suppress(tk.TclError):
                self._welcome.place_forget()
            self._welcome.stop()
            _safe_del(self._welcome)
            self._welcome = None
        self.sync_layers()

    def set_perf_profile(self, profile):
        """Define 'perf profile'.

        Args:
            profile: Parâmetro da operação.
        """
        self._profile = profile
        if self._engine:
            with contextlib.suppress(tk.TclError):
                self._engine.set_profile(profile)
        if self._topbar:
            with contextlib.suppress(tk.TclError):
                self._topbar.set_profile(profile)
        if self._welcome and isinstance(self._welcome, KEngine):
            with contextlib.suppress(tk.TclError):
                self._welcome.set_profile(profile)


def build_kui_theme() -> Theme:
    """Constrói e retorna o tema padrão K-UI (Obsidian/Electric-Indigo) do K-UI."""
    return Theme(
        name="kui",
        dark=True,
        bg="#020209",
        editor_bg="#03030c",
        sidebar_bg="#010108",
        tabbar_bg="#010108",
        panel_bg="#020209",
        activity_bg="#010105",
        overlay_bg="#000000",
        fg="#e8eaf6",
        fg_muted="#546e7a",
        fg_dim="#18183a",
        border="#0a0a28",
        border_focus="#7c4dff",
        gutter_bg="#03030c",
        gutter_fg="#1e2030",
        gutter_active_fg="#e8eaf6",
        line_hl="#07072a",
        line_hl2="#09093a",
        sel_bg="#18186a",
        sel_fg="#e8eaf6",
        cursor="#7c4dff",
        ruler_color="#0a0a28",
        indent_guide="#0a0a28",
        whitespace="#18183a",
        input_bg="#07072a",
        input_border="#7c4dff",
        btn_bg="#0a0a28",
        btn_fg="#e8eaf6",
        btn_hover="#18184e",
        btn_active="#282880",
        btn_primary="#7c4dff",
        btn_primary_fg="#020209",
        tab_act_bg="#03030c",
        tab_act_fg="#e8eaf6",
        tab_act_border="#7c4dff",
        tab_in_bg="#010108",
        tab_in_fg="#546e7a",
        tab_hover_bg="#0a0a20",
        tab_modified="#ff6d00",
        err_fg="#ff5252",
        warn_fg="#ff9100",
        info_fg="#00e5ff",
        hint_fg="#69ff47",
        ok_fg="#69ff47",
        err_bg="#1a0010",
        warn_bg="#1a0800",
        info_bg="#00101a",
        status_bg="#010105",
        status_fg="#e8eaf6",
        status_err_bg="#ff5252",
        status_err_fg="#020209",
        status_warn_bg="#ff9100",
        status_warn_fg="#020209",
        scroll_bg="#03030c",
        scroll_thumb="#0a0a28",
        scroll_hover="#18184e",
        diff_add_bg="#001a0a",
        diff_add_fg="#69ff47",
        diff_del_bg="#1a0008",
        diff_del_fg="#ff5252",
        diff_change_bg="#0a0a00",
        diff_change_fg="#ff9100",
        diff_hunk_bg="#03030c",
        diff_hunk_fg="#546e7a",
        minimap_bg="#010108",
        minimap_viewport="#0a0a28",
        minimap_err="#ff5252",
        minimap_warn="#ff9100",
        minimap_find="#ffd740",
        breadcrumb_bg="#03030c",
        breadcrumb_fg="#546e7a",
        breadcrumb_active_fg="#e8eaf6",
        t_keyword="#ea80fc",
        t_string="#69ff47",
        t_number="#ffd740",
        t_comment="#2e3d50",
        t_func="#40c4ff",
        t_class="#ffe57f",
        t_builtin="#18ffff",
        t_decorator="#ff4081",
        t_type="#ffe57f",
        t_operator="#e8eaf6",
        t_variable="#e8eaf6",
        t_tag="#ff4081",
        t_attr="#40c4ff",
        t_heading="#ea80fc",
        t_regex="#ff80ab",
        t_constant="#ffd740",
        t_escape="#ff4081",
        t_embedded="#40c4ff",
        t_invalid="#ff5252",
        t_namespace="#ea80fc",
        corner_radius=14,
        use_blur=False,
        blur_alpha=0.92,
        shadow_color="#000000",
        floating_bg="#06061c",
        floating_border="#18186a",
        floating_shadow="#000000",
        activity_icon_active="#e8eaf6",
        activity_icon_inactive="#1e2030",
        activity_active_indicator="#7c4dff",
    )

