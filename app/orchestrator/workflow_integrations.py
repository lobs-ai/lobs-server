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
    Task,
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

def _deadline_priority_score(deadline: TrackerEntry, hours_until: float) -> int:
    """Compute a 0-100 priority score for deadline triage."""
    score = 0

    if hours_until <= 4:
        score += 60
    elif hours_until <= 24:
        score += 45
    elif hours_until <= 72:
        score += 30
    else:
        score += 10

    estimated = deadline.estimated_minutes or 0
    if estimated >= 240:
        score += 15
    elif estimated >= 120:
        score += 10
    elif estimated >= 60:
        score += 5

    commitment = (deadline.commitment_type or "").lower()
    if commitment == "interview":
        score += 15
    elif commitment == "class":
        score += 10
    elif commitment == "scrim":
        score += 8

    text = (deadline.raw_text or "").lower()
    if any(k in text for k in ["final", "onsite", "midterm", "exam", "submission"]):
        score += 10

    return max(0, min(100, score))


async def _ensure_escalation_task(db: AsyncSession, deadline: TrackerEntry, hours_until: float) -> str:
    """Create a concrete task for a deadline if one does not already exist."""
    if deadline.escalation_task_id:
        existing = await db.execute(select(Task).where(Task.id == deadline.escalation_task_id))
        if existing.scalar_one_or_none() is not None:
            return deadline.escalation_task_id

    due_local = deadline.due_date.astimezone(ET).strftime("%a %b %d %I:%M %p ET") if deadline.due_date else "unknown"
    next_action = deadline.next_action or "Block 45 minutes now and start the highest-risk subtask."

    task_id = str(uuid.uuid4())
    task = Task(
        id=task_id,
        title=f"🚨 Deadline escalation: {deadline.raw_text[:90]}",
        status="inbox",
        owner="lobs",
        notes=(
            f"Escalated automatically by Deadline Sentinel.\n"
            f"Due: {due_local} ({round(hours_until, 1)}h remaining)\n"
            f"Category: {deadline.category or 'uncategorized'}\n"
            f"Commitment: {deadline.commitment_type or 'unspecified'}\n"
            f"Next action: {next_action}"
        ),
        pinned=True,
        agent="project-manager",
        model_tier="small",
    )
    db.add(task)

    deadline.escalation_task_id = task_id
    deadline.last_escalated_at = datetime.now(timezone.utc)
    return task_id


async def check_deadlines(db: AsyncSession, worker_manager=None, context=None, **kw) -> dict[str, Any]:
    """Deadline Sentinel: T-72/T-24/T-4 reminders with escalation to task."""
    now = datetime.now(timezone.utc)
    window_end = now + timedelta(hours=72)

    result = await db.execute(
        select(TrackerEntry).where(
            and_(
                TrackerEntry.type == "deadline",
                TrackerEntry.due_date != None,
                TrackerEntry.due_date <= window_end,
                TrackerEntry.due_date >= now,
            )
        ).order_by(TrackerEntry.due_date).limit(50)
    )
    deadlines = result.scalars().all()

    if not deadlines:
        return {"deadlines": 0, "notified": 0, "escalated": 0, "alerts": []}

    windows = [(4, "T-4"), (24, "T-24"), (72, "T-72")]
    notified = 0
    escalated = 0
    alerts: list[dict[str, Any]] = []

    for dl in deadlines:
        due = dl.due_date
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        hours_until = (due - now).total_seconds() / 3600
        if hours_until < 0:
            continue

        window_label = None
        for threshold, label in windows:
            if hours_until <= threshold:
                window_label = label
                break

        if not window_label:
            continue

        priority_score = _deadline_priority_score(dl, hours_until)
        dl.priority_score = priority_score
        if not dl.next_action:
            dl.next_action = "Define the exact deliverable and ship the first meaningful draft in the next 45 minutes."

        deadline_key = f"{dl.id}-{due.date().isoformat()}-{window_label}"
        notif_type = f"deadline_reminder_{window_label.lower()}"

        existing = await db.execute(
            select(TrackerNotification).where(
                and_(
                    TrackerNotification.deadline_key == deadline_key,
                    TrackerNotification.notification_type == notif_type,
                )
            )
        )
        if existing.scalar_one_or_none() is not None:
            continue

        should_escalate = window_label == "T-4" or priority_score >= 80
        escalation_task_id = None
        if should_escalate:
            escalation_task_id = await _ensure_escalation_task(db, dl, hours_until)
            escalated += 1

        alerts.append({
            "id": dl.id,
            "text": dl.raw_text,
            "category": dl.category,
            "commitment_type": dl.commitment_type,
            "due": due.astimezone(ET).strftime("%a %I:%M %p"),
            "hours_until": round(hours_until, 1),
            "window": window_label,
            "priority_score": priority_score,
            "next_action": dl.next_action,
            "escalation_task_id": escalation_task_id,
        })

        db.add(TrackerNotification(
            id=str(uuid.uuid4()),
            deadline_key=deadline_key,
            notification_type=notif_type,
            message_summary=f"{window_label} ({priority_score}): {dl.raw_text}",
            cooldown_hours=12,
        ))
        notified += 1

    if notified:
        top = sorted(alerts, key=lambda a: a["priority_score"], reverse=True)[:5]
        lines = [
            "🛎️ **Deadline Sentinel**",
            f"Reminders sent: {notified} | Escalations: {escalated}",
        ]
        for a in top:
            suffix = f" → Task {a['escalation_task_id'][:8]}" if a.get("escalation_task_id") else ""
            lines.append(
                f"• {a['window']} | {a['text']} ({a['hours_until']}h, score {a['priority_score']}) — next: {a['next_action']}{suffix}"
            )

        db.add(InboxItem(
            id=str(uuid.uuid4()),
            title="🛎️ Deadline Sentinel Alerts",
            content="\n".join(lines),
            is_read=False,
            summary=f"deadline_sentinel:{now.date().isoformat()}",
        ))

        await db.commit()

    return {"deadlines": len(deadlines), "notified": notified, "escalated": escalated, "alerts": alerts}


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
