"""Event scheduler for calendar system.

Checks for due events and fires them (creates tasks, logs reminders, etc.).
"""

import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional
from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ScheduledEvent, Task

logger = logging.getLogger(__name__)


def compute_next_fire_time(cron_rule: str, base_time: Optional[datetime] = None) -> Optional[datetime]:
    """
    Compute the next fire time from a cron expression.
    
    Uses croniter if available, otherwise falls back to simple parsing.
    """
    if base_time is None:
        base_time = datetime.now(timezone.utc)
    
    try:
        from croniter import croniter
        cron = croniter(cron_rule, base_time)
        next_time = cron.get_next(datetime)
        return next_time.replace(tzinfo=timezone.utc) if next_time.tzinfo is None else next_time
    except ImportError:
        logger.warning("croniter not installed, using simple cron parser")
        return _simple_cron_parse(cron_rule, base_time)
    except Exception as e:
        logger.error(f"Failed to parse cron rule '{cron_rule}': {e}")
        raise ValueError(f"Invalid cron expression: {cron_rule}")


def _simple_cron_parse(cron_rule: str, base_time: datetime) -> Optional[datetime]:
    """
    Simple cron parser for basic cases without croniter.
    
    Supports: "M H * * D" format
    - M = minute (0-59)
    - H = hour (0-23)
    - D = day of week (0-6, 0=Sunday) or * for every day
    
    Examples:
    - "0 9 * * *" = every day at 9am
    - "0 9 * * 1-5" = weekdays at 9am (not fully supported here, just returns daily)
    """
    parts = cron_rule.split()
    if len(parts) != 5:
        raise ValueError("Cron expression must have 5 parts: M H DOM MON DOW")
    
    minute_str, hour_str, _, _, dow_str = parts
    
    try:
        minute = int(minute_str) if minute_str != "*" else 0
        hour = int(hour_str) if hour_str != "*" else 0
    except ValueError:
        raise ValueError("Minute and hour must be integers or *")
    
    # Start from base_time
    next_time = base_time.replace(minute=minute, hour=hour, second=0, microsecond=0)
    
    # If the time has already passed today, move to tomorrow
    if next_time <= base_time:
        next_time += timedelta(days=1)
    
    return next_time


class EventScheduler:
    """Scheduler that checks for due events and fires them."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def check_due_events(self) -> dict:
        """
        Check for events that are due and fire them.
        
        Returns a summary of what was processed.
        """
        now = datetime.now(timezone.utc)
        
        # Find pending events that are due
        pending_query = (
            select(ScheduledEvent)
            .where(
                and_(
                    ScheduledEvent.status == "pending",
                    ScheduledEvent.scheduled_at <= now
                )
            )
        )
        
        # Find recurring events that are due
        recurring_query = (
            select(ScheduledEvent)
            .where(
                and_(
                    ScheduledEvent.status == "recurring",
                    ScheduledEvent.next_fire_at <= now
                )
            )
        )
        
        # Execute both queries
        pending_result = await self.db.execute(pending_query)
        recurring_result = await self.db.execute(recurring_query)
        
        pending_events = pending_result.scalars().all()
        recurring_events = recurring_result.scalars().all()
        
        all_due_events = list(pending_events) + list(recurring_events)
        
        fired_count = 0
        reminder_count = 0
        task_count = 0
        
        for event in all_due_events:
            try:
                await self.fire_event(event)
                fired_count += 1
                
                if event.event_type == "reminder":
                    reminder_count += 1
                elif event.event_type == "task":
                    task_count += 1
                    
            except Exception as e:
                logger.error(
                    f"Failed to fire event {event.id} ({event.title}): {e}",
                    exc_info=True
                )
        
        if fired_count > 0:
            logger.info(
                f"[SCHEDULER] Fired {fired_count} event(s): "
                f"{reminder_count} reminder(s), {task_count} task(s)"
            )
        
        return {
            "checked_at": now.isoformat(),
            "total_fired": fired_count,
            "reminders": reminder_count,
            "tasks": task_count
        }
    
    async def fire_event(self, event: ScheduledEvent) -> None:
        """
        Fire a single event.
        
        Actions depend on event_type:
        - reminder: Log it (app will poll for notifications)
        - task: Create a new Task in the database
        - meeting: Just log it (informational)
        """
        now = datetime.now(timezone.utc)
        
        logger.info(
            f"[SCHEDULER] Firing event: {event.title} "
            f"(type={event.event_type}, target={event.target_type})"
        )
        
        if event.event_type == "reminder":
            # For reminders, just log and mark as fired
            # The frontend will poll for fired reminders and show notifications
            logger.info(f"[SCHEDULER] REMINDER: {event.title}")
            if event.description:
                logger.info(f"  Description: {event.description}")
        
        elif event.event_type == "task":
            # Create a new Task in the database
            if not event.task_project_id:
                logger.warning(
                    f"[SCHEDULER] Event {event.id} is type 'task' but has no task_project_id"
                )
            else:
                task = Task(
                    id=str(uuid.uuid4()),
                    title=event.title,
                    status="todo",
                    project_id=event.task_project_id,
                    notes=event.task_notes or event.description,
                    owner=event.target_type if event.target_type != "orchestrator" else None,
                    agent=event.target_agent,
                )
                
                if event.task_priority:
                    # Store priority in notes if not a direct field
                    priority_note = f"Priority: {event.task_priority}\n\n"
                    task.notes = priority_note + (task.notes or "")
                
                self.db.add(task)
                logger.info(
                    f"[SCHEDULER] Created task '{event.title}' in project {event.task_project_id}"
                )
        
        elif event.event_type == "meeting":
            # For meetings, just log (calendar display only)
            logger.info(f"[SCHEDULER] MEETING: {event.title}")
            if event.end_at:
                duration = (event.end_at - event.scheduled_at).total_seconds() / 60
                logger.info(f"  Duration: {duration} minutes")
        
        # Update event status
        event.last_fired_at = now
        event.fire_count += 1
        
        # Handle recurring events
        if event.recurrence_rule and event.status == "recurring":
            try:
                # Compute next fire time
                next_fire = compute_next_fire_time(event.recurrence_rule, now)
                
                # Check if recurrence has ended
                if event.recurrence_end and next_fire > event.recurrence_end:
                    event.status = "fired"
                    event.next_fire_at = None
                    logger.info(
                        f"[SCHEDULER] Recurring event {event.id} has ended (past recurrence_end)"
                    )
                else:
                    event.next_fire_at = next_fire
                    logger.info(
                        f"[SCHEDULER] Recurring event {event.id} will fire again at {next_fire}"
                    )
            except Exception as e:
                logger.error(
                    f"[SCHEDULER] Failed to compute next fire time for event {event.id}: {e}"
                )
                event.status = "fired"
        else:
            # One-time event, mark as fired
            event.status = "fired"
        
        await self.db.flush()
