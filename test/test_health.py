"""Unit tests for health check endpoint.

# Feature: keeplink-mcp

Tests cover:
- GET /api/health: successful health response with all fields
- GET /api/health: DB unavailable → readiness=false
- Validates: Requirements 2.1, 2.2, 2.3, 2.5
"""

import pytest
from httpx import ASGITransport, AsyncClient

from keeplink_mcp.api.app import create_app
from keeplink_mcp.db.models import ArchiveTask, TaskStatus


@pytest.fixture
async def client(session_factory):
    """Create an httpx.AsyncClient bound to the FastAPI app under test."""
    # Simulate a running worker by providing a getter that returns True
    worker_running = True

    def get_worker_running():
        return worker_running

    app = create_app(session_factory, worker_running_getter=get_worker_running)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def client_with_stopped_worker(session_factory):
    """Create a client where worker is not running."""
    worker_running = False

    def get_worker_running():
        return worker_running

    app = create_app(session_factory, worker_running_getter=get_worker_running)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# GET /api/health — successful health response
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    """GET /api/health endpoint tests."""

    async def test_health_returns_200_when_healthy(self, client: AsyncClient):
        """A healthy system returns 200 with liveness=true and readiness=true."""
        resp = await client.get("/api/health")

        assert resp.status_code == 200
        body = resp.json()
        assert body["liveness"] is True
        assert body["readiness"] is True
        assert body["readiness_error"] is None

    async def test_health_includes_stats_with_queue_depth(
        self, client: AsyncClient, session_factory
    ):
        """Health response includes queue_depth counting pending tasks."""
        # Create some pending tasks
        async with session_factory() as session:
            for i in range(3):
                task = ArchiveTask(url=f"https://example.com/pending-{i}")
                session.add(task)
            await session.commit()

        resp = await client.get("/api/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["stats"]["queue_depth"] >= 3

    async def test_health_includes_success_count(self, client: AsyncClient, session_factory):
        """Health response includes success_count for tasks completed successfully."""
        # Create a successful task
        async with session_factory() as session:
            task = ArchiveTask(
                url="https://example.com/success",
                status=TaskStatus.SUCCESS,
                result_url="https://web.archive.org/web/20240101/example.com",
            )
            session.add(task)
            await session.commit()

        resp = await client.get("/api/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["stats"]["success_count"] >= 1

    async def test_health_includes_failure_count(self, client: AsyncClient, session_factory):
        """Health response includes failure_count for failed tasks."""
        # Create a failed task
        async with session_factory() as session:
            task = ArchiveTask(
                url="https://example.com/failed",
                status=TaskStatus.FAILED,
                error_message="timeout",
            )
            session.add(task)
            await session.commit()

        resp = await client.get("/api/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["stats"]["failure_count"] >= 1

    async def test_health_all_fields_present(self, client: AsyncClient):
        """Health response contains all required fields."""
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        body = resp.json()

        # Top-level fields
        assert "liveness" in body
        assert "readiness" in body
        assert "readiness_error" in body
        assert "stats" in body

        # Stats fields
        stats = body["stats"]
        assert "queue_depth" in stats
        assert "success_count" in stats
        assert "failure_count" in stats


# ---------------------------------------------------------------------------
# GET /api/health — readiness=false scenarios
# ---------------------------------------------------------------------------


class TestHealthReadinessFalse:
    """Tests for health endpoint when readiness is false."""

    async def test_worker_not_running_returns_503(
        self, client_with_stopped_worker: AsyncClient
    ):
        """When worker is not running, readiness is false with 503 status."""
        resp = await client_with_stopped_worker.get("/api/health")

        assert resp.status_code == 503
        body = resp.json()
        assert body["liveness"] is True
        assert body["readiness"] is False
        assert body["readiness_error"] is not None
        assert "worker" in body["readiness_error"].lower()

    async def test_db_unavailable_returns_503(self, session_factory):
        """When database is unavailable, readiness is false with 503 status."""
        # Create an app with a session factory that points to an invalid path
        # to simulate DB unavailability
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker

        # Use an invalid path that will cause a database error
        # Note: SQLite :memory: can't be made "unavailable" after creation,
        # so we use a file path in a non-existent directory
        engine = create_async_engine(
            "sqlite+aiosqlite:///nonexistent/path/task.db",
            echo=False,
        )

        disposed_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        app = create_app(
            disposed_factory,
            worker_running_getter=lambda: True,
        )
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/health")

            assert resp.status_code == 503
            body = resp.json()
            assert body["liveness"] is True
            assert body["readiness"] is False
            assert body["readiness_error"] is not None
            # The error should mention database or connectivity
            error_lower = body["readiness_error"].lower()
            assert "database" in error_lower or "connectivity" in error_lower

        await engine.dispose()

    async def test_db_unavailable_stats_are_zero(self, session_factory):
        """When database is unavailable, stats should be zero."""
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker

        engine = create_async_engine(
            "sqlite+aiosqlite:///nonexistent/path2/task.db",
            echo=False,
        )

        disposed_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        app = create_app(
            disposed_factory,
            worker_running_getter=lambda: True,
        )
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/health")

            body = resp.json()
            assert body["stats"]["queue_depth"] == 0
            assert body["stats"]["success_count"] == 0
            assert body["stats"]["failure_count"] == 0

        await engine.dispose()
