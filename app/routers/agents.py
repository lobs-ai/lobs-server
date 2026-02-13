"""Agent status API endpoints."""

import os
import subprocess
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from app.database import get_db
from app.models import AgentStatus as AgentStatusModel
from app.schemas import AgentStatus, AgentStatusUpdate
from app.config import settings

router = APIRouter(prefix="/agents", tags=["agents"])


class AgentSetupResponse(BaseModel):
    """Response for agent setup operation."""
    success: bool
    message: str
    agents_setup: list[str]


class AgentFileContent(BaseModel):
    """Schema for agent file content."""
    content: str


# Agent file storage directory (configurable via env)
AGENT_FILES_DIR = os.getenv("AGENT_FILES_DIR", os.path.expanduser("~/lobs-orchestrator/agents"))


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


@router.get("/{agent_type}/files/{filename}")
async def get_agent_file(
    agent_type: str,
    filename: str
) -> AgentFileContent:
    """Read an agent memory file (SOUL.md, MEMORY.md, etc.)."""
    # Validate filename to prevent directory traversal
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    
    file_path = Path(AGENT_FILES_DIR) / agent_type / filename
    
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File {filename} not found for agent {agent_type}")
    
    try:
        content = file_path.read_text(encoding="utf-8")
        return AgentFileContent(content=content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading file: {str(e)}")


@router.put("/{agent_type}/files/{filename}")
async def update_agent_file(
    agent_type: str,
    filename: str,
    file_content: AgentFileContent
) -> AgentFileContent:
    """Write an agent memory file (SOUL.md, MEMORY.md, etc.)."""
    # Validate filename to prevent directory traversal
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    
    agent_dir = Path(AGENT_FILES_DIR) / agent_type
    agent_dir.mkdir(parents=True, exist_ok=True)
    
    file_path = agent_dir / filename
    
    try:
        file_path.write_text(file_content.content, encoding="utf-8")
        return file_content
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error writing file: {str(e)}")


@router.post("/setup")
async def setup_agents() -> AgentSetupResponse:
    """
    Run the setup-agents script to configure all agent workspaces.
    
    This creates workspace directories, copies template files, 
    and registers agents with OpenClaw.
    """
    # Find the setup script
    server_dir = Path(__file__).resolve().parent.parent.parent
    setup_script = server_dir / "bin" / "setup-agents"
    
    if not setup_script.exists():
        raise HTTPException(
            status_code=500,
            detail=f"Setup script not found at {setup_script}"
        )
    
    try:
        # Run the setup script
        result = subprocess.run(
            [str(setup_script)],
            capture_output=True,
            text=True,
            timeout=120,
            check=True
        )
        
        # Parse output to find which agents were setup
        agents_setup = []
        for line in result.stdout.split("\n"):
            if line.startswith("🔧 "):
                agent_name = line.split("🔧 ")[1].rstrip(":")
                agents_setup.append(agent_name)
        
        return AgentSetupResponse(
            success=True,
            message="Agent workspaces configured successfully",
            agents_setup=agents_setup
        )
        
    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=500,
            detail="Setup script timed out"
        )
    except subprocess.CalledProcessError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Setup script failed: {e.stderr}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error running setup script: {str(e)}"
        )
