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
    from datetime import datetime, timezone
    import json
    from app.models import Task as TaskModel

    result = await db.execute(select(ProjectModel).where(ProjectModel.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.tracking != "github" or not project.github_repo:
        raise HTTPException(status_code=400, detail="Project is not configured for GitHub tracking")

    try:
        cmd = ["gh", "issue", "list", "--repo", project.github_repo, "--state", "all", "--json", "number,title,state,updatedAt"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
        if proc.returncode != 0:
            raise HTTPException(status_code=500, detail=f"GitHub CLI error: {proc.stderr}")

        issues = json.loads(proc.stdout)
        imported, updated, conflicts, pushed = 0, 0, 0, 0

        existing_tasks_q = await db.execute(select(TaskModel).where(TaskModel.project_id == project_id, TaskModel.external_source == "github"))
        existing_tasks = {(t.external_id or str(t.github_issue_number)): t for t in existing_tasks_q.scalars().all()}

        for issue in issues:
            key = str(issue["number"])
            issue_updated = datetime.fromisoformat(issue["updatedAt"].replace("Z", "+00:00"))
            task = existing_tasks.get(key)
            mapped_status = "completed" if issue["state"].lower() == "closed" else "inbox"
            if not task:
                db.add(TaskModel(
                    id=f"gh-{project_id}-{issue['number']}",
                    title=issue["title"],
                    status=mapped_status,
                    owner="lobs",
                    project_id=project_id,
                    github_issue_number=issue["number"],
                    external_source="github",
                    external_id=key,
                    external_updated_at=issue_updated,
                    sync_state="synced",
                ))
                imported += 1
                continue

            if task.updated_at and task.external_updated_at and task.updated_at > task.external_updated_at and issue_updated > task.external_updated_at:
                task.sync_state = "conflict"
                task.conflict_payload = {"remote_title": issue["title"], "remote_state": issue["state"], "remote_updated_at": issue["updatedAt"]}
                conflicts += 1
                continue

            task.title = issue["title"]
            task.status = mapped_status
            task.external_updated_at = issue_updated
            task.sync_state = "synced"
            updated += 1

        if push and settings.GITHUB_SYNC_PUSH_ENABLED:
            local_changed_q = await db.execute(select(TaskModel).where(TaskModel.project_id == project_id, TaskModel.external_source == "github", TaskModel.sync_state.in_(["local_changed", "synced"])))
            for task in local_changed_q.scalars().all():
                if not task.github_issue_number:
                    continue
                new_state = "close" if task.status == "completed" else "open"
                edit_cmd = ["gh", "issue", "edit", str(task.github_issue_number), "--repo", project.github_repo, "--title", task.title]
                st_cmd = ["gh", "issue", new_state, str(task.github_issue_number), "--repo", project.github_repo]
                e = subprocess.run(edit_cmd, capture_output=True, text=True, timeout=30)
                if e.returncode == 0:
                    subprocess.run(st_cmd, capture_output=True, text=True, timeout=30)
                    task.sync_state = "synced"
                    task.external_updated_at = datetime.now(timezone.utc)
                    pushed += 1

        return {
            "status": "synced",
            "project_id": project_id,
            "repo": project.github_repo,
            "issues_count": len(issues),
            "imported": imported,
            "updated": updated,
            "conflicts": conflicts,
            "pushed": pushed,
            "push_enabled": bool(push and settings.GITHUB_SYNC_PUSH_ENABLED),
        }

    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="GitHub sync timed out")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Sync error: {str(e)}")
