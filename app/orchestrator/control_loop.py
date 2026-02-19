"""Server-native Lobs-as-PM control loop phases."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Any, Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ControlLoopEvent, ControlLoopHeartbeat

logger = logging.getLogger(__name__)


@dataclass
class ControlLoopResult:
    events_processed: int = 0
    reflection_triggered: bool = False
    compression_triggered: bool = False


class LobsControlLoopService:
    """Deterministic control-loop phases run from orchestrator tick."""

    def __init__(
        self,
        db: AsyncSession,
        *,
        reflection_interval_seconds: int,
        reflection_last_run_at: float,
        compression_hour_et: int,
        last_compression_date_et: str | None,
        run_reflection: Callable[[], Awaitable[dict[str, Any]]],
        run_daily_compression: Callable[[], Awaitable[dict[str, Any]]],
        route_task_created: Callable[[dict[str, Any]], Awaitable[bool]],
    ) -> None:
        self.db = db
        self.reflection_interval_seconds = reflection_interval_seconds
        self.reflection_last_run_at = reflection_last_run_at
        self.compression_hour_et = max(0, min(23, compression_hour_et))
        self.last_compression_date_et = last_compression_date_et
        self.run_reflection = run_reflection
        self.run_daily_compression = run_daily_compression
        self.route_task_created = route_task_created

    async def run_once(self, now_utc: datetime | None = None) -> ControlLoopResult:
        now_utc = now_utc or datetime.now(timezone.utc)
        result = ControlLoopResult()

        result.events_processed = await self._phase_event_handling(now_utc)
        result.reflection_triggered = await self._phase_reflection(now_utc)
        result.compression_triggered = await self._phase_daily_compression(now_utc)
        await self._write_heartbeat(now_utc, result)
        return result

    async def _phase_event_handling(self, now_utc: datetime) -> int:
        q = await self.db.execute(
            select(ControlLoopEvent)
            .where(ControlLoopEvent.status == "pending")
            .order_by(ControlLoopEvent.created_at.asc())
            .limit(50)
        )
        events = q.scalars().all()
        processed = 0

        for event in events:
            try:
                payload = event.payload if isinstance(event.payload, dict) else {}
                if event.event_type == "TaskCreated":
                    routed = await self.route_task_created(payload)
                    event.status = "completed"
                    event.processed_at = now_utc
                    event.result = {"routed": routed}
                    processed += 1
                    logger.info(
                        "[CONTROL_LOOP] phase=event_handling event=TaskCreated id=%s routed=%s",
                        event.id,
                        routed,
                    )
                else:
                    event.status = "ignored"
                    event.processed_at = now_utc
                    event.result = {"reason": "unknown_event_type"}
            except Exception as e:
                event.status = "failed"
                event.processed_at = now_utc
                event.result = {"error": str(e)}
                logger.error("[CONTROL_LOOP] Failed event id=%s: %s", event.id, e, exc_info=True)

        return processed

    async def _phase_reflection(self, now_utc: datetime) -> bool:
        now_ts = now_utc.timestamp()
        if now_ts - self.reflection_last_run_at < self.reflection_interval_seconds:
            return False

        reflection_result = await self.run_reflection()
        self.reflection_last_run_at = now_ts
        logger.info(
            "[CONTROL_LOOP] phase=reflection triggered=true spawned=%s",
            reflection_result.get("spawned", 0),
        )
        return True

    async def _phase_daily_compression(self, now_utc: datetime) -> bool:
        et = ZoneInfo("America/New_York")
        now_et = now_utc.astimezone(et)
        today_key = now_et.date().isoformat()

        if self.last_compression_date_et == today_key or now_et.hour < self.compression_hour_et:
            return False

        compression_result = await self.run_daily_compression()
        self.last_compression_date_et = today_key
        logger.info(
            "[CONTROL_LOOP] phase=daily_compression triggered=true rewritten=%s",
            compression_result.get("rewritten", 0),
        )
        return True

    async def _write_heartbeat(self, now_utc: datetime, result: ControlLoopResult) -> None:
        heartbeat = await self.db.get(ControlLoopHeartbeat, "main")
        payload = {
            "events_processed": result.events_processed,
            "reflection_triggered": result.reflection_triggered,
            "compression_triggered": result.compression_triggered,
        }
        if heartbeat is None:
            heartbeat = ControlLoopHeartbeat(
                id="main",
                phase="tick_complete",
                last_heartbeat_at=now_utc,
                heartbeat_metadata=payload,
            )
            self.db.add(heartbeat)
        else:
            heartbeat.phase = "tick_complete"
            heartbeat.last_heartbeat_at = now_utc
            heartbeat.heartbeat_metadata = payload
