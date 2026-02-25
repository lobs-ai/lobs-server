"""Canonical task-status aggregation service.

This is the single source of truth for task status counts across all API
surfaces (Home brief, Overview dashboard, Task list, Detail views).

All endpoints that expose task status counts MUST obtain them through
:func:`get_task_status_counts` rather than issuing ad-hoc COUNT queries.
Doing so guarantees that counts for `active`, `inbox`, `completed`,
`cancelled`, `rejected`, `waiting`, and `blocked` are identical regardless
of which endpoint Mission Control queries.

Usage
-----
    from app.services.task_status import get_task_status_counts

    counts = await get_task_status_counts(db)
    counts.active       # tasks with status="active"
    counts.inbox        # tasks with status="inbox"
    counts.completed    # tasks with status="completed"
    counts.cancelled    # tasks with status="cancelled"
    counts.rejected     # tasks with status="rejected"
    counts.waiting      # tasks with status="waiting_on"
    counts.blocked      # tasks with blocked_by not null AND status not completed
    counts.total_open   # sum of all non-terminal statuses
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Task


# ---------------------------------------------------------------------------
# Canonical status buckets
# ---------------------------------------------------------------------------

#: Terminal statuses — a task in one of these is "done".
TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "cancelled", "rejected", "archived"})

#: Open statuses — a task is "in-flight" when in one of these.
OPEN_STATUSES: frozenset[str] = frozenset({"inbox", "active", "waiting_on"})


@dataclass
class TaskStatusCounts:
    """Canonical, immutable snapshot of task status counts.

    This dataclass is the contract between the aggregation layer and every
    consumer (endpoints, brief adapters, CI contract tests).  Adding a new
    bucket here is a deliberate, tracked change; ad-hoc counts scattered
    across routers are explicitly prohibited.
    """

    # Open statuses
    inbox: int = 0       # awaiting assignment / human triage
    active: int = 0      # currently being worked
    waiting: int = 0     # waiting_on — blocked on external input

    # Terminal statuses
    completed: int = 0
    cancelled: int = 0
    rejected: int = 0
    archived: int = 0

    # Derived / cross-cutting
    blocked: int = 0         # blocked_by not null AND not terminal
    completed_today: int = 0 # completed with finished_at >= today 00:00 UTC

    @property
    def total_open(self) -> int:
        """Sum of all in-flight (non-terminal) statuses."""
        return self.inbox + self.active + self.waiting

    @property
    def total_terminal(self) -> int:
        """Sum of all terminal statuses."""
        return self.completed + self.cancelled + self.rejected + self.archived

    def as_dict(self) -> dict:
        return {
            "inbox": self.inbox,
            "active": self.active,
            "waiting": self.waiting,
            "completed": self.completed,
            "cancelled": self.cancelled,
            "rejected": self.rejected,
            "archived": self.archived,
            "blocked": self.blocked,
            "completed_today": self.completed_today,
            "total_open": self.total_open,
            "total_terminal": self.total_terminal,
        }


async def get_task_status_counts(
    db: AsyncSession,
    *,
    project_id: Optional[str] = None,
) -> TaskStatusCounts:
    """Return a :class:`TaskStatusCounts` populated from a single DB pass.

    All counts come from one GROUP-BY query (plus a focused completed_today
    sub-query) to minimise round-trips and to guarantee consistency.

    Parameters
    ----------
    db:
        An open async SQLAlchemy session.
    project_id:
        Optional project filter.  When provided only tasks belonging to that
        project are counted.
    """
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # ── 1. Status counts via GROUP BY ──────────────────────────────────────
    status_q = select(Task.status, func.count().label("cnt")).group_by(Task.status)
    if project_id:
        status_q = status_q.where(Task.project_id == project_id)

    status_result = await db.execute(status_q)
    by_status: dict[str, int] = {row.status: row.cnt for row in status_result.all()}

    # ── 2. Blocked count ───────────────────────────────────────────────────
    blocked_q = select(func.count()).select_from(Task).where(
        and_(
            Task.blocked_by.isnot(None),
            Task.status.notin_(TERMINAL_STATUSES),
        )
    )
    if project_id:
        blocked_q = blocked_q.where(Task.project_id == project_id)
    blocked_result = await db.execute(blocked_q)
    blocked_count = blocked_result.scalar() or 0

    # ── 3. Completed today ─────────────────────────────────────────────────
    ct_q = select(func.count()).select_from(Task).where(
        and_(
            Task.status == "completed",
            Task.finished_at >= today_start,
        )
    )
    if project_id:
        ct_q = ct_q.where(Task.project_id == project_id)
    ct_result = await db.execute(ct_q)
    completed_today = ct_result.scalar() or 0

    return TaskStatusCounts(
        inbox=by_status.get("inbox", 0),
        active=by_status.get("active", 0),
        waiting=by_status.get("waiting_on", 0),
        completed=by_status.get("completed", 0),
        cancelled=by_status.get("cancelled", 0),
        rejected=by_status.get("rejected", 0),
        archived=by_status.get("archived", 0),
        blocked=blocked_count,
        completed_today=completed_today,
    )
