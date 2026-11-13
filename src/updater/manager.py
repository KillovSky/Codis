"""Gerenciador central do sistema de atualização do Codis.

Modos suportados
----------------
- :attr:`UpdateMode.CHECK_ONLY` - apenas verifica se há versão nova
- :attr:`UpdateMode.SMART` - aplica somente os arquivos modificados
- :attr:`UpdateMode.ZIP` - baixa o ZIP da release e sobrescreve
- :attr:`UpdateMode.REINSTALL` - reinstala do zero (remove + ZIP)
- :attr:`UpdateMode.FORCE` - força reinstalação mesmo na versão atual
- :attr:`UpdateMode.BINARY` - substitui executável PyInstaller em tempo real
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import logging
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile
from collections.abc import Callable
from pathlib import Path

from .backup import BackupManager
from .channel import UpdateChannel
from .result import ReleaseInfo, UpdateMode, UpdateResult, UpdateStatus

logger = logging.getLogger("Codis.updater")

_PROTECTED = frozenset(
    {
        "config.json",
        ".codis",
        "logs",
        "plugins",
        "themes",
        "snippets",
        "session.json",
        "keybindings.json",
    }
)

_DEFAULT_APP_DIR = Path(__file__).resolve().parents[2]


def _safe_extractall(zf: zipfile.ZipFile, dest: Path) -> None:
    """Extrai um ZIP para ``dest`` validando cada entrada contra path traversal.

    Protege o auto-updater contra zip-slip: sem essa validação, uma release
    (ou uma resposta HTTP interceptada/adulterada) contendo entradas como
    ``../../etc/cron.d/evil`` poderia escrever arquivos fora do diretório
    temporário de extração.
    """
    resolved_dest = dest.resolve()
    safe_members = []
    for info in zf.infolist():
        try:
            resolved_target = (dest / info.filename).resolve()
        except (OSError, ValueError):
            continue
        if resolved_target == resolved_dest or resolved_dest in resolved_target.parents:
            safe_members.append(info)
        else:
            logging.getLogger("Codis").warning(
                "Updater: ignorando entrada insegura no ZIP (path traversal): %r",
                info.filename,
            )
    zf.extractall(dest, members=safe_members)


class UpdateManager:
    """Orquestra todos os modos de atualização do Codis.

    Parâmetros
    ----------
    current_version:
        Versão instalada (ex. ``"1.0.0"``).
    app_dir:
        Diretório raiz da instalação. Se omitido, autodetecta.
    channel:
        Canal de comunicação com o GitHub. Se omitido, usa o padrão.
    github_token:
        Token pessoal do GitHub (opcional, aumenta rate limit).
    on_progress:
        Callback ``(message: str, percent: float)`` chamado durante operações.
    """

    def __init__(
        self,
        current_version: str,
        app_dir: Path | None = None,
        channel: UpdateChannel | None = None,
        github_token: str = "",
        on_progress: Callable[[str, float], None] | None = None,
    ) -> None:
        self._current_version = current_version.lstrip("v")
        self._app_dir = app_dir or _DEFAULT_APP_DIR
        self._channel = channel or UpdateChannel(token=github_token)
        self._on_progress = on_progress
        self._lock = threading.Lock()
        self._cancelled = threading.Event()

    def check(self) -> UpdateResult:
        """Verifica se há uma versão mais nova sem instalar nada."""
        return self._run_check(mode=UpdateMode.CHECK_ONLY)

    def update(
        self,
        mode: UpdateMode = UpdateMode.SMART,
        *,
        target_tag: str = "",
        dry_run: bool = False,
    ) -> UpdateResult:
        """Executa a atualização no modo especificado.

        Parâmetros
        ----------
        mode:
            Modo de atualização desejado.
        target_tag:
            Se fornecido, usa essa tag em vez da release mais recente.
        dry_run:
            Se ``True``, apenas simula a operação sem modificar nada.
        """
        with self._lock:
            self._cancelled.clear()
            return self._dispatch(mode, target_tag=target_tag, dry_run=dry_run)

    def cancel(self) -> None:
        """Sinaliza para cancelar a operação em andamento."""
        self._cancelled.set()

    def run_in_thread(
        self,
        mode: UpdateMode = UpdateMode.SMART,
        *,
        target_tag: str = "",
        callback: Callable[[UpdateResult], None] | None = None,
    ) -> threading.Thread:
        """Executa a atualização em thread separada (não bloqueia a UI).

        Retorna a thread criada (já iniciada).
        """

        def _worker() -> None:
            result = self.update(mode, target_tag=target_tag)
            if callback:
                callback(result)

        t = threading.Thread(target=_worker, daemon=True, name="codis-updater")
        t.start()
        return t

    def _dispatch(
        self,
        mode: UpdateMode,
        target_tag: str,
        dry_run: bool,
    ) -> UpdateResult:
        try:
            result = self._run_check(mode)
        except Exception as exc:  # pylint: disable=broad-except
            return UpdateResult(
                status=UpdateStatus.ERROR,
                mode=mode,
                current_version=self._current_version,
                message=str(exc),
                error=exc,
            )

        if mode == UpdateMode.CHECK_ONLY:
            return result

        skip_version_check = mode in (UpdateMode.FORCE, UpdateMode.BINARY)
        if (
            result.status == UpdateStatus.UP_TO_DATE
            and not skip_version_check
            and not target_tag
        ):
            return result

        if result.release is None:
            return UpdateResult(
                status=UpdateStatus.ERROR,
                mode=mode,
                current_version=self._current_version,
                message="Nenhuma release encontrada no GitHub.",
            )

        release = result.release
        if target_tag:
            try:
                release = self._channel.fetch_release_by_tag(target_tag)
            except Exception as exc:  # pylint: disable=broad-except
                return UpdateResult(
                    status=UpdateStatus.ERROR,
                    mode=mode,
                    current_version=self._current_version,
                    message=f"Tag '{target_tag}' não encontrada: {exc}",
                    error=exc,
                )

        dispatch: dict[UpdateMode, Callable[..., UpdateResult]] = {
            UpdateMode.SMART: self._update_smart,
            UpdateMode.ZIP: self._update_zip,
            UpdateMode.REINSTALL: self._update_reinstall,
            UpdateMode.FORCE: self._update_reinstall,
            UpdateMode.BINARY: self._update_binary,
        }
        handler = dispatch.get(mode, self._update_zip)
        try:
            return handler(release=release, dry_run=dry_run)
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception("Erro durante atualização no modo %s", mode)
            return UpdateResult(
                status=UpdateStatus.ERROR,
                mode=mode,
                current_version=self._current_version,
                latest_version=release.version,
                release=release,
                message=f"Erro durante atualização: {exc}",
                error=exc,
            )

    def _run_check(self, mode: UpdateMode) -> UpdateResult:
        """Busca a release mais recente e compara com a versão instalada."""
        self._progress("Verificando atualizações…", 0.0)
        release = self._channel.fetch_latest_release()
        self._progress(f"Release mais recente: {release.tag}", 0.5)
        latest = release.version

        if self._version_tuple(latest) > self._version_tuple(self._current_version):
            status = UpdateStatus.UPDATE_AVAILABLE
            message = f"Nova versão disponível: {release.tag}"
        else:
            status = UpdateStatus.UP_TO_DATE
            message = f"Codis está atualizado ({self._current_version})"

        self._progress(message, 1.0)
        return UpdateResult(
            status=status,
            mode=mode,
            current_version=self._current_version,
            latest_version=latest,
            release=release,
            message=message,
        )

    def _update_smart(self, release: ReleaseInfo, dry_run: bool) -> UpdateResult:
        """Baixa somente os arquivos alterados entre versões via GitHub Compare."""
        self._progress("Identificando arquivos modificados…", 0.05)

        base_tag = f"v{self._current_version}"
        head_tag = release.tag

        try:
            changed = self._channel.fetch_changed_files(base_tag, head_tag)
        except RuntimeError:
            logger.warning(
                "Compare API falhou entre %s...%s, caindo para ZIP.",
                base_tag,
                head_tag,
            )
            return self._update_zip(release=release, dry_run=dry_run)

        if self._cancelled.is_set():
            return UpdateResult(status=UpdateStatus.CANCELLED, mode=UpdateMode.SMART)

        processable = [
            f
            for f in changed
            if f["status"] != "removed"
            and not self._is_protected(f["filename"])
            and f["raw_url"]
        ]
        removed = [
            f
            for f in changed
            if f["status"] == "removed" and not self._is_protected(f["filename"])
        ]

        self._progress(
            f"{len(processable)} arquivos a atualizar, {len(removed)} a remover",
            0.1,
        )

        if dry_run:
            return UpdateResult(
                status=UpdateStatus.SUCCESS,
                mode=UpdateMode.SMART,
                current_version=self._current_version,
                latest_version=release.version,
                release=release,
                message=f"[dry-run] {len(processable)} modificados, {len(removed)} removidos",
                files_updated=[f["filename"] for f in processable],
            )

        backup = BackupManager(self._app_dir)
        backup.create(label=f"{self._current_version}_smart")
        updated: list[str] = []
        backed_up = backup.list_tracked_files()

        try:
            total = len(processable) + len(removed)
            for idx, finfo in enumerate(processable):
                if self._cancelled.is_set():
                    backup.restore()
                    return UpdateResult(
                        status=UpdateStatus.CANCELLED,
                        mode=UpdateMode.SMART,
                    )

                dest = self._app_dir / finfo["filename"]
                self._progress(
                    f"Baixando {finfo['filename']}…",
                    0.1 + 0.85 * (idx / max(total, 1)),
                )

                raw = self._channel.download_file(finfo["raw_url"])
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(raw)
                updated.append(finfo["filename"])

            for finfo in removed:
                target = self._app_dir / finfo["filename"]
                with contextlib.suppress(FileNotFoundError):
                    target.unlink()
                    updated.append(f"[removed] {finfo['filename']}")

            self._clear_pycache()
            self._progress("Smart update concluído!", 1.0)
            backup.cleanup()

        except Exception:
            backup.restore()
            backup.cleanup()
            raise

        return UpdateResult(
            status=UpdateStatus.SUCCESS,
            mode=UpdateMode.SMART,
            current_version=self._current_version,
            latest_version=release.version,
            release=release,
            message=f"Atualização aplicada: {len(updated)} arquivo(s) modificado(s)",
            files_updated=updated,
            files_backed_up=backed_up,
            restart_required=True,
        )

    def _update_zip(self, release: ReleaseInfo, dry_run: bool) -> UpdateResult:
        """Baixa o ZIP da release e sobrescreve os arquivos existentes."""
        self._progress("Preparando download do ZIP…", 0.02)

        zip_url = self._resolve_zip_asset(release)
        if not zip_url:
            raise RuntimeError("Nenhum ZIP de release encontrado no GitHub.")

        if dry_run:
            return UpdateResult(
                status=UpdateStatus.SUCCESS,
                mode=UpdateMode.ZIP,
                current_version=self._current_version,
                latest_version=release.version,
                release=release,
                message=f"[dry-run] Baixaria ZIP de {zip_url}",
            )

        self._progress("Baixando ZIP da release…", 0.05)
        zip_data = self._channel.download_bytes(
            zip_url,
            progress_cb=lambda done, total: self._progress(
                f"Baixando… {self._fmt_bytes(done)}/{self._fmt_bytes(total)}",
                0.05 + 0.55 * (done / max(total, 1)),
            ),
        )

        if self._cancelled.is_set():
            return UpdateResult(status=UpdateStatus.CANCELLED, mode=UpdateMode.ZIP)

        self._progress("Verificando integridade do download…", 0.58)
        self._verify_zip_checksum(release, zip_data)

        self._progress("Extraindo ZIP…", 0.60)
        with tempfile.TemporaryDirectory(prefix="codis_zip_") as tmp:
            tmp_path = Path(tmp)
            with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
                _safe_extractall(zf, tmp_path)

            extracted_root = self._find_zip_root(tmp_path)

            backup = BackupManager(self._app_dir)
            backup.create(label=f"{self._current_version}_zip")
            backed_up = backup.list_tracked_files()
            updated: list[str] = []

            try:
                self._overwrite_from_dir(
                    extracted_root,
                    self._app_dir,
                    updated,
                    progress_base=0.65,
                )
                self._clear_pycache()
                self._progress("ZIP aplicado com sucesso!", 1.0)
                backup.cleanup()
            except Exception:
                backup.restore()
                backup.cleanup()
                raise

        return UpdateResult(
            status=UpdateStatus.SUCCESS,
            mode=UpdateMode.ZIP,
            current_version=self._current_version,
            latest_version=release.version,
            release=release,
            message=f"Atualização via ZIP concluída ({len(updated)} arquivo(s))",
            files_updated=updated,
            files_backed_up=backed_up,
            restart_required=True,
        )

    def _update_reinstall(self, release: ReleaseInfo, dry_run: bool) -> UpdateResult:
        """Remove a instalação existente e instala do zero via ZIP."""
        self._progress("Preparando reinstalação completa…", 0.02)

        zip_url = self._resolve_zip_asset(release)
        if not zip_url:
            raise RuntimeError(
                "Nenhum ZIP de release encontrado para reinstalação.")

        if dry_run:
            return UpdateResult(
                status=UpdateStatus.SUCCESS,
                mode=UpdateMode.REINSTALL,
                current_version=self._current_version,
                latest_version=release.version,
                release=release,
                message=f"[dry-run] Reinstalaria do ZIP {zip_url}",
            )

        self._progress("Baixando ZIP para reinstalação…", 0.05)
        zip_data = self._channel.download_bytes(
            zip_url,
            progress_cb=lambda done, total: self._progress(
                f"Baixando… {self._fmt_bytes(done)}/{self._fmt_bytes(total)}",
                0.05 + 0.45 * (done / max(total, 1)),
            ),
        )

        if self._cancelled.is_set():
            return UpdateResult(
                status=UpdateStatus.CANCELLED, mode=UpdateMode.REINSTALL
            )

        self._verify_zip_checksum(release, zip_data)

        self._progress("Criando backup de segurança…", 0.52)
        backup = BackupManager(self._app_dir)
        backup.create(label=f"{self._current_version}_reinstall")
        backed_up = backup.list_tracked_files()

        with tempfile.TemporaryDirectory(prefix="codis_reinstall_") as tmp:
            tmp_path = Path(tmp)
            with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
                _safe_extractall(zf, tmp_path)
            extracted_root = self._find_zip_root(tmp_path)

            self._progress("Removendo instalação anterior…", 0.58)
            try:
                self._remove_app_files(keep_protected=True)
            except OSError as exc:
                backup.restore()
                backup.cleanup()
                raise RuntimeError(
                    f"Falha ao remover arquivos: {exc}") from exc

            updated: list[str] = []
            try:
                self._overwrite_from_dir(
                    extracted_root,
                    self._app_dir,
                    updated,
                    progress_base=0.62,
                )
                self._clear_pycache()
                self._progress("Reinstalação concluída!", 1.0)
                backup.cleanup()
            except Exception:
                backup.restore()
                backup.cleanup()
                raise

        return UpdateResult(
            status=UpdateStatus.SUCCESS,
            mode=UpdateMode.REINSTALL,
            current_version=self._current_version,
            latest_version=release.version,
            release=release,
            message=f"Reinstalação concluída ({len(updated)} arquivo(s))",
            files_updated=updated,
            files_backed_up=backed_up,
            restart_required=True,
        )

    def _update_binary(self, release: ReleaseInfo, dry_run: bool) -> UpdateResult:
        """Substitui o executável empacotado (estilo AnyDesk/VSCode).

        Estratégia
        ----------
        1. Localiza o asset de executável correto para a plataforma.
        2. Baixa para um arquivo temporário ao lado do executável atual.
        3. No Windows: usa um script batch/subprocess pós-restart.
           Em Unix: rename atômico (funciona mesmo com o arquivo aberto).
        4. Reinicia o processo com o novo executável.
        """
        if not self._is_frozen():
            raise RuntimeError(
                "Modo BINARY requer que o Codis esteja empacotado com PyInstaller. "
                "Em ambiente de desenvolvimento, use ZIP ou SMART."
            )

        exe_path = Path(sys.executable)
        asset = self._find_binary_asset(release, exe_path)

        if not asset:
            raise RuntimeError(
                f"Nenhum executável compatível encontrado para "
                f"{platform.system()}/{platform.machine()} na release {release.tag}."
            )

        self._progress(f"Baixando executável {asset['name']}…", 0.05)

        if dry_run:
            return UpdateResult(
                status=UpdateStatus.SUCCESS,
                mode=UpdateMode.BINARY,
                current_version=self._current_version,
                latest_version=release.version,
                release=release,
                message=f"[dry-run] Baixaria {asset['name']} ({self._fmt_bytes(asset['size'])})",
            )

        binary_data = self._channel.download_bytes(
            asset["browser_download_url"],
            progress_cb=lambda done, total: self._progress(
                f"Baixando… {self._fmt_bytes(done)}/{self._fmt_bytes(total)}",
                0.05 + 0.80 * (done / max(total, 1)),
            ),
        )

        if self._cancelled.is_set():
            return UpdateResult(status=UpdateStatus.CANCELLED, mode=UpdateMode.BINARY)

        self._progress("Preparando substituição do executável…", 0.87)

        tmp_exe = exe_path.with_suffix(".new")
        tmp_exe.write_bytes(binary_data)

        if sys.platform != "win32":
            tmp_exe.chmod(tmp_exe.stat().st_mode | 0o111)

        bak_exe = exe_path.with_suffix(".bak")
        with contextlib.suppress(FileNotFoundError):
            bak_exe.unlink()

        self._progress("Aplicando substituto…", 0.92)

        if sys.platform == "win32":
            self._schedule_windows_swap(exe_path, tmp_exe, bak_exe)
            return UpdateResult(
                status=UpdateStatus.PENDING_RESTART,
                mode=UpdateMode.BINARY,
                current_version=self._current_version,
                latest_version=release.version,
                release=release,
                message="Executável baixado. Reinicie o Codis para concluir a atualização.",
                restart_required=True,
            )

        exe_path.rename(bak_exe)
        tmp_exe.rename(exe_path)

        self._progress("Executável substituído! Reiniciando…", 1.0)

        return UpdateResult(
            status=UpdateStatus.PENDING_RESTART,
            mode=UpdateMode.BINARY,
            current_version=self._current_version,
            latest_version=release.version,
            release=release,
            message="Executável atualizado. Reinicie o Codis.",
            restart_required=True,
        )

    @staticmethod
    def restart_app(args: list[str] | None = None) -> None:
        """Reinicia o processo do Codis com o executável atual.

        Deve ser chamado após o usuário confirmar o restart na UI.
        """
        exe = sys.executable
        argv = args or sys.argv
        logger.info("Reiniciando Codis: %s %s", exe, argv)
        os.execv(exe, [exe] + argv)

    def _overwrite_from_dir(
        self,
        src: Path,
        dst: Path,
        updated: list[str],
        progress_base: float = 0.65,
    ) -> None:
        """Copia recursivamente src -> dst, respeitando _PROTECTED."""
        all_files = [p for p in src.rglob("*") if p.is_file()]
        total = len(all_files)
        for idx, src_file in enumerate(all_files):
            rel = src_file.relative_to(src)
            if self._is_protected(str(rel)):
                continue
            dest_file = dst / rel
            dest_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, dest_file)
            updated.append(str(rel))
            self._progress(
                f"Copiando {rel}…",
                progress_base + (1.0 - progress_base) * (idx / max(total, 1)),
            )
            if self._cancelled.is_set():
                raise RuntimeError("Operação cancelada durante cópia.")

    def _remove_app_files(self, keep_protected: bool = True) -> None:
        """Remove os arquivos da instalação atual."""
        for item in self._app_dir.iterdir():
            if keep_protected and self._is_protected(item.name):
                continue
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                with contextlib.suppress(OSError):
                    item.unlink()

    def _clear_pycache(self) -> None:
        """Remove todos os __pycache__ e .pyc após update."""
        for cache_dir in self._app_dir.rglob("__pycache__"):
            shutil.rmtree(cache_dir, ignore_errors=True)
        for pyc in self._app_dir.rglob("*.pyc"):
            with contextlib.suppress(OSError):
                pyc.unlink()

    def _is_protected(self, relative_path: str) -> bool:
        """Retorna True se o caminho relativo está na lista de proteção."""
        parts = Path(relative_path).parts
        return bool(parts and parts[0] in _PROTECTED)

    def _find_zip_root(self, extracted: Path) -> Path:
        """Encontra o diretório raiz dentro de um ZIP extraído do GitHub."""
        children = [p for p in extracted.iterdir() if p.is_dir()]
        if len(children) == 1:
            return children[0]
        return extracted

    def _resolve_zip_asset(self, release: ReleaseInfo) -> str:
        """Retorna a URL do ZIP: prefere asset explícito, fallback para zipball."""
        for asset in release.assets:
            name: str = asset.get("name", "")
            if name.endswith(".zip") and "source" not in name.lower():
                return asset["browser_download_url"]
        return release.zip_url

    def _resolve_zip_asset_name(self, release: ReleaseInfo) -> str | None:
        """Retorna o nome do asset ZIP escolhido por ``_resolve_zip_asset``, se houver."""
        for asset in release.assets:
            name: str = asset.get("name", "")
            if name.endswith(".zip") and "source" not in name.lower():
                return name
        return None

    def _verify_zip_checksum(self, release: ReleaseInfo, zip_data: bytes) -> None:
        """Verifica o SHA-256 do ZIP baixado contra um asset de checksums da release.

        Procura por um asset chamado ``SHA256SUMS``/``SHA256SUMS.txt``/
        ``checksums.txt`` na release e, se encontrado, confirma que o hash do
        ZIP baixado bate com o publicado antes de prosseguir com a
        atualização. Isso mitiga (parcialmente) comprometimento do
        artefato/release sem exigir infraestrutura de assinatura GPG.

        Se a release não publicar um arquivo de checksums, a verificação é
        pulada com um aviso no log. Se o arquivo existir mas não puder ser
        baixado, ou não listar o asset, a atualização é abortada, pois isso
        é indistinguível de uma adulteração.
        """
        checksum_asset = None
        for asset in release.assets:
            name = asset.get("name", "").lower()
            if name in ("sha256sums", "sha256sums.txt", "checksums.txt"):
                checksum_asset = asset
                break

        if checksum_asset is None:
            logger.warning(
                "Updater: release %s não publica um arquivo de checksums "
                "(SHA256SUMS) — integridade do ZIP não pôde ser verificada.",
                release.version,
            )
            return

        asset_name = self._resolve_zip_asset_name(release)
        if asset_name is None:
            raise RuntimeError(
                "Não foi possível identificar o asset do ZIP para conferir o checksum. "
                "A atualização foi abortada por segurança."
            )
        try:
            sums_text = self._channel.download_file(
                checksum_asset["browser_download_url"]
            ).decode("utf-8", errors="replace")
        except (RuntimeError, OSError, ValueError) as exc:
            raise RuntimeError(
                f"Falha ao baixar o arquivo de checksums: {exc}. "
                "A atualização foi abortada por segurança."
            ) from exc

        actual_hash = hashlib.sha256(zip_data).hexdigest()
        expected_hash = None
        for line in sums_text.splitlines():
            parts = line.strip().split()
            if len(parts) != 2:
                continue
            candidate_hash, candidate_name = parts
            if candidate_name.lstrip("*") == asset_name:
                expected_hash = candidate_hash.lower()
                break

        if expected_hash is None:
            raise RuntimeError(
                f"Nenhum hash para '{asset_name}' no arquivo de checksums. "
                "A atualização foi abortada por segurança."
            )

        if actual_hash.lower() != expected_hash:
            raise RuntimeError(
                "Falha de verificação de integridade: o hash SHA-256 do ZIP "
                "baixado não corresponde ao publicado na release. A "
                "atualização foi abortada por segurança."
            )
        logger.info("Updater: checksum SHA-256 do ZIP verificado com sucesso.")

    def _find_binary_asset(self, release: ReleaseInfo, exe_path: Path) -> dict | None:
        """Localiza o asset de executável correto para a plataforma atual."""
        system = platform.system().lower()
        machine = platform.machine().lower()

        arch_aliases: dict[str, list[str]] = {
            "x86_64": ["x86_64", "amd64", "x64"],
            "aarch64": ["aarch64", "arm64"],
            "armv7l": ["armv7", "arm32", "arm"],
            "i686": ["x86", "i686", "i386", "x86_32"],
        }
        arch_keys = arch_aliases.get(machine, [machine])

        ext = ".exe" if system == "windows" else ""

        scored: list[tuple[int, dict]] = []
        for asset in release.assets:
            name: str = asset.get("name", "").lower()
            if not name.endswith(ext if ext else ("", ".bin", "")):
                continue
            score = 0
            if system in name:
                score += 10
            for key in arch_keys:
                if key in name:
                    score += 5
                    break
            if "codis" in name:
                score += 3
            if score > 0:
                scored.append((score, asset))

        if not scored:
            return None
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1]

    def _schedule_windows_swap(
        self,
        current: Path,
        new_exe: Path,
        backup: Path,
    ) -> None:
        """Cria um script .bat que troca os executáveis após o processo terminar."""
        bat_path = current.parent / "_codis_update.bat"
        script = (
            "@echo off\n"
            f":WAIT\n"
            f'tasklist /fi "PID eq {os.getpid()}" 2>NUL | find /I "codis" >NUL\n'
            f"if not errorlevel 1 (timeout /t 1 /nobreak >NUL & goto WAIT)\n"
            f'move /y "{current}" "{backup}"\n'
            f'move /y "{new_exe}" "{current}"\n'
            f'start "" "{current}"\n'
            f'del "%~f0"\n'
        )
        bat_path.write_text(script, encoding="ascii")
        subprocess.Popen(  # pylint: disable=consider-using-with
            ["cmd.exe", "/c", str(bat_path)],
            # type: ignore[attr-defined]
            creationflags=subprocess.CREATE_NO_WINDOW,
            close_fds=True,
        )

    @staticmethod
    def _is_frozen() -> bool:
        """Retorna True se rodando como executável PyInstaller."""
        return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")

    def _progress(self, message: str, percent: float) -> None:
        """Emite progresso para o callback registrado."""
        logger.debug("[%.0f%%] %s", percent * 100, message)
        if self._on_progress:
            with contextlib.suppress(Exception):
                self._on_progress(message, percent)

    @staticmethod
    def _version_tuple(version: str) -> tuple[int, ...]:
        """Converte ``"1.2.3"`` em ``(1, 2, 3)`` para comparação."""
        parts = version.lstrip("v").split(".")
        result = []
        for part in parts:
            numeric = ""
            for ch in part:
                if ch.isdigit():
                    numeric += ch
                else:
                    break
            result.append(int(numeric) if numeric else 0)
        return tuple(result)

    @staticmethod
    def _fmt_bytes(n: int) -> str:
        """Formata bytes de forma legível."""
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024:
                return f"{n:.1f} {unit}"
            n //= 1024
        return f"{n:.1f} TB"
