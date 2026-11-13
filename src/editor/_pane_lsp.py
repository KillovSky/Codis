"""Mixin ``_PaneLSPMixin`` - camada LSP do painel de edição.

Adiciona ao ``EditorPane`` toda a integração com Language Servers: hover,
autocompletar, go-to-definition, referências, rename, code actions,
formatação, inlay hints e call hierarchy.
"""

from __future__ import annotations

import contextlib
import logging
import re
import subprocess
import sys
import time
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog
from typing import TYPE_CHECKING, Any

from src.constants import (
    AUTO_CLOSE,
    AUTOCOMPLETE_LINE_LIMIT,
    EXT_LANG,
    FONT_MAX,
    FONT_MIN,
    FONT_MONO,
    FONT_SIZE,
    FONT_UI,
    GIANT_FILE_BYTES,
    HL_FULL_DISABLE,
    HL_LINE_LIMIT,
    LANG_COMMENT,
    LANG_LABEL,
    LARGE_CODE_BYTES,
    LARGE_FILE_BYTES,
    LINT_LINE_LIMIT,
    LSP_LINE_LIMIT,
    VIEWPORT_LINES,
    VIEWPORT_SHIFT,
    VIEWPORT_TRIGGER,
)
from src.editor.save_hooks import notify_saved as _notify_saved
from src.editor._pane_utils import _BinaryDialog, _lsp_uri_to_path
from src.editor.kdocs import (
    JSDocParser,
    KDocsParser,
    find_all_usages,
    find_definition_line,
    kdocs_annotation_to_sections,
    lsp_hover_to_sections,
)
from src.i18n import _t
from src.ui.file_timeline import FileTimeline

try:
    from src.editor.large_file import VirtualFileBuffer

    _VFB_AVAILABLE = True
except ImportError:
    _VFB_AVAILABLE = False

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from src.lint.manager import Diag
    from src.lsp.client import LSPManager

__all__ = ["_PaneLSPMixin"]

_MOTION_MIN_INTERVAL_S = 0.03


class _PaneLSPMixin:
    """Mixin com toda a lógica LSP, hover e navegação do EditorPane.

    Não instanciar diretamente - usado via herança múltipla por EditorPane.
    Todos os atributos de instância são inicializados por EditorPane.__init__.
    """

    _diag_motion_t = 0.0
    _diag_tip_line: int | None = None
    _diag_index_src: Any = None
    _diag_index_len = -1
    _diag_index: dict[int, list] = {}

    def _hide_hover(self, _: Any = None) -> None:
        """Executa a operação 'hide hover'.

        Args:
            _: Parâmetro da operação.
        """
        if self._hover_job:
            with contextlib.suppress(tk.TclError):
                self.after_cancel(self._hover_job)
            self._hover_job = None
        self._close_balloon()

    def _close_balloon(self) -> None:
        """Fecha o balloon hover atual - respeita o modo interativo (pinned)."""
        if self._hover_tip and self._hover_tip.winfo_exists():
            with contextlib.suppress(tk.TclError):
                self._hover_tip.destroy()
            self._hover_tip = None
        if self._balloon and self._balloon.is_visible():
            if getattr(self._balloon, "_pinned", False):
                return
            with contextlib.suppress(tk.TclError):
                self._balloon.hide()
        self._balloon = None
        self._last_hover_word = ""
        self._last_hover_line = -1

    def _get_balloon(self) -> Any:
        """Retorna (ou cria) a instância compartilhada do KDocsBalloon."""
        if self._balloon is None or not self._balloon.is_visible():
            from src.editor.kdocs import KDocsBalloon

            self._balloon = KDocsBalloon(self._tmgr.theme)
        return self._balloon

    _HOVER_SKIP_WORDS: frozenset[str] = frozenset(
        {
            "if",
            "for",
            "while",
            "def",
            "class",
            "return",
            "import",
            "from",
            "in",
            "is",
            "and",
            "or",
            "not",
            "else",
            "elif",
            "try",
            "except",
            "finally",
            "with",
            "as",
            "pass",
            "break",
            "continue",
            "raise",
            "yield",
            "lambda",
            "global",
            "nonlocal",
            "True",
            "False",
            "None",
            "function",
            "var",
            "let",
            "const",
            "new",
            "this",
            "typeof",
            "instanceof",
            "void",
            "delete",
            "throw",
            "catch",
            "switch",
            "case",
            "default",
            "do",
            "of",
            "async",
            "await",
            "export",
            "extends",
            "implements",
            "interface",
            "type",
            "enum",
            "namespace",
            "module",
            "declare",
            "abstract",
            "override",
            "public",
            "private",
            "protected",
            "static",
            "readonly",
            "true",
            "false",
            "null",
            "undefined",
            "super",
        }
    )

    @staticmethod
    def _src_sep(label: str) -> dict:
        """Retorna um dict separador de fonte para o balloon de hover."""
        return {"kind": "source_label", "label": label}

    def _collect_local_hover_sections(
        self,
        word: str,
        line: int,
        line_text: str,
    ) -> list[dict]:
        """Coleta seções de hover das fontes locais (KDocs, JSDoc, Peek).

        Não realiza chamadas de rede - é completamente síncrono.
        """
        sections: list[dict] = []
        skip = self._HOVER_SKIP_WORDS
        sep = self._src_sep

        if self._kdocs and not self._is_large_mode and not self._is_giant_mode:
            content_txt = self.text.get("1.0", "end-1c")
            ann = KDocsParser.resolve_hover(
                word, line, self._kdocs, content_txt)
            if ann is not None:
                sections.append(sep("KDocs"))
                sections.extend(kdocs_annotation_to_sections(ann))

        if word and word not in skip:
            result = JSDocParser.resolve_hover(word, line, self._doc_symbols)
            if result:
                display, doc, mode = result
                src_lang = doc.get("_lang", "") or (self.lang or "jsdoc")
                jsdoc_sections = JSDocParser.format_sections_for_mode(
                    display, doc, mode
                )
                if jsdoc_sections:
                    if jsdoc_sections[0].get("kind") == "title":
                        jsdoc_sections[0]["source"] = src_lang
                    sections.append(sep(src_lang.upper() or "Doc"))
                    sections.extend(jsdoc_sections)

        if not sections and word and word not in skip:
            stripped = line_text.strip()
            if stripped.startswith(("/**", "* ", "*/")):
                full_txt = self.text.get("1.0", "end-1c")
                block_lines = full_txt.splitlines()
                end_idx = -1
                for k in range(line - 1, min(line + 20, len(block_lines))):
                    if "*/" in block_lines[k]:
                        end_idx = k
                        break
                if end_idx != -1:
                    for k in range(end_idx + 1, min(end_idx + 10, len(block_lines))):
                        nl = block_lines[k].strip()
                        if not nl:
                            continue
                        for pat in JSDocParser._COMPILED_PATTERNS:
                            m = pat.match(nl)
                            if m:
                                sym_name = m.group(1)
                                sym_doc = self._doc_symbols.get(sym_name)
                                if sym_doc:
                                    src_lang = sym_doc.get("_lang", "") or (
                                        self.lang or "jsdoc"
                                    )
                                    bs = JSDocParser.format_sections(
                                        sym_name, sym_doc, source=src_lang
                                    )
                                    if bs:
                                        if bs[0].get("kind") == "title":
                                            bs[0]["source"] = src_lang
                                        sections.append(
                                            sep(src_lang.upper() or "Doc"))
                                        sections.extend(bs)
                                break
                        if sections:
                            break

        if (
            not sections
            and word
            and word not in skip
            and not self._is_large_mode
            and not self._is_giant_mode
        ):
            full_txt = self.text.get("1.0", "end-1c")
            def_line = find_definition_line(word, full_txt, line)
            if def_line is not None:
                def_text = full_txt.splitlines()[def_line - 1].strip()[:90]
                sections.extend(
                    [
                        {
                            "kind": "title",
                            "symbol": word,
                            "ret_type": "",
                            "type_tag": "",
                            "modifiers": [],
                            "tpl_params": [],
                            "source": "peek",
                        },
                        {"kind": "example", "lines": [def_text]},
                        {"kind": "meta", "lines": [
                            f"definido na linha {def_line}"]},
                    ]
                )

        return sections

    def _make_lsp_hover_callback(
        self,
        word: str,
        sx: int,
        sy: int,
        hover_key: str,
        hover_line: int,
        local_sections: list[dict],
    ):
        """Constrói o callback LSP que mescla seções remotas com as locais."""
        sep = self._src_sep

        def _lsp_cb(result):
            if not result:
                if local_sections:

                    def _show_local():
                        if self._last_hover_word != hover_key:
                            self._close_balloon()
                            balloon = self._get_balloon()
                            balloon.show(
                                self.text, sx, sy, local_sections
                            )
                            self._last_hover_word = hover_key
                            self._last_hover_line = hover_line

                    self.after(0, _show_local)
                return

            md = ""
            if hasattr(result, "contents"):
                c = result.contents
                if isinstance(c, dict):
                    md = c.get("value", str(c))
                elif hasattr(c, "value"):
                    md = c.value
                elif isinstance(c, list):
                    md = "\n\n".join(
                        (
                            item
                            if isinstance(item, str)
                            else item.get("value", "") if isinstance(item, dict) else ""
                        )
                        for item in c
                    )
                else:
                    md = str(c)
            elif isinstance(result, dict):
                c = result.get("contents", {})
                md = c.get("value", str(c)) if isinstance(c, dict) else str(c)

            if not md or not md.strip():
                if local_sections:

                    def _show_local2():
                        if self._last_hover_word != hover_key:
                            self._close_balloon()
                            balloon = self._get_balloon()
                            balloon.show(
                                self.text, sx, sy, local_sections
                            )
                            self._last_hover_word = hover_key
                            self._last_hover_line = hover_line

                    self.after(0, _show_local2)
                return

            lsp_sections = lsp_hover_to_sections(word, md)

            def _show_merged():
                merged = []
                if lsp_sections:
                    merged.append(sep("LSP"))
                    merged.extend(lsp_sections)
                if local_sections:
                    merged.extend(local_sections)
                if merged:
                    self._close_balloon()
                    balloon = self._get_balloon()
                    balloon.show(self.text, sx, sy, merged)
                    self._last_hover_word = hover_key
                    self._last_hover_line = hover_line

            self.after(0, _show_merged)

        return _lsp_cb

    def _show_hover(self, x: int, y: int) -> None:
        """Sistema unificado de hover: coleta fontes em paralelo (LSP, KDocs, JSDoc, Peek).

        Exibe uma única bolha com seções separadas por origem - sem sobrescrita.
        Ctrl ao mover o mouse pina o balloon (não fecha automaticamente).
        """
        try:
            if self.text.tag_ranges(tk.SEL) and self.text.tag_ranges("sel"):
                return

            idx = self.text.index(f"@{x},{y}")
            line = int(idx.split(".", maxsplit=1)[0])
            col = int(idx.split(".", maxsplit=1)[1])
            line_text = self.text.get(f"{line}.0", f"{line}.end")

            word = ""
            word_start = col
            for m in re.finditer(r"[$\w][\w$]*", line_text):
                if m.start() <= col <= m.end():
                    word = m.group()
                    word_start = m.start()
                    break

            hover_key = f"{word}:{line}"
            if hover_key == self._last_hover_word and line == self._last_hover_line:
                return

            try:
                bbox = self.text.bbox(f"{line}.{word_start}")
                if bbox:
                    char_x, char_y, _cw, char_h = bbox
                    sx = self.text.winfo_rootx() + char_x
                    sy = self.text.winfo_rooty() + char_y + char_h
                else:
                    raise tk.TclError
            except tk.TclError:
                sx = self.text.winfo_rootx() + x
                sy = self.text.winfo_rooty() + y + 18

            sections = self._collect_local_hover_sections(
                word, line, line_text)

            if sections and not (
                self._lsp_mgr
                and self.path
                and word
                and word not in self._HOVER_SKIP_WORDS
            ):
                self._close_balloon()
                balloon = self._get_balloon()
                balloon.show(self.text, sx, sy, sections)
                self._last_hover_word = hover_key
                self._last_hover_line = line

            if (
                self._lsp_mgr
                and self.path
                and word
                and word not in self._HOVER_SKIP_WORDS
            ):
                cb = self._make_lsp_hover_callback(
                    word, sx, sy, hover_key, line, list(sections)
                )
                with contextlib.suppress(tk.TclError):
                    self._lsp_mgr.hover(
                        self.path, self.lang, line - 1, col, cb)

        except (tk.TclError, AttributeError):
            pass

    def _cancel_hide(self) -> None:
        """Cancela o agendamento de ocultação do hover balloon."""
        if hasattr(self, "_hide_job") and self._hide_job:
            with contextlib.suppress(tk.TclError):
                self.after_cancel(self._hide_job)
            self._hide_job = None

    def _schedule_hide(self) -> None:
        """Agenda o ocultamento do hover balloon após delay configurado."""
        self._cancel_hide()
        self._hide_job = self.after(500, self._hide_hover)

    def _make_auto_pair(self, opener: str) -> Callable:
        """Executa 'make auto pair' e retorna o resultado.

        Args:
            opener: Parâmetro da operação.
        """
        closer = AUTO_CLOSE[opener]

        def handler(_e: tk.Event) -> str | None:
            """Insere o par de delimitadores, envolve seleção ou avança o cursor se já fechado."""
            try:
                if self.text.tag_ranges(tk.SEL):
                    sel_text = self.text.get(tk.SEL_FIRST, tk.SEL_LAST)
                    self.text.delete(tk.SEL_FIRST, tk.SEL_LAST)
                    self.text.insert(tk.INSERT, opener + sel_text + closer)
                    return "break"
                next_char = self.text.get(tk.INSERT)

                if next_char == closer and opener == closer:
                    self.text.mark_set(tk.INSERT, f"{tk.INSERT}+1c")
                    return "break"

                if next_char and next_char not in " \n\t\r)]}\"'":
                    return None
                self.text.insert(tk.INSERT, opener + closer)
                self.text.mark_set(tk.INSERT, f"{tk.INSERT}-1c")
            except tk.TclError:
                pass
            return "break"

        return handler

    def _show_context_menu(self, _e: tk.Event) -> None:
        """Executa a operação 'show context menu'.

        Args:
            _e: Parâmetro da operação.
        """
        t = self._tmgr.theme
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
            label=_t("edit.context_undo"),
            command=lambda: self.text.event_generate("<<Undo>>"),
        )
        menu.add_command(
            label=_t("edit.context_redo"),
            command=lambda: self.text.event_generate("<<Redo>>"),
        )
        menu.add_separator()
        menu.add_command(
            label=_t("edit.context_cut"),
            command=lambda: self.text.event_generate("<<Cut>>"),
        )
        menu.add_command(
            label=_t("edit.context_copy"),
            command=lambda: self.text.event_generate("<<Copy>>"),
        )
        menu.add_command(
            label=_t("edit.context_paste"),
            command=lambda: self.text.event_generate("<<Paste>>"),
        )
        menu.add_command(
            label=_t("edit.context_select_all"),
            command=lambda: self.text.tag_add(tk.SEL, "1.0", "end"),
        )
        menu.add_separator()
        menu.add_command(label=_t("edit.context_comment"),
                         command=self.toggle_comment)
        menu.add_command(
            label=_t("edit.context_duplicate"), command=self.duplicate_line
        )
        menu.add_command(label=_t("edit.context_delete_line"),
                         command=self.delete_line)
        menu.add_separator()
        menu.add_command(label=_t("edit.context_format"),
                         command=self.format_document)
        menu.add_command(
            label=_t("edit.context_goto_line"), command=self.goto_line_dialog
        )
        menu.add_separator()

        word = self.get_word_at_cursor()
        nav_sub = tk.Menu(
            menu,
            tearoff=False,
            bg=t.floating_bg,
            fg=t.fg,
            activebackground=t.sel_bg,
            activeforeground=t.sel_fg,
            relief="flat",
            bd=1,
        )

        def _goto_def():
            if self._lsp_mgr and self.path:
                self.lsp_goto_definition()
            else:
                self._local_goto_definition()

        nav_sub.add_command(
            label=_t("hc.pane.ir_para_defini_o_f12"), command=_goto_def)

        def _find_refs():
            if self._lsp_mgr and self.path:
                self.lsp_find_references()
            else:
                self._show_context_info("LSP não disponível para referências")

        nav_sub.add_command(
            label=_t("hc.pane.localizar_refer_ncias_shift_f1"), command=_find_refs
        )

        nav_sub.add_command(
            label=_t("hc.pane.localizar_usos_alt_shift_f"),
            command=self._find_all_usages_popup,
        )

        if self._lsp_mgr and self.path:
            nav_sub.add_separator()
            nav_sub.add_command(
                label="📣  Ir para Declaração",
                command=self.lsp_goto_declaration,
            )
            nav_sub.add_command(
                label="🔷  Ir para Tipo da Definição",
                command=self.lsp_goto_type_definition,
            )
            nav_sub.add_command(
                label="⚙  Ir para Implementação  (Ctrl+F12)",
                command=self.lsp_goto_implementation,
            )
            nav_sub.add_separator()
            nav_sub.add_command(
                label="Code Actions  (Ctrl+.)",
                command=self.lsp_code_action,
            )
            nav_sub.add_command(
                label="✏️   Renomear Símbolo  (F6)",
                command=self.lsp_rename_symbol,
            )
            nav_sub.add_separator()
            nav_sub.add_command(
                label="📋  Hierarquia: Chamadas Entrantes",
                command=self.lsp_incoming_calls,
            )
            nav_sub.add_command(
                label="📤  Hierarquia: Chamadas Saindo",
                command=self.lsp_outgoing_calls,
            )

        label = f"🧭  Navegar: '{word}'" if word else "🧭  Navegar"
        menu.add_cascade(label=label, menu=nav_sub)
        menu.add_separator()

        def _mk_sub():
            return tk.Menu(
                menu,
                tearoff=False,
                bg=t.floating_bg,
                fg=t.fg,
                activebackground=t.sel_bg,
                activeforeground=t.sel_fg,
                relief="flat",
                bd=1,
            )

        try:
            from src.editor.text_transforms import (
                TRANSFORMS,
                apply_transform,
                insert_at_cursor,
            )

            _tt_ok = True
        except ImportError:
            _tt_ok = False

        if _tt_ok:
            trans_sub = _mk_sub()
            _case_items = [
                ("camel", "camelCase"),
                ("pascal", "PascalCase"),
                ("snake", "snake_case"),
                ("kebab", "kebab-case"),
                ("constant", "CONSTANT_CASE"),
            ]
            for key, label_t in _case_items:
                entry = TRANSFORMS[key]
                trans_sub.add_command(
                    label=f"{entry[2]}  {label_t}",
                    command=lambda k=key: apply_transform(self.text, k),
                )
            trans_sub.add_separator()
            _enc_items = [
                ("b64enc", "Base64 Encode"),
                ("b64dec", "Base64 Decode"),
                ("urlenc", "URL Encode"),
                ("urldec", "URL Decode"),
                ("htmlesc", "HTML Escape"),
                ("htmlune", "HTML Unescape"),
                ("jsonesc", "JSON Escape"),
            ]
            for key, label_t in _enc_items:
                entry = TRANSFORMS[key]
                trans_sub.add_command(
                    label=f"{entry[2]}  {label_t}",
                    command=lambda k=key: apply_transform(self.text, k),
                )
            trans_sub.add_separator()
            _hash_items = [("md5", "MD5"), ("sha1", "SHA-1"),
                           ("sha256", "SHA-256")]
            for key, label_t in _hash_items:
                trans_sub.add_command(
                    label=f"#  {label_t} Hash",
                    command=lambda k=key: apply_transform(self.text, k),
                )
            trans_sub.add_separator()
            _line_items = [
                ("dedup", "Remover Linhas Duplicadas"),
                ("revlines", "Inverter Linhas"),
                ("numlines", "Numerar Linhas"),
                ("remempty", "Remover Linhas Vazias"),
            ]
            for key, label_t in _line_items:
                entry = TRANSFORMS[key]
                trans_sub.add_command(
                    label=f"{entry[2]}  {label_t}",
                    command=lambda k=key: apply_transform(self.text, k),
                )
            menu.add_cascade(
                label=_t("edit.context_transform", "Transformar"),
                menu=trans_sub,
            )

            ins_sub = _mk_sub()
            _ins_items = [
                ("uuid", "UUID v4"),
                ("ts_iso", "Timestamp ISO 8601"),
                ("ts_date", "Data  (YYYY-MM-DD)"),
                ("ts_unix", "Unix Timestamp"),
                ("lorem1", "Lorem Ipsum  (1 frase)"),
                ("lorem3", "Lorem Ipsum  (3 frases)"),
                ("lorem5", "Lorem Ipsum  (5 frases)"),
            ]
            for key, label_t in _ins_items:
                ins_sub.add_command(
                    label=label_t,
                    command=lambda k=key: insert_at_cursor(self.text, k),
                )
            menu.add_cascade(
                label=_t("edit.context_insert", "➕  Inserir"),
                menu=ins_sub,
            )
            menu.add_separator()

        menu.add_command(label=_t("edit.context_copy_path"),
                         command=self._copy_path)
        menu.add_command(
            label=_t("edit.context_reveal"), command=self._reveal_in_explorer
        )

        try:
            cursor_idx = self.text.index(f"@{_e.x},{_e.y}")
            cursor_line = int(cursor_idx.split(".")[0])
        except (tk.TclError, ValueError, AttributeError):
            cursor_line = -1

        line_diags = [d for d in self._diags if d.line ==
                      cursor_line and d.fix]
        all_fixable = [d for d in self._diags if d.fix]

        if line_diags or all_fixable:
            menu.add_separator()

            for d in line_diags:
                _d = d
                label_fix = f"🔧  Corrigir: {d.rule or d.message[:40]}"
                menu.add_command(
                    label=label_fix,
                    command=lambda diag=_d: self._apply_single_fix(diag),
                )

            if line_diags:
                for d in line_diags:
                    if d.rule:
                        same_rule = [
                            x for x in self._diags if x.fix and x.rule == d.rule
                        ]
                        if len(same_rule) > 1:
                            _rule = d.rule
                            _same = same_rule
                            menu.add_command(
                                label=f"🔧  Corrigir todas: {d.rule} ({len(same_rule)})",
                                command=lambda diags=_same: self._apply_fix_list(
                                    diags),
                            )
                            break

            if all_fixable:
                menu.add_command(
                    label=f"✅  Corrigir todas as falhas do arquivo ({len(all_fixable)})",
                    command=self.fix_all,
                )

        try:
            menu.tk_popup(_e.x_root, _e.y_root)
        finally:
            menu.grab_release()

    def _apply_single_fix(self, diag: Diag) -> None:
        """Aplica o fix de um único diagnóstico ESLint no editor.

        Args:
            diag: Diagnóstico com campo ``fix`` (dict com ``range`` e ``text``).
        """
        if not diag.fix or not self.path:
            return
        try:
            content = self.text.get("1.0", "end-1c")
            from src.lint.manager import LintManager

            fixed = LintManager.apply_eslint_fix(content, diag.fix)
            if fixed != content:
                self.set_content(fixed)
        except Exception as exc:  # noqa: BLE001
            import logging

            logging.getLogger("Codis").warning("_apply_single_fix: %s", exc)

    def _apply_fix_list(self, diags: list[Diag]) -> None:
        """Aplica uma lista de fixes num único passo atômico.

        Usa o algoritmo de merge do ESLint via ``apply_all_eslint_fixes``:
        ordena por posição, descarta sobrepostos e aplica numa única varredura.
        Nunca aplica fixes sequencialmente sobre texto já modificado.

        Args:
            diags: Lista de diagnósticos com fix disponível.
        """
        if not diags or not self.path:
            return
        try:
            content = self.text.get("1.0", "end-1c")
            fixed = self._lint_mgr.apply_all_eslint_fixes(
                self.path, content, diags)
            if fixed != content:
                self.set_content(fixed)
        except Exception as exc:  # noqa: BLE001
            import logging

            logging.getLogger("Codis").warning("_apply_fix_list: %s", exc)

    def _diags_for_line(self, line: int) -> list:
        """Retorna os diagnósticos de ``line`` usando um índice por linha.

        O índice é reconstruído só quando a lista de diagnósticos muda, em vez
        de varrê-la inteira a cada movimento do mouse.
        """
        source = self._diags
        if self._diag_index_src is not source or self._diag_index_len != len(source):
            index: dict[int, list] = {}
            for diag in source:
                index.setdefault(diag.line, []).append(diag)
            self._diag_index = index
            self._diag_index_src = source
            self._diag_index_len = len(source)
        return self._diag_index.get(line, [])

    def _on_mouse_motion_diag(self, e: tk.Event) -> None:
        """Mostra tooltip de diagnóstico ao passar o mouse sobre uma linha com erro/aviso.

        O handler é limitado a ~30 Hz e não recria o balão enquanto o mouse
        continua sobre a mesma linha.

        Args:
            e: Evento de movimento do mouse.
        """
        now = time.monotonic()
        if now - self._diag_motion_t < _MOTION_MIN_INTERVAL_S:
            return
        self._diag_motion_t = now
        try:
            idx = self.text.index(f"@{e.x},{e.y}")
            line = int(idx.split(".")[0])
        except (tk.TclError, ValueError):
            self._hide_diag_tip()
            return

        line_diags = self._diags_for_line(line)
        if not line_diags:
            if self._diag_tip_job or self._diag_tip_win:
                self._hide_diag_tip()
            return

        if line == self._diag_tip_line and (self._diag_tip_job or self._diag_tip_win):
            return

        if self._diag_tip_job:
            with contextlib.suppress(tk.TclError):
                self.text.after_cancel(self._diag_tip_job)
        self._diag_tip_line = line
        self._diag_tip_job = self.text.after(
            400, lambda: self._show_diag_tip(e.x_root, e.y_root, line_diags)
        )

    def _show_diag_tip(self, rx: int, ry: int, diags: list[Diag]) -> None:
        """Exibe o balão com os erros do linter para a linha.

        Args:
            rx: Coordenada X global.
            ry: Coordenada Y global.
            diags: Lista de diagnósticos da linha.
        """
        self._hide_diag_tip()
        t = self._tmgr.theme
        win = tk.Toplevel(self)
        win.wm_overrideredirect(True)
        win.wm_attributes("-topmost", True)

        frame = tk.Frame(
            win,
            bg=t.floating_bg,
            highlightthickness=1,
            highlightbackground=t.floating_border,
        )
        frame.pack()

        _SEV_COLOR = {
            1: getattr(t, "err_fg", "#f87171"),
            2: getattr(t, "warn_fg", "#fabd2f"),
            3: getattr(t, "info_fg", "#89dceb"),
            4: getattr(t, "hint_fg", "#a6e3a1"),
        }
        _SEV_ICON = {1: "✖", 2: "⚠", 3: "ℹ", 4: "-"}

        for d in diags[:5]:
            icon = _SEV_ICON.get(d.severity, "·")
            color = _SEV_COLOR.get(d.severity, t.fg)
            rule_txt = f"  [{d.rule}]" if d.rule else ""
            text = f"{icon}  {d.message}{rule_txt}"
            tk.Label(
                frame,
                text=text,
                bg=t.floating_bg,
                fg=color,
                font=(self._font_family, self._font_size - 1),
                padx=8,
                pady=2,
                anchor="w",
                justify="left",
                wraplength=400,
            ).pack(fill="x")

        if len(diags) > 5:
            tk.Label(
                frame,
                text=f"  … e mais {len(diags) - 5} problema(s)",
                bg=t.floating_bg,
                fg=t.fg_muted,
                font=(self._font_family, self._font_size - 2),
                padx=8,
                pady=2,
                anchor="w",
            ).pack(fill="x")

        win.geometry(f"+{rx + 14}+{ry + 14}")
        self._diag_tip_win = win
        self._diag_tip_line = diags[0].line if diags else None

        win.bind("<Enter>", lambda _: None)

    def _hide_diag_tip(self, _e=None) -> None:
        """Oculta o tooltip de diagnóstico do linter."""
        import contextlib

        if self._diag_tip_job:
            with contextlib.suppress(tk.TclError):
                self.text.after_cancel(self._diag_tip_job)
            self._diag_tip_job = None
        if self._diag_tip_win and self._diag_tip_win.winfo_exists():
            with contextlib.suppress(tk.TclError):
                self._diag_tip_win.destroy()
        self._diag_tip_win = None
        self._diag_tip_line = None

    def _local_goto_definition(self) -> None:
        """Vai para a definição sem LSP - busca puramente textual no arquivo."""
        word = self.get_word_at_cursor()
        if not word:
            return
        idx = self.text.index(tk.INSERT)
        line = int(idx.split(".", maxsplit=1)[0])
        full = self.text.get("1.0", "end-1c")
        def_line = find_definition_line(word, full, line)
        if def_line:
            self.goto_line(def_line)
        else:
            self._show_context_info(f"Sem definição para '{word}'")

    def _find_all_usages_popup(self) -> None:
        """Abre popup com todas as referências do símbolo sob o cursor."""
        word = self.get_word_at_cursor()
        if not word:
            return
        if self._is_large_mode or self._is_giant_mode:
            self._show_context_info(
                "Busca de usos desativada para arquivos grandes")
            return
        full = self.text.get("1.0", "end-1c")
        lines = full.splitlines()
        usages = find_all_usages(word, full)
        if not usages:
            self._show_context_info(f"Sem usos de '{word}' encontrados")
            return

        t = self._tmgr.theme
        sx = self.text.winfo_rootx() + 80
        sy = self.text.winfo_rooty() + 60

        win = tk.Toplevel(self)
        win.wm_overrideredirect(True)
        win.wm_attributes("-topmost", True)
        win.geometry("+9999+9999")

        outer = tk.Frame(
            win, bg=getattr(t, "floating_border", "#3d4455"), padx=1, pady=1
        )
        outer.pack(fill="both", expand=True)
        inner = tk.Frame(outer, bg=t.floating_bg)
        inner.pack(fill="both", expand=True)

        hdr = tk.Frame(inner, bg=getattr(t, "activity_bg", t.floating_bg))
        hdr.pack(fill="x")
        tk.Label(
            hdr,
            text=f"  🔍 {len(usages)} usos de '{word}'",
            bg=hdr["bg"],
            fg=t.fg,
            font=(FONT_UI, 9, "bold"),
        ).pack(side="left", padx=6, pady=5)
        close = tk.Label(
            hdr,
            text="✕",
            bg=hdr["bg"],
            fg=t.fg_muted,
            font=(FONT_UI, 9),
            cursor="hand2",
            padx=8,
        )
        close.pack(side="right", pady=5)
        close.bind("<Button-1>", lambda _: win.destroy())

        tk.Frame(inner, bg=getattr(t, "border", "#3d4455"),
                 height=1).pack(fill="x")

        canvas = tk.Canvas(
            inner,
            bg=t.floating_bg,
            highlightthickness=0,
            width=420,
            height=min(len(usages), 18) * 22 + 4,
        )
        vsb = tk.Scrollbar(inner, orient="vertical", command=canvas.yview)
        canvas.config(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(fill="both", expand=True)

        lf = tk.Frame(canvas, bg=t.floating_bg)
        win_id = canvas.create_window((0, 0), window=lf, anchor="nw")
        lf.bind("<Configure>", lambda _: canvas.config(
            scrollregion=canvas.bbox("all")))
        canvas.bind(
            "<Configure>",
            lambda _: canvas.itemconfig(win_id, width=canvas.winfo_width()),
        )

        mono = getattr(t, "font_mono", FONT_MONO)
        for ln in usages:
            row_bg = t.floating_bg
            text_preview = lines[ln - 1].strip()[:70]
            row = tk.Frame(lf, bg=row_bg, cursor="hand2")
            row.pack(fill="x", padx=4, pady=1)
            num_lbl = tk.Label(
                row,
                text=f"{ln:4d} ",
                bg=row_bg,
                fg=t.fg_muted,
                font=(mono, 8),
                width=5,
                anchor="e",
            )
            num_lbl.pack(side="left")
            code_lbl = tk.Label(
                row, text=text_preview, bg=row_bg, fg=t.fg, font=(mono, 8), anchor="w"
            )
            code_lbl.pack(side="left", fill="x", expand=True)

            def _on_click(l_=ln, w=win):
                w.destroy()
                self.goto_line(l_)

            def _enter(_ev, r=row, nl=num_lbl, cl=code_lbl):
                r.config(bg=t.sel_bg)
                nl.config(bg=t.sel_bg)
                cl.config(bg=t.sel_bg)

            def _leave(_ev, r=row, nl=num_lbl, cl=code_lbl, bg=row_bg):
                r.config(bg=bg)
                nl.config(bg=bg)
                cl.config(bg=bg)

            for w2 in (row, num_lbl, code_lbl):
                w2.bind("<Button-1>", lambda ev, l_=ln, fn=_on_click: fn(l_))
                w2.bind("<Enter>", _enter)
                w2.bind("<Leave>", _leave)

        def _wheel(ev):
            delta = -1 * \
                (ev.delta // 120) if ev.delta else (1 if ev.num == 5 else -1)
            canvas.yview_scroll(delta, "units")

        canvas.bind("<MouseWheel>", _wheel)
        canvas.bind("<Button-4>", _wheel)
        canvas.bind("<Button-5>", _wheel)

        win.bind("<Escape>", lambda _: win.destroy())
        win.bind("<FocusOut>", lambda _: win.destroy()
                 if win.winfo_exists() else None)

        win.update_idletasks()
        w2 = win.winfo_reqwidth()
        h2 = win.winfo_reqheight()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        bx = min(sx, sw - w2 - 8)
        by = min(sy, sh - h2 - 8)
        win.geometry(f"+{bx}+{by}")

    def _show_context_info(self, msg: str) -> None:
        """Exibe brevemente uma mensagem informativa próxima ao editor."""
        t = self._tmgr.theme
        sx = self.text.winfo_rootx() + 40
        sy = self.text.winfo_rooty() + self.text.winfo_height() - 40
        tip = tk.Toplevel(self.winfo_toplevel())
        tip.wm_overrideredirect(True)
        tip.wm_attributes("-topmost", True)
        tip.geometry("+9999+9999")
        frm = tk.Frame(
            tip,
            bg=t.floating_bg,
            highlightthickness=1,
            highlightbackground=getattr(t, "floating_border", "#3d4455"),
        )
        frm.pack()
        tk.Label(
            frm,
            text=msg,
            bg=t.floating_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
            padx=10,
            pady=6,
        ).pack()
        tip.update_idletasks()
        tip.winfo_reqwidth()
        tip.geometry(f"+{sx}+{sy}")
        tip.after(2500, tip.destroy)

    def _copy_path(self) -> None:
        """Copia o caminho do arquivo para a área de transferência."""
        if self.path:
            self.clipboard_clear()
            self.clipboard_append(str(self.path))

    def _reveal_in_explorer(self) -> None:
        """Abre o explorador de arquivos do sistema com o arquivo selecionado."""
        if self.path:
            try:
                if sys.platform == "win32":
                    subprocess.Popen(["explorer", "/select,", str(self.path)])
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", "-R", str(self.path)])
                else:
                    subprocess.Popen(["xdg-open", str(self.path.parent)])
            except OSError:
                pass

    def _update_cursor(self) -> None:
        """Atualiza o cursor visual de acordo com o modo LSP ativo."""
        try:
            idx = self.text.index(tk.INSERT)
            ln, col = map(int, idx.split("."))
            self.gutter.set_current_line(ln)
            self._on_cursor(ln, col + 1)

            with contextlib.suppress(tk.TclError):
                if self.inline_blame and self._blame_enabled:
                    self.inline_blame.notify_cursor_moved()
        except tk.TclError:
            pass

    def _on_marked_lines_change(self, lines: set[int]) -> None:
        """Chamado quando o conjunto de linhas marcadas no gutter muda."""
        self._multicursor_lines = lines
        if lines:
            self._multicursor_active = True

            self.text.tag_remove("multicursor_line", "1.0", "end")
            for ln in lines:
                self.text.tag_add("multicursor_line",
                                  f"{ln}.0", f"{ln}.end+1c")
            t = self._tmgr.theme
            accent = getattr(t, "accent", "#4fc3f7")

            from src.theme.utils import hex_blend

            hl = hex_blend(t.editor_bg, accent, 0.15)
            self.text.tag_configure("multicursor_line", background=hl)
        else:
            self._multicursor_active = False
            self.text.tag_remove("multicursor_line", "1.0", "end")

        with contextlib.suppress(tk.TclError):
            self.text.tag_lower("multicursor_line")

    def _multicursor_insert(self, char: str) -> None:
        """Insere char no início de cada linha marcada (multicursor simultâneo).

        Usado para edição síncrona de múltiplas linhas quando multicursor
        está ativo. Insere na coluna 0 de cada linha marcada.
        """
        if not self._multicursor_lines:
            return

        for ln in sorted(self._multicursor_lines, reverse=True):
            with contextlib.suppress(tk.TclError):
                self.text.insert(f"{ln}.0", char)
        self.gutter.redraw()

    def _multicursor_delete_col(self, col: int = 0) -> None:
        """Remove o caractere na coluna col de cada linha marcada."""
        for ln in sorted(self._multicursor_lines, reverse=True):
            try:
                ch = self.text.get(f"{ln}.{col}", f"{ln}.{col + 1}")
                if ch and ch != "\n":
                    self.text.delete(f"{ln}.{col}", f"{ln}.{col + 1}")
            except tk.TclError:
                pass

    def _on_modified(self, _e: Any = None) -> None:
        """Trata o evento 'modified'.

        Args:
            _e: Parâmetro da operação.
        """
        try:
            if self.text.edit_modified():
                if not self.modified:
                    self.modified = True
                    name = self.path.name if self.path else _t(
                        "ui.tab_untitled")
                    self._on_title(name, True)
                self._on_change()
                self.text.edit_modified(False)
                if getattr(self, "ligature_engine", None):
                    self.ligature_engine.on_change()
        except tk.TclError:
            pass

    def load(self, path: Path) -> bool:
        """Carrega um arquivo no editor usando a estratégia adequada ao tamanho.

        Tier 1 (< GIANT_FILE_BYTES):  inserção assíncrona em chunks, features pesadas off.
        Tier 2 (>= GIANT_FILE_BYTES): viewport virtual via mmap, só VIEWPORT_LINES linhas.

        Antes de qualquer carga, verifica se o arquivo é binário, compactado ou de formato
        não gerenciável - exibindo um diálogo apropriado em cada caso.
        """
        self._close_vfb()
        try:
            file_size = path.stat().st_size
        except OSError as exc:
            messagebox.showerror(
                _t("err.file_not_found_title"), _t(
                    "err.file_open", err=str(exc))
            )
            return False

        _IMAGE_EXTS = {
            ".png",
            ".jpg",
            ".jpeg",
            ".bmp",
            ".gif",
            ".webp",
            ".tiff",
            ".tif",
            ".ico",
            ".svg",
        }
        _MEDIA_EXTS = {
            ".mp3",
            ".mp4",
            ".wav",
            ".ogg",
            ".flac",
            ".aac",
            ".m4a",
            ".webm",
            ".mkv",
            ".avi",
            ".mov",
            ".wma",
        }
        ext = path.suffix.lower()
        if ext in _IMAGE_EXTS:
            self._open_image_viewer(path)
            return False
        if ext in _MEDIA_EXTS:
            self._open_media_player(path)
            return False

        guard = self._guard_binary(path, file_size)
        if guard == "cancel":
            return False
        if guard == "archive":
            self._open_archive_viewer(path)
            return False

        if file_size >= GIANT_FILE_BYTES and _VFB_AVAILABLE:
            return self._load_tier2(path, file_size)

        lang_hint = EXT_LANG.get(path.suffix.lower(), "text")
        _TEXT_LANGS = {"text", "markdown", "plaintext", ""}
        force_tier1 = (
            guard != "force_text"
            and lang_hint not in _TEXT_LANGS
            and file_size >= LARGE_CODE_BYTES
        )
        if file_size >= LARGE_FILE_BYTES or force_tier1:
            return self._load_tier1(path, file_size)
        return self._load_tier0(path)

    _ARCHIVE_MAGIC: list[tuple[bytes, int, str]] = [
        (b"PK\x03\x04", 0, "zip"),
        (b"PK\x05\x06", 0, "zip"),
        (b"Rar!\x1a\x07\x00", 0, "rar"),
        (b"Rar!\x1a\x07\x01", 0, "rar"),
        (b"7z\xbc\xaf\x27\x1c", 0, "7z"),
        (b"\x1f\x8b", 0, "gz"),
        (b"BZh", 0, "bz2"),
        (b"ustar", 257, "tar"),
    ]

    _MAX_UNMANAGED_OPEN = 500 * 1024 * 1024

    def _guard_binary(self, path: Path, file_size: int) -> str:
        """Verifica se o arquivo é binário, compactado ou não gerenciável.

        Lê os primeiros 512 bytes e analisa magic bytes + null bytes.

        Returns:
            'ok'         - pode abrir normalmente
            'cancel'     - usuário cancelou / arquivo bloqueado
            'force_text' - usuário forçou exibição em modo texto
            'archive'    - usuário quer visualizar como arquivo compactado
        """
        if file_size == 0:
            return "ok"

        read_size = min(file_size, 512)
        try:
            with open(path, "rb") as fh:
                header = fh.read(read_size)
        except OSError:
            return "ok"

        for magic, offset, kind in self._ARCHIVE_MAGIC:
            end = offset + len(magic)
            if len(header) >= end and header[offset:end] == magic:
                return self._ask_archive_action(path, kind, file_size)

        if header[:2] in (b"\xff\xfe", b"\xfe\xff"):
            return "ok"

        check = header[3:] if header[:3] == b"\xef\xbb\xbf" else header
        if b"\x00" in check[:512]:
            if file_size > self._MAX_UNMANAGED_OPEN:
                size_gb = file_size / (1024**3)
                ext = path.suffix or "desconhecido"
                messagebox.showwarning(
                    "Arquivo não suportado",
                    f'O arquivo "{path.name}" ({size_gb:.1f} GB) é um binário de grande '
                    f'porte sem gerenciador registrado para o formato "{ext}".\n\n'
                    "Para abri-lo, instale uma extensão que suporte este formato ou ative "
                    "o modo 'Forçar exibição' nas configurações.",
                )
                return "cancel"
            return self._ask_binary_force(path, file_size)

        return "ok"

    def _ask_archive_action(self, path: Path, kind: str, file_size: int) -> str:
        """Diálogo para arquivo compactado: listar conteúdo, forçar texto ou cancelar."""
        label_map = {
            "zip": "ZIP",
            "rar": "RAR",
            "7z": "7-Zip",
            "gz": "GZip",
            "bz2": "BZip2",
            "tar": "TAR",
        }
        label = label_map.get(kind, kind.upper())
        size_mb = file_size / (1024 * 1024)

        native_browser = kind == "zip"

        if native_browser:
            msg = (
                f'O arquivo "{path.name}" ({size_mb:.1f} MB) é um arquivo {label}.\n\n'
                "- Listar conteudo - abre o visualizador de arquivo compactado\n"
                "- Forcar exibicao - abre os bytes brutos como texto (pode ser ilegivel)\n"
                "- Cancelar - nao abre o arquivo"
            )
            choice = _BinaryDialog.ask(
                self,
                title=f"Arquivo {label} detectado",
                message=msg,
                buttons=[
                    ("📂  Listar conteúdo", "archive"),
                    ("📄  Forçar exibição", "force_text"),
                    ("Cancelar", "cancel"),
                ],
            )
        else:
            msg = (
                f'O arquivo "{path.name}" ({size_mb:.1f} MB) é um arquivo {label}.\n\n'
                f"O Codis não possui visualizador nativo para o formato {label}.\n"
                "Instale uma extensão que suporte este formato, ou force a exibição "
                "dos dados binários como texto.\n\n"
                "- Forcar exibicao - mostra os bytes brutos (pode ser ilegivel)\n"
                "- Cancelar"
            )
            choice = _BinaryDialog.ask(
                self,
                title=f"Arquivo {label} detectado",
                message=msg,
                buttons=[
                    ("📄  Forçar exibição", "force_text"),
                    ("Cancelar", "cancel"),
                ],
            )
        return choice or "cancel"

    def _ask_binary_force(self, path: Path, file_size: int) -> str:
        """Diálogo para arquivo binário genérico: forçar exibição ou cancelar."""
        size_mb = file_size / (1024 * 1024)
        ext = path.suffix or "sem extensão"
        msg = (
            f'O arquivo "{path.name}" ({size_mb:.1f} MB) parece ser um binário '
            f"não reconhecido (formato: {ext}).\n\n"
            "Forçar a exibição pode mostrar caracteres ilegíveis ou travar o editor "
            "em arquivos muito grandes.\n\n"
            "- Forcar exibicao - abre mesmo assim\n"
            "- Cancelar"
        )
        choice = _BinaryDialog.ask(
            self,
            title=_t("editor.binary_detected"),
            message=msg,
            buttons=[
                ("⚠️  Forçar exibição", "force_text"),
                ("Cancelar", "cancel"),
            ],
        )
        return choice or "cancel"

    def _open_image_viewer(self, path: Path) -> None:
        """Abre uma imagem no Paint integrado do Codis."""
        try:
            from src.ui.paint_panel import PaintPanel

            win = tk.Toplevel(self)
            win.title(f"Cor: {path.name}")
            win.geometry("900x650")
            panel = PaintPanel(win, self._tmgr, initial_path=path)
            panel.pack(fill="both", expand=True)
        except Exception as exc:
            logger.warning("PaintPanel (imagem) falhou: %s", exc)
            messagebox.showerror(_t("editor.open_file_error"), str(exc))

    def _open_media_player(self, path: Path) -> None:
        """Abre um arquivo de áudio/vídeo no Zen Media Player."""
        try:
            from src.ui.zen_media import ZenMediaPlayer

            player = ZenMediaPlayer(tk_root=self.winfo_toplevel())
            player.set_playlist([str(path)])
            player.show()  # pylint: disable=no-member
        except Exception as exc:
            logger.warning("ZenMediaPlayer falhou: %s", exc)
            messagebox.showerror(_t("editor.open_file_error"), str(exc))

    def _open_archive_viewer(self, path: Path) -> None:
        """Abre o visualizador de arquivo compactado em uma nova janela."""
        try:
            from src.ui.archive_panel import ArchivePanel

            win = tk.Toplevel(self)
            win.title(f"📦  {path.name}")
            win.geometry("700x500")
            panel = ArchivePanel(
                win, path, on_open_file=self._on_archive_extract)
            panel.pack(fill="both", expand=True)
        except Exception as exc:
            logger.warning("ArchivePanel falhou: %s", exc)
            messagebox.showerror(_t("editor.open_file_error"), str(exc))

    def _on_archive_extract(self, extracted_path: Path) -> None:
        """Callback do ArchivePanel: abre o arquivo extraído no editor."""
        self.load(extracted_path)

    def _load_tier0(self, path: Path) -> bool:
        """Carrega o arquivo em tier-0 (síncrono, somente leitura). Retorna True se bem-sucedido."""
        try:
            self.path = path
            self.lang = EXT_LANG.get(path.suffix.lower(), "text")
            if self.code_folding:
                self.code_folding.set_lang(self.lang)
            self._is_large_mode = False
            self._is_giant_mode = False
            self._viewport_start = 0

            raw = path.read_bytes()
            content = self._decode_bytes(raw)
            if raw.startswith(b"\xef\xbb\xbf"):
                self.encoding = "UTF-8 BOM"
            content, self.eol = self._normalize_eol(content)
            content, _n_rem, _n_trunc = self._sanitize_for_display(content)
            self._last_saved_content = content

            self.text.config(state="normal", undo=True, maxundo=-1)
            self.text.delete("1.0", "end")
            self.text.insert("1.0", content)
            self.text.edit_reset()
            self.text.edit_modified(False)
            self.modified = False

            self._restore_full_features()
            self.hl.reenable()
            self.hl.set_lang(self.lang)

            n_lines = content.count("\n") + 1
            self._code_line_count = n_lines

            if n_lines >= HL_FULL_DISABLE:
                self.hl.disable_large_file()
                self._show_perf_banner("highlight desativado", n_lines)
            elif n_lines >= HL_LINE_LIMIT:
                self.hl.enable_viewport_only()
                self.hl.highlight()
                self._show_perf_banner("highlight parcial (viewport)", n_lines)
            else:
                self.hl.highlight()

            self.autocomplete.set_lang(self.lang)

            self.gutter._update_width()
            self.gutter.redraw()
            self.minimap._schedule_redraw()

            self.autocomplete.update_doc_symbols(content, self.lang)
            _fname = str(
                self.path) if self.path else f"untitled.{self.lang or 'txt'}"
            self._doc_symbols = JSDocParser.parse_file(content, _fname)
            self._update_kdocs()

            if n_lines < LINT_LINE_LIMIT:
                self._lint_mgr.schedule(path, content, self.lang)

            if self._lsp_mgr and path:
                with contextlib.suppress(tk.TclError):
                    if n_lines < LSP_LINE_LIMIT:
                        self._lsp_mgr.did_open(path, self.lang, content)

                        if n_lines < AUTOCOMPLETE_LINE_LIMIT:
                            self.autocomplete.set_lsp_manager(
                                self._lsp_mgr, path)
                    if self.lang not in (
                        "text",
                        "plaintext",
                        "",
                    ) and not self._lsp_mgr.available_for(self.lang):
                        self._on_lsp_hint(self.lang)

            self._post_load_common(path)
            if _n_rem or _n_trunc:
                self.after(200, lambda: self._show_sanitize_banner(
                    _n_rem, _n_trunc))
            return True

        except (OSError, UnicodeDecodeError, MemoryError) as exc:
            messagebox.showerror(
                _t("err.file_not_found_title"), _t(
                    "err.file_open", err=str(exc))
            )
            return False

    def _load_tier1(self, path: Path, file_size: int) -> bool:
        """Arquivo grande: lê na RAM, insere no widget em chunks via after_idle.

        Desativa: undo stack, syntax highlight, minimap, lint, autocomplete,
        kdocs, jsdoc, inlay hints, color preview, inline blame, code folding,
        sticky scroll, LSP - para garantir UI responsiva durante a carga.
        """
        try:
            self.path = path
            self.lang = EXT_LANG.get(path.suffix.lower(), "text")
            if self.code_folding:
                self.code_folding.set_lang(self.lang)
            self._is_large_mode = True
            self._is_giant_mode = False
            self._viewport_start = 0

            raw = path.read_bytes()
            content = self._decode_bytes(raw)
            if raw.startswith(b"\xef\xbb\xbf"):
                self.encoding = "UTF-8 BOM"
            content, self.eol = self._normalize_eol(content)
            content, _n_rem, _n_trunc = self._sanitize_for_display(content)
            self._last_saved_content = content
            self._sanitize_stats = (_n_rem, _n_trunc)

            self._disable_heavy_features()
            self.text.config(state="normal", undo=False)
            self.text.delete("1.0", "end")
            self.text.edit_reset()
            self.text.edit_modified(False)
            self.modified = False

            self.hl.set_lang(self.lang)
            self.autocomplete.set_lang(self.lang)

            mb = file_size / (1024 * 1024)
            self._show_loading_overlay(
                f"\u23f3  Abrindo arquivo grande ({mb:.1f} MB)\u2026  0%"
            )
            self._is_loading = True
            CHUNK = 80_000
            self._load_chunks = [
                content[i: i + CHUNK] for i in range(0, len(content), CHUNK)
            ]
            self._load_total = len(self._load_chunks)
            self._on_title(path.name, False)
            del content, raw
            self.after_idle(self._insert_chunk, 0)
            return True

        except (OSError, UnicodeDecodeError, MemoryError) as exc:
            messagebox.showerror(
                _t("err.file_not_found_title"), _t(
                    "err.file_open", err=str(exc))
            )
            return False

    def _insert_chunk(self, idx: int) -> None:
        """Insere um chunk de texto e agenda o próximo via after_idle."""
        if not self._is_loading:
            return
        if idx >= self._load_total:
            self._on_tier1_load_complete()
            return
        try:
            self.text.insert("end", self._load_chunks[idx])
        except tk.TclError:
            return
        pct = int((idx + 1) / max(1, self._load_total) * 100)
        total_chars = sum(len(c) for c in self._load_chunks)
        mb = total_chars / (1024 * 1024)
        self._update_loading_overlay(
            f"\u23f3  Abrindo arquivo grande ({mb:.1f} MB)\u2026  {pct}%"
        )
        self.after_idle(self._insert_chunk, idx + 1)

    def _on_tier1_load_complete(self) -> None:
        """Finaliza o carregamento tier-1: atualiza UI e notifica o bus de eventos."""
        self._is_loading = False
        self._load_chunks = []
        self._hide_loading_overlay()
        self.text.edit_reset()
        self.text.edit_modified(False)
        self.modified = False
        self.hl.disable_large_file()
        self.gutter._update_width()
        self.gutter.redraw()
        with contextlib.suppress(tk.TclError):
            self.minimap._schedule_redraw()
        if self.path:
            self._post_load_common(self.path)
        self._show_large_file_notice()
        _n_rem, _n_trunc = self._sanitize_stats
        if _n_rem or _n_trunc:
            self.after(
                300, lambda r=_n_rem, t=_n_trunc: self._show_sanitize_banner(
                    r, t)
            )

    def _load_tier2(self, path: Path, file_size: int) -> bool:
        """Arquivo gigante: mmap + índice de linhas em background + viewport virtual."""
        self.path = path
        self.lang = EXT_LANG.get(path.suffix.lower(), "text")
        if self.code_folding:
            self.code_folding.set_lang(self.lang)
        self._is_large_mode = False
        self._is_giant_mode = True
        self._viewport_start = 0

        try:
            with open(path, "rb") as fh:
                header = fh.read(4)
        except OSError as exc:
            messagebox.showerror(
                _t("err.file_not_found_title"), _t(
                    "err.file_open", err=str(exc))
            )
            return False

        self.encoding, self.eol = self._sniff_encoding_eol(header)

        self._disable_heavy_features()
        self.text.config(state="normal", undo=False)
        self.text.delete("1.0", "end")
        self.hl.set_lang(self.lang)
        self.autocomplete.set_lang(self.lang)

        mb = file_size / (1024 * 1024)
        self._show_loading_overlay(
            f"\U0001f5fa\ufe0f  Indexando arquivo gigante ({mb:.0f} MB)\u2026\n"
            "Aguarde - isso ocorre apenas uma vez por sessão."
        )
        self._on_title(path.name, False)

        enc_for_vfb = (
            self.encoding.lower().replace(" bom", "").replace("utf-8-sig", "utf-8")
        )
        self._vfb = VirtualFileBuffer(
            path=path,
            encoding=enc_for_vfb,
            eol=self.eol,
            on_ready=self._on_vfb_ready,
        )
        return True

    def _on_vfb_ready(self, total_lines: int) -> None:
        """Callback da thread VFB - agenda continuação no thread principal."""
        self.after(0, lambda: self._giant_init_complete(total_lines))

    def _giant_init_complete(self, total_lines: int) -> None:
        """Executado no thread principal quando o índice VFB fica pronto."""
        self._hide_loading_overlay()
        vfb = self._vfb
        if total_lines == 0 or vfb is None:
            err = getattr(
                vfb, "error", "desconhecido") if vfb else "desconhecido"
            messagebox.showerror(
                _t("err.file_not_found_title"), f"Falha ao indexar arquivo: {err}"
            )
            self._is_giant_mode = False
            return

        lines_to_load = min(VIEWPORT_LINES, total_lines)
        initial_lines = vfb.get_lines(0, lines_to_load)
        content = "\n".join(initial_lines)
        content, _n_rem, _n_trunc = self._sanitize_for_display(content)
        self._sanitize_stats = (_n_rem, _n_trunc)

        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", content)
        self.text.edit_reset()
        self.text.edit_modified(False)
        self.modified = False
        self._viewport_start = 0

        self.hl.disable_large_file()
        self.gutter._update_width()
        self.gutter.redraw()
        self._show_giant_status_label()
        self._update_giant_status()
        if self.path:
            self._post_load_common(self.path)
        self._show_large_file_notice()
        if _n_rem or _n_trunc:
            self.after(
                300, lambda r=_n_rem, t=_n_trunc: self._show_sanitize_banner(
                    r, t)
            )

    def _schedule_viewport_shift(self, direction: int) -> None:
        """Agenda um shift de viewport com debounce de 120 ms."""
        if self._vp_shift_job:
            with contextlib.suppress(tk.TclError):
                self.after_cancel(self._vp_shift_job)
        self._vp_shift_job = self.after(
            120, lambda: self._do_viewport_shift(direction))

    def _do_viewport_shift(self, direction: int) -> None:
        """Executa o shift de viewport: persiste edições e recarrega novo intervalo."""
        self._vp_shift_job = None
        vfb = self._vfb
        if vfb is None or not vfb.is_ready:
            return

        total = vfb.total_lines
        old_start = self._viewport_start

        if direction > 0:
            new_start = min(old_start + VIEWPORT_SHIFT,
                            max(0, total - VIEWPORT_LINES))
        else:
            new_start = max(0, old_start - VIEWPORT_SHIFT)

        if new_start == old_start:
            return

        try:
            raw_w = self.text.get("1.0", "end-1c")
            w_lines = raw_w.split("\n")
            vfb.flush_widget_edits(w_lines, old_start)
        except tk.TclError:
            pass

        try:
            cur_idx = self.text.index(tk.INSERT)
            cur_rel = int(cur_idx.split(".")[0])
            cur_col = cur_idx.split(".")[1]
        except (tk.TclError, ValueError, IndexError):
            cur_rel, cur_col = 1, "0"

        end_line = min(new_start + VIEWPORT_LINES, total)
        new_lines = vfb.get_lines(new_start, end_line)
        new_content = "\n".join(new_lines)
        new_content, _, _ = self._sanitize_for_display(new_content)

        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", new_content)
        self._viewport_start = new_start

        shift_delta = new_start - old_start
        new_rel = max(1, cur_rel - shift_delta)
        with contextlib.suppress(tk.TclError):
            self.text.mark_set(tk.INSERT, f"{new_rel}.{cur_col}")
            self.text.see(tk.INSERT)

        self.gutter._schedule_redraw()
        self._update_giant_status()

        if vfb.has_edits() and not self.modified:
            self.modified = True
            name = self.path.name if self.path else _t("ui.tab_untitled")
            self._on_title(name, True)

    def _sync_scroll_y(self, first: str, last: str) -> None:
        """Sincroniza scrollbar, gutter e minimapa.

        Guard: pode ser chamado pelo yscrollcommand do tk.Text antes de
        self.gutter ser atribuído em _build_ui (a ordem de init é:
        text.config(yscrollcommand=...) -> gutter = LineNumbers(...)).
        hasattr() evita o AttributeError nessa janela de tempo.

        Estratégia anti-lag:
        - scrollbar e gutter são atualizados imediatamente (operações leves)
        - minimap usa seu próprio debounce (120ms) - atualiza só o retângulo
          de viewport durante scroll, evitando redraw completo
        - highlight de sintaxe é reagendado apenas quando o scroll para
          (via _hl_scroll_job com debounce de 100ms), não a cada frame
        - sticky_scroll e inline_blame também só são notificados no "scroll end"

        Em modo giant mapeia posição do viewport para posição real no arquivo
        completo e dispara viewport shifts quando o usuário se aproxima das bordas.
        """
        if not hasattr(self, "gutter"):
            return

        first_f = float(first)
        last_f = float(last)

        if self._is_giant_mode and self._vfb is not None and self._vfb.is_ready:
            total = self._vfb.total_lines
            vp = min(VIEWPORT_LINES, max(1, total))
            if total > 0:
                abs_first = (self._viewport_start + first_f * vp) / total
                abs_last = (self._viewport_start + last_f * vp) / total
                self._vsb.set(str(max(0.0, abs_first)),
                              str(min(1.0, abs_last)))
            else:
                self._vsb.set(first, last)
            self.gutter._schedule_redraw()

            if last_f > (1.0 - VIEWPORT_TRIGGER) and (
                self._viewport_start + VIEWPORT_LINES < total
            ):
                self._schedule_viewport_shift(+1)
            elif first_f < VIEWPORT_TRIGGER and self._viewport_start > 0:
                self._schedule_viewport_shift(-1)
            return

        self._vsb.set(first, last)
        self.gutter._schedule_redraw()

        self.minimap._schedule_viewport_update()

        if hasattr(self, "_hl_scroll_job") and self._hl_scroll_job:
            with contextlib.suppress(tk.TclError):
                self.after_cancel(self._hl_scroll_job)

        def _on_scroll_end() -> None:
            self._hl_scroll_job = None
            with contextlib.suppress(tk.TclError):
                self.hl.schedule(debounce=80)
            with contextlib.suppress(tk.TclError):
                if self.sticky_scroll:
                    self.sticky_scroll.notify_scroll()
            with contextlib.suppress(tk.TclError):
                if self.inline_blame and self._blame_enabled:
                    self.inline_blame.notify_scroll()

        self._hl_scroll_job = self.after(100, _on_scroll_end)

    def save(self) -> bool:
        """Salva o arquivo; redireciona para save_as se sem caminho."""
        if not self.path:
            return self.save_as()
        return self._write(self.path)

    def save_as(self) -> bool:
        """Abre diálogo para escolher caminho e escreve o arquivo."""
        p = filedialog.asksaveasfilename(
            defaultextension="",
            filetypes=[
                (_t("menu.file"), "*.*"),
                ("Python", "*.py"),
                ("JavaScript", "*.js"),
                ("TypeScript", "*.ts"),
                ("HTML", "*.html"),
                ("CSS", "*.css"),
                ("JSON", "*.json"),
                ("Markdown", "*.md"),
            ],
            initialfile=self.path.name if self.path else "untitled",
        )
        if not p:
            return False
        self.path = Path(p)
        self.lang = EXT_LANG.get(self.path.suffix.lower(), "text")
        self.hl.set_lang(self.lang)
        self.autocomplete.set_lang(self.lang)
        return self._write(self.path)

    def revert(self) -> None:
        """Reverte para o último conteúdo salvo."""
        if not self.path or not self.path.exists():
            return
        if self.modified:
            name = self.path.name
            if not messagebox.askyesno(
                _t("edit.revert"), _t("edit.revert_confirm", name=name), parent=self
            ):
                return
        self.load(self.path)

    def _write(self, path: Path) -> bool:
        """Escreve o arquivo no disco.

        Em modo giant, usa streaming via VirtualFileBuffer (zero-copy para linhas
        não editadas). Nos outros modos usa escrita atômica convencional.
        """
        with contextlib.suppress(Exception):
            if getattr(self, "file_watcher", None):
                self.file_watcher.pause()
        try:
            trim = bool(self._cfg.get("trim_trailing_whitespace"))
            fnl = bool(self._cfg.get("insert_final_newline"))
            eol = self.eol
            enc = self._cfg.get("encoding", "utf-8") or "utf-8"

            if self._is_giant_mode and self._vfb is not None and self._vfb.is_ready:
                try:
                    raw_w = self.text.get("1.0", "end-1c")
                    w_lines = raw_w.split("\n")
                    self._vfb.flush_widget_edits(w_lines, self._viewport_start)
                except tk.TclError:
                    pass

                self._vfb.save_to_path(
                    dest=path,
                    eol=eol,
                    encoding=enc,
                    trim_trailing=trim,
                    insert_final_newline=fnl,
                )
                self.text.edit_modified(False)
                self.modified = False
                self._on_title(path.name, False)
                _notify_saved(path)
                return True

            content = self.text.get("1.0", "end-1c")

            if not self._is_large_mode and self._cfg.get("format_on_save") and path:
                with contextlib.suppress(tk.TclError):
                    formatted = self._format_content(content)
                    if formatted and formatted != content:
                        pos = self.text.index(tk.INSERT)
                        self.text.delete("1.0", "end")
                        self.text.insert("1.0", formatted.rstrip("\n"))
                        with contextlib.suppress(tk.TclError):
                            self.text.mark_set(tk.INSERT, pos)
                        content = formatted.rstrip("\n")

            if trim:
                content = "\n".join(ln.rstrip() for ln in content.split("\n"))

            file_content = content
            if fnl and not file_content.endswith("\n"):
                file_content += "\n"
            if eol != "\n":
                file_content = file_content.replace("\n", eol)

            tmp = path.with_suffix(path.suffix + ".codis_tmp")
            try:
                tmp.write_text(file_content, encoding=enc)
                tmp.replace(path)
            except OSError:
                with contextlib.suppress(OSError):
                    tmp.unlink()
                path.write_text(file_content, encoding=enc)

            self._last_saved_content = content
            self.text.edit_modified(False)
            self.modified = False
            self._on_title(path.name, False)

            if self._lsp_mgr and path:
                with contextlib.suppress(tk.TclError):
                    self._lsp_mgr.did_save(path, self.lang, content)

            with contextlib.suppress(tk.TclError):
                FileTimeline.save_backup(
                    str(path),
                    content,
                    max_backups=self._cfg.get("timeline_max_backups", 20),
                    max_age_days=self._cfg.get("timeline_max_age_days", 30),
                )
            _notify_saved(path)
            return True

        except OSError as exc:
            messagebox.showerror(_t("err.file_save"), _t(
                "err.file_save", err=str(exc)))
            return False
        finally:
            with contextlib.suppress(Exception):
                if getattr(self, "file_watcher", None):
                    self.file_watcher.resume(grace_ms=1500)

    def _format_content(self, content: str) -> str | None:
        """Formata o conteúdo com a ferramenta apropriada."""
        if self.lang in (
            "javascript",
            "typescript",
            "json",
            "css",
            "scss",
            "html",
            "markdown",
        ):
            if self.path:
                return self._lint_mgr.format_prettier(self.path, content)
        elif self.lang == "python":
            return self._lint_mgr.format_python(content)
        return None

    def format_document(self) -> None:
        """Formata o documento atual."""
        content = self.text.get("1.0", "end-1c")
        formatted = self._format_content(content)
        if formatted and formatted.rstrip("\n") != content.rstrip("\n"):
            pos = self.text.index(tk.INSERT)
            self.text.delete("1.0", "end")
            self.text.insert("1.0", formatted.rstrip("\n"))
            with contextlib.suppress(tk.TclError):
                self.text.mark_set(tk.INSERT, pos)
            self.hl.schedule(debounce=0)
        else:
            lang_name = LANG_LABEL.get(self.lang, self.lang)
            if formatted is None:
                messagebox.showinfo(
                    _t("run.format_document"),
                    _t("msg.no_formatter", lang=lang_name),
                )

    def toggle_comment(self) -> None:
        """Ctrl+/ - comenta/descomenta linhas selecionadas."""
        comment = LANG_COMMENT.get(self.lang)
        if not comment:
            self._toggle_block_comment()
            return
        try:
            if self.text.tag_ranges(tk.SEL):
                start = int(self.text.index(
                    tk.SEL_FIRST).split(".", maxsplit=1)[0])
                end = int(self.text.index(
                    tk.SEL_LAST).split(".", maxsplit=1)[0])

                if self.text.index(tk.SEL_LAST).split(".", maxsplit=1)[1] == "0":
                    end = max(start, end - 1)
            else:
                start = end = int(self.text.index(
                    tk.INSERT).split(".", maxsplit=1)[0])

            all_commented = all(
                self.text.get(
                    f"{ln}.0", f"{ln}.end").lstrip().startswith(comment)
                for ln in range(start, end + 1)
                if self.text.get(f"{ln}.0", f"{ln}.end").strip()
            )

            for ln in range(start, end + 1):
                line = self.text.get(f"{ln}.0", f"{ln}.end")
                stripped = line.lstrip()
                indent = len(line) - len(stripped)

                if all_commented:
                    if stripped.startswith(comment + " "):
                        new = " " * indent + stripped[len(comment) + 1:]
                    elif stripped.startswith(comment):
                        new = " " * indent + stripped[len(comment):]
                    else:
                        new = line
                else:
                    new = " " * indent + comment + " " + stripped

                self.text.delete(f"{ln}.0", f"{ln}.end")
                self.text.insert(f"{ln}.0", new)
        except tk.TclError:
            pass

    def _toggle_block_comment(self) -> None:
        """Alterna comentário de bloco para CSS/HTML."""
        try:
            if self.text.tag_ranges(tk.SEL):
                sel = self.text.get(tk.SEL_FIRST, tk.SEL_LAST)
                if sel.startswith("/*") and sel.endswith("*/"):
                    new = sel[2:-2].strip()
                else:
                    new = f"/* {sel} */"
                self.text.delete(tk.SEL_FIRST, tk.SEL_LAST)
                self.text.insert(tk.INSERT, new)
        except tk.TclError:
            pass

    def duplicate_line(self) -> None:
        """Duplica a linha atual (Ctrl+D)."""
        try:
            pos = self.text.index(tk.INSERT)
            ln = pos.split(".", maxsplit=1)[0]
            line = self.text.get(f"{ln}.0", f"{ln}.end")
            self.text.insert(f"{ln}.end", "\n" + line)
        except tk.TclError:
            pass

    def delete_line(self) -> None:
        """Deleta a linha atual."""
        try:
            pos = self.text.index(tk.INSERT)
            ln = pos.split(".", maxsplit=1)[0]

            self.text.delete(f"{ln}.0", f"{int(ln) + 1}.0")
        except tk.TclError:
            pass

    def move_line(self, direction: int) -> None:
        """Move a linha para cima (-1) ou para baixo (+1)."""
        try:
            pos = self.text.index(tk.INSERT)
            ln = int(pos.split(".", maxsplit=1)[0])
            col = pos.split(".", maxsplit=1)[1]

            if direction == -1 and ln <= 1:
                return
            total = int(self.text.index("end").split(".", maxsplit=1)[0])
            if direction == 1 and ln >= total - 1:
                return

            curr_line = self.text.get(f"{ln}.0", f"{ln}.end")
            other_ln = ln + direction
            other_line = self.text.get(f"{other_ln}.0", f"{other_ln}.end")

            self.text.delete(f"{ln}.0", f"{ln}.end")
            self.text.insert(f"{ln}.0", other_line)
            self.text.delete(f"{other_ln}.0", f"{other_ln}.end")
            self.text.insert(f"{other_ln}.0", curr_line)
            self.text.mark_set(tk.INSERT, f"{other_ln}.{col}")
        except tk.TclError:
            pass

    def select_line(self) -> None:
        """Seleciona a linha atual."""
        try:
            pos = self.text.index(tk.INSERT)
            ln = pos.split(".", maxsplit=1)[0]
            self.text.tag_remove(tk.SEL, "1.0", "end")
            self.text.tag_add(tk.SEL, f"{ln}.0", f"{ln}.end+1c")
            self.text.mark_set(tk.INSERT, f"{ln}.end")
        except tk.TclError:
            pass

    def delete_word_left(self) -> str:
        """Ctrl+Backspace: apaga a palavra à esquerda."""
        try:
            pos = self.text.index(tk.INSERT)
            line = self.text.get(f"{pos} linestart", pos)
            m = re.search(r"(\w+|\W+)\s*$", line)
            if m:
                start = f"{pos} - {len(m.group())}c"
                self.text.delete(start, pos)
        except tk.TclError:
            pass
        return "break"

    def delete_word_right(self) -> str:
        """Ctrl+Delete: apaga a palavra à direita."""
        try:
            pos = self.text.index(tk.INSERT)
            rest = self.text.get(pos, f"{pos} lineend")
            m = re.match(r"\s*(\w+|\W+)", rest)
            if m:
                self.text.delete(pos, f"{pos} + {len(m.group())}c")
        except tk.TclError:
            pass
        return "break"

    def sort_lines(self, reverse: bool = False) -> None:
        """Ordena as linhas selecionadas."""
        try:
            if self.text.tag_ranges(tk.SEL):
                start = self.text.index(tk.SEL_FIRST).split(".", maxsplit=1)[0]
                end = self.text.index(tk.SEL_LAST).split(".", maxsplit=1)[0]
            else:
                start = end = "1"
                end = str(
                    int(self.text.index("end").split(".", maxsplit=1)[0]) - 1)
            s_ln, e_ln = int(start), int(end)
            lines = [
                self.text.get(f"{ln}.0", f"{ln}.end") for ln in range(s_ln, e_ln + 1)
            ]
            lines.sort(reverse=reverse)
            for i, ln in enumerate(range(s_ln, e_ln + 1)):
                self.text.delete(f"{ln}.0", f"{ln}.end")
                self.text.insert(f"{ln}.0", lines[i])
        except tk.TclError:
            pass

    def join_lines(self) -> None:
        """Une a linha atual com a próxima."""
        try:
            pos = self.text.index(tk.INSERT)
            ln = int(pos.split(".", maxsplit=1)[0])
            self.text.delete(f"{ln}.end", f"{ln + 1}.0")
            self.text.insert(f"{ln}.end", " ")
        except tk.TclError:
            pass

    def transform_case(self, mode: str) -> None:
        """Transforma a seleção: upper, lower, title, camel, snake, kebab, pascal, constant."""
        try:
            with contextlib.suppress(ImportError):
                from src.editor.text_transforms import apply_transform

                case_map = {
                    "camel": "camel",
                    "snake": "snake",
                    "kebab": "kebab",
                    "pascal": "pascal",
                    "constant": "constant",
                }
                if mode in case_map:
                    apply_transform(self.text, case_map[mode])
                    return
            if self.text.tag_ranges(tk.SEL):
                sel = self.text.get(tk.SEL_FIRST, tk.SEL_LAST)
                if mode == "upper":
                    new = sel.upper()
                elif mode == "lower":
                    new = sel.lower()
                elif mode == "title":
                    new = sel.title()
                else:
                    return
                self.text.delete(tk.SEL_FIRST, tk.SEL_LAST)
                self.text.insert(tk.INSERT, new)
        except tk.TclError:
            pass

    def trim_trailing_whitespace(self) -> None:
        """Remove espaços/tabs do final de cada linha.

        Em modo giant, delega ao VFB (o trim ocorre no save_to_path). Em modo
        large, processa o conteúdo do widget em chunks para não bloquear a UI.
        """
        if self._is_giant_mode:
            self._show_context_info("Espaços finais serão removidos ao salvar")
            return
        try:
            content = self.text.get("1.0", "end-1c")
            lines = content.split("\n")
            new_content = "\n".join(l_.rstrip() for l_ in lines)
            if new_content != content:
                pos = self.text.index(tk.INSERT)
                self.text.delete("1.0", "end")
                self.text.insert("1.0", new_content)
                with contextlib.suppress(tk.TclError):
                    self.text.mark_set(tk.INSERT, pos)
        except tk.TclError:
            pass

    def zoom(self, delta: int) -> None:
        """Aumenta (+1) ou diminui (-1) o tamanho da fonte (Ctrl+= / Ctrl+-)."""
        if delta == 0:
            self._font_size = self._cfg.get("font_size", FONT_SIZE)
        else:
            self._font_size = max(FONT_MIN, min(
                FONT_MAX, self._font_size + delta))
        self.text.config(font=(self._font_family, self._font_size))
        self.gutter.update_font(self._font_family, self._font_size)
        self.hl.reconfigure()

    def toggle_word_wrap(self) -> None:
        """Alterna o modo de quebra de linha (ativado ↔ desativado)."""
        self.set_word_wrap(not self._word_wrap)

    def set_word_wrap(self, enabled: bool) -> None:
        """Define explicitamente o estado da quebra de linha (idempotente)."""
        if self._word_wrap == enabled:
            return
        self._word_wrap = enabled
        self.text.config(wrap="word" if self._word_wrap else "none")

    def toggle_minimap(self) -> None:
        """Alterna a visibilidade do minimapa (ativado ↔ desativado)."""
        self.set_minimap(not self._show_minimap)

    def set_minimap(self, enabled: bool) -> None:
        """Define explicitamente a visibilidade do minimapa (idempotente)."""
        if self._show_minimap == enabled:
            return
        self._show_minimap = enabled
        if self._show_minimap:
            self._minimap_frame.pack(side="right", fill="y")
        else:
            self._minimap_frame.pack_forget()

    def goto_line(self, line: int, col: int = 0) -> None:
        """Posiciona o cursor na linha e coluna especificadas, garantindo visibilidade."""
        try:
            idx = f"{line}.{col}"
            self.text.mark_set(tk.INSERT, idx)
            self.text.see(idx)
            self.hl.hl_current_line()
            self._update_cursor()
        except tk.TclError:
            pass

    def goto_line_dialog(self) -> None:
        """Abre diálogo para o usuário digitar um número de linha e navega até ela."""
        result = simpledialog.askstring(
            _t("go.to_line"),
            _t("go.to_line") + ":",
            parent=self,
        )
        if result:
            try:
                if ":" in result:
                    ln, col = map(int, result.split(":", 1))
                elif "," in result:
                    ln, col = map(int, result.split(",", 1))
                else:
                    ln, col = int(result), 0
                self.goto_line(ln, col)
            except ValueError:
                pass

    def get_word_at_cursor(self) -> str:
        """Retorna a palavra sob o cursor."""
        try:
            pos = self.text.index(tk.INSERT)
            line = self.text.get(f"{pos} linestart", f"{pos} lineend")
            col = int(pos.split(".", maxsplit=1)[1])
            for m in re.finditer(r"\b\w+\b", line):
                if m.start() <= col <= m.end():
                    return m.group()
        except (tk.TclError, ValueError):
            pass
        return ""

    @property
    def filepath(self) -> str | None:
        """Caminho do arquivo como string, ou ``None``."""
        return str(self.path) if self.path else None

    def get_content(self) -> str:
        """Retorna o conteúdo completo do editor como string."""
        return self.text.get("1.0", "end-1c")

    def set_lsp_manager(self, lsp_mgr: LSPManager | None) -> None:
        """Conecta o gerenciador LSP. Chamado pelo CodisApp após a inicialização."""
        self._lsp_mgr = lsp_mgr
        if lsp_mgr and self.path:
            self.autocomplete.set_lsp_manager(lsp_mgr, self.path)
        elif lsp_mgr:
            self.autocomplete.set_lsp_manager(lsp_mgr, None)

    @staticmethod
    def _on_lsp_hint(lang: str) -> None:
        """Registra no log uma dica de instalação quando não há servidor LSP disponível.

        O log é capturado pelo handler de output panel do CodisApp, tornando
        a mensagem visível para o usuário sem violar a injeção de dependência
        do EditorPane. Exibido no máximo uma vez por linguagem por sessão.
        """
        from src.editor.pane import EditorPane

        already_warned: set = getattr(EditorPane, "_lsp_warned_langs", set())
        if lang in already_warned:
            return
        already_warned.add(lang)
        EditorPane._lsp_warned_langs = already_warned

        install_hints = {
            "python": "pip install python-lsp-server",
            "javascript": "npm i -g typescript-language-server typescript",
            "typescript": "npm i -g typescript-language-server typescript",
            "rust": "rustup component add rust-analyzer",
            "go": "go install golang.org/x/tools/gopls@latest",
            "c": "apt install clangd  (ou brew install llvm)",
            "cpp": "apt install clangd  (ou brew install llvm)",
            "lua": "brew install lua-language-server",
            "bash": "npm i -g bash-language-server",
            "shell": "npm i -g bash-language-server",
            "markdown": "brew install marksman  (ou baixe em github.com/artempyanykh/marksman)",
        }
        hint = install_hints.get(lang, "")
        if hint:
            logger.info(
                "LSP para '%s' não encontrado. Instale com: %s "
                "(ver wiki/linting.md para guia completo)",
                lang,
                hint,
            )
        else:
            logger.info(
                "LSP para '%s' não encontrado. Consulte wiki/linting.md para guias de instalação.",
                lang,
            )

    def _lsp_did_change(self, content: str) -> None:
        """Notificação LSP textDocument/didChange com debounce."""
        if self._lsp_mgr and self.path:
            with contextlib.suppress(tk.TclError):
                self._lsp_mgr.did_change(self.path, self.lang, content)

            _doc_langs = {
                "javascript",
                "typescript",
                "python",
                "rust",
                "go",
                "java",
                "kotlin",
                "swift",
                "csharp",
                "cpp",
                "c",
                "ruby",
                "lua",
                "php",
                "dart",
                "elixir",
                "r",
                "julia",
            }
            if self.lang in _doc_langs and self.path:
                _fname = str(self.path)
                self._doc_symbols = JSDocParser.parse_file(content, _fname)

    def _set_ctrl(self, pressed: bool) -> None:
        """Rastreia se a tecla Ctrl está pressionada - usada pelo balloon para pinar."""
        with contextlib.suppress(tk.TclError):
            self.tk.call("set", "_codis_ctrl_held", "1" if pressed else "0")

    def _on_ctrl_click(self, event: tk.Event) -> None:

        with contextlib.suppress(tk.TclError):
            self.lsp_goto_definition()

    def lsp_goto_definition(self) -> None:
        """Vai para a definição via LSP (F12 / Ctrl+Clique)."""
        from src.theme.utils import lsp_loc_pair

        if not self._lsp_mgr or not self.path:
            self._local_goto_definition()
            return
        try:
            idx = self.text.index(tk.INSERT)
            line = int(idx.split(".", maxsplit=1)[0]) - 1
            col = int(idx.split(".", maxsplit=1)[1])

            client = self._lsp_mgr.client_for(self.lang)
            if client is None:
                self._local_goto_definition()
                return

            def _do_request() -> None:
                if client.starting and not client.wait_initialized(timeout=5.0):
                    self.after(0, self._local_goto_definition)
                    return
                if not client.running:
                    self.after(0, self._local_goto_definition)
                    return

                def _cb(locations: list) -> None:
                    if not locations:
                        self._local_goto_definition()
                        return
                    loc = locations[0] if isinstance(
                        locations, list) else locations
                    try:
                        uri, tline = lsp_loc_pair(loc)
                        tpath = _lsp_uri_to_path(uri)
                        self.after(
                            0, lambda: self._goto_location(tpath, tline))
                    except (ValueError, AttributeError, KeyError):
                        self.after(0, self._local_goto_definition)

                client.goto_definition(self.path, line, col, _cb)

            import threading as _thr

            _thr.Thread(target=_do_request, daemon=True,
                        name="lsp-goto-def").start()
        except (tk.TclError, ValueError):
            self._local_goto_definition()

    def lsp_find_references(self) -> None:
        """Localiza todas as referências via LSP (Shift+F12)."""
        if not self._lsp_mgr or not self.path:
            return
        try:
            idx = self.text.index(tk.INSERT)
            line = int(idx.split(".", maxsplit=1)[0]) - 1
            col = int(idx.split(".", maxsplit=1)[1])

            client = self._lsp_mgr.client_for(self.lang)
            if client is None:
                return

            def _do_request() -> None:
                if client.starting and not client.wait_initialized(timeout=5.0):
                    return
                if not client.running:
                    return

                def _cb(refs: list) -> None:
                    from src.theme.utils import lsp_loc_pair

                    if not refs:
                        self.after(0, lambda: self._show_refs_popup(""))
                        return
                    linhas: list[str] = []
                    for r in refs if isinstance(refs, list) else []:
                        try:
                            uri, tline = lsp_loc_pair(r)
                            tpath = _lsp_uri_to_path(uri)
                            linhas.append(f"{tpath}:{tline}")
                        except (ValueError, AttributeError, KeyError):
                            pass
                    msg = "\n".join(linhas[:50])
                    if len(refs) > 50:
                        msg += f"\n...e mais {len(refs) - 50}"
                    self.after(0, lambda: self._show_refs_popup(msg))

                client.find_references(self.path, line, col, _cb)

            import threading as _thr

            _thr.Thread(target=_do_request, daemon=True,
                        name="lsp-find-refs").start()
        except (tk.TclError, ValueError):
            pass

    def lsp_rename_symbol(self) -> None:
        """Renomeia o símbolo via LSP (F6)."""
        if not self._lsp_mgr or not self.path:
            return
        new_name = simpledialog.askstring(
            _t("lsp.rename_symbol"), _t("lsp.new_name"), parent=self
        )
        if not new_name:
            return
        try:
            idx = self.text.index(tk.INSERT)
            line = int(idx.split(".", maxsplit=1)[0]) - 1
            col = int(idx.split(".", maxsplit=1)[1])

            def _rename_cb(workspace_edit):
                if not workspace_edit:
                    return
                with contextlib.suppress(tk.TclError):
                    changes = (
                        workspace_edit.get("changes")
                        or workspace_edit.get("documentChanges")
                        or {}
                    )
                    if isinstance(changes, list):
                        for ch in changes:
                            edits = ch.get("edits", [])
                            uri = ch.get("textDocument", {}).get("uri", "")
                            self._apply_text_edits(uri, edits)
                    elif isinstance(changes, dict):
                        for uri, edits in changes.items():
                            self._apply_text_edits(uri, edits)

            self._lsp_mgr.rename(
                self.path, self.lang, line, col, new_name, callback=_rename_cb
            )
        except (tk.TclError, ValueError):
            pass

    @staticmethod
    def _apply_text_edits(uri: str, edits: list) -> None:
        """Aplica edições de texto LSP ao editor correspondente."""

    def apply_lsp_edits(self, edits: list) -> None:
        """Aplica uma lista de TextEdit (objetos ou dicts) ao conteúdo do editor."""
        try:

            def _line(e: object) -> int:
                if hasattr(e, "range"):
                    return e.range.start.line
                return e.get("range", {}).get("start", {}).get("line", 0)

            sorted_edits = sorted(edits, key=_line, reverse=True)
            self.text.config(state="normal")
            for edit in sorted_edits:
                if hasattr(edit, "range"):
                    rng = edit.range
                    new_text = edit.new_text
                    sl, sc = rng.start.line + 1, rng.start.character
                    el, ec = rng.end.line + 1, rng.end.character
                else:
                    rng = edit.get("range", {})
                    new_text = edit.get("newText", "")
                    st = rng.get("start", {})
                    en = rng.get("end", {})
                    sl, sc = st.get("line", 0) + 1, st.get("character", 0)
                    el, ec = en.get("line", 0) + 1, en.get("character", 0)
                self.text.delete(f"{sl}.{sc}", f"{el}.{ec}")
                self.text.insert(f"{sl}.{sc}", new_text)
        except (tk.TclError, AttributeError, TypeError):
            pass

    def _lsp_cursor_pos(self) -> tuple[int, int]:
        """Retorna (line_0based, col) da posição atual do cursor."""
        idx = self.text.index(tk.INSERT)
        line = int(idx.split(".", maxsplit=1)[0]) - 1
        col = int(idx.split(".", maxsplit=1)[1])
        return line, col

    def _lsp_run_in_bg(self, fn, name: str = "lsp-bg") -> None:
        """Executa fn em thread de background daemon."""
        import threading as _thr

        _thr.Thread(target=fn, daemon=True, name=name).start()

    def _lsp_wait_client(self, client) -> bool:
        """Aguarda o cliente inicializar (uso em threads de background). Retorna False se falhar."""
        if client.starting:
            return client.wait_initialized(timeout=6.0)
        return client.running

    def _lsp_goto_single(
        self,
        client_method_name: str,
        bg_tag: str,
    ) -> None:
        """Implementação comum para goto_declaration / goto_type_definition.

        Ambos têm comportamento idêntico (navega para o primeiro resultado);
        só diferem no método LSP chamado e na tag de background thread.
        """
        from src.theme.utils import lsp_loc_pair

        if not self._lsp_mgr or not self.path:
            return
        try:
            line, col = self._lsp_cursor_pos()
            client = self._lsp_mgr.client_for(self.lang)
            if not client:
                return
            method = getattr(client, client_method_name)

            def _cb(locs: list) -> None:
                if not locs:
                    return
                uri, tline = lsp_loc_pair(locs[0])
                tpath = _lsp_uri_to_path(uri)
                self.after(0, lambda: self._goto_location(tpath, tline))

            def _run() -> None:
                if self._lsp_wait_client(client):
                    method(self.path, line, col, _cb)

            self._lsp_run_in_bg(_run, bg_tag)
        except (tk.TclError, ValueError):
            pass

    def lsp_goto_declaration(self) -> None:
        """Vai para a declaração do símbolo via LSP."""
        self._lsp_goto_single("goto_declaration", "lsp-goto-decl")

    def lsp_goto_type_definition(self) -> None:
        """Vai para a definição de tipo do símbolo via LSP."""
        self._lsp_goto_single("goto_type_definition", "lsp-goto-typedef")

    def lsp_goto_implementation(self) -> None:
        """Vai para a implementação do símbolo via LSP."""
        from src.theme.utils import lsp_loc_pair

        if not self._lsp_mgr or not self.path:
            return
        try:
            line, col = self._lsp_cursor_pos()
            client = self._lsp_mgr.client_for(self.lang)
            if not client:
                return

            def _cb(locs: list) -> None:
                if not locs:
                    return
                if len(locs) == 1:
                    uri, tline = lsp_loc_pair(locs[0])
                    tpath = _lsp_uri_to_path(uri)
                    self.after(0, lambda: self._goto_location(tpath, tline))
                else:
                    lines = [
                        f"{_lsp_uri_to_path(lsp_loc_pair(r)[0])}:{lsp_loc_pair(r)[1]}"
                        for r in locs[:50]
                    ]
                    msg = "\n".join(lines)
                    self.after(0, lambda: self._show_refs_popup(msg))

            def _run() -> None:
                if self._lsp_wait_client(client):
                    client.goto_implementation(self.path, line, col, _cb)

            self._lsp_run_in_bg(_run, "lsp-goto-impl")
        except (tk.TclError, ValueError):
            pass

    def lsp_code_action(self) -> None:
        """Mostra code actions (quick fix, refactor...) via LSP para a posição atual."""
        if not self._lsp_mgr or not self.path:
            return
        try:
            line, col = self._lsp_cursor_pos()
            client = self._lsp_mgr.client_for(self.lang)
            if not client:
                return

            from src.lsp.protocol import Position as LSPPos
            from src.lsp.protocol import Range as LSPRange

            rng = LSPRange(LSPPos(line, col), LSPPos(line, col))

            def _run() -> None:
                if not self._lsp_wait_client(client):
                    return

                def _cb(actions: list) -> None:
                    if not actions:
                        self.after(
                            0,
                            lambda: messagebox.showinfo(
                                "Code Actions", "Nenhuma ação disponível aqui."
                            ),
                        )
                        return
                    self.after(
                        0, lambda a=actions: self._show_code_actions_popup(a))

                client.code_action(self.path, rng, [], _cb)

            self._lsp_run_in_bg(_run, "lsp-code-action")
        except (tk.TclError, ValueError):
            pass

    def _show_code_actions_popup(self, actions: list) -> None:
        """Exibe menu popup com as code actions disponíveis."""
        menu = tk.Menu(self, tearoff=False)
        t = self._tmgr.theme
        menu.config(
            bg=t.floating_bg,
            fg=t.fg,
            activebackground=t.sel_bg,
            activeforeground=t.sel_fg,
        )
        for action in actions[:20]:
            title = (
                action.title if hasattr(
                    action, "title") else action.get("title", "")
            )
            kind_lbl = (
                action.kind_label
                if hasattr(action, "kind_label")
                else action.get("kind", "")
            )
            label = f"{kind_lbl}: {title}" if kind_lbl else title

            def _apply(a=action) -> None:
                self._apply_code_action(a)

            menu.add_command(label=label, command=_apply)
        try:
            x = self.winfo_rootx() + self.text.winfo_x()
            y = self.winfo_rooty() + self.text.winfo_y()
            menu.tk_popup(x + 20, y + 20)
        except tk.TclError:
            pass

    def _apply_code_action(self, action) -> None:
        """Aplica o WorkspaceEdit de uma code action, ou executa seu command."""
        try:
            edit = action.edit if hasattr(action, "edit") else None
            if edit:
                doc_changes = getattr(edit, "document_changes", []) or []
                for ch in doc_changes:
                    if not isinstance(ch, dict):
                        continue
                    uri = ch.get("textDocument", {}).get("uri", "")
                    edits = ch.get("edits", [])
                    if uri and str(self.path) in uri:
                        self.apply_lsp_edits(edits)
                for uri, text_edits in (getattr(edit, "changes", None) or {}).items():
                    if str(self.path) in uri:
                        self.apply_lsp_edits(text_edits)
            cmd = action.command if hasattr(action, "command") else None
            if cmd and self._lsp_mgr:
                command_id = (
                    cmd.get("command", "")
                    if isinstance(cmd, dict)
                    else getattr(cmd, "command", "")
                )
                arguments = (
                    cmd.get("arguments")
                    if isinstance(cmd, dict)
                    else getattr(cmd, "arguments", None)
                )
                self._lsp_mgr.execute_command(self.lang, command_id, arguments)
        except Exception:
            logger.debug("Suppressed error", exc_info=True)

    def lsp_incoming_calls(self) -> None:
        """Solicita ao servidor LSP as chamadas de entrada do símbolo sob o cursor."""
        if not self._lsp_mgr or not self.path:
            return
        try:
            line, col = self._lsp_cursor_pos()
            client = self._lsp_mgr.client_for(self.lang)
            if not client:
                return

            def _run() -> None:
                if not self._lsp_wait_client(client):
                    return

                def _on_items(items: list) -> None:
                    if not items:
                        self.after(
                            0,
                            lambda: messagebox.showinfo(
                                "Call Hierarchy", "Nenhum resultado."
                            ),
                        )
                        return
                    item = items[0]

                    def _on_calls(calls: list) -> None:
                        if not calls:
                            self.after(
                                0,
                                lambda: messagebox.showinfo(
                                    "Incoming Calls", "Nenhuma chamada encontrada."
                                ),
                            )
                            return
                        lines: list[str] = []
                        for c in calls[:50]:
                            ci = c.item if hasattr(c, "item") else {}
                            name = (
                                ci.name if hasattr(
                                    ci, "name") else ci.get("name", "?")
                            )
                            uri = ci.uri if hasattr(
                                ci, "uri") else ci.get("uri", "")
                            tpath = _lsp_uri_to_path(uri)
                            lines.append(f"{name}  ({tpath})")
                        self.after(
                            0, lambda m="\n".join(
                                lines): self._show_refs_popup(m)
                        )

                    client.incoming_calls(item, _on_calls)

                client.prepare_call_hierarchy(self.path, line, col, _on_items)

            self._lsp_run_in_bg(_run, "lsp-incoming-calls")
        except (tk.TclError, ValueError):
            pass

    def lsp_outgoing_calls(self) -> None:
        """Solicita ao servidor LSP as chamadas de saída do símbolo sob o cursor."""
        if not self._lsp_mgr or not self.path:
            return
        try:
            line, col = self._lsp_cursor_pos()
            client = self._lsp_mgr.client_for(self.lang)
            if not client:
                return

            def _run() -> None:
                if not self._lsp_wait_client(client):
                    return

                def _on_items(items: list) -> None:
                    if not items:
                        self.after(
                            0,
                            lambda: messagebox.showinfo(
                                "Call Hierarchy", "Nenhum resultado."
                            ),
                        )
                        return
                    item = items[0]

                    def _on_calls(calls: list) -> None:
                        if not calls:
                            self.after(
                                0,
                                lambda: messagebox.showinfo(
                                    "Outgoing Calls", "Nenhuma chamada encontrada."
                                ),
                            )
                            return
                        lines: list[str] = []
                        for c in calls[:50]:
                            ci = c.item if hasattr(c, "item") else {}
                            name = (
                                ci.name if hasattr(
                                    ci, "name") else ci.get("name", "?")
                            )
                            uri = ci.uri if hasattr(
                                ci, "uri") else ci.get("uri", "")
                            tpath = _lsp_uri_to_path(uri)
                            lines.append(f"{name}  ({tpath})")
                        self.after(
                            0, lambda m="\n".join(
                                lines): self._show_refs_popup(m)
                        )

                    client.outgoing_calls(item, _on_calls)

                client.prepare_call_hierarchy(self.path, line, col, _on_items)

            self._lsp_run_in_bg(_run, "lsp-outgoing-calls")
        except (tk.TclError, ValueError):
            pass

    def _goto_location(self, target_path: Path, target_line: int) -> None:
        """Despacha para o callback externo se definido, caso contrário não faz nada."""
        cb = getattr(self, "_goto_location_cb", None)
        if cb is not None:
            cb(target_path, target_line)

    @staticmethod
    def _show_refs_popup(text: str) -> None:
        """Exibe os resultados de "localizar referências" em um popup simples."""
        messagebox.showinfo(_t("lsp.references"),
                            text or _t("lsp.no_references"))

    def set_goto_callback(self, cb) -> None:
        """Define o callback externo para navegar a arquivo:linha."""
        self._goto_location_cb = cb
