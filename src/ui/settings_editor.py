"""Codis - SettingsEditor: editor de configurações em modo avançado (painel inline).

Exibe TODAS as configurações do Codis em um painel scrollável com busca em
tempo real, controles visuais (toggle, slider, combo, entry) e botão de
reset por item. Usado no modo "Advanced" acessível pela barra de atividades.

Este módulo também é a fonte de verdade do SETTINGS_SCHEMA - a lista
completa de todas as configurações disponíveis, seus tipos, valores padrão,
descrições e opções. O SettingsWindow consome este schema para popular
a janela flutuante de configurações categorizadas.

Extensível por plugins via ``PluginAPI.add_settings_section()``.
"""

from __future__ import annotations

import contextlib
import logging
import tkinter as tk
from collections.abc import Callable
from tkinter import ttk
from typing import Any

from src.config import ConfigManager
from src._shells import SHELL_OPTIONS, get_default_shell
from src.constants import (
    CONFIG_DIR,
    FONT_MONO,
    FONT_SIZE,
    FONT_UI,
    TAB_SIZE,
)
from src.i18n import I18n, _t
from src.theme.base import THEME_ORDER, Theme
from src.theme.base import _define_themes as _ensure_themes
from src.theme.manager import ThemeManager
from src.ui._perf_utils import (
    BatchPacker,
    debounce_canvas,
    debounce_fn,
    make_hover_highlight,
)
from src.ui.dropdown import DropDown

logger = logging.getLogger("Codis")

_BUILTIN_MONO_FONTS: list[str] = [
    FONT_MONO,
    "Courier New",
    "Fira Code",
    "JetBrains Mono",
    "Source Code Pro",
    "Cascadia Code",
    "Cascadia Mono",
    "Inconsolata",
    "Hack",
    "Victor Mono",
    "Iosevka",
    "Maple Mono",
    "Monaspace Neon",
]

_BUILTIN_UI_FONTS: list[str] = [
    FONT_UI,
    "Segoe UI",
    "Helvetica",
    "Arial",
    "Calibri",
    "Roboto",
    "Inter",
    "Ubuntu",
    "Cantarell",
    "Open Sans",
    "Noto Sans",
    "Source Sans Pro",
]

_MONO_HINTS = (
    "mono",
    "code",
    "courier",
    "consol",
    "fixed",
    "terminal",
    "hack",
    "iosevka",
    "inconsolata",
    "fira",
    "jetbrains",
    "cascadia",
    "source code",
    "nerd",
    "victor",
    "maple",
    "noto sans mono",
)


def _get_system_mono_fonts() -> list[str]:
    """Retorna fontes monoespaçadas disponíveis no sistema + built-ins.

    Combina fontes reconhecidas como monoespaçadas (via dicas de nome)
    com todas as fontes do sistema, garantindo que qualquer fonte instalada
    (incluindo Fira Code, Monaspace, etc.) apareça na lista.
    Fontes monoespaçadas reconhecidas aparecem no início da lista.
    """
    try:
        import tkinter.font as tkfont

        all_families = tkfont.families()
    except Exception:
        return list(_BUILTIN_MONO_FONTS)

    system_mono = sorted(
        {f for f in all_families if any(h in f.lower() for h in _MONO_HINTS)},
        key=str.casefold,
    )
    all_other = sorted(
        {f for f in all_families if not any(
            h in f.lower() for h in _MONO_HINTS)},
        key=str.casefold,
    )

    merged: list[str] = list(_BUILTIN_MONO_FONTS)
    for f in system_mono:
        if f not in merged:
            merged.append(f)
    for f in all_other:
        if f not in merged:
            merged.append(f)
    return merged


def _get_system_ui_fonts() -> list[str]:
    """Retorna todas as fontes do sistema para uso na interface."""
    try:
        import tkinter.font as tkfont

        all_families = tkfont.families()
    except Exception:
        return list(_BUILTIN_UI_FONTS)
    system_all = sorted(set(all_families), key=str.casefold)
    merged: list[str] = list(_BUILTIN_UI_FONTS)
    for f in system_all:
        if f not in merged:
            merged.append(f)
    return merged


def _get_language_options() -> list[str]:
    """Retorna lista dinâmica de códigos de locale disponíveis.

    Inclui automaticamente locales registrados por plugins via
    ``api.extend_i18n()``, sem necessidade de reiniciar o editor.

    Returns:
        list[str]: Lista de códigos de locale (ex: ['en', 'pt-BR']).
    """
    return [code for code, _ in I18n.get_instance().AVAILABLE_LOCALES]


def _get_language_labels() -> dict[str, str]:
    """
    Returns:
        dict[str, str]: Mapeamento código -> nome (ex: {'en': 'English'}).
    """
    return dict(I18n.get_instance().AVAILABLE_LOCALES)


class SettingsEditor(tk.Frame):
    """Editor de configurações em modo avançado - painel inline scrollável.

    Exibe o schema completo de configurações com busca em tempo real,
    controles visuais (toggle/slider/combo/entry) e reset individual.
    Salva imediatamente ao alterar cada item.

    Extensível por plugins: use ``PluginAPI.add_settings_section()`` para
    adicionar seções extras ao final do schema.

    Args:
        parent: Widget pai Tkinter.
        tmgr: Instância de ThemeManager.
        cfg: Instância de ConfigManager.
        on_change: Callback(key, value) acionado a cada alteração salva.
    """

    _EXTRA_SCHEMA: list = []

    @classmethod
    def _get_schema(cls) -> list:
        """Retorna o schema completo de configurações do Codis.

        Cada entrada é uma tupla de 8 elementos:
        ``(key, label, description, kind, default, options, type_hint, example)``

        onde ``kind`` é um dos: 'toggle', 'slider', 'combo', 'entry', 'sep'.

        Returns:
            list: Lista de tuplas de schema de configuração.
        """
        return [
            (
                "__sep_editor",
                "✏  " + _t("setting.cat_editor", "Editor"),
                "",
                "sep",
                None,
                None,
                None,
                None,
            ),
            (
                "font_family",
                _t("setting.font_family", "Família da Fonte"),
                _t(
                    "setting.desc.font_family",
                    "Fonte monoespaçada usada no editor de código.",
                ),
                "combo",
                FONT_MONO,
                _get_system_mono_fonts(),
                "string",
                f'"{FONT_MONO}"',
            ),
            (
                "font_size",
                _t("setting.font_size", "Tamanho da Fonte"),
                _t("setting.desc.font_size",
                   "Tamanho da fonte do editor em pontos."),
                "slider",
                FONT_SIZE,
                (8, 32),
                "integer (8–32)",
                "13",
            ),
            (
                "font_ligatures",
                _t("setting.font_ligatures", "Ligaturas de Fonte"),
                _t(
                    "setting.desc.font_ligatures",
                    "Ativa ligaturas tipográficas (ex: -> vira →, != vira ≠) em fontes que suportam como Fira Code, JetBrains Mono, Cascadia Code. Requer Tk 8.6+ com HarfBuzz - suporte varia por SO e fonte.",
                ),
                "toggle",
                False,
                None,
                "boolean",
                "false",
            ),
            (
                "ligature_mode",
                _t("setting.ligature_mode", "Modo de Ligatura"),
                _t(
                    "setting.desc.ligature_mode",
                    "'native' usa recursos OpenType da fonte (requer HarfBuzz). 'pseudo' substitui sequências comuns por caracteres Unicode equivalentes em todos os sistemas.",
                ),
                "combo",
                "pseudo",
                ["native", "pseudo", "both"],
                "native | pseudo | both",
                "pseudo",
            ),
            (
                "tab_size",
                _t("setting.tab_size", "Tamanho do Tab"),
                _t(
                    "setting.desc.tab_size",
                    "Número de espaços por nível de indentação.",
                ),
                "slider",
                TAB_SIZE,
                (2, 8),
                "integer (2–8)",
                "4",
            ),
            (
                "use_spaces",
                _t("setting.use_spaces", "Usar Espaços"),
                _t(
                    "setting.desc.use_spaces",
                    "Insere espaços ao pressionar Tab em vez de caractere de tabulação.",
                ),
                "toggle",
                False,
                None,
                "boolean",
                "false",
            ),
            (
                "word_wrap",
                _t("setting.word_wrap", "Quebra de Linha"),
                _t(
                    "setting.desc.word_wrap",
                    "Quebra linhas longas na borda da janela em vez de rolar horizontalmente.",
                ),
                "toggle",
                False,
                None,
                "boolean",
                "false",
            ),
            (
                "cursor_style",
                _t("setting.cursor_style", "Estilo do Cursor"),
                _t(
                    "setting.desc.cursor_style",
                    "Forma visual do cursor de inserção no editor.",
                ),
                "combo",
                "line",
                ["line", "block", "underline"],
                "line | block | underline",
                "line",
            ),
            (
                "cursor_blinking",
                _t("setting.cursor_blinking", "Piscada do Cursor"),
                _t(
                    "setting.desc.cursor_blinking",
                    "Comportamento de piscada do cursor de inserção.",
                ),
                "combo",
                "blink",
                ["blink", "solid", "smooth", "phase", "expand"],
                "blink | solid | smooth | phase | expand",
                "blink",
            ),
            (
                "smooth_scroll",
                _t("setting.smooth_scroll", "Rolagem Suave"),
                _t(
                    "setting.desc.smooth_scroll",
                    "Habilita animação de rolagem suave no editor.",
                ),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "scroll_speed",
                _t("setting.scroll_speed", "Sensibilidade da Rolagem"),
                _t(
                    "setting.desc.scroll_speed",
                    "Número de linhas roladas por cada passo do scroll do mouse. Valores maiores = rolagem mais rápida.",
                ),
                "slider",
                3,
                (1, 15),
                "integer (1–15)",
                "3",
            ),
            (
                "multicursor_lines",
                _t("setting.multicursor_lines", "Multicursor por Linhas"),
                _t(
                    "setting.desc.multicursor_lines",
                    "Alt+Clique no gutter marca linhas; teclas aplicam em todas simultaneamente. Esc limpa.",
                ),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "multi_cursor",
                _t("setting.multi_cursor", "Multi-Cursor"),
                _t(
                    "setting.desc.multi_cursor",
                    "Permite adicionar múltiplos cursores com Alt+Clique ou Ctrl+D.",
                ),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "render_whitespace",
                _t("setting.render_whitespace", "Renderizar Espaços em Branco"),
                _t(
                    "setting.desc.render_whitespace",
                    "Controla a visibilidade de caracteres de espaço em branco no editor.",
                ),
                "combo",
                "none",
                ["none", "boundary", "selection", "trailing", "all"],
                "none | boundary | selection | trailing | all",
                "none",
            ),
            (
                "indent_guides",
                _t("setting.indent_guides", "Guias de Indentação"),
                _t(
                    "setting.desc.indent_guides",
                    "Exibe linhas verticais indicando os níveis de indentação.",
                ),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "bracket_matching",
                _t("setting.bracket_match", "Correspondência de Colchetes"),
                _t(
                    "setting.desc.bracket_matching",
                    "Destaca o par de colchete/parêntese/chave ao posicionar o cursor.",
                ),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "highlight_current_line",
                _t("setting.highlight_current_line", "Destacar Linha Atual"),
                _t(
                    "setting.desc.highlight_current_line",
                    "Destaca com fundo sutil a linha onde o cursor está posicionado.",
                ),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "line_numbers",
                _t("setting.line_numbers", "Números de Linha"),
                _t(
                    "setting.desc.line_numbers",
                    "Exibe numeração de linhas no gutter (painel lateral esquerdo).",
                ),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "relative_numbers",
                _t("setting.relative_numbers", "Números Relativos"),
                _t(
                    "setting.desc.relative_numbers",
                    "Exibe numeração relativa ao cursor (estilo Vim) em vez de absoluta.",
                ),
                "toggle",
                False,
                None,
                "boolean",
                "false",
            ),
            (
                "breadcrumbs",
                _t("setting.breadcrumbs", "Breadcrumbs"),
                _t(
                    "setting.desc.breadcrumbs",
                    "Exibe a trilha de navegação (arquivo > símbolo) no topo do editor.",
                ),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "minimap",
                _t("setting.minimap", "Minimapa"),
                _t(
                    "setting.desc.minimap",
                    "Exibe o minimapa (visão geral do código) no lado direito do editor.",
                ),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "encoding",
                _t("setting.encoding", "Codificação Padrão"),
                _t(
                    "setting.desc.encoding",
                    "Codificação de caracteres padrão para arquivos novos e sem BOM.",
                ),
                "combo",
                "utf-8",
                ["utf-8", "utf-8-bom", "utf-16", "latin-1", "cp1252", "ascii"],
                "string",
                '"utf-8"',
            ),
            (
                "eol",
                _t("setting.eol", "Fim de Linha Padrão"),
                _t(
                    "setting.desc.eol",
                    "Sequência de fim de linha padrão para arquivos novos.",
                ),
                "combo",
                "lf",
                ["lf", "crlf", "cr"],
                "lf | crlf | cr",
                "lf",
            ),
            (
                "__sep_advanced_editor",
                _t("setting.cat_advanced_editor",
                   "Recursos Avançados do Editor"),
                "",
                "sep",
                None,
                None,
                None,
                None,
            ),
            (
                "sticky_scroll",
                _t("setting.sticky_scroll", "Rolagem Fixada"),
                _t(
                    "setting.desc.sticky_scroll",
                    "Exibe cabeçalhos do escopo atual fixos no topo do editor ao rolar.",
                ),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "code_folding",
                _t("setting.code_folding", "Dobramento de Código"),
                _t(
                    "setting.desc.code_folding",
                    "Permite recolher blocos de código (funções, classes, regiões).",
                ),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "inline_blame",
                _t("setting.inline_blame", "Blame Git Inline"),
                _t(
                    "setting.desc.inline_blame",
                    "Exibe autoria Git (autor, hash, tempo relativo) ao lado de cada linha.",
                ),
                "toggle",
                False,
                None,
                "boolean",
                "false",
            ),
            (
                "inline_blame_mode",
                _t("setting.inline_blame_mode", "Modo do Blame Inline"),
                _t(
                    "setting.desc.inline_blame_mode",
                    "'cursor' exibe blame só na linha atual. 'all' exibe em todas as linhas visíveis.",
                ),
                "combo",
                "cursor",
                ["cursor", "all"],
                "cursor | all",
                "cursor",
            ),
            (
                "color_preview",
                _t("setting.color_preview", "Prévia de Cor Inline"),
                _t(
                    "setting.desc.color_preview",
                    "Exibe swatches de cor ao lado de valores CSS/hex/rgb/hsl no código.",
                ),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "inlay_hints",
                _t("setting.inlay_hints", "Dicas Inline (Inlay Hints)"),
                _t(
                    "setting.desc.inlay_hints",
                    "Exibe dicas de tipo e nome de parâmetro inline no código.",
                ),
                "toggle",
                False,
                None,
                "boolean",
                "false",
            ),
            (
                "smart_tips",
                _t("setting.smart_tips", "Dicas Flutuantes Inteligentes"),
                _t(
                    "setting.desc.smart_tips",
                    "Exibe dicas contextuais sobre qualidade de código, tamanho de arquivo, segurança e boas práticas durante a edição.",
                ),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "smart_tips_interval",
                _t("setting.smart_tips_interval",
                   "Intervalo entre Dicas (segundos)"),
                _t(
                    "setting.desc.smart_tips_interval",
                    "Tempo mínimo entre duas dicas. Valores maiores fazem as dicas aparecerem com menos frequência.",
                ),
                "slider",
                30,
                (10, 300),
                "integer (10-300 s)",
                "30",
            ),
            (
                "smart_tips_large_file_lines",
                _t(
                    "setting.smart_tips_large_file_lines",
                    "Dica: Arquivo Grande (linhas)",
                ),
                _t(
                    "setting.desc.smart_tips_large_file_lines",
                    "Número de linhas a partir do qual o arquivo é considerado 'grande' e exibe a dica de refatoração. Aumente para silenciar em arquivos grandes.",
                ),
                "slider",
                1000,
                (100, 10000),
                "integer (100–10000)",
                "1000",
            ),
            (
                "smart_tips_huge_file_lines",
                _t(
                    "setting.smart_tips_huge_file_lines",
                    "Dica: Arquivo Enorme (linhas)",
                ),
                _t(
                    "setting.desc.smart_tips_huge_file_lines",
                    "Número de linhas a partir do qual o arquivo é considerado 'enorme' e exibe alerta de performance. Deve ser maior que o limite de arquivo grande.",
                ),
                "slider",
                2000,
                (200, 50000),
                "integer (200–50000)",
                "2000",
            ),
            (
                "smart_tips_big_function_lines",
                _t(
                    "setting.smart_tips_big_function_lines",
                    "Dica: Função Grande (linhas)",
                ),
                _t(
                    "setting.desc.smart_tips_big_function_lines",
                    "Número de linhas a partir do qual uma função Python é considerada 'grande'. Valores maiores reduzem a frequência desta dica.",
                ),
                "slider",
                60,
                (20, 500),
                "integer (20–500)",
                "60",
            ),
            (
                "smart_tips_long_line_chars",
                _t(
                    "setting.smart_tips_long_line_chars",
                    "Dica: Linha Longa (caracteres)",
                ),
                _t(
                    "setting.desc.smart_tips_long_line_chars",
                    "Largura máxima de linha antes de disparar a dica de 'linhas longas'. O padrão PEP8 é 79; muitos projetos usam 100 ou 120.",
                ),
                "slider",
                100,
                (60, 300),
                "integer (60–300)",
                "100",
            ),
            (
                "smart_tips_deep_nesting",
                _t(
                    "setting.smart_tips_deep_nesting",
                    "Dica: Aninhamento Profundo (níveis)",
                ),
                _t(
                    "setting.desc.smart_tips_deep_nesting",
                    "Nível de indentação (blocos aninhados) a partir do qual a dica de complexidade é disparada. 4 espaços = 1 nível.",
                ),
                "slider",
                5,
                (3, 15),
                "integer (3–15)",
                "5",
            ),
            (
                "smart_tips_todo_threshold",
                _t("setting.smart_tips_todo_threshold",
                   "Dica: Quantidade de TODOs"),
                _t(
                    "setting.desc.smart_tips_todo_threshold",
                    "Número mínimo de comentários TODO/FIXME/HACK para disparar a dica. Aumente para projetos com muitos TODOs intencionais.",
                ),
                "slider",
                5,
                (2, 50),
                "integer (2–50)",
                "5",
            ),
            (
                "smart_tips_generic_var_threshold",
                _t(
                    "setting.smart_tips_generic_var_threshold",
                    "Dica: Variáveis Genéricas (quantidade)",
                ),
                _t(
                    "setting.desc.smart_tips_generic_var_threshold",
                    "Número mínimo de variáveis com nomes genéricos (x, tmp, data...) para disparar a dica de nomenclatura.",
                ),
                "slider",
                4,
                (2, 20),
                "integer (2–20)",
                "4",
            ),
            (
                "__sep_autocomplete",
                "💬  " + _t("setting.cat_autocomplete", "Autocomplete & Hover"),
                "",
                "sep",
                None,
                None,
                None,
                None,
            ),
            (
                "autocomplete",
                _t("setting.autocomplete", "Autocomplete"),
                _t(
                    "setting.desc.autocomplete",
                    "Habilita sugestões de autocomplete ao digitar.",
                ),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "autocomplete_delay",
                _t("setting.autocomplete_delay", "Atraso do Autocomplete (ms)"),
                _t(
                    "setting.desc.autocomplete_delay",
                    "Tempo de espera após digitar antes de exibir sugestões.",
                ),
                "slider",
                200,
                (50, 1000),
                "integer (50–1000 ms)",
                "200",
            ),
            (
                "snippets",
                _t("setting.snippets", "Snippets"),
                _t(
                    "setting.desc.snippets",
                    "Habilita expansão de snippets de código ao pressionar Tab.",
                ),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "parameter_hints",
                _t("setting.parameter_hints", "Dicas de Parâmetro"),
                _t(
                    "setting.desc.parameter_hints",
                    "Exibe dicas de parâmetros de função ao digitar chamadas.",
                ),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "hover_docs",
                _t("setting.hover_docs", "Documentação ao Passar o Mouse"),
                _t(
                    "setting.desc.hover_docs",
                    "Exibe documentação KDocs/JSDoc/LSP ao passar o mouse sobre um símbolo.",
                ),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "__sep_appearance",
                _t("setting.cat_appearance", "Aparência"),
                "",
                "sep",
                None,
                None,
                None,
                None,
            ),
            (
                "theme",
                _t("setting.theme", "Tema"),
                _t(
                    "setting.desc.theme",
                    "Tema visual do editor. Alterne também via Ctrl+Shift+P → 'theme'.",
                ),
                "combo",
                "catppuccin_mocha",
                _ensure_themes() or THEME_ORDER,
                "string",
                '"catppuccin_mocha"',
            ),
            (
                "language",
                _t("setting.language", "Idioma da Interface"),
                _t(
                    "setting.desc.language",
                    "Idioma das mensagens e menus do Codis. Requer reinicialização.",
                ),
                "combo",
                "pt-BR",
                _get_language_options(),
                "string",
                '"pt-BR"',
            ),
            (
                "ui_font_family",
                _t("setting.ui_font_family", "Fonte da Interface"),
                _t(
                    "setting.desc.ui_font_family",
                    "Fonte usada em menus, painéis e textos da interface.",
                ),
                "combo",
                "Segoe UI",
                _get_system_ui_fonts(),
                "string",
                '"Segoe UI"',
            ),
            (
                "ui_scale",
                _t("setting.ui_scale", "Escala da Interface (%)"),
                _t(
                    "setting.desc.ui_scale",
                    "Tamanho geral da interface em porcentagem. 0 = automático "
                    "(ajusta à resolução da tela); 50-250 = manual. Reduza em "
                    "telas pequenas, aumente em telas grandes.",
                ),
                "slider",
                0,
                (0, 250),
                "integer (0 ou 50–250)",
                "0",
            ),
            (
                "ui_font_size",
                _t("setting.ui_font_size", "Tamanho da Fonte da Interface"),
                _t(
                    "setting.desc.ui_font_size",
                    "Tamanho da fonte da interface em pontos.",
                ),
                "slider",
                10,
                (8, 16),
                "integer (8–16)",
                "10",
            ),
            (
                "activity_bar",
                _t("setting.activity_bar", "Barra de Atividades"),
                _t(
                    "setting.desc.activity_bar",
                    "Exibe a barra de atividades lateral com ícones de painel.",
                ),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "show_welcome",
                _t("setting.show_welcome", "Tela de Boas-Vindas"),
                _t(
                    "setting.desc.show_welcome",
                    "Exibe a tela de boas-vindas ao iniciar sem um projeto aberto.",
                ),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "__sep_layout",
                "🖥  " + _t("setting.cat_ui", "Interface & Layout"),
                "",
                "sep",
                None,
                None,
                None,
                None,
            ),
            (
                "sidebar_width",
                _t("setting.sidebar_width", "Largura da Sidebar (px)"),
                _t(
                    "setting.desc.sidebar_width",
                    "Largura inicial da barra lateral em pixels.",
                ),
                "slider",
                320,
                (160, 600),
                "integer (160–600 px)",
                "320",
            ),
            (
                "panel_height",
                _t("setting.panel_height", "Altura do Painel Inferior (px)"),
                _t(
                    "setting.desc.panel_height",
                    "Altura inicial do painel inferior (terminal, problemas, output).",
                ),
                "slider",
                240,
                (120, 600),
                "integer (120–600 px)",
                "240",
            ),
            (
                "minimap_width",
                _t("setting.minimap_width", "Largura do Minimapa (px)"),
                _t(
                    "setting.desc.minimap_width",
                    "Largura do painel de minimapa em pixels.",
                ),
                "slider",
                110,
                (60, 200),
                "integer (60–200 px)",
                "110",
            ),
            (
                "minimap_scale",
                _t("setting.minimap_scale", "Escala do Minimapa"),
                _t(
                    "setting.desc.minimap_scale",
                    "Pixels por coluna de caractere no minimapa. Maior = mais denso.",
                ),
                "slider",
                3,
                (1, 6),
                "integer (1–6)",
                "3",
            ),
            (
                "window_width",
                _t("setting.window_width", "Largura da Janela (px)"),
                _t(
                    "setting.desc.window_width",
                    "Largura inicial da janela principal em pixels.",
                ),
                "slider",
                1600,
                (800, 3840),
                "integer (800–3840 px)",
                "1600",
            ),
            (
                "window_height",
                _t("setting.window_height", "Altura da Janela (px)"),
                _t(
                    "setting.desc.window_height",
                    "Altura inicial da janela principal em pixels.",
                ),
                "slider",
                960,
                (600, 2160),
                "integer (600–2160 px)",
                "960",
            ),
            (
                "max_recent",
                _t("setting.max_recent", "Máximo de Recentes"),
                _t(
                    "setting.desc.max_recent",
                    "Número máximo de arquivos e pastas recentes na lista.",
                ),
                "slider",
                20,
                (5, 50),
                "integer (5–50)",
                "20",
            ),
            (
                "__sep_save",
                "💾  " + _t("setting.cat_save", "Salvamento & Sessão"),
                "",
                "sep",
                None,
                None,
                None,
                None,
            ),
            (
                "autosave",
                _t("setting.autosave", "Salvamento Automático"),
                _t(
                    "setting.desc.autosave",
                    "Salva automaticamente o arquivo após um período de inatividade.",
                ),
                "toggle",
                False,
                None,
                "boolean",
                "false",
            ),
            (
                "autosave_delay",
                _t("setting.autosave_delay", "Atraso do Autosave (s)"),
                _t(
                    "setting.desc.autosave_delay",
                    "Tempo de inatividade em segundos antes de salvar automaticamente.",
                ),
                "slider",
                30,
                (5, 300),
                "integer (5–300 s)",
                "30",
            ),
            (
                "format_on_save",
                _t("setting.format_on_save", "Formatar ao Salvar"),
                _t(
                    "setting.desc.format_on_save",
                    "Executa o formatador (Black/Prettier) automaticamente ao salvar.",
                ),
                "toggle",
                False,
                None,
                "boolean",
                "false",
            ),
            (
                "trim_trailing_whitespace",
                _t("setting.trim_trailing_whitespace",
                   "Remover Espaços no Final"),
                _t(
                    "setting.desc.trim_trailing",
                    "Remove espaços em branco no final de cada linha ao salvar.",
                ),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "insert_final_newline",
                _t("setting.insert_final_newline", "Inserir Nova Linha Final"),
                _t(
                    "setting.desc.insert_newline",
                    "Garante que o arquivo termine com uma linha vazia ao salvar.",
                ),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "session_restore",
                _t("setting.session_restore", "Restaurar Sessão"),
                _t(
                    "setting.desc.session_restore",
                    "Reabre os arquivos e pastas da última sessão ao iniciar.",
                ),
                "toggle",
                False,
                None,
                "boolean",
                "false",
            ),
            (
                "exit_save_mode",
                _t("setting.exit_save_mode", "Ao Sair com Alterações"),
                _t("setting.desc.exit_save_mode", "ask = pergunta; save = sempre salva; discard = sai sem salvar nada."),
                "combo",
                "ask",
                ['ask', 'save', 'discard'],
                "string",
                '"ask"',
            ),
            (
                "session_save_on_exit",
                _t("setting.session_save_on_exit", "Lembrar Arquivos Abertos ao Sair"),
                _t("setting.desc.session_save_on_exit", "Grava a lista de abas/pasta ao sair. Desligado: o Codis abre limpo na próxima vez."),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "__sep_trash",
                "🗑  " + _t("setting.cat_trash", "Lixeira & Exclusão"),
                "",
                "sep",
                None,
                None,
                None,
                None,
            ),
            (
                "confirm_delete",
                _t("setting.confirm_delete", "Confirmar Antes de Excluir"),
                _t("setting.desc.confirm_delete", "Pergunta antes de mover itens para a lixeira (Shift+Del sempre pergunta)."),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "trash_backend",
                _t("setting.trash_backend", "Destino da Lixeira"),
                _t("setting.desc.trash_backend", "workspace = pasta .codis_trash do projeto (instantâneo, mesmo disco); system = lixeira do SO."),
                "combo",
                "workspace",
                ['workspace', 'system'],
                "string",
                '"workspace"',
            ),
            (
                "trash_retention_days",
                _t("setting.trash_retention_days", "Esvaziar Lixeira Após (dias)"),
                _t("setting.desc.trash_retention_days", "0 = nunca apagar automaticamente."),
                "slider",
                0,
                (0, 365),
                "integer (0–365)",
                "0",
            ),
            (
                "trash_max_mb",
                _t("setting.trash_max_mb", "Limite da Lixeira (MB)"),
                _t("setting.desc.trash_max_mb", "0 = sem limite. Ao exceder, os itens mais antigos são removidos."),
                "slider",
                0,
                (0, 102400),
                "integer (0–102400)",
                "0",
            ),
            (
                "__sep_lint",
                "🔍  " + _t("setting.cat_lint", "Lint & Análise de Código"),
                "",
                "sep",
                None,
                None,
                None,
                None,
            ),
            (
                "lint_enabled",
                _t("setting.lint_enabled", "Lint Habilitado"),
                _t(
                    "setting.desc.lint_enabled",
                    "Habilita a análise de lint em tempo real para todas as linguagens configuradas.",
                ),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "lint_python",
                _t("setting.lint_python", "Lint Python"),
                _t(
                    "setting.desc.lint_python",
                    "Habilita análise de lint para arquivos Python (Flake8, Pylint, Mypy).",
                ),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "lint_js",
                _t("setting.lint_js", "Lint JavaScript/TypeScript"),
                _t(
                    "setting.desc.lint_js",
                    "Habilita análise de lint para arquivos JS/TS com ESLint.",
                ),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "lint_html",
                _t("setting.lint_html", "Lint HTML"),
                _t(
                    "setting.desc.lint_html",
                    "Habilita análise de lint básica para arquivos HTML (tags não fechadas, atributos duplicados).",
                ),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "lint_delay",
                _t("setting.lint_delay", "Atraso do Lint (ms)"),
                _t(
                    "setting.desc.lint_delay",
                    "Tempo de espera após parar de digitar antes de executar o lint.",
                ),
                "slider",
                750,
                (100, 3000),
                "integer (100–3000 ms)",
                "750",
            ),
            (
                "eslint_path",
                _t("setting.eslint_path", "Caminho do ESLint"),
                _t(
                    "setting.desc.eslint_path",
                    "Caminho ou comando para executar o ESLint.",
                ),
                "entry",
                "eslint",
                None,
                "string",
                '"eslint"',
            ),
            (
                "pylint_path",
                _t("setting.pylint_path", "Caminho do Pylint"),
                _t(
                    "setting.desc.pylint_path",
                    "Caminho ou comando para executar o Pylint.",
                ),
                "entry",
                "pylint",
                None,
                "string",
                '"pylint"',
            ),
            (
                "flake8_path",
                _t("setting.flake8_path", "Caminho do Flake8"),
                _t(
                    "setting.desc.flake8_path",
                    "Caminho ou comando para executar o Flake8.",
                ),
                "entry",
                "flake8",
                None,
                "string",
                '"flake8"',
            ),
            (
                "mypy_path",
                _t("setting.mypy_path", "Caminho do Mypy"),
                _t(
                    "setting.desc.mypy_path",
                    "Caminho ou comando para executar o Mypy (verificação de tipos).",
                ),
                "entry",
                "mypy",
                None,
                "string",
                '"mypy"',
            ),
            (
                "prettier_path",
                _t("setting.prettier_path", "Caminho do Prettier"),
                _t(
                    "setting.desc.prettier_path",
                    "Caminho ou comando para executar o Prettier.",
                ),
                "entry",
                "prettier",
                None,
                "string",
                '"prettier"',
            ),
            (
                "__sep_lsp",
                "🔤  " + _t("setting.cat_lsp", "LSP - Language Servers"),
                "",
                "sep",
                None,
                None,
                None,
                None,
            ),
            (
                "lsp_enabled",
                _t("setting.lsp_enabled", "LSP Habilitado"),
                _t(
                    "setting.desc.lsp_enabled",
                    "Habilita integração com servidores LSP para Go to Definition, Find References, rename, etc.",
                ),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "lsp_hover_enabled",
                _t("setting.lsp_hover_enabled", "Hover LSP"),
                _t(
                    "setting.desc.lsp_hover",
                    "Exibe documentação do LSP ao passar o mouse sobre símbolos.",
                ),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "lsp_format_enabled",
                _t("setting.lsp_format_enabled", "Formatação LSP"),
                _t(
                    "setting.desc.lsp_format",
                    "Usa o formatador do LSP em vez do Prettier/Black ao formatar o documento.",
                ),
                "toggle",
                False,
                None,
                "boolean",
                "false",
            ),
            (
                "lsp_diagnostics",
                _t("setting.lsp_diagnostics", "Diagnósticos LSP"),
                _t(
                    "setting.desc.lsp_diagnostics",
                    "Exibe diagnósticos (erros/avisos) recebidos do servidor LSP no painel Problemas.",
                ),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "__sep_git",
                "🌿  " + _t("setting.cat_git", "Git & Controle de Versão"),
                "",
                "sep",
                None,
                None,
                None,
                None,
            ),
            (
                "git_enabled",
                _t("setting.git_enabled", "Git Habilitado"),
                _t(
                    "setting.desc.git_enabled",
                    "Habilita integração Git (painel Git, decorações na file tree, blame).",
                ),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "git_decorations",
                _t("setting.git_decorations", "Decorações Git na File Tree"),
                _t(
                    "setting.desc.git_decorations",
                    "Exibe indicadores de status Git (M, A, D, ?) nos arquivos da árvore.",
                ),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "git_auto_fetch",
                _t("setting.git_auto_fetch", "Fetch Automático"),
                _t(
                    "setting.desc.git_auto_fetch",
                    "Executa git fetch automaticamente em intervalos regulares.",
                ),
                "toggle",
                False,
                None,
                "boolean",
                "false",
            ),
            (
                "git_fetch_interval",
                _t("setting.git_fetch_interval", "Intervalo do Fetch (s)"),
                _t(
                    "setting.desc.git_fetch_interval",
                    "Intervalo em segundos entre fetches automáticos.",
                ),
                "slider",
                180,
                (30, 3600),
                "integer (30–3600 s)",
                "180",
            ),
            (
                "__sep_terminal",
                "💻  " + _t("setting.cat_terminal", "Terminal Integrado"),
                "",
                "sep",
                None,
                None,
                None,
                None,
            ),
            (
                "default_shell",
                _t("setting.default_shell", "Shell Padrão"),
                _t(
                    "setting.desc.default_shell",
                    "Shell padrão do terminal integrado. Requer reinicialização do terminal.",
                ),
                "combo",
                get_default_shell(),
                list(SHELL_OPTIONS.keys()),
                "string",
                f'"{get_default_shell()}"',
            ),
            (
                "terminal_font_size",
                _t("setting.terminal_font_size", "Tamanho da Fonte do Terminal"),
                _t(
                    "setting.desc.terminal_font_size",
                    "Tamanho da fonte no terminal integrado em pontos.",
                ),
                "slider",
                12,
                (8, 24),
                "integer (8–24)",
                "12",
            ),
            (
                "terminal_max_lines",
                _t("setting.terminal_max_lines", "Máximo de Linhas do Terminal"),
                _t(
                    "setting.desc.terminal_max_lines",
                    "Número máximo de linhas mantidas no buffer do terminal.",
                ),
                "slider",
                8000,
                (1000, 50000),
                "integer (1000–50000)",
                "8000",
            ),
            (
                "__sep_filetree",
                "📁  " + _t("setting.cat_filetree", "Explorador de Arquivos"),
                "",
                "sep",
                None,
                None,
                None,
                None,
            ),
            (
                "show_hidden_files",
                _t("setting.show_hidden_files", "Mostrar Arquivos Ocultos"),
                _t(
                    "setting.desc.show_hidden_files",
                    "Exibe arquivos e pastas que começam com ponto (.) na árvore de arquivos.",
                ),
                "toggle",
                False,
                None,
                "boolean",
                "false",
            ),
            (
                "respect_ignore_files",
                _t("setting.respect_ignore_files", "Respeitar .gitignore"),
                _t(
                    "setting.desc.respect_ignore_files",
                    "Oculta arquivos listados no .gitignore da file tree e da busca.",
                ),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "__sep_render",
                "🌐  " + _t("setting.cat_render", "Preview & Render"),
                "",
                "sep",
                None,
                None,
                None,
                None,
            ),
            (
                "render_internal",
                _t("setting.render_internal", "Render Interno"),
                _t(
                    "setting.desc.render_internal",
                    "Usa o renderizador interno para Markdown/HTML. Desative para usar o navegador do sistema.",
                ),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "__sep_timeline",
                "🕒  " + _t("setting.cat_timeline", "Timeline de Arquivos"),
                "",
                "sep",
                None,
                None,
                None,
                None,
            ),
            (
                "timeline_max_backups",
                _t("setting.timeline_max_backups",
                   "Máximo de Backups por Arquivo"),
                _t(
                    "setting.desc.timeline_max_backups",
                    "Número máximo de snapshots mantidos por arquivo no histórico da timeline.",
                ),
                "slider",
                20,
                (5, 100),
                "integer (5–100)",
                "20",
            ),
            (
                "timeline_max_age_days",
                _t("setting.timeline_max_age_days",
                   "Idade Máxima dos Backups (dias)"),
                _t(
                    "setting.desc.timeline_max_age_days",
                    "Backups mais antigos que este número de dias são removidos automaticamente.",
                ),
                "slider",
                30,
                (1, 365),
                "integer (1–365 dias)",
                "30",
            ),
            (
                "__sep_flowchart",
                "🔀  " + _t("setting.cat_flowchart", "Flowchart"),
                "",
                "sep",
                None,
                None,
                None,
                None,
            ),
            (
                "flowchart_auto",
                _t("setting.flowchart_auto", "Flowchart Automático"),
                _t(
                    "setting.desc.flowchart_auto",
                    "Gera o diagrama de fluxo automaticamente ao editar o arquivo.",
                ),
                "toggle",
                False,
                None,
                "boolean",
                "false",
            ),
            (
                "flowchart_max_depth",
                _t("setting.flowchart_max_depth", "Profundidade Máxima"),
                _t(
                    "setting.desc.flowchart_max_depth",
                    "Profundidade máxima de chamadas analisadas para o flowchart.",
                ),
                "slider",
                3,
                (1, 8),
                "integer (1–8)",
                "3",
            ),
            (
                "__sep_pomodoro",
                "⏱  " + _t("setting.cat_pomodoro", "Pomodoro Timer"),
                "",
                "sep",
                None,
                None,
                None,
                None,
            ),
            (
                "pomodoro_work_min",
                _t("setting.pomodoro_work_min", "Duração do Trabalho (min)"),
                _t(
                    "setting.desc.pomodoro_work",
                    "Duração de cada sessão de trabalho do Pomodoro em minutos.",
                ),
                "slider",
                25,
                (5, 90),
                "integer (5–90 min)",
                "25",
            ),
            (
                "pomodoro_break_min",
                _t("setting.pomodoro_break_min", "Pausa Curta (min)"),
                _t(
                    "setting.desc.pomodoro_break",
                    "Duração da pausa curta entre sessões de trabalho.",
                ),
                "slider",
                5,
                (1, 30),
                "integer (1–30 min)",
                "5",
            ),
            (
                "pomodoro_long_break_min",
                _t("setting.pomodoro_long_break_min", "Pausa Longa (min)"),
                _t(
                    "setting.desc.pomodoro_long_break",
                    "Duração da pausa longa após completar um ciclo de Pomodoros.",
                ),
                "slider",
                15,
                (5, 60),
                "integer (5–60 min)",
                "15",
            ),
            (
                "__sep_perf",
                _t("setting.cat_perf", "Performance"),
                "",
                "sep",
                None,
                None,
                None,
                None,
            ),
            (
                "kui_perf",
                _t("setting.kui_perf", "Perfil de Performance K-UI"),
                _t(
                    "setting.desc.kui_perf",
                    "Controla a intensidade das animações do K-UI. 'minimal' desativa a maioria para máxima performance.",
                ),
                "combo",
                "balanced",
                ["ultra", "balanced", "power_saver", "minimal"],
                "ultra | balanced | power_saver | minimal",
                "balanced",
            ),
            (
                "hl_idle_ms",
                _t("setting.hl_idle_ms", "Debounce do Highlight (ms)"),
                _t(
                    "setting.desc.hl_idle_ms",
                    "Tempo de inatividade antes de re-executar o syntax highlighting completo.",
                ),
                "slider",
                60,
                (20, 500),
                "integer (20–500 ms)",
                "60",
            ),
            (
                "autocomplete_min_chars",
                _t(
                    "setting.autocomplete_min_chars",
                    "Mínimo de Caracteres para Autocomplete",
                ),
                _t(
                    "setting.desc.autocomplete_min_chars",
                    "Número mínimo de caracteres digitados antes de exibir sugestões.",
                ),
                "slider",
                1,
                (1, 5),
                "integer (1–5)",
                "1",
            ),
            (
                "autocomplete_max_items",
                _t("setting.autocomplete_max_items", "Máximo de Sugestões"),
                _t(
                    "setting.desc.autocomplete_max_items",
                    "Número máximo de itens exibidos na lista de autocomplete.",
                ),
                "slider",
                24,
                (5, 50),
                "integer (5–50)",
                "24",
            ),
            (
                "large_file_mb",
                _t("setting.large_file_mb", "Limite de Arquivo Grande (MB)"),
                _t(
                    "setting.desc.large_file_mb",
                    "Arquivos maiores que este limite têm syntax highlighting e lint desabilitados.",
                ),
                "slider",
                3,
                (1, 20),
                "integer (1–20 MB)",
                "3",
            ),
            (
                "search_max_results",
                _t("setting.search_max_results", "Máximo de Resultados de Busca"),
                _t(
                    "setting.desc.search_max_results",
                    "Número máximo de resultados exibidos no Find in Files.",
                ),
                "slider",
                2000,
                (100, 10000),
                "integer (100–10000)",
                "2000",
            ),
            (
                "perf_auto_mode",
                _t("setting.perf_auto_mode", "Detecção Automática de Travamentos"),
                _t(
                    "setting.desc.perf_auto_mode",
                    "Monitora a UI e ativa o modo performance automaticamente quando detectar freeze.",
                ),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "perf_freeze_threshold_ms",
                _t("setting.perf_freeze_threshold_ms", "Limiar de Freeze (ms)"),
                _t(
                    "setting.desc.perf_freeze_threshold_ms",
                    "Quanto de atraso na UI (ms) aciona o modo performance automático. Menor = mais sensível.",
                ),
                "slider",
                500,
                (150, 2000),
                "integer (150–2000 ms)",
                "500",
            ),
            (
                "perf_kui_fallback",
                _t("setting.perf_kui_fallback",
                   "Perfil K-UI no modo performance"),
                _t(
                    "setting.desc.perf_kui_fallback",
                    "Perfil de animação K-UI usado quando o modo performance desativa as animações.",
                ),
                "combo",
                "low",
                ["low", "minimal", "windows", "cosmic_lite", "power_saver"],
                "string",
                "low",
            ),
            (
                "perf_keep_minimap",
                _t("setting.perf_keep_minimap", "[Modo Perf] Manter Minimap"),
                _t(
                    "setting.desc.perf_keep_minimap",
                    "Se desmarcado, o minimap é ocultado ao ativar o modo performance.",
                ),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "perf_keep_sticky_scroll",
                _t(
                    "setting.perf_keep_sticky_scroll",
                    "[Modo Perf] Manter Sticky Scroll",
                ),
                _t(
                    "setting.desc.perf_keep_sticky_scroll",
                    "Se desmarcado, o sticky scroll é desativado no modo performance.",
                ),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "perf_keep_heatmap",
                _t("setting.perf_keep_heatmap",
                   "[Modo Perf] Manter Edit Heatmap"),
                _t(
                    "setting.desc.perf_keep_heatmap",
                    "Se desmarcado, o heatmap de edições é parado no modo performance.",
                ),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "perf_keep_rhythm",
                _t("setting.perf_keep_rhythm",
                   "[Modo Perf] Manter Code Rhythm"),
                _t(
                    "setting.desc.perf_keep_rhythm",
                    "Se desmarcado, a análise de ritmo de digitação é parada no modo performance.",
                ),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "perf_keep_lint",
                _t("setting.perf_keep_lint",
                   "[Modo Perf] Manter Lint Automático"),
                _t(
                    "setting.desc.perf_keep_lint",
                    "Se desmarcado, o lint automático é desativado no modo performance.",
                ),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "perf_keep_kui_anim",
                _t("setting.perf_keep_kui_anim",
                   "[Modo Perf] Manter Animações K-UI"),
                _t(
                    "setting.desc.perf_keep_kui_anim",
                    "Se desmarcado, o K-UI troca para o perfil de fallback no modo performance.",
                ),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "__sep_security",
                "🔒  " + _t("setting.cat_security", "Segurança"),
                "",
                "sep",
                None,
                None,
                None,
                None,
            ),
            (
                "workspace_trust",
                _t("setting.workspace_trust", "Workspace Trust"),
                _t(
                    "setting.desc.workspace_trust",
                    "Exibe diálogo de confiança ao abrir pastas desconhecidas.",
                ),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "__sep_advanced",
                "⚙  " + _t("setting.cat_advanced", "Avançado"),
                "",
                "sep",
                None,
                None,
                None,
                None,
            ),
            (
                "font_min",
                _t("setting.font_min", "Tamanho Mínimo da Fonte"),
                _t(
                    "setting.desc.font_min",
                    "Menor tamanho de fonte permitido ao usar Ctrl+- para diminuir.",
                ),
                "slider",
                7,
                (4, 12),
                "integer (4–12)",
                "7",
            ),
            (
                "font_max",
                _t("setting.font_max", "Tamanho Máximo da Fonte"),
                _t(
                    "setting.desc.font_max",
                    "Maior tamanho de fonte permitido ao usar Ctrl+= para aumentar.",
                ),
                "slider",
                36,
                (20, 72),
                "integer (20–72)",
                "36",
            ),
            (
                "__sep_accessibility",
                "♿  " + _t("setting.cat_accessibility", "Acessibilidade"),
                "",
                "sep",
                None,
                None,
                None,
                None,
            ),
            (
                "a11y_high_contrast",
                _t("setting.a11y_high_contrast", "Alto Contraste (WCAG AA)"),
                _t(
                    "setting.desc.a11y_high_contrast",
                    "Ativa paleta de alto contraste em toda a interface para melhor legibilidade.",
                ),
                "toggle",
                False,
                None,
                "boolean",
                "false",
            ),
            (
                "a11y_colorblind_mode",
                _t("setting.a11y_colorblind_mode", "Modo para Daltonismo"),
                _t(
                    "setting.desc.a11y_colorblind_mode",
                    "Substitui cores problemáticas por paletas otimizadas para cada tipo de daltonismo. Efeito imediato após salvar.",
                ),
                "combo",
                "",
                ["", "protanopia", "deuteranopia", "tritanopia", "achromatopsia"],
                "string",
                "",
            ),
            (
                "a11y_screen_reader",
                _t("setting.a11y_screen_reader",
                   "Habilitar Leitor de Tela / TTS"),
                _t(
                    "setting.desc.a11y_screen_reader",
                    "Ativa saída de voz para leitores de tela (NVDA, ORCA, espeak, say).",
                ),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "a11y_keyboard_nav",
                _t("setting.a11y_keyboard_nav",
                   "Navegação Aprimorada por Teclado"),
                _t(
                    "setting.desc.a11y_keyboard_nav",
                    "Ativa foco visível aprimorado e navegação Tab/Shift+Tab em todos os painéis.",
                ),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "a11y_large_ui_font",
                _t("setting.a11y_large_ui_font",
                   "Fonte de UI Grande (mín. 13pt)"),
                _t(
                    "setting.desc.a11y_large_ui_font",
                    "Aumenta a fonte da interface para no mínimo 13pt em todos os menus e painéis.",
                ),
                "toggle",
                False,
                None,
                "boolean",
                "false",
            ),
            (
                "a11y_reduce_motion",
                _t("setting.a11y_reduce_motion", "Reduzir Animações e Movimento"),
                _t(
                    "setting.desc.a11y_reduce_motion",
                    "Desativa animações, transições e efeitos visuais em movimento.",
                ),
                "toggle",
                False,
                None,
                "boolean",
                "false",
            ),
            (
                "__sep_ssh",
                "🖧  " + _t("setting.cat_ssh", "SSH Remoto"),
                "",
                "sep",
                None,
                None,
                None,
                None,
            ),
            (
                "ssh_default_port",
                _t("setting.ssh_default_port", "Porta SSH Padrão"),
                _t(
                    "setting.desc.ssh_default_port",
                    "Porta padrão para novas conexões SSH quando não especificada.",
                ),
                "slider",
                22,
                (1, 65535),
                "integer",
                "22",
            ),
            (
                "ssh_connect_timeout",
                _t("setting.ssh_connect_timeout", "Timeout de Conexão (s)"),
                _t(
                    "setting.desc.ssh_connect_timeout",
                    "Segundos antes de abortar tentativa de conexão SSH.",
                ),
                "slider",
                10,
                (3, 60),
                "integer",
                "10",
            ),
            (
                "ssh_keepalive_interval",
                _t("setting.ssh_keepalive_interval", "Intervalo Keepalive (s)"),
                _t(
                    "setting.desc.ssh_keepalive_interval",
                    "Intervalo em segundos para envio de pacotes keepalive. 0 desativa.",
                ),
                "slider",
                30,
                (0, 300),
                "integer",
                "30",
            ),
            (
                "ssh_agent_forwarding",
                _t("setting.ssh_agent_forwarding", "Forwarding de Agente SSH"),
                _t(
                    "setting.desc.ssh_agent_forwarding",
                    "Encaminha o agente SSH local para o host remoto (opção -A).",
                ),
                "toggle",
                False,
                None,
                "boolean",
                "false",
            ),
            (
                "ssh_strict_host_key",
                _t("setting.ssh_strict_host_key", "Verificação Estrita de Host"),
                _t(
                    "setting.desc.ssh_strict_host_key",
                    "Se ativado, rejeita hosts desconhecidos. Desativado usa accept-new para novos hosts.",
                ),
                "toggle",
                False,
                None,
                "boolean",
                "false",
            ),
            (
                "ssh_sftp_buffer_kb",
                _t("setting.ssh_sftp_buffer_kb", "Buffer SFTP (KB)"),
                _t(
                    "setting.desc.ssh_sftp_buffer_kb",
                    "Tamanho do buffer de transferência SFTP em kilobytes.",
                ),
                "slider",
                32,
                (8, 512),
                "integer",
                "32",
            ),
            (
                "ssh_open_in_editor",
                _t("setting.ssh_open_in_editor",
                   "Abrir Arquivo Remoto no Editor"),
                _t(
                    "setting.desc.ssh_open_in_editor",
                    "Ao clicar em um arquivo remoto, baixa e abre no editor automaticamente.",
                ),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "debug_dap_enabled",
                _t("setting.debug_dap_enabled",
                   "Habilitar Debug via debugpy (DAP)"),
                _t(
                    "setting.desc.debug_dap_enabled",
                    "Usa debugpy para depuração real com breakpoints, stepping e "
                    "variáveis em Python. Ativado automaticamente quando o "
                    "debugpy está disponível no PATH/ambiente - o Codis conecta "
                    "a ele sozinho, sem precisar de um cliente DAP externo. "
                    "Desative aqui para forçar a execução simples (sem "
                    "breakpoints reais).",
                ),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "debug_dap_port",
                _t("setting.debug_dap_port", "Porta do debugpy"),
                _t(
                    "setting.desc.debug_dap_port",
                    "Porta local usada pelo servidor debugpy para conexão DAP.",
                ),
                "slider",
                5678,
                (1024, 65535),
                "integer",
                "5678",
            ),
            (
                "__sep_collab",
                "👥  " + _t("setting.cat_collab", "Colaboração"),
                "",
                "sep",
                None,
                None,
                None,
                None,
            ),
            (
                "collab_enabled",
                _t("setting.collab_enabled", "Habilitar Edição Colaborativa"),
                _t(
                    "setting.desc.collab_enabled",
                    "Ativa o servidor de colaboração em tempo real para edição simultânea.",
                ),
                "toggle",
                False,
                None,
                "boolean",
                "false",
            ),
            (
                "collab_port",
                _t("setting.collab_port", "Porta do Servidor Collab"),
                _t(
                    "setting.desc.collab_port",
                    "Porta TCP usada pelo servidor de colaboração.",
                ),
                "slider",
                9001,
                (1024, 65535),
                "integer",
                "9001",
            ),
            (
                "collab_max_peers",
                _t("setting.collab_max_peers", "Máx. Participantes"),
                _t(
                    "setting.desc.collab_max_peers",
                    "Número máximo de usuários simultâneos na sessão colaborativa.",
                ),
                "slider",
                5,
                (2, 20),
                "integer",
                "5",
            ),
            (
                "collab_show_cursors",
                _t("setting.collab_show_cursors", "Mostrar Cursores Remotos"),
                _t(
                    "setting.desc.collab_show_cursors",
                    "Exibe a posição do cursor de cada participante com cor diferente.",
                ),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "collab_show_chat",
                _t("setting.collab_show_chat", "Mostrar Chat Integrado"),
                _t(
                    "setting.desc.collab_show_chat",
                    "Exibe painel de chat lateral durante sessões colaborativas.",
                ),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "collab_autosave_sync",
                _t("setting.collab_autosave_sync", "Sincronizar Auto-save"),
                _t(
                    "setting.desc.collab_autosave_sync",
                    "Sincroniza o estado do arquivo entre participantes ao auto-salvar.",
                ),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "__sep_plugins_cfg",
                "🔌  " + _t("setting.cat_plugins_cfg", "Plugins (Isolamento)"),
                "",
                "sep",
                None,
                None,
                None,
                None,
            ),
            (
                "plugin_isolation",
                _t(
                    "setting.plugin_isolation",
                    "Executar Plugins em Processos Separados",
                ),
                _t(
                    "setting.desc.plugin_isolation",
                    "Plugins com isolated=True rodam em subprocessos Python independentes para maior segurança.",
                ),
                "toggle",
                True,
                None,
                "boolean",
                "true",
            ),
            (
                "plugin_timeout_s",
                _t("setting.plugin_timeout_s", "Timeout de Chamada ao Plugin (s)"),
                _t(
                    "setting.desc.plugin_timeout_s",
                    "Segundos antes de considerar um plugin travado e encerrar o processo.",
                ),
                "slider",
                10,
                (2, 60),
                "integer",
                "10",
            ),
            (
                "plugin_heartbeat_s",
                _t("setting.plugin_heartbeat_s", "Timeout de Heartbeat (s)"),
                _t(
                    "setting.desc.plugin_heartbeat_s",
                    "Intervalo em segundos entre verificações de saúde do processo do plugin.",
                ),
                "slider",
                5,
                (1, 30),
                "integer",
                "5",
            ),
            (
                "plugin_max_restarts",
                _t("setting.plugin_max_restarts",
                   "Máx. Reinicializações Automáticas"),
                _t(
                    "setting.desc.plugin_max_restarts",
                    "Número máximo de vezes que um plugin travado é reiniciado automaticamente.",
                ),
                "slider",
                3,
                (0, 10),
                "integer",
                "3",
            ),
            (
                "plugin_sandbox_network",
                _t(
                    "setting.plugin_sandbox_network",
                    "Bloquear Rede em Plugins Isolados",
                ),
                _t(
                    "setting.desc.plugin_sandbox_network",
                    "Impede que plugins isolados façam conexões de rede (experimental, Linux).",
                ),
                "toggle",
                False,
                None,
                "boolean",
                "false",
            ),
            (
                "__sep_theme_adv",
                _t("setting.cat_theme_adv", "Aparência Avançada"),
                "",
                "sep",
                None,
                None,
                None,
                None,
            ),
            (
                "ui_cursor",
                _t("setting.ui_cursor", "Cursor Customizado"),
                _t(
                    "setting.desc.ui_cursor",
                    "Cursor do mouse em toda a interface. Nomes Tk: arrow, crosshair, hand2, pirate, star, fleur...",
                ),
                "combo",
                "default",
                [
                    "default",
                    "arrow",
                    "crosshair",
                    "hand2",
                    "move",
                    "xterm",
                    "watch",
                    "plus",
                    "fleur",
                    "heart",
                    "star",
                    "pirate",
                    "target",
                ],
                "string",
                "default",
            ),
            (
                "ui_icon_set",
                _t("setting.ui_icon_set", "Conjunto de Ícones"),
                _t(
                    "setting.desc.ui_icon_set",
                    "Nome da pasta em src/theme/icons/ para ícones customizados. Em branco = padrões.",
                ),
                "text",
                "",
                None,
                "string",
                "",
            ),
            (
                "ui_font_family",
                _t("setting.ui_font_family", "Família de Fonte da UI"),
                _t(
                    "setting.desc.ui_font_family",
                    "Fonte usada em menus e painéis (não no editor). Em branco = tema padrão.",
                ),
                "text",
                "",
                None,
                "string",
                "",
            ),
            (
                "ui_scale",
                _t("setting.ui_scale", "Escala da Interface (%)"),
                _t(
                    "setting.desc.ui_scale",
                    "Tamanho geral da interface em porcentagem. 0 = automático "
                    "(ajusta à resolução da tela); 50-250 = manual. Reduza em "
                    "telas pequenas, aumente em telas grandes.",
                ),
                "slider",
                0,
                (0, 250),
                "integer (0 ou 50–250)",
                "0",
            ),
            (
                "ui_font_size",
                _t("setting.ui_font_size", "Tamanho da Fonte da UI"),
                _t(
                    "setting.desc.ui_font_size",
                    "Tamanho em pontos da fonte da UI (0 = auto baseado no DPI).",
                ),
                "slider",
                0,
                (0, 20),
                "integer",
                "0",
            ),
        ] + cls._EXTRA_SCHEMA

    def __init__(
        self,
        parent: tk.Widget,
        tmgr: ThemeManager,
        cfg: ConfigManager,
        on_change: Callable | None = None,
    ) -> None:
        """Inicializa o editor de configurações JSON avançado."""
        t = tmgr.theme
        super().__init__(parent, bg=t.editor_bg)
        self._tmgr = tmgr
        self._cfg = cfg
        self._on_change = on_change
        self._all_rows: list[dict] = []
        self._active_cat: str | None = None
        self._filter_q = ""
        self._built = False
        self._theme_stale = False
        self.bind("<Map>", self._on_map, add=True)
        I18n.get_instance().on_locale_registry_change(self._refresh_language_combo)

    def _ensure_built(self) -> None:
        """Constrói a UI na primeira exibição (idempotente)."""
        if self._built:
            return
        self._built = True
        self._build_ui()

    def _on_map(self, _event: Any = None) -> None:
        """Garante a construção e reaplica um tema pendente ao exibir o painel."""
        self._ensure_built()
        if self._theme_stale:
            self._theme_stale = False
            self._rebuild_for_theme()

    def _rebuild_for_theme(self) -> None:
        """Reconstrói linhas e categorias com o tema atual."""
        for w in self._content.winfo_children():
            w.destroy()
        self._all_rows.clear()
        self._build_rows()
        self._build_cat_panel()
        if self._filter_q:
            self._apply_filter(self._filter_q)

    def _build_ui(self) -> None:
        t = self._tmgr.theme

        top = tk.Frame(self, bg=t.editor_bg)
        top.pack(fill="x", padx=0, pady=0)

        tk.Label(
            top,
            text="  ⚙  " + _t("ui.settings_title", "Configurações"),
            bg=t.editor_bg,
            fg=t.fg,
            font=(FONT_UI, 11, "bold"),
        ).pack(side="left", pady=6)

        edit_btn = tk.Label(
            top,
            text="{ } JSON",
            bg=t.editor_bg,
            fg=getattr(t, "t_link", t.accent),
            font=(FONT_UI, 8),
            cursor="hand2",
            padx=8,
        )
        edit_btn.pack(side="right", pady=6)
        edit_btn.bind("<Button-1>", self._open_json)
        edit_btn.bind("<Enter>", lambda _: edit_btn.config(fg=t.fg))
        edit_btn.bind(
            "<Leave>", lambda _: edit_btn.config(
                fg=getattr(t, "t_link", t.accent))
        )

        sb = tk.Frame(self, bg=t.editor_bg, pady=0)
        sb.pack(fill="x", padx=8, pady=(0, 4))

        search_wrap = tk.Frame(
            sb, bg=t.input_bg, highlightthickness=1, highlightbackground=t.border
        )
        search_wrap.pack(fill="x")

        tk.Label(
            search_wrap, text="  🔍", bg=t.input_bg, fg=t.fg_muted, font=(FONT_UI, 9)
        ).pack(side="left")

        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", self._on_search_change)
        self._search_entry = tk.Entry(
            search_wrap,
            textvariable=self._search_var,
            bg=t.input_bg,
            fg=t.fg,
            insertbackground=getattr(t, "cursor", t.fg),
            relief="flat",
            bd=0,
            font=(FONT_UI, 10),
            highlightthickness=0,
        )
        self._search_entry.pack(side="left", fill="x",
                                expand=True, padx=4, pady=4)

        self._clear_btn = tk.Label(
            search_wrap,
            text="✕",
            bg=t.input_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 9),
            cursor="hand2",
            padx=6,
        )
        self._clear_btn.bind(
            "<Button-1>",
            lambda _: (self._search_var.set(""),
                       self._search_entry.focus_set()),
        )

        tk.Frame(self, bg=t.border, height=1).pack(fill="x")

        body = tk.Frame(self, bg=t.editor_bg)
        body.pack(fill="both", expand=True)

        self._cat_panel = tk.Frame(
            body, bg=getattr(t, "sidebar_bg", t.panel_bg), width=148
        )
        self._cat_panel.pack(side="left", fill="y")
        self._cat_panel.pack_propagate(False)

        tk.Frame(body, bg=t.border, width=1).pack(side="left", fill="y")

        self._right_pane = right = tk.Frame(body, bg=t.editor_bg)
        right.pack(side="left", fill="both", expand=True)

        self._canvas = tk.Canvas(right, bg=t.editor_bg, highlightthickness=0)
        vsb = ttk.Scrollbar(
            right,
            orient="vertical",
            command=self._canvas.yview,
            style="Thin.Vertical.TScrollbar",
        )
        vsb.pack(side="right", fill="y")
        self._canvas.config(yscrollcommand=vsb.set)
        self._canvas.pack(fill="both", expand=True)

        self._content = tk.Frame(self._canvas, bg=t.editor_bg)
        self._cw = self._canvas.create_window(
            (0, 0), window=self._content, anchor="nw")

        debounce_canvas(self._canvas, self._cw, ms=40,
                        extra_widget=self._content)

        self._bind_scroll(self._canvas)
        self._bind_scroll(self._content)

        self._build_rows()
        self._build_cat_panel()
        self._init_search_debounce()

    def _build_cat_panel(self) -> None:
        """Popula o painel lateral de categorias."""
        for w in self._cat_panel.winfo_children():
            w.destroy()

        schema = self._get_schema()
        seen: list[tuple[str, str]] = []
        for entry in schema:
            if entry[3] == "sep":
                seen.append((entry[0], entry[1]))

        for cat_key, cat_label in seen:
            self._build_cat_button(cat_key, cat_label)

    def _build_cat_button(self, cat_key: str, cat_label: str) -> None:
        t = self._tmgr.theme
        is_active = self._active_cat == cat_key

        btn_bg = (
            getattr(t, "sel_bg", t.panel_bg)
            if is_active
            else getattr(t, "sidebar_bg", t.panel_bg)
        )
        btn_fg = t.fg if is_active else t.fg_muted

        btn = tk.Label(
            self._cat_panel,
            text=cat_label,
            anchor="w",
            bg=btn_bg,
            fg=btn_fg,
            font=(FONT_UI, 8, "bold" if is_active else "normal"),
            padx=10,
            pady=5,
            cursor="hand2",
            wraplength=130,
            justify="left",
        )
        btn.pack(fill="x")

        def _enter(_, b=btn):
            if self._active_cat != cat_key:
                b.config(
                    bg=getattr(t, "hover_bg", getattr(
                        t, "sidebar_hover", t.sel_bg))
                )

        def _leave(_, b=btn):
            if self._active_cat != cat_key:
                b.config(bg=getattr(t, "sidebar_bg", t.panel_bg))

        def _click(_, k=cat_key):
            self._scroll_to_category(k)

        btn.bind("<Enter>", _enter)
        btn.bind("<Leave>", _leave)
        btn.bind("<Button-1>", _click)

    def _scroll_to_category(self, cat_key: str) -> None:
        """Rola o painel de conteúdo até o separador da categoria."""
        self._active_cat = cat_key
        self._build_cat_panel()
        for row_info in self._all_rows:
            if row_info.get("is_sep") and row_info.get("cat_key") == cat_key:
                w = row_info["widget"]
                try:
                    self._content.update_idletasks()
                    wy = w.winfo_y()
                    ch = self._canvas.winfo_height()
                    total = self._content.winfo_height()
                    if total > ch:
                        frac = max(0.0, min(1.0, wy / (total - ch)))
                        self._canvas.yview_moveto(frac)
                except tk.TclError:
                    pass
                return

    def _build_rows(self) -> None:
        t = self._tmgr.theme
        self._all_rows.clear()

        schema = self._get_schema()
        current_cat: str | None = None

        for entry in schema:
            key, label, desc, kind, default, opts, type_hint, example = entry

            if kind == "sep":
                current_cat = key
                w = self._build_sep(key, label)
                self._all_rows.append(
                    {
                        "widget": w,
                        "search_key": label.lower(),
                        "is_sep": True,
                        "cat_key": key,
                    }
                )
            else:
                w = self._build_row(
                    key,
                    label,
                    desc,
                    kind,
                    default,
                    opts,
                    t,
                    type_hint,
                    example,
                    current_cat,
                )
                self._all_rows.append(
                    {
                        "widget": w,
                        "search_key": (key + " " + label + " " + desc).lower(),
                        "is_sep": False,
                        "cat_key": current_cat,
                    }
                )

    def _build_sep(self, cat_key: str, label: str) -> tk.Frame:
        t = self._tmgr.theme

        wrapper = tk.Frame(self._content, bg=t.editor_bg)
        wrapper.pack(fill="x", pady=(16, 0))

        accent_color = getattr(t, "accent", "#7aa2f7")
        sep_bar = tk.Frame(wrapper, bg=accent_color, width=3)
        sep_bar.pack(side="left", fill="y", padx=(12, 0))

        lbl = tk.Label(
            wrapper,
            text=f"  {label}",
            bg=t.editor_bg,
            fg=t.fg,
            font=(FONT_UI, 9, "bold"),
            anchor="w",
        )
        lbl.pack(side="left", fill="x", expand=True, pady=4)

        tk.Frame(self._content, bg=t.border, height=1).pack(
            fill="x", padx=12, pady=(0, 4)
        )

        return wrapper

    def _build_row(
        self,
        key: str,
        label: str,
        desc: str,
        kind: str,
        default: Any,
        opts: Any,
        t: Theme,
        type_hint: str | None,
        example: str | None,
        cat_key: str | None,
    ) -> tk.Frame:
        row = tk.Frame(self._content, bg=t.editor_bg, highlightthickness=0)
        row.pack(fill="x", padx=0, pady=0)

        inner = tk.Frame(row, bg=t.editor_bg, padx=14, pady=6)
        inner.pack(fill="x", expand=True)

        cur_val = self._cfg.get(key, default)

        txt_col = tk.Frame(inner, bg=t.editor_bg)
        txt_col.pack(side="left", fill="x", expand=True, anchor="n")

        lbl_w = tk.Label(
            txt_col,
            text=label,
            bg=t.editor_bg,
            fg=t.fg,
            font=(FONT_UI, 9, "bold"),
            anchor="w",
            justify="left",
        )
        lbl_w.pack(anchor="w")

        if desc:
            desc_w = tk.Label(
                txt_col,
                text=desc,
                bg=t.editor_bg,
                fg=t.fg_muted,
                font=(FONT_UI, 8),
                anchor="w",
                justify="left",
                wraplength=1,
            )
            desc_w.pack(anchor="w")
            object.__setattr__(row, "_desc_lbl", desc_w)

        ctrl_col = tk.Frame(inner, bg=t.editor_bg)
        ctrl_col.pack(side="right", anchor="n", padx=(8, 0))

        self._build_control(ctrl_col, key, kind, default,
                            opts, cur_val, t, row)

        rb = tk.Label(
            ctrl_col,
            text="↺",
            bg=t.editor_bg,
            fg=t.fg_muted,
            font=(FONT_UI, 7),
            cursor="hand2",
        )
        rb.pack(anchor="e", pady=(2, 0))
        rb.bind("<Button-1>", lambda _, k=key, d=default: self._reset(k, d))
        rb.bind("<Enter>", lambda _, w=rb: w.config(
            fg=getattr(t, "accent", t.fg)))
        rb.bind("<Leave>", lambda _, w=rb: w.config(fg=t.fg_muted))

        tk.Frame(self._content, bg=t.border, height=1).pack(fill="x", padx=12)

        hover_bg = getattr(t, "hover_bg", getattr(t, "list_hover", t.sel_bg))
        _hover_children = list(inner.winfo_children())
        make_hover_highlight(row, _hover_children +
                             [inner], hover_bg, t.editor_bg)
        make_hover_highlight(inner, _hover_children, hover_bg, t.editor_bg)

        self._bind_scroll(row)
        self._bind_scroll(inner)

        def _on_row_resize(e, _r=row):
            dl = getattr(_r, "_desc_lbl", None)
            if dl:
                avail = max(100, e.width - 230)
                with contextlib.suppress(tk.TclError):
                    dl.config(wraplength=avail)

        row.bind("<Configure>", _on_row_resize)

        return row

    def _build_control(
        self,
        parent: tk.Widget,
        key: str,
        kind: str,
        default: Any,
        opts: Any,
        cur_val: Any,
        t: Theme,
        row: tk.Frame,
    ) -> None:
        if kind == "toggle":
            self._build_toggle(parent, key, cur_val, t)
        elif kind == "slider":
            self._build_slider(parent, key, opts, cur_val, t)
        elif kind == "combo":
            if key == "language":
                self._build_language_combo(parent, key, cur_val, t, row)
            else:
                self._build_combo(parent, key, opts, cur_val, t)
        elif kind == "entry":
            self._build_entry(parent, key, cur_val, t)

    def _build_toggle(
        self, parent: tk.Widget, key: str, cur_val: Any, t: Theme
    ) -> None:
        """Custom toggle switch (sliding pill, no ttk Checkbutton)."""
        is_on = bool(cur_val)

        ON_BG = getattr(t, "accent", "#7aa2f7")
        OFF_BG = getattr(t, "border", "#3a3f58")
        PILL_W, PILL_H = 36, 18
        DOT_R = 7

        c = tk.Canvas(
            parent,
            width=PILL_W,
            height=PILL_H,
            bg=t.editor_bg,
            highlightthickness=0,
            cursor="hand2",
        )
        c.pack(pady=6)

        state = {"on": is_on}

        def _draw(on: bool) -> None:
            c.delete("all")
            bg = ON_BG if on else OFF_BG
            c.create_oval(0, 0, PILL_H, PILL_H, fill=bg, outline="")
            c.create_oval(PILL_W - PILL_H, 0, PILL_W,
                          PILL_H, fill=bg, outline="")
            c.create_rectangle(
                PILL_H // 2, 0, PILL_W - PILL_H // 2, PILL_H, fill=bg, outline=""
            )
            dx = PILL_W - DOT_R - 3 if on else DOT_R + 3
            dy = PILL_H // 2
            c.create_oval(
                dx - DOT_R,
                dy - DOT_R,
                dx + DOT_R,
                dy + DOT_R,
                fill="#ffffff",
                outline="",
            )

        _draw(is_on)

        def _toggle(_e: Any) -> None:
            state["on"] = not state["on"]
            _draw(state["on"])
            self._save(key, state["on"])

        c.bind("<Button-1>", _toggle)

    def _build_slider(
        self, parent: tk.Widget, key: str, opts: tuple, cur_val: Any, t: Theme
    ) -> None:
        lo, hi = opts
        try:
            val = int(float(cur_val))
        except (TypeError, ValueError):
            val = lo

        frame = tk.Frame(parent, bg=t.editor_bg)
        frame.pack(pady=4)

        val_lbl = tk.Label(
            frame,
            text=str(val),
            bg=t.editor_bg,
            fg=t.fg,
            font=(FONT_UI, 8),
            width=6,
            anchor="e",
        )
        val_lbl.pack(side="right")

        var = tk.DoubleVar(value=val)

        def _on_slide(v: str) -> None:
            iv = int(float(v))
            val_lbl.config(text=str(iv))
            if getattr(frame, "_slide_job", None):
                with contextlib.suppress(Exception):
                    frame.after_cancel(frame._slide_job)
            frame._slide_job = frame.after(
                120, lambda k=key, iv=iv: self._save(k, iv))

        sl = ttk.Scale(
            frame,
            from_=lo,
            to=hi,
            orient="horizontal",
            variable=var,
            length=150,
            command=_on_slide,
        )
        sl.pack(side="left")

    def _build_combo(
        self, parent: tk.Widget, key: str, opts: list, cur_val: Any, t: Theme
    ) -> None:
        var = tk.StringVar(value=str(cur_val))
        dd = DropDown(
            parent,
            textvariable=var,
            values=opts,
            width=20,
            font=(FONT_UI, 9),
            state="readonly",
        )
        dd.pack(pady=4)
        dd.bind("<<ComboboxSelected>>", lambda _,
                k=key, v=var: self._save(k, v.get()))

    def _build_entry(self, parent: tk.Widget, key: str, cur_val: Any, t: Theme) -> None:
        var = tk.StringVar(value=str(cur_val))
        wrap = tk.Frame(
            parent, bg=t.input_bg, highlightthickness=1, highlightbackground=t.border
        )
        wrap.pack(pady=4)
        ent = tk.Entry(
            wrap,
            textvariable=var,
            bg=t.input_bg,
            fg=t.fg,
            insertbackground=getattr(t, "cursor", t.fg),
            relief="flat",
            bd=0,
            font=(FONT_UI, 9),
            width=20,
        )
        ent.pack(padx=4, pady=2)

        def _save_entry(*_: Any) -> None:
            self._save(key, var.get())

        ent.bind("<FocusOut>", _save_entry)
        ent.bind("<Return>", _save_entry)

        def _focus_in(_e: Any) -> None:
            wrap.config(highlightbackground=getattr(t, "accent", t.border))

        def _focus_out(_e: Any) -> None:
            wrap.config(highlightbackground=t.border)
            _save_entry()

        ent.bind("<FocusIn>", _focus_in)
        ent.bind("<FocusOut>", _focus_out)

    def _build_language_combo(
        self, parent: tk.Widget, key: str, cur_val: Any, t: Theme, row: tk.Frame
    ) -> None:
        labels_by_code = _get_language_labels()
        codes = _get_language_options()
        if str(cur_val) not in codes:
            codes.append(str(cur_val))
        display_names = [labels_by_code.get(c, c) for c in codes]
        current_name = labels_by_code.get(str(cur_val), str(cur_val))

        var = tk.StringVar(value=current_name)
        dd = DropDown(
            parent,
            textvariable=var,
            values=display_names,
            width=20,
            font=(FONT_UI, 9),
            state="readonly",
        )
        dd.pack(pady=4)

        def _on_select(_: Any) -> None:
            name_sel = var.get()
            try:
                code_sel = codes[display_names.index(name_sel)]
            except (ValueError, IndexError):
                code_sel = name_sel
            self._save(key, code_sel)

        dd.bind("<<ComboboxSelected>>", _on_select)
        object.__setattr__(row, "_lang_combo", dd)
        object.__setattr__(row, "_lang_var", var)

    def _on_search_change(self, *_: Any) -> None:
        q = self._search_var.get().strip().lower()

        if q:
            self._clear_btn.pack(side="right")
        else:
            self._clear_btn.pack_forget()

        if q:
            self._cat_panel.pack_forget()
        else:
            self._cat_panel.pack(side="left", fill="y",
                                 before=self._right_pane)

        self._search_debounce(q)

    _search_after_id: Any = None

    def _init_search_debounce(self) -> None:
        self._search_debounce = debounce_fn(self, self._apply_filter, ms=250)

    def _apply_filter(self, q: str) -> None:
        """Show/hide rows and separators based on query - batched via BatchPacker."""
        batcher = BatchPacker(self._content)

        if not q:
            for info in self._all_rows:
                w = info["widget"]
                is_sep = info.get("is_sep", False)
                kw = {"fill": "x", "pady": (16, 0) if is_sep else 0, "padx": 0}
                batcher.show(w, **kw)
            batcher.flush()
            return

        cats_with_matches: set[str] = set()
        for info in self._all_rows:
            if not info.get("is_sep") and q in info.get("search_key", ""):
                cats_with_matches.add(info.get("cat_key") or "")

        for info in self._all_rows:
            w = info["widget"]
            is_sep = info.get("is_sep", False)
            ck = info.get("cat_key") or ""
            if is_sep:
                if ck in cats_with_matches:
                    batcher.show(w, fill="x", pady=(16, 0), padx=0)
                else:
                    batcher.hide(w)
            elif q in info.get("search_key", ""):
                batcher.show(w, fill="x", pady=0, padx=0)
            else:
                batcher.hide(w)
        batcher.flush()

    def _bind_scroll(self, widget: tk.Widget) -> None:
        def _on_wheel(e: Any) -> None:
            if e.delta:
                self._canvas.yview_scroll(-1 * (e.delta // 120), "units")
            elif e.num == 4:
                self._canvas.yview_scroll(-1, "units")
            else:
                self._canvas.yview_scroll(1, "units")

        widget.bind("<MouseWheel>", _on_wheel, add=True)
        widget.bind("<Button-4>", _on_wheel, add=True)
        widget.bind("<Button-5>", _on_wheel, add=True)

    def _save(self, key: str, value: Any) -> None:
        self._cfg.set(key, value)
        if self._on_change:
            self._on_change(key, value)

    def _reset(self, key: str, default: Any) -> None:
        self._cfg.set(key, default)
        if self._on_change:
            self._on_change(key, default)
        yview = self._canvas.yview()
        for w in self._content.winfo_children():
            w.destroy()
        self._all_rows.clear()
        self._build_rows()
        self._content.update_idletasks()
        self._canvas.yview_moveto(yview[0])
        if self._filter_q:
            self._apply_filter(self._filter_q)

    def _refresh_language_combo(self) -> None:
        labels = _get_language_labels()
        codes = _get_language_options()
        names = [labels.get(c, c) for c in codes]
        cur = self._cfg.get("language", "pt-BR")
        cur_nm = labels.get(str(cur), str(cur))

        for info in self._all_rows:
            w = info["widget"]
            combo = getattr(w, "_lang_combo", None)
            var = getattr(w, "_lang_var", None)
            if combo is None:
                continue
            with contextlib.suppress(tk.TclError):
                combo.config(values=names)
                var.set(cur_nm)

    def _open_json(self, _event: Any = None) -> None:
        p = CONFIG_DIR / "config.json"
        if self._on_change:
            self._on_change("_open_json", str(p))

    def apply_theme(self, t: Theme) -> None:
        """Aplica o tema ``t`` ao editor de configurações.

        Se a UI ainda não foi construída, nada a refazer (será criada já com o
        tema atual). Se estiver oculta, o rebuild é adiado até o próximo
        ``<Map>`` para evitar recriar ~140 widgets sem o usuário ver.
        """
        self.config(bg=t.editor_bg)
        if not self._built:
            return
        if not self.winfo_ismapped():
            self._theme_stale = True
            return
        self._theme_stale = False
        self._rebuild_for_theme()


def _set_bg_recursive(widget: tk.Widget, color: str) -> None:
    """Aplica bg recursivamente a todos os filhos (para hover highlight)."""
    with contextlib.suppress(tk.TclError, AttributeError):
        widget.config(bg=color)
    for child in widget.winfo_children():
        _set_bg_recursive(child, color)
