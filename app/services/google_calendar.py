"""Google Calendar integration service.

Uses Google Calendar API via service account or OAuth2.
Syncs events bidirectionally with the internal calendar.

Setup:
  1. Create a Google Cloud project
  2. Enable Google Calendar API
  3. Create OAuth2 credentials (or service account)
  4. Set environment variables:
     - GOOGLE_CALENDAR_CREDENTIALS_FILE: path to credentials.json
     - GOOGLE_CALENDAR_TOKEN_FILE: path to token.json (auto-created on first auth)
     - GOOGLE_CALENDAR_ID: calendar ID to sync (default: "primary")
"""

import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

CREDENTIALS_FILE = os.environ.get("GOOGLE_CALENDAR_CREDENTIALS_FILE", "credentials/google_calendar.json")
TOKEN_FILE = os.environ.get("GOOGLE_CALENDAR_TOKEN_FILE", "credentials/google_calendar_token.json")
CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID", "primary")

# Scopes needed
SCOPES = ["https://www.googleapis.com/auth/calendar"]


def _get_calendar_service():
    """Build and return an authorized Google Calendar API service."""
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError:
        logger.error("[GCAL] google-api-python-client not installed. Run: pip install google-api-python-client google-auth-oauthlib")
        return None

    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_FILE):
                logger.error("[GCAL] No credentials file at %s. Set up Google Calendar API first.", CREDENTIALS_FILE)
                return None
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        # Save token for next run
        os.makedirs(os.path.dirname(TOKEN_FILE) or ".", exist_ok=True)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    return build("calendar", "v3", credentials=creds)


class GoogleCalendarService:
    """Sync between Google Calendar and internal calendar."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self._service = None

    def _get_service(self):
        if self._service is None:
            self._service = _get_calendar_service()
        return self._service

    def is_configured(self) -> bool:
        """Check if Google Calendar credentials exist."""
        return os.path.exists(CREDENTIALS_FILE) or os.path.exists(TOKEN_FILE)

    async def fetch_upcoming_events(self, days: int = 7, max_results: int = 50) -> list[dict[str, Any]]:
        """Fetch upcoming events from Google Calendar."""
        service = self._get_service()
        if not service:
            return []

        now = datetime.now(timezone.utc).isoformat()
        end = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()

        try:
            events_result = service.events().list(
                calendarId=CALENDAR_ID,
                timeMin=now,
                timeMax=end,
                maxResults=max_results,
                singleEvents=True,
                orderBy="startTime",
            ).execute()

            events = events_result.get("items", [])
            return [self._normalize_event(e) for e in events]
        except Exception as e:
            logger.error("[GCAL] Failed to fetch events: %s", e, exc_info=True)
            return []

    async def create_event(
        self,
        title: str,
        start: datetime,
        end: datetime | None = None,
        description: str = "",
        location: str = "",
        all_day: bool = False,
    ) -> dict[str, Any] | None:
        """Create an event on Google Calendar."""
        service = self._get_service()
        if not service:
            return None

        if not end:
            end = start + timedelta(hours=1)

        event_body: dict[str, Any] = {
            "summary": title,
            "description": description,
            "location": location,
        }

        if all_day:
            event_body["start"] = {"date": start.strftime("%Y-%m-%d")}
            event_body["end"] = {"date": end.strftime("%Y-%m-%d")}
        else:
            event_body["start"] = {"dateTime": start.isoformat(), "timeZone": "America/New_York"}
            event_body["end"] = {"dateTime": end.isoformat(), "timeZone": "America/New_York"}

        try:
            created = service.events().insert(calendarId=CALENDAR_ID, body=event_body).execute()
            logger.info("[GCAL] Created event: %s (%s)", title, created.get("id"))
            return self._normalize_event(created)
        except Exception as e:
            logger.error("[GCAL] Failed to create event: %s", e, exc_info=True)
            return None

    async def sync_to_internal(self, days: int = 14) -> dict[str, Any]:
        """Sync Google Calendar events to internal calendar DB."""
        from app.models import ScheduledEvent

        events = await self.fetch_upcoming_events(days=days)
        if not events:
            return {"fetched": 0, "created": 0, "updated": 0}

        created = 0
        updated = 0

        for event in events:
            gcal_id = event.get("gcal_id")
            if not gcal_id:
                continue

            # Check if we already have this event
            result = await self.db.execute(
                select(ScheduledEvent).where(ScheduledEvent.external_id == gcal_id)
            )
            existing = result.scalar_one_or_none()

            if existing:
                # Update if changed
                if existing.title != event["title"] or existing.scheduled_at != event.get("start"):
                    existing.title = event["title"]
                    existing.description = event.get("description", "")
                    existing.scheduled_at = event.get("start")
                    existing.end_at = event.get("end")
                    updated += 1
            else:
                # Create new internal event
                new_event = ScheduledEvent(
                    id=str(uuid.uuid4()),
                    title=event["title"],
                    description=event.get("description", ""),
                    event_type="meeting",
                    scheduled_at=event.get("start"),
                    end_at=event.get("end"),
                    all_day=event.get("all_day", False),
                    status="pending",
                    target_type="self",
                    external_id=gcal_id,
                    external_source="google_calendar",
                )
                self.db.add(new_event)
                created += 1

        await self.db.commit()
        return {"fetched": len(events), "created": created, "updated": updated}

    @staticmethod
    def _normalize_event(event: dict) -> dict[str, Any]:
        """Normalize a Google Calendar event to our internal format."""
        start = event.get("start", {})
        end = event.get("end", {})

        start_dt = start.get("dateTime") or start.get("date")
        end_dt = end.get("dateTime") or end.get("date")
        all_day = "date" in start and "dateTime" not in start

        return {
            "gcal_id": event.get("id"),
            "title": event.get("summary", "(No title)"),
            "description": event.get("description", ""),
            "location": event.get("location", ""),
            "start": start_dt,
            "end": end_dt,
            "all_day": all_day,
            "status": event.get("status", "confirmed"),
            "html_link": event.get("htmlLink", ""),
            "attendees": [a.get("email") for a in event.get("attendees", [])],
        }
