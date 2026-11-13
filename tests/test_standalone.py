"""Testes para src/standalone.py - registry, _PanelSpec e dispatch (headless).

Importamos apenas as partes que não exigem Tkinter ativo:
- _REGISTRY (dict com as specs de 29+ painéis)
- _PanelSpec (dataclass de especificação)
- dispatch() (roteador que retorna True/False sem abrir janelas quando
  testado com flags inválidas ou --help no conftest headless)
- _normalize_subcommand de run.py
"""

from __future__ import annotations

import pytest

from src.standalone import _REGISTRY, _SPECIAL, _PanelSpec


class TestPanelSpec:
    def test_basic_creation(self):
        spec = _PanelSpec(
            title_i18n="standalone.sql.title",
            title_def="Codis - SQL Studio",
            geometry="1100x760",
            bg_attr="panel_bg",
            cls_path="src.ui.sql_studio.SQLStudioPanel",
        )
        assert spec.title_def == "Codis - SQL Studio"
        assert spec.geometry == "1100x760"
        assert spec.bg_attr == "panel_bg"
        assert spec.cls_path == "src.ui.sql_studio.SQLStudioPanel"
        assert spec.resizable == (True, True)
        assert spec.post_build is None

    def test_resizable_false(self):
        spec = _PanelSpec("k", "t", "400x280", resizable=(False, False))
        assert spec.resizable == (False, False)

    def test_post_build_stored(self):
        def fn(*a):
            return None
        spec = _PanelSpec("k", "t", "400x280", post_build=fn)
        assert spec.post_build is fn

    def test_default_bg_attr(self):
        spec = _PanelSpec("k", "t", "400x280")
        assert spec.bg_attr == "panel_bg"


class TestRegistry:
    _PRIMARY_FLAGS = {
        "--pomodoro",
        "--scratch",
        "--rest",
        "--sql",
        "--tasks",
        "--pip",
        "--formatter",
        "--env",
        "--calendar",
        "--clock",
        "--calculator",
        "--todo",
        "--output",
        "--processes",
        "--flowchart",
        "--render",
        "--stats",
        "--player",
        "--regex",
        "--diff",
        "--paint",
        "--color-picker",
        "--snippets",
        "--accessibility",
        "--git",
        "--terminal",
        "--plugins",
        "--ssh",
        "--archive",
    }
    _ALIAS_FLAGS = {"--preview", "--procs", "--calc", "--a11y"}

    def test_all_primary_flags_present(self):
        missing = self._PRIMARY_FLAGS - \
            set(_REGISTRY.keys()) - set(_SPECIAL.keys())
        assert not missing, f"Flags ausentes do registry: {missing}"

    def test_aliases_point_to_same_spec_or_func(self):
        """--preview -> --render; --procs -> --processes; --calc -> --calculator."""
        if "--preview" in _REGISTRY and "--render" in _REGISTRY:
            assert _REGISTRY["--preview"] is _REGISTRY["--render"]
        if "--procs" in _REGISTRY and "--processes" in _REGISTRY:
            assert _REGISTRY["--procs"] is _REGISTRY["--processes"]
        if "--calc" in _REGISTRY and "--calculator" in _REGISTRY:
            assert _REGISTRY["--calc"] is _REGISTRY["--calculator"]

    def test_total_flags_at_least_29(self):
        total = len(set(_REGISTRY.keys()) | set(_SPECIAL.keys()))
        assert total >= 29, f"Esperado >= 29 flags, encontrado {total}"

    def test_all_registry_values_are_panel_spec(self):
        for flag, spec in _REGISTRY.items():
            assert isinstance(
                spec, _PanelSpec
            ), f"{flag} não é _PanelSpec: {type(spec)}"

    def test_all_special_values_are_callable(self):
        for flag, fn in _SPECIAL.items():
            assert callable(fn), f"{flag} não é callable: {type(fn)}"

    def test_all_specs_have_valid_geometry(self):
        import re

        geo_re = re.compile(r"^\d+x\d+$")
        for flag, spec in _REGISTRY.items():
            assert geo_re.match(
                spec.geometry
            ), f"{flag}: geometria inválida '{spec.geometry}'"

    def test_all_specs_have_cls_path_or_known_omission(self):
        """cls_path deve estar preenchido para specs de painel."""
        for flag, spec in _REGISTRY.items():
            if spec.cls_path:
                assert (
                    "." in spec.cls_path
                ), f"{flag}: cls_path sem módulo: '{spec.cls_path}'"

    def test_all_specs_have_title(self):
        for flag, spec in _REGISTRY.items():
            assert spec.title_def, f"{flag}: title_def vazio"

    def test_all_specs_have_i18n_key(self):
        for flag, spec in _REGISTRY.items():
            assert spec.title_i18n, f"{flag}: title_i18n vazio"

    def test_sql_spec_has_correct_class(self):
        assert "--sql" in _REGISTRY
        assert "sql_studio" in _REGISTRY["--sql"].cls_path

    def test_pomodoro_is_not_resizable(self):
        if "--pomodoro" in _REGISTRY:
            assert _REGISTRY["--pomodoro"].resizable == (False, False)

    def test_calculator_has_fixed_width(self):
        if "--calculator" in _REGISTRY:
            w, _ = _REGISTRY["--calculator"].resizable
            assert w is False

    def test_flowchart_has_post_build(self):
        if "--flowchart" in _REGISTRY:
            assert _REGISTRY["--flowchart"].post_build is not None

    def test_render_has_post_build(self):
        if "--render" in _REGISTRY:
            assert _REGISTRY["--render"].post_build is not None

    def test_stats_has_post_build(self):
        if "--stats" in _REGISTRY:
            assert _REGISTRY["--stats"].post_build is not None

    def test_scratch_no_post_build(self):
        if "--scratch" in _REGISTRY:
            assert _REGISTRY["--scratch"].post_build is None

    def test_sql_no_post_build(self):
        if "--sql" in _REGISTRY:
            assert _REGISTRY["--sql"].post_build is None


class TestNormalizeSubcommand:
    @pytest.fixture(autouse=True)
    def _import(self):
        from run import _normalize_subcommand

        self._fn = _normalize_subcommand

    def test_bare_word_becomes_flag(self):
        result = self._fn(["player"])
        assert result == ["--player"]

    def test_bare_word_lowercased(self):
        result = self._fn(["PLAYER"])
        assert result == ["--player"]

    def test_already_flag_returns_none(self):
        result = self._fn(["--player"])
        assert result is None

    def test_empty_list_returns_none(self):
        result = self._fn([])
        assert result is None

    def test_subcommand_with_arg_preserves_arg(self):
        result = self._fn(["diff", "a.py", "b.py"])
        assert result == ["--diff", "a.py", "b.py"]

    def test_sql_subcommand(self):
        result = self._fn(["sql"])
        assert result == ["--sql"]

    def test_scratch_subcommand(self):
        result = self._fn(["scratch"])
        assert result == ["--scratch"]

    def test_dash_prefix_returns_none(self):
        result = self._fn(["-h"])
        assert result is None

    def test_double_dash_prefix_returns_none(self):
        result = self._fn(["--help"])
        assert result is None


class TestDispatch:
    def test_dispatch_empty_returns_false(self):
        from src.standalone import dispatch

        assert dispatch([]) is False

    def test_dispatch_unknown_flag_returns_false(self):
        from src.standalone import dispatch

        assert dispatch(["--nonexistent-xyz-abc"]) is False

    def test_dispatch_help_returns_true(self, capsys):
        from src.standalone import dispatch

        result = dispatch(["--help"])
        assert result is True
        out = capsys.readouterr().out
        assert "Codis" in out or "--" in out

    def test_dispatch_h_alias_returns_true(self, capsys):
        from src.standalone import dispatch

        result = dispatch(["-h"])
        assert result is True

    def test_dispatch_help_alias_returns_true(self, capsys):
        from src.standalone import dispatch

        result = dispatch(["help"])
        assert result is True
