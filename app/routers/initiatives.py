"""Initiatives API — list initiatives and manage build/no-build decision memos.

Every approved research initiative must close with a ResearchMemo that records:
  - problem, user_segment, spec_touchpoints, mvp_scope, owner
  - decision ("build" or "no_build")
  - rationale

The memo gate is enforced here (POST /memo) and in the task work-state PATCH.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import AgentInitiative as AgentInitiativeModel, ResearchMemo as ResearchMemoModel
from app.schemas import AgentInitiative, ResearchMemo, ResearchMemoCreate, ResearchMemoUpdate

router = APIRouter(prefix="/initiatives", tags=["initiatives"])

VALID_DECISIONS = {"build", "no_build"}


# ============================================================================
# Initiative list / detail
# ============================================================================


@router.get("")
async def list_initiatives(
    status: str | None = Query(None, description="Filter by status (proposed/approved/rejected/deferred/awaiting_rafe)"),
    category: str | None = Query(None),
    agent: str | None = Query(None, description="Filter by proposed_by_agent"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[AgentInitiative]:
    """List agent initiatives with optional filters."""
    q = select(AgentInitiativeModel).order_by(AgentInitiativeModel.created_at.desc())
    if status:
        q = q.where(AgentInitiativeModel.status == status)
    if category:
        q = q.where(AgentInitiativeModel.category == category)
    if agent:
        q = q.where(AgentInitiativeModel.proposed_by_agent == agent)
    q = q.offset(offset).limit(limit)
    result = await db.execute(q)
    return [AgentInitiative.model_validate(row) for row in result.scalars().all()]


@router.get("/{initiative_id}")
async def get_initiative(
    initiative_id: str,
    db: AsyncSession = Depends(get_db),
) -> AgentInitiative:
    """Get a single initiative by ID."""
    row = await db.get(AgentInitiativeModel, initiative_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Initiative not found")
    return AgentInitiative.model_validate(row)


# ============================================================================
# Research memo (build/no-build gate)
# ============================================================================


@router.post("/{initiative_id}/memo")
async def create_memo(
    initiative_id: str,
    payload: ResearchMemoCreate,
    db: AsyncSession = Depends(get_db),
) -> ResearchMemo:
    """Create a build/no-build memo for a research initiative.

    Each initiative may have at most one memo. Returns 409 if one already exists.
    Returns 422 if decision is not 'build' or 'no_build'.
    """
    # Validate initiative exists
    initiative = await db.get(AgentInitiativeModel, initiative_id)
    if initiative is None:
        raise HTTPException(status_code=404, detail="Initiative not found")

    # Validate decision value
    if payload.decision not in VALID_DECISIONS:
        raise HTTPException(
            status_code=422,
            detail=f"decision must be one of: {', '.join(sorted(VALID_DECISIONS))}",
        )

    # Enforce uniqueness (the DB constraint will also catch it, but give a nicer error)
    existing = await db.execute(
        select(ResearchMemoModel).where(ResearchMemoModel.initiative_id == initiative_id)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=409,
            detail="A memo already exists for this initiative. Use PATCH to update it.",
        )

    memo = ResearchMemoModel(
        id=str(uuid.uuid4()),
        initiative_id=initiative_id,
        task_id=payload.task_id or initiative.task_id,
        problem=payload.problem,
        user_segment=payload.user_segment,
        spec_touchpoints=payload.spec_touchpoints or [],
        mvp_scope=payload.mvp_scope,
        owner=payload.owner,
        decision=payload.decision,
        rationale=payload.rationale,
        stale_flagged=False,
    )
    db.add(memo)
    await db.flush()
    await db.refresh(memo)
    return ResearchMemo.model_validate(memo)


@router.get("/{initiative_id}/memo")
async def get_memo(
    initiative_id: str,
    db: AsyncSession = Depends(get_db),
) -> ResearchMemo:
    """Get the build/no-build memo for an initiative."""
    result = await db.execute(
        select(ResearchMemoModel).where(ResearchMemoModel.initiative_id == initiative_id)
    )
    memo = result.scalar_one_or_none()
    if memo is None:
        raise HTTPException(status_code=404, detail="No memo found for this initiative")
    return ResearchMemo.model_validate(memo)


@router.patch("/{initiative_id}/memo")
async def update_memo(
    initiative_id: str,
    payload: ResearchMemoUpdate,
    db: AsyncSession = Depends(get_db),
) -> ResearchMemo:
    """Update fields on an existing research memo."""
    result = await db.execute(
        select(ResearchMemoModel).where(ResearchMemoModel.initiative_id == initiative_id)
    )
    memo = result.scalar_one_or_none()
    if memo is None:
        raise HTTPException(status_code=404, detail="No memo found for this initiative")

    update_data = payload.model_dump(exclude_unset=True)

    # Validate decision if it's being updated
    if "decision" in update_data and update_data["decision"] not in VALID_DECISIONS:
        raise HTTPException(
            status_code=422,
            detail=f"decision must be one of: {', '.join(sorted(VALID_DECISIONS))}",
        )

    for key, value in update_data.items():
        setattr(memo, key, value)

    await db.flush()
    await db.refresh(memo)
    return ResearchMemo.model_validate(memo)


# ============================================================================
# Stale initiatives pruning report
# ============================================================================


@router.get("/stale/report")
async def stale_initiatives_report(
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return a report of stale initiatives: approved with no memo after 7+ days.

    This is the same logic used by the weekly pruning service.
    """
    from app.services.initiative_pruning import get_stale_initiatives
    stale = await get_stale_initiatives(db)
    return {
        "count": len(stale),
        "initiatives": [AgentInitiative.model_validate(i).model_dump() for i in stale],
    }
