#!/usr/bin/env python3
"""Testes unitários para src.constants - valores, tipos e invariantes."""

from __future__ import annotations

from pathlib import Path

import src.constants as c


class TestIdentity:
    """Verifica identidade e formato dos metadados do aplicativo."""

    @staticmethod
    def test_app_name():
        """O nome do aplicativo deve ser exatamente 'Codis'."""
        assert c.APP_NAME == "Codis"

    @staticmethod
    def test_app_version_format():
        """A versão deve seguir o formato semântico X.Y.Z com três partes numéricas."""
        parts = c.APP_VERSION.split(".")
        assert len(parts) == 3
        assert all(p.isdigit() for p in parts)

    @staticmethod
    def test_app_codename_is_string():
        """O codinome do aplicativo deve ser uma string não vazia."""
        assert isinstance(c.APP_CODENAME, str) and c.APP_CODENAME


class TestFonts:
    """Verifica as constantes de fonte padrão."""

    @staticmethod
    def test_font_mono_string():
        """A fonte monoespaçada padrão deve ser uma string."""
        assert isinstance(c.FONT_MONO, str)

    @staticmethod
    def test_font_ui_string():
        """A fonte de UI padrão deve ser uma string."""
        assert isinstance(c.FONT_UI, str)

    @staticmethod
    def test_font_size_positive():
        """O tamanho de fonte padrão deve ser um inteiro positivo."""
        assert isinstance(c.FONT_SIZE, int) and c.FONT_SIZE > 0

    @staticmethod
    def test_font_min_less_than_max():
        """O tamanho mínimo de fonte deve ser menor que o máximo."""
        assert c.FONT_MIN < c.FONT_MAX

    @staticmethod
    def test_font_size_in_range():
        """O tamanho padrão de fonte deve estar dentro do intervalo permitido."""
        assert c.FONT_MIN <= c.FONT_SIZE <= c.FONT_MAX


class TestEditor:
    """Verifica constantes relacionadas ao comportamento do editor."""

    @staticmethod
    def test_tab_size_positive():
        """O tamanho de tabulação deve ser um inteiro positivo."""
        assert isinstance(c.TAB_SIZE, int) and c.TAB_SIZE > 0

    @staticmethod
    def test_window_size_tuple():
        """O tamanho da janela deve ser uma tupla com dois valores positivos."""
        assert len(c.WINDOW_SIZE) == 2
        assert all(v > 0 for v in c.WINDOW_SIZE)

    @staticmethod
    def test_window_min_tuple():
        """O tamanho mínimo da janela deve ser uma tupla com dois valores positivos."""
        assert len(c.WINDOW_MIN) == 2
        assert all(v > 0 for v in c.WINDOW_MIN)

    @staticmethod
    def test_window_min_smaller_than_default():
        """O tamanho mínimo da janela deve ser menor ou igual ao padrão."""
        assert c.WINDOW_MIN[0] <= c.WINDOW_SIZE[0]
        assert c.WINDOW_MIN[1] <= c.WINDOW_SIZE[1]

    @staticmethod
    def test_max_recent_positive():
        """O número máximo de arquivos recentes deve ser um inteiro positivo."""
        assert isinstance(c.MAX_RECENT, int) and c.MAX_RECENT > 0

    @staticmethod
    def test_lint_delay_positive():
        """O delay de lint deve ser um inteiro positivo (em ms)."""
        assert isinstance(c.LINT_DELAY, int) and c.LINT_DELAY > 0

    @staticmethod
    def test_hl_idle_ms_positive():
        """O tempo de idle para highlight deve ser um inteiro positivo."""
        assert isinstance(c.HL_IDLE_MS, int) and c.HL_IDLE_MS > 0

    @staticmethod
    def test_autocomplete_delay_positive():
        """O delay de autocomplete deve ser positivo."""
        assert c.AUTOCOMPLETE_DELAY > 0

    @staticmethod
    def test_minimap_width_positive():
        """A largura do minimap deve ser positiva."""
        assert c.MINIMAP_WIDTH > 0

    @staticmethod
    def test_search_max_results_positive():
        """O número máximo de resultados de busca deve ser positivo."""
        assert c.SEARCH_MAX_RESULTS > 0

    @staticmethod
    def test_large_file_bytes_positive():
        """O limiar de arquivo grande deve ser um valor positivo em bytes."""
        assert c.LARGE_FILE_BYTES > 0


class TestPaths:
    """Verifica que as constantes de caminho são objetos Path."""

    @staticmethod
    def test_config_dir_is_path():
        """CONFIG_DIR deve ser um objeto Path."""
        assert isinstance(c.CONFIG_DIR, Path)

    @staticmethod
    def test_config_file_is_path():
        """CONFIG_FILE deve ser um objeto Path."""
        assert isinstance(c.CONFIG_FILE, Path)

    @staticmethod
    def test_plugins_dir_is_path():
        """PLUGINS_DIR deve ser um objeto Path."""
        assert isinstance(c.PLUGINS_DIR, Path)

    @staticmethod
    def test_snippets_dir_is_path():
        """SNIPPETS_DIR deve ser um objeto Path."""
        assert isinstance(c.SNIPPETS_DIR, Path)

    @staticmethod
    def test_sessions_file_is_path():
        """SESSIONS_FILE deve ser um objeto Path."""
        assert isinstance(c.SESSIONS_FILE, Path)


class TestDefaultConfig:
    """Verifica a estrutura e os valores do dicionário DEFAULT_CONFIG."""

    @staticmethod
    def test_default_config_is_dict():
        """DEFAULT_CONFIG deve ser um dicionário."""
        assert isinstance(c.DEFAULT_CONFIG, dict)

    @staticmethod
    def test_font_size_in_default():
        """A chave 'font_size' deve existir no DEFAULT_CONFIG."""
        assert "font_size" in c.DEFAULT_CONFIG

    @staticmethod
    def test_theme_in_default():
        """A chave 'theme' deve existir no DEFAULT_CONFIG."""
        assert "theme" in c.DEFAULT_CONFIG

    @staticmethod
    def test_word_wrap_in_default():
        """A chave 'word_wrap' deve existir no DEFAULT_CONFIG."""
        assert "word_wrap" in c.DEFAULT_CONFIG

    @staticmethod
    def test_recent_files_list():
        """O valor de 'recent_files' no DEFAULT_CONFIG deve ser uma lista."""
        assert isinstance(c.DEFAULT_CONFIG.get("recent_files", []), list)

    @staticmethod
    def test_font_size_is_int():
        """O valor de 'font_size' no DEFAULT_CONFIG deve ser um inteiro."""
        assert isinstance(c.DEFAULT_CONFIG["font_size"], int)

    @staticmethod
    def test_font_size_in_valid_range():
        """O font_size padrão deve estar dentro do intervalo FONT_MIN - FONT_MAX."""
        fs = c.DEFAULT_CONFIG["font_size"]
        assert c.FONT_MIN <= fs <= c.FONT_MAX


class TestExtLang:
    """Verifica o mapeamento de extensões de arquivo para linguagens."""

    @staticmethod
    def test_ext_lang_is_dict():
        """EXT_LANG deve ser um dicionário."""
        assert isinstance(c.EXT_LANG, dict)

    @staticmethod
    def test_py_extension_maps_to_python():
        """A extensão '.py' deve mapear para 'python'."""
        assert c.EXT_LANG.get(".py") == "python"

    @staticmethod
    def test_js_extension_maps_to_javascript():
        """A extensão '.js' deve mapear para 'javascript'."""
        assert c.EXT_LANG.get(".js") == "javascript"

    @staticmethod
    def test_ts_extension_maps_to_typescript():
        """A extensão '.ts' deve mapear para 'typescript'."""
        assert c.EXT_LANG.get(".ts") == "typescript"

    @staticmethod
    def test_html_extension_maps_to_html():
        """A extensão '.html' deve mapear para 'html'."""
        assert c.EXT_LANG.get(".html") == "html"

    @staticmethod
    def test_css_extension_present():
        """A extensão '.css' deve existir no mapeamento."""
        assert ".css" in c.EXT_LANG

    @staticmethod
    def test_json_extension_present():
        """A extensão '.json' deve existir no mapeamento."""
        assert ".json" in c.EXT_LANG

    @staticmethod
    def test_md_extension_present():
        """A extensão '.md' deve existir no mapeamento."""
        assert ".md" in c.EXT_LANG

    @staticmethod
    def test_all_extensions_start_with_dot_or_are_filename():
        """Cada entrada de EXT_LANG deve ser extensão com ponto ou nome de arquivo válido."""
        for ext_key in c.EXT_LANG:
            assert (
                ext_key.startswith(
                    ".") or ext_key[0].isupper() or ext_key.islower()
            ), f"extensão com formato inesperado: {ext_key!r}"

    @staticmethod
    def test_all_lang_values_are_strings():
        """Todos os valores de EXT_LANG devem ser strings não vazias."""
        for _ext, lang_val in c.EXT_LANG.items():
            assert isinstance(lang_val, str) and lang_val


class TestLangLabel:
    """Verifica o mapeamento de identificadores de linguagem para rótulos legíveis."""

    @staticmethod
    def test_lang_label_is_dict():
        """LANG_LABEL deve ser um dicionário."""
        assert isinstance(c.LANG_LABEL, dict)

    @staticmethod
    def test_python_has_label():
        """A linguagem 'python' deve ter um rótulo definido."""
        assert "python" in c.LANG_LABEL

    @staticmethod
    def test_javascript_has_label():
        """A linguagem 'javascript' deve ter um rótulo definido."""
        assert "javascript" in c.LANG_LABEL

    @staticmethod
    def test_labels_are_strings():
        """Todos os rótulos de LANG_LABEL devem ser strings não vazias."""
        for _lang, label in c.LANG_LABEL.items():
            assert isinstance(label, str) and label


class TestAutosave:
    """Verifica as constantes de autosave."""

    @staticmethod
    def test_autosave_default_positive():
        """O intervalo padrão de autosave deve ser positivo."""
        assert c.AUTOSAVE_DEFAULT > 0

    @staticmethod
    def test_autosave_min_less_than_max():
        """O intervalo mínimo de autosave deve ser menor que o máximo."""
        assert c.AUTOSAVE_MIN < c.AUTOSAVE_MAX

    @staticmethod
    def test_autosave_default_in_range():
        """O intervalo padrão de autosave deve estar dentro dos limites."""
        assert c.AUTOSAVE_MIN <= c.AUTOSAVE_DEFAULT <= c.AUTOSAVE_MAX
