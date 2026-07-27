"""Pydantic request/response schemas for the internal FastAPI API.

These models define the HTTP API contract between the MCP Server and
the FastAPI Service, ensuring consistent serialization and validation
of archive task data.

Corresponds to Requirements 1.2 (archive_url response) and 2.1 (status response).
"""

from datetime import datetime

from pydantic import BaseModel


class ArchiveRequest(BaseModel):
    """Request body for POST /api/archive."""

    url: str


class ArchiveResponse(BaseModel):
    """Response for POST /api/archive (201 Created or 200 OK on dedup hit)."""

    task_id: str
    url: str
    status: str
    result_url: str | None = None
    created_at: datetime
    is_deduplicated: bool = False


class TaskStatusResponse(BaseModel):
    """Response for GET /api/status/{task_id} and GET /api/status?url=..."""

    task_id: str
    url: str
    status: str
    result_url: str | None = None
    error_message: str | None = None
    retry_count: int
    created_at: datetime
    updated_at: datetime


class ErrorResponse(BaseModel):
    """Standard error response body."""

    detail: str
