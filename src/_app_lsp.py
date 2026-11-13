"""Mixin ``_AppLspMixin`` - integração do protocolo LSP (Language Server Protocol).

Gerencia o ciclo de vida dos language servers, roteamento de diagnósticos,
e a abertura do painel LSP na interface do Codis.
"""

from __future__ import annotations

import contextlib
import logging
import subprocess
import sys
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from src.constants import LANG_LABEL
from src.i18n import _t
from src.lint.manager import Diag

if TYPE_CHECKING:
    pass

logger = logging.getLogger("Codis")

__all__ = ["_AppLspMixin"]


class _AppLspMixin:
    """Mixin de Lsp do CodisApp."""

    def _on_cursor_change(self, line: int, col: int) -> None:
        """Chamado quando a posição do cursor muda."""
        self._status.set_cursor(line, col)
        e = self._active_editor()
        if e:
            with contextlib.suppress(tk.TclError):
                if e.text.tag_ranges(tk.SEL):
                    sel = e.text.get(tk.SEL_FIRST, tk.SEL_LAST)
                    self._status.set_selection(len(sel), sel.count("\n") + 1)
                else:
                    self._status.set_selection(0, 0)
            self._fire_hook("cursor_move", (line, col))

            if hasattr(e, "_rhythm") and e._rhythm is not None:
                try:
                    m = e._rhythm.metrics()
                    self._status.set_rhythm(m.flow_state, m.wpm)
                except Exception:
                    logger.debug("Suppressed error", exc_info=True)
            if self._bus is not None:
                self._bus.emit(
                    "editor.cursor_move",
                    {
                        "line": line,
                        "col": col,
                        "path": str(e.path) if e.path else "",
                        "lang": getattr(e, "lang", ""),
                    },
                    source="CodisApp",
                )

    def _on_editor_title(self, name: str, modified: bool) -> None:
        """Chamado quando o título do editor (nome do arquivo) muda."""
        if 0 <= self._active_idx < len(self._editors):
            self._tabbar.update_tab(self._active_idx, name, modified)
        self._update_title()

    def _on_lsp_diagnostics(self, lang: str, uri: str, lsp_diags: list) -> None:
        """Recebe diagnósticos do LSP e os encaminha ao editor e painéis."""

        if uri.startswith("file://"):
            file_path = uri[7:]
            if sys.platform == "win32" and file_path.startswith("/"):
                file_path = file_path[1:]

                if len(file_path) >= 2 and file_path[1] == ":":
                    file_path = file_path[0].upper() + file_path[1:]
        else:
            file_path = uri

        lint_diags: list[Diag] = []
        for d in lsp_diags:
            with contextlib.suppress(tk.TclError):
                rng = getattr(d, "range", None)
                if rng:
                    start_pos = getattr(rng, "start", rng)
                    line_0 = getattr(start_pos, "line", 0)
                    col_0 = getattr(start_pos, "character", 0)
                    end_pos = getattr(rng, "end", start_pos)
                    end_c = getattr(end_pos, "character", col_0)
                else:
                    line_0 = 0
                    col_0 = 0
                    end_c = 0
                lint_diags.append(
                    Diag(
                        line=line_0 + 1,
                        col=col_0,
                        end_col=end_c,
                        severity=getattr(d, "severity", 2),
                        message=getattr(d, "message", str(d)),
                        rule=str(getattr(d, "code", "") or ""),
                        source=getattr(d, "source", lang) or lang,
                    )
                )

        for e in self._editors:
            if e.path and str(e.path) == file_path:
                with contextlib.suppress(tk.TclError):
                    e.deliver_diags(lint_diags)
                    e.minimap.set_lint_lines(lint_diags)
                break

        ae = self._active_editor()
        if ae and ae.path and str(ae.path) == file_path:
            self._status.set_diags(lint_diags)

        with contextlib.suppress(tk.TclError):
            open_paths = [str(ed.path) for ed in self._editors if ed.path]
            self._problems_panel.set_open_files(open_paths)
            self._problems_panel.set_diags(lint_diags, file=file_path)
            self._sidebar_problems.set_open_files(open_paths)
            self._sidebar_problems.set_diags(lint_diags, file=file_path)
            errors = sum(1 for d in lint_diags if d.severity == 1)
            warns = sum(1 for d in lint_diags if d.severity == 2)
            if errors + warns > 0:
                self._actbar.set_badge("problems", errors + warns)
            if self._kui:
                with contextlib.suppress(tk.TclError):
                    self._kui.notify_diagnostics(errors, warns)

        if lint_diags:
            with contextlib.suppress(OSError):
                fname = Path(file_path).name if file_path else uri
                self._output_panel.write(
                    f"🔤 LSP [{lang}] {len(lint_diags)} diagnostic(s) in {fname}",
                    "LSP",
                    "info",
                    timestamp=False,
                )

    def _on_lsp_log(self, lang: str, message: str) -> None:
        """Encaminha mensagens de log do servidor LSP para o painel de saída."""
        with contextlib.suppress(tk.TclError):
            self._output_panel.write(
                f"[LSP:{lang}] {message}", "LSP", "info", timestamp=False
            )

    def _on_lsp_apply_edit(self, edit) -> None:
        """Aplica um WorkspaceEdit recebido do servidor LSP (ex: rename, refactor)."""
        try:
            doc_changes = getattr(edit, "document_changes", [])
            for change in doc_changes:
                if not isinstance(change, dict):
                    continue
                uri = change.get("textDocument", {}).get("uri", "")
                edits = change.get("edits", [])
                self._apply_lsp_text_edits(uri, edits)

            for uri, text_edits in (getattr(edit, "changes", None) or {}).items():
                self._apply_lsp_text_edits(uri, text_edits)
        except Exception as exc:
            logger.warning("_on_lsp_apply_edit falhou: %s", exc)

    def _apply_lsp_text_edits(self, uri: str, edits) -> None:
        """Aplica uma lista de TextEdit (objetos ou dicts) ao editor correspondente."""
        if not uri or not edits:
            return

        file_path = uri[7:] if uri.startswith("file://") else uri
        if sys.platform == "win32" and file_path.startswith("/"):
            file_path = file_path[1:]
        for ed in self._editors:
            if ed.path and str(ed.path) == file_path:
                with contextlib.suppress(tk.TclError):
                    ed.apply_lsp_edits(edits)
                return

    def _open_lsp_panel(self) -> None:
        """Abre o painel de controle LSP - status, enable/disable por linguagem."""
        if not self._lsp_mgr:
            from tkinter import messagebox as _mb

            _mb.showinfo(
                "LSP", "LSP não está disponível nesta sessão.", parent=self._root
            )
            return

        t = self._tmgr.theme
        dlg = tk.Toplevel(self._root)
        dlg.title("LSP - Servidores de Linguagem")
        dlg.configure(bg=t.floating_bg)
        dlg.geometry("620x500")
        dlg.minsize(500, 380)
        dlg.transient(self._root)

        tk.Frame(dlg, bg=t.accent, height=2).pack(fill="x")
        hdr = tk.Frame(dlg, bg=t.floating_bg)
        hdr.pack(fill="x", padx=14, pady=(10, 4))
        tk.Label(
            hdr,
            text=_t("lsp.panel_title"),
            bg=t.floating_bg,
            fg=t.t_func,
            font=("Segoe UI", 12, "bold"),
        ).pack(side="left")

        btn_kw = {
            "relief": "flat",
            "cursor": "hand2",
            "font": ("Segoe UI", 8),
            "padx": 8,
            "pady": 3,
        }
        tk.Button(
            hdr,
            text=_t("lsp.btn_enable_all"),
            bg=t.btn_primary,
            fg=t.btn_primary_fg,
            command=lambda: _enable_all(),
            **btn_kw,
        ).pack(
            side="right", padx=2
        )  # pylint: disable=unnecessary-lambda
        tk.Button(
            hdr,
            text=_t("lsp.btn_disable_all"),
            bg=t.btn_bg,
            fg=t.fg,
            command=lambda: _disable_all(),
            **btn_kw,
        ).pack(
            side="right", padx=2
        )  # pylint: disable=unnecessary-lambda

        lgd = tk.Frame(dlg, bg=t.floating_bg)
        lgd.pack(fill="x", padx=14, pady=(0, 6))
        for sym, color, label in [
            ("●", "#69ff47", _t("lsp.status_active")),
            ("●", "#ff9100", _t("lsp.status_starting")),
            ("●", "#546e7a", _t("lsp.status_available")),
            ("●", "#ff5252", _t("lsp.status_disabled")),
            ("○", "#2d3040", _t("lsp.status_none")),
        ]:
            tk.Label(
                lgd,
                text=f"{sym} {label}",
                bg=t.floating_bg,
                fg=color,
                font=("Segoe UI", 8),
            ).pack(side="left", padx=6)

        container = tk.Frame(dlg, bg=t.floating_bg)
        container.pack(fill="both", expand=True, padx=14, pady=4)

        canvas = tk.Canvas(container, bg=t.floating_bg, highlightthickness=0)
        scrollbar = tk.Scrollbar(
            container, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=t.floating_bg)

        inner.bind(
            "<Configure>", lambda _: canvas.configure(
                scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        row_vars: dict[str, tk.BooleanVar] = {}

        def _refresh_rows() -> None:
            for w in inner.winfo_children():
                w.destroy()
            row_vars.clear()

            status = self._lsp_mgr.status()
            all_langs = sorted(
                set(
                    list(status.keys())
                    + (
                        [self._active_editor().lang]
                        if self._active_editor()
                        and self._active_editor().lang != "text"
                        else []
                    )
                )
            )

            for i, lang in enumerate(all_langs):
                st = status.get(lang, "unavailable")
                color = {
                    "running": "#69ff47",
                    "starting": "#ff9100",
                    "available": "#546e7a",
                    "disabled": "#ff5252",
                    "stopped": "#ff9100",
                    "unavailable": "#2d3040",
                }.get(st, "#2d3040")

                row_bg = t.editor_bg if i % 2 == 0 else t.floating_bg
                row = tk.Frame(inner, bg=row_bg)
                row.pack(fill="x", pady=1)

                tk.Label(
                    row, text="●", bg=row_bg, fg=color, font=("Segoe UI", 10), width=2
                ).pack(side="left", padx=(4, 0))

                label_text = LANG_LABEL.get(lang, lang.capitalize())
                tk.Label(
                    row,
                    text=label_text,
                    bg=row_bg,
                    fg=t.fg,
                    font=("Segoe UI", 9),
                    width=18,
                    anchor="w",
                ).pack(side="left", padx=4)

                status_text = {
                    "running": "Ativo",
                    "starting": "Iniciando...",
                    "available": "Disponível",
                    "disabled": "Desabilitado",
                    "stopped": "Parado",
                    "unavailable": "Sem servidor",
                }.get(st, st)
                tk.Label(
                    row,
                    text=status_text,
                    bg=row_bg,
                    fg=color,
                    font=("Segoe UI", 8),
                    width=14,
                ).pack(side="left")

                hint = self._lsp_mgr.install_hint(lang)
                if hint and st == "unavailable":
                    tk.Label(
                        row,
                        text=f"  instalar: {hint[:40]}",
                        bg=row_bg,
                        fg=t.fg_muted,
                        font=("Courier New", 7),
                        anchor="w",
                    ).pack(side="left")

                enabled = st not in ("disabled", "unavailable")
                var = tk.BooleanVar(value=enabled)
                row_vars[lang] = var

                def _make_toggle(lg: str) -> Callable:
                    def _toggle() -> None:
                        if self._lsp_mgr.is_lang_enabled(lg):
                            self._lsp_mgr.disable_lang(lg)
                            self._status.notify(f"LSP {lg}: desabilitado")
                        else:
                            self._lsp_mgr.enable_lang(lg)
                            self._status.notify(f"LSP {lg}: habilitado")
                        dlg.after(600, _refresh_rows)

                    return _toggle

                def _make_restart(lg: str) -> Callable:
                    def _restart() -> None:
                        self._lsp_mgr.restart_lang(lg)
                        self._status.notify(f"LSP {lg}: reiniciando...")

                        def _rewire() -> None:
                            for ed in self._editors:
                                if ed.path and ed.lang == lg:
                                    with contextlib.suppress(Exception):
                                        content = ed.text.get("1.0", "end-1c")
                                        self._lsp_mgr.did_open(
                                            ed.path, lg, content)
                                        ed.autocomplete.set_lsp_manager(
                                            self._lsp_mgr, ed.path
                                        )

                        dlg.after(1500, _rewire)
                        dlg.after(1600, _refresh_rows)

                    return _restart

                if st != "unavailable":
                    btn_label = "⏹" if enabled else "▶"
                    tk.Button(
                        row,
                        text=btn_label,
                        bg=t.btn_bg,
                        fg=t.fg,
                        relief="flat",
                        cursor="hand2",
                        font=("Segoe UI", 9),
                        command=_make_toggle(lang),
                    ).pack(side="right", padx=2)

                if st in ("running", "starting", "stopped"):
                    tk.Button(
                        row,
                        text="↺",
                        bg=t.btn_bg,
                        fg=t.fg,
                        relief="flat",
                        cursor="hand2",
                        font=("Segoe UI", 9),
                        command=_make_restart(lang),
                    ).pack(side="right", padx=2)

        def _enable_all() -> None:
            for lang in list(self._lsp_mgr._disabled_langs):
                self._lsp_mgr.enable_lang(lang)
            _refresh_rows()

        def _disable_all() -> None:
            status = self._lsp_mgr.status()
            for lang, st in status.items():
                if st in ("running", "starting", "available", "stopped"):
                    self._lsp_mgr.disable_lang(lang)
            _refresh_rows()

        _refresh_rows()

        foot = tk.Frame(dlg, bg=t.floating_bg)
        foot.pack(fill="x", padx=14, pady=(4, 10))
        tk.Button(
            foot,
            text=_t("lsp.btn_refresh"),
            bg=t.btn_bg,
            fg=t.fg,
            relief="flat",
            cursor="hand2",
            font=("Segoe UI", 9),
            padx=12,
            pady=4,
            command=_refresh_rows,
        ).pack(side="left")
        tk.Button(
            foot,
            text=_t("ui.close"),
            bg=t.btn_primary,
            fg=t.btn_primary_fg,
            relief="flat",
            cursor="hand2",
            font=("Segoe UI", 9),
            padx=12,
            pady=4,
            command=dlg.destroy,
        ).pack(side="right")

    def _on_lint_result(self, diags: list[Diag]) -> None:
        """Recebe resultados do lint e os distribui ao editor, painéis e output."""
        e = self._active_editor()
        if e:
            e.deliver_diags(diags)
            e.minimap.set_lint_lines(diags)
        self._status.set_diags(diags)

        open_paths = [str(ed.path) for ed in self._editors if ed.path]
        file_str = str(e.path) if e and e.path else ""
        self._problems_panel.set_open_files(open_paths)
        self._problems_panel.set_diags(diags, file=file_str)
        with contextlib.suppress(tk.TclError):
            self._sidebar_problems.set_open_files(open_paths)
            self._sidebar_problems.set_diags(diags, file=file_str)

        errors = warns = 0
        for d in diags:
            if d.severity == 1:
                errors += 1
            elif d.severity == 2:
                warns += 1

        self._actbar.set_badge("problems", errors + warns)
        if self._kui:
            with contextlib.suppress(tk.TclError):
                self._kui.notify_diagnostics(errors, warns)
        self._fire_hook("lint_complete", diags)

        if not (diags and e and e.path):
            return

        fname = e.path.name
        by_source: dict[str, list[Diag]] = {}
        for d in diags:
            by_source.setdefault(d.source or "", []).append(d)

        _source_channel = {
            "eslint": "ESLint",
            "pyflakes": "Python",
            "flake8": "Python",
            "mypy": "Python",
            "py_compile": "Python",
        }
        for src, src_diags in by_source.items():
            channel = _source_channel.get(src, "Codis")
            self._output_panel.write(
                f"-- {fname} --", channel, "info", timestamp=True)
            for d in src_diags:
                lvl = "error" if d.is_error else "warn"
                rule = f"  [{d.rule}]" if d.rule else ""
                self._output_panel.write(
                    f"  {d.icon} L{d.line}:{d.col}  {d.message}{rule}", channel, lvl
                )
            e_cnt = sum(1 for d in src_diags if d.is_error)
            w_cnt = len(src_diags) - e_cnt
            self._output_panel.write(
                f"  {len(src_diags)} problema(s) - {e_cnt} erro(s), {w_cnt} aviso(s)",
                channel,
                "info" if e_cnt == 0 else "error",
            )

    def _apply_problem_fix(self, file_path: str, diag: Diag) -> None:
        """Aplica o fix de um único diagnóstico no editor correspondente ao arquivo.

        Percorre os editores abertos e aplica o fix no editor cujo caminho
        corresponde a ``file_path``. Se o arquivo não estiver aberto, ignora.

        Args:
            file_path: Caminho absoluto do arquivo com o diagnóstico.
            diag:      Diagnóstico com campo ``fix`` (dict ESLint).
        """
        if not diag.fix:
            return
        for ed in self._editors:
            if ed.path and str(ed.path) == file_path:
                ed._apply_single_fix(diag)
                return
        e = self._active_editor()
        if e and e.path and str(e.path) == file_path:
            e._apply_single_fix(diag)

    def _apply_problem_fix_all(self, file_path: str, diags: list[Diag]) -> None:
        """Aplica todos os fixes de um arquivo no editor correspondente.

        Usa o algoritmo atômico do ESLint (merge de fixes não-sobrepostos
        numa única varredura) para garantir que os offsets originais
        permaneçam válidos durante toda a operação.

        Args:
            file_path: Caminho absoluto do arquivo.
            diags:     Lista de diagnósticos com fix disponível.
        """
        for ed in self._editors:
            if ed.path and str(ed.path) == file_path:
                ed._apply_fix_list(diags)
                return
        e = self._active_editor()
        if e and e.path and str(e.path) == file_path:
            e._apply_fix_list(diags)

    def _on_git_refresh(self) -> None:
        """Chamado quando o repositório Git é atualizado."""
        self._file_tree.refresh()

        if self._workspace:
            try:
                r = subprocess.run(
                    ["git", "status", "--porcelain"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    cwd=str(self._workspace),
                    timeout=5,
                    check=False,
                )
                n = len(r.stdout.splitlines()) if r.returncode == 0 else 0
            except (OSError, FileNotFoundError):
                n = 0
            self._actbar.set_badge("git", n)

    def _show_panel(self, key: str) -> None:
        """Exibe o painel inferior correspondente à chave.

        Args:
            key: Chave do painel (``"problems"``, ``"terminal"``, etc.).
        """
        t = self._tmgr.theme
        if not self._panel_visible:
            self._show_bottom_panel()

        panel_map: dict[str, tk.Widget] = {
            "problems": self._problems_panel,
            "terminal": self._terminal,
            "output": self._output_panel,
            "git": self._git_bottom,
            "diff": self._diff_pane,
            "debug": self._debug_panel,
            "regex": self._regex_panel,
        }
        if getattr(self, "_sql_bottom", None) is not None:
            panel_map["sql"] = self._sql_bottom  # type: ignore[assignment]
        for w in self._panel_content.winfo_children():
            w.pack_forget()
        panel = panel_map.get(key)
        if panel:
            panel.pack(fill="both", expand=True)

        for k, lbl in self._panel_tabs.items():
            active = k == key
            lbl.config(
                bg=t.tab_act_bg if active else t.tabbar_bg,
                fg=t.tab_act_fg if active else t.tab_in_fg,
            )

        self._current_panel = key
