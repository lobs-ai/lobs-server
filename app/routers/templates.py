"""Task template API endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import TaskTemplate as TaskTemplateModel
from app.schemas import TaskTemplate, TaskTemplateCreate, TaskTemplateUpdate
from app.config import settings

router = APIRouter(prefix="/templates", tags=["templates"])


@router.get("")
async def list_templates(
    limit: int = settings.DEFAULT_LIMIT,
    offset: int = 0,
    db: AsyncSession = Depends(get_db)
) -> list[TaskTemplate]:
    """List task templates."""
    query = select(TaskTemplateModel).offset(offset).limit(min(limit, settings.MAX_LIMIT))
    result = await db.execute(query)
    templates = result.scalars().all()
    return [TaskTemplate.model_validate(t) for t in templates]


@router.post("")
async def create_template(
    template: TaskTemplateCreate,
    db: AsyncSession = Depends(get_db)
) -> TaskTemplate:
    """Create a new task template."""
    db_template = TaskTemplateModel(**template.model_dump())
    db.add(db_template)
    await db.flush()
    await db.refresh(db_template)
    return TaskTemplate.model_validate(db_template)


@router.get("/{template_id}")
async def get_template(
    template_id: str,
    db: AsyncSession = Depends(get_db)
) -> TaskTemplate:
    """Get a specific task template."""
    result = await db.execute(select(TaskTemplateModel).where(TaskTemplateModel.id == template_id))
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return TaskTemplate.model_validate(template)


@router.put("/{template_id}")
async def update_template(
    template_id: str,
    template_update: TaskTemplateUpdate,
    db: AsyncSession = Depends(get_db)
) -> TaskTemplate:
    """Update a task template."""
    result = await db.execute(select(TaskTemplateModel).where(TaskTemplateModel.id == template_id))
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    
    update_data = template_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(template, key, value)
    
    await db.flush()
    await db.refresh(template)
    return TaskTemplate.model_validate(template)


@router.delete("/{template_id}")
async def delete_template(
    template_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Delete a task template."""
    result = await db.execute(select(TaskTemplateModel).where(TaskTemplateModel.id == template_id))
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    
    await db.delete(template)
    return {"status": "deleted"}
