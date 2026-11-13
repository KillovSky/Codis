"""Testes para src/events/pipeline.py, correlator.py e window.py."""

from __future__ import annotations

import threading
import time

import pytest

from src.events.bus import Event, EventBus
from src.events.correlator import EventCorrelator, SequenceDetector
from src.events.kinds import EventKind
from src.events.pipeline import Pipeline, StageMetrics
from src.events.window import SlidingWindow, TumblingWindow, WindowManager


def _ev(kind: str = "test", payload: dict | None = None) -> Event:
    return Event(kind=kind, payload=payload or {})


def _wait(fn, timeout=2.0, interval=0.02):
    """Aguarda até fn() ser verdadeiro ou timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if fn():
            return True
        time.sleep(interval)
    return False


class TestStageMetrics:
    def test_defaults(self):
        m = StageMetrics()
        assert m.processed == 0
        assert m.dropped == 0
        assert m.errors == 0
        assert m.avg_us == 0.0
        assert m.throughput == 0.0

    def test_avg_us_with_data(self):
        m = StageMetrics(processed=2, total_time_ns=2_000_000)
        assert m.avg_us == pytest.approx(1_000.0)

    def test_throughput_nonzero(self):
        m = StageMetrics(processed=100, total_time_ns=1_000_000_000)
        assert m.throughput == pytest.approx(100.0)


class TestPipeline:
    def test_repr(self):
        p = Pipeline("test")
        p.add_stage("a", lambda e: e)
        assert "test" in repr(p)
        assert "a" in repr(p)

    def test_push_before_start_returns_false(self):
        p = Pipeline("p")
        p.add_stage("s", lambda e: e)
        assert p.push(_ev()) is False

    def test_start_empty_raises(self):
        p = Pipeline("empty")
        with pytest.raises(ValueError):
            p.start()

    def test_add_stage_after_start_raises(self):
        p = Pipeline("p")
        p.add_stage("s", lambda e: e)
        p.start()
        try:
            with pytest.raises(RuntimeError):
                p.add_stage("s2", lambda e: e)
        finally:
            p.stop()

    def test_single_stage_delivery(self):
        received = []
        done = threading.Event()

        def sink(e):
            received.append(e.kind)
            done.set()
            return None

        p = Pipeline("single")
        p.add_stage("sink", sink)
        p.start()
        try:
            p.push(_ev("hello"))
            done.wait(timeout=2.0)
        finally:
            p.stop()

        assert "hello" in received

    def test_chain_two_stages(self):
        received = []
        done = threading.Event()

        def double(e: Event) -> Event:
            return Event(kind=e.kind, payload={"v": e.payload.get("v", 0) * 2})

        def sink(e: Event):
            received.append(e.payload["v"])
            done.set()
            return None

        p = Pipeline("chain")
        p.add_stage("double", double)
        p.add_stage("sink", sink)
        p.start()
        try:
            p.push(_ev("x", {"v": 3}))
            done.wait(timeout=2.0)
        finally:
            p.stop()

        assert 6 in received

    def test_none_return_drops_event(self):
        received = []

        def drop_all(e):
            return None

        def sink(e):
            received.append(e)
            return None

        p = Pipeline("drop")
        p.add_stage("filter", drop_all)
        p.add_stage("sink", sink)
        p.start()
        try:
            p.push(_ev())
            time.sleep(0.1)
        finally:
            p.stop()

        assert received == []

    def test_metrics_structure(self):
        p = Pipeline("m")
        p.add_stage("s1", lambda e: e)
        p.add_stage("s2", lambda e: None)
        p.start()
        try:
            m = p.metrics()
        finally:
            p.stop()
        assert "s1" in m
        assert "s2" in m
        for stage_m in m.values():
            assert "processed" in stage_m
            assert "dropped" in stage_m
            assert "errors" in stage_m
            assert "avg_us" in stage_m

    def test_error_in_stage_increments_errors(self):
        done = threading.Event()

        def bad(e):
            done.set()
            raise RuntimeError("boom")

        p = Pipeline("err")
        p.add_stage("bad", bad)
        p.start()
        try:
            p.push(_ev())
            done.wait(timeout=2.0)
            time.sleep(0.05)
            m = p.metrics()
        finally:
            p.stop()

        assert m["bad"]["errors"] >= 1

    def test_stop_idempotent(self):
        p = Pipeline("p")
        p.add_stage("s", lambda e: e)
        p.start()
        p.stop()
        p.stop()

    def test_push_after_stop_returns_false(self):
        p = Pipeline("p")
        p.add_stage("s", lambda e: e)
        p.start()
        p.stop()
        assert p.push(_ev()) is False

    def test_discard_drop_policy(self):
        """drop_policy='discard' não bloqueia em fila cheia."""
        p = Pipeline("disc")
        p.add_stage("s", lambda e: None, capacity=1,
                    drop_policy="discard", workers=1)
        p.start()
        try:
            for _ in range(20):
                p.push(_ev())
        finally:
            p.stop()


class TestEventCorrelator:
    def setup_method(self):
        self.bus = EventBus()

    def teardown_method(self):
        self.bus.shutdown()

    def test_attach_without_triggers_raises(self):
        corr = EventCorrelator(window_ms=500)
        with pytest.raises(ValueError):
            corr.attach(self.bus)

    def test_match_fires_when_all_triggers_received(self):
        matched = []
        done = threading.Event()

        corr = EventCorrelator(window_ms=500)
        corr.add_trigger(EventKind.EDITOR_CHANGE)
        corr.add_trigger(EventKind.FILE_SAVE)
        corr.on_match(lambda evs: (matched.append(evs), done.set()))
        corr.attach(self.bus)

        self.bus.emit(EventKind.EDITOR_CHANGE)
        self.bus.emit(EventKind.FILE_SAVE)

        done.wait(timeout=2.0)
        assert len(matched) == 1
        assert EventKind.EDITOR_CHANGE.value in matched[0]
        assert EventKind.FILE_SAVE.value in matched[0]
        corr.detach()

    def test_no_match_if_only_one_trigger(self):
        matched = []
        corr = EventCorrelator(window_ms=100)
        corr.add_trigger("a")
        corr.add_trigger("b")
        corr.on_match(lambda evs: matched.append(evs))
        corr.attach(self.bus)

        self.bus.emit("a")
        time.sleep(0.15)

        assert matched == []
        corr.detach()

    def test_reset_clears_state(self):
        matched = []
        corr = EventCorrelator(window_ms=500)
        corr.add_trigger("x")
        corr.add_trigger("y")
        corr.on_match(lambda evs: matched.append(evs))
        corr.attach(self.bus)

        self.bus.emit("x")
        corr.reset()
        self.bus.emit("y")
        time.sleep(0.05)

        assert matched == []
        corr.detach()

    def test_detach_stops_subscription(self):
        matched = []
        corr = EventCorrelator(window_ms=500)
        corr.add_trigger("a")
        corr.add_trigger("b")
        corr.on_match(lambda evs: matched.append(evs))
        corr.attach(self.bus)
        corr.detach()

        self.bus.emit("a")
        self.bus.emit("b")
        time.sleep(0.05)
        assert matched == []

    def test_add_trigger_returns_self(self):
        corr = EventCorrelator(window_ms=100)
        result = corr.add_trigger("x")
        assert result is corr

    def test_on_match_returns_self(self):
        corr = EventCorrelator(window_ms=100)
        result = corr.on_match(lambda evs: None)
        assert result is corr


class TestSequenceDetector:
    def setup_method(self):
        self.bus = EventBus()

    def teardown_method(self):
        self.bus.shutdown()

    def test_ordered_sequence_fires(self):
        complete = []
        done = threading.Event()

        seq = SequenceDetector(window_ms=500)
        seq.then(EventKind.EDITOR_CURSOR_MOVE)
        seq.then(EventKind.EDITOR_FOCUS)
        seq.on_complete(lambda evts: (complete.append(evts), done.set()))
        seq.attach(self.bus)

        self.bus.emit(EventKind.EDITOR_CURSOR_MOVE)
        self.bus.emit(EventKind.EDITOR_FOCUS)

        done.wait(timeout=2.0)
        assert len(complete) == 1
        assert len(complete[0]) == 2
        seq.detach()

    def test_wrong_order_does_not_fire(self):
        complete = []
        seq = SequenceDetector(window_ms=500)
        seq.then("first")
        seq.then("second")
        seq.on_complete(lambda evts: complete.append(evts))
        seq.attach(self.bus)

        self.bus.emit("second")
        self.bus.emit("first")
        time.sleep(0.05)

        assert complete == []
        seq.detach()

    def test_then_returns_self(self):
        seq = SequenceDetector(window_ms=100)
        result = seq.then("x")
        assert result is seq

    def test_on_complete_returns_self(self):
        seq = SequenceDetector(window_ms=100)
        result = seq.on_complete(lambda evts: None)
        assert result is seq

    def test_detach_stops_detection(self):
        complete = []
        seq = SequenceDetector(window_ms=500)
        seq.then("a").then("b")
        seq.on_complete(lambda evts: complete.append(evts))
        seq.attach(self.bus)
        seq.detach()

        self.bus.emit("a")
        self.bus.emit("b")
        time.sleep(0.05)
        assert complete == []

    def test_sequence_resets_after_completion(self):
        complete = []
        done = threading.Event()

        seq = SequenceDetector(window_ms=500)
        seq.then("p").then("q")

        def on_complete(evts):
            complete.append(evts)
            if len(complete) >= 2:
                done.set()

        seq.on_complete(on_complete)
        seq.attach(self.bus)

        self.bus.emit("p")
        self.bus.emit("q")
        self.bus.emit("p")
        self.bus.emit("q")

        done.wait(timeout=2.0)
        assert len(complete) == 2
        seq.detach()


class TestTumblingWindow:
    def test_flush_delivers_batch(self):
        received = []
        w = TumblingWindow(
            size_ms=10_000, on_close=lambda b: received.extend(b))
        w.push(_ev("a"))
        w.push(_ev("b"))
        w.flush()
        w.stop()
        assert len(received) == 2

    def test_stop_prevents_new_events(self):
        received = []
        w = TumblingWindow(size_ms=50, on_close=lambda b: received.extend(b))
        w.stop()
        w.push(_ev())
        time.sleep(0.1)
        assert received == []

    def test_min_events_respected(self):
        received_batches = []
        w = TumblingWindow(
            size_ms=10_000, on_close=lambda b: received_batches.append(b), min_events=3
        )
        w.push(_ev())
        w.flush()
        w.stop()
        assert received_batches == []

    def test_min_events_fires_when_met(self):
        received = []
        w = TumblingWindow(
            size_ms=10_000, on_close=lambda b: received.extend(b), min_events=2
        )
        w.push(_ev("x"))
        w.push(_ev("y"))
        w.flush()
        w.stop()
        assert len(received) == 2

    def test_timer_fires_automatically(self):
        received = []
        done = threading.Event()

        def on_close(batch):
            received.extend(batch)
            done.set()

        w = TumblingWindow(size_ms=50, on_close=on_close)
        w.push(_ev("auto"))
        done.wait(timeout=2.0)
        w.stop()
        assert any(e.kind == "auto" for e in received)


class TestSlidingWindow:
    def test_sliding_window_fires_within_timeout(self):
        received_batches = []
        done = threading.Event()

        def on_slide(batch):
            received_batches.append(batch)
            done.set()

        w = SlidingWindow(size_ms=100, step_ms=50, on_close=on_slide)
        w.push(_ev("s1"))
        w.push(_ev("s2"))
        done.wait(timeout=3.0)
        w.stop()
        assert len(received_batches) >= 1
        assert any(e.kind in ("s1", "s2")
                   for batch in received_batches for e in batch)

    def test_stop_idempotent(self):
        w = SlidingWindow(size_ms=100, step_ms=50, on_close=lambda b: None)
        w.stop()
        w.stop()


class TestWindowManager:
    def test_create_tumbling(self):
        received = []
        mgr = WindowManager()
        mgr.tumbling("t", size_ms=10_000,
                     on_close=lambda b: received.extend(b))
        mgr.push("t", _ev("ok"))
        mgr.stop("t")

    def test_create_sliding(self):
        mgr = WindowManager()
        mgr.sliding("s", size_ms=100, step_ms=50, on_close=lambda b: None)
        mgr.push("s", _ev())
        mgr.stop("s")

    def test_push_unknown_window_ignored(self):
        mgr = WindowManager()
        mgr.push("ghost", _ev())

    def test_stop_unknown_ignored(self):
        mgr = WindowManager()
        mgr.stop("ghost")

    def test_stop_all(self):
        received = []
        mgr = WindowManager()
        mgr.tumbling("a", size_ms=10_000,
                     on_close=lambda b: received.extend(b))
        mgr.tumbling("b", size_ms=10_000,
                     on_close=lambda b: received.extend(b))
        mgr.push("a", _ev("a"))
        mgr.push("b", _ev("b"))
        mgr.stop_all()

    def test_multiple_windows_independent(self):
        r1, r2 = [], []
        mgr = WindowManager()
        mgr.tumbling("w1", size_ms=10_000, on_close=lambda b: r1.extend(b))
        mgr.tumbling("w2", size_ms=10_000, on_close=lambda b: r2.extend(b))
        mgr.push("w1", _ev("only-w1"))
        mgr.stop_all()
