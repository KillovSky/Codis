#!/usr/bin/env python3
"""Testes unitários para src.theme.utils - helpers de tema e cor."""

from __future__ import annotations

import tkinter as tk

import pytest

from src.theme.utils import (
    alpha_blend_hex,
    cfg,
    cfg_many,
    hex_blend,
    hex_to_rgb,
    lsp_loc_line,
    lsp_loc_pair,
    lsp_loc_uri,
    make_toolbar_button,
    recolor,
)


class _FakeWidget:
    """Widget mínimo que registra chamadas a config()."""

    def __init__(self):
        """Inicializa o widget sem opções aplicadas."""
        self._opts: dict = {}
        self._destroyed = False

    def config(self, **kw):
        """Aplica as opções ou lança TclError se destruído."""
        if self._destroyed:
            raise tk.TclError("widget destroyed")
        self._opts.update(kw)

    configure = config


class TestCfg:
    """Verifica o helper cfg() que aplica opções a um widget com tolerância a erros."""

    @staticmethod
    def test_applies_options():
        """cfg() deve aplicar todas as opções fornecidas ao widget."""
        w = _FakeWidget()
        cfg(w, bg="#ff0000", fg="#ffffff")
        assert w._opts == {"bg": "#ff0000", "fg": "#ffffff"}

    @staticmethod
    def test_suppresses_tcl_error():
        """cfg() não deve propagar TclError de widget destruído."""
        w = _FakeWidget()
        w._destroyed = True
        cfg(w, bg="#aaa")
        assert not w._opts

    @staticmethod
    def test_suppresses_attribute_error():
        """cfg() não deve propagar AttributeError de objeto sem config()."""
        cfg(object(), bg="#aaa")

    @staticmethod
    def test_empty_options():
        """cfg() sem kwargs não deve modificar o widget."""
        w = _FakeWidget()
        cfg(w)
        assert not w._opts


class TestCfgMany:
    """Verifica o helper cfg_many() que aplica opções a uma lista de widgets."""

    @staticmethod
    def test_applies_to_all_widgets():
        """cfg_many() deve aplicar as opções a todos os widgets da lista."""
        ws = [_FakeWidget(), _FakeWidget(), _FakeWidget()]
        cfg_many(ws, bg="#123456")
        assert all(w._opts.get("bg") == "#123456" for w in ws)

    @staticmethod
    def test_skips_destroyed_without_error():
        """cfg_many() deve pular widgets destruídos sem lançar exceção."""
        w1 = _FakeWidget()
        w2 = _FakeWidget()
        w2._destroyed = True
        cfg_many([w1, w2], fg="#fff")
        assert w1._opts["fg"] == "#fff"
        assert not w2._opts

    @staticmethod
    def test_empty_list():
        """cfg_many() com lista vazia não deve lançar exceção."""
        cfg_many([], bg="#000")


class TestRecolor:
    """Verifica o helper recolor() que aplica opções por widget individualmente."""

    @staticmethod
    def test_applies_per_widget_options():
        """recolor() deve aplicar as opções específicas de cada widget."""
        w1 = _FakeWidget()
        w2 = _FakeWidget()
        recolor({w1: {"bg": "#111"}, w2: {"bg": "#222", "fg": "#333"}})
        assert w1._opts == {"bg": "#111"}
        assert w2._opts == {"bg": "#222", "fg": "#333"}

    @staticmethod
    def test_suppresses_destroyed():
        """recolor() não deve propagar TclError de widget destruído."""
        w = _FakeWidget()
        w._destroyed = True
        recolor({w: {"bg": "#000"}})


class TestHexBlend:
    """Verifica a interpolação de cores hexadecimais por hex_blend()."""

    @staticmethod
    def test_alpha_zero_returns_base():
        """Com alpha=0.0, o resultado deve ser igual à cor base."""
        assert hex_blend("#000000", "#ffffff", 0.0) == "#000000"

    @staticmethod
    def test_alpha_one_returns_overlay():
        """Com alpha=1.0, o resultado deve ser igual à cor de sobreposição."""
        assert hex_blend("#000000", "#ffffff", 1.0) == "#ffffff"

    @staticmethod
    def test_half_is_midpoint():
        """Com alpha=0.5, o canal R deve estar próximo do ponto médio (120-136)."""
        result = hex_blend("#000000", "#ffffff", 0.5)
        r = int(result[1:3], 16)
        assert 120 <= r <= 136

    @staticmethod
    def test_short_hex_expanded():
        """Hex curto '#fff' deve ser expandido antes da interpolação."""
        result = hex_blend("#000", "#fff", 1.0)
        assert result == "#ffffff"

    @staticmethod
    def test_invalid_base_returns_base():
        """Cor base inválida deve ser retornada sem alteração."""
        result = hex_blend("notcolor", "#ffffff", 0.5)
        assert result == "notcolor"

    @staticmethod
    def test_result_is_hex_format():
        """O resultado deve ser uma string hex de 7 caracteres."""
        result = hex_blend("#1a2b3c", "#4d5e6f", 0.3)
        assert result.startswith("#")
        assert len(result) == 7

    @staticmethod
    def test_channels_clamped_at_255():
        """Canal que ultrapassaria 255 deve ser limitado a 255."""
        result = hex_blend("#ffffff", "#ffffff", 0.5)
        r = int(result[1:3], 16)
        assert r == 255

    @staticmethod
    def test_channels_clamped_at_zero():
        """Canal que ficaria abaixo de 0 deve ser limitado a 0."""
        assert hex_blend("#000000", "#000000", 0.5) == "#000000"

    @pytest.mark.parametrize("alpha", [0.0, 0.25, 0.5, 0.75, 1.0])
    @staticmethod
    def test_result_always_valid_hex(alpha):
        """Para qualquer alpha, o resultado deve ser um hex de 7 caracteres válido."""
        result = hex_blend("#3c3c3c", "#eeeeee", alpha)
        assert result.startswith("#") and len(result) == 7
        int(result[1:], 16)


class _LspLoc:
    """Simula um objeto LSP Location com range e uri."""

    class _Range:
        """Range de localização LSP simulado."""

        class _Start:
            """Posição inicial do range."""

            line = 4

        start = _Start()

    range = _Range()
    uri = "file:///home/user/project/main.py"


class TestLspLocHelpers:
    """Verifica os helpers de extração de dados de LSP Location."""

    @staticmethod
    def test_uri_from_object():
        """lsp_loc_uri() deve extrair uri de objeto com atributo .uri."""
        assert lsp_loc_uri(_LspLoc()) == "file:///home/user/project/main.py"

    @staticmethod
    def test_uri_from_dict():
        """lsp_loc_uri() deve extrair uri de dicionário."""
        loc = {"uri": "file:///tmp/foo.py", "range": {"start": {"line": 0}}}
        assert lsp_loc_uri(loc) == "file:///tmp/foo.py"

    @staticmethod
    def test_uri_fallback_empty():
        """lsp_loc_uri() sem uri deve retornar string vazia."""
        assert lsp_loc_uri({}) == ""
        assert lsp_loc_uri(object()) == ""

    @staticmethod
    def test_line_from_object_is_1based():
        """lsp_loc_line() deve retornar linha 1-based (LSP 0-based + 1)."""
        assert lsp_loc_line(_LspLoc()) == 5

    @staticmethod
    def test_line_from_dict_is_1based():
        """lsp_loc_line() de dicionário deve retornar linha 1-based."""
        loc = {"range": {"start": {"line": 9}}}
        assert lsp_loc_line(loc) == 10

    @staticmethod
    def test_line_fallback_zero():
        """lsp_loc_line() sem linha deve retornar 1 (0 + 1)."""
        assert lsp_loc_line({}) == 1

    @staticmethod
    def test_pair_returns_tuple():
        """lsp_loc_pair() de objeto deve retornar (uri, linha 1-based)."""
        uri, line = lsp_loc_pair(_LspLoc())
        assert uri == "file:///home/user/project/main.py"
        assert line == 5

    @staticmethod
    def test_pair_from_dict():
        """lsp_loc_pair() de dicionário deve retornar (uri, linha 1-based)."""
        loc = {"uri": "file:///x.py", "range": {"start": {"line": 2}}}
        uri, line = lsp_loc_pair(loc)
        assert uri == "file:///x.py"
        assert line == 3


class TestHexToRgb:
    """Testes para hex_to_rgb() - conversão hex -> RGB inteiro."""

    @staticmethod
    def test_full_hex():
        """Hex de 6 dígitos deve ser decodificado corretamente."""
        assert hex_to_rgb("#ff8800") == (255, 136, 0)

    @staticmethod
    def test_short_hex_expanded():
        """Hex de 3 dígitos deve ser expandido antes de decodificar."""
        assert hex_to_rgb("#f80") == (255, 136, 0)

    @staticmethod
    def test_without_hash_prefix():
        """Deve funcionar mesmo sem o prefixo '#'."""
        assert hex_to_rgb("000000") == (0, 0, 0)


class TestAlphaBlendHex:
    """Testes para alpha_blend_hex() - blend de cores."""

    @staticmethod
    def test_full_opacity_returns_foreground():
        """alpha=1.0 deve retornar a cor de frente inalterada."""
        assert alpha_blend_hex("#ff0000", "#0000ff", 1.0) == "#ff0000"

    @staticmethod
    def test_zero_opacity_returns_background():
        """alpha=0.0 deve retornar a cor de fundo inalterada."""
        assert alpha_blend_hex("#ff0000", "#0000ff", 0.0) == "#0000ff"

    @staticmethod
    def test_half_opacity_blends_channels():
        """alpha=0.5 deve misturar os canais proporcionalmente."""
        assert alpha_blend_hex("#ffffff", "#000000", 0.5) == "#7f7f7f"

    @staticmethod
    def test_invalid_hex_returns_foreground_unchanged():
        """Hex inválido deve retornar fg sem lançar exceção (comportamento
        preservado dos antigos duplicados em minimap.py/task_manager.py/
        zen_media.py)."""
        assert alpha_blend_hex("not-a-color", "#000000", 0.5) == "not-a-color"


class _RecordingLabel:
    """``tk.Label`` falso que registra pack()/bind()/config() para inspeção.

    Usado para testar make_toolbar_button() sem
    depender do stub genérico de tkinter em conftest.py, cujo bind() é um
    no-op que descarta o callback - aqui precisamos capturar os callbacks de
    clique/hover para simulá-los nos testes.
    """

    def __init__(self, parent, **kw):
        self.parent = parent
        self.kw: dict = dict(kw)
        self.packed: dict | None = None
        self.binds: dict = {}

    def pack(self, **kw):
        self.packed = kw

    def bind(self, event, cb):
        self.binds[event] = cb

    def config(self, **kw):
        self.kw.update(kw)

    configure = config


class _FakeTheme:
    accent = "#89b4fa"
    sidebar_bg = "#1e1e2e"
    fg = "#cdd6f4"
    btn_hover = "#313244"


class TestMakeToolbarButton:
    """Testes para make_toolbar_button() - helper de botão de toolbar.

    Substitui as cópias _btn/_tb antes duplicadas em sql_studio.py,
    task_manager.py, diff_panel.py, formatter_panel.py, env_manager.py e
    pip_manager.py.
    """

    @staticmethod
    def _make(monkeypatch, **kwargs):
        import src.theme.utils as theme_utils

        monkeypatch.setattr(
            theme_utils, "tk", type("_TkNS", (), {"Label": _RecordingLabel})
        )
        calls: list = []
        btn = make_toolbar_button(
            object(), _FakeTheme(), "Salvar", lambda: calls.append("clicked"), **kwargs
        )
        return btn, calls

    def test_neutral_style_by_default(self, monkeypatch):
        """Sem accent=True, deve usar sidebar_bg/fg neutros do tema."""
        btn, _ = self._make(monkeypatch)
        assert btn.kw["text"] == "Salvar"
        assert btn.kw["bg"] == _FakeTheme.sidebar_bg
        assert btn.kw["fg"] == _FakeTheme.fg
        assert btn.kw["cursor"] == "hand2"

    def test_accent_style_uses_theme_accent_and_white_text(self, monkeypatch):
        """accent=True deve usar theme.accent como fundo e texto branco."""
        btn, _ = self._make(monkeypatch, accent=True)
        assert btn.kw["bg"] == _FakeTheme.accent
        assert btn.kw["fg"] == "#FFF"

    def test_click_invokes_callback(self, monkeypatch):
        """O bind de <Button-1> deve chamar o callback fornecido."""
        btn, calls = self._make(monkeypatch)
        assert "<Button-1>" in btn.binds
        btn.binds["<Button-1>"](None)
        assert calls == ["clicked"]

    def test_hover_enter_and_leave_swap_background(self, monkeypatch):
        """<Enter> deve mudar bg para btn_hover; <Leave> deve restaurar o original.

        """
        btn, _ = self._make(monkeypatch)
        assert "<Enter>" in btn.binds
        assert "<Leave>" in btn.binds
        btn.binds["<Enter>"](None)
        assert btn.kw["bg"] == _FakeTheme.btn_hover
        btn.binds["<Leave>"](None)
        assert btn.kw["bg"] == _FakeTheme.sidebar_bg

    def test_pack_side_and_padding_defaults(self, monkeypatch):
        """Deve empacotar com side='left' e o padding padrão por padrão."""
        btn, _ = self._make(monkeypatch)
        assert btn.packed == {"side": "left", "padx": 2, "pady": 4}

    def test_pack_side_and_padding_overridable(self, monkeypatch):
        """side/outer_padx/outer_pady devem ser configuráveis (uso em toolbars à direita)."""
        btn, _ = self._make(monkeypatch, side="right",
                            outer_padx=0, outer_pady=3)
        assert btn.packed == {"side": "right", "padx": 0, "pady": 3}
