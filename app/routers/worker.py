"""Worker status and history API endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import WorkerStatus as WorkerStatusModel, WorkerRun as WorkerRunModel, Task
from app.schemas import WorkerStatus, WorkerStatusUpdate, WorkerRun, WorkerRunCreate
from app.config import settings

router = APIRouter(prefix="/worker", tags=["worker"])


@router.get("/status")
async def get_worker_status(
    db: AsyncSession = Depends(get_db)
) -> WorkerStatus:
    """Get current worker status (singleton).
    
    Uses retry logic to handle database locks under high concurrency.
    """
    import asyncio
    import logging
    
    logger = logging.getLogger(__name__)
    
    # Retry with exponential backoff: helps when multiple requests hit DB simultaneously
    for _attempt in range(5):
        try:
            if _attempt > 0:
                await asyncio.sleep(_attempt * 0.5)
            
            result = await db.execute(select(WorkerStatusModel).where(WorkerStatusModel.id == 1))
            status = result.scalar_one_or_none()
            
            if not status:
                status = WorkerStatusModel(id=1, active=False, tasks_completed=0, input_tokens=0, output_tokens=0)
                db.add(status)
                await db.flush()
                await db.refresh(status)
            
            return WorkerStatus.model_validate(status)
        
        except Exception as e:
            if _attempt < 4:
                logger.debug("[WORKER_STATUS] Read failed (attempt %d/5): %s, retrying...", _attempt + 1, e)
                try:
                    await db.rollback()
                except Exception:
                    pass
            else:
                logger.error("[WORKER_STATUS] Failed to get worker status after 5 attempts: %s", e, exc_info=True)
                raise


@router.put("/status")
async def update_worker_status(
    status_update: WorkerStatusUpdate,
    db: AsyncSession = Depends(get_db)
) -> WorkerStatus:
    """Update worker status with retry-on-lock logic."""
    import asyncio
    import logging
    
    logger = logging.getLogger(__name__)
    
    # Retry with exponential backoff: helps when multiple requests hit DB simultaneously
    for _attempt in range(5):
        try:
            if _attempt > 0:
                await asyncio.sleep(_attempt * 0.5)
            
            result = await db.execute(select(WorkerStatusModel).where(WorkerStatusModel.id == 1))
            status = result.scalar_one_or_none()
            
            if not status:
                status = WorkerStatusModel(id=1, **status_update.model_dump(exclude_unset=True))
                db.add(status)
            else:
                update_data = status_update.model_dump(exclude_unset=True)
                for key, value in update_data.items():
                    setattr(status, key, value)
            
            await db.commit()
            await db.refresh(status)
            return WorkerStatus.model_validate(status)
        
        except Exception as e:
            if _attempt < 4:
                logger.debug("[WORKER_STATUS] Update failed (attempt %d/5): %s, retrying...", _attempt + 1, e)
                try:
                    await db.rollback()
                except Exception:
                    pass
            else:
                logger.error("[WORKER_STATUS] Failed to update worker status after 5 attempts: %s", e, exc_info=True)
                raise


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


@router.get("/activity")
async def list_activity(
    limit: int = 20,
    offset: int = 0,
    db: AsyncSession = Depends(get_db)
) -> list[dict]:
    """List recent agent activity with task details and summaries."""
    query = (
        select(WorkerRunModel)
        .order_by(WorkerRunModel.id.desc())
        .offset(offset)
        .limit(min(limit, 100))
    )
    result = await db.execute(query)
    runs = result.scalars().all()
    
    activity = []
    for run in runs:
        entry = {
            "id": run.id,
            "worker_id": run.worker_id,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "ended_at": run.ended_at.isoformat() if run.ended_at else None,
            "succeeded": run.succeeded,
            "summary": run.summary,
            "source": run.source,
            "task_id": run.task_id,
            "task_title": None,
            "project_id": None,
            "agent": None,
        }
        
        # Join task info
        if run.task_id:
            task = await db.get(Task, run.task_id)
            if task:
                entry["task_title"] = task.title
                entry["project_id"] = task.project_id
                entry["agent"] = task.agent
        
        activity.append(entry)
    
    return activity


@router.post("/history")
async def create_worker_run(
    run: WorkerRunCreate,
    db: AsyncSession = Depends(get_db)
) -> WorkerRun:
    """Create a new worker run record with retry-on-lock logic."""
    import asyncio
    import logging
    
    logger = logging.getLogger(__name__)
    
    # Retry with exponential backoff: helps when multiple requests hit DB simultaneously
    for _attempt in range(5):
        try:
            if _attempt > 0:
                await asyncio.sleep(_attempt * 0.5)
            
            db_run = WorkerRunModel(**run.model_dump())
            db.add(db_run)
            await db.commit()
            await db.refresh(db_run)
            return WorkerRun.model_validate(db_run)
        
        except Exception as e:
            if _attempt < 4:
                logger.debug("[WORKER_RUN] Create failed (attempt %d/5): %s, retrying...", _attempt + 1, e)
                try:
                    await db.rollback()
                except Exception:
                    pass
            else:
                logger.error("[WORKER_RUN] Failed to create worker run after 5 attempts: %s", e, exc_info=True)
                raise
