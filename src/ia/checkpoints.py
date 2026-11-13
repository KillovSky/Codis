"""Codis IA - Checkpoints: desfazer tudo o que a IA mudou, turno a turno.

Antes de a IA alterar qualquer coisa, o estado anterior é preservado:

* **Arquivo editado**  - snapshot por *hardlink* do inode antigo (custo zero, sem
  copiar; a escrita atômica troca o inode, então o antigo sobrevive).  Se o
  hardlink não for possível, copia (até :data:`MAX_COPY_BYTES`).
* **Arquivo criado**   - registrado; desfazer remove.
* **Arquivo/pasta removido** - movido para a lixeira do projeto (``rename``
  atômico, mesmo volume), nunca ``rmtree``; desfazer restaura.
* **Movido/renomeado** - desfazer move de volta.

Tudo mora em ``<raiz>/.codis_trash/.ai/<sessão>/`` (mesmo volume do projeto e
auto-ignorado pelo git), nunca em ``~/.codis``.

Desfazer detecta **conflitos**: se o arquivo mudou depois da IA (você editou),
ele é pulado e reportado, a menos que ``force=True``.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.trash_core import TRASH_DIRNAME, TrashStore, _remove_tree, trash_root_for

logger = logging.getLogger("Codis.IA.Checkpoints")

MAX_COPY_BYTES = 50 * 1024 * 1024
KEEP_CHECKPOINTS = 30
_META = "meta.json"


def _sha(path: Path) -> str | None:
    try:
        h = hashlib.sha1()  # noqa: S324
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


@dataclass
class Change:
    """Uma alteração registrada dentro de um checkpoint."""

    kind: str
    path: str
    snapshot: str | None = None
    after_sha: str | None = None
    trash_id: str | None = None
    trash_dir: str | None = None
    dst: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class Checkpoint:
    """Um turno da IA com as mudanças que ele fez."""

    id: int
    label: str
    ts: float = field(default_factory=time.time)
    changes: list[Change] = field(default_factory=list)
    undone: bool = False

    @property
    def files(self) -> list[str]:
        seen: list[str] = []
        for c in self.changes:
            if c.path not in seen:
                seen.append(c.path)
        return seen

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "label": self.label, "ts": self.ts, "undone": self.undone,
                "changes": [c.to_json() for c in self.changes]}

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Checkpoint:
        return cls(id=int(d["id"]), label=str(d.get("label", "")), ts=float(d.get("ts", 0)),
                   undone=bool(d.get("undone", False)),
                   changes=[Change(**c) for c in d.get("changes", [])])


@dataclass
class UndoReport:
    """Resultado de :meth:`CheckpointStore.undo`."""

    restored: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.conflicts and not self.errors

    def summary(self) -> str:
        parts = []
        if self.restored:
            parts.append(f"{len(self.restored)} restaurado(s)")
        if self.removed:
            parts.append(f"{len(self.removed)} removido(s)")
        if self.conflicts:
            parts.append(f"{len(self.conflicts)} em conflito (você editou depois da IA)")
        if self.errors:
            parts.append(f"{len(self.errors)} erro(s)")
        return ", ".join(parts) or "nada a desfazer"


class CheckpointStore:
    """Checkpoints de uma sessão de IA dentro de um workspace."""

    def __init__(self, workspace: Path | str, session_id: str) -> None:
        self.workspace = Path(workspace)
        self.session_id = session_id
        anchor = trash_root_for(self.workspace / "x", self.workspace)
        self.dir = anchor / TRASH_DIRNAME / ".ai" / session_id
        self._cps: list[Checkpoint] = []
        self._current: Checkpoint | None = None
        self._recorded: set[str] = set()
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads((self.dir / _META).read_text(encoding="utf-8"))
            self._cps = [Checkpoint.from_json(c) for c in raw.get("checkpoints", [])]
        except (OSError, ValueError, KeyError, TypeError):
            self._cps = []

    def _save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        gi = self.dir.parent.parent / ".gitignore"
        if not gi.exists():
            with contextlib.suppress(OSError):
                gi.write_text("# Lixeira local do Codis - não versionar\n*\n", encoding="utf-8")
        tmp = self.dir / (_META + ".tmp")
        tmp.write_text(json.dumps({"checkpoints": [c.to_json() for c in self._cps]},
                                  ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.dir / _META)

    def begin(self, label: str) -> Checkpoint:
        """Abre um novo checkpoint (um por mensagem do usuário)."""
        nid = max((c.id for c in self._cps), default=0) + 1
        self._current = Checkpoint(nid, label[:120])
        self._recorded = set()
        return self._current

    def commit(self) -> Checkpoint | None:
        """Fecha o checkpoint atual; descarta-o se nada mudou."""
        cp, self._current = self._current, None
        if cp is None or not cp.changes:
            return None
        self._cps.append(cp)
        self._prune()
        with contextlib.suppress(OSError):
            self._save()
        return cp

    def list(self) -> list[Checkpoint]:
        return list(reversed(self._cps))

    def _prune(self) -> None:
        while len(self._cps) > KEEP_CHECKPOINTS:
            old = self._cps.pop(0)
            shutil.rmtree(self.dir / str(old.id), ignore_errors=True)

    def _snap_path(self, cp: Checkpoint, n: int) -> Path:
        return self.dir / str(cp.id) / f"{n}.snap"

    def _active(self) -> Checkpoint | None:
        return self._current

    def record_write(self, path: Path) -> None:
        """Preserva o conteúdo atual de *path* (ou registra criação)."""
        cp = self._active()
        key = str(path)
        if cp is None or key in self._recorded:
            return
        self._recorded.add(key)
        if not path.exists():
            cp.changes.append(Change("create", key))
            return
        snap = self._snap_path(cp, len(cp.changes))
        snap.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            snap.unlink()
        try:
            os.link(path, snap)
        except OSError:
            if path.stat().st_size > MAX_COPY_BYTES:
                logger.warning("Checkpoint: '%s' grande demais para copiar; sem snapshot.", path)
                return
            shutil.copy2(path, snap)
        cp.changes.append(Change("write", key, snapshot=str(snap)))

    def record_after(self, path: Path) -> None:
        """Guarda o hash pós-escrita (para detectar edição posterior do usuário)."""
        cp = self._active()
        if cp is None:
            return
        for c in reversed(cp.changes):
            if c.path == str(path) and c.kind in ("write", "create"):
                c.after_sha = _sha(path)
                return

    def trash(self, path: Path) -> bool:
        """Move *path* (arquivo ou pasta) para a lixeira do projeto, registrando-o."""
        cp = self._active()
        store = TrashStore.for_root(trash_root_for(path, self.workspace))
        entry = store.put(path)
        if cp is not None:
            cp.changes.append(Change("trash", str(path), trash_id=entry.id, trash_dir=str(store.dir)))
        return True

    def record_move(self, src: Path, dst: Path) -> None:
        cp = self._active()
        if cp is not None:
            cp.changes.append(Change("move", str(src), dst=str(dst), after_sha=_sha(dst) if dst.is_file() else None))

    def record_created_dir(self, path: Path) -> None:
        cp = self._active()
        if cp is not None and not path.exists():
            cp.changes.append(Change("create", str(path)))

    def undo(self, cp_id: int | None = None, *, force: bool = False) -> UndoReport:
        """Desfaz o checkpoint *cp_id* (padrão: o mais recente ainda não desfeito)."""
        rep = UndoReport()
        cp = self._pick(cp_id)
        if cp is None:
            return rep
        for c in reversed(cp.changes):
            try:
                self._undo_change(c, rep, force)
            except OSError as exc:
                rep.errors.append(f"{c.path}: {exc}")
        if rep.ok:
            cp.undone = True
            shutil.rmtree(self.dir / str(cp.id), ignore_errors=True)
            with contextlib.suppress(OSError):
                self._save()
        return rep

    def _pick(self, cp_id: int | None) -> Checkpoint | None:
        if cp_id is None:
            return next((c for c in reversed(self._cps) if not c.undone), None)
        return next((c for c in self._cps if c.id == cp_id), None)

    def _changed_since(self, c: Change, path: Path) -> bool:
        return bool(c.after_sha) and path.exists() and _sha(path) != c.after_sha

    def _undo_change(self, c: Change, rep: UndoReport, force: bool) -> None:
        path = Path(c.path)
        if c.kind == "write":
            if self._changed_since(c, path) and not force:
                rep.conflicts.append(c.path)
                return
            assert c.snapshot
            snap = Path(c.snapshot)
            if not snap.exists():
                rep.errors.append(f"{c.path}: snapshot ausente")
                return
            tmp = path.with_name(f".{path.name}.undo.tmp")
            shutil.copy2(snap, tmp)
            os.replace(tmp, path)
            rep.restored.append(c.path)
        elif c.kind == "create":
            if self._changed_since(c, path) and not force:
                rep.conflicts.append(c.path)
                return
            if path.is_dir():
                with contextlib.suppress(OSError):
                    path.rmdir()
            elif path.exists():
                _remove_tree(path)
            rep.removed.append(c.path)
        elif c.kind == "trash":
            assert c.trash_id and c.trash_dir
            if path.exists():
                rep.conflicts.append(c.path)
                return
            TrashStore(Path(c.trash_dir)).restore(c.trash_id)
            rep.restored.append(c.path)
        elif c.kind == "move":
            assert c.dst
            dst = Path(c.dst)
            if path.exists():
                rep.conflicts.append(c.path)
                return
            if self._changed_since(c, dst) and not force:
                rep.conflicts.append(c.dst)
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(dst), str(path))
            rep.restored.append(c.path)
