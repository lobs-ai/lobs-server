"""Google Calendar integration — two-calendar architecture.

- Lobs authenticates as thelobsbot@gmail.com (its own Google account)
- Reads Rafe's personal calendar (shared read-only with thelobsbot@gmail.com)
- Creates events on Lobs's own calendar and invites Rafe
- Replaces internal Mission Control calendar

Setup:
  1. Google Cloud Console → enable Google Calendar API
  2. Create OAuth2 Desktop credentials → download JSON
  3. Place at: ~/lobs-server/credentials/google_calendar.json
  4. Run: cd ~/lobs-server && python3 -m app.services.google_calendar --auth
     (sign in as thelobsbot@gmail.com)
  5. In Rafe's Google Calendar settings:
     Share with specific people → thelobsbot@gmail.com → "See all event details"
  6. Set env vars:
     RAFE_CALENDAR_ID=<rafe's email>
     RAFE_EMAIL=<rafe's email>  (for invites)
"""

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_BASE = os.path.join(os.path.dirname(__file__), "../..")
CREDENTIALS_FILE = os.environ.get("GOOGLE_CALENDAR_CREDENTIALS_FILE", os.path.join(_BASE, "credentials/google_calendar.json"))
TOKEN_FILE = os.environ.get("GOOGLE_CALENDAR_TOKEN_FILE", os.path.join(_BASE, "credentials/google_calendar_token.json"))
RAFE_CALENDAR_ID = os.environ.get("RAFE_CALENDAR_ID", "")
LOBS_CALENDAR_ID = os.environ.get("LOBS_CALENDAR_ID", "primary")
RAFE_EMAIL = os.environ.get("RAFE_EMAIL", "")

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def _get_calendar_service():
    """Authenticate as thelobsbot@gmail.com and return Calendar API service."""
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError:
        logger.error("[GCAL] pip install google-api-python-client google-auth-oauthlib")
        return None

    creds_path = os.path.abspath(CREDENTIALS_FILE)
    token_path = os.path.abspath(TOKEN_FILE)
    creds = None

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(creds_path):
                logger.error("[GCAL] No credentials at %s", creds_path)
                return None
            flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
            creds = flow.run_local_server(port=0)
        os.makedirs(os.path.dirname(token_path) or ".", exist_ok=True)
        with open(token_path, "w") as f:
            f.write(creds.to_json())

    return build("calendar", "v3", credentials=creds)


class GoogleCalendarService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self._svc = None

    def _api(self):
        if not self._svc:
            self._svc = _get_calendar_service()
        return self._svc

    async def _api_async(self):
        """Get the Calendar API service without blocking the event loop.

        The Google client library uses synchronous HTTP (httplib2) to fetch
        the API discovery document and refresh OAuth tokens. Running this in a
        thread executor prevents it from stalling the asyncio event loop.
        """
        if not self._svc:
            import asyncio
            self._svc = await asyncio.to_thread(_get_calendar_service)
        return self._svc

    async def _run(self, fn):
        """Run a synchronous Google API call (.execute()) in a thread executor."""
        import asyncio
        return await asyncio.to_thread(fn)

    def is_configured(self) -> bool:
        return os.path.exists(os.path.abspath(TOKEN_FILE)) or os.path.exists(os.path.abspath(CREDENTIALS_FILE))

    # ── Read Rafe's Calendar ─────────────────────────────────────────

    async def get_rafe_schedule(self, days: int = 7, max_results: int = 50) -> list[dict]:
        if not RAFE_CALENDAR_ID:
            logger.warning("[GCAL] RAFE_CALENDAR_ID not set")
            return []
        return await self._list_events(RAFE_CALENDAR_ID, days, max_results)

    async def get_rafe_free_busy(self, start: datetime, end: datetime) -> list[dict]:
        if not RAFE_CALENDAR_ID:
            return []
        svc = await self._api_async()
        if not svc:
            return []
        try:
            result = await self._run(svc.freebusy().query(body={
                "timeMin": start.isoformat(), "timeMax": end.isoformat(),
                "timeZone": "America/New_York",
                "items": [{"id": RAFE_CALENDAR_ID}],
            }).execute)
            return result.get("calendars", {}).get(RAFE_CALENDAR_ID, {}).get("busy", [])
        except Exception as e:
            logger.error("[GCAL] Free/busy failed: %s", e)
            return []

    # ── Write to Lobs's Calendar ─────────────────────────────────────

    async def get_lobs_events(self, days: int = 7) -> list[dict]:
        return await self._list_events(LOBS_CALENDAR_ID, days, 50)

    async def create_event(
        self, title: str, start: datetime, end: datetime | None = None,
        description: str = "", location: str = "",
        all_day: bool = False, invite_rafe: bool = True,
    ) -> dict | None:
        svc = await self._api_async()
        if not svc:
            return None
        if not end:
            end = start + timedelta(hours=1)

        body: dict[str, Any] = {"summary": title, "description": description, "location": location}
        if all_day:
            body["start"] = {"date": start.strftime("%Y-%m-%d")}
            body["end"] = {"date": end.strftime("%Y-%m-%d")}
        else:
            body["start"] = {"dateTime": start.isoformat(), "timeZone": "America/New_York"}
            body["end"] = {"dateTime": end.isoformat(), "timeZone": "America/New_York"}

        if invite_rafe and RAFE_EMAIL:
            body["attendees"] = [{"email": RAFE_EMAIL}]

        try:
            created = await self._run(svc.events().insert(
                calendarId=LOBS_CALENDAR_ID, body=body,
                sendUpdates="all" if invite_rafe and RAFE_EMAIL else "none",
            ).execute)
            logger.info("[GCAL] Created: %s (invited_rafe=%s)", title, invite_rafe and bool(RAFE_EMAIL))
            return _norm(created)
        except Exception as e:
            logger.error("[GCAL] Create failed: %s", e, exc_info=True)
            return None

    async def update_event(self, event_id: str, **fields) -> dict | None:
        svc = await self._api_async()
        if not svc:
            return None
        try:
            ev = await self._run(svc.events().get(calendarId=LOBS_CALENDAR_ID, eventId=event_id).execute)
            if "title" in fields and fields["title"]:
                ev["summary"] = fields["title"]
            if "description" in fields:
                ev["description"] = fields["description"]
            if "start" in fields and fields["start"]:
                ev["start"] = {"dateTime": fields["start"].isoformat(), "timeZone": "America/New_York"}
            if "end" in fields and fields["end"]:
                ev["end"] = {"dateTime": fields["end"].isoformat(), "timeZone": "America/New_York"}
            updated = await self._run(svc.events().update(
                calendarId=LOBS_CALENDAR_ID, eventId=event_id, body=ev, sendUpdates="all"
            ).execute)
            return _norm(updated)
        except Exception as e:
            logger.error("[GCAL] Update failed: %s", e)
            return None

    async def delete_event(self, event_id: str) -> bool:
        svc = await self._api_async()
        if not svc:
            return False
        try:
            await self._run(svc.events().delete(calendarId=LOBS_CALENDAR_ID, eventId=event_id, sendUpdates="all").execute)
            return True
        except Exception as e:
            logger.error("[GCAL] Delete failed: %s", e)
            return False

    # ── Sync to internal DB ──────────────────────────────────────────

    async def sync_to_internal(self, days: int = 14) -> dict:
        from app.models import ScheduledEvent

        all_events = []
        for cal_id, label in [(RAFE_CALENDAR_ID, "rafe"), (LOBS_CALENDAR_ID, "lobs")]:
            if not cal_id:
                continue
            events = await self._list_events(cal_id, days, 100)
            for e in events:
                e["_source"] = label
            all_events.extend(events)

        if not all_events:
            return {"fetched": 0, "created": 0, "updated": 0}

        created = updated = 0
        for ev in all_events:
            gcal_id = ev.get("gcal_id")
            if not gcal_id:
                continue
            result = await self.db.execute(select(ScheduledEvent).where(ScheduledEvent.external_id == gcal_id))
            existing = result.scalar_one_or_none()
            start_dt = _parse_dt(ev.get("start"))
            end_dt = _parse_dt(ev.get("end"))

            if existing:
                if existing.title != ev["title"] or (start_dt and existing.scheduled_at != start_dt):
                    existing.title = ev["title"]
                    existing.description = ev.get("description", "")
                    if start_dt:
                        existing.scheduled_at = start_dt
                    if end_dt:
                        existing.end_at = end_dt
                    updated += 1
            else:
                if not start_dt:
                    continue
                self.db.add(ScheduledEvent(
                    id=str(uuid.uuid4()), title=ev["title"],
                    description=ev.get("description", ""),
                    event_type="meeting", scheduled_at=start_dt, end_at=end_dt,
                    all_day=ev.get("all_day", False), status="pending",
                    target_type="self", external_id=gcal_id,
                    external_source=f"google_calendar:{ev.get('_source', 'unknown')}",
                ))
                created += 1
        await self.db.commit()
        return {"fetched": len(all_events), "created": created, "updated": updated}

    # ── Internal ─────────────────────────────────────────────────────

    async def _list_events(self, calendar_id: str, days: int, max_results: int) -> list[dict]:
        svc = await self._api_async()
        if not svc:
            return []
        now = datetime.now(timezone.utc).isoformat()
        end = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
        try:
            result = await self._run(svc.events().list(
                calendarId=calendar_id, timeMin=now, timeMax=end,
                maxResults=max_results, singleEvents=True, orderBy="startTime",
            ).execute)
            return [_norm(e) for e in result.get("items", [])]
        except Exception as e:
            logger.error("[GCAL] List events failed for %s: %s", calendar_id, e)
            return []


def _norm(event: dict) -> dict:
    s = event.get("start", {})
    e = event.get("end", {})
    return {
        "gcal_id": event.get("id"),
        "title": event.get("summary", "(No title)"),
        "description": event.get("description", ""),
        "location": event.get("location", ""),
        "start": s.get("dateTime") or s.get("date"),
        "end": e.get("dateTime") or e.get("date"),
        "all_day": "date" in s and "dateTime" not in s,
        "status": event.get("status", "confirmed"),
        "html_link": event.get("htmlLink", ""),
        "attendees": [a.get("email") for a in event.get("attendees", [])],
        "organizer": event.get("organizer", {}).get("email", ""),
    }


def _parse_dt(dt_str: str | None) -> datetime | None:
    if not dt_str:
        return None
    try:
        if "T" in dt_str:
            from dateutil.parser import parse
            return parse(dt_str)
        return datetime.strptime(dt_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except Exception:
        return None


if __name__ == "__main__":
    import sys
    if "--auth" in sys.argv:
        print("🔐 Google Calendar OAuth — sign in as thelobsbot@gmail.com")
        print(f"   Credentials: {os.path.abspath(CREDENTIALS_FILE)}")
        print(f"   Token save:  {os.path.abspath(TOKEN_FILE)}")
        svc = _get_calendar_service()
        if svc:
            cals = svc.calendarList().list().execute().get("items", [])
            print(f"\n✅ Authenticated! {len(cals)} calendars:")
            for c in cals:
                print(f"   • {c.get('summary','?')} ({c.get('id')}) [{c.get('accessRole')}]")
            print("\nNext steps:")
            print("  1. Set RAFE_CALENDAR_ID=<rafe's email> in env")
            print("  2. Set RAFE_EMAIL=<rafe's email> in env")
            print("  3. Have Rafe share his calendar with thelobsbot@gmail.com (read-only)")
        else:
            print("❌ Auth failed")
    else:
        print("Usage: python3 -m app.services.google_calendar --auth")
