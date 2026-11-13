"""Testes de validação de entrada da colaboração em tempo real."""

from __future__ import annotations

import pytest

from src.ui.collab_panel import TextOp, _apply_op


@pytest.mark.parametrize(
    "payload",
    [
        {"op": "insert", "pos": -1, "text": "x"},
        {"op": "delete", "pos": 0, "len": -5},
        {"op": "explode", "pos": 0},
        {"op": "insert", "pos": "0", "text": "x"},
        {"op": "insert", "pos": True, "text": "x"},
        {"op": "insert", "pos": 0, "text": 5},
        {"op": "insert", "pos": 0, "text": "x" * 1_000_001},
        "insert",
        None,
    ],
)
def test_malformed_ops_are_rejected(payload):
    with pytest.raises(ValueError):
        TextOp.from_dict(payload)


def test_valid_ops_round_trip():
    op = TextOp.from_dict({"op": "insert", "pos": 2, "text": "ab"})
    assert _apply_op("0123", op) == "01ab23"
    op = TextOp.from_dict({"op": "delete", "pos": 1, "len": 2})
    assert _apply_op("0123", op) == "03"


def test_negative_length_can_no_longer_duplicate_text():
    with pytest.raises(ValueError):
        TextOp.from_dict({"op": "delete", "pos": 3, "len": -3})
