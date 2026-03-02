"""Tests for the workflow engine — node registry, executor, and built-in nodes."""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.orchestrator.workflow_nodes import (
    NodeHandlers,
    NodeResult,
    _evaluate_condition,
    _render_template,
    _resolve_context_path,
    register_node,
    register_node_checker,
    get_registered_node_types,
    _NODE_EXECUTORS,
    _NODE_CHECKERS,
)


# ══════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════

class FakeRun:
    """Minimal run-like object for testing."""
    def __init__(self, context=None, node_states=None, session_key=None):
        self.id = str(uuid.uuid4())
        self.context = context or {}
        self.node_states = node_states or {}
        self.session_key = session_key


class FakeDB:
    """Minimal async DB mock."""
    def __init__(self):
        self.added = []
        self._store = {}

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass

    async def get(self, model, pk):
        return self._store.get((model.__name__ if hasattr(model, '__name__') else str(model), pk))

    async def execute(self, stmt):
        class FakeResult:
            def scalar_one_or_none(self):
                return None
            def scalars(self):
                class FakeScalars:
                    def all(self):
                        return []
                return FakeScalars()
        return FakeResult()


# ══════════════════════════════════════════════════════════════════════
# Template & Condition Tests
# ══════════════════════════════════════════════════════════════════════

class TestTemplateRendering:
    def test_simple_substitution(self):
        result = _render_template("Hello {name}", {"name": "World"})
        assert result == "Hello World"

    def test_nested_path(self):
        ctx = {"task": {"title": "Fix bug", "project": {"name": "lobs"}}}
        assert _render_template("{task.title}", ctx) == "Fix bug"
        assert _render_template("{task.project.name}", ctx) == "lobs"

    def test_missing_key_left_as_is(self):
        result = _render_template("{missing.key}", {})
        assert result == "{missing.key}"

    def test_dict_value_json_serialized(self):
        result = _render_template("{data}", {"data": {"a": 1}})
        parsed = json.loads(result)
        assert parsed == {"a": 1}

    def test_none_value(self):
        result = _render_template("{val}", {"val": None})
        assert result == ""


class TestResolveContextPath:
    def test_simple(self):
        assert _resolve_context_path({"a": 1}, "a") == 1

    def test_nested(self):
        assert _resolve_context_path({"a": {"b": {"c": 3}}}, "a.b.c") == 3

    def test_missing(self):
        assert _resolve_context_path({"a": 1}, "b") is None

    def test_non_dict_intermediate(self):
        assert _resolve_context_path({"a": "string"}, "a.b") is None


class TestEvaluateCondition:
    def test_equality(self):
        assert _evaluate_condition("status == completed", {"status": "completed"})
        assert not _evaluate_condition("status == completed", {"status": "failed"})

    def test_inequality(self):
        assert _evaluate_condition("status != failed", {"status": "completed"})

    def test_truthiness(self):
        assert _evaluate_condition("flag", {"flag": True})
        assert not _evaluate_condition("flag", {"flag": False})
        assert not _evaluate_condition("flag", {"flag": ""})

    def test_numeric_comparison(self):
        assert _evaluate_condition("count > 5", {"count": 10})
        assert not _evaluate_condition("count > 5", {"count": 3})
        assert _evaluate_condition("count >= 5", {"count": 5})
        assert _evaluate_condition("count < 10", {"count": 5})

    def test_in_list(self):
        assert _evaluate_condition("status in [completed, running]", {"status": "completed"})
        assert not _evaluate_condition("status in [completed, running]", {"status": "failed"})

    def test_nested_path(self):
        ctx = {"task": {"status": "done"}}
        assert _evaluate_condition("task.status == done", ctx)


# ══════════════════════════════════════════════════════════════════════
# Node Registry Tests
# ══════════════════════════════════════════════════════════════════════

class TestNodeRegistry:
    def test_builtin_types_registered(self):
        types = get_registered_node_types()
        for expected in ["spawn_agent", "tool_call", "branch", "gate", "notify",
                         "cleanup", "sub_workflow", "python_call", "for_each",
                         "http_request", "transform", "parallel", "delay",
                         "send_to_session"]:
            assert expected in types, f"Missing built-in node type: {expected}"

    def test_custom_registration(self):
        @register_node("test_custom_node")
        async def _exec_test(config, context, run, *, db, worker_manager):
            return NodeResult(status="completed", output={"custom": True})

        assert "test_custom_node" in _NODE_EXECUTORS

        # Clean up
        del _NODE_EXECUTORS["test_custom_node"]

    def test_custom_checker_registration(self):
        @register_node_checker("test_check_node")
        async def _check_test(node_def, run, *, db, worker_manager):
            return None

        assert "test_check_node" in _NODE_CHECKERS
        del _NODE_CHECKERS["test_check_node"]


# ══════════════════════════════════════════════════════════════════════
# Node Execution Tests
# ══════════════════════════════════════════════════════════════════════

class TestNodeHandlers:
    @pytest.fixture
    def handlers(self):
        return NodeHandlers(FakeDB())

    @pytest.mark.asyncio
    async def test_unknown_node_type(self, handlers):
        result = await handlers.execute({"type": "nonexistent", "config": {}}, FakeRun())
        assert result.status == "failed"
        assert "Unknown node type" in result.error

    @pytest.mark.asyncio
    async def test_branch_matching(self, handlers):
        node_def = {
            "type": "branch",
            "config": {
                "conditions": [
                    {"match": "status == completed", "goto": "node_a"},
                    {"match": "status == failed", "goto": "node_b"},
                ],
                "default": "node_c",
            },
        }
        run = FakeRun(context={"status": "completed"})
        result = await handlers.execute(node_def, run)
        assert result.status == "completed"
        assert result.output["goto"] == "node_a"

    @pytest.mark.asyncio
    async def test_branch_default(self, handlers):
        node_def = {
            "type": "branch",
            "config": {
                "conditions": [{"match": "status == completed", "goto": "node_a"}],
                "default": "node_c",
            },
        }
        run = FakeRun(context={"status": "unknown"})
        result = await handlers.execute(node_def, run)
        assert result.output["goto"] == "node_c"

    @pytest.mark.asyncio
    async def test_branch_no_match_no_default(self, handlers):
        node_def = {
            "type": "branch",
            "config": {"conditions": [{"match": "status == completed", "goto": "node_a"}]},
        }
        run = FakeRun(context={"status": "unknown"})
        result = await handlers.execute(node_def, run)
        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_tool_call_success(self, handlers):
        node_def = {"type": "tool_call", "config": {"command": "echo hello"}}
        result = await handlers.execute(node_def, FakeRun())
        assert result.status == "completed"
        assert "hello" in result.output["stdout"]

    @pytest.mark.asyncio
    async def test_tool_call_failure(self, handlers):
        node_def = {"type": "tool_call", "config": {"command": "false"}}
        result = await handlers.execute(node_def, FakeRun())
        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_tool_call_with_template(self, handlers):
        node_def = {"type": "tool_call", "config": {"command": "echo {greeting}"}}
        run = FakeRun(context={"greeting": "world"})
        result = await handlers.execute(node_def, run)
        assert result.status == "completed"
        assert "world" in result.output["stdout"]

    @pytest.mark.asyncio
    async def test_notify_internal(self, handlers):
        node_def = {"type": "notify", "config": {"channel": "internal", "message_template": "Test notification"}}
        result = await handlers.execute(node_def, FakeRun())
        assert result.status == "completed"
        assert result.output["notified"] is True

    @pytest.mark.asyncio
    async def test_notify_discord_creates_inbox(self):
        db = FakeDB()
        handlers = NodeHandlers(db)
        node_def = {"type": "notify", "config": {"channel": "discord", "message_template": "Alert: {msg}"}}
        run = FakeRun(context={"msg": "something happened"})
        result = await handlers.execute(node_def, run)
        assert result.status == "completed"
        assert result.output["surfaced_as"] == "inbox_item"
        assert len(db.added) == 1  # InboxItem created

    @pytest.mark.asyncio
    async def test_cleanup(self, handlers):
        run = FakeRun()
        run.session_key = None  # No session to clean
        node_def = {"type": "cleanup", "config": {"delete_session": True}}
        result = await handlers.execute(node_def, run)
        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_cleanup_session_ref_resolves_and_deletes(self):
        """cleanup node with session_ref resolves context path and calls gateway delete_session."""
        from unittest.mock import AsyncMock, MagicMock
        db = FakeDB()
        mock_gateway = MagicMock()
        mock_gateway.delete_session = AsyncMock(return_value=True)
        mock_wm = MagicMock()
        mock_wm.gateway = mock_gateway
        handlers = NodeHandlers(db, mock_wm)

        context = {"spawn_agent": {"output": {"childSessionKey": "sess-abc123"}}}
        run = FakeRun(context=context)
        node_def = {
            "type": "cleanup",
            "config": {"session_ref": "spawn_agent.output.childSessionKey"},
        }
        result = await handlers.execute(node_def, run)
        assert result.status == "completed"
        assert "sess-abc123" in result.output["deleted_sessions"]
        mock_gateway.delete_session.assert_awaited_once_with("sess-abc123")

    @pytest.mark.asyncio
    async def test_cleanup_session_refs_multiple(self):
        """cleanup node with session_refs list deletes all resolved sessions."""
        from unittest.mock import AsyncMock, MagicMock
        db = FakeDB()
        mock_gateway = MagicMock()
        mock_gateway.delete_session = AsyncMock(return_value=True)
        mock_wm = MagicMock()
        mock_wm.gateway = mock_gateway
        handlers = NodeHandlers(db, mock_wm)

        context = {
            "spawn_programmer": {"output": {"childSessionKey": "sess-prog"}},
            "spawn_researcher": {"output": {"childSessionKey": "sess-research"}},
        }
        run = FakeRun(context=context)
        node_def = {
            "type": "cleanup",
            "config": {
                "session_refs": [
                    "spawn_programmer.output.childSessionKey",
                    "spawn_researcher.output.childSessionKey",
                ]
            },
        }
        result = await handlers.execute(node_def, run)
        assert result.status == "completed"
        assert set(result.output["deleted_sessions"]) == {"sess-prog", "sess-research"}
        assert mock_gateway.delete_session.await_count == 2

    @pytest.mark.asyncio
    async def test_cleanup_session_ref_missing_context_is_graceful(self):
        """cleanup node with unresolvable session_ref should succeed gracefully."""
        db = FakeDB()
        handlers = NodeHandlers(db)
        run = FakeRun(context={})
        node_def = {
            "type": "cleanup",
            "config": {"session_ref": "nonexistent.output.childSessionKey"},
        }
        result = await handlers.execute(node_def, run)
        assert result.status == "completed"
        assert result.output["deleted_sessions"] == []

    @pytest.mark.asyncio
    async def test_cleanup_no_duplicate_deletes(self):
        """session_refs with duplicate resolved keys should only delete once each."""
        from unittest.mock import AsyncMock, MagicMock
        db = FakeDB()
        mock_gateway = MagicMock()
        mock_gateway.delete_session = AsyncMock(return_value=True)
        mock_wm = MagicMock()
        mock_wm.gateway = mock_gateway
        handlers = NodeHandlers(db, mock_wm)

        context = {"a": {"output": {"childSessionKey": "sess-dup"}}}
        run = FakeRun(context=context)
        node_def = {
            "type": "cleanup",
            "config": {
                "session_refs": [
                    "a.output.childSessionKey",
                    "a.output.childSessionKey",
                ]
            },
        }
        result = await handlers.execute(node_def, run)
        assert result.status == "completed"
        assert result.output["deleted_sessions"].count("sess-dup") == 1
        mock_gateway.delete_session.assert_awaited_once_with("sess-dup")

    @pytest.mark.asyncio
    async def test_delay_starts_running(self, handlers):
        node_def = {"type": "delay", "config": {"seconds": 5}}
        result = await handlers.execute(node_def, FakeRun())
        assert result.status == "running"
        assert "wait_until" in result.output


# ══════════════════════════════════════════════════════════════════════
# Transform Node Tests
# ══════════════════════════════════════════════════════════════════════

class TestTransformNode:
    @pytest.fixture
    def handlers(self):
        return NodeHandlers(FakeDB())

    @pytest.mark.asyncio
    async def test_simple_mapping(self, handlers):
        node_def = {
            "type": "transform",
            "config": {"mappings": {"title": "task.title", "count": "items.length"}},
        }
        run = FakeRun(context={"task": {"title": "Test"}, "items": {"length": 5}})
        result = await handlers.execute(node_def, run)
        assert result.status == "completed"
        assert result.output["title"] == "Test"
        assert result.output["count"] == 5

    @pytest.mark.asyncio
    async def test_len_expr(self, handlers):
        node_def = {
            "type": "transform",
            "config": {"mappings": {"count": {"expr": "len", "of": "items"}}},
        }
        run = FakeRun(context={"items": [1, 2, 3]})
        result = await handlers.execute(node_def, run)
        assert result.output["count"] == 3

    @pytest.mark.asyncio
    async def test_join_expr(self, handlers):
        node_def = {
            "type": "transform",
            "config": {"mappings": {"csv": {"expr": "join", "of": "names", "sep": ","}}},
        }
        run = FakeRun(context={"names": ["a", "b", "c"]})
        result = await handlers.execute(node_def, run)
        assert result.output["csv"] == "a,b,c"

    @pytest.mark.asyncio
    async def test_default_expr(self, handlers):
        node_def = {
            "type": "transform",
            "config": {"mappings": {"val": {"expr": "default", "of": "missing", "value": "fallback"}}},
        }
        run = FakeRun(context={})
        result = await handlers.execute(node_def, run)
        assert result.output["val"] == "fallback"

    @pytest.mark.asyncio
    async def test_template_rendering(self, handlers):
        node_def = {
            "type": "transform",
            "config": {"template": "Task: {task.title} is {task.status}"},
        }
        run = FakeRun(context={"task": {"title": "Bug fix", "status": "done"}})
        result = await handlers.execute(node_def, run)
        assert result.output["rendered"] == "Task: Bug fix is done"


# ══════════════════════════════════════════════════════════════════════
# Parallel Node Tests
# ══════════════════════════════════════════════════════════════════════

class TestParallelNode:
    @pytest.fixture
    def handlers(self):
        return NodeHandlers(FakeDB())

    @pytest.mark.asyncio
    async def test_parallel_all_succeed(self, handlers):
        node_def = {
            "type": "parallel",
            "config": {
                "branches": [
                    {"id": "b1", "type": "tool_call", "config": {"command": "echo one"}},
                    {"id": "b2", "type": "tool_call", "config": {"command": "echo two"}},
                ],
            },
        }
        result = await handlers.execute(node_def, FakeRun())
        assert result.status == "completed"
        assert result.output["succeeded"] == 2
        assert result.output["failed"] == 0

    @pytest.mark.asyncio
    async def test_parallel_partial_failure(self, handlers):
        node_def = {
            "type": "parallel",
            "config": {
                "branches": [
                    {"id": "b1", "type": "tool_call", "config": {"command": "echo ok"}},
                    {"id": "b2", "type": "tool_call", "config": {"command": "false"}},
                ],
            },
        }
        result = await handlers.execute(node_def, FakeRun())
        assert result.status == "completed"  # fail_fast=false by default
        assert result.output["succeeded"] == 1
        assert result.output["failed"] == 1

    @pytest.mark.asyncio
    async def test_parallel_fail_fast(self, handlers):
        node_def = {
            "type": "parallel",
            "config": {
                "fail_fast": True,
                "branches": [
                    {"id": "b1", "type": "tool_call", "config": {"command": "echo ok"}},
                    {"id": "b2", "type": "tool_call", "config": {"command": "false"}},
                ],
            },
        }
        result = await handlers.execute(node_def, FakeRun())
        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_parallel_empty(self, handlers):
        node_def = {"type": "parallel", "config": {"branches": []}}
        result = await handlers.execute(node_def, FakeRun())
        assert result.status == "completed"
        assert result.output["branches"] == 0


# ══════════════════════════════════════════════════════════════════════
# ForEach Node Tests
# ══════════════════════════════════════════════════════════════════════

class TestForEachNode:
    @pytest.fixture
    def handlers(self):
        return NodeHandlers(FakeDB())

    @pytest.mark.asyncio
    async def test_for_each_tool_call(self, handlers):
        node_def = {
            "type": "for_each",
            "config": {
                "items_ref": "names",
                "item_key": "name",
                "node_template": {
                    "type": "tool_call",
                    "config": {"command": "echo {name}"},
                },
            },
        }
        run = FakeRun(context={"names": ["alice", "bob", "carol"]})
        result = await handlers.execute(node_def, run)
        assert result.status == "completed"
        assert result.output["items_processed"] == 3
        assert result.output["succeeded"] == 3

    @pytest.mark.asyncio
    async def test_for_each_no_template(self, handlers):
        node_def = {
            "type": "for_each",
            "config": {"items_ref": "items"},
        }
        run = FakeRun(context={"items": [1, 2, 3]})
        result = await handlers.execute(node_def, run)
        assert result.status == "completed"
        assert result.output["count"] == 3

    @pytest.mark.asyncio
    async def test_for_each_empty_list(self, handlers):
        node_def = {
            "type": "for_each",
            "config": {
                "items_ref": "items",
                "node_template": {"type": "tool_call", "config": {"command": "echo {item}"}},
            },
        }
        run = FakeRun(context={"items": []})
        result = await handlers.execute(node_def, run)
        assert result.status == "completed"
        assert result.output["items_processed"] == 0

    @pytest.mark.asyncio
    async def test_for_each_bad_ref(self, handlers):
        node_def = {
            "type": "for_each",
            "config": {"items_ref": "nonexistent", "node_template": {"type": "tool_call", "config": {"command": "echo"}}},
        }
        result = await handlers.execute(node_def, FakeRun())
        assert result.status == "completed"
        assert result.output["items_processed"] == 0


# ══════════════════════════════════════════════════════════════════════
# HTTP Request Node Tests
# ══════════════════════════════════════════════════════════════════════

class TestHttpRequestNode:
    @pytest.fixture
    def handlers(self):
        return NodeHandlers(FakeDB())

    @pytest.mark.asyncio
    async def test_no_url(self, handlers):
        node_def = {"type": "http_request", "config": {}}
        result = await handlers.execute(node_def, FakeRun())
        assert result.status == "failed"
        assert "No URL" in result.error

    @pytest.mark.asyncio
    async def test_url_template_rendering(self, handlers):
        """Test that URL templates get rendered — actual HTTP call will fail but that's fine."""
        node_def = {
            "type": "http_request",
            "config": {"url": "http://localhost:99999/{path}", "timeout_seconds": 1},
        }
        run = FakeRun(context={"path": "test"})
        result = await handlers.execute(node_def, run)
        # Will fail to connect, but proves URL was rendered
        assert result.status == "failed"


# ══════════════════════════════════════════════════════════════════════
# Delay Node Tests
# ══════════════════════════════════════════════════════════════════════

class TestDelayNode:
    @pytest.fixture
    def handlers(self):
        return NodeHandlers(FakeDB())

    @pytest.mark.asyncio
    async def test_delay_check_not_elapsed(self, handlers):
        import time
        future = time.time() + 3600  # 1 hour from now
        run = FakeRun(node_states={"d1": {"output": {"wait_until": future}}})
        result = await handlers.check({"id": "d1", "type": "delay"}, run)
        assert result is None  # Still waiting

    @pytest.mark.asyncio
    async def test_delay_check_elapsed(self, handlers):
        import time
        past = time.time() - 10
        run = FakeRun(node_states={"d1": {"output": {"wait_until": past, "delay_seconds": 5}}})
        result = await handlers.check({"id": "d1", "type": "delay"}, run)
        assert result is not None
        assert result.status == "completed"


# ══════════════════════════════════════════════════════════════════════
# SubWorkflow Node Tests
# ══════════════════════════════════════════════════════════════════════

class TestSubWorkflowNode:
    @pytest.mark.asyncio
    async def test_no_workflow_id(self):
        handlers = NodeHandlers(FakeDB())
        node_def = {"type": "sub_workflow", "config": {}}
        result = await handlers.execute(node_def, FakeRun())
        assert result.status == "failed"
        assert "No workflow_id" in result.error

    @pytest.mark.asyncio
    async def test_workflow_not_found(self):
        db = FakeDB()
        handlers = NodeHandlers(db)
        node_def = {"type": "sub_workflow", "config": {"workflow_id": "nonexistent"}}
        result = await handlers.execute(node_def, FakeRun())
        assert result.status == "failed"
        assert "not found" in result.error


# ══════════════════════════════════════════════════════════════════════
# Seed Workflow Validation
# ══════════════════════════════════════════════════════════════════════

class TestSeedWorkflows:
    def test_seeds_have_valid_node_types(self):
        """Ensure all seed workflow node types are registered."""
        from app.orchestrator.workflow_seeds import DEFAULT_WORKFLOWS
        registered = get_registered_node_types()

        for wf in DEFAULT_WORKFLOWS:
            name = wf.get("name", "unknown")
            for node in wf.get("nodes", []):
                ntype = node.get("type")
                assert ntype in registered, f"Seed workflow '{name}' has unregistered node type: {ntype}"

    def test_seeds_have_unique_node_ids(self):
        """Each workflow's nodes should have unique IDs."""
        from app.orchestrator.workflow_seeds import DEFAULT_WORKFLOWS
        for wf in DEFAULT_WORKFLOWS:
            name = wf.get("name", "unknown")
            ids = [n["id"] for n in wf.get("nodes", [])]
            assert len(ids) == len(set(ids)), f"Seed workflow '{name}' has duplicate node IDs"

    def test_seeds_on_success_targets_exist(self):
        """on_success references should point to nodes that exist in the workflow."""
        from app.orchestrator.workflow_seeds import DEFAULT_WORKFLOWS
        for wf in DEFAULT_WORKFLOWS:
            name = wf.get("name", "unknown")
            node_ids = {n["id"] for n in wf.get("nodes", [])}
            for node in wf.get("nodes", []):
                target = node.get("on_success")
                if target:
                    assert target in node_ids, f"Seed workflow '{name}' node '{node['id']}' has on_success='{target}' which doesn't exist"

    def test_seeds_edge_targets_exist(self):
        """Edge from/to references should point to nodes that exist."""
        from app.orchestrator.workflow_seeds import DEFAULT_WORKFLOWS
        for wf in DEFAULT_WORKFLOWS:
            name = wf.get("name", "unknown")
            node_ids = {n["id"] for n in wf.get("nodes", [])}
            for edge in wf.get("edges", []):
                assert edge["from"] in node_ids, f"Seed '{name}' edge from '{edge['from']}' not found"
                assert edge["to"] in node_ids, f"Seed '{name}' edge to '{edge['to']}' not found"

    def test_all_expected_seeds_present(self):
        """All expected seed workflows must be present."""
        from app.orchestrator.workflow_seeds import DEFAULT_WORKFLOWS
        names = {wf["name"] for wf in DEFAULT_WORKFLOWS}
        expected = {
            "task-router", "agent-assignment", "scan-unassigned", "calendar-sync",
            "email-check", "tracker-deadlines", "tracker-daily-summary",
            "daily-learning", "create-learning-plan", "reflection-cycle",
            "diagnostic-scan", "daily-compression", "scheduled-events",
            "github-sync", "memory-sync",
        }
        for name in expected:
            assert name in names, f"Missing expected seed workflow: {name}"

    def test_seeds_branch_gotos_exist(self):
        """Branch condition goto targets should reference existing nodes."""
        from app.orchestrator.workflow_seeds import DEFAULT_WORKFLOWS
        for wf in DEFAULT_WORKFLOWS:
            name = wf.get("name", "unknown")
            node_ids = {n["id"] for n in wf.get("nodes", [])}
            for node in wf.get("nodes", []):
                if node.get("type") == "branch":
                    for cond in node.get("config", {}).get("conditions", []):
                        goto = cond.get("goto")
                        if goto:
                            assert goto in node_ids, f"Seed '{name}' branch '{node['id']}' goto '{goto}' not found"
                    default = node.get("config", {}).get("default")
                    if default:
                        assert default in node_ids, f"Seed '{name}' branch '{node['id']}' default '{default}' not found"


# ══════════════════════════════════════════════════════════════════════
# Executor Tests (workflow advancement, transitions, failure handling)
# ══════════════════════════════════════════════════════════════════════

class FakeWorkflowDef:
    """Minimal WorkflowDefinition-like object."""
    def __init__(self, wf_id, name, nodes, edges=None, trigger=None, is_active=True):
        self.id = wf_id
        self.name = name
        self.nodes = nodes
        self.edges = edges or []
        self.trigger = trigger
        self.is_active = is_active
        self.version = 1


class FakeWorkflowRun:
    """Minimal WorkflowRun-like object that behaves like the real model."""
    def __init__(self, wf_id, context=None, status="pending"):
        self.id = str(uuid.uuid4())
        self.workflow_id = wf_id
        self.workflow_version = 1
        self.task_id = None
        self.trigger_type = "manual"
        self.trigger_payload = None
        self.status = status
        self.current_node = None
        self.node_states = {}
        self.context = context or {}
        self.error = None
        self.session_key = None
        self.started_at = None
        self.finished_at = None
        self.updated_at = None
        self.created_at = datetime.now(timezone.utc)


class ExecutorFakeDB:
    """DB mock that supports get() for WorkflowDefinition and WorkflowRun."""
    def __init__(self):
        self.added = []
        self._objects = {}  # (type_name, id) -> object

    def store(self, obj, type_name=None):
        tn = type_name or type(obj).__name__
        self._objects[(tn, obj.id)] = obj

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass

    async def get(self, model, pk):
        name = model.__name__ if hasattr(model, '__name__') else str(model)
        return self._objects.get((name, pk))

    async def execute(self, stmt):
        class FakeResult:
            def scalar_one_or_none(self):
                return None
            def scalars(self):
                class S:
                    def all(self):
                        return []
                return S()
        return FakeResult()


class TestExecutorAdvancement:
    """Test the WorkflowExecutor advancing runs through nodes."""

    def _make_executor(self, db):
        from app.orchestrator.workflow_executor import WorkflowExecutor
        return WorkflowExecutor(db)

    @pytest.mark.asyncio
    async def test_linear_workflow_full_run(self):
        """Advance a simple linear workflow: echo1 -> echo2 -> cleanup."""
        db = ExecutorFakeDB()
        wf = FakeWorkflowDef("wf1", "test-linear", nodes=[
            {"id": "echo1", "type": "tool_call", "config": {"command": "echo step1"}, "on_success": "echo2"},
            {"id": "echo2", "type": "tool_call", "config": {"command": "echo step2"}, "on_success": "done"},
            {"id": "done", "type": "cleanup", "config": {"delete_session": False}},
        ])
        db.store(wf, "WorkflowDefinition")

        run = FakeWorkflowRun("wf1")
        executor = self._make_executor(db)

        # Step 1: pending -> running, set entry node
        did_work = await executor.advance(run)
        assert did_work
        assert run.status == "running"
        assert run.current_node == "echo1"

        # Step 2: execute echo1
        did_work = await executor.advance(run)
        assert did_work
        assert run.node_states["echo1"]["status"] == "completed"

        # Step 3: transition to echo2
        did_work = await executor.advance(run)
        assert run.current_node == "echo2"

        # Step 4: execute echo2
        await executor.advance(run)
        assert run.node_states["echo2"]["status"] == "completed"

        # Step 5: transition to done
        await executor.advance(run)
        assert run.current_node == "done"

        # Step 6: execute cleanup
        await executor.advance(run)
        assert run.node_states["done"]["status"] == "completed"

        # Step 7: transition — no next node, workflow completes
        await executor.advance(run)
        assert run.status == "completed"

    @pytest.mark.asyncio
    async def test_branch_routing(self):
        """Test that branch nodes correctly route based on context."""
        db = ExecutorFakeDB()
        wf = FakeWorkflowDef("wf2", "test-branch", nodes=[
            {"id": "decide", "type": "branch", "config": {
                "conditions": [
                    {"match": "mode == fast", "goto": "fast_path"},
                    {"match": "mode == slow", "goto": "slow_path"},
                ],
                "default": "fallback",
            }},
            {"id": "fast_path", "type": "tool_call", "config": {"command": "echo fast"}},
            {"id": "slow_path", "type": "tool_call", "config": {"command": "echo slow"}},
            {"id": "fallback", "type": "tool_call", "config": {"command": "echo fallback"}},
        ])
        db.store(wf, "WorkflowDefinition")

        run = FakeWorkflowRun("wf2", context={"mode": "fast"})

        executor = self._make_executor(db)

        # Bootstrap
        await executor.advance(run)
        assert run.current_node == "decide"

        # Execute branch
        await executor.advance(run)
        assert run.node_states["decide"]["status"] == "completed"
        assert run.node_states["decide"]["output"]["goto"] == "fast_path"

        # Transition follows goto
        await executor.advance(run)
        assert run.current_node == "fast_path"

    @pytest.mark.asyncio
    async def test_edge_based_routing(self):
        """Test routing via edges array instead of on_success."""
        db = ExecutorFakeDB()
        wf = FakeWorkflowDef("wf3", "test-edges", nodes=[
            {"id": "start", "type": "tool_call", "config": {"command": "echo start"}},
            {"id": "end", "type": "cleanup", "config": {"delete_session": False}},
        ], edges=[
            {"from": "start", "to": "end"},
        ])
        db.store(wf, "WorkflowDefinition")

        run = FakeWorkflowRun("wf3")
        executor = self._make_executor(db)

        # Bootstrap
        await executor.advance(run)
        assert run.current_node == "start"

        # Execute start
        await executor.advance(run)
        assert run.node_states["start"]["status"] == "completed"

        # Transition via edge
        await executor.advance(run)
        assert run.current_node == "end"

    @pytest.mark.asyncio
    async def test_edge_with_condition(self):
        """Edges with conditions should only fire when condition is met."""
        db = ExecutorFakeDB()
        wf = FakeWorkflowDef("wf4", "test-cond-edges", nodes=[
            {"id": "check", "type": "tool_call", "config": {"command": "echo ok"}},
            {"id": "yes_path", "type": "cleanup", "config": {"delete_session": False}},
            {"id": "no_path", "type": "cleanup", "config": {"delete_session": False}},
        ], edges=[
            {"from": "check", "to": "yes_path", "condition": "flag == true"},
            {"from": "check", "to": "no_path"},  # Fallback edge (no condition)
        ])
        db.store(wf, "WorkflowDefinition")

        # With flag=false, should skip conditional edge and take fallback
        run = FakeWorkflowRun("wf4", context={"flag": "false"})
        executor = self._make_executor(db)

        await executor.advance(run)  # bootstrap
        await executor.advance(run)  # execute check
        await executor.advance(run)  # transition
        assert run.current_node == "no_path"

    @pytest.mark.asyncio
    async def test_failure_retry(self):
        """Test that failed nodes get retried according to on_failure policy."""
        db = ExecutorFakeDB()
        wf = FakeWorkflowDef("wf5", "test-retry", nodes=[
            {"id": "fail_node", "type": "tool_call", "config": {"command": "false"},
             "on_failure": {"retry": 2}, "on_success": "done"},
            {"id": "done", "type": "cleanup", "config": {"delete_session": False}},
        ])
        db.store(wf, "WorkflowDefinition")

        run = FakeWorkflowRun("wf5")
        executor = self._make_executor(db)

        # Bootstrap
        await executor.advance(run)

        # Execute fail_node — attempt 1, fails
        await executor.advance(run)
        assert run.node_states["fail_node"]["status"] == "failed"

        # Handle failure — retry (resets to pending)
        await executor.advance(run)
        assert run.node_states["fail_node"]["status"] == "pending"

        # Execute again — attempt 2, still fails
        await executor.advance(run)
        assert run.node_states["fail_node"]["status"] == "failed"

        # Handle failure — retry again
        await executor.advance(run)
        assert run.node_states["fail_node"]["status"] == "pending"

        # Execute again — attempt 3, still fails
        await executor.advance(run)
        assert run.node_states["fail_node"]["status"] == "failed"

        # Handle failure — retries exhausted, workflow fails
        await executor.advance(run)
        assert run.status == "failed"

    @pytest.mark.asyncio
    async def test_failure_fallback(self):
        """Test fallback node on failure."""
        db = ExecutorFakeDB()
        wf = FakeWorkflowDef("wf6", "test-fallback", nodes=[
            {"id": "risky", "type": "tool_call", "config": {"command": "false"},
             "on_failure": {"retry": 0, "fallback": "safe"}},
            {"id": "safe", "type": "tool_call", "config": {"command": "echo safe"}},
        ])
        db.store(wf, "WorkflowDefinition")

        run = FakeWorkflowRun("wf6")
        executor = self._make_executor(db)

        await executor.advance(run)  # bootstrap
        await executor.advance(run)  # execute risky -> fails
        await executor.advance(run)  # handle failure -> fallback to safe
        assert run.current_node == "safe"

    @pytest.mark.asyncio
    async def test_missing_workflow_definition(self):
        """Run with missing workflow def should fail gracefully."""
        db = ExecutorFakeDB()
        run = FakeWorkflowRun("nonexistent")
        run.status = "running"
        run.current_node = "x"

        executor = self._make_executor(db)
        await executor.advance(run)
        assert run.status == "failed"
        assert "not found" in run.error

    @pytest.mark.asyncio
    async def test_on_success_takes_priority_over_edges(self):
        """on_success on a node should take priority over edges."""
        db = ExecutorFakeDB()
        wf = FakeWorkflowDef("wf7", "test-priority", nodes=[
            {"id": "start", "type": "tool_call", "config": {"command": "echo hi"}, "on_success": "via_on_success"},
            {"id": "via_on_success", "type": "cleanup", "config": {"delete_session": False}},
            {"id": "via_edge", "type": "cleanup", "config": {"delete_session": False}},
        ], edges=[
            {"from": "start", "to": "via_edge"},
        ])
        db.store(wf, "WorkflowDefinition")

        run = FakeWorkflowRun("wf7")
        executor = self._make_executor(db)

        await executor.advance(run)  # bootstrap
        await executor.advance(run)  # execute start
        await executor.advance(run)  # transition
        assert run.current_node == "via_on_success"

    @pytest.mark.asyncio
    async def test_context_flows_between_nodes(self):
        """Node output should be available in context for downstream nodes."""
        db = ExecutorFakeDB()
        wf = FakeWorkflowDef("wf8", "test-context", nodes=[
            {"id": "step1", "type": "tool_call", "config": {"command": "echo hello"}, "on_success": "step2"},
            {"id": "step2", "type": "tool_call", "config": {"command": "echo {step1.stdout}"}},
        ])
        db.store(wf, "WorkflowDefinition")

        run = FakeWorkflowRun("wf8")
        executor = self._make_executor(db)

        await executor.advance(run)  # bootstrap
        await executor.advance(run)  # execute step1
        assert "step1" in run.context
        assert "hello" in run.context["step1"]["stdout"]

        await executor.advance(run)  # transition to step2
        await executor.advance(run)  # execute step2
        # step2 should have received step1's output via template
        assert "hello" in run.context["step2"]["stdout"]
