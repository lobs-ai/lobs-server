"""Test helpers library for lobs-server tests.

This module provides shared fixtures, factories, and utilities to reduce
duplication across test files.

Modules:
- factories: Data factories for creating test objects
- mocks: Common mock objects and helpers
- assertions: Custom assertion helpers
- fixtures: Additional pytest fixtures (imported in conftest.py)
"""

from tests.helpers.factories import (
    create_project_data,
    create_task_data,
    create_inbox_data,
    create_document_data,
    create_agent_data,
    create_memory_data,
    create_topic_data,
    create_calendar_event_data,
    create_template_data,
    # DB-level model factories
    make_project_model,
    make_task_model,
)

from tests.helpers.mocks import (
    mock_worker_manager,
    mock_scanner,
    mock_monitor,
    mock_scheduler,
    mock_reflection_manager,
    mock_openclaw_bridge,
    mock_routine_runner,
    mock_sweep_arbitrator,
    mock_inbox_processor,
    mock_db_session,
    # Combined engine component patcher
    mock_engine_components,
)

from tests.helpers.assertions import (
    assert_task_status,
    assert_response_success,
    assert_response_error,
    assert_has_fields,
    assert_list_response,
    assert_pagination_headers,
    assert_timestamp_fields,
    assert_db_object_matches,
    assert_json_schema,
    assert_not_found,
    assert_deleted,
    assert_created,
    assert_updated,
)

__all__ = [
    # Factories — API payload dicts
    "create_project_data",
    "create_task_data",
    "create_inbox_data",
    "create_document_data",
    "create_agent_data",
    "create_memory_data",
    "create_topic_data",
    "create_calendar_event_data",
    "create_template_data",
    # Factories — SQLAlchemy model instances
    "make_project_model",
    "make_task_model",
    # Mocks — individual components
    "mock_worker_manager",
    "mock_scanner",
    "mock_monitor",
    "mock_scheduler",
    "mock_reflection_manager",
    "mock_openclaw_bridge",
    "mock_routine_runner",
    "mock_sweep_arbitrator",
    "mock_inbox_processor",
    "mock_db_session",
    # Mocks — combined helpers
    "mock_engine_components",
    # Assertions
    "assert_task_status",
    "assert_response_success",
    "assert_response_error",
    "assert_has_fields",
    "assert_list_response",
    "assert_pagination_headers",
    "assert_timestamp_fields",
    "assert_db_object_matches",
    "assert_json_schema",
    "assert_not_found",
    "assert_deleted",
    "assert_created",
    "assert_updated",
]
