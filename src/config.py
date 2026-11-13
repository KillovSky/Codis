#!/usr/bin/env python3
"""Gerenciamento de configuração persistente do Codis.

Provê ``ConfigManager``, responsável por ler, escrever e observar o arquivo
JSON de configuração do usuário, com suporte a callbacks de mudança e
perfis de configuração multi-workspace.
"""

from __future__ import annotations

import collections
import contextlib
import json
import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from src.constants import CONFIG_DIR, CONFIG_FILE, DEFAULT_CONFIG, MAX_RECENT

logger = logging.getLogger("Codis")

_FALSE_WORDS = frozenset({"false", "0", "no", "off", "n", "nao", "não", ""})
_TRUE_WORDS = frozenset({"true", "1", "yes", "on", "y", "sim"})


def _to_bool(value: Any, default: bool = False) -> bool:
    """Converte valores de JSON editado à mão em ``bool`` sem pegadinhas.

    ``bool("false")`` é ``True`` em Python; aqui strings são interpretadas.
    """
    if isinstance(value, str):
        v = value.strip().lower()
        if v in _FALSE_WORDS:
            return False
        if v in _TRUE_WORDS:
            return True
        return default
    return bool(value)

_CHOICES: dict[str, tuple[str, ...]] = {
    "exit_save_mode": ("ask", "save", "discard"),
    "trash_backend": ("workspace", "system"),
}


class ConfigManager:
    """Gerencia a configuração persistente do Codis.

    Responsável por carregar, salvar e fornecer acesso às preferências do
    usuário, além de manter listas de arquivos e pastas recentes.
    Opera em modo degradado se o diretório de configuração não puder ser
    criado ou acessado.
    """

    def __init__(self) -> None:
        """
        Inicializa o gerenciador de configuração.

        Cria o diretório de configuração (se possível) e carrega os dados
        existentes. Em caso de falha na criação do diretório, entra em modo
        degradado (sem persistência).
        """
        self._degraded = False
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning(
                "Diretório de configuração indisponível (%s). Operando em modo degradado.",
                exc,
            )
            self._degraded = True
        self._data: dict[str, Any] = dict(DEFAULT_CONFIG)
        self._change_callbacks: dict[str, list[Callable]] = collections.defaultdict(
            list
        )
        self._load()

    def _load(self) -> None:
        """
        Carrega o arquivo de configuração do disco.

        Se o arquivo existir e for um JSON válido, mescla seus dados com os
        padrões. Em caso de erro (arquivo corrompido), faz backup e mantém
        os valores padrão. Não faz nada se estiver em modo degradado.
        """
        if self._degraded or not CONFIG_FILE.exists():
            return
        try:
            raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("config.json não é um objeto JSON válido")
            self._data.update(raw)
            self._coerce_types()
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            logger.warning(
                "Configuração corrompida (%s) - usando padrões.", exc)
            with contextlib.suppress(OSError):
                CONFIG_FILE.rename(CONFIG_FILE.with_suffix(".json.bak"))

    def _coerce_types(self) -> None:
        """
        Garante que os tipos dos valores carregados correspondam aos esperados.

        O JSON pode representar inteiros como strings, booleanos como outros
        conforme conjuntos predefinidos de chaves.
        """
        _int = {
            "font_size",
            "tab_size",
            "sidebar_width",
            "panel_height",
            "autosave_delay",
            "git_fetch_interval",
            "terminal_font_size",
            "ui_font_size",
            "ui_scale",
            "autocomplete_delay",
            "ssh_default_port",
            "ssh_connect_timeout",
            "ssh_keepalive_interval",
            "ssh_sftp_buffer_kb",
            "debug_dap_port",
            "collab_port",
            "collab_max_peers",
            "plugin_timeout_s",
            "plugin_heartbeat_s",
            "plugin_max_restarts",
            "perf_freeze_threshold_ms",
            "hl_idle_ms",
            "autocomplete_min_chars",
            "autocomplete_max_items",
            "large_file_mb",
            "search_max_results",
            "trash_retention_days",
            "trash_max_mb",
        }
        _bool = {
            "word_wrap",
            "minimap",
            "breadcrumbs",
            "line_numbers",
            "autosave",
            "lint_enabled",
            "lint_js",
            "lint_python",
            "autocomplete",
            "snippets",
            "parameter_hints",
            "hover_docs",
            "show_welcome",
            "session_restore",
            "format_on_save",
            "trim_trailing_whitespace",
            "insert_final_newline",
            "git_enabled",
            "git_auto_fetch",
            "relative_numbers",
            "highlight_current_line",
            "bracket_matching",
            "indent_guides",
            "smooth_scroll",
            "multi_cursor",
            "use_spaces",
            "activity_bar",
            "respect_ignore_files",
            "sticky_scroll",
            "code_folding",
            "inline_blame",
            "color_preview",
            "inlay_hints",
            "workspace_trust",
            "lsp_enabled",
            "lsp_hover_enabled",
            "lsp_format_enabled",
            "lsp_diagnostics",
            "smart_tips",
            "a11y_high_contrast",
            "a11y_screen_reader",
            "a11y_keyboard_nav",
            "a11y_large_ui_font",
            "a11y_reduce_motion",
            "ssh_agent_forwarding",
            "ssh_strict_host_key",
            "ssh_open_in_editor",
            "debug_dap_enabled",
            "collab_enabled",
            "collab_show_cursors",
            "collab_show_chat",
            "collab_autosave_sync",
            "plugin_isolation",
            "plugin_sandbox_network",
            "perf_auto_mode",
            "perf_native_bindings",
            "session_save_on_exit",
            "confirm_delete",
        }
        _list = {"recent_files", "recent_folders", "rulers"}
        for k in _int:
            if k in self._data:
                try:
                    self._data[k] = int(self._data[k])
                except (TypeError, ValueError):
                    self._data[k] = DEFAULT_CONFIG.get(k, 0)
        for k in _bool:
            if k in self._data and not isinstance(self._data[k], bool):
                self._data[k] = _to_bool(self._data[k], DEFAULT_CONFIG.get(k, False))
        for k, allowed in _CHOICES.items():
            if k in self._data and self._data[k] not in allowed:
                self._data[k] = DEFAULT_CONFIG.get(k, allowed[0])
        for k in _list:
            if k in self._data and not isinstance(self._data[k], list):
                self._data[k] = list(DEFAULT_CONFIG.get(k, []))

    def save(self) -> bool:
        """
        Salva a configuração atual no disco de forma atômica.

        Escreve primeiro em um arquivo temporário e depois o renomeia para
        o arquivo definitivo. Retorna True se a operação for bem-sucedida,
        False em caso de erro ou se estiver em modo degradado.

        Returns:
            bool: True se salvou com sucesso, False caso contrário.
        """
        if self._degraded:
            return False
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            tmp = CONFIG_FILE.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(self._data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            if sys.platform != "win32":
                with contextlib.suppress(OSError):
                    os.chmod(tmp, 0o600)
            tmp.replace(CONFIG_FILE)
            return True
        except OSError as exc:
            logger.warning("Erro ao salvar configuração: %s", exc)
            return False

    def get(self, key: str, default: Any = None) -> Any:
        """
        Retorna o valor da chave de configuração.

        Se a chave não existir, tenta obter o valor padrão do dicionário
        DEFAULT_CONFIG ou, em última instância, o `default` fornecido.

        Args:
            key: Nome da chave.
            default: Valor retornado se a chave não for encontrada.

        Returns:
            Valor associado à chave.
        """
        return self._data.get(key, DEFAULT_CONFIG.get(key, default))

    def set(self, key: str, value: Any) -> None:
        """
        Define o valor de uma chave de configuração.

        chave são invocados com o novo valor.

        Args:
            key: Nome da chave.
            value: Novo valor a ser armazenado.
        """
        old = self._data.get(key)
        self._data[key] = value
        if old != value:
            for cb in list(self._change_callbacks.get(key, [])):
                try:
                    cb(value)
                except Exception as exc:
                    logger.warning(
                        "Callback de config falhou (%s): %s", key, exc)

    def on_change(self, key: str, cb: Callable[[Any], None]) -> None:
        """
        Registra um callback para ser chamado quando a chave especificada mudar.

        O callback receberá o novo valor como argumento.

        Args:
            key: Chave a ser monitorada.
            cb: Função callable que aceita um argumento (o novo valor).
        """
        if cb not in self._change_callbacks[key]:
            self._change_callbacks[key].append(cb)

    def add_recent_file(self, path: Path) -> None:
        """
        Adiciona um caminho de arquivo à lista de arquivos recentes.

        O arquivo é movido para o topo da lista e a lista é truncada para
        o tamanho máximo definido em MAX_RECENT.

        Args:
            path: Caminho do arquivo a ser adicionado.
        """
        recent: list[str] = self._data.get("recent_files", [])
        s = str(path)
        if s in recent:
            recent.remove(s)
        recent.insert(0, s)
        self._data["recent_files"] = recent[:MAX_RECENT]

    def add_recent_folder(self, path: Path) -> None:
        """
        Adiciona um caminho de pasta à lista de pastas recentes.

        A pasta é movida para o topo da lista e a lista é truncada para
        o tamanho máximo definido em MAX_RECENT.

        Args:
            path: Caminho da pasta a ser adicionada.
        """
        recent: list[str] = self._data.get("recent_folders", [])
        s = str(path)
        if s in recent:
            recent.remove(s)
        recent.insert(0, s)
        self._data["recent_folders"] = recent[:MAX_RECENT]

    def get_recent_files(self) -> list[Path]:
        """Retorna arquivos recentes ainda existentes no disco.

        Remove automaticamente entradas inexistentes da lista persistida
        para evitar acúmulo de caminhos mortos no menu de recentes.

        Returns:
            Lista de ``Path`` dos arquivos recentes existentes.
        """
        raw: list[str] = self._data.get("recent_files", [])
        valid = [p for p in raw if Path(p).exists()]
        if len(valid) != len(raw):
            self._data["recent_files"] = valid
        return [Path(p) for p in valid]
