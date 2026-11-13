"""Regressões do painel de preview de temas."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.theme.base import THEME_ORDER, THEMES, _define_themes
from src.ui import theme_preview_panel as tpp

_define_themes()


class _FakePanel:
    """Dublê mínimo de ThemePreviewPanel para exercitar a lógica de reflow."""

    _schedule_reflow = tpp.ThemePreviewPanel._schedule_reflow
    _run_reflow = tpp.ThemePreviewPanel._run_reflow
    _shutdown = tpp.ThemePreviewPanel._shutdown
    _column_count = tpp.ThemePreviewPanel._column_count

    def __init__(self, width: int = 700):
        self._closed = False
        self._reflow_job = None
        self._cols = 0
        self._width = width
        self.scheduled: list[tuple[int, object]] = []
        self.cancelled: list[object] = []
        self.rebuilds = 0
        self._canvas = self

    def winfo_width(self):
        return self._width

    def after(self, ms, fn):
        self.scheduled.append((ms, fn))
        return f"job{len(self.scheduled)}"

    def after_cancel(self, job):
        self.cancelled.append(job)

    def _apply_filter(self):
        self.rebuilds += 1
        self._cols = self._column_count()


def test_first_configure_event_does_not_raise():
    panel = _FakePanel()
    panel._schedule_reflow()
    assert panel.scheduled and panel.scheduled[0][0] == 120


def test_repeated_configure_events_cancel_previous_job():
    panel = _FakePanel()
    panel._schedule_reflow()
    panel._schedule_reflow()
    assert panel.cancelled == ["job1"]


def test_reflow_rebuilds_only_when_columns_change():
    panel = _FakePanel(width=700)
    panel._run_reflow()
    assert panel.rebuilds == 1
    panel._width = 710
    panel._run_reflow()
    assert panel.rebuilds == 1
    panel._width = 400
    panel._run_reflow()
    assert panel.rebuilds == 2


def test_closed_panel_ignores_reflow_and_cancels_pending_job():
    panel = _FakePanel()
    panel._schedule_reflow()
    panel._shutdown()
    assert panel._closed and panel.cancelled == ["job1"]
    panel._schedule_reflow()
    panel._run_reflow()
    assert len(panel.scheduled) == 1 and panel.rebuilds == 0


@pytest.fixture
def permissive_tk(monkeypatch):
    """Widgets filhos viram MagicMock e os cards ganham métodos Tk no-op.

    O stub headless de Tkinter não implementa todos os métodos de layout; o
    objetivo aqui é só exercitar o acesso aos atributos de cada tema.
    """

    def noop(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *a, **k: []

    monkeypatch.setattr(tpp, "tk", MagicMock())
    monkeypatch.setattr(tpp._ThemeCard, "__getattr__", noop, raising=False)
    monkeypatch.setattr(tpp._ThemePreview, "__getattr__", noop, raising=False)


@pytest.mark.parametrize("name", THEME_ORDER)
def test_every_theme_builds_card_and_preview(permissive_tk, name):
    theme = THEMES[name]
    tpp._ThemeCard(None, theme, on_select=lambda _n: None, on_apply=lambda _n: None)
    tpp._ThemePreview(None)._render(theme)
