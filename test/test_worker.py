"""Integration tests for BackgroundWorker complete flow.

# Feature: keeplink-mcp

Tests cover:
1. Success flow: mock SPN2 returns archive_url → task transitions pending → processing → success
2. Retryable error: mock SPN2 raises 429 → retry_count +1, next_retry_at set, status back to pending
3. Non-retryable error: mock SPN2 raises 403 → task immediately failed
4. Max retries exhausted: retry_count at max → mark failed
5. Property 7: next_retry_at in the future → task not fetched by fetch_pending_tasks

Validates: Requirements 6.2, 6.3, 6.6, 7.1, 7.2, 7.3, 7.4, 8.1, 8.3
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import httpx
from sqlalchemy.orm import sessionmaker

from keeplink_mcp.config import Config
from keeplink_mcp.db.models import ArchiveTask, TaskStatus
from keeplink_mcp.db.repository import TaskRepository
from keeplink_mcp.worker.archiver import BackgroundWorker

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(max_retries: int = 5, base_backoff: float = 60.0) -> Config:
    """Build a Config suitable for testing with overridden retry settings."""
    return Config(
        max_retry_count=max_retries,
        base_backoff_sec=base_backoff,
        worker_poll_interval_sec=0.1,
        worker_concurrency=5,
    )


def _make_http_status_error(status_code: int) -> httpx.HTTPStatusError:
    """Create an httpx.HTTPStatusError with the given status code."""
    response = httpx.Response(status_code=status_code, request=httpx.Request("GET", "http://x"))
    return httpx.HTTPStatusError(
        message=f"HTTP {status_code}",
        request=response.request,
        response=response,
    )


async def _create_task(session_factory: sessionmaker, url: str = "https://example.com") -> str:
    """Insert a pending task and return its task_id."""
    async with session_factory() as session:
        repo = TaskRepository(session)
        task = await repo.create_task(url)
        return task.task_id


async def _get_task(session_factory: sessionmaker, task_id: str) -> ArchiveTask | None:
    """Fetch a task by ID for assertion."""
    async with session_factory() as session:
        repo = TaskRepository(session)
        return await repo.get_task(task_id)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestWorkerSuccessFlow:
    """Test that a successful SPN2 call transitions task to success."""

    async def test_success_flow(self, session_factory: sessionmaker) -> None:
        """Mock _call_spn2 returns archive URL → task ends up in success state."""
        config = _make_config()
        logger = logging.getLogger("test_worker")
        worker = BackgroundWorker(config, session_factory, logger)

        task_id = await _create_task(session_factory, "https://example.com/page1")

        expected_archive_url = "https://web.archive.org/web/20240101/https://example.com/page1"

        with patch.object(worker, "_call_spn2", new_callable=AsyncMock) as mock_spn2:
            mock_spn2.return_value = expected_archive_url
            await worker._poll_and_process()

        task = await _get_task(session_factory, task_id)
        assert task is not None
        assert task.status == TaskStatus.SUCCESS
        assert task.result_url == expected_archive_url
        assert task.retry_count == 0


class TestWorkerRetryableError:
    """Test that a retryable error (429) schedules a retry."""

    async def test_retryable_429_schedules_retry(self, session_factory: sessionmaker) -> None:
        """429 error → retry_count +1, next_retry_at in the future, status reverts to pending."""
        config = _make_config(max_retries=5, base_backoff=60.0)
        logger = logging.getLogger("test_worker")
        worker = BackgroundWorker(config, session_factory, logger)

        task_id = await _create_task(session_factory, "https://example.com/rate-limited")

        with patch.object(worker, "_call_spn2", new_callable=AsyncMock) as mock_spn2:
            mock_spn2.side_effect = _make_http_status_error(429)
            await worker._poll_and_process()

        task = await _get_task(session_factory, task_id)
        assert task is not None
        assert task.status == TaskStatus.PENDING
        assert task.retry_count == 1
        assert task.next_retry_at is not None
        # SQLite may return naive datetimes; normalize for comparison
        next_retry = task.next_retry_at
        if next_retry.tzinfo is None:
            next_retry = next_retry.replace(tzinfo=timezone.utc)
        assert next_retry > datetime.now(timezone.utc)


class TestWorkerNonRetryableError:
    """Test that a non-retryable error (403) marks task as failed immediately."""

    async def test_non_retryable_403_fails_immediately(self, session_factory: sessionmaker) -> None:
        """403 error → task directly marked as failed, retry_count stays 0."""
        config = _make_config()
        logger = logging.getLogger("test_worker")
        worker = BackgroundWorker(config, session_factory, logger)

        task_id = await _create_task(session_factory, "https://example.com/forbidden")

        with patch.object(worker, "_call_spn2", new_callable=AsyncMock) as mock_spn2:
            mock_spn2.side_effect = _make_http_status_error(403)
            await worker._poll_and_process()

        task = await _get_task(session_factory, task_id)
        assert task is not None
        assert task.status == TaskStatus.FAILED
        assert task.retry_count == 0
        assert task.error_message is not None
        assert "403" in task.error_message


class TestWorkerMaxRetriesExhausted:
    """Test that a task at max retry_count is marked as failed."""

    async def test_max_retries_marks_failed(self, session_factory: sessionmaker) -> None:
        """When retry_count reaches max, task is marked failed even for retryable errors."""
        max_retries = 5
        config = _make_config(max_retries=max_retries, base_backoff=60.0)
        logger = logging.getLogger("test_worker")
        worker = BackgroundWorker(config, session_factory, logger)

        task_id = await _create_task(session_factory, "https://example.com/exhausted")

        # Manually set retry_count to max to simulate exhaustion
        async with session_factory() as session:
            repo = TaskRepository(session)
            task = await repo.get_task(task_id)
            assert task is not None
            task.retry_count = max_retries
            await session.commit()

        with patch.object(worker, "_call_spn2", new_callable=AsyncMock) as mock_spn2:
            mock_spn2.side_effect = _make_http_status_error(429)
            await worker._poll_and_process()

        task = await _get_task(session_factory, task_id)
        assert task is not None
        assert task.status == TaskStatus.FAILED
        assert task.retry_count == max_retries  # not incremented further
        assert task.error_message is not None


class TestWorkerSkipsFutureRetry:
    """Property 7: tasks with next_retry_at in the future are not fetched.

    **Validates: Requirements 6.6, 7.4**
    """

    async def test_future_next_retry_at_not_fetched(self, session_factory: sessionmaker) -> None:
        """A pending task with next_retry_at in the future should NOT be processed."""
        config = _make_config()
        logger = logging.getLogger("test_worker")
        worker = BackgroundWorker(config, session_factory, logger)

        task_id = await _create_task(session_factory, "https://example.com/future-retry")

        # Set next_retry_at far in the future
        async with session_factory() as session:
            repo = TaskRepository(session)
            task = await repo.get_task(task_id)
            assert task is not None
            task.next_retry_at = datetime.now(timezone.utc) + timedelta(hours=1)
            await session.commit()

        with patch.object(worker, "_call_spn2", new_callable=AsyncMock) as mock_spn2:
            await worker._poll_and_process()
            # _call_spn2 should never be called because the task is skipped
            mock_spn2.assert_not_called()

        # Task should remain in PENDING state, untouched
        task = await _get_task(session_factory, task_id)
        assert task is not None
        assert task.status == TaskStatus.PENDING

    async def test_past_next_retry_at_is_fetched(self, session_factory: sessionmaker) -> None:
        """A pending task with next_retry_at in the past SHOULD be processed."""
        config = _make_config()
        logger = logging.getLogger("test_worker")
        worker = BackgroundWorker(config, session_factory, logger)

        task_id = await _create_task(session_factory, "https://example.com/past-retry")

        # Set next_retry_at in the past
        async with session_factory() as session:
            repo = TaskRepository(session)
            task = await repo.get_task(task_id)
            assert task is not None
            task.next_retry_at = datetime.now(timezone.utc) - timedelta(minutes=5)
            await session.commit()

        expected_url = "https://web.archive.org/web/20240101/https://example.com/past-retry"

        with patch.object(worker, "_call_spn2", new_callable=AsyncMock) as mock_spn2:
            mock_spn2.return_value = expected_url
            await worker._poll_and_process()
            mock_spn2.assert_called_once()

        task = await _get_task(session_factory, task_id)
        assert task is not None
        assert task.status == TaskStatus.SUCCESS

    async def test_null_next_retry_at_is_fetched(self, session_factory: sessionmaker) -> None:
        """A pending task with next_retry_at=None (fresh task) SHOULD be processed."""
        config = _make_config()
        logger = logging.getLogger("test_worker")
        worker = BackgroundWorker(config, session_factory, logger)

        task_id = await _create_task(session_factory, "https://example.com/fresh")

        expected_url = "https://web.archive.org/web/20240101/https://example.com/fresh"

        with patch.object(worker, "_call_spn2", new_callable=AsyncMock) as mock_spn2:
            mock_spn2.return_value = expected_url
            await worker._poll_and_process()
            mock_spn2.assert_called_once()

        task = await _get_task(session_factory, task_id)
        assert task is not None
        assert task.status == TaskStatus.SUCCESS
