"""Orchestrator reflection endpoints - intelligence, initiatives, and budgets."""

from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Any

from app.database import get_db
from app.models import OrchestratorSetting, AgentInitiative, AgentReflection, SystemSweep
from app.orchestrator.sweep_arbitrator import DEFAULT_DAILY_BUDGET
from app.orchestrator.initiative_decisions import InitiativeDecisionEngine
from app.orchestrator import OrchestratorEngine

router = APIRouter(prefix="/orchestrator", tags=["orchestrator"])


class AutonomyBudgetUpdate(BaseModel):
    daily: dict[str, int]


class InitiativeDecisionRequest(BaseModel):
    decision: str  # approve|defer|reject|escalate
    revised_title: str | None = None
    revised_description: str | None = None
    selected_agent: str | None = None
    selected_project_id: str | None = None
    decision_summary: str | None = None
    learning_feedback: str | None = None
    decided_by: str = "lobs"  # lobs or rafe


class BatchInitiativeDecision(BaseModel):
    initiative_id: str
    decision: str  # approve|defer|reject|escalate
    revised_title: str | None = None
    revised_description: str | None = None
    selected_agent: str | None = None
    selected_project_id: str | None = None
    decision_summary: str | None = None
    learning_feedback: str | None = None


class LobsNewTask(BaseModel):
    """A task Lobs creates directly during batch review — ideas inspired by the initiatives."""
    title: str
    notes: str | None = None
    project_id: str | None = None
    agent: str | None = None
    status: str = "active"
    work_state: str = "not_started"
    owner: str = "lobs"
    rationale: str | None = None  # why Lobs is creating this


class BatchInitiativeDecisionRequest(BaseModel):
    decisions: list[BatchInitiativeDecision] = Field(default_factory=list)
    new_tasks: list[LobsNewTask] = Field(default_factory=list)


def get_orchestrator(request: Request) -> OrchestratorEngine:
    """Get the orchestrator instance from app state."""
    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is None:
        raise HTTPException(
            status_code=503,
            detail="Orchestrator not initialized or disabled"
        )
    return orchestrator


@router.post("/reflection/trigger")
async def trigger_reflection_cycle(
    request: Request,
) -> dict[str, Any]:
    """Manually trigger a strategic reflection cycle.
    
    Signals the engine to run reflections on its next tick (avoids DB lock
    contention by letting the engine's own session handle the work).
    """
    engine: OrchestratorEngine | None = getattr(request.app.state, "orchestrator", None)
    if not engine:
        raise HTTPException(status_code=503, detail="Orchestrator not running")

    engine._force_reflection = True
    return {"ok": True, "message": "Reflection cycle will run on next engine tick (within ~10s)"}


@router.get("/intelligence/summary")
async def get_intelligence_summary(
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Summarize reflection/initiative/sweep pipeline state for Status dashboard."""
    from datetime import datetime, timedelta

    # Load all data
    reflections = await db.execute(select(AgentReflection).order_by(AgentReflection.created_at.desc()))
    initiatives = await db.execute(select(AgentInitiative).order_by(AgentInitiative.created_at.desc()))
    sweeps = await db.execute(select(SystemSweep).order_by(SystemSweep.created_at.desc()).limit(10))

    reflection_rows = list(reflections.scalars().all())
    initiative_rows = list(initiatives.scalars().all())
    sweep_rows = list(sweeps.scalars().all())

    # Pending reviews: initiatives waiting for Lobs decision
    pending_reviews = sum(1 for i in initiative_rows if i.status in ("proposed", "lobs_review"))

    # Recent approval rate (last 7 days)
    # Database times are timezone-naive UTC
    seven_days_ago = datetime.utcnow() - timedelta(days=7)
    recent_initiatives = [i for i in initiative_rows if i.created_at and i.created_at >= seven_days_ago]
    recent_approved = sum(1 for i in recent_initiatives if i.status == "approved")
    recent_total = len(recent_initiatives)
    
    recent_approval_rate = None
    if recent_total > 0:
        recent_approval_rate = {
            "approved": recent_approved,
            "total": recent_total,
            "days": 7,
        }

    # Last reflection cycle info
    last_reflection_info = None
    if reflection_rows:
        last_reflection = reflection_rows[0]
        # Count unique agents in this reflection cycle
        agent_count = 1  # Default to 1 if we don't have agent tracking
        # Count initiatives proposed from this reflection
        initiatives_proposed = sum(1 for i in initiative_rows if hasattr(i, 'reflection_id') and i.reflection_id == last_reflection.id)
        if initiatives_proposed == 0:
            # Fallback: count all initiatives from around the same time (within 1 hour)
            if last_reflection.created_at:
                time_window_start = last_reflection.created_at - timedelta(hours=1)
                time_window_end = last_reflection.created_at + timedelta(hours=1)
                initiatives_proposed = sum(1 for i in initiative_rows 
                                         if i.created_at and time_window_start <= i.created_at <= time_window_end)
        
        last_reflection_info = {
            "timestamp": last_reflection.created_at.isoformat() if last_reflection.created_at else None,
            "agentCount": agent_count,
            "initiativesProposed": initiatives_proposed,
        }

    # Last sweep info
    last_sweep_info = None
    if sweep_rows:
        last_sweep = sweep_rows[0]
        # Count decisions made in this sweep
        decisions_made = 0
        
        # Summary is a JSON dict with counts
        if last_sweep.summary and isinstance(last_sweep.summary, dict):
            # For initiative_sweep: count approved + deferred + rejected
            decisions_made = (
                last_sweep.summary.get("approved", 0) +
                last_sweep.summary.get("deferred", 0) +
                last_sweep.summary.get("rejected", 0)
            )
        
        last_sweep_info = {
            "timestamp": last_sweep.created_at.isoformat() if last_sweep.created_at else None,
            "decisionsMade": decisions_made,
        }

    return {
        "pendingReviews": pending_reviews,
        "recentApprovalRate": recent_approval_rate,
        "lastReflection": last_reflection_info,
        "lastSweep": last_sweep_info,
    }


@router.get("/intelligence/initiatives")
async def list_initiatives(
    status: str | None = None,
    limit: int = 200,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """List initiatives proposed from reflection cycles."""

    query = select(AgentInitiative).order_by(AgentInitiative.created_at.desc())
    if status:
        query = query.where(AgentInitiative.status == status)

    result = await db.execute(query.limit(max(1, min(1000, int(limit)))))
    rows = result.scalars().all()

    return {
        "count": len(rows),
        "items": [
            {
                "id": row.id,
                "proposed_by_agent": row.proposed_by_agent,
                "owner_agent": row.owner_agent,
                "selected_agent": row.selected_agent,
                "selected_project_id": row.selected_project_id,
                "task_id": row.task_id,
                "title": row.title,
                "description": row.description,
                "category": row.category,
                "risk_tier": row.risk_tier,
                "policy_lane": row.policy_lane,
                "policy_reason": row.policy_reason,
                "status": row.status,
                "rationale": row.rationale,
                "decision_summary": row.decision_summary,
                "learning_feedback": row.learning_feedback,
                "approved_by": row.approved_by,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
            for row in rows
        ],
    }


@router.post("/intelligence/initiatives/{initiative_id}/decide")
async def decide_initiative(
    initiative_id: str,
    payload: InitiativeDecisionRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Apply a Lobs decision to an initiative and convert approved ideas into tasks."""

    initiative = await db.get(AgentInitiative, initiative_id)
    if initiative is None:
        raise HTTPException(status_code=404, detail="Initiative not found")

    engine = InitiativeDecisionEngine(db)
    try:
        result = await engine.decide(
            initiative,
            decision=payload.decision,
            revised_title=payload.revised_title,
            revised_description=payload.revised_description,
            selected_agent=payload.selected_agent,
            selected_project_id=payload.selected_project_id,
            decision_summary=payload.decision_summary,
            learning_feedback=payload.learning_feedback,
            decided_by=payload.decided_by,
        )
        return result
    except (ValueError, PermissionError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/intelligence/initiatives/batch-decide")
async def batch_decide_initiatives(
    payload: BatchInitiativeDecisionRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Process multiple initiative decisions in a single batch.
    
    This is the RECOMMENDED way for Lobs to review initiatives:
    1. Fetch all pending: GET /intelligence/initiatives?status=proposed
    2. Review as a batch with full context (spot duplicates, prioritize)
    3. Submit all decisions together: POST /intelligence/initiatives/batch-decide
    
    The batch processor is forgiving:
    - Missing initiative IDs are reported as errors but don't block the batch
    - Each decision is processed independently
    - Full stats returned: approved/deferred/rejected counts
    
    Returns:
        {
            "total": <number of decisions in request>,
            "processed": <number successfully processed>,
            "approved": <number approved and converted to tasks>,
            "deferred": <number deferred for later>,
            "rejected": <number rejected>,
            "failed": <number that failed (not found or validation error)>,
            "results": [
                {
                    "initiative_id": "...",
                    "status": "approved|deferred|rejected",
                    "task_id": "..." (only if approved),
                    ...
                },
                ...
            ],
            "errors": [
                {"initiative_id": "...", "error": "..."},
                ...
            ]
        }
    """
    if not payload.decisions and not payload.new_tasks:
        raise HTTPException(status_code=400, detail="No decisions or new tasks provided")

    # Track stats
    total = len(payload.decisions)
    processed = 0
    approved = 0
    deferred = 0
    rejected = 0
    failed = 0
    
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    # Pre-fetch all initiatives in one query
    if payload.decisions:
        ids = [d.initiative_id for d in payload.decisions]
        result = await db.execute(
            select(AgentInitiative).where(AgentInitiative.id.in_(ids))
        )
        initiatives_by_id = {i.id: i for i in result.scalars().all()}
    else:
        initiatives_by_id = {}

    engine = InitiativeDecisionEngine(db)

    for d in payload.decisions:
        initiative_id = d.initiative_id
        
        # Handle missing initiatives gracefully
        if initiative_id not in initiatives_by_id:
            errors.append({
                "initiative_id": initiative_id,
                "error": "Initiative not found"
            })
            failed += 1
            continue
        
        initiative = initiatives_by_id[initiative_id]
        
        try:
            r = await engine.decide(
                initiative,
                decision=d.decision,
                revised_title=d.revised_title,
                revised_description=d.revised_description,
                selected_agent=d.selected_agent,
                selected_project_id=d.selected_project_id,
                decision_summary=d.decision_summary,
                learning_feedback=d.learning_feedback,
                decided_by="lobs",
            )
            results.append(r)
            processed += 1
            
            # Track decision type
            if d.decision == "approve":
                approved += 1
            elif d.decision == "defer":
                deferred += 1
            elif d.decision == "reject":
                rejected += 1
                
        except (ValueError, PermissionError) as e:
            errors.append({
                "initiative_id": initiative_id,
                "error": str(e)
            })
            failed += 1

    # --- Create Lobs-originated tasks ---
    from app.models import Task as TaskModel, Project as ProjectModel, ControlLoopEvent
    import uuid as _uuid
    from datetime import datetime as _dt, timezone as _tz

    created_tasks: list[dict[str, Any]] = []

    for new_task in payload.new_tasks:
        if new_task.project_id:
            proj = await db.get(ProjectModel, new_task.project_id)
            if not proj:
                errors.append({"error": f"Project not found: {new_task.project_id}", "task_title": new_task.title})
                failed += 1
                continue

        task_id = str(_uuid.uuid4())
        notes_parts = []
        if new_task.rationale:
            notes_parts.append(f"Created by Lobs during batch initiative review.\nRationale: {new_task.rationale}")
        if new_task.notes:
            notes_parts.append(new_task.notes)

        task = TaskModel(
            id=task_id,
            title=new_task.title,
            status=new_task.status,
            work_state=new_task.work_state,
            owner=new_task.owner,
            project_id=new_task.project_id,
            agent=new_task.agent,
            notes="\n\n".join(notes_parts) if notes_parts else None,
        )
        db.add(task)

        db.add(ControlLoopEvent(
            id=str(_uuid.uuid4()),
            event_type="TaskCreated",
            status="pending",
            payload={
                "task_id": task_id,
                "project_id": new_task.project_id,
                "title": new_task.title,
                "agent": new_task.agent,
                "source": "lobs_batch_review",
                "created_at": _dt.now(_tz.utc).isoformat(),
            },
        ))

        created_tasks.append({
            "task_id": task_id,
            "title": new_task.title,
            "project_id": new_task.project_id,
            "agent": new_task.agent,
        })

    await db.commit()

    return {
        "total": total,
        "processed": processed,
        "approved": approved,
        "deferred": deferred,
        "rejected": rejected,
        "failed": failed,
        "results": results,
        "errors": errors,
        "new_tasks": {
            "created": len(created_tasks),
            "tasks": created_tasks,
        },
    }


@router.get("/intelligence/reflections")
async def list_reflections(
    limit: int = 50,
    offset: int = 0,
    agent_type: str | None = None,
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """List reflection cycles with their associated initiatives.
    
    Returns reflections ordered by created_at desc, with pagination support.
    Each reflection includes parsed result data and linked initiatives with their
    current decision status and feedback.
    """
    
    # Build query
    query = select(AgentReflection).order_by(AgentReflection.created_at.desc())
    
    # Apply filters
    if agent_type:
        query = query.where(AgentReflection.agent_type == agent_type)
    if status:
        query = query.where(AgentReflection.status == status)
    
    # Apply pagination
    query = query.limit(max(1, min(1000, int(limit)))).offset(max(0, int(offset)))
    
    result = await db.execute(query)
    reflections = result.scalars().all()
    
    # Fetch linked initiatives for all reflections in one query
    reflection_ids = [r.id for r in reflections]
    initiatives_result = await db.execute(
        select(AgentInitiative).where(
            AgentInitiative.source_reflection_id.in_(reflection_ids)
        )
    )
    initiatives_by_reflection = {}
    for initiative in initiatives_result.scalars().all():
        if initiative.source_reflection_id not in initiatives_by_reflection:
            initiatives_by_reflection[initiative.source_reflection_id] = []
        initiatives_by_reflection[initiative.source_reflection_id].append(initiative)
    
    # Build response
    items = []
    for reflection in reflections:
        # Extract parsed result data from JSON fields
        inefficiencies = reflection.inefficiencies if isinstance(reflection.inefficiencies, list) else []
        missed_opportunities = reflection.missed_opportunities if isinstance(reflection.missed_opportunities, list) else []
        system_risks = reflection.system_risks if isinstance(reflection.system_risks, list) else []
        identity_adjustments = reflection.identity_adjustments if isinstance(reflection.identity_adjustments, list) else []
        
        # Extract proposed_initiatives from result JSON if present
        proposed_initiatives = []
        if isinstance(reflection.result, dict):
            proposed_initiatives = reflection.result.get("proposed_initiatives", [])
        
        # Get linked initiatives
        linked_initiatives = []
        for initiative in initiatives_by_reflection.get(reflection.id, []):
            linked_initiatives.append({
                "id": initiative.id,
                "title": initiative.title,
                "description": initiative.description,
                "category": initiative.category,
                "risk_tier": initiative.risk_tier,
                "status": initiative.status,
                "decision_summary": initiative.decision_summary,
                "learning_feedback": initiative.learning_feedback,
                "task_id": initiative.task_id,
                "selected_agent": initiative.selected_agent,
                "selected_project_id": initiative.selected_project_id,
                "created_at": initiative.created_at.isoformat() if initiative.created_at else None,
            })
        
        items.append({
            "id": reflection.id,
            "agent_type": reflection.agent_type,
            "reflection_type": reflection.reflection_type,
            "status": reflection.status,
            "window_start": reflection.window_start.isoformat() if reflection.window_start else None,
            "window_end": reflection.window_end.isoformat() if reflection.window_end else None,
            "created_at": reflection.created_at.isoformat() if reflection.created_at else None,
            "completed_at": reflection.completed_at.isoformat() if reflection.completed_at else None,
            "inefficiencies": inefficiencies,
            "missed_opportunities": missed_opportunities,
            "system_risks": system_risks,
            "identity_adjustments": identity_adjustments,
            "proposed_initiatives": proposed_initiatives,
            "linked_initiatives": linked_initiatives,
        })
    
    # Get total count for pagination
    count_query = select(AgentReflection)
    if agent_type:
        count_query = count_query.where(AgentReflection.agent_type == agent_type)
    if status:
        count_query = count_query.where(AgentReflection.status == status)
    
    from sqlalchemy import func as sql_func
    total_result = await db.execute(
        select(sql_func.count()).select_from(count_query.subquery())
    )
    total = total_result.scalar_one()
    
    return {
        "reflections": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/intelligence/budgets")
async def get_autonomy_budgets(
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get per-agent daily autonomy budgets for auto-approved initiatives."""

    row = await db.get(OrchestratorSetting, "autonomy_budget.daily")
    data = row.value if row and isinstance(row.value, dict) else {}

    budgets = dict(DEFAULT_DAILY_BUDGET)
    for key, value in data.items():
        try:
            budgets[str(key).lower()] = int(value)
        except (TypeError, ValueError):
            continue

    return {"daily": budgets}


@router.put("/intelligence/budgets")
async def update_autonomy_budgets(
    payload: AutonomyBudgetUpdate,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Update per-agent daily autonomy budgets."""

    normalized: dict[str, int] = {}
    for key, value in payload.daily.items():
        try:
            normalized[str(key).lower()] = max(0, int(value))
        except (TypeError, ValueError):
            continue

    row = await db.get(OrchestratorSetting, "autonomy_budget.daily")
    if row is None:
        row = OrchestratorSetting(key="autonomy_budget.daily", value=normalized)
        db.add(row)
    else:
        row.value = normalized

    await db.commit()
    return {"daily": normalized}
