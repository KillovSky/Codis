"""Chamadas de Tk vindas de threads devem rodar na thread principal, sem corrupção."""

from __future__ import annotations

import threading
import time

import pytest

from src.ui import thread_safe_tk as bridge


@pytest.fixture
def installed(tk_root):
    bridge.install(tk_root)
    yield tk_root
    bridge.uninstall()


def spin(root, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        root.update()
        time.sleep(0.002)


def test_after_from_a_worker_thread_runs_on_the_main_thread(installed):
    seen = []
    main = threading.current_thread()

    def worker():
        installed.after(0, lambda: seen.append(threading.current_thread()))

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    spin(installed, 0.2)
    assert seen == [main]


def test_after_on_main_thread_keeps_original_semantics(installed):
    hit = []
    token = installed.after(10, lambda: hit.append(1))
    assert token and hit == []
    spin(installed, 0.15)
    assert hit == [1]
    cancelled = installed.after(50, lambda: hit.append(2))
    installed.after_cancel(cancelled)
    spin(installed, 0.12)
    assert hit == [1]


def test_after_idle_and_event_generate_from_threads(installed):
    got = []
    installed.bind("<<Ping>>", lambda _e: got.append(threading.current_thread()))

    def worker():
        installed.after_idle(lambda: got.append("idle"))
        installed.event_generate("<<Ping>>", when="tail")

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    spin(installed, 0.3)
    assert "idle" in got and threading.current_thread() in got


def test_many_threads_hammering_after_do_not_crash_or_lose_calls(installed):
    total, per_thread = 16, 300
    counter = {"n": 0}
    threads_seen = set()

    def cb():
        counter["n"] += 1
        threads_seen.add(threading.current_thread())

    def worker():
        for _ in range(per_thread):
            installed.after(0, cb)

    workers = [threading.Thread(target=worker) for _ in range(total)]
    for w in workers:
        w.start()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and counter["n"] < total * per_thread:
        installed.update()
        time.sleep(0.002)
    for w in workers:
        w.join()
    assert counter["n"] == total * per_thread
    assert threads_seen == {threading.current_thread()}


def test_callbacks_on_destroyed_widgets_are_ignored(installed):
    import tkinter as tk

    frame = tk.Frame(installed)
    frame.destroy()
    t = threading.Thread(target=lambda: frame.after(0, lambda: None))
    t.start()
    t.join()
    spin(installed, 0.1)


def test_order_of_calls_from_one_thread_is_preserved(installed):
    order = []

    def worker():
        for i in range(50):
            installed.after(0, lambda i=i: order.append(i))

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    spin(installed, 0.3)
    assert order == list(range(50))
