"""Project API endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Project as ProjectModel
from app.schemas import Project, ProjectCreate, ProjectUpdate
from app.config import settings
from app.services.github_sync import GitHubSyncService

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
    # Check for duplicate ID
    existing = await db.execute(select(ProjectModel).where(ProjectModel.id == project.id))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Project with id '{project.id}' already exists")
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
    push: bool = False,
    db: AsyncSession = Depends(get_db)
):
    """Two-way sync for GitHub-tracked projects with basic conflict detection."""
    result = await db.execute(select(ProjectModel).where(ProjectModel.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    sync_service = GitHubSyncService(db)
    response = await sync_service.sync_project(
        project,
        push=bool(push and settings.GITHUB_SYNC_PUSH_ENABLED),
    )
    return response
