"""Integration status and actions for calendar, email."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.auth import require_auth

router = APIRouter(prefix="/integrations", tags=["integrations"])


@router.get("/status", dependencies=[Depends(require_auth)])
async def integration_status(db: AsyncSession = Depends(get_db)):
    """Check which integrations are configured and available."""
    status = {}

    # Google Calendar
    try:
        from app.services.google_calendar import GoogleCalendarService
        gcal = GoogleCalendarService(db)
        status["google_calendar"] = {
            "configured": gcal.is_configured(),
            "status": "ready" if gcal.is_configured() else "not_configured",
        }
    except Exception as e:
        status["google_calendar"] = {"configured": False, "status": "error", "error": str(e)}

    # Email
    try:
        from app.services.email_service import EmailService
        email = EmailService(db)
        status["email"] = {
            "configured": email.is_configured(),
            "mode": email._detect_mode(),
            "status": "ready" if email.is_configured() else "not_configured",
        }
    except Exception as e:
        status["email"] = {"configured": False, "status": "error", "error": str(e)}

    return status


@router.post("/calendar/sync", dependencies=[Depends(require_auth)])
async def sync_calendar(db: AsyncSession = Depends(get_db)):
    """Manually trigger Google Calendar sync."""
    from app.services.google_calendar import GoogleCalendarService
    svc = GoogleCalendarService(db)
    if not svc.is_configured():
        return {"status": "error", "error": "Google Calendar not configured"}
    result = await svc.sync_to_internal(days=14)
    return {"status": "ok", **result}


@router.get("/calendar/events", dependencies=[Depends(require_auth)])
async def calendar_events(days: int = 7, db: AsyncSession = Depends(get_db)):
    """Get upcoming Google Calendar events."""
    from app.services.google_calendar import GoogleCalendarService
    svc = GoogleCalendarService(db)
    if not svc.is_configured():
        return {"status": "error", "error": "Google Calendar not configured", "events": []}
    events = await svc.fetch_upcoming_events(days=days)
    return {"status": "ok", "events": events}


@router.get("/email/unread", dependencies=[Depends(require_auth)])
async def email_unread(max_results: int = 10, db: AsyncSession = Depends(get_db)):
    """Get unread emails."""
    from app.services.email_service import EmailService
    svc = EmailService(db)
    if not svc.is_configured():
        return {"status": "error", "error": "Email not configured", "emails": []}
    emails = await svc.get_unread(max_results=max_results)
    return {"status": "ok", "emails": emails}


@router.get("/email/search", dependencies=[Depends(require_auth)])
async def email_search(q: str, max_results: int = 10, db: AsyncSession = Depends(get_db)):
    """Search emails."""
    from app.services.email_service import EmailService
    svc = EmailService(db)
    if not svc.is_configured():
        return {"status": "error", "error": "Email not configured", "emails": []}
    emails = await svc.search(query=q, max_results=max_results)
    return {"status": "ok", "emails": emails}
