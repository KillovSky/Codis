#!/usr/bin/env python3
"""Testes para src.ui.k_ui._perf - perfis de performance do motor cósmico."""

from __future__ import annotations

from src.ui.k_ui._perf import _DEFAULT_PERF, _PERF

_REQUIRED_KEYS = [
    "stars",
    "star_layers",
    "fps",
    "particles",
    "n_particles",
    "rings",
    "scan",
    "meteors",
    "solar_system",
    "black_holes",
]


class TestPerfProfiles:
    """Verifica a estrutura e os valores dos perfis de performance."""

    @staticmethod
    def test_default_perf_exists():
        """O perfil padrão definido em _DEFAULT_PERF deve existir em _PERF."""
        assert _DEFAULT_PERF in _PERF

    @staticmethod
    def test_all_expected_profiles_exist():
        """Todos os perfis nomeados devem estar presentes em _PERF."""
        for p in ("quantum", "ultra", "high", "balanced", "low", "minimal"):
            assert p in _PERF, f"perfil '{p}' ausente"

    @staticmethod
    def test_all_profiles_have_required_keys():
        """Cada perfil deve conter todas as chaves obrigatórias."""
        for profile, cfg in _PERF.items():
            for key in _REQUIRED_KEYS:
                assert key in cfg, f"perfil '{profile}' sem chave '{key}'"

    @staticmethod
    def test_fps_is_positive_int():
        """A taxa de quadros (fps) deve ser um inteiro positivo em todos os perfis."""
        for profile, cfg in _PERF.items():
            assert (
                isinstance(cfg["fps"], int) and cfg["fps"] > 0
            ), f"perfil '{profile}' fps inválido: {cfg['fps']}"

    @staticmethod
    def test_stars_is_positive_int():
        """A quantidade de estrelas deve ser um inteiro positivo em todos os perfis."""
        for _profile, cfg in _PERF.items():
            assert isinstance(cfg["stars"], int) and cfg["stars"] > 0

    @staticmethod
    def test_star_layers_at_least_one():
        """O número de camadas de estrelas deve ser pelo menos 1."""
        for _profile, cfg in _PERF.items():
            assert cfg["star_layers"] >= 1

    @staticmethod
    def test_n_particles_non_negative():
        """O número de partículas deve ser não-negativo."""
        for _profile, cfg in _PERF.items():
            assert cfg["n_particles"] >= 0

    @staticmethod
    def test_booleans_are_bool():
        """Os efeitos opcionais devem ser valores booleanos."""
        bool_keys = [
            "particles",
            "rings",
            "scan",
            "meteors",
            "solar_system",
            "black_holes",
        ]
        for profile, cfg in _PERF.items():
            for key in bool_keys:
                assert isinstance(
                    cfg[key], bool
                ), f"perfil '{profile}', '{key}' não é bool: {cfg[key]!r}"

    @staticmethod
    def test_quantum_has_highest_fps():
        """O perfil 'quantum' deve ter fps >= 'ultra' >= 'high'."""
        fps_values = {p: cfg["fps"] for p, cfg in _PERF.items()}
        assert fps_values["quantum"] >= fps_values["ultra"]
        assert fps_values["ultra"] >= fps_values["high"]

    @staticmethod
    def test_minimal_has_lowest_stars():
        """O perfil 'minimal' deve ter a menor quantidade de estrelas."""
        stars_values = {p: cfg["stars"] for p, cfg in _PERF.items()}
        assert stars_values["minimal"] <= stars_values["low"]
        assert stars_values["low"] <= stars_values["balanced"]

    @staticmethod
    def test_quantum_all_effects_enabled():
        """O perfil 'quantum' deve ter todos os efeitos principais habilitados."""
        cfg = _PERF["quantum"]
        assert cfg["particles"] is True
        assert cfg["rings"] is True
        assert cfg["meteors"] is True
        assert cfg["solar_system"] is True

    @staticmethod
    def test_minimal_reduces_effects():
        """O perfil 'minimal' deve ter menos partículas que o 'quantum'."""
        cfg = _PERF["minimal"]
        assert cfg["n_particles"] < _PERF["quantum"]["n_particles"]

    @staticmethod
    def test_bg_fps_present_and_positive():
        """Quando presente, bg_fps deve ser um valor positivo."""
        for _profile, cfg in _PERF.items():
            if "bg_fps" in cfg:
                assert cfg["bg_fps"] > 0

    @staticmethod
    def test_vignette_steps_present():
        """Quando presente, vignette_steps deve ser não-negativo."""
        for _profile, cfg in _PERF.items():
            if "vignette_steps" in cfg:
                assert cfg["vignette_steps"] >= 0

    @staticmethod
    def test_screensaver_idle_present_when_screensaver():
        """Perfis com screensaver ativo devem definir screensaver_idle_s positivo."""
        for _profile, cfg in _PERF.items():
            if cfg.get("screensaver"):
                assert "screensaver_idle_s" in cfg
                assert cfg["screensaver_idle_s"] > 0

    @staticmethod
    def test_profiles_are_independent_dicts():
        """Modificar um perfil não deve afetar os demais."""
        _PERF["quantum"]["_test_key"] = True
        assert "_test_key" not in _PERF.get("minimal", {})
        del _PERF["quantum"]["_test_key"]
