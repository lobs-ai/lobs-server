"""Tests for database retry utilities."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.utils.db_retry import execute_with_retry, query_with_retry, commit_with_retry


class TestExecuteWithRetry:
    """Test execute_with_retry function."""
    
    @pytest.mark.asyncio
    async def test_succeeds_first_attempt(self):
        """Test successful execution on first attempt."""
        operation = AsyncMock(return_value="success")
        result = await execute_with_retry(operation, operation_name="test op")
        
        assert result == "success"
        assert operation.call_count == 1
    
    @pytest.mark.asyncio
    async def test_retries_and_succeeds(self):
        """Test retry logic - fails first 2 times, succeeds on 3rd."""
        operation = AsyncMock(side_effect=[
            Exception("database is locked"),
            Exception("database is locked"),
            "success"
        ])
        
        result = await execute_with_retry(operation, operation_name="test op", max_attempts=5)
        
        assert result == "success"
        assert operation.call_count == 3
    
    @pytest.mark.asyncio
    async def test_gives_up_after_max_attempts(self):
        """Test that execution gives up after max attempts."""
        operation = AsyncMock(side_effect=Exception("database is locked"))
        
        with pytest.raises(Exception, match="database is locked"):
            await execute_with_retry(operation, operation_name="test op", max_attempts=3)
        
        assert operation.call_count == 3
    
    @pytest.mark.asyncio
    async def test_calls_failure_callback(self):
        """Test that failure callback is called on retries."""
        operation = AsyncMock(side_effect=[
            Exception("database is locked"),
            "success"
        ])
        failure_callback = AsyncMock()
        
        result = await execute_with_retry(
            operation,
            operation_name="test op",
            on_failure_callback=failure_callback
        )
        
        assert result == "success"
        assert failure_callback.call_count == 1  # Called after first failure
    
    @pytest.mark.asyncio
    async def test_exponential_backoff(self):
        """Test exponential backoff delays."""
        operation = AsyncMock(side_effect=[
            Exception("database is locked"),
            Exception("database is locked"),
            "success"
        ])
        
        sleep_delays = []
        original_sleep = asyncio.sleep
        
        async def mock_sleep(delay):
            sleep_delays.append(delay)
        
        # Monkey patch asyncio.sleep
        import app.utils.db_retry as db_retry_module
        original_module_sleep = db_retry_module.asyncio.sleep
        db_retry_module.asyncio.sleep = mock_sleep
        
        try:
            result = await execute_with_retry(operation, operation_name="test op", max_attempts=5)
            assert result == "success"
            # First retry has delay of 0.5 * 1 = 0.5, second has 0.5 * 2 = 1.0
            assert sleep_delays == [0.5, 1.0]
        finally:
            # Restore original sleep
            db_retry_module.asyncio.sleep = original_module_sleep
    
    @pytest.mark.asyncio
    async def test_custom_initial_backoff(self):
        """Test custom initial backoff value."""
        operation = AsyncMock(side_effect=[
            Exception("locked"),
            "success"
        ])
        
        sleep_delays = []
        
        async def mock_sleep(delay):
            sleep_delays.append(delay)
        
        import app.utils.db_retry as db_retry_module
        original_sleep = db_retry_module.asyncio.sleep
        db_retry_module.asyncio.sleep = mock_sleep
        
        try:
            result = await execute_with_retry(
                operation,
                operation_name="test op",
                initial_backoff_seconds=0.1,
                max_attempts=3
            )
            assert result == "success"
            # First retry has delay of 0.1 * 1 = 0.1
            assert sleep_delays == [0.1]
        finally:
            db_retry_module.asyncio.sleep = original_sleep


class TestQueryWithRetry:
    """Test query_with_retry function."""
    
    @pytest.mark.asyncio
    async def test_executes_query_successfully(self):
        """Test successful query execution."""
        db_session = AsyncMock()
        query_fn = AsyncMock(return_value={"count": 42})
        
        result = await query_with_retry(db_session, query_fn, query_name="test query")
        
        assert result == {"count": 42}
        assert query_fn.call_count == 1
    
    @pytest.mark.asyncio
    async def test_retries_query_on_failure(self):
        """Test query retry on failure."""
        db_session = AsyncMock()
        query_fn = AsyncMock(side_effect=[
            Exception("database is locked"),
            {"count": 42}
        ])
        
        result = await query_with_retry(db_session, query_fn, query_name="test query")
        
        assert result == {"count": 42}
        assert query_fn.call_count == 2
        assert db_session.rollback.call_count == 1


class TestCommitWithRetry:
    """Test commit_with_retry function."""
    
    @pytest.mark.asyncio
    async def test_commits_successfully(self):
        """Test successful commit."""
        db_session = AsyncMock()
        
        await commit_with_retry(db_session, commit_name="test commit")
        
        assert db_session.commit.call_count == 1
        assert db_session.rollback.call_count == 0
    
    @pytest.mark.asyncio
    async def test_retries_commit_on_lock(self):
        """Test commit retry on lock."""
        db_session = AsyncMock()
        db_session.commit = AsyncMock(side_effect=[
            Exception("database is locked"),
            None  # Success
        ])
        
        await commit_with_retry(db_session, commit_name="test commit")
        
        assert db_session.commit.call_count == 2
        assert db_session.rollback.call_count == 1
    
    @pytest.mark.asyncio
    async def test_gives_up_after_max_attempts(self):
        """Test that commit gives up after max attempts."""
        db_session = AsyncMock()
        db_session.commit = AsyncMock(side_effect=Exception("database is locked"))
        
        with pytest.raises(Exception, match="database is locked"):
            await commit_with_retry(db_session, commit_name="test commit", max_attempts=3)
        
        assert db_session.commit.call_count == 3
        # Rollback is called on each failure BEFORE the final attempt, so 2 rollbacks for 3 attempts
        assert db_session.rollback.call_count == 2
