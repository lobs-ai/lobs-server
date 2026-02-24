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
