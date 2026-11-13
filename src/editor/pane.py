"""Painel de edição principal do Codis - ``EditorPane``.

Widget central do IDE: combina edição de texto com highlight de sintaxe,
números de linha, minimap, multi-cursor, vim mode, sticky scroll e toda
a integração LSP via ``_PaneLSPMixin``.
"""

from __future__ import annotations

import contextlib
import logging
import re
import tkinter as tk
import tkinter.font as tkfont
from collections.abc import Callable
from pathlib import Path
from tkinter import ttk

from src.config import ConfigManager
from src.constants import (
    _SANITIZE_TABLE,
    AUTO_CLOSE,
    FONT_MONO,
    FONT_SIZE,
    FONT_UI,
    LINT_JS,
    MAX_LINE_RENDER_CHARS,
    MINIMAP_WIDTH,
    TAB_SIZE,
    VIEWPORT_LINES,
)
from src.editor._pane_lsp import _PaneLSPMixin
from src.editor.autocomplete import AutocompletePopup
from src.editor.kdocs import (
    JSDocParser,
    KDocsBalloon,
    KDocsNotes,
    KDocsParser,
)
from src.editor.line_numbers import LineNumbers
from src.editor.minimap import MiniMap
from src.editor.snippets import SnippetEngine
from src.i18n import _t
from src.lint.manager import Diag, LintManager
from src.syntax.highlighter import _OM_CACHE, SyntaxHL
from src.theme.base import Theme
from src.theme.manager import ThemeManager

try:
    _VFB_AVAILABLE = True
except ImportError:
    _VFB_AVAILABLE = False

try:
    from src.editor.sticky_scroll import StickyScroll

    _STICKY_AVAILABLE = True
except ImportError:
    _STICKY_AVAILABLE = False

try:
    from src.editor.code_folding import CodeFolding

    _FOLDING_AVAILABLE = True
except ImportError:
    _FOLDING_AVAILABLE = False

try:
    from src.editor.complexity_lens import ComplexityLens

    _COMPLEXITY_LENS_OK = True
except ImportError:
    _COMPLEXITY_LENS_OK = False

try:
    from src.editor.focus_block import FocusBlock

    _FOCUS_BLOCK_OK = True
except ImportError:
    _FOCUS_BLOCK_OK = False

try:
    from src.editor.sticky_note_gutter import StickyNoteGutter

    _STICKY_NOTES_OK = True
except ImportError:
    _STICKY_NOTES_OK = False

try:
    from src.editor.inline_blame import InlineBlame

    _BLAME_AVAILABLE = True
except ImportError:
    _BLAME_AVAILABLE = False

try:
    from src.editor.color_preview import ColorPreview

    _COLOR_PREVIEW_AVAILABLE = True
except ImportError:
    _COLOR_PREVIEW_AVAILABLE = False

try:
    from src.editor.inlay_hints import InlayHints

    _INLAY_AVAILABLE = True
except ImportError:
    _INLAY_AVAILABLE = False

try:
    from src.editor.smart_selection import SmartSelection

    _SMART_SEL_AVAILABLE = True
except ImportError:
    _SMART_SEL_AVAILABLE = False

logger = logging.getLogger("Codis")

try:
    from src.editor.heatmap import EditHeatmap

    _HEATMAP_OK = True
except ImportError:
    _HEATMAP_OK = False

try:
    from src.editor.rhythm import CodeRhythm

    _RHYTHM_OK = True
except ImportError:
    _RHYTHM_OK = False

try:
    from src.editor.ligatures import LigatureEngine

    _LIGATURE_OK = True
except ImportError:
    _LIGATURE_OK = False

try:
    from src.editor.file_watcher import FileWatcher

    _FILEWATCHER_OK = True
except ImportError:
    _FILEWATCHER_OK = False

try:
    from src.lsp.client import LSPManager

    _LSP_AVAILABLE = True
except ImportError:
    _LSP_AVAILABLE = False


class EditorPane(_PaneLSPMixin, tk.Frame):
    """
    Painel de edição completo com todas as funcionalidades:

    Editor:
    - Realce de sintaxe multi-linguagem
    - Autocompletar inteligente (snippets + palavras-chave + símbolos)
    - Auto-indentação e fechamento automático de pares
    - Correspondência de colchetes
    - Realce da linha atual
    - Números de linha (gutter) com breakpoints
    - Minimapa lateral
    - Breadcrumb (exposto via callback)
    - Alternar quebra de linha
    - Múltiplos cursores (simulado via seleções)
    - Rolagem sincronizada (gutter + minimapa)

    Análise:
    - Lint integrado (ESLint, pyflakes, flake8, mypy)
    - Anotações KDocs
    - Documentação JSDoc ao passar o mouse
    - Localizar e substituir embutido

    Edição avançada:
    - Duplicar linha / mover linha
    - Comentar/descomentar (linha e bloco)
    - Ordenar linhas
    - Transformar maiúsculas/minúsculas
    - Juntar linhas
    - Remover espaços em branco no final
    - Aumentar/Diminuir indentação do bloco
    - Excluir linha
    - Ir para linha
    - Formatar documento (prettier/black/etc.)

    Persistência:
    - Detecção e conversão de codificação
    - Detecção de final de linha (LF/CRLF/CR)
    - Indicador de modificação
    - Desfazer/Refazer ilimitado
    """

    def __init__(
        self,
        parent: tk.Widget,
        tmgr: ThemeManager,
        cfg: ConfigManager,
        lint_mgr: LintManager,
        snippets: SnippetEngine,
        on_change: Callable[[], None],
        on_cursor: Callable[[int, int], None],
        on_title: Callable[[str, bool], None],
        on_copy: Callable[[str, str], None] | None = None,
        idx: int = 0,
    ) -> None:
        """Cria o painel de edição principal do Codis.

        Args:
            parent: Widget pai Tkinter.
            config: Instância de ConfigManager.
            i18n: Instância do sistema de internacionalização.
        """
        t = tmgr.theme
        super().__init__(parent, bg=t.editor_bg)
        self._tmgr = tmgr
        self._cfg = cfg
        self._lint_mgr = lint_mgr
        self._snippets = snippets
        self._on_change = on_change
        self._on_cursor = on_cursor
        self._on_title = on_title
        self._on_copy_cb: Callable[[str, str], None] | None = on_copy
        self.idx = idx

        self.path: Path | None = None
        self.lang: str = "text"
        self.encoding: str = "UTF-8"
        self.eol: str = "\n"
        self.modified: bool = False
        self._font_size: int = cfg.get("font_size", FONT_SIZE)
        self._font_family: str = cfg.get("font_family", FONT_MONO)
        self._diags: list[Diag] = []
        self._kdocs: list[KDocsNotes] = []
        self._last_saved_content: str = ""
        self._autocomplete_enabled: bool = cfg.get("autocomplete", True)
        self._word_wrap: bool = cfg.get("word_wrap", False)
        self._show_minimap: bool = cfg.get("minimap", True)
        self._scroll_speed: int = max(1, min(15, cfg.get("scroll_speed", 3)))
        self._ligature_mode: str = "off"
        JSDocParser()

        self._doc_symbols: dict = {}

        self._lsp_mgr: LSPManager | None = None
        self._hover_job: str | None = None
        self._hide_job: str | None = None
        self._hl_scroll_job: str | None = None
        self._kdocs_job: str | None = None
        self._symbols_job: str | None = None
        self._lsp_change_job: str | None = None
        self._last_hover_word: str = ""
        self._last_hover_line: int = -1
        self._diag_tip_win: tk.Toplevel | None = None
        self._diag_tip_job: str | None = None

        self._is_large_mode: bool = False

        self._is_giant_mode: bool = False
        self._vfb: object | None = None
        self._viewport_start: int = 0
        self._vp_shift_job: str | None = None
        self._is_loading: bool = False
        self._load_chunks: list[str] = []
        self._load_total: int = 0
        self._sanitize_stats: tuple[int, int] = (0, 0)
        self._loading_overlay: tk.Frame | None = None
        self._loading_label: tk.Label | None = None
        self._giant_status: tk.Label | None = None

        self._code_line_count: int = 0
        self._perf_banner: tk.Label | None = None

        self.sticky_scroll: StickyScroll | None = None
        self.code_folding: CodeFolding | None = None
        self.inline_blame: InlineBlame | None = None
        self.color_preview: ColorPreview | None = None
        self.inlay_hints: InlayHints | None = None
        self.smart_selection: SmartSelection | None = None
        self.ligature_engine: LigatureEngine | None = None
        self.file_watcher: FileWatcher | None = None
        self.complexity_lens: ComplexityLens | None = None
        self.focus_block: FocusBlock | None = None
        self.sticky_notes: StickyNoteGutter | None = None

        self._sticky_enabled: bool = cfg.get("sticky_scroll", True)
        self._folding_enabled: bool = cfg.get("code_folding", True)
        self._blame_enabled: bool = cfg.get("inline_blame", False)
        self._color_preview_enabled: bool = cfg.get("color_preview", True)
        self._inlay_hints_enabled: bool = cfg.get("inlay_hints", False)
        self._blame_mode: str = cfg.get("inline_blame_mode", "cursor")

        self._build_ui()
        self._bind_events()
        self._apply_config()

    def _build_ui(self) -> None:
        """Constrói todos os widgets do editor: canvas, scrollbars e sobreposições."""
        t = self._tmgr.theme

        self._main = tk.Frame(self, bg=t.editor_bg)
        self._main.pack(fill="both", expand=True)

        self._gutter_frame = tk.Frame(self._main, bg=t.gutter_bg)
        self._gutter_frame.pack(side="left", fill="y")

        self._editor_frame = tk.Frame(self._main, bg=t.editor_bg)
        self._editor_frame.pack(side="left", fill="both", expand=True)

        self.text = tk.Text(
            self._editor_frame,
            bg=t.editor_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            selectbackground=t.sel_bg,
            selectforeground=t.sel_fg,
            inactiveselectbackground=t.sel_bg,
            relief="flat",
            bd=0,
            font=(self._font_family, self._font_size),
            undo=True,
            maxundo=-1,
            tabs=(self._calc_tab_pixels(),),
            wrap="word" if self._word_wrap else "none",
            highlightthickness=0,
            padx=8,
            pady=0,
            spacing1=4,
            spacing3=4,
        )
        from src.ui.ui_scale import exclude_from_scale

        exclude_from_scale(self.text)
        self.text.pack(side="left", fill="both", expand=True)

        self._vsb = ttk.Scrollbar(
            self._editor_frame,
            orient="vertical",
            command=self.text.yview,
            style="Thin.Vertical.TScrollbar",
        )
        self._vsb.pack(side="right", fill="y")
        self.text.config(yscrollcommand=self._sync_scroll_y)

        self._hsb = ttk.Scrollbar(
            self._main,
            orient="horizontal",
            command=self.text.xview,
            style="Thin.Horizontal.TScrollbar",
        )
        self.text.config(xscrollcommand=self._hsb_set)

        self.gutter = LineNumbers(
            self._gutter_frame,
            self.text,
            self._tmgr.theme,
            bg=t.gutter_bg,
        )
        self.gutter.pack(fill="both", expand=True)
        self._multicursor_lines: set[int] = set()
        self._multicursor_active: bool = False
        self.gutter.set_on_marked_change(self._on_marked_lines_change)
        self._heatmap: EditHeatmap | None = (
            EditHeatmap(on_update=self.gutter.redraw) if _HEATMAP_OK else None
        )
        self._rhythm: CodeRhythm | None = CodeRhythm() if _RHYTHM_OK else None

        self._minimap_frame = tk.Frame(
            self._main, bg=t.minimap_bg, width=MINIMAP_WIDTH)
        self.minimap = MiniMap(self._minimap_frame, self.text, self._tmgr)
        self.minimap.pack(fill="both", expand=True)
        if self._show_minimap:
            self._minimap_frame.pack(side="right", fill="y")
            self._minimap_frame.pack_propagate(False)

        self.hl = SyntaxHL(self.text, self.lang, self._tmgr)
        self.hl.set_config(self._cfg)

        self.autocomplete = AutocompletePopup(
            self,
            self.text,
            self._tmgr,
        )
        self.autocomplete.set_lang(self.lang)
        self.autocomplete.set_snippets(self._snippets)

        self._hover_tip: tk.Toplevel | None = None
        self._balloon: KDocsBalloon | None = None

        if _STICKY_AVAILABLE and self._sticky_enabled:
            self.sticky_scroll = StickyScroll(
                self._editor_frame,
                self.text,
                self._tmgr.theme,
                font_family=self._font_family,
                font_size=self._font_size,
                on_goto=self.goto_line,
            )

            self.sticky_scroll.pack(fill="x", side="top", before=self.text)

        if _FOLDING_AVAILABLE and self._folding_enabled:
            self.code_folding = CodeFolding(
                self.text,
                self._tmgr.theme,
                on_gutter_update=self._on_fold_gutter_update,
                lang=self.lang,
            )
            self.gutter.set_on_fold_toggle(self._on_gutter_fold_click)

        if _COMPLEXITY_LENS_OK:
            self.complexity_lens = ComplexityLens(self.text, self._tmgr.theme)

        if _FOCUS_BLOCK_OK:
            self.focus_block = FocusBlock(self.text, self._tmgr.theme)

        if _STICKY_NOTES_OK:
            self.sticky_notes = StickyNoteGutter(
                self.text,
                self.gutter,
                self._tmgr.theme,
                self._cfg,
            )

        if _BLAME_AVAILABLE:
            self.inline_blame = InlineBlame(
                self.text,
                self._tmgr.theme,
                mode=self._blame_mode,
            )
            if self._blame_enabled:
                self.inline_blame.set_enabled(True)

        if _COLOR_PREVIEW_AVAILABLE and self._color_preview_enabled:
            self.color_preview = ColorPreview(
                self.text,
                self._tmgr.theme,
                on_edit_callback=self._on_color_edit,
            )
            self.color_preview.set_enabled(True)

        if _INLAY_AVAILABLE and self._inlay_hints_enabled:
            self.inlay_hints = InlayHints(
                self.text,
                self._tmgr.theme,
                lang=self.lang,
            )
            self.inlay_hints.set_enabled(True)

        if _SMART_SEL_AVAILABLE:
            self.smart_selection = SmartSelection(self.text)

        if _LIGATURE_OK:
            accent = getattr(self._tmgr.theme, "accent", "#cba6f7")
            self.ligature_engine = LigatureEngine(
                self.text, accent_color=accent)
            lig_enabled = self._cfg.get("font_ligatures", False)
            lig_mode = self._cfg.get("ligature_mode", "pseudo")
            if lig_enabled and lig_mode in ("pseudo", "both"):
                self.ligature_engine.set_enabled(True)

        if _FILEWATCHER_OK:
            self.file_watcher = FileWatcher(
                self,
                on_changed=self._on_file_changed_externally,
                interval_ms=2000,
            )

    def _calc_tab_pixels(self) -> int:
        """Calcula o tamanho do tab em pixels com base na fonte e no tab_size configurados."""
        ts = self._cfg.get("tab_size", TAB_SIZE)
        try:
            f = tkfont.Font(family=self._font_family, size=self._font_size)
            return max(1, f.measure("0") * ts)
        except tk.TclError:
            return ts * 8

    def _hsb_set(self, first: str, last: str) -> None:
        """Exibe a barra de rolagem horizontal apenas quando o conteúdo ultrapassa a borda."""
        self._hsb.set(first, last)
        if float(first) <= 0.0 and float(last) >= 1.0:
            self._hsb.pack_forget()
        elif not self._hsb.winfo_ismapped():
            self._hsb.pack(side="bottom", fill="x")

    def _bind_events(self) -> None:
        """Registra todos os bindings de teclado, mouse e clipboard do editor."""
        txt = self.text

        txt.bind("<<Modified>>", self._on_modified)

        txt.bind("<KeyRelease>", self._on_key)
        txt.bind("<ButtonRelease-1>", self._on_click)
        txt.bind("<ButtonRelease-3>", self._on_right_click)

        txt.bind("<B1-Motion>", self._on_drag_select)

        txt.bind("<Return>", self._on_return)

        txt.bind("<Tab>", self._on_tab)

        txt.bind("<Shift-Tab>", self._on_shift_tab)

        txt.bind("<BackSpace>", self._on_backspace)

        txt.bind("<Delete>", self._on_delete_key)

        txt.bind("<Up>", self._on_up)
        txt.bind("<Down>", self._on_down)
        txt.bind("<Escape>", self._on_escape)

        for opener in AUTO_CLOSE:
            txt.bind(opener, self._make_auto_pair(opener))

        txt.bind("<Control-equal>", lambda _: self.zoom(+1))
        txt.bind("<Control-minus>", lambda _: self.zoom(-1))
        txt.bind("<Control-0>", lambda _: self.zoom(0))
        txt.bind("<Control-KP_Add>", lambda _: self.zoom(+1))
        txt.bind("<Control-KP_Subtract>", lambda _: self.zoom(-1))

        txt.bind("<Control-slash>", lambda _: self.toggle_comment())
        txt.bind("<Control-d>", lambda _: self.duplicate_line())
        txt.bind("<Alt-Up>", lambda _: self.move_line(-1))
        txt.bind("<Alt-Down>", lambda _: self.move_line(+1))
        txt.bind("<Control-l>", lambda _: self.select_line())
        txt.bind("<Control-BackSpace>", lambda _: self.delete_word_left())
        txt.bind("<Control-Delete>", lambda _: self.delete_word_right())

        txt.bind("<Control-c>", self._on_copy)
        txt.bind("<Control-x>", self._on_cut)
        txt.bind("<Control-v>", self._on_paste)

        def _safe_undo(_e):
            with contextlib.suppress(tk.TclError):
                self.text.edit_undo()
            return "break"

        def _safe_redo(_e):
            with contextlib.suppress(tk.TclError):
                self.text.edit_redo()
            return "break"

        txt.bind("<Control-z>", _safe_undo)
        txt.bind("<Control-Z>", _safe_redo)
        txt.bind("<Control-y>", _safe_redo)

        txt.bind("<Control-a>", self._on_select_all)

        txt.bind("<KeyPress-Control_L>", lambda _: self._set_ctrl(True))
        txt.bind("<KeyPress-Control_R>", lambda _: self._set_ctrl(True))
        txt.bind("<KeyRelease-Control_L>", lambda _: self._set_ctrl(False))
        txt.bind("<KeyRelease-Control_R>", lambda _: self._set_ctrl(False))
        txt.bind("<FocusOut>", lambda _: self._set_ctrl(False), add=True)

        txt.bind("<Motion>", self._on_mouse_motion)
        txt.bind("<Leave>", self._hide_hover)
        txt.bind("<Motion>", self._on_mouse_motion_diag, add=True)
        txt.bind("<Leave>", self._hide_diag_tip, add=True)

        txt.bind("<F12>", lambda _: self.lsp_goto_definition())
        txt.bind("<Control-F12>", lambda _: self.lsp_goto_definition())
        txt.bind("<Shift-F12>", lambda _: self.lsp_find_references())
        txt.bind("<Control-Button-1>", self._on_ctrl_click)
        txt.bind("<F6>", lambda _: self.lsp_rename_symbol())

        txt.bind("<MouseWheel>", self._on_mousewheel)
        txt.bind("<Button-4>", self._on_button4)
        txt.bind("<Button-5>", self._on_button5)

        txt.bind("<Alt-Shift-Right>", lambda _: self._smart_expand())
        txt.bind("<Alt-Shift-Left>", lambda _: self._smart_shrink())

        txt.bind("<Control-Shift-a>", lambda _: self._smart_expand())

        txt.bind("<Control-Shift-bracketleft>",
                 lambda _: self._fold_at_cursor())
        txt.bind("<Control-Shift-bracketright>",
                 lambda _: self._unfold_at_cursor())

        txt.bind("<Control-k>", self._on_ctrl_k_prefix)

        txt.bind("<Control-Shift-u>", lambda _: self.transform_case("upper"))
        txt.bind("<Control-Shift-l>", lambda _: self.transform_case("lower"))
        txt.bind("<Control-j>", lambda _: self.join_lines())
        txt.bind("<Control-Shift-k>", lambda _: self.delete_line())

        txt.bind("<ButtonRelease-1>",
                 lambda _: self._reset_smart_history(), add=True)
        txt.bind("<KeyRelease-Left>",
                 lambda _: self._reset_smart_history(), add=True)
        txt.bind("<KeyRelease-Right>",
                 lambda _: self._reset_smart_history(), add=True)
        txt.bind("<KeyRelease-Up>",
                 lambda _: self._reset_smart_history(), add=True)
        txt.bind("<KeyRelease-Down>",
                 lambda _: self._reset_smart_history(), add=True)

    _MOD_KEYS: frozenset[str] = frozenset(
        {
            "Shift_L",
            "Shift_R",
            "Control_L",
            "Control_R",
            "Alt_L",
            "Alt_R",
            "Meta_L",
            "Meta_R",
            "Super_L",
            "Super_R",
            "Caps_Lock",
            "Num_Lock",
            "Scroll_Lock",
            "F1",
            "F2",
            "F3",
            "F4",
            "F5",
            "F6",
            "F7",
            "F8",
            "F9",
            "F10",
            "F11",
            "F12",
        }
    )

    def _on_key(self, e: tk.Event) -> None:
        """Após a inserção: atualiza tudo. Intercepta teclas em modo multicursor."""
        sym = getattr(e, "keysym", "")
        if sym in self._MOD_KEYS:
            return

        if self._multicursor_active and self._multicursor_lines:
            ch = getattr(e, "char", "")
            if sym == "Escape":
                self.gutter.clear_marked()
                return
            if ch and ch.isprintable() and len(ch) == 1:
                self._multicursor_insert(ch)
            elif sym == "BackSpace":
                self._multicursor_delete_col(0)

        if self._heatmap is not None:
            try:
                cur_ln = int(self.text.index(tk.INSERT).split(".")[0])
                self._heatmap.record_edit(cur_ln)
            except (tk.TclError, ValueError):
                pass
        if self._rhythm is not None:
            char = getattr(e, "char", "") or ""
            self._rhythm.record_keystroke(char)
        self.hl.schedule()
        self.hl.hl_current_line()
        self.hl.match_brackets()
        self._update_cursor()
        self.gutter._schedule_redraw()
        if not (self._is_large_mode or self._is_giant_mode):
            self.minimap._schedule_redraw()

        if not (self._is_large_mode or self._is_giant_mode):
            with contextlib.suppress(tk.TclError):
                if self.color_preview:
                    self.color_preview.notify_change()
            with contextlib.suppress(tk.TclError):
                if self.inlay_hints:
                    self.inlay_hints.notify_change()
            with contextlib.suppress(tk.TclError):
                if self.code_folding:
                    self.code_folding._schedule_scan()

        if sym in ("Return", "BackSpace", "Delete", "space"):
            if self._kdocs_job:
                with contextlib.suppress(tk.TclError):
                    self.after_cancel(self._kdocs_job)
            self._kdocs_job = self.after(250, self._run_kdocs_debounced)

            if self.lang in ("javascript", "typescript"):
                if self._symbols_job:
                    with contextlib.suppress(tk.TclError):
                        self.after_cancel(self._symbols_job)
                self._symbols_job = self.after(
                    500, self._run_symbols_debounced)

        if not (self._is_large_mode or self._is_giant_mode) and sym not in (
            "Left",
            "Right",
            "Up",
            "Down",
            "Home",
            "End",
            "Prior",
            "Next",
        ):
            self._lint_mgr.schedule(
                self.path, self.text.get("1.0", "end-1c"), self.lang
            )
            if self._lsp_mgr and self.path:
                _content = self.text.get("1.0", "end-1c")
                if self._lsp_change_job:
                    with contextlib.suppress(tk.TclError):
                        self.after_cancel(self._lsp_change_job)
                self._lsp_change_job = self.after(
                    350, lambda c=_content: self._run_lsp_change_debounced(c)
                )

        if self._autocomplete_enabled:
            if sym and (
                (len(sym) == 1 and (sym.isalnum() or sym in "_$"))
                or sym in ("BackSpace", "Delete", "period")
            ):
                self.autocomplete.trigger()
            elif sym not in (
                "Up",
                "Down",
                "Return",
                "Tab",
                "Escape",
                "Left",
                "Right",
                "Shift_L",
                "Shift_R",
                "Control_L",
                "Control_R",
                "Alt_L",
                "Alt_R",
                "Meta_L",
                "Meta_R",
            ):
                self.autocomplete.hide()

    def _on_click(self, _e: tk.Event) -> None:
        """Trata o evento 'click'.

        Args:
            _e: Parâmetro da operação.
        """
        self.autocomplete.hide()
        self.hl.hl_current_line()
        self.hl.match_brackets()
        self._update_cursor()

    def _on_right_click(self, e: tk.Event) -> None:
        """Trata o evento 'right click'.

        Args:
            e: Evento Tkinter.
        """
        self._show_context_menu(e)

    def _on_return(self, _e: tk.Event) -> str:

        if self.autocomplete.is_visible() and self.autocomplete.accept():
            return "break"
        try:
            pos = self.text.index(tk.INSERT)
            line_num = int(pos.split(".", maxsplit=1)[0])
            cur_line = self.text.get(f"{line_num}.0", f"{line_num}.end")
            stripped = cur_line.lstrip()
            indent = len(cur_line) - len(stripped)

            opened_block = False
            if (
                stripped
                and stripped[-1] in "{(["
                or self.lang == "python"
                and stripped.endswith(":")
            ):
                indent += self._cfg.get("tab_size", TAB_SIZE)
                opened_block = True

            closer_on_cursor = self.text.get(tk.INSERT)
            if opened_block and closer_on_cursor in ")]}":
                tab_size = self._cfg.get("tab_size", TAB_SIZE)
                outer_indent = max(0, indent - tab_size)

                self.text.insert(
                    tk.INSERT, "\n" + " " * indent + "\n" + " " * outer_indent
                )
                self.text.mark_set(tk.INSERT, f"{line_num + 1}.{indent}")
                return "break"

            self.text.insert(tk.INSERT, "\n" + " " * indent)
        except tk.TclError:
            return "break"
        return "break"

    def _on_tab(self, _e: tk.Event) -> str:

        if self.autocomplete.is_visible():
            self.autocomplete.accept()
            return "break"

        try:
            pos = self.text.index(tk.INSERT)
            line = int(pos.split(".", maxsplit=1)[0])
            line_text = self.text.get(f"{line}.0", pos)
            m = re.search(r"[\w$]+$", line_text)
            if m:
                prefix = m.group()
                snip = self._snippets.find_prefix(self.lang, prefix)
                if snip:
                    self._snippets.expand(snip, self.text, self.path)
                    return "break"
        except tk.TclError:
            pass

        try:
            if self.text.tag_ranges(tk.SEL):
                start = int(self.text.index(
                    tk.SEL_FIRST).split(".", maxsplit=1)[0])
                end = int(self.text.index(
                    tk.SEL_LAST).split(".", maxsplit=1)[0])
                ts = self._cfg.get("tab_size", TAB_SIZE)
                for ln in range(start, end + 1):
                    self.text.insert(f"{ln}.0", " " * ts)
            else:
                ts = self._cfg.get("tab_size", TAB_SIZE)
                self.text.insert(tk.INSERT, " " * ts)
        except tk.TclError:
            pass
        return "break"

    def _on_shift_tab(self, _e: tk.Event) -> str:

        try:
            ts = self._cfg.get("tab_size", TAB_SIZE)
            if self.text.tag_ranges(tk.SEL):
                start = int(self.text.index(
                    tk.SEL_FIRST).split(".", maxsplit=1)[0])
                end = int(self.text.index(
                    tk.SEL_LAST).split(".", maxsplit=1)[0])
                for ln in range(start, end + 1):
                    line = self.text.get(f"{ln}.0", f"{ln}.end")
                    spaces = len(line) - len(line.lstrip(" "))
                    remove = min(spaces, ts)
                    if remove:
                        self.text.delete(f"{ln}.0", f"{ln}.{remove}")
            else:
                pos = self.text.index(tk.INSERT)
                line_num = pos.split(".", maxsplit=1)[0]
                line = self.text.get(f"{line_num}.0", pos)
                spaces = len(line) - len(line.lstrip(" "))
                remove = min(spaces, ts)
                if remove:
                    self.text.delete(f"{line_num}.0", f"{line_num}.{remove}")
        except tk.TclError:
            pass
        return "break"

    def _on_backspace(self, _e: tk.Event) -> str | None:

        try:
            if self.text.tag_ranges(tk.SEL):
                return None
            pos = self.text.index(tk.INSERT)
            col = int(pos.split(".", maxsplit=1)[1])
            line = self.text.get(f"{pos} linestart", pos)
            ts = self._cfg.get("tab_size", TAB_SIZE)
            if line and all(c == " " for c in line) and col > 0:
                remove = col % ts or ts
                self.text.delete(f"{pos} - {remove}c", pos)
                return "break"
        except tk.TclError:
            pass
        return None

    def _on_delete_key(self, _e: tk.Event) -> str | None:

        try:
            if self.text.tag_ranges(tk.SEL):
                return None
            pos = self.text.index(tk.INSERT)
            rest = self.text.get(pos, f"{pos} lineend")
            ts = self._cfg.get("tab_size", TAB_SIZE)
            if rest and all(c == " " for c in rest) and len(rest) > 0:
                remove = len(rest) % ts or ts
                self.text.delete(pos, f"{pos}+{remove}c")
                return "break"
        except tk.TclError:
            pass
        return None

    def _on_up(self, _: tk.Event) -> str | None:
        """Trata o evento 'up'.

        Args:
            _: Parâmetro da operação.
        """
        if self.autocomplete.navigate(-1):
            return "break"
        return None

    def _on_down(self, _: tk.Event) -> str | None:
        """Trata o evento 'down'.

        Args:
            _: Parâmetro da operação.
        """
        if self.autocomplete.navigate(+1):
            return "break"
        return None

    def _on_escape(self, _: tk.Event) -> str | None:
        """Trata o evento 'escape'.

        Args:
            _: Parâmetro da operação.
        """
        if self.autocomplete.is_visible():
            self.autocomplete.hide()
            return "break"
        return None

    def _on_copy(self, _e: tk.Event) -> str:
        """Copia a seleção para o clipboard e retorna 'break' para suprimir o evento padrão."""
        try:
            if self.text.tag_ranges(tk.SEL):
                text = self.text.get(tk.SEL_FIRST, tk.SEL_LAST)
                self.text.clipboard_clear()
                self.text.clipboard_append(text)
                if text and self._on_copy_cb:
                    self._on_copy_cb(text, "copy")
        except tk.TclError:
            pass
        return "break"

    def _on_cut(self, _e: tk.Event) -> str:
        """Recorta a seleção para o clipboard e retorna 'break' para suprimir o evento padrão."""
        try:
            if self.text.tag_ranges(tk.SEL):
                text = self.text.get(tk.SEL_FIRST, tk.SEL_LAST)
                self.text.clipboard_clear()
                self.text.clipboard_append(text)
                self.text.delete(tk.SEL_FIRST, tk.SEL_LAST)
                if text and self._on_copy_cb:
                    self._on_copy_cb(text, "cut")
        except tk.TclError:
            pass
        return "break"

    def _on_paste(self, _e: tk.Event) -> str:
        """Colar: insere o conteúdo da área de transferência e reagenda o realce."""
        try:
            content = self.text.clipboard_get()
            if self.text.tag_ranges(tk.SEL):
                self.text.delete(tk.SEL_FIRST, tk.SEL_LAST)
            self.text.insert(tk.INSERT, content)
            self.text.after(50, self.hl.schedule)
        except tk.TclError:
            pass
        return "break"

    def _on_select_all(self, _: tk.Event) -> str:
        """Seleciona todo o conteúdo do editor e retorna 'break' para suprimir o evento padrão."""
        self.text.tag_add(tk.SEL, "1.0", "end")
        self.text.mark_set(tk.INSERT, "end")
        return "break"

    def _on_drag_select(self, e: tk.Event) -> str | None:

        try:
            pos = self.text.index(f"@{e.x},{e.y}")
            sel_start = tk.SEL_FIRST if self.text.tag_ranges(
                tk.SEL) else tk.INSERT
            self.text.tag_add(tk.SEL, sel_start, pos)
            self.text.mark_set(tk.INSERT, pos)
        except tk.TclError:
            pass

    def _on_mousewheel(self, e: tk.Event) -> str | None:
        """Rolagem com Ctrl+Roda do mouse = zoom; roda normal = scroll com sensibilidade configurável."""
        if e.state & 0x4:
            delta = 1 if e.delta > 0 else -1
            self.zoom(delta)
            return "break"
        if e.delta:
            speed = getattr(self, "_scroll_speed", 3)
            steps = -1 * (e.delta // 120) * speed
            self.text.yview_scroll(int(steps), "units")
            return "break"
        return None

    def _on_button4(self, e: tk.Event) -> str | None:
        """Linux: rolagem para cima - Ctrl+Botão-4 = aumentar zoom."""
        if e.state & 0x4:
            self.zoom(1)
            return "break"
        speed = getattr(self, "_scroll_speed", 3)
        self.text.yview_scroll(-speed, "units")
        if getattr(self, "ligature_engine", None):
            self.ligature_engine.on_scroll()
        return None

    def _on_button5(self, e: tk.Event) -> str | None:
        """Linux: rolagem para baixo - Ctrl+Botão-5 = diminuir zoom."""
        if e.state & 0x4:
            self.zoom(-1)
            return "break"
        speed = getattr(self, "_scroll_speed", 3)
        self.text.yview_scroll(speed, "units")
        if getattr(self, "ligature_engine", None):
            self.ligature_engine.on_scroll()
        return None

    def _on_mouse_motion(self, e: tk.Event) -> None:
        """Hover para JSDoc e KDocs (estilo VSCode).

        Proteção: NÃO acionar hover durante arrasto com o botão 1 do mouse (seleção).
        O bit 0x0100 de e.state indica Botão-1 pressionado.
        """
        if e.state & 0x0100:
            return
        if self._hover_job:
            with contextlib.suppress(tk.TclError):
                self.after_cancel(self._hover_job)

        self._hover_job = self.after(400, lambda: self._show_hover(e.x, e.y))

    def set_content(self, text: str, preserve_pos: bool = True) -> None:
        """Substitui o conteúdo do editor, preservando a posição do cursor se solicitado."""
        try:
            text, _r, _t = self._sanitize_for_display(text)
            pos = self.text.index(tk.INSERT) if preserve_pos else "1.0"
            self.text.delete("1.0", "end")
            self.text.insert("1.0", text)
            if preserve_pos:
                with contextlib.suppress(tk.TclError):
                    self.text.mark_set(tk.INSERT, pos)
            self.hl.schedule()
        except tk.TclError:
            pass

    def deliver_diags(self, diags: list[Diag]) -> None:
        """Recebe diagnósticos do LintManager e aplica."""
        self._diags = diags
        self.hl.apply_lint(diags)
        self.gutter.set_lint_lines(diags)
        self.minimap.set_lint_lines(diags)

    def fix_all(self) -> None:
        """Aplica todas as correções automáticas do ESLint num único passo atômico.

        Usa ``apply_all_eslint_fixes`` do LintManager, que implementa o algoritmo
        oficial do ESLint: ordena os fixes por posição de início, descarta os que
        se sobrepõem e aplica os restantes numa única varredura do conteúdo.
        Isso garante que os offsets originais permaneçam válidos durante toda
        a operação - ao contrário da abordagem sequencial, que corrompe o arquivo
        quando dois fixes estão na mesma região.
        """
        if self.lang in LINT_JS and self.path:
            content = self.text.get("1.0", "end-1c")
            fixed = self._lint_mgr.apply_all_eslint_fixes(
                self.path, content, self._diags
            )
            if fixed != content:
                self.set_content(fixed)

    def _run_kdocs_debounced(self) -> None:
        """Executa _update_kdocs após debounce; limpa o job id."""
        self._kdocs_job = None
        self._update_kdocs()

    def _run_symbols_debounced(self) -> None:
        """Executa _refresh_doc_symbols após debounce; limpa o job id."""
        self._symbols_job = None
        self._refresh_doc_symbols()

    def _run_lsp_change_debounced(self, c: str) -> None:
        """Executa _lsp_did_change após debounce; limpa o job id."""
        self._lsp_change_job = None
        self._lsp_did_change(c)

    def _update_kdocs(self) -> None:
        """Atualiza 'kdocs' - ignorado em modo large/giant (operação O(n) sobre o arquivo inteiro)."""
        if self._is_large_mode or self._is_giant_mode:
            return
        content = self.text.get("1.0", "end-1c")
        self._kdocs = KDocsParser.parse(content)
        self.hl.apply_kdocs(self._kdocs)
        self.gutter.set_kdocs_lines(self._kdocs)

    def _refresh_doc_symbols(self) -> None:
        """Reanalisa símbolos JSDoc - ignorado em modo large/giant."""
        if self._is_large_mode or self._is_giant_mode:
            return
        content = self.text.get("1.0", "end-1c")
        self._doc_symbols = JSDocParser.parse(content)

    def refresh_custom_keywords(self) -> None:
        """Reaplica as keywords customizadas do config no highlighter.

        Chamado após o usuário modificar uma entry no CustomKeywordsPanel,
        ou quando o config é recarregado.
        """
        if hasattr(self, "hl") and self.hl is not None:
            self.hl._setup_custom_kw_tags()
            self.hl.schedule()

    def apply_theme(self, t: Theme) -> None:
        """Aplica o tema ao editor (cores, fontes, balão)."""

        self._balloon = None
        self.config(bg=t.editor_bg)
        self._main.config(bg=t.editor_bg)
        self._editor_frame.config(bg=t.editor_bg)
        self.text.config(
            bg=t.editor_bg,
            fg=t.fg,
            insertbackground=t.cursor,
            selectbackground=t.sel_bg,
            selectforeground=t.sel_fg,
            inactiveselectbackground=t.sel_bg,
        )
        self._gutter_frame.config(bg=t.gutter_bg)
        self.gutter.update_theme(t)
        self.minimap.update_theme()
        self._minimap_frame.config(bg=t.minimap_bg)
        self.hl.reconfigure()

        with contextlib.suppress(tk.TclError):
            if self.sticky_scroll:
                self.sticky_scroll.apply_theme(t)
        with contextlib.suppress(tk.TclError):
            if self.code_folding:
                self.code_folding.apply_theme(t)
        with contextlib.suppress(tk.TclError):
            if self.inline_blame:
                self.inline_blame.apply_theme(t)
        with contextlib.suppress(tk.TclError):
            if self.color_preview:
                self.color_preview.apply_theme(t)
        with contextlib.suppress(tk.TclError):
            if self.inlay_hints:
                self.inlay_hints.apply_theme(t)
        with contextlib.suppress(tk.TclError):
            if self.complexity_lens:
                self.complexity_lens.apply_theme(t)
        with contextlib.suppress(tk.TclError):
            if self.focus_block:
                self.focus_block.apply_theme(t)
        with contextlib.suppress(tk.TclError):
            if self.sticky_notes:
                self.sticky_notes.apply_theme(t)

    def _apply_config(self) -> None:
        """Relê as configurações e as aplica ao widget de edição (fonte, tabs, etc.)."""
        self._autocomplete_enabled = self._cfg.get("autocomplete", True)
        if self._cfg.get("line_numbers", True):
            self._gutter_frame.pack(side="left", fill="y")
        else:
            self._gutter_frame.pack_forget()

    def apply_config(self, cfg: ConfigManager) -> None:
        """Aplica as configurações ao editor (fonte, indentação, minimapa, autocompletar, etc.)."""
        self._cfg = cfg
        self._font_size = cfg.get("font_size", FONT_SIZE)
        self._font_family = cfg.get("font_family", FONT_MONO)
        self.text.config(font=(self._font_family, self._font_size))
        self.gutter.update_font(self._font_family, self._font_size)

        lig_enabled = cfg.get("font_ligatures", False)
        lig_mode = cfg.get("ligature_mode", "pseudo")
        self._ligature_mode = lig_mode if lig_enabled else "off"

        if lig_enabled and lig_mode in ("native", "both"):
            with contextlib.suppress(Exception):
                f = tkfont.Font(family=self._font_family, size=self._font_size)
                try:
                    f.configure(features="liga")
                    self.text.config(font=f)
                except tk.TclError:
                    pass

        self._scroll_speed = max(1, min(15, cfg.get("scroll_speed", 3)))

        self._autocomplete_enabled = cfg.get("autocomplete", True)
        self._word_wrap = cfg.get("word_wrap", False)
        self.text.config(wrap="word" if self._word_wrap else "none")
        self.text.config(tabs=(self._calc_tab_pixels(),))
        if cfg.get("minimap", True):
            if not self._show_minimap:
                self.toggle_minimap()
        elif self._show_minimap:
            self.toggle_minimap()
        self.hl.reconfigure()
        with contextlib.suppress(Exception):
            self.hl.set_idle_ms(cfg.get("hl_idle_ms", 60))

        with contextlib.suppress(tk.TclError):
            if self.sticky_scroll:
                self.sticky_scroll.update_font(
                    self._font_family, self._font_size)
                self.sticky_scroll.set_enabled(cfg.get("sticky_scroll", True))
        with contextlib.suppress(tk.TclError):
            if self.code_folding:
                enabled = cfg.get("code_folding", True)
                self._folding_enabled = enabled
        with contextlib.suppress(tk.TclError):
            if self.inline_blame:
                self._blame_enabled = cfg.get("inline_blame", False)
                self._blame_mode = cfg.get("inline_blame_mode", "cursor")
                self.inline_blame.set_mode(self._blame_mode)
                self.inline_blame.set_enabled(self._blame_enabled)
        with contextlib.suppress(tk.TclError):
            if self.color_preview:
                enabled = cfg.get("color_preview", True)
                self._color_preview_enabled = enabled
                self.color_preview.set_enabled(enabled)
        with contextlib.suppress(tk.TclError):
            if self.inlay_hints:
                enabled = cfg.get("inlay_hints", False)
                self._inlay_hints_enabled = enabled
                self.inlay_hints.set_enabled(enabled)

        with contextlib.suppress(Exception):
            if self.ligature_engine:
                lig_enabled = cfg.get("font_ligatures", False)
                lig_mode = cfg.get("ligature_mode", "pseudo")
                pseudo_on = lig_enabled and lig_mode in ("pseudo", "both")
                self.ligature_engine.set_enabled(pseudo_on)
                accent = getattr(self._tmgr.theme, "accent", "#cba6f7")
                self.ligature_engine.update_accent(accent)

    def _smart_expand(self) -> str:
        """Expande a seleção para o próximo nível semântico (Alt+Shift+ -> ).

        Returns:
            str: 'break' para suprimir o comportamento padrão do Tkinter.
        """
        if self.smart_selection:
            self.smart_selection.expand()
        return "break"

    def _smart_shrink(self) -> str:
        """Contrai a seleção para o nível semântico anterior (Alt+Shift+ <- ).

        Returns:
            str: 'break' para suprimir o comportamento padrão do Tkinter.
        """
        if self.smart_selection:
            self.smart_selection.shrink()
        return "break"

    def _reset_smart_history(self) -> None:
        """Reinicia o histórico de posições do cursor para navegação inteligente."""
        if self.smart_selection:
            self.smart_selection.reset_history()

    def _fold_at_cursor(self) -> str:
        """Dobra o bloco de código na linha atual do cursor.

        Returns:
            str: 'break' para suprimir o comportamento padrão do Tkinter.
        """
        if self.code_folding:
            try:
                idx = self.text.index(tk.INSERT)
                ln = int(idx.split(".", 1)[0])
                self.code_folding.toggle_fold(ln)
            except (tk.TclError, ValueError):
                pass
        return "break"

    def _unfold_at_cursor(self) -> str:
        """Expande o bloco de código na linha atual do cursor.

        Returns:
            str: 'break' para suprimir o comportamento padrão do Tkinter.
        """
        if self.code_folding:
            try:
                idx = self.text.index(tk.INSERT)
                ln = int(idx.split(".", 1)[0])
                region = self.code_folding._find_region_at(ln)
                if region and region.folded:
                    self.code_folding.unfold(region)
            except (tk.TclError, ValueError, AttributeError):
                pass
        return "break"

    def _on_ctrl_k_prefix(self, _event: tk.Event) -> None:
        """Captura o prefixo Ctrl+K para atalhos compostos.

        Segunda tecla:
          0 = dobrar tudo
          J = expandir tudo
          L = alternar dobramento na linha atual
          U = MAIÚSCULAS (seleção)
          D = minúsculas (seleção)
          T = title Case (seleção)
          C = camelCase (seleção)
          S = snake_case (seleção)
          B = Base64 encode (seleção)
          W = word count rápido (tooltip no cursor)
          I = inserir UUID
          N = inserir timestamp ISO
          X = deletar linha
          R = reverter arquivo
        """

        def _handle_second_key(event: tk.Event) -> str:
            """Processa a segunda tecla do atalho Ctrl+K+X."""
            self.text.unbind("<KeyPress>")
            sym = getattr(event, "keysym", "").lower()

            if sym == "0" and self.code_folding:
                self.code_folding.fold_all()
            elif sym == "j" and self.code_folding:
                self.code_folding.unfold_all()
            elif sym == "l" and self.code_folding:
                with contextlib.suppress(tk.TclError, ValueError):
                    ln = int(self.text.index(tk.INSERT).split(".", 1)[0])
                    self.code_folding.toggle_fold(ln)

            elif sym == "u":
                self.transform_case("upper")
            elif sym == "d":
                self.transform_case("lower")
            elif sym == "t":
                self.transform_case("title")
            elif sym == "c":
                self.transform_case("camel")
            elif sym == "s":
                self.transform_case("snake")

            elif sym == "b":
                with contextlib.suppress(ImportError):
                    from src.editor.text_transforms import apply_transform

                    apply_transform(self.text, "b64enc")

            elif sym == "i":
                with contextlib.suppress(ImportError):
                    from src.editor.text_transforms import insert_at_cursor

                    insert_at_cursor(self.text, "uuid")
            elif sym == "n":
                with contextlib.suppress(ImportError):
                    from src.editor.text_transforms import insert_at_cursor

                    insert_at_cursor(self.text, "ts_iso")

            elif sym == "w":
                self._show_word_count_tip()

            elif sym == "x":
                self.delete_line()

            elif sym == "r":
                self.revert()

            return "break"

        self.text.unbind("<KeyPress>")
        self.text.bind("<KeyPress>", _handle_second_key)

    def _show_word_count_tip(self) -> None:
        """Mostra contagem de palavras/linhas/chars em um tooltip rápido."""
        try:
            if self.text.tag_ranges(tk.SEL):
                content = self.text.get(tk.SEL_FIRST, tk.SEL_LAST)
                scope = "seleção"
            else:
                content = self.text.get("1.0", "end-1c")
                scope = "arquivo"
            words = len(re.findall(r"\S+", content))
            lines = content.count("\n") + (1 if content else 0)
            chars = len(content)
            msg = f"{scope}: {words} palavras - {lines} linhas - {chars} chars"
            self._show_context_info(msg)
        except tk.TclError:
            pass

    def _on_fold_gutter_update(self, fold_map: dict) -> None:
        """Atualiza o gutter com ícones de fold/unfold.

        Chamado pelo CodeFolding quando as regiões dobráveis mudam.

        Args:
            fold_map: Dicionário {linha_1based: está_dobrada}.
        """
        self.gutter.set_fold_map(fold_map)
        logger.debug("CodeFolding gutter update: %d regions", len(fold_map))

    def _on_gutter_fold_click(self, line: int) -> None:
        """Alterna fold ao clicar no ícone ▸/▾ do gutter.

        Args:
            line: Número da linha (1-based) clicada.
        """
        if self.code_folding:
            self.code_folding.toggle_fold(line)

    def _on_color_edit(
        self,
        old_text: str,
        new_hex: str,
        line: int,
        col: int,
    ) -> None:
        """Substitui uma cor no editor após edição pelo color picker.

        Chamado pelo ColorPreview quando o usuário escolhe uma nova cor.

        Args:
            old_text: Texto original da cor (ex: '#ff0000').
            new_hex: Nova cor em formato hex (ex: '#00ff00').
            line: Número da linha (1-based) onde a cor está.
            col: Coluna (0-based) de início do valor de cor.
        """
        try:
            line_text = self.text.get(f"{line}.0", f"{line}.end")
            pos = line_text.find(old_text, col)
            if pos >= 0:
                start = f"{line}.{pos}"
                end = f"{line}.{pos + len(old_text)}"
                self.text.delete(start, end)
                self.text.insert(start, new_hex)
                self.modified = True
        except tk.TclError:
            pass

    @staticmethod
    def _detect_git_root(path: Path) -> Path | None:
        """Detecta a raiz do repositório Git para um arquivo.

        Percorre os diretórios pais buscando um diretório `.git`.

        Args:
            path: Caminho do arquivo aberto no editor.

        Returns:
            Path | None: Caminho da raiz do repositório ou None.
        """
        try:
            current = path.parent if path.is_file() else path
            for parent in (current, *current.parents):
                if (parent / ".git").exists():
                    return parent
        except OSError:
            pass
        return None

    def _decode_bytes(self, raw: bytes) -> str:
        """Detecta encoding e decodifica bytes, atualizando self.encoding.

        Ordem de tentativa:
        1. chardet / charset_normalizer se disponível (detecção automática)
        2. BOM UTF-8 / UTF-16 / UTF-32
        3. Lista de encodings comuns por tentativa
        """
        _detected: str | None = None
        with contextlib.suppress(ImportError, Exception):
            import charset_normalizer as _cn

            result = _cn.from_bytes(raw).best()
            if result and result.encoding:
                _detected = str(result.encoding)
        if _detected is None:
            with contextlib.suppress(ImportError, Exception):
                import chardet as _cd

                guess = _cd.detect(raw)
                if guess and guess.get("confidence", 0) > 0.7:
                    _detected = guess["encoding"]

        if _detected:
            with contextlib.suppress(UnicodeDecodeError, LookupError):
                content = raw.decode(_detected)
                self.encoding = _detected.upper().replace("-SIG", "")
                return content

        if raw.startswith(b"\xef\xbb\xbf"):
            self.encoding = "UTF-8"
            return raw[3:].decode("utf-8", errors="replace")
        if raw.startswith(b"\xff\xfe\x00\x00"):
            self.encoding = "UTF-32-LE"
            return raw[4:].decode("utf-32-le", errors="replace")
        if raw.startswith(b"\x00\x00\xfe\xff"):
            self.encoding = "UTF-32-BE"
            return raw[4:].decode("utf-32-be", errors="replace")
        if raw.startswith(b"\xff\xfe"):
            self.encoding = "UTF-16-LE"
            return raw[2:].decode("utf-16-le", errors="replace")
        if raw.startswith(b"\xfe\xff"):
            self.encoding = "UTF-16-BE"
            return raw[2:].decode("utf-16-be", errors="replace")

        for enc in (
            "utf-8",
            "cp1252",
            "cp1250",
            "iso-8859-1",
            "iso-8859-2",
            "shift-jis",
            "gb2312",
            "euc-kr",
            "latin-1",
        ):
            with contextlib.suppress(UnicodeDecodeError, LookupError):
                content = raw.decode(enc)
                self.encoding = enc.upper()
                return content

        self.encoding = "LATIN-1"
        return raw.decode("latin-1", errors="replace")

    def _sanitize_for_display(self, content: str) -> tuple[str, int, int]:
        """Remove/substitui caracteres invisíveis que destroem a performance do Tk.

        Usa str.translate() com tabela pré-computada - O(n) único passo, muito
        mais rápido que múltiplos .replace() em loop.

        Também trunca linhas individuais que excedem MAX_LINE_RENDER_CHARS para
        evitar que o layout engine do Tk trave em linhas monstruosas.

        Returns:
            (content_sanitizado, n_chars_removidos, n_linhas_truncadas)
        """
        original_len = len(content)

        sanitized = content.translate(_SANITIZE_TABLE)
        n_removed = original_len - len(sanitized)

        n_truncated = 0
        if any(len(ln) > MAX_LINE_RENDER_CHARS for ln in sanitized.split("\n", 500)):
            lines_out: list[str] = []
            for ln in sanitized.split("\n"):
                if len(ln) > MAX_LINE_RENDER_CHARS:
                    lines_out.append(
                        ln[:MAX_LINE_RENDER_CHARS]
                        + f"  [...+{len(ln) - MAX_LINE_RENDER_CHARS} chars truncados]"
                    )
                    n_truncated += 1
                else:
                    lines_out.append(ln)
            sanitized = "\n".join(lines_out)

        return sanitized, n_removed, n_truncated

    def _show_sanitize_banner(self, n_removed: int, n_truncated: int) -> None:
        """Exibe banner informativo quando o arquivo foi sanitizado."""
        parts: list[str] = []
        if n_removed:
            parts.append(
                f"{n_removed:,} caractere(s) invisível(is) removido(s)")
        if n_truncated:
            parts.append(
                f"{n_truncated} linha(s) truncada(s) (>{MAX_LINE_RENDER_CHARS:,} chars)"
            )
        if not parts:
            return
        msg = (
            "Sanitizacao aplicada: " +
            ", ".join(parts) + "  (arquivo original intacto)"
        )
        try:
            t = self._tmgr.theme
            lbl = tk.Label(
                self._editor_frame,
                text=msg,
                bg=getattr(t, "warn_bg", "#332b00"),
                fg=getattr(t, "warn_fg", "#f9e2af"),
                font=(FONT_UI, 8),
                anchor="w",
                padx=8,
                cursor="hand2",
            )
            lbl.pack(side="bottom", fill="x")
            lbl.bind(
                "<Button-1>", lambda _e: lbl.destroy() if lbl.winfo_exists() else None
            )
            self.after(12_000, lambda: lbl.destroy()
                       if lbl.winfo_exists() else None)
        except tk.TclError:
            pass

    def _normalize_eol(self, content: str) -> tuple[str, str]:
        """Detecta e normaliza final de linha; retorna (content_lf, eol_original)."""
        crlf = "\r\n"
        cr = "\r"
        lf = "\n"
        if crlf in content:
            return content.replace(crlf, lf), crlf
        if cr in content:
            return content.replace(cr, lf), cr
        return content, lf

    def _sniff_encoding_eol(self, header: bytes) -> tuple[str, str]:
        """Detecta encoding e EOL a partir dos primeiros bytes do arquivo."""
        bom8 = bytes([0xEF, 0xBB, 0xBF])
        bom16le = bytes([0xFF, 0xFE])
        bom16be = bytes([0xFE, 0xFF])
        if header.startswith(bom8):
            enc = "UTF-8 BOM"
        elif header.startswith(bom16le) or header.startswith(bom16be):
            enc = "UTF-16"
        else:
            enc = "UTF-8"
        crlf_b = bytes([0x0D, 0x0A])
        cr_b = bytes([0x0D])
        if crlf_b in header:
            eol = chr(0x0D) + chr(0x0A)
        elif cr_b in header:
            eol = chr(0x0D)
        else:
            eol = chr(0x0A)
        return enc, eol

    def _disable_heavy_features(self) -> None:
        """Desativa funcionalidades pesadas para melhorar performance em arquivos grandes."""

        self._is_loading = False
        self._load_chunks = []
        with contextlib.suppress(tk.TclError):
            self.hl.disable_large_file()
        with contextlib.suppress(tk.TclError):
            if self.sticky_scroll:
                self.sticky_scroll.set_enabled(False)
        with contextlib.suppress(tk.TclError):
            if self.code_folding:
                self.code_folding.unfold_all()
        with contextlib.suppress(tk.TclError):
            if self.inline_blame:
                self.inline_blame.set_enabled(False)
        with contextlib.suppress(tk.TclError):
            if self.color_preview:
                self.color_preview.set_enabled(False)
        with contextlib.suppress(tk.TclError):
            if self.inlay_hints:
                self.inlay_hints.set_enabled(False)
        with contextlib.suppress(Exception):
            self._lint_mgr.schedule(None, "", "text")

    def _restore_full_features(self) -> None:
        """
        IMPORTANTE: chamar somente DEPOIS que o conteúdo correto já está no widget.
        """
        with contextlib.suppress(tk.TclError):
            if self.sticky_scroll and self._cfg.get("sticky_scroll", True):
                self.after(
                    50,
                    lambda: (
                        self.sticky_scroll.set_enabled(True)
                        if self.sticky_scroll
                        else None
                    ),
                )
        with contextlib.suppress(tk.TclError):
            if self.inline_blame:
                self.inline_blame.set_enabled(
                    self._cfg.get("inline_blame", False))
        with contextlib.suppress(tk.TclError):
            if self.color_preview:
                self.color_preview.set_enabled(
                    self._cfg.get("color_preview", True))
        with contextlib.suppress(tk.TclError):
            if self.inlay_hints:
                self.inlay_hints.set_enabled(
                    self._cfg.get("inlay_hints", False))
        with contextlib.suppress(tk.TclError):
            self.text.config(undo=True, maxundo=-1)

    def _post_load_common(self, path: Path) -> None:
        """Rotinas comuns executadas após qualquer tipo de carga."""
        self._on_title(path.name, False)
        self._on_cursor(1, 1)
        with contextlib.suppress(tk.TclError):
            self.text.mark_set(tk.INSERT, "1.0")
            self.text.see("1.0")
        _git_root = self._detect_git_root(path)
        if not (self._is_large_mode or self._is_giant_mode):
            with contextlib.suppress(tk.TclError):
                if self.inline_blame:
                    self.inline_blame.set_file(path, _git_root)
            with contextlib.suppress(tk.TclError):
                if self.color_preview:
                    self.color_preview.notify_change()
            with contextlib.suppress(tk.TclError):
                if self.inlay_hints:
                    self.inlay_hints.set_lang(self.lang)
                    self.inlay_hints.notify_change()
            with contextlib.suppress(tk.TclError):
                if self.code_folding:
                    self.code_folding._scan_regions()
            with contextlib.suppress(tk.TclError):
                if self.sticky_notes:
                    self.sticky_notes.set_file(str(path))
            with contextlib.suppress(tk.TclError, AttributeError):
                if self.sticky_scroll:
                    self.sticky_scroll.notify_scroll()
            with contextlib.suppress(tk.TclError):
                self.after(300, self._check_indent_style)

        with contextlib.suppress(Exception):
            if self.file_watcher:
                self.file_watcher.watch(path)

        with contextlib.suppress(tk.TclError):
            self.text.edit_modified(False)
        self.modified = False

    def _check_indent_style(self) -> None:
        """Detecta o estilo de indentação predominante e avisa sobre mistura."""
        with contextlib.suppress(tk.TclError, Exception):
            content = self.text.get("1.0", "1000.end")
            lines = content.splitlines()
            tab_lines = sum(1 for l_ in lines if l_.startswith("\t"))
            space_lines = sum(
                1 for l_ in lines if l_.startswith("    ") or l_.startswith("  ")
            )
            total = tab_lines + space_lines
            if total < 3:
                return
            if tab_lines > 0 and space_lines > 0:
                minority = min(tab_lines, space_lines)
                if minority / total > 0.15:
                    self._show_context_info(
                        _t(
                            "editor.mixed_indent_warn",
                            "⚠ Indentação mista: {t} linhas com tab - {s} com espaços",
                            t=tab_lines,
                            s=space_lines,
                        )
                    )

    def _on_file_changed_externally(self, path: Path) -> None:
        """Notifica o usuário que o arquivo foi modificado por outro programa.

        Oferece opção de recarregar ou manter versão em edição.

        Args:
            path: Caminho do arquivo que foi alterado externamente.
        """
        with contextlib.suppress(tk.TclError, Exception):
            if not path.exists():
                self._show_context_info(
                    _t(
                        "editor.file_deleted_extern",
                        "⚠ Arquivo deletado externamente: {name}",
                        name=path.name,
                    )
                )
                return

            banner = tk.Frame(
                self._editor_frame,
                bg="#8b4513",
                relief="flat",
            )

            def _reload(_=None):
                with contextlib.suppress(tk.TclError):
                    banner.destroy()
                if self.file_watcher:
                    self.file_watcher.reset_baseline()
                self.load(path)

            def _dismiss(_=None):
                with contextlib.suppress(tk.TclError):
                    banner.destroy()
                if self.file_watcher:
                    self.file_watcher.reset_baseline()

            msg = _t(
                "editor.file_changed_extern",
                "{name} foi modificado externamente.",
                name=path.name,
            )
            tk.Label(banner, text=msg, bg="#8b4513", fg="#fff", font=(FONT_UI, 9)).pack(
                side="left", padx=8, pady=4
            )
            for lbl, cmd in (
                (_t("editor.file_reload", "Recarregar"), _reload),
                (_t("editor.file_keep", "Manter"), _dismiss),
            ):
                btn = tk.Label(
                    banner,
                    text=lbl,
                    bg="#a0522d",
                    fg="#fff",
                    font=(FONT_UI, 8, "bold"),
                    cursor="hand2",
                    padx=6,
                    pady=2,
                )
                btn.pack(side="right", padx=4, pady=3)
                btn.bind("<Button-1>", cmd)
                btn.bind("<Enter>", lambda _, w=btn: w.config(bg="#c47a45"))
                btn.bind("<Leave>", lambda _, w=btn: w.config(bg="#a0522d"))

            banner.pack(fill="x", before=self.text)
            banner.after(
                30_000, lambda: banner.destroy() if banner.winfo_exists() else None
            )

    def _close_vfb(self) -> None:
        """Fecha e descarta o VirtualFileBuffer atual, limpa estado giant."""
        if self._vfb is not None:
            with contextlib.suppress(Exception):
                self._vfb._cancelled = True
                self._vfb.close()
            self._vfb = None
        if self._vp_shift_job:
            with contextlib.suppress(tk.TclError):
                self.after_cancel(self._vp_shift_job)
            self._vp_shift_job = None

        with contextlib.suppress(tk.TclError, AttributeError):
            if self._giant_status and self._giant_status.winfo_exists():
                self._giant_status.pack_forget()
                self._giant_status.destroy()
        self._giant_status = None
        self._is_giant_mode = False
        self._is_large_mode = False

    def _show_loading_overlay(self, message: str = "") -> None:
        """Exibe banner de carregamento centralizado sobre o editor."""
        self._hide_loading_overlay()
        t = self._tmgr.theme
        try:
            overlay = tk.Frame(
                self._editor_frame,
                bg=getattr(t, "floating_bg", t.editor_bg),
                relief="flat",
                highlightthickness=1,
                highlightbackground=getattr(t, "floating_border", t.fg_muted),
            )
            lbl = tk.Label(
                overlay,
                text=message,
                bg=getattr(t, "floating_bg", t.editor_bg),
                fg=t.fg,
                font=(FONT_UI, 11),
                justify="center",
                padx=24,
                pady=16,
                wraplength=460,
            )
            lbl.pack()
            overlay.place(relx=0.5, rely=0.5, anchor="center")
            self._loading_overlay = overlay
            self._loading_label = lbl
        except tk.TclError:
            pass

    def _update_loading_overlay(self, text: str) -> None:
        """Atualiza o texto do banner de carregamento."""
        try:
            if self._loading_label and self._loading_label.winfo_exists():
                self._loading_label.config(text=text)
        except tk.TclError:
            pass

    def _hide_loading_overlay(self) -> None:
        """Remove o banner de carregamento."""
        try:
            if self._loading_overlay and self._loading_overlay.winfo_exists():
                self._loading_overlay.place_forget()
                self._loading_overlay.destroy()
        except tk.TclError:
            pass
        self._loading_overlay = None
        self._loading_label = None

    def _show_perf_banner(self, reason: str, n_lines: int) -> None:
        """Exibe faixa informativa quando features são reduzidas por arquivo denso.

        Args:
            reason:  Descrição curta do que foi desativado (ex: 'highlight parcial').
            n_lines: Número de linhas do arquivo atual.
        """
        self._hide_perf_banner()
        try:
            t = self._tmgr.theme
            msg = (
                f"Arquivo com {n_lines:,} linhas - {reason} ativo. "
                "Lint e LSP operam sob demanda.  "
                "[Limpar memória]"
            )
            self._perf_banner = tk.Label(
                self._editor_frame,
                text=msg,
                bg=getattr(t, "warn_bg", "#3a2a00"),
                fg=getattr(t, "warn_fg", "#ffd740"),
                font=(FONT_UI, 8),
                anchor="w",
                padx=8,
                cursor="hand2",
            )
            self._perf_banner.pack(side="bottom", fill="x")
            self._perf_banner.bind(
                "<Button-1>", lambda _e: self.purge_memory())
        except tk.TclError:
            pass

    def _hide_perf_banner(self) -> None:
        """Remove o banner de performance se estiver visível."""
        try:
            if self._perf_banner and self._perf_banner.winfo_exists():
                self._perf_banner.destroy()
        except tk.TclError:
            pass
        self._perf_banner = None

    def purge_memory(self) -> None:
        """Libera toda a memória não-essencial da aba atual.

        grandes - o arquivo permanece editável mas sem realce de sintaxe até
        recarregar.

        Pode ser chamado via botão no banner de performance ou via comando/menu.
        """
        import gc

        self.hl.disable_large_file()
        with contextlib.suppress(tk.TclError):
            for tag in self.text.tag_names():
                if tag not in (
                    "sel",
                    "current_line",
                    "bracket_match",
                    "find_highlight",
                    "find_current",
                ):
                    self.text.tag_remove(tag, "1.0", "end")
        _OM_CACHE.clear()
        with contextlib.suppress(tk.TclError):
            self.text.edit_reset()
        self._diags.clear()
        self._kdocs.clear()
        self.gutter.set_lint_lines([])
        self.gutter.set_kdocs_lines([])
        gc.collect()
        self._hide_perf_banner()

        try:
            t = self._tmgr.theme
            lbl = tk.Label(
                self._editor_frame,
                text=_t("pane.memory_freed"),
                bg=getattr(t, "ok_bg", "#002200"),
                fg=getattr(t, "ok_fg", "#69ff47"),
                font=(FONT_UI, 8),
                anchor="w",
                padx=8,
            )
            lbl.pack(side="bottom", fill="x")
            self.after(4000, lambda: lbl.destroy()
                       if lbl.winfo_exists() else None)
        except tk.TclError:
            pass

    def _show_giant_status_label(self) -> None:
        """Cria a faixa de posição exibida em modo giant."""
        try:
            if self._giant_status and self._giant_status.winfo_exists():
                self._giant_status.destroy()
            t = self._tmgr.theme
            self._giant_status = tk.Label(
                self._editor_frame,
                text="",
                bg=getattr(t, "activity_bg", t.editor_bg),
                fg=getattr(t, "fg_muted", t.fg),
                font=(FONT_UI, 8),
                anchor="w",
                padx=8,
            )
            self._giant_status.pack(side="bottom", fill="x")
        except tk.TclError:
            pass

    def _update_giant_status(self) -> None:
        """Atualiza a faixa de posição com a linha atual no arquivo completo."""
        vfb = self._vfb
        if not self._is_giant_mode or vfb is None:
            return
        try:
            if not vfb.is_ready:
                return
            total = vfb.total_lines
            start = self._viewport_start + 1
            end = min(self._viewport_start + VIEWPORT_LINES, total)
            edits = len(vfb._edits)
            edit_s = f"  -  {edits} linha(s) editada(s)" if edits else ""
            text = (
                f"  📄  Exibindo linhas {start:,}–{end:,} "
                f"de {total:,}{edit_s}   "
                "[Modo Arquivo Gigante - scroll para navegar]"
            )
            if self._giant_status and self._giant_status.winfo_exists():
                self._giant_status.config(text=text)
        except tk.TclError:
            pass

    def _show_large_file_notice(self) -> None:
        """Exibe brevemente uma mensagem sobre os recursos desativados."""
        if self._is_giant_mode:
            msg = (
                "Modo Arquivo Gigante ativo - viewport virtual. "
                "Scroll para navegar. Salvar funciona normalmente."
            )
        else:
            msg = "Modo Arquivo Grande - highlight, lint e autocomplete desativados para performance."
        self._show_context_info(msg)

    def destroy(self) -> None:
        """Libera todos os recursos associados ao painel: bindings, after-jobs e overlays."""

        for job_attr in (
            "_hover_job",
            "_hide_job",
            "_vp_shift_job",
            "_hl_scroll_job",
            "_kdocs_job",
            "_symbols_job",
            "_lsp_change_job",
        ):
            job_id = getattr(self, job_attr, None)
            if job_id:
                with contextlib.suppress(tk.TclError, ValueError):
                    self.after_cancel(job_id)
                setattr(self, job_attr, None)

        if hasattr(self, "line_numbers") and self.line_numbers:
            after_id = getattr(self.line_numbers, "_after_id", None)
            if after_id:
                with contextlib.suppress(tk.TclError, ValueError):
                    self.line_numbers.after_cancel(after_id)
                self.line_numbers._after_id = None

        self._close_vfb()

        if self._vp_shift_job:
            with contextlib.suppress(tk.TclError):
                self.after_cancel(self._vp_shift_job)
            self._vp_shift_job = None

        with contextlib.suppress(tk.TclError):
            self._hide_loading_overlay()
        with contextlib.suppress(tk.TclError):
            self._hide_perf_banner()
        with contextlib.suppress(tk.TclError):
            if self._giant_status and self._giant_status.winfo_exists():
                self._giant_status.destroy()
            self._giant_status = None

        if hasattr(self, "autocomplete"):
            with contextlib.suppress(tk.TclError):
                self.autocomplete.hide()
        with contextlib.suppress(tk.TclError):
            self._hide_hover()
        with contextlib.suppress(tk.TclError):
            self._close_balloon()

        with contextlib.suppress(tk.TclError):
            if self.sticky_scroll:
                self.sticky_scroll.destroy()
        with contextlib.suppress(tk.TclError):
            if self.code_folding:
                self.code_folding.destroy()
        with contextlib.suppress(tk.TclError):
            if self.inline_blame:
                self.inline_blame.destroy()
        with contextlib.suppress(tk.TclError):
            if self.color_preview:
                self.color_preview.destroy()
        with contextlib.suppress(tk.TclError):
            if self.inlay_hints:
                self.inlay_hints.destroy()
        with contextlib.suppress(tk.TclError):
            if self.complexity_lens:
                self.complexity_lens.destroy()
        with contextlib.suppress(tk.TclError):
            if self.focus_block:
                self.focus_block.destroy()
        with contextlib.suppress(tk.TclError):
            if self.sticky_notes:
                self.sticky_notes.destroy()

        with contextlib.suppress(tk.TclError):
            self.text.config(state="normal")
            self.text.delete("1.0", "end")
            self.text.edit_reset()

        with contextlib.suppress(Exception):
            _OM_CACHE.clear()

        with contextlib.suppress(tk.TclError):
            super().destroy()
