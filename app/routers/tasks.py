"""Task API endpoints."""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Task as TaskModel
from app.schemas import Task, TaskCreate, TaskUpdate, TaskStatusUpdate, TaskWorkStateUpdate, TaskReviewStateUpdate
from app.config import settings

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("")
async def list_tasks(
    limit: int = settings.DEFAULT_LIMIT,
    offset: int = 0,
    project_id: Optional[str] = None,
    status: Optional[str] = None,
    owner: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
) -> list[Task]:
    """List tasks with filtering and pagination."""
    query = select(TaskModel)
    
    if project_id:
        query = query.where(TaskModel.project_id == project_id)
    if status:
        query = query.where(TaskModel.status == status)
    if owner:
        query = query.where(TaskModel.owner == owner)
    
    query = query.offset(offset).limit(min(limit, settings.MAX_LIMIT))
    result = await db.execute(query)
    tasks = result.scalars().all()
    return [Task.model_validate(t) for t in tasks]


@router.post("")
async def create_task(
    task: TaskCreate,
    db: AsyncSession = Depends(get_db)
) -> Task:
    """Create a new task."""
    db_task = TaskModel(**task.model_dump())
    db.add(db_task)
    await db.flush()
    await db.refresh(db_task)
    return Task.model_validate(db_task)


@router.get("/{task_id}")
async def get_task(
    task_id: str,
    db: AsyncSession = Depends(get_db)
) -> Task:
    """Get a specific task."""
    result = await db.execute(select(TaskModel).where(TaskModel.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return Task.model_validate(task)


@router.put("/{task_id}")
async def update_task(
    task_id: str,
    task_update: TaskUpdate,
    db: AsyncSession = Depends(get_db)
) -> Task:
    """Update a task."""
    result = await db.execute(select(TaskModel).where(TaskModel.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    update_data = task_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(task, key, value)
    
    await db.flush()
    await db.refresh(task)
    return Task.model_validate(task)


@router.delete("/{task_id}")
async def delete_task(
    task_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Delete a task."""
    result = await db.execute(select(TaskModel).where(TaskModel.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    await db.delete(task)
    return {"status": "deleted"}


@router.post("/{task_id}/archive")
async def archive_task(
    task_id: str,
    db: AsyncSession = Depends(get_db)
) -> Task:
    """Archive a task by setting status to completed."""
    result = await db.execute(select(TaskModel).where(TaskModel.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    task.status = "completed"
    await db.flush()
    await db.refresh(task)
    return Task.model_validate(task)


@router.patch("/{task_id}/status")
async def update_task_status(
    task_id: str,
    status_update: TaskStatusUpdate,
    db: AsyncSession = Depends(get_db)
) -> Task:
    """Update task status."""
    result = await db.execute(select(TaskModel).where(TaskModel.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    task.status = status_update.status
    await db.flush()
    await db.refresh(task)
    return Task.model_validate(task)


@router.patch("/{task_id}/work-state")
async def update_task_work_state(
    task_id: str,
    work_state_update: TaskWorkStateUpdate,
    db: AsyncSession = Depends(get_db)
) -> Task:
    """Update task work state."""
    result = await db.execute(select(TaskModel).where(TaskModel.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    task.work_state = work_state_update.work_state
    await db.flush()
    await db.refresh(task)
    return Task.model_validate(task)


@router.patch("/{task_id}/review-state")
async def update_task_review_state(
    task_id: str,
    review_state_update: TaskReviewStateUpdate,
    db: AsyncSession = Depends(get_db)
) -> Task:
    """Update task review state."""
    result = await db.execute(select(TaskModel).where(TaskModel.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    task.review_state = review_state_update.review_state
    await db.flush()
    await db.refresh(task)
    return Task.model_validate(task)
