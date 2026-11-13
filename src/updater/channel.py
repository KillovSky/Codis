"""Canal de comunicação com a API do GitHub para o sistema de atualização."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .result import ReleaseInfo

logger = logging.getLogger("Codis.updater")

_GITHUB_OWNER = "KillovSky"
_GITHUB_REPO = "Codis"
_API_BASE = "https://api.github.com"
_TIMEOUT = 15

_MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024
_TOKEN_HOSTS = ("github.com", "githubusercontent.com")


def _assert_https_url(url: str) -> None:
    """Garante que ``url`` usa HTTPS antes de repassá-la a ``urlopen``.

    Bloqueia esquemas como ``file://`` (que fariam ``urlopen`` ler um arquivo
    local) e ``http://`` (que expõe o conteúdo baixado e o token a quem estiver
    na rede).
    """
    scheme = urllib.parse.urlparse(url).scheme.lower()
    if scheme != "https":
        raise ValueError(
            f"Esquema de URL não suportado para download: {scheme!r}")


def _is_token_host(url: str) -> bool:
    """Indica se ``url`` pertence a um host do GitHub autorizado a receber o token."""
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in _TOKEN_HOSTS)


class UpdateChannel:
    """Gerencia comunicação com a API REST do GitHub (zero dependências externas).

    Todos os métodos são síncronos - execute-os em thread dedicada.
    """

    def __init__(
        self,
        owner: str = _GITHUB_OWNER,
        repo: str = _GITHUB_REPO,
        token: str = "",
    ) -> None:
        self._owner = owner
        self._repo = repo
        self._token = token

    def fetch_latest_release(self) -> ReleaseInfo:
        """Busca os metadados da release mais recente."""
        url = f"{_API_BASE}/repos/{self._owner}/{self._repo}/releases/latest"
        data = self._get_json(url)
        return ReleaseInfo.from_api(data)

    def fetch_release_by_tag(self, tag: str) -> ReleaseInfo:
        """Busca a release de uma tag específica."""
        url = f"{_API_BASE}/repos/{self._owner}/{self._repo}/releases/tags/{tag}"
        data = self._get_json(url)
        return ReleaseInfo.from_api(data)

    def fetch_changed_files(self, base_tag: str, head_tag: str) -> list[dict]:
        """Retorna os arquivos modificados entre duas tags/commits.

        Returns:
            Lista de dicts com ``filename``, ``status`` e ``raw_url``.
        """
        url = (
            f"{_API_BASE}/repos/{self._owner}/{self._repo}"
            f"/compare/{base_tag}...{head_tag}"
        )
        data = self._get_json(url)
        files = data.get("files", [])
        return [
            {
                "filename": f["filename"],
                "status": f["status"],
                "raw_url": f.get("raw_url", ""),
                "sha": f.get("sha", ""),
                "additions": f.get("additions", 0),
                "deletions": f.get("deletions", 0),
            }
            for f in files
        ]

    def download_bytes(
        self,
        url: str,
        progress_cb: Any = None,
    ) -> bytes:
        """Baixa uma URL e retorna os bytes brutos.

        Args:
            url: URL para baixar.
            progress_cb: Callable opcional ``(downloaded: int, total: int)``.
        """
        _assert_https_url(url)
        req = self._new_request(url, accept_zip=True)
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                total = int(resp.headers.get("Content-Length", 0) or 0)
                if total > _MAX_DOWNLOAD_BYTES:
                    raise RuntimeError("O arquivo de atualização excede o tamanho máximo permitido.")
                buf = bytearray()
                chunk_size = 65536
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    buf.extend(chunk)
                    if len(buf) > _MAX_DOWNLOAD_BYTES:
                        raise RuntimeError("O arquivo de atualização excede o tamanho máximo permitido.")
                    if progress_cb:
                        progress_cb(len(buf), total)
                return bytes(buf)
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"A conexão caiu durante o download: {exc.reason}. Tente novamente."
            ) from exc
        except (OSError, ConnectionError, TimeoutError) as exc:
            raise RuntimeError(
                f"A conexão caiu durante o download: {exc}. Tente novamente."
            ) from exc

    def download_file(self, url: str) -> bytes:
        """Baixa um arquivo de texto/fonte da raw URL do GitHub."""
        _assert_https_url(url)
        req = self._new_request(url)
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                return resp.read()
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"A conexão caiu durante o download: {exc.reason}. Tente novamente."
            ) from exc
        except (OSError, ConnectionError, TimeoutError) as exc:
            raise RuntimeError(
                f"A conexão caiu durante o download: {exc}. Tente novamente."
            ) from exc

    def _get_json(self, url: str) -> Any:
        """Faz GET JSON com tratamento de erros."""
        _assert_https_url(url)
        req = self._new_request(url)
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            msg = f"GitHub API HTTP {exc.code} em {url}"
            if exc.code == 404:
                msg = f"Release não encontrada em {url}"
            elif exc.code == 403:
                msg = "GitHub API rate limit atingido — tente mais tarde"
            raise RuntimeError(msg) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Sem conexão com o GitHub: {exc.reason}") from exc

    def _new_request(self, url: str, accept_zip: bool = False) -> urllib.request.Request:
        """Cria a requisição sem deixar o token seguir redirecionamentos.

        O ``Authorization`` entra como cabeçalho não redirecionável e só para
        hosts do GitHub; assim ele não é reenviado ao CDN de assets para o qual
        o GitHub redireciona os downloads.
        """
        headers = self._build_headers(accept_zip=accept_zip)
        auth = headers.pop("Authorization", None)
        req = urllib.request.Request(url, headers=headers)
        if auth and _is_token_host(url):
            req.add_unredirected_header("Authorization", auth)
        return req

    def _build_headers(self, accept_zip: bool = False) -> dict[str, str]:
        headers: dict[str, str] = {
            "User-Agent": f"Codis-Updater/{_GITHUB_REPO}",
            "Accept": (
                "application/octet-stream"
                if accept_zip
                else "application/vnd.github+json"
            ),
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers
