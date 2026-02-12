"""Reminder API endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Reminder as ReminderModel
from app.schemas import Reminder, ReminderCreate
from app.config import settings

router = APIRouter(prefix="/reminders", tags=["reminders"])


@router.get("")
async def list_reminders(
    limit: int = settings.DEFAULT_LIMIT,
    offset: int = 0,
    db: AsyncSession = Depends(get_db)
) -> list[Reminder]:
    """List reminders."""
    query = select(ReminderModel).offset(offset).limit(min(limit, settings.MAX_LIMIT))
    result = await db.execute(query)
    reminders = result.scalars().all()
    return [Reminder.model_validate(r) for r in reminders]


@router.post("")
async def create_reminder(
    reminder: ReminderCreate,
    db: AsyncSession = Depends(get_db)
) -> Reminder:
    """Create a new reminder."""
    db_reminder = ReminderModel(**reminder.model_dump())
    db.add(db_reminder)
    await db.flush()
    await db.refresh(db_reminder)
    return Reminder.model_validate(db_reminder)


@router.delete("/{reminder_id}")
async def delete_reminder(
    reminder_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Delete a reminder."""
    result = await db.execute(select(ReminderModel).where(ReminderModel.id == reminder_id))
    reminder = result.scalar_one_or_none()
    if not reminder:
        raise HTTPException(status_code=404, detail="Reminder not found")
    
    await db.delete(reminder)
    return {"status": "deleted"}
