"""
Reflections API — CRUD + review actions for AgentReflection records.

Endpoints:
  GET  /reflections              list (filters: agent, status, limit, offset)
  POST /reflections              create a new reflection record
  GET  /reflections/{id}         get single reflection
  POST /reflections/{id}/approve approve a reflection
  POST /reflections/{id}/reject  reject a reflection
  POST /reflections/{id}/feedback add reviewer feedback
"""

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import AgentReflection

router = APIRouter(prefix="/reflections", tags=["reflections"])

UTC = timezone.utc


# ─── helpers ─────────────────────────────────────────────────────────────────

def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _payload(r: AgentReflection) -> dict[str, Any]:
    """Serialize a reflection row to the camelCase shape the frontend expects."""
    # Derive a human summary if none stored yet
    summary = getattr(r, "summary", None)
    if not summary and isinstance(r.result, dict):
        summary = r.result.get("summary") or r.result.get("executive_summary")

    return {
        "id": r.id,
        "agentType": r.agent_type,
        "reflectionType": r.reflection_type,
        "status": r.status,
        "summary": summary,
        "approvedBy": getattr(r, "approved_by", None),
        "feedback": getattr(r, "feedback", None),
        "windowStart": _iso(r.window_start),
        "windowEnd": _iso(r.window_end),
        "inefficiencies": r.inefficiencies or [],
        "missedOpportunities": r.missed_opportunities or [],
        "systemRisks": r.system_risks or [],
        "identityAdjustments": r.identity_adjustments or [],
        "result": r.result,
        "createdAt": _iso(r.created_at),
        "completedAt": _iso(r.completed_at),
    }


# ─── schemas ─────────────────────────────────────────────────────────────────

class CreateReflectionRequest(BaseModel):
    agentType: str
    reflectionType: str = "strategic"
    status: str = "pending"
    summary: str | None = None
    windowStart: str | None = None
    windowEnd: str | None = None
    inefficiencies: list[str] | None = None
    missedOpportunities: list[str] | None = None
    systemRisks: list[str] | None = None
    identityAdjustments: list[str] | None = None
    result: dict | None = None


class ApproveRequest(BaseModel):
    approvedBy: str = "lobs"
    feedback: str | None = None


class RejectRequest(BaseModel):
    rejectedBy: str = "lobs"
    feedback: str | None = None


class FeedbackRequest(BaseModel):
    feedback: str
    author: str = "lobs"


# ─── routes ──────────────────────────────────────────────────────────────────

@router.get("")
async def list_reflections(
    agent: str | None = Query(None, description="Filter by agent_type"),
    status: str | None = Query(None, description="Filter by status"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """List reflections, newest first."""
    query = select(AgentReflection).order_by(AgentReflection.created_at.desc())
    if agent:
        query = query.where(AgentReflection.agent_type == agent)
    if status:
        query = query.where(AgentReflection.status == status)
    query = query.offset(offset).limit(limit)

    result = await db.execute(query)
    rows = result.scalars().all()
    return [_payload(r) for r in rows]


@router.post("", status_code=201)
async def create_reflection(
    body: CreateReflectionRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Create a new reflection record (used by agents or tests)."""
    rid = str(uuid.uuid4())
    now = datetime.now(UTC).replace(tzinfo=None)

    def _parse_dt(s: str | None):
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None

    refl = AgentReflection(
        id=rid,
        agent_type=body.agentType,
        reflection_type=body.reflectionType,
        status=body.status,
        summary=body.summary,
        window_start=_parse_dt(body.windowStart),
        window_end=_parse_dt(body.windowEnd),
        inefficiencies=body.inefficiencies or [],
        missed_opportunities=body.missedOpportunities or [],
        system_risks=body.systemRisks or [],
        identity_adjustments=body.identityAdjustments or [],
        result=body.result,
        created_at=now,
    )
    db.add(refl)
    await db.commit()
    await db.refresh(refl)
    return _payload(refl)


@router.get("/{reflection_id}")
async def get_reflection(
    reflection_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Fetch a single reflection by ID."""
    refl = await db.get(AgentReflection, reflection_id)
    if refl is None:
        raise HTTPException(status_code=404, detail="Reflection not found")
    return _payload(refl)


@router.post("/{reflection_id}/approve")
async def approve_reflection(
    reflection_id: str,
    body: ApproveRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Approve a reflection (sets status=approved, records who approved)."""
    refl = await db.get(AgentReflection, reflection_id)
    if refl is None:
        raise HTTPException(status_code=404, detail="Reflection not found")

    refl.status = "approved"
    refl.approved_by = body.approvedBy
    if body.feedback:
        refl.feedback = body.feedback

    await db.commit()
    await db.refresh(refl)
    return _payload(refl)


@router.post("/{reflection_id}/reject")
async def reject_reflection(
    reflection_id: str,
    body: RejectRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Reject a reflection (sets status=rejected)."""
    refl = await db.get(AgentReflection, reflection_id)
    if refl is None:
        raise HTTPException(status_code=404, detail="Reflection not found")

    refl.status = "rejected"
    refl.approved_by = body.rejectedBy
    if body.feedback:
        refl.feedback = body.feedback

    await db.commit()
    await db.refresh(refl)
    return _payload(refl)


@router.post("/{reflection_id}/feedback")
async def add_feedback(
    reflection_id: str,
    body: FeedbackRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Append reviewer feedback to a reflection without changing its status."""
    refl = await db.get(AgentReflection, reflection_id)
    if refl is None:
        raise HTTPException(status_code=404, detail="Reflection not found")

    existing = getattr(refl, "feedback", None) or ""
    timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    new_entry = f"[{timestamp} — {body.author}] {body.feedback}"
    refl.feedback = f"{existing}\n{new_entry}".strip() if existing else new_entry

    await db.commit()
    await db.refresh(refl)
    return _payload(refl)
