"""Mock helpers for common test patterns.

Provides pre-configured mock objects for frequently mocked components
in the orchestrator and other services.
"""

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, Mock, patch
from typing import Generator, Optional, List, Dict, Any, Tuple


def mock_worker_manager(
    spawn_result: bool = True,
    active_workers: Optional[List[Dict[str, Any]]] = None,
    sweep_requested: bool = False,
    **kwargs
) -> AsyncMock:
    """Create a mock WorkerManager.
    
    Args:
        spawn_result: Return value for spawn_worker()
        active_workers: List of active worker dicts
        sweep_requested: Value for sweep_requested attribute
        **kwargs: Additional attributes to set on mock
        
    Returns:
        Configured AsyncMock for WorkerManager
    """
    mock = AsyncMock()
    mock.spawn_worker = AsyncMock(return_value=spawn_result)
    mock.get_active_workers = AsyncMock(return_value=active_workers or [])
    mock.sweep_requested = sweep_requested
    mock.cleanup_worker = AsyncMock()
    mock.get_worker_status = AsyncMock(return_value=None)
    
    # Set additional attributes
    for key, value in kwargs.items():
        setattr(mock, key, value)
    
    return mock


def mock_scanner(
    work_items: Optional[List[Dict[str, Any]]] = None,
    **kwargs
) -> AsyncMock:
    """Create a mock Scanner.
    
    Args:
        work_items: List of work items to return from scan_for_work()
        **kwargs: Additional attributes to set on mock
        
    Returns:
        Configured AsyncMock for Scanner
    """
    mock = AsyncMock()
    mock.scan_for_work = AsyncMock(return_value=work_items or [])
    mock.find_eligible_tasks = AsyncMock(return_value=work_items or [])
    
    # Set additional attributes
    for key, value in kwargs.items():
        setattr(mock, key, value)
    
    return mock


def mock_monitor(
    stuck_tasks: Optional[List[Dict[str, Any]]] = None,
    **kwargs
) -> AsyncMock:
    """Create a mock MonitorEnhanced.
    
    Args:
        stuck_tasks: List of stuck task dicts to return from check_stuck_tasks()
        **kwargs: Additional attributes to set on mock
        
    Returns:
        Configured AsyncMock for MonitorEnhanced
    """
    mock = AsyncMock()
    mock.check_stuck_tasks = AsyncMock(return_value=stuck_tasks or [])
    mock.handle_stuck_task = AsyncMock()
    mock.escalate_task = AsyncMock()
    
    # Set additional attributes
    for key, value in kwargs.items():
        setattr(mock, key, value)
    
    return mock


def mock_scheduler(
    due_events: Optional[List[Dict[str, Any]]] = None,
    **kwargs
) -> AsyncMock:
    """Create a mock EventScheduler.
    
    Args:
        due_events: List of due event dicts to return from check_due_events()
        **kwargs: Additional attributes to set on mock
        
    Returns:
        Configured AsyncMock for EventScheduler
    """
    mock = AsyncMock()
    mock.check_due_events = AsyncMock(return_value=due_events or [])
    mock.execute_event = AsyncMock()
    mock.schedule_event = AsyncMock()
    
    # Set additional attributes
    for key, value in kwargs.items():
        setattr(mock, key, value)
    
    return mock


def mock_reflection_manager(
    should_reflect: bool = False,
    **kwargs
) -> AsyncMock:
    """Create a mock ReflectionCycleManager.
    
    Args:
        should_reflect: Return value for should_trigger_reflection()
        **kwargs: Additional attributes to set on mock
        
    Returns:
        Configured AsyncMock for ReflectionCycleManager
    """
    mock = AsyncMock()
    mock.should_trigger_reflection = AsyncMock(return_value=should_reflect)
    mock.trigger_reflection = AsyncMock()
    mock.get_last_reflection = AsyncMock(return_value=None)
    
    # Set additional attributes
    for key, value in kwargs.items():
        setattr(mock, key, value)
    
    return mock


def mock_openclaw_bridge(
    webhook_result: Optional[Dict[str, Any]] = None,
    **kwargs
) -> AsyncMock:
    """Create a mock OpenClawBridge.
    
    Args:
        webhook_result: Return value for handle_webhook()
        **kwargs: Additional attributes to set on mock
        
    Returns:
        Configured AsyncMock for OpenClawBridge
    """
    mock = AsyncMock()
    mock.handle_webhook = AsyncMock(return_value=webhook_result or {"status": "ok"})
    mock.send_message = AsyncMock()
    
    # Set additional attributes
    for key, value in kwargs.items():
        setattr(mock, key, value)
    
    return mock


def mock_routine_runner(
    routines: Optional[List[Dict[str, Any]]] = None,
    **kwargs
) -> AsyncMock:
    """Create a mock RoutineRunner.
    
    Args:
        routines: List of routine dicts to return from get_due_routines()
        **kwargs: Additional attributes to set on mock
        
    Returns:
        Configured AsyncMock for RoutineRunner
    """
    mock = AsyncMock()
    mock.get_due_routines = AsyncMock(return_value=routines or [])
    mock.execute_routine = AsyncMock()
    
    # Set additional attributes
    for key, value in kwargs.items():
        setattr(mock, key, value)
    
    return mock


def mock_sweep_arbitrator(
    should_sweep: bool = False,
    **kwargs
) -> AsyncMock:
    """Create a mock SweepArbitrator.
    
    Args:
        should_sweep: Return value for should_trigger_sweep()
        **kwargs: Additional attributes to set on mock
        
    Returns:
        Configured AsyncMock for SweepArbitrator
    """
    mock = AsyncMock()
    mock.should_trigger_sweep = AsyncMock(return_value=should_sweep)
    mock.trigger_sweep = AsyncMock()
    mock.get_sweep_status = AsyncMock(return_value={"active": False})
    
    # Set additional attributes
    for key, value in kwargs.items():
        setattr(mock, key, value)
    
    return mock


def mock_inbox_processor(
    processed_count: int = 0,
    **kwargs
) -> AsyncMock:
    """Create a mock InboxProcessor.
    
    Args:
        processed_count: Return value for process_inbox()
        **kwargs: Additional attributes to set on mock
        
    Returns:
        Configured AsyncMock for InboxProcessor
    """
    mock = AsyncMock()
    mock.process_inbox = AsyncMock(return_value=processed_count)
    mock.process_item = AsyncMock()
    
    # Set additional attributes
    for key, value in kwargs.items():
        setattr(mock, key, value)
    
    return mock


def mock_db_session(**kwargs) -> AsyncMock:
    """Create a mock database session.
    
    Args:
        **kwargs: Additional attributes to set on mock
        
    Returns:
        Configured AsyncMock for database session
    """
    mock = AsyncMock()
    mock.add = Mock()
    mock.commit = AsyncMock()
    mock.rollback = AsyncMock()
    mock.refresh = AsyncMock()
    mock.execute = AsyncMock()
    mock.scalar = AsyncMock()
    mock.close = AsyncMock()
    
    # Set additional attributes
    for key, value in kwargs.items():
        setattr(mock, key, value)
    
    return mock


@contextmanager
def mock_engine_components(
    work_items: Optional[List[Dict[str, Any]]] = None,
    spawn_result: bool = True,
    active_workers: Optional[List[Dict[str, Any]]] = None,
    sweep_requested: bool = False,
) -> Generator[Tuple[AsyncMock, AsyncMock], None, None]:
    """Context manager that patches WorkerManager and Scanner for engine tests.

    This eliminates the repeated nested ``patch`` boilerplate seen in
    ``test_orchestrator_engine.py`` and ``test_engine_extended.py``.

    Example::

        async def test_something(db_session, orchestrator_engine):
            with mock_engine_components(work_items=[{"id": "t1", ...}]) as (wm, sc):
                activity = await orchestrator_engine._run_once()
                assert activity is True
                wm.spawn_worker.assert_called_once()

    Args:
        work_items: List of task dicts returned by ``scanner.scan_for_work()``.
            Defaults to an empty list (no work).
        spawn_result: Return value for ``worker_manager.spawn_worker()``.
            Defaults to ``True`` (successful spawn).
        active_workers: List of active worker dicts returned by
            ``worker_manager.get_active_workers()``.  Defaults to ``[]``.
        sweep_requested: Value of ``worker_manager.sweep_requested`` attribute.

    Yields:
        Tuple of ``(mock_worker_manager, mock_scanner)`` so callers can make
        additional assertions or configure further return values.
    """
    with patch("app.orchestrator.engine.WorkerManager") as MockWorkerManager, \
         patch("app.orchestrator.engine.Scanner") as MockScanner:

        # Configure WorkerManager mock
        _wm = AsyncMock()
        _wm.spawn_worker = AsyncMock(return_value=spawn_result)
        _wm.get_active_workers = AsyncMock(return_value=active_workers or [])
        _wm.check_workers = AsyncMock()
        _wm.cleanup_worker = AsyncMock()
        _wm.active_workers = {}
        _wm.sweep_requested = sweep_requested
        _wm.get_worker_status = AsyncMock(return_value=None)
        MockWorkerManager.return_value = _wm

        # Configure Scanner mock
        _sc = AsyncMock()
        _sc.scan_for_work = AsyncMock(return_value=work_items or [])
        _sc.find_eligible_tasks = AsyncMock(return_value=work_items or [])
        _sc.get_eligible_tasks = AsyncMock(return_value=work_items or [])
        MockScanner.return_value = _sc

        yield _wm, _sc
