"""Gerenciamento de backup antes de atualizações."""

from __future__ import annotations

import contextlib
import logging
import shutil
import tempfile
import time
from pathlib import Path

logger = logging.getLogger("Codis.updater")


class BackupManager:
    """Cria e restaura snapshots do diretório do app antes de atualizações.

    O backup fica em um diretório temporário e é apagado automaticamente
    após confirmação de sucesso ou pode ser restaurado em caso de falha.
    """

    def __init__(self, app_dir: Path) -> None:
        self._app_dir = app_dir
        self._backup_dir: Path | None = None
        # type: ignore[type-arg]
        self._backup_root: tempfile.TemporaryDirectory | None = None

    def create(self, label: str = "") -> Path:
        """Cria um snapshot do diretório do app.

        Returns:
            Caminho para o diretório de backup criado.
        """
        self._backup_root = tempfile.TemporaryDirectory(prefix="codis_backup_")
        tag = label or time.strftime("%Y%m%d_%H%M%S")
        dest = Path(self._backup_root.name) / f"codis_{tag}"
        logger.info("Criando backup em %s", dest)
        shutil.copytree(
            self._app_dir,
            dest,
            ignore=shutil.ignore_patterns(
                "__pycache__",
                "*.pyc",
                "*.pyo",
                ".git",
                ".mypy_cache",
                ".ruff_cache",
            ),
        )
        self._backup_dir = dest
        logger.info("Backup criado: %s", dest)
        return dest

    def restore(self) -> bool:
        """Restaura o backup para o diretório do app.

        Returns:
            True se a restauração foi bem-sucedida.
        """
        if not self._backup_dir or not self._backup_dir.exists():
            logger.warning("Nenhum backup disponível para restaurar.")
            return False

        logger.warning("Restaurando backup de %s → %s",
                       self._backup_dir, self._app_dir)
        try:
            if self._app_dir.exists():
                shutil.rmtree(self._app_dir)
            shutil.copytree(self._backup_dir, self._app_dir)
            logger.info("Backup restaurado com sucesso.")
            return True
        except OSError as exc:
            logger.error("Falha ao restaurar backup: %s", exc)
            return False

    def cleanup(self) -> None:
        """Remove o backup temporário."""
        if self._backup_root is not None:
            with contextlib.suppress(Exception):
                self._backup_root.cleanup()
            self._backup_root = None
            self._backup_dir = None
            logger.debug("Backup temporário removido.")

    def list_tracked_files(self) -> list[str]:
        """Retorna os caminhos relativos dos arquivos no backup."""
        if not self._backup_dir:
            return []
        return [
            str(p.relative_to(self._backup_dir))
            for p in self._backup_dir.rglob("*")
            if p.is_file()
        ]

    def __enter__(self) -> BackupManager:
        return self

    def __exit__(self, exc_type: object, *_: object) -> None:
        if exc_type is not None:
            self.restore()
        self.cleanup()
