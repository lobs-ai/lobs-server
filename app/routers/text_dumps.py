"""Text dump API endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import TextDump as TextDumpModel
from app.schemas import TextDump, TextDumpCreate, TextDumpUpdate
from app.config import settings

router = APIRouter(prefix="/text-dumps", tags=["text-dumps"])


@router.get("")
async def list_text_dumps(
    limit: int = settings.DEFAULT_LIMIT,
    offset: int = 0,
    db: AsyncSession = Depends(get_db)
) -> list[TextDump]:
    """List text dumps."""
    query = select(TextDumpModel).offset(offset).limit(min(limit, settings.MAX_LIMIT))
    result = await db.execute(query)
    dumps = result.scalars().all()
    return [TextDump.model_validate(d) for d in dumps]


@router.post("")
async def create_text_dump(
    dump: TextDumpCreate,
    db: AsyncSession = Depends(get_db)
) -> TextDump:
    """Create a new text dump."""
    db_dump = TextDumpModel(**dump.model_dump())
    db.add(db_dump)
    await db.flush()
    await db.refresh(db_dump)
    return TextDump.model_validate(db_dump)


@router.get("/{dump_id}")
async def get_text_dump(
    dump_id: str,
    db: AsyncSession = Depends(get_db)
) -> TextDump:
    """Get a specific text dump."""
    result = await db.execute(select(TextDumpModel).where(TextDumpModel.id == dump_id))
    dump = result.scalar_one_or_none()
    if not dump:
        raise HTTPException(status_code=404, detail="Text dump not found")
    return TextDump.model_validate(dump)


@router.put("/{dump_id}")
async def update_text_dump(
    dump_id: str,
    dump_update: TextDumpUpdate,
    db: AsyncSession = Depends(get_db)
) -> TextDump:
    """Update a text dump."""
    result = await db.execute(select(TextDumpModel).where(TextDumpModel.id == dump_id))
    dump = result.scalar_one_or_none()
    if not dump:
        raise HTTPException(status_code=404, detail="Text dump not found")
    
    update_data = dump_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(dump, key, value)
    
    await db.flush()
    await db.refresh(dump)
    return TextDump.model_validate(dump)


@router.delete("/{dump_id}")
async def delete_text_dump(
    dump_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Delete a text dump."""
    result = await db.execute(select(TextDumpModel).where(TextDumpModel.id == dump_id))
    dump = result.scalar_one_or_none()
    if not dump:
        raise HTTPException(status_code=404, detail="Text dump not found")
    
    await db.delete(dump)
    return {"status": "deleted"}
