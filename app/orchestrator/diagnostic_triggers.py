"""Reactive diagnostic triggers for stalls, failures, drift, and idle regressions."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentReflection, AgentStatus, DiagnosticTriggerEvent, OrchestratorSetting, Task, WorkerRun
from app.orchestrator.context_packets import ContextPacketBuilder
from app.orchestrator.model_chooser import ModelChooser
from app.orchestrator.runtime_settings import (
    DEFAULT_RUNTIME_SETTINGS,
    SETTINGS_KEY_DIAG_DEBOUNCE_SECONDS,
    SETTINGS_KEY_DIAG_FAILURE_RETRY_COUNT,
    SETTINGS_KEY_DIAG_IDLE_HOURS,
    SETTINGS_KEY_DIAG_PERF_DROP_PERCENT,
    SETTINGS_KEY_DIAG_PR_REJECTION_HOURS,
    SETTINGS_KEY_DIAG_REPO_DRIFT_COUNT,
    SETTINGS_KEY_DIAG_STALL_HOURS,
)
from app.orchestrator.worker import WorkerManager

logger = logging.getLogger(__name__)


class DiagnosticTriggerEngine:
    """Detect trigger conditions and spawn small diagnostic sessions."""

    def __init__(self, db: AsyncSession, worker_manager: WorkerManager):
        self.db = db
        self.worker_manager = worker_manager
        self.packet_builder = ContextPacketBuilder(db)
        self.model_chooser = ModelChooser(db)

    async def run_once(self) -> dict[str, int]:
        settings = await self._load_settings()
        candidates = []

        candidates.extend(await self._stalled_task_triggers(settings))
        candidates.extend(await self._failure_pattern_triggers(settings))
        candidates.extend(await self._pr_rejection_triggers(settings))
        candidates.extend(await self._idle_agent_triggers(settings))
        candidates.extend(await self._performance_drop_triggers(settings))
        candidates.extend(await self._repo_drift_triggers(settings))

        fired = 0
        spawned = 0
        suppressed = 0
        for trigger in candidates:
            event = await self._record_or_suppress(trigger, settings)
            if event is None:
                suppressed += 1
                continue

            fired += 1
            if await self._spawn_diagnostic(trigger, event):
                spawned += 1

        return {
            "triggers": len(candidates),
            "fired": fired,
            "spawned": spawned,
            "suppressed": suppressed,
        }

    async def _load_settings(self) -> dict[str, Any]:
        keys = {
            SETTINGS_KEY_DIAG_STALL_HOURS,
            SETTINGS_KEY_DIAG_FAILURE_RETRY_COUNT,
            SETTINGS_KEY_DIAG_PR_REJECTION_HOURS,
            SETTINGS_KEY_DIAG_IDLE_HOURS,
            SETTINGS_KEY_DIAG_PERF_DROP_PERCENT,
            SETTINGS_KEY_DIAG_REPO_DRIFT_COUNT,
            SETTINGS_KEY_DIAG_DEBOUNCE_SECONDS,
        }
        result = await self.db.execute(
            select(OrchestratorSetting).where(OrchestratorSetting.key.in_(keys))
        )
        from_db = {row.key: row.value for row in result.scalars().all()}
        merged = {**DEFAULT_RUNTIME_SETTINGS, **from_db}
        return merged

    async def _stalled_task_triggers(self, settings: dict[str, Any]) -> list[dict[str, Any]]:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, int(settings[SETTINGS_KEY_DIAG_STALL_HOURS])))
        result = await self.db.execute(
            select(Task).where(Task.work_state == "in_progress", Task.updated_at <= cutoff)
        )
        tasks = result.scalars().all()
        return [
            {
                "kind": "stalled_task",
                "trigger_key": f"stalled:{task.id}",
                "agent_type": (task.agent or "programmer"),
                "task_id": task.id,
                "project_id": task.project_id,
                "details": {
                    "title": task.title,
                    "project_id": task.project_id,
                    "updated_at": task.updated_at.isoformat() if task.updated_at else None,
                },
            }
            for task in tasks
        ]

    async def _failure_pattern_triggers(self, settings: dict[str, Any]) -> list[dict[str, Any]]:
        retry_count = max(1, int(settings[SETTINGS_KEY_DIAG_FAILURE_RETRY_COUNT]))
        result = await self.db.execute(
            select(Task).where(Task.retry_count >= retry_count, Task.work_state.in_(["blocked", "not_started"]))
        )
        tasks = result.scalars().all()
        return [
            {
                "kind": "repeated_failure",
                "trigger_key": f"failure:{task.id}",
                "agent_type": (task.agent or "programmer"),
                "task_id": task.id,
                "project_id": task.project_id,
                "details": {
                    "title": task.title,
                    "retry_count": task.retry_count,
                    "failure_reason": task.failure_reason,
                },
            }
            for task in tasks
        ]

    async def _pr_rejection_triggers(self, settings: dict[str, Any]) -> list[dict[str, Any]]:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, int(settings[SETTINGS_KEY_DIAG_PR_REJECTION_HOURS])))
        result = await self.db.execute(
            select(Task).where(
                Task.review_state == "rejected",
                Task.updated_at >= cutoff,
                Task.work_state.in_(["completed", "failed", "blocked"]),
            )
        )
        tasks = result.scalars().all()
        return [
            {
                "kind": "pr_rejection",
                "trigger_key": f"pr_rejected:{task.id}",
                "agent_type": (task.agent or "programmer"),
                "task_id": task.id,
                "project_id": task.project_id,
                "details": {
                    "title": task.title,
                    "review_state": task.review_state,
                    "updated_at": task.updated_at.isoformat() if task.updated_at else None,
                },
            }
            for task in tasks
        ]

    async def _idle_agent_triggers(self, settings: dict[str, Any]) -> list[dict[str, Any]]:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, int(settings[SETTINGS_KEY_DIAG_IDLE_HOURS])))
        result = await self.db.execute(select(AgentStatus).where(AgentStatus.last_active_at <= cutoff))
        statuses = result.scalars().all()
        out: list[dict[str, Any]] = []
        for status in statuses:
            if (status.status or "").lower() in {"working", "busy", "active"}:
                continue
            out.append(
                {
                    "kind": "idle_drift",
                    "trigger_key": f"idle:{status.agent_type}",
                    "agent_type": status.agent_type,
                    "task_id": None,
                    "project_id": status.current_project_id,
                    "details": {
                        "last_active_at": status.last_active_at.isoformat() if status.last_active_at else None,
                        "activity": status.activity,
                    },
                }
            )
        return out

    async def _performance_drop_triggers(self, settings: dict[str, Any]) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        recent_since = now - timedelta(hours=6)
        baseline_since = now - timedelta(hours=30)
        perf_drop_percent = max(1, float(settings[SETTINGS_KEY_DIAG_PERF_DROP_PERCENT]))

        recent_result = await self.db.execute(
            select(WorkerRun.worker_id, func.count(WorkerRun.id), func.sum(func.coalesce(WorkerRun.succeeded, False)))
            .where(WorkerRun.started_at >= recent_since)
            .group_by(WorkerRun.worker_id)
        )
        recent_rows = recent_result.all()

        triggers: list[dict[str, Any]] = []
        for worker_id, run_count, success_count in recent_rows:
            if not worker_id or run_count < 3:
                continue

            baseline_result = await self.db.execute(
                select(func.count(WorkerRun.id), func.sum(func.coalesce(WorkerRun.succeeded, False))).where(
                    WorkerRun.worker_id == worker_id,
                    WorkerRun.started_at >= baseline_since,
                    WorkerRun.started_at < recent_since,
                )
            )
            baseline_count, baseline_successes = baseline_result.one()
            if not baseline_count or baseline_count < 3:
                continue

            recent_rate = (float(success_count or 0) / float(run_count)) * 100.0
            baseline_rate = (float(baseline_successes or 0) / float(baseline_count)) * 100.0
            drop = baseline_rate - recent_rate
            if drop < perf_drop_percent:
                continue

            agent_type = worker_id.split("-")[0]
            triggers.append(
                {
                    "kind": "performance_drop",
                    "trigger_key": f"perf:{worker_id}",
                    "agent_type": agent_type,
                    "task_id": None,
                    "project_id": None,
                    "details": {
                        "worker_id": worker_id,
                        "recent_rate": round(recent_rate, 2),
                        "baseline_rate": round(baseline_rate, 2),
                        "drop_percent": round(drop, 2),
                    },
                }
            )

        return triggers

    async def _repo_drift_triggers(self, settings: dict[str, Any]) -> list[dict[str, Any]]:
        drift_count = max(1, int(settings[SETTINGS_KEY_DIAG_REPO_DRIFT_COUNT]))
        result = await self.db.execute(
            select(Task.project_id, func.count(Task.id))
            .where(Task.sync_state.in_(["conflict", "local_changed"]))
            .group_by(Task.project_id)
            .having(func.count(Task.id) >= drift_count)
        )
        rows = result.all()
        return [
            {
                "kind": "repo_drift",
                "trigger_key": f"repo_drift:{project_id}",
                "agent_type": "project-manager",
                "task_id": None,
                "project_id": project_id,
                "details": {"project_id": project_id, "drifted_tasks": count},
            }
            for project_id, count in rows
            if project_id
        ]

    async def _record_or_suppress(
        self,
        trigger: dict[str, Any],
        settings: dict[str, Any],
    ) -> DiagnosticTriggerEvent | None:
        now = datetime.now(timezone.utc)
        debounce_seconds = max(60, int(settings[SETTINGS_KEY_DIAG_DEBOUNCE_SECONDS]))
        debounce_cutoff = now - timedelta(seconds=debounce_seconds)

        duplicate_query = await self.db.execute(
            select(DiagnosticTriggerEvent)
            .where(
                DiagnosticTriggerEvent.trigger_key == trigger["trigger_key"],
                DiagnosticTriggerEvent.trigger_type == trigger["kind"],
                DiagnosticTriggerEvent.created_at >= debounce_cutoff,
                DiagnosticTriggerEvent.status.in_(["fired", "spawned", "completed"]),
            )
            .order_by(DiagnosticTriggerEvent.created_at.desc())
            .limit(1)
        )
        duplicate = duplicate_query.scalar_one_or_none()
        if duplicate is not None:
            suppressed = DiagnosticTriggerEvent(
                id=str(uuid.uuid4()),
                trigger_type=trigger["kind"],
                trigger_key=trigger["trigger_key"],
                status="suppressed",
                suppression_reason=f"debounce:{debounce_seconds}s",
                agent_type=trigger.get("agent_type"),
                task_id=trigger.get("task_id"),
                project_id=trigger.get("project_id"),
                trigger_payload=trigger,
                outcome={"suppressed_by_event_id": duplicate.id},
            )
            self.db.add(suppressed)
            await self.db.commit()
            return None

        event = DiagnosticTriggerEvent(
            id=str(uuid.uuid4()),
            trigger_type=trigger["kind"],
            trigger_key=trigger["trigger_key"],
            status="fired",
            agent_type=trigger.get("agent_type"),
            task_id=trigger.get("task_id"),
            project_id=trigger.get("project_id"),
            trigger_payload=trigger,
        )
        self.db.add(event)
        await self.db.commit()
        return event

    async def _spawn_diagnostic(self, trigger: dict[str, Any], event: DiagnosticTriggerEvent) -> bool:
        agent_type = trigger["agent_type"]

        packet = await self.packet_builder.build_for_agent(agent_type, hours=2)
        context_packet = packet.to_dict()
        context_packet["trigger"] = {**trigger, "trigger_event_id": event.id}

        reflection = AgentReflection(
            id=str(uuid.uuid4()),
            agent_type=agent_type,
            reflection_type="diagnostic",
            status="pending",
            window_start=datetime.now(timezone.utc) - timedelta(hours=2),
            window_end=datetime.now(timezone.utc),
            context_packet=context_packet,
        )
        self.db.add(reflection)
        event.status = "spawned"
        event.diagnostic_reflection_id = reflection.id
        await self.db.commit()

        prompt = self._build_prompt(agent_type, reflection.id, context_packet)
        choice = await self.model_chooser.choose(
            agent_type=agent_type,
            task={
                "id": reflection.id,
                "title": f"Diagnostic trigger: {trigger.get('kind', 'unknown')}",
                "notes": "Reactive diagnostic analysis run",
                "status": "inbox",
            },
            purpose="diagnostic",
        )
        label = f"diagnostic-{agent_type}"
        result, error, _error_type = await self.worker_manager._spawn_session(
            task_prompt=prompt,
            agent_id=agent_type,
            model=choice.model,
            label=label,
        )

        if result:
            self.worker_manager.register_external_worker(
                result,
                agent_type=agent_type,
                model=choice.model,
                label=label,
            )
            return True

        reflection.status = "failed"
        reflection.result = {"error": error or "spawn_failed"}
        reflection.completed_at = datetime.now(timezone.utc)
        event.status = "failed"
        event.outcome = {"spawn_error": error or "spawn_failed"}
        await self.db.commit()
        logger.warning("[DIAGNOSTIC] Failed to spawn diagnostic for %s: %s", agent_type, error)
        return False

    @staticmethod
    def _build_prompt(agent_type: str, reflection_id: str, packet: dict[str, Any]) -> str:
        return f"""## Agent Diagnostic Mode

Agent: {agent_type}
Reflection record: {reflection_id}

Context packet JSON:
{packet}

Return STRICT JSON (no prose outside JSON):
{{
  "issue_summary": "...",
  "root_causes": ["..."],
  "recommended_actions": ["..."],
  "confidence": 0.0
}}
"""
