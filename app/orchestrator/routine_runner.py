"""Routine runner.

This module turns `RoutineRegistry` entries into executable scheduled work.

It intentionally stays lightweight:
- The registry defines *what* to run (hook key) and *when* (cron schedule).
- The runner evaluates due routines and either executes them or creates inbox
  items based on the execution policy (auto/notify/confirm).
- All outcomes are appended to `RoutineAuditEvent`.

The orchestrator engine is expected to call `RoutineRunner.process_due_routines()`
periodically.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import InboxItem, RoutineAuditEvent, RoutineRegistry
from app.orchestrator.scheduler import compute_next_fire_time

logger = logging.getLogger(__name__)


HookFn = Callable[[RoutineRegistry], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class RoutineRunResult:
    executed: int = 0
    notified: int = 0
    confirmation_requested: int = 0
    errors: int = 0


class RoutineRunner:
    def __init__(self, db: AsyncSession, hooks: dict[str, HookFn] | None = None):
        self.db = db
        self.hooks: dict[str, HookFn] = hooks or {}

    async def process_due_routines(self, *, now: datetime | None = None, limit: int = 25) -> RoutineRunResult:
        now = now or datetime.now(timezone.utc)

        # Ensure any routines with a schedule but no next_run_at get initialized.
        await self._initialize_missing_next_runs(now)

        result = await self.db.execute(
            select(RoutineRegistry)
            .where(
                and_(
                    RoutineRegistry.enabled.is_(True),
                    RoutineRegistry.next_run_at.is_not(None),
                    RoutineRegistry.next_run_at <= now,
                )
            )
            .order_by(RoutineRegistry.next_run_at.asc())
            .limit(limit)
        )
        due = result.scalars().all()

        counts = RoutineRunResult()
        for routine in due:
            try:
                if routine.paused_until and routine.paused_until > now:
                    await self._audit(routine, action="due", status="ok", message="routine paused; skipping")
                    routine.next_run_at = self._compute_next_run(routine, now)
                    continue

                if routine.cooldown_seconds and routine.last_run_at:
                    if routine.last_run_at + timedelta(seconds=routine.cooldown_seconds) > now:
                        await self._audit(routine, action="due", status="ok", message="cooldown active; skipping")
                        routine.next_run_at = self._compute_next_run(routine, now)
                        continue

                if routine.max_runs_per_day:
                    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                    count_q = await self.db.execute(
                        select(func.count(RoutineAuditEvent.id)).where(
                            and_(
                                RoutineAuditEvent.routine_id == routine.id,
                                RoutineAuditEvent.action == "executed",
                                RoutineAuditEvent.created_at >= today_start,
                            )
                        )
                    )
                    today_count = int(count_q.scalar() or 0)
                    if today_count >= routine.max_runs_per_day:
                        await self._audit(
                            routine,
                            action="due",
                            status="ok",
                            message=f"max_runs_per_day reached ({today_count}/{routine.max_runs_per_day}); skipping",
                        )
                        routine.next_run_at = self._compute_next_run(routine, now)
                        continue

                policy = (routine.execution_policy or "auto").lower()

                if policy == "notify":
                    await self._create_inbox_item(
                        routine,
                        now,
                        kind="routine_notification",
                        title=f"Routine due: {routine.name}",
                        content=f"Routine '{routine.name}' is due (hook={routine.hook}). Execution policy=notify.",
                    )
                    await self._audit(routine, action="notified", status="ok")
                    routine.next_run_at = self._compute_next_run(routine, now)
                    counts = RoutineRunResult(
                        executed=counts.executed,
                        notified=counts.notified + 1,
                        confirmation_requested=counts.confirmation_requested,
                        errors=counts.errors,
                    )
                    continue

                if policy == "confirm":
                    if not routine.pending_confirmation:
                        await self._create_inbox_item(
                            routine,
                            now,
                            kind="routine_confirmation",
                            title=f"Confirm routine: {routine.name}",
                            content=f"Routine '{routine.name}' requested confirmation (hook={routine.hook}).",
                        )
                        routine.pending_confirmation = True
                        await self._audit(routine, action="confirmation_requested", status="ok")
                        counts = RoutineRunResult(
                            executed=counts.executed,
                            notified=counts.notified,
                            confirmation_requested=counts.confirmation_requested + 1,
                            errors=counts.errors,
                        )
                    # Advance schedule to avoid repeated prompts; confirmation can be run manually.
                    routine.next_run_at = self._compute_next_run(routine, now)
                    continue

                # auto (default)
                await self._execute(routine, now=now)
                counts = RoutineRunResult(
                    executed=counts.executed + 1,
                    notified=counts.notified,
                    confirmation_requested=counts.confirmation_requested,
                    errors=counts.errors,
                )

            except Exception as e:
                logger.exception("Routine %s failed", routine.name)
                await self._audit(routine, action="error", status="error", message=str(e))
                counts = RoutineRunResult(
                    executed=counts.executed,
                    notified=counts.notified,
                    confirmation_requested=counts.confirmation_requested,
                    errors=counts.errors + 1,
                )

        await self.db.flush()
        return counts

    async def run_routine_now(self, routine: RoutineRegistry, *, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        routine.pending_confirmation = False
        return await self._execute(routine, now=now)

    async def _execute(self, routine: RoutineRegistry, *, now: datetime) -> dict[str, Any]:
        await self._audit(routine, action="due", status="ok")

        hook_key = (routine.hook or routine.name).strip() if routine.hook or routine.name else None
        if not hook_key:
            raise ValueError("routine has no hook")

        hook = self.hooks.get(hook_key)
        if hook is None:
            raise ValueError(f"unknown routine hook: {hook_key}")

        payload = await hook(routine)

        routine.last_run_at = now
        routine.next_run_at = self._compute_next_run(routine, now)
        routine.run_count = (routine.run_count or 0) + 1

        await self._audit(routine, action="executed", status="ok", event_metadata=payload)
        return payload

    def _compute_next_run(self, routine: RoutineRegistry, now: datetime) -> datetime | None:
        if not routine.schedule:
            return None
        return compute_next_fire_time(routine.schedule, now)

    async def _initialize_missing_next_runs(self, now: datetime) -> None:
        result = await self.db.execute(
            select(RoutineRegistry).where(
                and_(
                    RoutineRegistry.enabled.is_(True),
                    RoutineRegistry.schedule.is_not(None),
                    RoutineRegistry.next_run_at.is_(None),
                )
            )
        )
        for routine in result.scalars().all():
            routine.next_run_at = self._compute_next_run(routine, now)

    async def _create_inbox_item(self, routine: RoutineRegistry, now: datetime, *, kind: str, title: str, content: str) -> None:
        # InboxItem schema varies; keep minimal required fields.
        item = InboxItem(
            id=str(uuid.uuid4()),
            title=title,
            filename=kind,
            content=content,
            is_read=False,
            modified_at=now,
            summary=f"{kind} for routine {routine.name}",
        )
        self.db.add(item)

    async def _audit(
        self,
        routine: RoutineRegistry,
        *,
        action: str,
        status: str,
        message: str | None = None,
        event_metadata: dict[str, Any] | None = None,
    ) -> None:
        self.db.add(
            RoutineAuditEvent(
                id=str(uuid.uuid4()),
                routine_id=routine.id,
                routine_name=routine.name,
                action=action,
                status=status,
                message=message,
                event_metadata=event_metadata,
            )
        )
