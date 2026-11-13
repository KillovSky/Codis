"""Barra de status do Codis."""

from __future__ import annotations

import contextlib
import logging
import tkinter as tk
from collections.abc import Callable

from src.config import ConfigManager
from src.constants import FONT_UI, LANG_LABEL
from src.i18n import _t
from src.lint.manager import Diag
from src.theme.base import Theme
from src.theme.manager import ThemeManager

logger = logging.getLogger("Codis")

_LANG_ICONS: dict[str, str] = {
    "python": "🐍",
    "javascript": "JS",
    "typescript": "TS",
    "html": "◈",
    "css": "✦",
    "json": "{}",
    "markdown": "M↓",
    "rust": "🦀",
    "go": "Go",
    "java": "♨",
    "c": "C",
    "cpp": "C+",
    "sh": "$",
    "bash": "$",
    "sql": "🗄",
    "xml": "<>",
    "yaml": "≡",
    "toml": "⚙",
    "text": "Aa",
}


class StatusBar(tk.Frame):
    """
    Status bar modernizada: segmentos pill clicáveis, separadores discretos,
    ícones contextuais e hierarquia visual clara.
    """

    def __init__(
        self, parent: tk.Widget, tmgr: ThemeManager, cfg: ConfigManager
    ) -> None:
        t = tmgr.theme
        super().__init__(parent, bg=t.statusbar_bg, height=26, pady=0)
        self.pack_propagate(False)
        self._tmgr = tmgr
        self._cfg = cfg
        self._segs: dict[str, tk.Label] = {}
        self._right_labels: dict[str, tk.Label] = {}
        self._extra_segs: dict[str, tk.Label] = {}
        self._notif_job: str | None = None
        self._notif_history: list[tuple[str, str]] = []
        self._build()

    def _build(self) -> None:
        t = self._tmgr.theme

        self._left = tk.Frame(self, bg=t.statusbar_bg)
        self._left.pack(side="left", fill="y")

        self._segs["git"] = self._mk_left("git", "⎇  main", accent=True)
        self._segs["errors"] = self._mk_left("errors", "✖ 0")
        self._segs["warnings"] = self._mk_left("warnings", "⚠ 0")

        self._segs["dual"] = self._mk_left("dual", "⬜ SPLIT")
        self._segs["dual"].pack_forget()

        self._notif = tk.Label(
            self,
            text="",
            bg=t.statusbar_bg,
            fg=t.statusbar_fg,
            font=(FONT_UI, 9),
            padx=8,
            cursor="hand2",
        )
        self._notif.pack(side="left", fill="x", expand=True)
        self._notif.bind("<Button-1>", self._show_notif_history)

        self._right = tk.Frame(self, bg=t.statusbar_bg)
        self._right.pack(side="right", fill="y")

        for key, text in (
            ("lang", "Aa  Text"),
            ("eol", "LF"),
            ("encoding", "UTF-8"),
            ("cursor", "Ln 1, Col 1"),
            ("selection", ""),
            ("indent", "Spaces: 4"),
            ("zoom", "100%"),
        ):
            lbl = self._mk_right(key, text)
            self._right_labels[key] = lbl

    def _mk_left(self, _key: str, text: str, accent: bool = False) -> tk.Label:
        """Cria um segmento clicável no lado esquerdo."""
        t = self._tmgr.theme
        fg = getattr(t, "accent", t.statusbar_fg) if accent else t.statusbar_fg

        lbl = tk.Label(
            self._left,
            text=f"  {text}  ",
            bg=t.statusbar_bg,
            fg=fg,
            font=(FONT_UI, 9),
            cursor="hand2",
            relief="flat",
            pady=0,
        )
        lbl.pack(side="left", fill="y")

        tk.Frame(self._left, bg=t.statusbar_sep, width=1).pack(
            side="left", fill="y", pady=4
        )
        return lbl

    def _mk_right(self, _key: str, text: str) -> tk.Label:
        """Cria um segmento clicável no lado direito."""
        t = self._tmgr.theme

        tk.Frame(self._right, bg=t.statusbar_sep, width=1).pack(
            side="right", fill="y", pady=4
        )

        lbl = tk.Label(
            self._right,
            text=f"  {text}  ",
            bg=t.statusbar_bg,
            fg=t.statusbar_fg,
            font=(FONT_UI, 9),
            cursor="hand2",
            relief="flat",
            pady=0,
        )
        lbl.pack(side="right", fill="y")
        return lbl

    def set_cursor(self, line: int, col: int) -> None:
        self._right_labels["cursor"].config(text=f"  Ln {line}, Col {col}  ")

    def set_selection(self, chars: int, lines: int) -> None:
        if chars > 0:
            txt = f"({chars} sel)"
            if lines > 1:
                txt = f"({lines}L {chars}C sel)"
            self._right_labels["selection"].config(text=f"  {txt}  ")
        else:
            self._right_labels["selection"].config(text="")

    def set_lang(self, lang: str) -> None:
        label = LANG_LABEL.get(lang, lang.title() if lang else "Text")
        icon = _LANG_ICONS.get(lang.lower() if lang else "", "Aa")
        self._right_labels["lang"].config(text=f"  {icon}  {label}  ")

    def set_encoding(self, enc: str) -> None:
        self._right_labels["encoding"].config(text=f"  {enc}  ")

    def set_eol(self, eol: str) -> None:
        labels = {"\n": "LF", "\r\n": "CRLF", "\r": "CR"}
        self._right_labels["eol"].config(text=f"  {labels.get(eol, 'LF')}  ")

    def set_diags(self, diags: list[Diag]) -> None:
        errors = sum(1 for d in diags if d.severity == 1)
        warnings = sum(1 for d in diags if d.severity == 2)
        t = self._tmgr.theme
        self._segs["errors"].config(
            text=f"  ✖ {errors}  ",
            fg=t.err_fg if errors else t.statusbar_fg,
        )
        self._segs["warnings"].config(
            text=f"  ⚠ {warnings}  ",
            fg=t.warn_fg if warnings else t.statusbar_fg,
        )

    def set_zoom(self, pct: int) -> None:
        self._right_labels["zoom"].config(text=f"  {pct}%  ")

    def set_rhythm(self, flow_state: str, wpm: float) -> None:
        if not hasattr(self, "_rhythm_lbl"):
            return
        _icons = {"idle": "", "warming": "🌱", "flow": "~", "deep_flow": "**"}
        icon = _icons.get(flow_state, "")
        if icon and wpm > 0:
            self._rhythm_lbl.config(text=f" {icon} {wpm:.0f}wpm ")
        else:
            self._rhythm_lbl.config(text="")

    def set_indent(self, size: int, use_tabs: bool) -> None:
        txt = f"Tab: {size}" if use_tabs else f"Spaces: {size}"
        self._right_labels["indent"].config(text=f"  {txt}  ")

    def set_dual_mode(self, active: bool) -> None:
        t = self._tmgr.theme
        lbl = self._segs.get("dual")
        if not lbl:
            return
        accent = getattr(t, "accent", "#7c4dff")
        if active:
            with contextlib.suppress(tk.TclError):
                lbl.config(
                    text=f"  {_t('statusbar.split', '⬜ SPLIT')}  ",
                    fg=accent,
                )
                lbl.pack(side="left", fill="y",
                         after=self._segs.get("warnings", lbl))
        else:
            with contextlib.suppress(tk.TclError):
                lbl.pack_forget()

    def notify(self, msg: str, duration: int = 3000, level: str = "info") -> None:
        t = self._tmgr.theme
        colors = {
            "info": t.statusbar_fg,
            "ok": getattr(t, "diff_add_fg", "#4caf50"),
            "warn": getattr(t, "warn_fg", "#f9e2af"),
            "error": getattr(t, "err_fg", "#f44336"),
        }
        icons = {"info": "ℹ", "ok": "✔", "warn": "⚠", "error": "✖"}
        color = colors.get(level, t.statusbar_fg)
        icon = icons.get(level, "")
        self._notif.config(text=f"  {icon}  {msg}", fg=color)

        if hasattr(self, "_notif_history"):
            self._notif_history.append((msg, level))
            if len(self._notif_history) > 20:
                self._notif_history.pop(0)

        if self._notif_job:
            with contextlib.suppress(tk.TclError):
                self.after_cancel(self._notif_job)
        if duration:
            self._notif_job = self.after(
                duration, lambda: self._notif.config(text=""))

    def _show_notif_history(self, _e=None) -> None:
        history = getattr(self, "_notif_history", [])
        if not history:
            return
        t = self._tmgr.theme
        popup = tk.Toplevel(self)
        popup.wm_overrideredirect(True)
        popup.wm_attributes("-topmost", True)
        popup.configure(bg=t.floating_border)

        inner = tk.Frame(popup, bg=t.floating_bg)
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        hdr = tk.Frame(inner, bg=t.floating_bg)
        hdr.pack(fill="x")
        tk.Label(
            hdr,
            text="  🕐  Notificações recentes",
            bg=t.floating_bg,
            fg=t.fg,
            font=(FONT_UI, 9, "bold"),
            anchor="w",
            pady=6,
        ).pack(side="left", fill="x", expand=True)
        close_x = tk.Label(
            hdr,
            text=" ✕ ",
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
            cursor="hand2",
        )
        close_x.pack(side="right", padx=4)
        close_x.bind("<Button-1>", lambda _: popup.destroy())

        tk.Frame(inner, bg=t.border, height=1).pack(fill="x")

        colors = {
            "info": t.statusbar_fg,
            "ok": getattr(t, "diff_add_fg", "#4caf50"),
            "warn": getattr(t, "warn_fg", "#f9e2af"),
            "error": getattr(t, "err_fg", "#f44336"),
        }
        icons = {"info": "ℹ", "ok": "✔", "warn": "⚠", "error": "✖"}

        for msg, level in reversed(history):
            fg = colors.get(level, t.statusbar_fg)
            ico = icons.get(level, "-")
            row = tk.Frame(inner, bg=t.floating_bg)
            row.pack(fill="x")
            tk.Label(
                row, text=f" {ico}", bg=t.floating_bg, fg=fg, font=(FONT_UI, 9), width=3
            ).pack(side="left")
            tk.Label(
                row,
                text=msg,
                bg=t.floating_bg,
                fg=t.fg,
                font=(FONT_UI, 8),
                anchor="w",
                pady=3,
                wraplength=360,
                justify="left",
            ).pack(side="left", fill="x", expand=True, padx=4)

        tk.Frame(inner, bg=t.border, height=1).pack(fill="x")

        x = self.winfo_rootx()
        popup.update_idletasks()
        h = min(len(history) * 26 + 70, 380)
        y = self.winfo_rooty() - h - 4
        popup.geometry(f"400x{h}+{x}+{max(0, y)}")
        popup.bind("<FocusOut>", lambda _: popup.destroy())
        popup.after(10, popup.focus_set)

    def apply_theme(self, t: Theme) -> None:
        from src.theme.utils import cfg, cfg_many

        cfg(self, bg=t.statusbar_bg)
        cfg_many([self._left, self._right], bg=t.statusbar_bg)
        all_labels = (
            list(self._segs.values())
            + list(self._right_labels.values())
            + list(self._extra_segs.values())
            + [self._notif]
        )
        cfg_many(all_labels, bg=t.statusbar_bg, fg=t.statusbar_fg)

    def bind_callbacks(self, **kw: Callable) -> None:
        t = self._tmgr.theme

        if "git" in kw:
            lbl = self._segs["git"]
            lbl.bind("<Button-1>", lambda _: kw["git"]())
            lbl.bind("<Enter>", lambda _: lbl.config(bg=t.statusbar_hover))
            lbl.bind("<Leave>", lambda _: lbl.config(bg=t.statusbar_bg))

        if "problems" in kw:
            for seg_key in ("errors", "warnings"):
                lbl = self._segs[seg_key]
                lbl.bind("<Button-1>", lambda _: kw["problems"]())
                lbl.bind("<Enter>", lambda _,
                         w=lbl: w.config(bg=t.statusbar_hover))
                lbl.bind("<Leave>", lambda _,
                         w=lbl: w.config(bg=t.statusbar_bg))

        right_map = {
            "lang": kw.get("lang"),
            "eol": kw.get("eol"),
            "encoding": kw.get("encoding"),
            "indent": kw.get("indent"),
            "zoom": kw.get("zoom"),
        }
        for _seg_key, callback in right_map.items():
            if callback and _seg_key in self._right_labels:
                _lbl = self._right_labels[_seg_key]
                _lbl.bind("<Button-1>", lambda _, c=callback: c())
                _lbl.bind("<Enter>", lambda _,
                          w=_lbl: w.config(bg=t.statusbar_hover))
                _lbl.bind("<Leave>", lambda _,
                          w=_lbl: w.config(bg=t.statusbar_bg))
