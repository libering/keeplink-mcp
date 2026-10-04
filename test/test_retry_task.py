# Feature: keeplink-v1x-improvements
"""Example (concrete-value) unit tests for improvement C — retry_task.

Complements the property-based tests (test_retry_repository_properties.py) and
the integration test (test_retry_integration.py) with concrete, example-based
assertions across the three layers touched by improvement C:

1. Tool registration (Req 2.1): list_tools() exposes retry_task with
   inputSchema.required == ["task_id"].
2. Endpoint exists (Req 2.2): POST /api/retry is routable.
3. Not-found -> 404 (Req 2.7).
4. Missing task_id in the tool -> {"error": "task_id is required"} (Req 2.1).
5. Backend down (injected httpx.ConnectError) -> backend-unavailable (Req 2.8).
6. A concrete non-failed rejection: create a task in a non-failed status, POST
   /api/retry, assert 409 and detail contains the current status string.

Follows the conventions in test_archive_and_cite_tool.py (respx.mock + direct
handler import for the tool layer) and test_cite_endpoint.py (in-memory SQLite
session_factory + ASGI test client for the endpoint layer).
"""

import json

import httpx
import mcp.types as mcp_types
import pytest
import respx
from httpx import ASGITransport, AsyncClient

from keeplink_mcp.api.app import create_app
from keeplink_mcp.config import Config
from keeplink_mcp.db.models import ArchiveTask, TaskStatus
from keeplink_mcp.mcp_server.server import (
    _handle_retry_task,
    create_mcp_server,
)

BASE_URL = "http://127.0.0.1:9210"


# ---------------------------------------------------------------------------
# Tool-layer helpers (mirror test_archive_and_cite_tool.py)
# ---------------------------------------------------------------------------


async def _list_tools() -> list[mcp_types.Tool]:
    """Invoke the server's registered list_tools handler and return the tools."""
    server = create_mcp_server(Config())
    handler = server.request_handlers[mcp_types.ListToolsRequest]
    result = await handler(mcp_types.ListToolsRequest(method="tools/list", params=None))
    return result.root.tools


def _find_tool(tools: list[mcp_types.Tool], name: str) -> mcp_types.Tool | None:
    """Return the tool with the given name, or None if not registered."""
    return next((tool for tool in tools if tool.name == name), None)


# ---------------------------------------------------------------------------
# Endpoint-layer fixtures/helpers (mirror test_cite_endpoint.py)
# ---------------------------------------------------------------------------


@pytest.fixture
async def client(session_factory):
    """Create an httpx.AsyncClient bound to the FastAPI app under test."""
    app = create_app(session_factory)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _insert_task(session_factory, *, task_id: str, status: TaskStatus) -> None:
    """Insert an ArchiveTask with the given task_id and status."""
    async with session_factory() as session:
        session.add(
            ArchiveTask(
                task_id=task_id,
                url=f"https://example.com/{task_id}",
                status=status,
            )
        )
        await session.commit()


# ---------------------------------------------------------------------------
# Req 2.1 — Tool registration: retry_task with required == ["task_id"]
# ---------------------------------------------------------------------------


class TestRetryTaskRegistration:
    """list_tools() registers retry_task with the expected schema."""

    async def test_tool_is_registered(self):
        """**Validates: Requirements 2.1**"""
        tools = await _list_tools()
        assert _find_tool(tools, "retry_task") is not None

    async def test_task_id_is_the_only_required_param(self):
        """**Validates: Requirements 2.1**

        inputSchema.required is exactly ["task_id"], and task_id is a string.
        """
        tool = _find_tool(await _list_tools(), "retry_task")
        assert tool is not None
        assert tool.inputSchema["required"] == ["task_id"]
        assert tool.inputSchema["properties"]["task_id"]["type"] == "string"


# ---------------------------------------------------------------------------
# Req 2.2 — Endpoint exists: POST /api/retry is routable
# ---------------------------------------------------------------------------


class TestRetryEndpointRoutable:
    """POST /api/retry is a real route (not a 404 from an unregistered path)."""

    async def test_route_is_registered_in_app(self, client: AsyncClient):
        """**Validates: Requirements 2.2**

        A POST with a valid body reaches the retry handler. Even for a
        non-existent task the endpoint returns its own not-found (404 with a
        JSON `detail`), proving the route is wired — as opposed to POSTing to an
        undefined path (which would surface FastAPI's default not-found shape).
        """
        resp = await client.post("/api/retry", json={"task_id": "does-not-exist"})

        # The route exists and handled the request through retry_task_endpoint.
        assert resp.status_code == 404
        assert resp.json() == {"detail": "Task not found"}

    async def test_retry_path_exists_but_rejects_get(self, client: AsyncClient):
        """**Validates: Requirements 2.2**

        /api/retry is registered as a POST route: a GET to the same path is
        rejected with 405 Method Not Allowed (the path resolves to a route) —
        distinct from 404, which an unregistered path would return.
        """
        resp = await client.get("/api/retry")
        assert resp.status_code == 405


# ---------------------------------------------------------------------------
# Req 2.7 — Not-found task -> 404
# ---------------------------------------------------------------------------


class TestRetryNotFound:
    """Retrying an unknown task_id returns a not-found error."""

    async def test_unknown_task_returns_404(self, client: AsyncClient):
        """**Validates: Requirements 2.7**"""
        resp = await client.post("/api/retry", json={"task_id": "missing-task-id"})

        assert resp.status_code == 404
        assert resp.json()["detail"] == "Task not found"


# ---------------------------------------------------------------------------
# Req 2.6 — Concrete non-failed rejection: 409 with current status in detail
# ---------------------------------------------------------------------------


class TestRetryNonFailedRejected:
    """A non-failed task cannot be retried; the 409 detail names its status."""

    async def test_success_task_rejected_with_409_and_status_in_detail(
        self, client: AsyncClient, session_factory
    ):
        """**Validates: Requirements 2.6**

        A `success` task (a concrete non-failed status) is rejected with HTTP
        409 and the detail message contains the current status string
        ("success").
        """
        await _insert_task(
            session_factory, task_id="done-task", status=TaskStatus.SUCCESS
        )

        resp = await client.post("/api/retry", json={"task_id": "done-task"})

        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert "success" in detail
        # The message also states the only permitted transition (fail-fast).
        assert "failed -> pending" in detail


# ---------------------------------------------------------------------------
# Req 2.1 — Tool handler: missing task_id -> error, no POST
# ---------------------------------------------------------------------------


class TestRetryToolMissingTaskId:
    """The tool handler rejects a missing task_id before contacting the backend."""

    @respx.mock
    async def test_missing_task_id_returns_error_without_posting(self):
        """**Validates: Requirements 2.1**"""
        route = respx.post(f"{BASE_URL}/api/retry").mock(
            return_value=httpx.Response(200, json={"task_id": "x", "status": "pending"})
        )

        result = await _handle_retry_task({}, BASE_URL)

        assert len(result) == 1
        assert json.loads(result[0].text) == {"error": "task_id is required"}
        # No POST was made because task_id was absent (fail-fast).
        assert not route.called


# ---------------------------------------------------------------------------
# Req 2.8 — Backend down (httpx.ConnectError) -> backend-unavailable
# ---------------------------------------------------------------------------


class TestRetryToolBackendUnavailable:
    """A backend connection failure yields a backend-unavailable error."""

    @respx.mock
    async def test_connect_error_returns_backend_unavailable(self):
        """**Validates: Requirements 2.8**

        When the backend is unreachable (httpx.ConnectError), the tool returns
        {"error": "Backend service unavailable"} rather than raising.
        """
        respx.post(f"{BASE_URL}/api/retry").mock(
            side_effect=httpx.ConnectError("Connection refused")
        )

        result = await _handle_retry_task({"task_id": "any-task"}, BASE_URL)

        assert len(result) == 1
        assert json.loads(result[0].text) == {"error": "Backend service unavailable"}
