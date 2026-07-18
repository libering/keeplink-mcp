"""FastAPI route handlers for the internal archive task API.

Implements three endpoints:
- POST /api/archive: Submit a URL for archiving (with dedup)
- GET /api/status/{task_id}: Query task status by ID
- GET /api/status: Query most recent task by URL

Corresponds to Requirements 4.1, 4.2, 4.3, 4.4, 2.1, 2.2.
"""

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from omniarchive_mcp.api.schemas import (
    ArchiveRequest,
    ArchiveResponse,
    ErrorResponse,
    TaskStatusResponse,
)
from omniarchive_mcp.db.repository import TaskRepository
from omniarchive_mcp.mcp_server.url_validator import ValidationError, validate_url

router = APIRouter(prefix="/api")

# Module-level session factory, set by app.py during startup via set_session_factory()
_session_factory = None


def set_session_factory(factory) -> None:
    """Inject the async session factory at application startup.

    Args:
        factory: A sessionmaker bound to an AsyncEngine.
    """
    global _session_factory  # noqa: PLW0603
    _session_factory = factory


async def _get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async DB session."""
    if _session_factory is None:
        raise RuntimeError("Session factory not initialized")
    async with _session_factory() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(_get_session)]


@router.post(
    "/archive",
    response_model=ArchiveResponse,
    responses={422: {"model": ErrorResponse}},
)
async def create_archive(
    request: ArchiveRequest,
    response: Response,
    session: SessionDep,
) -> ArchiveResponse | ErrorResponse:
    """Submit a URL for archiving with deduplication check.

    Validates the URL, checks for existing tasks within the dedup window,
    and either returns an existing task or creates a new one.
    """
    # Validate URL — reject invalid input immediately (fail-fast)
    try:
        validated_url = validate_url(request.url)
    except ValidationError as exc:
        return JSONResponse(
            status_code=422, content={"detail": f"Invalid URL: {exc}"}
        )

    repo = TaskRepository(session)

    # Dedup check: reuse existing pending/processing/success task
    existing = await repo.find_recent_task(validated_url)
    if existing is not None:
        return ArchiveResponse(
            task_id=existing.task_id,
            url=existing.url,
            status=existing.status.value,
            result_url=existing.result_url,
            created_at=existing.created_at,
            is_deduplicated=True,
        )

    # No dedup hit — create new task
    task = await repo.create_task(validated_url)
    response.status_code = 201
    return ArchiveResponse(
        task_id=task.task_id,
        url=task.url,
        status=task.status.value,
        result_url=task.result_url,
        created_at=task.created_at,
        is_deduplicated=False,
    )


@router.get(
    "/status/{task_id}",
    response_model=TaskStatusResponse,
    responses={404: {"model": ErrorResponse}},
)
async def get_status_by_id(
    task_id: str,
    response: Response,
    session: SessionDep,
) -> TaskStatusResponse | ErrorResponse:
    """Query task status by task_id."""
    repo = TaskRepository(session)
    task = await repo.get_task(task_id)

    if task is None:
        return JSONResponse(status_code=404, content={"detail": "Task not found"})

    return TaskStatusResponse(
        task_id=task.task_id,
        url=task.url,
        status=task.status.value,
        result_url=task.result_url,
        error_message=task.error_message,
        retry_count=task.retry_count,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


@router.get(
    "/status",
    response_model=TaskStatusResponse,
    responses={404: {"model": ErrorResponse}},
)
async def get_status_by_url(
    url: Annotated[str, Query(description="Target URL to look up")],
    response: Response,
    session: SessionDep,
) -> TaskStatusResponse | ErrorResponse:
    """Query the most recent task for a given URL (includes all statuses)."""
    repo = TaskRepository(session)
    task = await repo.get_latest_task_by_url(url)

    if task is None:
        return JSONResponse(
            status_code=404, content={"detail": "No tasks found for this URL"}
        )

    return TaskStatusResponse(
        task_id=task.task_id,
        url=task.url,
        status=task.status.value,
        result_url=task.result_url,
        error_message=task.error_message,
        retry_count=task.retry_count,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )
