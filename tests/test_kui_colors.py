#!/usr/bin/env python3
"""Testes para src.ui.k_ui._colors - paletas, interpolação e utilitários de cor."""

from __future__ import annotations

import sys

import pytest

from src.ui.k_ui._colors import _hex2rgb, _hue, _lc, _lerp, _pal, _safe_del


class TestPal:
    """Verifica a geração de paletas de cores dark e light."""

    @staticmethod
    def test_dark_palette_returns_dict():
        """_pal(True) deve retornar um dicionário."""
        p = _pal(True)
        assert isinstance(p, dict)

    @staticmethod
    def test_light_palette_returns_dict():
        """_pal(False) deve retornar um dicionário."""
        p = _pal(False)
        assert isinstance(p, dict)

    @staticmethod
    def test_dark_has_required_keys():
        """A paleta dark deve conter todas as chaves obrigatórias."""
        p = _pal(True)
        for key in ("void", "accent", "fg", "star", "aur_a", "aur_b", "glass"):
            assert key in p, f"chave '{key}' ausente na paleta dark"

    @staticmethod
    def test_light_has_required_keys():
        """A paleta light deve conter todas as chaves obrigatórias."""
        p = _pal(False)
        for key in ("void", "accent", "fg", "star", "aur_a", "aur_b", "glass"):
            assert key in p, f"chave '{key}' ausente na paleta light"

    @staticmethod
    def test_dark_and_light_differ():
        """As paletas dark e light devem ter cores de fundo diferentes."""
        dark = _pal(True)
        light = _pal(False)
        assert dark["void"] != light["void"]

    @staticmethod
    def test_colors_are_hex_strings():
        """Todas as cores de string nas paletas devem estar no formato hexadecimal."""
        for dark in (True, False):
            p = _pal(dark)
            for k, v in p.items():
                if isinstance(v, str):
                    assert v.startswith(
                        "#"), f"paleta['{k}'] = {v!r} não é hex"

    @staticmethod
    def test_neb_colors_are_tuples():
        """As cores de nebulosa (neb_*) devem ser tuplas RGB."""
        p = _pal(True)
        for k in ("neb_a", "neb_b", "neb_c", "neb_d"):
            if k in p:
                assert isinstance(p[k], tuple)
                assert len(p[k]) == 3

    @staticmethod
    def test_aur_rgb_are_tuples():
        """As cores aurora em RGB (aur_rgb_*) devem ser tuplas de 3 elementos."""
        p = _pal(True)
        for k in ("aur_rgb_a", "aur_rgb_b", "aur_rgb_c"):
            assert k in p
            assert isinstance(p[k], tuple) and len(p[k]) == 3

    @staticmethod
    def test_void_rgb_is_tuple():
        """void_rgb deve ser uma tupla com valores RGB válidos (0-255)."""
        p = _pal(True)
        assert "void_rgb" in p
        r, g, b = p["void_rgb"]
        assert 0 <= r <= 255 and 0 <= g <= 255 and 0 <= b <= 255

    @staticmethod
    def test_ok_warn_err_present():
        """As cores semânticas ok, warn e err devem estar presentes em ambas as paletas."""
        for dark in (True, False):
            p = _pal(dark)
            assert "ok" in p
            assert "warn" in p
            assert "err" in p

    @staticmethod
    def test_plasma_quantum_present():
        """As cores especiais plasma e quantum devem estar na paleta dark."""
        p = _pal(True)
        assert "plasma" in p
        assert "quantum" in p


class TestLerp:
    """Verifica a interpolação linear _lerp(a, b, t)."""

    @staticmethod
    def test_t_zero_returns_a():
        """Com t=0 deve retornar o valor a."""
        assert _lerp(0.0, 100.0, 0.0) == pytest.approx(0.0)

    @staticmethod
    def test_t_one_returns_b():
        """Com t=1 deve retornar o valor b."""
        assert _lerp(0.0, 100.0, 1.0) == pytest.approx(100.0)

    @staticmethod
    def test_t_half_returns_midpoint():
        """Com t=0.5 deve retornar o ponto médio entre a e b."""
        assert _lerp(0.0, 100.0, 0.5) == pytest.approx(50.0)

    @staticmethod
    def test_negative_values():
        """A interpolação deve funcionar corretamente com valores negativos."""
        result = _lerp(-100.0, 100.0, 0.5)
        assert result == pytest.approx(0.0)

    @staticmethod
    def test_same_a_b_any_t():
        """Quando a == b, o resultado deve ser sempre a independente de t."""
        assert _lerp(42.0, 42.0, 0.7) == pytest.approx(42.0)

    @staticmethod
    def test_monotone():
        """A interpolação deve ser monótonamente crescente de a para b."""
        vals = [_lerp(0, 10, t / 10) for t in range(11)]
        assert vals == sorted(vals)

    @staticmethod
    def test_integer_inputs():
        """A interpolação com inteiros deve retornar float."""
        result = _lerp(0, 10, 0.3)
        assert isinstance(result, float)


class TestLc:
    """Verifica _lc(c1, c2, t_quantized) - t_quantized no intervalo 0-255."""

    @staticmethod
    def test_t_zero_returns_c1():
        """Com t=0 deve retornar c1 sem modificação."""
        assert _lc("#000000", "#ffffff", 0) == "#000000"

    @staticmethod
    def test_t_one_returns_c2():
        """Com t=255 deve retornar c2 sem modificação."""
        assert _lc("#000000", "#ffffff", 255) == "#ffffff"

    @staticmethod
    def test_t_half_is_midpoint():
        """Com t=128 o resultado deve estar próximo do cinza médio."""
        result = _lc("#000000", "#ffffff", 128)
        r = int(result[1:3], 16)
        g = int(result[3:5], 16)
        b = int(result[5:7], 16)
        assert 120 <= r <= 136
        assert 120 <= g <= 136
        assert 120 <= b <= 136

    @staticmethod
    def test_result_is_hex_string():
        """O resultado de _lc deve ser uma string hexadecimal de 7 caracteres."""
        result = _lc("#7c4dff", "#00e5ff", 128)
        assert result.startswith("#") and len(result) == 7

    @staticmethod
    def test_t_clamped_above_max():
        """Valores de t acima de 255 devem ser tratados como 255."""
        result = _lc("#000000", "#ffffff", 512)
        assert result == "#ffffff"

    @staticmethod
    def test_t_clamped_below_zero():
        """Valores de t negativos devem ser tratados como 0."""
        result = _lc("#000000", "#ffffff", -10)
        assert result == "#000000"

    @staticmethod
    def test_invalid_hex_returns_c1():
        """Hex inválido deve retornar c1 como fallback."""
        result = _lc("notahex", "#ffffff", 128)
        assert result == "notahex"

    @staticmethod
    def test_same_colors_any_t():
        """Quando c1 == c2, qualquer t deve retornar a mesma cor."""
        for tq in (0, 77, 178, 255):
            assert _lc("#abcdef", "#abcdef", tq) == "#abcdef"

    @staticmethod
    def test_only_red_channel():
        """Interpolação de vermelho para preto deve manter apenas o canal R."""
        result = _lc("#ff0000", "#000000", 128)
        r = int(result[1:3], 16)
        g = int(result[3:5], 16)
        b = int(result[5:7], 16)
        assert r > 0
        assert g == 0
        assert b == 0


class TestHue:
    """Verifica a conversão de ângulo de matiz para tupla RGB."""

    @staticmethod
    def test_returns_tuple_of_three_ints():
        """_hue() deve retornar uma tupla de três inteiros."""
        r, g, b = _hue(0)
        assert all(isinstance(x, int) for x in (r, g, b))

    @staticmethod
    def test_all_values_in_range():
        """Todos os canais de _hue() devem estar no intervalo 0-255."""
        for angle in range(0, 360, 15):
            r, g, b = _hue(angle)
            assert 0 <= r <= 255
            assert 0 <= g <= 255
            assert 0 <= b <= 255

    @staticmethod
    def test_angle_0_is_red():
        """Ângulo 0° deve corresponder ao vermelho puro."""
        r_ch, _g, b_ch = _hue(0)
        assert r_ch == 255
        assert b_ch == 0

    @staticmethod
    def test_angle_120_is_green():
        """Ângulo 120° deve corresponder ao verde puro."""
        r_ch, g_ch, _b = _hue(120)
        assert g_ch == 255
        assert r_ch == 0

    @staticmethod
    def test_angle_240_is_blue():
        """Ângulo 240° deve corresponder ao azul puro."""
        _r, g_ch, b_ch = _hue(240)
        assert b_ch == 255
        assert g_ch == 0

    @staticmethod
    def test_periodic_360():
        """O ângulo 360° deve produzir a mesma cor que 0°."""
        h0 = _hue(0)
        h360 = _hue(360)
        assert h0 == h360

    @staticmethod
    def test_produces_different_colors():
        """Ângulos distintos devem gerar cores diferentes."""
        colors = {_hue(h) for h in range(0, 360, 60)}
        assert len(colors) >= 4


class TestHex2rgb:
    """Verifica a conversão de string hexadecimal para tupla RGB."""

    @staticmethod
    def test_black():
        """'#000000' deve converter para (0, 0, 0)."""
        assert _hex2rgb("#000000") == (0, 0, 0)

    @staticmethod
    def test_white():
        """'#ffffff' deve converter para (255, 255, 255)."""
        assert _hex2rgb("#ffffff") == (255, 255, 255)

    @staticmethod
    def test_red():
        """'#ff0000' deve converter para (255, 0, 0)."""
        assert _hex2rgb("#ff0000") == (255, 0, 0)

    @staticmethod
    def test_green():
        """'#00ff00' deve converter para (0, 255, 0)."""
        assert _hex2rgb("#00ff00") == (0, 255, 0)

    @staticmethod
    def test_blue():
        """'#0000ff' deve converter para (0, 0, 255)."""
        assert _hex2rgb("#0000ff") == (0, 0, 255)

    @staticmethod
    def test_arbitrary_color():
        """Cor arbitrária deve ter os canais extraídos corretamente."""
        r, g, b = _hex2rgb("#7c4dff")
        assert r == 0x7C
        assert g == 0x4D
        assert b == 0xFF

    @staticmethod
    def test_uppercase_hex():
        """Hex em maiúsculas deve ser convertido corretamente."""
        r, g, b = _hex2rgb("#FF8800")
        assert r == 255
        assert g == 0x88
        assert b == 0

    @staticmethod
    def test_returns_tuple_of_three():
        """_hex2rgb() deve sempre retornar uma tupla de exatamente 3 elementos."""
        result = _hex2rgb("#aabbcc")
        assert len(result) == 3


class TestSafeDel:
    """Verifica o utilitário _safe_del para destruição segura de widgets."""

    @staticmethod
    def test_none_does_not_raise():
        """_safe_del(None) não deve lançar nenhuma exceção."""
        _safe_del(None)

    @staticmethod
    def test_object_without_destroy():
        """_safe_del() com objeto sem método destroy não deve lançar exceção."""
        _safe_del(42)

    @staticmethod
    def test_destroyable_widget():
        """_safe_del() com widget Tkinter não deve lançar exceção."""
        tk = sys.modules["tkinter"]
        w = tk.Frame()
        _safe_del(w)

    @staticmethod
    def test_already_destroyed():
        """Chamar _safe_del() duas vezes no mesmo widget não deve lançar exceção."""
        tk = sys.modules["tkinter"]
        w = tk.Frame()
        _safe_del(w)
        _safe_del(w)
