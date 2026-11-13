"""Testes para src/events/stream.py."""

from __future__ import annotations

import threading
import time

from src.events.bus import Event, EventBus
from src.events.kinds import EventKind


def _ev(kind: str = "test", payload: dict | None = None) -> Event:
    return Event(kind=kind, payload=payload or {})


def _wait(fn, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if fn():
            return True
        time.sleep(0.01)
    return False


class TestEventStreamOperators:
    def setup_method(self):
        self.bus = EventBus()

    def teardown_method(self):
        self.bus.shutdown()

    def test_filter_passes_matching(self):
        received = []
        self.bus.stream("t").filter(lambda e: e.payload.get("ok")).subscribe(
            lambda e: received.append(e)
        )

        self.bus.emit("t", {"ok": True})
        self.bus.emit("t", {"ok": False})
        assert len(received) == 1
        assert received[0].payload["ok"] is True

    def test_map_transforms_event(self):
        received = []

        def transform(e: Event) -> Event:
            return Event(kind=e.kind, payload={"v": e.payload.get("v", 0) * 10})

        self.bus.stream("t").map(transform).subscribe(
            lambda e: received.append(e))
        self.bus.emit("t", {"v": 5})
        assert received[0].payload["v"] == 50

    def test_distinct_suppresses_duplicate_payload(self):
        received = []
        self.bus.stream("t").distinct().subscribe(lambda e: received.append(e))
        self.bus.emit("t", {"x": 1})
        self.bus.emit("t", {"x": 1})
        self.bus.emit("t", {"x": 2})
        assert len(received) == 2

    def test_distinct_allows_different_payload(self):
        received = []
        self.bus.stream("t").distinct().subscribe(lambda e: received.append(e))
        self.bus.emit("t", {"x": 1})
        self.bus.emit("t", {"x": 2})
        assert len(received) == 2

    def test_aggregate_accumulates_state(self):
        received = []
        self.bus.stream("t").aggregate(
            lambda s, e: s + e.payload.get("n", 0), initial=0
        ).subscribe(lambda e: received.append(e.payload["value"]))

        self.bus.emit("t", {"n": 1})
        self.bus.emit("t", {"n": 2})
        self.bus.emit("t", {"n": 3})
        assert received == [1, 3, 6]

    def test_filter_chain_with_map(self):
        received = []
        self.bus.stream("t").filter(lambda e: e.payload.get("v", 0) > 0).map(
            lambda e: Event(kind=e.kind, payload={"v": e.payload["v"] * 2})
        ).subscribe(lambda e: received.append(e.payload["v"]))

        self.bus.emit("t", {"v": -1})
        self.bus.emit("t", {"v": 3})
        assert received == [6]

    def test_stream_with_eventkind(self):
        received = []
        self.bus.stream(EventKind.FILE_SAVE).subscribe(
            lambda e: received.append(e))
        self.bus.emit(EventKind.FILE_SAVE, {"path": "/a"})
        assert len(received) == 1

    def test_dispose_unsubscribes(self):
        received = []
        token = self.bus.stream("t").subscribe(lambda e: received.append(e))
        self.bus.emit("t")
        assert len(received) == 1

        self.bus.unsubscribe(token)
        self.bus.emit("t")
        assert len(received) == 1


class TestEventStreamTakeSkip:
    def setup_method(self):
        self.bus = EventBus()

    def teardown_method(self):
        self.bus.shutdown()

    def test_take_limits_events(self):
        received = []
        self.bus.stream("t").take(2).subscribe(lambda e: received.append(e))
        for _ in range(5):
            self.bus.emit("t")
        assert len(received) == 2

    def test_skip_drops_first_n(self):
        received = []
        self.bus.stream("t").skip(3).subscribe(lambda e: received.append(e))
        for i in range(5):
            self.bus.emit("t", {"i": i})
        assert len(received) == 2
        assert received[0].payload["i"] == 3

    def test_take_1(self):
        received = []
        self.bus.stream("t").take(1).subscribe(lambda e: received.append(e))
        self.bus.emit("t")
        self.bus.emit("t")
        self.bus.emit("t")
        assert len(received) == 1

    def test_skip_0_passes_all(self):
        received = []
        self.bus.stream("t").skip(0).subscribe(lambda e: received.append(e))
        self.bus.emit("t")
        self.bus.emit("t")
        assert len(received) == 2

    def test_skip_and_take_combined(self):
        received = []
        self.bus.stream("t").skip(2).take(2).subscribe(
            lambda e: received.append(e.payload["i"])
        )
        for i in range(6):
            self.bus.emit("t", {"i": i})
        assert received == [2, 3]


class TestEventStreamThrottle:
    def setup_method(self):
        self.bus = EventBus()

    def teardown_method(self):
        self.bus.shutdown()

    def test_throttle_limits_rate(self):
        received = []
        self.bus.stream("t").throttle(200).subscribe(
            lambda e: received.append(e))
        for _ in range(5):
            self.bus.emit("t")
        assert len(received) == 1

    def test_throttle_allows_after_cooldown(self):
        received = []
        self.bus.stream("t").throttle(50).subscribe(
            lambda e: received.append(e))
        self.bus.emit("t")
        time.sleep(0.08)
        self.bus.emit("t")
        assert len(received) == 2


class TestEventStreamDebounce:
    def setup_method(self):
        self.bus = EventBus()

    def teardown_method(self):
        self.bus.shutdown()

    def test_debounce_trailing(self):
        received = []
        done = threading.Event()

        def h(e):
            received.append(e)
            done.set()

        self.bus.stream("t").debounce(80).subscribe(h)
        for _ in range(5):
            self.bus.emit("t")

        done.wait(timeout=2.0)
        assert len(received) == 1

    def test_debounce_leading_fires_first(self):
        received = []
        done = threading.Event()

        def h(e):
            received.append(time.monotonic())
            if len(received) >= 2:
                done.set()

        self.bus.stream("t").debounce(80, leading=True).subscribe(h)
        self.bus.emit("t")
        for _ in range(4):
            self.bus.emit("t")

        done.wait(timeout=2.0)
        assert len(received) >= 1

    def test_debounce_dispose_clears_timer(self):
        received = []
        stream = self.bus.stream("t").debounce(200)
        stream.subscribe(lambda e: received.append(e))
        self.bus.emit("t")
        stream.dispose()
        time.sleep(0.25)
        assert received == []


class TestIdleStream:
    def setup_method(self):
        self.bus = EventBus()

    def teardown_method(self):
        self.bus.shutdown()

    def test_idle_fires_after_silence(self):
        received = []
        done = threading.Event()

        def h(e):
            received.append(e)
            done.set()

        self.bus.stream("t").idle(80).subscribe(h)
        self.bus.emit("t")
        done.wait(timeout=2.0)
        assert len(received) == 1

    def test_idle_resets_on_new_event(self):
        received = []

        self.bus.stream("t").idle(100).subscribe(lambda e: received.append(e))
        for _ in range(4):
            self.bus.emit("t")
            time.sleep(0.03)

        time.sleep(0.15)
        assert len(received) == 1

    def test_idle_dispose(self):
        received = []
        stream = self.bus.stream("t").idle(100)
        stream.subscribe(lambda e: received.append(e))
        stream.dispose()
        self.bus.emit("t")
        time.sleep(0.15)
        assert received == []


class TestPauseableStream:
    def setup_method(self):
        self.bus = EventBus()

    def teardown_method(self):
        self.bus.shutdown()

    def test_paused_blocks_events(self):
        received = []
        ps = self.bus.stream("t").pauseable()
        ps.subscribe(lambda e: received.append(e))
        ps.pause()
        self.bus.emit("t")
        self.bus.emit("t")
        assert received == []

    def test_resumed_receives_events(self):
        received = []
        ps = self.bus.stream("t").pauseable()
        ps.subscribe(lambda e: received.append(e))
        ps.pause()
        ps.resume()
        self.bus.emit("t")
        assert len(received) == 1

    def test_paused_property(self):
        ps = self.bus.stream("t").pauseable()
        ps.subscribe(lambda e: None)
        assert ps.paused is False
        ps.pause()
        assert ps.paused is True
        ps.resume()
        assert ps.paused is False

    def test_dispose_unsubscribes(self):
        received = []
        ps = self.bus.stream("t").pauseable()
        ps.subscribe(lambda e: received.append(e))
        self.bus.emit("t")
        ps.dispose()
        self.bus.emit("t")
        assert len(received) == 1


class TestWindowedStream:
    def setup_method(self):
        self.bus = EventBus()

    def teardown_method(self):
        self.bus.shutdown()

    def test_windowed_collects_batch(self):
        received_batches = []
        done = threading.Event()

        def on_batch(batch):
            received_batches.append(batch)
            done.set()

        ws = self.bus.stream("t").window(80)
        ws.subscribe(on_batch)

        self.bus.emit("t", {"i": 0})
        self.bus.emit("t", {"i": 1})

        done.wait(timeout=2.0)
        ws.dispose()

        assert len(received_batches) >= 1
        assert len(received_batches[0]) >= 1

    def test_windowed_dispose(self):
        received = []
        ws = self.bus.stream("t").window(10_000)
        ws.subscribe(lambda b: received.extend(b))
        ws.dispose()
        self.bus.emit("t")
        time.sleep(0.05)
        assert received == []
