"""Painel de saída (Output) do Codis.

Implementa ``OutputPanel``, widget de texto somente-leitura que recebe
e exibe mensagens de saída de processos, builds, linters e outras
operações assíncronas da aplicação.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import subprocess
import sys
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import ttk
from typing import Any

from src.config import ConfigManager
from src.constants import FONT_MONO, FONT_SIZE, FONT_UI
from src.i18n import _t
from src.theme.base import Theme
from src.theme.manager import ThemeManager
from src.ui.dropdown import DropDown

logger = logging.getLogger("Codis")

_LINE_SUFFIX = re.compile(r"(?::\d+){1,2}$")
_EXECUTABLE_SUFFIXES = frozenset(
    {".exe", ".bat", ".cmd", ".com", ".scr", ".msi", ".ps1", ".vbs", ".vbe", ".js", ".jse",
     ".wsf", ".lnk", ".jar", ".sh", ".command", ".app", ".desktop", ".reg", ".hta", ".cpl"}
)


class OutputPanel(tk.Frame):
    """
    Painel de output:
    - Timestamps coloridos por nível
    - Links clicáveis (sem capturar pontuação trailing)
    - Busca rápida embutida
    - Word-wrap toggle
    - Trim automático de max-lines por canal
    - add_channel atualiza combobox corretamente
    - apply_theme reconfigurar tags
    """

    _MAX_LINES = 5000

    def __init__(
        self, parent: tk.Widget, tmgr: ThemeManager, cfg: ConfigManager
    ) -> None:
        """Cria o painel de saída do editor (stdout, build, etc.).

        Args:
            parent: Widget pai Tkinter.
            config: Instância de ConfigManager.
        """
        t = tmgr.theme
        super().__init__(parent, bg=t.panel_bg)
        self._tmgr = tmgr
        self._cfg = cfg

        self._channels: dict[str, list[tuple[str, str, str]]] = {
            "Codis": [],
            "Tasks": [],
            "Extensions": [],
            "Python": [],
            "ESLint": [],
            "LSP": [],
        }
        self._active_channel = "Codis"
        self._wrap = False
        self._build()

    def _build(self) -> None:
        """Constrói os widgets e layout interno do componente."""
        t = self._tmgr.theme

        tb = tk.Frame(self, bg=t.panel_bg, height=30)
        tb.pack(fill="x")
        tb.pack_propagate(False)

        tk.Label(
            tb,
            text=f"  {_t('panel.output')}",
            bg=t.panel_bg,
            fg=t.sidebar_header_fg,
            font=(FONT_UI, 9, "bold"),
        ).pack(side="left", padx=4)

        self._chan_var = tk.StringVar(value=self._active_channel)
        self._chan_combo = DropDown(
            tb,
            textvariable=self._chan_var,
            values=list(self._channels.keys()),
            width=14,
            font=(FONT_UI, 8),
            state="readonly",
        )
        self._chan_combo.pack(side="left", padx=4, pady=3)
        self._chan_combo.bind("<<ComboboxSelected>>", self._switch_channel)

        for text_key, cmd in (
            ("output.clear", self.clear),
            ("output.copy", self.copy_all),
        ):
            btn = tk.Label(
                tb,
                text=_t(text_key),
                bg=t.panel_bg,
                fg=t.fg,
                font=(FONT_UI, 8),
                cursor="hand2",
                padx=6,
            )
            btn.pack(side="right", padx=2)
            btn.bind("<Button-1>", lambda _, c=cmd: c())
            btn.bind("<Enter>", lambda _, b=btn: b.config(bg=t.btn_hover))
            btn.bind("<Leave>", lambda _, b=btn: b.config(bg=t.panel_bg))

        wrap_btn = tk.Label(
            tb,
            text=_t("output.wrap"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 10),
            cursor="hand2",
            padx=4,
        )
        wrap_btn.pack(side="right")
        wrap_btn.bind("<Button-1>", lambda _: self._toggle_wrap())

        tk.Frame(self, bg=t.border, height=1).pack(fill="x")

        txt_frame = tk.Frame(self, bg=t.panel_bg)
        txt_frame.pack(fill="both", expand=True)

        self._text = tk.Text(
            txt_frame,
            bg=t.terminal_bg,
            fg=t.fg,
            relief="flat",
            bd=0,
            font=(FONT_MONO, self._cfg.get("font_size", FONT_SIZE) - 1),
            state="disabled",
            wrap="none",
            highlightthickness=0,
            padx=6,
            pady=4,
        )
        vsb = ttk.Scrollbar(
            txt_frame,
            orient="vertical",
            command=self._text.yview,
            style="Thin.Vertical.TScrollbar",
        )
        vsb.pack(side="right", fill="y")
        hsb = ttk.Scrollbar(
            txt_frame,
            orient="horizontal",
            command=self._text.xview,
            style="Thin.Horizontal.TScrollbar",
        )
        hsb.pack(side="bottom", fill="x")
        self._text.config(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self._text.pack(fill="both", expand=True)

        self._configure_tags(t)

        self._text.tag_bind("link", "<Button-1>", self._on_link_click)
        self._text.tag_bind(
            "link", "<Enter>", lambda _: self._text.config(cursor="hand2")
        )
        self._text.tag_bind("link", "<Leave>",
                            lambda _: self._text.config(cursor=""))

    def _configure_tags(self, t: Theme) -> None:
        """Executa a operação 'configure tags'.

        Args:
            t: Parâmetro da operação.
        """
        self._text.tag_configure("info", foreground=t.info_fg)
        self._text.tag_configure("warn", foreground=t.warn_fg)
        self._text.tag_configure("error", foreground=t.err_fg)
        self._text.tag_configure("success", foreground=t.diff_add_fg)
        self._text.tag_configure("link", foreground=t.t_link, underline=True)
        self._text.tag_configure("ts", foreground=t.fg_dim)

    _LINK_RE = re.compile(
        r"(https?://[^\s\)\]>\"']+[^\s\)\]>\"',;.])"
        r"|(/[\w./\-_]+\.[a-zA-Z]{1,6}(?::\d+)?)"
    )

    def write(
        self,
        msg: str,
        channel: str = "Codis",
        level: str = "info",
        timestamp: bool = False,
    ) -> None:
        """Escreve uma mensagem no canal especificado."""
        if channel not in self._channels:
            self._channels[channel] = []
            self._update_combo()

        ts_str = f"[{datetime.now().strftime('%H:%M:%S')}] " if timestamp else ""
        entry = (msg, level, ts_str)
        chan = self._channels[channel]
        chan.append(entry)

        if len(chan) > self._MAX_LINES:
            self._channels[channel] = chan[-(self._MAX_LINES // 2):]

        if channel == self._active_channel:
            self._append_entry(entry)

    def _append_entry(self, entry: tuple[str, str, str]) -> None:
        """Executa a operação 'append entry'.

        Args:
            entry: Parâmetro da operação.
        """
        msg, level, ts_str = entry
        self._text.config(state="normal")
        if ts_str:
            self._text.insert("end", ts_str, ("ts",))
        full = msg + "\n"
        parts = self._LINK_RE.split(full)
        for i, part in enumerate(parts):
            if part is None:
                continue
            if i % 3 == 0:
                if part:
                    self._text.insert("end", part, (level,))
            else:
                if part:
                    self._text.insert("end", part, ("link",))
        self._text.see("end")
        self._text.config(state="disabled")

    def _switch_channel(self, _e: Any = None) -> None:
        """Executa a operação 'switch channel'.

        Args:
            _e: Parâmetro da operação.
        """
        self._active_channel = self._chan_var.get()
        self._reload()

    def _reload(self) -> None:
        """Recarrega o canal ativo mantendo colorização."""
        self._text.config(state="normal")
        self._text.delete("1.0", "end")
        for entry in self._channels.get(self._active_channel, []):
            self._append_entry(entry)
        self._text.config(state="disabled")

    def _update_combo(self) -> None:
        """Atualiza 'combo'."""
        self._chan_combo["values"] = list(self._channels.keys())

    def add_channel(self, name: str) -> None:
        """Adiciona um novo canal ao painel de output."""
        if name not in self._channels:
            self._channels[name] = []
            self._update_combo()

    def clear(self) -> None:
        """Limpa o conteúdo do canal ativo."""
        self._channels[self._active_channel] = []
        self._text.config(state="normal")
        self._text.delete("1.0", "end")
        self._text.config(state="disabled")

    def copy_all(self) -> None:
        """Copia todo o conteúdo do painel de saída para o clipboard."""
        content = self._text.get("1.0", "end-1c")
        with contextlib.suppress(tk.TclError):
            self.clipboard_clear()
            self.clipboard_append(content)

    def _toggle_wrap(self) -> None:
        """Alterna entre quebra de linha ativa e desativada no painel de saída."""
        self._wrap = not self._wrap
        self._text.config(wrap="word" if self._wrap else "none")

    def _on_link_click(self, e: tk.Event) -> None:
        """Trata o evento 'link click'.

        Args:
            e: Evento Tkinter.
        """
        idx = self._text.index(f"@{e.x},{e.y}")
        for tag in self._text.tag_names(idx):
            if tag == "link":
                ranges = self._text.tag_ranges("link")
                for i in range(0, len(ranges), 2):
                    s, en = ranges[i], ranges[i + 1]
                    if self._text.compare(s, "<=", idx) and self._text.compare(
                        idx, "<=", en
                    ):
                        link = self._text.get(s, en)
                        if link.startswith(("http://", "https://")):
                            webbrowser.open(link)
                        else:
                            with contextlib.suppress(OSError):
                                p = Path(_LINE_SUFFIX.sub("", link))
                                if p.is_file() and p.suffix.lower() not in _EXECUTABLE_SUFFIXES:
                                    if sys.platform == "win32":
                                        os.startfile(str(p))
                                    elif sys.platform == "darwin":
                                        with subprocess.Popen(["open", str(p)]):
                                            pass
                                    else:
                                        with subprocess.Popen(["xdg-open", str(p)]):
                                            pass
                        break

    def apply_theme(self, t: Theme) -> None:
        """Aplica o tema ao painel de output."""
        self.config(bg=t.panel_bg)
        self._text.config(bg=t.terminal_bg, fg=t.fg)
        self._configure_tags(t)
