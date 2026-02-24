"""Workflow executor — advances workflow runs one step at a time.

Called by the engine on each tick. Each call to `advance()` progresses
a single run by at most one node transition, keeping the engine loop
responsive.
"""

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    WorkflowDefinition,
    WorkflowRun,
    WorkflowEvent,
    WorkflowSubscription,
    Task,
    Project,
)
from app.orchestrator.workflow_nodes import NodeHandlers, NodeResult

logger = logging.getLogger(__name__)


class WorkflowExecutor:
    """Drives workflow runs through their DAG one step per tick."""

    def __init__(self, db: AsyncSession, worker_manager: Any = None):
        self.db = db
        self.worker_manager = worker_manager
        self.node_handlers = NodeHandlers(db, worker_manager=worker_manager)

    # ── Public API ───────────────────────────────────────────────────

    async def get_active_runs(self, limit: int = 20) -> list[WorkflowRun]:
        """Return runs that need advancement."""
        result = await self.db.execute(
            select(WorkflowRun)
            .where(WorkflowRun.status.in_(["pending", "running"]))
            .order_by(WorkflowRun.created_at)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def advance(self, run: WorkflowRun) -> bool:
        """Advance *run* by at most one step.  Returns True if work was done."""
        try:
            workflow = await self.db.get(WorkflowDefinition, run.workflow_id)
            if not workflow:
                await self._finish_run(run, "failed", error="Workflow definition not found")
                return True

            nodes_by_id = {n["id"]: n for n in (workflow.nodes or [])}

            # ── Bootstrap ────────────────────────────────────────────
            if run.status == "pending":
                run.status = "running"
                run.started_at = datetime.now(timezone.utc)
                # Find entry node (first node, or one with no incoming edges)
                entry = self._find_entry_node(workflow)
                if not entry:
                    await self._finish_run(run, "failed", error="No entry node found")
                    return True
                run.current_node = entry
                run.updated_at = datetime.now(timezone.utc)
                await self.db.commit()
                return True

            # ── Main step ────────────────────────────────────────────
            node_id = run.current_node
            if not node_id:
                await self._finish_run(run, "completed")
                return True

            node_def = nodes_by_id.get(node_id)
            if not node_def:
                await self._finish_run(run, "failed", error=f"Node {node_id} not found in definition")
                return True

            node_states = dict(run.node_states or {})
            ns = node_states.get(node_id, {})
            status = ns.get("status", "pending")

            if status == "pending":
                return await self._start_node(run, node_def, node_states)

            if status == "running":
                return await self._check_node(run, node_def, node_states)

            if status == "completed":
                return await self._transition(run, node_def, node_states, workflow)

            if status == "failed":
                return await self._handle_failure(run, node_def, node_states, workflow)

            # Unknown status — abort
            await self._finish_run(run, "failed", error=f"Unknown node status: {status}")
            return True

        except Exception as e:
            logger.error("[WORKFLOW] Error advancing run %s: %s", run.id[:8], e, exc_info=True)
            try:
                await self._finish_run(run, "failed", error=str(e))
            except Exception:
                pass
            return True

    async def start_run(
        self,
        workflow: WorkflowDefinition,
        *,
        task: Optional[dict[str, Any]] = None,
        trigger_type: str = "manual",
        trigger_payload: Optional[dict[str, Any]] = None,
    ) -> WorkflowRun:
        """Create and persist a new workflow run."""
        context: dict[str, Any] = {}
        task_id = None

        if task:
            task_id = task.get("id")

            # Dedup: don't create a new run if one already exists for this task
            if task_id:
                existing_result = await self.db.execute(
                    select(WorkflowRun).where(
                        WorkflowRun.task_id == task_id,
                        WorkflowRun.status.in_(["pending", "running"]),
                    )
                )
                existing_run = existing_result.scalar_one_or_none()
                if existing_run:
                    logger.debug(
                        "[WORKFLOW] Skipping duplicate run for task %s — active run exists",
                        task_id[:8],
                    )
                    return existing_run

            context["task"] = task
            # Resolve project info
            project_id = task.get("project_id")
            if project_id:
                project = await self.db.get(Project, project_id)
                if project:
                    context["project"] = {
                        "id": project.id,
                        "title": project.title,
                        "repo_path": project.repo_path or "",
                    }

        if trigger_payload:
            context["trigger"] = trigger_payload

        run = WorkflowRun(
            id=str(uuid.uuid4()),
            workflow_id=workflow.id,
            workflow_version=workflow.version,
            task_id=task_id,
            trigger_type=trigger_type,
            trigger_payload=trigger_payload,
            status="pending",
            node_states={},
            context=context,
        )
        self.db.add(run)

        # Update task work_state if linked
        if task_id:
            db_task = await self.db.get(Task, task_id)
            if db_task:
                db_task.work_state = "in_progress"
                db_task.updated_at = datetime.now(timezone.utc)

        await self.db.commit()
        logger.info(
            "[WORKFLOW] Started run %s for workflow '%s' (trigger=%s, task=%s)",
            run.id[:8], workflow.name, trigger_type, (task_id or "none")[:8],
        )
        return run

    async def match_workflow(self, task: dict[str, Any]) -> Optional[WorkflowDefinition]:
        """Find a workflow definition that matches a task (by trigger config)."""
        result = await self.db.execute(
            select(WorkflowDefinition).where(
                WorkflowDefinition.is_active == True,
            )
        )
        workflows = result.scalars().all()

        for wf in workflows:
            trigger = wf.trigger
            if not trigger:
                continue
            if trigger.get("type") == "task_match":
                agent_types = trigger.get("agent_types", [])
                if task.get("agent") in agent_types:
                    return wf
        return None

    async def process_events(self, limit: int = 10) -> int:
        """Process unhandled workflow events, starting matching runs."""
        result = await self.db.execute(
            select(WorkflowEvent)
            .where(WorkflowEvent.processed == False)
            .order_by(WorkflowEvent.created_at)
            .limit(limit)
        )
        events = result.scalars().all()
        started = 0

        for event in events:
            subs = await self._match_subscriptions(event)
            for sub in subs:
                workflow = await self.db.get(WorkflowDefinition, sub.workflow_id)
                if workflow and workflow.is_active:
                    await self.start_run(
                        workflow,
                        trigger_type="event",
                        trigger_payload={"event_id": event.id, "event_type": event.event_type, **(event.payload or {})},
                    )
                    started += 1
            event.processed = True

        if started:
            await self.db.commit()
        return started

    async def process_schedules(self) -> int:
        """Check schedule-triggered workflows and start runs when due.

        Uses a simple last-run tracking approach: for each schedule-triggered
        workflow, check if enough time has elapsed since its last run started.
        Cron expressions are evaluated against the current time.
        """
        try:
            from croniter import croniter
        except ImportError:
            logger.debug("[WORKFLOW] croniter not installed — schedule triggers disabled")
            return 0

        result = await self.db.execute(
            select(WorkflowDefinition).where(
                WorkflowDefinition.is_active == True,
            )
        )
        workflows = result.scalars().all()
        started = 0

        now = datetime.now(timezone.utc)

        for wf in workflows:
            trigger = wf.trigger
            if not trigger or trigger.get("type") != "schedule":
                continue

            cron_expr = trigger.get("cron")
            if not cron_expr:
                continue

            tz_name = trigger.get("timezone", "UTC")
            try:
                from zoneinfo import ZoneInfo
                tz = ZoneInfo(tz_name)
            except Exception:
                tz = timezone.utc

            now_local = now.astimezone(tz)

            # Check if there's already an active run for this workflow
            active_q = await self.db.execute(
                select(WorkflowRun).where(
                    WorkflowRun.workflow_id == wf.id,
                    WorkflowRun.status.in_(["pending", "running"]),
                ).limit(1)
            )
            if active_q.scalar_one_or_none():
                continue  # Already running

            # Find last completed/failed run
            last_run_q = await self.db.execute(
                select(WorkflowRun).where(
                    WorkflowRun.workflow_id == wf.id,
                ).order_by(WorkflowRun.created_at.desc()).limit(1)
            )
            last_run = last_run_q.scalar_one_or_none()

            # Use croniter to check if we're due
            try:
                cron = croniter(cron_expr, now_local)
                prev_fire = cron.get_prev(datetime)

                if last_run and last_run.created_at:
                    last_created = last_run.created_at
                    if last_created.tzinfo is None:
                        last_created = last_created.replace(tzinfo=timezone.utc)
                    # Only fire if prev_fire is after last run
                    if prev_fire <= last_created:
                        continue

                # Due — start a run
                await self.start_run(
                    wf,
                    trigger_type="schedule",
                    trigger_payload={"cron": cron_expr, "fired_at": now.isoformat()},
                )
                started += 1
                logger.info("[WORKFLOW] Schedule fired for '%s' (cron=%s)", wf.name, cron_expr)

            except Exception as e:
                logger.warning("[WORKFLOW] Cron evaluation failed for '%s': %s", wf.name, e)

        return started

    async def emit_event(self, event_type: str, payload: dict[str, Any], source: str = "internal") -> str:
        """Emit a workflow event to the event bus."""
        event = WorkflowEvent(
            id=str(uuid.uuid4()),
            event_type=event_type,
            payload=payload,
            source=source,
        )
        self.db.add(event)
        await self.db.commit()
        return event.id

    # ── Internal ─────────────────────────────────────────────────────

    async def _start_node(self, run: WorkflowRun, node_def: dict, node_states: dict) -> bool:
        node_id = node_def["id"]
        ns = node_states.get(node_id, {"attempts": 0})
        ns["attempts"] = ns.get("attempts", 0) + 1
        ns["started_at"] = datetime.now(timezone.utc).isoformat()

        try:
            result = await self.node_handlers.execute(node_def, run)
            ns["status"] = result.status
            if result.output:
                ns["output"] = result.output
                # Merge output into run context
                ctx = dict(run.context or {})
                ctx[node_id] = result.output
                run.context = ctx
            if result.error:
                ns["error"] = result.error
                ns["error_type"] = result.error_type
            if result.session_key:
                # Store session key for the run
                ctx = dict(run.context or {})
                ctx[f"{node_id}.session_key"] = result.session_key
                run.context = ctx
                if not run.session_key:
                    run.session_key = result.session_key
        except Exception as e:
            ns["status"] = "failed"
            ns["error"] = str(e)
            logger.error("[WORKFLOW] Node %s execution error: %s", node_id, e, exc_info=True)

        node_states[node_id] = ns
        run.node_states = node_states
        run.updated_at = datetime.now(timezone.utc)
        await self.db.commit()
        return True

    async def _check_node(self, run: WorkflowRun, node_def: dict, node_states: dict) -> bool:
        """Poll a running node for completion."""
        node_id = node_def["id"]
        ns = node_states.get(node_id, {})

        try:
            result = await self.node_handlers.check(node_def, run)
            if result is None:
                return False  # Still running, nothing to do

            ns["status"] = result.status
            if result.output:
                ns["output"] = result.output
                ctx = dict(run.context or {})
                ctx[node_id] = result.output
                run.context = ctx
            if result.error:
                ns["error"] = result.error
            ns["finished_at"] = datetime.now(timezone.utc).isoformat()
        except Exception as e:
            ns["status"] = "failed"
            ns["error"] = str(e)

        node_states[node_id] = ns
        run.node_states = node_states
        run.updated_at = datetime.now(timezone.utc)
        await self.db.commit()
        return True

    async def _transition(self, run: WorkflowRun, node_def: dict, node_states: dict, workflow: WorkflowDefinition) -> bool:
        """Move to the next node after successful completion."""
        ns = node_states.get(node_def["id"], {})
        output = ns.get("output", {})

        # Branch nodes specify their own routing
        goto = output.get("goto") if isinstance(output, dict) else None

        # Otherwise use on_success or edges
        if not goto:
            goto = node_def.get("on_success")

        if not goto:
            # Check edges
            for edge in (workflow.edges or []):
                if edge.get("from") == node_def["id"]:
                    condition = edge.get("condition")
                    if not condition or self._evaluate_simple_condition(condition, run.context):
                        goto = edge["to"]
                        break

        if goto:
            run.current_node = goto
            run.updated_at = datetime.now(timezone.utc)
            await self.db.commit()
            return True

        # No next node — workflow complete
        await self._finish_run(run, "completed")
        return True

    async def _handle_failure(self, run: WorkflowRun, node_def: dict, node_states: dict, workflow: WorkflowDefinition) -> bool:
        """Apply per-node failure policy: retry → fallback → escalate → abort."""
        node_id = node_def["id"]
        ns = node_states.get(node_id, {})
        attempts = ns.get("attempts", 1)
        error_type = ns.get("error_type", "")

        policy = node_def.get("on_failure", {})

        # Abort conditions
        abort_on = policy.get("abort_on", [])
        if error_type in abort_on:
            logger.warning("[WORKFLOW] Node %s hit abort condition '%s'", node_id, error_type)
            await self._finish_run(run, "failed", error=ns.get("error", "Abort condition met"))
            return True

        # Retry
        max_retries = policy.get("retry", 0)
        if attempts <= max_retries:
            logger.info("[WORKFLOW] Retrying node %s (attempt %d/%d)", node_id, attempts + 1, max_retries + 1)
            ns["status"] = "pending"
            node_states[node_id] = ns
            run.node_states = node_states
            run.updated_at = datetime.now(timezone.utc)
            await self.db.commit()
            return True

        # Fallback node
        fallback = policy.get("fallback")
        escalate_after = policy.get("escalate_after", 999)
        if fallback and attempts <= escalate_after:
            logger.info("[WORKFLOW] Falling back from %s to %s", node_id, fallback)
            run.current_node = fallback
            run.updated_at = datetime.now(timezone.utc)
            await self.db.commit()
            return True

        # Escalate — finish as failed, create inbox item
        logger.warning("[WORKFLOW] Node %s exhausted retries, escalating", node_id)
        await self._finish_run(run, "failed", error=ns.get("error", "All retries exhausted"))
        return True

    async def _finish_run(self, run: WorkflowRun, status: str, error: Optional[str] = None) -> None:
        """Mark a run as terminal (completed/failed/cancelled)."""
        run.status = status
        run.error = error
        run.finished_at = datetime.now(timezone.utc)
        run.current_node = None
        run.updated_at = datetime.now(timezone.utc)

        # Update linked task
        if run.task_id:
            db_task = await self.db.get(Task, run.task_id)
            if db_task:
                if status == "completed":
                    db_task.status = "completed"
                    db_task.work_state = "completed"
                    db_task.finished_at = datetime.now(timezone.utc)
                elif status == "failed":
                    db_task.work_state = "blocked"
                    db_task.failure_reason = error
                db_task.updated_at = datetime.now(timezone.utc)

        # Emit completion event
        self.db.add(WorkflowEvent(
            id=str(uuid.uuid4()),
            event_type=f"workflow.{status}",
            payload={"run_id": run.id, "workflow_id": run.workflow_id, "task_id": run.task_id, "error": error},
            source="workflow_executor",
        ))

        await self.db.commit()
        logger.info("[WORKFLOW] Run %s finished: %s%s", run.id[:8], status, f" ({error[:80]})" if error else "")

        # Cleanup session if present
        if run.session_key and status in ("completed", "failed"):
            try:
                await self.node_handlers.delete_session(run.session_key)
            except Exception as e:
                logger.debug("[WORKFLOW] Session cleanup failed: %s", e)

    async def _match_subscriptions(self, event: WorkflowEvent) -> list[WorkflowSubscription]:
        """Find active subscriptions matching an event."""
        result = await self.db.execute(
            select(WorkflowSubscription).where(WorkflowSubscription.is_active == True)
        )
        subs = result.scalars().all()
        matched = []
        for sub in subs:
            if self._pattern_matches(sub.event_pattern, event.event_type):
                if self._filter_matches(sub.filter_conditions, event.payload):
                    matched.append(sub)
        return matched

    @staticmethod
    def _find_entry_node(workflow: WorkflowDefinition) -> Optional[str]:
        """Find the entry node — first node with no incoming edges."""
        nodes = workflow.nodes or []
        edges = workflow.edges or []
        if not nodes:
            return None

        targets = {e["to"] for e in edges if "to" in e}
        for node in nodes:
            if node["id"] not in targets:
                return node["id"]
        # Fallback: first node
        return nodes[0]["id"]

    @staticmethod
    def _pattern_matches(pattern: str, event_type: str) -> bool:
        """Simple glob matching for event patterns."""
        # Convert glob to regex
        regex = pattern.replace(".", r"\.").replace("*", ".*")
        return bool(re.fullmatch(regex, event_type))

    @staticmethod
    def _filter_matches(conditions: Optional[dict], payload: Optional[dict]) -> bool:
        if not conditions:
            return True
        if not payload:
            return False
        for key, value in conditions.items():
            if payload.get(key) != value:
                return False
        return True

    @staticmethod
    def _evaluate_simple_condition(condition: str, context: dict) -> bool:
        """Evaluate a simple condition string like 'run_tests.returncode == 0'."""
        # Very basic — just check truthiness of a context path
        parts = condition.split("==")
        if len(parts) == 2:
            path = parts[0].strip()
            expected = parts[1].strip().strip("'\"")
            # Navigate context
            value = context
            for p in path.split("."):
                if isinstance(value, dict):
                    value = value.get(p)
                else:
                    return False
            return str(value) == expected
        return True
