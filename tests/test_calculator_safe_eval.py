"""Testes do avaliador aritmético seguro da calculadora."""

from __future__ import annotations

import math

import pytest

from src.ui.calculator_panel import CalculatorPanel, _decimal_commas_to_points, _safe_eval

NAMES = dict(CalculatorPanel._SAFE_BUILTINS)


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("2+3*4", 14),
        ("2**10", 1024),
        ("-5%3", 1),
        ("sqrt(16)", 4.0),
        ("max(1,2)", 2),
        ("atan2(1,2)", math.atan2(1, 2)),
        ("pi*2", math.tau),
        ("1e3+1", 1001.0),
        ("5<<2", 20),
    ],
)
def test_valid_expressions(expression, expected):
    assert _safe_eval(expression, NAMES) == pytest.approx(expected)


@pytest.mark.parametrize(
    "expression",
    [
        "().__class__",
        "__import__('os')",
        "[x for x in ()]",
        "sqrt(x=4)",
        "'a'*5",
        "abs.__call__(1)",
        "lambda: 1",
        "(1).real",
    ],
)
def test_code_execution_is_rejected(expression):
    with pytest.raises((ValueError, SyntaxError)):
        _safe_eval(expression, NAMES)


@pytest.mark.parametrize("expression", ["9**9**9", "factorial(10**7)", "1<<99999999", "pow(9, 99999999)"])
def test_resource_exhaustion_is_rejected(expression):
    with pytest.raises(OverflowError):
        _safe_eval(expression, NAMES)


def test_decimal_commas_only_outside_calls():
    assert _decimal_commas_to_points("3,5 + 2,25") == "3.5 + 2.25"
    assert _decimal_commas_to_points("max(1,2)") == "max(1,2)"
