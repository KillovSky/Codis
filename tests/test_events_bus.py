"""Testes para src/events/bus.py, events/kinds.py e events/__init__.py."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

import src.events as events_pkg
from src.events.bus import (
    Event,
    EventBus,
    SubscriptionGroup,
    SubscriptionToken,
    _TopicMetrics,
)
from src.events.kinds import EventKind


class TestEventKind:
    def test_editor_kinds_are_strings(self):
        assert EventKind.EDITOR_CHANGE == "editor.change"
        assert EventKind.FILE_SAVE == "file.save"
        assert EventKind.LSP_DIAGNOSTICS == "lsp.diagnostics"

    def test_all_kinds_have_dot_prefix(self):
        """Todo EventKind (exceto WILDCARD) deve ter a forma 'namespace.action'."""
        for kind in EventKind:
            if kind == EventKind.WILDCARD:
                continue
            assert "." in kind.value, f"{kind} sem ponto: {kind.value!r}"

    def test_kind_usable_as_string(self):
        assert str(EventKind.EDITOR_CHANGE) == "editor.change"

    def test_git_kinds_exist(self):
        assert EventKind.GIT_COMMIT
        assert EventKind.GIT_BRANCH

    def test_theme_kinds_exist(self):
        assert EventKind.THEME_CHANGE

    def test_plugin_kinds_exist(self):
        assert EventKind.PLUGIN_LOADED

    def test_ia_kinds_exist(self):
        assert EventKind.IA_REQUEST
        assert EventKind.IA_DONE
        assert EventKind.IA_ERROR

    def test_at_least_50_kinds(self):
        assert len(EventKind) >= 50


class TestEvent:
    def test_defaults(self):
        e = Event(kind="test")
        assert e.kind == "test"
        assert e.payload == {}
        assert e.source == ""
        assert e.seq == 0
        assert e.batch_id == 0
        assert e.timestamp > 0

    def test_frozen(self):
        e = Event(kind="x")
        with pytest.raises((TypeError, AttributeError)):
            e.kind = "y"  # type: ignore[misc]

    def test_custom_payload(self):
        e = Event(kind="test", payload={
                  "path": "/foo"}, source="editor", seq=5)
        assert e.payload["path"] == "/foo"
        assert e.source == "editor"
        assert e.seq == 5


class TestSubscriptionToken:
    def test_namedtuple_fields(self):
        t = SubscriptionToken(topic="editor.change", handler_id=7)
        assert t.topic == "editor.change"
        assert t.handler_id == 7

    def test_equality(self):
        a = SubscriptionToken("a", 1)
        b = SubscriptionToken("a", 1)
        assert a == b


class TestTopicMetrics:
    def test_initial_state(self):
        m = _TopicMetrics()
        assert m.emit_count == 0
        assert m.handler_errors == 0
        assert m.avg_us == 0.0
        assert m.p50_us == 0.0
        assert m.p95_us == 0.0

    def test_record_increments(self):
        m = _TopicMetrics()
        m.record(elapsed_ns=1_000_000, errors=0)
        assert m.emit_count == 1
        assert m.avg_us == pytest.approx(1_000.0)

    def test_error_count(self):
        m = _TopicMetrics()
        m.record(1_000, 2)
        assert m.handler_errors == 2

    def test_percentile_single_sample(self):
        m = _TopicMetrics()
        m.record(5_000_000, 0)
        assert m.p50_us == pytest.approx(5_000.0)
        assert m.p95_us == pytest.approx(5_000.0)

    def test_percentile_multiple_samples(self):
        m = _TopicMetrics()
        for ns in [1_000, 2_000, 3_000, 4_000, 10_000_000]:
            m.record(ns, 0)
        assert m.p50_us > 0
        assert m.p95_us >= m.p50_us


class TestEventBusSubscribe:
    def setup_method(self):
        self.bus = EventBus(max_workers=2)

    def teardown_method(self):
        self.bus.shutdown()

    def test_subscribe_returns_token(self):
        token = self.bus.subscribe("test", lambda e: None)
        assert isinstance(token, SubscriptionToken)
        assert token.topic == "test"

    def test_subscribe_with_eventkind(self):
        token = self.bus.subscribe(EventKind.EDITOR_CHANGE, lambda e: None)
        assert token.topic == "editor.change"

    def test_unsubscribe_returns_true_if_found(self):
        token = self.bus.subscribe("x", lambda e: None)
        assert self.bus.unsubscribe(token) is True

    def test_unsubscribe_returns_false_if_missing(self):
        token = SubscriptionToken(topic="ghost", handler_id=999)
        assert self.bus.unsubscribe(token) is False

    def test_priority_order(self):
        """Handlers com prioridade menor devem ser chamados primeiro."""
        order = []
        self.bus.subscribe("p", lambda e: order.append("high"), priority=10)
        self.bus.subscribe("p", lambda e: order.append("low"), priority=90)
        self.bus.emit("p")
        assert order == ["high", "low"]

    def test_subscribe_once_auto_unsubscribes(self):
        calls = []
        self.bus.once("once", lambda e: calls.append(1))
        self.bus.emit("once")
        self.bus.emit("once")
        assert calls == [1]

    def test_once_method_shortcut(self):
        calls = []
        self.bus.once("q", lambda e: calls.append(1))
        self.bus.emit("q")
        self.bus.emit("q")
        assert len(calls) == 1


class TestEventBusEmit:
    def setup_method(self):
        self.bus = EventBus(max_workers=2)

    def teardown_method(self):
        self.bus.shutdown()

    def test_emit_calls_handler(self):
        received = []
        self.bus.subscribe("ev", lambda e: received.append(e))
        self.bus.emit("ev", {"x": 1})
        assert len(received) == 1
        assert received[0].payload["x"] == 1

    def test_emit_with_eventkind(self):
        received = []
        self.bus.subscribe(EventKind.FILE_SAVE, lambda e: received.append(e))
        self.bus.emit(EventKind.FILE_SAVE, {"path": "/a"})
        assert received[0].payload["path"] == "/a"

    def test_emit_source_propagated(self):
        received = []
        self.bus.subscribe("s", lambda e: received.append(e))
        self.bus.emit("s", source="editor")
        assert received[0].source == "editor"

    def test_emit_sequence_numbers_increment(self):
        received = []
        self.bus.subscribe("s", lambda e: received.append(e.seq))
        self.bus.emit("s")
        self.bus.emit("s")
        assert received[1] == received[0] + 1

    def test_emit_handler_error_does_not_crash(self):
        def bad(e):
            raise ValueError("boom")

        self.bus.subscribe("err", bad)
        self.bus.emit("err")

    def test_emit_async_delivers(self):
        received = []
        ev = threading.Event()

        def h(e):
            received.append(e)
            ev.set()

        self.bus.subscribe("async", h)
        self.bus.emit("async", async_=True)
        ev.wait(timeout=2.0)
        assert len(received) == 1

    def test_wildcard_subscription(self):
        received = []
        self.bus.subscribe("*", lambda e: received.append(e.kind))
        self.bus.emit("editor.change")
        self.bus.emit("file.save")
        assert "editor.change" in received
        assert "file.save" in received

    def test_hierarchical_wildcard(self):
        received = []
        self.bus.subscribe("editor.*", lambda e: received.append(e.kind))
        self.bus.emit("editor.change")
        self.bus.emit("editor.save")
        self.bus.emit("file.save")
        assert all(k.startswith("editor.") for k in received)
        assert "file.save" not in received

    def test_no_handler_no_error(self):
        self.bus.emit("no_subscriber")

    def test_metrics_recorded(self):
        self.bus.subscribe("m", lambda e: None)
        self.bus.emit("m")
        self.bus.emit("m")
        m = self.bus.metrics()
        assert m["m"]["emit_count"] == 2

    def test_metrics_error_count(self):
        def bad(e):
            raise RuntimeError

        self.bus.subscribe("e", bad)
        self.bus.emit("e")
        m = self.bus.metrics()
        assert m["e"]["handler_errors"] >= 1


class TestEventBusEmitBatch:
    def setup_method(self):
        self.bus = EventBus()

    def teardown_method(self):
        self.bus.shutdown()

    def test_batch_delivers_all(self):
        received = []
        self.bus.subscribe("a", lambda e: received.append(e.kind))
        self.bus.subscribe("b", lambda e: received.append(e.kind))
        self.bus.emit_batch([("a", {}), ("b", {"x": 1})])
        assert "a" in received
        assert "b" in received

    def test_batch_same_batch_id(self):
        received = []
        self.bus.subscribe("x", lambda e: received.append(e.batch_id))
        self.bus.subscribe("y", lambda e: received.append(e.batch_id))
        self.bus.emit_batch([("x", {}), ("y", {})])
        assert received[0] == received[1]
        assert received[0] > 0

    def test_batch_empty_does_nothing(self):
        self.bus.emit_batch([])

    def test_batch_contiguous_seqs(self):
        received = []
        self.bus.subscribe("t", lambda e: received.append(e.seq))
        self.bus.emit_batch([("t", {}), ("t", {}), ("t", {})])
        seqs = sorted(received)
        assert seqs[1] == seqs[0] + 1
        assert seqs[2] == seqs[1] + 1


class TestEventBusEmitTk:
    def setup_method(self):
        self.bus = EventBus()

    def teardown_method(self):
        self.bus.shutdown()

    def test_emit_tk_without_root_falls_back_to_emit(self):
        received = []
        self.bus.subscribe("t", lambda e: received.append(e))
        self.bus.emit_tk("t", {"a": 1})
        assert len(received) == 1

    def test_set_tk_root_accepted(self):
        mock_root = MagicMock()
        self.bus.set_tk_root(mock_root)
        assert self.bus._tk_root is mock_root


class TestSubscriptionGroup:
    def setup_method(self):
        self.bus = EventBus()

    def teardown_method(self):
        self.bus.shutdown()

    def test_group_subscribe_and_dispose(self):
        received = []
        g = self.bus.group()
        g.subscribe("t", lambda e: received.append(1))
        self.bus.emit("t")
        g.dispose()
        self.bus.emit("t")
        assert received == [1]

    def test_group_context_manager(self):
        received = []
        with self.bus.group() as g:
            g.subscribe("t", lambda e: received.append(1))
            self.bus.emit("t")
        self.bus.emit("t")
        assert received == [1]

    def test_group_multiple_topics(self):
        received = []
        with self.bus.group() as g:
            g.subscribe("a", lambda e: received.append("a"))
            g.subscribe("b", lambda e: received.append("b"))
            self.bus.emit("a")
            self.bus.emit("b")
        self.bus.emit("a")
        self.bus.emit("b")
        assert received == ["a", "b"]

    def test_group_method_returns_group(self):
        g = self.bus.group()
        assert isinstance(g, SubscriptionGroup)

    def test_dispose_idempotent(self):
        g = self.bus.group()
        g.subscribe("x", lambda e: None)
        g.dispose()
        g.dispose()


class TestRecursiveEmit:
    def setup_method(self):
        self.bus = EventBus()

    def teardown_method(self):
        self.bus.shutdown()

    def test_recursive_emit_handled(self):
        received = []

        def handler(e):
            if e.payload.get("depth", 0) < 1:
                self.bus.emit("r", {"depth": e.payload.get("depth", 0) + 1})
            received.append(e.payload.get("depth", 0))

        self.bus.subscribe("r", handler)
        self.bus.emit("r", {"depth": 0})
        assert 0 in received
        assert 1 in received


class TestEventsInit:
    def test_bus_singleton_exported(self):
        assert hasattr(events_pkg, "bus")
        assert isinstance(events_pkg.bus, EventBus)

    def test_all_exports_present(self):
        for name in events_pkg.__all__:
            assert hasattr(events_pkg, name), f"'{name}' ausente em events"

    def test_event_kind_exported(self):
        assert events_pkg.EventKind is EventKind

    def test_subscription_group_exported(self):
        assert events_pkg.SubscriptionGroup is SubscriptionGroup

    def test_pipeline_exported(self):
        from src.events.pipeline import Pipeline

        assert events_pkg.Pipeline is Pipeline

    def test_correlator_exported(self):
        from src.events.correlator import EventCorrelator

        assert events_pkg.EventCorrelator is EventCorrelator
