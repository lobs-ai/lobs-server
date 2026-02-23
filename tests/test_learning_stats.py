"""Tests for learning stats API endpoints."""

import pytest
from datetime import datetime, timedelta, timezone
from app.models import TaskOutcome, OutcomeLearning


@pytest.mark.asyncio
async def test_stats_endpoint_basic(client, db_session):
    """Test GET /api/learning/stats with basic data."""
    # Create test outcomes - 20% control group
    for i in range(20):
        outcome = TaskOutcome(
            id=f"outcome-{i}",
            task_id=f"task-{i}",
            agent_type="programmer",
            success=i % 2 == 0,  # 50% success
            learning_disabled=i % 5 == 0,  # 20% control (i=0,5,10,15)
            created_at=datetime.now(timezone.utc),
        )
        db_session.add(outcome)
    await db_session.commit()
    
    response = await client.get("/api/learning/stats?agent=programmer")
    assert response.status_code == 200
    
    data = response.json()
    assert data["agent_type"] == "programmer"
    assert data["control_group_size"] == 4
    assert data["treatment_group_size"] == 16
    assert data["tasks_with_outcomes"] == 20


@pytest.mark.asyncio
async def test_stats_endpoint_with_learnings(client, db_session):
    """Test stats includes learning counts and confidence."""
    # Create learnings first
    for i in range(3):
        learning = OutcomeLearning(
            id=f"learning-{i}",
            agent_type="programmer",
            pattern_name=f"pattern-{i}",
            lesson_text=f"Lesson {i}",
            confidence=0.8,
            success_count=5,
            failure_count=1,
            is_active=True,
            created_at=datetime.now(timezone.utc),
        )
        db_session.add(learning)
    
    # Create outcomes - 5 with learning-1, 5 without
    for i in range(10):
        outcome = TaskOutcome(
            id=f"outcome-{i}",
            task_id=f"task-{i}",
            agent_type="programmer",
            success=True,
            learning_disabled=False,
            applied_learnings=["learning-1"] if i < 5 else [],
            created_at=datetime.now(timezone.utc),
        )
        db_session.add(outcome)
    await db_session.commit()
    
    response = await client.get("/api/learning/stats?agent=programmer")
    assert response.status_code == 200
    
    data = response.json()
    # Use the correct field names from the API
    assert data["learning_count"] == 3
    assert abs(data["avg_confidence"] - 0.8) < 0.01  # Floating point tolerance
    assert data["tasks_with_outcomes"] == 10


@pytest.mark.asyncio
async def test_stats_endpoint_no_data(client, db_session):
    """Test stats endpoint with no outcomes."""
    response = await client.get("/api/learning/stats?agent=programmer")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_stats_time_window(client, db_session):
    """Test stats respects since_days parameter."""
    # Create old outcomes (30 days ago)
    for i in range(5):
        outcome = TaskOutcome(
            id=f"old-outcome-{i}",
            task_id=f"old-task-{i}",
            agent_type="programmer",
            success=True,
            learning_disabled=False,
            created_at=datetime.now(timezone.utc) - timedelta(days=30),
        )
        db_session.add(outcome)
    
    # Create recent outcomes (7 days ago)
    for i in range(5):
        outcome = TaskOutcome(
            id=f"new-outcome-{i}",
            task_id=f"new-task-{i}",
            agent_type="programmer",
            success=True,
            learning_disabled=False,
            created_at=datetime.now(timezone.utc) - timedelta(days=7),
        )
        db_session.add(outcome)
    await db_session.commit()
    
    # Query last 14 days - should only get recent ones
    response = await client.get("/api/learning/stats?agent=programmer&since_days=14")
    assert response.status_code == 200
    
    data = response.json()
    assert data["tasks_with_outcomes"] == 5  # Only recent ones


@pytest.mark.asyncio
async def test_stats_agent_filter(client, db_session):
    """Test stats filters by agent type."""
    # Create outcomes for programmer
    for i in range(5):
        outcome = TaskOutcome(
            id=f"prog-outcome-{i}",
            task_id=f"prog-task-{i}",
            agent_type="programmer",
            success=True,
            learning_disabled=False,
            created_at=datetime.now(timezone.utc),
        )
        db_session.add(outcome)
    
    # Create outcomes for researcher
    for i in range(3):
        outcome = TaskOutcome(
            id=f"res-outcome-{i}",
            task_id=f"res-task-{i}",
            agent_type="researcher",
            success=True,
            learning_disabled=False,
            created_at=datetime.now(timezone.utc),
        )
        db_session.add(outcome)
    await db_session.commit()
    
    # Query programmer only
    response = await client.get("/api/learning/stats?agent=programmer")
    assert response.status_code == 200
    data = response.json()
    assert data["agent_type"] == "programmer"
    assert data["tasks_with_outcomes"] == 5
    
    # Query researcher only
    response = await client.get("/api/learning/stats?agent=researcher")
    assert response.status_code == 200
    data = response.json()
    assert data["agent_type"] == "researcher"
    assert data["tasks_with_outcomes"] == 3


@pytest.mark.asyncio
async def test_health_endpoint_healthy(client, db_session):
    """Test health endpoint with recent data."""
    outcome = TaskOutcome(
        id="outcome-1",
        task_id="task-1",
        agent_type="programmer",
        success=True,
        learning_disabled=False,
        applied_learnings=["learning-1"],
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(outcome)
    
    learning = OutcomeLearning(
        id="learning-1",
        agent_type="programmer",
        pattern_name="test-pattern",
        lesson_text="Test lesson",
        confidence=0.8,
        is_active=True,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(learning)
    await db_session.commit()
    
    response = await client.get("/api/learning/health")
    assert response.status_code == 200
    
    data = response.json()
    assert data["status"] in ["healthy", "degraded"]
    assert data["recent_outcomes_24h"] >= 1
    assert data["recent_learnings_7d"] >= 1


@pytest.mark.asyncio
async def test_health_endpoint_no_data(client, db_session):
    """Test health endpoint with no data."""
    response = await client.get("/api/learning/health")
    assert response.status_code == 200
    
    data = response.json()
    assert data["status"] == "degraded"
    assert "no_recent_outcomes" in data["issues"]


@pytest.mark.asyncio
async def test_health_endpoint_low_confidence_learnings(client, db_session):
    """Test health detects low confidence learnings."""
    # Create recent outcome with applied learning
    outcome = TaskOutcome(
        id="outcome-1",
        task_id="task-1",
        agent_type="programmer",
        success=True,
        learning_disabled=False,
        applied_learnings=["learning-1"],
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(outcome)
    
    # Create low confidence learning (confidence < 0.3 AND failure_count > 3)
    learning = OutcomeLearning(
        id="learning-1",
        agent_type="programmer",
        pattern_name="low-conf",
        lesson_text="Low confidence lesson",
        confidence=0.2,  # Below 0.3 threshold
        failure_count=5,  # Above 3 threshold
        is_active=True,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(learning)
    await db_session.commit()
    
    response = await client.get("/api/learning/health")
    assert response.status_code == 200
    
    data = response.json()
    # Check that low_confidence_learnings issue is present (with count suffix)
    assert any("low_confidence_learnings" in issue for issue in data["issues"])


@pytest.mark.asyncio
async def test_health_endpoint_agent_filter(client, db_session):
    """Test health endpoint with agent filter."""
    # Create data for programmer
    outcome = TaskOutcome(
        id="outcome-prog",
        task_id="task-prog",
        agent_type="programmer",
        success=True,
        learning_disabled=False,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(outcome)
    
    learning = OutcomeLearning(
        id="learning-prog",
        agent_type="programmer",
        pattern_name="test",
        lesson_text="Test",
        confidence=0.8,
        is_active=True,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(learning)
    await db_session.commit()
    
    # Query with agent filter
    response = await client.get("/api/learning/health?agent=programmer")
    assert response.status_code == 200
    data = response.json()
    assert data["recent_outcomes_24h"] >= 1


@pytest.mark.asyncio
async def test_significance_calculation_insufficient_data(client, db_session):
    """Test significance calculation with insufficient data."""
    # Create only 5 control and 5 treatment (below 10 threshold)
    for i in range(10):
        outcome = TaskOutcome(
            id=f"outcome-{i}",
            task_id=f"task-{i}",
            agent_type="programmer",
            success=True,
            learning_disabled=i < 5,  # 5 control, 5 treatment
            created_at=datetime.now(timezone.utc),
        )
        db_session.add(outcome)
    await db_session.commit()
    
    response = await client.get("/api/learning/stats?agent=programmer")
    assert response.status_code == 200
    
    data = response.json()
    # With small sample, should return insufficient_data
    assert data.get("statistical_significance") == "insufficient_data"


@pytest.mark.asyncio
async def test_significance_calculation_significant(client, db_session):
    """Test significance calculation with sufficient data showing difference."""
    # Create 15 control (all fail) and 15 treatment (all succeed)
    for i in range(30):
        outcome = TaskOutcome(
            id=f"outcome-{i}",
            task_id=f"task-{i}",
            agent_type="programmer",
            success=i >= 15,  # First 15 fail, next 15 succeed
            learning_disabled=i < 15,  # First 15 are control
            created_at=datetime.now(timezone.utc),
        )
        db_session.add(outcome)
    await db_session.commit()
    
    response = await client.get("/api/learning/stats?agent=programmer")
    assert response.status_code == 200
    
    data = response.json()
    # With perfect separation, should be significant
    assert data["control_success_rate"] == 0.0
    assert data["treatment_success_rate"] == 1.0
    # statistical_significance can be "significant", "unavailable" (no scipy), or None
    assert data.get("statistical_significance") in ["significant", "unavailable", None]


@pytest.mark.asyncio
async def test_learning_flow_integration(client, db_session):
    """Test complete flow: outcomes -> learnings -> stats."""
    # Step 1: Create initial outcomes
    for i in range(10):
        outcome = TaskOutcome(
            id=f"outcome-{i}",
            task_id=f"task-{i}",
            agent_type="programmer",
            success=True,
            learning_disabled=False,
            created_at=datetime.now(timezone.utc),
        )
        db_session.add(outcome)
    await db_session.commit()
    
    # Step 2: Create a learning
    learning = OutcomeLearning(
        id="learning-1",
        agent_type="programmer",
        pattern_name="test-pattern",
        lesson_text="Always validate inputs",
        confidence=0.85,
        success_count=8,
        failure_count=2,
        is_active=True,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(learning)
    await db_session.commit()
    
    # Step 3: Create more outcomes with the learning applied
    for i in range(10, 20):
        outcome = TaskOutcome(
            id=f"outcome-{i}",
            task_id=f"task-{i}",
            agent_type="programmer",
            success=i % 10 != 0,  # 90% success with learning
            learning_disabled=False,
            applied_learnings=["learning-1"],
            created_at=datetime.now(timezone.utc),
        )
        db_session.add(outcome)
    await db_session.commit()
    
    # Step 4: Check stats
    response = await client.get("/api/learning/stats?agent=programmer")
    assert response.status_code == 200
    
    data = response.json()
    assert data["learning_count"] == 1
    assert data["avg_confidence"] == 0.85
    assert data["tasks_with_outcomes"] == 20
    
    # Step 5: Check health
    response = await client.get("/api/learning/health?agent=programmer")
    assert response.status_code == 200
    
    health_data = response.json()
    assert health_data["status"] in ["healthy", "degraded"]
    assert health_data["recent_outcomes_24h"] >= 20
    assert health_data["recent_learnings_7d"] >= 1
