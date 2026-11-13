"""Codis - Code Runner Configurável.

Permite executar arquivos em qualquer linguagem com configurações
customizáveis por linguagem: comando, argumentos extras, variáveis de ambiente.

Features
--------
- 30+ linguagens pré-configuradas
- Suporte a comandos customizados por usuário
- Variáveis de ambiente configuráveis
- Timeout configurável
- Histórico de execuções com timestamps
- Diálogo de gerenciamento completo
"""

from __future__ import annotations

import json
import logging
import shutil
import sys
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from typing import Any

from src.constants import CONFIG_DIR, FONT_MONO, FONT_UI
from src.i18n import _t
from src.pyexec import NoPythonError, is_frozen, python_executable
from src.ui.grab_safe import safe_grab_set

logger = logging.getLogger("Codis")

_RUNNER_CONFIG_FILE = CONFIG_DIR / "code_runners.json"

_PY_SENTINEL = "{python}"


def _resolve_cmd(cmd: str) -> str:
    """Troca ``{python}`` por um Python real ('' se não houver nenhum)."""
    if cmd != _PY_SENTINEL:
        return cmd
    try:
        return python_executable()
    except NoPythonError:
        return ""

_BUILTIN_RUNNERS: dict[str, dict[str, Any]] = {
    "python": {
        "cmd": sys.executable if not is_frozen() else _PY_SENTINEL,
        "args": ["{file}"],
        "label": "Python",
        "ext": [".py", ".pyw"],
    },
    "javascript": {
        "cmd": "node",
        "args": ["{file}"],
        "label": "JavaScript",
        "ext": [".js", ".mjs"],
    },
    "typescript": {
        "cmd": "ts-node",
        "args": ["{file}"],
        "label": "TypeScript",
        "ext": [".ts"],
    },
    "ruby": {"cmd": "ruby", "args": ["{file}"], "label": "Ruby", "ext": [".rb"]},
    "php": {"cmd": "php", "args": ["{file}"], "label": "PHP", "ext": [".php"]},
    "go": {"cmd": "go", "args": ["run", "{file}"], "label": "Go", "ext": [".go"]},
    "rust": {
        "cmd": "cargo",
        "args": ["script", "{file}"],
        "label": "Rust",
        "ext": [".rs"],
    },
    "java": {"cmd": "java", "args": ["{stem}"], "label": "Java", "ext": [".java"]},
    "kotlin": {"cmd": "kotlin", "args": ["{file}"], "label": "Kotlin", "ext": [".kt"]},
    "swift": {"cmd": "swift", "args": ["{file}"], "label": "Swift", "ext": [".swift"]},
    "lua": {"cmd": "lua", "args": ["{file}"], "label": "Lua", "ext": [".lua"]},
    "perl": {"cmd": "perl", "args": ["{file}"], "label": "Perl", "ext": [".pl", ".pm"]},
    "bash": {
        "cmd": "bash",
        "args": ["{file}"],
        "label": "Bash",
        "ext": [".sh", ".bash"],
    },
    "powershell": {
        "cmd": "powershell",
        "args": ["-File", "{file}"],
        "label": "PowerShell",
        "ext": [".ps1"],
    },
    "r": {"cmd": "Rscript", "args": ["{file}"], "label": "R", "ext": [".r", ".R"]},
    "julia": {"cmd": "julia", "args": ["{file}"], "label": "Julia", "ext": [".jl"]},
    "dart": {
        "cmd": "dart",
        "args": ["run", "{file}"],
        "label": "Dart",
        "ext": [".dart"],
    },
    "scala": {"cmd": "scala", "args": ["{file}"], "label": "Scala", "ext": [".scala"]},
    "elixir": {
        "cmd": "elixir",
        "args": ["{file}"],
        "label": "Elixir",
        "ext": [".ex", ".exs"],
    },
    "haskell": {
        "cmd": "runhaskell",
        "args": ["{file}"],
        "label": "Haskell",
        "ext": [".hs"],
    },
    "c": {
        "cmd": "gcc",
        "args": ["{file}", "-o", "{stem}", "&&", "./{stem}"],
        "label": "C (gcc)",
        "ext": [".c"],
        "shell": True,
    },
    "cpp": {
        "cmd": "g++",
        "args": ["{file}", "-o", "{stem}", "&&", "./{stem}"],
        "label": "C++ (g++)",
        "ext": [".cpp", ".cxx"],
        "shell": True,
    },
}


def _expand_args(args: list[str], file_path: Path) -> list[str]:
    """Substitui placeholders {file}, {stem}, {dir} nos argumentos."""
    result = []
    for arg in args:
        arg = arg.replace("{file}", str(file_path))
        arg = arg.replace("{stem}", str(file_path.stem))
        arg = arg.replace("{dir}", str(file_path.parent))
        arg = arg.replace("{name}", file_path.name)
        result.append(arg)
    return result


class CodeRunnerStore:
    """Gerencia configuração dos runners (builtin + customizados)."""

    def __init__(self) -> None:
        """Inicializa a store de configurações de runners de código."""
        self._custom: dict[str, dict[str, Any]] = {}
        self._overrides: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not _RUNNER_CONFIG_FILE.exists():
            return
        try:
            raw = json.loads(_RUNNER_CONFIG_FILE.read_text(encoding="utf-8"))
            self._custom = raw.get(_t("runner.type_custom"), {})
            self._overrides = raw.get("overrides", {})
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("code_runners.json corrompido: %s", exc)

    def save(self) -> None:
        """Persiste a configuração do runner no arquivo de configuração."""
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            data = {"custom": self._custom, "overrides": self._overrides}
            _RUNNER_CONFIG_FILE.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("Falha ao salvar code_runners.json: %s", exc)

    def all_runners(self) -> dict[str, dict[str, Any]]:
        """Retorna todos os runners (builtin + override + custom)."""
        merged: dict[str, dict[str, Any]] = {}
        merged.update(_BUILTIN_RUNNERS)
        merged.update(self._overrides)
        merged.update(self._custom)
        return merged

    def get_runner(self, lang: str) -> dict[str, Any] | None:
        """Retorna o runner mais adequado para a extensão de arquivo fornecida."""
        runners = self.all_runners()
        return runners.get(lang)

    def set_override(self, lang: str, runner: dict[str, Any]) -> None:
        """Define um runner customizado para uma extensão."""
        self._overrides[lang] = runner
        self.save()

    def add_custom(self, lang_id: str, runner: dict[str, Any]) -> None:
        """Registra um novo runner customizado."""
        self._custom[lang_id] = runner
        self.save()

    def remove_custom(self, lang_id: str) -> None:
        """Remove um runner customizado pelo nome."""
        self._custom.pop(lang_id, None)
        self._overrides.pop(lang_id, None)
        self.save()

    def reset_override(self, lang: str) -> None:
        """Remove o override customizado de uma extensão."""
        self._overrides.pop(lang, None)
        self.save()

    def is_available(self, lang: str) -> bool:
        """Verifica se o executável do runner está disponível no PATH."""
        runner = self.get_runner(lang)
        if not runner:
            return False
        cmd = _resolve_cmd(runner.get("cmd", ""))
        return bool(cmd) and bool(shutil.which(cmd))

    def build_command(self, lang: str, file_path: Path) -> list[str] | None:
        """Constrói o comando de execução para o arquivo especificado."""
        runner = self.get_runner(lang)
        if not runner:
            return None
        cmd = _resolve_cmd(runner["cmd"])
        if not cmd:
            return None
        args = _expand_args(runner.get("args", ["{file}"]), file_path)
        if runner.get("shell"):
            full = [cmd] + args
            return full
        return [cmd] + args


class CodeRunnerDialog(tk.Toplevel):
    """Diálogo de configuração dos Code Runners.

    Permite visualizar, editar e adicionar runners de linguagem.
    """

    def __init__(
        self,
        parent: tk.Widget,
        tmgr: Any,
        store: CodeRunnerStore,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        """Inicializa o diálogo de configuração de runner de código."""
        t = tmgr.theme
        super().__init__(parent)
        self.title(_t("runner.title"))
        self.configure(bg=t.floating_bg)
        self.geometry("780x540")
        self.minsize(600, 400)
        self.transient(parent)
        safe_grab_set(self)

        self._tmgr = tmgr
        self._store = store
        self._on_change = on_change

        self._build(t)
        self._populate()
        self._center(parent)

    def _build(self, t: Any) -> None:
        tk.Frame(self, bg=t.accent, height=2).pack(fill="x")

        hdr = tk.Frame(self, bg=t.floating_bg)
        hdr.pack(fill="x", padx=14, pady=(10, 4))
        tk.Label(
            hdr,
            text=_t("runner.window_title"),
            bg=t.floating_bg,
            fg=t.t_func,
            font=(FONT_UI, 12, "bold"),
        ).pack(side="left")

        add_btn = tk.Label(
            hdr,
            text=_t("runner.new_btn"),
            bg=t.btn_primary,
            fg=t.btn_primary_fg,
            font=(FONT_UI, 8, "bold"),
            cursor="hand2",
            padx=8,
            pady=3,
            relief="flat",
        )
        add_btn.pack(side="right")
        add_btn.bind("<Button-1>", lambda _: self._new_runner())

        sf = tk.Frame(self, bg=t.floating_bg)
        sf.pack(fill="x", padx=14, pady=(0, 4))
        tk.Label(
            sf, text="🔍", bg=t.floating_bg, fg=t.fg_muted, font=(FONT_UI, 10)
        ).pack(side="left")
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._populate())
        tk.Entry(
            sf,
            textvariable=self._search_var,
            bg=t.input_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            bd=0,
            font=(FONT_UI, 10),
            highlightthickness=1,
            highlightbackground=t.border,
        ).pack(side="left", fill="x", expand=True, padx=4, pady=2)

        body = tk.Frame(self, bg=t.floating_bg)
        body.pack(fill="both", expand=True, padx=14, pady=4)

        left = tk.Frame(body, bg=t.floating_bg)
        left.pack(side="left", fill="both", expand=True)

        cols = tk.Frame(left, bg=t.floating_bg)
        cols.pack(fill="x")
        for text, w in (
            (_t("runner.col_lang"), 20),
            (_t("runner.col_cmd"), 16),
            (_t("runner.col_status"), 10),
            (_t("runner.col_type"), 10),
        ):
            tk.Label(
                cols,
                text=text,
                bg=t.floating_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 8, "bold"),
                width=w,
                anchor="w",
            ).pack(side="left")

        vsb = tk.Scrollbar(left, orient="vertical", bg=t.scroll_thumb)
        self._listbox = tk.Listbox(
            left,
            bg=t.editor_bg,
            fg=t.fg,
            selectbackground=t.sel_bg,
            selectforeground=t.sel_fg,
            font=(FONT_MONO, 9),
            relief="flat",
            bd=0,
            activestyle="none",
            highlightthickness=0,
            yscrollcommand=vsb.set,
        )
        vsb.config(command=self._listbox.yview)
        vsb.pack(side="right", fill="y")
        self._listbox.pack(fill="both", expand=True)
        self._listbox.bind("<<ListboxSelect>>", self._on_select)
        self._listbox.bind("<Double-Button-1>",
                           lambda _: self._edit_selected())

        sep = tk.Frame(body, bg=t.border, width=1)
        sep.pack(side="left", fill="y", padx=8)

        right_w = 260
        right = tk.Frame(body, bg=t.editor_bg, width=right_w)
        right.pack(side="left", fill="y")
        right.pack_propagate(False)

        tk.Label(
            right,
            text=_t("runner.detail_title"),
            bg=t.tabbar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8, "bold"),
            anchor="w",
            padx=8,
            pady=3,
        ).pack(fill="x")

        self._detail_text = tk.Text(
            right,
            bg=t.editor_bg,
            fg=t.fg,
            font=(FONT_MONO, 8),
            relief="flat",
            bd=0,
            state="disabled",
            padx=8,
            pady=6,
            wrap="word",
            cursor="arrow",
        )
        self._detail_text.pack(fill="both", expand=True)

        detail_btns = tk.Frame(right, bg=t.editor_bg)
        detail_btns.pack(fill="x", pady=4)

        self._edit_btn = tk.Label(
            detail_btns,
            text=_t("runner.edit_btn"),
            bg=t.btn_bg,
            fg=t.fg,
            font=(FONT_UI, 8),
            cursor="hand2",
            padx=6,
            pady=3,
            relief="flat",
        )
        self._edit_btn.pack(side="left", padx=4)
        self._edit_btn.bind("<Button-1>", lambda _: self._edit_selected())

        self._reset_btn = tk.Label(
            detail_btns,
            text=_t("runner.reset_btn"),
            bg=t.btn_bg,
            fg=t.fg,
            font=(FONT_UI, 8),
            cursor="hand2",
            padx=6,
            pady=3,
            relief="flat",
        )
        self._reset_btn.pack(side="left", padx=2)
        self._reset_btn.bind("<Button-1>", lambda _: self._reset_selected())

        self._remove_btn = tk.Label(
            detail_btns,
            text="🗑",
            bg=t.btn_bg,
            fg="#ef5350",
            font=(FONT_UI, 8),
            cursor="hand2",
            padx=6,
            pady=3,
            relief="flat",
        )
        self._remove_btn.pack(side="right", padx=4)
        self._remove_btn.bind("<Button-1>", lambda _: self._remove_selected())

        tk.Frame(self, bg=t.border, height=1).pack(fill="x", padx=14)

        foot = tk.Frame(self, bg=t.floating_bg)
        foot.pack(fill="x", padx=14, pady=8)
        self._status_lbl = tk.Label(
            foot,
            text="",
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        )
        self._status_lbl.pack(side="left")

        tk.Label(
            foot,
            text=_t("runner.close"),
            bg=t.btn_bg,
            fg=t.fg,
            font=(FONT_UI, 9),
            cursor="hand2",
            padx=12,
            pady=4,
            relief="flat",
        ).pack(side="right")
        foot.winfo_children()[-1].bind("<Button-1>", lambda _: self.destroy())

        self._row_ids: list[str] = []
        self._selected_id: str | None = None

    def _populate(self) -> None:
        q = self._search_var.get().lower()
        self._listbox.delete(0, "end")
        self._row_ids.clear()

        runners = self._store.all_runners()
        custom_ids = set(self._store._custom.keys())
        override_ids = set(self._store._overrides.keys())

        for lang_id, runner in sorted(
            runners.items(), key=lambda x: x[1].get("label", x[0])
        ):
            label = runner.get("label", lang_id)
            if q and q not in label.lower() and q not in lang_id.lower():
                continue

            cmd = runner.get("cmd", "?")
            available = self._store.is_available(lang_id)
            status = "disponivel" if available else "ausente"
            kind = (
                "custom"
                if lang_id in custom_ids
                else (
                    _t("runner.type_override")
                    if lang_id in override_ids
                    else _t("runner.type_builtin")
                )
            )

            line = f"  {label:<22}  {cmd:<18}  {status:<8}  {kind}"
            self._listbox.insert("end", line)
            self._row_ids.append(lang_id)

            idx = len(self._row_ids) - 1
            t = self._tmgr.theme
            if not available:
                self._listbox.itemconfig(idx, fg=t.fg_muted)
            elif lang_id in custom_ids:
                self._listbox.itemconfig(idx, fg=t.t_string)
            elif lang_id in override_ids:
                self._listbox.itemconfig(idx, fg=t.t_func)

        total = len(runners)
        avail = sum(1 for lid in runners if self._store.is_available(lid))
        self._status_lbl.config(
            text=_t("runner.available_count", avail=avail, total=total)
        )

    def _on_select(self, _e: Any = None) -> None:
        sel = self._listbox.curselection()
        if not sel or sel[0] >= len(self._row_ids):
            return
        self._selected_id = self._row_ids[sel[0]]
        self._update_detail()

    def _update_detail(self) -> None:
        if not self._selected_id:
            return
        runner = self._store.get_runner(self._selected_id)
        if not runner:
            return
        lines = [
            _t("runner.detail_lang", label=runner.get("label", self._selected_id)),
            _t("runner.detail_id", id=self._selected_id),
            "",
            _t("runner.detail_cmd", cmd=runner.get("cmd", "?")),
            _t("runner.detail_args", args=" ".join(runner.get("args", []))),
            "",
        ]

        ext = runner.get("ext", [])
        if ext:
            lines.append(_t("runner.detail_ext", ext=", ".join(ext)))

        env = runner.get("env", {})
        if env:
            lines.append("")
            lines.append(_t("runner.detail_env"))
            for k, v in env.items():
                lines.append(f"  {k}={v}")

        available = self._store.is_available(self._selected_id)
        lines.append("")
        lines.append(
            _t("runner.detail_status_ok")
            if available
            else _t("runner.detail_status_err")
        )

        self._detail_text.config(state="normal")
        self._detail_text.delete("1.0", "end")
        self._detail_text.insert("1.0", "\n".join(lines))
        self._detail_text.config(state="disabled")

    def _edit_selected(self) -> None:
        if not self._selected_id:
            return
        runner = self._store.get_runner(self._selected_id) or {}
        self._open_edit_dialog(self._selected_id, dict(runner))

    def _reset_selected(self) -> None:
        if not self._selected_id:
            return
        self._store.reset_override(self._selected_id)
        self._populate()
        self._update_detail()

    def _remove_selected(self) -> None:
        if not self._selected_id:
            return
        custom_ids = set(self._store._custom.keys())
        if self._selected_id not in custom_ids:
            return
        self._store.remove_custom(self._selected_id)
        self._selected_id = None
        self._populate()
        self._detail_text.config(state="normal")
        self._detail_text.delete("1.0", "end")
        self._detail_text.config(state="disabled")

    def _new_runner(self) -> None:
        self._open_edit_dialog(
            None,
            {
                "label": _t("runner.new_default_label"),
                "cmd": "",
                "args": ["{file}"],
                "ext": [],
            },
        )

    def _open_edit_dialog(self, lang_id: str | None, runner: dict[str, Any]) -> None:
        t = self._tmgr.theme
        dlg = tk.Toplevel(self)
        dlg.title(_t("runner.edit_title"))
        dlg.configure(bg=t.floating_bg)
        dlg.geometry("480x420")
        dlg.transient(self)
        safe_grab_set(dlg)
        dlg.resizable(False, False)

        tk.Label(
            dlg,
            text=_t("runner.configure_title"),
            bg=t.floating_bg,
            fg=t.t_func,
            font=(FONT_UI, 11, "bold"),
        ).pack(padx=14, pady=(12, 6), anchor="w")

        ew = {
            "bg": t.input_bg,
            "fg": t.fg,
            "insertbackground": t.cursor,
            "relief": "flat",
            "bd": 0,
            "font": (FONT_MONO, 10),
            "highlightthickness": 1,
            "highlightbackground": t.border,
        }

        vars_: dict[str, tk.StringVar] = {}

        fields = [
            ("id", _t("runner.field_id"), lang_id or ""),
            ("label", _t("runner.field_label"), runner.get("label", "")),
            ("cmd", _t("runner.field_cmd"), runner.get("cmd", "")),
            ("args", _t("runner.field_args"), " ".join(
                runner.get("args", ["{file}"]))),
            ("ext", _t("runner.field_ext"), " ".join(runner.get("ext", []))),
            (
                "env",
                _t("runner.field_env"),
                " ".join(f"{k}={v}" for k, v in runner.get("env", {}).items()),
            ),
        ]

        for key, label, default in fields:
            row = tk.Frame(dlg, bg=t.floating_bg)
            row.pack(fill="x", padx=14, pady=3)
            tk.Label(
                row,
                text=label + ":",
                bg=t.floating_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 8),
                width=24,
                anchor="w",
            ).pack(side="left")
            var = tk.StringVar(value=default)
            vars_[key] = var
            entry = tk.Entry(row, textvariable=var, **ew, width=28)
            entry.pack(side="left", fill="x", expand=True)

            if key == "id" and lang_id:
                entry.config(state="readonly", readonlybackground=t.editor_bg)

        hints = tk.Label(
            dlg,
            text=_t("runner.placeholder_hint"),
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 7),
            wraplength=440,
            justify="left",
        )
        hints.pack(padx=14, pady=(4, 8), anchor="w")

        def _save() -> None:
            lid = vars_["id"].get().strip()
            if not lid:
                return
            new_runner: dict[str, Any] = {
                "label": vars_["label"].get().strip() or lid,
                "cmd": vars_["cmd"].get().strip(),
                "args": vars_["args"].get().strip().split(),
                "ext": vars_["ext"].get().strip().split(),
            }
            env_raw = vars_["env"].get().strip()
            if env_raw:
                env: dict[str, str] = {}
                for pair in env_raw.split():
                    if "=" in pair:
                        k, v = pair.split("=", 1)
                        env[k] = v
                new_runner["env"] = env

            if lang_id is None:
                self._store.add_custom(lid, new_runner)
            else:
                self._store.set_override(lid, new_runner)

            if self._on_change:
                self._on_change()
            self._populate()
            dlg.destroy()

        btn_row = tk.Frame(dlg, bg=t.floating_bg)
        btn_row.pack(pady=10)
        for text, cmd, primary in (
            (_t("runner.save"), _save, True),
            (_t("runner.cancel"), dlg.destroy, False),
        ):
            btn = tk.Label(
                btn_row,
                text=text,
                bg=t.btn_primary if primary else t.btn_bg,
                fg=t.btn_primary_fg if primary else t.fg,
                font=(FONT_UI, 9, "bold" if primary else "normal"),
                cursor="hand2",
                padx=12,
                pady=4,
                relief="flat",
            )
            btn.pack(side="left", padx=4)
            btn.bind("<Button-1>", lambda _, c=cmd: c())

        dlg.update_idletasks()
        px = self.winfo_rootx() + (self.winfo_width() - dlg.winfo_reqwidth()) // 2
        py = self.winfo_rooty() + (self.winfo_height() - dlg.winfo_reqheight()) // 2
        dlg.geometry(f"+{px}+{py}")

    def _center(self, parent: tk.Widget) -> None:
        self.update_idletasks()
        try:
            px, py = parent.winfo_rootx(), parent.winfo_rooty()
            pw, ph = parent.winfo_width(), parent.winfo_height()
        except tk.TclError:
            return
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry(f"+{px + (pw - w) // 2}+{py + (ph - h) // 2}")
