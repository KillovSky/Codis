"""Codis - Formatador e Validador de dados estruturados.

Formatos suportados: JSON - YAML - TOML - XML
Funcionalidades:
  - Formatar / minificar
  - Validar e exibir erros com linha/coluna
  - Converter entre formatos (JSON ↔ YAML ↔ TOML)
  - Comparar dois fragmentos (via DiffPanel)
  - Copiar resultado / salvar arquivo
  - Explorar estrutura em árvore
"""

from __future__ import annotations

import json
import logging
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Any

from src.constants import FONT_MONO, FONT_UI
from src.i18n import _t
from src.theme.utils import make_toolbar_button

logger = logging.getLogger("Codis.Formatter")


def _try_import(mod: str) -> Any:
    try:
        import importlib

        return importlib.import_module(mod)
    except ImportError:
        return None


def _parse_json(text: str) -> tuple[Any, str | None]:
    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        return None, f"JSON: {e}"


def _parse_yaml(text: str) -> tuple[Any, str | None]:
    yaml = _try_import("yaml")
    if yaml is None:
        return None, "PyYAML não instalado. Execute: pip install pyyaml"
    try:
        return yaml.safe_load(text), None
    except Exception as e:
        return None, f"YAML: {e}"


def _parse_toml(text: str) -> tuple[Any, str | None]:
    try:
        import tomllib

        return tomllib.loads(text), None
    except ImportError:
        pass
    tomli = _try_import("tomli")
    if tomli is None:
        return None, "tomli não instalado. Execute: pip install tomli"
    try:
        return tomli.loads(text), None
    except Exception as e:
        return None, f"TOML: {e}"


def _parse_xml(text: str) -> tuple[Any, str | None]:
    try:
        import xml.etree.ElementTree as ET

        root = ET.fromstring(text)
        return root, None
    except Exception as e:
        return None, f"XML: {e}"


def _to_json(obj: Any, indent: int = 2) -> str:
    return json.dumps(obj, indent=indent, ensure_ascii=False)


def _to_yaml(obj: Any) -> str:
    yaml = _try_import("yaml")
    if yaml is None:
        return "# PyYAML não instalado"
    return yaml.dump(obj, allow_unicode=True, default_flow_style=False)


def _to_toml(obj: Any) -> str:
    tomli_w = _try_import("tomli_w")
    if tomli_w is None:
        return "# tomli-w não instalado. Execute: pip install tomli-w"
    try:
        return tomli_w.dumps(obj)
    except Exception as e:
        return f"# Erro: {e}"


def _xml_to_dict(elem: Any) -> Any:
    """Converte Element XML para dict recursivamente."""
    d: dict[str, Any] = {}
    for child in elem:
        val = _xml_to_dict(child)
        if child.tag in d:
            if not isinstance(d[child.tag], list):
                d[child.tag] = [d[child.tag]]
            d[child.tag].append(val)
        else:
            d[child.tag] = val
    if elem.text and elem.text.strip():
        d["_text"] = elem.text.strip()
    if elem.attrib:
        d["_attr"] = dict(elem.attrib)
    return d or (elem.text or "")


class FormatterPanel(tk.Frame):
    """Formatador/validador de dados estruturados integrado ao Codis."""

    def __init__(self, parent: tk.Widget, tmgr: Any, cfg: Any) -> None:
        """Inicializa o painel de formatadores de código."""
        t = tmgr.theme
        super().__init__(parent, bg=t.panel_bg)
        self._tmgr = tmgr
        self._cfg = cfg
        self._fmt = "json"
        self._build(t)

    def _build(self, t: Any) -> None:
        hdr = tk.Frame(self, bg=t.activity_bg, height=52)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(
            hdr, text="{ }", bg=t.activity_bg, fg=t.accent, font=(FONT_MONO, 16, "bold")
        ).pack(side="left", padx=(12, 4), pady=8)
        tk.Label(
            hdr,
            text=_t("fmt.title", "Formatador"),
            bg=t.activity_bg,
            fg=t.fg,
            font=(FONT_UI, 13, "bold"),
        ).pack(side="left")

        self._valid_lbl = tk.Label(
            hdr, text="", bg=t.activity_bg, font=(FONT_UI, 9, "bold")
        )
        self._valid_lbl.pack(side="right", padx=12)

        tk.Frame(self, bg=t.border, height=1).pack(fill="x")

        progress_bar = tk.Frame(self, bg=t.sidebar_bg)
        progress_bar.pack(fill="x")

        tk.Label(
            progress_bar,
            text=_t("fmt.format", "Formato:"),
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        ).pack(side="left", padx=(8, 2), pady=6)
        self._fmt_var = tk.StringVar(value="JSON")
        fmt_cb = ttk.Combobox(
            progress_bar,
            textvariable=self._fmt_var,
            state="readonly",
            width=6,
            font=(FONT_UI, 8),
            values=["JSON", "YAML", "TOML", "XML"],
        )
        fmt_cb.pack(side="left", pady=6)
        fmt_cb.bind("<<ComboboxSelected>>", self._on_fmt_change)

        def _btn(lbl: str, cmd: Any, accent: bool = False) -> tk.Label:
            return make_toolbar_button(
                progress_bar, t, lbl, cmd, accent=accent, inner_pady=5
            )

        _btn(_t("fmt.validate", "Validar"), self._validate, accent=True)
        _btn(_t("fmt.format_btn", "⬛ Formatar"), self._format)
        _btn(_t("fmt.minify", "⬜ Minificar"), self._minify)
        _btn(_t("fmt.open_file", "📂 Abrir"), self._open_file)
        _btn(_t("fmt.save", "💾 Salvar"), self._save_file)
        _btn(_t("fmt.copy", "📋 Copiar"), self._copy)

        tk.Label(
            progress_bar,
            text=_t("fmt.convert_to", "->"),
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        ).pack(side="left", padx=(8, 2))
        self._conv_var = tk.StringVar(value="YAML")
        conv_cb = ttk.Combobox(
            progress_bar,
            textvariable=self._conv_var,
            state="readonly",
            width=5,
            font=(FONT_UI, 8),
            values=["JSON", "YAML", "TOML"],
        )
        conv_cb.pack(side="left", pady=6)
        _btn(_t("fmt.convert", "Converter"), self._convert)

        tk.Frame(self, bg=t.border, height=1).pack(fill="x")

        body = tk.Frame(self, bg=t.panel_bg)
        body.pack(fill="both", expand=True)

        left = tk.Frame(body, bg=t.panel_bg)
        left.pack(side="left", fill="both", expand=True)

        editor_hdr = tk.Frame(left, bg=t.sidebar_bg)
        editor_hdr.pack(fill="x")
        tk.Label(
            editor_hdr,
            text=_t("fmt.editor", "Editor"),
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 7, "bold"),
        ).pack(side="left", padx=8, pady=3)
        self._line_lbl = tk.Label(
            editor_hdr, text="", bg=t.sidebar_bg, fg=t.fg_muted, font=(FONT_UI, 7)
        )
        self._line_lbl.pack(side="right", padx=8)

        self._editor = tk.Text(
            left,
            bg=t.editor_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            font=(FONT_MONO, 10),
            relief="flat",
            bd=8,
            wrap="none",
            selectbackground=t.sel_bg,
            undo=True,
        )
        vsb = ttk.Scrollbar(left, orient="vertical",
                            command=self._editor.yview)
        hsb = ttk.Scrollbar(left, orient="horizontal",
                            command=self._editor.xview)
        self._editor.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.pack(side="right", fill="y")
        hsb.pack(side="bottom", fill="x")
        self._editor.pack(fill="both", expand=True)
        self._editor.bind("<KeyRelease>", self._on_key)
        self._editor.bind("<Button-1>", self._update_line_lbl)

        tk.Frame(body, bg=t.border, width=1).pack(side="left", fill="y")

        right = tk.Frame(body, bg=t.panel_bg, width=260)
        right.pack(side="right", fill="y")
        right.pack_propagate(False)

        tree_hdr = tk.Frame(right, bg=t.sidebar_bg)
        tree_hdr.pack(fill="x")
        tk.Label(
            tree_hdr,
            text=_t("fmt.tree", "Estrutura"),
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 7, "bold"),
        ).pack(side="left", padx=8, pady=3)
        refresh_l = tk.Label(
            tree_hdr,
            text="↻",
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
            cursor="hand2",
        )
        refresh_l.pack(side="right", padx=6)
        refresh_l.bind("<Button-1>", lambda _: self._refresh_tree())

        self._tree = ttk.Treeview(right, show="tree", selectmode="browse")
        tree_vsb = ttk.Scrollbar(
            right, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=tree_vsb.set)
        tree_vsb.pack(side="right", fill="y")
        self._tree.pack(fill="both", expand=True)

        self._err_bar = tk.Label(
            self, text="", bg="#3a1a1a", fg="#f38ba8", font=(FONT_UI, 8), anchor="w"
        )

    def _on_key(self, _e: Any = None) -> None:
        self._update_line_lbl()

    def _update_line_lbl(self, _e: Any = None) -> None:
        idx = self._editor.index("insert")
        line, col = idx.split(".")
        self._line_lbl.config(text=f"L{line}:C{col}")

    def _on_fmt_change(self, _e: Any = None) -> None:
        self._fmt = self._fmt_var.get().lower()

    def _get_text(self) -> str:
        return self._editor.get("1.0", "end-1c")

    def _set_text(self, text: str) -> None:
        self._editor.delete("1.0", "end")
        self._editor.insert("1.0", text)
        self._editor.edit_reset()

    def _parse(self) -> tuple[Any, str | None]:
        text = self._get_text()
        fmt = self._fmt_var.get().lower()
        if fmt == "json":
            return _parse_json(text)
        if fmt == "yaml":
            return _parse_yaml(text)
        if fmt == "toml":
            return _parse_toml(text)
        if fmt == "xml":
            return _parse_xml(text)
        return None, f"Formato desconhecido: {fmt}"

    def _show_error(self, msg: str) -> None:
        self._err_bar.config(text=f"  ✖  {msg}")
        self._err_bar.pack(fill="x")
        self._valid_lbl.config(
            text=_t("formatter.invalid", "✖ Inválido"), fg="#f38ba8")

    def _hide_error(self) -> None:
        self._err_bar.pack_forget()

    def _validate(self) -> None:
        obj, err = self._parse()
        if err:
            self._show_error(err)
        else:
            self._hide_error()
            self._valid_lbl.config(
                text=_t("fmt.valid", "Valido"), fg="#a6e3a1")
            self._refresh_tree(obj)

    def _format(self) -> None:
        obj, err = self._parse()
        if err:
            self._show_error(err)
            return
        self._hide_error()
        fmt = self._fmt_var.get().lower()
        if fmt == "json":
            result = _to_json(obj)
        elif fmt == "yaml":
            result = _to_yaml(obj)
        elif fmt == "toml":
            result = _to_toml(obj)
        else:
            import xml.etree.ElementTree as ET

            result = ET.tostring(obj, encoding="unicode",
                                 xml_declaration=False)
        self._set_text(result)
        self._valid_lbl.config(
            text=_t("fmt.formatted", "Formatado"), fg="#a6e3a1")
        self._refresh_tree(obj)

    def _minify(self) -> None:
        obj, err = self._parse()
        if err:
            self._show_error(err)
            return
        self._hide_error()
        fmt = self._fmt_var.get().lower()
        if fmt == "json":
            result = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
        elif fmt == "yaml":
            yaml = _try_import("yaml")
            result = (
                yaml.dump(obj, default_flow_style=True, allow_unicode=True)
                if yaml
                else _to_yaml(obj)
            )
        else:
            result = self._get_text()
        self._set_text(result)

    def _convert(self) -> None:
        obj, err = self._parse()
        if err:
            self._show_error(err)
            return
        import xml.etree.ElementTree as ET

        if isinstance(obj, ET.Element):
            obj = _xml_to_dict(obj)

        target = self._conv_var.get().lower()
        if target == "json":
            result = _to_json(obj)
        elif target == "yaml":
            result = _to_yaml(obj)
        elif target == "toml":
            result = _to_toml(obj)
        else:
            result = self._get_text()

        self._set_text(result)
        self._fmt_var.set(target.upper())
        self._fmt = target
        self._hide_error()
        self._valid_lbl.config(text=f"→ {target.upper()}", fg="#89b4fa")

    def _open_file(self) -> None:
        path = filedialog.askopenfilename(
            filetypes=[
                ("JSON", "*.json"),
                ("YAML", "*.yaml *.yml"),
                ("TOML", "*.toml"),
                ("XML", "*.xml"),
                ("All", "*.*"),
            ]
        )
        if not path:
            return
        text = Path(path).read_text(encoding="utf-8", errors="replace")
        self._set_text(text)
        ext = Path(path).suffix.lower()
        if ".json" in ext:
            self._fmt_var.set("JSON")
            self._fmt = "json"
        elif ext in (".yaml", ".yml"):
            self._fmt_var.set("YAML")
            self._fmt = "yaml"
        elif ".toml" in ext:
            self._fmt_var.set("TOML")
            self._fmt = "toml"
        elif ".xml" in ext:
            self._fmt_var.set("XML")
            self._fmt = "xml"
        self._hide_error()

    def _save_file(self) -> None:
        fmt = self._fmt_var.get().lower()
        ext_map = {"json": "*.json", "yaml": "*.yaml",
                   "toml": "*.toml", "xml": "*.xml"}
        path = filedialog.asksaveasfilename(
            defaultextension=f".{fmt}",
            filetypes=[(fmt.upper(), ext_map.get(fmt, "*.*")), ("All", "*.*")],
        )
        if path:
            Path(path).write_text(self._get_text(), encoding="utf-8")

    def _copy(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self._get_text())

    def _refresh_tree(self, obj: Any = None) -> None:
        if obj is None:
            obj, err = self._parse()
            if err or obj is None:
                return
        import xml.etree.ElementTree as ET

        if isinstance(obj, ET.Element):
            obj = _xml_to_dict(obj)
        self._tree.delete(*self._tree.get_children())
        self._build_tree("", obj, depth=0)

    def _build_tree(self, parent: str, obj: Any, depth: int) -> None:
        if depth > 12:
            return
        if isinstance(obj, dict):
            for k, v in list(obj.items())[:200]:
                label = (
                    f"📁 {k}" if isinstance(
                        v, (dict, list)) else f"  {k}: {_short(v)}"
                )
                node = self._tree.insert(
                    parent, "end", text=label, open=depth < 2)
                if isinstance(v, (dict, list)):
                    self._build_tree(node, v, depth + 1)
        elif isinstance(obj, list):
            for i, v in enumerate(obj[:200]):
                label = (
                    f"[{i}]" if isinstance(
                        v, (dict, list)) else f"  [{i}]: {_short(v)}"
                )
                node = self._tree.insert(
                    parent, "end", text=label, open=depth < 1)
                if isinstance(v, (dict, list)):
                    self._build_tree(node, v, depth + 1)


def _short(v: Any) -> str:
    s = str(v)
    return s[:60] + "..." if len(s) > 60 else s
