"""Workflow engine API endpoints."""

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import (
    WorkflowDefinition,
    WorkflowRun,
    WorkflowEvent,
    WorkflowSubscription,
)

router = APIRouter(prefix="/workflows", tags=["workflows"])


# ── Schemas ──────────────────────────────────────────────────────────


class WorkflowCreate(BaseModel):
    name: str
    description: Optional[str] = None
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]] = Field(default_factory=list)
    trigger: Optional[dict[str, Any]] = None
    metadata: Optional[dict[str, Any]] = None
    is_active: bool = True


class WorkflowUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    nodes: Optional[list[dict[str, Any]]] = None
    edges: Optional[list[dict[str, Any]]] = None
    trigger: Optional[dict[str, Any]] = None
    metadata: Optional[dict[str, Any]] = None
    is_active: Optional[bool] = None


class RunTrigger(BaseModel):
    trigger_payload: Optional[dict[str, Any]] = None


class EventCreate(BaseModel):
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    source: str = "manual"


class SubscriptionCreate(BaseModel):
    workflow_id: str
    event_pattern: str
    filter_conditions: Optional[dict[str, Any]] = None
    is_active: bool = True


# ── Workflow Definitions ─────────────────────────────────────────────


@router.get("")
async def list_workflows(
    active_only: bool = Query(True),
    db: AsyncSession = Depends(get_db),
):
    query = select(WorkflowDefinition).order_by(WorkflowDefinition.name)
    if active_only:
        query = query.where(WorkflowDefinition.is_active == True)
    result = await db.execute(query)
    workflows = result.scalars().all()
    return [_wf_to_dict(wf) for wf in workflows]


@router.post("", status_code=201)
async def create_workflow(body: WorkflowCreate, db: AsyncSession = Depends(get_db)):
    # Validate unique name
    existing = await db.execute(
        select(WorkflowDefinition).where(WorkflowDefinition.name == body.name)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(409, f"Workflow with name '{body.name}' already exists")

    wf = WorkflowDefinition(
        id=str(uuid.uuid4()),
        name=body.name,
        description=body.description,
        version=1,
        nodes=body.nodes,
        edges=body.edges,
        trigger=body.trigger,
        metadata_=body.metadata,
        is_active=body.is_active,
    )
    db.add(wf)
    await db.commit()
    await db.refresh(wf)
    return _wf_to_dict(wf)


@router.get("/{workflow_id}")
async def get_workflow(workflow_id: str, db: AsyncSession = Depends(get_db)):
    wf = await db.get(WorkflowDefinition, workflow_id)
    if not wf:
        raise HTTPException(404, "Workflow not found")
    return _wf_to_dict(wf)


@router.put("/{workflow_id}")
async def update_workflow(workflow_id: str, body: WorkflowUpdate, db: AsyncSession = Depends(get_db)):
    wf = await db.get(WorkflowDefinition, workflow_id)
    if not wf:
        raise HTTPException(404, "Workflow not found")

    if body.name is not None:
        wf.name = body.name
    if body.description is not None:
        wf.description = body.description
    if body.nodes is not None:
        wf.nodes = body.nodes
        wf.version += 1  # Bump version on node changes
    if body.edges is not None:
        wf.edges = body.edges
    if body.trigger is not None:
        wf.trigger = body.trigger
    if body.metadata is not None:
        wf.metadata_ = body.metadata
    if body.is_active is not None:
        wf.is_active = body.is_active
    wf.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(wf)
    return _wf_to_dict(wf)


@router.delete("/{workflow_id}")
async def delete_workflow(workflow_id: str, db: AsyncSession = Depends(get_db)):
    wf = await db.get(WorkflowDefinition, workflow_id)
    if not wf:
        raise HTTPException(404, "Workflow not found")
    await db.delete(wf)
    await db.commit()
    return {"deleted": True}


# ── Workflow Runs ────────────────────────────────────────────────────


@router.get("/{workflow_id}/runs")
async def list_workflow_runs(
    workflow_id: str,
    limit: int = Query(20, ge=1, le=100),
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    query = select(WorkflowRun).where(WorkflowRun.workflow_id == workflow_id).order_by(desc(WorkflowRun.created_at)).limit(limit)
    if status:
        query = query.where(WorkflowRun.status == status)
    result = await db.execute(query)
    runs = result.scalars().all()
    return [_run_to_dict(r) for r in runs]


@router.post("/{workflow_id}/runs", status_code=201)
async def trigger_workflow(workflow_id: str, body: RunTrigger, db: AsyncSession = Depends(get_db)):
    wf = await db.get(WorkflowDefinition, workflow_id)
    if not wf:
        raise HTTPException(404, "Workflow not found")

    from app.orchestrator.workflow_executor import WorkflowExecutor
    executor = WorkflowExecutor(db)
    run = await executor.start_run(wf, trigger_type="manual", trigger_payload=body.trigger_payload)
    return _run_to_dict(run)


# ── Run Details ──────────────────────────────────────────────────────


runs_router = APIRouter(prefix="/workflow-runs", tags=["workflows"])


@runs_router.get("/{run_id}")
async def get_run(run_id: str, db: AsyncSession = Depends(get_db)):
    run = await db.get(WorkflowRun, run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return _run_to_dict(run)


@runs_router.get("/{run_id}/trace")
async def get_run_trace(run_id: str, db: AsyncSession = Depends(get_db)):
    """Get execution trace — timing and status per node."""
    run = await db.get(WorkflowRun, run_id)
    if not run:
        raise HTTPException(404, "Run not found")

    wf = await db.get(WorkflowDefinition, run.workflow_id)
    node_names = {n["id"]: n.get("type", "unknown") for n in (wf.nodes if wf else [])}

    trace = []
    for node_id, ns in (run.node_states or {}).items():
        trace.append({
            "id": node_id,
            "type": node_names.get(node_id, "unknown"),
            "status": ns.get("status", "unknown"),
            "attempts": ns.get("attempts", 0),
            "error": ns.get("error"),
            "started_at": ns.get("started_at"),
            "finished_at": ns.get("finished_at"),
        })

    return {
        "run_id": run.id,
        "workflow": wf.name if wf else run.workflow_id,
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "nodes": trace,
    }


@runs_router.post("/{run_id}/cancel")
async def cancel_run(run_id: str, db: AsyncSession = Depends(get_db)):
    run = await db.get(WorkflowRun, run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    if run.status not in ("pending", "running"):
        raise HTTPException(400, f"Cannot cancel run in status '{run.status}'")

    run.status = "cancelled"
    run.finished_at = datetime.now(timezone.utc)
    run.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return _run_to_dict(run)


# ── Events ───────────────────────────────────────────────────────────


events_router = APIRouter(prefix="/workflow-events", tags=["workflows"])


@events_router.get("")
async def list_events(
    limit: int = Query(50, ge=1, le=200),
    unprocessed_only: bool = False,
    db: AsyncSession = Depends(get_db),
):
    query = select(WorkflowEvent).order_by(desc(WorkflowEvent.created_at)).limit(limit)
    if unprocessed_only:
        query = query.where(WorkflowEvent.processed == False)
    result = await db.execute(query)
    events = result.scalars().all()
    return [_event_to_dict(e) for e in events]


@events_router.post("", status_code=201)
async def emit_event(body: EventCreate, db: AsyncSession = Depends(get_db)):
    event = WorkflowEvent(
        id=str(uuid.uuid4()),
        event_type=body.event_type,
        payload=body.payload,
        source=body.source,
    )
    db.add(event)
    await db.commit()
    return _event_to_dict(event)


# ── Subscriptions ────────────────────────────────────────────────────


subs_router = APIRouter(prefix="/workflow-subscriptions", tags=["workflows"])


@subs_router.get("")
async def list_subscriptions(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(WorkflowSubscription).order_by(WorkflowSubscription.created_at))
    subs = result.scalars().all()
    return [_sub_to_dict(s) for s in subs]


@subs_router.post("", status_code=201)
async def create_subscription(body: SubscriptionCreate, db: AsyncSession = Depends(get_db)):
    wf = await db.get(WorkflowDefinition, body.workflow_id)
    if not wf:
        raise HTTPException(404, "Workflow not found")

    sub = WorkflowSubscription(
        id=str(uuid.uuid4()),
        workflow_id=body.workflow_id,
        event_pattern=body.event_pattern,
        filter_conditions=body.filter_conditions,
        is_active=body.is_active,
    )
    db.add(sub)
    await db.commit()
    return _sub_to_dict(sub)


# ── Helpers ──────────────────────────────────────────────────────────


def _wf_to_dict(wf: WorkflowDefinition) -> dict:
    return {
        "id": wf.id,
        "name": wf.name,
        "description": wf.description,
        "version": wf.version,
        "nodes": wf.nodes,
        "edges": wf.edges,
        "trigger": wf.trigger,
        "metadata": wf.metadata_,
        "is_active": wf.is_active,
        "node_count": len(wf.nodes) if wf.nodes else 0,
        "created_at": wf.created_at.isoformat() if wf.created_at else None,
        "updated_at": wf.updated_at.isoformat() if wf.updated_at else None,
    }


def _run_to_dict(r: WorkflowRun) -> dict:
    return {
        "id": r.id,
        "workflow_id": r.workflow_id,
        "workflow_version": r.workflow_version,
        "task_id": r.task_id,
        "trigger_type": r.trigger_type,
        "status": r.status,
        "current_node": r.current_node,
        "node_states": r.node_states,
        "error": r.error,
        "session_key": r.session_key,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def _event_to_dict(e: WorkflowEvent) -> dict:
    return {
        "id": e.id,
        "event_type": e.event_type,
        "payload": e.payload,
        "source": e.source,
        "processed": e.processed,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }


def _sub_to_dict(s: WorkflowSubscription) -> dict:
    return {
        "id": s.id,
        "workflow_id": s.workflow_id,
        "event_pattern": s.event_pattern,
        "filter_conditions": s.filter_conditions,
        "is_active": s.is_active,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }
