"""Codis - SQL Studio.

Motor de banco de dados integrado ao editor.
Drivers suportados (instalação opcional via pip):
  - SQLite   - embutido no Python, zero dependências
  - PostgreSQL - psycopg2  (pip install psycopg2-binary)
  - MySQL/MariaDB - pymysql (pip install pymysql)
  - DuckDB   - duckdb     (pip install duckdb)

Funcionalidades:
  - Browser de esquema: databases -> tabelas -> colunas / índices / FKs
  - Editor SQL multi-linha com syntax highlighting e autocomplete
  - Execução de query ou seleção atual (Ctrl+Enter / F5)
  - Grid de resultados com ordenação por coluna, exportação CSV/JSON
  - Histórico persistente de queries com favoritos
  - Múltiplas conexões salvas em ~/.codis/sql_connections.json
  - Suporte a apply_theme(tmgr) para recoloração dinâmica
"""

from __future__ import annotations

import contextlib
import csv as _csv
import json
import logging
import re
import sqlite3
import threading
import time
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from src.constants import CONFIG_DIR, FONT_MONO, FONT_UI
from src.i18n import _t
from src.theme.utils import make_toolbar_button
from src.ui._persistence_utils import load_dataclass_list, save_dataclass_list
from src.ui.grab_safe import safe_grab_set

logger = logging.getLogger("Codis.SQLStudio")

_CONNS_FILE = CONFIG_DIR / "sql_connections.json"
_HISTORY_FILE = CONFIG_DIR / "sql_history.json"
_MAX_HISTORY = 500
_MAX_ROWS_GRID = 5000

_SQL_KW = [
    "SELECT",
    "FROM",
    "WHERE",
    "INSERT",
    "INTO",
    "VALUES",
    "UPDATE",
    "SET",
    "DELETE",
    "CREATE",
    "TABLE",
    "DROP",
    "ALTER",
    "ADD",
    "COLUMN",
    "INDEX",
    "JOIN",
    "LEFT",
    "RIGHT",
    "INNER",
    "OUTER",
    "FULL",
    "ON",
    "AS",
    "AND",
    "OR",
    "NOT",
    "NULL",
    "IS",
    "IN",
    "LIKE",
    "BETWEEN",
    "ORDER",
    "BY",
    "GROUP",
    "HAVING",
    "LIMIT",
    "OFFSET",
    "DISTINCT",
    "COUNT",
    "SUM",
    "AVG",
    "MIN",
    "MAX",
    "CASE",
    "WHEN",
    "THEN",
    "ELSE",
    "END",
    "WITH",
    "UNION",
    "ALL",
    "EXCEPT",
    "INTERSECT",
    "EXISTS",
    "UNIQUE",
    "PRIMARY",
    "KEY",
    "FOREIGN",
    "REFERENCES",
    "CASCADE",
    "DEFAULT",
    "CHECK",
    "NOT NULL",
    "AUTO_INCREMENT",
    "AUTOINCREMENT",
    "PRAGMA",
    "EXPLAIN",
    "ANALYZE",
    "BEGIN",
    "COMMIT",
    "ROLLBACK",
    "TRANSACTION",
    "SAVEPOINT",
    "RELEASE",
    "VACUUM",
    "ATTACH",
    "DETACH",
    "RETURNING",
    "ILIKE",
    "SIMILAR",
    "COALESCE",
    "NULLIF",
    "CAST",
    "CONVERT",
    "SUBSTR",
    "LENGTH",
    "TRIM",
    "UPPER",
    "LOWER",
    "REPLACE",
    "STRFTIME",
    "DATE",
    "TIME",
    "DATETIME",
    "CURRENT_TIMESTAMP",
    "CURRENT_DATE",
    "CURRENT_TIME",
]

DRIVERS: dict[str, dict[str, Any]] = {
    "sqlite": {"label": "SQLite", "module": None, "builtin": True},
    "postgres": {"label": "PostgreSQL", "module": "psycopg2", "builtin": False},
    "mysql": {"label": "MySQL", "module": "pymysql", "builtin": False},
    "duckdb": {"label": "DuckDB", "module": "duckdb", "builtin": False},
}

_DEFAULT_PORTS: dict[str, int] = {
    "postgres": 5432,
    "mysql": 3306,
    "duckdb": 0,
}


def _driver_available(key: str) -> bool:
    info = DRIVERS.get(key, {})
    if info.get("builtin"):
        return True
    mod = info.get("module")
    if not mod:
        return False
    try:
        __import__(mod)
        return True
    except ImportError:
        return False


def _quote_ident(name: str) -> str:
    """Escapa um identificador SQL (tabela/coluna) para uso entre aspas duplas.

    Nomes de tabela/coluna vêm do schema introspectado, mas SQLite/Postgres
    aceitam identificadores com aspas duplas embutidas
    (ex.: ``foo" ; DROP TABLE bar; --``), o que quebraria o SQL gerado.
    Dobra cada ``"`` interna, seguindo a regra padrão SQL de escape de
    identificador entre aspas.
    """
    return name.replace('"', '""')


def _quote_backtick_ident(name: str) -> str:
    """Escapa um identificador SQL para uso entre backticks (dialeto MySQL/MariaDB).

    Mesmo racional de ``_quote_ident``, mas para o delimitador de
    identificador do MySQL/MariaDB (crase em vez de aspas duplas).
    """
    return name.replace("`", "``")


@dataclass
class Connection:
    """Perfil de conexão salvo."""

    id: str = field(default_factory=lambda: f"conn_{int(time.time())}")
    name: str = "Nova Conexão"
    driver: str = "sqlite"
    filepath: str = ""
    host: str = "localhost"
    port: int = 5432
    database: str = ""
    user: str = ""
    password: str = ""
    readonly: bool = False

    @property
    def display(self) -> str:
        """Exibe o resultado da query no widget de saída."""
        if self.driver in ("sqlite", "duckdb"):
            label = DRIVERS[self.driver]["label"]
            name = Path(self.filepath).name or ":memory:"
            return f"{label} - {name}"
        return (
            f"{DRIVERS[self.driver]['label']} - {self.user}@{self.host}/{self.database}"
        )


def _load_connections() -> list[Connection]:
    return load_dataclass_list(_CONNS_FILE, Connection, logger=logger)


def _save_connections(conns: list[Connection]) -> None:
    save_dataclass_list(_CONNS_FILE, conns)


def _load_history() -> list[dict]:
    if not _HISTORY_FILE.exists():
        return []
    try:
        return json.loads(_HISTORY_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("Falha ao carregar histórico SQL: %s",
                       exc, exc_info=True)
        return []


def _save_history(hist: list[dict]) -> None:
    _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    _HISTORY_FILE.write_text(
        json.dumps(hist[-_MAX_HISTORY:], indent=2, ensure_ascii=False), encoding="utf-8"
    )


class DBConnection:
    """Abstração sobre diferentes drivers de BD."""

    def __init__(self, profile: Connection) -> None:
        """Inicializa a conexão de banco de dados."""
        self._profile = profile
        self._conn: Any = None
        self._driver = profile.driver
        self._lock = threading.Lock()

    def connect(self) -> None:
        """Estabelece a conexão com o banco de dados."""
        p = self._profile
        if p.driver == "sqlite":
            path = p.filepath or ":memory:"
            flags = sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES
            if p.readonly:
                uri = f"file:{path}?mode=ro"
                self._conn = sqlite3.connect(
                    uri, uri=True, detect_types=flags, check_same_thread=False
                )
            else:
                self._conn = sqlite3.connect(
                    path, detect_types=flags, check_same_thread=False
                )
            self._conn.row_factory = sqlite3.Row
        elif p.driver == "postgres":
            import base64

            import psycopg2  # type: ignore[import]

            pw = base64.b64decode(p.password).decode() if p.password else ""
            self._conn = psycopg2.connect(
                host=p.host,
                port=p.port,
                dbname=p.database,
                user=p.user,
                password=pw,
            )
        elif p.driver == "mysql":
            import base64

            import pymysql  # type: ignore[import]

            pw = base64.b64decode(p.password).decode() if p.password else ""
            self._conn = pymysql.connect(
                host=p.host,
                port=p.port,
                database=p.database,
                user=p.user,
                password=pw,
                autocommit=True,
            )
        elif p.driver == "duckdb":
            import duckdb  # type: ignore[import]

            path = p.filepath or ":memory:"
            self._conn = duckdb.connect(path, read_only=p.readonly)
        else:
            raise ValueError(f"Driver desconhecido: {p.driver}")

    def close(self) -> None:
        """Encerra a conexão com o banco de dados."""
        try:
            if self._conn:
                self._conn.close()
        except Exception:
            logger.debug(
                "Falha ao fechar conexão com o banco de dados.", exc_info=True)
        self._conn = None

    @property
    def connected(self) -> bool:
        """Retorna ``True`` se a conexão está ativa."""
        return self._conn is not None

    def execute(
        self, sql: str, params: tuple = ()
    ) -> tuple[list[str], list[tuple], str]:
        """Executa SQL. Retorna (colunas, linhas, mensagem)."""
        if not self._conn:
            return [], [], _t("sql.not_connected", "Não conectado.")
        with self._lock:
            try:
                cur = self._conn.cursor()
                t0 = time.perf_counter()
                cur.execute(sql, params)
                elapsed_ms = (time.perf_counter() - t0) * 1000

                if cur.description:
                    cols = [d[0] for d in cur.description]
                    rows = cur.fetchmany(_MAX_ROWS_GRID)
                    rows = [tuple(r) for r in rows]
                    n_rows = len(rows)
                    msg = _t(
                        "sql.rows_fetched", f"{n_rows} linha(s) em {elapsed_ms:.1f} ms"
                    )
                else:
                    cols = []
                    rows = []
                    count = getattr(cur, "rowcount", 0) or 0
                    msg = _t(
                        "sql.rows_affected",
                        f"OK - {count} linha(s) afetada(s) em {elapsed_ms:.1f} ms",
                    )
                    with contextlib.suppress(Exception):
                        self._conn.commit()
                cur.close()
                return cols, rows, msg
            except Exception as exc:
                with contextlib.suppress(Exception):
                    self._conn.rollback()
                return [], [], f"❌ {exc}"

    def get_schema(self) -> dict[str, list[dict]]:
        """Retorna esquema: {tabela: [{name, type, pk, notnull, default}]}."""
        if not self._conn:
            return {}
        schema: dict[str, list[dict]] = {}
        with self._lock:
            try:
                drv = self._driver
                cur = self._conn.cursor()
                if drv == "sqlite":
                    cur.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                    )
                    tables = [r[0] for r in cur.fetchall()]
                    for tbl in tables:
                        cur.execute(
                            f'PRAGMA table_info("{_quote_ident(tbl)}")')
                        schema[tbl] = [
                            {
                                "name": r[1],
                                "type": r[2],
                                "notnull": bool(r[3]),
                                "default": r[4],
                                "pk": bool(r[5]),
                            }
                            for r in cur.fetchall()
                        ]
                elif drv == "postgres":
                    cur.execute(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema='public' ORDER BY table_name"
                    )
                    tables = [r[0] for r in cur.fetchall()]
                    for tbl in tables:
                        cur.execute(
                            "SELECT column_name, data_type, is_nullable, "
                            "column_default FROM information_schema.columns "
                            "WHERE table_name=%s ORDER BY ordinal_position",
                            (tbl,),
                        )
                        schema[tbl] = [
                            {
                                "name": r[0],
                                "type": r[1],
                                "notnull": r[2] == "NO",
                                "default": r[3],
                                "pk": False,
                            }
                            for r in cur.fetchall()
                        ]
                elif drv in ("mysql", "mariadb"):
                    cur.execute("SHOW TABLES")
                    tables = [r[0] for r in cur.fetchall()]
                    for tbl in tables:
                        cur.execute(f"DESCRIBE `{_quote_backtick_ident(tbl)}`")
                        schema[tbl] = [
                            {
                                "name": r[0],
                                "type": r[1],
                                "notnull": r[2] == "NO",
                                "default": r[4],
                                "pk": r[3] == "PRI",
                            }
                            for r in cur.fetchall()
                        ]
                elif drv == "duckdb":
                    cur.execute("SHOW ALL TABLES")
                    tables = [r[2] for r in cur.fetchall()]
                    for tbl in tables:
                        cur.execute(f'DESCRIBE "{_quote_ident(tbl)}"')
                        schema[tbl] = [
                            {
                                "name": r[0],
                                "type": r[1],
                                "notnull": False,
                                "default": None,
                                "pk": False,
                            }
                            for r in cur.fetchall()
                        ]
                cur.close()
            except Exception as exc:
                logger.warning("get_schema falhou: %s", exc)
        return schema


class SQLStudioPanel(tk.Frame):
    """SQL Studio integrado ao Codis."""

    def __init__(self, parent: tk.Widget, tmgr: Any, cfg: Any) -> None:
        """Inicializa o painel principal do SQL Studio."""
        t = tmgr.theme
        super().__init__(parent, bg=t.panel_bg)
        self._tmgr = tmgr
        self._cfg = cfg

        self._connections = _load_connections()
        self._history = _load_history()
        self._active_conn: DBConnection | None = None
        self._active_profile: Connection | None = None
        self._schema: dict[str, list[dict]] = {}
        self._busy = False

        self._build(t)

    def apply_theme(self, tmgr: Any) -> None:
        """Reaplica tema visualmente sem recriar widgets."""
        self._tmgr = tmgr
        t = tmgr.theme
        for w in self.winfo_children():
            w.destroy()
        self.config(bg=t.panel_bg)
        self._build(t)

    def _build(self, t: Any) -> None:
        for child in self.winfo_children():
            with contextlib.suppress(tk.TclError):
                child.destroy()

        hdr = tk.Frame(self, bg=t.activity_bg, height=52)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text="🗄", bg=t.activity_bg, fg=t.accent, font=(FONT_UI, 18)).pack(
            side="left", padx=(12, 4), pady=8
        )
        tk.Label(
            hdr,
            text=_t("sql.title", "SQL Studio"),
            bg=t.activity_bg,
            fg=t.fg,
            font=(FONT_UI, 13, "bold"),
        ).pack(side="left")

        self._conn_badge = tk.Label(
            hdr,
            text=_t("sql.no_connection", "● sem conexão"),
            bg=t.activity_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        )
        self._conn_badge.pack(side="right", padx=12)

        tk.Frame(self, bg=t.border, height=1).pack(fill="x")

        conn_bar = tk.Frame(self, bg=t.sidebar_bg)
        conn_bar.pack(fill="x")
        self._build_connection_bar(t, conn_bar)

        tk.Frame(self, bg=t.border, height=1).pack(fill="x")

        paned = tk.PanedWindow(
            self, orient="horizontal", bg=t.border, sashwidth=4, sashrelief="flat"
        )
        paned.pack(fill="both", expand=True)

        left = tk.Frame(paned, bg=t.panel_bg)
        paned.add(left, minsize=160, width=220)
        self._build_schema_browser(t, left)

        right = tk.Frame(paned, bg=t.panel_bg)
        paned.add(right, minsize=400)
        self._build_editor_area(t, right)

    def _build_connection_bar(self, t: Any, parent: tk.Widget) -> None:
        tk.Label(
            parent,
            text=_t("sql.connection", "Conexão:"),
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        ).pack(side="left", padx=(8, 4), pady=5)

        self._conn_var = tk.StringVar()
        self._conn_cb = ttk.Combobox(
            parent,
            textvariable=self._conn_var,
            state="readonly",
            width=26,
            font=(FONT_UI, 8),
        )
        self._conn_cb.pack(side="left", pady=5)
        self._refresh_conn_combo()

        def _btn(lbl: str, cmd: Any, accent: bool = False) -> tk.Label:
            return make_toolbar_button(parent, t, lbl, cmd, accent=accent, outer_pady=5)

        _btn(_t("sql.connect_btn", "▶ Conectar"), self._on_connect, accent=True)
        _btn(_t("sql.disconnect_btn", "⏹ Desconectar"), self._on_disconnect)
        _btn(_t("sql.new_conn", "+ Nova"), self._new_connection)
        _btn(_t("sql.edit_conn", "✏ Editar"), self._edit_connection)
        _btn(_t("sql.del_conn", "🗑"), self._delete_connection)

        self._status_var = tk.StringVar(value=_t("sql.ready", "Pronto."))
        tk.Label(
            parent,
            textvariable=self._status_var,
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 7),
        ).pack(side="right", padx=12)

    def _refresh_conn_combo(self) -> None:
        values = [c.display for c in self._connections]
        self._conn_cb["values"] = values
        if values:
            self._conn_cb.current(0)

    def _build_schema_browser(self, t: Any, parent: tk.Widget) -> None:
        hdr = tk.Frame(parent, bg=t.sidebar_bg)
        hdr.pack(fill="x")
        tk.Label(
            hdr,
            text=_t("sql.schema_browser", "Esquema"),
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 7, "bold"),
        ).pack(side="left", padx=8, pady=3)

        refresh_l = tk.Label(
            hdr,
            text="↻",
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
            cursor="hand2",
        )
        refresh_l.pack(side="right", padx=6)
        refresh_l.bind("<Button-1>", lambda _: self._refresh_schema())

        self._schema_search_var = tk.StringVar()
        srch = tk.Entry(
            parent,
            textvariable=self._schema_search_var,
            bg=t.editor_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            bd=4,
            font=(FONT_UI, 8),
            highlightthickness=0,
        )
        srch.pack(fill="x", padx=4, pady=(2, 0))
        srch.bind("<KeyRelease>", lambda _: self._filter_schema())

        self._schema_tree = ttk.Treeview(
            parent, show="tree", selectmode="browse")
        schema_vsb = ttk.Scrollbar(
            parent, orient="vertical", command=self._schema_tree.yview
        )
        self._schema_tree.configure(yscrollcommand=schema_vsb.set)
        schema_vsb.pack(side="right", fill="y")
        self._schema_tree.pack(fill="both", expand=True, padx=(4, 0))

        self._schema_tree.bind("<Double-1>", self._on_schema_double)
        self._schema_tree.bind("<Button-3>", self._schema_context_menu)

    def _refresh_schema(self) -> None:
        if not self._active_conn or not self._active_conn.connected:
            return
        self._set_status(_t("sql.loading_schema", "Carregando esquema..."))

        def _work() -> None:
            schema = self._active_conn.get_schema()  # type: ignore[union-attr]
            self.after(0, lambda: self._populate_schema(schema))

        threading.Thread(target=_work, daemon=True).start()

    def _populate_schema(self, schema: dict[str, list[dict]]) -> None:
        self._schema = schema
        self._render_schema(schema)
        self._set_status(
            _t("sql.schema_loaded", f"{len(schema)} tabela(s) carregada(s)")
        )

    def _render_schema(self, schema: dict[str, list[dict]]) -> None:
        self._schema_tree.delete(*self._schema_tree.get_children())
        q = self._schema_search_var.get().lower()

        for tbl, cols in sorted(schema.items()):
            if q and q not in tbl.lower():
                continue
            node = self._schema_tree.insert(
                "", "end", iid=f"tbl:{tbl}", text=f"📋 {tbl}", open=False
            )

            for col in cols:
                pk_icon = "🔑 " if col.get("pk") else ""
                nn_icon = "!" if col.get("notnull") else ""
                col_text = f"  {pk_icon}{col['name']}  {col['type']}{nn_icon}"
                self._schema_tree.insert(
                    node, "end", iid=f"col:{tbl}.{col['name']}", text=col_text
                )

    def _filter_schema(self) -> None:
        self._render_schema(self._schema)

    def _on_schema_double(self, _e: Any) -> None:
        sel = self._schema_tree.selection()
        if not sel:
            return
        iid = sel[0]
        if iid.startswith("tbl:"):
            tbl = iid[4:]
            self._insert_to_editor(
                f'SELECT * FROM "{_quote_ident(tbl)}" LIMIT 100;')
        elif iid.startswith("col:"):
            col = iid[4:].split(".")[1]
            self._insert_to_editor(col)

    def _schema_context_menu(self, event: Any) -> None:
        iid = self._schema_tree.identify_row(event.y)
        if not iid:
            return
        self._schema_tree.selection_set(iid)
        t = self._tmgr.theme
        menu = tk.Menu(self, tearoff=False, bg=t.panel_bg, fg=t.fg)

        if iid.startswith("tbl:"):
            tbl = iid[4:]
            menu.add_command(
                label=_t("sql.ctx_select", "SELECT * FROM ..."),
                command=lambda: self._insert_to_editor(
                    f'SELECT * FROM "{_quote_ident(tbl)}" LIMIT 100;'
                ),
            )
            menu.add_command(
                label=_t("sql.ctx_count", "COUNT(*)"),
                command=lambda: self._insert_to_editor(
                    f'SELECT COUNT(*) FROM "{_quote_ident(tbl)}";'
                ),
            )
            menu.add_command(
                label=_t("sql.ctx_drop", "DROP TABLE ..."),
                command=lambda: self._insert_to_editor(
                    f'DROP TABLE "{_quote_ident(tbl)}";'
                ),
            )
            menu.add_command(
                label=_t("sql.ctx_schema", "Ver esquema"),
                command=lambda: self._show_table_schema(tbl),
            )

        menu.tk_popup(event.x_root, event.y_root)

    def _show_table_schema(self, tbl: str) -> None:
        cols = self._schema.get(tbl, [])
        lines = [f"-- {tbl}", f'CREATE TABLE "{_quote_ident(tbl)}" (']
        for i, col in enumerate(cols):
            pk = " PRIMARY KEY" if col.get("pk") else ""
            nn = " NOT NULL" if col.get("notnull") else ""
            df = f" DEFAULT {col['default']}" if col.get("default") else ""
            sep = "," if i < len(cols) - 1 else ""
            lines.append(
                f'  "{_quote_ident(col["name"])}" {col["type"]}{pk}{nn}{df}{sep}'
            )
        lines.append(");")
        self._set_editor_text("\n".join(lines))

    def _build_editor_area(self, t: Any, parent: tk.Widget) -> None:
        vpaned = tk.PanedWindow(
            parent, orient="vertical", bg=t.border, sashwidth=4)
        vpaned.pack(fill="both", expand=True)

        editor_frame = tk.Frame(vpaned, bg=t.panel_bg)
        vpaned.add(editor_frame, minsize=100, height=260)
        self._build_editor(t, editor_frame)

        result_frame = tk.Frame(vpaned, bg=t.panel_bg)
        vpaned.add(result_frame, minsize=80)
        self._build_results(t, result_frame)

    def _build_editor(self, t: Any, parent: tk.Widget) -> None:
        toolbar = tk.Frame(parent, bg=t.sidebar_bg)
        toolbar.pack(fill="x")

        def _btn(lbl: str, cmd: Any, accent: bool = False) -> tk.Label:
            return make_toolbar_button(
                toolbar, t, lbl, cmd, accent=accent, outer_pady=3
            )

        _btn(_t("sql.run_btn", "▶ Executar  F5"), self._run_query, accent=True)
        _btn(_t("sql.run_sel_btn", "▶ Seleção"), self._run_selection)
        _btn(_t("sql.format_btn", "⬛ Formatar"), self._format_sql)
        _btn(_t("sql.explain_btn", "📊 EXPLAIN"), self._explain_query)
        _btn(_t("sql.clear_btn", "🗑 Limpar"), self._clear_editor)
        _btn(_t("sql.history_btn", "⏱ Histórico"), self._show_history)
        _btn(_t("sql.save_btn", "💾 Salvar .sql"), self._save_sql)
        _btn(_t("sql.open_btn", "📂 Abrir .sql"), self._open_sql)

        self._editor_info = tk.Label(
            toolbar, text="", bg=t.sidebar_bg, fg=t.fg_muted, font=(FONT_UI, 7)
        )
        self._editor_info.pack(side="right", padx=8)

        editor_wrap = tk.Frame(parent, bg=t.editor_bg)
        editor_wrap.pack(fill="both", expand=True)

        self._line_nums = tk.Text(
            editor_wrap,
            width=4,
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_MONO, 10),
            relief="flat",
            state="disabled",
            padx=4,
            pady=4,
            wrap="none",
        )
        self._line_nums.pack(side="left", fill="y")

        self._editor = tk.Text(
            editor_wrap,
            bg=t.editor_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            font=(FONT_MONO, 10),
            relief="flat",
            bd=0,
            padx=8,
            pady=4,
            wrap="none",
            selectbackground=t.sel_bg,
            undo=True,
        )
        vsb = ttk.Scrollbar(editor_wrap, orient="vertical",
                            command=self._editor.yview)
        hsb = ttk.Scrollbar(parent, orient="horizontal",
                            command=self._editor.xview)
        self._editor.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.pack(side="right", fill="y")
        self._editor.pack(fill="both", expand=True)
        hsb.pack(fill="x")

        self._editor.tag_configure(
            "kw", foreground="#569cd6", font=(FONT_MONO, 10, "bold")
        )
        self._editor.tag_configure("str", foreground="#ce9178")
        self._editor.tag_configure("num", foreground="#b5cea8")
        self._editor.tag_configure(
            "cmt", foreground="#6a9955", font=(FONT_MONO, 10, "italic")
        )

        self._editor.bind("<KeyRelease>", self._on_key_release)
        self._editor.bind("<F5>", lambda _: self._run_query())
        self._editor.bind("<Control-Return>", lambda _: self._run_query())
        self._editor.bind("<Control-space>", self._show_autocomplete)
        self._editor.bind("<Tab>", self._handle_tab)

        self._ac_popup: tk.Toplevel | None = None

        self._editor.insert(
            "end",
            "-- "
            + _t("sql.editor_hint",
                 "Escreva SQL aqui. F5 ou Ctrl+Enter para executar.")
            + "\n"
            "SELECT sqlite_version();\n",
        )
        self._update_line_numbers()

    def _on_key_release(self, _e: Any = None) -> None:
        self._update_line_numbers()
        self._highlight_syntax()
        self._update_editor_info()

    def _update_line_numbers(self) -> None:
        self._line_nums.config(state="normal")
        self._line_nums.delete("1.0", "end")
        count = int(self._editor.index("end-1c").split(".")[0])
        self._line_nums.insert("end", "\n".join(str(i)
                               for i in range(1, count + 1)))
        self._line_nums.config(state="disabled")

    def _update_editor_info(self) -> None:
        idx = self._editor.index("insert")
        line, col = idx.split(".")
        total = int(self._editor.index("end-1c").split(".")[0])
        self._editor_info.config(text=f"L{line}:C{col}  |  {total} linhas")

    def _highlight_syntax(self) -> None:
        txt = self._editor
        for tag in ("kw", "str", "num", "cmt"):
            txt.tag_remove(tag, "1.0", "end")

        content = txt.get("1.0", "end")

        kw_pattern = r"\b(" + "|".join(_SQL_KW) + r")\b"
        for m in re.finditer(kw_pattern, content, re.IGNORECASE):
            txt.tag_add("kw", f"1.0+{m.start()}c", f"1.0+{m.end()}c")

        for m in re.finditer(r"'[^']*'|\"[^\"]*\"", content):
            txt.tag_add("str", f"1.0+{m.start()}c", f"1.0+{m.end()}c")

        for m in re.finditer(r"\b\d+(\.\d+)?\b", content):
            txt.tag_add("num", f"1.0+{m.start()}c", f"1.0+{m.end()}c")

        for m in re.finditer(r"--[^\n]*", content):
            txt.tag_add("cmt", f"1.0+{m.start()}c", f"1.0+{m.end()}c")

        for m in re.finditer(r"/\*.*?\*/", content, re.DOTALL):
            txt.tag_add("cmt", f"1.0+{m.start()}c", f"1.0+{m.end()}c")

    def _handle_tab(self, event: Any) -> str:
        self._editor.insert("insert", "    ")
        return "break"

    def _insert_to_editor(self, text: str) -> None:
        self._editor.insert("insert", text)
        self._editor.focus_set()
        self._on_key_release()

    def _set_editor_text(self, text: str) -> None:
        self._editor.delete("1.0", "end")
        self._editor.insert("1.0", text)
        self._on_key_release()

    def _get_editor_text(self) -> str:
        return self._editor.get("1.0", "end-1c")

    def _clear_editor(self) -> None:
        self._editor.delete("1.0", "end")
        self._on_key_release()

    def _save_sql(self) -> None:
        path = filedialog.asksaveasfilename(
            defaultextension=".sql",
            filetypes=[("SQL", "*.sql"),
                       (_t("sql.all_files", "Todos"), "*.*")],
        )
        if path:
            Path(path).write_text(self._get_editor_text(), encoding="utf-8")

    def _open_sql(self) -> None:
        path = filedialog.askopenfilename(
            filetypes=[
                ("SQL", "*.sql"),
                ("Text", "*.txt"),
                (_t("sql.all_files", "Todos"), "*.*"),
            ]
        )
        if path:
            self._set_editor_text(
                Path(path).read_text(encoding="utf-8", errors="replace")
            )

    def _show_autocomplete(self, event: Any = None) -> str:
        if self._ac_popup:
            self._ac_popup.destroy()
            self._ac_popup = None

        idx = self._editor.index("insert")
        line_text = self._editor.get(f"{idx.split('.')[0]}.0", idx)
        m = re.search(r"[\w.]+$", line_text)
        prefix = m.group(0).upper() if m else ""

        candidates: list[str] = []
        for kw in _SQL_KW:
            if kw.startswith(prefix):
                candidates.append(kw)
        for tbl in self._schema:
            if tbl.upper().startswith(prefix):
                candidates.append(tbl)
            for col in self._schema[tbl]:
                cn = col["name"]
                if cn.upper().startswith(prefix):
                    candidates.append(cn)
        candidates = sorted(set(candidates))[:20]

        if not candidates:
            return "break"

        t = self._tmgr.theme
        x = self._editor.winfo_rootx() + 40
        y = (
            self._editor.winfo_rooty()
            + int(self._editor.index("insert").split(".")[0]) * 16
        )

        popup = tk.Toplevel(self)
        popup.wm_overrideredirect(True)
        popup.geometry(f"+{x}+{y}")
        popup.config(bg=t.border)
        self._ac_popup = popup

        lb = tk.Listbox(
            popup,
            bg=t.panel_bg,
            fg=t.fg,
            font=(FONT_MONO, 9),
            selectbackground=t.accent,
            relief="flat",
            bd=1,
            activestyle="none",
            width=28,
            height=min(8, len(candidates)),
        )
        lb.pack(padx=1, pady=1)
        for c in candidates:
            lb.insert("end", c)
        lb.selection_set(0)

        def _commit(_e: Any = None) -> None:
            sel = lb.curselection()
            if not sel:
                return
            word = lb.get(sel[0])
            start = self._editor.index("insert")
            if prefix:
                col_offset = int(start.split(".")[1])
                new_col = col_offset - len(prefix)
                start = f"{start.split('.')[0]}.{max(0, new_col)}"
            self._editor.delete(start, "insert")
            self._editor.insert("insert", word)
            popup.destroy()
            self._ac_popup = None

        lb.bind("<Return>", _commit)
        lb.bind("<Tab>", _commit)
        lb.bind("<Double-1>", _commit)
        lb.bind("<Escape>", lambda _: popup.destroy())
        lb.focus_set()

        return "break"

    def _build_results(self, t: Any, parent: tk.Widget) -> None:
        result_hdr = tk.Frame(parent, bg=t.sidebar_bg)
        result_hdr.pack(fill="x")
        tk.Label(
            result_hdr,
            text=_t("sql.results", "Resultados"),
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 7, "bold"),
        ).pack(side="left", padx=8, pady=3)

        self._result_info = tk.Label(
            result_hdr, text="", bg=t.sidebar_bg, fg=t.fg_muted, font=(FONT_UI, 7)
        )
        self._result_info.pack(side="left", padx=6)

        def _exp_btn(lbl: str, cmd: Any) -> tk.Label:
            b = tk.Label(
                result_hdr,
                text=lbl,
                bg=t.sidebar_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 7),
                padx=6,
                pady=2,
                cursor="hand2",
            )
            b.pack(side="right", padx=2)
            b.bind("<Button-1>", lambda _: cmd())
            return b

        _exp_btn(_t("sql.export_csv", "📤 CSV"), self._export_csv)
        _exp_btn(_t("sql.export_json", "📤 JSON"), self._export_json)

        grid_wrap = tk.Frame(parent, bg=t.panel_bg)
        grid_wrap.pack(fill="both", expand=True)

        self._result_tree = ttk.Treeview(
            grid_wrap, show="headings", selectmode="extended"
        )
        self._result_vsb = ttk.Scrollbar(
            grid_wrap, orient="vertical", command=self._result_tree.yview
        )
        self._result_hsb = ttk.Scrollbar(
            grid_wrap, orient="horizontal", command=self._result_tree.xview
        )
        self._result_tree.configure(
            yscrollcommand=self._result_vsb.set, xscrollcommand=self._result_hsb.set
        )

        self._result_vsb.pack(side="right", fill="y")
        self._result_tree.pack(fill="both", expand=True, padx=(8, 0))
        self._result_hsb.pack(fill="x", padx=8)

        self._msg_lbl = tk.Label(
            parent,
            text="",
            bg=t.editor_bg,
            fg=t.fg_muted,
            font=(FONT_MONO, 8),
            anchor="w",
        )
        self._msg_lbl.pack(fill="x", padx=8, pady=2)

        self._result_cols: list[str] = []
        self._result_rows: list[tuple] = []

    def _show_results(self, cols: list[str], rows: list[tuple], msg: str) -> None:
        t = self._tmgr.theme
        self._result_cols = cols
        self._result_rows = rows

        ok = "linha" in msg.lower() or "ok" in msg.lower()
        err = "❌" in msg
        fg = "#a6e3a1" if ok else "#f38ba8" if err else t.fg_muted
        self._msg_lbl.config(text=msg, fg=fg)

        self._result_tree.delete(*self._result_tree.get_children())
        if not cols:
            self._result_info.config(text="")
            return

        self._result_tree["columns"] = cols
        for col in cols:
            self._result_tree.heading(
                col, text=col, command=lambda c=col: self._sort_by(c)
            )
            self._result_tree.column(col, width=120, minwidth=50, stretch=True)

        for i, row in enumerate(rows):
            tag = "even" if i % 2 == 0 else "odd"
            self._result_tree.insert(
                "",
                "end",
                values=[str(v) if v is not None else "NULL" for v in row],
                tags=(tag,),
            )

        self._result_tree.tag_configure("even", background=t.editor_bg)
        self._result_tree.tag_configure("odd", background=t.panel_bg)
        self._result_info.config(text=f"{len(rows)} × {len(cols)}")

    def _sort_by(self, col: str) -> None:
        if not self._result_rows:
            return
        try:
            idx = self._result_cols.index(col)
        except ValueError:
            return

        def _key(row: tuple) -> Any:
            v = row[idx]
            try:
                return (0, float(str(v)))
            except (ValueError, TypeError):
                return (1, str(v).lower())

        self._result_rows = sorted(self._result_rows, key=_key)
        self._show_results(
            self._result_cols, self._result_rows, self._msg_lbl.cget("text")
        )

    def _export_csv(self) -> None:
        if not self._result_rows:
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"),
                       (_t("sql.all_files", "Todos"), "*.*")],
        )
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = _csv.writer(f)
            w.writerow(self._result_cols)
            w.writerows(self._result_rows)
        self._set_status(f"CSV exportado: {path}")

    def _export_json(self) -> None:
        if not self._result_rows:
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON", "*.json"),
                       (_t("sql.all_files", "Todos"), "*.*")],
        )
        if not path:
            return
        data = [
            dict(zip(self._result_cols, row, strict=False)) for row in self._result_rows
        ]
        Path(path).write_text(
            json.dumps(data, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        self._set_status(f"JSON exportado: {path}")

    def _run_query(self) -> None:
        sql = self._get_editor_text().strip()
        if sql:
            self._execute_sql(sql)

    def _run_selection(self) -> None:
        try:
            sql = self._editor.get("sel.first", "sel.last").strip()
        except tk.TclError:
            sql = self._get_editor_text().strip()
        if sql:
            self._execute_sql(sql)

    def _explain_query(self) -> None:
        sql = self._get_editor_text().strip()
        if sql:
            self._execute_sql(f"EXPLAIN QUERY PLAN {sql}")

    def _execute_sql(self, sql: str) -> None:
        if not self._active_conn or not self._active_conn.connected:
            messagebox.showwarning(
                _t("sql.not_connected_title", "Sem conexão"),
                _t("sql.connect_first", "Conecte a um banco de dados primeiro."),
            )
            return
        if self._busy:
            return

        self._busy = True
        self._set_status(_t("sql.running", "Executando..."))
        self._history_add(sql)

        def _work() -> None:
            cols, rows, msg = self._active_conn.execute(
                sql)  # type: ignore[union-attr]
            self.after(0, lambda: self._on_query_done(cols, rows, msg))

        threading.Thread(target=_work, daemon=True).start()

    def _on_query_done(self, cols: list[str], rows: list[tuple], msg: str) -> None:
        self._busy = False
        self._show_results(cols, rows, msg)
        self._set_status(msg)
        if any(kw in msg.upper() for kw in ("CREATE", "DROP", "ALTER", "RENAME")):
            self.after(300, self._refresh_schema)

    def _format_sql(self) -> None:
        sql = self._get_editor_text()
        if not sql.strip():
            return

        kws = "|".join(_SQL_KW)
        formatted = re.sub(
            rf"\b({kws})\b",
            lambda m: m.group(0).upper(),
            sql,
            flags=re.IGNORECASE,
        )
        for kw in (
            "SELECT",
            "FROM",
            "WHERE",
            "JOIN",
            "LEFT JOIN",
            "RIGHT JOIN",
            "INNER JOIN",
            "OUTER JOIN",
            "GROUP BY",
            "ORDER BY",
            "HAVING",
            "LIMIT",
            "UNION",
            "EXCEPT",
            "INTERSECT",
            "VALUES",
        ):
            formatted = re.sub(
                rf"(?<!\n)\b{kw}\b", f"\n{kw}", formatted, flags=re.IGNORECASE
            )
        self._set_editor_text(formatted.strip())

    def _history_add(self, sql: str) -> None:
        entry = {"sql": sql, "ts": time.strftime(
            "%Y-%m-%d %H:%M:%S"), "fav": False}
        if self._history and self._history[-1]["sql"] == sql:
            return
        self._history.append(entry)
        _save_history(self._history)

    def _show_history(self) -> None:
        _HistoryWindow(self, self._tmgr, self._history,
                       on_select=self._set_editor_text)

    def _on_connect(self) -> None:
        idx = self._conn_cb.current()
        if idx < 0 or idx >= len(self._connections):
            if not self._connections:
                self._new_connection()
            return
        profile = self._connections[idx]
        self._set_status(
            _t("sql.connecting", f"Conectando a {profile.display}..."))

        def _work() -> None:
            conn = DBConnection(profile)
            try:
                conn.connect()
                self.after(0, lambda: self._on_connected(conn, profile))
            except Exception as exc:
                err = str(exc)
                self.after(0, lambda: self._on_connect_error(err))

        threading.Thread(target=_work, daemon=True).start()

    def _on_connected(self, conn: DBConnection, profile: Connection) -> None:
        if self._active_conn:
            self._active_conn.close()
        self._active_conn = conn
        self._active_profile = profile
        t = self._tmgr.theme
        self._conn_badge.config(text=f"● {profile.display}", fg=t.t_func)
        self._set_status(
            _t("sql.connected_ok", f"Conectado a {profile.display}"))
        self._refresh_schema()

    def _on_connect_error(self, err: str) -> None:
        err_lower = err.lower()
        if "connection refused" in err_lower:
            hint = _t(
                "sql.err_refused",
                "Conexão recusada - servidor não está rodando ou porta/host incorretos.",
            )
        elif "permission denied" in err_lower or "access denied" in err_lower:
            hint = _t("sql.err_access",
                      "Permissão negada - verifique usuário e senha.")
        elif "does not exist" in err_lower or "unknown database" in err_lower:
            hint = _t(
                "sql.err_no_db", "Banco não encontrado - verifique o nome do banco."
            )
        elif "no such file" in err_lower:
            hint = _t(
                "sql.err_no_file", "Arquivo não encontrado - verifique o caminho."
            )
        elif "module" in err_lower and "import" in err_lower:
            hint = _t(
                "sql.err_driver",
                "Driver não instalado - execute: pip install <driver>.",
            )
        elif "could not connect" in err_lower or "network" in err_lower:
            hint = _t(
                "sql.err_network", "Erro de rede - verifique host, porta e firewall."
            )
        else:
            hint = err
        detail = (
            (hint + "\n\n" + _t("sql.err_detail", "Detalhe:") + " " + err)
            if hint != err
            else err
        )
        self._set_status(f"\u274c {hint}")
        messagebox.showerror(
            _t("sql.error_title", "Erro de conexão"), detail, parent=self
        )

    def _on_disconnect(self) -> None:
        if self._active_conn:
            self._active_conn.close()
            self._active_conn = None
            self._active_profile = None
        t = self._tmgr.theme
        self._conn_badge.config(
            text=_t("sql.no_connection", "● sem conexão"), fg=t.fg_muted
        )
        self._schema_tree.delete(*self._schema_tree.get_children())
        self._set_status(_t("sql.disconnected", "Desconectado."))

    def _new_connection(self) -> None:
        _ConnectionDialog(self, self._tmgr, None, on_save=self._on_conn_saved)

    def _edit_connection(self) -> None:
        idx = self._conn_cb.current()
        if 0 <= idx < len(self._connections):
            _ConnectionDialog(
                self, self._tmgr, self._connections[idx], on_save=self._on_conn_saved
            )

    def _delete_connection(self) -> None:
        idx = self._conn_cb.current()
        if idx < 0 or idx >= len(self._connections):
            return
        name = self._connections[idx].name
        if messagebox.askyesno(
            _t("sql.del_conn_title", "Remover"),
            _t("sql.del_conn_confirm", f"Remover '{name}'?"),
        ):
            self._connections.pop(idx)
            _save_connections(self._connections)
            self._refresh_conn_combo()

    def _on_conn_saved(self, conn: Connection) -> None:
        existing = next(
            (i for i, c in enumerate(self._connections) if c.id == conn.id), None
        )
        if existing is not None:
            self._connections[existing] = conn
        else:
            self._connections.append(conn)
        _save_connections(self._connections)
        self._refresh_conn_combo()

    def _set_status(self, msg: str) -> None:
        self._status_var.set(msg)


class _HistoryWindow(tk.Toplevel):
    def __init__(
        self, parent: tk.Widget, tmgr: Any, history: list[dict], on_select: Any
    ) -> None:
        """Inicializa a janela de histórico de queries SQL."""
        super().__init__(parent)
        t = tmgr.theme
        self.title(_t("sql.history_title", "Histórico de Queries"))
        self.geometry("680x500")
        self.config(bg=t.panel_bg)
        safe_grab_set(self)

        hdr = tk.Frame(self, bg=t.activity_bg)
        hdr.pack(fill="x")
        tk.Label(
            hdr,
            text=_t("sql.history_title", "Histórico"),
            bg=t.activity_bg,
            fg=t.fg,
            font=(FONT_UI, 11, "bold"),
        ).pack(side="left", padx=12, pady=8)
        tk.Label(
            hdr,
            text=_t("sql.history_hint", "Duplo clique para inserir no editor"),
            bg=t.activity_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        ).pack(side="left")

        tk.Frame(self, bg=t.border, height=1).pack(fill="x")

        paned = tk.PanedWindow(self, orient="horizontal",
                               bg=t.border, sashwidth=3)
        paned.pack(fill="both", expand=True)

        list_frame = tk.Frame(paned, bg=t.panel_bg)
        paned.add(list_frame, width=300)

        lb = tk.Listbox(
            list_frame,
            bg=t.editor_bg,
            fg=t.fg,
            font=(FONT_MONO, 9),
            relief="flat",
            bd=0,
            selectbackground=t.accent,
            activestyle="none",
        )
        vsb = ttk.Scrollbar(list_frame, orient="vertical", command=lb.yview)
        lb.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        lb.pack(fill="both", expand=True)

        prev_frame = tk.Frame(paned, bg=t.panel_bg)
        paned.add(prev_frame)

        prev_txt = tk.Text(
            prev_frame,
            bg=t.editor_bg,
            fg=t.fg,
            font=(FONT_MONO, 9),
            relief="flat",
            bd=8,
            wrap="word",
            state="disabled",
        )
        prev_txt.pack(fill="both", expand=True)

        for entry in reversed(history):
            ts = entry.get("ts", "")
            sql = entry.get("sql", "").replace("\n", " ")[:60]
            fav = "⭐ " if entry.get("fav") else ""
            lb.insert("end", f"{fav}{ts}  {sql}")

        def _on_sel(_e: Any = None) -> None:
            sel = lb.curselection()
            if not sel:
                return
            idx = len(history) - 1 - sel[0]
            entry = history[idx]
            prev_txt.config(state="normal")
            prev_txt.delete("1.0", "end")
            prev_txt.insert("1.0", entry.get("sql", ""))
            prev_txt.config(state="disabled")

        def _on_double(_e: Any = None) -> None:
            sel = lb.curselection()
            if not sel:
                return
            idx = len(history) - 1 - sel[0]
            on_select(history[idx].get("sql", ""))
            self.destroy()

        lb.bind("<<ListboxSelect>>", _on_sel)
        lb.bind("<Double-1>", _on_double)

        btn_row = tk.Frame(self, bg=t.panel_bg)
        btn_row.pack(fill="x", padx=8, pady=6)
        use_b = tk.Label(
            btn_row,
            text=_t("sql.history_use", "↑ Usar no editor"),
            bg=t.accent,
            fg="#FFF",
            font=(FONT_UI, 9),
            padx=12,
            pady=5,
            cursor="hand2",
        )
        use_b.pack(side="right", padx=4)
        use_b.bind("<Button-1>", _on_double)


class _FormRowBuilder:
    """Cria labels/entries de formulário empilhados em ``grid()``, uma linha por vez.

    Usado pelo ``_ConnectionDialog`` tanto no formulário estático quanto na
    área de campos dinâmicos por driver; cada instância mantém o próprio
    contador ``row`` e é parametrizada pelo frame/tema de destino.
    """

    def __init__(self, frame: tk.Widget, theme: Any) -> None:
        self._frame = frame
        self._t = theme
        self.row = 0

    def take_row(self) -> int:
        """Retorna a linha atual e avança o contador (para grids manuais)."""
        r = self.row
        self.row += 1
        return r

    def lbl(self, text: str) -> tk.Label:
        t = self._t
        w = tk.Label(
            self._frame,
            text=text,
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
            anchor="w",
        )
        w.grid(row=self.take_row(), column=0, sticky="w", padx=16, pady=(6, 0))
        return w

    def ent(self, var: tk.Variable, show: str = "") -> tk.Entry:
        t = self._t
        e = tk.Entry(
            self._frame,
            textvariable=var,
            bg=t.editor_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            bd=0,
            font=(FONT_UI, 10),
            highlightthickness=1,
            highlightbackground=t.border,
            show=show,
        )
        e.grid(
            row=self.take_row(), column=0, sticky="ew", padx=16, pady=(1, 0), ipady=5
        )
        return e


class _ConnectionDialog(tk.Toplevel):
    def __init__(
        self, parent: tk.Widget, tmgr: Any, conn: Connection | None, on_save: Any
    ) -> None:
        """Inicializa o diálogo de configuração de conexão com banco de dados."""
        super().__init__(parent)
        t = tmgr.theme
        self._tmgr = tmgr
        self._conn = conn or Connection()
        self._on_save = on_save
        is_edit = conn is not None

        self.title(
            _t("sql.edit_conn_title", "Editar Conexão")
            if is_edit
            else _t("sql.new_conn_title", "Nova Conexão")
        )
        self.geometry("480x500")
        self.resizable(False, True)
        self.config(bg=t.panel_bg)
        safe_grab_set(self)

        self._build(t, is_edit)

        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        x = (self.winfo_screenwidth() - w) // 2
        y = (self.winfo_screenheight() - h) // 2
        self.geometry(f"480x500+{x}+{y}")

    def _build(self, t: Any, is_edit: bool) -> None:
        c = self._conn

        hdr = tk.Frame(self, bg=t.activity_bg)
        hdr.pack(fill="x")
        tk.Label(
            hdr,
            text=(
                _t("sql.edit_conn_title", "✏ Editar")
                if is_edit
                else _t("sql.new_conn_title", "🗄 Nova Conexão")
            ),
            bg=t.activity_bg,
            fg=t.fg,
            font=(FONT_UI, 11, "bold"),
        ).pack(side="left", padx=12, pady=8)
        tk.Frame(self, bg=t.border, height=1).pack(fill="x")

        form = tk.Frame(self, bg=t.panel_bg)
        form.pack(fill="both", expand=True)
        form.columnconfigure(0, weight=1)

        fb = _FormRowBuilder(form, t)

        fb.lbl(_t("sql.field_name", "Nome da conexão"))
        self._name_var = tk.StringVar(value=c.name)
        fb.ent(self._name_var)

        fb.lbl(_t("sql.field_driver", "Driver"))
        self._driver_var = tk.StringVar(value=c.driver)
        avail = [
            (
                k,
                f"{v['label']}{'  ok' if _driver_available(k) else '  (pip install ' + (v['module'] or '') + ')'}",
            )
            for k, v in DRIVERS.items()
        ]
        self._driver_keys = [k for k, _ in avail]
        driver_cb = ttk.Combobox(
            form, textvariable=self._driver_var, state="readonly", font=(FONT_UI, 9)
        )
        driver_cb["values"] = [v for _, v in avail]
        try:
            driver_cb.current(self._driver_keys.index(c.driver))
        except ValueError:
            driver_cb.current(0)
        driver_cb.grid(row=fb.take_row(), column=0,
                       sticky="ew", padx=16, pady=(1, 0))
        driver_cb.bind("<<ComboboxSelected>>", self._on_driver_changed)

        self._dyn_frame = tk.Frame(form, bg=t.panel_bg)
        self._dyn_frame.grid(row=fb.take_row(), column=0, sticky="nsew")

        self._readonly_var = tk.BooleanVar(value=c.readonly)
        tk.Checkbutton(
            form,
            text=_t("sql.field_readonly", "Somente leitura"),
            variable=self._readonly_var,
            bg=t.panel_bg,
            fg=t.fg,
            selectcolor=t.editor_bg,
            activebackground=t.panel_bg,
            font=(FONT_UI, 8),
        ).grid(row=fb.row, column=0, sticky="w", padx=16, pady=6)

        btn_row = tk.Frame(self, bg=t.panel_bg)
        btn_row.pack(fill="x", padx=16, pady=(4, 12))

        test_b = tk.Label(
            btn_row,
            text=_t("sql.test_conn", "🔗 Testar"),
            bg=t.sidebar_bg,
            fg=t.fg,
            font=(FONT_UI, 9),
            padx=10,
            pady=5,
            cursor="hand2",
        )
        test_b.pack(side="left")
        test_b.bind("<Button-1>", lambda _: self._test())

        save_b = tk.Label(
            btn_row,
            text=_t("sql.save_conn", "Salvar"),
            bg=t.accent,
            fg="#FFF",
            font=(FONT_UI, 9, "bold"),
            padx=12,
            pady=5,
            cursor="hand2",
        )
        save_b.pack(side="right", padx=4)
        save_b.bind("<Button-1>", lambda _: self._save())

        cancel_b = tk.Label(
            btn_row,
            text=_t("sql.cancel", "Cancelar"),
            bg=t.sidebar_bg,
            fg=t.fg,
            font=(FONT_UI, 9),
            padx=10,
            pady=5,
            cursor="hand2",
        )
        cancel_b.pack(side="right")
        cancel_b.bind("<Button-1>", lambda _: self.destroy())

        self._rebuild_dyn_fields(c.driver)

    def _on_driver_changed(self, _e: Any = None) -> None:
        sel_label = self._driver_var.get().split("  ")[0]
        drv = "sqlite"
        for k, v in DRIVERS.items():
            if v["label"] == sel_label:
                drv = k
                break
        self._rebuild_dyn_fields(drv)

    def _rebuild_dyn_fields(self, driver: str) -> None:
        t = self._tmgr.theme
        for w in self._dyn_frame.winfo_children():
            w.destroy()
        f = self._dyn_frame
        f.columnconfigure(0, weight=1)

        fb = _FormRowBuilder(f, t)

        c = self._conn

        if driver in ("sqlite", "duckdb"):
            label = DRIVERS[driver]["label"]
            hint = _t("sql.field_file",
                      "Arquivo .db (deixe vazio para :memory:)")
            fb.lbl(f"{label} - {hint}")
            self._filepath_var = tk.StringVar(value=c.filepath)

            file_row = tk.Frame(f, bg=t.panel_bg)
            file_row.grid(
                row=fb.take_row(), column=0, sticky="ew", padx=16, pady=(1, 0)
            )
            file_row.columnconfigure(0, weight=1)
            fe = tk.Entry(
                file_row,
                textvariable=self._filepath_var,
                bg=t.editor_bg,
                fg=t.fg,
                insertbackground=t.cursor,
                relief="flat",
                bd=0,
                font=(FONT_UI, 10),
                highlightthickness=1,
                highlightbackground=t.border,
            )
            fe.grid(row=0, column=0, sticky="ew", ipady=5)
            browse_b = tk.Label(
                file_row,
                text="📂",
                bg=t.sidebar_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 10),
                cursor="hand2",
                padx=6,
            )
            browse_b.grid(row=0, column=1, padx=(4, 0))
            browse_b.bind("<Button-1>", lambda _: self._browse_db(driver))

        else:
            fb.lbl(_t("sql.field_host", "Host"))
            self._host_var = tk.StringVar(value=c.host)
            fb.ent(self._host_var)

            fb.lbl(_t("sql.field_port", "Porta"))
            default_port = _DEFAULT_PORTS.get(driver, c.port)
            self._port_var = tk.StringVar(value=str(c.port or default_port))
            fb.ent(self._port_var)

            fb.lbl(_t("sql.field_database", "Database"))
            self._db_var = tk.StringVar(value=c.database)
            fb.ent(self._db_var)

            fb.lbl(_t("sql.field_user", "Usuário"))
            self._user_var = tk.StringVar(value=c.user)
            fb.ent(self._user_var)

            fb.lbl(_t("sql.field_password", "Senha"))
            import base64

            try:
                pw = base64.b64decode(
                    c.password).decode() if c.password else ""
            except Exception:
                pw = ""
            self._pw_var = tk.StringVar(value=pw)
            fb.ent(self._pw_var, show="●")

    def _browse_db(self, driver: str = "sqlite") -> None:
        if driver == "duckdb":
            ftypes = [
                ("DuckDB", "*.duckdb *.db"),
                (_t("sql.all_files", "Todos"), "*.*"),
            ]
        else:
            ftypes = [
                ("SQLite", "*.db *.sqlite *.sqlite3"),
                (_t("sql.all_files", "Todos"), "*.*"),
            ]
        path = filedialog.askopenfilename(filetypes=ftypes)
        if path:
            self._filepath_var.set(path)

    def _get_driver_key(self) -> str:
        label = self._driver_var.get().split("  ")[0]
        for k, v in DRIVERS.items():
            if v["label"] == label:
                return k
        return "sqlite"

    def _collect(self) -> Connection:
        import base64

        c = self._conn
        drv = self._get_driver_key()
        c.name = self._name_var.get().strip() or "Sem nome"
        c.driver = drv
        c.readonly = self._readonly_var.get()
        if drv in ("sqlite", "duckdb"):
            c.filepath = getattr(self, "_filepath_var",
                                 tk.StringVar()).get().strip()
        else:
            c.host = getattr(self, "_host_var",
                             tk.StringVar(value="localhost")).get()
            c.database = getattr(self, "_db_var", tk.StringVar()).get()
            c.user = getattr(self, "_user_var", tk.StringVar()).get()
            pw_raw = getattr(self, "_pw_var", tk.StringVar()).get()
            c.password = base64.b64encode(
                pw_raw.encode()).decode() if pw_raw else ""
            try:
                c.port = int(
                    getattr(self, "_port_var",
                            tk.StringVar(value="5432")).get()
                )
            except ValueError:
                c.port = _DEFAULT_PORTS.get(drv, 5432)
        return c

    def _test(self) -> None:
        conn = DBConnection(self._collect())
        try:
            conn.connect()
            conn.execute("SELECT 1 as ok")
            conn.close()
            messagebox.showinfo(
                _t("sql.test_ok_title", "Conexão OK"),
                _t("sql.test_ok_msg", "Conexão estabelecida com sucesso!"),
                parent=self,
            )
        except Exception as exc:
            messagebox.showerror(
                _t("sql.test_fail_title", "Falha"), str(exc), parent=self
            )

    def _save(self) -> None:
        self._on_save(self._collect())
        self.destroy()
