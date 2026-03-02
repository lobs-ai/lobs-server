"""Tests for session cleanup in workflow_nodes and worker_gateway."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── NodeHandlers.delete_session ─────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_session_calls_gateway():
    from app.orchestrator.workflow_nodes import NodeHandlers
    gateway = AsyncMock()
    gateway.delete_session = AsyncMock(return_value=True)
    worker_manager = MagicMock()
    worker_manager.gateway = gateway
    handlers = NodeHandlers(db=None, worker_manager=worker_manager)
    await handlers.delete_session("test-session-key")
    gateway.delete_session.assert_awaited_once_with("test-session-key")


@pytest.mark.asyncio
async def test_delete_session_no_gateway_is_noop():
    from app.orchestrator.workflow_nodes import NodeHandlers
    handlers = NodeHandlers(db=None, worker_manager=None)
    await handlers.delete_session("test-session-key")


@pytest.mark.asyncio
async def test_delete_session_gateway_failure_is_logged_not_raised():
    from app.orchestrator.workflow_nodes import NodeHandlers
    gateway = AsyncMock()
    gateway.delete_session = AsyncMock(side_effect=RuntimeError("ws error"))
    worker_manager = MagicMock()
    worker_manager.gateway = gateway
    handlers = NodeHandlers(db=None, worker_manager=worker_manager)
    await handlers.delete_session("test-session-key")


@pytest.mark.asyncio
async def test_cleanup_node_session_refs():
    from app.orchestrator.workflow_nodes import _exec_cleanup
    gateway = AsyncMock()
    gateway.delete_session = AsyncMock(return_value=True)
    worker_manager = MagicMock()
    worker_manager.gateway = gateway
    run = MagicMock()
    run.session_key = None
    context = {
        "spawn_programmer": {"output": {"childSessionKey": "sess-prog"}},
        "spawn_researcher": {"output": {"childSessionKey": "sess-research"}},
    }
    config = {
        "session_refs": [
            "spawn_programmer.output.childSessionKey",
            "spawn_researcher.output.childSessionKey",
        ]
    }
    result = await _exec_cleanup(config, context, run, db=None, worker_manager=worker_manager)
    assert result.status == "completed"
    assert set(result.output["deleted_sessions"]) == {"sess-prog", "sess-research"}
    assert gateway.delete_session.await_count == 2


@pytest.mark.asyncio
async def test_cleanup_node_missing_ref_is_skipped():
    from app.orchestrator.workflow_nodes import _exec_cleanup
    gateway = AsyncMock()
    gateway.delete_session = AsyncMock(return_value=True)
    worker_manager = MagicMock()
    worker_manager.gateway = gateway
    run = MagicMock()
    run.session_key = None
    context = {}
    config = {"session_refs": ["spawn_programmer.output.childSessionKey"]}
    result = await _exec_cleanup(config, context, run, db=None, worker_manager=worker_manager)
    assert result.status == "completed"
    assert result.output["deleted_sessions"] == []
    gateway.delete_session.assert_not_awaited()


def test_task_router_cleanup_has_session_refs():
    from app.orchestrator.workflow_seeds import DEFAULT_WORKFLOWS as WORKFLOW_SEEDS
    task_router = next((w for w in WORKFLOW_SEEDS if w["name"] == "task-router"), None)
    assert task_router is not None
    done_node = next((n for n in task_router["nodes"] if n["id"] == "done"), None)
    assert done_node is not None
    refs = done_node.get("config", {}).get("session_refs", [])
    assert len(refs) >= 5
    assert any("spawn_programmer" in r for r in refs)
    assert any("spawn_researcher" in r for r in refs)
    assert any("spawn_writer" in r for r in refs)
    assert any("spawn_architect" in r for r in refs)
    assert any("spawn_reviewer" in r for r in refs)


def test_no_sessions_kill_references():
    import os
    app_dir = os.path.join(os.path.dirname(__file__), "..", "app")
    for root, dirs, files in os.walk(app_dir):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            if f.endswith(".py"):
                path = os.path.join(root, f)
                with open(path) as fh:
                    content = fh.read()
                assert "sessions_kill" not in content, f"Stale sessions_kill in {path}"
