"""Agent status API endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import AgentStatus as AgentStatusModel
from app.schemas import AgentStatus, AgentStatusUpdate
from app.config import settings

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("")
async def list_agents(
    limit: int = settings.DEFAULT_LIMIT,
    offset: int = 0,
    db: AsyncSession = Depends(get_db)
) -> list[AgentStatus]:
    """List all agent statuses."""
    query = select(AgentStatusModel).offset(offset).limit(min(limit, settings.MAX_LIMIT))
    result = await db.execute(query)
    agents = result.scalars().all()
    return [AgentStatus.model_validate(a) for a in agents]


@router.get("/{agent_type}")
async def get_agent_status(
    agent_type: str,
    db: AsyncSession = Depends(get_db)
) -> AgentStatus:
    """Get status for a specific agent type."""
    result = await db.execute(select(AgentStatusModel).where(AgentStatusModel.agent_type == agent_type))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return AgentStatus.model_validate(agent)


@router.put("/{agent_type}")
async def update_agent_status(
    agent_type: str,
    status_update: AgentStatusUpdate,
    db: AsyncSession = Depends(get_db)
) -> AgentStatus:
    """Update agent status."""
    result = await db.execute(select(AgentStatusModel).where(AgentStatusModel.agent_type == agent_type))
    agent = result.scalar_one_or_none()
    
    if not agent:
        agent = AgentStatusModel(agent_type=agent_type, **status_update.model_dump(exclude_unset=True))
        db.add(agent)
    else:
        update_data = status_update.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(agent, key, value)
    
    await db.flush()
    await db.refresh(agent)
    return AgentStatus.model_validate(agent)
