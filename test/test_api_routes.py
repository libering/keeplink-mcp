"""Integration tests for FastAPI route handlers.

# Feature: omniarchive-mcp

Tests cover:
- POST /api/archive: normal creation, dedup hit, failed retry, URL validation
- GET /api/status/{task_id}: normal query, not found
- GET /api/status?url=...: normal query, not found
- Property 5: dedup idempotency
- Property 6: failed tasks allow retry
"""

import pytest
from httpx import ASGITransport, AsyncClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from omniarchive_mcp.api.app import create_app
from omniarchive_mcp.db.models import ArchiveTask, TaskStatus


@pytest.fixture
async def client(session_factory):
    """Create an httpx.AsyncClient bound to the FastAPI app under test."""
    app = create_app(session_factory)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# POST /api/archive — normal creation (201)
# ---------------------------------------------------------------------------


class TestArchiveCreate:
    """POST /api/archive endpoint tests."""

    async def test_create_returns_201_with_task_id(self, client: AsyncClient):
        """A valid URL produces 201 with a task_id and is_deduplicated=False."""
        resp = await client.post("/api/archive", json={"url": "https://example.com/page"})

        assert resp.status_code == 201
        body = resp.json()
        assert body["task_id"]
        assert body["url"] == "https://example.com/page"
        assert body["status"] == "pending"
        assert body["is_deduplicated"] is False

    async def test_dedup_hit_pending(self, client: AsyncClient):
        """Second POST for same URL (pending task exists) returns 200 + is_deduplicated=True."""
        url = "https://example.com/dedup-pending"
        first = await client.post("/api/archive", json={"url": url})
        assert first.status_code == 201
        first_id = first.json()["task_id"]

        second = await client.post("/api/archive", json={"url": url})
        assert second.status_code == 200
        assert second.json()["task_id"] == first_id
        assert second.json()["is_deduplicated"] is True

    async def test_dedup_hit_success(self, client: AsyncClient, session_factory):
        """Dedup returns existing task when status is success within window."""
        url = "https://example.com/dedup-success"
        # Create a task and manually set it to success
        async with session_factory() as session:
            task = ArchiveTask(
                url=url,
                status=TaskStatus.SUCCESS,
                result_url="https://web.archive.org/web/20240101/example.com",
            )
            session.add(task)
            await session.commit()
            await session.refresh(task)
            existing_id = task.task_id

        resp = await client.post("/api/archive", json={"url": url})
        assert resp.status_code == 200
        assert resp.json()["task_id"] == existing_id
        assert resp.json()["is_deduplicated"] is True
        assert resp.json()["result_url"] is not None

    async def test_failed_allows_retry(self, client: AsyncClient, session_factory):
        """If only a failed task exists within window, a new task is created."""
        url = "https://example.com/dedup-failed"
        # Create a failed task
        async with session_factory() as session:
            task = ArchiveTask(url=url, status=TaskStatus.FAILED, error_message="timeout")
            session.add(task)
            await session.commit()
            await session.refresh(task)
            failed_id = task.task_id

        resp = await client.post("/api/archive", json={"url": url})
        assert resp.status_code == 201
        assert resp.json()["task_id"] != failed_id
        assert resp.json()["is_deduplicated"] is False

    async def test_invalid_url_returns_422(self, client: AsyncClient):
        """Submitting an invalid URL returns 422 with a descriptive error."""
        resp = await client.post("/api/archive", json={"url": "not-a-url"})

        assert resp.status_code == 422
        body = resp.json()
        assert "detail" in body

    async def test_invalid_scheme_returns_422(self, client: AsyncClient):
        """ftp:// URL is rejected with 422."""
        resp = await client.post("/api/archive", json={"url": "ftp://files.example.com/a.zip"})

        assert resp.status_code == 422
        assert "scheme" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# GET /api/status/{task_id}
# ---------------------------------------------------------------------------


class TestStatusById:
    """GET /api/status/{task_id} endpoint tests."""

    async def test_get_status_returns_200(self, client: AsyncClient):
        """Querying an existing task by ID returns 200 with full status."""
        create_resp = await client.post(
            "/api/archive", json={"url": "https://example.com/status-test"}
        )
        task_id = create_resp.json()["task_id"]

        resp = await client.get(f"/api/status/{task_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["task_id"] == task_id
        assert body["status"] == "pending"
        assert body["retry_count"] == 0

    async def test_get_status_not_found(self, client: AsyncClient):
        """Querying a non-existent task_id returns 404."""
        resp = await client.get("/api/status/nonexistent_id_12345678")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# GET /api/status?url=...
# ---------------------------------------------------------------------------


class TestStatusByUrl:
    """GET /api/status?url=... endpoint tests."""

    async def test_get_status_by_url_returns_200(self, client: AsyncClient):
        """Querying an existing URL returns 200 with the latest task."""
        url = "https://example.com/url-status"
        await client.post("/api/archive", json={"url": url})

        resp = await client.get("/api/status", params={"url": url})
        assert resp.status_code == 200
        assert resp.json()["url"] == url

    async def test_get_status_by_url_not_found(self, client: AsyncClient):
        """Querying a URL with no tasks returns 404."""
        resp = await client.get(
            "/api/status", params={"url": "https://never-submitted.example.com"}
        )
        assert resp.status_code == 404
        assert "no tasks found" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Property 5: 去重窗口內的冪等性
# Validates: Requirements 4.1, 4.2
# ---------------------------------------------------------------------------


@settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(submit_count=st.integers(min_value=2, max_value=10))
@pytest.mark.asyncio
async def test_property_dedup_idempotency(submit_count: int, session_factory):
    """**Validates: Requirements 4.1, 4.2**

    Property 5: For any URL submitted multiple times within the dedup window,
    if a pending/processing/success task already exists, the total task count
    for that URL shall not increase beyond 1.
    """
    app = create_app(session_factory)
    transport = ASGITransport(app=app)
    url = "https://example.com/property5-idempotent"

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Submit the URL multiple times
        task_ids = []
        for _ in range(submit_count):
            resp = await client.post("/api/archive", json={"url": url})
            assert resp.status_code in (200, 201)
            task_ids.append(resp.json()["task_id"])

        # All submissions should return the same task_id
        assert len(set(task_ids)) == 1

    # Verify only 1 task exists in DB
    async with session_factory() as session:
        from sqlalchemy import func, select

        stmt = select(func.count()).select_from(ArchiveTask).where(ArchiveTask.url == url)
        result = await session.execute(stmt)
        count = result.scalar()
        assert count == 1


# ---------------------------------------------------------------------------
# Property 6: 去重窗口內失敗任務允許重試
# Validates: Requirements 4.3
# ---------------------------------------------------------------------------


@settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(retry_count=st.integers(min_value=1, max_value=5))
@pytest.mark.asyncio
async def test_property_failed_allows_retry(retry_count: int, session_factory):
    """**Validates: Requirements 4.3**

    Property 6: If the only existing task within the dedup window has status 'failed',
    a new POST shall create a new task — the total task count shall increase by one.
    """
    app = create_app(session_factory)
    transport = ASGITransport(app=app)
    url = f"https://example.com/property6-retry-{retry_count}"

    # Seed the DB with a failed task for this URL
    async with session_factory() as session:
        failed_task = ArchiveTask(
            url=url,
            status=TaskStatus.FAILED,
            error_message="simulated failure",
        )
        session.add(failed_task)
        await session.commit()

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/archive", json={"url": url})
        assert resp.status_code == 201
        assert resp.json()["is_deduplicated"] is False

    # Verify task count increased (1 failed + 1 new pending = 2)
    async with session_factory() as session:
        from sqlalchemy import func, select

        stmt = select(func.count()).select_from(ArchiveTask).where(ArchiveTask.url == url)
        result = await session.execute(stmt)
        count = result.scalar()
        assert count == 2
