"""Tests for new modular workflow nodes: expression, llm_route, enhanced branch."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.orchestrator.workflow_nodes import (
    NodeHandlers,
    NodeResult,
    _NODE_EXECUTORS,
)


class FakeRun:
    def __init__(self, context=None, node_states=None, session_key=None):
        self.id = str(uuid.uuid4())
        self.context = context or {}
        self.node_states = node_states or {}
        self.session_key = session_key


class FakeDB:
    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass

    async def get(self, model, pk):
        return None

    async def execute(self, query):
        return FakeResult(0)


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
# Expression Node Tests
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_expression_node_basic():
    """Test that expression node evaluates expressions and stores results."""
    db = FakeDB()
    wm = FakeWorkerManager()
    handlers = NodeHandlers(db, worker_manager=wm)

    node_def = {
        "id": "eval1",
        "type": "expression",
        "config": {
            "expressions": {
                "sum": "3 + 4",
                "greeting": '"hello"',
                "is_big": "100 > 50",
            },
        },
    }

    run = FakeRun()
    result = await handlers.execute(node_def, run)
    assert result.status == "completed"
    assert result.output["sum"] == 7.0
    assert result.output["greeting"] == "hello"
    assert result.output["is_big"] is True


@pytest.mark.asyncio
async def test_expression_node_with_goto():
    """Test expression node with conditional routing."""
    db = FakeDB()
    wm = FakeWorkerManager()
    handlers = NodeHandlers(db, worker_manager=wm)

    node_def = {
        "id": "eval_route",
        "type": "expression",
        "config": {
            "expressions": {
                "count": "5",
            },
            "goto_if": [
                {"match": "count > 3", "goto": "high_count_node"},
                {"match": "count <= 3", "goto": "low_count_node"},
            ],
        },
    }

    run = FakeRun()
    result = await handlers.execute(node_def, run)
    assert result.status == "completed"
    assert result.output["goto"] == "high_count_node"


@pytest.mark.asyncio
async def test_expression_node_default():
    """Test expression node falls back to default when no goto_if matches."""
    db = FakeDB()
    wm = FakeWorkerManager()
    handlers = NodeHandlers(db, worker_manager=wm)

    node_def = {
        "id": "eval_default",
        "type": "expression",
        "config": {
            "expressions": {"val": "1"},
            "goto_if": [
                {"match": "val > 100", "goto": "never"},
            ],
            "default": "fallback_node",
        },
    }

    run = FakeRun()
    result = await handlers.execute(node_def, run)
    assert result.status == "completed"
    assert result.output["goto"] == "fallback_node"


@pytest.mark.asyncio
async def test_expression_node_with_functions():
    """Test expression node using built-in functions."""
    db = FakeDB()
    w1 = MagicMock()
    w1.agent_type = "programmer"
    wm = FakeWorkerManager({"w1": w1})
    handlers = NodeHandlers(db, worker_manager=wm)

    node_def = {
        "id": "eval_fn",
        "type": "expression",
        "config": {
            "expressions": {
                "prog_status": 'agentStatus("programmer")',
                "writer_status": 'agentStatus("writer")',
            },
        },
    }

    run = FakeRun()
    result = await handlers.execute(node_def, run)
    assert result.status == "completed"
    assert result.output["prog_status"] == "busy"
    assert result.output["writer_status"] == "idle"


# ══════════════════════════════════════════════════════════════════════
# Enhanced Branch Node Tests
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_branch_with_function_calls():
    """Test that branch node supports function-based conditions."""
    db = FakeDB()
    w1 = MagicMock()
    w1.agent_type = "programmer"
    wm = FakeWorkerManager({"w1": w1})
    handlers = NodeHandlers(db, worker_manager=wm)

    node_def = {
        "id": "branch1",
        "type": "branch",
        "config": {
            "conditions": [
                {"match": 'agentStatus("programmer") == "busy"', "goto": "wait_node"},
                {"match": 'agentStatus("programmer") == "idle"', "goto": "spawn_node"},
            ],
            "default": "error_node",
        },
    }

    run = FakeRun()
    result = await handlers.execute(node_def, run)
    assert result.status == "completed"
    assert result.output["goto"] == "wait_node"


@pytest.mark.asyncio
async def test_branch_legacy_simple_conditions():
    """Test that legacy simple conditions still work."""
    db = FakeDB()
    handlers = NodeHandlers(db)

    node_def = {
        "id": "branch2",
        "type": "branch",
        "config": {
            "conditions": [
                {"match": "task.agent == programmer", "goto": "prog_node"},
            ],
            "default": "default_node",
        },
    }

    run = FakeRun(context={"task": {"agent": "programmer"}})
    result = await handlers.execute(node_def, run)
    assert result.status == "completed"
    assert result.output["goto"] == "prog_node"


# ══════════════════════════════════════════════════════════════════════
# LLM Route Node Tests
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_llm_route_no_candidates():
    """Test llm_route fails gracefully with no candidates."""
    db = FakeDB()
    handlers = NodeHandlers(db)

    node_def = {
        "id": "llm1",
        "type": "llm_route",
        "config": {
            "prompt_template": "Choose a path",
            "candidates": [],
        },
    }

    run = FakeRun()
    result = await handlers.execute(node_def, run)
    assert result.status == "failed"
    assert "No candidates" in result.error


@pytest.mark.asyncio
async def test_llm_route_with_mock():
    """Test llm_route with mocked Gateway response."""
    db = FakeDB()
    handlers = NodeHandlers(db)

    node_def = {
        "id": "llm2",
        "type": "llm_route",
        "config": {
            "prompt_template": "Task is about documentation",
            "candidates": [
                {"id": "run_tests", "description": "Run test suite"},
                {"id": "skip_tests", "description": "Skip tests for docs-only changes"},
            ],
            "model": "haiku",
        },
    }

    # Mock aiohttp — the llm_route node uses `async with ClientSession() as session: resp = await session.post(...)`
    mock_resp = MagicMock()
    mock_resp.json = AsyncMock(return_value={
        "ok": True,
        "content": "skip_tests",
    })

    mock_session = MagicMock()
    mock_session.post = AsyncMock(return_value=mock_resp)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("app.orchestrator.workflow_nodes.aiohttp.ClientSession", return_value=mock_session):
        run = FakeRun(context={"task": {"title": "Update README"}})
        result = await handlers.execute(node_def, run)

    assert result.status == "completed"
    assert result.output["goto"] == "skip_tests"


# ══════════════════════════════════════════════════════════════════════
# Node Registration
# ══════════════════════════════════════════════════════════════════════

def test_new_nodes_registered():
    """Verify new node types are registered."""
    assert "expression" in _NODE_EXECUTORS
    assert "llm_route" in _NODE_EXECUTORS
    assert "branch" in _NODE_EXECUTORS
