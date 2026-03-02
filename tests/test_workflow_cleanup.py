"""Tests for session cleanup node — context path resolution and session deletion."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def test_resolve_context_path_direct():
    """Session key is stored at ctx[node_id], not ctx[node_id]['output']."""
    from app.orchestrator.workflow_nodes import _resolve_context_path

    context = {
        "spawn_programmer": {
            "runId": "worker-123",
            "childSessionKey": "agent:programmer:subagent:abc123",
        }
    }

    # Correct path — no .output. intermediate
    result = _resolve_context_path(context, "spawn_programmer.childSessionKey")
    assert result == "agent:programmer:subagent:abc123"

    # Wrong path — with .output. intermediate — should return None
    wrong = _resolve_context_path(context, "spawn_programmer.output.childSessionKey")
    assert wrong is None


def test_resolve_context_path_missing_node():
    from app.orchestrator.workflow_nodes import _resolve_context_path
    context = {}
    result = _resolve_context_path(context, "spawn_programmer.childSessionKey")
    assert result is None


def test_resolve_context_path_empty_key():
    from app.orchestrator.workflow_nodes import _resolve_context_path
    context = {"spawn_programmer": {"runId": "w1", "childSessionKey": ""}}
    result = _resolve_context_path(context, "spawn_programmer.childSessionKey")
    assert result == ""


@pytest.mark.asyncio
async def test_cleanup_node_calls_delete_session():
    from app.orchestrator.workflow_nodes import _exec_cleanup, NodeHandlers

    context = {
        "spawn_programmer": {"runId": "w1", "childSessionKey": "agent:programmer:subagent:abc123"},
        "spawn_researcher": {"runId": "w2", "childSessionKey": "agent:researcher:subagent:def456"},
    }
    config = {
        "session_refs": [
            "spawn_programmer.childSessionKey",
            "spawn_researcher.childSessionKey",
            "spawn_missing.childSessionKey",
        ]
    }
    run = MagicMock()
    run.context = context
    run.session_key = None

    deleted = []

    async def mock_delete(self, key):
        deleted.append(key)

    with patch.object(NodeHandlers, "delete_session", mock_delete):
        result = await _exec_cleanup(config, context, run, db=None, worker_manager=None)

    assert result.status == "completed"
    assert "agent:programmer:subagent:abc123" in deleted
    assert "agent:researcher:subagent:def456" in deleted
    assert len(deleted) == 2


@pytest.mark.asyncio
async def test_cleanup_node_skips_empty_session_key():
    from app.orchestrator.workflow_nodes import _exec_cleanup, NodeHandlers

    context = {"spawn_programmer": {"runId": "w1", "childSessionKey": ""}}
    config = {"session_refs": ["spawn_programmer.childSessionKey"]}
    run = MagicMock()
    run.context = context
    run.session_key = None

    deleted = []

    async def mock_delete(self, key):
        deleted.append(key)

    with patch.object(NodeHandlers, "delete_session", mock_delete):
        result = await _exec_cleanup(config, context, run, db=None, worker_manager=None)

    assert result.status == "completed"
    assert len(deleted) == 0


def test_workflow_seeds_session_refs_correct_paths():
    import re
    with open("app/orchestrator/workflow_seeds.py") as f:
        content = f.read()

    bad = re.findall(r'["\']([^"\']+\.output\.childSessionKey)["\']', content)
    assert not bad, f"Found wrong .output. paths: {bad}"

    good = re.findall(r'["\'](\w+\.childSessionKey)["\']', content)
    assert good, "No correct session_ref paths found"
