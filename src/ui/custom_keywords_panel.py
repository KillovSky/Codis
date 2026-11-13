"""Painel de Keywords de Sintaxe Customizadas - Codis.

Permite ao usuário adicionar palavras-chave extras para qualquer linguagem,
com uma cor de destaque escolhida livremente. As keywords são persistidas em
``config["custom_keywords"]`` como uma lista de dicts::

    [{"keyword": "myFunc", "color": "#ff79c6", "lang": "python"}, ...]

O highlighter injeta essas entradas em tempo real no próximo ciclo de
realce, adicionando uma tag ``custom_kw_<idx>`` por entrada.
"""

from __future__ import annotations

import contextlib
import logging
import re
import tkinter as tk
from tkinter import colorchooser, messagebox
from typing import TYPE_CHECKING

from src.constants import FONT_MONO, FONT_UI
from src.i18n import _t

if TYPE_CHECKING:
    from src.config import ConfigManager
    from src.theme.manager import ThemeManager

logger = logging.getLogger("Codis.CustomKeywords")


def _lighten(hex_color: str, amount: int = 20) -> str:
    """Retorna uma versão mais clara da cor hex fornecida."""
    try:
        hex_color = hex_color.lstrip("#")
        r, g, b = (int(hex_color[i: i + 2], 16) for i in (0, 2, 4))
        return f"#{min(255, r + amount):02x}{min(255, g + amount):02x}{min(255, b + amount):02x}"
    except Exception:
        return hex_color


CFG_KEY = "custom_keywords"

_DEFAULT: list[dict] = []

_COMMON_LANGS = [
    "python",
    "javascript",
    "typescript",
    "rust",
    "go",
    "java",
    "c",
    "cpp",
    "csharp",
    "html",
    "css",
    "json",
    "yaml",
    "toml",
    "sql",
    "bash",
    "lua",
    "ruby",
    "php",
    "swift",
    "kotlin",
    "scala",
    "*",
]


def _is_valid_color(color: str) -> bool:
    """Verifica se *color* é um hex #rrggbb ou #rgb válido."""
    return bool(re.fullmatch(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})", color.strip()))


class CustomKeywordsPanel(tk.Frame):
    """Painel de gerenciamento de keywords customizadas de sintaxe.

    Exibe uma tabela rolável:

        Keyword  |  Cor  |  Linguagem  |  Ações

    O botão ``+`` no rodapé adiciona uma nova linha vazia.
    Cada linha tem um botão ``✕`` para remover a entrada.
    As alterações são salvas imediatamente via ``_cfg.set`` / ``_cfg.save``.
    """

    def __init__(
        self,
        parent: tk.Widget,
        cfg: ConfigManager,
        tmgr: ThemeManager,
        on_change: callable | None = None,
    ) -> None:
        t = tmgr.theme
        super().__init__(parent, bg=t.floating_bg)
        self._cfg = cfg
        self._tmgr = tmgr
        self._on_change = on_change
        self._rows: list[dict] = []

        self._build()
        self._load()

    def _build(self) -> None:
        t = self._tmgr.theme

        hdr = tk.Frame(self, bg=t.floating_bg)
        hdr.pack(fill="x", padx=20, pady=(16, 6))
        tk.Label(
            hdr,
            text=_t("ckw.panel_title", "🎨  Keywords de Sintaxe Customizadas"),
            bg=t.floating_bg,
            fg=t.fg,
            font=(FONT_UI, 13, "bold"),
            anchor="w",
        ).pack(anchor="w")
        tk.Label(
            hdr,
            text=_t(
                "ckw.panel_desc",
                "Adicione palavras-chave extras a qualquer linguagem com cor personalizada.\nUse '*' em Linguagem para aplicar a todos os arquivos.",
            ),
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
            justify="left",
            anchor="w",
        ).pack(anchor="w", pady=(2, 0))
        tk.Frame(self, bg=t.border, height=1).pack(
            fill="x", padx=20, pady=(4, 0))

        col_hdr = tk.Frame(self, bg=t.activity_bg)
        col_hdr.pack(fill="x", padx=20, pady=(8, 0))

        for text, w in (
            (_t("ckw.col_keyword", "Keyword"), 24),
            (_t("ckw.col_color", "Cor"), 9),
            (_t("ckw.col_lang", "Linguagem"), 14),
            ("", 5),
        ):
            tk.Label(
                col_hdr,
                text=text,
                bg=t.activity_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 8, "bold"),
                width=w,
                anchor="w",
            ).pack(side="left", padx=(4, 0))
        tk.Frame(self, bg=t.border, height=1).pack(
            fill="x", padx=20, pady=(2, 0))

        outer = tk.Frame(self, bg=t.floating_bg)
        outer.pack(fill="both", expand=True, padx=20, pady=(4, 0))

        vsb = tk.Scrollbar(outer, orient="vertical")
        vsb.pack(side="right", fill="y")
        self._canvas = tk.Canvas(
            outer,
            bg=t.floating_bg,
            highlightthickness=0,
            yscrollcommand=vsb.set,
        )
        self._canvas.pack(side="left", fill="both", expand=True)
        vsb.config(command=self._canvas.yview)

        self._list_frame = tk.Frame(self._canvas, bg=t.floating_bg)
        self._cw_id = self._canvas.create_window(
            (0, 0), window=self._list_frame, anchor="nw"
        )
        self._list_frame.bind(
            "<Configure>",
            lambda _e: self._canvas.configure(
                scrollregion=self._canvas.bbox("all")),
        )
        self._canvas.bind(
            "<Configure>",
            lambda e: self._canvas.itemconfig(self._cw_id, width=e.width),
        )
        self._canvas.bind(
            "<MouseWheel>",
            lambda e: self._canvas.yview_scroll(
                int(-1 * (e.delta / 120)), "units"),
        )

        tk.Frame(self, bg=t.border, height=1).pack(
            fill="x", padx=20, pady=(4, 0))
        footer = tk.Frame(self, bg=t.floating_bg)
        footer.pack(fill="x", padx=20, pady=8)

        add_btn = tk.Label(
            footer,
            text=_t("ckw.btn_add", "  +  Adicionar Keyword  "),
            bg=t.accent,
            fg="#ffffff",
            font=(FONT_UI, 9, "bold"),
            cursor="hand2",
            padx=8,
            pady=4,
            relief="flat",
        )
        add_btn.pack(side="left")
        add_btn.bind("<Button-1>", lambda _: self._add_row())
        add_btn.bind("<Enter>", lambda _: add_btn.config(
            bg=_lighten(t.accent)))
        add_btn.bind("<Leave>", lambda _: add_btn.config(bg=t.accent))

        clear_btn = tk.Label(
            footer,
            text=_t("ckw.btn_clear", "  Limpar Tudo  "),
            bg=t.btn_bg,
            fg=t.fg,
            font=(FONT_UI, 9),
            cursor="hand2",
            padx=8,
            pady=4,
            relief="flat",
        )
        clear_btn.pack(side="left", padx=(8, 0))
        clear_btn.bind("<Button-1>", lambda _: self._clear_all())
        clear_btn.bind("<Enter>", lambda _: clear_btn.config(bg=t.btn_hover))
        clear_btn.bind("<Leave>", lambda _: clear_btn.config(bg=t.btn_bg))

        self._count_lbl = tk.Label(
            footer,
            text=_t("ckw.count", "{n} keyword(s)", n=0),
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        )
        self._count_lbl.pack(side="right")

    def _load(self) -> None:
        """Carrega as keywords salvas e cria as linhas visuais."""
        entries: list[dict] = self._cfg.get(CFG_KEY, _DEFAULT)
        if not isinstance(entries, list):
            entries = []
        for entry in entries:
            self._add_row(
                keyword=entry.get("keyword", ""),
                color=entry.get("color", "#cba6f7"),
                lang=entry.get("lang", "*"),
                save=False,
            )
        self._update_count()

    def _save(self) -> None:
        """Persiste o estado atual no config."""
        data = []
        for row in self._rows:
            kw = row["kw_var"].get().strip()
            color = row["color_var"].get().strip()
            lang = row["lang_var"].get().strip() or "*"
            if not kw:
                continue
            if not _is_valid_color(color):
                color = "#cba6f7"
            data.append({"keyword": kw, "color": color, "lang": lang})
        self._cfg.set(CFG_KEY, data)
        self._cfg.save()
        if self._on_change:
            with contextlib.suppress(Exception):
                self._on_change(CFG_KEY, data)
        self._update_count()

    def _add_row(
        self,
        keyword: str = "",
        color: str = "#cba6f7",
        lang: str = "*",
        save: bool = True,
    ) -> None:
        """Cria uma nova linha de keyword na tabela."""
        t = self._tmgr.theme
        idx = len(self._rows)

        row_bg = t.floating_bg if idx % 2 == 0 else t.sidebar_bg
        row_frame = tk.Frame(
            self._list_frame,
            bg=row_bg,
            highlightthickness=1,
            highlightbackground=t.border,
        )
        row_frame.pack(fill="x", pady=1)

        kw_var = tk.StringVar(value=keyword)
        color_var = tk.StringVar(value=color)
        lang_var = tk.StringVar(value=lang)

        kw_entry = tk.Entry(
            row_frame,
            textvariable=kw_var,
            bg=t.input_bg,
            fg=t.fg,
            insertbackground=t.fg,
            relief="flat",
            font=(FONT_MONO, 9),
            width=22,
        )
        kw_entry.pack(side="left", padx=(6, 4), pady=4)
        kw_entry.bind("<FocusOut>", lambda _: self._save())
        kw_entry.bind("<Return>", lambda _: self._save())

        color_frame = tk.Frame(row_frame, bg=row_bg)
        color_frame.pack(side="left", padx=(0, 4))

        swatch = tk.Label(
            color_frame,
            bg=color if _is_valid_color(color) else t.accent,
            width=3,
            cursor="hand2",
            relief="flat",
        )
        swatch.pack(side="left", padx=(0, 2), ipady=6)

        color_entry = tk.Entry(
            color_frame,
            textvariable=color_var,
            bg=t.input_bg,
            fg=t.fg,
            insertbackground=t.fg,
            relief="flat",
            font=(FONT_MONO, 9),
            width=8,
        )
        color_entry.pack(side="left")

        def _pick_color(sv=color_var, sw=swatch):
            initial = sv.get() if _is_valid_color(sv.get()) else "#cba6f7"
            chosen = colorchooser.askcolor(
                color=initial, title=_t("ckw.dlg_pick_color", "Escolher Cor")
            )
            if chosen and chosen[1]:
                sv.set(chosen[1])
                with contextlib.suppress(tk.TclError):
                    sw.config(bg=chosen[1])
                self._save()

        def _on_color_entry(sv=color_var, sw=swatch, *_):
            c = sv.get().strip()
            if _is_valid_color(c):
                with contextlib.suppress(tk.TclError):
                    sw.config(bg=c)

        swatch.bind("<Button-1>", lambda _: _pick_color())
        color_var.trace_add("write", _on_color_entry)
        color_entry.bind("<FocusOut>", lambda _: self._save())
        color_entry.bind("<Return>", lambda _: self._save())

        lang_entry = tk.Entry(
            row_frame,
            textvariable=lang_var,
            bg=t.input_bg,
            fg=t.fg,
            insertbackground=t.fg,
            relief="flat",
            font=(FONT_MONO, 9),
            width=12,
        )
        lang_entry.pack(side="left", padx=(0, 4), pady=4)
        lang_entry.bind("<FocusOut>", lambda _: self._save())
        lang_entry.bind("<Return>", lambda _: self._save())

        rem_btn = tk.Label(
            row_frame,
            text=" ✕ ",
            bg=row_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
            cursor="hand2",
        )
        rem_btn.pack(side="left", padx=(2, 4))

        row_data = {
            "frame": row_frame,
            "kw_var": kw_var,
            "color_var": color_var,
            "lang_var": lang_var,
        }
        self._rows.append(row_data)

        def _remove(rd=row_data):
            self._remove_row(rd)

        rem_btn.bind("<Button-1>", lambda _: _remove())
        rem_btn.bind("<Enter>", lambda _, w=rem_btn: w.config(fg=t.err_fg))
        rem_btn.bind("<Leave>", lambda _, w=rem_btn: w.config(fg=t.fg_muted))

        if save:
            self._save()

    def _remove_row(self, row_data: dict) -> None:
        """Remove uma linha da tabela e persiste."""
        if row_data in self._rows:
            self._rows.remove(row_data)
        with contextlib.suppress(tk.TclError):
            row_data["frame"].destroy()
        self._save()

    def _clear_all(self) -> None:
        """Remove todas as keywords após confirmação."""
        if not self._rows:
            return
        if not messagebox.askyesno(
            _t("ckw.dlg_clear_title", "Limpar Keywords"),
            _t("ckw.dlg_clear_msg", "Remover todas as keywords customizadas?"),
        ):
            return
        for row in list(self._rows):
            with contextlib.suppress(tk.TclError):
                row["frame"].destroy()
        self._rows.clear()
        self._save()

    def _update_count(self) -> None:
        valid = sum(1 for r in self._rows if r["kw_var"].get().strip())
        with contextlib.suppress(tk.TclError):
            self._count_lbl.config(
                text=_t("ckw.count", "{n} keyword(s)", n=valid))
