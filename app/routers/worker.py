"""Worker status and history API endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import WorkerStatus as WorkerStatusModel, WorkerRun as WorkerRunModel
from app.schemas import WorkerStatus, WorkerStatusUpdate, WorkerRun, WorkerRunCreate
from app.config import settings

router = APIRouter(prefix="/worker", tags=["worker"])


@router.get("/status")
async def get_worker_status(
    db: AsyncSession = Depends(get_db)
) -> WorkerStatus:
    """Get current worker status (singleton)."""
    result = await db.execute(select(WorkerStatusModel).where(WorkerStatusModel.id == 1))
    status = result.scalar_one_or_none()
    
    if not status:
        # Create default status if doesn't exist
        status = WorkerStatusModel(id=1, active=False, tasks_completed=0, input_tokens=0, output_tokens=0)
        db.add(status)
        await db.flush()
        await db.refresh(status)
    
    return WorkerStatus.model_validate(status)


@router.put("/status")
async def update_worker_status(
    status_update: WorkerStatusUpdate,
    db: AsyncSession = Depends(get_db)
) -> WorkerStatus:
    """Update worker status."""
    result = await db.execute(select(WorkerStatusModel).where(WorkerStatusModel.id == 1))
    status = result.scalar_one_or_none()
    
    if not status:
        # Create with update data
        status = WorkerStatusModel(id=1, **status_update.model_dump(exclude_unset=True))
        db.add(status)
    else:
        # Update existing
        update_data = status_update.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(status, key, value)
    
    await db.flush()
    await db.refresh(status)
    return WorkerStatus.model_validate(status)


@router.get("/history")
async def list_worker_runs(
    limit: int = settings.DEFAULT_LIMIT,
    offset: int = 0,
    db: AsyncSession = Depends(get_db)
) -> list[WorkerRun]:
    """List worker run history."""
    query = select(WorkerRunModel).order_by(WorkerRunModel.id.desc()).offset(offset).limit(min(limit, settings.MAX_LIMIT))
    result = await db.execute(query)
    runs = result.scalars().all()
    return [WorkerRun.model_validate(r) for r in runs]


@router.post("/history")
async def create_worker_run(
    run: WorkerRunCreate,
    db: AsyncSession = Depends(get_db)
) -> WorkerRun:
    """Create a new worker run record."""
    db_run = WorkerRunModel(**run.model_dump())
    db.add(db_run)
    await db.flush()
    await db.refresh(db_run)
    return WorkerRun.model_validate(db_run)
