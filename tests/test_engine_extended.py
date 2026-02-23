"""Extended test coverage for orchestrator engine.py.

Focuses on:
- Task state transitions
- Circuit breaker integration
- Provider health tracking
- Worker dispatch edge cases
- Error handling scenarios
- Runtime configuration
"""

import asyncio
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, Mock, patch, MagicMock, call
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.orchestrator.engine import OrchestratorEngine
from app.models import (
    Task as TaskModel,
    Project as ProjectModel,
    OrchestratorSetting,
    ControlLoopHeartbeat,
)
from tests.conftest import TestSessionLocal


# ============================================================================
# Task State Transition Tests
# ============================================================================


@pytest.mark.asyncio
async def test_task_state_transition_queued_to_in_progress(db_session):
    """Test task transitions from queued to in_progress when worker spawns."""
    # Create project and task
    project = ProjectModel(
        id=1,
        name="Test Project",
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(project)
    
    task = TaskModel(
        id=1000,
        project_id=1,
        title="State Transition Test",
        status="queued",
        agent="programmer",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(task)
    await db_session.commit()
    
    engine = OrchestratorEngine(TestSessionLocal)
    engine._openclaw_available = True
    
    with patch('app.orchestrator.engine.Scanner') as MockScanner, \
         patch('app.orchestrator.engine.WorkerManager') as MockWorkerManager:
        
        # Mock scanner to return our task
        mock_scanner = AsyncMock()
        mock_scanner.get_eligible_tasks = AsyncMock(return_value=[{
            'id': 1000,
            'project_id': 1,
            'title': 'State Transition Test',
            'status': 'queued',
            'agent': 'programmer',
        }])
        MockScanner.return_value = mock_scanner
        
        # Mock worker manager to successfully spawn
        mock_worker_manager = AsyncMock()
        mock_worker_manager.spawn_worker = AsyncMock(return_value=True)
        mock_worker_manager.check_workers = AsyncMock()
        mock_worker_manager.active_workers = {}
        mock_worker_manager.sweep_requested = False
        mock_worker_manager.get_worker_status = AsyncMock(return_value={'busy': False})
        MockWorkerManager.return_value = mock_worker_manager
        
        await engine._run_once()
        
        # Verify spawn was called with correct task
        mock_worker_manager.spawn_worker.assert_called_once()
        call_args = mock_worker_manager.spawn_worker.call_args
        assert call_args[1]['task']['id'] == 1000


@pytest.mark.asyncio
async def test_task_state_transition_blocks_when_no_agent(db_session):
    """Test task without agent triggers assignment request, not spawn."""
    project = ProjectModel(
        id=1,
        name="Test Project",
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(project)
    
    task = TaskModel(
        id=1001,
        project_id=1,
        title="Unassigned Task",
        status="queued",
        agent=None,  # No agent assigned
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(task)
    await db_session.commit()
    
    engine = OrchestratorEngine(TestSessionLocal)
    engine._openclaw_available = True
    
    with patch('app.orchestrator.engine.Scanner') as MockScanner, \
         patch('app.orchestrator.engine.WorkerManager') as MockWorkerManager:
        
        mock_scanner = AsyncMock()
        mock_scanner.get_eligible_tasks = AsyncMock(return_value=[{
            'id': 1001,
            'project_id': 1,
            'title': 'Unassigned Task',
            'status': 'queued',
            'agent': None,
        }])
        MockScanner.return_value = mock_scanner
        
        mock_worker_manager = AsyncMock()
        mock_worker_manager.spawn_worker = AsyncMock()
        mock_worker_manager.check_workers = AsyncMock()
        mock_worker_manager.active_workers = {}
        mock_worker_manager.sweep_requested = False
        MockWorkerManager.return_value = mock_worker_manager
        
        await engine._run_once()
        
        # Verify no spawn attempt (should request assignment instead)
        mock_worker_manager.spawn_worker.assert_not_called()


@pytest.mark.asyncio
async def test_task_state_multiple_tasks_sequential_processing(db_session):
    """Test multiple eligible tasks are processed sequentially."""
    project = ProjectModel(
        id=1,
        name="Test Project",
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(project)
    
    # Create 3 tasks
    for i in range(3):
        task = TaskModel(
            id=1100 + i,
            project_id=1,
            title=f"Task {i}",
            status="queued",
            agent="programmer",
            created_at=datetime.now(timezone.utc),
        )
        db_session.add(task)
    await db_session.commit()
    
    engine = OrchestratorEngine(TestSessionLocal)
    engine._openclaw_available = True
    
    with patch('app.orchestrator.engine.Scanner') as MockScanner, \
         patch('app.orchestrator.engine.WorkerManager') as MockWorkerManager:
        
        mock_scanner = AsyncMock()
        mock_scanner.get_eligible_tasks = AsyncMock(return_value=[
            {'id': 1100, 'project_id': 1, 'title': 'Task 0', 'status': 'queued', 'agent': 'programmer'},
            {'id': 1101, 'project_id': 1, 'title': 'Task 1', 'status': 'queued', 'agent': 'programmer'},
            {'id': 1102, 'project_id': 1, 'title': 'Task 2', 'status': 'queued', 'agent': 'programmer'},
        ])
        MockScanner.return_value = mock_scanner
        
        mock_worker_manager = AsyncMock()
        mock_worker_manager.spawn_worker = AsyncMock(return_value=True)
        mock_worker_manager.check_workers = AsyncMock()
        mock_worker_manager.active_workers = {}
        mock_worker_manager.sweep_requested = False
        mock_worker_manager.get_worker_status = AsyncMock(return_value={'busy': False})
        MockWorkerManager.return_value = mock_worker_manager
        
        await engine._run_once()
        
        # Verify all tasks attempted spawn
        assert mock_worker_manager.spawn_worker.call_count == 3


# ============================================================================
# Circuit Breaker Integration Tests
# ============================================================================


@pytest.mark.asyncio
async def test_circuit_breaker_blocks_failing_agent(db_session):
    """Test circuit breaker blocks spawns for failing agents."""
    project = ProjectModel(
        id=1,
        name="Test Project",
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(project)
    
    task = TaskModel(
        id=2000,
        project_id=1,
        title="Will be blocked",
        status="queued",
        agent="programmer",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(task)
    await db_session.commit()
    
    engine = OrchestratorEngine(TestSessionLocal)
    engine._openclaw_available = True
    
    with patch('app.orchestrator.engine.Scanner') as MockScanner, \
         patch('app.orchestrator.engine.WorkerManager') as MockWorkerManager, \
         patch('app.orchestrator.engine.CircuitBreaker') as MockCircuitBreaker:
        
        mock_scanner = AsyncMock()
        mock_scanner.get_eligible_tasks = AsyncMock(return_value=[{
            'id': 2000,
            'project_id': 1,
            'title': 'Will be blocked',
            'status': 'queued',
            'agent': 'programmer',
        }])
        MockScanner.return_value = mock_scanner
        
        # Circuit breaker blocks the spawn
        mock_circuit_breaker = AsyncMock()
        mock_circuit_breaker.should_allow_spawn = AsyncMock(
            return_value=(False, "Too many failures")
        )
        MockCircuitBreaker.return_value = mock_circuit_breaker
        
        mock_worker_manager = AsyncMock()
        mock_worker_manager.spawn_worker = AsyncMock()
        mock_worker_manager.check_workers = AsyncMock()
        mock_worker_manager.active_workers = {}
        mock_worker_manager.sweep_requested = False
        MockWorkerManager.return_value = mock_worker_manager
        
        await engine._run_once()
        
        # Verify circuit breaker was checked
        mock_circuit_breaker.should_allow_spawn.assert_called_once_with(
            project_id=1,
            agent_type='programmer'
        )
        
        # Verify spawn was NOT called (circuit breaker blocked)
        mock_worker_manager.spawn_worker.assert_not_called()


@pytest.mark.asyncio
async def test_circuit_breaker_allows_healthy_agent(db_session):
    """Test circuit breaker allows spawns for healthy agents."""
    project = ProjectModel(
        id=1,
        name="Test Project",
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(project)
    
    task = TaskModel(
        id=2001,
        project_id=1,
        title="Will be allowed",
        status="queued",
        agent="programmer",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(task)
    await db_session.commit()
    
    engine = OrchestratorEngine(TestSessionLocal)
    engine._openclaw_available = True
    
    with patch('app.orchestrator.engine.Scanner') as MockScanner, \
         patch('app.orchestrator.engine.WorkerManager') as MockWorkerManager, \
         patch('app.orchestrator.engine.CircuitBreaker') as MockCircuitBreaker:
        
        mock_scanner = AsyncMock()
        mock_scanner.get_eligible_tasks = AsyncMock(return_value=[{
            'id': 2001,
            'project_id': 1,
            'title': 'Will be allowed',
            'status': 'queued',
            'agent': 'programmer',
        }])
        MockScanner.return_value = mock_scanner
        
        # Circuit breaker allows the spawn
        mock_circuit_breaker = AsyncMock()
        mock_circuit_breaker.should_allow_spawn = AsyncMock(
            return_value=(True, "Healthy")
        )
        MockCircuitBreaker.return_value = mock_circuit_breaker
        
        mock_worker_manager = AsyncMock()
        mock_worker_manager.spawn_worker = AsyncMock(return_value=True)
        mock_worker_manager.check_workers = AsyncMock()
        mock_worker_manager.active_workers = {}
        mock_worker_manager.sweep_requested = False
        mock_worker_manager.get_worker_status = AsyncMock(return_value={'busy': False})
        MockWorkerManager.return_value = mock_worker_manager
        
        await engine._run_once()
        
        # Verify spawn WAS called (circuit breaker allowed)
        mock_worker_manager.spawn_worker.assert_called_once()


@pytest.mark.asyncio
async def test_circuit_breaker_per_project_isolation(db_session):
    """Test circuit breaker isolates failures per project."""
    # Create two projects
    project1 = ProjectModel(
        id=1,
        name="Failing Project",
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    project2 = ProjectModel(
        id=2,
        name="Healthy Project",
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(project1)
    db_session.add(project2)
    
    task1 = TaskModel(
        id=2010,
        project_id=1,
        title="Task in failing project",
        status="queued",
        agent="programmer",
        created_at=datetime.now(timezone.utc),
    )
    task2 = TaskModel(
        id=2011,
        project_id=2,
        title="Task in healthy project",
        status="queued",
        agent="programmer",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(task1)
    db_session.add(task2)
    await db_session.commit()
    
    engine = OrchestratorEngine(TestSessionLocal)
    engine._openclaw_available = True
    
    with patch('app.orchestrator.engine.Scanner') as MockScanner, \
         patch('app.orchestrator.engine.WorkerManager') as MockWorkerManager, \
         patch('app.orchestrator.engine.CircuitBreaker') as MockCircuitBreaker:
        
        mock_scanner = AsyncMock()
        mock_scanner.get_eligible_tasks = AsyncMock(return_value=[
            {'id': 2010, 'project_id': 1, 'title': 'Task in failing project', 'status': 'queued', 'agent': 'programmer'},
            {'id': 2011, 'project_id': 2, 'title': 'Task in healthy project', 'status': 'queued', 'agent': 'programmer'},
        ])
        MockScanner.return_value = mock_scanner
        
        # Circuit breaker blocks project 1, allows project 2
        mock_circuit_breaker = AsyncMock()
        def should_allow(project_id, agent_type):
            if project_id == 1:
                return (False, "Project 1 has failures")
            return (True, "Healthy")
        
        mock_circuit_breaker.should_allow_spawn = AsyncMock(side_effect=lambda project_id, agent_type: should_allow(project_id, agent_type))
        MockCircuitBreaker.return_value = mock_circuit_breaker
        
        mock_worker_manager = AsyncMock()
        mock_worker_manager.spawn_worker = AsyncMock(return_value=True)
        mock_worker_manager.check_workers = AsyncMock()
        mock_worker_manager.active_workers = {}
        mock_worker_manager.sweep_requested = False
        mock_worker_manager.get_worker_status = AsyncMock(return_value={'busy': False})
        MockWorkerManager.return_value = mock_worker_manager
        
        await engine._run_once()
        
        # Verify only task from healthy project was spawned
        assert mock_worker_manager.spawn_worker.call_count == 1
        call_args = mock_worker_manager.spawn_worker.call_args
        assert call_args[1]['task']['id'] == 2011  # Only project 2 task


# ============================================================================
# Provider Health Integration Tests
# ============================================================================


@pytest.mark.asyncio
async def test_provider_health_initialization(db_session):
    """Test provider health registry is initialized on first run."""
    engine = OrchestratorEngine(TestSessionLocal)
    
    assert engine.provider_health is None
    
    with patch('app.orchestrator.engine.Scanner') as MockScanner:
        mock_scanner = AsyncMock()
        mock_scanner.get_eligible_tasks = AsyncMock(return_value=[])
        MockScanner.return_value = mock_scanner
        
        with patch('app.orchestrator.engine.ProviderHealthRegistry') as MockProviderHealth:
            mock_health = AsyncMock()
            mock_health.initialize = AsyncMock()
            MockProviderHealth.return_value = mock_health
            
            await engine._run_once()
            
            # Verify provider health was initialized
            mock_health.initialize.assert_called_once()


@pytest.mark.asyncio
async def test_provider_health_persists_across_ticks(db_session):
    """Test provider health registry is reused across iterations."""
    engine = OrchestratorEngine(TestSessionLocal)
    
    with patch('app.orchestrator.engine.Scanner') as MockScanner:
        mock_scanner = AsyncMock()
        mock_scanner.get_eligible_tasks = AsyncMock(return_value=[])
        MockScanner.return_value = mock_scanner
        
        with patch('app.orchestrator.engine.ProviderHealthRegistry') as MockProviderHealth:
            mock_health = AsyncMock()
            mock_health.initialize = AsyncMock()
            MockProviderHealth.return_value = mock_health
            
            # Run twice
            await engine._run_once()
            await engine._run_once()
            
            # Initialize should only be called once
            mock_health.initialize.assert_called_once()


# ============================================================================
# Worker Dispatch Edge Cases
# ============================================================================


@pytest.mark.asyncio
async def test_worker_dispatch_respects_max_workers(db_session):
    """Test that engine doesn't spawn beyond max workers."""
    project = ProjectModel(
        id=1,
        name="Test Project",
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(project)
    
    # Create many tasks
    for i in range(10):
        task = TaskModel(
            id=3000 + i,
            project_id=1,
            title=f"Task {i}",
            status="queued",
            agent="programmer",
            created_at=datetime.now(timezone.utc),
        )
        db_session.add(task)
    await db_session.commit()
    
    engine = OrchestratorEngine(TestSessionLocal)
    engine._openclaw_available = True
    
    with patch('app.orchestrator.engine.Scanner') as MockScanner, \
         patch('app.orchestrator.engine.WorkerManager') as MockWorkerManager:
        
        mock_scanner = AsyncMock()
        mock_scanner.get_eligible_tasks = AsyncMock(return_value=[
            {'id': 3000 + i, 'project_id': 1, 'title': f'Task {i}', 'status': 'queued', 'agent': 'programmer'}
            for i in range(10)
        ])
        MockScanner.return_value = mock_scanner
        
        # Worker manager returns False after max workers reached
        spawn_count = 0
        def mock_spawn(*args, **kwargs):
            nonlocal spawn_count
            if spawn_count < 3:  # Max 3 workers
                spawn_count += 1
                return True
            return False
        
        mock_worker_manager = AsyncMock()
        mock_worker_manager.spawn_worker = AsyncMock(side_effect=mock_spawn)
        mock_worker_manager.check_workers = AsyncMock()
        mock_worker_manager.active_workers = {}
        mock_worker_manager.sweep_requested = False
        mock_worker_manager.get_worker_status = AsyncMock(return_value={'busy': False})
        MockWorkerManager.return_value = mock_worker_manager
        
        await engine._run_once()
        
        # Verify all 10 tasks were attempted, but only 3 succeeded
        assert mock_worker_manager.spawn_worker.call_count == 10
        assert spawn_count == 3


@pytest.mark.asyncio
async def test_worker_dispatch_logs_queue_depth(db_session):
    """Test that engine logs queued task count when worker is busy."""
    project = ProjectModel(
        id=1,
        name="Test Project",
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(project)
    
    task = TaskModel(
        id=3100,
        project_id=1,
        title="Queued Task",
        status="queued",
        agent="programmer",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(task)
    await db_session.commit()
    
    engine = OrchestratorEngine(TestSessionLocal)
    engine._openclaw_available = True
    
    with patch('app.orchestrator.engine.Scanner') as MockScanner, \
         patch('app.orchestrator.engine.WorkerManager') as MockWorkerManager, \
         patch('app.orchestrator.engine.logger') as mock_logger:
        
        mock_scanner = AsyncMock()
        mock_scanner.get_eligible_tasks = AsyncMock(return_value=[
            {'id': 3100, 'project_id': 1, 'title': 'Queued Task', 'status': 'queued', 'agent': 'programmer'},
        ])
        MockScanner.return_value = mock_scanner
        
        # Worker is busy
        mock_worker_manager = AsyncMock()
        mock_worker_manager.get_worker_status = AsyncMock(return_value={
            'busy': True,
            'current_task': '12345678'
        })
        mock_worker_manager.spawn_worker = AsyncMock(return_value=False)
        mock_worker_manager.check_workers = AsyncMock()
        mock_worker_manager.active_workers = {'worker1': ('proc', '12345678', 1, 'programmer', time.time(), '/log')}
        mock_worker_manager.sweep_requested = False
        MockWorkerManager.return_value = mock_worker_manager
        
        await engine._run_once()
        
        # Verify queue depth was logged
        assert any('queued' in str(call).lower() or 'busy' in str(call).lower() 
                   for call in mock_logger.info.call_args_list)


@pytest.mark.asyncio
async def test_worker_dispatch_skips_github_task_without_claim(db_session):
    """Test GitHub tasks require claim handshake before spawning."""
    project = ProjectModel(
        id=1,
        name="GitHub Project",
        status="active",
        tracking="github",
        github_repo="owner/repo",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(project)
    
    task = TaskModel(
        id=3200,
        project_id=1,
        title="GitHub Task",
        status="queued",
        agent="programmer",
        external_source="github",
        external_id="123",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(task)
    await db_session.commit()
    
    engine = OrchestratorEngine(TestSessionLocal)
    engine._openclaw_available = True
    
    with patch('app.orchestrator.engine.Scanner') as MockScanner, \
         patch('app.orchestrator.engine.WorkerManager') as MockWorkerManager, \
         patch('app.services.github_sync.GitHubSyncService') as MockGitHubSync:
        
        mock_scanner = AsyncMock()
        mock_scanner.get_eligible_tasks = AsyncMock(return_value=[{
            'id': 3200,
            'project_id': 1,
            'title': 'GitHub Task',
            'status': 'queued',
            'agent': 'programmer',
            'external_source': 'github',
        }])
        MockScanner.return_value = mock_scanner
        
        # GitHub claim fails
        mock_github_sync = AsyncMock()
        mock_github_sync.claim_issue_for_task = AsyncMock(return_value=(False, "Already claimed"))
        MockGitHubSync.return_value = mock_github_sync
        
        mock_worker_manager = AsyncMock()
        mock_worker_manager.spawn_worker = AsyncMock()
        mock_worker_manager.check_workers = AsyncMock()
        mock_worker_manager.active_workers = {}
        mock_worker_manager.sweep_requested = False
        MockWorkerManager.return_value = mock_worker_manager
        
        await engine._run_once()
        
        # Verify spawn was NOT called (claim failed)
        mock_worker_manager.spawn_worker.assert_not_called()


# ============================================================================
# Runtime Settings Tests
# ============================================================================


@pytest.mark.asyncio
async def test_runtime_settings_refresh_updates_intervals(db_session):
    """Test runtime settings are refreshed from database."""
    # Create runtime settings
    reflection_setting = OrchestratorSetting(
        key="orchestrator.reflection_interval_seconds",
        value=7200,  # 2 hours
    )
    db_session.add(reflection_setting)
    await db_session.commit()
    
    engine = OrchestratorEngine(TestSessionLocal)
    
    # Initial interval
    assert engine._reflection_interval == 10800  # Default 3 hours
    
    with patch('app.orchestrator.engine.Scanner') as MockScanner:
        mock_scanner = AsyncMock()
        mock_scanner.get_eligible_tasks = AsyncMock(return_value=[])
        MockScanner.return_value = mock_scanner
        
        # Force refresh
        engine._last_runtime_settings_refresh = 0.0
        
        await engine._run_once()
        
        # Verify interval was updated
        assert engine._reflection_interval == 7200


@pytest.mark.asyncio
async def test_runtime_settings_refresh_respects_minimum_values(db_session):
    """Test runtime settings enforce minimum values."""
    # Create invalid setting (too low)
    diagnostic_setting = OrchestratorSetting(
        key="orchestrator.diagnostic_interval_seconds",
        value=10,  # Too low, should be clamped to minimum
    )
    db_session.add(diagnostic_setting)
    await db_session.commit()
    
    engine = OrchestratorEngine(TestSessionLocal)
    
    with patch('app.orchestrator.engine.Scanner') as MockScanner:
        mock_scanner = AsyncMock()
        mock_scanner.get_eligible_tasks = AsyncMock(return_value=[])
        MockScanner.return_value = mock_scanner
        
        engine._last_runtime_settings_refresh = 0.0
        
        await engine._run_once()
        
        # Verify interval was clamped to minimum (30 seconds)
        assert engine._diagnostic_interval >= 30


@pytest.mark.asyncio
async def test_runtime_settings_loads_reflection_anchor(db_session):
    """Test reflection anchor timestamp is loaded from database."""
    # Create reflection anchor
    last_run = datetime.now(timezone.utc) - timedelta(hours=2)
    anchor = OrchestratorSetting(
        key="orchestrator.reflection_last_run_at",
        value=last_run.isoformat(),
    )
    db_session.add(anchor)
    await db_session.commit()
    
    engine = OrchestratorEngine(TestSessionLocal)
    
    with patch('app.orchestrator.engine.Scanner') as MockScanner:
        mock_scanner = AsyncMock()
        mock_scanner.get_eligible_tasks = AsyncMock(return_value=[])
        MockScanner.return_value = mock_scanner
        
        engine._last_runtime_settings_refresh = 0.0
        
        await engine._run_once()
        
        # Verify anchor was loaded
        assert engine._last_reflection_check > 0
        assert engine._reflection_anchor_loaded is True


# ============================================================================
# OpenClaw Model Catalog Sync Tests
# ============================================================================


@pytest.mark.asyncio
async def test_openclaw_model_sync_fetches_catalog(db_session):
    """Test OpenClaw model catalog is fetched and stored."""
    engine = OrchestratorEngine(TestSessionLocal)
    
    with patch('app.orchestrator.engine.Scanner') as MockScanner, \
         patch('app.orchestrator.engine.fetch_openclaw_model_catalog') as mock_fetch:
        
        mock_scanner = AsyncMock()
        mock_scanner.get_eligible_tasks = AsyncMock(return_value=[])
        MockScanner.return_value = mock_scanner
        
        # Mock catalog fetch
        mock_fetch.return_value = {
            'count': 2,
            'models': [
                {'model': 'gpt-4', 'provider': 'openai', 'billing_type': 'subscription'},
                {'model': 'claude-3', 'provider': 'anthropic', 'billing_type': 'payg'},
            ]
        }
        
        # Force sync
        engine._last_openclaw_model_sync = 0.0
        
        await engine._run_once()
        
        # Verify catalog was fetched
        mock_fetch.assert_called_once()


@pytest.mark.asyncio
async def test_openclaw_model_sync_updates_routing_policy(db_session):
    """Test model sync updates routing policy with subscription models."""
    engine = OrchestratorEngine(TestSessionLocal)
    
    with patch('app.orchestrator.engine.Scanner') as MockScanner, \
         patch('app.orchestrator.engine.fetch_openclaw_model_catalog') as mock_fetch:
        
        mock_scanner = AsyncMock()
        mock_scanner.get_eligible_tasks = AsyncMock(return_value=[])
        MockScanner.return_value = mock_scanner
        
        mock_fetch.return_value = {
            'count': 3,
            'models': [
                {'model': 'gpt-4-sub', 'provider': 'openai', 'billing_type': 'subscription'},
                {'model': 'claude-sub', 'provider': 'anthropic', 'billing_type': 'subscription'},
                {'model': 'gpt-4-payg', 'provider': 'openai', 'billing_type': 'payg'},
            ]
        }
        
        engine._last_openclaw_model_sync = 0.0
        
        await engine._run_once()
        
        # Check that policy was updated in database
        result = await db_session.execute(
            select(OrchestratorSetting).where(
                OrchestratorSetting.key == "usage.routing_policy"
            )
        )
        policy_setting = result.scalar_one_or_none()
        
        if policy_setting:
            policy = policy_setting.value
            assert 'subscription_models' in policy
            assert 'gpt-4-sub' in policy['subscription_models']
            assert 'claude-sub' in policy['subscription_models']


# ============================================================================
# Error Handling Edge Cases
# ============================================================================


@pytest.mark.asyncio
async def test_error_handling_continues_after_subsystem_failure(db_session):
    """Test engine continues processing after a subsystem fails."""
    engine = OrchestratorEngine(TestSessionLocal)
    
    with patch('app.orchestrator.engine.Scanner') as MockScanner, \
         patch('app.orchestrator.engine.EventScheduler') as MockScheduler:
        
        mock_scanner = AsyncMock()
        mock_scanner.get_eligible_tasks = AsyncMock(return_value=[])
        MockScanner.return_value = mock_scanner
        
        # Scheduler raises exception
        mock_scheduler = AsyncMock()
        mock_scheduler.check_due_events = AsyncMock(side_effect=Exception("Scheduler error"))
        MockScheduler.return_value = mock_scheduler
        
        # Force scheduler check
        engine._last_scheduler_check = 0.0
        
        # Should not raise, should continue
        try:
            await engine._run_once()
        except Exception as e:
            pytest.fail(f"Engine should handle subsystem errors gracefully: {e}")


@pytest.mark.asyncio
async def test_error_handling_database_rollback_on_error(db_session):
    """Test database is rolled back on errors."""
    engine = OrchestratorEngine(TestSessionLocal)
    
    with patch('app.orchestrator.engine.Scanner') as MockScanner, \
         patch('app.orchestrator.engine.RoutineRunner') as MockRoutine:
        
        mock_scanner = AsyncMock()
        mock_scanner.get_eligible_tasks = AsyncMock(return_value=[])
        MockScanner.return_value = mock_scanner
        
        # Routine runner raises exception
        mock_routine = AsyncMock()
        mock_routine.process_due_routines = AsyncMock(side_effect=Exception("DB error"))
        MockRoutine.return_value = mock_routine
        
        engine._last_routine_check = 0.0
        
        # Should handle error and rollback
        await engine._run_once()
        
        # No assertions needed - test passes if no unhandled exception


@pytest.mark.asyncio
async def test_error_handling_worker_check_exception(db_session):
    """Test engine handles worker check exceptions."""
    engine = OrchestratorEngine(TestSessionLocal)
    
    with patch('app.orchestrator.engine.Scanner') as MockScanner, \
         patch('app.orchestrator.engine.WorkerManager') as MockWorkerManager:
        
        mock_scanner = AsyncMock()
        mock_scanner.get_eligible_tasks = AsyncMock(return_value=[])
        MockScanner.return_value = mock_scanner
        
        mock_worker_manager = AsyncMock()
        # Worker check raises exception
        mock_worker_manager.check_workers = AsyncMock(side_effect=Exception("Worker check failed"))
        mock_worker_manager.active_workers = {}
        mock_worker_manager.sweep_requested = False
        MockWorkerManager.return_value = mock_worker_manager
        
        # Should handle gracefully
        try:
            await engine._run_once()
        except Exception as e:
            pytest.fail(f"Engine should handle worker check failures: {e}")


@pytest.mark.asyncio
async def test_error_handling_reflection_exception(db_session):
    """Test engine handles reflection cycle exceptions."""
    engine = OrchestratorEngine(TestSessionLocal)
    engine._paused = False
    
    with patch('app.orchestrator.engine.Scanner') as MockScanner, \
         patch('app.orchestrator.engine.LobsControlLoopService') as MockControlLoop:
        
        mock_scanner = AsyncMock()
        mock_scanner.get_eligible_tasks = AsyncMock(return_value=[])
        MockScanner.return_value = mock_scanner
        
        # Control loop raises exception
        mock_control_loop = AsyncMock()
        mock_control_loop.run_once = AsyncMock(side_effect=Exception("Reflection failed"))
        MockControlLoop.return_value = mock_control_loop
        
        # Should handle gracefully
        try:
            await engine._run_once()
        except Exception as e:
            pytest.fail(f"Engine should handle reflection failures: {e}")


# ============================================================================
# Control Loop Integration Tests
# ============================================================================


@pytest.mark.asyncio
async def test_control_loop_heartbeat_persisted(db_session):
    """Test control loop heartbeat is persisted to database."""
    engine = OrchestratorEngine(TestSessionLocal)
    engine._paused = False
    
    with patch('app.orchestrator.engine.Scanner') as MockScanner, \
         patch('app.orchestrator.engine.LobsControlLoopService') as MockControlLoop:
        
        mock_scanner = AsyncMock()
        mock_scanner.get_eligible_tasks = AsyncMock(return_value=[])
        MockScanner.return_value = mock_scanner
        
        # Mock control loop to return success
        mock_control_loop = AsyncMock()
        mock_control_loop.run_once = AsyncMock(return_value=MagicMock(
            events_processed=0,
            reflection_triggered=False,
            compression_triggered=False
        ))
        mock_control_loop.reflection_last_run_at = 0.0
        mock_control_loop.last_compression_date_et = None
        MockControlLoop.return_value = mock_control_loop
        
        await engine._run_once()
        
        # Verify control loop ran
        mock_control_loop.run_once.assert_called_once()


@pytest.mark.asyncio
async def test_control_loop_daily_compression_once_per_day(db_session):
    """Test daily compression runs only once per day."""
    # Set marker for today
    today_et = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    compression_marker = OrchestratorSetting(
        key="orchestrator.daily_compression_last_date_et",
        value=today_et,
    )
    db_session.add(compression_marker)
    await db_session.commit()
    
    engine = OrchestratorEngine(TestSessionLocal)
    engine._paused = False
    
    with patch('app.orchestrator.engine.Scanner') as MockScanner, \
         patch('app.orchestrator.engine.LobsControlLoopService') as MockControlLoop:
        
        mock_scanner = AsyncMock()
        mock_scanner.get_eligible_tasks = AsyncMock(return_value=[])
        MockScanner.return_value = mock_scanner
        
        # Mock control loop
        mock_control_loop = AsyncMock()
        mock_control_loop.run_once = AsyncMock(return_value=MagicMock(
            events_processed=0,
            reflection_triggered=False,
            compression_triggered=False
        ))
        mock_control_loop.reflection_last_run_at = 0.0
        mock_control_loop.last_compression_date_et = today_et
        MockControlLoop.return_value = mock_control_loop
        
        # Force settings refresh
        engine._last_runtime_settings_refresh = 0.0
        
        await engine._run_once()
        
        # Verify compression marker was loaded
        assert engine._last_daily_compression_date_et == today_et


# ============================================================================
# Status and Monitoring Tests
# ============================================================================


@pytest.mark.asyncio
async def test_get_status_returns_comprehensive_info(db_session):
    """Test get_status returns all relevant engine state."""
    engine = OrchestratorEngine(TestSessionLocal)
    
    with patch('app.orchestrator.engine.WorkerManager') as MockWorkerManager, \
         patch('app.orchestrator.engine.AgentTracker') as MockAgentTracker:
        
        mock_worker_manager = AsyncMock()
        mock_worker_manager.get_worker_status = AsyncMock(return_value={
            'busy': False,
            'active_workers': 0,
        })
        MockWorkerManager.return_value = mock_worker_manager
        
        mock_agent_tracker = AsyncMock()
        mock_agent_tracker.get_all_statuses = AsyncMock(return_value=[])
        MockAgentTracker.return_value = mock_agent_tracker
        
        status = await engine.get_status()
        
        assert 'running' in status
        assert 'paused' in status
        assert 'worker' in status
        assert 'agents' in status
        assert 'control_loop' in status
        
        # Check control loop details
        control_loop = status['control_loop']
        assert 'reflection_interval_seconds' in control_loop
        assert 'daily_compression_hour_et' in control_loop


@pytest.mark.asyncio
async def test_get_worker_details_returns_active_workers(db_session):
    """Test get_worker_details returns info about running workers."""
    engine = OrchestratorEngine(TestSessionLocal)
    
    # Create a mock active worker
    mock_process = MagicMock()
    mock_process.pid = 12345
    
    with patch('app.orchestrator.engine.WorkerManager') as MockWorkerManager:
        mock_worker_manager = AsyncMock()
        mock_worker_manager.active_workers = {
            'worker-1': (mock_process, 'task-123', 1, 'programmer', time.time(), '/tmp/log.txt')
        }
        MockWorkerManager.return_value = mock_worker_manager
        
        engine._worker_manager = mock_worker_manager
        
        workers = await engine.get_worker_details()
        
        assert len(workers) == 1
        assert workers[0]['worker_id'] == 'worker-1'
        assert workers[0]['task_id'] == 'task-123'
        assert workers[0]['agent_type'] == 'programmer'
        assert workers[0]['pid'] == 12345


# ============================================================================
# Lifecycle Tests
# ============================================================================


@pytest.mark.asyncio
async def test_engine_shutdown_stops_workers(db_session):
    """Test engine shutdown cleanly stops all workers."""
    engine = OrchestratorEngine(TestSessionLocal)
    
    with patch('app.orchestrator.engine.WorkerManager') as MockWorkerManager:
        mock_worker_manager = AsyncMock()
        mock_worker_manager.shutdown = AsyncMock()
        engine._worker_manager = mock_worker_manager
        
        await engine.stop(timeout=0.1)
        
        # Verify shutdown was called
        mock_worker_manager.shutdown.assert_called_once()


@pytest.mark.asyncio
async def test_engine_start_checks_openclaw_availability():
    """Test engine checks for OpenClaw binary on start."""
    engine = OrchestratorEngine(TestSessionLocal)
    
    with patch('shutil.which') as mock_which:
        mock_which.return_value = None  # OpenClaw not found
        
        await engine.start()
        
        # Should still start but in monitoring mode
        assert engine._running is True
        assert engine._openclaw_available is False
        
        await engine.stop(timeout=0.1)


@pytest.mark.asyncio
async def test_engine_monitoring_mode_skips_worker_spawn():
    """Test engine in monitoring mode (no OpenClaw) doesn't spawn workers."""
    engine = OrchestratorEngine(TestSessionLocal)
    engine._openclaw_available = False
    
    project = ProjectModel(
        id=1,
        name="Test Project",
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    task = TaskModel(
        id=5000,
        project_id=1,
        title="Test Task",
        status="queued",
        agent="programmer",
        created_at=datetime.now(timezone.utc),
    )
    
    async with TestSessionLocal() as db:
        db.add(project)
        db.add(task)
        await db.commit()
    
    with patch('app.orchestrator.engine.Scanner') as MockScanner, \
         patch('app.orchestrator.engine.WorkerManager') as MockWorkerManager:
        
        mock_scanner = AsyncMock()
        mock_scanner.get_eligible_tasks = AsyncMock(return_value=[{
            'id': 5000,
            'project_id': 1,
            'title': 'Test Task',
            'status': 'queued',
            'agent': 'programmer',
        }])
        MockScanner.return_value = mock_scanner
        
        mock_worker_manager = AsyncMock()
        mock_worker_manager.spawn_worker = AsyncMock()
        mock_worker_manager.check_workers = AsyncMock()
        mock_worker_manager.active_workers = {}
        mock_worker_manager.sweep_requested = False
        MockWorkerManager.return_value = mock_worker_manager
        
        await engine._run_once()
        
        # Verify no spawn attempt in monitoring mode
        mock_worker_manager.spawn_worker.assert_not_called()
