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
            Tool(
                name="archive_and_cite",
                description=(
                    "Archive a cited web source AND get back a paste-ready citation in one call. "
                    "Non-blocking (<50ms): archiving runs in the background. "
                    "If the URL was already archived within 24h, you get a COMPLETE citation "
                    "immediately (formatted markdown link to the permanent Wayback Machine URL). "
                    "Otherwise you get a PENDING citation with a task_id — call get_archive_status "
                    "with that task_id later to obtain the permanent archived_url and complete the "
                    "citation. KeepLink does NOT fetch page content or call any LLM; supply the "
                    "page title yourself if you want it in the citation. "
                    "Response fields: title, original_url, archived_url, archived_at, "
                    "task_id, formatted."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "The source URL to archive and cite (http or https).",
                        },
                        "title": {
                            "type": "string",
                            "description": (
                                "Optional page title you already read; used as the citation "
                                "link text. Whitespace-only titles are treated as absent."
                            ),
                        },
                        "format": {
                            "type": "string",
                            "enum": ["markdown", "bibtex", "apa", "plain"],
                            "description": (
                                "Citation output format for the `formatted` field. One of "
                                "markdown (default), bibtex, apa, plain. Only affects "
                                "`formatted`; structured fields are unchanged."
                            ),
                        },
                    },
                    "required": ["url"],
                },
            ),
            Tool(
                name="retry_task",
                description=(
                    "Re-queue a permanently FAILED archive task so the background worker "
                    "processes it again (e.g. after a transient 403/429). Only tasks whose "
                    "status is 'failed' can be retried; the task is reset to 'pending' with "
                    "its retry budget refreshed. Returns the task_id and new status "
                    "('pending'). If the task is not failed (pending/processing/success) the "
                    "call is rejected; if the task_id does not exist a not-found error is "
                    "returned."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "task_id": {
                            "type": "string",
                            "description": "The task_id of the failed task to re-queue.",
                        },
                    },
                    "required": ["task_id"],
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
        elif name == "archive_and_cite":
            return await _handle_archive_and_cite(arguments, base_url)
        elif name == "retry_task":
            return await _handle_retry_task(arguments, base_url)
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


async def _handle_archive_and_cite(arguments: dict, base_url: str) -> list[TextContent]:
    """Handle the archive_and_cite tool invocation.

    Flow: validate URL → POST /api/cite → return structured Citation JSON.

    - Invalid URL      → error result, no POST, no task created (Req 6.1, 6.3).
    - Backend down     → {"error": "Backend service unavailable"} (Req 1.5).
    - Pending citation → CitationResponse JSON plus guidance text telling the
      caller to invoke get_archive_status(task_id) to complete it (Req 4.4).
    - Complete citation → CitationResponse JSON as TextContent (Req 1.3).
    """
    raw_url = arguments.get("url", "")
    title = arguments.get("title")

    # Validate URL before forwarding to backend — fail-fast, never POST on
    # invalid input so no ArchiveTask is created (Req 6.1, 6.3).
    try:
        validated_url = validate_url(raw_url)
    except ValidationError as exc:
        error_msg = f"Invalid URL format: {exc}"
        logger.warning("archive_and_cite validation failed: %s", error_msg)
        return [TextContent(type="text", text=json.dumps({"error": error_msg}))]

    # Forward the original title untouched; the endpoint normalizes it.
    payload: dict[str, str] = {"url": validated_url}
    if title is not None:
        payload["title"] = title
    # Only forward format when the caller supplies it, letting the backend
    # default (markdown) apply. Invalid values are validated by the enum-typed
    # CiteRequest.format on the backend → 422 → {"error": detail}; the tool
    # layer stays fail-fast and never silently substitutes a default (Req 3.7).
    fmt = arguments.get("format")
    if fmt is not None:
        payload["format"] = fmt

    # Forward to FastAPI service
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
            response = await client.post("/api/cite", json=payload)
        data = response.json()
    except (httpx.ConnectError, httpx.ConnectTimeout):
        logger.error("Backend service unavailable at %s", base_url)
        return [TextContent(type="text", text=json.dumps({"error": "Backend service unavailable"}))]
    except (httpx.HTTPError, ValueError) as exc:
        logger.error("Unexpected backend communication error: %s", exc)
        return [TextContent(type="text", text=json.dumps({"error": "Backend service unavailable"}))]

    if response.status_code in (200, 201):
        result = [TextContent(type="text", text=json.dumps(data))]
        # Pending citation: archiving is still in progress. Guide the caller to
        # complete the citation later via get_archive_status(task_id) (Req 4.4).
        if data.get("archived_url") is None:
            guidance = (
                "Archiving is still in progress. Call get_archive_status with "
                f"task_id '{data['task_id']}' later to obtain the permanent "
                "archived_url and complete this citation."
            )
            result.append(TextContent(type="text", text=guidance))
        return result

    # Unexpected error from backend (e.g. 422 validation failure)
    detail = data.get("detail", "Unknown error from backend")
    return [TextContent(type="text", text=json.dumps({"error": detail}))]


async def _handle_retry_task(arguments: dict, base_url: str) -> list[TextContent]:
    """Handle the retry_task tool invocation.

    Flow: require task_id → POST /api/retry → return task_id + new status.

    - Missing task_id  → {"error": "task_id is required"} (no POST).
    - Backend down     → {"error": "Backend service unavailable"} (Req 2.8),
      catching httpx.ConnectError/ConnectTimeout exactly like existing handlers.
    - 200              → {"task_id", "status"} JSON.
    - 404 / 409        → {"error": detail} surfaced verbatim from the backend so
      the caller distinguishes not-found from not-failed rejection (Req 2.6, 2.7,
      2.9).
    """
    task_id = arguments.get("task_id")

    # Require task_id before forwarding — fail-fast, never POST on missing input.
    if not task_id:
        return [TextContent(type="text", text=json.dumps({"error": "task_id is required"}))]

    # Forward to FastAPI service
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
            response = await client.post("/api/retry", json={"task_id": task_id})
        data = response.json()
    except (httpx.ConnectError, httpx.ConnectTimeout):
        logger.error("Backend service unavailable at %s", base_url)
        return [TextContent(type="text", text=json.dumps({"error": "Backend service unavailable"}))]
    except (httpx.HTTPError, ValueError) as exc:
        logger.error("Unexpected backend communication error: %s", exc)
        return [TextContent(type="text", text=json.dumps({"error": "Backend service unavailable"}))]

    if response.status_code == 200:
        result = {
            "task_id": data["task_id"],
            "status": data["status"],
        }
        return [TextContent(type="text", text=json.dumps(result))]

    # 404 not-found or 409 non-failed rejection — surface the backend detail
    # verbatim so the caller can tell the two apart (Req 2.6, 2.7, 2.9).
    detail = data.get("detail", "Unknown error from backend")
    return [TextContent(type="text", text=json.dumps({"error": detail}))]
