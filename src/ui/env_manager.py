"""Codis - Gerenciador de Variáveis de Ambiente.

Funcionalidades:
  - Visualizar todas as variáveis do processo atual
  - Adicionar / editar / remover variáveis para sessão corrente
  - Perfis de env (.env): criar, importar, exportar, aplicar
  - Pesquisa e filtro
  - Geração de snippets export/setx/PowerShell
  - Diff entre o env atual e um perfil
"""

from __future__ import annotations

import contextlib
import logging
import os
import tkinter as tk
import tkinter.simpledialog as _sd
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from src.constants import CONFIG_DIR, FONT_MONO, FONT_UI
from src.i18n import _t
from src.theme.utils import make_toolbar_button
from src.ui.grab_safe import safe_grab_set

logger = logging.getLogger("Codis.EnvManager")

_PROFILES_DIR = CONFIG_DIR / "env_profiles"


def _load_dotenv(text: str) -> dict[str, str]:
    """Parseia um arquivo .env simples."""
    result: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key:
                result[key] = val
    return result


def _dump_dotenv(d: dict[str, str]) -> str:
    return "\n".join(f'{k}="{v}"' for k, v in sorted(d.items())) + "\n"


class EnvManagerPanel(tk.Frame):
    """Gerenciador de variáveis de ambiente integrado ao Codis."""

    def __init__(self, parent: tk.Widget, tmgr: Any, cfg: Any) -> None:
        """Inicializa o painel de gerenciamento de variáveis de ambiente."""
        t = tmgr.theme
        super().__init__(parent, bg=t.panel_bg)
        self._tmgr = tmgr
        self._cfg = cfg
        self._current_env: dict[str, str] = dict(os.environ)
        self._session_overrides: dict[str, str] = {}
        self._profiles_dir = _PROFILES_DIR
        self._profiles_dir.mkdir(parents=True, exist_ok=True)
        self._build(t)
        self._refresh()

    def _build(self, t: Any) -> None:
        hdr = tk.Frame(self, bg=t.activity_bg, height=52)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text="⚙", bg=t.activity_bg, fg=t.accent, font=(FONT_UI, 18)).pack(
            side="left", padx=(12, 4), pady=8
        )
        tk.Label(
            hdr,
            text=_t("env.title", "Variáveis de Ambiente"),
            bg=t.activity_bg,
            fg=t.fg,
            font=(FONT_UI, 12, "bold"),
        ).pack(side="left")
        self._count_lbl = tk.Label(
            hdr, text="", bg=t.activity_bg, fg=t.fg_muted, font=(FONT_UI, 8)
        )
        self._count_lbl.pack(side="right", padx=12)
        tk.Frame(self, bg=t.border, height=1).pack(fill="x")

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)

        self._tab_env = tk.Frame(nb, bg=t.panel_bg)
        self._tab_profiles = tk.Frame(nb, bg=t.panel_bg)
        self._tab_snippets = tk.Frame(nb, bg=t.panel_bg)

        nb.add(self._tab_env, text=_t("env.tab_current", "  Processo  "))
        nb.add(self._tab_profiles, text=_t(
            "env.tab_profiles", "  .env Perfis  "))
        nb.add(self._tab_snippets, text=_t("env.tab_snippets", "  Snippets  "))

        self._build_env_tab(t)
        self._build_profiles_tab(t)
        self._build_snippets_tab(t)

    def _build_env_tab(self, t: Any) -> None:
        p = self._tab_env

        toolbar_fr = tk.Frame(p, bg=t.panel_bg)
        toolbar_fr.pack(fill="x", padx=8, pady=6)

        self._search_var = tk.StringVar()
        srch = tk.Entry(
            toolbar_fr,
            textvariable=self._search_var,
            bg=t.editor_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            bd=4,
            font=(FONT_UI, 9),
            width=22,
            highlightthickness=1,
            highlightbackground=t.border,
        )
        srch.pack(side="left")
        srch.bind("<KeyRelease>", lambda _: self._refresh())
        tk.Label(
            toolbar_fr, text="🔍", bg=t.panel_bg, fg=t.fg_muted, font=(FONT_UI, 9)
        ).pack(side="left", padx=2)

        def _tb(lbl: str, cmd: Any, accent: bool = False) -> tk.Label:
            return make_toolbar_button(
                toolbar_fr, t, lbl, cmd, accent=accent, side="right", outer_pady=0
            )

        _tb(_t("env.add", "+ Adicionar"), self._add_var, accent=True)
        _tb(_t("env.edit", "✏ Editar"), self._edit_var)
        _tb(_t("env.delete", "🗑 Remover"), self._delete_var)
        _tb(_t("env.refresh", "↻ Atualizar"), self._reload_env)

        cols = ("key", "value", "source")
        self._tree = ttk.Treeview(
            p, columns=cols, show="headings", selectmode="browse")
        self._tree.heading("key", text=_t("env.col_key", "Variável"))
        self._tree.heading("value", text=_t("env.col_value", "Valor"))
        self._tree.heading("source", text=_t("env.col_source", "Origem"))
        self._tree.column("key", width=200, minwidth=100)
        self._tree.column("value", width=360, minwidth=100)
        self._tree.column("source", width=80, minwidth=50)

        even_bg = t.editor_bg
        self._tree.tag_configure(
            "override",
            foreground="#f9e2af",
            background=t.panel_bg,
            font=(FONT_MONO, 9, "bold"),
        )
        self._tree.tag_configure(
            "system", foreground=t.fg, background=t.panel_bg)
        self._tree.tag_configure("even", foreground=t.fg, background=even_bg)

        vsb = ttk.Scrollbar(p, orient="vertical", command=self._tree.yview)
        hsb = ttk.Scrollbar(p, orient="horizontal", command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.pack(side="right", fill="y")
        self._tree.pack(fill="both", expand=True, padx=(8, 0))
        hsb.pack(fill="x", padx=8)

        self._tree.bind("<Double-1>", lambda _: self._edit_var())

        val_bar = tk.Frame(p, bg=t.sidebar_bg)
        val_bar.pack(fill="x")
        tk.Label(
            val_bar,
            text=_t("env.selected", "Valor:"),
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 7),
        ).pack(side="left", padx=8, pady=4)
        self._val_var = tk.StringVar()
        tk.Label(
            val_bar,
            textvariable=self._val_var,
            bg=t.sidebar_bg,
            fg=t.fg,
            font=(FONT_MONO, 8),
            anchor="w",
        ).pack(side="left", fill="x", expand=True, padx=4, pady=4)
        copy_l = tk.Label(
            val_bar,
            text="📋",
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
            cursor="hand2",
        )
        copy_l.pack(side="right", padx=8)
        copy_l.bind("<Button-1>", lambda _: self._copy_value())
        self._tree.bind("<<TreeviewSelect>>", self._on_select)

    def _build_profiles_tab(self, t: Any) -> None:
        p = self._tab_profiles

        toolbar_fr = tk.Frame(p, bg=t.panel_bg)
        toolbar_fr.pack(fill="x", padx=8, pady=6)
        tk.Label(
            toolbar_fr,
            text=_t("env.profiles_hint", "Perfis .env salvos:"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        ).pack(side="left")

        def _tb(lbl: str, cmd: Any, accent: bool = False) -> tk.Label:
            return make_toolbar_button(
                toolbar_fr, t, lbl, cmd, accent=accent, side="right", outer_pady=0
            )

        _tb(_t("env.new_profile", "＋ Novo perfil"),
            self._new_profile, accent=True)
        _tb(_t("env.import_profile", "📥 Importar"), self._import_profile)
        _tb(_t("env.apply_profile", "▶ Aplicar"), self._apply_profile)
        _tb(_t("env.export_profile", "📤 Exportar"), self._export_profile)
        _tb(_t("env.delete_profile", "🗑"), self._delete_profile)

        self._profiles_lb = tk.Listbox(
            p,
            bg=t.editor_bg,
            fg=t.fg,
            font=(FONT_MONO, 9),
            relief="flat",
            bd=0,
            selectbackground=t.accent,
            activestyle="none",
        )
        vsb = ttk.Scrollbar(p, orient="vertical",
                            command=self._profiles_lb.yview)
        vsb.pack(side="right", fill="y", padx=(0, 8))
        self._profiles_lb.pack(fill="both", expand=True,
                               padx=(8, 0), pady=(0, 4))
        self._profiles_lb.bind("<<ListboxSelect>>", self._on_profile_select)

        prev_hdr = tk.Frame(p, bg=t.sidebar_bg)
        prev_hdr.pack(fill="x")
        tk.Label(
            prev_hdr,
            text=_t("env.preview", "Preview:"),
            bg=t.sidebar_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 7, "bold"),
        ).pack(side="left", padx=8, pady=3)

        self._preview_txt = tk.Text(
            p,
            bg=t.editor_bg,
            fg=t.fg,
            font=(FONT_MONO, 8),
            relief="flat",
            bd=4,
            height=8,
            state="disabled",
        )
        self._preview_txt.pack(fill="x", padx=8, pady=(0, 6))

        self._refresh_profiles_list()

    def _build_snippets_tab(self, t: Any) -> None:
        p = self._tab_snippets

        toolbar_fr = tk.Frame(p, bg=t.panel_bg)
        toolbar_fr.pack(fill="x", padx=8, pady=6)
        tk.Label(
            toolbar_fr,
            text=_t(
                "env.snippets_hint",
                "Selecione variáveis e gere snippets de exportação:",
            ),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 8),
        ).pack(side="left")

        self._snippet_fmt = tk.StringVar(value="bash")
        for label, val in [
            ("bash export", "bash"),
            ("Windows setx", "setx"),
            ("PowerShell", "ps1"),
            (".env", "dotenv"),
        ]:
            rb = tk.Radiobutton(
                toolbar_fr,
                text=label,
                variable=self._snippet_fmt,
                value=val,
                bg=t.panel_bg,
                fg=t.fg,
                selectcolor=t.editor_bg,
                activebackground=t.panel_bg,
                font=(FONT_UI, 8),
                command=self._generate_snippet,
            )
            rb.pack(side="left", padx=4)

        gen_b = tk.Label(
            toolbar_fr,
            text=_t("env.generate", "Gerar →"),
            bg=t.accent,
            fg="#FFF",
            font=(FONT_UI, 8),
            padx=8,
            pady=4,
            cursor="hand2",
        )
        gen_b.pack(side="right")
        gen_b.bind("<Button-1>", lambda _: self._generate_snippet())

        self._snippet_txt = tk.Text(
            p,
            bg=t.editor_bg,
            fg=t.fg,
            font=(FONT_MONO, 9),
            relief="flat",
            bd=8,
            wrap="none",
            state="disabled",
            selectbackground=t.sel_bg,
        )
        vsb = ttk.Scrollbar(p, orient="vertical",
                            command=self._snippet_txt.yview)
        hsb = ttk.Scrollbar(p, orient="horizontal",
                            command=self._snippet_txt.xview)
        self._snippet_txt.configure(
            yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.pack(side="right", fill="y")
        hsb.pack(side="bottom", fill="x")
        self._snippet_txt.pack(fill="both", expand=True, padx=8)

        btn_row = tk.Frame(p, bg=t.panel_bg)
        btn_row.pack(fill="x", padx=8, pady=4)
        copy_b = tk.Label(
            btn_row,
            text="📋 " + _t("env.copy", "Copiar"),
            bg=t.sidebar_bg,
            fg=t.fg,
            font=(FONT_UI, 8),
            padx=8,
            pady=4,
            cursor="hand2",
        )
        copy_b.pack(side="right")
        copy_b.bind(
            "<Button-1>",
            lambda _: (
                self.clipboard_clear(),
                self.clipboard_append(self._snippet_txt.get("1.0", "end-1c")),
            ),
        )

    def _reload_env(self) -> None:
        self._current_env = dict(os.environ)
        self._refresh()

    def _refresh(self) -> None:
        q = self._search_var.get().lower()
        all_vars = {**self._current_env, **self._session_overrides}
        filtered = {
            k: v
            for k, v in sorted(all_vars.items())
            if not q or q in k.lower() or q in v.lower()
        }

        self._tree.delete(*self._tree.get_children())
        for i, (k, v) in enumerate(filtered.items()):
            is_override = k in self._session_overrides
            source = "sessão" if is_override else "sistema"
            tag = "override" if is_override else (
                "even" if i % 2 == 0 else "system")
            display_val = v[:200] + ("..." if len(v) > 200 else "")
            self._tree.insert(
                "", "end", iid=k, values=(k, display_val, source), tags=(tag,)
            )
        self._count_lbl.config(text=f"{len(filtered)} variáveis")

    def _on_select(self, _e: Any = None) -> None:
        sel = self._tree.selection()
        if not sel:
            return
        key = sel[0]
        all_vars = {**self._current_env, **self._session_overrides}
        self._val_var.set(all_vars.get(key, ""))

    def _copy_value(self) -> None:
        val = self._val_var.get()
        self.clipboard_clear()
        self.clipboard_append(val)

    def _add_var(self) -> None:
        _EnvVarDialog(self, self._tmgr, None, None, self._on_var_saved)

    def _edit_var(self) -> None:
        sel = self._tree.selection()
        if not sel:
            return
        key = sel[0]
        all_vars = {**self._current_env, **self._session_overrides}
        val = all_vars.get(key, "")
        _EnvVarDialog(self, self._tmgr, key, val, self._on_var_saved)

    def _on_var_saved(self, key: str, val: str) -> None:
        self._session_overrides[key] = val
        os.environ[key] = val
        self._refresh()

    def _delete_var(self) -> None:
        sel = self._tree.selection()
        if not sel:
            return
        key = sel[0]
        if not messagebox.askyesno(
            _t("env.delete_title", "Remover variável"),
            _t("env.delete_confirm",
               f"Remover '{key}' do ambiente de sessão?"),
        ):
            return
        self._session_overrides.pop(key, None)
        with contextlib.suppress(KeyError):
            del os.environ[key]
        self._refresh()

    def _refresh_profiles_list(self) -> None:
        self._profiles_lb.delete(0, "end")
        for p in sorted(self._profiles_dir.glob("*.env")):
            self._profiles_lb.insert("end", p.stem)

    def _on_profile_select(self, _e: Any = None) -> None:
        sel = self._profiles_lb.curselection()
        if not sel:
            return
        name = self._profiles_lb.get(sel[0])
        path = self._profiles_dir / f"{name}.env"
        if path.exists():
            content = path.read_text(encoding="utf-8")
            self._preview_txt.config(state="normal")
            self._preview_txt.delete("1.0", "end")
            self._preview_txt.insert("1.0", content)
            self._preview_txt.config(state="disabled")

    def _new_profile(self) -> None:
        name = tk.simpledialog.askstring(
            _t("env.new_profile_title", "Novo perfil"),
            _t("env.new_profile_name", "Nome do perfil:"),
            parent=self,
        )
        if not name:
            return
        path = self._profiles_dir / f"{name}.env"
        path.write_text(_dump_dotenv(
            self._session_overrides), encoding="utf-8")
        self._refresh_profiles_list()

    def _import_profile(self) -> None:
        path = filedialog.askopenfilename(
            filetypes=[(".env", "*.env"), ("All", "*.*")])
        if not path:
            return
        dest = self._profiles_dir / Path(path).name
        dest.write_text(Path(path).read_text(
            encoding="utf-8"), encoding="utf-8")
        self._refresh_profiles_list()

    def _apply_profile(self) -> None:
        sel = self._profiles_lb.curselection()
        if not sel:
            return
        name = self._profiles_lb.get(sel[0])
        path = self._profiles_dir / f"{name}.env"
        if not path.exists():
            return
        data = _load_dotenv(path.read_text(encoding="utf-8"))
        for k, v in data.items():
            os.environ[k] = v
            self._session_overrides[k] = v
        self._reload_env()
        messagebox.showinfo(
            _t("env.applied_title", "Perfil aplicado"),
            _t("env.applied_msg",
               f"{len(data)} variáveis carregadas de '{name}'."),
        )

    def _export_profile(self) -> None:
        sel = self._profiles_lb.curselection()
        if not sel:
            return
        name = self._profiles_lb.get(sel[0])
        src = self._profiles_dir / f"{name}.env"
        dst = filedialog.asksaveasfilename(
            defaultextension=".env",
            initialfile=f"{name}.env",
            filetypes=[(".env", "*.env"), ("All", "*.*")],
        )
        if dst:
            Path(dst).write_text(src.read_text(
                encoding="utf-8"), encoding="utf-8")

    def _delete_profile(self) -> None:
        sel = self._profiles_lb.curselection()
        if not sel:
            return
        name = self._profiles_lb.get(sel[0])
        if messagebox.askyesno(
            _t("env.delete_prof_title", "Remover perfil"),
            _t("env.delete_prof_confirm", f"Remover o perfil '{name}'?"),
        ):
            (self._profiles_dir / f"{name}.env").unlink(missing_ok=True)
            self._refresh_profiles_list()

    def _generate_snippet(self) -> None:
        fmt = self._snippet_fmt.get()
        all_vars = {**self._current_env, **self._session_overrides}
        src = self._session_overrides if self._session_overrides else all_vars

        lines: list[str] = []
        if fmt == "bash":
            for k, v in sorted(src.items()):
                lines.append(f'export {k}="{v}"')
        elif fmt == "setx":
            for k, v in sorted(src.items()):
                lines.append(f'setx {k} "{v}"')
        elif fmt == "ps1":
            for k, v in sorted(src.items()):
                lines.append(f'$env:{k} = "{v}"')
        elif fmt == "dotenv":
            for k, v in sorted(src.items()):
                lines.append(f'{k}="{v}"')

        snippet = "\n".join(lines)
        self._snippet_txt.config(state="normal")
        self._snippet_txt.delete("1.0", "end")
        self._snippet_txt.insert("1.0", snippet)
        self._snippet_txt.config(state="disabled")


class _EnvVarDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Widget,
        tmgr: Any,
        key: str | None,
        val: str | None,
        on_save: Any,
    ) -> None:
        """Inicializa o diálogo de adição/edição de variável de ambiente."""
        super().__init__(parent)
        t = tmgr.theme
        self.title(_t("env.edit_var_title", "Variável de Ambiente"))
        self.config(bg=t.panel_bg)
        self.resizable(False, False)
        safe_grab_set(self)
        self._on_save = on_save

        pad = {"padx": 16, "pady": 6}

        tk.Label(
            self,
            text=_t("env.var_key", "Nome:"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
        ).pack(anchor="w", **pad)
        self._key_var = tk.StringVar(value=key or "")
        tk.Entry(
            self,
            textvariable=self._key_var,
            bg=t.editor_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            bd=4,
            font=(FONT_MONO, 10),
            width=34,
        ).pack(**pad)

        tk.Label(
            self,
            text=_t("env.var_value", "Valor:"),
            bg=t.panel_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
        ).pack(anchor="w", **pad)
        self._val_var = tk.StringVar(value=val or "")
        tk.Entry(
            self,
            textvariable=self._val_var,
            bg=t.editor_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            relief="flat",
            bd=4,
            font=(FONT_MONO, 10),
            width=34,
        ).pack(**pad)

        btn_row = tk.Frame(self, bg=t.panel_bg)
        btn_row.pack(fill="x", padx=16, pady=(4, 12))
        ok_b = tk.Label(
            btn_row,
            text=_t("env.save", "Salvar"),
            bg=t.accent,
            fg="#FFF",
            font=(FONT_UI, 9),
            padx=12,
            pady=5,
            cursor="hand2",
        )
        ok_b.pack(side="right", padx=4)
        ok_b.bind("<Button-1>", lambda _: self._save())
        cancel_b = tk.Label(
            btn_row,
            text=_t("env.cancel", "Cancelar"),
            bg=t.sidebar_bg,
            fg=t.fg,
            font=(FONT_UI, 9),
            padx=12,
            pady=5,
            cursor="hand2",
        )
        cancel_b.pack(side="right")
        cancel_b.bind("<Button-1>", lambda _: self.destroy())

        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        x = (self.winfo_screenwidth() - w) // 2
        y = (self.winfo_screenheight() - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _save(self) -> None:
        k = self._key_var.get().strip()
        v = self._val_var.get()
        if not k:
            return
        self._on_save(k, v)
        self.destroy()


tk.simpledialog = _sd  # type: ignore[attr-defined]
