"""Tipos de resultado e modos de atualização do Codis."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class UpdateMode(enum.Enum):
    """Modos de atualização disponíveis."""

    CHECK_ONLY = "check_only"
    """Apenas verifica se há nova versão, sem instalar."""

    SMART = "smart"
    """Baixa somente os arquivos modificados no commit (diff patch)."""

    ZIP = "zip"
    """Baixa o ZIP da release e sobrescreve a instalação atual."""

    REINSTALL = "reinstall"
    """Remove a instalação existente e instala do zero via ZIP."""

    FORCE = "force"
    """Força a reinstalação mesmo estando na versão mais recente."""

    BINARY = "binary"
    """Substitui o executável empacotado (PyInstaller) em tempo real."""


class UpdateStatus(enum.Enum):
    """Status de conclusão de uma operação de atualização."""

    UP_TO_DATE = "up_to_date"
    UPDATE_AVAILABLE = "update_available"
    SUCCESS = "success"
    PENDING_RESTART = "pending_restart"
    ERROR = "error"
    CANCELLED = "cancelled"


@dataclass
class ReleaseInfo:
    """Metadados de uma release do GitHub."""

    tag: str
    """Tag semver, ex. ``v1.2.3``."""

    version: str
    """Versão limpa sem prefixo ``v``, ex. ``1.2.3``."""

    name: str
    """Título legível da release."""

    body: str
    """Corpo/changelog da release (Markdown)."""

    html_url: str
    """URL da página da release no GitHub."""

    published_at: str
    """Data/hora de publicação (ISO 8601)."""

    assets: list[dict] = field(default_factory=list)
    """Lista de assets da release (executáveis, ZIPs, etc.)."""

    zip_url: str = ""
    """URL do source ZIP da release."""

    tarball_url: str = ""
    """URL do source tarball."""

    commit_sha: str = ""
    """SHA do commit apontado pela tag."""

    @classmethod
    def from_api(cls, data: dict) -> ReleaseInfo:
        """Constrói a partir de um payload da API do GitHub."""
        version = data.get("tag_name", "").lstrip("v")
        return cls(
            tag=data.get("tag_name", ""),
            version=version,
            name=data.get("name", "") or version,
            body=data.get("body", "") or "",
            html_url=data.get("html_url", ""),
            published_at=data.get("published_at", ""),
            assets=data.get("assets", []),
            zip_url=data.get("zipball_url", ""),
            tarball_url=data.get("tarball_url", ""),
        )


@dataclass
class UpdateResult:
    """Resultado de qualquer operação de atualização."""

    status: UpdateStatus
    mode: UpdateMode | None = None
    current_version: str = ""
    latest_version: str = ""
    release: ReleaseInfo | None = None
    message: str = ""
    error: Exception | None = None
    files_updated: list[str] = field(default_factory=list)
    files_backed_up: list[str] = field(default_factory=list)
    restart_required: bool = False

    @property
    def ok(self) -> bool:
        """Retorna True se a operação foi bem-sucedida."""
        return self.status in (
            UpdateStatus.SUCCESS,
            UpdateStatus.PENDING_RESTART,
            UpdateStatus.UP_TO_DATE,
            UpdateStatus.UPDATE_AVAILABLE,
        )

    @property
    def update_available(self) -> bool:
        """Retorna True se há uma versão mais nova disponível."""
        return self.status == UpdateStatus.UPDATE_AVAILABLE
