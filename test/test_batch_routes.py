"""Unit tests for batch status query endpoint.

# Feature: keeplink-mcp

Tests cover:
- GET /api/status/batch: valid batch query with mixed task_ids and urls
- GET /api/status/batch: 422 response when > 50 identifiers
- GET /api/status/batch: partial results (some IDs exist, some don't)
- GET /api/status/batch: 422 response when neither parameter provided

Validates: Requirements 7.1, 7.2, 7.3, 7.4, 7.5, 7.6
"""

import pytest
from httpx import ASGITransport, AsyncClient

from keeplink_mcp.api.app import create_app
from keeplink_mcp.db.models import ArchiveTask, TaskStatus


@pytest.fixture
async def client(session_factory):
    """Create an httpx.AsyncClient bound to the FastAPI app under test."""
    app = create_app(session_factory)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# GET /api/status/batch — valid batch query with mixed task_ids and urls
# ---------------------------------------------------------------------------


class TestBatchStatusValidQuery:
    """GET /api/status/batch valid query tests."""

    async def test_batch_query_by_task_ids_only(self, client: AsyncClient):
        """Querying multiple tasks by IDs returns all matching tasks."""
        # Create two tasks
        resp1 = await client.post(
            "/api/archive", json={"url": "https://example.com/batch-1"}
        )
        resp2 = await client.post(
            "/api/archive", json={"url": "https://example.com/batch-2"}
        )
        task_id_1 = resp1.json()["task_id"]
        task_id_2 = resp2.json()["task_id"]

        # Query by both IDs
        resp = await client.get(
            "/api/status/batch",
            params={"task_ids": f"{task_id_1},{task_id_2}"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["total_requested"] == 2
        assert body["total_found"] == 2
        assert len(body["results"]) == 2
        returned_ids = {r["task_id"] for r in body["results"]}
        assert task_id_1 in returned_ids
        assert task_id_2 in returned_ids

    async def test_batch_query_by_urls_only(self, client: AsyncClient):
        """Querying multiple URLs returns most recent task per URL."""
        url1 = "https://example.com/batch-url-1"
        url2 = "https://example.com/batch-url-2"

        # Create tasks for both URLs
        await client.post("/api/archive", json={"url": url1})
        await client.post("/api/archive", json={"url": url2})

        resp = await client.get(
            "/api/status/batch",
            params={"url": f"{url1},{url2}"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["total_requested"] == 2
        assert body["total_found"] == 2
        assert len(body["results"]) == 2
        returned_urls = {r["url"] for r in body["results"]}
        assert url1 in returned_urls
        assert url2 in returned_urls

    async def test_batch_query_mixed_task_ids_and_urls(
        self, client: AsyncClient, session_factory
    ):
        """Querying with both task_ids and urls returns merged results."""
        url1 = "https://example.com/batch-mixed-1"
        url2 = "https://example.com/batch-mixed-2"

        # Create task for url1 via API to get task_id
        resp1 = await client.post("/api/archive", json={"url": url1})
        task_id_1 = resp1.json()["task_id"]

        # Create another task for url2 directly in DB
        async with session_factory() as session:
            task2 = ArchiveTask(url=url2, status=TaskStatus.PENDING)
            session.add(task2)
            await session.commit()
            await session.refresh(task2)
            task_id_2 = task2.task_id

        # Query with task_id for url1 and URL for url2
        resp = await client.get(
            "/api/status/batch",
            params={"task_ids": task_id_1, "url": url2},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["total_requested"] == 2
        assert body["total_found"] == 2
        returned_ids = {r["task_id"] for r in body["results"]}
        assert task_id_1 in returned_ids
        assert task_id_2 in returned_ids

    async def test_batch_query_deduplicates_same_task(
        self, client: AsyncClient
    ):
        """Querying same task by both ID and URL returns it once."""
        url = "https://example.com/batch-dedup"
        resp = await client.post("/api/archive", json={"url": url})
        task_id = resp.json()["task_id"]

        # Query by both task_id and URL (same task)
        resp = await client.get(
            "/api/status/batch",
            params={"task_ids": task_id, "url": url},
        )

        assert resp.status_code == 200
        body = resp.json()
        # Should request 2 identifiers but find 1 unique task
        assert body["total_requested"] == 2
        assert body["total_found"] == 1
        assert len(body["results"]) == 1
        assert body["results"][0]["task_id"] == task_id

    async def test_batch_query_returns_task_status_fields(
        self, client: AsyncClient
    ):
        """Batch response includes all required task status fields."""
        url = "https://example.com/batch-fields"
        resp = await client.post("/api/archive", json={"url": url})
        task_id = resp.json()["task_id"]

        resp = await client.get(
            "/api/status/batch",
            params={"task_ids": task_id},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["results"]) == 1
        task = body["results"][0]

        # Verify all required fields are present
        assert task["task_id"] == task_id
        assert task["url"] == url
        assert task["status"] == "pending"
        assert task["result_url"] is None
        assert task["error_message"] is None
        assert task["retry_count"] == 0
        assert "created_at" in task
        assert "updated_at" in task


# ---------------------------------------------------------------------------
# GET /api/status/batch — 422 response when > 50 identifiers
# ---------------------------------------------------------------------------


class TestBatchStatusIdentifierLimit:
    """GET /api/status/batch identifier limit tests."""

    async def test_422_when_exceeding_50_task_ids(self, client: AsyncClient):
        """Request with 51 task_ids returns HTTP 422."""
        # Generate 51 fake task IDs
        task_ids = ",".join(f"fake-id-{i}" for i in range(51))

        resp = await client.get(
            "/api/status/batch",
            params={"task_ids": task_ids},
        )

        assert resp.status_code == 422
        body = resp.json()
        assert "50" in body["detail"]
        assert "51" in body["detail"]

    async def test_422_when_exceeding_50_urls(self, client: AsyncClient):
        """Request with 51 URLs returns HTTP 422."""
        # Generate 51 URLs
        urls = ",".join(f"https://example.com/page-{i}" for i in range(51))

        resp = await client.get(
            "/api/status/batch",
            params={"url": urls},
        )

        assert resp.status_code == 422
        body = resp.json()
        assert "50" in body["detail"]
        assert "51" in body["detail"]

    async def test_422_when_combined_count_exceeds_50(
        self, client: AsyncClient
    ):
        """Request with 25 task_ids + 26 URLs (total 51) returns HTTP 422."""
        task_ids = ",".join(f"fake-id-{i}" for i in range(25))
        urls = ",".join(f"https://example.com/page-{i}" for i in range(26))

        resp = await client.get(
            "/api/status/batch",
            params={"task_ids": task_ids, "url": urls},
        )

        assert resp.status_code == 422
        body = resp.json()
        assert "50" in body["detail"]
        assert "51" in body["detail"]

    async def test_accepts_exactly_50_identifiers(self, client: AsyncClient):
        """Request with exactly 50 task_ids returns HTTP 200 (not 422)."""
        # Generate 50 fake task IDs (none exist, but should still return 200)
        task_ids = ",".join(f"fake-id-{i}" for i in range(50))

        resp = await client.get(
            "/api/status/batch",
            params={"task_ids": task_ids},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["total_requested"] == 50
        assert body["total_found"] == 0
        assert len(body["results"]) == 0


# ---------------------------------------------------------------------------
# GET /api/status/batch — partial results (some IDs exist, some don't)
# ---------------------------------------------------------------------------


class TestBatchStatusPartialResults:
    """GET /api/status/batch partial results tests."""

    async def test_partial_results_some_ids_exist_some_dont(
        self, client: AsyncClient
    ):
        """Query with mix of existing and non-existent IDs returns partial results."""
        # Create one real task
        resp = await client.post(
            "/api/archive", json={"url": "https://example.com/partial-real"}
        )
        real_task_id = resp.json()["task_id"]

        # Query with one real ID and two non-existent IDs
        fake_id_1 = "nonexistent-id-00000001"
        fake_id_2 = "nonexistent-id-00000002"

        resp = await client.get(
            "/api/status/batch",
            params={"task_ids": f"{real_task_id},{fake_id_1},{fake_id_2}"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["total_requested"] == 3
        assert body["total_found"] == 1
        assert len(body["results"]) == 1
        assert body["results"][0]["task_id"] == real_task_id

    async def test_partial_results_some_urls_exist_some_dont(
        self, client: AsyncClient
    ):
        """Query with mix of existing and non-existent URLs returns partial results."""
        # Create one real task
        real_url = "https://example.com/partial-url-real"
        await client.post("/api/archive", json={"url": real_url})

        # Query with one real URL and two non-existent URLs
        fake_url_1 = "https://example.com/never-submitted-1"
        fake_url_2 = "https://example.com/never-submitted-2"

        resp = await client.get(
            "/api/status/batch",
            params={"url": f"{real_url},{fake_url_1},{fake_url_2}"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["total_requested"] == 3
        assert body["total_found"] == 1
        assert len(body["results"]) == 1
        assert body["results"][0]["url"] == real_url

    async def test_empty_results_when_no_ids_exist(self, client: AsyncClient):
        """Query with only non-existent IDs returns empty results with 200."""
        resp = await client.get(
            "/api/status/batch",
            params={"task_ids": "nonexistent-id-1,nonexistent-id-2"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["total_requested"] == 2
        assert body["total_found"] == 0
        assert len(body["results"]) == 0

    async def test_empty_results_when_no_urls_exist(self, client: AsyncClient):
        """Query with only non-existent URLs returns empty results with 200."""
        resp = await client.get(
            "/api/status/batch",
            params={"url": "https://never1.example.com,https://never2.example.com"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["total_requested"] == 2
        assert body["total_found"] == 0
        assert len(body["results"]) == 0


# ---------------------------------------------------------------------------
# GET /api/status/batch — 422 response when neither parameter provided
# ---------------------------------------------------------------------------


class TestBatchStatusMissingParameters:
    """GET /api/status/batch missing parameters tests."""

    async def test_422_when_no_parameters_provided(self, client: AsyncClient):
        """Request with neither task_ids nor url returns HTTP 422."""
        resp = await client.get("/api/status/batch")

        assert resp.status_code == 422
        body = resp.json()
        assert "task_ids" in body["detail"].lower() or "url" in body["detail"].lower()

    async def test_422_when_empty_task_ids_and_empty_url(
        self, client: AsyncClient
    ):
        """Request with empty string parameters returns HTTP 422."""
        resp = await client.get(
            "/api/status/batch",
            params={"task_ids": "", "url": ""},
        )

        assert resp.status_code == 422
        body = resp.json()
        assert "task_ids" in body["detail"].lower() or "url" in body["detail"].lower()

    async def test_422_when_only_whitespace_parameters(
        self, client: AsyncClient
    ):
        """Request with whitespace-only parameters returns HTTP 422."""
        resp = await client.get(
            "/api/status/batch",
            params={"task_ids": "   ,  ,  ", "url": " ,  "},
        )

        assert resp.status_code == 422
        body = resp.json()
        assert "task_ids" in body["detail"].lower() or "url" in body["detail"].lower()


# ---------------------------------------------------------------------------
# GET /api/status/batch — URL returns most recent task
# ---------------------------------------------------------------------------


class TestBatchStatusMostRecentTask:
    """Tests for verifying URL queries return most recent task."""

    async def test_url_query_returns_most_recent_task(
        self, client: AsyncClient, session_factory
    ):
        """When URL has multiple tasks, batch query returns the most recent one."""
        url = "https://example.com/multiple-tasks"

        # Create first task
        async with session_factory() as session:
            task1 = ArchiveTask(
                url=url,
                status=TaskStatus.SUCCESS,
                result_url="https://web.archive.org/old",
            )
            session.add(task1)
            await session.commit()
            await session.refresh(task1)

        # Create second task via API (more recent)
        resp = await client.post("/api/archive", json={"url": url})
        task2_id = resp.json()["task_id"]

        # Query by URL should return the most recent task
        resp = await client.get(
            "/api/status/batch",
            params={"url": url},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["total_found"] == 1
        assert body["results"][0]["task_id"] == task2_id
