"""Agent profile registry, routine registry, and knowledge requests."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import (
    AgentProfile as AgentProfileModel,
    RoutineRegistry as RoutineRegistryModel,
    RoutineAuditEvent as RoutineAuditEventModel,
    KnowledgeRequest as KnowledgeRequestModel,
    ResearchRequest as ResearchRequestModel,
)
from app.orchestrator.routine_runner import RoutineRunner
from app.schemas import (
    AgentProfile,
    AgentProfileCreate,
    RoutineRegistry,
    RoutineRegistryCreate,
    RoutineAuditEvent,
    KnowledgeRequest,
    KnowledgeRequestCreate,
)

router = APIRouter(prefix="/governance", tags=["governance"])


@router.get("/agent-profiles")
async def list_agent_profiles(db: AsyncSession = Depends(get_db)) -> list[AgentProfile]:
    result = await db.execute(select(AgentProfileModel))
    return [AgentProfile.model_validate(x) for x in result.scalars().all()]


@router.post("/agent-profiles")
async def create_agent_profile(payload: AgentProfileCreate, db: AsyncSession = Depends(get_db)) -> AgentProfile:
    existing = await db.execute(select(AgentProfileModel).where(AgentProfileModel.agent_type == payload.agent_type))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="agent_type already registered")
    row = AgentProfileModel(**payload.model_dump())
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return AgentProfile.model_validate(row)


@router.get("/routines")
async def list_routines(db: AsyncSession = Depends(get_db)) -> list[RoutineRegistry]:
    result = await db.execute(select(RoutineRegistryModel))
    return [RoutineRegistry.model_validate(x) for x in result.scalars().all()]


@router.post("/routines")
async def create_routine(payload: RoutineRegistryCreate, db: AsyncSession = Depends(get_db)) -> RoutineRegistry:
    row = RoutineRegistryModel(**payload.model_dump())

    # Initialize next_run_at on creation if a schedule is provided.
    if row.schedule and row.next_run_at is None:
        runner = RoutineRunner(db)
        row.next_run_at = runner._compute_next_run(row, datetime.now(timezone.utc))

    db.add(row)
    await db.flush()
    await db.refresh(row)
    return RoutineRegistry.model_validate(row)


@router.get("/routines/{routine_id}/audit")
async def list_routine_audit_events(
    routine_id: str,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
) -> list[RoutineAuditEvent]:
    result = await db.execute(
        select(RoutineAuditEventModel)
        .where(RoutineAuditEventModel.routine_id == routine_id)
        .order_by(RoutineAuditEventModel.created_at.desc())
        .limit(min(limit, 500))
    )
    return [RoutineAuditEvent.model_validate(x) for x in result.scalars().all()]


@router.post("/routines/{routine_id}/run")
async def run_routine_now(
    routine_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    routine = await db.get(RoutineRegistryModel, routine_id)
    if routine is None:
        raise HTTPException(status_code=404, detail="routine not found")

    # Manual execution: bypass execution_policy gate.
    # The runner still records audit events.
    runner = RoutineRunner(db)

    # Provide a minimal built-in no-op hook to allow manual testing.
    async def noop(_routine: RoutineRegistryModel):
        return {"hook": "noop", "status": "ok"}

    runner.hooks.setdefault("noop", noop)

    payload = await runner.run_routine_now(routine)
    await db.flush()
    return {"status": "ok", "routine_id": routine_id, "result": payload}


@router.get("/knowledge-requests")
async def list_knowledge_requests(project_id: str | None = None, db: AsyncSession = Depends(get_db)) -> list[KnowledgeRequest]:
    query = select(KnowledgeRequestModel)
    if project_id:
        query = query.where(KnowledgeRequestModel.project_id == project_id)
    result = await db.execute(query)
    return [KnowledgeRequest.model_validate(x) for x in result.scalars().all()]


@router.post("/knowledge-requests")
async def create_knowledge_request(payload: KnowledgeRequestCreate, db: AsyncSession = Depends(get_db)) -> KnowledgeRequest:
    row = KnowledgeRequestModel(**payload.model_dump())
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return KnowledgeRequest.model_validate(row)


@router.post("/knowledge-requests/backfill-from-research")
async def backfill_knowledge_from_research(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ResearchRequestModel))
    created = 0
    for r in result.scalars().all():
        existing = await db.get(KnowledgeRequestModel, r.id)
        if existing:
            continue
        db.add(KnowledgeRequestModel(
            id=r.id,
            project_id=r.project_id,
            topic_id=r.topic_id,
            prompt=r.prompt or "",
            status=r.status or "pending",
            response=r.response,
            source_research_request_id=r.id,
        ))
        created += 1
    await db.flush()
    return {"status": "ok", "created": created}
