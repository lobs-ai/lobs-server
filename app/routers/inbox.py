"""Inbox API endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import InboxItem as InboxItemModel, InboxThread as InboxThreadModel, InboxMessage as InboxMessageModel
from app.schemas import InboxItem, InboxItemCreate, InboxItemUpdate, InboxThread, InboxMessage, InboxMessageCreate, InboxTriageUpdate
from app.config import settings

router = APIRouter(prefix="/inbox", tags=["inbox"])


@router.get("")
async def list_inbox_items(
    limit: int = settings.DEFAULT_LIMIT,
    offset: int = 0,
    db: AsyncSession = Depends(get_db)
) -> list[InboxItem]:
    """List inbox items with pagination."""
    query = select(InboxItemModel).offset(offset).limit(min(limit, settings.MAX_LIMIT))
    result = await db.execute(query)
    items = result.scalars().all()
    return [InboxItem.model_validate(i) for i in items]


@router.post("")
async def create_inbox_item(
    item: InboxItemCreate,
    db: AsyncSession = Depends(get_db)
) -> InboxItem:
    """Create a new inbox item."""
    db_item = InboxItemModel(**item.model_dump())
    db.add(db_item)
    await db.flush()
    await db.refresh(db_item)
    return InboxItem.model_validate(db_item)


@router.get("/{item_id}")
async def get_inbox_item(
    item_id: str,
    db: AsyncSession = Depends(get_db)
) -> InboxItem:
    """Get a specific inbox item."""
    result = await db.execute(select(InboxItemModel).where(InboxItemModel.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Inbox item not found")
    return InboxItem.model_validate(item)


@router.put("/{item_id}")
async def update_inbox_item(
    item_id: str,
    item_update: InboxItemUpdate,
    db: AsyncSession = Depends(get_db)
) -> InboxItem:
    """Update an inbox item."""
    result = await db.execute(select(InboxItemModel).where(InboxItemModel.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Inbox item not found")
    
    update_data = item_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(item, key, value)
    
    await db.flush()
    await db.refresh(item)
    return InboxItem.model_validate(item)


@router.delete("/{item_id}")
async def delete_inbox_item(
    item_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Delete an inbox item."""
    result = await db.execute(select(InboxItemModel).where(InboxItemModel.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Inbox item not found")
    
    await db.delete(item)
    return {"status": "deleted"}


@router.get("/{item_id}/thread")
async def get_inbox_thread(
    item_id: str,
    db: AsyncSession = Depends(get_db)
) -> dict:
    """Get inbox item thread and messages."""
    result = await db.execute(select(InboxThreadModel).where(InboxThreadModel.doc_id == item_id))
    thread = result.scalar_one_or_none()
    
    if not thread:
        return {"thread": None, "messages": []}
    
    messages_result = await db.execute(
        select(InboxMessageModel).where(InboxMessageModel.thread_id == thread.id).order_by(InboxMessageModel.created_at)
    )
    messages = messages_result.scalars().all()
    
    return {
        "thread": InboxThread.model_validate(thread),
        "messages": [InboxMessage.model_validate(m) for m in messages]
    }


@router.post("/{item_id}/thread/messages")
async def create_inbox_message(
    item_id: str,
    message: InboxMessageCreate,
    db: AsyncSession = Depends(get_db)
) -> InboxMessage:
    """Add a message to an inbox thread."""
    # Ensure thread exists
    result = await db.execute(select(InboxThreadModel).where(InboxThreadModel.doc_id == item_id))
    thread = result.scalar_one_or_none()
    
    if not thread:
        # Create thread if it doesn't exist
        from uuid import uuid4
        thread = InboxThreadModel(
            id=str(uuid4()),
            doc_id=item_id,
            triage_status="needs_response"
        )
        db.add(thread)
        await db.flush()
    
    db_message = InboxMessageModel(**message.model_dump())
    db.add(db_message)
    await db.flush()
    await db.refresh(db_message)
    return InboxMessage.model_validate(db_message)


@router.patch("/{item_id}/triage")
async def update_inbox_triage(
    item_id: str,
    triage_update: InboxTriageUpdate,
    db: AsyncSession = Depends(get_db)
) -> InboxThread:
    """Update inbox item triage status."""
    result = await db.execute(select(InboxThreadModel).where(InboxThreadModel.doc_id == item_id))
    thread = result.scalar_one_or_none()
    
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    
    thread.triage_status = triage_update.triage_status
    await db.flush()
    await db.refresh(thread)
    return InboxThread.model_validate(thread)
