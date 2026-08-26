"""Health check endpoint providing liveness, readiness, and operational stats.

This module implements the health check endpoint for KeepLink MCP, exposing
liveness, readiness, and operational statistics for monitoring systems.

Corresponds to Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from fastapi import APIRouter, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from sqlalchemy.orm import sessionmaker

logger = logging.getLogger(__name__)

# Module-level session factory, set by app.py during startup via set_health_session_factory()
_session_factory: sessionmaker | None = None

# Module-level reference to the worker's running flag, set during startup
_worker_running_flag: callable | None = None


def set_health_session_factory(factory: sessionmaker) -> None:
    """Inject the async session factory at application startup.

    Args:
        factory: A sessionmaker bound to an AsyncEngine.
    """
    global _session_factory  # noqa: PLW0603
    _session_factory = factory


def set_worker_running_flag(getter: callable) -> None:
    """Inject a function that returns the worker's _running flag.

    Args:
        getter: A callable that returns bool indicating if worker is running.
    """
    global _worker_running_flag  # noqa: PLW0603
    _worker_running_flag = getter


async def _get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async DB session."""
    if _session_factory is None:
        raise RuntimeError("Session factory not initialized")
    async with _session_factory() as session:
        yield session


class HealthStats(BaseModel):
    """Operational statistics included in health response.

    Attributes:
        queue_depth: Count of tasks in 'pending' status.
        success_count: Tasks with 'success' status within the time window (24h).
        failure_count: Tasks with 'failed' status within the time window (24h).
    """

    queue_depth: int
    success_count: int
    failure_count: int


class HealthResponse(BaseModel):
    """Response schema for GET /api/health.

    Attributes:
        liveness: Always True when the HTTP server is accepting connections.
        readiness: True only when DB is reachable and worker has started.
        readiness_error: Error description if readiness is False, None otherwise.
        stats: Operational statistics for the task queue.
    """

    liveness: bool
    readiness: bool
    readiness_error: str | None = None
    stats: HealthStats


# Create router with /api prefix
health_router = APIRouter(prefix="/api")


@health_router.get(
    "/health",
    response_model=HealthResponse,
)
async def health_check(response: Response) -> HealthResponse:
    """Compute health status by checking DB connectivity and counting tasks.

    Liveness: Always True (process is alive to respond).

    Readiness: True when:
    - Database connectivity check (SELECT 1) succeeds within 5000ms
    - Worker polling loop has executed at least one cycle (_running flag is True)

    Stats: queue_depth, success_count, failure_count from get_queue_stats().

    Returns HTTP 200 when both liveness and readiness are True.
    Returns HTTP 503 when either liveness or readiness is False.
    """
    liveness = True  # Process is alive if this handler executes

    readiness = True
    readiness_error: str | None = None
    stats = HealthStats(queue_depth=0, success_count=0, failure_count=0)

    # Check database connectivity
    db_ok, db_error = await _check_database_connectivity()
    if not db_ok:
        readiness = False
        readiness_error = db_error

    # Check worker running flag
    worker_ok = _check_worker_running()
    if not worker_ok:
        readiness = False
        if readiness_error is None:
            readiness_error = "Worker has not started or is not running"

    # Fetch stats only if DB is available
    if db_ok:
        stats = await _fetch_stats()

    # Set HTTP status code based on readiness
    if not readiness:
        response.status_code = 503
    else:
        response.status_code = 200

    return HealthResponse(
        liveness=liveness,
        readiness=readiness,
        readiness_error=readiness_error,
        stats=stats,
    )


async def _execute_db_check() -> None:
    """Execute a simple DB query to verify connectivity."""
    async with _session_factory() as session:
        result = await session.execute(text("SELECT 1"))
        result.scalar()


async def _check_database_connectivity() -> tuple[bool, str | None]:
    """Check database connectivity by executing SELECT 1 with a 5-second timeout.

    Returns:
        Tuple of (success, error_message). error_message is None if successful.
    """
    if _session_factory is None:
        return False, "Database session factory not initialized"

    try:
        await asyncio.wait_for(_execute_db_check(), timeout=5.0)
        return True, None

    except asyncio.TimeoutError:
        return False, "Database connectivity check timed out after 5000ms"
    except Exception as exc:
        return False, f"Database connectivity check failed: {type(exc).__name__}: {exc}"


def _check_worker_running() -> bool:
    """Check if the worker has started and is running.

    Returns:
        True if worker is running, False otherwise.
    """
    if _worker_running_flag is None:
        return False
    return _worker_running_flag()


async def _fetch_stats() -> HealthStats:
    """Fetch operational statistics from the database.

    Returns:
        HealthStats with queue_depth, success_count, failure_count.
    """
    from keeplink_mcp.db.repository import TaskRepository

    if _session_factory is None:
        return HealthStats(queue_depth=0, success_count=0, failure_count=0)

    try:
        async with _session_factory() as session:
            repo = TaskRepository(session)
            stats_dict = await repo.get_queue_stats(window_hours=24)
            return HealthStats(
                queue_depth=stats_dict.get("queue_depth", 0),
                success_count=stats_dict.get("success_count", 0),
                failure_count=stats_dict.get("failure_count", 0),
            )
    except Exception as exc:
        logger.warning(
            "Failed to fetch health stats",
            extra={"action": "health_stats_error", "error": str(exc)},
        )
        return HealthStats(queue_depth=0, success_count=0, failure_count=0)
