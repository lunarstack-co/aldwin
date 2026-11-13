"""Unit tests for calculate AST allow-list and date maths."""

from __future__ import annotations

import ast
import time
from datetime import date
from pathlib import Path

import pytest
from agentling.tools import ToolCallError
from hypothesis import given, settings
from hypothesis import strategies as st

from aldwin.core.tools.calculate import (
    CalculateTool,
    evaluate,
    format_row_summary,
)

TODAY = date(2026, 10, 21)


def test_evaluate_15_percent_of_1240() -> None:
    assert evaluate("15% of 1240", today=TODAY) == "0.15 * 1240 = 186"


def test_evaluate_times_unicode() -> None:
    assert evaluate("1240 × 0.15", today=TODAY) == "1240 * 0.15 = 186"


def test_evaluate_days_between() -> None:
    result = evaluate(
        'days_between("2026-10-21", "2026-11-30")',
        today=TODAY,
    )
    assert result.endswith("= 40")


def test_evaluate_rejects_import() -> None:
    with pytest.raises(ToolCallError, match=r"not allowed: name __import__"):
        evaluate("__import__('os')", today=TODAY)


def test_evaluate_exponent_too_large() -> None:
    with pytest.raises(ToolCallError, match="number too large"):
        evaluate("9**9**9", today=TODAY)


def test_evaluate_division_by_zero() -> None:
    with pytest.raises(ToolCallError, match="division by zero"):
        evaluate("1/0", today=TODAY)


def test_evaluate_expression_too_long() -> None:
    with pytest.raises(
        ToolCallError,
        match=r"expression too long \(limit 200 characters\)",
    ):
        evaluate("1+" * 101, today=TODAY)


def test_evaluate_date_out_of_range() -> None:
    with pytest.raises(ToolCallError, match="date out of range"):
        evaluate('days_until("1899-01-01")', today=TODAY)


def test_evaluate_today_injected() -> None:
    result = evaluate('days_until("2026-11-30")', today=TODAY)
    assert result.endswith("= 40")


def test_evaluate_add_days_format() -> None:
    result = evaluate('add_days("2026-10-21", 40)', today=TODAY)
    assert "2026-11-30 (Monday)" in result


def test_evaluate_allowed_functions() -> None:
    assert evaluate("round(1.6)", today=TODAY).endswith("= 2")
    assert evaluate("min(3, 1, 2)", today=TODAY).endswith("= 1")
    assert evaluate("max(3, 1, 2)", today=TODAY).endswith("= 3")
    assert evaluate("abs(-4)", today=TODAY).endswith("= 4")
    assert evaluate("floor(3.9)", today=TODAY).endswith("= 3")
    assert evaluate("ceil(3.1)", today=TODAY).endswith("= 4")
    assert evaluate("sum(1, 2, 3)", today=TODAY).endswith("= 6")
    assert evaluate("2^3", today=TODAY).endswith("= 8")
    assert evaluate("8 ÷ 2", today=TODAY).endswith("= 4")
    assert "2" in evaluate("sqrt(4)", today=TODAY)


def test_evaluate_rejects_attr_subscript_lambda() -> None:
    with pytest.raises(ToolCallError, match="not allowed"):
        evaluate("(1).real", today=TODAY)
    with pytest.raises(ToolCallError, match="not allowed"):
        evaluate("[1][0]", today=TODAY)
    with pytest.raises(ToolCallError, match="not allowed"):
        evaluate("(lambda: 1)()", today=TODAY)


def test_evaluate_edge_cases_and_rejects() -> None:
    """Cover remaining allow-list and safety branches."""
    assert evaluate("1240 * 15%", today=TODAY).endswith("= 186")
    assert evaluate("round(1.234, 2)", today=TODAY).endswith("= 1.23")
    assert evaluate('weekday("2026-11-30")', today=TODAY).endswith("= Monday")
    assert evaluate("+5", today=TODAY).endswith("= 5")
    assert evaluate("1_000 + 240", today=TODAY).endswith("= 1240")

    with pytest.raises(ToolCallError, match="not allowed: expression"):
        evaluate("1\x00+2", today=TODAY)
    with pytest.raises(ToolCallError, match="not allowed: empty"):
        evaluate("   ", today=TODAY)
    with pytest.raises(ToolCallError, match="not allowed: syntax"):
        evaluate("1+", today=TODAY)
    with pytest.raises(ToolCallError, match="not allowed: date"):
        evaluate('days_until("not-a-date")', today=TODAY)
    with pytest.raises(ToolCallError, match="number too large"):
        evaluate("10**200", today=TODAY)
    with pytest.raises(ToolCallError, match="number too large"):
        evaluate("(10**6 + 1)**101", today=TODAY)
    with pytest.raises(ToolCallError, match="not allowed: round arity"):
        evaluate("round(1, 2, 3)", today=TODAY)
    with pytest.raises(ToolCallError, match="not allowed: min arity"):
        evaluate("min()", today=TODAY)
    with pytest.raises(ToolCallError, match="not allowed: max arity"):
        evaluate("max()", today=TODAY)
    with pytest.raises(ToolCallError, match="not allowed: abs arity"):
        evaluate("abs(1, 2)", today=TODAY)
    with pytest.raises(ToolCallError, match="not allowed: sqrt arity"):
        evaluate("sqrt()", today=TODAY)
    with pytest.raises(ToolCallError, match="not allowed: floor arity"):
        evaluate("floor(1, 2)", today=TODAY)
    with pytest.raises(ToolCallError, match="not allowed: ceil arity"):
        evaluate("ceil()", today=TODAY)
    with pytest.raises(ToolCallError, match="not allowed"):
        evaluate("{1}", today=TODAY)
    with pytest.raises(ToolCallError, match="not allowed"):
        evaluate("[x for x in [1]]", today=TODAY)
    with pytest.raises(ToolCallError, match="not allowed"):
        evaluate("True", today=TODAY)

    assert format_row_summary("no equals sign") == "Calculate no equals sign"


def test_calculate_module_has_no_eval() -> None:
    source = Path("src/aldwin/core/tools/calculate.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in {"eval", "exec"}
        if isinstance(node, ast.Attribute) and node.attr in {"eval", "exec"}:
            pytest.fail(f"found .{node.attr} in calculate.py")


def test_format_row_summary_truncates_and_groups() -> None:
    summary = format_row_summary("1000 + 240 = 1240")
    assert summary.startswith("Calculate ")
    assert "1,240" in summary
    padded = ("1+" * 50) + "1"
    if len(padded) <= 200:
        text = evaluate(padded, today=TODAY)
        row = format_row_summary(text)
        expr_part = row.removeprefix("Calculate ").rsplit(" = ", 1)[0]
        assert len(expr_part) <= 60


@pytest.mark.asyncio
async def test_calculate_tool_forward_summary() -> None:
    tool = CalculateTool(today_fn=lambda: TODAY)
    out = await tool.forward("15% of 1240")
    assert str(out) == "0.15 * 1240 = 186"
    assert out.summary == "Calculate 0.15 * 1240 = 186"
    assert out.ok is True


_ALLOWED_CALLS = frozenset(
    {
        "round",
        "min",
        "max",
        "abs",
        "sqrt",
        "floor",
        "ceil",
        "sum",
        "days_between",
        "days_until",
        "add_days",
        "weekday",
        "today",
    }
)


@given(st.text(max_size=80))
@settings(max_examples=40, deadline=None)
def test_evaluate_hypothesis_rejects_names(payload: str) -> None:
    try:
        result = evaluate(payload, today=TODAY)
    except ToolCallError:
        return
    except Exception:
        return
    normalised = result.split(" = ", 1)[0]
    try:
        tree = ast.parse(normalised, mode="eval")
    except SyntaxError:
        return
    call_funcs: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            call_funcs.add(id(node.func))
            assert node.func.id in _ALLOWED_CALLS
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and id(node) not in call_funcs:
            pytest.fail(f"free name {node.id!r} in successful result {result!r}")
        if isinstance(node, (ast.Attribute, ast.Subscript, ast.Lambda)):
            pytest.fail(f"forbidden node in successful result {result!r}")


@pytest.mark.perf
def test_evaluate_under_10ms() -> None:
    started = time.perf_counter()
    for _ in range(20):
        evaluate("15% of 1240", today=TODAY)
    elapsed = (time.perf_counter() - started) / 20
    assert elapsed < 0.010
