"""Integration tests for MCP Server tool handlers.

# Feature: keeplink-mcp

Tests the _handle_archive_url and _handle_get_archive_status handlers
by mocking the internal FastAPI HTTP calls with respx.
"""

import json

import httpx
import respx

from keeplink_mcp.mcp_server.server import (
    _handle_archive_url,
    _handle_get_archive_status,
)

BASE_URL = "http://127.0.0.1:9210"


# ---------------------------------------------------------------------------
# archive_url tests
# ---------------------------------------------------------------------------


class TestHandleArchiveUrl:
    """Tests for _handle_archive_url handler."""

    @respx.mock
    async def test_successful_submission(self):
        """正常提交 → mock POST 返回 201 → 驗證回傳 task_id + status."""
        respx.post(f"{BASE_URL}/api/archive").mock(
            return_value=httpx.Response(
                201,
                json={
                    "task_id": "abc123",
                    "url": "https://example.com",
                    "status": "pending",
                    "created_at": "2024-01-01T00:00:00Z",
                    "is_deduplicated": False,
                },
            )
        )

        result = await _handle_archive_url({"url": "https://example.com"}, BASE_URL)

        assert len(result) == 1
        data = json.loads(result[0].text)
        assert data["task_id"] == "abc123"
        assert data["status"] == "pending"
        assert data["url"] == "https://example.com"

    @respx.mock
    async def test_url_validation_failure(self):
        """URL 驗證失敗 → 不呼叫 FastAPI → 返回 error."""
        # No route mocked — any call would raise
        result = await _handle_archive_url({"url": "ftp://invalid"}, BASE_URL)

        assert len(result) == 1
        data = json.loads(result[0].text)
        assert "error" in data
        assert "Invalid URL format" in data["error"]

    @respx.mock
    async def test_backend_unavailable(self):
        """FastAPI 不可用 → ConnectError → 返回 "Backend service unavailable"."""
        respx.post(f"{BASE_URL}/api/archive").mock(
            side_effect=httpx.ConnectError("Connection refused")
        )

        result = await _handle_archive_url({"url": "https://example.com"}, BASE_URL)

        assert len(result) == 1
        data = json.loads(result[0].text)
        assert data["error"] == "Backend service unavailable"

    @respx.mock
    async def test_dedup_hit(self):
        """去重命中 → mock POST 返回 200 → 返回 existing task_id."""
        respx.post(f"{BASE_URL}/api/archive").mock(
            return_value=httpx.Response(
                200,
                json={
                    "task_id": "existing-id",
                    "url": "https://example.com",
                    "status": "success",
                    "result_url": "https://web.archive.org/web/20240101/https://example.com",
                    "created_at": "2024-01-01T00:00:00Z",
                    "is_deduplicated": True,
                },
            )
        )

        result = await _handle_archive_url({"url": "https://example.com"}, BASE_URL)

        assert len(result) == 1
        data = json.loads(result[0].text)
        assert data["task_id"] == "existing-id"
        assert data["status"] == "success"
        assert data["url"] == "https://example.com"


# ---------------------------------------------------------------------------
# get_archive_status tests
# ---------------------------------------------------------------------------


class TestHandleGetArchiveStatus:
    """Tests for _handle_get_archive_status handler."""

    @respx.mock
    async def test_query_by_task_id(self):
        """按 task_id 查詢成功 → mock GET 返回 200."""
        respx.get(f"{BASE_URL}/api/status/abc123").mock(
            return_value=httpx.Response(
                200,
                json={
                    "task_id": "abc123",
                    "url": "https://example.com",
                    "status": "success",
                    "result_url": "https://web.archive.org/web/20240101/https://example.com",
                    "error_message": None,
                    "retry_count": 0,
                    "created_at": "2024-01-01T00:00:00Z",
                    "updated_at": "2024-01-01T00:00:05Z",
                },
            )
        )

        result = await _handle_get_archive_status({"task_id": "abc123"}, BASE_URL)

        assert len(result) == 1
        data = json.loads(result[0].text)
        assert data["task_id"] == "abc123"
        assert data["status"] == "success"
        assert data["result_url"] == "https://web.archive.org/web/20240101/https://example.com"

    @respx.mock
    async def test_query_by_url(self):
        """按 URL 查詢成功 → mock GET 返回 200."""
        respx.get(f"{BASE_URL}/api/status").mock(
            return_value=httpx.Response(
                200,
                json={
                    "task_id": "xyz789",
                    "url": "https://example.com/page",
                    "status": "pending",
                    "result_url": None,
                    "error_message": None,
                    "retry_count": 0,
                    "created_at": "2024-01-01T00:00:00Z",
                    "updated_at": "2024-01-01T00:00:00Z",
                },
            )
        )

        result = await _handle_get_archive_status(
            {"url": "https://example.com/page"}, BASE_URL
        )

        assert len(result) == 1
        data = json.loads(result[0].text)
        assert data["task_id"] == "xyz789"
        assert data["status"] == "pending"

    @respx.mock
    async def test_not_found(self):
        """不存在 → mock GET 返回 404 → 返回 error message."""
        respx.get(f"{BASE_URL}/api/status/nonexist").mock(
            return_value=httpx.Response(
                404,
                json={"detail": "Task not found"},
            )
        )

        result = await _handle_get_archive_status({"task_id": "nonexist"}, BASE_URL)

        assert len(result) == 1
        data = json.loads(result[0].text)
        assert data["error"] == "Task not found"

    @respx.mock
    async def test_both_params_empty(self):
        """參數皆空 → 返回 "At least one of task_id or url is required"."""
        result = await _handle_get_archive_status({}, BASE_URL)

        assert len(result) == 1
        data = json.loads(result[0].text)
        assert data["error"] == "At least one of task_id or url is required"

    @respx.mock
    async def test_backend_unavailable(self):
        """服務不可用 → ConnectError → 返回 error."""
        respx.get(f"{BASE_URL}/api/status/abc123").mock(
            side_effect=httpx.ConnectError("Connection refused")
        )

        result = await _handle_get_archive_status({"task_id": "abc123"}, BASE_URL)

        assert len(result) == 1
        data = json.loads(result[0].text)
        assert data["error"] == "Backend service unavailable"
