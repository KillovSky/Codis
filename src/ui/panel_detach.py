"""Gerenciador de destacamento (detach) de painéis em janelas flutuantes.

``PanelDetachManager`` destaca painéis (terminal, output, git, debug, regex,
problems) em janelas flutuantes, tanto no modo clássico quanto no modo K-UI.
É instanciado uma única vez na inicialização do ``CodisApp``. A janela
flutuante em si (``KFloatWindow``) vem da K-UI - é um widget autocontido
(Toplevel arrastável/redimensionável com moldura holográfica) e não depende
do restante do engine cósmico.

Como a instância é compartilhada entre os dois modos, painéis destacados
persistem quando o usuário alterna o K-UI ligado/desligado.
"""

from __future__ import annotations

import contextlib
import logging
import tkinter as tk
from collections.abc import Callable

from src.ui.k_ui import KFloatWindow

logger = logging.getLogger("Codis.PanelDetach")

__all__ = ["PanelDetachManager"]


class PanelDetachManager:
    """Controla o ciclo de vida de painéis destacados em janelas flutuantes.

    Funciona de forma independente do modo K-UI: usa diretamente
    ``KFloatWindow`` para criar janelas flutuantes a partir de fábricas de
    painéis (``panel_factories``), permitindo detach/reattach tanto no modo
    clássico quanto no modo K-UI, sem duplicar estado entre os dois.
    """

    def __init__(
        self,
        root: tk.Misc,
        factories: dict[str, Callable] | None = None,
        dark: bool = True,
    ) -> None:
        """Inicializa o gerenciador.

        Args:
            root: Janela raiz Tkinter (pai das janelas flutuantes).
            factories: Mapa ``panel_key -> Callable(parent) -> Widget``.
            dark: Paleta holográfica clara/escura usada na moldura da
                janela flutuante (independente do tema clássico ativo).
        """
        self._root = root
        self._factories: dict[str, Callable] = dict(factories or {})
        self._dark = dark
        self._detached: dict[str, KFloatWindow] = {}

    def set_factories(self, factories: dict[str, Callable]) -> None:
        """Substitui o mapa de fábricas de painéis destacáveis."""
        self._factories = dict(factories or {})

    def set_dark(self, dark: bool) -> None:
        """Atualiza a paleta clara/escura usada em novas janelas flutuantes."""
        self._dark = dark

    def is_detached(self, panel_key: str) -> bool:
        """Retorna True se ``panel_key`` está atualmente em janela flutuante."""
        return panel_key in self._detached

    def can_detach(self, panel_key: str) -> bool:
        """Retorna True se existe fábrica registrada para ``panel_key``."""
        return panel_key in self._factories

    def detach(self, panel_key: str) -> None:
        """Destaca ``panel_key`` em janela flutuante (ou foca, se já destacado).

        Args:
            panel_key: Chave do painel registrado em ``_factories``.
        """
        if panel_key in self._detached:
            with contextlib.suppress(tk.TclError):
                self._detached[panel_key].lift()
            return
        factory = self._factories.get(panel_key)
        if not factory:
            logger.warning(
                "PanelDetachManager: sem fábrica para %r", panel_key)
            return

        def _on_close(k: str = panel_key) -> None:
            self._detached.pop(k, None)

        with contextlib.suppress(tk.TclError):
            self._detached[panel_key] = KFloatWindow(
                self._root,
                panel_key.replace("_", " ").title(),
                factory,
                _on_close,
                self._dark,
            )

    def reattach(self, panel_key: str) -> None:
        """Fecha a janela flutuante de ``panel_key`` e a remove do controle."""
        win = self._detached.pop(panel_key, None)
        if win is not None:
            with contextlib.suppress(tk.TclError):
                win.destroy()

    def close_all(self) -> None:
        """Fecha todas as janelas flutuantes atualmente abertas."""
        for win in list(self._detached.values()):
            with contextlib.suppress(tk.TclError):
                win.destroy()
        self._detached.clear()
