"""Tracker API endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import TrackerItem as TrackerItemModel
from app.schemas import TrackerItem, TrackerItemCreate, TrackerItemUpdate
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
