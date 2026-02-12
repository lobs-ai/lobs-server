"""Per-agent status tracking - writes to database instead of JSON files."""

import logging
from datetime import datetime, timezone
from typing import Any, Optional
from dataclasses import dataclass, field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentStatus as AgentStatusModel

logger = logging.getLogger(__name__)

AGENT_TYPES = ("architect", "programmer", "researcher", "reviewer", "writer")


@dataclass
class AgentStats:
    tasks_completed: int = 0
    tasks_failed: int = 0
    avg_duration_seconds: int = 0
    last_week_completed: int = 0
    _durations: list[float] = field(default_factory=list, repr=False)

    def record_duration(self, seconds: float) -> None:
        self._durations.append(seconds)
        # Keep last 50 for rolling average
        if len(self._durations) > 50:
            self._durations = self._durations[-50:]
        self.avg_duration_seconds = int(sum(self._durations) / len(self._durations))

    def to_dict(self) -> dict[str, Any]:
        return {
            "tasksCompleted": self.tasks_completed,
            "tasksFailed": self.tasks_failed,
            "avgDurationSeconds": self.avg_duration_seconds,
            "lastWeekCompleted": self.last_week_completed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentStats":
        return cls(
            tasks_completed=data.get("tasksCompleted", 0),
            tasks_failed=data.get("tasksFailed", 0),
            avg_duration_seconds=data.get("avgDurationSeconds", 0),
            last_week_completed=data.get("lastWeekCompleted", 0),
        )


@dataclass
class AgentStatus:
    agent_type: str
    status: str = "idle"  # idle|working|thinking|finalizing
    activity: Optional[str] = None
    thinking: Optional[str] = None
    current_task_id: Optional[str] = None
    current_project_id: Optional[str] = None
    last_active_at: Optional[str] = None
    last_completed_task_id: Optional[str] = None
    last_completed_at: Optional[str] = None
    stats: AgentStats = field(default_factory=AgentStats)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agentType": self.agent_type,
            "status": self.status,
            "activity": self.activity,
            "thinking": self.thinking,
            "currentTaskId": self.current_task_id,
            "currentProjectId": self.current_project_id,
            "lastActiveAt": self.last_active_at,
            "lastCompletedTaskId": self.last_completed_task_id,
            "lastCompletedAt": self.last_completed_at,
            "stats": self.stats.to_dict(),
        }


class AgentTracker:
    """Tracks per-agent-type status via database."""

    def __init__(self, db: AsyncSession, agent_types: Optional[list[str]] = None):
        self.db = db
        types = agent_types or list(AGENT_TYPES)
        self._statuses: dict[str, AgentStatus] = {}
        self._dirty: set[str] = set()
        self._completion_counts: dict[str, int] = {t: 0 for t in types}

    async def init_statuses(self):
        """Load agent statuses from database."""
        for agent_type in AGENT_TYPES:
            status = await self._load_status(agent_type)
            self._statuses[agent_type] = status

    async def _load_status(self, agent_type: str) -> AgentStatus:
        """Load status from database or create new."""
        try:
            result = await self.db.execute(
                select(AgentStatusModel).where(AgentStatusModel.agent_type == agent_type)
            )
            model = result.scalar_one_or_none()
            
            if model is None:
                return AgentStatus(agent_type=agent_type)
            
            stats_data = model.stats or {}
            return AgentStatus(
                agent_type=agent_type,
                status=model.status or "idle",
                activity=model.activity,
                thinking=model.thinking,
                current_task_id=model.current_task_id,
                current_project_id=model.current_project_id,
                last_active_at=model.last_active_at.isoformat() if model.last_active_at else None,
                last_completed_task_id=model.last_completed_task_id,
                last_completed_at=model.last_completed_at.isoformat() if model.last_completed_at else None,
                stats=AgentStats.from_dict(stats_data),
            )
        except Exception as e:
            logger.warning(f"Failed to load status for {agent_type}: {e}")
            return AgentStatus(agent_type=agent_type)

    async def _write_status(self, agent_type: str) -> None:
        """Write status to database."""
        try:
            status = self._statuses[agent_type]
            
            # Check if record exists
            result = await self.db.execute(
                select(AgentStatusModel).where(AgentStatusModel.agent_type == agent_type)
            )
            model = result.scalar_one_or_none()
            
            # Parse datetime strings
            last_active_at = datetime.fromisoformat(status.last_active_at) if status.last_active_at else None
            last_completed_at = datetime.fromisoformat(status.last_completed_at) if status.last_completed_at else None
            
            if model is None:
                # Create new
                model = AgentStatusModel(
                    agent_type=agent_type,
                    status=status.status,
                    activity=status.activity,
                    thinking=status.thinking,
                    current_task_id=status.current_task_id,
                    current_project_id=status.current_project_id,
                    last_active_at=last_active_at,
                    last_completed_task_id=status.last_completed_task_id,
                    last_completed_at=last_completed_at,
                    stats=status.stats.to_dict(),
                )
                self.db.add(model)
            else:
                # Update existing
                model.status = status.status
                model.activity = status.activity
                model.thinking = status.thinking
                model.current_task_id = status.current_task_id
                model.current_project_id = status.current_project_id
                model.last_active_at = last_active_at
                model.last_completed_task_id = status.last_completed_task_id
                model.last_completed_at = last_completed_at
                model.stats = status.stats.to_dict()
            
            await self.db.commit()
        except Exception as e:
            logger.error(f"Failed to write status for {agent_type}: {e}")
            await self.db.rollback()

    async def mark_working(self, agent_type: str, task_id: str, project_id: str, activity: str) -> None:
        """Mark an agent as actively working on a task."""
        status = self._statuses.get(agent_type)
        if not status:
            return
        status.status = "working"
        status.activity = activity
        status.thinking = None
        status.current_task_id = task_id
        status.current_project_id = project_id
        status.last_active_at = datetime.now(timezone.utc).isoformat()
        self._dirty.add(agent_type)
        logger.info(f"[AGENT_TRACKER] {agent_type} -> working: {activity[:80]}")

    async def update_thinking(self, agent_type: str, snippet: str) -> None:
        """Update the thinking snippet for an active agent."""
        status = self._statuses.get(agent_type)
        if not status or status.status == "idle":
            return
        old = status.thinking
        status.thinking = snippet[:500] if snippet else None
        if status.thinking != old:
            self._dirty.add(agent_type)

    async def mark_completed(self, agent_type: str, task_id: str, duration_seconds: float) -> None:
        """Mark a task as successfully completed."""
        status = self._statuses.get(agent_type)
        if not status:
            return
        now = datetime.now(timezone.utc).isoformat()
        status.last_completed_task_id = task_id
        status.last_completed_at = now
        status.last_active_at = now
        status.stats.tasks_completed += 1
        status.stats.record_duration(duration_seconds)
        self._dirty.add(agent_type)
        self._completion_counts[agent_type] = self._completion_counts.get(agent_type, 0) + 1
        logger.info(f"[AGENT_TRACKER] {agent_type} completed task {task_id[:8]} ({duration_seconds:.0f}s)")

    async def mark_failed(self, agent_type: str, task_id: str) -> None:
        """Mark a task as failed."""
        status = self._statuses.get(agent_type)
        if not status:
            return
        status.last_active_at = datetime.now(timezone.utc).isoformat()
        status.stats.tasks_failed += 1
        self._dirty.add(agent_type)
        logger.info(f"[AGENT_TRACKER] {agent_type} failed task {task_id[:8]}")

    async def mark_idle(self, agent_type: str) -> None:
        """Mark an agent as idle (no active work)."""
        status = self._statuses.get(agent_type)
        if not status:
            return
        status.status = "idle"
        status.activity = None
        status.thinking = None
        status.current_task_id = None
        status.current_project_id = None
        self._dirty.add(agent_type)
        logger.debug(f"[AGENT_TRACKER] {agent_type} -> idle")

    def get_all_statuses(self) -> dict[str, AgentStatus]:
        """Return all agent statuses (for API)."""
        return dict(self._statuses)

    def get_status(self, agent_type: str) -> Optional[AgentStatus]:
        return self._statuses.get(agent_type)

    async def sync_to_db(self, force: bool = False) -> None:
        """Write dirty agent status records to database."""
        if not self._dirty and not force:
            return

        for agent_type in list(self._dirty):
            await self._write_status(agent_type)
        self._dirty.clear()
