"""Codis - CodeFolding.

DETECÇÃO
--------
- Python/indent: bloco composto (try+except+finally, if+elif+else) é uma
  ÚNICA região. A seta fica na primeira linha (try, if) e recolhe tudo.
- A detecção processa linha a linha com um stack tipado. A ordem é:
  1. Verifica se a linha FECHA algum bloco aberto no stack.
  2. Verifica se a linha ABRE um novo bloco.
  Assim a seta fica sempre na linha de abertura do bloco.

FOLD/UNFOLD
-----------
- Salva de start_line.end -> end_line.end (inclui o \\n inicial), garantindo
  restauração exata sem acumular conteúdo na linha de abertura.

SUPORTE A LINGUAGENS
--------------------
  indent : Python, YAML, CoffeeScript, Haskell, Elixir, Elm
  brace  : JS, TS, Java, C, C++, C#, Rust, Go, PHP, Kotlin, Swift,
            Scala, Groovy, CSS, SCSS, JSON
  doend  : Ruby, Lua, Elixir (do/end), Crystal
  markup : HTML, XML, Markdown
  sql    : SQL, PL/pgSQL
  fallback: tenta brace depois indent
"""

from __future__ import annotations

import contextlib
import logging
import re
import tkinter as tk
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from src.constants import FONT_MONO, FONT_SIZE

if TYPE_CHECKING:
    from src.theme.base import Theme

logger = logging.getLogger("Codis")

_FOLD_PLACEHOLDER = " ... "
_TAG_FOLD = "codis_fold_placeholder"

INDENT_LANGS = frozenset(
    {
        "python",
        "yaml",
        "coffee",
        "coffeescript",
        "haskell",
        "elm",
        "pug",
        "haml",
        "stylus",
    }
)
BRACE_LANGS = frozenset(
    {
        "javascript",
        "typescript",
        "java",
        "c",
        "cpp",
        "csharp",
        "rust",
        "go",
        "kotlin",
        "swift",
        "scala",
        "groovy",
        "php",
        "dart",
        "fsharp",
        "css",
        "scss",
        "less",
        "json",
        "jsonc",
        "objectivec",
    }
)
DOEND_LANGS = frozenset(
    {
        "ruby",
        "lua",
        "elixir",
        "erlang",
        "crystal",
    }
)
MARKUP_LANGS = frozenset(
    {
        "html",
        "xml",
        "svg",
        "markdown",
    }
)
SQL_LANGS = frozenset(
    {
        "sql",
        "plpgsql",
        "tsql",
        "mysql",
    }
)

_PY_OPEN = re.compile(
    r"^(\s*)(async\s+def|def|class|if|for|while|with|try|match)\b[^#]*:\s*(?:#.*)?$"
)
_PY_CONT = re.compile(r"^(\s*)(elif|else|except|finally)\b")

_PY_DOCSTR = re.compile(r'"""')

_REGION_OPEN = re.compile(r"^\s*(?://|#)\s*region\b", re.IGNORECASE)
_REGION_CLOSE = re.compile(r"^\s*(?://|#)\s*endregion\b", re.IGNORECASE)

_BRACE_OPEN = re.compile(r"\{\s*(?://[^\n]*)?\s*$")
_BRACE_CLOSE = re.compile(r"^\s*\}")

_BLOCK_CMT_OPEN = re.compile(r"/\*")
_BLOCK_CMT_CLOSE = re.compile(r"\*/")

_DOEND_OPEN = re.compile(
    r"^\s*(def|class|module|if|unless|while|until|for|case|begin|do|function|then)\b",
    re.IGNORECASE,
)
_DOEND_CLOSE = re.compile(r"^\s*end\b", re.IGNORECASE)

_HTML_BLOCK = (
    "div|section|article|header|footer|nav|main|aside|form|ul|ol|"
    "table|template|component|script|style|html|body|head|svg|figure|"
    "details|summary|dialog|menu|fieldset|select"
)
_HTML_OPEN = re.compile(rf"<({_HTML_BLOCK})(\s[^>]*)?>", re.IGNORECASE)
_HTML_CLOSE = re.compile(rf"</({_HTML_BLOCK})>", re.IGNORECASE)
_HTML_SELF = re.compile(r"/>\s*$")

_MD_HEADING = re.compile(r"^(#{1,6})\s")

_SQL_OPEN = re.compile(r"\bBEGIN\b", re.IGNORECASE)
_SQL_CLOSE = re.compile(r"\bEND\b", re.IGNORECASE)


@dataclass
class FoldRegion:
    start_line: int
    end_line: int
    folded: bool = False
    hidden_text: str = ""
    kind: str = "block"
    folded_children: list = field(default_factory=list, repr=False)
    _tag: str = field(default="", repr=False)

    @property
    def tag(self) -> str:
        """Nome único da tag Tkinter para esta região, derivado do intervalo de linhas."""
        if not self._tag:
            self._tag = f"fold_{self.start_line}_{self.end_line}"
        return self._tag


def _detect_indent(lines: list[str]) -> list[FoldRegion]:
    """Detecta regiões de fold em linguagens baseadas em indentação (Python, YAML, ...).

    Palavras-chave de abertura (``def``, ``class``, ``if``, etc.) empilham um bloco.
    Palavras de continuação (``elif``, ``else``, ``except``, ``finally``) estendem o
    bloco corrente sem fechá-lo. Uma linha não-vazia com indentação ≤ à de abertura
    fecha o bloco. Docstrings com aspas triplas são detectadas como região separada
    do tipo ``"docstring"``.

    Args:
        lines: Linhas do arquivo-fonte (sem ``\\n`` terminal necessário).

    Returns:
        Lista não filtrada de objetos ``FoldRegion``; pode conter spans de linha única.
    """
    regions: list[FoldRegion] = []
    n = len(lines)

    stack: list[list] = []

    i = 0
    while i < n:
        raw = lines[i]
        stripped = raw.lstrip()
        indent = len(raw) - len(stripped)
        lnum = i + 1

        if not stripped:
            i += 1
            continue

        if '"""' in raw:
            count = raw.count('"""')
            if count == 1:
                j = i + 1
                while j < n:
                    if '"""' in lines[j]:
                        if j > i + 1:
                            regions.append(FoldRegion(
                                lnum, j + 1, kind="docstring"))
                        i = j
                        break
                    j += 1

        if _REGION_OPEN.match(raw):
            j = i + 1
            while j < n:
                if _REGION_CLOSE.match(lines[j]):
                    regions.append(FoldRegion(lnum, j + 1, kind="region"))
                    i = j
                    break
                j += 1

        if _PY_CONT.match(raw):
            for entry in reversed(stack):
                if entry[1] == indent:
                    entry[3] = lnum
                    break
            i += 1
            continue

        new_stack = []
        for entry in stack:
            s_line, s_indent, s_kind, cur_end = entry
            if s_kind == "block" and lnum > s_line and indent <= s_indent:
                if cur_end >= lnum:
                    new_stack.append(entry)
                    continue
                end = max(cur_end, s_line + 1)
                regions.append(FoldRegion(s_line, end, kind=s_kind))
            else:
                new_stack.append(entry)
        stack = new_stack

        if _PY_OPEN.match(raw):
            stack.append([lnum, indent, "block", lnum])

        i += 1

    for entry in stack:
        s_line, _, s_kind, cur_end = entry
        end = max(cur_end, n) if n > s_line else cur_end
        if end > s_line:
            regions.append(FoldRegion(s_line, end, kind=s_kind))

    return regions


def _detect_brace(lines: list[str]) -> list[FoldRegion]:
    """Detecta regiões de fold em linguagens com blocos ``{ }`` (JS, TS, Java, C, ...).

    Rastreia pares ``{`` / ``}`` em uma pilha; cada par casado vira uma região
    ``"brace"``. Comentários de bloco (``/* ... */``) imediatamente seguidos de uma
    declaração com ``{`` são ignorados para evitar colocar a seta no comentário.
    Marcadores ``# region`` / ``# endregion`` são detectados independentemente.

    Args:
        lines: Linhas do arquivo-fonte.

    Returns:
        Lista não filtrada de objetos ``FoldRegion``.
    """
    regions: list[FoldRegion] = []
    stack: list[int] = []
    n = len(lines)
    i = 0
    while i < n:
        raw = lines[i]
        lnum = i + 1

        if _REGION_OPEN.match(raw):
            j = i + 1
            while j < n:
                if _REGION_CLOSE.match(lines[j]):
                    regions.append(FoldRegion(lnum, j + 1, kind="region"))
                    break
                j += 1

        if _BLOCK_CMT_OPEN.search(raw) and not _BLOCK_CMT_CLOSE.search(raw):
            j = i + 1
            while j < n:
                if _BLOCK_CMT_CLOSE.search(lines[j]):
                    next_code = j + 1
                    if next_code < n and _BRACE_OPEN.search(lines[next_code]):
                        pass
                    elif j > i + 1:
                        regions.append(FoldRegion(lnum, j + 1, kind="comment"))
                    break
                j += 1

        if _BRACE_OPEN.search(raw):
            stack.append(lnum)

        if _BRACE_CLOSE.match(raw) and stack:
            start = stack.pop()
            if lnum > start:
                regions.append(FoldRegion(start, lnum, kind="brace"))

        i += 1
    return regions


def _detect_doend(lines: list[str]) -> list[FoldRegion]:
    """Detecta regiões de fold em linguagens do/end (Ruby, Lua, Elixir, Crystal).

    Palavras-chave de abertura empilham o número da linha; ``end`` desempilha a
    entrada mais recente e cria uma região ``"block"``. Marcadores
    ``# region`` / ``# endregion`` também são tratados.

    Args:
        lines: Linhas do arquivo-fonte.

    Returns:
        Lista não filtrada de objetos ``FoldRegion``.
    """
    regions: list[FoldRegion] = []
    stack: list[int] = []
    for i, raw in enumerate(lines):
        lnum = i + 1
        if not raw.strip():
            continue
        if _REGION_OPEN.match(raw):
            j = i + 1
            while j < len(lines):
                if _REGION_CLOSE.match(lines[j]):
                    regions.append(FoldRegion(lnum, j + 1, kind="region"))
                    break
                j += 1
        if _DOEND_OPEN.match(raw):
            stack.append(lnum)
        if _DOEND_CLOSE.match(raw) and stack:
            start = stack.pop()
            if lnum > start + 1:
                regions.append(FoldRegion(start, lnum, kind="block"))
    return regions


def _detect_markup(lines: list[str], lang: str) -> list[FoldRegion]:
    """Detecta regiões de fold em linguagens de marcação (HTML, XML, Markdown).

    Para Markdown, os níveis de cabeçalho conduzem uma pilha: um cabeçalho fecha
    todos os cabeçalhos abertos de nível igual ou mais profundo e abre uma nova
    região ``"section"``. Para HTML/XML, um conjunto de tags de bloco é casado por
    pares abrir/fechar; tags auto-fechantes (sufixo ``/>``) são ignoradas.

    Args:
        lines: Linhas do arquivo-fonte.
        lang:  Identificador de linguagem - ``"markdown"`` usa a estratégia de
               cabeçalhos; qualquer outro valor usa casamento de tags HTML/XML.

    Returns:
        Lista não filtrada de objetos ``FoldRegion``.
    """
    regions: list[FoldRegion] = []

    if lang == "markdown":
        heading_stack: list[tuple[int, int]] = []
        for i, raw in enumerate(lines):
            m = _MD_HEADING.match(raw)
            if not m:
                continue
            level = len(m.group(1))
            lnum = i + 1
            new_hs = []
            for h_line, h_level in heading_stack:
                if h_level >= level:
                    if lnum > h_line + 1:
                        regions.append(FoldRegion(
                            h_line, lnum - 1, kind="section"))
                else:
                    new_hs.append((h_line, h_level))
            heading_stack = new_hs
            heading_stack.append((lnum, level))
        total = len(lines)
        for h_line, _ in heading_stack:
            if total > h_line:
                regions.append(FoldRegion(h_line, total, kind="section"))
        return regions

    stack: list[tuple[int, str]] = []
    for i, raw in enumerate(lines):
        lnum = i + 1
        if _HTML_SELF.search(raw):
            continue
        m_open = _HTML_OPEN.search(raw)
        m_close = _HTML_CLOSE.search(raw)
        if m_open and not m_close:
            stack.append((lnum, m_open.group(1).lower()))
        if m_close and stack:
            tag = m_close.group(1).lower()
            for j in range(len(stack) - 1, -1, -1):
                if stack[j][1] == tag:
                    start = stack.pop(j)[0]
                    if lnum > start + 1:
                        regions.append(FoldRegion(start, lnum, kind="html"))
                    break
    return regions


def _detect_sql(lines: list[str]) -> list[FoldRegion]:
    """Detecta regiões de fold em arquivos SQL/PL-SQL usando pares BEGIN/END.

    Args:
        lines: Linhas do arquivo-fonte.

    Returns:
        Lista não filtrada de objetos ``FoldRegion``.
    """
    regions: list[FoldRegion] = []
    stack: list[int] = []
    for i, raw in enumerate(lines):
        lnum = i + 1
        if _SQL_OPEN.search(raw):
            stack.append(lnum)
        if _SQL_CLOSE.search(raw) and stack:
            start = stack.pop()
            if lnum > start + 1:
                regions.append(FoldRegion(start, lnum, kind="block"))
    return regions


def detect_regions(lines: list[str], lang: str) -> list[FoldRegion]:
    """Escolhe a estratégia correta para ``lang`` e retorna regiões válidas.

    Despacha para ``_detect_indent``, ``_detect_brace``, ``_detect_doend``,
    ``_detect_markup`` ou ``_detect_sql`` conforme a família da linguagem.
    Linguagens desconhecidas recorrem ao detector brace e depois ao indent.
    Spans de linha única (``start_line == end_line``) são descartados.

    Args:
        lines: Linhas do arquivo-fonte.
        lang:  Identificador de linguagem em minúsculas (ex.: ``"python"``, ``"typescript"``).

    Returns:
        Lista de objetos ``FoldRegion`` ordenada por ``start_line``, cada um
        cobrindo ao menos duas linhas (``end_line - start_line >= 1``).
    """
    if lang in INDENT_LANGS:
        raw = _detect_indent(lines)
    elif lang in BRACE_LANGS:
        raw = _detect_brace(lines)
    elif lang in DOEND_LANGS:
        raw = _detect_doend(lines)
    elif lang in MARKUP_LANGS:
        raw = _detect_markup(lines, lang)
    elif lang in SQL_LANGS:
        raw = _detect_sql(lines)
    else:
        raw = _detect_brace(lines)
        if not raw:
            raw = _detect_indent(lines)

    result = [r for r in raw if r.end_line - r.start_line >= 1]
    result.sort(key=lambda r: r.start_line)
    return result


class CodeFolding:
    """Motor de code folding para um widget ``tk.Text``.

    Detecta regiões foldáveis para a linguagem ativa, renderiza placeholders
    de fold inline e notifica o gutter das posições das linhas dobradas.
    Folds aninhados (filhos dentro de um fold pai) são preservados entre
    ciclos de fold/unfold via ``FoldRegion.folded_children``.
    """

    def __init__(
        self,
        txt: tk.Text,
        theme: Theme,
        on_gutter_update: callable | None = None,
        lang: str = "text",
    ) -> None:
        """Inicializa o motor de folding e o vincula ao widget ``txt``.

        Args:
            txt:              Widget ``tk.Text`` a ser gerenciado.
            theme:            Tema ativo, usado para as cores das tags.
            on_gutter_update: Callback que recebe ``{linha: dobrado}`` sempre que
                              o mapa de fold muda.
            lang:             Identificador de linguagem inicial para detecção de regiões.
        """
        self._txt = txt
        self._theme = theme
        self._on_gutter_update = on_gutter_update
        self._lang = lang
        self._regions: list[FoldRegion] = []
        self._after_id: str | None = None
        self._configure_tags()
        self._bind_events()

    def _configure_tags(self) -> None:
        """Configura o estilo da tag ``codis_fold_placeholder`` com as cores do tema ativo."""
        t = self._theme
        with contextlib.suppress(tk.TclError):
            self._txt.tag_configure(
                _TAG_FOLD,
                background=t.sel_bg,
                foreground=t.fg_muted,
                font=(FONT_MONO, FONT_SIZE - 1),
                relief="flat",
            )

    def _bind_events(self) -> None:
        """Vincula ``<<Modified>>`` e ``<FocusIn>`` ao scan com debounce."""
        self._txt.bind("<<Modified>>", self._schedule_scan, add=True)
        self._txt.bind("<FocusIn>", self._schedule_scan, add=True)

    def set_lang(self, lang: str) -> None:
        """Define a linguagem ativa e dispara um novo scan de regiões.

        Args:
            lang: Identificador de linguagem em minúsculas (ex.: ``"python"``, ``"go"``).
        """
        if lang != self._lang:
            self._lang = lang
            self._schedule_scan()

    def _schedule_scan(self, _e=None, delay: int = 400) -> None:
        """Aplica debounce na chamada a ``_scan_regions``.

        Args:
            delay: Milissegundos de espera antes do scan. Use ``400`` para eventos
                   de digitação e ``80`` após fold/unfold para que os ícones do
                   gutter atualizem antes do próximo pressionamento de tecla.
        """
        if self._after_id:
            with contextlib.suppress(tk.TclError):
                self._txt.after_cancel(self._after_id)
        self._after_id = self._txt.after(delay, self._scan_regions)

    def _scan_regions(self) -> None:
        """Detecta regiões de fold no conteúdo atual do widget e mescla o estado dobrado.

        Executa após o delay do debounce. Para cada região detectada, verifica se
        uma região previamente dobrada tem sua tag ainda presente na mesma linha do
        widget e transfere os campos ``folded``, ``hidden_text``, ``_tag`` e
        ``folded_children``. Regiões cujo placeholder ainda existe no widget mas
        cuja linha de início não coincide com nenhuma região detectada são mantidas
        como ``surviving_folded`` para que seus ícones no gutter permaneçam visíveis.
        """
        self._after_id = None
        if not self._txt.winfo_exists():
            return
        try:
            content = self._txt.get("1.0", "end-1c")
        except tk.TclError:
            return

        lines = content.splitlines()
        new_regions = detect_regions(lines, self._lang)

        old_by_tag: dict[str, FoldRegion] = {
            r.tag: r for r in self._regions if r.folded and r.tag
        }

        tag_to_current_line: dict[str, int] = {}
        for tag, _r in old_by_tag.items():
            with contextlib.suppress(tk.TclError):
                ranges = self._txt.tag_ranges(tag)
                if ranges:
                    idx = str(ranges[0])
                    tag_to_current_line[tag] = int(idx.split(".")[0])

        line_to_old: dict[int, FoldRegion] = {
            ln: old_by_tag[tag] for tag, ln in tag_to_current_line.items()
        }

        for reg in new_regions:
            old_r = line_to_old.get(reg.start_line)
            if old_r is not None:
                reg.folded = True
                reg.hidden_text = old_r.hidden_text
                reg._tag = old_r._tag
                reg.folded_children = old_r.folded_children

        self._regions = new_regions

        surviving_folded = []
        matched_tags = {r._tag for r in new_regions if r.folded}
        for old_r in old_by_tag.values():
            if old_r.tag in matched_tags:
                continue
            with contextlib.suppress(tk.TclError):
                ranges = self._txt.tag_ranges(old_r.tag)
                if ranges:
                    current_line = int(str(ranges[0]).split(".")[0])
                    old_r.start_line = current_line
                    surviving_folded.append(old_r)
        self._regions = new_regions + surviving_folded

        self._notify_gutter()

    def toggle_fold(self, line_number: int) -> bool | None:
        """Alterna o estado de fold da região que começa em ``line_number``.

        Args:
            line_number: Número de linha base 1 da linha de abertura da região.

        Returns:
            ``True`` se a região foi dobrada agora, ``False`` se desdobrada,
            ``None`` se nenhuma região começa nessa linha.
        """
        reg = self._find_region_at(line_number)
        if reg is None:
            return None
        if reg.folded:
            self.unfold(reg)
            return False
        self.fold(reg)
        return True

    def fold(self, region: FoldRegion) -> None:
        """Recolhe a região, ocultando o texto de start.end até end.end.

        Antes de recolher, captura todos os descendentes atualmente dobrados para
        que seu estado sobreviva ao scan posterior ao fold (que não consegue ver as
        tags que sumiram com o texto). Os filhos capturados são armazenados em
        ``region.folded_children`` e restaurados em ``unfold``.
        """
        if region.folded:
            return
        try:
            region.folded_children = [
                r
                for r in self._regions
                if r.folded
                and r is not region
                and region.start_line < r.start_line <= region.end_line
            ]

            start = f"{region.start_line}.end"
            end = f"{region.end_line}.end"
            region.hidden_text = self._txt.get(start, end)
            self._txt.delete(start, end)
            self._txt.insert(start, _FOLD_PLACEHOLDER, (region.tag, _TAG_FOLD))
            region.folded = True
            self._schedule_scan(delay=80)
        except tk.TclError as e:
            logger.debug("fold error: %s", e)

    def unfold(self, region: FoldRegion) -> None:
        """Expande uma região recolhida e restaura todos os descendentes dobrados.

        Quando o pai foi dobrado, seu texto oculto já continha os placeholders dos
        filhos como texto puro (as tags Tkinter foram removidas). ``fold()`` salvou
        referências a esses filhos em ``region.folded_children`` antes das tags
        desaparecerem do widget. Aqui reinserimos o texto e chamamos
        ``_restore_child_folds`` para recriar as tags dos filhos, de modo que o
        gutter mostre ▸ e o unfold dos próprios filhos continue funcionando.
        """
        if not region.folded:
            return
        try:
            ranges = self._txt.tag_ranges(region.tag)
            if not ranges:
                return

            insert_pos = str(ranges[0])
            insert_line = int(insert_pos.split(".")[0])

            folded_children: list[FoldRegion] = list(region.folded_children)

            self._txt.delete(ranges[0], ranges[1])
            self._txt.insert(insert_pos, region.hidden_text)
            region.folded = False
            region.hidden_text = ""
            region.folded_children = []

            if folded_children:
                self._restore_child_folds(folded_children, insert_line)
                existing_tags = {r._tag for r in self._regions}
                for child in folded_children:
                    if child._tag not in existing_tags:
                        self._regions.append(child)

            self._notify_gutter()

            self._schedule_scan(delay=80)
        except tk.TclError as e:
            logger.debug("unfold error: %s", e)

    def _restore_child_folds(
        self, children: list[FoldRegion], parent_insert_line: int
    ) -> None:
        """Recria as tags Tkinter dos filhos dobrados após o unfold do pai.

        Quando o pai foi dobrado, os placeholders dos filhos foram salvos como
        texto puro dentro de ``hidden_text``. Ao reinserir esse texto, os
        placeholders voltam mas sem suas tags. Este método varre as linhas
        restauradas em busca de cada placeholder e recria a tag correspondente
        do filho, para que o gutter mostre ▸ e o ``unfold`` continue funcionando
        nos filhos.

        Args:
            children:           Descendentes dobrados capturados no momento do fold.
            parent_insert_line: Número de linha no widget onde o placeholder do pai
                                estava (e onde o texto restaurado começa).
        """
        ph_stripped = _FOLD_PLACEHOLDER.strip()

        if not children:
            return
        max_child_end = max(c.end_line for c in children)
        scan_end = (
            parent_insert_line
            + (max_child_end - min(c.start_line for c in children))
            + 5
        )

        remaining = sorted(children, key=lambda c: c.start_line)

        for child in remaining:
            try:
                estimated_line = child.start_line
                found_line: int | None = None

                search_range = list(
                    range(
                        max(1, estimated_line - 3),
                        estimated_line + 4,
                    )
                )
                search_range += list(
                    range(
                        max(1, parent_insert_line),
                        min(scan_end, parent_insert_line + 200),
                    )
                )

                seen: set[int] = set()
                for ln in search_range:
                    if ln in seen:
                        continue
                    seen.add(ln)
                    try:
                        lt = self._txt.get(f"{ln}.0", f"{ln}.end")
                        if ph_stripped in lt:
                            found_line = ln
                            break
                    except tk.TclError:
                        break

                if found_line is None:
                    continue

                line_content = self._txt.get(
                    f"{found_line}.0", f"{found_line}.end")
                col = line_content.find(ph_stripped)
                if col == -1:
                    col = 0

                start_idx = f"{found_line}.{col}"
                end_idx = f"{found_line}.{col + len(_FOLD_PLACEHOLDER)}"

                self._txt.tag_add(child.tag, start_idx, end_idx)
                self._txt.tag_add(_TAG_FOLD, start_idx, end_idx)
                self._txt.tag_config(child.tag, elide=False)
                self._txt.tag_config(
                    _TAG_FOLD, foreground=self._theme.fg_muted)

                child.start_line = found_line

            except (tk.TclError, ValueError) as e:
                logger.debug(
                    "_restore_child_folds error for child %s: %s", child, e)

    def fold_all(self) -> None:
        """Dobra todas as regiões foldáveis de nível superior do documento."""
        for r in [r for r in self._regions if not r.folded and self._is_top(r)]:
            self.fold(r)

    def unfold_all(self) -> None:
        """Desdobra todas as regiões dobradas do documento."""
        for r in list(self._regions):
            if r.folded:
                self.unfold(r)

    def _find_region_at(self, line: int) -> FoldRegion | None:
        """Retorna o ``FoldRegion`` cujo ``start_line`` é igual a ``line``, ou ``None``."""
        for r in self._regions:
            if r.start_line == line:
                return r
        return None

    def _is_top(self, region: FoldRegion) -> bool:
        """Retorna ``True`` se ``region`` está no nível de indentação zero (topo)."""
        try:
            content = self._txt.get("1.0", f"{region.start_line}.0")
            line = content.splitlines()[-1] if content.splitlines() else ""
            return not line.strip() or len(line) - len(line.lstrip()) == 0
        except tk.TclError:
            return True

    def _notify_gutter(self) -> None:
        """Dispara ``on_gutter_update`` com o mapa ``{linha: dobrado}`` atual."""
        if self._on_gutter_update:
            fold_map = {r.start_line: r.folded for r in self._regions}
            with contextlib.suppress(tk.TclError, AttributeError):
                self._on_gutter_update(fold_map)

    def apply_theme(self, theme: Theme) -> None:
        """Aplica as cores de ``theme`` à tag de placeholder de fold."""
        self._theme = theme
        self._configure_tags()

    def destroy(self) -> None:
        """Cancela qualquer scan pendente e libera as referências ao widget."""
        if self._after_id:
            with contextlib.suppress(tk.TclError):
                self._txt.after_cancel(self._after_id)
