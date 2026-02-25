"""Tests for workflow_functions — expression engine and built-in functions."""

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.orchestrator.workflow_functions import (
    evaluate_expression,
    evaluate_condition_async,
    ExpressionError,
    register_function,
    get_registered_functions,
    _FUNCTION_REGISTRY,
)


# ══════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════

class FakeDB:
    """Minimal async DB mock."""
    def __init__(self):
        self._results = {}

    async def execute(self, query):
        return FakeResult(0)

    async def get(self, model, pk):
        return None


class FakeResult:
    def __init__(self, val):
        self._val = val

    def scalar(self):
        return self._val

    def scalar_one_or_none(self):
        return self._val

    def scalars(self):
        return FakeScalars([])


class FakeScalars:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class FakeWorkerManager:
    def __init__(self, workers=None):
        self.active_workers = workers or {}


# ══════════════════════════════════════════════════════════════════════
# Expression Engine Tests
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_literal_expressions():
    db = FakeDB()
    assert await evaluate_expression("42", {}, db) == 42
    assert await evaluate_expression('"hello"', {}, db) == "hello"
    assert await evaluate_expression("True", {}, db) is True
    assert await evaluate_expression("[1, 2, 3]", {}, db) == [1, 2, 3]


@pytest.mark.asyncio
async def test_comparison_expressions():
    db = FakeDB()
    assert await evaluate_expression("5 > 3", {}, db) is True
    assert await evaluate_expression("5 < 3", {}, db) is False
    assert await evaluate_expression("5 == 5", {}, db) is True
    assert await evaluate_expression("5 != 3", {}, db) is True
    assert await evaluate_expression("5 >= 5", {}, db) is True
    assert await evaluate_expression("5 <= 4", {}, db) is False


@pytest.mark.asyncio
async def test_boolean_expressions():
    db = FakeDB()
    assert await evaluate_expression("True and True", {}, db) is True
    assert await evaluate_expression("True and False", {}, db) is False
    assert await evaluate_expression("True or False", {}, db) is True
    assert await evaluate_expression("not False", {}, db) is True


@pytest.mark.asyncio
async def test_context_access():
    db = FakeDB()
    ctx = {"task": {"status": "pending", "priority": "high"}, "count": 5}
    assert await evaluate_expression("task", ctx, db) == {"status": "pending", "priority": "high"}
    assert await evaluate_expression("count", ctx, db) == 5
    assert await evaluate_expression("count > 3", ctx, db) is True


@pytest.mark.asyncio
async def test_attribute_access():
    db = FakeDB()
    ctx = {"task": {"status": "pending", "priority": "high"}}
    assert await evaluate_expression('task["status"]', ctx, db) == "pending"


@pytest.mark.asyncio
async def test_arithmetic():
    db = FakeDB()
    assert await evaluate_expression("3 + 4", {}, db) == 7.0
    assert await evaluate_expression("10 - 3", {}, db) == 7.0
    assert await evaluate_expression("3 * 4", {}, db) == 12.0
    assert await evaluate_expression("10 / 2", {}, db) == 5.0


@pytest.mark.asyncio
async def test_in_operator():
    db = FakeDB()
    assert await evaluate_expression('"a" in ["a", "b", "c"]', {}, db) is True
    assert await evaluate_expression('"d" in ["a", "b", "c"]', {}, db) is False
    assert await evaluate_expression('"d" not in ["a", "b", "c"]', {}, db) is True


@pytest.mark.asyncio
async def test_ternary():
    db = FakeDB()
    assert await evaluate_expression('"yes" if True else "no"', {}, db) == "yes"
    assert await evaluate_expression('"yes" if False else "no"', {}, db) == "no"


@pytest.mark.asyncio
async def test_string_concat():
    db = FakeDB()
    assert await evaluate_expression('"hello" + " " + "world"', {}, db) == "hello world"


@pytest.mark.asyncio
async def test_empty_expression_raises():
    db = FakeDB()
    with pytest.raises(ExpressionError):
        await evaluate_expression("", {}, db)


@pytest.mark.asyncio
async def test_syntax_error_raises():
    db = FakeDB()
    with pytest.raises(ExpressionError):
        await evaluate_expression("if while for", {}, db)


@pytest.mark.asyncio
async def test_unknown_function_raises():
    db = FakeDB()
    with pytest.raises(ExpressionError, match="Unknown function"):
        await evaluate_expression("nonexistent_func()", {}, db)


# ══════════════════════════════════════════════════════════════════════
# Built-in Function Tests
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_ctx_function():
    db = FakeDB()
    ctx = {"task": {"status": "pending", "agent": "programmer"}}
    result = await evaluate_expression('ctx("task.status")', ctx, db)
    assert result == "pending"

    result = await evaluate_expression('ctx("task.agent")', ctx, db)
    assert result == "programmer"

    result = await evaluate_expression('ctx("nonexistent")', ctx, db)
    assert result is None


@pytest.mark.asyncio
async def test_taskfield_function():
    db = FakeDB()
    ctx = {"task": {"status": "pending", "priority": "high"}}
    result = await evaluate_expression('taskField("status")', ctx, db)
    assert result == "pending"

    result = await evaluate_expression('taskField("priority")', ctx, db)
    assert result == "high"


@pytest.mark.asyncio
async def test_agent_status_function():
    db = FakeDB()

    # Busy worker
    worker = MagicMock()
    worker.agent_type = "programmer"
    wm = FakeWorkerManager({"w1": worker})
    result = await evaluate_expression('agentStatus("programmer")', {}, db, wm)
    assert result == "busy"

    # Idle
    result = await evaluate_expression('agentStatus("researcher")', {}, db, wm)
    assert result == "idle"


@pytest.mark.asyncio
async def test_active_workers_function():
    db = FakeDB()

    w1 = MagicMock()
    w1.agent_type = "programmer"
    w2 = MagicMock()
    w2.agent_type = "writer"
    wm = FakeWorkerManager({"w1": w1, "w2": w2})

    result = await evaluate_expression("activeWorkers()", {}, db, wm)
    assert result == 2

    result = await evaluate_expression('activeWorkers("programmer")', {}, db, wm)
    assert result == 1


@pytest.mark.asyncio
async def test_len_function():
    db = FakeDB()
    assert await evaluate_expression("len([1, 2, 3])", {}, db) == 3
    assert await evaluate_expression('len("hello")', {}, db) == 5
    assert await evaluate_expression("len([])", {}, db) == 0


@pytest.mark.asyncio
async def test_coalesce_function():
    db = FakeDB()
    assert await evaluate_expression('coalesce(None, "default")', {}, db) == "default"
    assert await evaluate_expression('coalesce("first", "second")', {}, db) == "first"


@pytest.mark.asyncio
async def test_contains_function():
    db = FakeDB()
    assert await evaluate_expression('contains("hello world", "hello")', {}, db) is True
    assert await evaluate_expression('contains("hello world", "xyz")', {}, db) is False
    assert await evaluate_expression("contains([1, 2, 3], 2)", {}, db) is True


@pytest.mark.asyncio
async def test_now_function():
    db = FakeDB()
    result = await evaluate_expression("now()", {}, db)
    assert isinstance(result, str)
    assert "T" in result  # ISO format


@pytest.mark.asyncio
async def test_hour_function():
    db = FakeDB()
    result = await evaluate_expression("hour()", {}, db)
    assert isinstance(result, int)
    assert 0 <= result <= 23


@pytest.mark.asyncio
async def test_day_of_week_function():
    db = FakeDB()
    result = await evaluate_expression("dayOfWeek()", {}, db)
    assert isinstance(result, int)
    assert 0 <= result <= 6


@pytest.mark.asyncio
async def test_evaluate_condition_async():
    db = FakeDB()
    assert await evaluate_condition_async("5 > 3", {}, db) is True
    assert await evaluate_condition_async("5 < 3", {}, db) is False
    assert await evaluate_condition_async("invalid syntax }{", {}, db) is False  # Fails gracefully


# ══════════════════════════════════════════════════════════════════════
# Compound Expressions (the real power)
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_compound_function_and_comparison():
    db = FakeDB()
    ctx = {"task": {"status": "pending"}}

    # Function call with comparison
    result = await evaluate_expression('taskField("status") == "pending"', ctx, db)
    assert result is True

    result = await evaluate_expression('taskField("status") != "completed"', ctx, db)
    assert result is True


@pytest.mark.asyncio
async def test_compound_boolean_with_functions():
    db = FakeDB()

    w1 = MagicMock()
    w1.agent_type = "programmer"
    wm = FakeWorkerManager({"w1": w1})

    # Combine multiple function calls with boolean logic
    result = await evaluate_expression(
        'agentStatus("programmer") == "busy" and agentStatus("writer") == "idle"',
        {}, db, wm,
    )
    assert result is True


@pytest.mark.asyncio
async def test_function_registry():
    """Verify that built-in functions are registered."""
    fns = get_registered_functions()
    assert "numTasks" in fns
    assert "agentStatus" in fns
    assert "ctx" in fns
    assert "taskField" in fns
    assert "len" in fns
    assert "coalesce" in fns
    assert "contains" in fns
    assert "now" in fns
    assert "hour" in fns
    assert "dayOfWeek" in fns
    assert "workerCapacity" in fns
    assert "activeWorkers" in fns
    assert "hasUnread" in fns
    assert "numUnread" in fns


@pytest.mark.asyncio
async def test_custom_function_registration():
    """Test that custom functions can be registered and used."""
    @register_function("_test_double")
    async def fn_test_double(args, context, db, worker_manager, **kw):
        return (args[0] if args else 0) * 2

    db = FakeDB()
    result = await evaluate_expression("_test_double(21)", {}, db)
    assert result == 42

    # Clean up
    del _FUNCTION_REGISTRY["_test_double"]
