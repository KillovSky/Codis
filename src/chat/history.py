"""Histórico persistente por canal (JSONL, permissão 0600, tamanho limitado)."""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import re
import sys
import threading
from pathlib import Path

logger = logging.getLogger("Codis.Chat")

MAX_FILE_BYTES = 1_000_000
KEEP_LINES = 1500


def _slug(name: str) -> str:
    base = re.sub(r"[^\w.#@+-]", "_", name, flags=re.UNICODE)[:60] or "_"
    return f"{base}-{hashlib.sha1(name.encode(), usedforsecurity=False).hexdigest()[:6]}"


class HistoryStore:
    def __init__(self, root: Path, profile_name: str) -> None:
        self._dir = root / _slug(profile_name)
        self._lock = threading.Lock()

    def _path(self, channel: str) -> Path:
        return self._dir / f"{_slug(channel)}.jsonl"

    def append(self, channel: str, ts: float, nick: str, text: str, kind: str) -> None:
        line = json.dumps({"ts": round(ts, 3), "n": nick, "t": text, "k": kind}, ensure_ascii=False)
        path = self._path(channel)
        with self._lock:
            try:
                self._dir.mkdir(parents=True, exist_ok=True)
                new = not path.exists()
                with path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
                if new and sys.platform != "win32":
                    with contextlib.suppress(OSError):
                        path.chmod(0o600)
            except OSError as exc:
                logger.debug("histórico: %s", exc)

    def load(self, channel: str, limit: int = 100) -> list[tuple[float, str, str, str]]:
        path = self._path(channel)
        with self._lock:
            try:
                if not path.exists():
                    return []
                if path.stat().st_size > MAX_FILE_BYTES:
                    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-KEEP_LINES:]
                    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                else:
                    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                return []
        out: list[tuple[float, str, str, str]] = []
        for ln in lines[-limit:]:
            try:
                d = json.loads(ln)
                out.append((float(d["ts"]), str(d["n"]), str(d["t"]), str(d.get("k", "msg"))))
            except (ValueError, KeyError, TypeError):
                continue
        return out

    def clear(self, channel: str | None = None) -> None:
        with self._lock, contextlib.suppress(OSError):
            if channel is not None:
                self._path(channel).unlink(missing_ok=True)
            elif self._dir.exists():
                for f in self._dir.glob("*.jsonl"):
                    f.unlink(missing_ok=True)
