"""Mixin ``_AppWebPreviewMixin`` - Preview Web (navegador embutido) no CodisApp."""

from __future__ import annotations

import contextlib
import logging
import tkinter as tk
from pathlib import Path
from typing import Any

from src.i18n import _t

logger = logging.getLogger("Codis")

__all__ = ["_AppWebPreviewMixin"]

_MAX_LIVE_BYTES = 2 * 1024 * 1024


class _AppWebPreviewMixin:
    """Abre/atualiza o :class:`~src.ui.web_preview.WebPreviewPanel` sobre o editor."""

    _web_preview: Any | None = None
    _web_live_job: str | None = None

    def _web_preview_alive(self) -> bool:
        panel = self._web_preview
        if panel is None:
            return False
        try:
            return bool(panel.winfo_exists())
        except tk.TclError:
            return False

    def _web_preview_root(self, path: Path) -> Path:
        ws = getattr(self, "_workspace", None)
        if ws is not None:
            with contextlib.suppress(ValueError, OSError):
                path.resolve().relative_to(Path(ws).resolve())
                return Path(ws)
        return path.parent

    def _open_web_preview(self, path: str | Path | None = None) -> None:
        """Abre o Preview Web para ``path`` (padrão: arquivo ativo ou o último previsível)."""
        from src.ui import webdev
        from src.ui.web_preview import WebPreviewPanel

        if path is None:
            ed = self._active_editor()  # type: ignore[attr-defined]
            path = getattr(ed, "path", None) if ed else None
        if path is None or not webdev.is_previewable(path):
            self._status.notify(  # type: ignore[attr-defined]
                _t("webpreview.pick_file", "Abra um arquivo .html, .md ou .svg para usar o Preview Web."))
            return
        path = Path(path)

        if self._web_preview_alive():
            self._web_preview.open_path(path)
            self._web_preview.lift()
            return
        container = getattr(self, "_editor_container", None)
        if container is None:
            return
        try:
            panel = WebPreviewPanel(
                container, self._tmgr, self._cfg,  # type: ignore[attr-defined]
                root_dir=self._web_preview_root(path),
                open_at=self._web_open_at,
                on_status=self._status.notify,  # type: ignore[attr-defined]
                on_close=self._web_preview_closed,
            )
        except (tk.TclError, OSError) as exc:
            logger.warning("Preview Web indisponível: %s", exc)
            self._status.notify(str(exc))  # type: ignore[attr-defined]
            return
        self._web_preview = panel
        panel.set_split(float(self._cfg.get("web_preview_split", 0.5)))  # type: ignore[attr-defined]
        ed = self._active_editor()  # type: ignore[attr-defined]
        panel.open_path(path)
        if ed is not None and getattr(ed, "path", None) and Path(ed.path) == path:
            self._web_preview_on_edit()

    def _web_preview_closed(self) -> None:
        self._web_preview = None

    def _web_open_at(self, file: str, line: int) -> None:
        """Clique num erro do console -> abre ``arquivo:linha`` no editor."""
        with contextlib.suppress(Exception):
            self._open_file_at_line(file, line)  # type: ignore[attr-defined]

    def _web_preview_on_edit(self) -> None:
        """Debounce (250 ms) do envio do buffer ativo ao servidor de preview."""
        if not self._web_preview_alive():
            return
        if self._web_live_job is not None:
            with contextlib.suppress(tk.TclError, ValueError):
                self._root.after_cancel(self._web_live_job)  # type: ignore[attr-defined]
        self._web_live_job = self._root.after(250, self._web_preview_push)  # type: ignore[attr-defined]

    def _web_preview_push(self) -> None:
        self._web_live_job = None
        if not self._web_preview_alive():
            return
        ed = self._active_editor()  # type: ignore[attr-defined]
        path = getattr(ed, "path", None) if ed else None
        if not path:
            return
        with contextlib.suppress(tk.TclError, AttributeError):
            if int(ed.text.index("end-1c").split(".")[0]) * 80 > _MAX_LIVE_BYTES:
                return
            self._web_preview.set_buffer(Path(path), ed.text.get("1.0", "end-1c"))

    def _web_preview_on_saved(self, path: Any) -> None:
        if self._web_preview_alive():
            with contextlib.suppress(Exception):
                self._web_preview.set_buffer(Path(path), None)

    def _web_preview_follow(self, path: Any) -> None:
        """Troca de aba: o preview acompanha o arquivo ativo se ele for previsível."""
        from src.ui import webdev

        if path and self._web_preview_alive() and webdev.is_previewable(path) and self._cfg.get(  # type: ignore[attr-defined]
            "web_preview_follow", True
        ):
            with contextlib.suppress(Exception):
                self._web_preview.open_path(Path(path))
