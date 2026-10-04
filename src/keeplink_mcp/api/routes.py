"""FastAPI route handlers for the internal archive task API.

Implements the endpoints:
- POST /api/archive: Submit a URL for archiving (with dedup)
- POST /api/cite: Archive a cited source and return a structured citation
- POST /api/retry: Re-queue a permanently failed task (failed -> pending)
- GET /api/status/{task_id}: Query task status by ID
- GET /api/status: Query most recent task by URL

Corresponds to Requirements 4.1, 4.2, 4.3, 4.4, 2.1, 2.2.
"""

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from keeplink_mcp.api.schemas import (
    ArchiveRequest,
    ArchiveResponse,
    CitationResponse,
    CiteRequest,
    ErrorResponse,
    RetryRequest,
    RetryResponse,
    TaskStatusResponse,
)
from keeplink_mcp.citation.builder import build_citation, normalize_title
from keeplink_mcp.db.models import TaskStatus
from keeplink_mcp.db.repository import RetryRejected, TaskRepository
from keeplink_mcp.mcp_server.url_validator import ValidationError, validate_url

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


@router.post(
    "/cite",
    response_model=CitationResponse,
    responses={422: {"model": ErrorResponse}},
)
async def create_citation(
    request: CiteRequest,
    session: SessionDep,
) -> CitationResponse | ErrorResponse:
    """Archive a cited source and return a structured citation.

    Flow (mirrors create_archive for validation + dedup):
      1. normalize title (whitespace-only -> None)
      2. validate_url -> 422 on failure (fail-fast); no task created
      3. find_recent_task for dedup
      4. classify state (cache-hit complete vs pending) and reuse/create task
      5. build Citation via Citation_Builder and return it

    Reads the DB and calls pure functions only: no page fetch, no LLM.
    """
    # Normalize the caller-supplied title up front so both branches share the
    # same rule (whitespace-only -> None).
    title = normalize_title(request.title)

    # Validate URL — reject invalid input immediately (fail-fast), never
    # creating a task on failure.
    try:
        validated_url = validate_url(request.url)
    except ValidationError as exc:
        return JSONResponse(
            status_code=422, content={"detail": f"Invalid URL: {exc}"}
        )

    repo = TaskRepository(session)

    # Dedup check: reuse an existing pending/processing/success task within the
    # 24h window rather than creating a new one.
    existing = await repo.find_recent_task(validated_url)
    if existing is not None:
        # Cache-hit COMPLETE only when the task actually succeeded and has a
        # result_url; otherwise (pending/processing) the citation stays pending
        # while archiving continues in the background.
        if existing.status == TaskStatus.SUCCESS and existing.result_url:
            archived_url = existing.result_url
            archived_at = existing.updated_at
        else:
            archived_url = None
            archived_at = None

        citation = build_citation(
            title=title,
            original_url=existing.url,
            task_id=existing.task_id,
            archived_url=archived_url,
            archived_at=archived_at,
            format=request.format,
        )
        return CitationResponse(
            title=citation.title,
            original_url=citation.original_url,
            archived_url=citation.archived_url,
            archived_at=citation.archived_at,
            task_id=citation.task_id,
            formatted=citation.formatted,
        )

    # No dedup hit — create a new task so the existing Worker, rate limiter, and
    # retry pipeline process it. The citation is necessarily pending.
    task = await repo.create_task(validated_url)
    citation = build_citation(
        title=title,
        original_url=task.url,
        task_id=task.task_id,
        archived_url=None,
        archived_at=None,
        format=request.format,
    )
    return CitationResponse(
        title=citation.title,
        original_url=citation.original_url,
        archived_url=citation.archived_url,
        archived_at=citation.archived_at,
        task_id=citation.task_id,
        formatted=citation.formatted,
    )


@router.post(
    "/retry",
    response_model=RetryResponse,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def retry_task_endpoint(
    request: RetryRequest,
    session: SessionDep,
) -> RetryResponse | ErrorResponse:
    """Re-queue a permanently failed task (failed -> pending).

    Mirrors create_archive in structure. Delegates the state transition to
    TaskRepository.retry_task and maps its three-way result to HTTP:
      - ArchiveTask   -> 200 RetryResponse(task_id, status='pending') (Req 2.4)
      - RetryRejected -> 409 with detail naming the current status and stating
                         only failed -> pending is allowed (Req 2.6, 2.9)
      - None          -> 404 not-found (Req 2.7)
    """
    repo = TaskRepository(session)
    result = await repo.retry_task(request.task_id)

    # Task exists but is not in 'failed' status: reject without mutation and
    # report which status blocked the transition (Req 2.6, 2.9).
    if isinstance(result, RetryRejected):
        return JSONResponse(
            status_code=409,
            content={
                "detail": (
                    f"Cannot retry task in status '{result.current_status.value}'; "
                    "only failed -> pending is allowed"
                )
            },
        )

    # Task not found (Req 2.7).
    if result is None:
        return JSONResponse(status_code=404, content={"detail": "Task not found"})

    # Transition succeeded: the task was reset to a clean pending state (Req 2.4).
    return RetryResponse(task_id=result.task_id, status=result.status.value)


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
