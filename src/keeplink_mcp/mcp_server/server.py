"""MCP Server implementation exposing archive_url and get_archive_status tools.

Uses the official `mcp` Python SDK (low-level Server class) to register tools
that proxy requests to the internal FastAPI service via httpx.

Requirements: 1.1, 1.2, 1.3, 2.1, 2.2, 2.3, 2.4, 2.5
"""

import json
import logging

import httpx
from mcp.server import Server
from mcp.types import TextContent, Tool

from keeplink_mcp.config import Config
from keeplink_mcp.mcp_server.url_validator import ValidationError, validate_url

logger = logging.getLogger(__name__)


def _build_base_url(config: Config) -> str:
    """Construct FastAPI service base URL from config."""
    return f"http://{config.api_host}:{config.api_port}"


def create_mcp_server(config: Config) -> Server:
    """Create and configure the MCP server with archive tools.

    Args:
        config: Application configuration containing api_host/api_port.

    Returns:
        Configured MCP Server instance ready to be connected to a transport.
    """
    server = Server("keeplink-mcp")
    base_url = _build_base_url(config)

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        """Expose available archive tools to the MCP client."""
        return [
            Tool(
                name="archive_url",
                description=(
                    "Submit a URL for archiving to the Internet Archive. "
                    "Returns immediately with a task_id (non-blocking, <50ms). "
                    "The actual archiving happens asynchronously in the background. "
                    "Use this to preserve web page evidence during research — "
                    "pages may change or disappear, so archive early and often. "
                    "Duplicate submissions within 24 hours are deduplicated automatically. "
                    "Response fields: task_id (tracking ID), status (always 'pending' "
                    "for new submissions), url (submitted URL), is_deduplicated (true "
                    "if an existing task was reused)."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": (
                                "The URL to archive (must be http or https). "
                                "The URL is validated and normalized before submission."
                            ),
                        },
                    },
                    "required": ["url"],
                },
            ),
            Tool(
                name="get_archive_status",
                description=(
                    "Check the status of an archive task. "
                    "Provide either a task_id or a url (at least one required). "
                    "Status values: 'PENDING' (queued, waiting to be processed), "
                    "'PROCESSING' (worker is archiving now), "
                    "'SUCCESS' (archived — result_url contains the Wayback Machine link), "
                    "'FAILED' (permanent failure after retries exhausted). "
                    "When status is SUCCESS, use result_url as a permanent citation link. "
                    "If status is PENDING/PROCESSING, check again after a few seconds."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "task_id": {
                            "type": "string",
                            "description": (
                                "The task ID returned by archive_url. "
                                "Use this for precise lookup of a specific submission."
                            ),
                        },
                        "url": {
                            "type": "string",
                            "description": (
                                "The URL to look up the most recent task for. "
                                "Returns the latest task regardless of status."
                            ),
                        },
                    },
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        """Dispatch tool calls to the appropriate handler."""
        if name == "archive_url":
            return await _handle_archive_url(arguments, base_url)
        elif name == "get_archive_status":
            return await _handle_get_archive_status(arguments, base_url)
        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    return server


async def _handle_archive_url(arguments: dict, base_url: str) -> list[TextContent]:
    """Handle the archive_url tool invocation.

    Flow: validate URL → POST to FastAPI → return task_id + status.
    """
    raw_url = arguments.get("url", "")

    # Validate URL before forwarding to backend
    try:
        validated_url = validate_url(raw_url)
    except ValidationError as exc:
        error_msg = f"Invalid URL format: {exc}"
        logger.warning("archive_url validation failed: %s", error_msg)
        return [TextContent(type="text", text=json.dumps({"error": error_msg}))]

    # Forward to FastAPI service
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
            response = await client.post("/api/archive", json={"url": validated_url})
        data = response.json()
    except (httpx.ConnectError, httpx.ConnectTimeout):
        logger.error("Backend service unavailable at %s", base_url)
        return [TextContent(type="text", text=json.dumps({"error": "Backend service unavailable"}))]
    except (httpx.HTTPError, ValueError) as exc:
        logger.error("Unexpected backend communication error: %s", exc)
        return [TextContent(type="text", text=json.dumps({"error": "Backend service unavailable"}))]
    if response.status_code in (200, 201):
        result = {
            "task_id": data["task_id"],
            "status": data["status"],
            "url": data["url"],
        }
        return [TextContent(type="text", text=json.dumps(result))]

    # Unexpected error from backend
    detail = data.get("detail", "Unknown error from backend")
    return [TextContent(type="text", text=json.dumps({"error": detail}))]


async def _handle_get_archive_status(arguments: dict, base_url: str) -> list[TextContent]:
    """Handle the get_archive_status tool invocation.

    Accepts task_id or url (at least one required).
    """
    task_id = arguments.get("task_id")
    url = arguments.get("url")

    if not task_id and not url:
        return [
            TextContent(
                type="text",
                text=json.dumps({"error": "At least one of task_id or url is required"}),
            )
        ]

    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
            if task_id:
                response = await client.get(f"/api/status/{task_id}")
            else:
                response = await client.get("/api/status", params={"url": url})
        data = response.json()
    except (httpx.ConnectError, httpx.ConnectTimeout):
        logger.error("Backend service unavailable at %s", base_url)
        return [TextContent(type="text", text=json.dumps({"error": "Backend service unavailable"}))]
    except (httpx.HTTPError, ValueError) as exc:
        logger.error("Unexpected backend communication error: %s", exc)
        return [TextContent(type="text", text=json.dumps({"error": "Backend service unavailable"}))]

    # Handle 404 — task or URL not found
    if response.status_code == 404:
        detail = data.get("detail", "Not found")
        return [TextContent(type="text", text=json.dumps({"error": detail}))]

    if response.status_code == 200:
        return [TextContent(type="text", text=json.dumps(data))]

    # Unexpected error
    detail = data.get("detail", "Unknown error from backend")
    return [TextContent(type="text", text=json.dumps({"error": detail}))]
