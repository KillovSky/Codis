#!/usr/bin/env python3
"""Assistente de primeira execução do Codis.

Implementa ``FirstRunWizard``, o wizard de configuração inicial exibido
na primeira vez que o usuário abre o aplicativo, guiando a escolha de
tema, fonte, linguagem e configurações de IA.
"""

from __future__ import annotations

import contextlib
import logging
import tkinter as tk
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from tkinter import ttk
from typing import Any

from src.config import ConfigManager
from src._shells import SHELL_OPTIONS, get_default_shell
from src.constants import (
    APP_NAME,
    FONT_MONO,
    FONT_SIZE,
    FONT_UI,
    TAB_SIZE,
)
from src.i18n import I18n, _t
from src.theme.base import THEME_ORDER, THEMES
from src.theme.manager import ThemeManager, _patch_theme
from src.ui.dropdown import DropDown
from src.ui.grab_safe import safe_grab_set

logger = logging.getLogger("Codis.Wizard")


@dataclass(frozen=True)
class WizardToggle:
    """Opção liga/desliga exibida no wizard e gravada na configuração.

    Attributes:
        key: chave na configuração do Codis.
        default: valor usado quando a chave ainda não existe.
        icon: ícone do cartão.
        label_key: chave de tradução do título (``label`` é o texto padrão).
        desc_key: chave de tradução da descrição (vazia se não houver).
        sum_key: chave de tradução do rótulo no resumo (usa ``label`` se vazia).
    """

    key: str
    default: bool
    icon: str
    label_key: str
    label: str
    desc_key: str = ""
    desc: str = ""
    sum_key: str = ""


FEATURE_TOGGLES: tuple[WizardToggle, ...] = (
    WizardToggle("lint_enabled", True, "\U0001f50d", "wiz.feat_lint", "Lint", "wiz.feat_lint_desc", "", "wiz.sum_lint"),
    WizardToggle("autocomplete", True, "\u2702", "wiz.feat_ac", "Autocomplete", "wiz.feat_ac_desc", "", "wiz.sum_ac"),
    WizardToggle("minimap", True, "\U0001f5fa", "wiz.feat_mm", "Minimap", "wiz.feat_mm_desc", "", "wiz.sum_mm"),
    WizardToggle("format_on_save", False, "\u2728", "wiz.feat_fmt", "Format on save", "wiz.feat_fmt_desc", "", "wiz.sum_fmt"),
    WizardToggle(
        "lsp_enabled", True, "\U0001f9e0", "wiz.feat_lsp", "Inteligência de código (LSP)",
        "wiz.feat_lsp_desc", "Autocompletar, ir para definição e diagnósticos usando servidores de linguagem.",
    ),
    WizardToggle(
        "line_numbers", True, "\U0001f522", "wiz.feat_linenum", "Números de linha",
        "wiz.feat_linenum_desc", "Mostra a numeração na margem do editor.",
    ),
    WizardToggle(
        "word_wrap", True, "\u21a9", "wiz.feat_wrap", "Quebra automática de linha",
        "wiz.feat_wrap_desc", "Quebra linhas longas na largura do editor em vez de rolar na horizontal.",
    ),
)

EXTRA_TOGGLES: tuple[WizardToggle, ...] = (
    WizardToggle("timeline_enabled", True, "\U0001f552", "wiz.timeline", "Timeline", "", "", "wiz.sum_timeline"),
    WizardToggle("autosave", False, "\U0001f4be", "wiz.autosave", "Salvamento automático", "", "", "wiz.sum_autosave"),
    WizardToggle(
        "session_restore", False, "\U0001f4c2", "wiz.session_restore", "Restaurar sessão",
        "wiz.session_restore_desc", "Reabre os arquivos e o workspace da última vez ao iniciar.",
    ),
    WizardToggle(
        "auto_check_updates", True, "\U0001f504", "wiz.auto_updates", "Verificar atualizações",
        "wiz.auto_updates_desc", "Procura novas versões do Codis ao iniciar.",
    ),
)

A11Y_TOGGLES: tuple[WizardToggle, ...] = (
    WizardToggle(
        "a11y_screen_reader", False, "\U0001f50a", "wiz.a11y_tts", "Leitura em voz alta (TTS)",
        "wiz.a11y_tts_desc", "Anuncia em voz alta o que recebe foco ou fica sob o mouse. Desligado por padrão.",
    ),
    WizardToggle(
        "a11y_high_contrast", False, "\u25d0", "wiz.a11y_contrast", "Alto contraste",
        "wiz.a11y_contrast_desc", "Troca para um tema de alto contraste.",
    ),
    WizardToggle(
        "a11y_reduce_motion", False, "\U0001f3ac", "wiz.a11y_motion", "Reduzir animações",
        "wiz.a11y_motion_desc", "Remove transições e deixa a interface mais leve em máquinas lentas.",
    ),
    WizardToggle(
        "a11y_large_ui_font", False, "\U0001f524", "wiz.a11y_large", "Fonte grande na interface",
        "wiz.a11y_large_desc", "Aumenta o texto de menus, painéis e botões.",
    ),
    WizardToggle(
        "a11y_keyboard_nav", True, "\u2328", "wiz.a11y_keys", "Navegação por teclado",
        "wiz.a11y_keys_desc", "Permite percorrer painéis e botões com Tab e atalhos.",
    ),
)

ALL_TOGGLES: tuple[WizardToggle, ...] = FEATURE_TOGGLES + EXTRA_TOGGLES + A11Y_TOGGLES


def initial_toggle_values(cfg_get: Callable[[str, Any], Any]) -> dict[str, bool]:
    """Valores iniciais das opções: o que já está salvo, ou o padrão da opção."""
    return {tg.key: bool(cfg_get(tg.key, tg.default)) for tg in ALL_TOGGLES}


def apply_toggles(cfg: Any, values: Mapping[str, bool]) -> None:
    """Grava na configuração os valores das opções do wizard (sem salvar em disco)."""
    for tg in ALL_TOGGLES:
        if tg.key in values:
            cfg.set(tg.key, bool(values[tg.key]))


def _lighten(hex_color: str, amount: int = 20) -> str:
    try:
        hex_color = hex_color.lstrip("#")
        r, g, b = (int(hex_color[i: i + 2], 16) for i in (0, 2, 4))
        return f"#{min(255, r + amount):02x}{min(255, g + amount):02x}{min(255, b + amount):02x}"
    except Exception:
        return hex_color


class FirstRunWizard(tk.Toplevel):
    """Assistente de configuracao inicial exibido na primeira execucao do Codis."""

    STEPS = 9

    _NAV_KEYS = [
        "wiz.nav_welcome",
        "wiz.nav_lang",
        "wiz.nav_theme",
        "wiz.nav_font",
        "wiz.nav_indent",
        "wiz.nav_features",
        "wiz.nav_extra",
        "wiz.nav_voice",
        "wiz.nav_summary",
    ]
    _NAV_ICONS = [
        "\u2736",
        "\U0001f310",
        "\U0001f3a8",
        "\u2712",
        "\u21e5",
        "\u2699",
        "\U0001f195",
        "\U0001f50a",
        "\u2705",
    ]

    def __init__(
        self,
        parent: tk.Tk,
        cfg: ConfigManager,
        tmgr: ThemeManager,
        on_finish: Callable,
    ) -> None:
        """Inicializa o wizard de configuração inicial do Codis."""
        t = tmgr.theme
        super().__init__(parent)
        self.title(f"{APP_NAME} \u2014 Setup")
        self.resizable(True, True)
        self.config(bg=t.floating_bg)
        self.wm_attributes("-topmost", True)
        safe_grab_set(self)

        self._cfg = cfg
        self._tmgr = tmgr
        self._on_finish = on_finish
        self._step = 0
        self._nav_labels: list[tuple] = []

        self._locale_var = tk.StringVar(value=cfg.get("language", "en"))
        self._theme_var = tk.StringVar(value=cfg.get("theme", "fluent_dark"))
        self._font_var = tk.StringVar(value=cfg.get("font_family", FONT_MONO))
        self._size_var = tk.IntVar(value=cfg.get("font_size", FONT_SIZE))
        self._tab_var = tk.IntVar(value=cfg.get("tab_size", TAB_SIZE))
        initial = initial_toggle_values(cfg.get)
        self._opts: dict[str, tk.BooleanVar] = {
            key: tk.BooleanVar(value=value) for key, value in initial.items()
        }
        self._opts["lint_enabled"]
        self._opts["autocomplete"]
        self._opts["minimap"]
        self._fmt_var = self._opts["format_on_save"]
        self._opts["timeline_enabled"]
        self._autosave_var = self._opts["autosave"]
        self._tts_bridge: Any = None
        self._tts_status: tk.Label | None = None
        self._shell_var2 = tk.StringVar(
            value=cfg.get("default_shell") or get_default_shell())

        I18n.get_instance().on_change(self._on_locale_changed)

        self._build_layout()
        self._show_step(0)

        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        w = min(1100, max(820, int(sw * 0.78)))
        h = min(720, max(540, int(sh * 0.82)))
        self.minsize(720, 500)
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

    def _on_locale_changed(self) -> None:
        with contextlib.suppress(tk.TclError):
            self._rebuild_nav_text()
            self._rebuild_footer_text()
            self._show_step(self._step)

    def _build_layout(self) -> None:
        t = self._tmgr.theme
        root_frame = tk.Frame(self, bg=t.floating_bg, highlightthickness=0)
        root_frame.pack(fill="both", expand=True)

        self._sidebar = tk.Frame(
            root_frame, bg=t.activity_bg, width=190, highlightthickness=0, bd=0
        )
        self._sidebar.pack(side="left", fill="y")
        self._sidebar.pack_propagate(False)

        right = tk.Frame(root_frame, bg=t.floating_bg, highlightthickness=0)
        right.pack(side="left", fill="both", expand=True)

        self._top_bar = tk.Frame(
            right, bg=t.activity_bg, height=50, highlightthickness=0, bd=0
        )
        self._top_bar.pack(fill="x")
        self._top_bar.pack_propagate(False)

        prog_track = tk.Frame(right, bg=t.border, height=3,
                              highlightthickness=0, bd=0)
        prog_track.pack(fill="x")
        self._prog_bar = tk.Frame(
            prog_track, bg=t.btn_primary, height=3, highlightthickness=0, bd=0
        )
        self._prog_bar.place(x=0, y=0, relwidth=1 / self.STEPS, relheight=1)

        canvas_wrap = tk.Frame(right, bg=t.floating_bg, highlightthickness=0)
        canvas_wrap.pack(fill="both", expand=True)

        self._canvas = tk.Canvas(
            canvas_wrap, bg=t.floating_bg, highlightthickness=0, bd=0
        )
        self._vbar = tk.Scrollbar(
            canvas_wrap, orient="vertical", command=self._canvas.yview
        )
        self._canvas.configure(yscrollcommand=self._vbar.set)
        self._vbar.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)

        self._content = tk.Frame(
            self._canvas, bg=t.floating_bg, highlightthickness=0)
        self._canvas_win = self._canvas.create_window(
            (0, 0), window=self._content, anchor="nw"
        )

        self._content.bind(
            "<Configure>",
            lambda _: self._canvas.configure(
                scrollregion=self._canvas.bbox("all")),
        )
        self._canvas.bind(
            "<Configure>",
            lambda e: self._canvas.itemconfig(self._canvas_win, width=e.width),
        )
        self._canvas.bind("<MouseWheel>", self._on_scroll)
        self._content.bind("<MouseWheel>", self._on_scroll)

        self._footer = tk.Frame(
            right, bg=t.floating_bg, height=56, highlightthickness=0, bd=0
        )
        self._footer.pack(fill="x")
        self._footer.pack_propagate(False)

        self._build_sidebar()
        self._build_topbar()
        self._build_footer()

    def _on_scroll(self, evt: Any) -> None:
        self._canvas.yview_scroll(int(-1 * (evt.delta / 120)), "units")

    def _build_sidebar(self) -> None:
        t = self._tmgr.theme
        for w in self._sidebar.winfo_children():
            w.destroy()
        self._nav_labels.clear()

        logo_frame = tk.Frame(
            self._sidebar, bg=t.activity_bg, highlightthickness=0)
        logo_frame.pack(fill="x", pady=(18, 4))
        tk.Label(
            logo_frame,
            text="\u2b21",
            bg=t.activity_bg,
            fg=t.btn_primary,
            font=(FONT_UI, 26),
        ).pack()
        tk.Label(
            logo_frame,
            text=APP_NAME,
            bg=t.activity_bg,
            fg=t.fg,
            font=(FONT_UI, 12, "bold"),
        ).pack()

        tk.Frame(self._sidebar, bg=t.border, height=1).pack(
            fill="x", padx=14, pady=(8, 10)
        )

        for i, (icon, key) in enumerate(
            zip(self._NAV_ICONS, self._NAV_KEYS, strict=False)
        ):
            row = tk.Frame(
                self._sidebar, bg=t.activity_bg, highlightthickness=0, cursor="hand2"
            )
            row.pack(fill="x")
            accent = tk.Frame(row, width=3, bg=t.activity_bg,
                              highlightthickness=0)
            accent.pack(side="left", fill="y")
            inner = tk.Frame(row, bg=t.activity_bg, highlightthickness=0)
            inner.pack(side="left", fill="x",
                       expand=True, padx=(6, 10), pady=5)
            icon_lbl = tk.Label(
                inner,
                text=icon,
                bg=t.activity_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 12),
                width=2,
            )
            icon_lbl.pack(side="left")
            text_lbl = tk.Label(
                inner,
                text=_t(key),
                bg=t.activity_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 9),
                anchor="w",
            )
            text_lbl.pack(side="left", fill="x", expand=True)

            self._nav_labels.append((row, accent, icon_lbl, text_lbl))

            def _go(step: int = i) -> None:
                if step <= self._step:
                    self._show_step(step)

            for widget in (row, inner, icon_lbl, text_lbl):
                widget.bind("<Button-1>", lambda _, s=i: _go(s), add="+")

        tk.Frame(self._sidebar, bg=t.activity_bg).pack(
            fill="both", expand=True)
        from src.constants import APP_VERSION as _av

        tk.Label(
            self._sidebar,
            text=f"v{_av}",
            bg=t.activity_bg,
            fg=t.border,
            font=(FONT_UI, 8),
        ).pack(pady=(0, 10))

    def _build_topbar(self) -> None:
        t = self._tmgr.theme
        for w in self._top_bar.winfo_children():
            w.destroy()
        self._top_title = tk.Label(
            self._top_bar,
            text="",
            bg=t.activity_bg,
            fg=t.fg,
            font=(FONT_UI, 11, "bold"),
        )
        self._top_title.pack(side="left", padx=20, pady=12)
        self._step_lbl = tk.Label(
            self._top_bar, text="", bg=t.activity_bg, fg=t.fg_muted, font=(FONT_UI, 9)
        )
        self._step_lbl.pack(side="right", padx=20)

    def _build_footer(self) -> None:
        t = self._tmgr.theme
        for w in self._footer.winfo_children():
            w.destroy()
        tk.Frame(self._footer, bg=t.border, height=1).pack(fill="x")
        btn_row = tk.Frame(self._footer, bg=t.floating_bg)
        btn_row.pack(fill="x", padx=20, pady=8)

        self._back_btn = tk.Label(
            btn_row,
            text=_t("wiz.back"),
            bg=t.btn_bg,
            fg=t.fg,
            font=(FONT_UI, 9),
            padx=18,
            pady=6,
            cursor="hand2",
            relief="flat",
        )
        self._back_btn.pack(side="left")
        self._back_btn.bind("<Button-1>", lambda _: self._prev())

        self._skip_btn = tk.Label(
            btn_row,
            text=_t("wiz.skip"),
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            cursor="hand2",
        )
        self._skip_btn.pack(side="right", padx=12)
        self._skip_btn.bind("<Button-1>", lambda _: self._finish())

        self._next_btn = tk.Label(
            btn_row,
            text=_t("wiz.next"),
            bg=t.btn_primary,
            fg="#000",
            font=(FONT_UI, 9, "bold"),
            padx=22,
            pady=6,
            cursor="hand2",
            relief="flat",
        )
        self._next_btn.pack(side="right")
        self._next_btn.bind("<Button-1>", lambda _: self._next())

    def _rebuild_nav_text(self) -> None:
        with contextlib.suppress(tk.TclError):
            for i, (_, _, _, text_lbl) in enumerate(self._nav_labels):
                text_lbl.config(text=_t(self._NAV_KEYS[i]))

    def _rebuild_footer_text(self) -> None:
        with contextlib.suppress(tk.TclError):
            is_last = self._step == self.STEPS - 1
            self._next_btn.config(text=_t("wiz.finish")
                                  if is_last else _t("wiz.next"))
            self._back_btn.config(text=_t("wiz.back"))
            self._skip_btn.config(text=_t("wiz.skip"))
            self._step_lbl.config(
                text=_t("wiz.step", n=self._step + 1, total=self.STEPS)
            )

    def _update_nav_highlight(self, step: int) -> None:
        if not self._nav_labels:
            return
        t = self._tmgr.theme
        with contextlib.suppress(tk.TclError):
            for i, (row, accent, icon_lbl, text_lbl) in enumerate(self._nav_labels):
                if i == step:
                    for w in [row, accent, icon_lbl, text_lbl, *row.winfo_children()]:
                        with contextlib.suppress(tk.TclError):
                            if w is accent:
                                w.config(bg=t.btn_primary)
                            else:
                                w.config(bg=t.activity_bg)
                    icon_lbl.config(fg=t.btn_primary)
                    text_lbl.config(fg=t.fg, font=(FONT_UI, 9, "bold"))
                elif i < step:
                    for w in [row, accent, icon_lbl, text_lbl, *row.winfo_children()]:
                        with contextlib.suppress(tk.TclError):
                            if w is accent:
                                w.config(bg=t.ok_fg)
                            else:
                                w.config(bg=t.activity_bg)
                    icon_lbl.config(fg=t.ok_fg)
                    text_lbl.config(fg=t.fg_muted, font=(FONT_UI, 9))
                else:
                    for w in [row, accent, icon_lbl, text_lbl, *row.winfo_children()]:
                        with contextlib.suppress(tk.TclError):
                            w.config(bg=t.activity_bg)
                    icon_lbl.config(fg=t.fg_muted)
                    text_lbl.config(fg=t.fg_muted, font=(FONT_UI, 9))

    def _clear(self) -> None:
        for w in self._content.winfo_children():
            w.destroy()
        self._canvas.yview_moveto(0)

    def _show_step(self, step: int) -> None:
        self._step = step
        self._clear()
        t = self._tmgr.theme

        self._prog_bar.place(relwidth=(step + 1) / self.STEPS)
        self._update_nav_highlight(step)

        is_last = step == self.STEPS - 1
        nav_key = (
            self._NAV_KEYS[step] if step < len(
                self._NAV_KEYS) else self._NAV_KEYS[-1]
        )
        with contextlib.suppress(tk.TclError):
            self._top_title.config(text=_t(nav_key))
            self._step_lbl.config(
                text=_t("wiz.step", n=step + 1, total=self.STEPS))
            self._next_btn.config(
                text=_t("wiz.finish") if is_last else _t("wiz.next"),
                bg=t.ok_fg if is_last else t.btn_primary,
            )
            self._back_btn.config(
                fg=t.fg if step > 0 else t.fg_muted,
                cursor="hand2" if step > 0 else "arrow",
            )

        dispatch = {
            0: self._step_welcome,
            1: self._step_lang,
            2: self._step_theme,
            3: self._step_font,
            4: self._step_indent,
            5: self._step_features,
            6: self._step_extra,
            7: self._step_voice,
            8: self._step_summary,
        }
        dispatch.get(step, lambda _: None)(t)

    def _section_title(self, text: str, t: Any) -> None:
        tk.Label(
            self._content,
            text=text,
            bg=t.floating_bg,
            fg=t.fg,
            font=(FONT_UI, 15, "bold"),
            anchor="w",
        ).pack(fill="x", padx=28, pady=(24, 4))

    def _section_desc(self, text: str, t: Any) -> None:
        tk.Label(
            self._content,
            text=text,
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
            anchor="w",
            justify="left",
            wraplength=680,
        ).pack(fill="x", padx=28, pady=(0, 14))

    def _divider(self, t: Any) -> None:
        tk.Frame(self._content, bg=t.border, height=1).pack(
            fill="x", padx=28, pady=8)

    def _step_welcome(self, t: Any) -> None:
        outer = tk.Frame(self._content, bg=t.floating_bg)
        outer.pack(fill="both", expand=True, padx=28, pady=(20, 16))

        hero = tk.Frame(
            outer, bg=t.activity_bg, highlightthickness=1, highlightbackground=t.border
        )
        hero.pack(fill="x", pady=(0, 20))

        inner_hero = tk.Frame(hero, bg=t.activity_bg)
        inner_hero.pack(padx=36, pady=26)
        tk.Label(
            inner_hero,
            text="\u2b21",
            bg=t.activity_bg,
            fg=t.btn_primary,
            font=(FONT_UI, 36),
        ).pack()
        tk.Label(
            inner_hero,
            text=_t("wiz.welcome_title"),
            bg=t.activity_bg,
            fg=t.fg,
            font=(FONT_UI, 19, "bold"),
        ).pack(pady=(4, 0))
        tk.Label(
            inner_hero,
            text=_t("wiz.welcome_sub"),
            bg=t.activity_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 10),
        ).pack(pady=(4, 0))

        feat_frame = tk.Frame(outer, bg=t.floating_bg)
        feat_frame.pack(fill="x")
        feat_frame.columnconfigure(0, weight=1)
        feat_frame.columnconfigure(1, weight=1)

        feats = [_t(f"wiz.welcome_feat{i}") for i in range(1, 6)]
        for i, feat in enumerate(feats):
            r, c = divmod(i, 2)
            card = tk.Frame(
                feat_frame,
                bg=t.activity_bg,
                highlightthickness=1,
                highlightbackground=t.border,
            )
            card.grid(row=r, column=c, padx=5, pady=5, sticky="ew")
            tk.Label(
                card,
                text=feat,
                bg=t.activity_bg,
                fg=t.fg,
                font=(FONT_UI, 9),
                anchor="w",
                padx=14,
                pady=10,
            ).pack(fill="x")

    def _step_lang(self, t: Any) -> None:
        self._section_title(_t("wiz.lang_title"), t)
        self._section_desc(_t("wiz.lang_desc"), t)

        sf = tk.Frame(self._content, bg=t.floating_bg)
        sf.pack(fill="both", expand=True, padx=28)
        canvas = tk.Canvas(sf, bg=t.floating_bg, highlightthickness=0)
        vsb = tk.Scrollbar(sf, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        inner = tk.Frame(canvas, bg=t.floating_bg)
        canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.columnconfigure(0, weight=1)
        inner.columnconfigure(1, weight=1)

        for idx, (code, name) in enumerate(I18n.AVAILABLE_LOCALES):
            r, c = divmod(idx, 2)
            selected = code == self._locale_var.get()
            card = tk.Frame(
                inner,
                bg=t.activity_bg if selected else t.floating_bg,
                highlightthickness=1,
                highlightbackground=t.btn_primary if selected else t.border,
                cursor="hand2",
            )
            card.grid(row=r, column=c, padx=4, pady=3, sticky="ew")
            inner_card = tk.Frame(card, bg=card.cget("bg"))
            inner_card.pack(fill="x", padx=12, pady=8)
            tk.Label(
                inner_card,
                text=name,
                bg=inner_card.cget("bg"),
                fg=t.btn_primary if selected else t.fg,
                font=(FONT_UI, 10, "bold" if selected else "normal"),
                anchor="w",
            ).pack(side="left")
            tk.Label(
                inner_card,
                text=f"  ({code})",
                bg=inner_card.cget("bg"),
                fg=t.fg_muted,
                font=(FONT_UI, 8),
                anchor="w",
            ).pack(side="left")
            if selected:
                tk.Label(
                    inner_card,
                    text="\u2713",
                    bg=inner_card.cget("bg"),
                    fg=t.btn_primary,
                    font=(FONT_UI, 12, "bold"),
                ).pack(side="right")

            def _pick(c2=code):
                self._apply_locale(c2)
                self._show_step(1)

            for w in [card, inner_card] + list(inner_card.winfo_children()):
                w.bind("<Button-1>", lambda _, fn=_pick: fn(), add="+")

        inner.bind(
            "<Configure>", lambda e: canvas.configure(
                scrollregion=canvas.bbox("all"))
        )
        canvas.bind(
            "<MouseWheel>",
            lambda e: canvas.yview_scroll(int(-1 * e.delta / 120), "units"),
        )

    def _step_theme(self, t: Any) -> None:
        self._section_title(_t("wiz.theme_title"), t)
        self._section_desc(_t("wiz.theme_desc"), t)

        grid = tk.Frame(self._content, bg=t.floating_bg)
        grid.pack(fill="both", expand=True, padx=28, pady=(0, 16))
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)
        grid.columnconfigure(2, weight=1)

        for i, name in enumerate(THEME_ORDER):
            th = THEMES.get(name)
            if not th:
                continue
            _patch_theme(th)
            r, c = divmod(i, 3)
            selected = name == self._theme_var.get()

            card = tk.Frame(
                grid,
                bg=th.editor_bg,
                highlightthickness=2,
                highlightbackground=t.btn_primary if selected else th.border,
                cursor="hand2",
            )
            card.grid(row=r, column=c, padx=5, pady=5, sticky="nsew")

            hdr = tk.Frame(card, bg=th.activity_bg)
            hdr.pack(fill="x")
            tk.Label(
                hdr,
                text=name,
                bg=th.activity_bg,
                fg=th.fg,
                font=(FONT_UI, 8, "bold"),
                padx=8,
                pady=5,
                anchor="w",
            ).pack(side="left", fill="x", expand=True)
            if selected:
                tk.Label(
                    hdr,
                    text="\u2713",
                    bg=th.activity_bg,
                    fg=t.btn_primary,
                    font=(FONT_UI, 9, "bold"),
                    padx=6,
                ).pack(side="right")

            code_f = tk.Frame(card, bg=th.editor_bg, pady=5, padx=8)
            code_f.pack(fill="x")
            row_f = tk.Frame(code_f, bg=th.editor_bg)
            row_f.pack(anchor="w")
            for color, txt in [
                (th.t_keyword, "def "),
                (th.t_func, "hello"),
                (th.fg, "():"),
            ]:
                tk.Label(
                    row_f, text=txt, bg=th.editor_bg, fg=color, font=(
                        FONT_MONO, 8)
                ).pack(side="left")
            tk.Label(
                code_f,
                text='    print("Hi!")',
                bg=th.editor_bg,
                fg=th.t_string,
                font=(FONT_MONO, 8),
                anchor="w",
            ).pack(anchor="w")

            strip = tk.Frame(card, bg=th.editor_bg, pady=3)
            strip.pack(fill="x", padx=8)
            for colour in (
                th.t_keyword,
                th.t_func,
                th.t_string,
                th.t_comment,
                th.t_number,
            ):
                tk.Frame(strip, bg=colour, width=10, height=10).pack(
                    side="left", padx=2
                )

            def _pick(n=name):
                self._theme_var.set(n)
                self._tmgr.set(n)
                _patch_theme(self._tmgr.theme)
                self._show_step(2)

            for w in [card, hdr, code_f, strip, row_f]:
                w.bind("<Button-1>", lambda _, fn=_pick: fn(), add="+")
                for child in getattr(w, "winfo_children", list)():
                    child.bind("<Button-1>", lambda _, fn=_pick: fn(), add="+")

    def _step_font(self, t: Any) -> None:
        self._section_title(_t("wiz.font_title"), t)
        self._section_desc(_t("wiz.font_desc"), t)

        body = tk.Frame(self._content, bg=t.floating_bg)
        body.pack(fill="x", padx=28)

        tk.Label(
            body,
            text=_t("wiz.font_family"),
            bg=t.floating_bg,
            fg=t.fg,
            font=(FONT_UI, 9),
            anchor="w",
        ).pack(anchor="w")
        fonts = [
            FONT_MONO,
            "Courier New",
            "Fira Code",
            "JetBrains Mono",
            "Source Code Pro",
            "Cascadia Code",
            "Inconsolata",
            "Consolas",
            "Liberation Mono",
            "DejaVu Sans Mono",
        ]
        DropDown(
            body,
            textvariable=self._font_var,
            values=fonts,
            width=30,
            font=(FONT_UI, 10),
            state="readonly",
        ).pack(anchor="w", pady=(4, 16))

        tk.Label(
            body,
            text=_t("wiz.font_size"),
            bg=t.floating_bg,
            fg=t.fg,
            font=(FONT_UI, 9),
            anchor="w",
        ).pack(anchor="w")
        sz_row = tk.Frame(body, bg=t.floating_bg)
        sz_row.pack(anchor="w", pady=(4, 0))
        sz_val = tk.Label(
            sz_row,
            text=str(self._size_var.get()),
            bg=t.activity_bg,
            fg=t.fg,
            font=(FONT_MONO, 10),
            width=4,
            pady=3,
        )
        sz_val.pack(side="right", padx=(8, 0))
        ttk.Scale(
            sz_row,
            from_=8,
            to=28,
            orient="horizontal",
            variable=self._size_var,
            length=260,
            command=lambda v: sz_val.config(text=str(int(float(v)))),
        ).pack(side="left")

        self._divider(t)

        pf = tk.Frame(
            self._content,
            bg=t.editor_bg,
            highlightthickness=1,
            highlightbackground=t.border,
        )
        pf.pack(fill="x", padx=28, pady=(0, 20))
        ph = tk.Frame(pf, bg=t.activity_bg)
        ph.pack(fill="x")
        tk.Label(
            ph,
            text="  preview.py",
            bg=t.activity_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            pady=4,
        ).pack(side="left")
        pv = tk.Label(
            pf,
            text='def hello_world():\n    msg = "Hello, Codis!"\n    print(msg)',
            bg=t.editor_bg,
            fg=t.t_func,
            font=(self._font_var.get(), self._size_var.get()),
            justify="left",
            padx=16,
            pady=12,
        )
        pv.pack(fill="x")

        def _refresh(*_: Any) -> None:
            with contextlib.suppress(tk.TclError):
                pv.config(
                    font=(self._font_var.get(), max(
                        8, int(self._size_var.get())))
                )

        self._font_var.trace_add("write", _refresh)
        self._size_var.trace_add("write", _refresh)

    def _step_indent(self, t: Any) -> None:
        self._section_title(_t("wiz.indent_title"), t)
        self._section_desc(_t("wiz.indent_desc"), t)

        body = tk.Frame(self._content, bg=t.floating_bg)
        body.pack(fill="x", padx=28)

        for size, key in [
            (2, "wiz.indent_2"),
            (4, "wiz.indent_4"),
            (8, "wiz.indent_8"),
        ]:
            selected = self._tab_var.get() == size
            card = tk.Frame(
                body,
                bg=t.activity_bg if selected else t.floating_bg,
                highlightthickness=1,
                highlightbackground=t.btn_primary if selected else t.border,
                cursor="hand2",
            )
            card.pack(fill="x", pady=5)
            inner = tk.Frame(card, bg=card.cget("bg"), pady=10, padx=16)
            inner.pack(fill="x")
            left_f = tk.Frame(inner, bg=inner.cget("bg"))
            left_f.pack(side="left", fill="x", expand=True)
            tk.Label(
                left_f,
                text=_t("wiz.spaces", n=size),
                bg=left_f.cget("bg"),
                fg=t.btn_primary if selected else t.fg,
                font=(FONT_UI, 11, "bold"),
            ).pack(anchor="w")
            tk.Label(
                left_f,
                text=_t(key),
                bg=left_f.cget("bg"),
                fg=t.fg_muted,
                font=(FONT_UI, 8),
            ).pack(anchor="w")
            preview = "\u00b7" * size + "  code here"
            tk.Label(
                inner,
                text=preview,
                bg=inner.cget("bg"),
                fg=t.fg_muted,
                font=(FONT_MONO, 9),
            ).pack(side="right", padx=12)
            if selected:
                tk.Label(
                    inner,
                    text="\u2713",
                    bg=inner.cget("bg"),
                    fg=t.btn_primary,
                    font=(FONT_UI, 14, "bold"),
                ).pack(side="right")

            def _pick(s=size):
                self._tab_var.set(s)
                self._show_step(4)

            for w in (
                [card, inner, left_f]
                + list(left_f.winfo_children())
                + list(inner.winfo_children())
            ):
                w.bind("<Button-1>", lambda _, fn=_pick: fn(), add="+")

    def _step_features(self, t: Any) -> None:
        self._section_title(_t("wiz.features_title"), t)
        self._section_desc(_t("wiz.features_desc"), t)
        body = tk.Frame(self._content, bg=t.floating_bg)
        body.pack(fill="x", padx=28)
        self._toggle_cards(body, t, FEATURE_TOGGLES)

    def _toggle_cards(
        self, body: tk.Frame, t: Any, toggles: tuple[WizardToggle, ...]
    ) -> None:
        """Cria um cartão liga/desliga para cada opção de ``toggles``."""
        for tg in toggles:
            desc = _t(tg.desc_key, tg.desc) if tg.desc_key else ""
            self._feature_card(
                body, t, self._opts[tg.key], tg.icon, _t(tg.label_key, tg.label), desc
            )

    def _feature_card(
        self,
        parent: tk.Frame,
        t: Any,
        var: tk.BooleanVar,
        icon: str,
        label: str,
        desc: str,
    ) -> None:
        card = tk.Frame(
            parent,
            bg=t.activity_bg,
            highlightthickness=1,
            highlightbackground=t.border,
            cursor="hand2",
        )
        card.pack(fill="x", pady=5)
        inner = tk.Frame(card, bg=t.activity_bg, padx=16, pady=10)
        inner.pack(fill="x")

        icon_lbl = tk.Label(
            inner, text=icon, bg=t.activity_bg, fg=t.fg, font=(FONT_UI, 16), width=2
        )
        icon_lbl.pack(side="left")

        text_f = tk.Frame(inner, bg=t.activity_bg)
        text_f.pack(side="left", fill="x", expand=True, padx=10)
        tk.Label(
            text_f,
            text=label,
            bg=t.activity_bg,
            fg=t.fg,
            font=(FONT_UI, 10, "bold"),
            anchor="w",
        ).pack(fill="x")
        if desc:
            tk.Label(
                text_f,
                text=desc,
                bg=t.activity_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 8),
                anchor="w",
            ).pack(fill="x")

        toggle_outer = tk.Frame(inner, bg=t.activity_bg)
        toggle_outer.pack(side="right")
        toggle_bg = tk.Frame(
            toggle_outer, bg=t.border, width=40, height=20, cursor="hand2"
        )
        toggle_bg.pack_propagate(False)
        toggle_bg.pack()
        toggle_dot = tk.Frame(toggle_bg, bg=t.fg_muted, width=14, height=14)

        def _draw(*_: Any) -> None:
            on = var.get()
            toggle_bg.config(bg=t.btn_primary if on else t.border)
            toggle_dot.config(bg="#fff" if on else t.fg_muted)
            toggle_dot.place(x=22 if on else 2, rely=0.5, anchor="w")

        _draw()

        def _toggle(_evt: Any = None) -> None:
            var.set(not var.get())
            _draw()

        var.trace_add("write", _draw)
        for w in [card, inner, icon_lbl, text_f, toggle_outer, toggle_bg, toggle_dot]:
            w.bind("<Button-1>", _toggle, add="+")
        for child in text_f.winfo_children():
            child.bind("<Button-1>", _toggle, add="+")

    def _step_extra(self, t: Any) -> None:
        self._section_title(_t("wiz.extra_title"), t)
        self._section_desc(_t("wiz.extra_desc"), t)

        body = tk.Frame(self._content, bg=t.floating_bg)
        body.pack(fill="x", padx=28)

        shell_card = tk.Frame(
            body, bg=t.activity_bg, highlightthickness=1, highlightbackground=t.border
        )
        shell_card.pack(fill="x", pady=5)
        shell_inner = tk.Frame(shell_card, bg=t.activity_bg, padx=16, pady=12)
        shell_inner.pack(fill="x")
        tk.Label(
            shell_inner,
            text="\U0001f4bb",
            bg=t.activity_bg,
            fg=t.fg,
            font=(FONT_UI, 16),
            width=2,
        ).pack(side="left")
        tk.Label(
            shell_inner,
            text=_t("wiz.shell"),
            bg=t.activity_bg,
            fg=t.fg,
            font=(FONT_UI, 10, "bold"),
            padx=10,
        ).pack(side="left")
        DropDown(
            shell_inner,
            textvariable=self._shell_var2,
            values=list(SHELL_OPTIONS.keys()),
            width=18,
            font=(FONT_UI, 9),
            state="readonly",
        ).pack(side="right")

        self._toggle_cards(body, t, EXTRA_TOGGLES)

        self._divider(t)
        tk.Label(
            self._content,
            text=_t("wiz.extra_note"),
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
            justify="left",
            anchor="w",
            wraplength=680,
        ).pack(fill="x", padx=28, pady=(0, 20))

    def _step_voice(self, t: Any) -> None:
        self._section_title(_t("wiz.voice_title", "Voz e acessibilidade"), t)
        self._section_desc(
            _t(
                "wiz.voice_desc",
                "Ajuste a leitura em voz alta e as opções de acessibilidade. "
                "Tudo pode ser mudado depois em Configurações.",
            ),
            t,
        )
        body = tk.Frame(self._content, bg=t.floating_bg)
        body.pack(fill="x", padx=28)
        self._toggle_cards(body, t, A11Y_TOGGLES)

        self._divider(t)
        card = tk.Frame(
            body, bg=t.activity_bg, highlightthickness=1, highlightbackground=t.border
        )
        card.pack(fill="x", pady=5)
        inner = tk.Frame(card, bg=t.activity_bg, padx=16, pady=12)
        inner.pack(fill="x")
        self._tts_status = tk.Label(
            inner,
            text=self._tts_status_text(),
            bg=t.activity_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
            anchor="w",
            justify="left",
            wraplength=480,
        )
        self._tts_status.pack(side="left", fill="x", expand=True)
        tk.Button(
            inner,
            text=_t("wiz.tts_test", "Testar voz"),
            bg=t.btn_bg,
            fg=t.btn_fg,
            activebackground=t.btn_hover,
            activeforeground=t.fg,
            relief="flat",
            bd=0,
            padx=14,
            pady=6,
            cursor="hand2",
            font=(FONT_UI, 9),
            command=self._test_voice,
        ).pack(side="right")

    def _speech_bridge(self) -> Any:
        """Cria (uma vez) a ponte de fala do sistema; ``None`` se indisponível."""
        if self._tts_bridge is None:
            try:
                from src.ui.accessibility import ScreenReaderBridge

                self._tts_bridge = ScreenReaderBridge()
            except Exception:
                logger.debug("Ponte de TTS indisponível.", exc_info=True)
                return None
        return self._tts_bridge

    def _tts_status_text(self) -> str:
        bridge = self._speech_bridge()
        if bridge is not None and bridge.available:
            return _t("wiz.tts_detected", "Motor de voz do sistema detectado.")
        return _t(
            "wiz.tts_missing",
            "Nenhum motor de voz detectado (Windows: SAPI, Linux: spd-say ou espeak, macOS: say). "
            "A opção fica salva e passa a valer quando houver um.",
        )

    def _test_voice(self) -> None:
        """Fala uma frase de exemplo para o usuário conferir o som."""
        bridge = self._speech_bridge()
        if bridge is not None and bridge.available:
            bridge.speak(
                _t("wiz.tts_sample", "Olá! Esta é a voz do Codis."), interrupt=True
            )
        if self._tts_status is not None:
            with contextlib.suppress(tk.TclError):
                self._tts_status.config(text=self._tts_status_text())

    def _step_summary(self, t: Any) -> None:
        outer = tk.Frame(self._content, bg=t.floating_bg)
        outer.pack(fill="both", expand=True, padx=28, pady=(20, 16))

        hero = tk.Frame(
            outer, bg=t.activity_bg, highlightthickness=1, highlightbackground=t.ok_fg
        )
        hero.pack(fill="x", pady=(0, 20))
        tk.Label(hero, text="\U0001f389", bg=t.activity_bg, font=(FONT_UI, 30)).pack(
            pady=(18, 4)
        )
        tk.Label(
            hero,
            text=_t("wiz.sum_title"),
            bg=t.activity_bg,
            fg=t.ok_fg,
            font=(FONT_UI, 17, "bold"),
        ).pack()
        tk.Label(
            hero,
            text=_t("wiz.sum_sub"),
            bg=t.activity_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
            justify="center",
            wraplength=580,
        ).pack(pady=(4, 18))

        self._divider(t)

        tk.Label(
            self._content,
            text=_t("wiz.sum_your_setup"),
            bg=t.floating_bg,
            fg=t.fg,
            font=(FONT_UI, 11, "bold"),
        ).pack(padx=28, anchor="w", pady=(8, 8))

        grid_f = tk.Frame(self._content, bg=t.floating_bg)
        grid_f.pack(fill="x", padx=28, pady=(0, 16))
        grid_f.columnconfigure(0, weight=1)
        grid_f.columnconfigure(1, weight=1)

        on_s = _t("wiz.on")
        off_s = _t("wiz.off")
        items = [
            ("\U0001f310", _t("wiz.sum_lang"), self._locale_var.get()),
            ("\U0001f3a8", _t("wiz.sum_theme"), self._theme_var.get()),
            (
                "\u2712",
                _t("wiz.sum_font"),
                f"{self._font_var.get()}  {self._size_var.get()}px",
            ),
            ("\u21e5", _t("wiz.sum_tab"), _t("wiz.spaces", n=self._tab_var.get())),
            ("\U0001f4bb", _t("wiz.sum_shell"), self._shell_var2.get()),
        ]
        for tg in ALL_TOGGLES:
            label = _t(tg.sum_key) if tg.sum_key else _t(tg.label_key, tg.label)
            items.append((tg.icon, label, on_s if self._opts[tg.key].get() else off_s))
        for i, (icon, key, val) in enumerate(items):
            r, c = divmod(i, 2)
            cell = tk.Frame(
                grid_f,
                bg=t.activity_bg,
                highlightthickness=1,
                highlightbackground=t.border,
            )
            cell.grid(row=r, column=c, padx=4, pady=3, sticky="ew")
            inn = tk.Frame(cell, bg=t.activity_bg, padx=10, pady=6)
            inn.pack(fill="x")
            tk.Label(
                inn, text=icon, bg=t.activity_bg, fg=t.fg, font=(FONT_UI, 12), width=2
            ).pack(side="left")
            lf = tk.Frame(inn, bg=t.activity_bg)
            lf.pack(side="left", fill="x", expand=True, padx=6)
            tk.Label(
                lf,
                text=key,
                bg=t.activity_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 7),
                anchor="w",
            ).pack(fill="x")
            tk.Label(
                lf,
                text=val,
                bg=t.activity_bg,
                fg=t.fg,
                font=(FONT_UI, 9, "bold"),
                anchor="w",
            ).pack(fill="x")

    def _apply_locale(self, code: str) -> None:
        self._locale_var.set(code)
        I18n.get_instance().set_locale(code)

    def _next(self) -> None:
        if self._step < self.STEPS - 1:
            self._show_step(self._step + 1)
        else:
            self._finish()

    def _prev(self) -> None:
        if self._step > 0:
            self._show_step(self._step - 1)

    def _finish(self) -> None:
        self._cfg.set("language", self._locale_var.get())
        self._cfg.set("theme", self._theme_var.get())
        self._cfg.set("font_family", self._font_var.get())
        self._cfg.set("font_size", int(self._size_var.get()))
        self._cfg.set("tab_size", int(self._tab_var.get()))
        apply_toggles(self._cfg, {key: var.get() for key, var in self._opts.items()})
        self._cfg.set("default_shell", self._shell_var2.get())
        self._cfg.set("first_run_done", True)
        self._cfg.save()
        I18n.get_instance().set_locale(self._locale_var.get())
        self._tmgr.set(self._theme_var.get())
        _patch_theme(self._tmgr.theme)
        self.destroy()
        self._on_finish()
