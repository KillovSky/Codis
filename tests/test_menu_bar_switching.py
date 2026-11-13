"""Regressões do flicker ao passar o mouse rapidamente entre menus da barra."""

from __future__ import annotations

from src.ui import menu_bar as mb


class _Btn:
    def __init__(self, name, log):
        self.name, self.log = name, log
        self.widget = object()
        self._open = False
        self._drop = None

    @property
    def is_open(self):
        return self._open

    def open(self, animate=True):
        self._open = True
        self.log.append(("open", self.name, animate))

    def close(self):
        self._open = False
        self.log.append(("close", self.name))


class _Bar:
    _request_switch = mb.MenuBar._request_switch
    _cancel_switch = mb.MenuBar._cancel_switch
    _commit_switch = mb.MenuBar._commit_switch
    _open = mb.MenuBar._open
    _close_all = mb.MenuBar._close_all

    def __init__(self):
        self._active = None
        self._switch_job = None
        self._last_open = 0.0
        self.jobs = {}
        self.cancelled = []
        self.pointer_target = None
        self._counter = 0

    def after(self, ms, fn):
        self._counter += 1
        job = f"job{self._counter}"
        self.jobs[job] = (ms, fn)
        return job

    def after_cancel(self, job):
        self.cancelled.append(job)
        self.jobs.pop(job, None)

    def winfo_pointerxy(self):
        return (0, 0)

    def winfo_containing(self, _x, _y):
        return self.pointer_target


def _setup():
    log: list = []
    bar = _Bar()
    return bar, _Btn("A", log), _Btn("B", log), log


def test_first_open_animates_and_switch_is_instant():
    bar, a, b, log = _setup()
    bar._open(a)
    bar._open(b)
    assert log[0] == ("open", "A", True)
    assert log[1] == ("open", "B", False)


def test_new_menu_opens_before_previous_closes():
    bar, a, b, log = _setup()
    bar._open(a)
    log.clear()
    bar._open(b)
    assert [entry[:2] for entry in log] == [("open", "B"), ("close", "A")]


def test_reopening_the_open_menu_does_nothing():
    bar, a, _b, log = _setup()
    bar._open(a)
    log.clear()
    bar._open(a)
    assert log == []


def test_hover_switch_is_delayed_and_cancels_previous_request():
    bar, a, b, _log = _setup()
    bar._open(a)
    bar._request_switch(b)
    bar._request_switch(b)
    assert len(bar.jobs) == 1 and bar.cancelled == ["job1"]
    assert next(iter(bar.jobs.values()))[0] >= mb._HOVER_INTENT_MS


def test_switch_right_after_open_waits_for_cooldown():
    bar, a, b, _log = _setup()
    bar._open(a)
    bar._request_switch(b)
    delay = next(iter(bar.jobs.values()))[0]
    assert delay >= 60


def test_commit_ignored_when_pointer_left_the_button():
    bar, a, b, log = _setup()
    bar._open(a)
    log.clear()
    bar.pointer_target = object()
    bar._commit_switch(b)
    assert log == [] and bar._active is a


def test_commit_opens_when_pointer_is_over_the_button():
    bar, a, b, log = _setup()
    bar._open(a)
    bar.pointer_target = b.widget
    bar._commit_switch(b)
    assert bar._active is b


def test_commit_does_nothing_when_no_menu_is_open():
    bar, _a, b, log = _setup()
    bar.pointer_target = b.widget
    bar._commit_switch(b)
    assert bar._active is None and log == []


def test_close_all_cancels_pending_switch():
    bar, a, b, _log = _setup()
    bar._open(a)
    bar._request_switch(b)
    bar._close_all()
    assert bar._switch_job is None and not bar.jobs
    assert bar._active is None


class _Drop:
    _is_current = mb._DropMenu._is_current
    _on_focusout = mb._DropMenu._on_focusout

    def __init__(self, bar):
        self._bar = bar
        self._sub = None
        self._win = _Win()


class _Win:
    def __init__(self):
        self.scheduled = []

    def after(self, ms, fn, *args):
        self.scheduled.append(lambda: fn(*args))

    def focus_get(self):
        return None


class _ActiveBtn:
    def __init__(self, drop):
        self._drop = drop


def test_focusout_of_replaced_popup_does_not_close_the_new_menu():
    closed = []
    bar = type("B", (), {"_active": None, "_close_all": lambda self: closed.append(1)})()
    old, new = _Drop(bar), _Drop(bar)
    bar._active = _ActiveBtn(new)
    old._on_focusout(None)
    old._win.scheduled[0]()
    assert closed == []


def _focus_bar(closed, *, pointer_inside, recent):
    """Barra falsa para o fluxo de FocusOut: foco sem dono."""
    refocused = []
    bar = type("B", (), {
        "_active": None,
        "_close_all": lambda self: closed.append(1),
        "_pointer_in_menus": lambda self: pointer_inside,
        "_chain_changed_recently": lambda self: recent,
        "_refocus_deepest": lambda self: refocused.append(1),
    })()
    bar.refocused = refocused
    return bar


def test_focusout_with_pointer_outside_menus_closes():
    """Clique fora / app desativado: foco sem dono e ponteiro fora => fecha."""
    closed = []
    bar = _focus_bar(closed, pointer_inside=False, recent=False)
    drop = _Drop(bar)
    bar._active = _ActiveBtn(drop)
    drop._on_focusout(None)
    drop._win.scheduled[0]()
    assert closed == [1]


def test_focusout_with_pointer_inside_menus_keeps_them_open_and_refocuses():
    """Submenu criado/destruído ao mover rápido: não fecha, devolve o foco."""
    closed = []
    bar = _focus_bar(closed, pointer_inside=True, recent=False)
    drop = _Drop(bar)
    bar._active = _ActiveBtn(drop)
    drop._on_focusout(None)
    drop._win.scheduled[0]()
    assert closed == [] and bar.refocused == [1]
    assert len(drop._win.scheduled) == 2


def test_focusout_right_after_chain_change_is_deferred_not_closed():
    closed = []
    bar = _focus_bar(closed, pointer_inside=False, recent=True)
    drop = _Drop(bar)
    bar._active = _ActiveBtn(drop)
    drop._on_focusout(None)
    drop._win.scheduled[0]()
    assert closed == []
    bar._chain_changed_recently = lambda: False
    drop._win.scheduled[1]()
    assert closed == [1]


def test_submenu_belongs_to_current_chain():
    bar = type("B", (), {"_active": None})()
    parent, sub = _Drop(bar), _Drop(bar)
    parent._sub = sub
    bar._active = _ActiveBtn(parent)
    assert sub._is_current() and parent._is_current()
