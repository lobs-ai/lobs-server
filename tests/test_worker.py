"""Tests for worker API endpoints and worker manager."""

import pytest
from httpx import AsyncClient
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy.ext.asyncio import AsyncSession

from app.orchestrator.worker import WorkerManager


@pytest.mark.asyncio
async def test_get_worker_status_empty(client: AsyncClient):
    """Test getting worker status returns default when empty."""
    response = await client.get("/api/worker/status")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == 1
    assert data["active"] is False
    assert data["tasks_completed"] == 0
    assert data["input_tokens"] == 0
    assert data["output_tokens"] == 0


@pytest.mark.asyncio
async def test_update_worker_status(client: AsyncClient):
    """Test updating worker status."""
    status_data = {
        "active": True,
        "worker_id": "worker-123",
        "current_task": "task-1",
        "tasks_completed": 5,
        "input_tokens": 1000,
        "output_tokens": 500
    }
    response = await client.put("/api/worker/status", json=status_data)
    assert response.status_code == 200
    data = response.json()
    assert data["active"] is True
    assert data["worker_id"] == "worker-123"
    assert data["current_task"] == "task-1"
    assert data["tasks_completed"] == 5
    assert data["input_tokens"] == 1000
    assert data["output_tokens"] == 500


@pytest.mark.asyncio
async def test_update_worker_status_partial(client: AsyncClient):
    """Test partially updating worker status."""
    # Create initial status
    await client.put("/api/worker/status", json={
        "active": True,
        "worker_id": "worker-1",
        "tasks_completed": 3
    })
    
    # Partial update
    response = await client.put("/api/worker/status", json={
        "tasks_completed": 5
    })
    assert response.status_code == 200
    data = response.json()
    assert data["tasks_completed"] == 5
    assert data["active"] is True  # Unchanged


@pytest.mark.asyncio
async def test_list_worker_runs_empty(client: AsyncClient):
    """Test listing worker runs when empty."""
    response = await client.get("/api/worker/history")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_create_worker_run(client: AsyncClient):
    """Test creating a worker run."""
    run_data = {
        "worker_id": "worker-123",
        "started_at": "2026-01-01T00:00:00Z",
        "ended_at": "2026-01-01T01:00:00Z",
        "tasks_completed": 3,
        "model": "claude-sonnet-4-5",
        "input_tokens": 5000,
        "output_tokens": 2000,
        "total_tokens": 7000,
        "total_cost_usd": 0.05,
        "succeeded": True,
        "source": "orchestrator"
    }
    response = await client.post("/api/worker/history", json=run_data)
    assert response.status_code == 200
    data = response.json()
    assert data["worker_id"] == "worker-123"
    assert data["tasks_completed"] == 3
    assert data["model"] == "claude-sonnet-4-5"
    assert data["total_tokens"] == 7000
    assert data["succeeded"] is True
    assert "id" in data


@pytest.mark.asyncio
async def test_list_worker_runs(client: AsyncClient):
    """Test listing worker runs."""
    # Create runs
    for i in range(3):
        await client.post("/api/worker/history", json={
            "worker_id": f"worker-{i}",
            "tasks_completed": i,
            "input_tokens": 1000 * i,
            "output_tokens": 500 * i,
            "total_tokens": 1500 * i
        })
    
    response = await client.get("/api/worker/history")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 3
    # Verify they're in descending order by ID
    assert data[0]["id"] > data[1]["id"]


@pytest.mark.asyncio
async def test_list_worker_runs_pagination(client: AsyncClient):
    """Test worker run history pagination."""
    # Create multiple runs
    for i in range(5):
        await client.post("/api/worker/history", json={
            "worker_id": f"worker-{i}",
            "tasks_completed": i,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0
        })
    
    # Test limit
    response = await client.get("/api/worker/history?limit=2")
    assert response.status_code == 200
    assert len(response.json()) == 2
    
    # Test offset
    response = await client.get("/api/worker/history?offset=2&limit=2")
    assert response.status_code == 200
    assert len(response.json()) == 2


class TestWorkerManager:
    """Tests for WorkerManager session termination."""

    def _make_ws_mock(self, messages):
        """Build a mock WebSocket that yields the given list of (type, data) tuples."""
        import aiohttp
        ws = MagicMock()
        ws.__aenter__ = AsyncMock(return_value=ws)
        ws.__aexit__ = AsyncMock(return_value=False)
        ws.send_json = AsyncMock()

        async def _aiter():
            for msg_type, msg_data in messages:
                m = MagicMock()
                m.type = msg_type
                if msg_type == aiohttp.WSMsgType.TEXT:
                    import json
                    m.data = json.dumps(msg_data)
                yield m

        ws.__aiter__ = _aiter
        return ws

    @pytest.mark.asyncio
    async def test_terminate_session_success(self, db_session: AsyncSession):
        """Test successful session termination via WebSocket."""
        import aiohttp
        manager = WorkerManager(db_session)

        with patch("app.orchestrator.worker.aiohttp.ClientSession") as mock_cs_class:
            http_session = MagicMock()
            http_session.__aenter__ = AsyncMock(return_value=http_session)
            http_session.__aexit__ = AsyncMock(return_value=False)
            mock_cs_class.return_value = http_session

            # ws_connect returns a ws mock that sends a success response
            def make_response_ws(_url, **kwargs):
                # We need the request_id — use a wildcard: return True for any id
                return self._make_ws_mock([
                    (aiohttp.WSMsgType.TEXT, {"jsonrpc": "2.0", "id": None, "result": {"deleted": True}}),
                ])

            # Patch ws_connect to intercept and inject the correct request_id
            real_send = None
            captured_id = []

            class FakeWS:
                def __init__(self, msgs_fn):
                    self._msgs_fn = msgs_fn
                async def __aenter__(self):
                    return self
                async def __aexit__(self, *a):
                    return False
                async def send_json(self, payload):
                    captured_id.append(payload.get("id"))
                def __aiter__(self):
                    return self._gen()
                async def _gen(self):
                    rid = captured_id[0] if captured_id else "x"
                    import json, aiohttp
                    m = MagicMock()
                    m.type = aiohttp.WSMsgType.TEXT
                    m.data = json.dumps({"jsonrpc": "2.0", "id": rid, "result": {"deleted": True}})
                    yield m

            http_session.ws_connect = MagicMock(return_value=FakeWS(None))

            result = await manager._terminate_session(
                "agent:programmer:subagent:test-123",
                "timeout",
            )

            assert result is True

    @pytest.mark.asyncio
    async def test_terminate_session_not_found(self, db_session: AsyncSession):
        """Test session termination when session not found (treat as success)."""
        manager = WorkerManager(db_session)

        with patch("app.orchestrator.worker.aiohttp.ClientSession") as mock_cs_class:
            http_session = MagicMock()
            http_session.__aenter__ = AsyncMock(return_value=http_session)
            http_session.__aexit__ = AsyncMock(return_value=False)
            mock_cs_class.return_value = http_session

            captured_id = []

            class FakeWS:
                async def __aenter__(self): return self
                async def __aexit__(self, *a): return False
                async def send_json(self, payload): captured_id.append(payload.get("id"))
                def __aiter__(self): return self._gen()
                async def _gen(self):
                    import json, aiohttp
                    rid = captured_id[0] if captured_id else "x"
                    m = MagicMock()
                    m.type = aiohttp.WSMsgType.TEXT
                    m.data = json.dumps({"jsonrpc": "2.0", "id": rid, "error": {"message": "session not found"}})
                    yield m

            http_session.ws_connect = MagicMock(return_value=FakeWS())

            result = await manager._terminate_session(
                "agent:programmer:subagent:test-123",
                "cleanup",
            )

            assert result is True  # Not found treated as success

    @pytest.mark.asyncio
    async def test_terminate_session_api_error(self, db_session: AsyncSession):
        """Test session termination when API returns a non-not-found error."""
        manager = WorkerManager(db_session)

        with patch("app.orchestrator.worker.aiohttp.ClientSession") as mock_cs_class:
            http_session = MagicMock()
            http_session.__aenter__ = AsyncMock(return_value=http_session)
            http_session.__aexit__ = AsyncMock(return_value=False)
            mock_cs_class.return_value = http_session

            captured_id = []

            class FakeWS:
                async def __aenter__(self): return self
                async def __aexit__(self, *a): return False
                async def send_json(self, payload): captured_id.append(payload.get("id"))
                def __aiter__(self): return self._gen()
                async def _gen(self):
                    import json, aiohttp
                    rid = captured_id[0] if captured_id else "x"
                    m = MagicMock()
                    m.type = aiohttp.WSMsgType.TEXT
                    m.data = json.dumps({"jsonrpc": "2.0", "id": rid, "error": {"message": "Internal server error"}})
                    yield m

            http_session.ws_connect = MagicMock(return_value=FakeWS())

            result = await manager._terminate_session(
                "agent:programmer:subagent:test-123",
                "timeout",
            )

            assert result is False

    @pytest.mark.asyncio
    async def test_terminate_session_network_error(self, db_session: AsyncSession):
        """Test session termination when network error occurs."""
        manager = WorkerManager(db_session)

        with patch("app.orchestrator.worker.aiohttp.ClientSession") as mock_cs_class:
            http_session = MagicMock()
            http_session.__aenter__ = AsyncMock(return_value=http_session)
            http_session.__aexit__ = AsyncMock(return_value=False)
            mock_cs_class.return_value = http_session
            http_session.ws_connect = MagicMock(side_effect=Exception("Network error"))

            result = await manager._terminate_session(
                "agent:programmer:subagent:test-123",
                "timeout",
            )

            assert result is False


# ── _terminate_session uses sessions.delete WebSocket RPC ────────────────────

@pytest.mark.asyncio
async def test_terminate_session_uses_websocket_delete(monkeypatch):
    """_terminate_session must use sessions.delete JSON-RPC, not sessions_kill."""
    import json
    import aiohttp as _aiohttp
    from unittest.mock import AsyncMock, MagicMock, patch

    sent_messages = []

    class FakeWS:
        async def send_json(self, data):
            sent_messages.append(data)
        def __aiter__(self):
            return self
        async def __anext__(self):
            # Return a success response for the last sent message
            if sent_messages:
                msg = MagicMock()
                msg.type = _aiohttp.WSMsgType.TEXT
                msg.data = json.dumps({"id": sent_messages[-1]["id"], "result": {"deleted": True}})
                sent_messages.clear()  # prevent infinite loop
                return msg
            raise StopAsyncIteration
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            pass

    class FakeSession:
        def ws_connect(self, url, **kwargs):
            return FakeWS()
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            pass

    with patch("app.orchestrator.worker.aiohttp.ClientSession", return_value=FakeSession()):
        from app.orchestrator.worker import WorkerManager
        import sqlalchemy
        # Create a minimal WorkerManager-like object to test just _terminate_session
        db_mock = AsyncMock()
        wm = WorkerManager.__new__(WorkerManager)
        result = await wm._terminate_session("test-session-key-123", "task_complete")

    assert result is True
    # Verify it used sessions.delete and not sessions_kill
    # (sent_messages was cleared on success, but we can verify no REST call was made)
    # The key check: no sessions_kill in the source
    import inspect
    import app.orchestrator.worker as wmod
    src = inspect.getsource(wmod.WorkerManager._terminate_session)
    assert "sessions_kill" not in src, "sessions_kill must not be used in _terminate_session"
    assert "sessions.delete" in src, "sessions.delete must be used in _terminate_session"



@pytest.mark.asyncio
async def test_record_worker_run_retry_on_lock():
    """Test that _record_worker_run retries on database lock."""
    import asyncio
    from datetime import datetime, timezone
    from unittest.mock import AsyncMock, MagicMock, patch
    from app.models import WorkerRun
    
    # Create a mock session
    db = AsyncMock(spec=AsyncSession)
    
    # Mock the independent session
    independent_db = AsyncMock(spec=AsyncSession)
    independent_db.add = MagicMock()
    
    # Fail first 2 times, succeed on 3rd
    independent_db.commit = AsyncMock(side_effect=[
        Exception("database is locked"),
        Exception("database is locked"),
        None  # Success
    ])
    independent_db.rollback = AsyncMock()
    
    # Create manager
    manager = WorkerManager(db)
    
    # Mock the independent session context manager
    async def mock_session_context():
        return independent_db
    
    manager._get_independent_session = MagicMock()
    manager._get_independent_session.return_value.__aenter__ = AsyncMock(return_value=independent_db)
    manager._get_independent_session.return_value.__aexit__ = AsyncMock(return_value=None)
    
    # Call the method
    await manager._record_worker_run(
        worker_id="worker-123",
        task_id="task-1",
        start_time=datetime.now(timezone.utc).timestamp(),
        duration=60.0,
        succeeded=True,
        exit_code=0,
        summary="Test run"
    )
    
    # Verify retry behavior
    assert independent_db.commit.call_count == 3  # 2 failures + 1 success
    assert independent_db.rollback.call_count == 2  # 2 rollbacks for failures


@pytest.mark.asyncio
async def test_persist_reflection_output_retry_on_lock():
    """Test that _persist_reflection_output retries on database lock."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch
    
    # Create a mock session
    db = AsyncMock(spec=AsyncSession)
    
    # Mock the independent session
    independent_db = AsyncMock(spec=AsyncSession)
    
    # Fail first 1 time, succeed on 2nd
    independent_db.commit = AsyncMock(side_effect=[
        Exception("database is locked"),
        None  # Success
    ])
    independent_db.rollback = AsyncMock()
    
    # Create manager
    manager = WorkerManager(db)
    
    # Mock the independent session context manager
    manager._get_independent_session = MagicMock()
    manager._get_independent_session.return_value.__aenter__ = AsyncMock(return_value=independent_db)
    manager._get_independent_session.return_value.__aexit__ = AsyncMock(return_value=None)
    
    # Mock _persist_reflection_output_impl
    manager._persist_reflection_output_impl = AsyncMock()
    
    # Call the method
    await manager._persist_reflection_output(
        agent_type="programmer",
        reflection_label="test",
        reflection_type="strategic",
        summary="Test reflection",
        succeeded=True
    )
    
    # Verify retry behavior
    assert independent_db.commit.call_count == 2  # 1 failure + 1 success
    assert independent_db.rollback.call_count == 1  # 1 rollback for failure
