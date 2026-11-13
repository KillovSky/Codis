"""Painel e gerenciador de bookmarks do Codis.

Provê ``BookmarkManager`` para persistência de marcadores de posição em
arquivos, e ``BookmarksPanel``, o widget de listagem e navegação de
bookmarks integrado à barra lateral do editor.
"""

from __future__ import annotations

import contextlib
import json
import logging
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import ttk
from typing import Any

from src.constants import FONT_MONO, FONT_UI
from src.i18n import _t

logger = logging.getLogger("Codis")
_BM_FILE = Path.home() / ".codis" / "bookmarks.json"


class BookmarkManager:
    """
    Gerencia marcadores persistentes por arquivo.
    Formato: {filepath: [{line, col, snippet, note}]}
    """

    def __init__(self) -> None:
        """Inicializa o gerenciador de bookmarks com persistência em JSON."""
        self._data: dict[str, list[dict[str, Any]]] = {}
        self._load()

    def _load(self) -> None:
        """Carrega dados do disco ou fonte configurada."""
        with contextlib.suppress(tk.TclError):
            if _BM_FILE.exists():
                raw = json.loads(_BM_FILE.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self._data = raw

    def _save(self) -> None:
        """Persiste os dados no disco."""
        with contextlib.suppress(OSError):
            _BM_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = _BM_FILE.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            tmp.replace(_BM_FILE)

    def add(
        self, filepath: str, line: int, col: int, snippet: str, note: str = ""
    ) -> None:
        """Adiciona ou substitui um marcador na linha especificada do arquivo."""
        key = filepath
        if key not in self._data:
            self._data[key] = []

        self._data[key] = [b for b in self._data[key] if b["line"] != line]
        self._data[key].append(
            {
                "line": line,
                "col": col,
                "snippet": snippet[:80],
                "note": note,
            }
        )
        self._data[key].sort(key=lambda b: b["line"])
        self._save()

    def remove(self, filepath: str, line: int) -> None:
        """Remove o marcador na linha especificada do arquivo."""
        if filepath in self._data:
            self._data[filepath] = [
                b for b in self._data[filepath] if b["line"] != line
            ]
            self._save()

    def get(self, filepath: str) -> list[dict[str, Any]]:
        """Retorna o bookmark registrado para ``path``, ou ``None`` se não existir."""
        return list(self._data.get(filepath, []))

    def get_all(self) -> dict[str, list[dict[str, Any]]]:
        """Retorna todos os bookmarks agrupados por caminho de arquivo."""
        return dict(self._data)

    def next_bookmark(self, filepath: str, current_line: int) -> dict[str, Any] | None:
        """Retorna o próximo marcador após a linha atual, com wrap-around."""
        bms = self.get(filepath)
        for bm in sorted(bms, key=lambda b: b["line"]):
            if bm["line"] > current_line:
                return bm
        return bms[0] if bms else None

    def prev_bookmark(self, filepath: str, current_line: int) -> dict[str, Any] | None:
        """Retorna o marcador anterior à linha atual, com wrap-around."""
        bms = self.get(filepath)
        for bm in sorted(bms, key=lambda b: b["line"], reverse=True):
            if bm["line"] < current_line:
                return bm
        return bms[-1] if bms else None

    def clear_all(self) -> None:
        """Remove todos os bookmarks de todos os arquivos."""
        self._data.clear()
        self._save()


class BookmarksPanel(tk.Frame):
    """
    Painel lateral de marcadores com:
    - Lista agrupada por arquivo
    - Ícone de goto
    - Ícone de remover
    """

    def __init__(
        self,
        parent: tk.Widget,
        tmgr,
        mgr: BookmarkManager,
        on_goto: Callable[[Path, int], None] | None = None,
    ) -> None:
        """Cria o painel de marcadores de posição no código.

        Args:
            parent: Widget pai Tkinter.
            config: Instância de ConfigManager.
            on_goto: Callback(path, line) para navegar até o marcador.
        """
        t = tmgr.theme
        super().__init__(parent, bg=t.sidebar_bg)
        self._tmgr = tmgr
        self._mgr = mgr
        self._on_goto = on_goto
        self._build()

    def _build(self) -> None:
        """Constrói os widgets e layout interno do componente."""
        t = self._tmgr.theme

        hdr = tk.Frame(self, bg=t.activity_bg, height=36)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        tk.Label(
            hdr,
            text=f"  🔖  {_t('ui.bookmarks')}",
            bg=t.activity_bg,
            fg=t.fg,
            font=(FONT_UI, 9, "bold"),
            anchor="w",
        ).pack(side="left", fill="both")

        clr_btn = tk.Label(
            hdr,
            text="✕ All",
            bg=t.activity_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 7),
            cursor="hand2",
        )
        clr_btn.pack(side="right", padx=6)
        clr_btn.bind("<Button-1>", self._clear_all)

        scroll_frame = tk.Frame(self, bg=t.sidebar_bg)
        scroll_frame.pack(fill="both", expand=True)

        vsb = ttk.Scrollbar(
            scroll_frame, orient="vertical", style="Thin.Vertical.TScrollbar"
        )
        vsb.pack(side="right", fill="y")

        self._canvas = tk.Canvas(
            scroll_frame, bg=t.sidebar_bg, highlightthickness=0, bd=0
        )
        self._canvas.config(yscrollcommand=vsb.set)
        vsb.config(command=self._canvas.yview)
        self._canvas.pack(fill="both", expand=True)

        self._list_frame = tk.Frame(self._canvas, bg=t.sidebar_bg)
        self._win = self._canvas.create_window(
            (0, 0), window=self._list_frame, anchor="nw"
        )
        self._list_frame.bind(
            "<Configure>",
            lambda _: (
                self._canvas.config(scrollregion=self._canvas.bbox("all")),
                self._canvas.itemconfig(
                    self._win, width=self._canvas.winfo_width()),
            ),
        )
        self._canvas.bind(
            "<Configure>", lambda e: self._canvas.itemconfig(
                self._win, width=e.width)
        )

        def _wheel(e):
            delta = -1 * \
                (e.delta // 120) if e.delta else (1 if e.num == 5 else -1)
            self._canvas.yview_scroll(delta, "units")

        self._canvas.bind("<MouseWheel>", _wheel)
        self._canvas.bind("<Button-4>", _wheel)
        self._canvas.bind("<Button-5>", _wheel)

        self._populate()

    def _populate(self) -> None:
        """Popula a lista ou árvore com dados."""
        t = self._tmgr.theme
        for w in self._list_frame.winfo_children():
            w.destroy()

        data = self._mgr.get_all()
        if not data:
            tk.Label(
                self._list_frame,
                text=_t("ui.no_bookmarks"),
                bg=t.sidebar_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 9),
                justify="left",
                anchor="w",
            ).pack(fill="x", pady=8)
            return

        for filepath, bms in sorted(data.items()):
            if not bms:
                continue

            fhdr = tk.Frame(self._list_frame, bg=t.activity_bg)
            fhdr.pack(fill="x", pady=(4, 0))
            name = Path(filepath).name
            tk.Label(
                fhdr,
                text=f"  📄 {name}",
                bg=t.activity_bg,
                fg=t.fg,
                font=(FONT_UI, 8, "bold"),
                anchor="w",
            ).pack(fill="x", padx=4, pady=2)

            for bm in bms:
                row = tk.Frame(self._list_frame,
                               bg=t.sidebar_bg, cursor="hand2")
                row.pack(fill="x")

                tk.Label(
                    row,
                    text=f":{bm['line']}",
                    bg=t.sidebar_bg,
                    fg=t.accent,
                    font=(FONT_MONO, 8),
                    width=5,
                    anchor="e",
                ).pack(side="left")

                label = (bm.get("note") or bm.get("snippet", ""))[:50]
                lbl = tk.Label(
                    row,
                    text=label,
                    bg=t.sidebar_bg,
                    fg=t.fg,
                    font=(FONT_UI, 9),
                    anchor="w",
                )
                lbl.pack(side="left", fill="x", expand=True, padx=2)

                del_btn = tk.Label(
                    row,
                    text="✕",
                    bg=t.sidebar_bg,
                    fg=t.fg_muted,
                    font=(FONT_UI, 8),
                    cursor="hand2",
                )
                del_btn.pack(side="right", padx=4)
                del_btn.bind(
                    "<Button-1>",
                    lambda _, fp=filepath, ln=bm["line"]: (
                        self._mgr.remove(fp, ln),
                        self._populate(),
                    ),
                )

                def _goto(_, fp=filepath, ln=bm["line"]):
                    if self._on_goto:
                        with contextlib.suppress(OSError):
                            self._on_goto(Path(fp), ln)

                for w in (row, lbl):
                    w.bind("<Button-1>", _goto)
                    w.bind("<Enter>", lambda _, r=row: r.config(bg=t.sel_bg))
                    w.bind("<Leave>", lambda _,
                           r=row: r.config(bg=t.sidebar_bg))

    def refresh(self) -> None:
        """Atualiza a lista de marcadores exibidos no painel."""
        self._populate()

    def _clear_all(self, _e=None) -> None:
        """Limpa todos os bookmarks após confirmação do usuário."""
        self._mgr.clear_all()
        self._populate()

    def apply_theme(self, t) -> None:
        """Aplica o tema ao painel de marcadores."""
        self.config(bg=t.sidebar_bg)
        self._canvas.config(bg=t.sidebar_bg)
        self._list_frame.config(bg=t.sidebar_bg)
        self._populate()
