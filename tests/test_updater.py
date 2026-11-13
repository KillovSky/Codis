#!/usr/bin/env python3
"""Testes unitários para o módulo src.updater.

Cobre: UpdateResult, UpdateMode, UpdateStatus, ReleaseInfo, UpdateChannel,
BackupManager e UpdateManager (todos os modos, cancelamento, versão, backup).

Todos os testes são offline - nenhuma chamada real ao GitHub é feita.
"""

from __future__ import annotations

import io
import threading
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.updater import UpdateChannel, UpdateManager, UpdateMode, UpdateResult
from src.updater.backup import BackupManager
from src.updater.result import ReleaseInfo, UpdateStatus


def _make_release_payload(**overrides) -> dict:
    """Payload mínimo válido de uma release do GitHub."""
    base = {
        "tag_name": "v2.0.0",
        "name": "Codis 2.0.0",
        "body": "Notas da release",
        "html_url": "https://github.com/KillovSky/Codis/releases/tag/v2.0.0",
        "published_at": "2026-01-01T00:00:00Z",
        "assets": [],
        "zipball_url": "https://api.github.com/repos/KillovSky/Codis/zipball/v2.0.0",
        "tarball_url": "https://api.github.com/repos/KillovSky/Codis/tarball/v2.0.0",
    }
    base.update(overrides)
    return base


def _make_release(**overrides) -> ReleaseInfo:
    return ReleaseInfo.from_api(_make_release_payload(**overrides))


def _make_zip_bytes(files: dict[str, str] | None = None) -> bytes:
    """Cria um ZIP em memória com arquivos opcionais."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.mkdir("repo-root/")
        for name, content in (files or {"src/main.py": "# main"}).items():
            zf.writestr(f"repo-root/{name}", content)
    return buf.getvalue()


@pytest.fixture()
def app_dir(tmp_path: Path) -> Path:
    """Diretório de app simulado com estrutura mínima."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("# versao 1.0.0\n", encoding="utf-8")
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    return tmp_path


@pytest.fixture()
def mock_channel() -> MagicMock:
    """Canal mockado que retorna uma release por padrão."""
    ch = MagicMock(spec=UpdateChannel)
    ch.fetch_latest_release.return_value = _make_release()
    ch.fetch_release_by_tag.return_value = _make_release()
    ch.fetch_changed_files.return_value = []
    ch.download_bytes.return_value = _make_zip_bytes()
    ch.download_file.return_value = b"# arquivo novo\n"
    return ch


@pytest.fixture()
def manager(app_dir: Path, mock_channel: MagicMock) -> UpdateManager:
    """UpdateManager na versão 1.0.0, apontando para app_dir mockado."""
    return UpdateManager(
        current_version="1.0.0",
        app_dir=app_dir,
        channel=mock_channel,
    )


class TestReleaseInfo:
    """Testa a construção e campos de ReleaseInfo."""

    def test_from_api_parses_tag_and_version(self):
        """Deve extrair version sem o 'v' prefixo da tag."""
        r = _make_release(tag_name="v1.2.3")
        assert r.tag == "v1.2.3"
        assert r.version == "1.2.3"

    def test_from_api_strips_v_prefix(self):
        """Versão deve ser sem prefixo 'v'."""
        r = ReleaseInfo.from_api(_make_release_payload(tag_name="v99.0.0"))
        assert r.version == "99.0.0"

    def test_from_api_fallback_name(self):
        """Deve usar version como name quando name está vazio."""
        r = ReleaseInfo.from_api(_make_release_payload(name=""))
        assert r.name == "2.0.0"

    def test_from_api_assets_list(self):
        """assets deve ser lista (vazia por padrão)."""
        r = _make_release()
        assert isinstance(r.assets, list)

    def test_from_api_zip_url(self):
        """zip_url deve vir de zipball_url."""
        r = _make_release()
        assert "zipball" in r.zip_url

    def test_from_api_missing_body_defaults_to_empty(self):
        """body None na API deve virar string vazia."""
        r = ReleaseInfo.from_api(_make_release_payload(body=None))
        assert r.body == ""


class TestUpdateResult:
    """Testa propriedades calculadas de UpdateResult."""

    def test_ok_true_for_success(self):
        r = UpdateResult(status=UpdateStatus.SUCCESS)
        assert r.ok is True

    def test_ok_true_for_up_to_date(self):
        r = UpdateResult(status=UpdateStatus.UP_TO_DATE)
        assert r.ok is True

    def test_ok_true_for_pending_restart(self):
        r = UpdateResult(status=UpdateStatus.PENDING_RESTART)
        assert r.ok is True

    def test_ok_true_for_update_available(self):
        r = UpdateResult(status=UpdateStatus.UPDATE_AVAILABLE)
        assert r.ok is True

    def test_ok_false_for_error(self):
        r = UpdateResult(status=UpdateStatus.ERROR)
        assert r.ok is False

    def test_ok_false_for_cancelled(self):
        r = UpdateResult(status=UpdateStatus.CANCELLED)
        assert r.ok is False

    def test_update_available_true(self):
        r = UpdateResult(status=UpdateStatus.UPDATE_AVAILABLE)
        assert r.update_available is True

    def test_update_available_false_when_up_to_date(self):
        r = UpdateResult(status=UpdateStatus.UP_TO_DATE)
        assert r.update_available is False

    def test_files_updated_default_empty(self):
        r = UpdateResult(status=UpdateStatus.SUCCESS)
        assert r.files_updated == []

    def test_restart_required_default_false(self):
        r = UpdateResult(status=UpdateStatus.SUCCESS)
        assert r.restart_required is False


class TestVersionTuple:
    """Testa a comparação semântica de versões."""

    @staticmethod
    def _vt(v: str) -> tuple:
        return UpdateManager._version_tuple(v)

    def test_simple_comparison(self):
        assert self._vt("2.0.0") > self._vt("1.9.9")

    def test_equal_versions(self):
        assert self._vt("1.0.0") == self._vt("1.0.0")

    def test_minor_version_bump(self):
        assert self._vt("1.1.0") > self._vt("1.0.9")

    def test_patch_version_bump(self):
        assert self._vt("1.0.1") > self._vt("1.0.0")

    def test_strips_v_prefix(self):
        assert self._vt("v1.2.3") == self._vt("1.2.3")

    def test_suffix_stripped(self):
        """Sufixos como -beta são ignorados."""
        assert self._vt("1.0.0-beta") == self._vt("1.0.0")

    def test_major_wins(self):
        assert self._vt("2.0.0") > self._vt("1.99.99")


class TestFmtBytes:
    @staticmethod
    def _f(n: int) -> str:
        return UpdateManager._fmt_bytes(n)

    def test_bytes_range(self):
        assert "B" in self._f(500)

    def test_kilobytes_range(self):
        assert "KB" in self._f(2048)

    def test_megabytes_range(self):
        assert "MB" in self._f(2 * 1024 * 1024)

    def test_gigabytes_range(self):
        assert "GB" in self._f(2 * 1024**3)


class TestIsProtected:
    @pytest.fixture()
    def mgr(self, app_dir, mock_channel):
        return UpdateManager("1.0.0", app_dir=app_dir, channel=mock_channel)

    def test_config_json_is_protected(self, mgr):
        assert mgr._is_protected("config.json") is True

    def test_codis_dir_is_protected(self, mgr):
        assert mgr._is_protected(".codis/settings.json") is True

    def test_plugins_dir_is_protected(self, mgr):
        assert mgr._is_protected("plugins/myplugin.py") is True

    def test_src_is_not_protected(self, mgr):
        assert mgr._is_protected("src/main.py") is False

    def test_readme_is_not_protected(self, mgr):
        assert mgr._is_protected("README.md") is False


class TestCheck:
    def test_returns_update_available_when_newer(self, manager):
        result = manager.check()
        assert result.status == UpdateStatus.UPDATE_AVAILABLE
        assert result.latest_version == "2.0.0"
        assert result.current_version == "1.0.0"

    def test_returns_up_to_date_when_same(self, app_dir, mock_channel):
        mock_channel.fetch_latest_release.return_value = _make_release(
            tag_name="v1.0.0"
        )
        mgr = UpdateManager("1.0.0", app_dir=app_dir, channel=mock_channel)
        result = mgr.check()
        assert result.status == UpdateStatus.UP_TO_DATE

    def test_returns_up_to_date_when_older_release(self, app_dir, mock_channel):
        mock_channel.fetch_latest_release.return_value = _make_release(
            tag_name="v0.9.0"
        )
        mgr = UpdateManager("1.0.0", app_dir=app_dir, channel=mock_channel)
        result = mgr.check()
        assert result.status == UpdateStatus.UP_TO_DATE

    def test_error_when_channel_raises(self, app_dir, mock_channel):
        mock_channel.fetch_latest_release.side_effect = RuntimeError(
            "sem rede")
        mgr = UpdateManager("1.0.0", app_dir=app_dir, channel=mock_channel)
        result = mgr.update(UpdateMode.CHECK_ONLY)
        assert result.status == UpdateStatus.ERROR
        assert "sem rede" in result.message

    def test_result_has_release_info(self, manager):
        result = manager.check()
        assert result.release is not None
        assert result.release.tag == "v2.0.0"

    def test_progress_callback_called(self, app_dir, mock_channel):
        calls: list = []
        mgr = UpdateManager(
            "1.0.0",
            app_dir=app_dir,
            channel=mock_channel,
            on_progress=lambda msg, pct: calls.append((msg, pct)),
        )
        mgr.check()
        assert len(calls) >= 2
        assert all(isinstance(pct, float) for _, pct in calls)


class TestUpdateSmart:
    def test_smart_no_changes_returns_success(self, manager):
        """Sem arquivos modificados, update SMART deve concluir com sucesso."""
        result = manager.update(UpdateMode.SMART)
        assert result.ok
        assert result.mode == UpdateMode.SMART

    def test_smart_dry_run_does_not_write_files(self, manager, app_dir):
        manager._channel.fetch_changed_files.return_value = [
            {
                "filename": "src/new_module.py",
                "status": "added",
                "raw_url": "https://raw.github.com/x",
                "sha": "abc",
                "additions": 5,
                "deletions": 0,
            }
        ]
        result = manager.update(UpdateMode.SMART, dry_run=True)
        assert result.ok
        assert not (app_dir / "src" / "new_module.py").exists()

    def test_smart_dry_run_lists_files(self, manager):
        manager._channel.fetch_changed_files.return_value = [
            {
                "filename": "src/foo.py",
                "status": "modified",
                "raw_url": "https://raw.github.com/x",
                "sha": "abc",
                "additions": 1,
                "deletions": 0,
            }
        ]
        result = manager.update(UpdateMode.SMART, dry_run=True)
        assert "src/foo.py" in result.files_updated

    def test_smart_falls_back_to_zip_on_compare_error(self, manager, app_dir):
        """Se Compare API falhar, deve cair para modo ZIP."""
        manager._channel.fetch_changed_files.side_effect = RuntimeError(
            "compare failed"
        )
        manager._channel.download_bytes.return_value = _make_zip_bytes()
        result = manager.update(UpdateMode.SMART)
        assert result.ok

    def test_smart_skips_protected_files(self, manager, app_dir):
        manager._channel.fetch_changed_files.return_value = [
            {
                "filename": "config.json",
                "status": "modified",
                "raw_url": "https://raw.github.com/config",
                "sha": "abc",
                "additions": 1,
                "deletions": 0,
            }
        ]
        result = manager.update(UpdateMode.SMART)
        assert result.ok
        assert "config.json" not in result.files_updated

    def test_smart_requires_restart(self, manager):
        result = manager.update(UpdateMode.SMART)
        assert result.restart_required is True

    def test_smart_cancelled_mid_operation(self, app_dir, mock_channel):
        """Cancelar durante o download deve retornar CANCELLED."""
        mgr = UpdateManager("1.0.0", app_dir=app_dir, channel=mock_channel)
        mock_channel.fetch_changed_files.return_value = [
            {
                "filename": "src/big_file.py",
                "status": "modified",
                "raw_url": "https://raw.github.com/x",
                "sha": "abc",
                "additions": 10,
                "deletions": 0,
            }
        ]

        def _cancel_and_raise(*a, **kw):
            mgr.cancel()
            raise RuntimeError("Operação cancelada durante cópia.")

        mock_channel.download_file.side_effect = _cancel_and_raise
        result = mgr.update(UpdateMode.SMART)
        assert result.status in (UpdateStatus.CANCELLED, UpdateStatus.ERROR)


class TestUpdateZip:
    def test_zip_success(self, manager):
        result = manager.update(UpdateMode.ZIP)
        assert result.ok
        assert result.mode == UpdateMode.ZIP
        assert result.restart_required is True

    def test_zip_dry_run(self, manager, app_dir):
        result = manager.update(UpdateMode.ZIP, dry_run=True)
        assert result.ok
        assert "[dry-run]" in result.message

    def test_zip_error_when_no_zip_url(self, manager):
        manager._channel.fetch_latest_release.return_value = ReleaseInfo.from_api(
            _make_release_payload(zipball_url="", assets=[])
        )
        result = manager.update(UpdateMode.ZIP)
        assert result.status == UpdateStatus.ERROR

    def test_zip_respects_protected_files(self, manager, app_dir):
        """config.json existente não deve ser sobrescrito pelo ZIP."""
        (app_dir /
         "config.json").write_text('{"key": "original"}', encoding="utf-8")
        manager._channel.download_bytes.return_value = _make_zip_bytes(
            {"config.json": '{"key": "overwritten"}'}
        )
        manager.update(UpdateMode.ZIP)
        content = (app_dir / "config.json").read_text(encoding="utf-8")
        assert "original" in content

    def test_zip_files_updated_not_empty_on_success(self, manager):
        manager._channel.download_bytes.return_value = _make_zip_bytes(
            {"src/module.py": "# module"}
        )
        result = manager.update(UpdateMode.ZIP)
        assert result.ok
        assert len(result.files_updated) > 0

    def test_zip_up_to_date_returns_early(self, app_dir, mock_channel):
        """Se já está na versão mais recente, ZIP não deve rodar."""
        mock_channel.fetch_latest_release.return_value = _make_release(
            tag_name="v1.0.0"
        )
        mgr = UpdateManager("1.0.0", app_dir=app_dir, channel=mock_channel)
        result = mgr.update(UpdateMode.ZIP)
        assert result.status == UpdateStatus.UP_TO_DATE
        mock_channel.download_bytes.assert_not_called()


class TestUpdateReinstall:
    def test_reinstall_success(self, manager):
        result = manager.update(UpdateMode.REINSTALL)
        assert result.ok
        assert result.mode == UpdateMode.REINSTALL

    def test_reinstall_dry_run(self, manager):
        result = manager.update(UpdateMode.REINSTALL, dry_run=True)
        assert result.ok
        assert "[dry-run]" in result.message

    def test_reinstall_preserves_config_json(self, manager, app_dir):
        (app_dir /
         "config.json").write_text('{"preserved": true}', encoding="utf-8")
        manager.update(UpdateMode.REINSTALL)
        assert (app_dir / "config.json").exists()

    def test_reinstall_requires_restart(self, manager):
        result = manager.update(UpdateMode.REINSTALL)
        assert result.restart_required is True


class TestUpdateForce:
    def test_force_runs_even_when_up_to_date(self, app_dir, mock_channel):
        """FORCE deve ignorar a checagem de versão."""
        mock_channel.fetch_latest_release.return_value = _make_release(
            tag_name="v1.0.0"
        )
        mgr = UpdateManager("1.0.0", app_dir=app_dir, channel=mock_channel)
        result = mgr.update(UpdateMode.FORCE)
        assert result.ok
        mock_channel.download_bytes.assert_called()

    def test_force_dry_run(self, manager):
        result = manager.update(UpdateMode.FORCE, dry_run=True)
        assert result.ok


class TestTargetTag:
    def test_target_tag_fetches_specific_release(self, manager, mock_channel):
        specific = _make_release(tag_name="v1.5.0")
        mock_channel.fetch_release_by_tag.return_value = specific
        result = manager.update(UpdateMode.ZIP, target_tag="v1.5.0")
        mock_channel.fetch_release_by_tag.assert_called_once_with("v1.5.0")
        assert result.ok

    def test_target_tag_not_found_returns_error(self, manager, mock_channel):
        mock_channel.fetch_release_by_tag.side_effect = RuntimeError("404")
        result = manager.update(UpdateMode.ZIP, target_tag="v0.0.1")
        assert result.status == UpdateStatus.ERROR
        assert "v0.0.1" in result.message


class TestCancel:
    def test_cancel_sets_event(self, manager):
        manager.cancel()
        assert manager._cancelled.is_set()

    def test_cancel_cleared_on_new_update(self, manager):
        manager.cancel()
        with patch.object(manager._cancelled, "clear") as mock_clear:
            import contextlib

            with contextlib.suppress(Exception):
                manager.update(UpdateMode.SMART)
            mock_clear.assert_called()


class TestRunInThread:
    def test_returns_thread_and_it_starts(self, manager):
        results: list[UpdateResult] = []
        t = manager.run_in_thread(
            UpdateMode.SMART,
            callback=lambda r: results.append(r),
        )
        assert isinstance(t, threading.Thread)
        t.join(timeout=10)
        assert not t.is_alive()
        assert len(results) == 1

    def test_callback_receives_result(self, manager):
        received: list[UpdateResult] = []
        t = manager.run_in_thread(
            UpdateMode.CHECK_ONLY,
            callback=lambda r: received.append(r),
        )
        t.join(timeout=10)
        assert received[0].ok

    def test_thread_is_daemon(self, manager):
        t = manager.run_in_thread(UpdateMode.CHECK_ONLY)
        assert t.daemon is True
        t.join(timeout=10)


class TestBackupManager:
    def test_create_returns_existing_path(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("# x", encoding="utf-8")
        bm = BackupManager(tmp_path)
        dest = bm.create(label="test")
        assert dest.exists()
        assert (dest / "src" / "main.py").exists()

    def test_restore_brings_back_files(self, tmp_path):
        src_file = tmp_path / "src" / "main.py"
        src_file.parent.mkdir()
        src_file.write_text("# original", encoding="utf-8")

        bm = BackupManager(tmp_path)
        bm.create(label="restore_test")

        src_file.write_text("# corrupted", encoding="utf-8")

        ok = bm.restore()
        assert ok is True
        assert src_file.read_text(encoding="utf-8") == "# original"

    def test_restore_returns_false_without_backup(self, tmp_path):
        bm = BackupManager(tmp_path)
        assert bm.restore() is False

    def test_cleanup_removes_backup(self, tmp_path):
        (tmp_path / "f.py").write_text("# f", encoding="utf-8")
        bm = BackupManager(tmp_path)
        bm.create()
        backup_dir = bm._backup_dir
        bm.cleanup()
        assert bm._backup_dir is None
        assert not (backup_dir and backup_dir.exists())

    def test_list_tracked_files_empty_before_create(self, tmp_path):
        bm = BackupManager(tmp_path)
        assert bm.list_tracked_files() == []

    def test_list_tracked_files_after_create(self, tmp_path):
        (tmp_path / "a.py").write_text("# a", encoding="utf-8")
        (tmp_path / "b.py").write_text("# b", encoding="utf-8")
        bm = BackupManager(tmp_path)
        bm.create()
        files = bm.list_tracked_files()
        names = [Path(f).name for f in files]
        assert "a.py" in names
        assert "b.py" in names
        bm.cleanup()

    def test_context_manager_restores_on_exception(self, tmp_path):
        original = tmp_path / "main.py"
        original.write_text("# original", encoding="utf-8")
        try:
            with BackupManager(tmp_path) as bm:
                bm.create()
                original.write_text("# broken", encoding="utf-8")
                raise RuntimeError("falha simulada")
        except RuntimeError:
            pass
        assert original.read_text(encoding="utf-8") == "# original"

    def test_context_manager_cleanup_on_success(self, tmp_path):
        (tmp_path / "x.py").write_text("# x", encoding="utf-8")
        with BackupManager(tmp_path) as bm:
            bm.create()
        assert bm._backup_dir is None


class TestUpdateChannelHeaders:
    """Testa a construção interna de headers do canal."""

    def _channel(self, token: str = "") -> UpdateChannel:
        return UpdateChannel(token=token)

    def test_headers_without_token(self):
        ch = self._channel()
        headers = ch._build_headers()
        assert "Authorization" not in headers
        assert "User-Agent" in headers

    def test_headers_with_token(self):
        ch = self._channel(token="ghp_secret")
        headers = ch._build_headers()
        assert headers["Authorization"] == "Bearer ghp_secret"

    def test_headers_accept_zip(self):
        ch = self._channel()
        headers = ch._build_headers(accept_zip=True)
        assert headers["Accept"] == "application/octet-stream"

    def test_headers_accept_json_default(self):
        ch = self._channel()
        headers = ch._build_headers()
        assert "github" in headers["Accept"]

    def test_get_json_raises_on_404(self):
        import urllib.error

        ch = self._channel()
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.side_effect = urllib.error.HTTPError(
                url="", code=404, msg="Not Found", hdrs=None, fp=None  # type: ignore
            )
            with pytest.raises(RuntimeError, match="não encontrada"):
                ch._get_json(
                    "https://api.github.com/repos/x/y/releases/tags/vX")

    def test_get_json_raises_on_403(self):
        import urllib.error

        ch = self._channel()
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.side_effect = urllib.error.HTTPError(
                url="", code=403, msg="Forbidden", hdrs=None, fp=None  # type: ignore
            )
            with pytest.raises(RuntimeError, match="rate limit"):
                ch._get_json(
                    "https://api.github.com/repos/x/y/releases/latest")

    def test_get_json_raises_on_url_error(self):
        import urllib.error

        ch = self._channel()
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.side_effect = urllib.error.URLError(
                reason="Name resolution failed"
            )
            with pytest.raises(RuntimeError, match="GitHub"):
                ch._get_json(
                    "https://api.github.com/repos/x/y/releases/latest")

    def test_fetch_latest_release_parses_response(self):
        ch = self._channel()
        payload = _make_release_payload()
        with patch.object(ch, "_get_json", return_value=payload):
            release = ch.fetch_latest_release()
        assert release.version == "2.0.0"

    def test_fetch_changed_files_parses_list(self):
        ch = self._channel()
        api_resp = {
            "files": [
                {
                    "filename": "src/foo.py",
                    "status": "modified",
                    "raw_url": "https://raw.github.com/x",
                    "sha": "abc",
                    "additions": 2,
                    "deletions": 1,
                }
            ]
        }
        with patch.object(ch, "_get_json", return_value=api_resp):
            files = ch.fetch_changed_files("v1.0.0", "v2.0.0")
        assert len(files) == 1
        assert files[0]["filename"] == "src/foo.py"

    def test_download_bytes_returns_content(self):
        ch = self._channel()
        fake_resp = MagicMock()
        fake_resp.__enter__ = lambda s: s
        fake_resp.__exit__ = MagicMock(return_value=False)
        fake_resp.headers.get.return_value = "5"
        fake_resp.read.side_effect = [b"hello", b""]
        with patch("urllib.request.urlopen", return_value=fake_resp):
            data = ch.download_bytes("https://example.com/file.zip")
        assert data == b"hello"

    def test_download_bytes_calls_progress(self):
        ch = self._channel()
        calls: list = []
        fake_resp = MagicMock()
        fake_resp.__enter__ = lambda s: s
        fake_resp.__exit__ = MagicMock(return_value=False)
        fake_resp.headers.get.return_value = "5"
        fake_resp.read.side_effect = [b"hello", b""]
        with patch("urllib.request.urlopen", return_value=fake_resp):
            ch.download_bytes(
                "https://example.com/file.zip",
                progress_cb=lambda done, total: calls.append((done, total)),
            )
        assert len(calls) > 0


class TestOverwriteFromDir:
    def test_protected_files_skipped(self, manager, tmp_path):
        src = tmp_path / "extracted"
        src.mkdir()
        (src / "config.json").write_text('{"bad": true}', encoding="utf-8")
        (src / "README.md").write_text("# New README", encoding="utf-8")

        updated: list[str] = []
        manager._overwrite_from_dir(src, manager._app_dir, updated)

        assert "config.json" not in updated
        assert "README.md" in updated

    def test_subdirectory_created(self, manager, tmp_path):
        src = tmp_path / "extracted"
        (src / "subdir").mkdir(parents=True)
        (src / "subdir" / "module.py").write_text("# m", encoding="utf-8")

        updated: list[str] = []
        manager._overwrite_from_dir(src, manager._app_dir, updated)

        assert (manager._app_dir / "subdir" / "module.py").exists()


class TestClearPycache:
    def test_removes_pycache_dirs(self, manager, app_dir):
        cache = app_dir / "src" / "__pycache__"
        cache.mkdir(parents=True)
        (cache / "main.cpython-311.pyc").write_bytes(b"\x00")
        manager._clear_pycache()
        assert not cache.exists()

    def test_removes_pyc_files(self, manager, app_dir):
        pyc = app_dir / "src" / "main.pyc"
        pyc.write_bytes(b"\x00")
        manager._clear_pycache()
        assert not pyc.exists()
