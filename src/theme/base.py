"""
Codis - Sistema de Temas

Define o dataclass ``Theme`` e todas as definições de temas built-in.

Estrutura do Tema
Cada tema é composto por grupos de cores com funções bem definidas:

  Grupo           Atributo(s)                       Descrição
  -------------   ---------------------------------     ----------------------
  Janela          bg, fg, fg_muted                  Superfície principal
  Editor          editor_bg, editor_fg, cursor       Área de código
  Sidebar         sidebar_bg, activity_bg            Painel lateral
  Tabs            tabbar_bg, tab_act_bg/fg           Barra de abas
                  tab_in_fg
  Painéis         panel_bg, floating_bg             Bottom/popup panels
  Seleção         sel_bg, sel_fg                    Texto selecionado
  Inputs          input_bg, border                  Campos e divisores
  Botões          btn_bg, btn_hover, btn_primary     Botões normais / CTA
                  btn_primary_fg, btn_fg
  Scrollbar       scroll_bg, scroll_thumb           Scrollbars
  Diagnósticos    ok_fg, warn_fg, err_fg            Status: ok/warn/erro
  Syntax          t_keyword, t_string, t_comment    Highlight de código
                  t_number, t_func, t_type
                  t_operator, t_decorator
  Metadados       name, dark                         Nome e polaridade

Adicionando Temas
  1. Crie um ``Theme(name="meu_tema", ...)`` dentro de ``_define_themes()``.
  2. Adicione ``name`` em ``THEME_ORDER`` na posição desejada.
  3. Opcional: ajuste ``t_*`` para as cores de syntax do tema.
  4. Execute o Codis - o tema aparecerá em View -> Theme.

garante que atributos opcionais possuam valores fallback seguros.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Theme:
    """
    Representa um tema visual completo do Codis.

    Campos marcados com ``field(default=...)`` são preenchidos automaticamente
    por ``_patch_all_themes()`` caso omitidos na definição.

    Parameters
    ----------
    name : str
        Identificador único do tema (snake_case). Deve ser idêntico à chave
        usada em ``THEMES``.
    dark : bool
        ``True`` para temas escuros, ``False`` para temas claros. Afeta
        lógica de contraste em componentes como o editor K-UI.

    Color Slots
    -----------
    bg / fg / fg_muted
        Cores da superfície principal da janela.
    editor_bg / cursor
        Fundo do texto editável e cursor de texto.
    sidebar_bg / activity_bg
        Sidebar (explorador, git, etc.) e barra de atividade (ícones).
    tabbar_bg / tab_act_bg / tab_act_fg / tab_in_fg
        Barra de abas: fundo, aba ativa (bg e fg), aba inativa (fg).
    panel_bg / floating_bg
        Painéis inferiores (terminal, output) e janelas flutuantes (dialogs).
    sel_bg / sel_fg
        Destaque de texto selecionado.
    input_bg / border
        Campos de entrada e linhas divisórias.
    btn_bg / btn_hover / btn_fg
        Botão padrão: fundo, hover, texto.
    btn_primary / btn_primary_fg
        Botão de ação principal (CTA) e seu texto.
    scroll_bg / scroll_thumb
        Trilho e polegar dos scrollbars.
    ok_fg / warn_fg / err_fg
        Cores semânticas para mensagens de status.
    accent
        Cor de destaque usada em indicadores, badges e links.
    t_keyword / t_string / t_comment / t_number / t_func / t_type
    t_operator / t_decorator
        Paleta de syntax highlighting.
    """

    name: str = "unnamed"
    dark: bool = True

    bg: str = "#1e1e2e"
    fg: str = "#cdd6f4"
    fg_muted: str = "#6c7086"

    editor_bg: str = "#1e1e2e"
    cursor: str = "#f5e0dc"

    sidebar_bg: str = "#181825"
    activity_bg: str = "#11111b"

    tabbar_bg: str = "#181825"
    tab_act_bg: str = "#1e1e2e"
    tab_act_fg: str = "#cdd6f4"
    tab_in_fg: str = "#6c7086"

    panel_bg: str = "#181825"
    floating_bg: str = "#1e1e2e"

    sel_bg: str = "#313244"
    sel_fg: str = "#cdd6f4"

    input_bg: str = "#313244"
    border: str = "#45475a"

    btn_bg: str = "#313244"
    btn_hover: str = "#45475a"
    btn_fg: str = "#cdd6f4"
    btn_primary: str = "#89b4fa"
    btn_primary_fg: str = "#1e1e2e"

    scroll_bg: str = "#181825"
    scroll_thumb: str = "#45475a"

    ok_fg: str = "#a6e3a1"
    warn_fg: str = "#f9e2af"
    err_fg: str = "#f38ba8"

    accent: str = "#89b4fa"

    t_keyword: str = "#cba6f7"
    t_string: str = "#a6e3a1"
    t_comment: str = "#585b70"
    t_number: str = "#fab387"
    t_func: str = "#89b4fa"
    t_type: str = "#f38ba8"
    t_operator: str = "#89dceb"
    t_decorator: str = "#f9e2af"
    t_class: str = field(default="")
    t_variable: str = field(default="")
    t_link: str = field(default="")

    statusbar_bg: str = field(default="")
    statusbar_fg: str = field(default="")
    statusbar_sep: str = field(default="")
    statusbar_hover: str = field(default="")

    actbar_bg: str = field(default="")
    actbar_fg: str = field(default="")
    actbar_active_bg: str = field(default="")
    actbar_active_fg: str = field(default="")

    sidebar_fg: str = field(default="")
    sidebar_header_fg: str = field(default="")

    breadcrumb_bg: str = field(default="")
    breadcrumb_fg: str = field(default="")
    breadcrumb_sep: str = field(default="")
    breadcrumb_active: str = field(default="")

    tab_bg: str = field(default="")
    tab_fg: str = field(default="")
    tab_in_bg: str = field(default="")
    tab_active_bg: str = field(default="")
    tab_active_fg: str = field(default="")
    tab_act_border: str = field(default="")

    gutter_bg: str = field(default="")
    gutter_fg: str = field(default="")
    gutter_active_fg: str = field(default="")

    minimap_bg: str = field(default="")
    minimap_viewport: str = field(default="")
    minimap_find: str = field(default="")

    terminal_bg: str = field(default="")
    terminal_fg: str = field(default="")

    line_hl: str = field(default="")
    floating_border: str = field(default="")
    comment_fg: str = field(default="")

    scroll_hover: str = field(default="")

    btn_active: str = field(default="")

    err_bg: str = field(default="")
    warn_bg: str = field(default="")

    fg_dim: str = field(default="")
    hint_fg: str = field(default="")
    info_fg: str = field(default="")

    diff_add_bg: str = field(default="")
    diff_add_fg: str = field(default="")
    diff_del_bg: str = field(default="")
    diff_del_fg: str = field(default="")
    diff_change_bg: str = field(default="")
    diff_change_fg: str = field(default="")
    diff_hunk_bg: str = field(default="")
    diff_hunk_fg: str = field(default="")

    overlay_bg: str = field(default="")
    border_focus: str = field(default="")
    line_hl2: str = field(default="")
    ruler_color: str = field(default="")
    indent_guide: str = field(default="")
    whitespace: str = field(default="")
    input_border: str = field(default="")
    tab_hover_bg: str = field(default="")
    tab_modified: str = field(default="")
    info_bg: str = field(default="")

    status_bg: str = field(default="")
    status_fg: str = field(default="")
    status_err_bg: str = field(default="")
    status_err_fg: str = field(default="")
    status_warn_bg: str = field(default="")
    status_warn_fg: str = field(default="")

    minimap_err: str = field(default="")
    minimap_warn: str = field(default="")

    breadcrumb_active_fg: str = field(default="")

    t_builtin: str = field(default="")
    t_tag: str = field(default="")
    t_attr: str = field(default="")
    t_heading: str = field(default="")
    t_regex: str = field(default="")
    t_constant: str = field(default="")
    t_escape: str = field(default="")
    t_embedded: str = field(default="")
    t_invalid: str = field(default="")
    t_namespace: str = field(default="")

    corner_radius: int = field(default=0)
    use_blur: bool = field(default=False)
    blur_alpha: float = field(default=1.0)
    shadow_color: str = field(default="")
    floating_shadow: str = field(default="")

    activity_icon_active: str = field(default="")
    activity_icon_inactive: str = field(default="")
    activity_active_indicator: str = field(default="")

    panel_header_bg: str = field(default="")
    card_selected_bg: str = field(default="")
    input_focus_border: str = field(default="")
    shimmer_bg: str = field(default="")
    list_hover_bg: str = field(default="")
    settings_active_bg: str = field(default="")


THEMES: dict[str, Theme] = {}
THEME_ORDER: list[str] = []


def _define_themes() -> None:
    """

    Chame esta função uma única vez durante a inicialização (antes de criar
    qualquer widget). A função é idempotente - chamadas subsequentes são
    ignoradas se ``THEMES`` já estiver populado.

    Notas para contribuidores
    * Prefira definir **todas** as cores de syntax (``t_*``) para garantir
      contraste adequado em cada tema.
    * Temas claros devem ter ``dark=False`` e bg/fg invertidos.
    * Use ``THEME_ORDER`` para controlar a ordem no menu View -> Theme.
    """
    if THEMES:
        return

    _add(
        Theme(
            name="catppuccin_mocha",
            dark=True,
            bg="#1e1e2e",
            fg="#cdd6f4",
            fg_muted="#6c7086",
            editor_bg="#1e1e2e",
            cursor="#f5e0dc",
            sidebar_bg="#181825",
            activity_bg="#11111b",
            tabbar_bg="#181825",
            tab_act_bg="#1e1e2e",
            tab_act_fg="#cdd6f4",
            tab_in_fg="#6c7086",
            panel_bg="#181825",
            floating_bg="#1e1e2e",
            sel_bg="#585b70",
            sel_fg="#cdd6f4",
            input_bg="#313244",
            border="#45475a",
            btn_bg="#313244",
            btn_hover="#45475a",
            btn_fg="#cdd6f4",
            btn_primary="#89b4fa",
            btn_primary_fg="#1e1e2e",
            scroll_bg="#181825",
            scroll_thumb="#45475a",
            ok_fg="#a6e3a1",
            warn_fg="#f9e2af",
            err_fg="#f38ba8",
            accent="#89b4fa",
            t_keyword="#cba6f7",
            t_string="#a6e3a1",
            t_comment="#585b70",
            t_number="#fab387",
            t_func="#89b4fa",
            t_type="#f38ba8",
            t_operator="#89dceb",
            t_decorator="#f9e2af",
        )
    )

    _add(
        Theme(
            name="fluent_dark",
            dark=True,
            bg="#1f1f1f",
            fg="#d4d4d4",
            fg_muted="#858585",
            editor_bg="#1e1e1e",
            cursor="#aeafad",
            sidebar_bg="#252526",
            activity_bg="#333333",
            tabbar_bg="#252526",
            tab_act_bg="#1e1e1e",
            tab_act_fg="#ffffff",
            tab_in_fg="#969696",
            panel_bg="#1e1e1e",
            floating_bg="#252526",
            sel_bg="#264f78",
            sel_fg="#d4d4d4",
            input_bg="#3c3c3c",
            border="#454545",
            btn_bg="#3a3d41",
            btn_hover="#505152",
            btn_fg="#cccccc",
            btn_primary="#0e639c",
            btn_primary_fg="#ffffff",
            scroll_bg="#1e1e1e",
            scroll_thumb="#424242",
            ok_fg="#89d185",
            warn_fg="#cca700",
            err_fg="#f48771",
            accent="#569cd6",
            t_keyword="#569cd6",
            t_string="#ce9178",
            t_comment="#6a9955",
            t_number="#b5cea8",
            t_func="#dcdcaa",
            t_type="#4ec9b0",
            t_operator="#d4d4d4",
            t_decorator="#c586c0",
        )
    )

    _add(
        Theme(
            name="fluent_light",
            dark=False,
            bg="#f3f3f3",
            fg="#1e1e1e",
            fg_muted="#717171",
            editor_bg="#ffffff",
            cursor="#000000",
            sidebar_bg="#f3f3f3",
            activity_bg="#2c2c2c",
            tabbar_bg="#ececec",
            tab_act_bg="#ffffff",
            tab_act_fg="#000000",
            tab_in_fg="#717171",
            panel_bg="#f5f5f5",
            floating_bg="#ffffff",
            sel_bg="#add6ff",
            sel_fg="#000000",
            input_bg="#ffffff",
            border="#d0d0d0",
            btn_bg="#e7e7e7",
            btn_hover="#d4d4d4",
            btn_fg="#1e1e1e",
            btn_primary="#0078d4",
            btn_primary_fg="#ffffff",
            scroll_bg="#f5f5f5",
            scroll_thumb="#c2c2c2",
            ok_fg="#14a02e",
            warn_fg="#b5700c",
            err_fg="#c72e0f",
            accent="#0078d4",
            t_keyword="#0000ff",
            t_string="#a31515",
            t_comment="#008000",
            t_number="#098658",
            t_func="#795e26",
            t_type="#267f99",
            t_operator="#1e1e1e",
            t_decorator="#af00db",
        )
    )

    _add(
        Theme(
            name="tokyo_night",
            dark=True,
            bg="#1a1b26",
            fg="#a9b1d6",
            fg_muted="#565f89",
            editor_bg="#1a1b26",
            cursor="#c0caf5",
            sidebar_bg="#16161e",
            activity_bg="#13131a",
            tabbar_bg="#15161e",
            tab_act_bg="#1a1b26",
            tab_act_fg="#c0caf5",
            tab_in_fg="#565f89",
            panel_bg="#16161e",
            floating_bg="#1f2335",
            sel_bg="#3d59a1",
            sel_fg="#c0caf5",
            input_bg="#1f2335",
            border="#292e42",
            btn_bg="#292e42",
            btn_hover="#3b4261",
            btn_fg="#a9b1d6",
            btn_primary="#7aa2f7",
            btn_primary_fg="#1a1b26",
            scroll_bg="#16161e",
            scroll_thumb="#3b4261",
            ok_fg="#9ece6a",
            warn_fg="#e0af68",
            err_fg="#f7768e",
            accent="#7aa2f7",
            t_keyword="#bb9af7",
            t_string="#9ece6a",
            t_comment="#565f89",
            t_number="#ff9e64",
            t_func="#7aa2f7",
            t_type="#2ac3de",
            t_operator="#89ddff",
            t_decorator="#e0af68",
        )
    )

    _add(
        Theme(
            name="dracula",
            dark=True,
            bg="#282a36",
            fg="#f8f8f2",
            fg_muted="#6272a4",
            editor_bg="#282a36",
            cursor="#f8f8f0",
            sidebar_bg="#21222c",
            activity_bg="#191a21",
            tabbar_bg="#21222c",
            tab_act_bg="#282a36",
            tab_act_fg="#f8f8f2",
            tab_in_fg="#6272a4",
            panel_bg="#21222c",
            floating_bg="#21222c",
            sel_bg="#44475a",
            sel_fg="#f8f8f2",
            input_bg="#44475a",
            border="#6272a4",
            btn_bg="#44475a",
            btn_hover="#6272a4",
            btn_fg="#f8f8f2",
            btn_primary="#bd93f9",
            btn_primary_fg="#282a36",
            scroll_bg="#21222c",
            scroll_thumb="#6272a4",
            ok_fg="#50fa7b",
            warn_fg="#ffb86c",
            err_fg="#ff5555",
            accent="#bd93f9",
            t_keyword="#ff79c6",
            t_string="#f1fa8c",
            t_comment="#6272a4",
            t_number="#bd93f9",
            t_func="#50fa7b",
            t_type="#8be9fd",
            t_operator="#ff79c6",
            t_decorator="#ffb86c",
        )
    )

    _add(
        Theme(
            name="gruvbox_dark",
            dark=True,
            bg="#282828",
            fg="#ebdbb2",
            fg_muted="#928374",
            editor_bg="#282828",
            cursor="#ebdbb2",
            sidebar_bg="#1d2021",
            activity_bg="#1b1b1b",
            tabbar_bg="#1d2021",
            tab_act_bg="#282828",
            tab_act_fg="#ebdbb2",
            tab_in_fg="#928374",
            panel_bg="#1d2021",
            floating_bg="#32302f",
            sel_bg="#504945",
            sel_fg="#ebdbb2",
            input_bg="#3c3836",
            border="#504945",
            btn_bg="#3c3836",
            btn_hover="#504945",
            btn_fg="#ebdbb2",
            btn_primary="#d79921",
            btn_primary_fg="#282828",
            scroll_bg="#1d2021",
            scroll_thumb="#504945",
            ok_fg="#b8bb26",
            warn_fg="#fabd2f",
            err_fg="#fb4934",
            accent="#d79921",
            t_keyword="#fb4934",
            t_string="#b8bb26",
            t_comment="#928374",
            t_number="#d3869b",
            t_func="#fabd2f",
            t_type="#8ec07c",
            t_operator="#fe8019",
            t_decorator="#fabd2f",
        )
    )

    _add(
        Theme(
            name="gruvbox_light",
            dark=False,
            bg="#fbf1c7",
            fg="#3c3836",
            fg_muted="#6a5f55",
            editor_bg="#fbf1c7",
            cursor="#3c3836",
            sidebar_bg="#f2e5bc",
            activity_bg="#d5c4a1",
            tabbar_bg="#ebdbb2",
            tab_act_bg="#fbf1c7",
            tab_act_fg="#3c3836",
            tab_in_fg="#7c6f64",
            panel_bg="#f2e5bc",
            floating_bg="#ebdbb2",
            sel_bg="#d5c4a1",
            sel_fg="#3c3836",
            input_bg="#ebdbb2",
            border="#bdae93",
            btn_bg="#d5c4a1",
            btn_hover="#bdae93",
            btn_fg="#3c3836",
            btn_primary="#af3a03",
            btn_primary_fg="#fbf1c7",
            scroll_bg="#f2e5bc",
            scroll_thumb="#bdae93",
            ok_fg="#79740e",
            warn_fg="#b57614",
            err_fg="#9d0006",
            accent="#af3a03",
            t_keyword="#9d0006",
            t_string="#79740e",
            t_comment="#928374",
            t_number="#8f3f71",
            t_func="#b57614",
            t_type="#427b58",
            t_operator="#af3a03",
            t_decorator="#b57614",
        )
    )

    _add(
        Theme(
            name="solarized_dark",
            dark=True,
            bg="#002b36",
            fg="#839496",
            fg_muted="#586e75",
            editor_bg="#002b36",
            cursor="#93a1a1",
            sidebar_bg="#073642",
            activity_bg="#001e26",
            tabbar_bg="#073642",
            tab_act_bg="#002b36",
            tab_act_fg="#93a1a1",
            tab_in_fg="#586e75",
            panel_bg="#073642",
            floating_bg="#073642",
            sel_bg="#2a5068",
            sel_fg="#fdf6e3",
            input_bg="#073642",
            border="#586e75",
            btn_bg="#073642",
            btn_hover="#094251",
            btn_fg="#839496",
            btn_primary="#268bd2",
            btn_primary_fg="#002b36",
            scroll_bg="#002b36",
            scroll_thumb="#586e75",
            ok_fg="#859900",
            warn_fg="#b58900",
            err_fg="#dc322f",
            accent="#268bd2",
            t_keyword="#859900",
            t_string="#2aa198",
            t_comment="#586e75",
            t_number="#d33682",
            t_func="#268bd2",
            t_type="#6c71c4",
            t_operator="#93a1a1",
            t_decorator="#b58900",
        )
    )

    _add(
        Theme(
            name="solarized_light",
            dark=False,
            bg="#fdf6e3",
            fg="#586e75",
            fg_muted="#73848a",
            editor_bg="#fdf6e3",
            cursor="#073642",
            sidebar_bg="#eee8d5",
            activity_bg="#ddd6c1",
            tabbar_bg="#eee8d5",
            tab_act_bg="#fdf6e3",
            tab_act_fg="#586e75",
            tab_in_fg="#93a1a1",
            panel_bg="#eee8d5",
            floating_bg="#fdf6e3",
            sel_bg="#eee8d5",
            sel_fg="#586e75",
            input_bg="#eee8d5",
            border="#c0bfaa",
            btn_bg="#eee8d5",
            btn_hover="#ddd6c1",
            btn_fg="#657b83",
            btn_primary="#268bd2",
            btn_primary_fg="#fdf6e3",
            scroll_bg="#eee8d5",
            scroll_thumb="#c0bfaa",
            ok_fg="#859900",
            warn_fg="#b58900",
            err_fg="#dc322f",
            accent="#268bd2",
            t_keyword="#859900",
            t_string="#2aa198",
            t_comment="#93a1a1",
            t_number="#d33682",
            t_func="#268bd2",
            t_type="#6c71c4",
            t_operator="#657b83",
            t_decorator="#b58900",
        )
    )

    _add(
        Theme(
            name="one_dark_pro",
            dark=True,
            bg="#282c34",
            fg="#abb2bf",
            fg_muted="#5c6370",
            editor_bg="#282c34",
            cursor="#528bff",
            sidebar_bg="#21252b",
            activity_bg="#1c2026",
            tabbar_bg="#21252b",
            tab_act_bg="#282c34",
            tab_act_fg="#abb2bf",
            tab_in_fg="#5c6370",
            panel_bg="#21252b",
            floating_bg="#21252b",
            sel_bg="#4a5568",
            sel_fg="#abb2bf",
            input_bg="#3e4451",
            border="#3e4451",
            btn_bg="#3e4451",
            btn_hover="#4b5363",
            btn_fg="#abb2bf",
            btn_primary="#61afef",
            btn_primary_fg="#282c34",
            scroll_bg="#21252b",
            scroll_thumb="#4b5363",
            ok_fg="#98c379",
            warn_fg="#e5c07b",
            err_fg="#e06c75",
            accent="#61afef",
            t_keyword="#c678dd",
            t_string="#98c379",
            t_comment="#5c6370",
            t_number="#d19a66",
            t_func="#61afef",
            t_type="#e06c75",
            t_operator="#56b6c2",
            t_decorator="#e5c07b",
        )
    )

    _add(
        Theme(
            name="nord",
            dark=True,
            bg="#2e3440",
            fg="#d8dee9",
            fg_muted="#4c566a",
            editor_bg="#2e3440",
            cursor="#d8dee9",
            sidebar_bg="#272c36",
            activity_bg="#1e2229",
            tabbar_bg="#272c36",
            tab_act_bg="#2e3440",
            tab_act_fg="#eceff4",
            tab_in_fg="#4c566a",
            panel_bg="#272c36",
            floating_bg="#3b4252",
            sel_bg="#5e81ac",
            sel_fg="#eceff4",
            input_bg="#3b4252",
            border="#434c5e",
            btn_bg="#3b4252",
            btn_hover="#434c5e",
            btn_fg="#d8dee9",
            btn_primary="#5e81ac",
            btn_primary_fg="#eceff4",
            scroll_bg="#272c36",
            scroll_thumb="#434c5e",
            ok_fg="#a3be8c",
            warn_fg="#ebcb8b",
            err_fg="#bf616a",
            accent="#88c0d0",
            t_keyword="#81a1c1",
            t_string="#a3be8c",
            t_comment="#4c566a",
            t_number="#b48ead",
            t_func="#88c0d0",
            t_type="#8fbcbb",
            t_operator="#81a1c1",
            t_decorator="#ebcb8b",
        )
    )

    _add(
        Theme(
            name="monokai_pro",
            dark=True,
            bg="#2d2a2e",
            fg="#fcfcfa",
            fg_muted="#727072",
            editor_bg="#2d2a2e",
            cursor="#fcfcfa",
            sidebar_bg="#221f22",
            activity_bg="#19181a",
            tabbar_bg="#221f22",
            tab_act_bg="#2d2a2e",
            tab_act_fg="#fcfcfa",
            tab_in_fg="#727072",
            panel_bg="#221f22",
            floating_bg="#2d2a2e",
            sel_bg="#6d5f7a",
            sel_fg="#fcfcfa",
            input_bg="#403e41",
            border="#5b595c",
            btn_bg="#403e41",
            btn_hover="#5b595c",
            btn_fg="#fcfcfa",
            btn_primary="#ff6188",
            btn_primary_fg="#2d2a2e",
            scroll_bg="#221f22",
            scroll_thumb="#5b595c",
            ok_fg="#a9dc76",
            warn_fg="#ffd866",
            err_fg="#ff6188",
            accent="#ff6188",
            t_keyword="#ff6188",
            t_string="#ffd866",
            t_comment="#727072",
            t_number="#ab9df2",
            t_func="#a9dc76",
            t_type="#78dce8",
            t_operator="#ff6188",
            t_decorator="#fc9867",
        )
    )

    _add(
        Theme(
            name="github_dark",
            dark=True,
            bg="#0d1117",
            fg="#c9d1d9",
            fg_muted="#8b949e",
            editor_bg="#0d1117",
            cursor="#58a6ff",
            sidebar_bg="#161b22",
            activity_bg="#0d1117",
            tabbar_bg="#161b22",
            tab_act_bg="#0d1117",
            tab_act_fg="#c9d1d9",
            tab_in_fg="#8b949e",
            panel_bg="#161b22",
            floating_bg="#161b22",
            sel_bg="#1f6feb",
            sel_fg="#c9d1d9",
            input_bg="#21262d",
            border="#30363d",
            btn_bg="#21262d",
            btn_hover="#30363d",
            btn_fg="#c9d1d9",
            btn_primary="#238636",
            btn_primary_fg="#ffffff",
            scroll_bg="#0d1117",
            scroll_thumb="#30363d",
            ok_fg="#3fb950",
            warn_fg="#d29922",
            err_fg="#f85149",
            accent="#58a6ff",
            t_keyword="#ff7b72",
            t_string="#a5d6ff",
            t_comment="#8b949e",
            t_number="#79c0ff",
            t_func="#d2a8ff",
            t_type="#ffa657",
            t_operator="#ff7b72",
            t_decorator="#d2a8ff",
        )
    )

    _add(
        Theme(
            name="github_light",
            dark=False,
            bg="#ffffff",
            fg="#24292e",
            fg_muted="#6a737d",
            editor_bg="#ffffff",
            cursor="#24292e",
            sidebar_bg="#f6f8fa",
            activity_bg="#ffffff",
            tabbar_bg="#f6f8fa",
            tab_act_bg="#ffffff",
            tab_act_fg="#24292e",
            tab_in_fg="#6a737d",
            panel_bg="#f6f8fa",
            floating_bg="#ffffff",
            sel_bg="#0366d6",
            sel_fg="#ffffff",
            input_bg="#ffffff",
            border="#e1e4e8",
            btn_bg="#eff3f6",
            btn_hover="#e1e4e8",
            btn_fg="#24292e",
            btn_primary="#2ea44f",
            btn_primary_fg="#ffffff",
            scroll_bg="#f6f8fa",
            scroll_thumb="#c1c8cf",
            ok_fg="#2ea44f",
            warn_fg="#b08800",
            err_fg="#cb2431",
            accent="#0366d6",
            t_keyword="#d73a49",
            t_string="#032f62",
            t_comment="#6a737d",
            t_number="#005cc5",
            t_func="#6f42c1",
            t_type="#e36209",
            t_operator="#d73a49",
            t_decorator="#6f42c1",
        )
    )

    _add(
        Theme(
            name="palenight",
            dark=True,
            bg="#292d3e",
            fg="#a6accd",
            fg_muted="#676e95",
            editor_bg="#292d3e",
            cursor="#ffcb6b",
            sidebar_bg="#242635",
            activity_bg="#1b1c2a",
            tabbar_bg="#242635",
            tab_act_bg="#292d3e",
            tab_act_fg="#ffffff",
            tab_in_fg="#8891b6",
            panel_bg="#242635",
            floating_bg="#32364a",
            sel_bg="#5c6bc0",
            sel_fg="#ffffff",
            input_bg="#32364a",
            border="#3d425b",
            btn_bg="#3d425b",
            btn_hover="#4d527b",
            btn_fg="#a6accd",
            btn_primary="#82aaff",
            btn_primary_fg="#292d3e",
            scroll_bg="#242635",
            scroll_thumb="#3d425b",
            ok_fg="#c3e88d",
            warn_fg="#ffcb6b",
            err_fg="#f07178",
            accent="#82aaff",
            t_keyword="#c792ea",
            t_string="#c3e88d",
            t_comment="#676e95",
            t_number="#f78c6c",
            t_func="#82aaff",
            t_type="#ffcb6b",
            t_operator="#89ddff",
            t_decorator="#ffcb6b",
        )
    )

    _add(
        Theme(
            name="rose_pine",
            dark=True,
            bg="#191724",
            fg="#e0def4",
            fg_muted="#6e6a86",
            editor_bg="#1f1d2e",
            cursor="#e0def4",
            sidebar_bg="#191724",
            activity_bg="#110f1a",
            tabbar_bg="#191724",
            tab_act_bg="#1f1d2e",
            tab_act_fg="#e0def4",
            tab_in_fg="#6e6a86",
            panel_bg="#191724",
            floating_bg="#26233a",
            sel_bg="#403d52",
            sel_fg="#e0def4",
            input_bg="#26233a",
            border="#403d52",
            btn_bg="#26233a",
            btn_hover="#403d52",
            btn_fg="#e0def4",
            btn_primary="#eb6f92",
            btn_primary_fg="#191724",
            scroll_bg="#191724",
            scroll_thumb="#403d52",
            ok_fg="#31748f",
            warn_fg="#f6c177",
            err_fg="#eb6f92",
            accent="#9ccfd8",
            t_keyword="#c4a7e7",
            t_string="#f6c177",
            t_comment="#6e6a86",
            t_number="#ebbcba",
            t_func="#9ccfd8",
            t_type="#eb6f92",
            t_operator="#31748f",
            t_decorator="#f6c177",
        )
    )

    _add(
        Theme(
            name="everforest_dark",
            dark=True,
            bg="#2d353b",
            fg="#d3c6aa",
            fg_muted="#7a8478",
            editor_bg="#272e33",
            cursor="#d3c6aa",
            sidebar_bg="#2d353b",
            activity_bg="#232a2e",
            tabbar_bg="#2d353b",
            tab_act_bg="#272e33",
            tab_act_fg="#d3c6aa",
            tab_in_fg="#7a8478",
            panel_bg="#2d353b",
            floating_bg="#3d484d",
            sel_bg="#3d484d",
            sel_fg="#d3c6aa",
            input_bg="#3d484d",
            border="#475258",
            btn_bg="#3d484d",
            btn_hover="#475258",
            btn_fg="#d3c6aa",
            btn_primary="#a7c080",
            btn_primary_fg="#2d353b",
            scroll_bg="#2d353b",
            scroll_thumb="#475258",
            ok_fg="#a7c080",
            warn_fg="#dbbc7f",
            err_fg="#e67e80",
            accent="#7fbbb3",
            t_keyword="#e67e80",
            t_string="#a7c080",
            t_comment="#7a8478",
            t_number="#d699b6",
            t_func="#7fbbb3",
            t_type="#83c092",
            t_operator="#e69875",
            t_decorator="#dbbc7f",
        )
    )

    _add(
        Theme(
            name="ayu_mirage",
            dark=True,
            bg="#1f2430",
            fg="#cbccc6",
            fg_muted="#5c6773",
            editor_bg="#1f2430",
            cursor="#ffcc66",
            sidebar_bg="#171c28",
            activity_bg="#0f1420",
            tabbar_bg="#171c28",
            tab_act_bg="#1f2430",
            tab_act_fg="#cbccc6",
            tab_in_fg="#5c6773",
            panel_bg="#171c28",
            floating_bg="#242b38",
            sel_bg="#4d6997",
            sel_fg="#cbccc6",
            input_bg="#242b38",
            border="#323a4c",
            btn_bg="#242b38",
            btn_hover="#323a4c",
            btn_fg="#cbccc6",
            btn_primary="#ffcc66",
            btn_primary_fg="#1f2430",
            scroll_bg="#171c28",
            scroll_thumb="#323a4c",
            ok_fg="#bae67e",
            warn_fg="#ffcc66",
            err_fg="#ff3333",
            accent="#73d0ff",
            t_keyword="#ffa759",
            t_string="#bae67e",
            t_comment="#5c6773",
            t_number="#d4bfff",
            t_func="#5ccfe6",
            t_type="#73d0ff",
            t_operator="#f29e74",
            t_decorator="#ffcc66",
        )
    )

    _add(
        Theme(
            name="midnight_void",
            dark=True,
            bg="#090909",
            fg="#e2e8f0",
            fg_muted="#6b7c93",
            editor_bg="#050505",
            cursor="#7c3aed",
            sidebar_bg="#0f0f0f",
            activity_bg="#030303",
            tabbar_bg="#0f0f0f",
            tab_act_bg="#090909",
            tab_act_fg="#e2e8f0",
            tab_in_fg="#475569",
            panel_bg="#0f0f0f",
            floating_bg="#141414",
            sel_bg="#5b21b6",
            sel_fg="#ffffff",
            input_bg="#141414",
            border="#1e293b",
            btn_bg="#141414",
            btn_hover="#1e293b",
            btn_fg="#e2e8f0",
            btn_primary="#7c3aed",
            btn_primary_fg="#ffffff",
            scroll_bg="#0f0f0f",
            scroll_thumb="#1e293b",
            ok_fg="#10b981",
            warn_fg="#f59e0b",
            err_fg="#ef4444",
            accent="#7c3aed",
            t_keyword="#a78bfa",
            t_string="#34d399",
            t_comment="#5a6a82",
            t_number="#fb923c",
            t_func="#60a5fa",
            t_type="#f472b6",
            t_operator="#22d3ee",
            t_decorator="#fbbf24",
        )
    )

    _add(
        Theme(
            name="sepia",
            dark=False,
            bg="#f1e7d0",
            fg="#433422",
            fg_muted="#7a6550",
            editor_bg="#f8f0e0",
            cursor="#433422",
            sidebar_bg="#e8d8b8",
            activity_bg="#d6c9a8",
            tabbar_bg="#e8d8b8",
            tab_act_bg="#f8f0e0",
            tab_act_fg="#433422",
            tab_in_fg="#9e8a6e",
            panel_bg="#e8d8b8",
            floating_bg="#f1e7d0",
            sel_bg="#c8b99a",
            sel_fg="#433422",
            input_bg="#f1e7d0",
            border="#c8b99a",
            btn_bg="#dcc9a8",
            btn_hover="#c8b99a",
            btn_fg="#433422",
            btn_primary="#7d5a1e",
            btn_primary_fg="#f8f0e0",
            scroll_bg="#e8d8b8",
            scroll_thumb="#c8b99a",
            ok_fg="#5a7a3a",
            warn_fg="#8a6a1e",
            err_fg="#8a1e1e",
            accent="#7d5a1e",
            t_keyword="#7d1e1e",
            t_string="#3a6a3a",
            t_comment="#9e8a6e",
            t_number="#6a3a7d",
            t_func="#1e4a7d",
            t_type="#7d4a1e",
            t_operator="#433422",
            t_decorator="#8a6a1e",
        )
    )

    _add(
        Theme(
            name="high_contrast",
            dark=True,
            bg="#000000",
            fg="#ffffff",
            fg_muted="#aaaaaa",
            editor_bg="#000000",
            cursor="#ffff00",
            sidebar_bg="#0a0a0a",
            activity_bg="#000000",
            tabbar_bg="#111111",
            tab_act_bg="#000000",
            tab_act_fg="#ffffff",
            tab_in_fg="#aaaaaa",
            panel_bg="#0a0a0a",
            floating_bg="#111111",
            sel_bg="#3355ff",
            sel_fg="#ffffff",
            input_bg="#111111",
            border="#555555",
            btn_bg="#222222",
            btn_hover="#333333",
            btn_fg="#ffffff",
            btn_primary="#ffff00",
            btn_primary_fg="#000000",
            scroll_bg="#000000",
            scroll_thumb="#555555",
            ok_fg="#00ff00",
            warn_fg="#ffff00",
            err_fg="#ff4444",
            accent="#00ffff",
            t_keyword="#ffff00",
            t_string="#00ff00",
            t_comment="#aaaaaa",
            t_number="#ff8800",
            t_func="#00ffff",
            t_type="#ff00ff",
            t_operator="#ffffff",
            t_decorator="#ffaa00",
        )
    )

    _apply_holographic_boost_all()


def _add(theme: Theme) -> None:
    """Registra um tema no dicionário global e na lista de ordem."""
    THEMES[theme.name] = theme
    if theme.name not in THEME_ORDER:
        THEME_ORDER.append(theme.name)


_HOLO_EXEMPT_THEMES = frozenset({"high_contrast"})

_HOLO_SURFACE_FIELDS = (
    "bg",
    "editor_bg",
    "sidebar_bg",
    "activity_bg",
    "tabbar_bg",
    "tab_act_bg",
    "panel_bg",
    "floating_bg",
    "sel_bg",
    "input_bg",
    "scroll_bg",
    "border",
    "btn_bg",
    "btn_hover",
)

_HOLO_ACCENT_FIELDS = (
    "accent",
    "btn_primary",
    "cursor",
    "ok_fg",
    "warn_fg",
    "err_fg",
)


def _apply_holographic_boost(t: Theme) -> None:
    """Aplica o boost holográfico 8K a um tema, in-place.

    Idempotente apenas na primeira chamada por instância: como altera
    valores já definidos (não é um "fill" de campos vazios), não deve
    ser chamada mais de uma vez sobre o mesmo objeto Theme, ou o boost
    seria acumulado. ``_define_themes()`` garante isso chamando uma vez
    por tema, imediatamente após o registro via ``_add()``.
    """
    from src.theme.utils import hex_glow, hex_vibrance

    if t.name in _HOLO_EXEMPT_THEMES:
        return

    surface_sat = 0.06
    surface_light = 0.015 if t.dark else -0.01
    for attr in _HOLO_SURFACE_FIELDS:
        val = getattr(t, attr, None)
        if val:
            setattr(t, attr, hex_vibrance(val, surface_sat, surface_light))

    for attr in _HOLO_ACCENT_FIELDS:
        val = getattr(t, attr, None)
        if val:
            setattr(t, attr, hex_vibrance(
                val, 0.10, 0.04 if t.dark else -0.02))

    if t.btn_primary:
        t.tab_act_border = hex_glow(t.btn_primary, 0.4)


def _apply_holographic_boost_all() -> None:
    """Aplica o boost holográfico a todos os temas já registrados em THEMES."""
    for theme in THEMES.values():
        _apply_holographic_boost(theme)
