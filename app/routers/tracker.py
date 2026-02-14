"""Tracker API endpoints."""

from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import TrackerItem as TrackerItemModel, ResearchRequest as ResearchRequestModel, TrackerEntry as TrackerEntryModel
from app.schemas import (
    TrackerItem, TrackerItemCreate, TrackerItemUpdate, 
    ResearchRequest, ResearchRequestCreate, ResearchRequestUpdate,
    TrackerEntry, TrackerEntryCreate, TrackerEntryUpdate,
    TrackerSummary, DeadlineEntry
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
            created_at=e.created_at
        )
        for e in entries
    ]
