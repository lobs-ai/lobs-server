"""Database retry utilities for handling 'database is locked' errors.

Provides retry-with-exponential-backoff helpers for common DB operations.
Used to handle high-frequency writes that may hit SQLite lock contention.
"""

import asyncio
import logging
from typing import Callable, TypeVar, Any, Optional

logger = logging.getLogger(__name__)

T = TypeVar('T')


async def execute_with_retry(
    operation: Callable[[], Any],
    operation_name: str = "database operation",
    max_attempts: int = 5,
    initial_backoff_seconds: float = 0.5,
    on_failure_callback: Optional[Callable[[], Any]] = None,
) -> Any:
    """
    Execute a database operation with retry-on-lock logic.
    
    Args:
        operation: Async function to execute
        operation_name: Human-readable name for logging
        max_attempts: Maximum number of attempts (default 5)
        initial_backoff_seconds: Initial backoff delay (default 0.5s)
        on_failure_callback: Optional async callback to run on each failure (e.g., rollback)
        
    Returns:
        The result of the operation
        
    Raises:
        The last exception if all attempts fail
    """
    last_error = None
    
    for attempt in range(max_attempts):
        try:
            if attempt > 0:
                # Exponential backoff: 0.5, 1.0, 1.5, 2.0, 2.5...
                delay = initial_backoff_seconds * attempt
                await asyncio.sleep(delay)
            
            return await operation()
            
        except Exception as e:
            last_error = e
            
            if attempt < max_attempts - 1:
                logger.debug(
                    "[DB_RETRY] %s failed (attempt %d/%d): %s, retrying...",
                    operation_name, attempt + 1, max_attempts, e
                )
                
                # Call failure callback (e.g., rollback)
                if on_failure_callback:
                    try:
                        await on_failure_callback()
                    except Exception as cb_error:
                        logger.debug("[DB_RETRY] Failure callback failed: %s", cb_error)
            else:
                logger.error(
                    "[DB_RETRY] %s failed after %d attempts: %s",
                    operation_name, max_attempts, e,
                    exc_info=True
                )
    
    raise last_error


async def query_with_retry(
    db_session: Any,
    query_fn: Callable[[], Any],
    query_name: str = "database query",
    max_attempts: int = 5,
) -> Any:
    """
    Execute a database query with retry-on-lock logic.
    
    Args:
        db_session: AsyncSession instance
        query_fn: Async function that performs the query (takes db as argument)
        query_name: Human-readable name for logging
        max_attempts: Maximum number of attempts
        
    Returns:
        Query result
    """
    async def _execute():
        return await query_fn(db_session)
    
    async def _rollback():
        try:
            await db_session.rollback()
        except Exception:
            pass
    
    return await execute_with_retry(
        _execute,
        operation_name=query_name,
        max_attempts=max_attempts,
        on_failure_callback=_rollback,
    )


async def commit_with_retry(
    db_session: Any,
    commit_name: str = "database commit",
    max_attempts: int = 5,
) -> None:
    """
    Commit a database transaction with retry-on-lock logic.
    
    Args:
        db_session: AsyncSession instance
        commit_name: Human-readable name for logging
        max_attempts: Maximum number of attempts
    """
    async def _execute():
        await db_session.commit()
    
    async def _rollback():
        try:
            await db_session.rollback()
        except Exception:
            pass
    
    await execute_with_retry(
        _execute,
        operation_name=commit_name,
        max_attempts=max_attempts,
        on_failure_callback=_rollback,
    )
