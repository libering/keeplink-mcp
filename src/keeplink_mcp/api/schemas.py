"""Pydantic request/response schemas for the internal FastAPI API.

These models define the HTTP API contract between the MCP Server and
the FastAPI Service, ensuring consistent serialization and validation
of archive task data.

Corresponds to Requirements 1.2 (archive_url response) and 2.1 (status response).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from keeplink_mcp.citation.builder import CitationFormat


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


class RetryRequest(BaseModel):
    """Request body for POST /api/retry."""

    task_id: str


class RetryResponse(BaseModel):
    """Response for POST /api/retry — the re-queued task's identity + new status."""

    task_id: str
    status: str  # always "pending" on success


class CiteRequest(BaseModel):
    """Request body for POST /api/cite."""

    url: str
    title: str | None = None  # whitespace-only is normalized to None at the endpoint
    # Optional; validated against CitationFormat so an invalid value -> 422.
    # Only affects `formatted`; defaults to markdown for backward compatibility.
    format: CitationFormat = CitationFormat.MARKDOWN


class CitationResponse(BaseModel):
    """Response for POST /api/cite — a structured, paste-ready citation.

    original_url, task_id, and formatted are always non-null. When archiving
    is still pending, archived_url and archived_at are both null.
    """

    title: str | None = None
    original_url: str
    archived_url: str | None = None
    archived_at: datetime | None = None
    task_id: str
    formatted: str
