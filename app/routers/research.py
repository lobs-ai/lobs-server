"""Research API endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import ResearchDoc as ResearchDocModel, ResearchSource as ResearchSourceModel, ResearchRequest as ResearchRequestModel
from app.schemas import ResearchDoc, ResearchDocUpdate, ResearchSource, ResearchSourceCreate, ResearchRequest, ResearchRequestCreate, ResearchRequestUpdate
from app.config import settings

router = APIRouter(prefix="/research", tags=["research"])


@router.get("/{project_id}/doc")
async def get_research_doc(
    project_id: str,
    db: AsyncSession = Depends(get_db)
) -> ResearchDoc:
    """Get research document for a project."""
    result = await db.execute(select(ResearchDocModel).where(ResearchDocModel.project_id == project_id))
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Research document not found")
    return ResearchDoc.model_validate(doc)


@router.put("/{project_id}/doc")
async def update_research_doc(
    project_id: str,
    doc_update: ResearchDocUpdate,
    db: AsyncSession = Depends(get_db)
) -> ResearchDoc:
    """Update or create research document for a project."""
    result = await db.execute(select(ResearchDocModel).where(ResearchDocModel.project_id == project_id))
    doc = result.scalar_one_or_none()
    
    if not doc:
        doc = ResearchDocModel(project_id=project_id, **doc_update.model_dump(exclude_unset=True))
        db.add(doc)
    else:
        update_data = doc_update.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(doc, key, value)
    
    await db.flush()
    await db.refresh(doc)
    return ResearchDoc.model_validate(doc)


@router.get("/{project_id}/sources")
async def list_research_sources(
    project_id: str,
    limit: int = settings.DEFAULT_LIMIT,
    offset: int = 0,
    db: AsyncSession = Depends(get_db)
) -> list[ResearchSource]:
    """List research sources for a project."""
    query = select(ResearchSourceModel).where(
        ResearchSourceModel.project_id == project_id
    ).offset(offset).limit(min(limit, settings.MAX_LIMIT))
    result = await db.execute(query)
    sources = result.scalars().all()
    return [ResearchSource.model_validate(s) for s in sources]


@router.post("/{project_id}/sources")
async def create_research_source(
    project_id: str,
    source: ResearchSourceCreate,
    db: AsyncSession = Depends(get_db)
) -> ResearchSource:
    """Add a research source to a project."""
    db_source = ResearchSourceModel(**source.model_dump())
    db.add(db_source)
    await db.flush()
    await db.refresh(db_source)
    return ResearchSource.model_validate(db_source)


@router.delete("/{project_id}/sources/{source_id}")
async def delete_research_source(
    project_id: str,
    source_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Delete a research source."""
    result = await db.execute(
        select(ResearchSourceModel).where(
            ResearchSourceModel.id == source_id,
            ResearchSourceModel.project_id == project_id
        )
    )
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Research source not found")
    
    await db.delete(source)
    return {"status": "deleted"}


@router.get("/{project_id}/requests")
async def list_research_requests(
    project_id: str,
    limit: int = settings.DEFAULT_LIMIT,
    offset: int = 0,
    db: AsyncSession = Depends(get_db)
) -> list[ResearchRequest]:
    """List research requests for a project."""
    query = select(ResearchRequestModel).where(
        ResearchRequestModel.project_id == project_id
    ).offset(offset).limit(min(limit, settings.MAX_LIMIT))
    result = await db.execute(query)
    requests = result.scalars().all()
    return [ResearchRequest.model_validate(r) for r in requests]


@router.post("/{project_id}/requests")
async def create_research_request(
    project_id: str,
    request: ResearchRequestCreate,
    db: AsyncSession = Depends(get_db)
) -> ResearchRequest:
    """Create a research request."""
    db_request = ResearchRequestModel(**request.model_dump())
    db.add(db_request)
    await db.flush()
    await db.refresh(db_request)
    return ResearchRequest.model_validate(db_request)


@router.get("/{project_id}/requests/{request_id}")
async def get_research_request(
    project_id: str,
    request_id: str,
    db: AsyncSession = Depends(get_db)
) -> ResearchRequest:
    """Get a specific research request."""
    result = await db.execute(
        select(ResearchRequestModel).where(
            ResearchRequestModel.id == request_id,
            ResearchRequestModel.project_id == project_id
        )
    )
    request = result.scalar_one_or_none()
    if not request:
        raise HTTPException(status_code=404, detail="Research request not found")
    return ResearchRequest.model_validate(request)


@router.put("/{project_id}/requests/{request_id}")
async def update_research_request(
    project_id: str,
    request_id: str,
    request_update: ResearchRequestUpdate,
    db: AsyncSession = Depends(get_db)
) -> ResearchRequest:
    """Update a research request."""
    result = await db.execute(
        select(ResearchRequestModel).where(
            ResearchRequestModel.id == request_id,
            ResearchRequestModel.project_id == project_id
        )
    )
    request = result.scalar_one_or_none()
    if not request:
        raise HTTPException(status_code=404, detail="Research request not found")
    
    update_data = request_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(request, key, value)
    
    await db.flush()
    await db.refresh(request)
    return ResearchRequest.model_validate(request)


@router.delete("/{project_id}/requests/{request_id}")
async def delete_research_request(
    project_id: str,
    request_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Delete a research request."""
    result = await db.execute(
        select(ResearchRequestModel).where(
            ResearchRequestModel.id == request_id,
            ResearchRequestModel.project_id == project_id
        )
    )
    request = result.scalar_one_or_none()
    if not request:
        raise HTTPException(status_code=404, detail="Research request not found")
    
    await db.delete(request)
    return {"status": "deleted"}
