"""Codis - PluginLoader.

* Passa ``plugin_id`` ao construtor de PluginAPI - cada plugin tem sua
  própria instância rastreável.
* Chama ``api._teardown()`` na desativação para limpar timers, storage
  e canais de saída automaticamente.
* Emite eventos ``plugin_loaded`` / ``plugin_unloaded`` no barramento global.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import logging
import shutil
import tkinter as tk
import traceback
import zipfile
from pathlib import Path
from typing import Any

from src.constants import PLUGINS_DIR
from src.i18n import _t
from src.plugins.api import (
    PluginAPI,
    PluginManifest,
    _get_plugin_readme,
    _get_plugin_source,
    _load_trusted_plugins,
    _normalize_manifest,
    _sandbox_check_imports,
    _save_trusted_plugins,
)

logger = logging.getLogger("Codis")

_MAX_PLUGIN_BYTES = 50 * 1024 * 1024


def _is_inside(path: Path, root: Path) -> bool:
    """Indica se ``path``, já resolvido, está dentro de ``root`` (ou é o próprio ``root``)."""
    return path == root or root in path.parents


class PluginLoader:
    """
    Carregador robusto de plugins com sandbox e análise estática.

    Suporta: módulos .py simples, .zip e pastas com plugin.py.

    Interface do plugin:
        MANIFEST = PluginManifest(name="...", version="...", ...)
        def activate(api: PluginAPI) -> None: ...
        def deactivate() -> None: ...

    Sandbox:
        - Análise estática de imports perigosos via AST antes de carregar
        - Lista de plugins confiáveis persistida em trusted_plugins.json
        - Aviso ao usuário antes de instalar plugin não confiável
    """

    def __init__(self, app_api: PluginAPI) -> None:
        """Inicializa o carregador de plugins do Codis."""
        self._app_api = app_api
        self._t = _t
        self._loaded: dict[str, Any] = {}
        self._apis: dict[str, PluginAPI] = {}
        self._errors: dict[str, str] = {}
        self._active: set[str] = set()
        self._disabled: set[str] = set()
        self._paths: dict[str, Path] = {}
        self._trusted: dict[str, str] = _load_trusted_plugins()
        self._proc_mgr: Any | None = None

    def load_all(self) -> None:
        """Carrega todos os plugins do diretório de instalação, em ordem estável."""
        PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
        candidatos: list[Path] = []
        vistos: set[str] = set()

        todas = sorted(PLUGINS_DIR.iterdir(),
                       key=lambda p: 0 if p.is_dir() else 1)
        for entry in todas:
            if entry.name.startswith("_"):
                continue
            stem = entry.stem
            if (
                entry.is_dir()
                and (entry / "plugin.py").exists()
                or entry.suffix == ".zip"
                and stem not in vistos
                or entry.suffix == ".py"
                and stem not in vistos
            ):
                candidatos.append(entry)
                vistos.add(stem)

        for c in candidatos:
            if c.stem not in self._loaded:
                self._load_one(c)

    def _load_one(self, path: Path) -> bool:
        """Carrega e ativa um plugin a partir do caminho informado.

        Plugins encontrados na pasta que nunca passaram pelo fluxo de
        instalação (e portanto não estão na lista de confiáveis para o
        conteúdo atual) são submetidos à verificação de sandbox. Se o
        sandbox detectar imports bloqueados o plugin é recusado e
        registrado em ``self._errors`` - nunca é executado.

        A confiança é vinculada ao hash SHA-256 do código-fonte, não apenas
        ao nome do arquivo: isso mitiga o vetor em que um plugin já
        confiável é substituído por uma versão maliciosa mantendo o mesmo
        nome - a mudança de conteúdo invalida a confiança anterior e força
        nova checagem de sandbox.
        """
        plugin_id = path.stem
        if plugin_id in self._loaded:
            return True
        if plugin_id in self._disabled:
            return False

        try:
            source = _get_plugin_source(path)
        except (OSError, ImportError, AttributeError):
            source = None

        content_hash = (
            hashlib.sha256(source.encode("utf-8")
                           ).hexdigest() if source else None
        )
        is_trusted = (
            content_hash is not None and self._trusted.get(
                plugin_id) == content_hash
        )

        if not is_trusted:
            try:
                if source is None:
                    source = _get_plugin_source(path)
                blocked, _warned = _sandbox_check_imports(source)
                if blocked:
                    err_msg = (
                        f"Plugin '{plugin_id}' bloqueado: contém imports "
                        "proibidos detectados pelo sandbox: "
                        + ", ".join(blocked)
                        + ". Use o painel de Plugins para instalá-lo "
                    )
                    self._errors[plugin_id] = err_msg
                    logger.warning(err_msg)
                    return False
            except (OSError, ImportError, AttributeError) as exc:
                logger.debug(
                    "Sandbox check falhou para '%s': %s - prosseguindo.",
                    plugin_id,
                    exc,
                )

        try:
            mod = self._exec_module(path, plugin_id)

            manifest = getattr(mod, "MANIFEST", None)
            _isolated_flag = getattr(manifest, "isolated", False)
            _cfg = getattr(getattr(self._app_api, "_app", None), "_cfg", None)
            _isolation_enabled = _cfg.get(
                "plugin_isolation", True) if _cfg else True

            if _isolated_flag and _isolation_enabled:
                try:
                    from src.plugins.process_runner import IsolatedPluginManager

                    if not hasattr(self, "_proc_mgr") or self._proc_mgr is None:
                        self._proc_mgr = IsolatedPluginManager(
                            self._app_api._app, cfg=_cfg
                        )
                    launched = self._proc_mgr.launch(plugin_id, path)
                    if launched:
                        mod._enabled = True
                        mod._plugin_path = path
                        mod._isolated_process = True
                        self._loaded[plugin_id] = mod
                        self._paths[plugin_id] = path
                        self._active.add(plugin_id)
                        self._errors.pop(plugin_id, None)
                        logger.info("Plugin isolado (processo): %s", plugin_id)
                        self._emit_event("plugin_loaded", plugin_id)
                        return True
                except Exception as proc_exc:
                    logger.warning(
                        "Falha ao isolar plugin '%s' em processo: %s - carregando no processo principal.",
                        plugin_id,
                        proc_exc,
                    )

            plugin_api = PluginAPI(self._app_api._app, plugin_id)
            if hasattr(mod, "activate"):
                mod.activate(plugin_api)
            mod._enabled = True
            mod._plugin_path = path
            mod._plugin_api = plugin_api
            self._loaded[plugin_id] = mod
            self._apis[plugin_id] = plugin_api
            self._paths[plugin_id] = path
            self._active.add(plugin_id)
            self._errors.pop(plugin_id, None)

            if (
                content_hash is not None
                and self._trusted.get(plugin_id) != content_hash
            ):
                self._trusted[plugin_id] = content_hash
                _save_trusted_plugins(self._trusted)
            display_name = getattr(
                getattr(mod, "MANIFEST", None), "name", plugin_id)
            logger.info("Plugin carregado: %s", display_name)

            self._emit_event("plugin_loaded", plugin_id)
            return True
        except Exception:
            err = traceback.format_exc()
            self._errors[plugin_id] = err
            logger.warning("Plugin %s falhou ao carregar:\n%s", plugin_id, err)
        return False

    def _emit_event(self, event: str, *args: Any) -> None:
        """Emite evento no barramento global de plugins."""
        with contextlib.suppress(tk.TclError):
            app = self._app_api._app
            for handler in list(app._plugin_hooks.get(event, [])):
                with contextlib.suppress(tk.TclError):
                    handler(*args)

    def _exec_module(self, path: Path, plugin_id: str) -> Any:
        """Executa o módulo do plugin e retorna o objeto module."""
        nome_mod = f"codis_plugin_{plugin_id}"
        if path.suffix == ".py":
            spec = importlib.util.spec_from_file_location(nome_mod, path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
        if path.suffix == ".zip":
            return self._load_zip(path, plugin_id)
        if path.is_dir():
            spec = importlib.util.spec_from_file_location(
                nome_mod, path / "plugin.py")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
        raise ImportError(f"Formato de plugin não suportado: {path}")

    @staticmethod
    def _load_zip(path: Path, plugin_id: str) -> Any:
        """
        Carrega um plugin de um ZIP.

        Estratégia:
          1. Tenta zipimport direto (ZIP plano com plugin.py na raiz).
          2. Inspeciona o ZIP para encontrar plugin.py / __init__.py.
          3. Extrai o conteúdo para PLUGINS_DIR/<stem>/ e carrega da pasta.
        """
        import zipimport

        with contextlib.suppress(tk.TclError):
            importer = zipimport.zipimporter(str(path))
            for entry in ("plugin", "__init__"):
                with contextlib.suppress(ImportError, AttributeError, Exception):
                    spec = importer.find_spec(entry, None)
                    if spec is not None:
                        mod = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(mod)
                        return mod

        if not zipfile.is_zipfile(str(path)):
            raise ImportError(f"Arquivo inválido ou corrompido: {path}")

        dest_dir = path.parent / path.stem
        try:
            with zipfile.ZipFile(str(path), "r") as zf:
                names = zf.namelist()
                arquivos_plugin = [
                    n
                    for n in names
                    if n.endswith("plugin.py") or n.endswith("__init__.py")
                ]
                if not arquivos_plugin:
                    raise ImportError(
                        +path.name + ". Arquivos no ZIP: " +
                        ", ".join(names[:10])
                    )

                first = arquivos_plugin[0]
                prefix = ""
                if "/" in first:
                    candidato = first.rsplit("/", 1)[0] + "/"
                    if all(n.startswith(candidato) or n == candidato for n in names):
                        prefix = candidato

                dest_dir.mkdir(parents=True, exist_ok=True)
                for membro in names:
                    if membro.endswith("/"):
                        continue
                    rel = (
                        membro[len(prefix):]
                        if prefix and membro.startswith(prefix)
                        else membro
                    )
                    if not rel:
                        continue
                    target = dest_dir / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(zf.read(membro))

            logger.info("Plugin ZIP extraído: %s → %s", path.name, dest_dir)
        except (OSError, zipfile.BadZipFile, KeyError) as exc:
            raise ImportError(
                f"Erro ao extrair ZIP {path.name}: {exc}") from exc

        plugin_py = dest_dir / "plugin.py"
        init_py = dest_dir / "__init__.py"
        entry_file = (
            plugin_py if plugin_py.exists() else (init_py if init_py.exists() else None)
        )
        if not entry_file:
            raise ImportError(
                f"plugin.py não encontrado após extração em {dest_dir}")

        spec = importlib.util.spec_from_file_location(
            f"codis_plugin_{plugin_id}", entry_file
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def load_file(self, path: Path, confirm_cb: Any | None = None) -> bool:
        """
        Carrega plugin de um arquivo externo (instalação manual).

        ``confirm_cb(plugin_id, blocked, warned) -> bool`` é chamado para
        perguntar ao usuário se deseja instalar plugin suspeito.
        """
        plugin_id = path.stem
        source = _get_plugin_source(path)
        content_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
        bloqueados, avisos = _sandbox_check_imports(source)
        is_trusted = self._trusted.get(plugin_id) == content_hash
        if not is_trusted and (bloqueados or avisos):
            if confirm_cb and not confirm_cb(plugin_id, bloqueados, avisos):
                return False
            self._trusted[plugin_id] = content_hash
            _save_trusted_plugins(self._trusted)
        return self._load_one(path)

    def install_from_path(self, src_path: Path, confirm_cb: Any | None = None) -> bool:
        """
        Instala um plugin em PLUGINS_DIR e ativa-o.

        ZIPs são extraídos para uma pasta - não copiados como ZIP.
        """
        plugin_id = src_path.stem
        if plugin_id in ("", ".", ".."):
            raise RuntimeError(f"Nome de plugin inválido: {src_path.name!r}")

        self._desativar_plugin(plugin_id)
        self._errors.pop(plugin_id, None)

        try:
            if src_path.suffix == ".zip" and zipfile.is_zipfile(str(src_path)):
                dest_dir = PLUGINS_DIR / plugin_id
                if dest_dir.exists():
                    shutil.rmtree(dest_dir)
                dest_dir.mkdir(parents=True, exist_ok=True)

                with zipfile.ZipFile(str(src_path), "r") as zf:
                    names = zf.namelist()
                    arqs_plugin = [
                        n
                        for n in names
                        if n.endswith("plugin.py") or n.endswith("__init__.py")
                    ]
                    prefix = ""
                    if arqs_plugin:
                        first = arqs_plugin[0]
                        if "/" in first:
                            candidato = first.rsplit("/", 1)[0] + "/"
                            if all(
                                n.startswith(candidato) or n == candidato for n in names
                            ):
                                prefix = candidato

                    extracted_bytes = 0
                    for membro in names:
                        if membro.endswith("/"):
                            continue
                        rel = (
                            membro[len(prefix):]
                            if prefix and membro.startswith(prefix)
                            else membro
                        )
                        if not rel:
                            continue
                        target = (dest_dir / rel).resolve()
                        if not _is_inside(target, dest_dir.resolve()):
                            raise RuntimeError(
                                f"Entrada insegura no ZIP do plugin: {membro!r}"
                            )
                        info = zf.getinfo(membro)
                        extracted_bytes += info.file_size
                        if extracted_bytes > _MAX_PLUGIN_BYTES:
                            raise RuntimeError("Plugin excede o tamanho máximo permitido.")
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(zf.read(membro))

                dest: Path = dest_dir
                logger.info("Plugin ZIP instalado: %s → %s",
                            src_path.name, dest_dir)

            elif src_path.is_dir():
                dest = PLUGINS_DIR / src_path.name
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(src_path, dest)
            else:
                dest = PLUGINS_DIR / src_path.name
                shutil.copy2(src_path, dest)

        except (OSError, shutil.Error, zipfile.BadZipFile, RuntimeError) as exc:
            if src_path.suffix == ".zip":
                shutil.rmtree(PLUGINS_DIR / plugin_id, ignore_errors=True)
            raise RuntimeError(f"Falha ao instalar plugin: {exc}") from exc

        return self.load_file(dest, confirm_cb)

    def _desativar_plugin(self, plugin_id: str) -> None:
        """Desativa e remove um plugin da memória sem apagar do disco."""
        mod = self._loaded.pop(plugin_id, None)
        api = self._apis.pop(plugin_id, None)
        if mod:
            if hasattr(mod, "deactivate"):
                with contextlib.suppress(tk.TclError):
                    mod.deactivate()
            if api:
                with contextlib.suppress(tk.TclError):
                    api._teardown()
        self._active.discard(plugin_id)
        self._emit_event("plugin_unloaded", plugin_id)

    def unload(self, plugin_id: str) -> bool:
        """Desativa e descarrega um plugin da memória (NÃO apaga do disco)."""
        if plugin_id not in self._loaded:
            return False
        self._desativar_plugin(plugin_id)
        return True

    def uninstall(self, plugin_id: str) -> bool:
        """Desativa e apaga o plugin do disco permanentemente."""
        self.unload(plugin_id)
        path = self._paths.pop(plugin_id, None)
        if path is None:
            for ext in (".py", ".zip", ""):
                candidato = PLUGINS_DIR / (plugin_id + ext)
                if candidato.exists():
                    path = candidato
                    break
        if path and path.exists():
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()

                cache = PLUGINS_DIR / "__pycache__"
                if cache.exists():
                    for f in cache.glob(f"codis_plugin_{plugin_id}*"):
                        with contextlib.suppress(OSError):
                            f.unlink()
                self._errors.pop(plugin_id, None)
                self._disabled.discard(plugin_id)
                return True
            except OSError as exc:
                logger.warning(
                    "Erro ao desinstalar plugin %s: %s", plugin_id, exc)
        return False

    def toggle(self, plugin_id: str) -> bool:
        """Ativa/desativa um plugin. Retorna o novo estado (True = active)."""
        if plugin_id in self._loaded:
            mod = self._loaded[plugin_id]
            active = getattr(mod, "_enabled", True)
            if active:
                if hasattr(mod, "deactivate"):
                    with contextlib.suppress(tk.TclError):
                        mod.deactivate()
                api = self._apis.get(plugin_id)
                if api:
                    with contextlib.suppress(tk.TclError):
                        api._teardown()
                mod._enabled = False
                self._disabled.add(plugin_id)
                return False

            plugin_api = PluginAPI(self._app_api._app, plugin_id)
            if hasattr(mod, "activate"):
                with contextlib.suppress(tk.TclError):
                    mod.activate(plugin_api)
            mod._enabled = True
            mod._plugin_api = plugin_api
            self._apis[plugin_id] = plugin_api
            self._disabled.discard(plugin_id)
            return True

        path = self._paths.get(plugin_id)
        if path and path.exists():
            self._disabled.discard(plugin_id)
            return self._load_one(path)
        return False

    def reload(self, plugin_id: str) -> bool:
        """Recarrega um plugin do disco."""
        path = self._paths.get(plugin_id)
        if path is None:
            for ext in (".py", ".zip", ""):
                c = PLUGINS_DIR / (plugin_id + ext)
                if c.exists():
                    path = c
                    break
        if path is None:
            return False
        self.unload(plugin_id)
        return self._load_one(path)

    def get_readme(self, plugin_id: str) -> str:
        """Retorna o README do plugin em Markdown."""
        mod = self._loaded.get(plugin_id)
        path = self._paths.get(plugin_id) or self._descobrir_caminho(plugin_id)
        return _get_plugin_readme(path, mod) if path else "*(Plugin não encontrado)*"

    def get_security_info(self, plugin_id: str) -> tuple[list[str], list[str]]:
        """Retorna (imports_bloqueados, imports_com_aviso) do plugin."""
        path = self._paths.get(plugin_id) or self._descobrir_caminho(plugin_id)
        if path:
            return _sandbox_check_imports(_get_plugin_source(path))
        return [], []

    @staticmethod
    def _descobrir_caminho(plugin_id: str) -> Path | None:
        """Tenta localizar o arquivo do plugin no disco."""
        for ext in (".py", ".zip", ""):
            c = PLUGINS_DIR / (plugin_id + ext)
            if c.exists():
                return c
        return None

    def all_plugin_ids(self) -> list[str]:
        """Retorna os IDs de todos os plugins carregados com sucesso."""
        ids = set(self._loaded.keys()) | set(self._errors.keys())
        if PLUGINS_DIR.exists():
            for entry in PLUGINS_DIR.iterdir():
                if entry.name.startswith("_"):
                    continue
                if entry.suffix in (".py", ".zip") or (
                    entry.is_dir() and (entry / "plugin.py").exists()
                ):
                    ids.add(entry.stem)
        return sorted(ids)

    def get_plugin_weight(self, plugin_id: str) -> int:
        """Retorna tamanho aproximado em KB do plugin no disco."""
        path = self._paths.get(plugin_id) or self._descobrir_caminho(plugin_id)
        if path is None:
            return 0
        try:
            if path.is_dir():
                return (
                    sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
                    // 1024
                )
            return path.stat().st_size // 1024
        except OSError:
            return 0

    @property
    def plugins(self) -> dict[str, Any]:
        """Retorna o dicionário de plugins carregados, indexado por ID."""
        return dict(self._loaded)

    @property
    def errors(self) -> dict[str, str]:
        """Retorna o dicionário de erros de carregamento, indexado por ID."""
        return dict(self._errors)

    def get_manifest(self, plugin_id: str) -> PluginManifest:
        """Retorna o PluginManifest de um plugin (com fallback seguro)."""
        mod = self._loaded.get(plugin_id)
        if mod:
            return _normalize_manifest(getattr(mod, "MANIFEST", None))
        return PluginManifest(name=plugin_id)
