"""Orchestrator reflection endpoints - intelligence, initiatives, and budgets."""

from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Any

from app.database import get_db
from app.models import OrchestratorSetting, AgentInitiative, AgentReflection, SystemSweep, InitiativeMessage
from app.orchestrator.sweep_arbitrator import DEFAULT_DAILY_BUDGET
from app.orchestrator.initiative_decisions import InitiativeDecisionEngine
from app.orchestrator import OrchestratorEngine

router = APIRouter(prefix="/orchestrator", tags=["orchestrator"])


def _initiative_payload(row: AgentInitiative) -> dict[str, Any]:
    return {
        "id": row.id,
        "proposed_by_agent": row.proposed_by_agent,
        "owner_agent": row.owner_agent,
        "selected_agent": row.selected_agent,
        "selected_project_id": row.selected_project_id,
        "task_id": row.task_id,
        # Forward-compatible with clients that support multiple task links.
        "task_ids": [row.task_id] if row.task_id else [],
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
        "items": [_initiative_payload(row) for row in rows],
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
        await engine.decide(
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
        # Return full initiative payload for Mission Control decoder compatibility.
        await db.refresh(initiative)
        return _initiative_payload(initiative)
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


class InitiativeMessageRequest(BaseModel):
    text: str
    author: str = "rafe"


@router.get("/intelligence/initiatives/{initiative_id}/thread")
async def get_initiative_thread(
    initiative_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get discussion thread for an initiative."""
    initiative = await db.get(AgentInitiative, initiative_id)
    if initiative is None:
        raise HTTPException(status_code=404, detail="Initiative not found")

    result = await db.execute(
        select(InitiativeMessage)
        .where(InitiativeMessage.initiative_id == initiative_id)
        .order_by(InitiativeMessage.created_at.asc())
    )
    messages = result.scalars().all()

    return {
        "messages": [
            {
                "id": m.id,
                "author": m.author,
                "text": m.text,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in messages
        ]
    }


@router.post("/intelligence/initiatives/{initiative_id}/thread")
async def post_initiative_message(
    initiative_id: str,
    payload: InitiativeMessageRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Post a message to an initiative's discussion thread."""
    import uuid

    initiative = await db.get(AgentInitiative, initiative_id)
    if initiative is None:
        raise HTTPException(status_code=404, detail="Initiative not found")

    msg = InitiativeMessage(
        id=str(uuid.uuid4()),
        initiative_id=initiative_id,
        author=payload.author,
        text=payload.text,
    )
    db.add(msg)
    await db.commit()
    await db.refresh(msg)

    return {
        "id": msg.id,
        "author": msg.author,
        "text": msg.text,
        "created_at": msg.created_at.isoformat() if msg.created_at else None,
    }


@router.get("/intelligence/reflections")
async def list_reflections(
    limit: int = 50,
    offset: int = 0,
    agent_type: str | None = None,
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """List reflection cycles grouped by batch (window_start).
    
    Returns batch-level reflections with aggregated data from all agents in that batch.
    Mission Control expects this format for the Intelligence dashboard.
    """
    from sqlalchemy import func as sql_func
    from collections import defaultdict
    
    # Fetch all reflections
    query = select(AgentReflection).order_by(AgentReflection.window_start.desc(), AgentReflection.created_at.desc())
    
    if agent_type:
        query = query.where(AgentReflection.agent_type == agent_type)
    if status:
        query = query.where(AgentReflection.status == status)
    
    result = await db.execute(query)
    all_reflections = result.scalars().all()
    
    # Group by window_start (batch ID)
    batches = defaultdict(list)
    for refl in all_reflections:
        batch_key = refl.window_start.isoformat() if refl.window_start else "unknown"
        batches[batch_key].append(refl)
    
    # Sort batch keys by window_start descending
    sorted_batches = sorted(batches.items(), key=lambda x: x[0], reverse=True)
    
    # Apply pagination on batches
    paginated_batches = sorted_batches[offset:offset + limit]
    
    # Build batch-level response
    items = []
    for batch_key, batch_reflections in paginated_batches:
        # Use first reflection for base fields
        first_refl = batch_reflections[0]
        
        # Aggregate agents
        agents = [r.agent_type for r in batch_reflections if r.agent_type]
        
        # Merge arrays from all reflections in the batch
        all_inefficiencies = []
        all_missed_opportunities = []
        all_system_risks = []
        all_identity_adjustments = []
        all_proposed_initiatives = []
        
        for refl in batch_reflections:
            if isinstance(refl.inefficiencies, list):
                all_inefficiencies.extend(refl.inefficiencies)
            if isinstance(refl.missed_opportunities, list):
                all_missed_opportunities.extend(refl.missed_opportunities)
            if isinstance(refl.system_risks, list):
                all_system_risks.extend(refl.system_risks)
            if isinstance(refl.identity_adjustments, list):
                all_identity_adjustments.extend(refl.identity_adjustments)
            if isinstance(refl.result, dict):
                all_proposed_initiatives.extend(refl.result.get("proposed_initiatives", []))
        
        # Check if all reflections in batch are completed
        all_completed = all(r.status == "completed" for r in batch_reflections)
        batch_status = "completed" if all_completed else "pending"
        
        # Latest completion time
        completed_times = [r.completed_at for r in batch_reflections if r.completed_at]
        latest_completed = max(completed_times) if completed_times else None
        
        items.append({
            "id": first_refl.id,
            "batch_id": batch_key,
            "agents": agents,
            "status": batch_status,
            "started_at": first_refl.window_start.isoformat() if first_refl.window_start else None,
            "completed_at": latest_completed.isoformat() if latest_completed else None,
            "inefficiencies": all_inefficiencies,
            "missed_opportunities": all_missed_opportunities,
            "system_risks": all_system_risks,
            "identity_adjustments": all_identity_adjustments,
            "proposed_initiatives": all_proposed_initiatives,
            "error_message": None,
        })
    
    return {
        "reflections": items,
        "total": len(batches),
    }


@router.get("/intelligence/sweeps")
async def list_sweeps(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """List system sweeps with decision counts."""
    
    query = select(SystemSweep).order_by(SystemSweep.created_at.desc()).limit(max(1, min(1000, int(limit))))
    result = await db.execute(query)
    sweeps = result.scalars().all()
    
    items = []
    for sweep in sweeps:
        # Extract counts from summary JSON
        summary = sweep.summary if isinstance(sweep.summary, dict) else {}
        
        # Handle different summary formats
        # For initiative_sweep: "proposed", "approved", "rejected", "deferred"
        # For other sweeps: may have different fields
        total_proposed = summary.get("total_proposed") or summary.get("proposed", 0)
        approved_count = summary.get("approved", 0)
        rejected_count = summary.get("rejected", 0)
        deferred_count = summary.get("deferred", 0)
        
        items.append({
            "id": sweep.id,
            "sweep_type": sweep.sweep_type,
            "status": sweep.status,
            "summary": summary,
            "total_proposed": total_proposed,
            "approved_count": approved_count,
            "rejected_count": rejected_count,
            "deferred_count": deferred_count,
            "created_at": sweep.created_at.isoformat() if sweep.created_at else None,
            "completed_at": sweep.completed_at.isoformat() if sweep.completed_at else None,
        })
    
    return {"sweeps": items}


@router.get("/intelligence/sweeps/{sweep_id}")
async def get_sweep_detail(
    sweep_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get sweep details with associated decisions."""
    from app.models import InitiativeDecisionRecord
    
    sweep = await db.get(SystemSweep, sweep_id)
    if sweep is None:
        raise HTTPException(status_code=404, detail="Sweep not found")
    
    # Fetch initiative decisions made during this sweep
    decisions_result = await db.execute(
        select(InitiativeDecisionRecord)
        .where(InitiativeDecisionRecord.sweep_id == sweep_id)
        .order_by(InitiativeDecisionRecord.created_at.asc())
    )
    decisions = decisions_result.scalars().all()
    
    # Fetch the full initiatives for context
    initiative_ids = [d.initiative_id for d in decisions]
    if initiative_ids:
        initiatives_result = await db.execute(
            select(AgentInitiative).where(AgentInitiative.id.in_(initiative_ids))
        )
        initiatives_by_id = {i.id: i for i in initiatives_result.scalars().all()}
    else:
        initiatives_by_id = {}
    
    # Build decisions list with initiative context
    decision_items = []
    for decision in decisions:
        initiative = initiatives_by_id.get(decision.initiative_id)
        decision_items.append({
            "id": decision.id,
            "initiative_id": decision.initiative_id,
            "initiative_title": initiative.title if initiative else None,
            "decision": decision.decision,
            "decided_by": decision.decided_by,
            "decision_summary": decision.decision_summary,
            "task_id": decision.task_id,
            "created_at": decision.created_at.isoformat() if decision.created_at else None,
        })
    
    # Extract summary
    summary = sweep.summary if isinstance(sweep.summary, dict) else {}
    
    # Handle different summary formats
    total_proposed = summary.get("total_proposed") or summary.get("proposed", 0)
    
    return {
        "sweep": {
            "id": sweep.id,
            "sweep_type": sweep.sweep_type,
            "status": sweep.status,
            "summary": summary,
            "total_proposed": total_proposed,
            "approved_count": summary.get("approved", 0),
            "rejected_count": summary.get("rejected", 0),
            "deferred_count": summary.get("deferred", 0),
            "created_at": sweep.created_at.isoformat() if sweep.created_at else None,
            "completed_at": sweep.completed_at.isoformat() if sweep.completed_at else None,
        },
        "decisions": decision_items,
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
