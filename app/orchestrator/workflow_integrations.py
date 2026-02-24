"""Workflow python_call handlers for calendar, email, and work tracker integrations.

Callables:
  - calendar.sync_google: Sync Google Calendar → internal calendar
  - calendar.check_upcoming: Check upcoming events and create reminders
  - email.check_inbox: Check for unread emails, summarize important ones
  - email.send: Send an email (requires explicit approval gate)
  - tracker.check_deadlines: Check approaching deadlines and notify
  - tracker.daily_summary: Generate daily work summary from tracker entries
"""

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ScheduledEvent,
    TrackerEntry,
    TrackerNotification,
    InboxItem,
)
from app.orchestrator.config import GATEWAY_URL, GATEWAY_TOKEN, GATEWAY_SESSION_KEY

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")


# ══════════════════════════════════════════════════════════════════════
# CALENDAR
# ══════════════════════════════════════════════════════════════════════

async def sync_google_calendar(db: AsyncSession, worker_manager=None, context=None, **kw) -> dict[str, Any]:
    """Sync Google Calendar events to internal calendar."""
    try:
        from app.services.google_calendar import GoogleCalendarService
        svc = GoogleCalendarService(db)
        if not svc.is_configured():
            return {"status": "skipped", "reason": "Google Calendar not configured"}
        result = await svc.sync_to_internal(days=14)
        logger.info("[CALENDAR] Synced: %s", result)
        return {"status": "ok", **result}
    except Exception as e:
        logger.error("[CALENDAR] Sync failed: %s", e, exc_info=True)
        return {"status": "error", "error": str(e)}


async def check_upcoming_events(db: AsyncSession, worker_manager=None, context=None, **kw) -> dict[str, Any]:
    """Check upcoming calendar events in the next 24h and create action items."""
    now = datetime.now(timezone.utc)
    window_end = now + timedelta(hours=24)

    result = await db.execute(
        select(ScheduledEvent).where(
            and_(
                ScheduledEvent.scheduled_at >= now,
                ScheduledEvent.scheduled_at <= window_end,
                ScheduledEvent.status.in_(["pending", "recurring"]),
            )
        ).order_by(ScheduledEvent.scheduled_at).limit(20)
    )
    events = result.scalars().all()

    if not events:
        return {"upcoming": 0, "alerts": 0}

    alerts = []
    now_et = now.astimezone(ET)

    for event in events:
        event_time = event.scheduled_at
        if event_time.tzinfo is None:
            event_time = event_time.replace(tzinfo=timezone.utc)
        hours_until = (event_time - now).total_seconds() / 3600

        # Alert thresholds
        if hours_until <= 1:
            alerts.append({
                "event": event.title,
                "time": event_time.astimezone(ET).strftime("%I:%M %p"),
                "hours_until": round(hours_until, 1),
                "type": event.event_type,
                "urgency": "imminent",
            })
        elif hours_until <= 4:
            alerts.append({
                "event": event.title,
                "time": event_time.astimezone(ET).strftime("%I:%M %p"),
                "hours_until": round(hours_until, 1),
                "type": event.event_type,
                "urgency": "soon",
            })

    return {"upcoming": len(events), "alerts": len(alerts), "alert_details": alerts}


# ══════════════════════════════════════════════════════════════════════
# EMAIL
# ══════════════════════════════════════════════════════════════════════

async def check_email_inbox(db: AsyncSession, worker_manager=None, context=None, **kw) -> dict[str, Any]:
    """Check for unread emails and create inbox items for important ones."""
    try:
        from app.services.email_service import EmailService
        svc = EmailService(db)
        if not svc.is_configured():
            return {"status": "skipped", "reason": "Email not configured"}

        unread = await svc.get_unread(max_results=10)
        if not unread:
            return {"status": "ok", "unread": 0, "actioned": 0}

        actioned = 0
        for email_msg in unread:
            if email_msg.get("error"):
                continue

            subject = email_msg.get("subject", "(no subject)")
            sender = email_msg.get("from", "unknown")
            snippet = email_msg.get("snippet", email_msg.get("body", ""))[:500]

            # Create inbox item for each unread email
            db.add(InboxItem(
                id=str(uuid.uuid4()),
                title=f"📧 {subject}",
                content=f"From: {sender}\n\n{snippet}",
                is_read=False,
                source="email",
                summary=f"email:{email_msg.get('id', '')}",
            ))
            actioned += 1

        if actioned:
            await db.commit()

        return {"status": "ok", "unread": len(unread), "actioned": actioned}

    except Exception as e:
        logger.error("[EMAIL] Check inbox failed: %s", e, exc_info=True)
        return {"status": "error", "error": str(e)}


async def send_email(db: AsyncSession, worker_manager=None, context=None, **kw) -> dict[str, Any]:
    """Send an email. Requires explicit parameters in context."""
    try:
        from app.services.email_service import EmailService
        svc = EmailService(db)
        if not svc.is_configured():
            return {"status": "skipped", "reason": "Email not configured"}

        to = kw.get("to") or (context or {}).get("email_to", "")
        subject = kw.get("subject") or (context or {}).get("email_subject", "")
        body = kw.get("body") or (context or {}).get("email_body", "")

        if not to or not subject:
            return {"status": "error", "error": "Missing 'to' or 'subject'"}

        result = await svc.send(to=to, subject=subject, body=body)
        return {"status": "sent" if result else "failed", **(result or {})}

    except Exception as e:
        logger.error("[EMAIL] Send failed: %s", e, exc_info=True)
        return {"status": "error", "error": str(e)}


# ══════════════════════════════════════════════════════════════════════
# WORK TRACKER
# ══════════════════════════════════════════════════════════════════════

async def check_deadlines(db: AsyncSession, worker_manager=None, context=None, **kw) -> dict[str, Any]:
    """Check for approaching deadlines and send notifications if needed."""
    now = datetime.now(timezone.utc)
    now_et = now.astimezone(ET)

    # Find deadlines in the next 48 hours
    window_end = now + timedelta(hours=48)

    result = await db.execute(
        select(TrackerEntry).where(
            and_(
                TrackerEntry.type == "deadline",
                TrackerEntry.due_date != None,
                TrackerEntry.due_date <= window_end,
                TrackerEntry.due_date >= now,
            )
        ).order_by(TrackerEntry.due_date).limit(20)
    )
    deadlines = result.scalars().all()

    if not deadlines:
        return {"deadlines": 0, "notified": 0}

    notified = 0
    alerts = []

    for dl in deadlines:
        due = dl.due_date
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        hours_until = (due - now).total_seconds() / 3600
        deadline_key = f"{dl.category or 'unknown'}-{dl.id[:8]}-{due.date().isoformat()}"

        # Check notification cooldown
        existing = await db.execute(
            select(TrackerNotification).where(
                and_(
                    TrackerNotification.deadline_key == deadline_key,
                    TrackerNotification.notification_type == "deadline_reminder",
                )
            )
        )
        if existing.scalar_one_or_none():
            continue  # Already notified

        urgency = "urgent" if hours_until <= 6 else "approaching" if hours_until <= 24 else "upcoming"

        alerts.append({
            "text": dl.raw_text,
            "category": dl.category,
            "due": due.astimezone(ET).strftime("%a %I:%M %p"),
            "hours_until": round(hours_until, 1),
            "urgency": urgency,
        })

        # Record notification
        db.add(TrackerNotification(
            id=str(uuid.uuid4()),
            deadline_key=deadline_key,
            notification_type="deadline_reminder",
            message_summary=f"{urgency}: {dl.raw_text} due in {round(hours_until)}h",
            cooldown_hours=12,
        ))
        notified += 1

    if notified:
        await db.commit()

    return {"deadlines": len(deadlines), "notified": notified, "alerts": alerts}


async def daily_work_summary(db: AsyncSession, worker_manager=None, context=None, **kw) -> dict[str, Any]:
    """Generate daily work summary from tracker entries and push as inbox item."""
    now_et = datetime.now(timezone.utc).astimezone(ET)
    today_start = now_et.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today_start - timedelta(days=1)

    # Get yesterday's work sessions
    result = await db.execute(
        select(TrackerEntry).where(
            and_(
                TrackerEntry.type == "work_session",
                TrackerEntry.created_at >= yesterday_start,
                TrackerEntry.created_at < today_start,
            )
        ).order_by(TrackerEntry.created_at)
    )
    sessions = result.scalars().all()

    # Get today's deadlines
    today_end = today_start + timedelta(days=1)
    result2 = await db.execute(
        select(TrackerEntry).where(
            and_(
                TrackerEntry.type == "deadline",
                TrackerEntry.due_date >= today_start,
                TrackerEntry.due_date < today_end,
            )
        ).order_by(TrackerEntry.due_date)
    )
    today_deadlines = result2.scalars().all()

    total_minutes = sum(s.duration or 0 for s in sessions)
    categories = {}
    for s in sessions:
        cat = s.category or "uncategorized"
        categories[cat] = categories.get(cat, 0) + (s.duration or 0)

    summary_lines = [
        f"📊 **Daily Summary** — {yesterday_start.strftime('%A, %b %d')}",
        f"Total work: {total_minutes // 60}h {total_minutes % 60}m across {len(sessions)} sessions",
    ]
    if categories:
        summary_lines.append("\n**By category:**")
        for cat, mins in sorted(categories.items(), key=lambda x: -x[1]):
            summary_lines.append(f"  • {cat}: {mins // 60}h {mins % 60}m")

    if today_deadlines:
        summary_lines.append(f"\n**Today's deadlines ({len(today_deadlines)}):**")
        for dl in today_deadlines:
            due_time = dl.due_date.astimezone(ET).strftime("%I:%M %p") if dl.due_date else "?"
            summary_lines.append(f"  • {dl.raw_text} @ {due_time}")

    summary = "\n".join(summary_lines)

    if sessions or today_deadlines:
        db.add(InboxItem(
            id=str(uuid.uuid4()),
            title=f"📊 Daily Summary — {yesterday_start.strftime('%b %d')}",
            content=summary,
            is_read=False,
            source="tracker",
            summary=f"daily_summary:{yesterday_start.date().isoformat()}",
        ))
        await db.commit()

    return {
        "sessions": len(sessions),
        "total_minutes": total_minutes,
        "categories": categories,
        "deadlines_today": len(today_deadlines),
        "summary_created": bool(sessions or today_deadlines),
    }
