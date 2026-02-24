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
]
