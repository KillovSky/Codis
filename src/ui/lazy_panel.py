"""Painéis construídos só na primeira vez que são usados.

A sidebar do Codis tem ~20 painéis (centenas de widgets Tk). Como a criação
de widgets é lenta (sobretudo no Windows), os painéis raramente usados são
instanciados sob demanda.

``LazyPanel`` é um proxy transparente: qualquer acesso a atributo (``pack``,
``refresh``, ``winfo_exists``...) constrói o painel real e repassa. Chamadas
"informativas" feitas enquanto ele não existe (tema, arquivo ativo,
workspace...) não forçam a construção: o tema é lido do ``tmgr`` no
``__init__`` do painel real, e as demais são *reproduzidas* na construção.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("Codis.Lazy")

_DROP = frozenset({"apply_theme", "notify_file_saved"})
_REPLAY = frozenset({"notify_active_file", "notify_active_lang", "set_workspace", "refresh"})


class LazyPanel:
    """Proxy que adia a criação do widget real até o primeiro uso."""

    def __init__(self, factory: Callable[[], Any], name: str = "") -> None:
        object.__setattr__(self, "_lp_factory", factory)
        object.__setattr__(self, "_lp_real", None)
        object.__setattr__(self, "_lp_name", name)
        object.__setattr__(self, "_lp_pending", {})

    def build(self) -> Any:
        """Cria (uma vez) e devolve o widget real."""
        real = object.__getattribute__(self, "_lp_real")
        if real is not None:
            return real
        factory = object.__getattribute__(self, "_lp_factory")
        real = factory()
        object.__setattr__(self, "_lp_real", real)
        pending = object.__getattribute__(self, "_lp_pending")
        object.__setattr__(self, "_lp_pending", {})
        for meth, (args, kw) in pending.items():
            fn = getattr(real, meth, None)
            if callable(fn):
                try:
                    fn(*args, **kw)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("replay %s.%s falhou: %s", object.__getattribute__(self, "_lp_name"), meth, exc)
        return real

    def __getattr__(self, item: str) -> Any:
        real = object.__getattribute__(self, "_lp_real")
        if real is None:
            if item in _DROP:
                return lambda *a, **k: None
            if item in _REPLAY:
                pending = object.__getattribute__(self, "_lp_pending")

                def _remember(*a: Any, **k: Any) -> None:
                    pending[item] = (a, k)

                return _remember
            real = self.build()
        return getattr(real, item)

    def __setattr__(self, key: str, value: Any) -> None:
        setattr(self.build(), key, value)

    def __bool__(self) -> bool:
        return True
