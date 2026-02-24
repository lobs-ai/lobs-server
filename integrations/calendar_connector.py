"""Calendar connector — Integration Contract v1 adapter.

Wraps the existing ``app.services.google_calendar.GoogleCalendarService`` and
exposes it through the canonical BaseConnector interface.

Supported capabilities
  auth       — is_configured, health_check
  fetch      — fetch_events
  act        — create_event, update_event, delete_event
  webhook_in — not supported (raises ConnectorNotImplementedError)
  webhook_out— not supported (raises ConnectorNotImplementedError)

Notes
  - Events are fetched from *Lobs's own calendar* by default.
  - ``fetch_messages()`` and ``send_message()``/``mark_read()`` raise
    ConnectorNotImplementedError (calendars don't carry messages).
  - ``search()`` is not implemented by the underlying service and also raises.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from integrations.base_connector import BaseConnector
from integrations.entities import (
    ActionResult,
    ConnectorHealth,
    EventDraft,
    NormalizedEvent,
)

logger = logging.getLogger(__name__)


def _raw_to_normalized(raw: dict, connector: str = "calendar") -> NormalizedEvent:
    """Convert a raw dict from GoogleCalendarService._norm() into a NormalizedEvent."""

    def _parse(dt_str: str | None) -> datetime:
        if not dt_str:
            return datetime.now(timezone.utc)
        try:
            from dateutil.parser import parse as _p
            dt = _p(dt_str)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            return datetime.now(timezone.utc)

    start = _parse(raw.get("start"))
    end = _parse(raw.get("end")) if raw.get("end") else start + timedelta(hours=1)

    return NormalizedEvent(
        id=raw.get("gcal_id", ""),
        connector=connector,
        title=raw.get("title", ""),
        description=raw.get("description", ""),
        start=start,
        end=end,
        attendees=raw.get("attendees", []),
        location=raw.get("location", ""),
        is_all_day=raw.get("all_day", False),
        status=raw.get("status", "confirmed"),
        raw=raw,
    )


class CalendarConnector(BaseConnector):
    """Canonical connector for Google Calendar.

    Pass ``db=None`` when constructing outside of a request context; the
    underlying service only uses the DB for sync operations which are not
    surfaced through this contract interface.
    """

    name = "calendar"

    def __init__(self, db=None) -> None:
        from app.services.google_calendar import GoogleCalendarService

        self._svc = GoogleCalendarService(db)  # type: ignore[arg-type]

    # ── auth ──────────────────────────────────────────────────────────

    def is_configured(self) -> bool:  # noqa: D102
        return self._svc.is_configured()

    async def health_check(self) -> ConnectorHealth:  # noqa: D102
        if not self.is_configured():
            return self._not_configured_health()

        t0 = time.monotonic()
        try:
            events = await self._svc.get_lobs_events(days=3)
            latency = self._ms(t0)
            return ConnectorHealth(
                connector=self.name,
                status="ok",
                latency_ms=latency,
                detail=f"Fetched {len(events)} event(s) successfully.",
            )
        except Exception as exc:
            return ConnectorHealth(
                connector=self.name,
                status="error",
                latency_ms=self._ms(t0),
                detail=str(exc),
            )

    # ── fetch ─────────────────────────────────────────────────────────

    async def fetch_events(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[NormalizedEvent]:
        """Return Lobs's calendar events in the given window.

        If ``start`` / ``end`` are omitted, defaults to the next 7 days.
        """
        now = datetime.now(timezone.utc)
        start = start or now
        end = end or (now + timedelta(days=7))

        days = max(1, (end - start).days)
        raw_list = await self._svc.get_lobs_events(days=days)
        return [_raw_to_normalized(r) for r in raw_list]

    # ── act ───────────────────────────────────────────────────────────

    async def create_event(self, event: EventDraft) -> ActionResult:
        """Create an event on Lobs's Google Calendar."""
        result = await self._svc.create_event(
            title=event.title,
            start=event.start,
            end=event.end,
            description=event.description,
            location=event.location,
            all_day=event.is_all_day,
            invite_rafe=False,  # connector layer does not apply business rules
        )
        if result:
            return ActionResult(
                success=True,
                connector=self.name,
                resource_id=result.get("gcal_id", ""),
                raw=result,
            )
        return ActionResult(
            success=False,
            connector=self.name,
            detail="Calendar service returned no result — backend may not be configured.",
        )

    async def update_event(self, event_id: str, patch: dict[str, Any]) -> ActionResult:
        """Patch an existing event.  ``patch`` keys mirror EventDraft fields."""
        result = await self._svc.update_event(event_id, **patch)
        if result:
            return ActionResult(
                success=True,
                connector=self.name,
                resource_id=result.get("gcal_id", event_id),
                raw=result,
            )
        return ActionResult(
            success=False,
            connector=self.name,
            resource_id=event_id,
            detail="update_event returned None — check event_id and credentials.",
        )

    async def delete_event(self, event_id: str) -> ActionResult:
        """Delete / cancel an event from Lobs's Google Calendar."""
        ok = await self._svc.delete_event(event_id)
        return ActionResult(
            success=ok,
            connector=self.name,
            resource_id=event_id,
            detail="" if ok else "delete_event returned False — check event_id and credentials.",
        )
