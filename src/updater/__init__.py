"""Módulo de atualização do Codis.

Expõe :class:`UpdateManager` como ponto de entrada principal.
"""

from .channel import UpdateChannel
from .manager import UpdateManager
from .result import UpdateMode, UpdateResult

__all__ = ["UpdateManager", "UpdateChannel", "UpdateResult", "UpdateMode"]
