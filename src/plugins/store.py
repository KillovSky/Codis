"""Codis - Plugin Store (src/plugins/store.py).

Sistema de busca e instalação de plugins a partir de um repositório GitHub.

Estrutura esperada do repositório de plugins (KillovSky/Plugins):
-----------------------------------------------------------------------------
  Plugins/
  +-- Codis/                           <- pasta do projeto (PROJECT_PREFIX)
  |   +-- index.json                   <- indice gerado pelo workflow de CI
  |   +-- KillovSky-word-counter-1.0.0/
  |   |   +-- plugin.py                <- entry-point obrigatorio
  |   |   +-- manifest.json            <- metadados do plugin
  |   |   `-- README.md                <- documentacao
  |   `-- autor-nome-versao.py         <- plugin arquivo unico
  `-- Iris/                            <- outro projeto (ignorado pelo Codis)
      `-- index.json

Convenção anti-colisão de nomes:
  <autor>-<nome>-<versao>[/]  ->  id = "<autor>-<nome>"
  Exemplo: KillovSky-word-counter-1.0.0/ -> id="KillovSky-word-counter" v="1.0.0"

INDEX_SCHEMA  (index.json):
-----------------------------------------------------------------------------
  {
    "version":   2,
    "project":   "Codis",
    "generated": "2025-01-01T00:00:00Z",
    "plugins": [
      {
        "id":           "KillovSky-word-counter",  // obrigatório
        "name":         "Word Counter",             // obrigatório
        "description":  "Descrição curta.",
        "version":      "1.0.0",
        "author":       "KillovSky",
        "category":     "editor",
        "tags":         ["words", "counter"],
        "codis_compat": ">=1.0.0",
        "path":         "KillovSky-word-counter-1.0.0",  // relativo a Codis/
        "entry":        "plugin.py",
        "stars":        0,
        "updated":      "2025-01-01",
        "homepage":     "https://github.com/KillovSky/Plugins",
        "readme_url":   "",      // URL raw do README.md (preenchido pelo workflow)
        "download_url": ""       // vazio = usa GitHub API (auto-detectado)
      }
    ]
  }

Categorias válidas: editor, git, ui, language, ai, tools, themes, other
"""

from __future__ import annotations

import contextlib
import io
import json
import logging
import tempfile
import threading
import time
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any

from src.constants import FONT_MONO, FONT_UI
from src.i18n import _t

logger = logging.getLogger("Codis.PluginStore")

DEFAULT_REPO = "KillovSky/Plugins"
INDEX_FILE = "index.json"
PROJECT_PREFIX = "Codis"
PAGES_BASE = "https://{owner}.github.io/{reponame}"
RAW_BASE = "https://raw.githubusercontent.com/{repo}/{branch}"
API_BASE = "https://api.github.com/repos/{repo}"
_REQUEST_TIMEOUT = 12
_MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024


def _require_web_url(url: str, allow_http: bool = False) -> None:
    """Recusa esquemas que não sejam HTTPS (ou HTTP, se permitido) antes de abrir a URL.

    Os índices de plugins são dados de terceiros; sem esta checagem uma
    entrada com ``file://`` faria ``urlopen`` ler arquivos locais.
    """
    scheme = urllib.parse.urlparse(url).scheme.lower()
    allowed = ("https", "http") if allow_http else ("https",)
    if scheme not in allowed:
        raise ValueError(f"Esquema de URL não permitido: {scheme!r}")


def _read_limited(resp: Any, limit: int = _MAX_DOWNLOAD_BYTES) -> bytes:
    """Lê a resposta até ``limit`` bytes, falhando se o conteúdo for maior."""
    data = resp.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Download excede o tamanho máximo permitido.")
    return data

CATEGORIES = [
    ("all", "plugin.store_cat_all"),
    ("editor", "plugin.store_cat_editor"),
    ("git", "plugin.store_cat_git"),
    ("ui", "plugin.store_cat_ui"),
    ("language", "plugin.store_cat_language"),
    ("ai", "plugin.store_cat_ai"),
    ("tools", "plugin.store_cat_tools"),
    ("themes", "plugin.store_cat_themes"),
    ("other", "plugin.store_cat_other"),
]


@dataclass
class PluginEntry:
    """Metadados de um plugin do repositório."""

    id: str
    name: str
    description: str
    version: str = "0.0.0"
    author: str = ""
    category: str = "other"
    tags: list[str] = field(default_factory=list)
    codis_compat: str = ">=1.0.0"
    path: str = ""
    entry: str = "plugin.py"
    stars: int = 0
    updated: str = ""
    homepage: str = ""
    download_url: str = ""
    readme_url: str = ""
    license: str = ""
    permissions: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PluginEntry:
        """Constrói um PluginEntry a partir de um dict do index.json."""
        return cls(
            id=data.get("id", ""),
            name=data.get("name", data.get("id", "?")),
            description=data.get("description", ""),
            version=data.get("version", "0.0.0"),
            author=data.get("author", ""),
            category=data.get("category", "other"),
            tags=data.get("tags", []),
            codis_compat=data.get("codis_compat", ">=1.0.0"),
            path=data.get("path", ""),
            entry=data.get("entry", "plugin.py"),
            stars=int(data.get("stars", 0)),
            updated=data.get("updated", ""),
            homepage=data.get("homepage", ""),
            download_url=data.get("download_url", ""),
            readme_url=data.get("readme_url", ""),
            license=data.get("license", ""),
            permissions=data.get("permissions", []),
        )


class PluginStoreClient:
    """
    Cliente de busca e download de plugins a partir de um repositório GitHub.

    Responsabilidades:
        - Baixar e cachear o index.json do repositório
        - Filtrar, ordenar e buscar plugins
        - Baixar um plugin específico como ZIP
        - Entregar o ZIP ao PluginLoader para instalação
    """

    def __init__(
        self,
        repo: str = DEFAULT_REPO,
        branch: str = "main",
        cfg: Any = None,
    ) -> None:
        """Inicializa o cliente com o repositório e branch alvo.

        Args:
            repo:   Repositório no formato `owner/repo`.
            branch: Branch do qual ler os arquivos. Padrão: main.
            cfg:    ConfigManager para persistir repositório customizado.
        """
        self._repo = repo
        self._branch = branch
        self._cfg = cfg
        self._cache: list[PluginEntry] = []
        self._last_fetch: float = 0
        self._cache_ttl: float = 300.0
        self._custom_url: str = ""
        self._local_path: str = ""

        if cfg:
            saved_repo = cfg.get("plugin_store_repo", "")
            saved_branch = cfg.get("plugin_store_branch", "main")
            if saved_repo:
                self._repo = saved_repo
            if saved_branch:
                self._branch = saved_branch

    def _pages_url(self) -> str:
        """URL do index.json no GitHub Pages para o projeto Codis.

        Estrutura: https://<owner>.github.io/<repo>/Codis/index.json
        """
        owner, reponame = (self._repo + "/x").split("/")[:2]
        base = PAGES_BASE.format(owner=owner.lower(), reponame=reponame)
        return f"{base}/{PROJECT_PREFIX}/{INDEX_FILE}"

    def _raw_url(self) -> str:
        """URL de fallback via raw.githubusercontent.

        Estrutura: https://raw.githubusercontent.com/<repo>/<branch>/Codis/index.json
        """
        return f"{RAW_BASE.format(repo=self._repo, branch=self._branch)}/{PROJECT_PREFIX}/{INDEX_FILE}"

    def search(
        self,
        query: str = "",
        category: str = "all",
        sort: str = "name",
        filter_: str = "all",
        installed_ids: set[str] | None = None,
    ) -> list[PluginEntry]:
        """Filtra e ordena a lista de plugins em cache."""
        plugins = list(self._cache)

        if category and category != "all":
            plugins = [p for p in plugins if p.category == category]

        if filter_ == "installed" and installed_ids is not None:
            plugins = [p for p in plugins if p.id in installed_ids]
        elif filter_ == "available" and installed_ids is not None:
            plugins = [p for p in plugins if p.id not in installed_ids]

        if query:
            q = query.lower()
            plugins = [
                p
                for p in plugins
                if q in p.name.lower()
                or q in p.description.lower()
                or q in p.author.lower()
                or any(q in t.lower() for t in p.tags)
            ]

        key_fn: dict[str, Any] = {
            "name": lambda p: p.name.lower(),
            "stars": lambda p: -p.stars,
            "updated": lambda p: p.updated or "",
        }
        plugins.sort(key=key_fn.get(sort, lambda p: p.name.lower()))
        return plugins

    def fetch_readme(self, entry: PluginEntry) -> str:
        """Baixa o README.md do plugin via readme_url (Pages) ou raw."""
        if entry.readme_url:
            try:
                _require_web_url(entry.readme_url)
                req = urllib.request.Request(
                    entry.readme_url,
                    headers={"User-Agent": "Codis-PluginStore/1.0"},
                )
                with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
                    return _read_limited(resp, 2_000_000).decode("utf-8", errors="replace")
            except Exception:
                logger.debug("Suppressed error", exc_info=True)
        if not entry.path:
            return ""
        base = f"{RAW_BASE.format(repo=self._repo, branch=self._branch)}/{PROJECT_PREFIX}/{entry.path}"
        for fname in ("README.md", "readme.md", "README.txt"):
            try:
                url = f"{base}/{fname}"
                req = urllib.request.Request(
                    url, headers={"User-Agent": "Codis-PluginStore/1.0"}
                )
                with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
                    return _read_limited(resp, 2_000_000).decode("utf-8", errors="replace")
            except Exception:
                continue
        return ""

    def download_plugin(self, entry: PluginEntry) -> bytes:
        """Baixa o plugin como bytes (ZIP).

        Estratégia:
          1. Usa download_url do manifesto se disponível
          2. Plugin arquivo único: baixa o .py via raw e empacota como ZIP
          3. Plugin em pasta: baixa archive completo do GitHub e extrai a subpasta
             Caminho no archive: <PROJECT_PREFIX>/<entry.path>/
        """
        if entry.download_url:
            return self._get_bytes(entry.download_url)

        raw_base = (
            f"{RAW_BASE.format(repo=self._repo, branch=self._branch)}/{PROJECT_PREFIX}"
        )

        if entry.path.endswith(".py"):
            raw_url = f"{raw_base}/{entry.path}"
            try:
                py_bytes = self._get_bytes(raw_url)
                zip_bytes = self._wrap_in_zip(entry.id, entry.entry, py_bytes)
                return zip_bytes
            except Exception:
                logger.debug(
                    "Suppressed error (arquivo único fallback)", exc_info=True)

        archive_url = (
            f"https://api.github.com/repos/{self._repo}/zipball/{self._branch}"
        )
        all_bytes = self._get_bytes(archive_url)
        return self._extract_plugin_folder(all_bytes, entry, PROJECT_PREFIX)

    def _get_json(self, url: str) -> Any:
        """Baixa e desserializa JSON de `url` com User-Agent do Codis."""
        _require_web_url(url, allow_http=True)
        req = urllib.request.Request(
            url, headers={"User-Agent": "Codis-PluginStore/1.0"}
        )
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
            return json.loads(_read_limited(resp).decode("utf-8"))

    def _get_bytes(self, url: str) -> bytes:
        """Baixa o conteúdo binário de `url` (somente HTTPS) com timeout de 60 segundos."""
        _require_web_url(url)
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Codis-PluginStore/1.0",
                "Accept": "application/vnd.github+json",
            },
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            return _read_limited(resp)

    @staticmethod
    def _wrap_in_zip(plugin_id: str, filename: str, content: bytes) -> bytes:
        """Cria um ZIP em memória contendo `filename` com `content`, compatível com PluginLoader."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(filename, content)
        return buf.getvalue()

    @staticmethod
    def _extract_plugin_folder(
        archive_bytes: bytes,
        entry: PluginEntry,
        project_prefix: str = "",
    ) -> bytes:
        """Extrai a subpasta do plugin de um archive ZIP completo do repositório.

        Considera a estrutura `<project_prefix>/<entry.path>` dentro do arquivo.
        Retorna um novo ZIP contendo apenas os arquivos dessa subpasta.
        """
        base_path = (
            f"{project_prefix}/{entry.path}".strip("/")
            if project_prefix
            else entry.path.lstrip("/")
        )
        search_path = base_path
        buf_out = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf_in:
            names = zf_in.namelist()
            root_prefix = names[0].split("/")[0] + "/" if names else ""
            target = root_prefix + search_path + "/"

            matched = [n for n in names if n.startswith(
                target) and not n.endswith("/")]
            if not matched:
                fallback = root_prefix + \
                    search_path.rstrip("/") + "/" + entry.entry
                if fallback in names:
                    matched = [fallback]

            if not matched:
                raise FileNotFoundError(
                    f"Plugin '{entry.id}' não encontrado no archive do repositório."
                )

            with zipfile.ZipFile(
                buf_out, "w", compression=zipfile.ZIP_DEFLATED
            ) as zf_out:
                for name in matched:
                    rel = name[len(target):]
                    if rel:
                        zf_out.writestr(rel, zf_in.read(name))

        return buf_out.getvalue()

    def set_repo(self, repo: str, branch: str = "main") -> None:
        """Troca o repositório e limpa o cache.

        Aceita:
          - Formato GitHub curto: ``owner/repo``
          - URL completa GitHub:  ``https://github.com/owner/repo``
          - URL direta de JSON:   ``https://example.com/my-index.json``
          - Caminho local de JSON: ``/path/to/index.json`` ou ``C:\\path\\index.json``
        """
        repo = repo.strip().strip("/")
        self._branch = branch.strip() or "main"
        self._cache = []
        self._last_fetch = 0
        self._custom_url: str = ""
        self._local_path: str = ""

        if repo.startswith("https://github.com/"):
            repo = repo.replace("https://github.com/", "").strip("/")
            self._repo = repo
        elif repo.startswith(("http://", "https://")):
            self._custom_url = repo
            self._repo = repo
        elif repo.endswith(".json") and ("/" in repo or "\\" in repo):
            p = Path(repo)
            if p.is_absolute() or p.exists():
                self._local_path = str(p.resolve())
                self._repo = repo
            else:
                self._repo = repo
        else:
            self._repo = repo

        if self._cfg:
            self._cfg.set("plugin_store_repo", self._repo)
            self._cfg.set("plugin_store_branch", self._branch)
            self._cfg.save()

    def fetch_index(self, force: bool = False) -> list[PluginEntry]:
        """Baixa o índice do repositório.

        Fontes suportadas (em ordem de prioridade):
          1. Arquivo JSON local (se set_repo recebeu um caminho local)
          2. URL HTTP/HTTPS arbitrária (se set_repo recebeu uma URL não-GitHub)
          3. GitHub Pages (sem rate limit) - padrão
          4. raw.githubusercontent.com - fallback do GitHub
        """
        now = time.time()
        if not force and self._cache and (now - self._last_fetch) < self._cache_ttl:
            return self._cache

        data: Any = None
        local_path = getattr(self, "_local_path", "")
        custom_url = getattr(self, "_custom_url", "")

        if local_path:
            try:
                data = json.loads(Path(local_path).read_text(encoding="utf-8"))
                logger.info(
                    "Plugin store: índice carregado de arquivo local: %s", local_path
                )
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"Arquivo local inválido: {exc}") from exc
        elif custom_url:
            try:
                data = self._get_json(custom_url)
                logger.info(
                    "Plugin store: índice carregado de URL customizada: %s", custom_url
                )
            except Exception as exc:
                raise RuntimeError(
                    f"Falha ao baixar de URL customizada: {exc}"
                ) from exc
        else:
            last_err: Exception | None = None
            for url in (self._pages_url(), self._raw_url()):
                try:
                    data = self._get_json(url)
                    break
                except Exception as exc:
                    last_err = exc
                    logger.debug("fetch_index fallback: %s - %s", url, exc)
            else:
                raise last_err  # type: ignore[misc]

        try:
            if not isinstance(data, dict) or "plugins" not in data:
                raise ValueError(
                    "Formato de índice inválido: esperado objeto JSON com chave 'plugins'"
                )
            self._cache = [
                PluginEntry.from_dict(p) for p in data["plugins"] if p.get("id")
            ]
            self._last_fetch = now
            logger.info("Plugin store: %d plugins carregados",
                        len(self._cache))
        except urllib.error.HTTPError as exc:
            if exc.code == 403:
                raise RuntimeError("rate_limit") from exc
            if exc.code == 404:
                raise RuntimeError(
                    f"index.json não encontrado em {self._repo}"
                ) from exc
            raise RuntimeError(str(exc)) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError("network") from exc
        except ValueError:
            raise
        except Exception as exc:
            raise RuntimeError(str(exc)) from exc

        return self._cache


class PluginStorePanel(tk.Toplevel):
    """
    Janela da loja de plugins do Codis.

    Exibe uma lista de plugins disponíveis no repositório remoto,
    com busca, filtros por categoria, ordenação e instalação com um clique.
    """

    _SORT_KEYS = ("name", "stars", "updated")

    def __init__(
        self,
        parent: tk.Widget,
        tmgr: Any,
        cfg: Any,
        loader: Any,
        on_refresh: Callable[[], None] | None = None,
    ) -> None:
        """Cria e exibe a janela da Plugin Store.

        Args:
            loader:     PluginLoader usado para instalar e listar plugins.
            on_refresh: Callback chamado após instalação bem-sucedida.
        """
        super().__init__(parent)
        t = tmgr.theme
        self._tmgr = tmgr
        self._cfg = cfg
        self._loader = loader
        self._on_refresh = on_refresh
        self._client = PluginStoreClient(cfg=cfg)
        self._entries: list[PluginEntry] = []
        self._selected: PluginEntry | None = None
        self._installing = False

        self.title(_t("plugin.store_title"))
        self.configure(bg=t.panel_bg)
        self.geometry("860x620")
        self.minsize(700, 480)
        self.transient(parent)
        self.focus_set()

        self._build()
        self._load_store(force=False)

    def _build(self) -> None:
        """Constrói toda a UI da janela: barra de topo, filtros, lista, painel de detalhe e status."""
        t = self._tmgr.theme

        top_bar = tk.Frame(self, bg=t.panel_bg, padx=12, pady=8)
        top_bar.pack(fill="x")

        tk.Label(
            top_bar,
            text=_t("plugin.store_title"),
            bg=t.panel_bg,
            fg=t.fg,
            font=(FONT_UI, 13, "bold"),
        ).pack(side="left")

        repo_row = tk.Frame(top_bar, bg=t.panel_bg)
        repo_row.pack(side="right")
        tk.Label(
            repo_row,
            text=_t("plugin.store_repo_label"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        ).pack(side="left", padx=(0, 4))
        self._repo_var = tk.StringVar(value=self._client._repo)
        repo_entry = tk.Entry(
            repo_row,
            textvariable=self._repo_var,
            bg=t.input_bg,
            fg=t.fg,
            font=(FONT_MONO, 8),
            relief="flat",
            highlightthickness=1,
            highlightbackground=t.border,
            insertbackground=t.cursor,
            width=26,
        )
        repo_entry.pack(side="left")
        repo_entry.bind("<Return>", lambda _: self._apply_repo())
        tk.Label(
            repo_row,
            text=_t("plugin.store_repo_hint_extended"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 7),
        ).pack(side="left", padx=4)
        tk.Button(
            repo_row,
            text=_t("plugin.store_browse_json"),
            command=self._browse_local_json,
            bg=t.btn_bg,
            fg=t.fg,
            relief="flat",
            font=(FONT_UI, 8),
            cursor="hand2",
            padx=6,
        ).pack(side="left", padx=(0, 2))
        tk.Button(
            repo_row,
            text=_t("plugin.store_repo_apply"),
            command=self._apply_repo,
            bg=t.btn_bg,
            fg=t.fg,
            relief="flat",
            font=(FONT_UI, 8),
            cursor="hand2",
            padx=6,
        ).pack(side="left")

        tk.Frame(self, bg=t.border, height=1).pack(fill="x")

        filter_bar = tk.Frame(self, bg=t.panel_bg, padx=8, pady=6)
        filter_bar.pack(fill="x")

        search_frame = tk.Frame(
            filter_bar,
            bg=t.input_bg,
            highlightthickness=1,
            highlightbackground=t.border,
        )
        search_frame.pack(side="left", fill="x", expand=True, padx=(0, 8))
        tk.Label(
            search_frame, text="🔍", bg=t.input_bg, fg=t.fg_muted, font=(FONT_UI, 9)
        ).pack(side="left", padx=4)
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._refilter())
        tk.Entry(
            search_frame,
            textvariable=self._search_var,
            bg=t.input_bg,
            fg=t.fg,
            relief="flat",
            bd=0,
            font=(FONT_UI, 9),
            insertbackground=t.cursor,
            highlightthickness=0,
        ).pack(side="left", fill="x", expand=True, pady=4)

        tk.Label(
            filter_bar,
            text=_t("plugin.store_category"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        ).pack(side="left")
        self._cat_var = tk.StringVar(value="all")
        cat_values = [_t(lk, default=cat) for cat, lk in CATEGORIES]
        self._cat_combo = ttk.Combobox(
            filter_bar,
            textvariable=self._cat_var,
            values=cat_values,
            state="readonly",
            width=14,
            font=(FONT_UI, 8),
        )
        self._cat_combo.pack(side="left", padx=4)
        self._cat_combo.bind("<<ComboboxSelected>>",
                             lambda _: self._refilter())

        tk.Label(
            filter_bar,
            text=_t("plugin.store_sort"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        ).pack(side="left")
        self._sort_var = tk.StringVar(value=_t("plugin.store_sort_name"))
        sort_opts = [
            _t("plugin.store_sort_name"),
            _t("plugin.store_sort_stars"),
            _t("plugin.store_sort_updated"),
        ]
        self._sort_combo = ttk.Combobox(
            filter_bar,
            textvariable=self._sort_var,
            values=sort_opts,
            state="readonly",
            width=16,
            font=(FONT_UI, 8),
        )
        self._sort_combo.pack(side="left", padx=4)
        self._sort_combo.bind("<<ComboboxSelected>>",
                              lambda _: self._refilter())

        self._filter_var = tk.StringVar(value=_t("plugin.store_filter_all"))
        filter_opts = [
            _t("plugin.store_filter_all"),
            _t("plugin.store_filter_inst"),
            _t("plugin.store_filter_avail"),
        ]
        self._filter_combo = ttk.Combobox(
            filter_bar,
            textvariable=self._filter_var,
            values=filter_opts,
            state="readonly",
            width=14,
            font=(FONT_UI, 8),
        )
        self._filter_combo.pack(side="left", padx=4)
        self._filter_combo.bind("<<ComboboxSelected>>",
                                lambda _: self._refilter())

        tk.Button(
            filter_bar,
            text=_t("plugin.store_refresh"),
            command=lambda: self._load_store(force=True),
            bg=t.btn_bg,
            fg=t.fg,
            relief="flat",
            font=(FONT_UI, 8),
            cursor="hand2",
            padx=6,
        ).pack(side="right")

        tk.Frame(self, bg=t.border, height=1).pack(fill="x")

        body = tk.PanedWindow(
            self,
            orient="horizontal",
            bg=t.panel_bg,
            sashwidth=4,
            sashrelief="flat",
            handlepad=0,
            handlesize=0,
        )
        body.pack(fill="both", expand=True, padx=4, pady=4)

        list_frame = tk.Frame(body, bg=t.editor_bg)
        body.add(list_frame, minsize=280, width=320)

        scroll_y = ttk.Scrollbar(list_frame, orient="vertical")
        scroll_y.pack(side="right", fill="y")

        self._listbox = tk.Listbox(
            list_frame,
            bg=t.editor_bg,
            fg=t.fg,
            font=(FONT_UI, 9),
            relief="flat",
            bd=0,
            selectbackground=t.sel_bg,
            selectforeground=t.sel_fg,
            yscrollcommand=scroll_y.set,
            activestyle="none",
            cursor="hand2",
        )
        self._listbox.pack(fill="both", expand=True)
        scroll_y.config(command=self._listbox.yview)
        self._listbox.bind("<<ListboxSelect>>", self._on_select)
        self._listbox.bind("<Double-Button-1>",
                           lambda _: self._install_selected())

        detail_outer = tk.Frame(body, bg=t.panel_bg)
        body.add(detail_outer, minsize=320)

        detail_header = tk.Frame(detail_outer, bg=t.panel_bg, padx=12, pady=8)
        detail_header.pack(fill="x")
        self._name_lbl = tk.Label(
            detail_header,
            text="",
            bg=t.panel_bg,
            fg=t.fg,
            font=(FONT_UI, 13, "bold"),
            anchor="w",
            wraplength=340,
        )
        self._name_lbl.pack(side="left", fill="x", expand=True)

        self._install_btn = tk.Button(
            detail_header,
            text=_t("plugin.store_install"),
            command=self._install_selected,
            bg=getattr(t, "accent", "#4fc3f7"),
            fg="#ffffff",
            relief="flat",
            font=(FONT_UI, 9, "bold"),
            cursor="hand2",
            padx=10,
            pady=4,
            state="disabled",
        )
        self._install_btn.pack(side="right")

        self._gh_btn = tk.Button(
            detail_header,
            text=_t("plugin.store_open_gh"),
            command=self._open_github,
            bg=t.btn_bg,
            fg=t.fg,
            relief="flat",
            font=(FONT_UI, 8),
            cursor="hand2",
            padx=6,
            pady=4,
        )
        self._gh_btn.pack(side="right", padx=4)

        meta_frame = tk.Frame(detail_outer, bg=t.panel_bg, padx=12)
        meta_frame.pack(fill="x")
        self._meta_lbl = tk.Label(
            meta_frame,
            text="",
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            anchor="w",
            justify="left",
            wraplength=380,
        )
        self._meta_lbl.pack(anchor="w")

        self._tags_frame = tk.Frame(detail_outer, bg=t.panel_bg, padx=12)
        self._tags_frame.pack(fill="x", pady=(2, 6))

        tk.Frame(detail_outer, bg=t.border, height=1).pack(fill="x", padx=8)

        readme_nb = ttk.Notebook(detail_outer)
        readme_nb.pack(fill="both", expand=True, padx=4, pady=4)

        readme_frame = tk.Frame(readme_nb, bg=t.editor_bg)
        readme_nb.add(readme_frame, text=_t("plugin.store_readme"))

        readme_scroll = ttk.Scrollbar(readme_frame, orient="vertical")
        readme_scroll.pack(side="right", fill="y")

        self._readme_txt = tk.Text(
            readme_frame,
            bg=t.editor_bg,
            fg=t.fg,
            font=(FONT_MONO, 8),
            relief="flat",
            bd=0,
            wrap="word",
            state="disabled",
            highlightthickness=0,
            padx=10,
            pady=8,
            yscrollcommand=readme_scroll.set,
        )
        self._readme_txt.pack(fill="both", expand=True)
        readme_scroll.config(command=self._readme_txt.yview)

        status_bar = tk.Frame(self, bg=t.panel_bg, height=24)
        status_bar.pack(fill="x", side="bottom")
        status_bar.pack_propagate(False)
        self._status_lbl = tk.Label(
            status_bar,
            text="",
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            anchor="w",
        )
        self._status_lbl.pack(side="left", padx=8)

        tk.Button(
            self,
            text=_t("plugin.store_close"),
            command=self.destroy,
            bg=t.btn_bg,
            fg=t.fg,
            relief="flat",
            font=(FONT_UI, 8),
            cursor="hand2",
            padx=8,
            pady=2,
        ).pack(side="bottom", anchor="e", padx=8, pady=4)

        self._show_no_selection()

    def _load_store(self, force: bool = False) -> None:
        """Dispara fetch do índice em thread separada e atualiza a lista ao concluir."""
        self._set_status(_t("plugin.store_loading"))
        self._listbox.delete(0, "end")
        self._listbox.insert("end", "  " + _t("plugin.store_loading"))

        def _fetch():
            try:
                entries = self._client.fetch_index(force=force)
                self.after(0, lambda e=entries: self._on_loaded(e))
            except RuntimeError as exc:
                msg = str(exc)
                if msg == "rate_limit":
                    self.after(0, lambda: self._on_error(
                        _t("plugin.store_rate_limit")))
                elif msg == "network":
                    self.after(0, lambda: self._on_error(
                        _t("plugin.store_net_err")))
                else:
                    self.after(
                        0, lambda m=msg: self._on_error(
                            _t("plugin.store_error", err=m))
                    )

        threading.Thread(target=_fetch, daemon=True).start()

    def _on_loaded(self, entries: list[PluginEntry]) -> None:
        """Callback de sucesso do fetch: armazena entradas e reaplica filtros."""
        self._entries = entries
        self._refilter()
        self._set_status(f"{len(entries)} plugins")

    def _on_error(self, msg: str) -> None:
        """Exibe mensagem de erro na lista e no status bar."""
        self._listbox.delete(0, "end")
        self._listbox.insert("end", "  " + msg)
        self._set_status(msg)

    def _refilter(self) -> None:
        """Lê os controles de filtro/busca/ordenação e repopula a listbox."""
        query = self._search_var.get()
        cat_idx = self._cat_combo.current()
        cat_key = CATEGORIES[cat_idx][0] if 0 <= cat_idx < len(
            CATEGORIES) else "all"
        sort_idx = self._sort_combo.current()
        sort_key = (
            self._SORT_KEYS[sort_idx]
            if 0 <= sort_idx < len(self._SORT_KEYS)
            else "name"
        )
        filt_idx = self._filter_combo.current()
        filt_map = {0: "all", 1: "installed", 2: "available"}
        filt_key = filt_map.get(filt_idx, "all")

        installed = set(self._loader.all_plugin_ids()
                        ) if self._loader else set()
        filtered = self._client.search(
            query, cat_key, sort_key, filt_key, installed)

        self._listbox.delete(0, "end")
        if not filtered:
            self._listbox.insert("end", "  " + _t("plugin.store_empty"))
            return
        for entry in filtered:
            is_inst = entry.id in installed
            icon = "✅" if is_inst else "  "
            stars = f"  ★{entry.stars}" if entry.stars else ""
            text = f"{icon} {entry.name}{stars}"
            self._listbox.insert("end", f"  {text}")

    def _on_select(self, _: Any) -> None:
        """Exibe o detalhe do plugin selecionado na listbox."""
        sel = self._listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        q = self._search_var.get()
        cat_idx = self._cat_combo.current()
        cat_key = CATEGORIES[cat_idx][0] if 0 <= cat_idx < len(
            CATEGORIES) else "all"
        s_idx = self._sort_combo.current()
        s_key = self._SORT_KEYS[s_idx] if 0 <= s_idx < len(
            self._SORT_KEYS) else "name"
        f_idx = self._filter_combo.current()
        f_map = {0: "all", 1: "installed", 2: "available"}
        f_key = f_map.get(f_idx, "all")
        installed = set(self._loader.all_plugin_ids()
                        ) if self._loader else set()
        filtered = self._client.search(q, cat_key, s_key, f_key, installed)

        if idx >= len(filtered):
            return
        entry = filtered[idx]
        self._selected = entry
        self._show_detail(entry, installed)

    def _show_no_selection(self) -> None:
        """Reseta o painel de detalhe para o estado sem seleção."""
        self._name_lbl.config(text="")
        self._meta_lbl.config(text=_t("plugin.store_detail_none"))
        self._install_btn.config(
            state="disabled", text=_t("plugin.store_install"))
        self._gh_btn.config(state="disabled")
        self._set_readme(_t("plugin.store_detail_none"))
        for w in self._tags_frame.winfo_children():
            w.destroy()

    def _show_detail(self, entry: PluginEntry, installed: set[str]) -> None:
        """Popula o painel de detalhe com os metadados de `entry` e dispara fetch do README."""
        t = self._tmgr.theme
        is_inst = entry.id in installed

        self._name_lbl.config(text=entry.name)

        meta_parts = [entry.description]
        if entry.version:
            meta_parts.append(_t("plugin.store_detail_ver", ver=entry.version))
        if entry.author:
            meta_parts.append(
                _t("plugin.store_detail_author", author=entry.author))
        if entry.codis_compat:
            meta_parts.append(
                _t("plugin.store_detail_compat", compat=entry.codis_compat)
            )
        if entry.stars:
            meta_parts.append(f"★ {entry.stars}")
        if entry.updated:
            meta_parts.append(entry.updated)
        self._meta_lbl.config(text="  |  ".join(meta_parts))

        for w in self._tags_frame.winfo_children():
            w.destroy()
        for tag in entry.tags[:8]:
            tk.Label(
                self._tags_frame,
                text=f"  {tag}  ",
                bg=getattr(t, "sel_bg", t.input_bg),
                fg=t.fg_muted,
                font=(FONT_UI, 7),
                relief="flat",
                padx=2,
                pady=1,
            ).pack(side="left", padx=2)

        self._install_btn.config(
            text=(
                _t("plugin.store_installed") if is_inst else _t(
                    "plugin.store_install")
            ),
            state="normal" if not is_inst and not self._installing else "disabled",
            bg="#555555" if is_inst else getattr(t, "accent", "#4fc3f7"),
        )
        self._gh_btn.config(state="normal" if entry.homepage else "disabled")

        self._set_readme("⟳ " + _t("plugin.store_loading"))
        threading.Thread(
            target=self._fetch_readme_async,
            args=(entry,),
            daemon=True,
        ).start()

    def _fetch_readme_async(self, entry: PluginEntry) -> None:
        """Baixa o README de `entry` em thread separada e atualiza o widget de texto."""
        try:
            text = self._client.fetch_readme(entry)
        except Exception:
            text = ""
        display = text or _t("plugin.store_no_readme")
        self.after(0, lambda d=display: self._set_readme(d))

    def _set_readme(self, text: str) -> None:
        """Substitui o conteúdo do widget de README pelo texto fornecido."""
        self._readme_txt.config(state="normal")
        self._readme_txt.delete("1.0", "end")
        self._readme_txt.insert("1.0", text)
        self._readme_txt.config(state="disabled")

    def _install_selected(self) -> None:
        """Inicia o download e instalação do plugin selecionado em thread separada."""
        if not self._selected or self._installing:
            return
        entry = self._selected
        installed = set(self._loader.all_plugin_ids()
                        ) if self._loader else set()
        if entry.id in installed:
            return

        self._installing = True
        self._install_btn.config(
            state="disabled", text=_t("plugin.store_installing", name=entry.name)
        )
        self._set_status(_t("plugin.store_installing", name=entry.name))

        def _do_install():
            try:
                zip_bytes = self._client.download_plugin(entry)
                with tempfile.NamedTemporaryFile(
                    suffix=".zip",
                    prefix=f"codis_plugin_{entry.id}_",
                    delete=False,
                ) as tmp:
                    tmp.write(zip_bytes)
                    tmp_path = Path(tmp.name)

                def _confirm_cb(pid, blocked, warned):
                    return messagebox.askyesno(
                        "⚠ Plugin",
                        f"Plugin '{pid}' contém imports suspeitos:\n{blocked}\n\nInstalar mesmo assim?",
                        parent=self,
                    )

                ok = self._loader.install_from_path(
                    tmp_path, confirm_cb=_confirm_cb)
                tmp_path.unlink(missing_ok=True)

                def _finish(success=ok, name=entry.name):
                    self._installing = False
                    if success:
                        self._set_status(
                            _t("plugin.store_install_ok", name=name))
                        self._refilter()
                        if self._on_refresh:
                            self._on_refresh()
                    else:
                        self._set_status(
                            _t(
                                "plugin.store_install_err",
                                name=name,
                                err="loader retornou False",
                            )
                        )
                    if self._selected and self._selected.id == entry.id:
                        installed_now = (
                            set(self._loader.all_plugin_ids())
                            if self._loader
                            else set()
                        )
                        self._show_detail(entry, installed_now)

                self.after(0, _finish)

            except Exception as exc:
                err_msg = str(exc)

                def _on_err(e=err_msg, n=entry.name):
                    self._installing = False
                    self._set_status(
                        _t("plugin.store_install_err", name=n, err=e))
                    self._install_btn.config(
                        state="normal", text=_t("plugin.store_install")
                    )

                self.after(0, _on_err)

        threading.Thread(target=_do_install, daemon=True).start()

    def _open_github(self) -> None:
        """Abre a homepage do plugin selecionado no navegador padrão do sistema."""
        if self._selected and self._selected.homepage:
            webbrowser.open(self._selected.homepage)

    def _apply_repo(self) -> None:
        """Aplica a fonte de plugins digitada no campo.

        Aceita:
          - GitHub curto:  ``owner/repo``
          - URL GitHub:    ``https://github.com/owner/repo``
          - URL direta:    ``https://example.com/index.json``
          - Arquivo local: ``/home/user/meus-plugins.json``
        """
        repo = self._repo_var.get().strip()
        if not repo:
            return
        self._client.set_repo(repo)
        self._load_store(force=True)

    def _browse_local_json(self) -> None:
        """Abre diálogo de seleção de arquivo JSON local para usar como índice de plugins."""
        from tkinter import filedialog

        path = filedialog.askopenfilename(
            parent=self,
            title=_t("plugin.store_browse_json"),
            filetypes=[("JSON", "*.json"), ("Todos", "*.*")],
        )
        if path:
            self._repo_var.set(path)
            self._apply_repo()

    def _set_status(self, text: str) -> None:
        """Atualiza o texto da barra de status, ignorando erros se a janela foi fechada."""
        with contextlib.suppress(tk.TclError):
            self._status_lbl.config(text=text)


def open_plugin_store(
    parent: tk.Widget,
    tmgr: Any,
    cfg: Any,
    loader: Any,
    on_refresh: Callable[[], None] | None = None,
) -> PluginStorePanel:
    """Abre a janela da Plugin Store e a retorna."""
    return PluginStorePanel(parent, tmgr, cfg, loader, on_refresh)
