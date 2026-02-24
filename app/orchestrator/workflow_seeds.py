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
            # Route to the correct agent-specific spawn node.
            # Each spawn_agent node delegates to WorkerManager which handles:
            # - Model selection (ModelChooser with fallback chain + provider health)
            # - Prompt building (Prompter with learning enhancement)
            # - Worker tracking (active_workers, project_locks)
            # - On completion: task status, agent tracker, circuit breaker,
            #   git auto-commit, failure escalation, provider health recording
            {
                "id": "route_by_agent",
                "type": "branch",
                "config": {
                    "conditions": [
                        {"match": "task.agent == programmer", "goto": "spawn_programmer"},
                        {"match": "task.agent == researcher", "goto": "spawn_researcher"},
                        {"match": "task.agent == writer", "goto": "spawn_writer"},
                        {"match": "task.agent == architect", "goto": "spawn_architect"},
                        {"match": "task.agent == reviewer", "goto": "spawn_reviewer"},
                        {"match": "task.agent == inbox-responder", "goto": "spawn_inbox"},
                    ],
                    "default": "spawn_generic",
                },
            },
            # ── Agent spawn nodes ────────────────────────────────────
            # Each delegates to WorkerManager.spawn_worker() which builds
            # the full prompt, selects the model, and handles completion.
            {
                "id": "spawn_programmer",
                "type": "spawn_agent",
                "config": {"agent_type": "programmer"},
                "on_success": "run_tests_1",
                "on_failure": {"retry": 1, "abort_on": ["spawn_error"]},
            },
            {
                "id": "run_tests_1",
                "type": "tool_call",
                "config": {
                    "command": "cd {project.repo_path} && if [ -d tests ] || ls *test*.py >/dev/null 2>&1; then python -m pytest --tb=short 2>&1; else echo 'No tests detected; skipping pytest'; fi",
                    "timeout_seconds": 300,
                },
                "on_success": "tests_gate_1",
                "on_failure": {"retry": 0},
            },
            {
                "id": "tests_gate_1",
                "type": "branch",
                "config": {
                    "conditions": [
                        {"match": "run_tests_1.returncode == 0", "goto": "done"},
                    ],
                    "default": "spawn_programmer_fix_1",
                },
            },
            {
                "id": "spawn_programmer_fix_1",
                "type": "spawn_agent",
                "config": {
                    "agent_type": "programmer",
                    "prompt_template": "Previous implementation failed project tests. Fix the failures and update code only as needed.\n\nTask: {task.title}\n\nOriginal notes:\n{task.notes}\n\nLatest test output:\n{run_tests_1.stdout}\n{run_tests_1.stderr}",
                },
                "on_success": "run_tests_2",
                "on_failure": {"retry": 0, "abort_on": ["spawn_error"]},
            },
            {
                "id": "run_tests_2",
                "type": "tool_call",
                "config": {
                    "command": "cd {project.repo_path} && if [ -d tests ] || ls *test*.py >/dev/null 2>&1; then python -m pytest --tb=short 2>&1; else echo 'No tests detected; skipping pytest'; fi",
                    "timeout_seconds": 300,
                },
                "on_success": "tests_gate_2",
                "on_failure": {"retry": 0},
            },
            {
                "id": "tests_gate_2",
                "type": "branch",
                "config": {
                    "conditions": [
                        {"match": "run_tests_2.returncode == 0", "goto": "done"},
                    ],
                    "default": "spawn_programmer_fix_2",
                },
            },
            {
                "id": "spawn_programmer_fix_2",
                "type": "spawn_agent",
                "config": {
                    "agent_type": "programmer",
                    "prompt_template": "Tests are still failing after one fix attempt. Make a second and final repair pass.\n\nTask: {task.title}\n\nOriginal notes:\n{task.notes}\n\nLatest test output:\n{run_tests_2.stdout}\n{run_tests_2.stderr}",
                },
                "on_success": "run_tests_3",
                "on_failure": {"retry": 0, "abort_on": ["spawn_error"]},
            },
            {
                "id": "run_tests_3",
                "type": "tool_call",
                "config": {
                    "command": "cd {project.repo_path} && if [ -d tests ] || ls *test*.py >/dev/null 2>&1; then python -m pytest --tb=short 2>&1; else echo 'No tests detected; skipping pytest'; fi",
                    "timeout_seconds": 300,
                },
                "on_success": "tests_gate_3",
                "on_failure": {"retry": 0},
            },
            {
                "id": "tests_gate_3",
                "type": "branch",
                "config": {
                    "conditions": [
                        {"match": "run_tests_3.returncode == 0", "goto": "done"},
                    ],
                    "default": "tests_failed_terminal",
                },
            },
            {
                "id": "tests_failed_terminal",
                "type": "gate",
                "config": {
                    "prompt": "Programmer task failed tests after max retries (2). Manual intervention required.",
                    "timeout_hours": 24,
                },
                "on_success": "done",
            },
            {
                "id": "spawn_researcher",
                "type": "spawn_agent",
                "config": {"agent_type": "researcher"},
                "on_success": "done",
                "on_failure": {"retry": 1, "abort_on": ["spawn_error"]},
            },
            {
                "id": "spawn_writer",
                "type": "spawn_agent",
                "config": {"agent_type": "writer"},
                "on_success": "done",
                "on_failure": {"retry": 1, "abort_on": ["spawn_error"]},
            },
            {
                "id": "spawn_architect",
                "type": "spawn_agent",
                "config": {"agent_type": "architect", "model_tier": "strong"},
                "on_success": "done",
                "on_failure": {"retry": 1, "abort_on": ["spawn_error"]},
            },
            {
                "id": "spawn_reviewer",
                "type": "spawn_agent",
                "config": {"agent_type": "reviewer"},
                "on_success": "done",
                "on_failure": {"retry": 1, "abort_on": ["spawn_error"]},
            },
            {
                "id": "spawn_inbox",
                "type": "spawn_agent",
                "config": {"agent_type": "inbox-responder", "model_tier": "medium"},
                "on_success": "done",
                "on_failure": {"retry": 0, "abort_on": ["spawn_error"]},
            },
            {
                "id": "spawn_generic",
                "type": "spawn_agent",
                "config": {"agent_type": "programmer"},
                "on_success": "done",
                "on_failure": {"retry": 1, "abort_on": ["spawn_error"]},
            },
            # ── Terminal ─────────────────────────────────────────────
            # WorkerManager already handled task completion, git commit,
            # escalation, etc. We just close the workflow run.
            {
                "id": "done",
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
    # ══════════════════════════════════════════════════════════════════
    # CALENDAR SYNC — Google Calendar → internal calendar
    # ══════════════════════════════════════════════════════════════════
    {
        "name": "calendar-sync",
        "description": "Sync Google Calendar events to internal calendar and check upcoming events for alerts.",
        "trigger": {"type": "schedule", "cron": "*/15 * * * *", "timezone": "America/New_York"},
        "is_active": True,
        "nodes": [
            {
                "id": "sync",
                "type": "python_call",
                "config": {"callable": "calendar.sync_google"},
                "on_success": "check_upcoming",
                "on_failure": {"retry": 1},
            },
            {
                "id": "check_upcoming",
                "type": "python_call",
                "config": {"callable": "calendar.check_upcoming"},
                "on_success": "check_alerts",
                "on_failure": {"retry": 0},
            },
            {
                "id": "check_alerts",
                "type": "branch",
                "config": {
                    "conditions": [
                        {"match": "check_upcoming.alerts > 0", "goto": "notify_alerts"},
                    ],
                    "default": "done",
                },
            },
            {
                "id": "notify_alerts",
                "type": "notify",
                "config": {
                    "channel": "internal",
                    "message_template": "📅 {check_upcoming.alerts} upcoming calendar alerts in next 24h",
                },
                "on_success": "done",
            },
            {"id": "done", "type": "cleanup", "config": {"delete_session": False}},
        ],
        "edges": [],
        "metadata": {"author": "lobs", "category": "integration", "system": True},
    },
    # ══════════════════════════════════════════════════════════════════
    # EMAIL CHECK — Monitor inbox for important emails
    # ══════════════════════════════════════════════════════════════════
    {
        "name": "email-check",
        "description": "Check email inbox for unread messages and create inbox items for important ones.",
        "trigger": {"type": "schedule", "cron": "*/30 * * * *", "timezone": "America/New_York"},
        "is_active": True,
        "nodes": [
            {
                "id": "check",
                "type": "python_call",
                "config": {"callable": "email.check_inbox"},
                "on_success": "check_results",
                "on_failure": {"retry": 1},
            },
            {
                "id": "check_results",
                "type": "branch",
                "config": {
                    "conditions": [
                        {"match": "check.actioned > 0", "goto": "notify"},
                    ],
                    "default": "done",
                },
            },
            {
                "id": "notify",
                "type": "notify",
                "config": {
                    "channel": "internal",
                    "message_template": "📧 {check.actioned} new emails added to inbox",
                },
                "on_success": "done",
            },
            {"id": "done", "type": "cleanup", "config": {"delete_session": False}},
        ],
        "edges": [],
        "metadata": {"author": "lobs", "category": "integration", "system": True},
    },
    # ══════════════════════════════════════════════════════════════════
    # WORK TRACKER — Deadline monitoring + daily summaries
    # ══════════════════════════════════════════════════════════════════
    {
        "name": "tracker-deadlines",
        "description": "Check approaching deadlines every 30 minutes and send reminders.",
        "trigger": {"type": "schedule", "cron": "*/30 * * * *", "timezone": "America/New_York"},
        "is_active": True,
        "nodes": [
            {
                "id": "check",
                "type": "python_call",
                "config": {"callable": "tracker.check_deadlines"},
                "on_success": "check_results",
                "on_failure": {"retry": 1},
            },
            {
                "id": "check_results",
                "type": "branch",
                "config": {
                    "conditions": [
                        {"match": "check.notified > 0", "goto": "notify"},
                    ],
                    "default": "done",
                },
            },
            {
                "id": "notify",
                "type": "notify",
                "config": {
                    "channel": "internal",
                    "message_template": "⏰ {check.notified} deadline reminders sent",
                },
                "on_success": "done",
            },
            {"id": "done", "type": "cleanup", "config": {"delete_session": False}},
        ],
        "edges": [],
        "metadata": {"author": "lobs", "category": "tracker", "system": True},
    },
    {
        "name": "tracker-daily-summary",
        "description": "Generate daily work summary from tracker entries at 7am ET.",
        "trigger": {"type": "schedule", "cron": "0 7 * * *", "timezone": "America/New_York"},
        "is_active": True,
        "nodes": [
            {
                "id": "summary",
                "type": "python_call",
                "config": {"callable": "tracker.daily_summary"},
                "on_success": "check_results",
                "on_failure": {"retry": 1},
            },
            {
                "id": "check_results",
                "type": "branch",
                "config": {
                    "conditions": [
                        {"match": "summary.summary_created == true", "goto": "notify"},
                    ],
                    "default": "done",
                },
            },
            {
                "id": "notify",
                "type": "notify",
                "config": {
                    "channel": "internal",
                    "message_template": "📊 Daily summary: {summary.total_minutes}min across {summary.sessions} sessions, {summary.deadlines_today} deadlines today",
                },
                "on_success": "done",
            },
            {"id": "done", "type": "cleanup", "config": {"delete_session": False}},
        ],
        "edges": [],
        "metadata": {"author": "lobs", "category": "tracker", "system": True},
    },
    # ══════════════════════════════════════════════════════════════════
    # DAILY LEARNING — Generate and deliver lessons from active plans
    # ══════════════════════════════════════════════════════════════════
    {
        "name": "daily-learning",
        "description": "Check active learning plans and generate+deliver the next lesson. Runs daily at 7am ET. Reusable for any topic — LLMs, distributed systems, cooking, etc.",
        "trigger": {"type": "schedule", "cron": "0 7 * * *", "timezone": "America/New_York"},
        "is_active": True,
        "nodes": [
            {
                "id": "check_plans",
                "type": "python_call",
                "config": {"callable": "learning.check_due"},
                "on_success": "check_results",
                "on_failure": {"retry": 1},
            },
            {
                "id": "check_results",
                "type": "branch",
                "config": {
                    "conditions": [
                        {"match": "check_plans.lessons_generated > 0", "goto": "deliver"},
                    ],
                    "default": "done",
                },
            },
            {
                "id": "deliver",
                "type": "notify",
                "config": {
                    "channel": "discord",
                    "message_template": "📚 **Daily Lesson**\n\n{check_plans.lessons[0].content_preview}...\n\n📄 Full lesson saved to: `{check_plans.lessons[0].document_path}`",
                },
                "on_success": "done",
            },
            {"id": "done", "type": "cleanup", "config": {"delete_session": False}},
        ],
        "edges": [],
        "metadata": {"author": "lobs", "category": "learning", "system": True},
    },
    # ══════════════════════════════════════════════════════════════════
    # LEARNING PLAN CREATION — Event-triggered, creates outline via LLM
    # ══════════════════════════════════════════════════════════════════
    {
        "name": "create-learning-plan",
        "description": "Create a new learning plan when requested. Generates a full day-by-day outline via LLM. Triggered by learning.plan_requested event.",
        "trigger": {"type": "event", "event_pattern": "learning.plan_requested"},
        "is_active": True,
        "nodes": [
            {
                "id": "create",
                "type": "python_call",
                "config": {
                    "callable": "learning.create_plan",
                    "args_template": {
                        "topic": "{trigger.topic}",
                        "goal": "{trigger.goal}",
                        "total_days": "{trigger.total_days}",
                    },
                },
                "on_success": "check_result",
                "on_failure": {"retry": 1},
            },
            {
                "id": "check_result",
                "type": "branch",
                "config": {
                    "conditions": [
                        {"match": "create.status == ok", "goto": "notify_created"},
                    ],
                    "default": "notify_failed",
                },
            },
            {
                "id": "notify_created",
                "type": "notify",
                "config": {
                    "channel": "discord",
                    "message_template": "📚 Learning plan created: **{create.topic}** ({create.total_days} days)\n\nOutline generated with {create.outline_days} lessons. First lesson delivers tomorrow at 7am!",
                },
                "on_success": "done",
            },
            {
                "id": "notify_failed",
                "type": "notify",
                "config": {
                    "channel": "internal",
                    "message_template": "Failed to create learning plan: {create.error}",
                },
                "on_success": "done",
            },
            {"id": "done", "type": "cleanup", "config": {"delete_session": False}},
        ],
        "edges": [],
        "metadata": {"author": "lobs", "category": "learning", "system": True},
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
