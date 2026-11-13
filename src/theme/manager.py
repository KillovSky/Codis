"""Codis - gerenciamento de tema visual: ThemeManager e funções de patch TTK."""

from __future__ import annotations

import contextlib
import logging
import tkinter as tk
from collections.abc import Callable
from pathlib import Path as _Path
from typing import TYPE_CHECKING
from typing import Any as _Any

from src.theme.base import THEME_ORDER, THEMES, Theme

if TYPE_CHECKING:
    from tkinter import ttk

logger = logging.getLogger("Codis")

try:
    from tkinter import ttk as _ttk

    _TK_AVAILABLE = True
except ImportError:
    _TK_AVAILABLE = False


class ThemeManager:
    """Gerencia o tema visual ativo do Codis.

    Mantém referência ao tema atual, notifica callbacks ao trocar de tema,
    aplica estilos TTK via :meth:`apply_ttk` e rotaciona temas via :meth:`cycle`.

    Args:
        initial: Nome do tema inicial. Usa o primeiro disponível se não encontrado.

    Example::

        tmgr = ThemeManager("dracula")
        tmgr.on_change(lambda t: print(f"Tema: {t.name}"))
        tmgr.set("nord")
        tmgr.cycle()
    """

    def __init__(self, initial: str = "fluent_dark") -> None:
        """Inicializa o gerenciador com o tema ``initial``, caindo no primeiro disponível se ausente."""
        self._change_callbacks: list[Callable[[Theme], None]] = []
        name = (
            initial
            if initial in THEMES
            else (THEME_ORDER[0] if THEME_ORDER else "fluent_dark")
        )
        self.theme: Theme = THEMES.get(name, Theme())
        _patch_theme(self.theme)

    def set(self, name: str) -> None:
        """
        Se o nome não estiver registrado em ``THEMES``, emite aviso e mantém o atual.

        Args:
            name: Nome do tema registrado em ``THEMES``.
        """
        if name not in THEMES:
            logger.warning(
                "Tema '%s' não encontrado - mantendo '%s'.", name, self.theme.name
            )
            return
        self.theme = THEMES[name]
        _patch_theme(self.theme)
        self._notify()

    def cycle(self) -> None:
        """Avança para o próximo tema em ``THEME_ORDER`` de forma circular."""
        if not THEME_ORDER:
            return
        try:
            idx = THEME_ORDER.index(self.theme.name)
        except ValueError:
            idx = -1
        next_name = THEME_ORDER[(idx + 1) % len(THEME_ORDER)]
        self.set(next_name)

    def on_change(self, callback: Callable[[Theme], None]) -> None:
        """Registra um callback invocado com o novo ``Theme`` a cada troca de tema."""
        if callback not in self._change_callbacks:
            self._change_callbacks.append(callback)

    def apply_ttk(self) -> None:
        """Aplica o tema atual ao sistema de estilos TTK.

        Deve ser chamado após cada troca de tema para que widgets como
        ``ttk.Scrollbar``, ``ttk.Treeview`` e ``ttk.Combobox`` reflitam
        as cores corretas. Erros de estilo são suprimidos silenciosamente.
        """
        if not _TK_AVAILABLE:
            return
        t = self.theme
        with contextlib.suppress(tk.TclError):
            style = _ttk.Style()
            style.theme_use("clam")
            _apply_ttk_styles(style, t)

    def token_map(self) -> dict:
        """
        de sintaxe usados pelo SyntaxHL / LANG_RE.

        ``src/syntax/patterns.py`` e faz fallback seguro para atributos
        do tema quando um token não tem cor própria.
        """
        t = self.theme
        return {
            "keyword": t.t_keyword,
            "type_kw": t.t_type,
            "string": t.t_string,
            "string_tmpl": t.t_string,
            "number": t.t_number,
            "operator": t.t_operator,
            "comment_line": t.t_comment,
            "comment_block": t.t_comment,
            "decorator": t.t_decorator,
            "func_def": t.t_func,
            "func_call": t.t_func,
            "class_def": t.t_keyword,
            "class_name": t.t_type,
            "variable": t.t_variable or t.fg,
            "builtin": t.t_builtin or t.t_func,
            "tag_open": t.t_tag or t.t_keyword,
            "attr_name": t.t_attr or t.t_func,
            "doctype": t.t_comment,
            "heading": t.t_heading or t.t_keyword,
            "bold": t.t_func,
            "italic": t.t_string,
            "blockquote": t.t_comment,
            "table": t.t_type,
            "link": t.t_link or t.accent,
            "bool": t.t_constant or t.t_number,
            "escape": t.t_escape or t.t_string,
            "regexp": t.t_regex or t.t_string,
            "directive": t.t_keyword,
            "section": t.t_heading or t.t_keyword,
            "shebang": t.t_comment,
            "storage": t.t_keyword,
        }

    def _notify(self) -> None:
        """Notifica todos os callbacks registrados sobre mudança de tema."""
        for cb in list(self._change_callbacks):
            try:
                cb(self.theme)
            except Exception as exc:
                logger.warning(
                    "Callback de tema falhou (%s): %s", getattr(
                        cb, "__name__", cb), exc
                )


def _scaled_rowheight(base: int = 22) -> int:
    """Altura de linha do Treeview respeitando a escala global da UI."""
    from src.ui.ui_scale import px

    return px(base)


def _apply_ttk_styles(style: ttk.Style, t: Theme) -> None:
    """
    Configura estilos TTK completos para o tema fornecido.

    Cobre os seguintes widgets:
    * ``TScrollbar`` e ``Thin.Vertical.TScrollbar``
    * ``Treeview`` e ``Treeview.Heading``
    * ``TCombobox`` (inclui fix de dropdown)
    * ``TEntry``
    * ``TNotebook`` e ``TNotebook.Tab``
    * ``TSeparator``
    * ``TFrame`` / ``TLabel`` / ``TButton``
    * ``TPanedwindow``

    Parameters
    ----------
    style : ttk.Style
        Instância do estilo TTK (já configurada com theme_use).
    t : Theme
        Tema ativo cujas cores serão aplicadas.
    """

    style.configure(
        "Vertical.TScrollbar",
        background=t.scroll_thumb,
        troughcolor=t.scroll_bg,
        borderwidth=0,
        arrowsize=0,
        relief="flat",
    )
    style.map(
        "Vertical.TScrollbar",
        background=[("active", t.accent), ("!active", t.scroll_thumb)],
    )

    style.configure(
        "Thin.Vertical.TScrollbar",
        background=t.scroll_thumb,
        troughcolor=t.scroll_bg,
        borderwidth=0,
        arrowsize=0,
        relief="flat",
        width=6,
    )
    style.map(
        "Thin.Vertical.TScrollbar",
        background=[("active", t.accent), ("!active", t.scroll_thumb)],
    )

    style.configure(
        "Treeview",
        background=t.sidebar_bg,
        foreground=t.fg,
        fieldbackground=t.sidebar_bg,
        borderwidth=0,
        relief="flat",
        rowheight=_scaled_rowheight(),
    )
    style.map(
        "Treeview",
        background=[("selected", t.sel_bg)],
        foreground=[("selected", t.sel_fg)],
    )
    style.configure(
        "Treeview.Heading",
        background=t.tabbar_bg,
        foreground=t.fg_muted,
        borderwidth=0,
        relief="flat",
    )
    style.map("Treeview.Heading", background=[("active", t.btn_hover)])

    style.configure(
        "TCombobox",
        fieldbackground=t.input_bg,
        background=t.btn_bg,
        foreground=t.fg,
        selectbackground=t.sel_bg,
        selectforeground=t.sel_fg,
        borderwidth=1,
        relief="flat",
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", t.input_bg)],
        selectbackground=[("readonly", t.sel_bg)],
        foreground=[("disabled", t.fg_muted)],
    )

    style.configure(
        "TEntry",
        fieldbackground=t.input_bg,
        foreground=t.fg,
        insertcolor=t.cursor,
        borderwidth=1,
        relief="flat",
    )
    style.map(
        "TEntry",
        fieldbackground=[("disabled", t.bg)],
        foreground=[("disabled", t.fg_muted)],
    )

    style.configure(
        "TNotebook",
        background=t.tabbar_bg,
        borderwidth=0,
    )
    style.configure(
        "TNotebook.Tab",
        background=t.tabbar_bg,
        foreground=t.tab_in_fg,
        padding=[8, 4],
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", t.tab_act_bg)],
        foreground=[("selected", t.tab_act_fg)],
    )

    style.configure("TSeparator", background=t.border)

    style.configure("TFrame", background=t.bg)
    style.configure("TLabel", background=t.bg, foreground=t.fg)
    style.configure(
        "TButton",
        background=t.btn_bg,
        foreground=t.btn_fg,
        borderwidth=0,
        relief="flat",
        padding=[8, 4],
    )
    style.map("TButton", background=[
              ("active", t.btn_hover), ("pressed", t.sel_bg)])

    style.configure("TPanedwindow", background=t.border)

    style.configure(
        "TProgressbar",
        background=t.accent,
        troughcolor=t.input_bg,
        borderwidth=0,
        thickness=4,
    )


def _patch_theme(t: Theme) -> None:
    """
    valores fallback derivados das cores base já definidas.

    Esta é a **única** função de patch do Codis - centraliza toda a lógica
    de preenchimento de campos opcionais dos temas. Deve ser chamada:

    * Por ``_patch_all_themes()`` na inicialização (cobre temas built-in).
    * Por ``ThemeManager.__init__`` e ``ThemeManager.set()`` para garantir
      que qualquer tema - incluindo temas criados dinamicamente (ex: kui)

    A função é idempotente: atributos já preenchidos não são sobrescritos.

    Parameters
    ----------
    t : Theme
        Tema a ser patcheado in-place.
    """

    def _fill(attr: str, value: str) -> None:
        """Preenche `attr` com `value` apenas se estiver vazio/ausente."""
        if not getattr(t, attr, None):
            setattr(t, attr, value)

    _fill("accent", t.btn_primary)
    _fill("ok_fg", t.t_string or "#50fa7b")
    _fill("warn_fg", t.t_number or "#ffb86c")
    _fill("err_fg", t.t_type or "#ff5555")
    _fill("btn_hover", t.sel_bg)
    _fill("btn_fg", t.fg)
    _fill("btn_primary_fg", "#ffffff" if t.dark else "#000000")
    _fill("activity_bg", t.sidebar_bg)
    _fill("input_bg", t.sel_bg)
    _fill("floating_bg", t.panel_bg)

    _syn = (
        {
            "t_keyword": "#ff79c6",
            "t_string": "#f1fa8c",
            "t_comment": "#6272a4",
            "t_number": "#bd93f9",
            "t_func": "#50fa7b",
            "t_type": "#8be9fd",
            "t_operator": "#ff79c6",
            "t_decorator": "#ffb86c",
        }
        if t.dark
        else {
            "t_keyword": "#0000ff",
            "t_string": "#a31515",
            "t_comment": "#008000",
            "t_number": "#098658",
            "t_func": "#795e26",
            "t_type": "#267f99",
            "t_operator": "#000000",
            "t_decorator": "#af00db",
        }
    )
    for attr, fallback in _syn.items():
        _fill(attr, fallback)

    _fill("t_class", t.t_type)
    _fill("t_variable", t.fg)
    _fill("t_link", t.accent)
    _fill("t_builtin", t.t_func)
    _fill("t_tag", t.t_keyword)
    _fill("t_attr", t.t_func)
    _fill("t_heading", t.t_keyword)
    _fill("t_regex", t.t_string)
    _fill("t_constant", t.t_number)
    _fill("t_escape", t.t_string)
    _fill("t_embedded", t.t_func)
    _fill("t_invalid", t.err_fg)
    _fill("t_namespace", t.t_type)

    _fill("statusbar_bg", t.activity_bg)
    _fill("statusbar_fg", t.fg)
    _fill("statusbar_sep", t.border)
    _fill("statusbar_hover", t.btn_hover)

    _fill("status_bg", t.statusbar_bg or t.activity_bg)
    _fill("status_fg", t.statusbar_fg or t.fg)
    _fill("status_err_bg", t.err_fg)
    _fill("status_err_fg", t.bg)
    _fill("status_warn_bg", t.warn_fg)
    _fill("status_warn_fg", t.bg)

    _fill("actbar_bg", t.activity_bg)

    def _is_dark_color(hex_c: str) -> bool:
        """Returns True if the hex color is dark (luminance < 0.35)."""
        try:
            h = hex_c.lstrip("#")
            if len(h) == 3:
                h = "".join(c * 2 for c in h)
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255
            return lum < 0.35
        except Exception:
            return t.dark

    _actbar_is_dark = _is_dark_color(t.activity_bg)
    _actbar_icon_fg = "#c8c8c8" if _actbar_is_dark else t.fg_muted
    _fill("actbar_fg", _actbar_icon_fg)
    _fill("actbar_active_bg", t.sel_bg)
    _fill("actbar_active_fg", "#ffffff" if _actbar_is_dark else t.fg)
    _fill("activity_icon_active", "#ffffff" if _actbar_is_dark else t.fg)
    _fill("activity_icon_inactive", _actbar_icon_fg)
    _fill("activity_active_indicator", t.accent)

    _fill("sidebar_fg", t.fg)
    _fill("sidebar_header_fg", t.fg_muted)

    _fill("breadcrumb_bg", t.tabbar_bg)
    _fill("breadcrumb_fg", t.fg_muted)
    _fill("breadcrumb_sep", t.border)
    _fill("breadcrumb_active", t.fg)
    _fill("breadcrumb_active_fg", t.fg)

    _fill("tab_bg", t.tabbar_bg)
    _fill("tab_fg", t.tab_in_fg)
    _fill("tab_in_bg", t.tabbar_bg)
    _fill("tab_active_bg", t.tab_act_bg)
    _fill("tab_active_fg", t.tab_act_fg)
    _fill("tab_act_border", t.accent)
    _fill("tab_hover_bg", t.btn_hover)
    _fill("tab_modified", t.warn_fg)

    _fill("gutter_bg", t.sidebar_bg)
    _fill("gutter_fg", t.fg_muted)
    _fill("gutter_active_fg", t.fg)

    _fill("minimap_bg", t.editor_bg)
    _fill("minimap_viewport", t.sel_bg)
    _fill("minimap_find", t.accent)
    _fill("minimap_err", t.err_fg)
    _fill("minimap_warn", t.warn_fg)

    _fill("terminal_bg", t.panel_bg)
    _fill("terminal_fg", t.fg)

    from src.theme.utils import hex_blend as _blend_hex

    if not getattr(t, "line_hl", None):
        t.line_hl = _blend_hex(t.editor_bg, t.sel_bg, 0.2)
    if not getattr(t, "line_hl2", None):
        t.line_hl2 = getattr(t, "line_hl", t.editor_bg)
    _fill("floating_border", t.border)
    _fill("floating_shadow", t.bg)
    _fill("comment_fg", t.t_comment)
    _fill("ruler_color", t.border)
    _fill("indent_guide", t.border)
    _fill("whitespace", t.fg_muted)

    _fill("input_border", t.border)
    _fill("border_focus", t.accent)
    _fill("overlay_bg", t.floating_bg)

    _fill("scroll_hover", t.btn_hover)

    _fill("btn_active", t.btn_primary)

    _fill("err_bg", t.input_bg)
    _fill("warn_bg", t.input_bg)
    _fill("info_bg", t.input_bg)

    _fill("fg_dim", t.fg_muted)
    _fill("hint_fg", t.fg_muted)
    _fill("info_fg", t.accent)

    _fill("diff_add_bg", t.panel_bg)
    _fill("diff_add_fg", t.ok_fg)
    _fill("diff_del_bg", t.panel_bg)
    _fill("diff_del_fg", t.err_fg)
    _fill("diff_change_bg", t.panel_bg)
    _fill("diff_change_fg", t.warn_fg)
    _fill("diff_hunk_bg", t.input_bg)
    _fill("diff_hunk_fg", t.accent)

    _fill("shadow_color", t.bg)

    if not getattr(t, "shadow_color", None):
        t.shadow_color = t.bg

    from src.theme.utils import hex_glow as _glow

    _fill("panel_header_bg", _blend_hex(t.panel_bg, t.bg, 0.35))
    _fill("card_selected_bg", _blend_hex(t.floating_bg, t.accent, 0.16))
    _fill("input_focus_border", _glow(t.accent, 0.3))
    _fill("shimmer_bg", _blend_hex(t.floating_bg, "#ffffff", 0.06))
    _fill("list_hover_bg", _blend_hex(t.panel_bg, t.accent, 0.10))
    _fill("settings_active_bg", _blend_hex(t.sel_bg, t.accent, 0.18))


def _patch_all_themes() -> None:
    """

    Deve ser chamado uma única vez após ``_define_themes()``, antes de
    instanciar qualquer widget. É seguro chamá-lo múltiplas vezes.
    """
    for theme in THEMES.values():
        with contextlib.suppress(tk.TclError):
            _patch_theme(theme)


def _apply_statusbar_patches() -> None:
    """
    Aplica patches de compatibilidade para a StatusBar e widgets especiais.

    Atualmente configura o estilo ``Thin.Vertical.TScrollbar`` com largura
    mínima de 6px e sem bordas, para uso em caixas de texto de painel.

    Deve ser chamado após ``apply_ttk()`` na inicialização do aplicativo.
    Esta função é idempotente - múltiplas chamadas são seguras.
    """
    if not _TK_AVAILABLE:
        return
    with contextlib.suppress(tk.TclError):
        style = _ttk.Style()
        style.configure(
            "Thin.Vertical.TScrollbar",
            borderwidth=0,
            arrowsize=0,
            relief="flat",
            width=6,
        )

        style.configure("StatusBar.TSeparator", background="#444444")

_CUSTOM_ICONS_DIR = _Path(__file__).parent / "icons"
_CUSTOM_CURSORS_DIR = _Path(__file__).parent / "cursors"

_icon_cache: dict[str, _Any] = {}


def apply_custom_cursor(root: _Any, cursor_name: str) -> bool:
    """Aplica um cursor customizado à janela raiz.

    Tenta usar cursores nativos primeiro (nome Tk), depois busca
    arquivos .cursor / .gif / .png em src/theme/cursors/.

    Args:
        root:        Janela Tk raiz.
        cursor_name: Nome do cursor (ex: "arrow", "hand2", "crosshair",
                     ou nome de arquivo em src/theme/cursors/).

    Returns:
        True se aplicado com sucesso, False caso contrário.
    """
    _TK_CURSORS = {
        "default",
        "arrow",
        "crosshair",
        "hand2",
        "move",
        "sb_h_double_arrow",
        "sb_v_double_arrow",
        "xterm",
        "watch",
        "target",
        "plus",
        "circle",
        "X_cursor",
        "pirate",
        "gobbler",
        "fleur",
        "heart",
        "star",
    }
    if cursor_name in _TK_CURSORS:
        with contextlib.suppress(Exception):
            root.config(cursor=cursor_name)
            return True

    for ext in (".gif", ".png", ".cur"):
        path = _CUSTOM_CURSORS_DIR / f"{cursor_name}{ext}"
        if not path.exists():
            continue
        try:
            if ext in (".png", ".gif"):
                import sys

                if sys.platform == "win32":
                    root.config(cursor=f"@{path}")
                else:
                    root.config(cursor=f"@{path} black")
            else:
                root.config(cursor=f"@{path}")
            return True
        except Exception:
            continue
    return False
