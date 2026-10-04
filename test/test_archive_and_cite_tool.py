# Feature: archive-and-cite
"""Unit tests for the archive_and_cite MCP tool (tool layer).

Complements the property-based test (test_archive_and_cite_tool_properties.py)
with concrete, example-based assertions covering three tool-layer behaviours:

1. Tool registration (Req 1.1): list_tools() exposes archive_and_cite with a
   required string `url` and an optional string `title` in its inputSchema.

2. Forwarding (Req 1.2): the handler POSTs to /api/cite with the validated /
   normalized url in the JSON body, and returns the backend CitationResponse.

3. Backend unreachable (Req 1.5): an httpx.ConnectError from the backend yields
   a {"error": "Backend service unavailable"} result.

Follows the conventions in test_mcp_server.py: respx.mock for HTTP mocking,
async test methods (pytest asyncio_mode=auto), handlers imported directly.
"""

import json

import httpx
import mcp.types as mcp_types
import respx

from keeplink_mcp.config import Config
from keeplink_mcp.mcp_server.server import (
    _handle_archive_and_cite,
    create_mcp_server,
)

BASE_URL = "http://127.0.0.1:9210"


async def _list_tools() -> list[mcp_types.Tool]:
    """Invoke the server's registered list_tools handler and return the tools.

    The low-level MCP SDK stores the decorated handler in
    ``server.request_handlers`` keyed by request type. Invoking it directly is
    the supported way to exercise tool registration without a live transport.
    """
    server = create_mcp_server(Config())
    handler = server.request_handlers[mcp_types.ListToolsRequest]
    result = await handler(mcp_types.ListToolsRequest(method="tools/list", params=None))
    return result.root.tools


def _find_tool(tools: list[mcp_types.Tool], name: str) -> mcp_types.Tool | None:
    """Return the tool with the given name, or None if not registered."""
    return next((tool for tool in tools if tool.name == name), None)


# ---------------------------------------------------------------------------
# Req 1.1 — Tool registration
# ---------------------------------------------------------------------------


class TestArchiveAndCiteRegistration:
    """list_tools() registers archive_and_cite with the expected schema."""

    async def test_tool_is_registered(self):
        """**Validates: Requirements 1.1**

        archive_and_cite appears in the list of exposed MCP tools.
        """
        tools = await _list_tools()
        assert _find_tool(tools, "archive_and_cite") is not None

    async def test_url_is_the_only_required_param(self):
        """**Validates: Requirements 1.1**

        inputSchema.required is exactly ["url"] — url is mandatory.
        """
        tool = _find_tool(await _list_tools(), "archive_and_cite")
        assert tool is not None
        assert tool.inputSchema["required"] == ["url"]

    async def test_url_and_title_properties_present_title_optional(self):
        """**Validates: Requirements 1.1**

        Both `url` and `title` are declared string properties, and `title` is
        optional (present in properties but absent from required).
        """
        tool = _find_tool(await _list_tools(), "archive_and_cite")
        assert tool is not None
        properties = tool.inputSchema["properties"]

        assert "url" in properties
        assert properties["url"]["type"] == "string"

        assert "title" in properties
        assert properties["title"]["type"] == "string"

        # title is optional: declared but not required.
        assert "title" not in tool.inputSchema["required"]


# ---------------------------------------------------------------------------
# Req 1.2 — Forwarding behaviour
# ---------------------------------------------------------------------------


class TestArchiveAndCiteForwarding:
    """The handler forwards a POST to /api/cite with the normalized url."""

    @respx.mock
    async def test_posts_to_cite_endpoint_with_normalized_url(self):
        """**Validates: Requirements 1.2**

        A valid url is validated/normalized, then POSTed to /api/cite with the
        normalized url in the JSON body. The backend's complete CitationResponse
        is returned unchanged as TextContent.
        """
        # validate_url normalizes by stripping surrounding whitespace; feed a
        # padded url and assert the POST body carries the stripped (normalized)
        # form rather than the raw input.
        raw_url = "  https://example.com/page  "
        normalized_url = "https://example.com/page"
        citation = {
            "title": "Example Domain",
            "original_url": normalized_url,
            "archived_url": "https://web.archive.org/web/20240101/https://example.com/page",
            "archived_at": "2024-01-01T00:00:05Z",
            "task_id": "task-abc",
            "formatted": (
                "[Example Domain](https://web.archive.org/web/20240101/https://example.com/page) "
                "(original: https://example.com/page, archived 2024-01-01)"
            ),
        }
        route = respx.post(f"{BASE_URL}/api/cite").mock(
            return_value=httpx.Response(200, json=citation)
        )

        result = await _handle_archive_and_cite(
            {"url": raw_url, "title": "Example Domain"}, BASE_URL
        )

        # The POST reached /api/cite with the normalized url in the body.
        assert route.called
        request = route.calls.last.request
        sent = json.loads(request.content)
        assert sent["url"] == normalized_url

        # A complete citation returns a single TextContent carrying the payload.
        assert len(result) == 1
        data = json.loads(result[0].text)
        assert data["task_id"] == "task-abc"
        assert data["archived_url"] == citation["archived_url"]
        assert data["formatted"] == citation["formatted"]


# ---------------------------------------------------------------------------
# Req 1.5 — Backend unreachable
# ---------------------------------------------------------------------------


class TestArchiveAndCiteBackendUnavailable:
    """A backend connection failure yields a backend-unavailable error."""

    @respx.mock
    async def test_connect_error_returns_backend_unavailable(self):
        """**Validates: Requirements 1.5**

        When the backend is unreachable (httpx.ConnectError), the tool returns
        {"error": "Backend service unavailable"} rather than raising.
        """
        respx.post(f"{BASE_URL}/api/cite").mock(
            side_effect=httpx.ConnectError("Connection refused")
        )

        result = await _handle_archive_and_cite(
            {"url": "https://example.com"}, BASE_URL
        )

        assert len(result) == 1
        data = json.loads(result[0].text)
        assert data["error"] == "Backend service unavailable"
