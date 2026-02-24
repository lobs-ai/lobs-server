"""Seed default workflow definitions on first startup."""

import uuid
import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import WorkflowDefinition

logger = logging.getLogger(__name__)


async def seed_default_workflows(db: AsyncSession) -> int:
    """Create or update default workflows. Returns count of new workflows created.

    Existing workflows are updated in-place (nodes, edges, trigger, is_active, description)
    to keep definitions in sync with code. Version is bumped on update.
    """
    from app.models import WorkflowSubscription

    created = 0

    for defn in DEFAULT_WORKFLOWS:
        result = await db.execute(
            select(WorkflowDefinition).where(WorkflowDefinition.name == defn["name"])
        )
        existing = result.scalar_one_or_none()
        if existing:
            # Update existing workflow definition in-place
            existing.description = defn["description"]
            existing.nodes = defn["nodes"]
            existing.edges = defn.get("edges", [])
            existing.trigger = defn.get("trigger")
            existing.metadata_ = defn.get("metadata")
            existing.is_active = defn.get("is_active", True)
            existing.version = (existing.version or 1) + 1
            wf = existing
            logger.info("[WORKFLOW_SEED] Updated workflow: %s (v%d)", defn["name"], wf.version)
            # Still process subscriptions below
        else:
            wf = WorkflowDefinition(
            id=str(uuid.uuid4()),
            name=defn["name"],
            description=defn["description"],
            version=1,
            nodes=defn["nodes"],
            edges=defn.get("edges", []),
            trigger=defn.get("trigger"),
            metadata_=defn.get("metadata"),
            is_active=defn.get("is_active", True),
        )
        if not existing:
            db.add(wf)
            created += 1
            logger.info("[WORKFLOW_SEED] Created workflow: %s", defn["name"])

        # Auto-create subscriptions for event-triggered workflows
        trigger = defn.get("trigger")
        if trigger and trigger.get("type") == "event":
            event_pattern = trigger.get("event_pattern", "")
            if event_pattern:
                sub_exists = await db.execute(
                    select(WorkflowSubscription).where(
                        WorkflowSubscription.workflow_id == wf.id,
                        WorkflowSubscription.event_pattern == event_pattern,
                    )
                )
                if not sub_exists.scalar_one_or_none():
                    db.add(WorkflowSubscription(
                        id=str(uuid.uuid4()),
                        workflow_id=wf.id,
                        event_pattern=event_pattern,
                        filter_conditions=trigger.get("filter_conditions"),
                        is_active=True,
                    ))
                    logger.info("[WORKFLOW_SEED] Created subscription: %s → %s", defn["name"], event_pattern)

    if created:
        await db.commit()
    return created


DEFAULT_WORKFLOWS = [
    # ══════════════════════════════════════════════════════════════════
    # MASTER TASK ROUTER — entry point for ALL tasks
    # ══════════════════════════════════════════════════════════════════
    {
        "name": "task-router",
        "description": "Master task router: routes all tasks to the correct agent-specific workflow based on assigned agent type. This is the single entry point for all task execution.",
        "trigger": {"type": "task_match", "agent_types": ["programmer", "researcher", "writer", "architect", "reviewer", "inbox-responder"]},
        "is_active": True,
        "nodes": [
            {
                "id": "route_by_agent",
                "type": "branch",
                "config": {
                    "conditions": [
                        {"match": "task.agent == programmer", "goto": "run_code_task"},
                        {"match": "task.agent == researcher", "goto": "run_research_task"},
                        {"match": "task.agent == writer", "goto": "run_writer_task"},
                        {"match": "task.agent == architect", "goto": "run_architect_task"},
                        {"match": "task.agent == reviewer", "goto": "run_reviewer_task"},
                        {"match": "task.agent == inbox-responder", "goto": "run_inbox_task"},
                    ],
                    "default": "run_generic_task",
                },
            },
            # ── Programmer path ──────────────────────────────────────
            {
                "id": "run_code_task",
                "type": "spawn_agent",
                "config": {
                    "agent_type": "programmer",
                    "prompt_template": "{task.title}\n\n{task.notes}",
                    "timeout_seconds": 900,
                },
                "on_success": "run_tests",
                "on_failure": {"retry": 0, "abort_on": ["spawn_error"]},
            },
            {
                "id": "run_tests",
                "type": "tool_call",
                "config": {
                    "command": "cd {project.repo_path} && python -m pytest --tb=short 2>&1 || true",
                    "timeout_seconds": 300,
                },
                "on_success": "check_tests",
                "on_failure": {"retry": 1, "fallback": "fix_tests", "escalate_after": 3, "abort_on": ["timeout"]},
            },
            {
                "id": "check_tests",
                "type": "branch",
                "config": {
                    "conditions": [
                        {"match": "run_tests.returncode == 0", "goto": "run_lint"},
                    ],
                    "default": "fix_tests",
                },
            },
            {
                "id": "fix_tests",
                "type": "send_to_session",
                "config": {
                    "session_ref": "run_code_task.session_key",
                    "message_template": "Tests failed. Fix these errors:\n\nSTDOUT:\n{run_tests.stdout}\n\nSTDERR:\n{run_tests.stderr}",
                },
                "on_success": "run_tests_retry",
                "on_failure": {"retry": 0, "abort_on": ["spawn_error"]},
            },
            {
                "id": "run_tests_retry",
                "type": "tool_call",
                "config": {
                    "command": "cd {project.repo_path} && python -m pytest --tb=short 2>&1 || true",
                    "timeout_seconds": 300,
                },
                "on_success": "check_tests_retry",
                "on_failure": {"retry": 0, "abort_on": ["timeout"]},
            },
            {
                "id": "check_tests_retry",
                "type": "branch",
                "config": {
                    "conditions": [
                        {"match": "run_tests_retry.returncode == 0", "goto": "run_lint"},
                    ],
                    "default": "escalate_test_failure",
                },
            },
            {
                "id": "escalate_test_failure",
                "type": "gate",
                "config": {
                    "prompt": "Tests failed after fix attempt for task: {task.title}. Manual review needed.",
                    "timeout_hours": 24,
                },
                "on_success": "cleanup",
            },
            {
                "id": "run_lint",
                "type": "tool_call",
                "config": {
                    "command": "cd {project.repo_path} && ruff check . 2>&1 || true",
                    "timeout_seconds": 120,
                },
                "on_success": "check_lint",
                "on_failure": {"retry": 0, "abort_on": ["timeout"]},
            },
            {
                "id": "check_lint",
                "type": "branch",
                "config": {
                    "conditions": [
                        {"match": "run_lint.returncode == 0", "goto": "commit"},
                    ],
                    "default": "fix_lint",
                },
            },
            {
                "id": "fix_lint",
                "type": "send_to_session",
                "config": {
                    "session_ref": "run_code_task.session_key",
                    "message_template": "Lint errors found. Fix them:\n\n{run_lint.stdout}",
                },
                "on_success": "run_lint",
                "on_failure": {"retry": 0},
            },
            {
                "id": "commit",
                "type": "tool_call",
                "config": {
                    "command": "cd {project.repo_path} && git add -A && git diff --cached --quiet || git commit -m 'agent(programmer): {task.title}' --author 'lobs-programmer <thelobsbot@gmail.com>'",
                    "timeout_seconds": 30,
                },
                "on_success": "notify_complete",
                "on_failure": {"retry": 1},
            },
            # ── Researcher path ──────────────────────────────────────
            {
                "id": "run_research_task",
                "type": "spawn_agent",
                "config": {
                    "agent_type": "researcher",
                    "prompt_template": "{task.title}\n\n{task.notes}",
                    "timeout_seconds": 900,
                },
                "on_success": "notify_complete",
                "on_failure": {"retry": 1, "abort_on": ["spawn_error"]},
            },
            # ── Writer path ──────────────────────────────────────────
            {
                "id": "run_writer_task",
                "type": "spawn_agent",
                "config": {
                    "agent_type": "writer",
                    "prompt_template": "{task.title}\n\n{task.notes}",
                    "timeout_seconds": 900,
                },
                "on_success": "notify_complete",
                "on_failure": {"retry": 1, "abort_on": ["spawn_error"]},
            },
            # ── Architect path ───────────────────────────────────────
            {
                "id": "run_architect_task",
                "type": "spawn_agent",
                "config": {
                    "agent_type": "architect",
                    "prompt_template": "{task.title}\n\n{task.notes}",
                    "model_tier": "strong",
                    "timeout_seconds": 1200,
                },
                "on_success": "notify_complete",
                "on_failure": {"retry": 1, "abort_on": ["spawn_error"]},
            },
            # ── Reviewer path ────────────────────────────────────────
            {
                "id": "run_reviewer_task",
                "type": "spawn_agent",
                "config": {
                    "agent_type": "reviewer",
                    "prompt_template": "{task.title}\n\n{task.notes}",
                    "timeout_seconds": 600,
                },
                "on_success": "notify_complete",
                "on_failure": {"retry": 1, "abort_on": ["spawn_error"]},
            },
            # ── Inbox responder path ─────────────────────────────────
            {
                "id": "run_inbox_task",
                "type": "spawn_agent",
                "config": {
                    "agent_type": "inbox-responder",
                    "prompt_template": "{task.title}\n\n{task.notes}",
                    "model_tier": "medium",
                    "timeout_seconds": 300,
                },
                "on_success": "notify_complete",
                "on_failure": {"retry": 0, "abort_on": ["spawn_error"]},
            },
            # ── Generic fallback ─────────────────────────────────────
            {
                "id": "run_generic_task",
                "type": "spawn_agent",
                "config": {
                    "agent_type": "programmer",
                    "prompt_template": "{task.title}\n\n{task.notes}",
                    "timeout_seconds": 900,
                },
                "on_success": "notify_complete",
                "on_failure": {"retry": 1, "abort_on": ["spawn_error"]},
            },
            # ── Shared terminal nodes ────────────────────────────────
            {
                "id": "notify_complete",
                "type": "notify",
                "config": {
                    "channel": "internal",
                    "message_template": "Task completed: {task.title} (agent: {task.agent})",
                },
                "on_success": "cleanup",
            },
            {
                "id": "cleanup",
                "type": "cleanup",
                "config": {"delete_session": True},
            },
        ],
        "edges": [],
        "metadata": {"author": "lobs", "category": "core", "system": True},
    },
    # ══════════════════════════════════════════════════════════════════
    # AGENT ASSIGNMENT — LLM assigns agent to unassigned tasks
    # ══════════════════════════════════════════════════════════════════
    {
        "name": "agent-assignment",
        "description": "Use an LLM to analyze unassigned tasks and assign the correct agent type. Triggered by task.created events or on a schedule.",
        "trigger": {"type": "event", "event_pattern": "task.needs_assignment"},
        "is_active": True,
        "nodes": [
            {
                "id": "assign_agent",
                "type": "python_call",
                "config": {
                    "callable": "assignment.assign_agent",
                    "args_template": {
                        "task_id": "{trigger.task_id}",
                    },
                },
                "on_success": "check_assignment",
                "on_failure": {"retry": 1, "abort_on": ["python_error"]},
            },
            {
                "id": "check_assignment",
                "type": "branch",
                "config": {
                    "conditions": [
                        {"match": "assign_agent.assigned == true", "goto": "done"},
                    ],
                    "default": "notify_unassignable",
                },
            },
            {
                "id": "notify_unassignable",
                "type": "notify",
                "config": {
                    "channel": "internal",
                    "message_template": "Could not assign agent for task: {assign_agent.task_title}. Reason: {assign_agent.reason}",
                },
                "on_success": "done",
            },
            {"id": "done", "type": "cleanup", "config": {"delete_session": False}},
        ],
        "edges": [],
        "metadata": {"author": "lobs", "category": "core", "system": True},
    },
    # ══════════════════════════════════════════════════════════════════
    # SCAN UNASSIGNED — periodic scan for tasks missing agents
    # ══════════════════════════════════════════════════════════════════
    {
        "name": "scan-unassigned",
        "description": "Periodically scan for active tasks without an assigned agent and emit assignment events.",
        "trigger": {"type": "schedule", "cron": "*/2 * * * *", "timezone": "UTC"},
        "is_active": True,
        "nodes": [
            {
                "id": "scan",
                "type": "python_call",
                "config": {"callable": "assignment.scan_unassigned"},
                "on_success": "done",
                "on_failure": {"retry": 1},
            },
            {"id": "done", "type": "cleanup", "config": {"delete_session": False}},
        ],
        "edges": [],
        "metadata": {"author": "lobs", "category": "core", "system": True},
    },
    # ══════════════════════════════════════════════════════════════════
    # LEGACY AGENT-SPECIFIC WORKFLOWS (kept for direct triggering)
    # ══════════════════════════════════════════════════════════════════
    {
        "name": "code-task",
        "description": "Standalone code task workflow (superseded by task-router). Available for direct triggering.",
        "trigger": None,  # No auto-trigger — task-router handles programmer tasks
        "is_active": False,
        "nodes": [
            {
                "id": "write_code",
                "type": "spawn_agent",
                "config": {
                    "agent_type": "programmer",
                    "prompt_template": "{task.title}\n\n{task.notes}",
                    "timeout_seconds": 900,
                },
                "on_success": "run_tests",
                "on_failure": {"retry": 0, "abort_on": ["spawn_error"]},
            },
            {
                "id": "run_tests",
                "type": "tool_call",
                "config": {
                    "command": "cd {project.repo_path} && python -m pytest --tb=short 2>&1 || true",
                    "timeout_seconds": 300,
                },
                "on_success": "check_tests",
                "on_failure": {"retry": 1, "fallback": "fix_tests", "escalate_after": 3, "abort_on": ["timeout"]},
            },
            {
                "id": "check_tests",
                "type": "branch",
                "config": {
                    "conditions": [
                        {"match": "run_tests.returncode == 0", "goto": "run_lint"},
                    ],
                    "default": "fix_tests",
                },
            },
            {
                "id": "fix_tests",
                "type": "send_to_session",
                "config": {
                    "session_ref": "write_code.session_key",
                    "message_template": "Tests failed. Fix these errors:\n\nSTDOUT:\n{run_tests.stdout}\n\nSTDERR:\n{run_tests.stderr}",
                },
                "on_success": "run_tests_retry",
                "on_failure": {"retry": 0, "abort_on": ["spawn_error"]},
            },
            {
                "id": "run_tests_retry",
                "type": "tool_call",
                "config": {
                    "command": "cd {project.repo_path} && python -m pytest --tb=short 2>&1 || true",
                    "timeout_seconds": 300,
                },
                "on_success": "check_tests_retry",
                "on_failure": {"retry": 0, "abort_on": ["timeout"]},
            },
            {
                "id": "check_tests_retry",
                "type": "branch",
                "config": {
                    "conditions": [
                        {"match": "run_tests_retry.returncode == 0", "goto": "run_lint"},
                    ],
                    "default": "escalate_test_failure",
                },
            },
            {
                "id": "escalate_test_failure",
                "type": "gate",
                "config": {
                    "prompt": "Tests failed after fix attempt. Manual review needed.",
                    "timeout_hours": 24,
                },
                "on_success": "cleanup",
            },
            {
                "id": "run_lint",
                "type": "tool_call",
                "config": {
                    "command": "cd {project.repo_path} && ruff check . 2>&1 || true",
                    "timeout_seconds": 120,
                },
                "on_success": "check_lint",
                "on_failure": {"retry": 0, "abort_on": ["timeout"]},
            },
            {
                "id": "check_lint",
                "type": "branch",
                "config": {
                    "conditions": [
                        {"match": "run_lint.returncode == 0", "goto": "commit"},
                    ],
                    "default": "fix_lint",
                },
            },
            {
                "id": "fix_lint",
                "type": "send_to_session",
                "config": {
                    "session_ref": "write_code.session_key",
                    "message_template": "Lint errors found. Fix them:\n\n{run_lint.stdout}",
                },
                "on_success": "run_lint",
                "on_failure": {"retry": 0},
            },
            {
                "id": "commit",
                "type": "tool_call",
                "config": {
                    "command": "cd {project.repo_path} && git add -A && git diff --cached --quiet || git commit -m 'agent(programmer): {task.title}' --author 'lobs-programmer <thelobsbot@gmail.com>'",
                    "timeout_seconds": 30,
                },
                "on_success": "cleanup",
                "on_failure": {"retry": 1},
            },
            {
                "id": "cleanup",
                "type": "cleanup",
                "config": {"delete_session": True},
            },
        ],
        "edges": [],
        "metadata": {"author": "lobs", "category": "code"},
    },
    {
        "name": "research-task",
        "description": "Standalone research task workflow (superseded by task-router). Available for direct triggering.",
        "trigger": None,  # No auto-trigger — task-router handles researcher tasks
        "is_active": False,
        "nodes": [
            {
                "id": "research",
                "type": "spawn_agent",
                "config": {
                    "agent_type": "researcher",
                    "prompt_template": "{task.title}\n\n{task.notes}",
                    "timeout_seconds": 900,
                },
                "on_success": "cleanup",
                "on_failure": {"retry": 1, "abort_on": ["spawn_error"]},
            },
            {
                "id": "cleanup",
                "type": "cleanup",
                "config": {"delete_session": True},
            },
        ],
        "edges": [],
        "metadata": {"author": "lobs", "category": "research"},
    },
    # ── System Workflows (recurring/event-driven) ────────────────────
    {
        "name": "reflection-cycle",
        "description": "Full strategic reflection pipeline: list agents → build context → spawn reflections → wait → sweep → notify. Replaces the hardcoded engine reflection logic.",
        "trigger": {"type": "schedule", "cron": "0 */6 * * *", "timezone": "America/New_York"},
        "is_active": False,
        "nodes": [
            {
                "id": "list_agents",
                "type": "python_call",
                "config": {"callable": "reflection.list_agents"},
                "on_success": "check_agents",
                "on_failure": {"retry": 1, "abort_on": ["python_error"]},
            },
            {
                "id": "check_agents",
                "type": "branch",
                "config": {
                    "conditions": [
                        {"match": "list_agents.count == 0", "goto": "done"},
                    ],
                    "default": "build_contexts",
                },
            },
            {
                "id": "build_contexts",
                "type": "python_call",
                "config": {"callable": "reflection.build_contexts"},
                "on_success": "spawn_agents",
                "on_failure": {"retry": 1, "abort_on": ["python_error"]},
            },
            {
                "id": "spawn_agents",
                "type": "python_call",
                "config": {"callable": "reflection.spawn_agents"},
                "on_success": "check_spawned",
                "on_failure": {"retry": 1, "abort_on": ["python_error"]},
            },
            {
                "id": "check_spawned",
                "type": "branch",
                "config": {
                    "conditions": [
                        {"match": "spawn_agents.spawned == 0", "goto": "done"},
                    ],
                    "default": "wait_for_completion",
                },
            },
            {
                "id": "wait_for_completion",
                "type": "python_call",
                "config": {
                    "callable": "reflection.check_complete",
                    "poll": True,
                },
                "on_success": "run_sweep",
                "on_failure": {"retry": 3, "abort_on": ["python_error"]},
            },
            {
                "id": "run_sweep",
                "type": "python_call",
                "config": {"callable": "reflection.run_sweep"},
                "on_success": "notify_sweep",
                "on_failure": {"retry": 2},
            },
            {
                "id": "notify_sweep",
                "type": "notify",
                "config": {
                    "channel": "internal",
                    "message_template": "Reflection cycle complete. Spawned {spawn_agents.spawned} agents. Sweep: {run_sweep.proposed} proposed, {run_sweep.rejected} rejected, {run_sweep.llm_review} pending review.",
                },
                "on_success": "emit_complete",
            },
            {
                "id": "emit_complete",
                "type": "notify",
                "config": {
                    "channel": "internal",
                    "message_template": "reflection.batch_complete",
                },
                "on_success": "done",
            },
            {"id": "done", "type": "cleanup", "config": {"delete_session": False}},
        ],
        "edges": [],
        "metadata": {"author": "lobs", "category": "system", "system": True},
    },
    {
        "name": "diagnostic-scan",
        "description": "Detect stalls, failures, idle agents, performance drops, repo drift. Spawn targeted diagnostics.",
        "trigger": {"type": "schedule", "cron": "*/30 * * * *", "timezone": "UTC"},
        "is_active": False,
        "nodes": [
            {
                "id": "run_diagnostics",
                "type": "python_call",
                "config": {"callable": "diagnostics.run_once"},
                "on_success": "check_results",
                "on_failure": {"retry": 1, "abort_on": ["python_error"]},
            },
            {
                "id": "check_results",
                "type": "branch",
                "config": {
                    "conditions": [
                        {"match": "run_diagnostics.spawned != 0", "goto": "notify"},
                    ],
                    "default": "done",
                },
            },
            {
                "id": "notify",
                "type": "notify",
                "config": {
                    "channel": "internal",
                    "message_template": "Diagnostics: {run_diagnostics.triggers} triggers, {run_diagnostics.fired} fired, {run_diagnostics.spawned} spawned, {run_diagnostics.suppressed} suppressed.",
                },
                "on_success": "done",
            },
            {"id": "done", "type": "cleanup", "config": {"delete_session": False}},
        ],
        "edges": [],
        "metadata": {"author": "lobs", "category": "system", "system": True},
    },
    {
        "name": "daily-compression",
        "description": "Compress agent reflections into versioned identity snapshots with validation gate.",
        "trigger": {"type": "schedule", "cron": "0 4 * * *", "timezone": "America/New_York"},
        "is_active": False,
        "nodes": [
            {
                "id": "compress",
                "type": "python_call",
                "config": {"callable": "reflection.run_compression"},
                "on_success": "check_results",
                "on_failure": {"retry": 1},
            },
            {
                "id": "check_results",
                "type": "branch",
                "config": {
                    "conditions": [
                        {"match": "compress.validation_failures != 0", "goto": "notify_failures"},
                    ],
                    "default": "notify_success",
                },
            },
            {
                "id": "notify_failures",
                "type": "notify",
                "config": {
                    "channel": "internal",
                    "message_template": "Daily compression: {compress.rewritten} rewritten, {compress.validation_failures} validation failures.",
                },
                "on_success": "done",
            },
            {
                "id": "notify_success",
                "type": "notify",
                "config": {
                    "channel": "internal",
                    "message_template": "Daily compression complete: {compress.rewritten} identities updated across {compress.agents} agents.",
                },
                "on_success": "done",
            },
            {"id": "done", "type": "cleanup", "config": {"delete_session": False}},
        ],
        "edges": [],
        "metadata": {"author": "lobs", "category": "system", "system": True},
    },
    {
        "name": "scheduled-events",
        "description": "Fire due calendar scheduled events and create tasks from them.",
        "trigger": {"type": "schedule", "cron": "* * * * *", "timezone": "UTC"},
        "is_active": False,
        "nodes": [
            {
                "id": "fire_events",
                "type": "python_call",
                "config": {"callable": "scheduler.fire_due_events"},
                "on_success": "done",
                "on_failure": {"retry": 1},
            },
            {"id": "done", "type": "cleanup", "config": {"delete_session": False}},
        ],
        "edges": [],
        "metadata": {"author": "lobs", "category": "system", "system": True},
    },
    {
        "name": "github-sync",
        "description": "Sync GitHub issues and PRs for all tracked projects.",
        "trigger": {"type": "schedule", "cron": "*/15 * * * *", "timezone": "UTC"},
        "is_active": False,
        "nodes": [
            {
                "id": "sync",
                "type": "python_call",
                "config": {"callable": "github_sync.sync_all"},
                "on_success": "done",
                "on_failure": {"retry": 2, "abort_on": ["timeout"]},
            },
            {"id": "done", "type": "cleanup", "config": {"delete_session": False}},
        ],
        "edges": [],
        "metadata": {"author": "lobs", "category": "system", "system": True},
    },
    {
        "name": "memory-sync",
        "description": "Sync agent memory files from disk to database.",
        "trigger": {"type": "schedule", "cron": "0 * * * *", "timezone": "UTC"},
        "is_active": False,
        "nodes": [
            {
                "id": "sync",
                "type": "python_call",
                "config": {"callable": "memory_sync.sync_all"},
                "on_success": "done",
                "on_failure": {"retry": 1},
            },
            {"id": "done", "type": "cleanup", "config": {"delete_session": False}},
        ],
        "edges": [],
        "metadata": {"author": "lobs", "category": "system", "system": True},
    },
]
