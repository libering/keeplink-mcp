# Feature: keeplink-mcp
"""Property-based tests for batch status query endpoint correctness.

Validates: Requirements 7.2, 7.3, 7.4, 7.5, 7.6

Property 4: Batch query by task_ids returns correct subset
Property 5: Batch query by URLs returns most recent task per URL
Property 6: Batch query rejects requests exceeding 50 identifiers
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from httpx import ASGITransport, AsyncClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy.orm import sessionmaker

from keeplink_mcp.api.app import create_app
from keeplink_mcp.db.models import ArchiveTask, TaskStatus
from keeplink_mcp.db.repository import TaskRepository

# ---------------------------------------------------------------------------
# Hypothesis Strategies
# ---------------------------------------------------------------------------

# Generate valid URL strings for testing
url_strategy = st.builds(
    lambda domain, path: f"https://{domain}.example.com/{path}",
    st.text(
        min_size=3, max_size=20,
        alphabet=st.characters(codec='utf-8', min_codepoint=ord('a'), max_codepoint=ord('z')),
    ),
    st.text(
        min_size=1, max_size=10,
        alphabet=st.characters(codec='utf-8', min_codepoint=ord('a'), max_codepoint=ord('z')),
    ),
)

# Generate a list of unique task_ids (32-char hex strings)
task_id_strategy = st.text(min_size=32, max_size=32, alphabet="0123456789abcdef")

# Generate a small integer for batch sizes
batch_size_strategy = st.integers(min_value=0, max_value=60)


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------

async def create_test_tasks(
    session_factory: sessionmaker,
    tasks_spec: list[dict[str, Any]],
) -> list[ArchiveTask]:
    """Create test tasks with specified properties.

    Args:
        session_factory: Async session factory.
        tasks_spec: List of dicts with keys: url, status, created_at_offset.

    Returns:
        List of created ArchiveTask objects.
    """
    tasks: list[ArchiveTask] = []
    async with session_factory() as session:
        repo = TaskRepository(session)
        for spec in tasks_spec:
            task = await repo.create_task(spec["url"])
            # Override created_at if specified
            if "created_at_offset" in spec:
                task.created_at = datetime.now(timezone.utc) + spec["created_at_offset"]
                await session.commit()
                await session.refresh(task)
            # Set status if not pending
            if spec.get("status") != TaskStatus.PENDING:
                await repo.mark_processing(task.task_id)
                if spec.get("status") == TaskStatus.SUCCESS:
                    await repo.mark_success(task.task_id, "https://web.archive.org/web/test")
                elif spec.get("status") == TaskStatus.FAILED:
                    await repo.mark_failed(task.task_id, "test error")
            tasks.append(task)
    return tasks


# ---------------------------------------------------------------------------
# Property 4: Batch query by task_ids returns correct subset
# For any set of existing tasks and any subset of task IDs (including
# non-existent IDs), the batch endpoint SHALL return exactly those tasks
# whose IDs exist in the database, omitting non-existent IDs.
# ---------------------------------------------------------------------------


class TestProperty4BatchQueryByTaskIds:
    """Property 4: Batch query by task_ids returns correct subset.

    **Validates: Requirements 7.2, 7.6**
    """

    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        num_existing=st.integers(min_value=0, max_value=20),
        num_queried=st.integers(min_value=0, max_value=25),
        num_nonexistent=st.integers(min_value=0, max_value=10),
    )
    async def test_returns_only_existing_task_ids(
        self,
        session_factory: sessionmaker,
        num_existing: int,
        num_queried: int,
        num_nonexistent: int,
    ) -> None:
        """Batch query by task_ids SHALL return only tasks that exist.

        **Validates: Requirements 7.2, 7.6**

        Test Strategy:
            - Create N tasks in the database
            - Query with a mix of existing and non-existent task IDs
            - Verify only existing IDs are returned
        """
        # Create test tasks
        async with session_factory() as session:
            repo = TaskRepository(session)
            existing_tasks = []
            for i in range(num_existing):
                task = await repo.create_task(f"https://example-{i}.com/test")
                existing_tasks.append(task)

        # Build query list: subset of existing + non-existent IDs
        existing_ids = [t.task_id for t in existing_tasks]
        queried_ids = existing_ids[: min(num_queried, len(existing_ids))]

        # Add non-existent IDs (use invalid hex strings that don't exist)
        for i in range(num_nonexistent):
            queried_ids.append(f"{'0' * 31}{i}"[:32])

        if not queried_ids:
            # Skip if no IDs to query
            return

        # Create app and test client
        app = create_app(session_factory=session_factory)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/status/batch",
                params={"task_ids": ",".join(queried_ids)},
            )

        assert response.status_code == 200
        data = response.json()

        # Verify results
        returned_ids = {r["task_id"] for r in data["results"]}
        expected_ids = set(queried_ids) & set(existing_ids)

        assert returned_ids == expected_ids, (
            f"Expected {expected_ids}, got {returned_ids}"
        )

        # Verify total_requested matches query count
        assert data["total_requested"] == len(queried_ids)

        # Verify total_found matches result count
        assert data["total_found"] == len(returned_ids)

    @settings(
        max_examples=30,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        num_tasks=st.integers(min_value=1, max_value=10),
    )
    async def test_all_existing_ids_returned(
        self,
        session_factory: sessionmaker,
        num_tasks: int,
    ) -> None:
        """When querying only existing IDs, all SHALL be returned.

        **Validates: Requirements 7.2, 7.6**
        """
        # Create test tasks
        async with session_factory() as session:
            repo = TaskRepository(session)
            tasks = []
            for i in range(num_tasks):
                task = await repo.create_task(f"https://all-existing-{i}.com/page")
                tasks.append(task)

        queried_ids = [t.task_id for t in tasks]
        existing_ids = set(queried_ids)

        app = create_app(session_factory=session_factory)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/status/batch",
                params={"task_ids": ",".join(queried_ids)},
            )

        assert response.status_code == 200
        data = response.json()

        returned_ids = {r["task_id"] for r in data["results"]}
        assert returned_ids == existing_ids
        assert data["total_found"] == num_tasks


# ---------------------------------------------------------------------------
# Property 5: Batch query by URLs returns most recent task per URL
# For any set of URLs where each URL has one or more tasks, the batch
# endpoint SHALL return exactly one task per URL, and that task SHALL have
# the most recent created_at timestamp among all tasks for that URL.
# ---------------------------------------------------------------------------


class TestProperty5BatchQueryByUrls:
    """Property 5: Batch query by URLs returns most recent task per URL.

    **Validates: Requirements 7.3**
    """

    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        num_urls=st.integers(min_value=1, max_value=10),
        tasks_per_url=st.integers(min_value=1, max_value=5),
    )
    async def test_returns_most_recent_task_per_url(
        self,
        session_factory: sessionmaker,
        num_urls: int,
        tasks_per_url: int,
    ) -> None:
        """For each URL, batch query SHALL return the most recent task.

        **Validates: Requirements 7.3**

        Test Strategy:
            - Create multiple tasks for each URL with different timestamps
            - Query by URLs
            - Verify each URL returns exactly one task, the most recent one
        """
        # Track expected results
        expected_latest: dict[str, ArchiveTask] = {}

        async with session_factory() as session:
            repo = TaskRepository(session)

            for url_idx in range(num_urls):
                url = f"https://url-{url_idx}.example.com/page"

                # Create multiple tasks for this URL
                for task_idx in range(tasks_per_url):
                    task = await repo.create_task(url)

                    # Stagger created_at times
                    # Use negative offsets so earlier tasks have earlier timestamps
                    offset = timedelta(seconds=-(tasks_per_url - task_idx))
                    task.created_at = datetime.now(timezone.utc) + offset
                    await session.commit()
                    await session.refresh(task)

                # The most recent task is the last one created
                expected_latest[url] = task

        # Query all URLs
        urls_to_query = list(expected_latest.keys())

        app = create_app(session_factory=session_factory)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/status/batch",
                params={"url": ",".join(urls_to_query)},
            )

        assert response.status_code == 200
        data = response.json()

        # Verify one result per URL
        assert len(data["results"]) == num_urls

        # Verify each result is the most recent task for its URL
        results_by_url = {r["url"]: r for r in data["results"]}

        for url, expected_task in expected_latest.items():
            assert url in results_by_url, f"URL {url} not in results"
            assert results_by_url[url]["task_id"] == expected_task.task_id, (
                f"Expected task_id {expected_task.task_id} for URL {url}, "
                f"got {results_by_url[url]['task_id']}"
            )

    @settings(
        max_examples=30,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        num_urls=st.integers(min_value=1, max_value=5),
        include_nonexistent=st.booleans(),
    )
    async def test_omits_urls_without_tasks(
        self,
        session_factory: sessionmaker,
        num_urls: int,
        include_nonexistent: bool,
    ) -> None:
        """Batch query SHALL omit URLs that have no tasks.

        **Validates: Requirements 7.3**
        """
        # Create tasks for only some URLs
        urls_with_tasks = [f"https://exists-{i}.com/page" for i in range(num_urls)]

        async with session_factory() as session:
            repo = TaskRepository(session)
            for url in urls_with_tasks:
                await repo.create_task(url)

        # Add URLs without tasks
        urls_without_tasks = [f"https://no-tasks-{i}.com/page" for i in range(3)]

        all_urls = urls_with_tasks + (urls_without_tasks if include_nonexistent else [])

        if not all_urls:
            return

        app = create_app(session_factory=session_factory)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/status/batch",
                params={"url": ",".join(all_urls)},
            )

        assert response.status_code == 200
        data = response.json()

        # Verify only URLs with tasks are returned
        returned_urls = {r["url"] for r in data["results"]}
        assert returned_urls == set(urls_with_tasks)
        assert data["total_found"] == num_urls


# ---------------------------------------------------------------------------
# Property 6: Batch query rejects requests exceeding 50 identifiers
# For any request containing more than 50 combined identifiers
# (task_ids + urls), the batch endpoint SHALL return HTTP 422 and no task data.
# ---------------------------------------------------------------------------


class TestProperty6BatchQueryRejectsOverLimit:
    """Property 6: Batch query rejects requests exceeding 50 identifiers.

    **Validates: Requirements 7.4, 7.5**
    """

    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        num_task_ids=st.integers(min_value=0, max_value=60),
        num_urls=st.integers(min_value=0, max_value=60),
    )
    async def test_rejects_over_50_combined_identifiers(
        self,
        session_factory: sessionmaker,
        num_task_ids: int,
        num_urls: int,
    ) -> None:
        """Requests with > 50 combined identifiers SHALL return HTTP 422.

        **Validates: Requirements 7.4, 7.5**

        Test Strategy:
            - Create tasks for querying
            - Send requests with varying combinations of task_ids and urls
            - Verify 422 response when combined count > 50
        """
        # Create some test tasks
        async with session_factory() as session:
            repo = TaskRepository(session)
            for i in range(min(60, max(num_task_ids, num_urls))):
                await repo.create_task(f"https://limit-test-{i}.com/page")

        # Build query parameters
        task_ids = [f"taskid{i:032d}"[-32:] for i in range(num_task_ids)]
        urls = [f"https://limit-test-{i}.com/page" for i in range(num_urls)]

        total_identifiers = num_task_ids + num_urls

        # Skip the edge case of 0 identifiers (that's a different validation)
        if total_identifiers == 0:
            return

        app = create_app(session_factory=session_factory)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            params = {}
            if task_ids:
                params["task_ids"] = ",".join(task_ids)
            if urls:
                params["url"] = ",".join(urls)

            response = await client.get("/api/status/batch", params=params)

        if total_identifiers > 50:
            # Should return 422
            assert response.status_code == 422, (
                f"Expected 422 for {total_identifiers} identifiers, "
                f"got {response.status_code}"
            )
            data = response.json()
            assert "detail" in data
            assert "50" in data["detail"] or "Maximum" in data["detail"]
        else:
            # Should succeed (200) or fail with 422 for empty params
            assert response.status_code in (200, 422)

    @settings(
        max_examples=30,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        num_identifiers=st.integers(min_value=51, max_value=100),
    )
    async def test_exactly_51_identifiers_returns_422(
        self,
        session_factory: sessionmaker,
        num_identifiers: int,
    ) -> None:
        """Any request with > 50 identifiers SHALL return 422.

        **Validates: Requirements 7.4, 7.5**
        """
        task_ids = [f"tid{i:032d}"[-32:] for i in range(num_identifiers)]

        app = create_app(session_factory=session_factory)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/status/batch",
                params={"task_ids": ",".join(task_ids)},
            )

        assert response.status_code == 422
        data = response.json()
        assert "detail" in data

    async def test_exactly_50_identifiers_succeeds(
        self,
        session_factory: sessionmaker,
    ) -> None:
        """Requests with exactly 50 identifiers SHALL succeed.

        **Validates: Requirements 7.4, 7.5**

        This is a boundary test to ensure the limit is exclusive (> 50, not >= 50).
        """
        # Create 50 tasks
        async with session_factory() as session:
            repo = TaskRepository(session)
            for i in range(50):
                await repo.create_task(f"https://boundary-{i}.com/page")

        task_ids = [f"boundary{i:032d}"[-32:] for i in range(50)]

        app = create_app(session_factory=session_factory)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/status/batch",
                params={"task_ids": ",".join(task_ids)},
            )

        # Should succeed with 200 (even if no tasks found)
        assert response.status_code == 200
        data = response.json()
        assert "results" in data
        assert "total_requested" in data
        assert data["total_requested"] == 50


# ---------------------------------------------------------------------------
# Unit Tests: Edge Cases and Mixed Queries
# ---------------------------------------------------------------------------


class TestBatchQueryEdgeCases:
    """Unit tests for batch query edge cases."""

    async def test_empty_query_returns_422(
        self,
        session_factory: sessionmaker,
    ) -> None:
        """Request with no parameters SHALL return 422.

        **Validates: Requirements 7.5**
        """
        app = create_app(session_factory=session_factory)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/status/batch")

        assert response.status_code == 422
        data = response.json()
        assert "detail" in data

    async def test_mixed_task_ids_and_urls(
        self,
        session_factory: sessionmaker,
    ) -> None:
        """Batch query with both task_ids and urls SHALL merge results.

        **Validates: Requirements 7.2, 7.3**
        """
        async with session_factory() as session:
            repo = TaskRepository(session)
            # Create task to query by ID
            task_by_id = await repo.create_task("https://by-id.example.com/page")
            # Create multiple tasks for URL query
            await repo.create_task("https://by-url.example.com/page")
            latest_for_url = await repo.create_task("https://by-url.example.com/page")

        app = create_app(session_factory=session_factory)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/status/batch",
                params={
                    "task_ids": task_by_id.task_id,
                    "url": "https://by-url.example.com/page",
                },
            )

        assert response.status_code == 200
        data = response.json()

        # Should have 2 results: one by ID, one by URL
        assert data["total_found"] == 2
        returned_ids = {r["task_id"] for r in data["results"]}
        assert task_by_id.task_id in returned_ids
        assert latest_for_url.task_id in returned_ids

    async def test_duplicate_task_in_both_query_types(
        self,
        session_factory: sessionmaker,
    ) -> None:
        """When a task matches both task_ids and urls, it SHALL appear only once.

        **Validates: Requirements 7.2, 7.3**
        """
        async with session_factory() as session:
            repo = TaskRepository(session)
            task = await repo.create_task("https://duplicate.example.com/page")

        app = create_app(session_factory=session_factory)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/status/batch",
                params={
                    "task_ids": task.task_id,
                    "url": "https://duplicate.example.com/page",
                },
            )

        assert response.status_code == 200
        data = response.json()

        # Should have exactly 1 result (deduplicated)
        assert data["total_found"] == 1
        assert data["results"][0]["task_id"] == task.task_id

    async def test_whitespace_in_comma_separated_params(
        self,
        session_factory: sessionmaker,
    ) -> None:
        """Whitespace around commas SHALL be handled gracefully.

        **Validates: Requirements 7.2**
        """
        async with session_factory() as session:
            repo = TaskRepository(session)
            task1 = await repo.create_task("https://whitespace1.example.com/page")
            task2 = await repo.create_task("https://whitespace2.example.com/page")

        app = create_app(session_factory=session_factory)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/status/batch",
                params={"task_ids": f" {task1.task_id} , {task2.task_id} "},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["total_found"] == 2
