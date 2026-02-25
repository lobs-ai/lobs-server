"""Workflow function registry and expression engine.

Provides built-in functions that can be called from workflow node conditions
and expressions without writing Python. Functions are registered with
@register_function and can be used in expressions like:

    numTasks("pending") > 0
    agentStatus("programmer") == "idle"
    hasUnread()
    taskField("priority") in ["high", "critical"]

The expression engine safely evaluates these by parsing the expression,
resolving function calls, and evaluating comparisons — no raw eval().
"""

import ast
import logging
import operator
import re
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from sqlalchemy import select, func as sql_func
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════
# Function Registry
# ══════════════════════════════════════════════════════════════════════

# Signature: async (args: list[Any], context: dict, db: AsyncSession, worker_manager: Any) -> Any
_FUNCTION_REGISTRY: dict[str, Callable] = {}


def register_function(name: str):
    """Decorator to register a workflow function.

    Usage:
        @register_function("numTasks")
        async def fn_num_tasks(args, context, db, worker_manager):
            ...
    """
    def decorator(fn: Callable) -> Callable:
        _FUNCTION_REGISTRY[name] = fn
        return fn
    return decorator


def get_registered_functions() -> list[str]:
    """Return all registered function names."""
    return sorted(_FUNCTION_REGISTRY.keys())


# ══════════════════════════════════════════════════════════════════════
# Expression Engine — safe evaluation with function calls
# ══════════════════════════════════════════════════════════════════════

# Supported operators
_COMPARE_OPS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Gt: operator.gt,
    ast.Lt: operator.lt,
    ast.GtE: operator.ge,
    ast.LtE: operator.le,
}

_BOOL_OPS = {
    ast.And: all,
    ast.Or: any,
}

_UNARY_OPS = {
    ast.Not: operator.not_,
    ast.USub: operator.neg,
}


class ExpressionError(Exception):
    """Raised when an expression cannot be evaluated."""
    pass


async def evaluate_expression(
    expr: str,
    context: dict[str, Any],
    db: AsyncSession,
    worker_manager: Any = None,
) -> Any:
    """Safely evaluate a workflow expression string.

    Supports:
    - Function calls: numTasks("pending"), agentStatus("programmer")
    - Comparisons: >, <, ==, !=, >=, <=
    - Boolean: and, or, not
    - Literals: strings, numbers, booleans, None, lists
    - Context paths: ctx.task.status (resolved from context dict)
    - Arithmetic: +, -, *, /

    Does NOT support:
    - Imports, attribute access on non-context objects, arbitrary Python
    """
    expr = expr.strip()
    if not expr:
        raise ExpressionError("Empty expression")

    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ExpressionError(f"Syntax error in expression: {e}") from e

    async def _eval_node(node: ast.AST) -> Any:
        # Literals
        if isinstance(node, ast.Expression):
            return await _eval_node(node.body)

        if isinstance(node, ast.Constant):
            return node.value

        if isinstance(node, (ast.List, ast.Tuple)):
            return [await _eval_node(el) for el in node.elts]

        if isinstance(node, ast.Set):
            return {await _eval_node(el) for el in node.elts}

        # Name — resolve from context
        if isinstance(node, ast.Name):
            name = node.id
            # Built-in constants
            if name == "True":
                return True
            if name == "False":
                return False
            if name == "None":
                return None
            # Context lookup
            return context.get(name)

        # Attribute access — only on context paths (ctx.task.status style)
        if isinstance(node, ast.Attribute):
            value = await _eval_node(node.value)
            if isinstance(value, dict):
                return value.get(node.attr)
            raise ExpressionError(f"Cannot access attribute '{node.attr}' on {type(value).__name__}")

        # Subscript — dict/list access
        if isinstance(node, ast.Subscript):
            value = await _eval_node(node.value)
            key = await _eval_node(node.slice)
            if isinstance(value, (dict, list)):
                try:
                    return value[key]
                except (KeyError, IndexError, TypeError):
                    return None
            return None

        # Function calls
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                func_name = node.func.id
                func = _FUNCTION_REGISTRY.get(func_name)
                if func is None:
                    raise ExpressionError(f"Unknown function: {func_name}")
                args = [await _eval_node(a) for a in node.args]
                # Support keyword args
                kwargs = {}
                for kw in node.keywords:
                    kwargs[kw.arg] = await _eval_node(kw.value)
                return await func(args, context, db, worker_manager, **kwargs)
            raise ExpressionError(f"Unsupported call target: {ast.dump(node.func)}")

        # Comparisons
        if isinstance(node, ast.Compare):
            left = await _eval_node(node.left)
            for op_node, comparator in zip(node.ops, node.comparators):
                right = await _eval_node(comparator)
                # Handle "in" operator
                if isinstance(op_node, ast.In):
                    if not isinstance(right, (list, tuple, set, dict)):
                        return False
                    if str(left) not in [str(x) for x in right]:
                        return False
                elif isinstance(op_node, ast.NotIn):
                    if isinstance(right, (list, tuple, set, dict)):
                        if str(left) in [str(x) for x in right]:
                            return False
                    else:
                        return True
                else:
                    op_func = _COMPARE_OPS.get(type(op_node))
                    if op_func is None:
                        raise ExpressionError(f"Unsupported comparison: {type(op_node).__name__}")
                    # Try numeric comparison first, fall back to string
                    try:
                        if not op_func(float(left), float(right)):
                            return False
                    except (ValueError, TypeError):
                        if not op_func(str(left), str(right)):
                            return False
                left = right
            return True

        # Boolean operators
        if isinstance(node, ast.BoolOp):
            op_func = _BOOL_OPS.get(type(node.op))
            if op_func is None:
                raise ExpressionError(f"Unsupported boolean op: {type(node.op).__name__}")
            values = [await _eval_node(v) for v in node.values]
            return op_func(values)

        # Unary operators
        if isinstance(node, ast.UnaryOp):
            op_func = _UNARY_OPS.get(type(node.op))
            if op_func is None:
                raise ExpressionError(f"Unsupported unary op: {type(node.op).__name__}")
            return op_func(await _eval_node(node.operand))

        # Binary operators (arithmetic)
        if isinstance(node, ast.BinOp):
            left = await _eval_node(node.left)
            right = await _eval_node(node.right)
            if isinstance(node.op, ast.Add):
                return _safe_add(left, right)
            if isinstance(node.op, ast.Sub):
                return float(left or 0) - float(right or 0)
            if isinstance(node.op, ast.Mult):
                return float(left or 0) * float(right or 0)
            if isinstance(node.op, ast.Div):
                r = float(right or 0)
                if r == 0:
                    raise ExpressionError("Division by zero")
                return float(left or 0) / r
            if isinstance(node.op, ast.Mod):
                r = float(right or 0)
                if r == 0:
                    raise ExpressionError("Modulo by zero")
                return float(left or 0) % r
            raise ExpressionError(f"Unsupported binary op: {type(node.op).__name__}")

        # IfExp (ternary)
        if isinstance(node, ast.IfExp):
            test = await _eval_node(node.test)
            if test:
                return await _eval_node(node.body)
            return await _eval_node(node.orelse)

        raise ExpressionError(f"Unsupported expression node: {type(node).__name__}")

    return await _eval_node(tree)


def _safe_add(left, right):
    """Add two values, handling string concatenation and numeric addition."""
    if isinstance(left, str) or isinstance(right, str):
        return str(left or "") + str(right or "")
    try:
        return float(left or 0) + float(right or 0)
    except (ValueError, TypeError):
        return str(left or "") + str(right or "")


async def evaluate_condition_async(
    expr: str,
    context: dict[str, Any],
    db: AsyncSession,
    worker_manager: Any = None,
) -> bool:
    """Evaluate an expression and return its truthiness."""
    try:
        result = await evaluate_expression(expr, context, db, worker_manager)
        return bool(result)
    except ExpressionError as e:
        logger.warning("[EXPR] Condition evaluation failed: %s — expr: %s", e, expr[:100])
        return False


# ══════════════════════════════════════════════════════════════════════
# Built-in Functions
# ══════════════════════════════════════════════════════════════════════


@register_function("numTasks")
async def fn_num_tasks(args, context, db, worker_manager, **kw):
    """Count tasks by status.

    Usage: numTasks("pending"), numTasks("in_progress"), numTasks()
    """
    from app.models import Task

    status_filter = args[0] if args else None
    query = select(sql_func.count(Task.id))

    if status_filter:
        if status_filter in ("pending", "in_progress", "completed", "blocked", "cancelled"):
            query = query.where(Task.status == status_filter)
        elif status_filter == "open":
            query = query.where(Task.status.in_(["pending", "in_progress"]))
        elif status_filter == "active":
            query = query.where(Task.work_state == "in_progress")
    result = await db.execute(query)
    return result.scalar() or 0


@register_function("numTasksForProject")
async def fn_num_tasks_for_project(args, context, db, worker_manager, **kw):
    """Count tasks for a specific project.

    Usage: numTasksForProject("project-id"), numTasksForProject("project-id", "pending")
    """
    from app.models import Task

    if not args:
        return 0
    project_id = args[0]
    status_filter = args[1] if len(args) > 1 else None

    query = select(sql_func.count(Task.id)).where(Task.project_id == project_id)
    if status_filter:
        query = query.where(Task.status == status_filter)
    result = await db.execute(query)
    return result.scalar() or 0


@register_function("taskField")
async def fn_task_field(args, context, db, worker_manager, **kw):
    """Get a field from the current task in context.

    Usage: taskField("status"), taskField("priority"), taskField("agent")
    """
    if not args:
        return None
    field = args[0]
    task = context.get("task", {})
    if isinstance(task, dict):
        return task.get(field)
    return None


@register_function("agentStatus")
async def fn_agent_status(args, context, db, worker_manager, **kw):
    """Check if an agent type currently has an active worker.

    Usage: agentStatus("programmer") -> "busy" | "idle"
    """
    if not args or not worker_manager:
        return "unknown"
    agent_type = args[0]
    for w in worker_manager.active_workers.values():
        if getattr(w, "agent_type", None) == agent_type:
            return "busy"
    return "idle"


@register_function("activeWorkers")
async def fn_active_workers(args, context, db, worker_manager, **kw):
    """Count active workers, optionally filtered by agent type.

    Usage: activeWorkers(), activeWorkers("programmer")
    """
    if not worker_manager:
        return 0
    agent_filter = args[0] if args else None
    count = 0
    for w in worker_manager.active_workers.values():
        if agent_filter and getattr(w, "agent_type", None) != agent_filter:
            continue
        count += 1
    return count


@register_function("hasUnread")
async def fn_has_unread(args, context, db, worker_manager, **kw):
    """Check if there are unread inbox items.

    Usage: hasUnread() -> bool
    """
    from app.models import InboxItem
    result = await db.execute(
        select(sql_func.count(InboxItem.id)).where(InboxItem.is_read == False)
    )
    count = result.scalar() or 0
    return count > 0


@register_function("numUnread")
async def fn_num_unread(args, context, db, worker_manager, **kw):
    """Count unread inbox items.

    Usage: numUnread() -> int
    """
    from app.models import InboxItem
    result = await db.execute(
        select(sql_func.count(InboxItem.id)).where(InboxItem.is_read == False)
    )
    return result.scalar() or 0


@register_function("projectExists")
async def fn_project_exists(args, context, db, worker_manager, **kw):
    """Check if a project exists by ID or title substring.

    Usage: projectExists("lobs-server"), projectExists("my-project-id")
    """
    from app.models import Project
    if not args:
        return False
    identifier = args[0]

    # Try by ID
    project = await db.get(Project, identifier)
    if project:
        return True

    # Try by title
    result = await db.execute(
        select(sql_func.count(Project.id)).where(Project.title.ilike(f"%{identifier}%"))
    )
    return (result.scalar() or 0) > 0


@register_function("ctx")
async def fn_ctx(args, context, db, worker_manager, **kw):
    """Get a value from the workflow run context by dotted path.

    Usage: ctx("task.status"), ctx("trigger.event_type")
    """
    if not args:
        return None
    path = args[0]
    value = context
    for part in path.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        else:
            return None
    return value


@register_function("env")
async def fn_env(args, context, db, worker_manager, **kw):
    """Get an environment variable (safe subset).

    Usage: env("LOBS_PROJECTS_DIR")
    """
    import os
    if not args:
        return None
    # Allowlist of safe env vars
    SAFE_PREFIXES = ("LOBS_", "OPENCLAW_", "HOME", "USER", "PATH")
    key = args[0]
    if not any(key.startswith(p) for p in SAFE_PREFIXES):
        return None
    return os.environ.get(key)


@register_function("now")
async def fn_now(args, context, db, worker_manager, **kw):
    """Get current UTC timestamp as ISO string.

    Usage: now() -> "2026-02-24T22:14:00+00:00"
    """
    return datetime.now(timezone.utc).isoformat()


@register_function("hour")
async def fn_hour(args, context, db, worker_manager, **kw):
    """Get current hour (0-23) in a timezone.

    Usage: hour() -> 17, hour("America/New_York") -> 17
    """
    tz_name = args[0] if args else "UTC"
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = timezone.utc
    return datetime.now(tz).hour


@register_function("dayOfWeek")
async def fn_day_of_week(args, context, db, worker_manager, **kw):
    """Get current day of week (0=Monday, 6=Sunday).

    Usage: dayOfWeek() -> 0
    """
    tz_name = args[0] if args else "UTC"
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = timezone.utc
    return datetime.now(tz).weekday()


@register_function("len")
async def fn_len(args, context, db, worker_manager, **kw):
    """Get the length of a list, dict, or string.

    Usage: len([1,2,3]) -> 3 (typically used with ctx: len(ctx("items")))
    Note: For inline use, pass the value directly.
    """
    if not args:
        return 0
    val = args[0]
    if val is None:
        return 0
    if isinstance(val, (list, dict, str, tuple, set)):
        return len(val)
    return 0


@register_function("coalesce")
async def fn_coalesce(args, context, db, worker_manager, **kw):
    """Return the first non-None argument.

    Usage: coalesce(ctx("override_model"), "default-model")
    """
    for a in args:
        if a is not None:
            return a
    return None


@register_function("contains")
async def fn_contains(args, context, db, worker_manager, **kw):
    """Check if a value contains a substring or element.

    Usage: contains("hello world", "hello") -> True
           contains([1,2,3], 2) -> True
    """
    if len(args) < 2:
        return False
    haystack, needle = args[0], args[1]
    if haystack is None:
        return False
    if isinstance(haystack, str):
        return str(needle) in haystack
    if isinstance(haystack, (list, tuple, set)):
        return needle in haystack
    return False


@register_function("recentRunStatus")
async def fn_recent_run_status(args, context, db, worker_manager, **kw):
    """Get the status of the most recent run for a workflow.

    Usage: recentRunStatus("task-router") -> "completed" | "failed" | "running" | None
    """
    from app.models import WorkflowRun, WorkflowDefinition

    if not args:
        return None
    workflow_name = args[0]

    # Find workflow by name
    wf_result = await db.execute(
        select(WorkflowDefinition.id).where(WorkflowDefinition.name == workflow_name)
    )
    wf_id = wf_result.scalar()
    if not wf_id:
        return None

    result = await db.execute(
        select(WorkflowRun.status)
        .where(WorkflowRun.workflow_id == wf_id)
        .order_by(WorkflowRun.created_at.desc())
        .limit(1)
    )
    return result.scalar()


@register_function("workerCapacity")
async def fn_worker_capacity(args, context, db, worker_manager, **kw):
    """Check remaining worker capacity.

    Usage: workerCapacity() -> 2 (slots available)
    """
    if not worker_manager:
        return 0
    from app.orchestrator.config import MAX_WORKERS
    active = len(worker_manager.active_workers)
    return max(0, MAX_WORKERS - active)


@register_function("projectField")
async def fn_project_field(args, context, db, worker_manager, **kw):
    """Get a field from a project.

    Usage: projectField("project-id", "repo_path"), projectField("project-id", "title")
    """
    from app.models import Project
    if len(args) < 2:
        return None
    project_id, field = args[0], args[1]
    project = await db.get(Project, project_id)
    if not project:
        return None
    return getattr(project, field, None)
