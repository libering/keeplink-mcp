# Feature: archive-and-cite
"""Unit tests for the Cite_Endpoint (POST /api/cite).

Complements the property-based tests (test_cite_endpoint_properties.py) with
concrete, example-based assertions for two behaviours that are about *what the
endpoint touches* rather than input-varying invariants:

1. Pipeline reuse (Req 2.2): on an empty DB, POST /api/cite creates a task via
   the existing archive pipeline (repo.create_task) and the task lands in
   pending status; the response is a Pending_Citation (archived_url null,
   task_id present).

2. No page fetch / no LLM (Req 2.4, 2.5): the cite request path only reads the
   DB and calls pure functions. It performs no outbound page fetch and invokes
   no LLM client. KeepLink is LLM-free, so this test asserts the cite endpoint
   introduces none: the only outbound page-fetch/archive client in the codebase
   is waybackpy (driven by the background Worker, not the request path), and
   there is no LLM client module at all.
"""

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from keeplink_mcp.api.app import create_app
from keeplink_mcp.db.models import ArchiveTask, TaskStatus
from keeplink_mcp.db.repository import TaskRepository


@pytest.fixture
async def client(session_factory):
    """Create an httpx.AsyncClient bound to the FastAPI app under test.

    Mirrors the fixture in test_api_routes.py so the cite tests share the
    project's in-memory SQLite + ASGI test client conventions.
    """
    app = create_app(session_factory)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _count_tasks(session_factory) -> int:
    """Return the total number of ArchiveTask rows in the DB."""
    async with session_factory() as session:
        result = await session.execute(select(func.count()).select_from(ArchiveTask))
        return result.scalar()


# ---------------------------------------------------------------------------
# Req 2.2 — Pipeline reuse: empty DB → create_task called, task pending
# ---------------------------------------------------------------------------


class TestCitePipelineReuse:
    """POST /api/cite on an empty DB reuses the existing create_task pipeline."""

    async def test_empty_db_creates_pending_task(
        self, client: AsyncClient, session_factory
    ):
        """**Validates: Requirements 2.2**

        With an empty DB, POST /api/cite creates exactly one task (0 -> 1) and
        that task is pending; the response is a Pending_Citation.
        """
        assert await _count_tasks(session_factory) == 0

        resp = await client.post("/api/cite", json={"url": "https://example.com/cited"})

        assert resp.status_code == 200
        body = resp.json()

        # Response is a pending citation: task_id present, archived_url null.
        assert body["task_id"]
        assert body["archived_url"] is None
        assert body["archived_at"] is None
        assert body["original_url"] == "https://example.com/cited"
        assert body["formatted"]

        # A single task was created and it is pending.
        assert await _count_tasks(session_factory) == 1
        async with session_factory() as session:
            task = await TaskRepository(session).get_task(body["task_id"])
            assert task is not None
            assert task.status == TaskStatus.PENDING
            assert task.url == "https://example.com/cited"

    async def test_create_task_is_invoked_on_cache_miss(
        self, client: AsyncClient, session_factory
    ):
        """**Validates: Requirements 2.2**

        Spy on the pipeline entry point: on a cache miss the endpoint routes
        through repo.create_task (rather than reinventing task creation).
        """
        original_create_task = TaskRepository.create_task

        with patch.object(
            TaskRepository,
            "create_task",
            autospec=True,
            side_effect=original_create_task,
        ) as spy_create_task:
            resp = await client.post(
                "/api/cite", json={"url": "https://example.com/spy-create"}
            )

        assert resp.status_code == 200
        # create_task was invoked exactly once with the validated URL.
        assert spy_create_task.call_count == 1
        called_url = spy_create_task.call_args.args[-1]
        assert called_url == "https://example.com/spy-create"

    async def test_dedup_reuses_task_without_create(
        self, client: AsyncClient, session_factory
    ):
        """**Validates: Requirements 2.2**

        A second POST for the same URL reuses the existing pending task and does
        NOT create a new one (task count stays at 1).
        """
        url = "https://example.com/cite-dedup"
        first = await client.post("/api/cite", json={"url": url})
        assert first.status_code == 200
        first_id = first.json()["task_id"]
        assert await _count_tasks(session_factory) == 1

        with patch.object(
            TaskRepository, "create_task", autospec=True
        ) as spy_create_task:
            second = await client.post("/api/cite", json={"url": url})

        assert second.status_code == 200
        assert second.json()["task_id"] == first_id
        spy_create_task.assert_not_called()
        assert await _count_tasks(session_factory) == 1


# ---------------------------------------------------------------------------
# Req 2.4, 2.5 — No page fetch, no LLM on the cite request path
# ---------------------------------------------------------------------------


class TestCiteNoFetchNoLLM:
    """The cite request path touches only DB + pure functions.

    The only outbound page-fetch/archive client in the codebase is
    ``waybackpy.WaybackMachineSaveAPI`` (used by the background Worker, not the
    request path). KeepLink has no LLM client module at all. These tests wire
    both potential outbound paths to explode if touched, then assert POST
    /api/cite still succeeds — proving the endpoint introduces neither.
    """

    async def test_cite_does_not_invoke_wayback_save_client(
        self, client: AsyncClient
    ):
        """**Validates: Requirements 2.4**

        Patch the Wayback (page-fetch/archive) client to raise if instantiated;
        POST /api/cite must still succeed, proving the request path performs no
        page fetch / archive save.
        """

        def _boom(*_args, **_kwargs):
            raise AssertionError(
                "cite path must not construct the Wayback save/page-fetch client"
            )

        with patch(
            "keeplink_mcp.worker.archiver.waybackpy.WaybackMachineSaveAPI",
            side_effect=_boom,
        ):
            resp = await client.post(
                "/api/cite", json={"url": "https://example.com/no-fetch"}
            )

        assert resp.status_code == 200
        assert resp.json()["archived_url"] is None

    async def test_cite_makes_no_outbound_socket_connection(
        self, client: AsyncClient
    ):
        """**Validates: Requirements 2.4, 2.5**

        Fail on any outbound network connect (page fetch or an LLM HTTP call).
        The in-memory SQLite DB uses no sockets, so a successful POST proves the
        cite path is DB + pure-function only, with no external I/O.
        """
        import socket

        original_connect = socket.socket.connect

        def _no_network(self, address, *args, **kwargs):
            raise AssertionError(
                f"cite path must not open an outbound socket (attempted {address!r})"
            )

        with patch.object(socket.socket, "connect", _no_network):
            resp = await client.post(
                "/api/cite", json={"url": "https://example.com/no-socket"}
            )

        # Restore is handled by the patch context manager.
        assert original_connect is not None
        assert resp.status_code == 200
        body = resp.json()
        assert body["task_id"]
        assert body["archived_url"] is None

    def test_no_llm_client_module_exists(self):
        """**Validates: Requirements 2.5**

        KeepLink is LLM-free by design: there is no LLM client to import in the
        cite path. Assert that common LLM client libraries are not dependencies,
        documenting that the endpoint invokes no LLM.
        """
        import importlib.util

        for llm_module in ("openai", "anthropic", "langchain"):
            assert importlib.util.find_spec(llm_module) is None, (
                f"unexpected LLM client dependency present: {llm_module}"
            )
