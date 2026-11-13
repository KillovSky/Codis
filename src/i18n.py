"""Codis - internacionalização (I18n).

As traduções ficam em ``src/locales/<locale>.json``.
O arquivo ``pt-BR.json`` é o fallback base.
Cada locale adicional sobrescreve apenas as chaves traduzidas.

Uso típico::

    from src.i18n import I18n, _t
    label = _t("menu.file")
    msg   = _t("err.save", path=p)
"""

from __future__ import annotations

import json
import logging
import pathlib
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("Codis")

_LOCALES_DIR = pathlib.Path(__file__).parent / "locales"


class I18n:
    """Singleton de internacionalização do Codis.

    Gerencia carregamento lazy de arquivos JSON em ``src/locales/``, usando
    ``pt-BR.json`` como fallback base.  Acesse a instância global via
    :meth:`get_instance` ou use diretamente a função :func:`_t`.
    """

    __slots__ = (
        "_locale",
        "_change_callbacks",
        "_registry_callbacks",
        "_cache",
        "_base",
    )

    _instance: I18n | None = None
    _lock: threading.Lock = threading.Lock()

    AVAILABLE_LOCALES: list[tuple[str, str]] = [
        ("pt-BR", "Português (Brasil)"),
        ("en", "English"),
        ("es", "Español"),
        ("fr", "Français"),
        ("de", "Deutsch"),
        ("zh-CN", "中文 (简体)"),
        ("ja", "日本語"),
        ("ru", "Русский"),
        ("ko", "한국어"),
        ("it", "Italiano"),
        ("pl", "Polski"),
        ("nl", "Nederlands"),
        ("tr", "Türkçe"),
        ("uk", "Українська"),
    ]

    def __init__(self) -> None:
        """Inicializa o singleton com o locale padrão ``pt-BR``."""
        self._locale: str = "pt-BR"
        self._change_callbacks: list[Callable[[], None]] = []
        self._registry_callbacks: list[Callable[[], None]] = []
        self._cache: dict[str, dict[str, str]] = {}
        self._base: dict[str, str] = self._load("pt-BR")

    def _load(self, locale: str) -> dict[str, str]:
        """Carrega e faz cache do JSON de um locale específico.

        Retorna dicionário vazio se o arquivo não existir, suportando
        locales parcialmente traduzidos ou injetados via plugin.

        Args:
            locale: Código do locale (ex: ``"pt-BR"``).

        Returns:
            Dicionário ``{chave: tradução}`` lido do JSON, ou vazio.
        """
        if locale in self._cache:
            return self._cache[locale]

        path = _LOCALES_DIR / f"{locale}.json"
        if path.exists():
            try:
                with path.open(encoding="utf-8") as fh:
                    data: dict[str, str] = json.load(fh)
                    self._cache[locale] = data
                    return data
            except (json.JSONDecodeError, OSError) as exc:
                logger.error("Falha ao carregar locale '%s': %s", locale, exc)

        self._cache[locale] = {}
        return self._cache[locale]

    def _fire(self, callbacks: list[Callable[[], None]]) -> None:
        """Dispara uma lista de callbacks, absorvendo falhas individualmente.

        Args:
            callbacks: Lista de callables sem argumentos a invocar.
        """
        for cb in list(callbacks):
            try:
                cb()
            except (TypeError, RuntimeError) as exc:
                logger.warning("Callback falhou: %s", exc)

    @classmethod
    def get_instance(cls) -> I18n:
        """Retorna a instância singleton, criando-a na primeira chamada (thread-safe)."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def set_locale(self, locale: str) -> None:
        """Define o locale ativo.

        Aceita qualquer locale presente em :attr:`AVAILABLE_LOCALES` ou
        registrado via :meth:`extend`.  Locales desconhecidos são ignorados
        silenciosamente.

        Args:
            locale: Código do locale (ex: ``"pt-BR"``, ``"en"``).
        """
        known = {code for code, _ in self.AVAILABLE_LOCALES} | set(self._cache)
        if locale not in known:
            return

        self._locale = locale
        self._load(locale)
        self._fire(self._change_callbacks)

    def get_locale(self) -> str:
        """Retorna o código do locale atualmente ativo."""
        return self._locale

    def translate(self, key: str, /, default: str | None = None, **kwargs: Any) -> str:
        """Retorna a string traduzida para ``key`` no locale ativo.

        Busca na ordem: locale atual -> base inglesa -> ``default`` -> ``key``.
        Aplica interpolação via :meth:`str.format` quando ``kwargs`` são
        fornecidos.

        Args:
            key:      Chave de tradução (ex: ``"menu.file"``).
            default:  Valor de fallback quando a chave não é encontrada.
            **kwargs: Argumentos de interpolação para :meth:`str.format`.

        Returns:
            String traduzida, com interpolação aplicada se cabível.
        """
        current = self._cache.get(self._locale, {})
        trans = current.get(key) or self._base.get(key) or default or key

        if kwargs:
            try:
                return trans.format(**kwargs)
            except (KeyError, IndexError):
                pass

        return trans

    def on_change(self, cb: Callable[[], None]) -> None:
        """Registra um callback invocado sempre que o locale mudar.

        Args:
            cb: Callable sem argumentos a ser chamado na troca de locale.
        """
        if cb not in self._change_callbacks:
            self._change_callbacks.append(cb)

    def on_locale_registry_change(self, cb: Callable[[], None]) -> None:
        """Registra callback chamado ao adicionar um novo locale via :meth:`extend`.

        Utilizado pelo ``SettingsEditor`` para atualizar o combo de idioma
        dinamicamente.

        Args:
            cb: Callable sem argumentos a ser chamado no registro de novos locales.
        """
        if cb not in self._registry_callbacks:
            self._registry_callbacks.append(cb)

    def extend(
        self, locale: str, translations: dict[str, str], label: str = ""
    ) -> None:
        """Adiciona ou sobrescreve traduções para um locale específico.

        Se o locale ainda não constar em :attr:`AVAILABLE_LOCALES`, é
        registrado automaticamente para aparecer no seletor de idioma.

        As traduções são mescladas **apenas em memória** - para persistir,
        crie o arquivo JSON em ``src/locales/<locale>.json``.

        Args:
            locale:       Código do locale alvo (ex: ``"tl"``, ``"pt-BR"``).
            translations: Dicionário ``{chave: tradução}`` a ser mesclado.
            label:        Nome exibido na UI (ex: ``"Tagalog (Filipino)"``).
                          Quando omitido, usa o código como fallback.
        """
        self._cache.setdefault(locale, {}).update(translations)

        if locale not in {code for code, _ in self.AVAILABLE_LOCALES}:
            name = label or translations.get("lang.name", locale)
            self.AVAILABLE_LOCALES.append((locale, name))
            self._fire(self._registry_callbacks)


def _t(key: str, /, default: str | None = None, **kwargs: Any) -> str:
    """Retorna a string traduzida para ``key`` no locale ativo.

    Atalho direto para :meth:`I18n.translate` na instância singleton.
    Suporta interpolação via ``kwargs`` repassados ao :meth:`str.format`.

    Exemplo::

        label = _t("menu.file")
        msg   = _t("err.save", path=p)

    Args:
        key:      Chave de tradução (ex: ``"menu.file"``).
        default:  Valor de fallback quando a chave não é encontrada.
        **kwargs: Argumentos de interpolação.

    Returns:
        String traduzida.
    """
    return I18n.get_instance().translate(key, default, **kwargs)
