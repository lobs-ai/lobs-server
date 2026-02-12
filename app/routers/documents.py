"""Documents router."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models.document import AgentDocument
from app.schemas.document import (
    AgentDocument as DocumentSchema,
    AgentDocumentCreate,
    AgentDocumentUpdate
)

router = APIRouter(prefix="/documents", tags=["documents"])


@router.get("", response_model=list[DocumentSchema])
async def list_documents(
    source: str | None = None,
    status: str | None = None,
    project_id: str | None = None,
    task_id: str | None = None,
    is_read: bool | None = None,
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db)
):
    """List all agent documents."""
    query = select(AgentDocument)
    
    if source:
        query = query.where(AgentDocument.source == source)
    if status:
        query = query.where(AgentDocument.status == status)
    if project_id:
        query = query.where(AgentDocument.project_id == project_id)
    if task_id:
        query = query.where(AgentDocument.task_id == task_id)
    if is_read is not None:
        query = query.where(AgentDocument.is_read == is_read)
    
    query = query.order_by(AgentDocument.created_at.desc())
    query = query.limit(limit).offset(offset)
    
    result = await db.execute(query)
    documents = result.scalars().all()
    return documents


@router.post("", response_model=DocumentSchema, status_code=201)
async def create_document(
    document: AgentDocumentCreate,
    db: AsyncSession = Depends(get_db)
):
    """Create a new agent document."""
    db_document = AgentDocument(**document.model_dump())
    db.add(db_document)
    await db.commit()
    await db.refresh(db_document)
    return db_document


@router.get("/{document_id}", response_model=DocumentSchema)
async def get_document(
    document_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Get a document by ID."""
    result = await db.execute(select(AgentDocument).where(AgentDocument.id == document_id))
    document = result.scalar_one_or_none()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


@router.put("/{document_id}", response_model=DocumentSchema)
async def update_document(
    document_id: str,
    document_update: AgentDocumentUpdate,
    db: AsyncSession = Depends(get_db)
):
    """Update a document."""
    result = await db.execute(select(AgentDocument).where(AgentDocument.id == document_id))
    document = result.scalar_one_or_none()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    for key, value in document_update.model_dump(exclude_unset=True).items():
        setattr(document, key, value)
    
    await db.commit()
    await db.refresh(document)
    return document


@router.post("/{document_id}/archive", response_model=DocumentSchema)
async def archive_document(
    document_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Archive a document (set status to approved)."""
    result = await db.execute(select(AgentDocument).where(AgentDocument.id == document_id))
    document = result.scalar_one_or_none()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    document.status = "approved"
    await db.commit()
    await db.refresh(document)
    return document
