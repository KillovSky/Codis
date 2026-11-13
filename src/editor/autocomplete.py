"""Motor de autocompletar do editor do Codis.

Provê ``AutocompletePopup`` para sugestões baseadas em LSP e palavras
do buffer, e ``IgnoreRules`` para filtrar itens indesejados com base
em regras de glob similares às do ``.gitignore``.
"""

from __future__ import annotations

import contextlib
import fnmatch
import logging
import re
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from typing import Any

from src.constants import (
    AUTOCOMPLETE_DELAY,
    AUTOCOMPLETE_MAX_ITEMS,
    AUTOCOMPLETE_MIN_CHARS,
    FONT_MONO,
    FONT_SIZE,
    LANG_KEYWORDS,
)
from src.editor.snippets import SnippetEngine
from src.editor.suggest import SuggestionEngine, analyze
from src.theme.manager import ThemeManager

logger = logging.getLogger("Codis")


class IgnoreRules:
    """
    Gerencia regras de exclusão (ignore) a partir de arquivos como .gitignore e .eslintignore.

    Implementa o comportamento semelhante ao gitignore:
      - Padrões com `*` e `?` (usando fnmatch).
      - Negação com `!` no início.
      - `**` para qualquer número de subdiretórios.
      - Padrões sem `/` são testados contra cada componente do caminho \
                                        (correspondência por nome base).
      - Padrões com `/` são testados contra o caminho relativo completo.

    Attributes:
        root: Diretório raiz a partir do qual os caminhos relativos são calculados.
        patterns: Lista de tuplas (pattern, is_negation, is_dir_only) \
            representando as regras carregadas.
    """

    def __init__(self, root: Path) -> None:
        """Inicializa as regras de exclusão de autocompletar a partir de padrões glob."""
        self.root = root
        self.patterns: list[tuple[str, bool, bool]] = []

    def load(self) -> None:
        """Carrega padrões dos arquivos .gitignore e .eslintignore presentes na raiz."""
        self.patterns.clear()
        for ignore_file in (".gitignore", ".eslintignore"):
            p = self.root / ignore_file
            if p.exists():
                try:
                    with open(p, encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line or line.startswith("#"):
                                continue
                            self._add_pattern(line)
                except (OSError, UnicodeDecodeError):
                    continue

    def _add_pattern(self, line: str) -> None:
        """Interpreta uma linha de regra e adiciona à lista de padrões."""
        is_negation = line.startswith("!")
        if is_negation:
            line = line[1:].lstrip()
        is_dir_only = line.endswith("/")
        if is_dir_only:
            line = line[:-1]
        self.patterns.append((line, is_negation, is_dir_only))

    @staticmethod
    def _matches(pattern: str, rel_path: str) -> bool:
        """
        Verifica se um caminho relativo corresponde a um padrão específico.

        Args:
            pattern: Padrão da regra (já tratado, sem `!` ou `/` final).
            rel_path: Caminho relativo no formato POSIX (separador '/').

        Returns:
            True se houver correspondência, False caso contrário.
        """
        parts = rel_path.split("/")

        if "**" in pattern:
            regex = re.escape(pattern).replace(r"\*\*", ".*")
            regex = regex.replace(r"\*", "[^/]*").replace(r"\?", "[^/]")
            return bool(re.match(f"^{regex}$", rel_path))

        if "/" in pattern:
            pat = pattern.lstrip("/")
            return fnmatch.fnmatch(rel_path, pat) or rel_path.startswith(pat + "/")

        return any(fnmatch.fnmatch(part, pattern) for part in parts)

    def is_ignored(self, path: Path) -> bool:
        """
        Determina se um caminho deve ser ignorado com base nas regras carregadas.

        A avaliação segue a ordem das regras: a última regra que corresponder
        (considerando negação) prevalece.

        Args:
            path: Caminho absoluto a ser verificado.

        Returns:
            True se o caminho for ignorado, False caso contrário.
        """
        try:
            rel = str(path.relative_to(self.root).as_posix())
        except ValueError:
            return False

        ignored = False
        for pattern, is_negation, _ in self.patterns:
            if self._matches(pattern, rel):
                if is_negation:
                    ignored = False
                else:
                    ignored = True
        return ignored


class AutocompletePopup:
    """
    Popup flutuante de autocomplete com estilo semelhante ao VS Code.

    Exibe sugestões contextuais:
      - Palavras-chave da linguagem.
      - Snippets registrados.
      - Símbolos do documento (funções, classes, variáveis).
      - Palavras do próprio buffer.
      - (Opcional) Sugestões do LSP, quando conectado.

    Atalhos de teclado:
      - ↑ / ↓ : navegar na lista.
      - Tab / Enter : aceitar a sugestão atual.
      - Esc : fechar o popup.
      - Qualquer tecla de caractere : refiltrar as sugestões (após um pequeno atraso).

    Ícones são usados para identificar visualmente o tipo de cada item.
    """

    ICONS = {
        "keyword": "🔑",
        "snippet": "✂️ ",
        "function": "ƒ ",
        "class": "◆ ",
        "variable": "𝑥 ",
        "property": "  ",
        "module": "📦",
        "constant": "π ",
        "param": "->",
        "method": "⚙ ",
        "field": "▪ ",
    }

    def __init__(
        self, parent: tk.Widget, text_widget: tk.Text, tmgr: ThemeManager
    ) -> None:
        """
        Inicializa o popup associado a um widget de texto.

        Args:
            parent: Widget pai (normalmente a janela principal).
            text_widget: Widget Text onde o autocomplete será inserido.
            tmgr: Gerenciador de temas para cores e estilos.
        """
        self._parent = parent
        self._txt = text_widget
        self._tmgr = tmgr
        self._popup: tk.Toplevel | None = None
        self._listbox: tk.Listbox | None = None
        self._items: list[tuple[str, str, str]] = []
        self._job: str | None = None
        self._lang = "text"
        self._snippets: SnippetEngine | None = None
        self._doc_symbols: list[str] = []
        self._hover_win: tk.Toplevel | None = None
        self._lsp_mgr = None
        self._lsp_path = None
        self._engine = SuggestionEngine(AUTOCOMPLETE_MAX_ITEMS)
        self._details: list[str] = []
        self._query_pos: str | None = None

        self._root_win = self._get_root_window()
        if self._root_win is not None:
            self._root_win.bind(
                "<Unmap>", lambda _: self._on_root_unmap(), add=True)
            self._root_win.bind(
                "<Map>", lambda _: self._on_root_map(), add=True)

    def _get_root_window(self) -> tk.Tk | None:
        """Retorna a janela raiz Tk a partir do widget pai."""
        try:
            w = self._parent
            while w is not None:
                if isinstance(w, tk.Tk):
                    return w
                w = w.master
        except Exception:
            logger.debug("Falha ao localizar janela raiz Tk.", exc_info=True)
        return None

    def _on_root_unmap(self) -> None:
        """Esconde todos os popups flutuantes quando a janela principal é minimizada."""
        import contextlib

        if self._popup and self._popup.winfo_exists():
            with contextlib.suppress(Exception):
                self._popup.withdraw()
        if self._hover_win and self._hover_win.winfo_exists():
            with contextlib.suppress(Exception):
                self._hover_win.withdraw()

    def _on_root_map(self) -> None:
        """Sem-op: não restaura popups automaticamente ao restaurar a janela.

        O autocomplete reaparece naturalmente na próxima vez que o usuário
        digitar. Tentar restaurar aqui causaria popups órfãos visíveis sem
        contexto de digitação.
        """

    def set_lang(self, lang: str) -> None:
        """Define a linguagem atual para filtrar palavras-chave e snippets."""
        self._lang = lang

    def set_snippets(self, engine: SnippetEngine) -> None:
        """Fornece o motor de snippets para inclusão de sugestões de snippets."""
        self._snippets = engine

    def set_lsp_manager(self, lsp_mgr, path) -> None:
        """
        Conecta um gerenciador LSP para obter sugestões adicionais.

        Args:
            path: Caminho do arquivo atual (para consulta LSP).
        """
        self._lsp_mgr = lsp_mgr
        self._lsp_path = path

    def trigger(self, delay: int = AUTOCOMPLETE_DELAY) -> None:
        """
        Agenda a exibição do popup após um atraso (debounce).

        Se já houver um job agendado, ele é cancelado antes.

        Args:
            delay: Milissegundos de espera antes de computar as sugestões.
        """
        if self._job:
            with contextlib.suppress(tk.TclError):
                self._txt.after_cancel(self._job)
        self._job = self._txt.after(delay, self._compute)

    _BIG_FILE_LINES = 4000

    def _icons(self) -> dict[str, str]:
        return dict(self.ICONS)

    def _rank(self, ctx: Any, line_no: int, lsp_items: Any = ()) -> list[tuple[str, str, str]]:
        """Pede ao motor as sugestões ordenadas e converte para ``(label, insert, kind)``."""
        snippets: list[tuple[str, str]] = []
        if self._snippets:
            snippets = [(sn.prefix, sn.description or "") for sn in self._snippets.get_for_lang(self._lang)]
        cands = self._engine.suggest(
            ctx, self._lang, cursor_line=line_no, keywords=LANG_KEYWORDS.get(self._lang, []),
            snippets=snippets, lsp_items=lsp_items, icons=self._icons(),
        )
        self._details = [c.detail for c in cands]
        return [(c.label, c.insert, c.kind) for c in cands]

    def _compute(self) -> None:
        """Calcula e exibe as sugestões para a posição do cursor.

        O contexto léxico (comentário/string/``def |``) pode suprimir o popup; após
        ``.`` sugere membros; o ranking combina casamento, fonte, frequência,
        proximidade e o que o usuário costuma aceitar.  O LSP, quando responde,
        entra no mesmo ranking (não apenas no fim da lista).
        """
        try:
            pos = self._txt.index(tk.INSERT)
            line_no, col_no = (int(x) for x in pos.split("."))
            start = "1.0" if line_no <= self._BIG_FILE_LINES else f"{line_no - self._BIG_FILE_LINES}.0"
            before = self._txt.get(start, pos)
            full = self._txt.get("1.0", "end-1c")
        except (tk.TclError, ValueError):
            return

        ctx = analyze(before, self._lang)
        if ctx.suppress or (not ctx.member and len(ctx.prefix) < AUTOCOMPLETE_MIN_CHARS):
            self.hide()
            return

        self._engine.update_document(full, self._lang, line_no)
        self._query_pos = pos
        items = self._rank(ctx, line_no)
        lsp_ready = bool(self._lsp_mgr and self._lsp_path)
        if not items and not lsp_ready:
            self.hide()
            return
        if items:
            self._items = items
            self._show(pos)
        elif self.is_visible():
            self.hide()

        if lsp_ready:
            try:

                def _lsp_cb(lsp_items: Any) -> None:
                    """Reordena com os itens do LSP no mesmo ranking."""
                    if not lsp_items or not self._txt.winfo_exists():
                        return
                    try:
                        if self._txt.index(tk.INSERT) != self._query_pos:
                            return
                    except tk.TclError:
                        return
                    conv = []
                    for ci in lsp_items:
                        ins = (ci.insert_text or ci.label).strip()
                        plain = re.sub(r"\$\{?\d+(?::[^}]*)?\}?", "", ins).strip() or ci.label
                        conv.append((ci.label, plain, str(ci.kind_name).lower(), ci.icon))
                    merged = self._rank(ctx, line_no, conv)
                    if merged:
                        self._items = merged
                        self._show(self._query_pos)

                self._lsp_mgr.completion(self._lsp_path, self._lang, line_no - 1, col_no, None, _lsp_cb)
            except (OSError, AttributeError, RuntimeError):
                pass

    def _show(self, cursor_pos: str) -> None:
        """
        Exibe o popup na posição do cursor, ajustando para não sair da tela.

        Args:
            cursor_pos: Índice Tkinter no formato "linha.coluna" (ex: "10.5").
        """
        t = self._tmgr.theme
        try:
            bbox = self._txt.bbox(cursor_pos)
            if bbox:
                x, y, _, h = bbox
            else:
                line, col = map(int, cursor_pos.split("."))
                dline = self._txt.dlineinfo(f"{line}.0")
                if not dline:
                    return
                x_line, y, _, h, _ = dline

                font = tkfont.Font(font=self._txt.cget("font"))
                char_width = font.measure("0")
                x = x_line + col * char_width
        except tk.TclError:
            return

        wx = self._txt.winfo_rootx() + x
        wy = self._txt.winfo_rooty() + y + h + 2

        if not self._popup or not self._popup.winfo_exists():
            self._popup = tk.Toplevel(self._parent)
            self._popup.wm_overrideredirect(True)
            self._popup.wm_attributes("-topmost", True)
            self._popup.withdraw()

            outer = tk.Frame(self._popup, bg=t.floating_border, padx=1, pady=1)
            outer.pack(fill="both", expand=True)
            inner = tk.Frame(outer, bg=t.floating_bg)
            inner.pack(fill="both", expand=True)

            self._listbox = tk.Listbox(
                inner,
                bg=t.floating_bg,
                fg=t.fg,
                selectbackground=t.sel_bg,
                selectforeground=t.sel_fg,
                relief="flat",
                bd=0,
                font=(FONT_MONO, self._get_font_size()),
                activestyle="none",
                highlightthickness=0,
                exportselection=False,
            )
            self._listbox.pack(fill="both", expand=True, padx=2, pady=2)
            self._listbox.bind("<Double-Button-1>", lambda _: self._accept())
            self._listbox.bind("<Motion>", self._on_hover)
        else:
            assert self._listbox is not None
            self._listbox.delete(0, "end")

        assert self._listbox is not None

        self._listbox.config(
            bg=t.floating_bg,
            fg=t.fg,
            selectbackground=t.sel_bg,
            selectforeground=t.sel_fg,
        )

        for i, (label, _, _kind) in enumerate(self._items):
            det = self._details[i] if i < len(self._details) and self._details[i] else ""
            self._listbox.insert("end", f"  {label}" + (f"    · {det}" if det else ""))

        for i, (_, _, kind) in enumerate(self._items):
            color = {
                "keyword": t.t_keyword,
                "snippet": t.t_func,
                "symbol": t.t_class,
                "variable": t.t_variable,
                "function": t.t_func,
                "method": t.t_func,
                "class": t.t_class,
                "module": t.t_class,
                "constant": t.t_variable,
                "property": t.t_variable,
            }.get(kind, t.fg)
            self._listbox.itemconfig(i, foreground=color)

        count = min(len(self._items), 12)
        lh = self._get_font_size() + 6
        height = count * lh + 8
        width = 380

        screen_w = self._popup.winfo_screenwidth()
        screen_h = self._popup.winfo_screenheight()
        if wx + width > screen_w:
            wx = screen_w - width - 4
        if wy + height > screen_h:
            wy = wy - h - height - 4

        self._popup.geometry(f"{width}x{height}+{wx}+{wy}")
        self._popup.deiconify()
        self._popup.lift()
        self._popup.update_idletasks()
        self._listbox.selection_set(0)
        self._listbox.see(0)

    def _get_font_size(self) -> int:
        """Obtém o tamanho da fonte do widget de texto ou usa o padrão."""
        try:
            spec = str(self._txt.cget("font"))
            for p in reversed(spec.split()):
                if p.isdigit():
                    return int(p)
        except tk.TclError:
            pass
        return FONT_SIZE

    @staticmethod
    def _on_hover(e: Any) -> None:
        """Callback para exibição futura de documentação ao pairar sobre um item."""

    def is_visible(self) -> bool:
        """Retorna ``True`` se o popup de autocompletar está visível."""
        return bool(
            self._popup and self._popup.winfo_exists() and self._popup.winfo_viewable()
        )

    def hide(self) -> None:
        """Esconde o popup e qualquer janela de hover associada."""
        self._hide_hover()
        if self._popup and self._popup.winfo_exists():
            with contextlib.suppress(tk.TclError):
                self._popup.withdraw()

    def _hide_hover(self) -> None:
        """Destrói a janela de hover, se existir."""
        if self._hover_win and self._hover_win.winfo_exists():
            with contextlib.suppress(tk.TclError):
                self._hover_win.destroy()
            self._hover_win = None

    def navigate(self, direction: int) -> bool:
        """
        Navega para cima (direction = -1) ou para baixo (direction = 1) na lista.

        Args:
            direction: -1 para subir, 1 para descer.

        Returns:
            True se a navegação foi realizada (popup visível), False caso contrário.
        """
        if not self.is_visible() or self._listbox is None:
            return False
        cur = self._listbox.curselection()
        idx = cur[0] if cur else -1
        new = max(0, min(len(self._items) - 1, idx + direction))
        self._listbox.selection_clear(0, "end")
        self._listbox.selection_set(new)
        self._listbox.see(new)
        return True

    def accept(self) -> bool:
        """
        Aceita a sugestão atualmente selecionada, inserindo o texto no editor.

        Returns:
            True se alguma sugestão foi aceita, False caso contrário.
        """
        if not self.is_visible() or self._listbox is None:
            return False
        cur = self._listbox.curselection()
        if not cur:
            return False
        idx = cur[0]
        _, insert_text, kind = self._items[idx]
        self._do_insert(insert_text, kind)
        self.hide()
        return True

    def _accept(self) -> None:
        """Versão sem argumentos para binding de eventos."""
        self.accept()

    def _do_insert(self, word: str, kind: str) -> None:
        """
        Insere a palavra selecionada no editor, substituindo o prefixo atual.

        Se for um snippet e houver um motor de snippets, expande-o.

        Args:
            word: Texto a ser inserido (ou prefixo do snippet).
            kind: Tipo do item ('snippet', 'keyword', etc.).
        """
        try:
            pos = self._txt.index(tk.INSERT)
            line_start = f"{pos} linestart"
            text_before = self._txt.get(line_start, pos)
            m = re.search(r"[\w$]+$", text_before)
            if m:
                start_col = len(text_before) - len(m.group())
                line_num = pos.split(".")[0]
                start_idx = f"{line_num}.{start_col}"
                self._txt.delete(start_idx, pos)

            SuggestionEngine.record_accept(word)
            if kind == "snippet" and self._snippets:
                snip = self._snippets.find_prefix(self._lang, word)
                if snip:
                    self._snippets.expand(snip, self._txt)
                    return

            self._txt.insert(tk.INSERT, word)
        except tk.TclError:
            pass

    def update_doc_symbols(self, content: str, lang: str) -> None:
        """
        Extrai símbolos (funções, classes, variáveis) do conteúdo do documento.

        Esses símbolos são adicionados às sugestões de autocomplete.

        Args:
            content: Texto completo do documento.
            lang: Linguagem do documento (para aplicar padrões específicos).
        """
        syms: list[str] = []
        patterns = {
            "python": [
                r"^\s*def\s+(\w+)",
                r"^\s*class\s+(\w+)",
                r"^\s*(\w+)\s*=",
            ],
            "javascript": [
                r"function\s+(\w+)",
                r"class\s+(\w+)",
                r"(?:const|let|var)\s+(\w+)",
                r"(\w+)\s*[:=]\s*(?:function|\(.*\)\s*=>)",
            ],
            "typescript": [
                r"function\s+(\w+)",
                r"class\s+(\w+)",
                r"interface\s+(\w+)",
                r"type\s+(\w+)",
                r"(?:const|let|var)\s+(\w+)",
            ],
        }
        for pattern in patterns.get(lang, []):
            for m in re.finditer(pattern, content, re.MULTILINE):
                name = m.group(1)
                if len(name) > 1 and name not in syms:
                    syms.append(name)
        self._doc_symbols = syms[:200]
