"""Núcleo da lixeira do Codis (sem dependência de Tkinter).

Princípios
----------
* **Mesmo volume, sempre.**  A lixeira de um arquivo vive em
  ``<raiz-do-projeto>/.codis_trash/``.  Mover para a lixeira é um
  ``os.rename`` atômico - instantâneo mesmo para 100 GB - e **nunca** uma
  cópia entre discos.  Se por algum motivo o rename cruzar dispositivos
  (``EXDEV``), a operação é recusada com :class:`CrossDeviceError` em vez de
  copiar gigabytes silenciosamente.
* **Autocontida.**  Cada ``.codis_trash`` tem o seu ``.meta.json``; copiar,
  mover ou apagar o projeto leva a lixeira junto.
* **Invisível para o git.**  A pasta recebe um ``.gitignore`` com ``*``, o que
  a faz ignorar a si mesma sem tocar no ``.gitignore`` do usuário.

API principal: :class:`TrashStore` (uma pasta de lixeira) e
:class:`TrashRegistry` (descobre e agrega as lixeiras conhecidas).
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import errno
import json
import logging
import os
import shutil
import threading
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("Codis.Trash")

TRASH_DIRNAME = ".codis_trash"
META_NAME = ".meta.json"
_PROJECT_MARKERS = (".git", ".codis", ".hg", ".svn")
_META_VERSION = 2

_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.RLock:
    key = str(path)
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = _LOCKS[key] = threading.RLock()
        return lock


class TrashError(OSError):
    """Erro genérico de lixeira."""


class CrossDeviceError(TrashError):
    """A lixeira ficaria em outro volume; mover exigiria copiar dados."""


class NothingToDoError(TrashError):
    """Item inexistente na lixeira."""


def _dev(path: Path) -> int | None:
    """``st_dev`` do próprio *path* (sem seguir symlink) ou ``None``."""
    try:
        return os.lstat(path).st_dev
    except OSError:
        return None


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
    except ValueError:
        return False
    return True


def _absolute(path: Path | str) -> Path:
    """Caminho absoluto *sem* resolver o último componente (preserva symlinks)."""
    p = Path(path)
    if not p.is_absolute():
        p = Path.cwd() / p
    return Path(os.path.normpath(p))


def find_project_root(path: Path) -> Path | None:
    """Sobe a partir de ``path.parent`` procurando um marcador de projeto."""
    for parent in _absolute(path).parents:
        if any((parent / m).exists() for m in _PROJECT_MARKERS):
            return parent
    return None


def trash_root_for(path: Path | str, workspace: Path | str | None = None) -> Path:
    """Escolhe a pasta que hospedará o ``.codis_trash`` de *path*.

    Ordem:

    1. A pasta de trabalho (*workspace*), se *path* estiver dentro dela
       (se *path* for a própria pasta de trabalho, a pasta pai dela);
    2. a raiz de projeto detectada (``.git``/``.codis``...);
    3. a pasta que contém o arquivo.

    Em qualquer caso a escolha é **rebaixada** até o ancestral mais alto que
    esteja no *mesmo volume* do arquivo (cobre pontos de montagem dentro do
    projeto), garantindo que o ``rename`` nunca cruze dispositivos.
    """
    p = _absolute(path)
    anchor: Path | None = None
    if workspace:
        ws = _absolute(workspace)
        if p == ws:
            anchor = ws.parent
        elif _is_relative_to(p, ws):
            anchor = ws
    if anchor is None:
        anchor = find_project_root(p) or p.parent

    file_dev = _dev(p)
    if file_dev is None:
        return anchor

    chain: list[Path] = []
    cur = p.parent
    while True:
        chain.append(cur)
        if cur == anchor or cur.parent == cur:
            break
        cur = cur.parent
    for cand in reversed(chain):
        if _dev(cand) == file_dev:
            return cand
    return p.parent


def _fmt_ts(ts: _dt.datetime | None = None) -> str:
    return (ts or _dt.datetime.now()).isoformat(timespec="seconds")


def tree_size(path: Path, *, limit_s: float | None = None, cancel: threading.Event | None = None) -> int | None:
    """Tamanho total em bytes (``None`` se estourar *limit_s* ou for cancelado).

    Não segue symlinks; erros de permissão são ignorados.
    """
    import time

    deadline = (time.monotonic() + limit_s) if limit_s else None
    try:
        st = os.lstat(path)
    except OSError:
        return 0
    if not os.path.isdir(path) or os.path.islink(path):
        return st.st_size
    total = 0
    stack = [str(path)]
    while stack:
        if cancel is not None and cancel.is_set():
            return None
        if deadline is not None and time.monotonic() > deadline:
            return None
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        else:
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def human_size(n: int | float | None) -> str:
    """Formata bytes (``None`` → ``"…"``)."""
    if n is None:
        return "…"
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"  # pragma: no cover


@dataclass
class TrashEntry:
    """Um item na lixeira."""

    id: str
    original: str
    name: str
    is_dir: bool
    deleted_at: str
    size: int | None = None
    store: Path | None = None

    @property
    def path(self) -> Path:
        assert self.store is not None
        return self.store / self.id

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "original": self.original,
            "name": self.name,
            "is_dir": self.is_dir,
            "deleted_at": self.deleted_at,
            "size": self.size,
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any], store: Path) -> TrashEntry | None:
        try:
            return cls(
                id=str(raw["id"]),
                original=str(raw["original"]),
                name=str(raw.get("name") or Path(str(raw["original"])).name),
                is_dir=bool(raw.get("is_dir", False)),
                deleted_at=str(raw.get("deleted_at", "")),
                size=raw.get("size") if isinstance(raw.get("size"), int) else None,
                store=store,
            )
        except (KeyError, TypeError):
            return None


class TrashStore:
    """Uma pasta ``.codis_trash`` com seus metadados."""

    def __init__(self, directory: Path) -> None:
        self.dir = Path(directory)
        self._meta = self.dir / META_NAME

    @classmethod
    def for_root(cls, root: Path | str) -> TrashStore:
        return cls(Path(root) / TRASH_DIRNAME)

    def _read(self) -> list[dict[str, Any]]:
        try:
            raw = json.loads(self._meta.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as exc:
            logger.warning("Metadados da lixeira ilegíveis (%s): %s", self._meta, exc)
            bak = self._meta.with_suffix(".json.bak")
            with contextlib.suppress(OSError):
                self._meta.replace(bak)
            return self._rebuild_from_disk()
        if isinstance(raw, dict):
            raw = raw.get("entries", [])
        return [e for e in raw if isinstance(e, dict)] if isinstance(raw, list) else []

    def _write(self, entries: list[dict[str, Any]]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self._meta.with_suffix(f".tmp{os.getpid()}")
        payload = {"version": _META_VERSION, "entries": entries}
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self._meta)

    def _rebuild_from_disk(self) -> list[dict[str, Any]]:
        """Reconstrói metadados mínimos quando o JSON foi perdido/corrompido."""
        out: list[dict[str, Any]] = []
        try:
            for child in self.dir.iterdir():
                if child.name in (META_NAME, ".gitignore") or child.name.startswith(META_NAME):
                    continue
                out.append(
                    {
                        "id": child.name,
                        "original": str(self.dir.parent / _strip_prefix(child.name)),
                        "name": _strip_prefix(child.name),
                        "is_dir": child.is_dir(),
                        "deleted_at": _fmt_ts(_dt.datetime.fromtimestamp(child.lstat().st_mtime)),
                        "size": None,
                    }
                )
        except OSError:
            pass
        return out

    def _ensure_dir(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        gi = self.dir / ".gitignore"
        if not gi.exists():
            with contextlib.suppress(OSError):
                gi.write_text("# Lixeira local do Codis - não versionar\n*\n", encoding="utf-8")

    def entries(self) -> list[TrashEntry]:
        """Itens existentes (descarta metadados órfãos) - mais recentes primeiro."""
        with _lock_for(self.dir):
            out: list[TrashEntry] = []
            for raw in self._read():
                e = TrashEntry.from_json(raw, self.dir)
                if e is not None and os.path.lexists(e.path):
                    out.append(e)
        out.sort(key=lambda e: e.deleted_at, reverse=True)
        return out

    def get(self, entry_id: str) -> TrashEntry | None:
        return next((e for e in self.entries() if e.id == entry_id), None)

    def put(self, path: Path | str) -> TrashEntry:
        """Move *path* para esta lixeira com ``rename`` atômico."""
        src = _absolute(path)
        if not os.path.lexists(src):
            raise FileNotFoundError(errno.ENOENT, "Arquivo inexistente", str(src))
        if _is_relative_to(src, self.dir) or src == self.dir:
            raise TrashError(f"'{src}' já está na lixeira.")
        if _dev(src) != _dev(self.dir.parent):
            raise CrossDeviceError(
                errno.EXDEV,
                "A lixeira ficaria em outro volume (mover exigiria copiar dados).",
                str(src),
            )
        is_dir = os.path.isdir(src) and not os.path.islink(src)
        try:
            size = None if is_dir else os.lstat(src).st_size
        except OSError:
            size = None
        tid = f"{_dt.datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}_{src.name}"
        with _lock_for(self.dir):
            self._ensure_dir()
            dest = self.dir / tid
            try:
                os.rename(src, dest)
            except OSError as exc:
                if exc.errno == errno.EXDEV:
                    raise CrossDeviceError(errno.EXDEV, "Cruzaria volumes; recusado.", str(src)) from exc
                raise
            entry = TrashEntry(tid, str(src), src.name, is_dir, _fmt_ts(), size, self.dir)
            entries = self._read()
            entries.append(entry.to_json())
            self._write(entries)
        logger.debug("Lixeira: '%s' -> '%s'", src, dest)
        return entry

    def restore(self, entry_id: str, *, to: Path | str | None = None) -> Path:
        """Restaura para o caminho original (ou *to*). Retorna o destino final.

        Se o destino existir, ganha o sufixo ``_restaurado[_N]``; pastas pai
        ausentes são recriadas.
        """
        with _lock_for(self.dir):
            entry = next((TrashEntry.from_json(r, self.dir) for r in self._read() if r.get("id") == entry_id), None)
            if entry is None or not os.path.lexists(entry.path):
                self._drop(entry_id)
                raise NothingToDoError(errno.ENOENT, "Item não está mais na lixeira.", entry_id)
            dest = _absolute(to) if to else Path(entry.original)
            dest = _free_name(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.rename(entry.path, dest)
            except OSError as exc:
                if exc.errno != errno.EXDEV:
                    raise
                shutil.move(str(entry.path), str(dest))
            self._drop(entry_id)
        return dest

    def purge(self, entry_id: str, *, cancel: threading.Event | None = None) -> bool:
        """Exclui definitivamente um item. Pode demorar - rode fora da UI."""
        with _lock_for(self.dir):
            target = self.dir / entry_id
        removed = _remove_tree(target, cancel)
        if removed:
            with _lock_for(self.dir):
                self._drop(entry_id)
        return removed

    def empty(self, *, cancel: threading.Event | None = None, progress: Callable[[int, int], None] | None = None) -> int:
        items = self.entries()
        n = 0
        for i, e in enumerate(items, 1):
            if cancel is not None and cancel.is_set():
                break
            if self.purge(e.id, cancel=cancel):
                n += 1
            if progress:
                progress(i, len(items))
        return n

    def _drop(self, entry_id: str) -> None:
        entries = [r for r in self._read() if r.get("id") != entry_id]
        self._write(entries)

    def set_size(self, entry_id: str, size: int) -> None:
        with _lock_for(self.dir):
            entries = self._read()
            for r in entries:
                if r.get("id") == entry_id:
                    r["size"] = int(size)
            self._write(entries)

    def purge_expired(self, *, older_than_days: int = 0, max_bytes: int = 0, cancel: threading.Event | None = None) -> int:
        """Remove itens vencidos (``older_than_days``) e/ou acima do teto de tamanho.

        ``0`` desativa cada critério.  O teto remove os mais antigos primeiro.
        """
        removed = 0
        entries = self.entries()
        if older_than_days > 0:
            limit = _dt.datetime.now() - _dt.timedelta(days=older_than_days)
            for e in list(entries):
                try:
                    when = _dt.datetime.fromisoformat(e.deleted_at)
                except ValueError:
                    continue
                if when < limit and self.purge(e.id, cancel=cancel):
                    removed += 1
                    entries.remove(e)
        if max_bytes > 0:
            sized = []
            for e in entries:
                if e.size is None:
                    e.size = tree_size(e.path, cancel=cancel)
                    if e.size is not None:
                        self.set_size(e.id, e.size)
                sized.append(e)
            total = sum(e.size or 0 for e in sized)
            for e in reversed(sized):
                if total <= max_bytes:
                    break
                if self.purge(e.id, cancel=cancel):
                    total -= e.size or 0
                    removed += 1
        return removed


def _strip_prefix(tid: str) -> str:
    """``20260101_120000_abcd1234_nome.ext`` → ``nome.ext``."""
    parts = tid.split("_", 3)
    return parts[3] if len(parts) == 4 else tid


def _free_name(dest: Path) -> Path:
    if not os.path.lexists(dest):
        return dest
    stem, suffix = dest.stem, "".join(dest.suffixes)
    if suffix:
        stem = dest.name[: -len(suffix)]
    n = 0
    while True:
        tag = "_restaurado" + (f"_{n}" if n else "")
        cand = dest.with_name(f"{stem}{tag}{suffix}")
        if not os.path.lexists(cand):
            return cand
        n += 1


def _remove_tree(target: Path, cancel: threading.Event | None = None) -> bool:
    """Remove arquivo/pasta tolerando somente-leitura (comum no Windows)."""
    if not os.path.lexists(target):
        return True

    def _onerror(func: Callable[..., Any], p: str, _exc: Any) -> None:
        with contextlib.suppress(OSError):
            os.chmod(p, 0o700)
            func(p)

    try:
        if os.path.isdir(target) and not os.path.islink(target):
            if cancel is not None and cancel.is_set():
                return False
            shutil.rmtree(target, onerror=_onerror)
        else:
            try:
                os.unlink(target)
            except PermissionError:
                os.chmod(target, 0o700)
                os.unlink(target)
    except OSError as exc:
        logger.warning("Falha ao remover '%s': %s", target, exc)
        return False
    return not os.path.lexists(target)


class TrashRegistry:
    """Descobre as lixeiras relevantes e oferece uma API única."""

    def stores_for(self, workspace: Path | str | None, extra_roots: Iterable[Path | str] = ()) -> list[TrashStore]:
        seen: set[str] = set()
        out: list[TrashStore] = []

        def _add(store: TrashStore) -> None:
            key = str(store.dir)
            if key not in seen and store.dir.is_dir():
                seen.add(key)
                out.append(store)

        if workspace:
            ws = Path(workspace)
            _add(TrashStore.for_root(ws))
            with contextlib.suppress(OSError):
                for child in ws.iterdir():
                    if child.is_dir() and not child.name.startswith(".") and _dev(child) != _dev(ws):
                        _add(TrashStore.for_root(child))
        for r in extra_roots:
            _add(TrashStore.for_root(r))
        return out

    def entries(self, workspace: Path | str | None, **kw: Any) -> list[TrashEntry]:
        items: list[TrashEntry] = []
        for s in self.stores_for(workspace, **kw):
            items.extend(s.entries())
        items.sort(key=lambda e: e.deleted_at, reverse=True)
        return items

    def find(self, entry_id: str, workspace: Path | str | None) -> TrashEntry | None:
        return next((e for e in self.entries(workspace) if e.id == entry_id), None)

    def restore(self, entry: TrashEntry, *, to: Path | str | None = None) -> Path:
        """Restaura *entry* para o caminho original (ou *to*)."""
        assert entry.store is not None
        return TrashStore(entry.store).restore(entry.id, to=to)

    def purge(self, entry: TrashEntry, *, cancel: threading.Event | None = None) -> bool:
        assert entry.store is not None
        return TrashStore(entry.store).purge(entry.id, cancel=cancel)

    def empty(self, workspace: Path | str | None, *, cancel: threading.Event | None = None,
              progress: Callable[[int, int], None] | None = None) -> int:
        items = self.entries(workspace)
        n = 0
        for i, e in enumerate(items, 1):
            if cancel is not None and cancel.is_set():
                break
            if self.purge(e, cancel=cancel):
                n += 1
            if progress:
                progress(i, len(items))
        return n

    def total_count(self, workspace: Path | str | None) -> int:
        return len(self.entries(workspace))
