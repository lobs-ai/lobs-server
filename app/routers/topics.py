"""Topics API endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Topic as TopicModel, AgentDocument as AgentDocumentModel
from app.schemas import Topic, TopicCreate, TopicUpdate, AgentDocument
from app.config import settings

router = APIRouter(prefix="/topics", tags=["topics"])


@router.get("")
async def list_topics(
    limit: int = settings.DEFAULT_LIMIT,
    offset: int = 0,
    db: AsyncSession = Depends(get_db)
) -> list[Topic]:
    """List all topics with pagination."""
    query = select(TopicModel).offset(offset).limit(min(limit, settings.MAX_LIMIT)).order_by(TopicModel.title)
    result = await db.execute(query)
    topics = result.scalars().all()
    return [Topic.model_validate(t) for t in topics]


@router.post("")
async def create_topic(
    topic: TopicCreate,
    db: AsyncSession = Depends(get_db)
) -> Topic:
    """Create a new topic."""
    # Check if topic with same title already exists
    result = await db.execute(select(TopicModel).where(TopicModel.title == topic.title))
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail=f"Topic with title '{topic.title}' already exists")
    
    db_topic = TopicModel(**topic.model_dump())
    db.add(db_topic)
    await db.flush()
    await db.refresh(db_topic)
    return Topic.model_validate(db_topic)


@router.get("/{topic_id}")
async def get_topic(
    topic_id: str,
    db: AsyncSession = Depends(get_db)
) -> Topic:
    """Get a specific topic by ID."""
    result = await db.execute(select(TopicModel).where(TopicModel.id == topic_id))
    topic = result.scalar_one_or_none()
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")
    return Topic.model_validate(topic)


@router.put("/{topic_id}")
async def update_topic(
    topic_id: str,
    topic_update: TopicUpdate,
    db: AsyncSession = Depends(get_db)
) -> Topic:
    """Update a topic."""
    result = await db.execute(select(TopicModel).where(TopicModel.id == topic_id))
    topic = result.scalar_one_or_none()
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")
    
    # Check if title is being updated and if it conflicts
    update_data = topic_update.model_dump(exclude_unset=True)
    if "title" in update_data and update_data["title"] != topic.title:
        result = await db.execute(select(TopicModel).where(TopicModel.title == update_data["title"]))
        existing = result.scalar_one_or_none()
        if existing:
            raise HTTPException(status_code=400, detail=f"Topic with title '{update_data['title']}' already exists")
    
    for key, value in update_data.items():
        setattr(topic, key, value)
    
    await db.flush()
    await db.refresh(topic)
    return Topic.model_validate(topic)


@router.delete("/{topic_id}")
async def delete_topic(
    topic_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Delete a topic.
    
    Note: This will fail if documents are still linked to this topic (foreign key constraint).
    Unlink documents first or use cascade delete in production.
    """
    result = await db.execute(select(TopicModel).where(TopicModel.id == topic_id))
    topic = result.scalar_one_or_none()
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")
    
    await db.delete(topic)
    return {"status": "deleted"}


@router.get("/{topic_id}/documents")
async def get_topic_documents(
    topic_id: str,
    limit: int = settings.DEFAULT_LIMIT,
    offset: int = 0,
    db: AsyncSession = Depends(get_db)
) -> list[AgentDocument]:
    """Get all documents linked to a specific topic."""
    # First verify topic exists
    result = await db.execute(select(TopicModel).where(TopicModel.id == topic_id))
    topic = result.scalar_one_or_none()
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")
    
    # Get documents for this topic
    query = (
        select(AgentDocumentModel)
        .where(AgentDocumentModel.topic_id == topic_id)
        .offset(offset)
        .limit(min(limit, settings.MAX_LIMIT))
        .order_by(AgentDocumentModel.date.desc())
    )
    result = await db.execute(query)
    documents = result.scalars().all()
    return [AgentDocument.model_validate(d) for d in documents]
