"""Codis - _PluginTrustDialog + PluginPanel."""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import re
import subprocess
import sys
import tkinter as tk
import webbrowser
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from src.constants import (
    FONT_MONO,
    FONT_UI,
    PLUGINS_DIR,
)
from src.i18n import _t
from src.plugins.api import (
    PluginManifest,
    _get_plugin_source,
    _normalize_manifest,
    _save_trusted_plugins,
)
from src.plugins.loader import PluginLoader
from src.plugins.store import open_plugin_store
from src.ui._perf_utils import debounce_canvas
from src.ui.dropdown import DropDown
from src.ui.grab_safe import safe_grab_set

logger = logging.getLogger("Codis")


class _PluginTrustDialog(tk.Toplevel):
    """
    Balão de aviso de segurança antes de instalar um plugin não confiável.
    Exibe os imports perigosos encontrados e pede confirmação.
    Tem opção _t("hc.plugin_panel.n_o_mostrar_novamente_para_est").
    """

    def __init__(
        self,
        parent: tk.Widget,
        tmgr: Any,
        plugin_id: str,
        blocked: list[str],
        warned: list[str],
    ) -> None:
        """Inicializa o diálogo de confirmação de confiança para um plugin."""
        super().__init__(parent)
        t = tmgr.theme
        self.result = False
        self.dont_ask = False
        self.title("⚠  Aviso de Segurança - Plugin")
        self.configure(bg=t.panel_bg)
        self.resizable(False, False)
        self.transient(parent)
        safe_grab_set(self)

        top = tk.Frame(self, bg=t.panel_bg, padx=16, pady=12)
        top.pack(fill="x")
        tk.Label(top, text="⚠", font=(FONT_UI, 28), bg=t.panel_bg, fg="#f0a500").pack(
            side="left", padx=(0, 12)
        )
        right = tk.Frame(top, bg=t.panel_bg)
        right.pack(side="left", fill="x", expand=True)
        tk.Label(
            right,
            text=_t("hc.plugin_panel.plugin_possivelmente_n_o_confi"),
            font=(FONT_UI, 11, "bold"),
            bg=t.panel_bg,
            fg=t.fg,
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            right,
            text=f"  {plugin_id}",
            font=(FONT_UI, 9),
            bg=t.panel_bg,
            fg=t.comment_fg,
            anchor="w",
        ).pack(fill="x")

        tk.Frame(self, bg=t.border, height=1).pack(fill="x")

        body = tk.Frame(self, bg=t.panel_bg, padx=16, pady=8)
        body.pack(fill="x")

        if blocked:
            tk.Label(
                body,
                text=_t("hc.plugin_panel.m_dulos_bloqueados_detectados"),
                font=(FONT_UI, 9, "bold"),
                bg=t.panel_bg,
                fg=t.err_fg,
                anchor="w",
            ).pack(fill="x")
            tk.Label(
                body,
                text="  " + ", ".join(blocked),
                font=(FONT_MONO, 9),
                bg=t.panel_bg,
                fg=t.err_fg,
                anchor="w",
                wraplength=380,
            ).pack(fill="x", pady=(0, 6))

        if warned:
            tk.Label(
                body,
                text=_t("hc.plugin_panel.requisito_de_risco"),
                font=(FONT_UI, 9, "bold"),
                bg=t.panel_bg,
                fg="#f0a500",
                anchor="w",
            ).pack(fill="x")
            tk.Label(
                body,
                text="  " + ", ".join(warned),
                font=(FONT_MONO, 9),
                bg=t.panel_bg,
                fg="#f0a500",
                anchor="w",
                wraplength=380,
            ).pack(fill="x", pady=(0, 6))

        tk.Label(
            body,
            text=_t("hc.plugin_panel.instale_apenas_plugins_de_font")
            + "\n"
            + "Plugins maliciosos podem danificar seu sistema.",
            font=(FONT_UI, 9),
            bg=t.panel_bg,
            fg=t.comment_fg,
            justify="left",
            wraplength=380,
        ).pack(fill="x", pady=(4, 0))

        self._dont_var = tk.BooleanVar(value=False)
        cb_frame = tk.Frame(self, bg=t.panel_bg, padx=16, pady=4)
        cb_frame.pack(fill="x")
        tk.Checkbutton(
            cb_frame,
            text=_t("hc.plugin_panel.n_o_mostrar_novamente_para_est"),
            variable=self._dont_var,
            bg=t.panel_bg,
            fg=t.comment_fg,
            activebackground=t.panel_bg,
            selectcolor=t.input_bg,
            font=(FONT_UI, 8),
        ).pack(anchor="w")

        tk.Frame(self, bg=t.border, height=1).pack(fill="x")
        btn_row = tk.Frame(self, bg=t.panel_bg, padx=12, pady=10)
        btn_row.pack(fill="x")

        def _cancel():
            self.result = False
            self.dont_ask = self._dont_var.get()
            self.destroy()

        def _install():
            self.result = True
            self.dont_ask = self._dont_var.get()
            self.destroy()

        tk.Button(
            btn_row,
            text=_t("hc.plugin_panel.cancelar"),
            command=_cancel,
            bg=t.btn_bg,
            fg=t.fg,
            relief="flat",
            padx=12,
            pady=4,
            font=(FONT_UI, 9),
            cursor="hand2",
        ).pack(side="right", padx=4)
        tk.Button(
            btn_row,
            text=_t("hc.plugin_panel.instalar_mesmo_assim"),
            command=_install,
            bg=t.err_fg,
            fg="#ffffff",
            relief="flat",
            padx=12,
            pady=4,
            font=(FONT_UI, 9, "bold"),
            cursor="hand2",
        ).pack(side="right")

        self.update_idletasks()
        pw = parent.winfo_rootx() + parent.winfo_width() // 2
        ph = parent.winfo_rooty() + parent.winfo_height() // 2
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry(f"+{pw - w // 2}+{ph - h // 2}")
        self.wait_window()


def _render_markdown(widget: tk.Text, md: str, theme: Any) -> None:
    """
    Renderiza Markdown simples num tk.Text (somente-leitura).
    Suporta: # títulos, **bold**, `code`, ```blocos```, listas, ---
    """
    t = theme
    widget.config(state="normal")
    widget.delete("1.0", "end")

    widget.tag_configure(
        "h1", font=(FONT_UI, 15, "bold"), foreground=t.fg, spacing1=10, spacing3=6
    )
    widget.tag_configure(
        "h2", font=(FONT_UI, 13, "bold"), foreground=t.fg, spacing1=8, spacing3=4
    )
    widget.tag_configure(
        "h3", font=(FONT_UI, 11, "bold"), foreground=t.fg, spacing1=6, spacing3=2
    )
    widget.tag_configure("bold", font=(FONT_UI, 10, "bold"), foreground=t.fg)
    widget.tag_configure("italic", font=(
        FONT_UI, 10, "italic"), foreground=t.fg)
    widget.tag_configure(
        "code_inline",
        font=(FONT_MONO, 9),
        background=getattr(t, "input_bg", t.panel_bg),
        foreground=getattr(t, "string_fg", t.fg),
    )
    widget.tag_configure(
        "code_block",
        font=(FONT_MONO, 9),
        background=getattr(t, "gutter_bg", t.panel_bg),
        foreground=getattr(t, "string_fg", t.fg),
        lmargin1=16,
        lmargin2=16,
        spacing1=2,
        spacing3=2,
    )
    widget.tag_configure(
        "hr", font=(FONT_UI, 4), foreground=t.border, spacing1=4, spacing3=4
    )
    widget.tag_configure("bullet", font=(FONT_UI, 10),
                         lmargin1=16, lmargin2=28)
    widget.tag_configure("normal", font=(FONT_UI, 10),
                         foreground=t.fg, spacing1=1)
    widget.tag_configure(
        "link",
        font=(FONT_UI, 10),
        foreground=getattr(t, "accent", "#4fc3f7"),
        underline=True,
    )
    widget.tag_configure(
        "badge_ok",
        font=(FONT_UI, 8, "bold"),
        background="#1b5e20",
        foreground="#a5d6a7",
        relief="flat",
    )
    widget.tag_configure(
        "badge_warn",
        font=(FONT_UI, 8, "bold"),
        background="#4a3000",
        foreground="#ffcc02",
        relief="flat",
    )

    lines = md.split("\n")
    in_code_block = False
    code_block_lines: list[str] = []

    def flush_code():
        """Insere as linhas de código acumuladas no widget com a tag ``code_block``."""
        if code_block_lines:
            widget.insert("end", "\n".join(
                code_block_lines) + "\n", "code_block")
            code_block_lines.clear()

    for line in lines:
        if line.startswith("```"):
            if in_code_block:
                flush_code()
                in_code_block = False
            else:
                in_code_block = True
            continue
        if in_code_block:
            code_block_lines.append(line)
            continue

        if line.startswith("### "):
            widget.insert("end", line[4:] + "\n", "h3")
            continue
        if line.startswith("## "):
            widget.insert("end", line[3:] + "\n", "h2")
            continue
        if line.startswith("# "):
            widget.insert("end", line[2:] + "\n", "h1")
            continue

        if re.match(r"^[-_*]{3,}$", line.strip()):
            widget.insert("end", "-" * 48 + "\n", "hr")
            continue

        m = re.match(r"^(\s*)([-*+]|\d+\.)\s+(.+)", line)
        if m:
            widget.insert("end", f"  {m.group(2)}  ", "bullet")
            _insert_inline(widget, m.group(3))
            widget.insert("end", "\n")
            continue

        if not line.strip():
            widget.insert("end", "\n", "normal")
            continue

        _insert_inline(widget, line)
        widget.insert("end", "\n", "normal")

    if in_code_block:
        flush_code()

    widget.config(state="disabled")


def _insert_inline(widget: tk.Text, text: str) -> None:
    """Insere texto com formatação inline (bold, italic, code, links)."""

    pattern = re.compile(
        r"(\*\*(.+?)\*\*)"
        r"|(\*(.+?)\*)"
        r"|(`(.+?)`)"
        r"|(\[(.+?)\]\((.+?)\))"
        r"|(.+?(?=\*\*|\*|`|\[|$))"
    )
    pos = 0
    for m in pattern.finditer(text):
        if m.start() > pos:
            widget.insert("end", text[pos: m.start()], "normal")
        if m.group(1):
            widget.insert("end", m.group(2), "bold")
        elif m.group(3):
            widget.insert("end", m.group(4), "italic")
        elif m.group(5):
            widget.insert("end", f" {m.group(6)} ", "code_inline")
        elif m.group(7):
            widget.insert("end", m.group(8), "link")
        elif m.group(10):
            widget.insert("end", m.group(10), "normal")
        pos = m.end()
    if pos < len(text):
        widget.insert("end", text[pos:], "normal")


class PluginPanel(tk.Frame):
    """
    Gerenciador de plugins com:
    - Lista filtrada (instalado, erro, desativado, por tag, tipo, peso)
    - Cards com ícone, nome, versão, autor, tags, peso
    - Painel de detalhe com README Markdown renderizado (somente-leitura)
    - Botões contextuais por estado (Enable/Disable, Uninstall, Reload, Homepage)
    - Sandbox: análise estática de imports + balão de aviso na instalação
    - Opção "editar conteúdo de plugins" para desbloquear o README
    - Header com contadores por categoria
    - Filtro avançado por tipo, tag, status, peso
    - Sem duplicação de botões nem fantasmas após desinstalação
    """

    _FILTER_OPTIONS = [
        ("Todos", "all"),
        ("Instalados", "installed"),
        (_t("hc.plugin_panel.bulk_err", default="Erros"), "error"),
        ("Desativados", "disabled"),
    ]

    def __init__(
        self, parent: tk.Widget, tmgr: Any, loader: PluginLoader, cfg: Any = None
    ) -> None:
        """Cria o painel de gerenciamento de plugins.

        Args:
            parent: Widget pai Tkinter.
            tmgr:   Any (ThemeManager).
            loader: Instância de PluginLoader.
            cfg:    ConfigManager (opcional, necessário para Plugin Store).
        """
        t = tmgr.theme
        super().__init__(parent, bg=t.panel_bg)
        self._tmgr = tmgr
        self._loader = loader
        self._cfg = cfg
        self._selected_id: str | None = None
        self._filter_mode = tk.StringVar(value="all")
        self._search_var = tk.StringVar()
        self._tag_var = tk.StringVar(value="")
        self._sort_var = tk.StringVar(value="name")
        self._edit_mode = tk.BooleanVar(value=False)
        self._card_widgets: dict[str, tk.Frame] = {}
        self._selected_ids: set = set()
        self._build()
        self.refresh()

    def _build(self) -> None:
        """Constrói os widgets e layout interno do componente."""
        t = self._tmgr.theme

        hdr = tk.Frame(self, bg=t.panel_bg, height=36)
        hdr.pack(fill="x", padx=0, pady=0)
        hdr.pack_propagate(False)

        tk.Label(
            hdr,
            text=_t("hc.plugin_panel.extens_es"),
            bg=t.panel_bg,
            fg=t.sidebar_header_fg,
            font=(FONT_UI, 9, "bold"),
        ).pack(side="left", padx=4, pady=6)

        self._install_btn = tk.Label(
            hdr,
            text=_t("hc.plugin_panel.instalar"),
            bg=t.panel_bg,
            fg=t.fg,
            font=(FONT_UI, 8),
            cursor="hand2",
            padx=6,
        )
        self._install_btn.pack(side="right", padx=2)
        self._install_btn.bind("<Button-1>", lambda _: self._install())

        store_btn = tk.Label(
            hdr,
            text=_t("plugin.store_title"),
            bg=t.panel_bg,
            fg=getattr(t, "accent", "#4fc3f7"),
            font=(FONT_UI, 8, "bold"),
            cursor="hand2",
            padx=6,
        )
        store_btn.pack(side="right", padx=2)
        store_btn.bind("<Button-1>", lambda _: self._open_store())
        self._ttp(store_btn, _t("plugin.store_title"))

        self._bulk_btn = tk.Label(
            hdr,
            text=_t("hc.plugin_panel.bulk"),
            bg=t.panel_bg,
            fg=t.fg,
            font=(FONT_UI, 8),
            cursor="hand2",
            padx=6,
        )
        self._bulk_btn.pack(side="right", padx=0)
        self._bulk_btn.bind("<Button-1>", self._show_bulk_menu)
        self._ttp(
            self._bulk_btn,
            _t("hc.plugin_panel.bulk_tooltip",
               default="Ações em massa (Ctrl+clique)"),
        )

        ref_btn = tk.Label(
            hdr,
            text="↻",
            bg=t.panel_bg,
            fg=t.fg,
            font=(FONT_UI, 10),
            cursor="hand2",
            padx=6,
        )
        ref_btn.pack(side="right", padx=0)
        ref_btn.bind("<Button-1>", lambda _: self.refresh())
        self._ttp(ref_btn, _t("hc.plugin_panel.recarrega_o_plugin_do_disco"))

        search_outer = tk.Frame(
            self, bg=t.input_bg, highlightthickness=1, highlightbackground=t.border
        )
        search_outer.pack(fill="x", padx=6, pady=(2, 0))
        tk.Label(
            search_outer, text="🔍", bg=t.input_bg, fg=t.comment_fg, font=(FONT_UI, 9)
        ).pack(side="left", padx=(4, 0))
        self._search_entry = tk.Entry(
            search_outer,
            textvariable=self._search_var,
            bg=t.input_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            bd=0,
            font=(FONT_UI, 9),
        )
        self._search_entry.pack(side="left", fill="x",
                                expand=True, padx=4, pady=3)
        self._search_var.trace_add("write", lambda *_: self._apply_filter())

        pills_frame = tk.Frame(self, bg=t.panel_bg)
        pills_frame.pack(fill="x", padx=6, pady=(4, 0))
        self._pill_btns: dict[str, tk.Label] = {}
        for label, mode in self._FILTER_OPTIONS:
            p = tk.Label(
                pills_frame,
                text=label,
                bg=t.panel_bg,
                fg=t.comment_fg,
                font=(FONT_UI, 8),
                padx=8,
                pady=2,
                cursor="hand2",
                relief="flat",
                highlightthickness=1,
                highlightbackground=t.border,
            )
            p.pack(side="left", padx=2, pady=2)
            p.bind("<Button-1>", lambda _, m=mode: self._set_filter(m))
            self._pill_btns[mode] = p
        self._update_pills()

        adv = tk.Frame(self, bg=t.panel_bg)
        adv.pack(fill="x", padx=6, pady=(2, 2))
        tk.Label(
            adv,
            text=_t("hc.plugin_panel.ordenar"),
            bg=t.panel_bg,
            fg=t.comment_fg,
            font=(FONT_UI, 8),
        ).pack(side="left")
        sort_cb = DropDown(
            adv,
            textvariable=self._sort_var,
            values=["name", "status", "weight", "author"],
            state="readonly",
            width=8,
            font=(FONT_UI, 8),
        )
        sort_cb.pack(side="left", padx=(2, 8))
        sort_cb.bind("<<ComboboxSelected>>", lambda _: self._apply_filter())

        tk.Label(
            adv,
            text=_t("hc.plugin_panel.tag"),
            bg=t.panel_bg,
            fg=t.comment_fg,
            font=(FONT_UI, 8),
        ).pack(side="left")
        self._tag_cb = DropDown(
            adv,
            textvariable=self._tag_var,
            values=[""],
            state="readonly",
            width=10,
            font=(FONT_UI, 8),
        )
        self._tag_cb.pack(side="left", padx=2)
        self._tag_cb.bind("<<ComboboxSelected>>",
                          lambda _: self._apply_filter())

        paned = tk.PanedWindow(
            self, orient="vertical", bg=t.border, sashwidth=4, sashrelief="flat", bd=0
        )
        paned.pack(fill="both", expand=True, padx=0, pady=4)

        list_outer = tk.Frame(paned, bg=t.panel_bg)
        paned.add(list_outer, minsize=100)

        scroll_frame = tk.Frame(list_outer, bg=t.panel_bg)
        scroll_frame.pack(fill="both", expand=True)

        vsb = ttk.Scrollbar(
            scroll_frame, orient="vertical", style="Thin.Vertical.TScrollbar"
        )
        vsb.pack(side="right", fill="y")

        self._canvas = tk.Canvas(
            scroll_frame,
            bg=t.panel_bg,
            bd=0,
            highlightthickness=0,
            yscrollcommand=vsb.set,
        )
        self._canvas.pack(side="left", fill="both", expand=True)
        vsb.config(command=self._canvas.yview)

        self._cards_frame = tk.Frame(self._canvas, bg=t.panel_bg)
        self._canvas_window = self._canvas.create_window(
            (0, 0), window=self._cards_frame, anchor="nw"
        )

        debounce_canvas(
            self._canvas, self._canvas_window, ms=40, extra_widget=self._cards_frame
        )
        self._canvas.bind("<MouseWheel>", self._on_mousewheel)
        self._canvas.bind(
            "<Button-4>", lambda e: self._canvas.yview_scroll(-1, "units")
        )
        self._canvas.bind(
            "<Button-5>", lambda e: self._canvas.yview_scroll(1, "units"))

        self._count_label = tk.Label(
            list_outer,
            text="",
            bg=t.panel_bg,
            fg=t.comment_fg,
            font=(FONT_UI, 8),
            anchor="e",
        )
        self._count_label.pack(fill="x", padx=8, pady=2)

        detail_outer = tk.Frame(paned, bg=t.panel_bg)
        paned.add(detail_outer, minsize=80)

        detail_hdr = tk.Frame(detail_outer, bg=t.panel_bg, height=28)
        detail_hdr.pack(fill="x")
        detail_hdr.pack_propagate(False)

        self._detail_title = tk.Label(
            detail_hdr,
            text=_t("hc.plugin_panel.selecione_um_plugin"),
            bg=t.panel_bg,
            fg=t.sidebar_header_fg,
            font=(FONT_UI, 9, "bold"),
            anchor="w",
        )
        self._detail_title.pack(side="left", padx=8, pady=4)

        self._edit_chk = tk.Checkbutton(
            detail_hdr,
            text=_t("hc.plugin_panel.editar"),
            variable=self._edit_mode,
            command=self._toggle_edit_mode,
            bg=t.panel_bg,
            fg=t.comment_fg,
            activebackground=t.panel_bg,
            selectcolor=t.input_bg,
            font=(FONT_UI, 8),
            cursor="hand2",
        )
        self._edit_chk.pack(side="right", padx=6)
        self._ttp(
            self._edit_chk, "Habilita edição do conteúdo do plugin (use com cuidado)"
        )

        tk.Frame(detail_outer, bg=t.border, height=1).pack(fill="x")

        readme_frame = tk.Frame(detail_outer, bg=t.panel_bg)
        readme_frame.pack(fill="both", expand=True)
        rvsb = ttk.Scrollbar(
            readme_frame, orient="vertical", style="Thin.Vertical.TScrollbar"
        )
        rvsb.pack(side="right", fill="y")
        self._readme = tk.Text(
            readme_frame,
            bg=t.panel_bg,
            fg=t.fg,
            relief="flat",
            bd=0,
            font=(FONT_UI, 10),
            state="disabled",
            wrap="word",
            highlightthickness=0,
            padx=12,
            pady=8,
            yscrollcommand=rvsb.set,
            cursor="arrow",
        )
        self._readme.pack(side="left", fill="both", expand=True)
        rvsb.config(command=self._readme.yview)

        tk.Frame(self, bg=t.border, height=1).pack(fill="x")
        self._action_bar = tk.Frame(self, bg=t.panel_bg, height=36)
        self._action_bar.pack(fill="x", padx=6, pady=4)
        self._action_bar.pack_propagate(False)

    def _ttp(self, widget: tk.Widget, text: str) -> None:
        """Adiciona tooltip simples."""
        tip_win: list = [None]

        def _show(_e):
            x = widget.winfo_rootx() + 8
            y = widget.winfo_rooty() + widget.winfo_height() + 4
            t = self._tmgr.theme
            tw = tk.Toplevel(widget)
            tw.wm_overrideredirect(True)
            tw.wm_attributes("-topmost", True)
            fr = tk.Frame(
                tw,
                bg=t.floating_bg,
                highlightthickness=1,
                highlightbackground=t.floating_border,
            )
            fr.pack()
            tk.Label(
                fr,
                text=text,
                bg=t.floating_bg,
                fg=t.fg,
                font=(FONT_UI, 8),
                padx=6,
                pady=3,
            ).pack()
            tw.geometry(f"+{x}+{y}")
            tip_win[0] = tw

        def _hide(_e):
            if tip_win[0]:
                with contextlib.suppress(tk.TclError):
                    tip_win[0].destroy()
                tip_win[0] = None

        widget.bind("<Enter>", _show, add="+")
        widget.bind("<Leave>", _hide, add="+")

    def _pill_btn(
        self,
        parent: tk.Widget,
        text: str,
        cmd: Callable,
        color: str | None = None,
        font_extra: str = "",
        tooltip: str = "",
    ) -> tk.Label:
        """Cria um botão-pílula reutilizável."""
        t = self._tmgr.theme
        fg = color or t.fg
        lbl = tk.Label(
            parent,
            text=text,
            bg=t.btn_bg,
            fg=fg,
            font=(FONT_UI, 8) if not font_extra else (FONT_UI, 8, font_extra),
            padx=8,
            pady=3,
            cursor="hand2",
            relief="flat",
            highlightthickness=1,
            highlightbackground=t.border,
        )
        lbl.pack(side="left", padx=2)
        lbl.bind("<Button-1>", lambda _: cmd())
        if tooltip:
            self._ttp(lbl, tooltip)
        return lbl

    def _update_pills(self) -> None:
        """Atualiza 'pills'."""
        t = self._tmgr.theme
        mode = self._filter_mode.get()
        for m, lbl in self._pill_btns.items():
            if m == mode:
                lbl.config(
                    bg=t.accent,
                    fg=getattr(t, "actbar_active_fg", "#fff"),
                    highlightbackground=t.accent,
                )
            else:
                lbl.config(bg=t.panel_bg, fg=t.comment_fg,
                           highlightbackground=t.border)

    def _on_mousewheel(self, e: Any) -> None:
        """Trata o evento 'mousewheel'.

        Args:
            e: Evento Tkinter.
        """
        self._canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")

    def _collect_all_ids(self) -> list[str]:
        """Retorna a lista de IDs de todos os plugins instalados."""
        return self._loader.all_plugin_ids()

    def _get_tags(self) -> list[str]:
        """Obtém e retorna 'tags'."""
        tags: set = set()
        for pid in self._loader.plugins:
            m = self._loader.get_manifest(pid)
            if m:
                tags.update(m.tags)
        return sorted(tags)

    def _get_plugin_info(self, pid: str) -> dict[str, Any]:
        """Retorna dict com metadados consolidados do plugin."""
        loaded = self._loader.plugins
        errors = self._loader.errors
        info = {}
        mod = loaded.get(pid)
        manifest = self._loader.get_manifest(pid)
        info["manifest"] = (
            _normalize_manifest(
                manifest) if manifest else PluginManifest(name=pid)
        )
        if manifest is None:
            manifest = PluginManifest(name=pid)
        enabled = getattr(mod, "_enabled", True) if mod else False
        status = (
            "error"
            if pid in errors
            else (
                "disabled"
                if pid in self._loader._disabled
                else "active" if enabled else "inactive"
            )
        )
        return {
            "id": pid,
            "mod": mod,
            "manifest": manifest,
            "status": status,
            "error": errors.get(pid, ""),
            "weight": self._loader.get_plugin_weight(pid),
            "loaded": mod is not None,
        }

    def _set_filter(self, mode: str) -> None:
        """Define o valor de 'filter'.

        Args:
            mode: Parâmetro da operação.
        """
        self._filter_mode.set(mode)
        self._update_pills()
        self._apply_filter()

    def _apply_filter(self) -> None:
        """Filtra os cards de plugins conforme o texto de busca e categoria."""
        q = self._search_var.get().lower().strip()
        mode = self._filter_mode.get()
        tag_flt = self._tag_var.get().strip()
        sort_by = self._sort_var.get()

        all_ids = self._collect_all_ids()
        infos = [self._get_plugin_info(pid) for pid in all_ids]

        if mode == "installed":
            infos = [i for i in infos if i["loaded"]]
        elif mode == "error":
            infos = [i for i in infos if i["status"] == "error"]
        elif mode == "disabled":
            infos = [i for i in infos if i["status"]
                     in ("disabled", "inactive")]

        if q:

            def _matches(info: dict) -> bool:
                m = info["manifest"]
                return (
                    q in info["id"].lower()
                    or q in m.name.lower()
                    or q in m.description.lower()
                    or q in m.author.lower()
                    or any(q in tg.lower() for tg in m.tags)
                )

            infos = [i for i in infos if _matches(i)]

        if tag_flt:
            infos = [i for i in infos if tag_flt in i["manifest"].tags]

        if sort_by == "name":
            infos.sort(key=lambda i: i["manifest"].name.lower())
        elif sort_by == "status":
            order = {"error": 0, "disabled": 1, "inactive": 2, "active": 3}
            infos.sort(key=lambda i: order.get(i["status"], 9))
        elif sort_by == "weight":
            infos.sort(key=lambda i: i["weight"], reverse=True)
        elif sort_by == "author":
            infos.sort(key=lambda i: i["manifest"].author.lower())

        self._render_cards(infos)
        total = len(all_ids)
        shown = len(infos)
        self._count_label.config(
            text=f"{shown} de {total} extensões  -  "
            f"{len(self._loader.plugins)} ativas  -  "
            f"{len(self._loader.errors)} com erro"
        )

    def _render_cards(self, infos: list[dict]) -> None:
        """Renderiza os cards dos plugins filtrados na grade de resultados."""
        t = self._tmgr.theme

        for child in self._cards_frame.winfo_children():
            child.destroy()
        self._card_widgets.clear()

        self._selected_ids = set(self._selected_ids)

        if not infos:
            tk.Label(
                self._cards_frame,
                text=_t("hc.plugin_panel.nenhum_plugin_encontrado"),
                bg=t.panel_bg,
                fg=t.comment_fg,
                font=(FONT_UI, 10),
                pady=20,
            ).pack()
            return

        for info in infos:
            self._create_card(info)

        self._canvas.update_idletasks()
        self._on_cards_configure()

    def _create_card(self, info: dict) -> None:
        """Executa a operação 'create card'.

        Args:
            info: Parâmetro da operação.
        """
        t = self._tmgr.theme
        pid = info["id"]
        m = info["manifest"]
        status = info["status"]

        status_colors = {
            "active": (getattr(t, "accent", "#4fc3f7"), "●"),
            "inactive": (t.comment_fg, "○"),
            "disabled": ("#f0a500", "⊘"),
            "error": (t.err_fg, "✕"),
        }
        s_color, s_icon = status_colors.get(status, (t.comment_fg, "?"))

        is_sel = pid == self._selected_id
        card_bg = getattr(t, "sel_bg", t.input_bg) if is_sel else t.panel_bg
        border_c = t.accent if is_sel else t.border

        card = tk.Frame(
            self._cards_frame,
            bg=card_bg,
            highlightthickness=1,
            highlightbackground=border_c,
            padx=0,
            pady=0,
        )
        card.pack(fill="x", padx=6, pady=2)
        self._card_widgets[pid] = card

        row1 = tk.Frame(card, bg=card_bg, padx=8, pady=5)
        row1.pack(fill="x")

        tk.Label(
            row1, text=m.icon or "🔌", bg=card_bg, fg=t.fg, font=(FONT_UI, 14)
        ).pack(side="left", padx=(0, 6))

        info_frame = tk.Frame(row1, bg=card_bg)
        info_frame.pack(side="left", fill="x", expand=True)

        name_row = tk.Frame(info_frame, bg=card_bg)
        name_row.pack(fill="x")

        tk.Label(
            name_row,
            text=m.name,
            bg=card_bg,
            fg=t.fg,
            font=(FONT_UI, 10, "bold"),
            anchor="w",
        ).pack(side="left")

        tk.Label(
            name_row,
            text=f" v{m.version}",
            bg=card_bg,
            fg=t.comment_fg,
            font=(FONT_UI, 8),
            anchor="w",
        ).pack(side="left")

        tk.Label(
            name_row, text=f" {s_icon}", bg=card_bg, fg=s_color, font=(FONT_UI, 9)
        ).pack(side="left")

        weight = info["weight"]
        if weight > 0:
            tk.Label(
                name_row,
                text=f"  {weight}KB",
                bg=card_bg,
                fg=t.comment_fg,
                font=(FONT_UI, 7),
            ).pack(side="right")

        if m.description:
            desc = (
                m.description
                if len(m.description) <= 80
                else m.description[:77] + "..."
            )
            tk.Label(
                info_frame,
                text=desc,
                bg=card_bg,
                fg=t.comment_fg,
                font=(FONT_UI, 8),
                anchor="w",
                wraplength=260,
            ).pack(fill="x")

        if m.tags:
            tags_row = tk.Frame(info_frame, bg=card_bg)
            tags_row.pack(fill="x", pady=(2, 0))
            for tg in m.tags[:4]:
                tk.Label(
                    tags_row,
                    text=tg,
                    bg=getattr(t, "gutter_bg", t.input_bg),
                    fg=t.comment_fg,
                    font=(FONT_UI, 7),
                    padx=4,
                    pady=1,
                    relief="flat",
                ).pack(side="left", padx=(0, 2))

        def _click(e=None, p=pid):
            if e and (e.state & 0x4):
                self._toggle_multi_select(p)
            else:
                self._selected_ids.clear()
                self._select_plugin(p)

        def _right_click(e=None, p=pid):
            if p not in self._selected_ids:
                self._selected_ids.clear()
                self._select_plugin(p)
            self._show_card_context_menu(e, p)

        def _enter(_e=None, p=pid, fr=card, _bg=card_bg):
            if p != self._selected_id:
                fr.config(highlightbackground=t.accent)

        def _leave(_e=None, p=pid, fr=card, _bg=card_bg, bc=border_c):
            if p != self._selected_id:
                fr.config(highlightbackground=bc)

        for w in card.winfo_children() + [card]:
            w.bind("<Button-1>", _click, add="+")
            w.bind("<Button-3>", _right_click, add="+")
            w.bind("<Enter>", _enter, add="+")
            w.bind("<Leave>", _leave, add="+")

        def _bind_all(widget):
            widget.bind("<Button-1>", _click, add="+")
            widget.bind("<Button-3>", _right_click, add="+")
            widget.bind("<Enter>", _enter, add="+")
            widget.bind("<Leave>", _leave, add="+")
            for c in widget.winfo_children():
                _bind_all(c)

        _bind_all(card)

    def _select_plugin(self, pid: str) -> None:
        """Executa a operação 'select plugin'.

        Args:
            pid: Parâmetro da operação.
        """
        old = self._selected_id
        self._selected_id = pid

        if old and old in self._card_widgets:
            self._restyle_card(old, selected=False)
        if pid in self._card_widgets:
            self._restyle_card(pid, selected=True)

        self._update_readme(pid)
        self._update_action_bar(pid)

    def _restyle_card(self, pid: str, selected: bool) -> None:
        """Executa a operação 'restyle card'.

        Args:
            pid: Parâmetro da operação.
            selected: Parâmetro da operação.
        """
        t = self._tmgr.theme
        card = self._card_widgets.get(pid)
        if not card or not card.winfo_exists():
            return
        bg = getattr(t, "sel_bg", t.input_bg) if selected else t.panel_bg
        bc = t.accent if selected else t.border
        try:
            card.config(bg=bg, highlightbackground=bc)
            for child in card.winfo_children():
                self._set_bg_recursive(child, bg)
        except tk.TclError:
            pass

    def _toggle_multi_select(self, pid: str) -> None:
        """Ctrl+clique: adiciona/remove da seleção múltipla."""
        if pid in self._selected_ids:
            self._selected_ids.discard(pid)
            self._restyle_card(pid, selected=(pid == self._selected_id))
        else:
            self._selected_ids.add(pid)
            self._restyle_card_multi(pid, True)

    def _restyle_card_multi(self, pid: str, selected: bool) -> None:
        """Estilo de destaque para seleção múltipla (contorno laranja)."""
        t = self._tmgr.theme
        card = self._card_widgets.get(pid)
        if not card or not card.winfo_exists():
            return
        accent2 = getattr(t, "warn_fg", "#f0a500")
        bc = accent2 if selected else t.border
        bg = getattr(t, "sel_bg", t.input_bg) if selected else t.panel_bg
        with contextlib.suppress(tk.TclError):
            card.config(highlightbackground=bc, bg=bg)
            for child in card.winfo_children():
                self._set_bg_recursive(child, bg)

    def _show_card_context_menu(self, e: tk.Event, pid: str) -> None:
        """Menu flutuante (botão direito) sobre um card de plugin."""
        t = self._tmgr.theme
        info = self._get_plugin_info(pid)
        status = info["status"]
        multi = len(self._selected_ids) > 1
        targets = list(self._selected_ids) if multi else [pid]

        menu = tk.Menu(
            self,
            tearoff=False,
            bg=t.floating_bg,
            fg=t.fg,
            activebackground=t.sel_bg,
            activeforeground=t.sel_fg,
            relief="flat",
            bd=1,
        )

        if multi:
            menu.add_command(
                label=f"▶ Ativar {len(targets)} plugins",
                command=lambda: self._bulk_action("enable", targets),
            )
            menu.add_command(
                label=f"⊘ Desativar {len(targets)} plugins",
                command=lambda: self._bulk_action("disable", targets),
            )
            menu.add_command(
                label=f"↻ Recarregar {len(targets)} plugins",
                command=lambda: self._bulk_action("reload", targets),
            )
            menu.add_separator()
            menu.add_command(
                label=f"🗑 {_t('hc.plugin_panel.desinstalar')} ({len(targets)})",
                foreground=t.err_fg,
                command=lambda: self._bulk_uninstall(targets),
            )
            menu.add_separator()
            menu.add_command(
                label=_t("hc.plugin_panel.limpar_sele_o_m_ltipla"),
                command=self._clear_multi_select,
            )
        else:
            if status in ("active", "inactive", "disabled"):
                is_enabled = status == "active"
                toggle_label = (
                    _t("hc.plugin_panel.desativar") if is_enabled else "▶ Ativar"
                )
                menu.add_command(
                    label=toggle_label, command=lambda p=pid: self._toggle(p)
                )
            menu.add_command(
                label=_t("hc.plugin_panel.recarregar"),
                command=lambda p=pid: self._reload(p),
            )
            menu.add_command(
                label=_t("hc.plugin_panel.reinstalar"),
                command=lambda p=pid: self._reinstall(p),
            )
            menu.add_separator()
            menu.add_command(
                label=_t("hc.plugin_panel.localizar_no_explorador"),
                command=lambda p=pid: self._open_in_explorer(p),
            )
            if info["manifest"].homepage:
                menu.add_command(
                    label=_t("hc.plugin_panel.homepage"),
                    command=lambda url=info["manifest"].homepage: webbrowser.open(
                        url),
                )
            menu.add_separator()
            menu.add_command(
                label=_t("hc.plugin_panel.desinstalar"),
                foreground=t.err_fg,
                command=lambda p=pid: self._uninstall(p),
            )

        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()

    def _show_bulk_menu(self, e: tk.Event) -> None:
        """Exibe o menu contextual de ações em lote para plugins selecionados."""
        t = self._tmgr.theme
        selected = list(self._selected_ids)
        visible = list(self._card_widgets.keys())
        targets = selected if selected else visible
        label = (
            f"{len(targets)} selecionados" if selected else f"todos ({len(targets)})"
        )

        menu = tk.Menu(
            self,
            tearoff=False,
            bg=t.floating_bg,
            fg=t.fg,
            activebackground=t.sel_bg,
            activeforeground=t.sel_fg,
            relief="flat",
            bd=1,
        )
        menu.add_command(
            label=f"▶ Ativar {label}",
            command=lambda: self._bulk_action("enable", targets),
        )
        menu.add_command(
            label=f"⊘ Desativar {label}",
            command=lambda: self._bulk_action("disable", targets),
        )
        menu.add_command(
            label=f"↻ Recarregar {label}",
            command=lambda: self._bulk_action("reload", targets),
        )
        menu.add_separator()
        menu.add_command(
            label=f"🗑 {_t('hc.plugin_panel.desinstalar')} {label}",
            foreground=t.err_fg,
            command=lambda: self._bulk_uninstall(targets),
        )
        menu.add_separator()
        menu.add_command(
            label=_t("hc.plugin_panel.selecionar_todos_vis_veis"),
            command=self._select_all_visible,
        )
        menu.add_command(
            label=_t("hc.plugin_panel.limpar_sele_o"), command=self._clear_multi_select
        )
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()

    def _select_all_visible(self) -> None:
        """Seleciona todos os plugins visíveis na grade de resultados."""
        self._selected_ids = set(self._card_widgets.keys())
        for pid in self._selected_ids:
            self._restyle_card_multi(pid, True)

    def _clear_multi_select(self) -> None:
        """Remove o highlight de seleção múltipla."""
        for pid in list(self._selected_ids):
            self._restyle_card(pid, selected=(pid == self._selected_id))
        self._selected_ids.clear()

    def _bulk_action(self, action: str, pids: list[str]) -> None:
        """Executa ação em lote: enable / disable / reload."""
        errors: list[str] = []
        for pid in pids:
            try:
                if action == "enable":
                    if self._get_plugin_info(pid)["status"] != "active":
                        self._loader.toggle(pid)
                elif action == "disable":
                    if self._get_plugin_info(pid)["status"] == "active":
                        self._loader.toggle(pid)
                elif action == "reload" and not self._loader.reload(pid):
                    errors.append(pid)
            except (OSError, RuntimeError) as exc:
                errors.append(f"{pid}: {exc}")
        self._clear_multi_select()
        self.refresh()
        if errors:
            messagebox.showerror(
                _t("hc.plugin_panel.bulk_err", default="Erros"),
                "Falhou em:\n" + "\n".join(errors),
                parent=self,
            )

    def _bulk_uninstall(self, pids: list[str]) -> None:
        """Desinstala múltiplos plugins com uma única confirmação."""
        if not pids:
            return
        names = [self._get_plugin_info(p)["manifest"].name for p in pids]
        if not messagebox.askyesno(
            _t("hc.plugin_panel.desinstalar"),
            f"Deseja remover permanentemente {len(pids)} plugin(s)?\n\n"
            + "\n".join(f"  - {n}" for n in names[:15])
            + ("\n  ..." if len(names) > 15 else ""),
            parent=self,
        ):
            return
        errors: list[str] = []
        for pid in pids:
            if not self._loader.uninstall(pid):
                errors.append(pid)
        self._selected_ids.clear()
        self._selected_id = None
        self.refresh()
        self._readme.config(state="normal")
        self._readme.delete("1.0", "end")
        self._readme.config(state="disabled")
        for w in self._action_bar.winfo_children():
            w.destroy()
        self._detail_title.config(
            text=_t("hc.plugin_panel.selecione_um_plugin"))
        if errors:
            messagebox.showerror(
                _t("hc.plugin_panel.desinstalar_err",
                   default="Erros ao desinstalar"),
                "Não foi possível remover:\n" + "\n".join(errors),
                parent=self,
            )

    def _reinstall(self, pid: str) -> None:
        """Reinstala: remove e pede novo arquivo."""
        info = self._get_plugin_info(pid)
        m = info["manifest"]
        if not messagebox.askyesno(
            _t("hc.plugin_panel.reinstalar"),
            f"'{m.name}' será removido e você escolherá um novo arquivo.\n\nDeseja continuar?",
            parent=self,
        ):
            return
        if not self._loader.uninstall(pid):
            messagebox.showerror(
                _t("hc.plugin_panel.reinstalar_err",
                   default="Erro ao reinstalar"),
                f"Não foi possível remover '{pid}' para reinstalação.",
                parent=self,
            )
            return
        self._selected_id = None
        self.refresh()
        self._install()

    def _set_bg_recursive(self, widget: tk.Widget, bg: str) -> None:
        """Define o valor de 'bg recursive'.

        Args:
            widget: Parâmetro da operação.
            bg: Parâmetro da operação.
        """
        with contextlib.suppress(tk.TclError):
            widget.config(bg=bg)
        for child in widget.winfo_children():
            self._set_bg_recursive(child, bg)

    def _update_readme(self, pid: str) -> None:
        """Atualiza 'readme'.

        Args:
            pid: Parâmetro da operação.
        """
        t = self._tmgr.theme
        info = self._get_plugin_info(pid)
        m = info["manifest"]

        if hasattr(m, "icon"):
            icon = m.icon
            name = m.name
            author = m.author if hasattr(m, "author") else ""
        else:
            icon = m.get("icon", "🔌")
            name = m.get("name", pid)
            author = m.get("author", "")

        self._detail_title.config(
            text=f"  {icon}  {name}  -  {author or 'autor desconhecido'}"
        )

        if info["status"] == "error":
            err_md = f"# ❌  Erro ao carregar {name}\n\n```\n{info['error']}\n```\n"
            _render_markdown(self._readme, err_md, t)
        else:
            readme_content = self._loader.get_readme(pid)

            blocked, warned = self._loader.get_security_info(pid)
            prefix = ""
            if blocked:
                prefix += f"⚠ **AVISO DE SEGURANÇA** - Imports bloqueados: `{'`, `'.join(blocked)}`\n\n"
            if warned:
                prefix += f"ℹ️ Requisito de Risco: `{'`, `'.join(warned)}`\n\n"
            _render_markdown(self._readme, prefix + readme_content, t)

    def _update_action_bar(self, pid: str) -> None:
        """Atualiza 'action bar'.

        Args:
            pid: Parâmetro da operação.
        """
        t = self._tmgr.theme
        info = self._get_plugin_info(pid)
        m = info["manifest"]
        m = _normalize_manifest(m)
        logger.debug("manifest: %s", m)

        for w in self._action_bar.winfo_children():
            w.destroy()

        info = self._get_plugin_info(pid)
        m = info["manifest"]
        m = _normalize_manifest(m)
        status = info["status"]

        if status in ("active", "inactive"):
            is_enabled = status == "active"
            toggle_text = _t(
                "hc.plugin_panel.desativar") if is_enabled else "▶ Ativar"
            toggle_color = "#f0a500" if is_enabled else getattr(
                t, "accent", "#4fc3f7")
            self._pill_btn(
                self._action_bar,
                toggle_text,
                lambda p=pid: self._toggle(p),
                color=toggle_color,
                tooltip=_t("hc.plugin_panel.ativar_desativar_plugin"),
            )
        elif status == "disabled":
            self._pill_btn(
                self._action_bar,
                "▶ Ativar",
                lambda p=pid: self._toggle(p),
                color=getattr(t, "accent", "#4fc3f7"),
                tooltip=_t("hc.plugin_panel.reativar_plugin"),
            )
        elif status == "error":
            self._pill_btn(
                self._action_bar,
                "↻ Tentar Recarregar",
                lambda p=pid: self._reload(p),
                tooltip=_t("hc.plugin_panel.tenta_recarregar_o_plugin_com_"),
            )

        if status != "error":
            self._pill_btn(
                self._action_bar,
                _t("hc.plugin_panel.recarregar"),
                lambda p=pid: self._reload(p),
                tooltip=_t("hc.plugin_panel.recarrega_o_plugin_do_disco"),
            )

        if m.homepage:
            self._pill_btn(
                self._action_bar,
                _t("hc.plugin_panel.homepage"),
                lambda url=m.homepage: webbrowser.open(url),
                tooltip=m.homepage,
            )

        self._pill_btn(
            self._action_bar,
            "📂 Localizar",
            lambda p=pid: self._open_in_explorer(p),
            tooltip=_t("hc.plugin_panel.abre_a_pasta_do_plugin_no_expl"),
        )

        self._pill_btn(
            self._action_bar,
            _t("hc.plugin_panel.desinstalar"),
            lambda p=pid: self._uninstall(p),
            color=t.err_fg,
            font_extra="",
            tooltip=_t("hc.plugin_panel.remove_permanentemente_o_plugi"),
        )

    def _toggle_edit_mode(self) -> None:
        """Alterna o modo de edição avançada do painel de plugins."""
        editing = self._edit_mode.get()
        self._readme.config(
            state="normal" if editing else "disabled",
            cursor="xterm" if editing else "arrow",
        )

    def _toggle(self, pid: str) -> None:
        """Alterna o estado do componente (ativo/inativo ou visível/oculto).

        Args:
            pid: Parâmetro da operação.
        """
        _ = self._loader.toggle(pid)
        self.refresh(keep_selection=pid)

    def _reload(self, pid: str) -> None:
        """Recarrega o componente sem reiniciar.

        Args:
            pid: Parâmetro da operação.
        """
        ok = self._loader.reload(pid)
        self.refresh(keep_selection=pid)
        if ok:
            logger.info("Plugin %s recarregado com sucesso.", pid)
        else:
            messagebox.showerror(
                "Reload Failed",
                f"Não foi possível recarregar o plugin '{pid}'.\n\n"
                + self._loader.errors.get(pid, "(sem detalhes)"),
                parent=self,
            )

    def _install(self) -> None:
        """Instala componente ou plugin."""
        paths = filedialog.askopenfilenames(
            title=_t("hc.app.instalar_plugin"),
            filetypes=[
                ("Plugins Codis", "*.py *.zip"),
                ("Python", "*.py"),
                ("ZIP", "*.zip"),
                ("Todos", "*.*"),
            ],
            parent=self,
        )
        if not paths:
            return
        errors: list[str] = []
        installed: list[str] = []

        for p in paths:
            src = Path(p)
            pid = src.stem

            def _confirm_cb(
                plugin_id: str, blocked: list[str], warned: list[str], _src=src
            ) -> bool:
                dlg = _PluginTrustDialog(
                    self, self._tmgr, plugin_id, blocked, warned)
                if dlg.dont_ask:
                    with contextlib.suppress(OSError):
                        source_text = _get_plugin_source(_src)
                        content_hash = hashlib.sha256(
                            source_text.encode("utf-8")
                        ).hexdigest()
                        self._loader._trusted[plugin_id] = content_hash
                        _save_trusted_plugins(self._loader._trusted)
                return dlg.result

            try:
                ok = self._loader.install_from_path(
                    src, confirm_cb=_confirm_cb)
                if ok:
                    installed.append(pid)
                else:
                    errors.append(f"{pid}: instalação cancelada ou falhou")
            except (OSError, RuntimeError) as exc:
                errors.append(f"{pid}: {exc}")

        self.refresh()

        if installed:
            logger.info("Plugins instalados: %s", ", ".join(installed))
        if errors:
            messagebox.showerror(
                _t("hc.plugin_panel.instalar_err",
                   default="Erros na instalação"),
                "Os seguintes plugins não foram instalados:\n\n" +
                "\n".join(errors),
                parent=self,
            )

    def _uninstall(self, pid: str) -> None:
        """Remove componente ou plugin instalado.

        Args:
            pid: Parâmetro da operação.
        """
        info = self._get_plugin_info(pid)
        m = info["manifest"]
        if not messagebox.askyesno(
            _t("hc.plugin_panel.desinstalar"),
            f"Deseja remover permanentemente '{m.name}'?\n\n"
            f"Todos os arquivos do plugin serão apagados.",
            parent=self,
        ):
            return
        ok = self._loader.uninstall(pid)
        if ok:
            self._selected_id = None
            self.refresh()

            self._readme.config(state="normal")
            self._readme.delete("1.0", "end")
            self._readme.config(state="disabled")
            for w in self._action_bar.winfo_children():
                w.destroy()
            self._detail_title.config(
                text=_t("hc.plugin_panel.selecione_um_plugin"))
        else:
            messagebox.showerror(
                _t("hc.plugin_panel.desinstalar_err",
                   default="Erro ao desinstalar"),
                f"Não foi possível remover completamente '{pid}'.\n"
                "Verifique permissões do sistema de arquivos.",
                parent=self,
            )

    def _open_in_explorer(self, pid: str) -> None:
        """Executa a operação 'open in explorer'.

        Args:
            pid: Parâmetro da operação.
        """
        path = self._loader._paths.get(pid, PLUGINS_DIR)
        if path:
            folder = path.parent if path.is_file() else path
            try:
                if sys.platform == "win32":
                    os.startfile(folder)
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", str(folder)])
                else:
                    subprocess.Popen(["xdg-open", str(folder)])
            except OSError as exc:
                messagebox.showerror(
                    _t("hc.plugin_panel.erro"), str(exc), parent=self)

    def refresh(self, keep_selection: str | None = None) -> None:
        """Atualiza completamente a UI de plugins."""

        tags = [""] + self._get_tags()
        self._tag_cb.config(values=tags)

        sel = keep_selection or self._selected_id
        self._apply_filter()

        if sel and sel in self._card_widgets:
            self._select_plugin(sel)
        elif sel:
            self._selected_id = None
            self._detail_title.config(
                text=_t("hc.plugin_panel.selecione_um_plugin"))
            self._readme.config(state="normal")
            self._readme.delete("1.0", "end")
            self._readme.config(state="disabled")
            for w in self._action_bar.winfo_children():
                w.destroy()

    def _open_store(self) -> None:
        """Abre a Plugin Store para buscar e instalar plugins do repositório remoto."""
        open_plugin_store(
            parent=self,
            tmgr=self._tmgr,
            cfg=self._cfg,
            loader=self._loader,
            on_refresh=self.refresh,
        )

    def apply_theme(self, t: Any) -> None:
        """Executa a operação 'apply theme'.

        Args:
            t: Parâmetro da operação.
        """
        self.config(bg=t.panel_bg)
        self.refresh()
