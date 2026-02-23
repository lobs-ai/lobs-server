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
async def test_sweep_marks_lobs_review_without_worker_manager(db_session):
    """Without worker_manager, all proposals are marked as lobs_review for inbox review."""
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

    # No worker_manager → everything sent to lobs_review
    result = await SweepArbitrator(db_session, worker_manager=None).run_once()
    assert result["llm_review"] == 1

    row = await db_session.get(AgentInitiative, initiative_id)
    assert row is not None
    assert row.status == "lobs_review"


@pytest.mark.asyncio
async def test_sweep_creates_rafe_inbox_for_hard_gate_categories(db_session):
    """High-risk categories create both review inbox items AND Rafe escalation items."""
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
    # Should have 2 items: 1 review item + 1 Rafe escalation item
    assert len(inbox_rows) == 2
    
    # Check that one is a review item
    review_items = [item for item in inbox_rows if "[REVIEW]" in item.title]
    assert len(review_items) == 1
    
    # Check that one is a Rafe escalation item
    rafe_items = [item for item in inbox_rows if "[RAFE]" in item.title or "🚨" in item.title]
    assert len(rafe_items) == 1
    assert "architecture" in rafe_items[0].content.lower()


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


@pytest.mark.asyncio
async def test_sweep_creates_inbox_items_for_each_initiative(db_session):
    """Each initiative that passes quality/dedup gets an inbox item."""
    await _ensure_project(db_session)
    
    # Create three valid initiatives
    initiatives = []
    for i in range(3):
        initiative_id = str(uuid.uuid4())
        db_session.add(
            AgentInitiative(
                id=initiative_id,
                proposed_by_agent="programmer",
                owner_agent="programmer",
                title=f"Implement feature {i} for better code quality",
                description=f"This is a detailed description for feature {i} that improves code quality",
                category="feature",
                risk_tier="B",
                score=3,  # 3 days effort
                status="proposed",
                created_at=datetime.now(timezone.utc),
            )
        )
        initiatives.append(initiative_id)
    
    await db_session.commit()

    # Run sweep (without worker_manager, so initiatives go to lobs_review)
    result = await SweepArbitrator(db_session, worker_manager=None).run_once()
    assert result["proposed"] == 3

    # Check that 3 inbox items were created (one per initiative)
    inbox_rows = (await db_session.execute(select(InboxItem))).scalars().all()
    review_items = [item for item in inbox_rows if item.summary and item.summary.startswith("initiative_review:")]
    assert len(review_items) == 3

    # Verify inbox item content
    for item in review_items:
        assert "[REVIEW]" in item.title
        assert "Initiative ID:" in item.content
        assert "Proposed by: programmer" in item.content
        assert "Category:" in item.content
        assert "Risk tier:" in item.content
        assert "Estimated effort:" in item.content
        assert "batch-decide" in item.content


@pytest.mark.asyncio
async def test_sweep_inbox_items_contain_initiative_id(db_session):
    """Inbox items include initiative_id in summary for linking back to API."""
    await _ensure_project(db_session)
    
    initiative_id = str(uuid.uuid4())
    db_session.add(
        AgentInitiative(
            id=initiative_id,
            proposed_by_agent="researcher",
            owner_agent="researcher",
            title="Research new API pattern for better performance",
            description="Investigate modern API patterns that could improve system performance",
            category="research",
            risk_tier="C",
            score=5,
            status="proposed",
            created_at=datetime.now(timezone.utc),
        )
    )
    await db_session.commit()

    await SweepArbitrator(db_session, worker_manager=None).run_once()

    # Find the inbox item
    inbox_rows = (await db_session.execute(select(InboxItem))).scalars().all()
    review_item = next((item for item in inbox_rows if item.summary and item.summary.startswith("initiative_review:")), None)
    
    assert review_item is not None
    assert review_item.summary == f"initiative_review:{initiative_id}"
    assert initiative_id in review_item.content


@pytest.mark.asyncio
async def test_sweep_inbox_items_include_all_initiative_details(db_session):
    """Inbox items include all required initiative metadata."""
    await _ensure_project(db_session)
    
    initiative_id = str(uuid.uuid4())
    db_session.add(
        AgentInitiative(
            id=initiative_id,
            proposed_by_agent="architect",
            owner_agent="programmer",
            title="Refactor authentication system",
            description="Modernize auth system with better security practices",
            category="architecture_change",
            risk_tier="A",
            score=8,  # 8 days
            status="proposed",
            created_at=datetime.now(timezone.utc),
        )
    )
    await db_session.commit()

    await SweepArbitrator(db_session, worker_manager=None).run_once()

    # Find the inbox item
    inbox_rows = (await db_session.execute(select(InboxItem))).scalars().all()
    review_item = next((item for item in inbox_rows if item.summary and item.summary.startswith("initiative_review:")), None)
    
    assert review_item is not None
    assert "Refactor authentication system" in review_item.title
    assert "Proposed by: architect" in review_item.content
    assert "Category: architecture_change" in review_item.content
    assert "Suggested owner: programmer" in review_item.content
    assert "Risk tier: A" in review_item.content
    assert "Estimated effort: 8 day(s)" in review_item.content
    assert "Modernize auth system" in review_item.content
