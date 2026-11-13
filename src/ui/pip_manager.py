"""Codis - Gerenciador de Pacotes Pip.

Funcionalidades:
  - Aba Instalados: cards visuais com versão, licença, links, badge de status
  - Aba PyPI: busca com cards ricos (descrição, autor, downloads, botão instalar)
  - Aba Ambientes: lista/cria/ativa virtual envs
  - Aba Outdated: lista pacotes com update disponível, atualização em massa
  - Log em tempo real com cores (ok/warn/err)
  - Instalar, atualizar, desinstalar com progresso
  - Exportar/importar requirements.txt
"""

from __future__ import annotations

import contextlib
import json
import logging
import queue
import subprocess
import sys
import threading
import tkinter as tk
import urllib.error
import urllib.request
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from src.constants import FONT_MONO, FONT_UI
from src.i18n import _t
from src.pyexec import NoPythonError, child_env, python_cmd, python_executable
from src.theme.utils import make_toolbar_button
from src.ui._perf_utils import debounce_canvas
from src.ui.grab_safe import safe_grab_set

logger = logging.getLogger("Codis.PipManager")

_PYPI_URL = "https://pypi.org/pypi/{}/json"
_PYPI_SRCH = (
    "https://pypi.org/search/?q={}&o=-zscore&c=Programming+Language+%3A%3A+Python"
)

_pypi_cache: dict[str, dict] = {}


def _safe_python(env_python: str | None = None) -> str:
    """Retorna um interpretador Python **real** para subprocessos.

    Nunca devolve o executável do próprio Codis: compilado, ``sys.executable``
    é o Codis, e ``[sys.executable, "-m", "pip"]`` abriria outro Codis em
    cascata. Ver :mod:`src.pyexec`.

    Raises:
        NoPythonError: se não houver Python utilizável (Codis compilado sem
            Python instalado).
    """
    return python_executable(env_python)


def _make_popen_kwargs() -> dict:
    """Kwargs extras para subprocess no Windows: oculta console e stdin."""
    kwargs: dict = {"stdin": subprocess.DEVNULL, "env": child_env()}
    if sys.platform == "win32":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        kwargs["startupinfo"] = si
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return kwargs


def _pip(env_python: str | None = None) -> list[str]:
    return python_cmd("-m", "pip", preferred=env_python)


def _run_pip(
    args: list[str], out_q: queue.Queue[str | None], env_python: str | None = None
) -> int:
    try:
        cmd = _pip(env_python) + args + ["--no-color"]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            **_make_popen_kwargs(),
        )
        for line in proc.stdout or []:
            out_q.put(line)
        proc.wait()
        out_q.put(None)
        return proc.returncode
    except Exception as exc:
        out_q.put(f"[Erro]: {exc}\n")
        out_q.put(None)
        return -1


def _fetch_pypi(name: str) -> dict | None:
    if name in _pypi_cache:
        return _pypi_cache[name]
    try:
        url = _PYPI_URL.format(name)
        req = urllib.request.Request(url, headers={"User-Agent": "Codis/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        _pypi_cache[name] = data
        return data
    except Exception:
        return None


def _list_installed(env_python: str | None = None) -> list[dict]:
    py = _safe_python(env_python)
    try:
        out = subprocess.check_output(
            [py, "-m", "pip", "list", "--format=json"],
            text=True,
            timeout=30,
            stderr=subprocess.DEVNULL,
            **_make_popen_kwargs(),
        )
        return json.loads(out)
    except NoPythonError:
        raise
    except Exception:
        return []


def _list_outdated(env_python: str | None = None) -> list[dict]:
    py = _safe_python(env_python)
    try:
        out = subprocess.check_output(
            [py, "-m", "pip", "list", "--outdated", "--format=json"],
            text=True,
            timeout=60,
            stderr=subprocess.DEVNULL,
            **_make_popen_kwargs(),
        )
        return json.loads(out)
    except NoPythonError:
        raise
    except Exception:
        return []


def _show_info(name: str, env_python: str | None = None) -> str:
    py = _safe_python(env_python)
    try:
        return subprocess.check_output(
            [py, "-m", "pip", "show", name],
            text=True,
            timeout=10,
            stderr=subprocess.DEVNULL,
            **_make_popen_kwargs(),
        )
    except NoPythonError:
        raise
    except Exception:
        return ""


def _find_venvs(base: Path | None = None) -> list[Path]:
    """Encontra virtual envs comuns."""
    candidates = []
    search_roots = [base] if base else []
    search_roots += [Path.home(), Path.cwd()]
    seen: set[Path] = set()
    for root in search_roots:
        if not root or not root.exists():
            continue
        for pattern in (
            "*/.venv",
            "*/venv",
            "*/.env",
            "*/env",
            ".venv",
            "venv",
            ".env",
            "env",
        ):
            for p in root.glob(pattern):
                rp = p.resolve()
                if rp not in seen and (rp / "pyvenv.cfg").exists():
                    seen.add(rp)
                    candidates.append(rp)
    return candidates[:30]


class PipManagerPanel(tk.Frame):
    """Gerenciador pip redesenhado para o Codis."""

    def __init__(self, parent: tk.Widget, tmgr: Any, cfg: Any) -> None:
        """Inicializa o painel principal do gerenciador de pacotes pip."""
        t = tmgr.theme
        super().__init__(parent, bg=t.panel_bg)
        self._tmgr = tmgr
        self._cfg = cfg

        self._packages: list[dict] = []
        self._filtered: list[dict] = []
        self._outdated: list[dict] = []
        self._busy = False
        self._active_env: str | None = None
        self._install_var = tk.StringVar()

        self._build(t)
        self.after(200, self._refresh_installed)

    def _build(self, t: Any) -> None:
        hdr = tk.Frame(self, bg=t.activity_bg, height=52)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        tk.Label(
            hdr, text="📦", bg=t.activity_bg, fg=t.accent, font=(FONT_UI, 18)
        ).pack(side="left", padx=(12, 4), pady=8)
        tk.Label(
            hdr,
            text=_t("pip.title", "Gerenciador Pip"),
            bg=t.activity_bg,
            fg=t.fg,
            font=(FONT_UI, 13, "bold"),
        ).pack(side="left")

        self._status_lbl = tk.Label(
            hdr, text="", bg=t.activity_bg, fg=t.fg_muted, font=(FONT_UI, 8)
        )
        self._status_lbl.pack(side="right", padx=12)

        inst_bar = tk.Frame(
            self, bg=t.sidebar_bg, highlightthickness=1, highlightbackground=t.border
        )
        inst_bar.pack(fill="x", padx=0, pady=0)

        tk.Label(
            inst_bar,
            text=_t("pip.install_pkg", "Instalar:"),
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        ).pack(side="left", padx=(10, 4), pady=6)
        inst_ent = tk.Entry(
            inst_bar,
            textvariable=self._install_var,
            bg=t.editor_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            bd=4,
            font=(FONT_MONO, 9),
            width=28,
        )
        inst_ent.pack(side="left", pady=6)
        inst_ent.bind("<Return>", lambda _: self._quick_install())

        inst_btn = tk.Label(
            inst_bar,
            text=_t("pip.install_btn", "↓ Instalar"),
            bg=t.accent,
            fg="#FFF",
            font=(FONT_UI, 8, "bold"),
            padx=10,
            pady=4,
            cursor="hand2",
        )
        inst_btn.pack(side="left", padx=(4, 0), pady=6)
        inst_btn.bind("<Button-1>", lambda _: self._quick_install())

        env_lbl = tk.Label(
            inst_bar,
            text=_t("pip.env_label", "Env:"),
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        )
        env_lbl.pack(side="right", padx=(0, 4), pady=6)
        self._env_var = tk.StringVar(value="system")
        self._env_combo = ttk.Combobox(
            inst_bar,
            textvariable=self._env_var,
            state="readonly",
            width=14,
            font=(FONT_UI, 8),
        )
        self._env_combo.pack(side="right", padx=(0, 8), pady=6)
        self._env_combo.bind("<<ComboboxSelected>>", self._on_env_changed)
        self._refresh_env_list()

        tk.Frame(self, bg=t.border, height=1).pack(fill="x")

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)

        self._tab_installed = tk.Frame(nb, bg=t.panel_bg)
        self._tab_search = tk.Frame(nb, bg=t.panel_bg)
        self._tab_outdated = tk.Frame(nb, bg=t.panel_bg)
        self._tab_venv = tk.Frame(nb, bg=t.panel_bg)
        self._tab_log = tk.Frame(nb, bg=t.panel_bg)
        self._tab_codis = tk.Frame(nb, bg=t.panel_bg)

        nb.add(self._tab_installed, text=_t(
            "pip.tab_installed", "  Instalados  "))
        nb.add(self._tab_search, text=_t("pip.tab_search", "  🔍 PyPI  "))
        nb.add(self._tab_outdated, text=_t(
            "pip.tab_outdated", "  ⚠ Outdated  "))
        nb.add(self._tab_venv, text=_t("pip.tab_venv", "  🐍 Envs  "))
        nb.add(self._tab_codis, text="  🧩 Codis Deps  ")
        nb.add(self._tab_log, text=_t("pip.tab_log", "  Log  "))

        self._build_installed_tab(t)
        self._build_search_tab(t)
        self._build_outdated_tab(t)
        self._build_venv_tab(t)
        self._build_codis_deps_tab(t)
        self._build_log_tab(t)

    def _build_installed_tab(self, t: Any) -> None:
        p = self._tab_installed

        toolbar = tk.Frame(p, bg=t.panel_bg)
        toolbar.pack(fill="x", padx=8, pady=6)

        self._inst_search_var = tk.StringVar()
        self._inst_search_job: str | None = None
        srch = tk.Entry(
            toolbar,
            textvariable=self._inst_search_var,
            bg=t.editor_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            bd=4,
            font=(FONT_UI, 9),
            width=20,
            highlightthickness=1,
            highlightbackground=t.border,
        )
        srch.pack(side="left")
        srch.bind("<KeyRelease>", lambda _: self._schedule_filter_installed())

        tk.Label(
            toolbar, text="🔍", bg=t.panel_bg, fg=t.fg_muted, font=(FONT_UI, 9)
        ).pack(side="left", padx=(2, 8))

        def _tb(lbl: str, cmd: Any) -> tk.Label:
            return make_toolbar_button(
                toolbar,
                t,
                lbl,
                cmd,
                side="right",
                inner_padx=7,
                inner_pady=3,
                font_size=7,
            )

        _tb(_t("pip.export", "📤 Exportar req"), self._export_requirements)
        _tb(_t("pip.import", "📥 Importar req"), self._import_requirements)
        _tb(_t("pip.refresh", "↻ Atualizar"), self._refresh_installed)

        self._inst_stat = tk.Label(
            p, text="", bg=t.panel_bg, fg=t.fg_muted, font=(FONT_UI, 7), anchor="w"
        )
        self._inst_stat.pack(fill="x", padx=10, pady=(0, 2))

        cols = ("name", "version", "summary")
        self._inst_tree = ttk.Treeview(
            p, columns=cols, show="headings", selectmode="browse"
        )
        self._inst_tree.heading("name", text=_t("pip.col_name", "Pacote"))
        self._inst_tree.heading(
            "version", text=_t("pip.col_version", "Versão"))
        self._inst_tree.heading("summary", text=_t(
            "pip.col_summary", "Descrição"))
        self._inst_tree.column("name", width=160, minwidth=100)
        self._inst_tree.column("version", width=80, minwidth=60)
        self._inst_tree.column("summary", width=340, minwidth=100)
        self._inst_tree.tag_configure("even", background=t.editor_bg)
        self._inst_tree.tag_configure("odd", background=t.panel_bg)
        self._inst_tree.tag_configure("outdated", foreground="#f9e2af")

        vsb = ttk.Scrollbar(p, orient="vertical",
                            command=self._inst_tree.yview)
        hsb = ttk.Scrollbar(p, orient="horizontal",
                            command=self._inst_tree.xview)
        self._inst_tree.configure(
            yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.pack(side="right", fill="y")
        self._inst_tree.pack(fill="both", expand=True, padx=(8, 0))
        hsb.pack(fill="x", padx=8)

        action_row = tk.Frame(p, bg=t.panel_bg)
        action_row.pack(fill="x", padx=8, pady=(4, 6))

        def _ab(
            lbl: str, cmd: Any, bg: str | None = None, fg: str | None = None
        ) -> tk.Label:
            abg = bg or t.sidebar_bg
            afg = fg or t.fg
            b = tk.Label(
                action_row,
                text=lbl,
                bg=abg,
                fg=afg,
                font=(FONT_UI, 8, "bold"),
                padx=10,
                pady=5,
                cursor="hand2",
            )
            b.pack(side="left", padx=2)
            b.bind("<Button-1>", lambda _: cmd())
            b.bind("<Enter>", lambda _: b.config(bg=t.btn_hover))
            b.bind("<Leave>", lambda _: b.config(bg=abg))
            return b

        _ab(_t("pip.upgrade", "⬆ Atualizar"), self._upgrade_selected)
        _ab(_t("pip.show_info", "ℹ Info"), self._show_package_info)
        _ab(_t("pip.open_pypi", "🌐 PyPI"), self._open_pypi_page)
        _ab(
            _t("pip.uninstall", "🗑 Remover"),
            self._uninstall_selected,
            bg="#c0392b",
            fg="#FFF",
        )

        self._inst_tree.bind("<Double-1>", lambda _: self._show_package_info())

    def _build_search_tab(self, t: Any) -> None:
        p = self._tab_search

        progress_bar = tk.Frame(p, bg=t.panel_bg)
        progress_bar.pack(fill="x", padx=8, pady=8)

        self._pypi_var = tk.StringVar()
        srch_ent = tk.Entry(
            progress_bar,
            textvariable=self._pypi_var,
            bg=t.editor_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            bd=4,
            font=(FONT_UI, 10),
            width=28,
        )
        srch_ent.pack(side="left")
        srch_ent.bind("<Return>", lambda _: self._search_pypi())
        srch_ent.focus_set()

        srch_btn = tk.Label(
            progress_bar,
            text=_t("pip.search_btn", "🔍 Buscar"),
            bg=t.accent,
            fg="#FFF",
            font=(FONT_UI, 9, "bold"),
            padx=12,
            pady=5,
            cursor="hand2",
        )
        srch_btn.pack(side="left", padx=6)
        srch_btn.bind("<Button-1>", lambda _: self._search_pypi())

        self._pypi_status = tk.Label(
            progress_bar, text="", bg=t.panel_bg, fg=t.fg_muted, font=(FONT_UI, 8)
        )
        self._pypi_status.pack(side="left", padx=8)

        wrap = tk.Frame(p, bg=t.panel_bg)
        wrap.pack(fill="both", expand=True, padx=8)

        self._pypi_canvas = tk.Canvas(
            wrap, bg=t.panel_bg, highlightthickness=0)
        vsb = ttk.Scrollbar(wrap, orient="vertical",
                            command=self._pypi_canvas.yview)
        self._pypi_canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self._pypi_canvas.pack(fill="both", expand=True)

        self._pypi_inner = tk.Frame(self._pypi_canvas, bg=t.panel_bg)
        self._pypi_win = self._pypi_canvas.create_window(
            (0, 0), window=self._pypi_inner, anchor="nw"
        )

        debounce_canvas(
            self._pypi_canvas, self._pypi_win, ms=40, extra_widget=self._pypi_inner
        )

        self._pypi_hint = tk.Label(
            self._pypi_inner,
            text=_t(
                "pip.search_hint", "🔍  Digite o nome de um pacote para buscar no PyPI"
            ),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 10),
        )
        self._pypi_hint.pack(pady=40)

    def _build_outdated_tab(self, t: Any) -> None:
        p = self._tab_outdated

        progress_bar = tk.Frame(p, bg=t.panel_bg)
        progress_bar.pack(fill="x", padx=8, pady=6)

        tk.Label(
            progress_bar,
            text=_t("pip.outdated_hint", "Pacotes com atualização disponível:"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        ).pack(side="left")

        def _tb(lbl: str, cmd: Any, accent: bool = False) -> tk.Label:
            return make_toolbar_button(
                progress_bar, t, lbl, cmd, accent=accent, side="right", outer_pady=0
            )

        _tb(_t("pip.update_all", "⬆⬆ Atualizar todos"),
            self._upgrade_all, accent=True)
        _tb(_t("pip.check_outdated", "↻ Verificar"), self._check_outdated)

        cols = ("name", "current", "latest", "type")
        self._out_tree = ttk.Treeview(
            p, columns=cols, show="headings", selectmode="browse"
        )
        self._out_tree.heading("name", text=_t("pip.col_name", "Pacote"))
        self._out_tree.heading("current", text=_t("pip.col_current", "Atual"))
        self._out_tree.heading("latest", text=_t(
            "pip.col_latest", "Disponível"))
        self._out_tree.heading("type", text=_t("pip.col_type", "Tipo"))
        self._out_tree.column("name", width=200)
        self._out_tree.column("current", width=100)
        self._out_tree.column("latest", width=100)
        self._out_tree.column("type", width=80)
        self._out_tree.tag_configure("pkg", foreground="#f9e2af")

        vsb = ttk.Scrollbar(p, orient="vertical", command=self._out_tree.yview)
        vsb.pack(side="right", fill="y")
        self._out_tree.pack(fill="both", expand=True, padx=(8, 0))

        act = tk.Frame(p, bg=t.panel_bg)
        act.pack(fill="x", padx=8, pady=4)
        upd_sel = tk.Label(
            act,
            text=_t("pip.upgrade", "⬆ Atualizar selecionado"),
            bg=t.sidebar_bg,
            fg=t.fg,
            font=(FONT_UI, 8),
            padx=8,
            pady=4,
            cursor="hand2",
        )
        upd_sel.pack(side="left", padx=2)
        upd_sel.bind("<Button-1>", lambda _: self._upgrade_outdated_selected())

    def _build_venv_tab(self, t: Any) -> None:
        p = self._tab_venv

        hdr = tk.Frame(p, bg=t.panel_bg)
        hdr.pack(fill="x", padx=8, pady=6)
        tk.Label(
            hdr,
            text=_t("pip.venv_title", "Ambientes Virtuais"),
            bg=t.panel_bg,
            fg=t.fg,
            font=(FONT_UI, 10, "bold"),
        ).pack(side="left")

        def _tb(lbl: str, cmd: Any, accent: bool = False) -> tk.Label:
            return make_toolbar_button(
                hdr, t, lbl, cmd, accent=accent, side="right", outer_pady=0
            )

        _tb(_t("pip.venv_new", "＋ Criar venv"), self._create_venv, accent=True)
        _tb(_t("pip.venv_browse", "📂 Procurar"), self._browse_venv)
        _tb(_t("pip.venv_scan", "↻ Escanear"), self._scan_venvs)

        wrap = tk.Frame(p, bg=t.panel_bg)
        wrap.pack(fill="both", expand=True, padx=8)

        venv_canvas = tk.Canvas(wrap, bg=t.panel_bg, highlightthickness=0)
        vsb = ttk.Scrollbar(wrap, orient="vertical", command=venv_canvas.yview)
        venv_canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        venv_canvas.pack(fill="both", expand=True)

        self._venv_inner = tk.Frame(venv_canvas, bg=t.panel_bg)
        venv_win = venv_canvas.create_window(
            (0, 0), window=self._venv_inner, anchor="nw"
        )

        debounce_canvas(venv_canvas, venv_win, ms=40,
                        extra_widget=self._venv_inner)

        self._venvs: list[Path] = []
        self.after(300, self._scan_venvs)

    _CODIS_DEPS: list[dict] = [
        {
            "pkg": "send2trash",
            "min": "1.8.0",
            "group": "🗑  Lixeira",
            "desc": "Lixeira nativa do SO - sem ele, usa a lixeira do projeto (.codis_trash/)",
            "check": "send2trash",
        },
        {
            "pkg": "Pillow",
            "min": "10.0.0",
            "group": "🖼  Imagens",
            "desc": "Renderização de imagens: paint panel, K-UI background, file tree thumbnails",
            "check": "PIL",
        },
        {
            "pkg": "markdown",
            "min": "3.4.0",
            "group": "📄  Preview",
            "desc": "Preview de Markdown no painel de renderização",
            "check": "markdown",
        },
        {
            "pkg": "Pygments",
            "min": "2.17.0",
            "group": "📄  Preview",
            "desc": "Highlight de sintaxe para exportação HTML",
            "check": "pygments",
        },
        {
            "pkg": "py7zr",
            "min": "0.20.0",
            "group": "📦  Arquivos",
            "desc": "Suporte a arquivos .7z no visualizador de arquivos compactados",
            "check": "py7zr",
        },
        {
            "pkg": "rarfile",
            "min": "4.0",
            "group": "📦  Arquivos",
            "desc": "Suporte a arquivos .rar no visualizador de arquivos compactados",
            "check": "rarfile",
        },
        {
            "pkg": "websockets",
            "min": "12.0",
            "group": "🌐  Rede",
            "desc": "WebSocket para painel de colaboração em tempo real e chat",
            "check": "websockets",
        },
        {
            "pkg": "pywinpty",
            "min": "3.0.5",
            "group": "💻  Terminal",
            "desc": "Terminal com PTY real no Windows (ConPTY wrapper)",
            "check": "winpty",
            "platform": "win32",
        },
        {
            "pkg": "psutil",
            "min": "5.9.0",
            "group": "📊  Sistema",
            "desc": "CPU/RAM em tempo real no Process Manager e monitor de performance",
            "check": "psutil",
        },
        {
            "pkg": "charset-normalizer",
            "min": "3.0.0",
            "group": "🔤  Encoding",
            "desc": "Detecção automática de encoding de arquivos (preferência sobre chardet)",
            "check": "charset_normalizer",
        },
        {
            "pkg": "chardet",
            "min": "5.0.0",
            "group": "🔤  Encoding",
            "desc": "Fallback de detecção de encoding quando charset-normalizer não está instalado",
            "check": "chardet",
        },
        {
            "pkg": "tomli",
            "min": "2.0.0",
            "group": "⚙  Config",
            "desc": "Parser TOML para Python 3.10 e anterior (nativo na 3.11+)",
            "check": "tomli",
            "python_lt": (3, 11),
        },
        {
            "pkg": "python-mpv",
            "min": "1.0.5",
            "group": "🎵  Mídia",
            "desc": "Player via libmpv - requer libmpv instalado no sistema",
            "check": "mpv",
        },
        {
            "pkg": "python-vlc",
            "min": "3.0.20123",
            "group": "🎵  Mídia",
            "desc": "Player via libVLC - requer VLC instalado no sistema",
            "check": "vlc",
        },
    ]

    def _build_codis_deps_tab(self, t: Any) -> None:
        """Constrói a aba de Dependências Opcionais do Codis."""
        import importlib
        import sys as _sys

        p = self._tab_codis

        hdr = tk.Frame(p, bg=t.panel_bg)
        hdr.pack(fill="x", padx=12, pady=(10, 4))

        tk.Label(
            hdr,
            text="🧩  Dependências Opcionais do Codis",
            bg=t.panel_bg,
            fg=t.fg,
            font=(FONT_UI, 11, "bold"),
            anchor="w",
        ).pack(side="left")

        btn_frame = tk.Frame(hdr, bg=t.panel_bg)
        btn_frame.pack(side="right")

        def _install_all_missing() -> None:
            missing = [
                d["pkg"]
                for d in self._CODIS_DEPS
                if not self._dep_installed(d, importlib)
                and d.get("platform", _sys.platform) == _sys.platform
            ]
            if not missing:
                self._log("\u2714  Todas as dependencias ja estao instaladas!\n")
                return
            self._install_packages(missing)

        tk.Button(
            btn_frame,
            text="⬇  Instalar todas ausentes",
            bg=getattr(t, "accent", t.sel_bg),
            fg=getattr(t, "sel_fg", "#fff"),
            font=(FONT_UI, 9),
            relief="flat",
            cursor="hand2",
            command=_install_all_missing,
            padx=10,
        ).pack(side="left", padx=(0, 6))

        tk.Button(
            btn_frame,
            text="↻  Atualizar lista",
            bg=t.sidebar_bg,
            fg=t.fg,
            font=(FONT_UI, 9),
            relief="flat",
            cursor="hand2",
            command=lambda: self._refresh_codis_deps(p, t),
            padx=8,
        ).pack(side="left")

        tk.Frame(p, bg=t.border, height=1).pack(fill="x", padx=12, pady=(6, 0))

        tk.Label(
            p,
            text=(
                "  O Codis funciona sem todas estas dependencias.\n"
                "  Instale apenas as que precisar - cada uma habilita funcionalidades especificas."
            ),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            anchor="w",
            justify="left",
        ).pack(fill="x", padx=12, pady=(6, 4))

        tk.Frame(p, bg=t.border, height=1).pack(fill="x", padx=12, pady=(0, 4))

        from tkinter import ttk as _ttk

        outer = tk.Frame(p, bg=t.panel_bg)
        outer.pack(fill="both", expand=True)

        canvas = tk.Canvas(outer, bg=t.panel_bg, highlightthickness=0)
        vsb = _ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.config(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        inner = tk.Frame(canvas, bg=t.panel_bg)
        canvas_win = canvas.create_window((0, 0), window=inner, anchor="nw")

        debounce_canvas(canvas, canvas_win, ms=40, extra_widget=inner)
        canvas.bind(
            "<MouseWheel>",
            lambda e: canvas.yview_scroll(-1 * (e.delta // 120), "units"),
        )
        canvas.bind("<Button-4>", lambda _: canvas.yview_scroll(-3, "units"))
        canvas.bind("<Button-5>", lambda _: canvas.yview_scroll(3, "units"))

        self._dep_inner = inner
        self._dep_canvas = canvas
        self._dep_t = t
        self._dep_importlib = importlib

        self._refresh_codis_deps(p, t)

    def _dep_installed(self, dep: dict, importlib) -> bool:
        """Verifica se uma dependência está disponível no Python atual."""
        import sys as _sys

        plat = dep.get("platform")
        if plat and _sys.platform != plat:
            return True
        python_lt = dep.get("python_lt")
        if python_lt and _sys.version_info >= python_lt:
            return True
        try:
            importlib.import_module(dep["check"])
            return True
        except ImportError:
            return False

    def _refresh_codis_deps(self, _p, t) -> None:
        """Reconstrói os cards de dependências com status atual."""
        import sys as _sys

        importlib = self._dep_importlib
        inner = self._dep_inner

        for w in inner.winfo_children():
            w.destroy()

        installed_count = sum(
            1 for d in self._CODIS_DEPS if self._dep_installed(d, importlib)
        )
        total = len(self._CODIS_DEPS)

        bar_pct = installed_count / total if total else 0
        bar_filled = int(bar_pct * 30)
        bar_txt = "█" * bar_filled + "░" * (30 - bar_filled)
        tk.Label(
            inner,
            text=f"  {bar_txt}  {installed_count}/{total} instaladas",
            bg=t.panel_bg,
            fg=getattr(t, "diff_add_fg",
                       "#4caf50") if bar_pct == 1.0 else t.fg_muted,
            font=(FONT_UI, 8),
            anchor="w",
        ).pack(fill="x", padx=8, pady=(4, 8))

        current_group = None
        for dep in self._CODIS_DEPS:
            plat = dep.get("platform")
            if plat and _sys.platform != plat:
                continue

            if dep["group"] != current_group:
                current_group = dep["group"]
                tk.Label(
                    inner,
                    text=f"  {current_group}",
                    bg=t.panel_bg,
                    fg=t.fg_muted,
                    font=(FONT_UI, 8, "bold"),
                    anchor="w",
                ).pack(fill="x", padx=8, pady=(8, 2))
                tk.Frame(inner, bg=t.border, height=1).pack(
                    fill="x", padx=8, pady=(0, 4)
                )

            ok = self._dep_installed(dep, importlib)
            card_bg = t.panel_bg
            status_fg = (
                getattr(t, "diff_add_fg", "#4caf50")
                if ok
                else getattr(t, "warn_fg", "#f9e2af")
            )
            status_txt = "✔  Instalado" if ok else "✖  Ausente"

            card = tk.Frame(
                inner,
                bg=card_bg,
                highlightthickness=1,
                highlightbackground=t.border,
            )
            card.pack(fill="x", padx=8, pady=3)

            top = tk.Frame(card, bg=card_bg)
            top.pack(fill="x", padx=10, pady=(6, 2))

            tk.Label(
                top,
                text=dep["pkg"],
                bg=card_bg,
                fg=t.fg,
                font=(FONT_UI, 9, "bold"),
                anchor="w",
            ).pack(side="left")

            tk.Label(
                top,
                text=f"≥ {dep['min']}",
                bg=card_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 8),
                anchor="w",
            ).pack(side="left", padx=(8, 0))

            tk.Label(
                top,
                text=status_txt,
                bg=card_bg,
                fg=status_fg,
                font=(FONT_UI, 8),
                anchor="e",
            ).pack(side="right", padx=(8, 0))

            if not ok:
                pkg_name = dep["pkg"]
                btn = tk.Label(
                    top,
                    text=" ⬇ Instalar ",
                    bg=getattr(t, "accent", t.sel_bg),
                    fg=getattr(t, "sel_fg", "#fff"),
                    font=(FONT_UI, 8),
                    cursor="hand2",
                    padx=4,
                    pady=1,
                )
                btn.pack(side="right", padx=4)
                btn.bind(
                    "<Button-1>",
                    lambda _e, pkg=pkg_name: self._install_packages([pkg]),
                )

            tk.Label(
                card,
                text=dep["desc"],
                bg=card_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 8),
                anchor="w",
                justify="left",
                wraplength=540,
            ).pack(fill="x", padx=10, pady=(0, 6))

        self._dep_canvas.update_idletasks()
        self._dep_canvas.config(scrollregion=self._dep_canvas.bbox("all"))

    def _install_packages(self, pkgs: list[str]) -> None:
        """Instala uma lista de pacotes e exibe log em tempo real."""
        if self._busy:
            return
        self._busy = True
        q: queue.Queue[str | None] = queue.Queue()
        args = ["install"] + pkgs

        self._log(f"▶  Instalando: {', '.join(pkgs)}\n")
        self._set_status(f"Instalando {len(pkgs)} pacote(s)...")

        def _work() -> None:
            _run_pip(args, q, self._active_env)

        def _done() -> None:
            self._refresh_codis_deps(None, self._dep_t)
            if hasattr(self, "_dep_importlib"):
                import importlib

                self._dep_importlib = importlib

        threading.Thread(target=_work, daemon=True).start()
        self._drain_queue(q, on_done=_done)

    def _build_log_tab(self, t: Any) -> None:
        p = self._tab_log

        btn_row = tk.Frame(p, bg=t.panel_bg)
        btn_row.pack(fill="x", padx=8, pady=4)
        clear_b = tk.Label(
            btn_row,
            text=_t("pip.clear_log", "🗑 Limpar log"),
            bg=t.sidebar_bg,
            fg=t.fg,
            font=(FONT_UI, 8),
            padx=8,
            pady=4,
            cursor="hand2",
        )
        clear_b.pack(side="right")
        clear_b.bind("<Button-1>", lambda _: self._clear_log())

        self._log_text = tk.Text(
            p,
            bg=t.editor_bg,
            fg=t.fg,
            font=(FONT_MONO, 9),
            wrap="word",
            insertbackground=t.cursor,
            relief="flat",
            state="disabled",
            selectbackground=t.sel_bg,
        )
        vsb = ttk.Scrollbar(p, orient="vertical", command=self._log_text.yview)
        vsb.pack(side="right", fill="y")
        self._log_text.config(yscrollcommand=vsb.set)
        self._log_text.pack(fill="both", expand=True, padx=8)

        self._log_text.tag_configure("ok", foreground="#a6e3a1")
        self._log_text.tag_configure("warn", foreground="#f9e2af")
        self._log_text.tag_configure("err", foreground="#f38ba8")
        self._log_text.tag_configure("info", foreground="#89b4fa")
        self._log_text.tag_configure("dim", foreground="#6c7086")

    def _set_status(self, msg: str) -> None:
        self._status_lbl.config(text=msg)

    def _log(self, line: str) -> None:
        self._log_text.config(state="normal")
        line_stripped = line.rstrip()
        if not line_stripped:
            self._log_text.config(state="disabled")
            return
        tag = "info"
        ll = line_stripped.lower()
        if "error" in ll or "failed" in ll or "exception" in ll:
            tag = "err"
        elif "warning" in ll or "warn" in ll:
            tag = "warn"
        elif "successfully" in ll or "installed" in ll or "upgraded" in ll:
            tag = "ok"
        elif line_stripped.startswith("  ") or line_stripped.startswith("-"):
            tag = "dim"
        self._log_text.insert("end", line_stripped + "\n", tag)
        self._log_text.see("end")
        self._log_text.config(state="disabled")

    def _clear_log(self) -> None:
        self._log_text.config(state="normal")
        self._log_text.delete("1.0", "end")
        self._log_text.config(state="disabled")

    def _drain_queue(self, q: queue.Queue[str | None], on_done: Any = None) -> None:
        try:
            while True:
                item = q.get_nowait()
                if item is None:
                    self._busy = False
                    self._set_status("")
                    if on_done:
                        on_done()
                    return
                self._log(item)
        except queue.Empty:
            pass
        self.after(80, lambda: self._drain_queue(q, on_done))

    def _refresh_installed(self) -> None:
        if self._busy:
            return
        self._busy = True
        self._set_status(_t("pip.loading", "Carregando..."))
        outdated_names = {p["name"].lower() for p in self._outdated}

        def _work() -> None:
            try:
                pkgs = _list_installed(self._active_env)
            except NoPythonError as exc:
                msg = str(exc)
                self.after(0, lambda m=msg: self._python_missing(m))
                return
            self.after(0, lambda: self._populate_installed(
                pkgs, outdated_names))

        threading.Thread(target=_work, daemon=True).start()

    def _python_missing(self, message: str) -> None:
        """Exibe, uma vez, que não há Python para o pip (Codis compilado)."""
        self._busy = False
        self._set_status(message)
        self._log(f"✗ {message}\n")
        if not getattr(self, "_python_missing_shown", False):
            self._python_missing_shown = True
            messagebox.showwarning(
                _t("pip.no_python_title", "Python não encontrado"), message, parent=self
            )

    def _populate_installed(self, pkgs: list[dict], outdated_names: set[str]) -> None:
        self._packages = pkgs
        self._filter_installed(outdated_names)
        self._busy = False
        self._set_status("")
        n = len(pkgs)
        self._inst_stat.config(
            text=_t("pip.pkg_count", f"{n} pacotes instalados"))

    def _schedule_filter_installed(self) -> None:
        """Debounce de 80ms na busca de pacotes instalados."""
        if self._inst_search_job:
            with contextlib.suppress(Exception):
                self.after_cancel(self._inst_search_job)
        self._inst_search_job = self.after(80, self._filter_installed)

    def _filter_installed(self, outdated_names: set[str] | None = None) -> None:
        q = self._inst_search_var.get().lower()
        if outdated_names is None:
            outdated_names = {p["name"].lower() for p in self._outdated}
        self._filtered = [p for p in self._packages if q in p["name"].lower()]
        self._inst_tree.delete(*self._inst_tree.get_children())
        for i, p in enumerate(self._filtered):
            tag = (
                "outdated"
                if p["name"].lower() in outdated_names
                else ("even" if i % 2 == 0 else "odd")
            )
            self._inst_tree.insert(
                "", "end", values=(p["name"], p["version"], ""), tags=(tag,)
            )

    def _selected_package(self) -> str | None:
        sel = self._inst_tree.selection()
        if not sel:
            return None
        return self._inst_tree.item(sel[0])["values"][0]

    def _quick_install(self) -> None:
        pkg = self._install_var.get().strip()
        if not pkg or self._busy:
            return
        self._install_var.set("")
        self._pip_op(["install", pkg], on_done=self._refresh_installed)

    def _upgrade_selected(self) -> None:
        pkg = self._selected_package()
        if not pkg:
            return
        self._pip_op(["install", "--upgrade", pkg],
                     on_done=self._refresh_installed)

    def _uninstall_selected(self) -> None:
        pkg = self._selected_package()
        if not pkg:
            return
        if not messagebox.askyesno(
            _t("pip.uninstall_title", "Desinstalar"),
            _t("pip.uninstall_confirm", f"Desinstalar '{pkg}'?"),
        ):
            return
        self._pip_op(["uninstall", "-y", pkg], on_done=self._refresh_installed)

    def _show_package_info(self) -> None:
        pkg = self._selected_package()
        if not pkg:
            return
        try:
            info = _show_info(pkg, self._active_env)
        except NoPythonError as exc:
            self._python_missing(str(exc))
            return
        if not info:
            return
        _InfoWindow(self, self._tmgr, pkg, info)

    def _open_pypi_page(self) -> None:
        pkg = self._selected_package()
        if not pkg:
            return
        import webbrowser

        webbrowser.open(f"https://pypi.org/project/{pkg}/")

    def _export_requirements(self) -> None:
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text", "*.txt"), ("All", "*.*")],
            initialfile="requirements.txt",
        )
        if not path:
            return
        lines = [f"{p['name']}=={p['version']}" for p in self._packages]
        Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
        self._log(f"Exportado: {path}\n")

    def _import_requirements(self) -> None:
        path = filedialog.askopenfilename(
            filetypes=[("Text", "*.txt"), ("All", "*.*")])
        if not path:
            return
        self._pip_op(["install", "-r", path], on_done=self._refresh_installed)

    def _check_outdated(self) -> None:
        if self._busy:
            return
        self._busy = True
        self._set_status(
            _t("pip.checking_updates", "Verificando atualizações..."))

        def _work() -> None:
            try:
                data = _list_outdated(self._active_env)
            except NoPythonError as exc:
                msg = str(exc)
                self.after(0, lambda m=msg: self._python_missing(m))
                return
            self.after(0, lambda: self._populate_outdated(data))

        threading.Thread(target=_work, daemon=True).start()

    def _populate_outdated(self, data: list[dict]) -> None:
        self._outdated = data
        self._busy = False
        self._set_status(
            _t("pip.outdated_found", f"{len(data)} pacote(s) desatualizado(s)")
        )
        self._out_tree.delete(*self._out_tree.get_children())
        for p in data:
            self._out_tree.insert(
                "",
                "end",
                values=(
                    p["name"],
                    p["version"],
                    p["latest_version"],
                    p.get("latest_filetype", ""),
                ),
                tags=("pkg",),
            )
        self._filter_installed()

    def _upgrade_outdated_selected(self) -> None:
        sel = self._out_tree.selection()
        if not sel:
            return
        pkg = self._out_tree.item(sel[0])["values"][0]
        self._pip_op(["install", "--upgrade", pkg],
                     on_done=self._check_outdated)

    def _upgrade_all(self) -> None:
        if not self._outdated:
            return
        names = [p["name"] for p in self._outdated]
        if not messagebox.askyesno(
            _t("pip.update_all_title", "Atualizar todos"),
            _t("pip.update_all_confirm", f"Atualizar {len(names)} pacotes?"),
        ):
            return
        self._pip_op(
            ["install", "--upgrade"] + names,
            on_done=lambda: (self._check_outdated(),
                             self._refresh_installed()),
        )

    def _pip_op(self, args: list[str], on_done: Any = None) -> None:
        if self._busy:
            return
        self._busy = True
        q: queue.Queue[str | None] = queue.Queue()
        op_name = " ".join(args[:2])
        self._set_status(f"{op_name}...")
        self._log(f"\n▶ pip {' '.join(args)}\n")
        threading.Thread(
            target=_run_pip, args=(args, q, self._active_env), daemon=True
        ).start()
        self._drain_queue(q, on_done=on_done)

    def _search_pypi(self) -> None:
        query = self._pypi_var.get().strip()
        if not query:
            return
        self._pypi_status.config(text=_t("pip.searching", "Buscando..."))
        for w in self._pypi_inner.winfo_children():
            w.destroy()

        threading.Thread(
            target=self._do_pypi_search, args=(query,), daemon=True
        ).start()

    def _do_pypi_search(self, query: str) -> None:
        exact = _fetch_pypi(query)
        results = [self._pypi_to_card(exact)] if exact else []
        self.after(0, lambda: self._render_pypi_cards(results))

    def _pypi_to_card(self, data: dict) -> dict:
        info = data.get("info", {})
        return {
            "name": info.get("name", ""),
            "version": info.get("version", ""),
            "summary": info.get("summary", ""),
            "author": info.get("author", ""),
            "license": info.get("license", ""),
            "home": info.get("home_page", "") or info.get("project_url", ""),
        }

    def _render_pypi_cards(self, results: list[dict]) -> None:
        t = self._tmgr.theme
        for w in self._pypi_inner.winfo_children():
            w.destroy()

        if not results:
            tk.Label(
                self._pypi_inner,
                text=_t("pip.no_results", "Nenhum resultado encontrado."),
                bg=t.panel_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 10),
            ).pack(pady=20)
            self._pypi_status.config(text="")
            return

        self._pypi_status.config(
            text=_t("pip.search_results", f"{len(results)} resultado(s)")
        )
        installed_names = {p["name"].lower() for p in self._packages}

        for r in results:
            self._build_pypi_card(t, r, installed_names)

    def _build_pypi_card(self, t: Any, r: dict, installed: set[str]) -> None:
        name = r.get("name", "")
        version = r.get("version", "")
        summary = r.get("summary", "") or ""
        author = r.get("author", "") or ""
        license_ = r.get("license", "") or ""
        is_inst = name.lower() in installed

        card = tk.Frame(
            self._pypi_inner,
            bg=t.sidebar_bg,
            highlightthickness=1,
            highlightbackground=t.border,
        )
        card.pack(fill="x", pady=3, padx=2)

        top = tk.Frame(card, bg=t.sidebar_bg)
        top.pack(fill="x", padx=10, pady=(8, 2))

        tk.Label(
            top, text=name, bg=t.sidebar_bg, fg=t.accent, font=(
                FONT_UI, 10, "bold")
        ).pack(side="left")
        if version:
            tk.Label(
                top,
                text=f"  v{version}",
                bg=t.sidebar_bg,
                fg=t.fg_muted,
                font=(FONT_MONO, 8),
            ).pack(side="left")

        if is_inst:
            tk.Label(
                top,
                text=_t("pip.installed_badge", "instalado"),
                bg="#1e6b2e",
                fg="#a6e3a1",
                font=(FONT_UI, 7),
                padx=5,
                pady=1,
            ).pack(side="left", padx=6)

        btn_text = (
            _t("pip.upgrade", "⬆ Atualizar")
            if is_inst
            else _t("pip.install_btn", "↓ Instalar")
        )
        inst_b = tk.Label(
            top,
            text=btn_text,
            bg=t.accent,
            fg="#FFF",
            font=(FONT_UI, 7, "bold"),
            padx=6,
            pady=2,
            cursor="hand2",
        )
        inst_b.pack(side="right")
        inst_b.bind(
            "<Button-1>",
            lambda _, n=name: self._pip_op(
                ["install", n], on_done=self._refresh_installed
            ),
        )

        if summary:
            tk.Label(
                card,
                text=summary[:120],
                bg=t.sidebar_bg,
                fg=t.fg,
                font=(FONT_UI, 8),
                anchor="w",
                wraplength=480,
            ).pack(fill="x", padx=10, pady=(0, 4))

        meta = tk.Frame(card, bg=t.sidebar_bg)
        meta.pack(fill="x", padx=10, pady=(0, 8))
        if author:
            tk.Label(
                meta,
                text=f"👤 {author}",
                bg=t.sidebar_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 7),
            ).pack(side="left", padx=(0, 8))
        if license_:
            tk.Label(
                meta,
                text=f"⚖ {license_[:30]}",
                bg=t.sidebar_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 7),
            ).pack(side="left")
        pypi_lnk = tk.Label(
            meta,
            text=_t("pip.pypi_link", "🌐 PyPI"),
            bg=t.sidebar_bg,
            fg=t.t_link,
            font=(FONT_UI, 7),
            cursor="hand2",
        )
        pypi_lnk.pack(side="right")
        pypi_lnk.bind(
            "<Button-1>",
            lambda _, n=name: __import__("webbrowser").open(
                f"https://pypi.org/project/{n}/"
            ),
        )

    def _scan_venvs(self) -> None:
        def _work() -> None:
            found = _find_venvs()
            self.after(0, lambda: self._render_venvs(found))

        threading.Thread(target=_work, daemon=True).start()

    def _render_venvs(self, venvs: list[Path]) -> None:
        t = self._tmgr.theme
        self._venvs = venvs
        for w in self._venv_inner.winfo_children():
            w.destroy()

        if not venvs:
            tk.Label(
                self._venv_inner,
                text=_t(
                    "pip.no_venvs",
                    "Nenhum ambiente virtual encontrado.\nCrie um com  ＋ Criar venv.",
                ),
                bg=t.panel_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 9),
                justify="center",
            ).pack(pady=24)
            return

        for venv_path in venvs:
            self._build_venv_card(t, venv_path)

    def _build_venv_card(self, t: Any, venv_path: Path) -> None:
        py_bin = venv_path / "bin" / "python"
        if not py_bin.exists():
            py_bin = venv_path / "Scripts" / "python.exe"

        is_active = (
            self._active_env is not None
            and Path(self._active_env).resolve() == py_bin.resolve()
        )

        card_bg = t.editor_bg if is_active else t.sidebar_bg
        border = t.accent if is_active else t.border
        card = tk.Frame(
            self._venv_inner,
            bg=card_bg,
            highlightthickness=1,
            highlightbackground=border,
        )
        card.pack(fill="x", pady=3, padx=2)

        top = tk.Frame(card, bg=card_bg)
        top.pack(fill="x", padx=10, pady=(8, 2))

        icon = "🟢" if is_active else "🐍"
        tk.Label(top, text=icon, bg=card_bg, font=(
            FONT_UI, 12)).pack(side="left")
        tk.Label(
            top,
            text=f"  {venv_path.name}",
            bg=card_bg,
            fg=t.fg,
            font=(FONT_UI, 9, "bold"),
        ).pack(side="left")

        if is_active:
            tk.Label(
                top,
                text=_t("pip.active_env", "ativo"),
                bg=t.accent,
                fg="#FFF",
                font=(FONT_UI, 7),
                padx=5,
                pady=1,
            ).pack(side="left", padx=6)

        act_btn = tk.Label(
            top,
            text=(
                _t("pip.env_deactivate", "⏻ Desativar")
                if is_active
                else _t("pip.env_activate", "▶ Usar")
            ),
            bg=t.accent if not is_active else t.sidebar_bg,
            fg="#FFF" if not is_active else t.fg,
            font=(FONT_UI, 7),
            padx=6,
            pady=2,
            cursor="hand2",
        )
        act_btn.pack(side="right")
        act_btn.bind(
            "<Button-1>", lambda _, p=py_bin, a=is_active: self._toggle_venv(
                p, a)
        )

        tk.Label(
            card,
            text=str(venv_path),
            bg=card_bg,
            fg=t.fg_muted,
            font=(FONT_MONO, 7),
            anchor="w",
        ).pack(fill="x", padx=10, pady=(0, 8))

    def _toggle_venv(self, py_bin: Path, currently_active: bool) -> None:
        if currently_active:
            self._active_env = None
            self._env_var.set("system")
        else:
            self._active_env = str(py_bin)
            self._env_var.set(py_bin.parent.parent.name)
        self._refresh_env_list()
        self._scan_venvs()
        self._refresh_installed()

    def _refresh_env_list(self) -> None:
        venvs = _find_venvs()
        values = ["system"] + [v.name for v in venvs]
        self._env_combo["values"] = values
        if not self._active_env:
            self._env_var.set("system")

    def _on_env_changed(self, _e: Any = None) -> None:
        sel = self._env_var.get()
        if sel == "system":
            self._active_env = None
        else:
            venvs = _find_venvs()
            for v in venvs:
                if v.name == sel:
                    py = v / "bin" / "python"
                    if not py.exists():
                        py = v / "Scripts" / "python.exe"
                    self._active_env = str(py) if py.exists() else None
                    break
        self._refresh_installed()

    def _create_venv(self) -> None:
        path = filedialog.askdirectory(
            title=_t("pip.venv_choose_dir", "Escolha onde criar o venv")
        )
        if not path:
            return
        name = _VenvNameDialog(self, self._tmgr).result
        if not name:
            return
        dest = Path(path) / name
        try:
            cmd = python_cmd("-m", "venv", str(dest))
        except NoPythonError as exc:
            self._python_missing(str(exc))
            return
        self._pip_op_raw(
            cmd,
            msg=f"Criando {dest}...",
            on_done=self._scan_venvs,
        )

    def _browse_venv(self) -> None:
        path = filedialog.askdirectory(
            title=_t("pip.venv_browse_dir", "Selecione pasta do venv")
        )
        if path and (Path(path) / "pyvenv.cfg").exists():
            venv_path = Path(path)
            if venv_path not in self._venvs:
                self._venvs.append(venv_path)
                self._render_venvs(self._venvs)

    def _pip_op_raw(self, cmd: list[str], msg: str = "", on_done: Any = None) -> None:
        """Executa qualquer comando de processo (não necessariamente pip)."""
        if self._busy:
            return
        self._busy = True
        q: queue.Queue[str | None] = queue.Queue()
        self._set_status(msg)
        self._log(f"\n▶ {' '.join(cmd)}\n")

        def _run() -> None:
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    **_make_popen_kwargs(),
                )
                for line in proc.stdout or []:
                    q.put(line)
                proc.wait()
            except Exception as exc:
                q.put(f"[Erro] {exc}\n")
            q.put(None)

        threading.Thread(target=_run, daemon=True).start()
        self._drain_queue(q, on_done=on_done)


class _InfoWindow(tk.Toplevel):
    def __init__(self, parent: tk.Widget, tmgr: Any, name: str, info: str) -> None:
        """Inicializa a janela de informações detalhadas de um pacote pip."""
        super().__init__(parent)
        t = tmgr.theme
        self.title(f"📦 {name}")
        self.geometry("540x400")
        self.config(bg=t.panel_bg)

        hdr = tk.Frame(self, bg=t.activity_bg)
        hdr.pack(fill="x")
        tk.Label(
            hdr,
            text=f"📦  {name}",
            bg=t.activity_bg,
            fg=t.accent,
            font=(FONT_UI, 12, "bold"),
        ).pack(side="left", padx=12, pady=8)

        txt = tk.Text(
            self,
            bg=t.editor_bg,
            fg=t.fg,
            font=(FONT_MONO, 9),
            relief="flat",
            bd=8,
            wrap="word",
            state="normal",
        )
        vsb = ttk.Scrollbar(self, orient="vertical", command=txt.yview)
        vsb.pack(side="right", fill="y")
        txt.config(yscrollcommand=vsb.set)
        txt.pack(fill="both", expand=True)
        txt.insert("end", info)
        txt.config(state="disabled")

        btn_row = tk.Frame(self, bg=t.panel_bg)
        btn_row.pack(fill="x", padx=8, pady=6)
        pypi_b = tk.Label(
            btn_row,
            text=_t("pip.view_pypi", "🌐 Ver no PyPI"),
            bg=t.accent,
            fg="#FFF",
            font=(FONT_UI, 9),
            padx=10,
            pady=4,
            cursor="hand2",
        )
        pypi_b.pack(side="left")
        pypi_b.bind(
            "<Button-1>",
            lambda _: __import__("webbrowser").open(
                f"https://pypi.org/project/{name}/"
            ),
        )


class _VenvNameDialog(tk.Toplevel):
    def __init__(self, parent: tk.Widget, tmgr: Any) -> None:
        """Inicializa o diálogo de nome para novo ambiente virtual."""
        super().__init__(parent)
        t = tmgr.theme
        self.title("Nome do ambiente virtual")
        self.config(bg=t.panel_bg)
        self.resizable(False, False)
        safe_grab_set(self)
        self.result: str | None = None

        tk.Label(
            self,
            text=_t("pip.field_name", "Nome do pacote:"),
            bg=t.panel_bg,
            fg=t.fg,
            font=(FONT_UI, 10),
        ).pack(padx=16, pady=(12, 4))
        self._var = tk.StringVar(value=".venv")
        ent = tk.Entry(
            self,
            textvariable=self._var,
            bg=t.editor_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            bd=4,
            font=(FONT_UI, 10),
            width=20,
        )
        ent.pack(padx=16, pady=4)
        ent.bind("<Return>", lambda _: self._ok())

        btn_row = tk.Frame(self, bg=t.panel_bg)
        btn_row.pack(padx=16, pady=10)
        ok_b = tk.Label(
            btn_row,
            text=_t("pip.create_venv", "Criar"),
            bg=t.accent,
            fg="#FFF",
            font=(FONT_UI, 9),
            padx=10,
            pady=4,
            cursor="hand2",
        )
        ok_b.pack(side="left", padx=4)
        ok_b.bind("<Button-1>", lambda _: self._ok())
        cancel_b = tk.Label(
            btn_row,
            text=_t("pip.btn_cancel", "Cancelar"),
            bg=t.sidebar_bg,
            fg=t.fg,
            font=(FONT_UI, 9),
            padx=10,
            pady=4,
            cursor="hand2",
        )
        cancel_b.pack(side="left", padx=4)
        cancel_b.bind("<Button-1>", lambda _: self.destroy())

        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        x = (self.winfo_screenwidth() - w) // 2
        y = (self.winfo_screenheight() - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.wait_window()

    def _ok(self) -> None:
        self.result = self._var.get().strip() or ".venv"
        self.destroy()
