"""O hover de diagnósticos deve ser barato e não recriar o balão a cada movimento."""

from __future__ import annotations

from types import SimpleNamespace

from src.editor import _pane_lsp as pl


class _Text:
    def __init__(self, line=3):
        self.line = line
        self.jobs = []
        self.cancelled = []
        self.index_calls = 0

    def index(self, _spec):
        self.index_calls += 1
        return f"{self.line}.0"

    def after(self, ms, fn):
        self.jobs.append((ms, fn))
        return f"job{len(self.jobs)}"

    def after_cancel(self, job):
        self.cancelled.append(job)


class _Pane:
    _diags_for_line = pl._PaneLSPMixin._diags_for_line
    _on_mouse_motion_diag = pl._PaneLSPMixin._on_mouse_motion_diag

    def __init__(self, diags):
        self._diags = diags
        self.text = _Text()
        self._diag_tip_job = None
        self._diag_tip_win = None
        self._diag_motion_t = 0.0
        self._diag_tip_line = None
        self._diag_index_src = None
        self._diag_index_len = -1
        self._diag_index = {}
        self.hidden = 0

    def _hide_diag_tip(self, _e=None):
        self.hidden += 1
        self._diag_tip_job = None
        self._diag_tip_win = None
        self._diag_tip_line = None

    def _show_diag_tip(self, *_a):
        pass


def _event():
    return SimpleNamespace(x=1, y=1, x_root=1, y_root=1)


def test_index_groups_by_line_and_rebuilds_when_list_changes():
    d1, d2, d3 = (SimpleNamespace(line=n) for n in (1, 1, 2))
    pane = _Pane([d1, d2, d3])
    assert pane._diags_for_line(1) == [d1, d2]
    assert pane._diags_for_line(9) == []
    pane._diags.append(SimpleNamespace(line=9))
    assert len(pane._diags_for_line(9)) == 1


def test_motion_events_are_throttled(monkeypatch):
    pane = _Pane([SimpleNamespace(line=3)])
    clock = iter([100.0, 100.001, 100.002, 100.5])
    monkeypatch.setattr(pl.time, "monotonic", lambda: next(clock))
    for _ in range(4):
        pane._on_mouse_motion_diag(_event())
    assert pane.text.index_calls == 2


def test_same_line_does_not_reschedule_tooltip(monkeypatch):
    pane = _Pane([SimpleNamespace(line=3)])
    clock = iter([10.0, 11.0, 12.0])
    monkeypatch.setattr(pl.time, "monotonic", lambda: next(clock))
    for _ in range(3):
        pane._on_mouse_motion_diag(_event())
    assert len(pane.text.jobs) == 1


def test_lines_without_diags_do_not_touch_the_tooltip(monkeypatch):
    pane = _Pane([SimpleNamespace(line=99)])
    monkeypatch.setattr(pl.time, "monotonic", lambda: 50.0)
    pane._on_mouse_motion_diag(_event())
    assert pane.hidden == 0 and pane.text.jobs == []
