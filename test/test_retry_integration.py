"""Integration tests for 改進 C — retry_task wiring to the Worker pickup rule.

# Feature: keeplink-v1x-improvements

These tests verify the *接線* (wiring) between ``TaskRepository.retry_task`` and
the existing Worker pickup pipeline ``TaskRepository.fetch_pending_tasks``:
after a permanently ``failed`` task is retried, it must reappear in the set of
pending tasks the Worker fetches on its next poll.

Design note (Req 2.5): retry clears ``next_retry_at`` (and resets status to
``pending``), which is exactly the condition ``fetch_pending_tasks`` filters on,
so the task is picked up on the next poll without any schema change.

Validates: Requirements 2.5
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import sessionmaker

from keeplink_mcp.db.models import ArchiveTask, TaskStatus
from keeplink_mcp.db.repository import TaskRepository


class TestRetryReQueuesForWorkerPickup:
    """retry_task must make a failed task visible to fetch_pending_tasks."""

    async def test_failed_task_reappears_in_pending_after_retry(
        self, session_factory: sessionmaker
    ) -> None:
        """A failed task is absent from the pending set, then present after retry."""
        async with session_factory() as session:
            repo = TaskRepository(session)
            task = await repo.create_task("https://example.com/requeue")
            await repo.mark_processing(task.task_id)
            await repo.mark_failed(task.task_id, "non-retryable error")

            # Precondition: a failed task is NOT picked up by the Worker.
            before = await repo.fetch_pending_tasks()
            assert all(t.task_id != task.task_id for t in before)

            result = await repo.retry_task(task.task_id)
            assert isinstance(result, ArchiveTask)
            assert result.status == TaskStatus.PENDING

            # Wiring assertion: the retried task now appears in the pending set.
            after = await repo.fetch_pending_tasks()
            assert task.task_id in {t.task_id for t in after}

    async def test_retry_clears_future_next_retry_at_so_worker_picks_it_up(
        self, session_factory: sessionmaker
    ) -> None:
        """A failed task with a future next_retry_at is still picked up after retry.

        fetch_pending_tasks excludes pending tasks whose next_retry_at is in the
        future. retry_task resets next_retry_at to NULL, which is why the task
        becomes immediately eligible on the next poll (Req 2.5).
        """
        async with session_factory() as session:
            repo = TaskRepository(session)
            task = await repo.create_task("https://example.com/backoff")
            await repo.mark_processing(task.task_id)
            await repo.mark_failed(task.task_id, "some error")

            # Simulate a stale backoff timestamp in the future on the failed task.
            future = datetime.now(timezone.utc) + timedelta(hours=1)
            failed = await repo.get_task(task.task_id)
            assert failed is not None
            failed.next_retry_at = future
            session.add(failed)
            await session.commit()

            result = await repo.retry_task(task.task_id)
            assert isinstance(result, ArchiveTask)
            assert result.next_retry_at is None

            # Because next_retry_at was cleared, the Worker picks it up now.
            after = await repo.fetch_pending_tasks()
            assert task.task_id in {t.task_id for t in after}
