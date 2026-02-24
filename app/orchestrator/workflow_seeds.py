"""Seed default workflow definitions on first startup."""

import uuid
import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import WorkflowDefinition

logger = logging.getLogger(__name__)


async def seed_default_workflows(db: AsyncSession) -> int:
    """Create default workflows if they don't exist. Returns count created."""
    created = 0

    for defn in DEFAULT_WORKFLOWS:
        result = await db.execute(
            select(WorkflowDefinition).where(WorkflowDefinition.name == defn["name"])
        )
        if result.scalar_one_or_none():
            continue

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
        db.add(wf)
        created += 1
        logger.info("[WORKFLOW_SEED] Created workflow: %s", defn["name"])

    if created:
        await db.commit()
    return created


DEFAULT_WORKFLOWS = [
    {
        "name": "code-task",
        "description": "Standard code task: spawn programmer, run tests, fix failures, lint, commit, cleanup.",
        "trigger": {"type": "task_match", "agent_types": ["programmer"]},
        "is_active": False,  # Opt-in — enable via API when ready
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
        "description": "Simple research task: spawn researcher, deliver results, cleanup.",
        "trigger": {"type": "task_match", "agent_types": ["researcher"]},
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
