#!/usr/bin/env python3
"""Testes para src.ui.k_ui._engine - KEngine (lógica pura, sem display)."""

from __future__ import annotations

import ast
import math
from pathlib import Path

import pytest

from src.ui.k_ui._colors import _lc, _pal
from src.ui.k_ui._perf import _DEFAULT_PERF, _PERF

_ENGINE_PATH = Path(__file__).parent.parent / "src" / \
    "ui" / "k_ui" / "_engine.py"


class TestEnginePureFunctions:
    """Testa lógica matemática e utilitários que não dependem de Tkinter."""

    @staticmethod
    def test_tau_value():
        """A constante _TAU deve ser igual a math.tau."""
        import src.ui.k_ui._engine as eng_mod

        assert pytest.approx(math.tau) == eng_mod._TAU

    @staticmethod
    def test_learn_keys_are_strings():
        """Todas as entradas de _LEARN_KEYS devem ser strings com ponto."""
        from src.ui.k_ui._engine import KEngine

        for key in KEngine._LEARN_KEYS:
            assert isinstance(key, str)
            assert "." in key

    @staticmethod
    def test_learn_groups_reference_valid_keys():
        """Cada chave em _LEARN_GROUPS deve existir em _LEARN_KEYS."""
        from src.ui.k_ui._engine import KEngine

        all_keys = set(KEngine._LEARN_KEYS)
        for group, keys in KEngine._LEARN_GROUPS.items():
            for key in keys:
                assert (
                    key in all_keys
                ), f"grupo '{group}': chave '{key}' não encontrada em _LEARN_KEYS"

    @staticmethod
    def test_learn_keys_no_duplicates():
        """_LEARN_KEYS não deve conter entradas duplicadas."""
        from src.ui.k_ui._engine import KEngine

        assert len(KEngine._LEARN_KEYS) == len(
            set(KEngine._LEARN_KEYS))


class TestBugFixVerification:
    """Verifica que bugs corrigidos não regridem."""

    @staticmethod
    def test_no_self_pal_reference_in_draw_particles():
        """_draw_particles não deve usar self._pal (atributo inexistente)."""
        src_text = _ENGINE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(src_text)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_draw_particles":
                for child in ast.walk(node):
                    if isinstance(child, ast.Attribute) and child.attr == "_pal":
                        pytest.fail(
                            f"_draw_particles ainda usa self._pal na linha {child.lineno}"
                        )
                break

    @staticmethod
    def test_no_duplicate_docstring_in_draw_wormholes():
        """_draw_wormholes não deve ter docstring duplicada consecutiva."""
        src_text = _ENGINE_PATH.read_text(encoding="utf-8")
        lines = src_text.split("\n")
        for i in range(len(lines) - 1):
            if (
                '"""Portais quânticos' in lines[i]
                and '"""Portais quânticos' in lines[i + 1]
            ):
                pytest.fail(
                    f"Docstring duplicada de wormholes na linha {i + 1}")

    @staticmethod
    def test_engine_file_parses_correctly():
        """O arquivo _engine.py deve ter sintaxe Python válida."""
        src_text = _ENGINE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(src_text)
        assert tree is not None

    @staticmethod
    def test_no_self_pal_anywhere_in_engine():
        """self._pal não deve existir em nenhum ponto do engine."""
        src_text = _ENGINE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(src_text)
        violations = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and (
                node.attr == "_pal"
                and isinstance(node.value, ast.Name)
                and node.value.id == "self"
            ):
                violations.append(getattr(node, "lineno", "?"))
        assert not violations, f"self._pal encontrado nas linhas: {violations}"


class TestPaletteIntegration:
    """Testa que as paletas usadas pelo engine são válidas."""

    @staticmethod
    def test_dark_palette_used_in_engine_is_valid():
        """A paleta dark deve conter todas as chaves acessadas pelo engine."""
        p = _pal(True)
        engine_palette_keys = [
            "void",
            "accent",
            "accent2",
            "star",
            "star2",
            "star3",
            "aur_a",
            "aur_b",
            "aur_c",
            "plasma",
            "quantum",
            "meteor",
            "glass",
            "fg",
            "fg_dim",
            "fg_muted",
        ]
        for key in engine_palette_keys:
            assert key in p, f"chave '{key}' ausente na paleta (usada pelo engine)"

    @staticmethod
    def test_particle_color_keys_exist():
        """_gen_particles usa: aur_a, aur_b, accent, accent2, plasma, quantum."""
        p = _pal(True)
        for key in ("aur_a", "aur_b", "accent", "accent2", "plasma", "quantum"):
            assert key in p

    @staticmethod
    def test_meteor_color_key_exists():
        """A chave 'meteor' deve existir na paleta dark."""
        p = _pal(True)
        assert "meteor" in p

    @staticmethod
    def test_lc_with_engine_palette_colors():
        """_lc com cores da paleta do engine deve retornar hex válido."""
        p = _pal(True)
        result = _lc(p["void"], p["accent"], 0.5)
        assert result.startswith("#") and len(result) == 7


class TestPerfIntegration:
    """Verifica compatibilidade entre _PERF e os campos acessados pelo engine."""

    @staticmethod
    def test_default_profile_has_all_engine_keys():
        """O perfil padrão deve ter todas as chaves lidas pelo engine."""
        cfg = _PERF[_DEFAULT_PERF]
        for key in (
            "particles",
            "rings",
            "meteors",
            "scan",
            "solar_system",
            "black_holes",
            "fps",
            "stars",
        ):
            assert key in cfg

    @staticmethod
    def test_cfg_get_subatomic_safe():
        """Engine usa cfg.get('subatomic') - não deve falhar em nenhum perfil."""
        for _profile, cfg in _PERF.items():
            _ = cfg.get("subatomic", False)

    @staticmethod
    def test_cfg_get_screensaver_safe():
        """Engine usa cfg.get('screensaver') - não deve falhar em nenhum perfil."""
        for _profile, cfg in _PERF.items():
            _ = cfg.get("screensaver", False)

    @staticmethod
    def test_cfg_get_quantum_tooltips_safe():
        """Engine usa cfg.get('quantum_tooltips') - não deve falhar."""
        for _profile, cfg in _PERF.items():
            _ = cfg.get("quantum_tooltips", False)

    @staticmethod
    def test_cfg_get_time_dilation_safe():
        """Engine usa cfg.get('time_dilation') - não deve falhar."""
        for _profile, cfg in _PERF.items():
            _ = cfg.get("time_dilation", False)

    @staticmethod
    def test_cfg_auto_fps_safe():
        """Engine usa cfg.get('auto_fps') - não deve falhar."""
        for _profile, cfg in _PERF.items():
            _ = cfg.get("auto_fps", True)


class TestKWelcomeInheritance:
    """Verifica a herança e interface pública de KWelcome e KEngine."""

    @staticmethod
    def test_kwelcome_inherits_from_kcosmicengine():
        """KWelcome deve ser subclasse de KEngine."""
        from src.ui.k_ui._engine import KEngine, KWelcome

        assert issubclass(KWelcome, KEngine)

    @staticmethod
    def test_kwelcome_has_stop():
        """KWelcome deve expor o método stop() chamável."""
        from src.ui.k_ui._engine import KWelcome

        assert hasattr(KWelcome, "stop")
        assert callable(KWelcome.stop)

    @staticmethod
    def test_kcosmicengine_has_set_reactive_metrics():
        """KEngine deve ter o método set_reactive_metrics."""
        from src.ui.k_ui._engine import KEngine

        assert hasattr(KEngine, "set_reactive_metrics")

    @staticmethod
    def test_kcosmicengine_has_notify_activity():
        """KEngine deve ter o método notify_activity."""
        from src.ui.k_ui._engine import KEngine

        assert hasattr(KEngine, "notify_activity")

    @staticmethod
    def test_kcosmicengine_has_trigger_cosmic_glitch():
        """KEngine deve ter o método trigger_cosmic_glitch."""
        from src.ui.k_ui._engine import KEngine

        assert hasattr(KEngine, "trigger_cosmic_glitch")

    @staticmethod
    def test_kcosmicengine_has_set_profile():
        """KEngine deve ter o método set_profile."""
        from src.ui.k_ui._engine import KEngine

        assert hasattr(KEngine, "set_profile")

    @staticmethod
    def test_kcosmicengine_has_trigger_flash():
        """KEngine deve ter o método trigger_flash."""
        from src.ui.k_ui._engine import KEngine

        assert hasattr(KEngine, "trigger_flash")

    @staticmethod
    def test_kcosmicengine_has_notify_keystroke():
        """KEngine deve ter o método notify_keystroke."""
        from src.ui.k_ui._engine import KEngine

        assert hasattr(KEngine, "notify_keystroke")

    @staticmethod
    def test_learn_groups_not_empty():
        """_LEARN_GROUPS deve conter pelo menos um grupo."""
        from src.ui.k_ui._engine import KEngine

        assert KEngine._LEARN_GROUPS

    @staticmethod
    def test_learn_groups_have_list_values():
        """Cada grupo em _LEARN_GROUPS deve ser uma lista não vazia."""
        from src.ui.k_ui._engine import KEngine

        for _group, keys in KEngine._LEARN_GROUPS.items():
            assert isinstance(keys, list) and keys
