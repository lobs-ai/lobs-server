"""Tracker API endpoints."""

from datetime import datetime, timedelta, timezone
import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import TrackerItem as TrackerItemModel, ResearchRequest as ResearchRequestModel, TrackerEntry as TrackerEntryModel, TrackerNotification as TrackerNotificationModel, Task as TaskModel
from app.schemas import (
    TrackerItem, TrackerItemCreate, TrackerItemUpdate, 
    ResearchRequest, ResearchRequestCreate, ResearchRequestUpdate,
    TrackerEntry, TrackerEntryCreate, TrackerEntryUpdate,
    TrackerSummary, DeadlineEntry,
    TrackerNotification, TrackerNotificationCreate
)
from app.config import settings

router = APIRouter(prefix="/tracker", tags=["tracker"])


@router.get("/{project_id}/items")
async def list_tracker_items(
    project_id: str,
    limit: int = settings.DEFAULT_LIMIT,
    offset: int = 0,
    db: AsyncSession = Depends(get_db)
) -> list[TrackerItem]:
    """List tracker items for a project."""
    query = select(TrackerItemModel).where(
        TrackerItemModel.project_id == project_id
    ).offset(offset).limit(min(limit, settings.MAX_LIMIT))
    result = await db.execute(query)
    items = result.scalars().all()
    return [TrackerItem.model_validate(i) for i in items]


@router.post("/{project_id}/items")
async def create_tracker_item(
    project_id: str,
    item: TrackerItemCreate,
    db: AsyncSession = Depends(get_db)
) -> TrackerItem:
    """Create a tracker item."""
    db_item = TrackerItemModel(**item.model_dump())
    db.add(db_item)
    await db.flush()
    await db.refresh(db_item)
    return TrackerItem.model_validate(db_item)


@router.get("/{project_id}/items/{item_id}")
async def get_tracker_item(
    project_id: str,
    item_id: str,
    db: AsyncSession = Depends(get_db)
) -> TrackerItem:
    """Get a specific tracker item."""
    result = await db.execute(
        select(TrackerItemModel).where(
            TrackerItemModel.id == item_id,
            TrackerItemModel.project_id == project_id
        )
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Tracker item not found")
    return TrackerItem.model_validate(item)


@router.put("/{project_id}/items/{item_id}")
async def update_tracker_item(
    project_id: str,
    item_id: str,
    item_update: TrackerItemUpdate,
    db: AsyncSession = Depends(get_db)
) -> TrackerItem:
    """Update a tracker item."""
    result = await db.execute(
        select(TrackerItemModel).where(
            TrackerItemModel.id == item_id,
            TrackerItemModel.project_id == project_id
        )
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Tracker item not found")
    
    update_data = item_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(item, key, value)
    
    await db.flush()
    await db.refresh(item)
    return TrackerItem.model_validate(item)


@router.delete("/{project_id}/items/{item_id}")
async def delete_tracker_item(
    project_id: str,
    item_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Delete a tracker item."""
    result = await db.execute(
        select(TrackerItemModel).where(
            TrackerItemModel.id == item_id,
            TrackerItemModel.project_id == project_id
        )
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Tracker item not found")
    
    await db.delete(item)
    return {"status": "deleted"}


# Tracker Requests (similar to research requests but for tracker projects)
@router.get("/{project_id}/requests")
async def list_tracker_requests(
    project_id: str,
    limit: int = settings.DEFAULT_LIMIT,
    offset: int = 0,
    db: AsyncSession = Depends(get_db)
) -> list[ResearchRequest]:
    """List tracker requests for a project."""
    query = select(ResearchRequestModel).where(
        ResearchRequestModel.project_id == project_id
    ).offset(offset).limit(min(limit, settings.MAX_LIMIT))
    result = await db.execute(query)
    requests = result.scalars().all()
    return [ResearchRequest.model_validate(r) for r in requests]


@router.post("/{project_id}/requests")
async def create_tracker_request(
    project_id: str,
    request: ResearchRequestCreate,
    db: AsyncSession = Depends(get_db)
) -> ResearchRequest:
    """Create a tracker request."""
    db_request = ResearchRequestModel(**request.model_dump())
    db.add(db_request)
    await db.flush()
    await db.refresh(db_request)
    return ResearchRequest.model_validate(db_request)


@router.get("/{project_id}/requests/{request_id}")
async def get_tracker_request(
    project_id: str,
    request_id: str,
    db: AsyncSession = Depends(get_db)
) -> ResearchRequest:
    """Get a specific tracker request."""
    result = await db.execute(
        select(ResearchRequestModel).where(
            ResearchRequestModel.id == request_id,
            ResearchRequestModel.project_id == project_id
        )
    )
    request = result.scalar_one_or_none()
    if not request:
        raise HTTPException(status_code=404, detail="Tracker request not found")
    return ResearchRequest.model_validate(request)


@router.put("/{project_id}/requests/{request_id}")
async def update_tracker_request(
    project_id: str,
    request_id: str,
    request_update: ResearchRequestUpdate,
    db: AsyncSession = Depends(get_db)
) -> ResearchRequest:
    """Update a tracker request."""
    result = await db.execute(
        select(ResearchRequestModel).where(
            ResearchRequestModel.id == request_id,
            ResearchRequestModel.project_id == project_id
        )
    )
    request = result.scalar_one_or_none()
    if not request:
        raise HTTPException(status_code=404, detail="Tracker request not found")
    
    update_data = request_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(request, key, value)
    
    await db.flush()
    await db.refresh(request)
    return ResearchRequest.model_validate(request)


@router.delete("/{project_id}/requests/{request_id}")
async def delete_tracker_request(
    project_id: str,
    request_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Delete a tracker request."""
    result = await db.execute(
        select(ResearchRequestModel).where(
            ResearchRequestModel.id == request_id,
            ResearchRequestModel.project_id == project_id
        )
    )
    request = result.scalar_one_or_none()
    if not request:
        raise HTTPException(status_code=404, detail="Tracker request not found")
    
    await db.delete(request)
    return {"status": "deleted"}


# Personal Work Tracker endpoints
@router.post("/entries")
async def create_tracker_entry(
    entry: TrackerEntryCreate,
    db: AsyncSession = Depends(get_db)
) -> TrackerEntry:
    """Create a work tracker entry."""
    db_entry = TrackerEntryModel(**entry.model_dump())
    db.add(db_entry)
    await db.flush()
    await db.refresh(db_entry)
    return TrackerEntry.model_validate(db_entry)


@router.get("/entries")
async def list_tracker_entries(
    limit: int = settings.DEFAULT_LIMIT,
    offset: int = 0,
    type: str | None = None,
    db: AsyncSession = Depends(get_db)
) -> list[TrackerEntry]:
    """List work tracker entries with optional type filter."""
    query = select(TrackerEntryModel)
    
    if type:
        query = query.where(TrackerEntryModel.type == type)
    
    query = query.order_by(TrackerEntryModel.created_at.desc()).offset(offset).limit(min(limit, settings.MAX_LIMIT))
    result = await db.execute(query)
    entries = result.scalars().all()
    return [TrackerEntry.model_validate(e) for e in entries]


@router.get("/entries/{entry_id}")
async def get_tracker_entry(
    entry_id: str,
    db: AsyncSession = Depends(get_db)
) -> TrackerEntry:
    """Get a specific tracker entry."""
    result = await db.execute(
        select(TrackerEntryModel).where(TrackerEntryModel.id == entry_id)
    )
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Tracker entry not found")
    return TrackerEntry.model_validate(entry)


@router.put("/entries/{entry_id}")
async def update_tracker_entry(
    entry_id: str,
    entry_update: TrackerEntryUpdate,
    db: AsyncSession = Depends(get_db)
) -> TrackerEntry:
    """Update a tracker entry."""
    result = await db.execute(
        select(TrackerEntryModel).where(TrackerEntryModel.id == entry_id)
    )
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Tracker entry not found")
    
    update_data = entry_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(entry, key, value)
    
    await db.flush()
    await db.refresh(entry)
    return TrackerEntry.model_validate(entry)


@router.delete("/entries/{entry_id}")
async def delete_tracker_entry(
    entry_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Delete a tracker entry."""
    result = await db.execute(
        select(TrackerEntryModel).where(TrackerEntryModel.id == entry_id)
    )
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Tracker entry not found")
    
    await db.delete(entry)
    return {"status": "deleted"}


@router.get("/summary")
async def get_tracker_summary(
    db: AsyncSession = Depends(get_db)
) -> TrackerSummary:
    """Get work tracker summary statistics."""
    # Total entries
    total_result = await db.execute(select(func.count()).select_from(TrackerEntryModel))
    total_entries = total_result.scalar() or 0
    
    # Work sessions count
    work_sessions_result = await db.execute(
        select(func.count()).select_from(TrackerEntryModel).where(TrackerEntryModel.type == "work_session")
    )
    work_sessions_count = work_sessions_result.scalar() or 0
    
    # Total minutes logged
    total_minutes_result = await db.execute(
        select(func.sum(TrackerEntryModel.duration)).where(TrackerEntryModel.type == "work_session")
    )
    total_minutes_logged = total_minutes_result.scalar() or 0
    
    # Deadlines count
    deadlines_result = await db.execute(
        select(func.count()).select_from(TrackerEntryModel).where(TrackerEntryModel.type == "deadline")
    )
    deadlines_count = deadlines_result.scalar() or 0
    
    # Upcoming deadlines (future only)
    now = datetime.now(timezone.utc)
    upcoming_result = await db.execute(
        select(func.count()).select_from(TrackerEntryModel).where(
            and_(
                TrackerEntryModel.type == "deadline",
                TrackerEntryModel.due_date >= now
            )
        )
    )
    upcoming_deadlines = upcoming_result.scalar() or 0
    
    # Notes count
    notes_result = await db.execute(
        select(func.count()).select_from(TrackerEntryModel).where(TrackerEntryModel.type == "note")
    )
    notes_count = notes_result.scalar() or 0
    
    # Categories count
    categories_result = await db.execute(
        select(TrackerEntryModel.category, func.count()).where(
            TrackerEntryModel.category.isnot(None)
        ).group_by(TrackerEntryModel.category)
    )
    categories = {cat: count for cat, count in categories_result.all()}
    
    # Last 7 days minutes
    seven_days_ago = now - timedelta(days=7)
    last_7_days_result = await db.execute(
        select(func.sum(TrackerEntryModel.duration)).where(
            and_(
                TrackerEntryModel.type == "work_session",
                TrackerEntryModel.created_at >= seven_days_ago
            )
        )
    )
    last_7_days_minutes = last_7_days_result.scalar() or 0
    
    return TrackerSummary(
        total_entries=total_entries,
        work_sessions_count=work_sessions_count,
        total_minutes_logged=total_minutes_logged,
        deadlines_count=deadlines_count,
        upcoming_deadlines=upcoming_deadlines,
        notes_count=notes_count,
        categories=categories,
        last_7_days_minutes=last_7_days_minutes
    )


@router.get("/deadlines")
async def get_deadlines(
    upcoming: bool = True,
    limit: int = settings.DEFAULT_LIMIT,
    db: AsyncSession = Depends(get_db)
) -> list[DeadlineEntry]:
    """Get deadline entries, optionally filtered to upcoming only."""
    query = select(TrackerEntryModel).where(TrackerEntryModel.type == "deadline")
    
    if upcoming:
        now = datetime.now(timezone.utc)
        query = query.where(TrackerEntryModel.due_date >= now)
    
    query = query.order_by(TrackerEntryModel.due_date.asc()).limit(min(limit, settings.MAX_LIMIT))
    result = await db.execute(query)
    entries = result.scalars().all()
    
    return [
        DeadlineEntry(
            id=e.id,
            raw_text=e.raw_text,
            category=e.category,
            due_date=e.due_date,
            estimated_minutes=e.estimated_minutes,
            commitment_type=e.commitment_type,
            priority_score=e.priority_score,
            next_action=e.next_action,
            escalation_task_id=e.escalation_task_id,
            created_at=e.created_at
        )
        for e in entries
    ]


# Work Tracker Analysis (AI-generated insights)
@router.get("/analysis/latest")
async def get_latest_analysis(
    db: AsyncSession = Depends(get_db)
) -> TrackerEntry | None:
    """Get the most recent AI analysis entry."""
    result = await db.execute(
        select(TrackerEntryModel)
        .where(TrackerEntryModel.type == "analysis")
        .order_by(TrackerEntryModel.created_at.desc())
        .limit(1)
    )
    entry = result.scalar_one_or_none()
    if not entry:
        return None
    return TrackerEntry.model_validate(entry)


@router.put("/analysis")
async def upsert_analysis(
    entry: TrackerEntryCreate,
    db: AsyncSession = Depends(get_db)
) -> TrackerEntry:
    """Create or update the AI analysis. Keeps only the latest one."""
    # Delete old analyses
    old = await db.execute(
        select(TrackerEntryModel).where(TrackerEntryModel.type == "analysis")
    )
    for old_entry in old.scalars().all():
        await db.delete(old_entry)
    
    # Create new
    db_entry = TrackerEntryModel(**entry.model_dump())
    db_entry.type = "analysis"  # Force type
    db.add(db_entry)
    await db.flush()
    await db.refresh(db_entry)
    return TrackerEntry.model_validate(db_entry)


# Notification Deduplication endpoints
@router.get("/notifications/check/{deadline_key}")
async def check_notification(deadline_key: str, cooldown_hours: int = 12, db: AsyncSession = Depends(get_db)):
    """Check if a notification was already sent for this deadline within cooldown."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=cooldown_hours)
    result = await db.execute(
        select(TrackerNotificationModel).where(
            TrackerNotificationModel.deadline_key == deadline_key,
            TrackerNotificationModel.sent_at >= cutoff
        )
    )
    existing = result.scalar_one_or_none()
    return {"already_sent": existing is not None, "last_sent": existing.sent_at.isoformat() if existing else None}


@router.post("/notifications")
async def record_notification(notification: TrackerNotificationCreate, db: AsyncSession = Depends(get_db)):
    """Record that a notification was sent."""
    db_notif = TrackerNotificationModel(**notification.model_dump())
    db.add(db_notif)
    await db.flush()
    await db.refresh(db_notif)
    return TrackerNotification.model_validate(db_notif)


@router.get("/notifications")
async def list_notifications(limit: int = 20, db: AsyncSession = Depends(get_db)):
    """List recent notifications."""
    result = await db.execute(
        select(TrackerNotificationModel).order_by(TrackerNotificationModel.sent_at.desc()).limit(limit)
    )
    return [TrackerNotification.model_validate(n) for n in result.scalars().all()]
