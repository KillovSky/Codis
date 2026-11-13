"""Codis - Painel de Performance Customizável.

Permite ao usuário controlar individualmente quais funcionalidades
são desativadas quando o monitor automático detecta travamentos,
além de poder acionar o modo manualmente e ajustar parâmetros.

Funcionalidades controláveis:
  Editor:
    - Minimap
    - Syntax Highlighting (debounce)
    - Sticky Scroll
    - Code Folding
    - Inline Blame
    - Color Preview
    - Inlay Hints
    - Ligatures
    - Edit Heatmap
    - Code Rhythm
    - Autocomplete
  K-UI:
    - Perfil de animações (dropdown)
  Linting:
    - Lint automático ao salvar
  Monitor automático:
    - Limiar de freeze (ms)
    - Período de graça
"""

from __future__ import annotations

import contextlib
import logging
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from tkinter import ttk
from typing import Any

from src.constants import FONT_MONO, FONT_UI
from src.i18n import _t
from src.ui.grab_safe import safe_grab_set

logger = logging.getLogger("Codis.PerfPanel")


@dataclass
class _FeatureDef:
    key: str
    label: str
    desc: str
    group: str
    default: bool = True
    heavy: bool = False


_FEATURES: list[_FeatureDef] = [
    _FeatureDef(
        "perf_keep_minimap",
        "Minimap",
        "Mapa do código na barra lateral direita.",
        "editor",
        True,
        True,
    ),
    _FeatureDef(
        "perf_keep_sticky_scroll",
        "Sticky Scroll",
        "Cabeçalhos de escopo fixos no topo do editor.",
        "editor",
        True,
        True,
    ),
    _FeatureDef(
        "perf_keep_inline_blame",
        "Inline Blame",
        "Anotações git inline por linha.",
        "editor",
        False,
        True,
    ),
    _FeatureDef(
        "perf_keep_color_preview",
        "Color Preview",
        "Preview de cores em hex/rgb no gutter.",
        "editor",
        True,
        False,
    ),
    _FeatureDef(
        "perf_keep_inlay_hints",
        "Inlay Hints",
        "Dicas de tipo e parâmetro inline.",
        "editor",
        False,
        False,
    ),
    _FeatureDef(
        "perf_keep_ligatures",
        "Ligatures",
        "Ligaduras de fonte (ex: -> vira →).",
        "editor",
        True,
        False,
    ),
    _FeatureDef(
        "perf_keep_heatmap",
        "Edit Heatmap",
        "Mapa de calor de edições no gutter.",
        "editor",
        True,
        True,
    ),
    _FeatureDef(
        "perf_keep_rhythm",
        "Code Rhythm",
        "Análise de ritmo de digitação.",
        "editor",
        True,
        True,
    ),
    _FeatureDef(
        "perf_keep_autocomplete",
        "Autocomplete",
        "Sugestões de código em tempo real.",
        "editor",
        True,
        False,
    ),
    _FeatureDef(
        "perf_keep_folding",
        "Code Folding",
        "Recolher blocos de código no gutter.",
        "editor",
        True,
        False,
    ),
    _FeatureDef(
        "perf_keep_hl",
        "Syntax Highlighting",
        "Coloração de sintaxe (reduz debounce no modo perf).",
        "editor",
        True,
        False,
    ),
    _FeatureDef(
        "perf_keep_lint",
        "Lint Automático",
        "Análise de erros/avisos ao salvar ou após inatividade.",
        "lint",
        True,
        True,
    ),
    _FeatureDef(
        "perf_keep_kui_anim",
        "Animações K-UI",
        "Efeitos visuais do fundo animado (K-UI). Troca para perfil 'low'.",
        "kui",
        True,
        True,
    ),
]

_GROUP_LABELS: dict[str, str] = {
    "editor": "Editor",
    "lint": "Linting",
    "kui": "K-UI",
    "system": "Sistema",
}

_GROUP_ICONS: dict[str, str] = {
    "editor": "✏️",
    "lint": "🔍",
    "kui": "*",
    "system": "⚙️",
}


def apply_perf_custom(cfg: Any, editors: list[Any]) -> None:
    """Aplica o modo performance customizado baseado nas preferências do usuário.

    Cada feature só é desativada se o usuário marcou ``keep=False`` para ela
    (ou seja, ``cfg.get("perf_keep_X", True) == False``).

    Args:
        cfg: ConfigManager do Codis.
        editors: Lista de EditorPane ativos.
    """
    import contextlib as _ctx

    keeps: dict[str, bool] = {f.key: bool(
        cfg.get(f.key, f.default)) for f in _FEATURES}

    for pane in editors:
        if not hasattr(pane, "text"):
            continue

        if not keeps["perf_keep_minimap"]:
            with _ctx.suppress(Exception):
                # type: ignore[attr-defined]
                if getattr(pane, "_show_minimap", False):
                    pane.toggle_minimap()  # type: ignore[attr-defined]

        if not keeps["perf_keep_sticky_scroll"]:
            with _ctx.suppress(Exception):
                if hasattr(pane, "sticky_scroll") and pane.sticky_scroll:
                    pane.sticky_scroll.set_enabled(False)

        if not keeps["perf_keep_inline_blame"]:
            with _ctx.suppress(Exception):
                if hasattr(pane, "inline_blame") and pane.inline_blame:
                    pane.inline_blame.set_enabled(False)

        if not keeps["perf_keep_color_preview"]:
            with _ctx.suppress(Exception):
                if hasattr(pane, "color_preview") and pane.color_preview:
                    pane.color_preview.set_enabled(False)

        if not keeps["perf_keep_inlay_hints"]:
            with _ctx.suppress(Exception):
                if hasattr(pane, "inlay_hints") and pane.inlay_hints:
                    pane.inlay_hints.set_enabled(False)

        if not keeps["perf_keep_ligatures"]:
            with _ctx.suppress(Exception):
                if hasattr(pane, "ligature_engine") and pane.ligature_engine:
                    pane.ligature_engine.set_enabled(False)

        if not keeps["perf_keep_heatmap"]:
            with _ctx.suppress(Exception):
                if hasattr(pane, "edit_heatmap") and pane.edit_heatmap:
                    pane.edit_heatmap.stop()

        if not keeps["perf_keep_rhythm"]:
            with _ctx.suppress(Exception):
                if hasattr(pane, "code_rhythm") and pane.code_rhythm:
                    pane.code_rhythm.stop()

        if not keeps["perf_keep_autocomplete"]:
            with _ctx.suppress(Exception):
                # type: ignore[attr-defined]
                pane._autocomplete_enabled = False

        if not keeps["perf_keep_hl"]:
            with _ctx.suppress(Exception):
                if hasattr(pane, "hl") and pane.hl:
                    pane.hl._idle_ms = max(
                        getattr(pane.hl, "_idle_ms", 60), 400)

        if not keeps["perf_keep_lint"]:
            with _ctx.suppress(Exception):
                if hasattr(pane, "_lint_mgr") and pane._lint_mgr:
                    pane._lint_mgr.schedule(None, "", "text")

    if not keeps["perf_keep_kui_anim"]:
        cfg.set("kui_perf", "low")

    logger.info(
        "Modo performance customizado aplicado. Keeps=%s",
        {k: v for k, v in keeps.items() if not v},
    )


class PerformancePanel(tk.Toplevel):
    """Janela de configuração do modo performance customizável.

    Permite ao usuário escolher exatamente quais features são desativadas
    quando o monitor automático detecta travamentos ou quando aciona
    manualmente o modo performance.

    Args:
        parent: Widget pai.
        cfg: ConfigManager do Codis.
        tmgr: ThemeManager do Codis.
        on_apply_now: Callback opcional chamado quando usuário clica
                      em "Aplicar Agora".
    """

    _W, _H = 700, 680

    def __init__(
        self,
        parent: tk.Widget,
        cfg: Any,
        tmgr: Any,
        on_apply_now: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._cfg = cfg
        self._tmgr = tmgr
        self._on_apply_now = on_apply_now

        t = tmgr.theme
        self.title(_t("perf.panel_title", "Modo Performance - Codis"))
        self.geometry(f"{self._W}x{self._H}")
        self.minsize(560, 500)
        self.config(bg=t.floating_bg)
        self.resizable(True, True)
        safe_grab_set(self)

        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        x = (sw - self._W) // 2
        y = (sh - self._H) // 2
        self.geometry(f"{self._W}x{self._H}+{x}+{y}")

        self._vars: dict[str, tk.BooleanVar] = {}
        self._build(t)

    def _build(self, t: Any) -> None:
        for child in self.winfo_children():
            with contextlib.suppress(tk.TclError):
                child.destroy()

        hdr = tk.Frame(self, bg=t.activity_bg, height=56)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(
            hdr,
            text="P",
            bg=t.activity_bg,
            fg=t.accent,
            font=(FONT_UI, 20),
        ).pack(side="left", padx=(16, 6), pady=8)
        tf = tk.Frame(hdr, bg=t.activity_bg)
        tf.pack(side="left", fill="x", expand=True, pady=8)
        tk.Label(
            tf,
            text=_t("perf.panel_title_short", "Modo Performance"),
            bg=t.activity_bg,
            fg=t.fg,
            font=(FONT_UI, 13, "bold"),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            tf,
            text=_t(
                "perf.panel_subtitle",
                "Escolha o que desligar quando a performance estiver ruim",
            ),
            bg=t.activity_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            anchor="w",
        ).pack(fill="x")
        tk.Frame(self, bg=t.border, height=1).pack(fill="x")

        body = tk.Frame(self, bg=t.floating_bg)
        body.pack(fill="both", expand=True)

        canvas = tk.Canvas(body, bg=t.floating_bg, highlightthickness=0)
        vsb = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(fill="both", expand=True)

        inner = tk.Frame(canvas, bg=t.floating_bg)
        win_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _on_cfg(e: Any = None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfig(win_id, width=canvas.winfo_width())

        inner.bind("<Configure>", _on_cfg)
        canvas.bind("<Configure>", _on_cfg)

        def _on_wheel(e: tk.Event) -> None:
            canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")

        canvas.bind_all("<MouseWheel>", _on_wheel)
        self.bind("<Destroy>", lambda _: canvas.unbind_all("<MouseWheel>"))

        self._build_auto_section(inner, t)

        groups_seen: set[str] = set()
        for feature in _FEATURES:
            if feature.group not in groups_seen:
                groups_seen.add(feature.group)
                self._build_group_header(inner, t, feature.group)
            self._build_feature_row(inner, t, feature)

        self._build_kui_profile_section(inner, t)

        tk.Frame(self, bg=t.border, height=1).pack(fill="x")
        self._build_footer(t)

    def _build_auto_section(self, parent: tk.Widget, t: Any) -> None:
        sec = tk.Frame(parent, bg=t.floating_bg)
        sec.pack(fill="x", padx=20, pady=(16, 8))

        tk.Label(
            sec,
            text=_t("perf.auto_section", "Monitor Automatico"),
            bg=t.floating_bg,
            fg=t.fg,
            font=(FONT_UI, 10, "bold"),
            anchor="w",
        ).pack(anchor="w")
        tk.Label(
            sec,
            text=_t(
                "perf.auto_desc",
                "Detecta travamentos e aciona o modo performance automaticamente.",
            ),
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            anchor="w",
        ).pack(anchor="w", pady=(2, 8))

        row_auto = tk.Frame(
            sec,
            bg=t.panel_bg,
            padx=12,
            pady=10,
            highlightthickness=1,
            highlightbackground=t.border,
        )
        row_auto.pack(fill="x", pady=2)
        lf = tk.Frame(row_auto, bg=t.panel_bg)
        lf.pack(side="left", fill="x", expand=True)
        tk.Label(
            lf,
            text=_t("perf.auto_enable", "Ativar detecção automática"),
            bg=t.panel_bg,
            fg=t.fg,
            font=(FONT_UI, 9, "bold"),
            anchor="w",
        ).pack(anchor="w")
        tk.Label(
            lf,
            text=_t(
                "perf.auto_enable_desc",
                "Quando detectar freeze, desativa automaticamente as features abaixo.",
            ),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            anchor="w",
        ).pack(anchor="w")
        self._auto_var = tk.BooleanVar(
            value=bool(self._cfg.get("perf_auto_mode", True))
        )
        self._build_toggle(row_auto, self._auto_var, "perf_auto_mode")

        row_thresh = tk.Frame(
            sec,
            bg=t.panel_bg,
            padx=12,
            pady=10,
            highlightthickness=1,
            highlightbackground=t.border,
        )
        row_thresh.pack(fill="x", pady=2)
        lf2 = tk.Frame(row_thresh, bg=t.panel_bg)
        lf2.pack(side="left", fill="x", expand=True)
        tk.Label(
            lf2,
            text=_t("perf.threshold", "Sensibilidade ao Freeze (ms)"),
            bg=t.panel_bg,
            fg=t.fg,
            font=(FONT_UI, 9, "bold"),
            anchor="w",
        ).pack(anchor="w")
        tk.Label(
            lf2,
            text=_t(
                "perf.threshold_desc",
                "Quanto de atraso na UI aciona o modo performance. Menor = mais sensível.",
            ),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            anchor="w",
        ).pack(anchor="w")
        rf2 = tk.Frame(row_thresh, bg=t.panel_bg)
        rf2.pack(side="right", anchor="e")
        thresh_val = int(self._cfg.get("perf_freeze_threshold_ms", 500))
        thresh_lbl = tk.Label(
            rf2,
            text=f"{thresh_val}ms",
            bg=t.panel_bg,
            fg=t.accent,
            font=(FONT_MONO, 9, "bold"),
            width=6,
        )
        thresh_lbl.pack(side="right")
        thresh_var = tk.IntVar(value=thresh_val)
        ttk.Scale(
            rf2,
            from_=150,
            to=2000,
            orient="horizontal",
            variable=thresh_var,
            length=180,
            command=lambda v: (
                thresh_lbl.config(text=f"{int(float(v))}ms"),
                self._cfg.set("perf_freeze_threshold_ms", int(float(v))),
            ),
        ).pack(side="left")

        row_preset = tk.Frame(sec, bg=t.floating_bg)
        row_preset.pack(fill="x", pady=(8, 0))
        tk.Label(
            row_preset,
            text=_t("perf.presets", "Presets rápidos:"),
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        ).pack(side="left", padx=(0, 8))
        for label, action in [
            (_t("perf.preset_keep_all", "Manter tudo"), self._preset_keep_all),
            (_t("perf.preset_light", "Modo leve"), self._preset_light),
            (_t("perf.preset_aggressive", "Modo agressivo"), self._preset_aggressive),
            (_t("perf.preset_disable_all", "Desligar tudo"), self._preset_disable_all),
        ]:
            btn = tk.Label(
                row_preset,
                text=label,
                bg=t.sidebar_bg,
                fg=t.fg,
                font=(FONT_UI, 8),
                cursor="hand2",
                padx=8,
                pady=4,
                relief="flat",
            )
            btn.pack(side="left", padx=3)
            btn.bind("<Button-1>", lambda _, a=action: a())
            btn.bind("<Enter>", lambda _, b=btn: b.config(bg=t.btn_hover))
            btn.bind("<Leave>", lambda _, b=btn: b.config(bg=t.sidebar_bg))

    def _build_group_header(self, parent: tk.Widget, t: Any, group: str) -> None:
        label = _GROUP_LABELS.get(group, group)
        icon = _GROUP_ICONS.get(group, "-")
        fr = tk.Frame(parent, bg=t.floating_bg)
        fr.pack(fill="x", padx=20, pady=(16, 4))
        tk.Label(
            fr,
            text=f"{icon}  {label}",
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9, "bold"),
        ).pack(side="left")
        sep = tk.Frame(fr, bg=t.border, height=1)
        sep.pack(side="left", fill="x", expand=True, padx=(8, 0), pady=7)

    def _build_feature_row(self, parent: tk.Widget, t: Any, feat: _FeatureDef) -> None:
        row = tk.Frame(
            parent,
            bg=t.panel_bg,
            padx=12,
            pady=8,
            highlightthickness=1,
            highlightbackground=t.border,
        )
        row.pack(fill="x", padx=20, pady=2)

        lf = tk.Frame(row, bg=t.panel_bg)
        lf.pack(side="left", fill="x", expand=True)

        name_row = tk.Frame(lf, bg=t.panel_bg)
        name_row.pack(anchor="w")
        tk.Label(
            name_row,
            text=feat.label,
            bg=t.panel_bg,
            fg=t.fg,
            font=(FONT_UI, 9, "bold"),
            anchor="w",
        ).pack(side="left")
        if feat.heavy:
            tk.Label(
                name_row,
                text=_t("perf.heavy_badge", " pesado"),
                bg=t.t_error if hasattr(t, "t_error") else "#c0392b",
                fg="#fff",
                font=(FONT_UI, 7, "bold"),
                padx=4,
                pady=1,
            ).pack(side="left", padx=(6, 0))

        tk.Label(
            lf,
            text=feat.desc,
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            anchor="w",
        ).pack(anchor="w")

        var = tk.BooleanVar(value=bool(self._cfg.get(feat.key, feat.default)))
        self._vars[feat.key] = var
        self._build_toggle(row, var, feat.key)

    def _build_toggle(
        self, parent: tk.Widget, var: tk.BooleanVar, cfg_key: str
    ) -> None:
        t = self._tmgr.theme
        rf = tk.Frame(parent, bg=parent.cget("bg"))
        rf.pack(side="right", anchor="e")

        lbl_text = (
            _t("perf.keep", "Manter")
            if var.get()
            else _t("perf.disable_on_perf", "Desligar")
        )
        state_lbl = tk.Label(
            rf,
            text=lbl_text,
            bg=parent.cget("bg"),
            fg=t.accent if var.get() else t.fg_muted,
            font=(FONT_UI, 8),
        )
        state_lbl.pack(side="left", padx=(0, 6))

        toggle_bg = t.accent if var.get() else t.border
        toggle = tk.Label(
            rf,
            text="● " if var.get() else "○ ",
            bg=toggle_bg,
            fg="#fff" if var.get() else t.fg_muted,
            font=(FONT_UI, 10),
            cursor="hand2",
            relief="flat",
            padx=4,
            width=3,
        )
        toggle.pack(side="left")

        def _click(k=cfg_key, v=var, sl=state_lbl, tg=toggle) -> None:
            v.set(not v.get())
            val = v.get()
            sl.config(
                text=(
                    _t("perf.keep", "Manter")
                    if val
                    else _t("perf.disable_on_perf", "Desligar")
                ),
                fg=t.accent if val else t.fg_muted,
            )
            tg.config(
                text="● " if val else "○ ",
                bg=t.accent if val else t.border,
                fg="#fff" if val else t.fg_muted,
            )
            self._cfg.set(k, val)

        toggle.bind("<Button-1>", lambda _: _click())
        state_lbl.bind("<Button-1>", lambda _: _click())

    def _build_kui_profile_section(self, parent: tk.Widget, t: Any) -> None:
        sec = tk.Frame(parent, bg=t.floating_bg)
        sec.pack(fill="x", padx=20, pady=(12, 8))

        tk.Label(
            sec,
            text=_t("perf.kui_section", "Perfil K-UI no modo performance"),
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9, "bold"),
            anchor="w",
        ).pack(anchor="w")
        tk.Label(
            sec,
            text=_t(
                "perf.kui_section_desc",
                "Quando o modo performance for ativado e 'Animações K-UI' estiver em "
                "'Desligar', o K-UI troca para este perfil:",
            ),
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            anchor="w",
            wraplength=580,
        ).pack(anchor="w", pady=(2, 6))

        row = tk.Frame(
            sec,
            bg=t.panel_bg,
            padx=12,
            pady=10,
            highlightthickness=1,
            highlightbackground=t.border,
        )
        row.pack(fill="x")
        tk.Label(
            row,
            text=_t("perf.kui_fallback", "Perfil de fallback:"),
            bg=t.panel_bg,
            fg=t.fg,
            font=(FONT_UI, 9),
        ).pack(side="left")

        profiles = ["low", "minimal", "windows", "cosmic_lite", "power_saver"]
        cur_fallback = str(self._cfg.get("perf_kui_fallback", "low"))
        fb_var = tk.StringVar(value=cur_fallback)
        fb_cb = ttk.Combobox(
            row,
            textvariable=fb_var,
            values=profiles,
            state="readonly",
            width=16,
            font=(FONT_UI, 9),
        )
        fb_cb.pack(side="right")
        fb_cb.bind(
            "<<ComboboxSelected>>",
            lambda _: self._cfg.set("perf_kui_fallback", fb_var.get()),
        )

    def _build_footer(self, t: Any) -> None:
        footer = tk.Frame(self, bg=t.floating_bg, pady=12)
        footer.pack(fill="x", padx=20)

        apply_btn = tk.Button(
            footer,
            text=_t("perf.apply_now", "Aplicar Agora"),
            bg=t.accent,
            fg="#000",
            activebackground=t.accent,
            relief="flat",
            font=(FONT_UI, 10, "bold"),
            padx=16,
            pady=6,
            cursor="hand2",
            command=self._on_apply_now_clicked,
        )
        apply_btn.pack(side="left", padx=(0, 8))

        restore_btn = tk.Button(
            footer,
            text=_t("perf.restore_all", "↩  Restaurar Tudo"),
            bg=t.sidebar_bg,
            fg=t.fg,
            activebackground=t.btn_hover,
            relief="flat",
            font=(FONT_UI, 9),
            padx=12,
            pady=6,
            cursor="hand2",
            command=self._preset_keep_all,
        )
        restore_btn.pack(side="left", padx=(0, 8))

        self._status_lbl = tk.Label(
            footer,
            text="",
            bg=t.floating_bg,
            fg=t.t_func,
            font=(FONT_UI, 8),
        )
        self._status_lbl.pack(side="left", padx=8)

        close_btn = tk.Button(
            footer,
            text=_t("ui.close", "Fechar"),
            bg=t.sidebar_bg,
            fg=t.fg,
            activebackground=t.btn_hover,
            relief="flat",
            font=(FONT_UI, 9),
            padx=12,
            pady=6,
            cursor="hand2",
            command=self.destroy,
        )
        close_btn.pack(side="right")

    def _preset_keep_all(self) -> None:
        """Marca todas as features para manter (desativa desligamento no modo perf)."""
        for feat in _FEATURES:
            self._cfg.set(feat.key, True)
            if feat.key in self._vars:
                self._vars[feat.key].set(True)
        self._rebuild()
        self._show_status(
            _t("perf.preset_applied", "Preset aplicado: manter tudo"))

    def _preset_light(self) -> None:
        """Desativa apenas as features marcadas como 'heavy'."""
        for feat in _FEATURES:
            val = not feat.heavy
            self._cfg.set(feat.key, val)
            if feat.key in self._vars:
                self._vars[feat.key].set(val)
        self._rebuild()
        self._show_status(
            _t("perf.preset_applied", "Preset aplicado: modo leve"))

    def _preset_aggressive(self) -> None:
        """Desativa todas as features, mantendo apenas o essencial."""
        always_keep = {"perf_keep_hl",
                       "perf_keep_folding", "perf_keep_autocomplete"}
        for feat in _FEATURES:
            val = feat.key in always_keep
            self._cfg.set(feat.key, val)
            if feat.key in self._vars:
                self._vars[feat.key].set(val)
        self._rebuild()
        self._show_status(
            _t("perf.preset_applied", "Preset aplicado: modo agressivo"))

    def _preset_disable_all(self) -> None:
        """Desativa absolutamente tudo no modo performance."""
        for feat in _FEATURES:
            self._cfg.set(feat.key, False)
            if feat.key in self._vars:
                self._vars[feat.key].set(False)
        self._rebuild()
        self._show_status(
            _t("perf.preset_applied", "Preset aplicado: desligar tudo"))

    def _rebuild(self) -> None:
        """Reconstrói toda a UI para refletir o estado atual dos vars."""
        t = self._tmgr.theme
        for w in self.winfo_children():
            w.destroy()
        self._vars.clear()
        self._build(t)

    def _on_apply_now_clicked(self) -> None:
        if self._on_apply_now:
            self._on_apply_now()
            self._show_status(
                _t("perf.mode_applied", "Modo performance aplicado!"))
        else:
            self._show_status(
                _t("perf.no_callback", "Callback não configurado."))

    def _show_status(self, msg: str) -> None:
        if hasattr(self, "_status_lbl"):
            self._status_lbl.config(text=msg)
            self.after(
                3000,
                lambda: (
                    self._status_lbl.config(text="")
                    if self._status_lbl.winfo_exists()
                    else None
                ),
            )


def open_performance_panel(
    parent: tk.Widget,
    cfg: Any,
    tmgr: Any,
    on_apply_now: Callable[[], None] | None = None,
) -> PerformancePanel:
    """Abre o painel de configuração de performance.

    Args:
        parent: Widget pai Tkinter.
        cfg: ConfigManager do Codis.
        tmgr: ThemeManager do Codis.
        on_apply_now: Callback chamado ao clicar em "Aplicar Agora".

    Returns:
        PerformancePanel: Instância da janela criada.
    """
    panel = PerformancePanel(parent, cfg, tmgr, on_apply_now=on_apply_now)
    return panel
