"""Memory API endpoints for second brain feature."""

from typing import Optional
from datetime import datetime, date
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from app.database import get_db
from app.models import Memory as MemoryModel
from app.schemas import (
    Memory,
    MemoryCreate,
    MemoryUpdate,
    MemoryListItem,
    MemorySearchResult,
)
from app.config import settings
from app.services.memory_sync import sync_agent_memories, get_agent_memory_counts
from app.services.memory_backend import get_memory_runtime_config
from app.auth import require_auth

router = APIRouter(prefix="/memories", tags=["memories"])


class QuickCaptureRequest(BaseModel):
    """Schema for quick capture endpoint."""
    content: str
    agent: str = "main"


@router.get("/runtime")
async def memory_runtime(
    _token: str = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return active memory backend + QMD fallback config."""
    return await get_memory_runtime_config(db)


def generate_snippet(text: str, query: str, max_length: int = 200) -> str:
    """Generate a snippet showing the match context."""
    query_lower = query.lower()
    text_lower = text.lower()
    
    # Find the position of the query in the text
    pos = text_lower.find(query_lower)
    if pos == -1:
        # Query not found, return beginning of text
        return text[:max_length] + ("..." if len(text) > max_length else "")
    
    # Calculate snippet boundaries
    start = max(0, pos - 50)
    end = min(len(text), pos + len(query) + 150)
    
    snippet = text[start:end]
    if start > 0:
        snippet = "..." + snippet
    if end < len(text):
        snippet = snippet + "..."
    
    return snippet


@router.get("")
async def list_memories(
    agent: Optional[str] = None,
    type: Optional[str] = None,
    limit: int = settings.DEFAULT_LIMIT,
    offset: int = 0,
    _token: str = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
) -> list[MemoryListItem]:
    """List memories (optionally filtered by agent/type, sorted by date desc)."""
    query = select(MemoryModel)
    
    if agent:
        query = query.where(MemoryModel.agent == agent)
    
    if type:
        query = query.where(MemoryModel.memory_type == type)
    
    query = query.order_by(MemoryModel.date.desc().nulls_last(), MemoryModel.updated_at.desc())
    query = query.offset(offset).limit(min(limit, settings.MAX_LIMIT))
    
    result = await db.execute(query)
    memories = result.scalars().all()
    return [MemoryListItem.model_validate(m) for m in memories]


@router.get("/search")
async def search_memories(
    q: str,
    agent: Optional[str] = None,
    _token: str = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
) -> list[MemorySearchResult]:
    """Full-text search across title and content (optionally filtered by agent)."""
    if not q or len(q.strip()) == 0:
        return []
    
    query_lower = q.lower()
    
    # Search across title and content
    query = select(MemoryModel).where(
        or_(
            MemoryModel.title.ilike(f"%{q}%"),
            MemoryModel.content.ilike(f"%{q}%")
        )
    )
    
    if agent:
        query = query.where(MemoryModel.agent == agent)
    
    result = await db.execute(query)
    memories = result.scalars().all()
    
    # Generate results with snippets and scores
    results = []
    for memory in memories:
        # Simple scoring: title match = 2.0, content match = 1.0
        score = 0.0
        if query_lower in memory.title.lower():
            score += 2.0
        if query_lower in memory.content.lower():
            score += 1.0
        
        snippet = generate_snippet(memory.content, q)
        
        results.append(MemorySearchResult(
            id=memory.id,
            path=memory.path,
            agent=memory.agent,
            title=memory.title,
            snippet=snippet,
            memory_type=memory.memory_type,
            date=memory.date,
            score=score
        ))
    
    # Sort by score descending
    results.sort(key=lambda x: x.score, reverse=True)
    
    return results


@router.get("/agents")
async def list_agents(
    _token: str = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
) -> list[dict]:
    """List all agents with memory counts."""
    return await get_agent_memory_counts(db)


@router.post("/sync")
async def sync_all_memories(
    _token: str = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
) -> dict:
    """Sync all agent memories from filesystem to database."""
    return await sync_agent_memories(db)


@router.post("/sync/{agent}")
async def sync_agent(
    agent: str,
    _token: str = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
) -> dict:
    """Sync a specific agent's memories from filesystem to database."""
    return await sync_agent_memories(db, agent=agent)


@router.get("/{memory_id}")
async def get_memory(
    memory_id: int,
    _token: str = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
) -> Memory:
    """Get full memory by ID."""
    result = await db.execute(select(MemoryModel).where(MemoryModel.id == memory_id))
    memory = result.scalar_one_or_none()
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")
    return Memory.model_validate(memory)


@router.get("/by-path/{path:path}")
async def get_memory_by_path(
    path: str,
    agent: str = "main",
    _token: str = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
) -> Memory:
    """Get memory by path and agent (e.g., 'memory/2026-02-12.md')."""
    result = await db.execute(
        select(MemoryModel).where(
            MemoryModel.path == path,
            MemoryModel.agent == agent
        )
    )
    memory = result.scalar_one_or_none()
    if not memory:
        raise HTTPException(status_code=404, detail=f"Memory not found at path: {path} (agent: {agent})")
    return Memory.model_validate(memory)


@router.put("/{memory_id}")
async def update_memory(
    memory_id: int,
    memory_update: MemoryUpdate,
    _token: str = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
) -> Memory:
    """Update memory content/title."""
    result = await db.execute(select(MemoryModel).where(MemoryModel.id == memory_id))
    memory = result.scalar_one_or_none()
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")
    
    update_data = memory_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(memory, key, value)
    
    await db.flush()
    await db.refresh(memory)
    return Memory.model_validate(memory)


@router.post("")
async def create_memory(
    memory: MemoryCreate,
    _token: str = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
) -> Memory:
    """Create new memory."""
    # Auto-generate path based on memory_type
    if memory.memory_type == "daily":
        if not memory.date:
            raise HTTPException(status_code=400, detail="date is required for daily memories")
        # Extract date portion only
        if isinstance(memory.date, datetime):
            memory_date = memory.date.date()
        else:
            memory_date = memory.date
        path = f"memory/{memory_date.isoformat()}.md"
    elif memory.memory_type == "long_term":
        path = "MEMORY.md"
    else:  # custom
        if not memory.path:
            raise HTTPException(status_code=400, detail="path is required for custom memories")
        path = memory.path
    
    # Check if path already exists for this agent
    existing = await db.execute(
        select(MemoryModel).where(
            MemoryModel.path == path,
            MemoryModel.agent == memory.agent
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Memory with path '{path}' already exists for agent '{memory.agent}'")
    
    db_memory = MemoryModel(
        path=path,
        agent=memory.agent,
        title=memory.title,
        content=memory.content,
        memory_type=memory.memory_type,
        date=memory.date
    )
    db.add(db_memory)
    await db.flush()
    await db.refresh(db_memory)
    return Memory.model_validate(db_memory)


@router.post("/capture")
async def quick_capture(
    request: QuickCaptureRequest,
    _token: str = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
) -> Memory:
    """Quick capture: append text to today's daily memory for specified agent."""
    # Get today's date
    today = date.today()
    today_datetime = datetime(today.year, today.month, today.day)
    path = f"memory/{today.isoformat()}.md"
    
    # Find or create today's memory for this agent
    result = await db.execute(
        select(MemoryModel).where(
            MemoryModel.path == path,
            MemoryModel.agent == request.agent
        )
    )
    memory = result.scalar_one_or_none()
    
    if not memory:
        # Create new daily memory
        memory = MemoryModel(
            path=path,
            agent=request.agent,
            title=f"{request.agent.capitalize()} Daily Memory - {today.isoformat()}",
            content=f"# {today.isoformat()}\n\n",
            memory_type="daily",
            date=today_datetime
        )
        db.add(memory)
        await db.flush()
    
    # Append content with timestamp
    timestamp = datetime.now().strftime("%H:%M:%S")
    if not memory.content.endswith("\n"):
        memory.content += "\n"
    memory.content += f"\n## {timestamp}\n\n{request.content}\n"
    
    await db.flush()
    await db.refresh(memory)
    return Memory.model_validate(memory)


@router.delete("/{memory_id}")
async def delete_memory(
    memory_id: int,
    _token: str = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """Delete a memory."""
    result = await db.execute(select(MemoryModel).where(MemoryModel.id == memory_id))
    memory = result.scalar_one_or_none()
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")
    
    await db.delete(memory)
    await db.flush()
    return {"status": "deleted", "id": memory_id}
