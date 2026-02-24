"""Calendar connector — Integration Contract v1 adapter.

Wraps ``app.services.google_calendar.GoogleCalendarService`` and translates
its raw dict payloads into normalized contract entities.

Supported capabilities:
  ✅ auth         — is_configured(), health_check()
  ❌ fetch_messages — not applicable (raises ConnectorNotImplementedError)
  ✅ fetch_events — fetch_events(start, end)
  ❌ search       — full-text search not supported by Google Calendar API
  ✅ act          — create_event(), update_event(), delete_event()
  ❌ webhook_in/out — Google Calendar push notifications not yet configured
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Union

from sqlalchemy.ext.asyncio import AsyncSession

from integrations.base_connector import BaseConnector
from integrations.entities import (
    ActionResult,
    ConnectorError,
    ConnectorHealth,
    EventDraft,
    NormalizedEvent,
)

logger = logging.getLogger(__name__)


def _parse_dt(dt_str: str | None) -> datetime:
    """Parse an ISO 8601 / date-only string into a datetime."""
    if not dt_str:
        return datetime.now(tz=timezone.utc)
    try:
        if "T" in dt_str:
            from dateutil.parser import parse
            return parse(dt_str)
        return datetime.strptime(dt_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except Exception:
        return datetime.now(tz=timezone.utc)


def _normalize(raw: dict, connector_name: str) -> NormalizedEvent:
    """Convert a GoogleCalendarService raw dict into a NormalizedEvent."""
    return NormalizedEvent(
        id=str(raw.get("gcal_id", "")),
        connector=connector_name,
        title=raw.get("title", "(No title)"),
        description=raw.get("description", ""),
        start=_parse_dt(raw.get("start")),
        end=_parse_dt(raw.get("end")),
        attendees=[a for a in raw.get("attendees", []) if a],
        location=raw.get("location", ""),
        is_all_day=raw.get("all_day", False),
        status=raw.get("status", "confirmed"),
        raw=raw,
    )


class CalendarConnector(BaseConnector):
    """Contract v1 adapter for the Google Calendar integration."""

    name = "calendar"

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._svc: object | None = None  # lazy-loaded GoogleCalendarService

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _service(self):
        """Return (cached) GoogleCalendarService instance."""
        if self._svc is None:
            from app.services.google_calendar import GoogleCalendarService  # avoid circular import
            self._svc = GoogleCalendarService(self._db)
        return self._svc

    # ------------------------------------------------------------------
    # 1. auth
    # ------------------------------------------------------------------

    def is_configured(self) -> bool:
        return self._service().is_configured()

    async def health_check(self) -> ConnectorHealth:
        if not self.is_configured():
            return ConnectorHealth(
                connector=self.name,
                status="not_configured",
                detail="Google Calendar not configured — set GOOGLE_CALENDAR_CREDENTIALS_FILE",
            )
        start = time.monotonic()
        try:
            # Fetch 1 event as a connectivity probe (next 1 day window).
            from datetime import timedelta
            now = datetime.now(tz=timezone.utc)
            events = await self._service().get_lobs_events(days=1)
            return self._timed_health(start, f"fetched {len(events)} events")
        except Exception as exc:
            return ConnectorHealth(
                connector=self.name,
                status="error",
                detail=str(exc),
            )

    # ------------------------------------------------------------------
    # 2. fetch
    # ------------------------------------------------------------------

    async def fetch_events(
        self,
        start: datetime,
        end: datetime,
    ) -> list[NormalizedEvent]:
        """Return Lobs's calendar events in the given window."""
        try:
            svc = self._service()
            # Compute days from start → end and call get_lobs_events.
            from datetime import timedelta
            days = max(1, int((end - start).total_seconds() / 86400))
            raws = await svc.get_lobs_events(days=days)
            return [_normalize(r, self.name) for r in raws if r]
        except Exception as exc:
            logger.error("[calendar_connector] fetch_events failed: %s", exc)
            raise ConnectorError(self.name, str(exc)) from exc

    # ------------------------------------------------------------------
    # 3. act
    # ------------------------------------------------------------------

    async def create_event(self, event: EventDraft) -> ActionResult:
        try:
            result = await self._service().create_event(
                title=event.title,
                start=event.start,
                end=event.end,
                description=event.description,
                location=event.location,
                all_day=event.is_all_day,
                invite_rafe=False,  # callers must explicitly invite via attendees
            )
            if result:
                return ActionResult(
                    success=True,
                    connector=self.name,
                    resource_id=str(result.get("gcal_id", "")),
                    detail="created",
                    raw=result,
                )
            return ActionResult(
                success=False,
                connector=self.name,
                detail="create_event returned None — check Google Calendar configuration",
            )
        except Exception as exc:
            logger.error("[calendar_connector] create_event failed: %s", exc)
            raise ConnectorError(self.name, str(exc)) from exc

    async def update_event(self, event_id: str, patch: dict) -> ActionResult:
        """Patch an existing event.

        Supported patch keys: title, description, start (datetime), end (datetime).
        """
        try:
            result = await self._service().update_event(event_id, **patch)
            if result:
                return ActionResult(
                    success=True,
                    connector=self.name,
                    resource_id=str(result.get("gcal_id", event_id)),
                    detail="updated",
                    raw=result,
                )
            return ActionResult(
                success=False,
                connector=self.name,
                resource_id=event_id,
                detail="update_event returned None — event may not exist or credentials missing",
            )
        except Exception as exc:
            logger.error("[calendar_connector] update_event failed: %s", exc)
            raise ConnectorError(self.name, str(exc)) from exc

    async def delete_event(self, event_id: str) -> ActionResult:
        try:
            ok = await self._service().delete_event(event_id)
            return ActionResult(
                success=ok,
                connector=self.name,
                resource_id=event_id,
                detail="deleted" if ok else "delete_event returned False — event may not exist",
            )
        except Exception as exc:
            logger.error("[calendar_connector] delete_event failed: %s", exc)
            raise ConnectorError(self.name, str(exc)) from exc
