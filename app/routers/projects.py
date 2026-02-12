"""Project API endpoints."""

import subprocess
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Project as ProjectModel
from app.schemas import Project, ProjectCreate, ProjectUpdate
from app.config import settings

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("")
async def list_projects(
    limit: int = settings.DEFAULT_LIMIT,
    offset: int = 0,
    archived: bool = False,
    db: AsyncSession = Depends(get_db)
) -> list[Project]:
    """List all projects with pagination."""
    query = select(ProjectModel).where(ProjectModel.archived == archived).offset(offset).limit(min(limit, settings.MAX_LIMIT))
    result = await db.execute(query)
    projects = result.scalars().all()
    return [Project.model_validate(p) for p in projects]


@router.post("")
async def create_project(
    project: ProjectCreate,
    db: AsyncSession = Depends(get_db)
) -> Project:
    """Create a new project."""
    db_project = ProjectModel(**project.model_dump())
    db.add(db_project)
    await db.flush()
    await db.refresh(db_project)
    return Project.model_validate(db_project)


@router.get("/{project_id}")
async def get_project(
    project_id: str,
    db: AsyncSession = Depends(get_db)
) -> Project:
    """Get a specific project."""
    result = await db.execute(select(ProjectModel).where(ProjectModel.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return Project.model_validate(project)


@router.put("/{project_id}")
async def update_project(
    project_id: str,
    project_update: ProjectUpdate,
    db: AsyncSession = Depends(get_db)
) -> Project:
    """Update a project."""
    result = await db.execute(select(ProjectModel).where(ProjectModel.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    update_data = project_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(project, key, value)
    
    await db.flush()
    await db.refresh(project)
    return Project.model_validate(project)


@router.delete("/{project_id}")
async def delete_project(
    project_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Delete a project."""
    result = await db.execute(select(ProjectModel).where(ProjectModel.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    await db.delete(project)
    return {"status": "deleted"}


@router.post("/{project_id}/archive")
async def archive_project(
    project_id: str,
    db: AsyncSession = Depends(get_db)
) -> Project:
    """Archive a project."""
    result = await db.execute(select(ProjectModel).where(ProjectModel.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    project.archived = True
    await db.flush()
    await db.refresh(project)
    return Project.model_validate(project)


@router.post("/{project_id}/unarchive")
async def unarchive_project(
    project_id: str,
    db: AsyncSession = Depends(get_db)
) -> Project:
    """Unarchive a project."""
    result = await db.execute(select(ProjectModel).where(ProjectModel.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    project.archived = False
    await db.flush()
    await db.refresh(project)
    return Project.model_validate(project)


@router.post("/{project_id}/github-sync")
async def sync_github_project(
    project_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Trigger a sync for GitHub-tracked projects."""
    result = await db.execute(select(ProjectModel).where(ProjectModel.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    if project.tracking != "github" or not project.github_repo:
        raise HTTPException(
            status_code=400,
            detail="Project is not configured for GitHub tracking"
        )
    
    # Use gh CLI to sync GitHub issues
    # This is a basic implementation - could be enhanced with proper task import logic
    try:
        cmd = ["gh", "issue", "list", "--repo", project.github_repo, "--json", "number,title,state,labels"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        
        if result.returncode != 0:
            raise HTTPException(
                status_code=500,
                detail=f"GitHub CLI error: {result.stderr}"
            )
        
        # Parse and return the issues
        import json
        issues = json.loads(result.stdout)
        
        return {
            "status": "synced",
            "project_id": project_id,
            "repo": project.github_repo,
            "issues_count": len(issues),
            "issues": issues
        }
        
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="GitHub sync timed out")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Sync error: {str(e)}")
