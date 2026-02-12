"""Agent documents API endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import AgentDocument as AgentDocumentModel
from app.schemas import AgentDocument, AgentDocumentCreate, AgentDocumentUpdate
from app.config import settings

router = APIRouter(prefix="/documents", tags=["documents"])


@router.get("")
async def list_documents(
    limit: int = settings.DEFAULT_LIMIT,
    offset: int = 0,
    db: AsyncSession = Depends(get_db)
) -> list[AgentDocument]:
    """List agent documents with pagination."""
    query = select(AgentDocumentModel).offset(offset).limit(min(limit, settings.MAX_LIMIT))
    result = await db.execute(query)
    documents = result.scalars().all()
    return [AgentDocument.model_validate(d) for d in documents]


@router.post("")
async def create_document(
    document: AgentDocumentCreate,
    db: AsyncSession = Depends(get_db)
) -> AgentDocument:
    """Create a new agent document."""
    db_document = AgentDocumentModel(**document.model_dump())
    db.add(db_document)
    await db.flush()
    await db.refresh(db_document)
    return AgentDocument.model_validate(db_document)


@router.get("/{document_id}")
async def get_document(
    document_id: str,
    db: AsyncSession = Depends(get_db)
) -> AgentDocument:
    """Get a specific agent document."""
    result = await db.execute(select(AgentDocumentModel).where(AgentDocumentModel.id == document_id))
    document = result.scalar_one_or_none()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    return AgentDocument.model_validate(document)


@router.put("/{document_id}")
async def update_document(
    document_id: str,
    document_update: AgentDocumentUpdate,
    db: AsyncSession = Depends(get_db)
) -> AgentDocument:
    """Update an agent document."""
    result = await db.execute(select(AgentDocumentModel).where(AgentDocumentModel.id == document_id))
    document = result.scalar_one_or_none()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    update_data = document_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(document, key, value)
    
    await db.flush()
    await db.refresh(document)
    return AgentDocument.model_validate(document)


@router.delete("/{document_id}")
async def delete_document(
    document_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Delete an agent document."""
    result = await db.execute(select(AgentDocumentModel).where(AgentDocumentModel.id == document_id))
    document = result.scalar_one_or_none()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    await db.delete(document)
    return {"status": "deleted"}


@router.post("/{document_id}/archive")
async def archive_document(
    document_id: str,
    db: AsyncSession = Depends(get_db)
) -> AgentDocument:
    """Archive a document by setting status to archived."""
    result = await db.execute(select(AgentDocumentModel).where(AgentDocumentModel.id == document_id))
    document = result.scalar_one_or_none()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    document.status = "archived"
    await db.flush()
    await db.refresh(document)
    return AgentDocument.model_validate(document)
