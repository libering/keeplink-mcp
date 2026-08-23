"""Batch status query endpoint for multiple tasks/URLs in a single request.

Implements GET /api/status/batch route that allows querying status of multiple
tasks by their IDs or URLs in a single request, reducing round-trip overhead.

Corresponds to Requirements 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8, 7.9.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from keeplink_mcp.api.routes import _get_session
from keeplink_mcp.api.schemas import (
    ErrorResponse,
    TaskStatusResponse,
)
from keeplink_mcp.db.repository import TaskRepository

router = APIRouter(prefix="/api")

SessionDep = Annotated[AsyncSession, Depends(_get_session)]

MAX_BATCH_IDENTIFIERS = 50


class BatchStatusResponse(BaseModel):
    """Response model for GET /api/status/batch."""

    results: list[TaskStatusResponse]
    total_requested: int
    total_found: int


def _parse_comma_separated(value: str | None) -> list[str]:
    """Parse a comma-separated string into a list of non-empty strings.

    Args:
        value: Comma-separated string or None.

    Returns:
        List of trimmed, non-empty strings.
    """
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


@router.get(
    "/status/batch",
    response_model=BatchStatusResponse,
    responses={422: {"model": ErrorResponse}},
)
async def get_batch_status(
    task_ids: Annotated[
        str | None,
        Query(
            description="Comma-separated task IDs (max combined 50 with url)",
        ),
    ] = None,
    url: Annotated[
        str | None,
        Query(
            description="Comma-separated URLs (max combined 50 with task_ids)",
        ),
    ] = None,
    session: SessionDep = None,
) -> BatchStatusResponse | JSONResponse:
    """Query status of multiple tasks by IDs and/or URLs in a single request.

    Accepts comma-separated task_ids and/or url query parameters, validates
    the combined count does not exceed 50, and returns partial results for
    matching identifiers (non-matching are silently omitted).

    Returns HTTP 422 if:
    - Neither task_ids nor url is provided
    - Combined identifier count exceeds 50

    Args:
        task_ids: Comma-separated task ID strings.
        url: Comma-separated URL strings.
        session: Async DB session dependency.

    Returns:
        BatchStatusResponse containing:
        - results: List of matching task status objects
        - total_requested: Total number of identifiers requested
        - total_found: Number of matching tasks found
    """
    # Parse query parameters
    task_id_list = _parse_comma_separated(task_ids)
    url_list = _parse_comma_separated(url)

    # Validate at least one parameter is provided (AC 7.5)
    if not task_id_list and not url_list:
        return JSONResponse(
            status_code=422,
            content={
                "detail": "At least one query parameter (task_ids or url) is required"
            },
        )

    # Validate combined count does not exceed limit (AC 7.6)
    total_requested = len(task_id_list) + len(url_list)
    if total_requested > MAX_BATCH_IDENTIFIERS:
        return JSONResponse(
            status_code=422,
            content={
                "detail": (
                    f"Maximum of {MAX_BATCH_IDENTIFIERS} identifiers "
                    f"per request, got {total_requested}"
                )
            },
        )

    repo = TaskRepository(session)

    # Query tasks by IDs (AC 7.2)
    tasks_by_id = await repo.get_tasks_by_ids(task_id_list) if task_id_list else []

    # Query tasks by URLs (AC 7.3)
    tasks_by_url = await repo.get_latest_tasks_by_urls(url_list) if url_list else []

    # Merge and deduplicate results (AC 7.4)
    # Use task_id as dedup key since same task could match both ID and URL queries
    seen_task_ids: set[str] = set()
    merged_results: list[TaskStatusResponse] = []

    for task in tasks_by_id + tasks_by_url:
        if task.task_id in seen_task_ids:
            continue
        seen_task_ids.add(task.task_id)

        merged_results.append(
            TaskStatusResponse(
                task_id=task.task_id,
                url=task.url,
                status=task.status.value,
                result_url=task.result_url,
                error_message=task.error_message,
                retry_count=task.retry_count,
                created_at=task.created_at,
                updated_at=task.updated_at,
            )
        )

    # Build response (AC 7.7, 7.9)
    return BatchStatusResponse(
        results=merged_results,
        total_requested=total_requested,
        total_found=len(merged_results),
    )
