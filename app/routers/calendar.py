"""Calendar/scheduling API endpoints."""

from typing import Optional
from datetime import datetime, date, timedelta, timezone
from collections import defaultdict
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import ScheduledEvent as ScheduledEventModel, Task as TaskModel
from app.schemas import (
    ScheduledEventCreate,
    ScheduledEventUpdate,
    ScheduledEventResponse,
    ScheduledEventList,
    CalendarView,
    CalendarDayEvents,
)
from app.config import settings
from app.auth import require_auth

router = APIRouter(prefix="/calendar", tags=["calendar"])


@router.post("/events", dependencies=[Depends(require_auth)])
async def create_event(
    event: ScheduledEventCreate,
    db: AsyncSession = Depends(get_db)
) -> ScheduledEventResponse:
    """Create a new scheduled event."""
    # Validate event_type
    if event.event_type not in ["reminder", "task", "meeting"]:
        raise HTTPException(
            status_code=400,
            detail="event_type must be one of: reminder, task, meeting"
        )
    
    # Validate target_type
    if event.target_type not in ["self", "agent", "orchestrator"]:
        raise HTTPException(
            status_code=400,
            detail="target_type must be one of: self, agent, orchestrator"
        )
    
    # If event_type is task, require task_project_id
    if event.event_type == "task" and not event.task_project_id:
        raise HTTPException(
            status_code=400,
            detail="task_project_id is required for event_type='task'"
        )
    
    # If target_type is agent, require target_agent
    if event.target_type == "agent" and not event.target_agent:
        raise HTTPException(
            status_code=400,
            detail="target_agent is required for target_type='agent'"
        )
    
    # Set initial status based on recurrence
    initial_status = "recurring" if event.recurrence_rule else "pending"
    
    # Compute next_fire_at for recurring events
    next_fire_at = None
    if event.recurrence_rule:
        try:
            from app.orchestrator.scheduler import compute_next_fire_time
            next_fire_at = compute_next_fire_time(
                event.recurrence_rule,
                event.scheduled_at
            )
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid recurrence_rule: {str(e)}"
            )
    
    db_event = ScheduledEventModel(
        **event.model_dump(),
        status=initial_status,
        next_fire_at=next_fire_at,
        fire_count=0
    )
    db.add(db_event)
    await db.flush()
    await db.refresh(db_event)
    return ScheduledEventResponse.model_validate(db_event)


@router.get("/events", dependencies=[Depends(require_auth)])
async def list_events(
    limit: int = Query(default=settings.DEFAULT_LIMIT, le=settings.MAX_LIMIT),
    offset: int = 0,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    event_type: Optional[str] = None,
    status: Optional[str] = None,
    target_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
) -> ScheduledEventList:
    """List scheduled events with filtering."""
    query = select(ScheduledEventModel)
    
    filters = []
    if start_date:
        filters.append(ScheduledEventModel.scheduled_at >= start_date)
    if end_date:
        filters.append(ScheduledEventModel.scheduled_at <= end_date)
    if event_type:
        filters.append(ScheduledEventModel.event_type == event_type)
    if status:
        filters.append(ScheduledEventModel.status == status)
    if target_type:
        filters.append(ScheduledEventModel.target_type == target_type)
    
    if filters:
        query = query.where(and_(*filters))
    
    # Get total count
    count_query = select(ScheduledEventModel)
    if filters:
        count_query = count_query.where(and_(*filters))
    count_result = await db.execute(count_query)
    total = len(count_result.scalars().all())
    
    # Get paginated results
    query = query.order_by(ScheduledEventModel.scheduled_at).offset(offset).limit(limit)
    result = await db.execute(query)
    events = result.scalars().all()
    
    return ScheduledEventList(
        events=[ScheduledEventResponse.model_validate(e) for e in events],
        total=total
    )


@router.get("/events/{event_id}", dependencies=[Depends(require_auth)])
async def get_event(
    event_id: str,
    db: AsyncSession = Depends(get_db)
) -> ScheduledEventResponse:
    """Get a single scheduled event."""
    result = await db.execute(
        select(ScheduledEventModel).where(ScheduledEventModel.id == event_id)
    )
    event = result.scalar_one_or_none()
    
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    
    return ScheduledEventResponse.model_validate(event)


@router.put("/events/{event_id}", dependencies=[Depends(require_auth)])
async def update_event(
    event_id: str,
    update: ScheduledEventUpdate,
    db: AsyncSession = Depends(get_db)
) -> ScheduledEventResponse:
    """Update a scheduled event."""
    result = await db.execute(
        select(ScheduledEventModel).where(ScheduledEventModel.id == event_id)
    )
    event = result.scalar_one_or_none()
    
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    
    # Update fields
    for field, value in update.model_dump(exclude_unset=True).items():
        setattr(event, field, value)
    
    # Recompute next_fire_at if recurrence_rule changed
    if update.recurrence_rule is not None and event.recurrence_rule:
        try:
            from app.orchestrator.scheduler import compute_next_fire_time
            event.next_fire_at = compute_next_fire_time(
                event.recurrence_rule,
                event.scheduled_at
            )
            event.status = "recurring"
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid recurrence_rule: {str(e)}"
            )
    
    await db.flush()
    await db.refresh(event)
    return ScheduledEventResponse.model_validate(event)


@router.delete("/events/{event_id}", dependencies=[Depends(require_auth)])
async def delete_event(
    event_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Delete a scheduled event."""
    result = await db.execute(
        select(ScheduledEventModel).where(ScheduledEventModel.id == event_id)
    )
    event = result.scalar_one_or_none()
    
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    
    await db.delete(event)
    return {"status": "deleted"}


@router.post("/events/{event_id}/cancel", dependencies=[Depends(require_auth)])
async def cancel_event(
    event_id: str,
    db: AsyncSession = Depends(get_db)
) -> ScheduledEventResponse:
    """Cancel a scheduled event (soft delete)."""
    result = await db.execute(
        select(ScheduledEventModel).where(ScheduledEventModel.id == event_id)
    )
    event = result.scalar_one_or_none()
    
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    
    event.status = "cancelled"
    await db.flush()
    await db.refresh(event)
    return ScheduledEventResponse.model_validate(event)


@router.post("/events/{event_id}/fire", dependencies=[Depends(require_auth)])
async def fire_event(
    event_id: str,
    db: AsyncSession = Depends(get_db)
) -> ScheduledEventResponse:
    """Manually trigger an event."""
    result = await db.execute(
        select(ScheduledEventModel).where(ScheduledEventModel.id == event_id)
    )
    event = result.scalar_one_or_none()
    
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    
    # Import scheduler to fire the event
    from app.orchestrator.scheduler import EventScheduler
    scheduler = EventScheduler(db)
    await scheduler.fire_event(event)
    
    await db.flush()
    await db.refresh(event)
    return ScheduledEventResponse.model_validate(event)


@router.get("/upcoming", dependencies=[Depends(require_auth)])
async def get_upcoming_events(
    limit: int = Query(default=10, le=100),
    db: AsyncSession = Depends(get_db)
) -> list[ScheduledEventResponse]:
    """Get the next N upcoming events."""
    now = datetime.now(timezone.utc)
    
    # Get pending events
    query = (
        select(ScheduledEventModel)
        .where(
            and_(
                ScheduledEventModel.status.in_(["pending", "recurring"]),
                or_(
                    ScheduledEventModel.scheduled_at >= now,
                    ScheduledEventModel.next_fire_at >= now
                )
            )
        )
        .order_by(ScheduledEventModel.scheduled_at)
        .limit(limit)
    )
    
    result = await db.execute(query)
    events = result.scalars().all()
    
    return [ScheduledEventResponse.model_validate(e) for e in events]


@router.get("/today", dependencies=[Depends(require_auth)])
async def get_today_events(
    db: AsyncSession = Depends(get_db)
) -> list[ScheduledEventResponse]:
    """Get today's events."""
    now = datetime.now(timezone.utc)
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_day = start_of_day + timedelta(days=1)
    
    query = (
        select(ScheduledEventModel)
        .where(
            and_(
                ScheduledEventModel.scheduled_at >= start_of_day,
                ScheduledEventModel.scheduled_at < end_of_day,
                ScheduledEventModel.status != "cancelled"
            )
        )
        .order_by(ScheduledEventModel.scheduled_at)
    )
    
    result = await db.execute(query)
    events = result.scalars().all()
    
    return [ScheduledEventResponse.model_validate(e) for e in events]


@router.get("/range", dependencies=[Depends(require_auth)])
async def get_calendar_range(
    start_date: str = Query(..., description="Start date (YYYY-MM-DD)"),
    end_date: str = Query(..., description="End date (YYYY-MM-DD)"),
    db: AsyncSession = Depends(get_db)
) -> CalendarView:
    """Get events in a date range, grouped by date."""
    try:
        start = datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc)
        end = datetime.fromisoformat(end_date).replace(tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid date format. Use YYYY-MM-DD"
        )
    
    # Make end inclusive (end of day)
    end = end.replace(hour=23, minute=59, second=59)
    
    query = (
        select(ScheduledEventModel)
        .where(
            and_(
                ScheduledEventModel.scheduled_at >= start,
                ScheduledEventModel.scheduled_at <= end,
                ScheduledEventModel.status != "cancelled"
            )
        )
        .order_by(ScheduledEventModel.scheduled_at)
    )
    
    result = await db.execute(query)
    events = result.scalars().all()
    
    # Group events by date
    events_by_date = defaultdict(list)
    for event in events:
        # Convert to date string (in UTC)
        date_str = event.scheduled_at.date().isoformat()
        events_by_date[date_str].append(ScheduledEventResponse.model_validate(event))
    
    # Create CalendarDayEvents for each date
    days = [
        CalendarDayEvents(date=date_str, events=day_events)
        for date_str, day_events in sorted(events_by_date.items())
    ]
    
    return CalendarView(
        start_date=start_date,
        end_date=end_date,
        days=days
    )
