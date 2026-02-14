"""Memory synchronization service for multi-agent workspaces."""

import os
import glob
from pathlib import Path
from datetime import datetime
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Memory as MemoryModel
from app.config import settings


# Agent workspace paths
AGENT_WORKSPACES = {
    "main": Path.home() / ".openclaw" / "workspace",
    "programmer": Path.home() / ".openclaw" / "workspace-programmer",
    "writer": Path.home() / ".openclaw" / "workspace-writer",
    "researcher": Path.home() / ".openclaw" / "workspace-researcher",
    "reviewer": Path.home() / ".openclaw" / "workspace-reviewer",
    "architect": Path.home() / ".openclaw" / "workspace-architect",
}


def discover_agent_workspaces() -> dict[str, Path]:
    """Discover all agent workspaces dynamically."""
    workspaces = {}
    base_dir = Path.home() / ".openclaw"
    
    # Main workspace
    main_ws = base_dir / "workspace"
    if main_ws.exists():
        workspaces["main"] = main_ws
    
    # Workspace-{agent} pattern
    for ws_dir in base_dir.glob("workspace-*"):
        if ws_dir.is_dir():
            agent_name = ws_dir.name.replace("workspace-", "")
            workspaces[agent_name] = ws_dir
    
    return workspaces


def parse_memory_file(file_path: Path, agent: str) -> dict:
    """Parse a memory file and extract metadata."""
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Determine memory type
    if file_path.name == "MEMORY.md":
        memory_type = "long_term"
        title = f"{agent.capitalize()} - Long-term Memory"
        date = None
    elif file_path.parent.name == "memory":
        # Daily memory file
        memory_type = "daily"
        date_str = file_path.stem  # e.g., "2026-02-12"
        try:
            date = datetime.strptime(date_str, "%Y-%m-%d")
            title = f"{agent.capitalize()} - {date_str}"
        except ValueError:
            # Custom memory file
            memory_type = "custom"
            title = f"{agent.capitalize()} - {file_path.stem}"
            date = None
    else:
        memory_type = "custom"
        title = f"{agent.capitalize()} - {file_path.stem}"
        date = None
    
    # Get relative path from workspace root
    workspace = AGENT_WORKSPACES.get(agent) or discover_agent_workspaces().get(agent)
    if workspace:
        try:
            rel_path = file_path.relative_to(workspace)
        except ValueError:
            rel_path = file_path
    else:
        rel_path = file_path
    
    return {
        "path": str(rel_path),
        "agent": agent,
        "title": title,
        "content": content,
        "memory_type": memory_type,
        "date": date,
    }


async def sync_agent_memories(db: AsyncSession, agent: Optional[str] = None) -> dict:
    """
    Sync memories from filesystem to database.
    
    Args:
        db: Database session
        agent: Specific agent to sync, or None for all agents
    
    Returns:
        dict with sync stats (new, updated, unchanged, errors)
    """
    stats = {
        "new": 0,
        "updated": 0,
        "unchanged": 0,
        "errors": [],
    }
    
    # Determine which agents to sync
    workspaces = discover_agent_workspaces()
    if agent:
        if agent not in workspaces:
            stats["errors"].append(f"Agent workspace not found: {agent}")
            return stats
        agents_to_sync = {agent: workspaces[agent]}
    else:
        agents_to_sync = workspaces
    
    # Sync each agent's workspace
    for agent_name, workspace_path in agents_to_sync.items():
        if not workspace_path.exists():
            stats["errors"].append(f"Workspace not found: {workspace_path}")
            continue
        
        # Find all memory files
        memory_files = []
        
        # MEMORY.md
        long_term_file = workspace_path / "MEMORY.md"
        if long_term_file.exists():
            memory_files.append(long_term_file)
        
        # memory/*.md
        memory_dir = workspace_path / "memory"
        if memory_dir.exists():
            memory_files.extend(memory_dir.glob("*.md"))
        
        # Process each file
        for file_path in memory_files:
            try:
                # Parse file
                memory_data = parse_memory_file(file_path, agent_name)
                
                # Get file modification time
                file_mtime = datetime.fromtimestamp(file_path.stat().st_mtime)
                
                # Check if memory exists in DB
                result = await db.execute(
                    select(MemoryModel).where(
                        MemoryModel.path == memory_data["path"],
                        MemoryModel.agent == agent_name
                    )
                )
                existing = result.scalar_one_or_none()
                
                if existing:
                    # Check if file is newer than DB record
                    if file_mtime > existing.updated_at:
                        # Update
                        existing.title = memory_data["title"]
                        existing.content = memory_data["content"]
                        existing.memory_type = memory_data["memory_type"]
                        existing.date = memory_data["date"]
                        stats["updated"] += 1
                    else:
                        stats["unchanged"] += 1
                else:
                    # Create new
                    new_memory = MemoryModel(**memory_data)
                    db.add(new_memory)
                    stats["new"] += 1
                
            except Exception as e:
                stats["errors"].append(f"Error processing {file_path}: {str(e)}")
    
    # Commit all changes
    try:
        await db.flush()
    except Exception as e:
        await db.rollback()
        stats["errors"].append(f"Database error: {str(e)}")
    
    return stats


async def get_agent_memory_counts(db: AsyncSession) -> list[dict]:
    """
    Get memory counts per agent.
    
    Returns:
        List of dicts with agent, memory_count, last_updated
    """
    from sqlalchemy import func as sql_func
    
    result = await db.execute(
        select(
            MemoryModel.agent,
            sql_func.count(MemoryModel.id).label("memory_count"),
            sql_func.max(MemoryModel.updated_at).label("last_updated")
        )
        .group_by(MemoryModel.agent)
        .order_by(MemoryModel.agent)
    )
    
    rows = result.all()
    return [
        {
            "agent": row.agent,
            "memory_count": row.memory_count,
            "last_updated": row.last_updated.isoformat() if row.last_updated else None
        }
        for row in rows
    ]
