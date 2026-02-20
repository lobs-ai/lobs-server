"""Tests for the initiative sweep arbitrator.

The new sweep design:
1. Server-side prefilter: quality gate + dedup
2. All remaining proposals → LLM review (async, via worker_manager)
3. Without worker_manager, everything is deferred (safe fallback)
"""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models import AgentInitiative, InboxItem, Project, SystemSweep
from app.orchestrator.sweep_arbitrator import SweepArbitrator


async def _ensure_project(db_session):
    project = await db_session.get(Project, "lobs-server")
    if project is None:
        db_session.add(Project(id="lobs-server", title="Lobs Server", type="software"))
        await db_session.commit()


@pytest.mark.asyncio
async def test_sweep_rejects_low_quality_initiatives(db_session):
    """Quality gate: too-short titles/descriptions are rejected."""
    await _ensure_project(db_session)
    initiative_id = str(uuid.uuid4())
    db_session.add(
        AgentInitiative(
            id=initiative_id,
            proposed_by_agent="writer",
            title="Hi",  # too short
            description="x",  # too short
            category="docs_sync",
            status="proposed",
            created_at=datetime.now(timezone.utc),
        )
    )
    await db_session.commit()

    result = await SweepArbitrator(db_session).run_once()
    assert result["rejected"] == 1

    row = await db_session.get(AgentInitiative, initiative_id)
    assert row is not None
    assert row.status == "rejected"
    assert "quality gate" in (row.rationale or "").lower()


@pytest.mark.asyncio
async def test_sweep_deduplicates_initiatives(db_session):
    """Duplicate proposals (same title+desc) are rejected, keeping newest."""
    await _ensure_project(db_session)
    older_id = str(uuid.uuid4())
    newer_id = str(uuid.uuid4())
    db_session.add_all([
        AgentInitiative(
            id=older_id,
            proposed_by_agent="writer",
            title="Sync API docs for orchestrator endpoints",
            description="Bring docs in sync with latest endpoints",
            category="docs_sync",
            status="proposed",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
        AgentInitiative(
            id=newer_id,
            proposed_by_agent="writer",
            title="Sync API docs for orchestrator endpoints",
            description="Bring docs in sync with latest endpoints",
            category="docs_sync",
            status="proposed",
            created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        ),
    ])
    await db_session.commit()

    result = await SweepArbitrator(db_session).run_once()
    assert result["rejected"] >= 1

    older = await db_session.get(AgentInitiative, older_id)
    assert older is not None
    assert older.status == "rejected"
    assert "duplicate" in (older.rationale or "").lower()


@pytest.mark.asyncio
async def test_sweep_defers_without_worker_manager(db_session):
    """Without worker_manager, all proposals are deferred (safe fallback)."""
    await _ensure_project(db_session)
    initiative_id = str(uuid.uuid4())
    db_session.add(
        AgentInitiative(
            id=initiative_id,
            proposed_by_agent="writer",
            owner_agent="writer",
            title="Sync API docs for orchestrator endpoints",
            description="Bring docs in sync with latest endpoints",
            category="docs_sync",
            status="proposed",
            created_at=datetime.now(timezone.utc),
        )
    )
    await db_session.commit()

    # No worker_manager → everything deferred
    result = await SweepArbitrator(db_session, worker_manager=None).run_once()
    assert result["deferred"] == 1

    row = await db_session.get(AgentInitiative, initiative_id)
    assert row is not None
    assert row.status == "deferred"


@pytest.mark.asyncio
async def test_sweep_creates_inbox_for_hard_gate_categories(db_session):
    """High-risk categories create Rafe inbox items."""
    await _ensure_project(db_session)
    initiative_id = str(uuid.uuid4())
    db_session.add(
        AgentInitiative(
            id=initiative_id,
            proposed_by_agent="programmer",
            title="Cross-project architecture rewrite proposal",
            description="Perform a broad architecture change touching multiple services",
            category="architecture_change",
            status="proposed",
            created_at=datetime.now(timezone.utc),
        )
    )
    await db_session.commit()

    await SweepArbitrator(db_session, worker_manager=None).run_once()

    inbox_rows = (await db_session.execute(select(InboxItem))).scalars().all()
    assert len(inbox_rows) == 1
    assert "high" in inbox_rows[0].title.lower()
    assert "architecture" in inbox_rows[0].content.lower()


@pytest.mark.asyncio
async def test_sweep_creates_sweep_record(db_session):
    """Sweep always creates a SystemSweep record."""
    await _ensure_project(db_session)
    db_session.add(
        AgentInitiative(
            id=str(uuid.uuid4()),
            proposed_by_agent="writer",
            title="Update stale endpoint documentation now",
            description="Docs are out of date, refresh them based on current code",
            category="docs_sync",
            status="proposed",
            created_at=datetime.now(timezone.utc),
        )
    )
    await db_session.commit()

    await SweepArbitrator(db_session).run_once()

    sweeps = (await db_session.execute(select(SystemSweep))).scalars().all()
    assert len(sweeps) == 1
    assert sweeps[0].sweep_type == "initiative_sweep"
    assert sweeps[0].summary["proposed"] == 1


@pytest.mark.asyncio
async def test_sweep_noop_when_no_proposals(db_session):
    """No proposals → clean noop."""
    result = await SweepArbitrator(db_session).run_once()
    assert result["proposed"] == 0
    assert result["approved"] == 0
