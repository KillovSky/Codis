"""
Codis - SyntaxHL: realce de sintaxe incremental e completo.

Estratégia de duas passadas
-----------------------------
O realce funciona em dois níveis para equilibrar velocidade e cobertura:

1. **Passada de viewport** (:meth:`highlight`): executada imediatamente após
   cada edição (com debounce), cobre apenas a região visível ± 80 linhas.
   É responsável pela resposta visual rápida enquanto o usuário digita.

2. **Passada completa** (:meth:`highlight_full`): agendada via ``after_idle``
   após a passada de viewport.  Processa o arquivo inteiro em lotes
   (*chunks*) de 500 linhas, cedendo o controle entre cada lote via
   ``after(0, ...)``.  Isso garante que o arquivo inteiro seja colorido sem
   travar a UI.

Ao rolar a tela, ``_sync_scroll_y`` do editor chama :meth:`schedule` para
re-destacar a nova região visível.  A passada completa é re-agendada junto.

Posicionamento por linha/coluna
---------------------------------
pré-computado por :func:`_build_offset_map`.  Isso elimina o ``"1.0 + Nc"``
que causava parada de cores após ~2.600 caracteres.
"""

from __future__ import annotations

import contextlib
import logging
import re
import tkinter as tk
from typing import Any

from src.constants import FONT_MONO, FONT_SIZE, HL_IDLE_MS
from src.editor.kdocs import KDocsNotes, KDocsParser
from src.lint.manager import Diag
from src.syntax.patterns import LANG_RE
from src.theme.manager import ThemeManager

logger = logging.getLogger("Codis")

_CHUNK_LINES = 500

_OM_CACHE: dict[tuple[int, int], list[tuple[int, int]]] = {}
_OM_MAX = 4


def _build_offset_map(text: str, start_line: int) -> list[tuple[int, int]]:
    """Constrói mapa de offset de caractere -> (linha, coluna) no widget.

    Percorre *text* uma única vez, registrando a posição ``(linha, coluna)``
    de cada índice.  Usar este mapa é O(n) vs O(n²) do ``index()`` repetido.
    Resultado cacheado por (hash(text), start_line) para reutilização entre
    a passada de viewport e a passada completa no mesmo conteúdo.

    Args:
        text:       String extraída do widget (o trecho a ser realçado).
        start_line: Número da linha do widget onde o trecho começa (base 1).

    Returns:
        Lista de tuplas ``(linha, coluna)`` com comprimento ``len(text) + 1``.
    """
    key = (hash(text), start_line)
    cached = _OM_CACHE.get(key)
    if cached is not None:
        return cached

    mapa: list[tuple[int, int]] = []
    line = start_line
    col = 0
    for ch in text:
        mapa.append((line, col))
        if ch == "\n":
            line += 1
            col = 0
        else:
            col += 1
    mapa.append((line, col))

    if len(_OM_CACHE) >= _OM_MAX:
        _OM_CACHE.pop(next(iter(_OM_CACHE)))
    _OM_CACHE[key] = mapa
    return mapa


class SyntaxHL:
    """Realça sintaxe apenas na viewport visível mais um buffer de 80 linhas.

    Funcionalidades:
        - Highlight incremental (apenas linhas visíveis ± buffer de 80 linhas).
        - Debounce configurável via :meth:`schedule`.
        - Correspondência de colchetes (*bracket matching*).
        - Realce da linha atual do cursor.
        - Tags de lint sobrepostas sem serem destruídas no re-highlight.
        - Anotações KDocs.
        - Renderização de espaços em branco.

    Posicionamento sem offset absoluto
    ------------------------------------
    os índices de cada *match* diretamente em ``"{linha}.{coluna}"`` - a
    notação nativa mais eficiente do Tkinter.  Isso elimina o problema de
    cores parando após certo ponto que ocorria com a notação ``"1.0 + Nc"``
    combinada com ``len()`` para arquivos grandes ou com caracteres multibyte.
    """

    _KEEP_TAGS = frozenset(
        {
            "bracket_match",
            "search_match",
            "search_cur",
            "current_line",
            "lint_error",
            "lint_warn",
            "lint_info",
            "lint_hint",
            "lint_err_line",
            "lint_warn_line",
            "kdocs_note",
            "kdocs_todo",
            "kdocs_warn",
            "kdocs_deprecated",
            "kdocs_link",
            "kdocs_see",
            "kdocs_param",
            "kdocs_return",
            "kdocs_fixme",
            "kdocs_hack",
            "diff_add",
            "diff_del",
            "diff_change",
            "diff_hunk",
            "sel",
        }
    )

    def __init__(self, widget: tk.Text, lang: str, tmgr: ThemeManager) -> None:
        """Inicializa o realçador de sintaxe.

        Args:
            widget: Widget ``tk.Text`` do editor a ser realçado.
            lang:   Identificador de linguagem (ex.: ``"python"``, ``"javascript"``).
            tmgr:   Gerenciador de temas para acesso às cores dos tokens.
        """
        self._w = widget
        self._lang = lang
        self._tmgr = tmgr
        self._cfg = None
        self._tags: set = set()
        self._custom_kw_tags: list[str] = []
        self._custom_kw_pats: list[tuple[re.Pattern, str]] = []
        self._job: str | None = None
        self._full_job: str | None = None
        self._full_gen: int = 0
        self._enabled = True
        self._viewport_only = False
        self._idle_ms: int = HL_IDLE_MS
        self._setup_tags()

    def _font_size(self) -> int:
        """Retorna o tamanho atual da fonte do editor a partir da configuração."""
        try:
            spec = str(self._w.cget("font"))
            parts = spec.split()
            for p in reversed(parts):
                if p.isdigit():
                    return int(p)
        except tk.TclError:
            pass
        return FONT_SIZE

    def _font_family(self) -> str:
        """Retorna a família de fonte atual do editor a partir da configuração."""
        try:
            spec = str(self._w.cget("font"))
            parts = spec.split()
            if parts:
                return parts[0].strip("{}")
        except tk.TclError:
            pass
        return FONT_MONO

    def _setup_tags(self) -> None:
        """Configura as tags de cor e estilo no widget Text para o realce de sintaxe."""
        t = self._tmgr.theme
        fs = self._font_size()
        ff = self._font_family()
        tok = self._tmgr.token_map()

        mono = (ff, fs)
        mono_bold = (ff, fs, "bold")
        mono_italic = (ff, fs, "italic")
        mono_bi = (ff, fs, "bold italic")

        BOLD_TAGS = {
            "keyword",
            "type_kw",
            "heading",
            "table",
            "section",
            "directive",
            "bool",
            "storage",
        }
        ITALIC_TAGS = {
            "comment_line",
            "comment_block",
            "italic",
            "blockquote",
            "shebang",
            "doctype",
        }
        BI_TAGS = {"bold"}

        for tag, color in tok.items():
            if tag in BI_TAGS:
                f = mono_bi
            elif tag in BOLD_TAGS:
                f = mono_bold
            elif tag in ITALIC_TAGS:
                f = mono_italic
            else:
                f = mono
            with contextlib.suppress(tk.TclError):
                self._w.tag_configure(tag, foreground=color, font=f)
            self._tags.add(tag)

        self._w.tag_configure(
            "lint_error", underline=True, foreground=t.err_fg, underlinefg=t.err_fg
        )
        self._w.tag_configure(
            "lint_warn", underline=True, foreground=t.warn_fg, underlinefg=t.warn_fg
        )
        self._w.tag_configure("lint_info", underline=True,
                              foreground=t.info_fg)
        self._w.tag_configure("lint_hint", underline=True,
                              foreground=t.hint_fg)
        self._w.tag_configure("lint_err_line", background=t.err_bg)
        self._w.tag_configure("lint_warn_line", background=t.warn_bg)

        self._w.tag_configure(
            "search_match", background="#614000", foreground="#ffffff"
        )
        self._w.tag_configure(
            "search_cur", background=t.t_func, foreground="#000000")

        self._w.tag_configure("current_line", background=t.line_hl)

        self._w.tag_configure(
            "bracket_match",
            background=t.sel_bg,
            foreground=t.sel_fg,
            font=(ff, fs, "bold"),
        )

        for kind, color in KDocsParser.KIND_COLORS.items():
            self._w.tag_configure(
                f"kdocs_{kind}",
                foreground=color,
                font=(ff, fs, "italic"),
            )

        self._w.tag_configure(
            "diff_add", background=t.diff_add_bg, foreground=t.diff_add_fg
        )
        self._w.tag_configure(
            "diff_del", background=t.diff_del_bg, foreground=t.diff_del_fg
        )
        self._w.tag_configure(
            "diff_change", background=t.diff_change_bg, foreground=t.diff_change_fg
        )
        self._w.tag_configure(
            "diff_hunk",
            background=t.diff_hunk_bg,
            foreground=t.diff_hunk_fg,
            font=(ff, fs, "bold"),
        )

        for bot_tag in (
            "lint_err_line",
            "lint_warn_line",
            "current_line",
            "diff_add",
            "diff_del",
            "diff_change",
            "diff_hunk",
        ):
            with contextlib.suppress(tk.TclError):
                self._w.tag_lower(bot_tag)

        for top_tag in (
            "lint_error",
            "lint_warn",
            "lint_info",
            "bracket_match",
            "search_match",
            "search_cur",
        ):
            with contextlib.suppress(tk.TclError):
                self._w.tag_raise(top_tag)

    def reconfigure(self) -> None:
        """Reconfigura o componente."""
        self._setup_tags()
        self._setup_custom_kw_tags()
        self.highlight()

    def set_config(self, cfg) -> None:
        """Injeta o ConfigManager e aplica as custom keywords imediatamente.

        Chamado pelo EditorPane logo após a criação do SyntaxHL.
        """
        self._cfg = cfg
        self._setup_custom_kw_tags()

    def _setup_custom_kw_tags(self) -> None:
        """Configura/reconfigura as tags de cor para keywords customizadas.

        Lê ``config["custom_keywords"]``, filtra pelas entradas que se aplicam
        à linguagem atual (``lang == "*"`` ou ``lang == self._lang``), cria uma
        tag ``custom_kw_<idx>`` por entrada com a cor escolhida pelo usuário e
        compila um regex ``\\b<keyword>\\b`` para a passada de highlight.
        """
        for tag in self._custom_kw_tags:
            with contextlib.suppress(tk.TclError):
                self._w.tag_remove(tag, "1.0", "end")
                self._w.tag_delete(tag)
        self._custom_kw_tags.clear()
        self._custom_kw_pats.clear()

        if self._cfg is None:
            return

        entries: list[dict] = self._cfg.get("custom_keywords", [])
        if not isinstance(entries, list):
            return

        ff = self._font_family()
        fs = self._font_size()

        for idx, entry in enumerate(entries):
            kw = entry.get("keyword", "").strip()
            color = entry.get("color", "#cba6f7").strip()
            lang = entry.get("lang", "*").strip()

            if not kw:
                continue
            if lang not in ("*", self._lang):
                continue
            if not re.fullmatch(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})", color):
                color = "#cba6f7"

            tag = f"custom_kw_{idx}"
            try:
                self._w.tag_configure(
                    tag, foreground=color, font=(ff, fs, "bold"))
            except tk.TclError:
                continue

            try:
                pat = re.compile(r"\b" + re.escape(kw) + r"\b")
            except re.error:
                continue

            self._custom_kw_tags.append(tag)
            self._custom_kw_pats.append((pat, tag))
            self._tags.add(tag)

    def _apply_custom_kw(
        self, content: str, s_idx: str, e_idx: str, mapa: list
    ) -> None:
        """Aplica as tags de custom keywords ao trecho de conteúdo dado."""
        if not self._custom_kw_pats:
            return
        for pat, tag in self._custom_kw_pats:
            with contextlib.suppress(tk.TclError):
                self._w.tag_remove(tag, s_idx, e_idx)
            for m in pat.finditer(content):
                try:
                    la, ca = mapa[m.start()]
                    lb, cb = mapa[m.end()]
                    self._w.tag_add(tag, f"{la}.{ca}", f"{lb}.{cb}")
                except (tk.TclError, IndexError):
                    pass

    def set_lang(self, lang: str) -> None:
        """Define 'lang'.

        Args:
            lang: Parâmetro da operação.
        """
        self._lang = lang
        self.reconfigure()

    def schedule(self, debounce: int = HL_IDLE_MS) -> None:
        """Agenda passada de viewport com debounce e passada completa em background.

        Cancela o agendamento anterior antes de criar um novo.
        Se o highlight estiver desativado (arquivo grande), não faz nada.

        Args:
            debounce: Tempo de espera em milissegundos antes da passada de viewport.
        """
        if not self._enabled:
            return
        try:
            if self._job:
                self._w.after_cancel(self._job)
            self._job = self._w.after(debounce, self._highlight_and_queue_full)
        except tk.TclError:
            pass

    def set_idle_ms(self, ms: int) -> None:
        """Atualiza o debounce padrão do highlight (lido do ConfigManager).

        Permite que o usuário ajuste o valor via Settings Editor sem reiniciar.
        O valor mínimo é 20ms para não saturar a UI; máximo 500ms.

        Args:
            ms: Novo valor de debounce em milissegundos (20-500).
        """
        self._idle_ms = max(20, min(500, ms))

    def _highlight_and_queue_full(self) -> None:
        """Executa a passada de viewport e agenda a passada completa (se não for viewport_only)."""
        self.highlight()
        if not self._viewport_only:
            self._schedule_full()

    def _schedule_full(self) -> None:
        """Agenda a passada completa para quando a UI estiver ociosa."""
        if not self._enabled:
            return
        self._full_gen += 1
        gen = self._full_gen
        try:
            if self._full_job:
                self._w.after_cancel(self._full_job)
            self._full_job = self._w.after_idle(
                lambda: self._highlight_full_chunked(gen, 1)
            )
        except tk.TclError:
            pass

    def _highlight_full_chunked(self, gen: int, start_line: int) -> None:
        """Processa o arquivo completo em lotes, cedendo o controle entre cada um.

        Cada chamada processa :data:`_CHUNK_LINES` linhas a partir de
        *start_line*.  Se ainda houver linhas, agenda o próximo lote via
        ``after(0, ...)`` para não bloquear a UI.

        Args:
            gen:        Geração desta passada completa - se mudar, abandona.
            start_line: Primeira linha do lote atual (base 1).
        """
        if gen != self._full_gen or not self._enabled:
            return
        pat = LANG_RE.get(self._lang)
        if not pat:
            return
        try:
            total_lines = int(self._w.index("end-1c").split(".")[0])
        except (tk.TclError, ValueError):
            return
        if start_line > total_lines:
            return

        end_line = min(start_line + _CHUNK_LINES - 1, total_lines)
        s_idx = f"{start_line}.0"
        e_idx = f"{end_line}.end"

        try:
            content = self._w.get(s_idx, e_idx)
        except tk.TclError:
            return

        for tag in self._tags:
            if tag not in self._KEEP_TAGS:
                with contextlib.suppress(tk.TclError):
                    self._w.tag_remove(tag, s_idx, e_idx)

        mapa = _build_offset_map(content, start_line)
        for m in pat.finditer(content):
            tag = m.lastgroup
            if not tag:
                continue
            base_tag = re.sub(r"_\d+$", "", tag)
            if base_tag in self._KEEP_TAGS:
                continue
            try:
                la, ca = mapa[m.start()]
                lb, cb = mapa[m.end()]
                self._w.tag_add(base_tag, f"{la}.{ca}", f"{lb}.{cb}")
            except (tk.TclError, IndexError):
                pass

        self._apply_custom_kw(content, s_idx, e_idx, mapa)

        if end_line < total_lines and gen == self._full_gen:
            with contextlib.suppress(tk.TclError):
                self._w.after(
                    0, lambda: self._highlight_full_chunked(gen, end_line + 1)
                )

    def disable_large_file(self) -> None:
        """Desativa o highlight para arquivos grandes.

        Remove todas as tags de sintaxe existentes e impede agendamentos
        futuros.  Deve ser chamado quando o arquivo excede o limite de linhas
        configurado em ``constants.py``.
        """
        self._enabled = False
        self._viewport_only = False
        try:
            if self._job:
                self._w.after_cancel(self._job)
                self._job = None
            if self._full_job:
                self._w.after_cancel(self._full_job)
                self._full_job = None
            self._w.tag_delete(
                *[t for t in self._w.tag_names() if t not in ("sel", "insert")]
            )
        except tk.TclError:
            pass

    def enable_viewport_only(self) -> None:
        """Ativa modo viewport-only: highlight apenas na região visível, sem passada completa.

        Usado para arquivos de código denso acima de HL_LINE_LIMIT linhas mas abaixo
        de HL_FULL_DISABLE.  O highlight é rápido (só ~160 linhas por vez) e não
        bloqueia a UI.  A passada completa em background é desabilitada.
        """
        self._enabled = True
        self._viewport_only = True

        if self._full_job:
            with contextlib.suppress(tk.TclError):
                self._w.after_cancel(self._full_job)
            self._full_job = None

    def reenable(self) -> None:
        """Reativa o highlight após ter sido desativado por disable_large_file().

        Chamado quando o usuário abre um arquivo normal depois de ter aberto
        um arquivo grande na mesma aba.
        """
        self._enabled = True
        self._viewport_only = False

    def suspend(self) -> None:
        """Suspende highlights temporariamente (ex: durante resize de sash)."""
        self._suspended = True
        if self._job:
            with contextlib.suppress(tk.TclError):
                self._w.after_cancel(self._job)
            self._job = None

    def resume(self) -> None:
        """Retoma highlights após suspend()."""
        self._suspended = False
        self.highlight()

    def highlight(self, _e: Any = None) -> None:
        """Realça a sintaxe da região visível (viewport ± 80 linhas).

        Esta é a passada rápida, chamada a cada edição ou rolagem.
        Para realce do arquivo completo, veja :meth:`_highlight_full_chunked`.

        Otimizações aplicadas:
        - ``tag_remove`` chamado apenas para tags que existem no range (evita
          chamadas Tcl desnecessárias para tags vazias).
        - Loop de matches usa variáveis locais hoisted para evitar lookup de
          atributo ``self._w`` por iteração.

        Args:
            _e: Evento Tkinter ignorado (permite uso como callback direto).
        """
        if not self._enabled:
            return
        pat = LANG_RE.get(self._lang)
        if not pat:
            return
        try:
            top_idx = self._w.index("@0,0")
            bot_idx = self._w.index(f"@0,{self._w.winfo_height()}")
            top_ln = max(1, int(top_idx.split(".")[0]) - 80)
            bot_ln = int(bot_idx.split(".")[0]) + 80
        except (tk.TclError, ValueError):
            return

        s_idx = f"{top_ln}.0"
        e_idx = f"{bot_ln}.end"

        _tag_remove = self._w.tag_remove
        _keep = self._KEEP_TAGS
        _names = self._w.tag_names
        with contextlib.suppress(tk.TclError):
            active_in_range = {t for t in _names(s_idx) if t not in _keep}
            active_in_range |= {t for t in _names(e_idx) if t not in _keep}
        for tag in self._tags - _keep:
            if tag in active_in_range:
                with contextlib.suppress(tk.TclError):
                    _tag_remove(tag, s_idx, e_idx)

        try:
            content = self._w.get(s_idx, e_idx)
        except tk.TclError:
            return

        _tag_add = self._w.tag_add
        _sub = re.sub
        mapa = _build_offset_map(content, top_ln)
        for m in pat.finditer(content):
            tag = m.lastgroup
            if not tag:
                continue
            base_tag = _sub(r"_\d+$", "", tag)
            if base_tag in _keep:
                continue
            try:
                la, ca = mapa[m.start()]
                lb, cb = mapa[m.end()]
                _tag_add(base_tag, f"{la}.{ca}", f"{lb}.{cb}")
            except (tk.TclError, IndexError):
                pass

        self._apply_custom_kw(content, s_idx, e_idx, mapa)

    def hl_current_line(self) -> None:
        """Realça a linha do cursor com a cor de destaque de linha atual."""
        try:
            self._w.tag_remove("current_line", "1.0", "end")
            ln = self._w.index(tk.INSERT).split(".")[0]
            self._w.tag_add("current_line", f"{ln}.0", f"{ln}.end+1c")
        except tk.TclError:
            pass

    def match_brackets(self) -> None:
        """Encontra e realça o par de colchetes/parênteses/chaves do cursor.

        Se o cursor estiver sobre um caractere de abertura ou fechamento,
        localiza o caractere correspondente e aplica a tag ``bracket_match``
        em ambos.

        OTIMIZAÇÃO: em vez de carregar ``"1.0","end"`` (arquivo inteiro),
        usa uma janela de ±2000 linhas ao redor do cursor.  Para arquivos
        com brackets aninhados além desse raio, o match simplesmente não
        encontra o par - comportamento aceitável sem travar a UI.
        """
        self._w.tag_remove("bracket_match", "1.0", "end")
        opens = {"(": ")", "[": "]", "{": "}"}
        closes = {v: k for k, v in opens.items()}
        try:
            pos = self._w.index(tk.INSERT)
            char = self._w.get(pos)
        except tk.TclError:
            return
        if char not in opens and char not in closes:
            try:
                pos2 = self._w.index(f"{pos}-1c")
                char = self._w.get(pos2)
                if char in opens or char in closes:
                    pos = pos2
                else:
                    return
            except tk.TclError:
                return

        target = opens.get(char) or closes.get(char)
        direction = 1 if char in opens else -1

        try:
            cur_ln = int(pos.split(".")[0])
        except (ValueError, IndexError):
            return
        win_start = max(1, cur_ln - 2000)
        win_end = cur_ln + 2000

        try:
            win_s = f"{win_start}.0"
            win_e = f"{win_end}.end"
            content = self._w.get(win_s, win_e)

            offset = len(self._w.get(win_s, pos))
        except tk.TclError:
            return

        depth = 0
        rng = range(offset, len(content)) if direction == 1 else range(
            offset, -1, -1)
        for i in rng:
            c = content[i]
            if c == char:
                depth += 1
            elif c == target:
                depth -= 1
                if depth == 0:
                    try:
                        match_abs = f"{win_start}.0 + {i}c"
                        idx = self._w.index(match_abs)
                        self._w.tag_add("bracket_match", pos, f"{pos}+1c")
                        self._w.tag_add("bracket_match", idx, f"{idx}+1c")
                    except tk.TclError:
                        pass
                    return

    def clear_lint(self) -> None:
        """Remove todas as tags de diagnóstico de lint do widget."""
        for tag in (
            "lint_error",
            "lint_warn",
            "lint_info",
            "lint_hint",
            "lint_err_line",
            "lint_warn_line",
        ):
            with contextlib.suppress(tk.TclError):
                self._w.tag_remove(tag, "1.0", "end")

    def apply_lint(self, diags: list[Diag]) -> None:
        """Aplica marcações visuais de diagnóstico no widget.

        Cada diagnóstico recebe uma sublinha colorida na coluna afetada e,
        para erros e avisos, também um fundo na linha inteira.

        Args:
            diags: Lista de objetos :class:`Diag` com posição e severidade.
        """
        self.clear_lint()
        SEV_INLINE = {1: "lint_error", 2: "lint_warn",
                      3: "lint_info", 4: "lint_hint"}
        SEV_LINE = {1: "lint_err_line", 2: "lint_warn_line"}
        for d in diags:
            try:
                s = f"{d.line}.{d.col}"
                e = f"{d.line}.{d.end_col}" if d.end_col > d.col else f"{d.line}.end"
                tg = SEV_INLINE.get(d.severity, "lint_warn")
                lt = SEV_LINE.get(d.severity)
                self._w.tag_add(tg, s, e)
                if lt:
                    self._w.tag_add(lt, f"{d.line}.0", f"{d.line}.end")
            except tk.TclError:
                pass

    def apply_kdocs(self, annotations: list[KDocsNotes]) -> None:
        """Aplica realce de anotações KDocs no widget.

        Remove as anotações anteriores antes de aplicar as novas.

        Args:
            annotations: Lista de :class:`KDocsNotes` com linha e tipo.
        """
        for kind in KDocsParser.KIND_ICONS:
            with contextlib.suppress(tk.TclError):
                self._w.tag_remove(f"kdocs_{kind}", "1.0", "end")
        for ann in annotations:
            try:
                tag = f"kdocs_{ann.kind}"
                self._w.tag_add(tag, f"{ann.line}.0", f"{ann.line}.end")
            except tk.TclError:
                pass

    def render_whitespace(self, mode: str = "none") -> None:
        """Configura a renderização visual de espaços e tabulações.

        Args:
            mode: ``"none"`` desativa; ``"boundary"`` mostra apenas nas bordas;
        """
        try:
            self._w.tag_remove("ws_space", "1.0", "end")
            self._w.tag_remove("ws_tab", "1.0", "end")
        except tk.TclError:
            pass
        if mode == "none":
            return
        t = self._tmgr.theme
        fs = self._font_size()
        ff = self._font_family()
        self._w.tag_configure(
            "ws_space", foreground=t.whitespace, font=(ff, fs))
        self._w.tag_configure("ws_tab", foreground=t.whitespace, font=(ff, fs))

    def clear_search(self) -> None:
        """Remove todas as tags de resultado de busca do widget."""
        try:
            self._w.tag_remove("search_match", "1.0", "end")
            self._w.tag_remove("search_cur", "1.0", "end")
        except tk.TclError:
            pass

    def apply_search(
        self, matches: list[tuple[str, str]], current_idx: int = 0
    ) -> None:
        """Aplica realce nos resultados de busca.

        O resultado na posição *current_idx* recebe a tag ``search_cur``
        (destaque mais intenso); os demais recebem ``search_match``.

        Args:
            matches:     Lista de tuplas ``(start_idx, end_idx)`` em formato
                         de índice Tkinter (ex.: ``"3.5"``).
            current_idx: Índice do resultado atualmente selecionado.
        """
        self.clear_search()
        for i, (s, e) in enumerate(matches):
            tag = "search_cur" if i == current_idx else "search_match"
            with contextlib.suppress(tk.TclError):
                self._w.tag_add(tag, s, e)
