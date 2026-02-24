"""Learning plan API endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.auth import require_auth
from app.models import LearningPlan, LearningLesson

router = APIRouter(prefix="/learning", tags=["learning"])


class CreatePlanRequest(BaseModel):
    topic: str
    goal: str = "Get 1% better every day"
    total_days: int = 30
    schedule_cron: str = "0 7 * * *"
    delivery_channel: str = "discord"


@router.post("/plans", dependencies=[Depends(require_auth)])
async def create_plan(req: CreatePlanRequest, db: AsyncSession = Depends(get_db)):
    """Create a new learning plan (generates outline via LLM)."""
    from app.services.learning_service import create_plan
    result = await create_plan(
        db, topic=req.topic, goal=req.goal, total_days=req.total_days,
        schedule_cron=req.schedule_cron, delivery_channel=req.delivery_channel,
    )
    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("error"))
    return result


@router.get("/plans", dependencies=[Depends(require_auth)])
async def list_plans(status: str = "active", db: AsyncSession = Depends(get_db)):
    """List learning plans."""
    query = select(LearningPlan)
    if status != "all":
        query = query.where(LearningPlan.status == status)
    result = await db.execute(query.order_by(LearningPlan.created_at.desc()))
    plans = result.scalars().all()
    return [
        {
            "id": p.id, "topic": p.topic, "goal": p.goal,
            "total_days": p.total_days, "current_day": p.current_day,
            "status": p.status, "schedule_cron": p.schedule_cron,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        }
        for p in plans
    ]


@router.get("/plans/{plan_id}", dependencies=[Depends(require_auth)])
async def get_plan(plan_id: str, db: AsyncSession = Depends(get_db)):
    """Get a plan with its outline."""
    plan = await db.get(LearningPlan, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    return {
        "id": plan.id, "topic": plan.topic, "goal": plan.goal,
        "total_days": plan.total_days, "current_day": plan.current_day,
        "status": plan.status, "schedule_cron": plan.schedule_cron,
        "plan_outline": plan.plan_outline,
        "created_at": plan.created_at.isoformat() if plan.created_at else None,
    }


@router.get("/plans/{plan_id}/lessons", dependencies=[Depends(require_auth)])
async def list_lessons(plan_id: str, db: AsyncSession = Depends(get_db)):
    """Get all generated lessons for a plan."""
    result = await db.execute(
        select(LearningLesson).where(LearningLesson.plan_id == plan_id)
        .order_by(LearningLesson.day_number)
    )
    lessons = result.scalars().all()
    return [
        {
            "id": l.id, "day": l.day_number, "title": l.title,
            "summary": l.summary, "document_path": l.document_path,
            "delivered_at": l.delivered_at.isoformat() if l.delivered_at else None,
            "created_at": l.created_at.isoformat() if l.created_at else None,
        }
        for l in lessons
    ]


@router.get("/lessons/{lesson_id}", dependencies=[Depends(require_auth)])
async def get_lesson(lesson_id: str, db: AsyncSession = Depends(get_db)):
    """Get a specific lesson with full content."""
    lesson = await db.get(LearningLesson, lesson_id)
    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")
    return {
        "id": lesson.id, "plan_id": lesson.plan_id,
        "day": lesson.day_number, "title": lesson.title,
        "content": lesson.content, "summary": lesson.summary,
        "document_path": lesson.document_path,
        "delivered_at": lesson.delivered_at.isoformat() if lesson.delivered_at else None,
    }


@router.post("/plans/{plan_id}/pause", dependencies=[Depends(require_auth)])
async def pause_plan(plan_id: str, db: AsyncSession = Depends(get_db)):
    """Pause an active plan."""
    plan = await db.get(LearningPlan, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    plan.status = "paused"
    await db.commit()
    return {"status": "paused", "plan_id": plan_id}


@router.post("/plans/{plan_id}/resume", dependencies=[Depends(require_auth)])
async def resume_plan(plan_id: str, db: AsyncSession = Depends(get_db)):
    """Resume a paused plan."""
    plan = await db.get(LearningPlan, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    plan.status = "active"
    await db.commit()
    return {"status": "active", "plan_id": plan_id}


@router.post("/plans/{plan_id}/generate-next", dependencies=[Depends(require_auth)])
async def generate_next(plan_id: str, db: AsyncSession = Depends(get_db)):
    """Manually trigger the next lesson generation."""
    from app.services.learning_service import generate_next_lesson
    result = await generate_next_lesson(db, plan_id)
    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("error"))
    return result
