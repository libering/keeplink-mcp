"""Data access layer for ArchiveTask CRUD operations.

Single Responsibility: encapsulates all DB queries so other layers
never import sqlalchemy directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from keeplink_mcp.db.models import ArchiveTask, TaskStatus


@dataclass(frozen=True)
class RetryRejected:
    """Returned when the task exists but is NOT in 'failed' status.

    Carries current_status so the endpoint can report which status blocked
    the transition (Req 2.6) without a second DB read.
    """

    current_status: TaskStatus


class TaskRepository:
    """Repository pattern for ArchiveTask persistence."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_task(self, url: str) -> ArchiveTask:
        """Insert a new pending task into the queue.

        Args:
            url: The target URL to be archived.

        Returns:
            The newly created ArchiveTask with generated task_id.
        """
        task = ArchiveTask(url=url)
        self._session.add(task)
        await self._session.commit()
        await self._session.refresh(task)
        return task

    async def get_task(self, task_id: str) -> ArchiveTask | None:
        """Retrieve a task by its ID.

        Args:
            task_id: UUID hex string.

        Returns:
            The ArchiveTask or None if not found.
        """
        stmt = select(ArchiveTask).where(ArchiveTask.task_id == task_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_recent_task(
        self, url: str, window_hours: int = 24
    ) -> ArchiveTask | None:
        """Find the most recent dedup-eligible task for a URL within a time window.

        Used for deduplication: if a pending/processing/success task already exists
        within the window, callers should reuse it instead of creating a new task.

        Args:
            url: The target URL to look up.
            window_hours: How far back (in hours) to search. Defaults to 24.

        Returns:
            The most recent ArchiveTask with status in {pending, processing, success},
            or None if no such task exists within the window (only failed or none).
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
        dedup_statuses = (
            TaskStatus.PENDING,
            TaskStatus.PROCESSING,
            TaskStatus.SUCCESS,
        )

        stmt = (
            select(ArchiveTask)
            .where(
                ArchiveTask.url == url,
                ArchiveTask.created_at >= cutoff,
                ArchiveTask.status.in_(dedup_statuses),
            )
            .order_by(ArchiveTask.created_at.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_latest_task_by_url(self, url: str) -> ArchiveTask | None:
        """Retrieve the most recent task for a URL regardless of status.

        Unlike find_recent_task, this has no time window and includes failed tasks.
        Used by GET /api/status?url= to return any recent activity for a URL.

        Args:
            url: The target URL to look up.

        Returns:
            The most recent ArchiveTask for this URL, or None if none exists.
        """
        stmt = (
            select(ArchiveTask)
            .where(ArchiveTask.url == url)
            .order_by(ArchiveTask.created_at.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def fetch_pending_tasks(self, limit: int = 10) -> list[ArchiveTask]:
        """Fetch oldest pending tasks that are ready to process.

        Only returns tasks where next_retry_at is either null (never retried)
        or already past (backoff window expired).

        Args:
            limit: Maximum number of tasks to return.

        Returns:
            List of pending ArchiveTask ordered by created_at ascending.
        """
        now = datetime.now(timezone.utc)
        stmt = (
            select(ArchiveTask)
            .where(
                ArchiveTask.status == TaskStatus.PENDING,
                or_(
                    ArchiveTask.next_retry_at.is_(None),
                    ArchiveTask.next_retry_at <= now,
                ),
            )
            .order_by(ArchiveTask.created_at.asc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def mark_processing(self, task_id: str) -> None:
        """Transition a task to processing state."""
        await self._update_status(task_id, TaskStatus.PROCESSING)

    async def mark_success(self, task_id: str, result_url: str) -> None:
        """Mark a task as successfully archived.

        Args:
            task_id: The task to update.
            result_url: The Wayback Machine URL for the archived page.
        """
        stmt = (
            update(ArchiveTask)
            .where(ArchiveTask.task_id == task_id)
            .values(
                status=TaskStatus.SUCCESS,
                result_url=result_url,
                error_message=None,
                updated_at=datetime.now(timezone.utc),
            )
        )
        await self._session.execute(stmt)
        await self._session.commit()

    async def mark_failed(self, task_id: str, error: str) -> None:
        """Mark a task as permanently failed (max retries exhausted or non-retryable error).

        Args:
            task_id: The task to update.
            error: Human-readable error description.
        """
        stmt = (
            update(ArchiveTask)
            .where(ArchiveTask.task_id == task_id)
            .values(
                status=TaskStatus.FAILED,
                error_message=error,
                updated_at=datetime.now(timezone.utc),
            )
        )
        await self._session.execute(stmt)
        await self._session.commit()

    async def schedule_retry(
        self, task_id: str, error: str, next_retry_at: datetime
    ) -> None:
        """Revert a task to pending with incremented retry count and scheduled retry time.

        Args:
            task_id: The task to retry.
            error: The error that triggered the retry.
            next_retry_at: The earliest time at which this task should be retried
                (caller computes based on exponential backoff strategy).
        """
        stmt = (
            update(ArchiveTask)
            .where(ArchiveTask.task_id == task_id)
            .values(
                status=TaskStatus.PENDING,
                retry_count=ArchiveTask.retry_count + 1,
                error_message=error,
                next_retry_at=next_retry_at,
                updated_at=datetime.now(timezone.utc),
            )
        )
        await self._session.execute(stmt)
        await self._session.commit()

    async def recover_processing_tasks(self) -> int:
        """Bulk transition all processing tasks to pending.

        Used at startup to recover tasks left in 'processing' state due to
        unexpected shutdowns. Preserves existing retry_count and clears
        next_retry_at to allow immediate retry.

        Returns:
            Number of tasks that were recovered (affected row count).
        """
        stmt = (
            update(ArchiveTask)
            .where(ArchiveTask.status == TaskStatus.PROCESSING)
            .values(
                status=TaskStatus.PENDING,
                next_retry_at=None,
                updated_at=datetime.now(timezone.utc),
            )
        )
        result = await self._session.execute(stmt)
        await self._session.commit()
        return result.rowcount

    async def retry_task(
        self, task_id: str
    ) -> ArchiveTask | RetryRejected | None:
        """Re-queue a permanently failed task by resetting it to a clean pending state.

        State-transition invariant: ONLY 'failed' -> 'pending' is permitted. No
        other status is ever modified (Req 2.6).

        Behavior:
        - Task not found                -> return None (endpoint -> 404, Req 2.7).
        - Task exists but status != failed -> return RetryRejected(current_status)
          WITHOUT modifying any field (endpoint -> 409, Req 2.6, 2.9).
        - Task status == failed         -> set status=pending, retry_count=0,
          next_retry_at=NULL, error_message=NULL; return the refreshed ArchiveTask
          (Req 2.3, 2.4). The cleared next_retry_at lets fetch_pending_tasks pick
          it up on the next poll (Req 2.5).

        Performs a status-guarded UPDATE (WHERE task_id=... AND status='failed')
        so the failed->pending transition is atomic and cannot race a concurrent
        Worker transition.
        """
        # Status-guarded UPDATE: the WHERE clause makes the failed->pending
        # transition atomic. If the task is not 'failed' (or does not exist),
        # rowcount is 0 and no field is touched (Req 2.6 no-mutation guarantee).
        stmt = (
            update(ArchiveTask)
            .where(
                ArchiveTask.task_id == task_id,
                ArchiveTask.status == TaskStatus.FAILED,
            )
            .values(
                status=TaskStatus.PENDING,
                retry_count=0,
                next_retry_at=None,
                error_message=None,
                updated_at=datetime.now(timezone.utc),
            )
        )
        result = await self._session.execute(stmt)
        await self._session.commit()

        if result.rowcount == 1:
            # Transition succeeded; re-read to return the refreshed task.
            return await self.get_task(task_id)

        # rowcount == 0: either the task does not exist, or it exists but was
        # not 'failed'. One get_task discriminates the two cases.
        task = await self.get_task(task_id)
        if task is None:
            return None
        return RetryRejected(current_status=task.status)

    async def _update_status(self, task_id: str, status: TaskStatus) -> None:
        """Generic status update helper."""
        stmt = (
            update(ArchiveTask)
            .where(ArchiveTask.task_id == task_id)
            .values(status=status, updated_at=datetime.now(timezone.utc))
        )
        await self._session.execute(stmt)
        await self._session.commit()

    async def get_tasks_by_ids(self, task_ids: list[str]) -> list[ArchiveTask]:
        """Fetch tasks matching the given IDs.

        Returns only tasks that exist in the database, omitting any non-existent IDs.
        Used by batch status endpoint to query multiple tasks by ID in a single request.

        Args:
            task_ids: List of task ID strings to look up.

        Returns:
            List of ArchiveTask objects for existing IDs, in no guaranteed order.
        """
        if not task_ids:
            return []

        stmt = select(ArchiveTask).where(ArchiveTask.task_id.in_(task_ids))
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_latest_tasks_by_urls(self, urls: list[str]) -> list[ArchiveTask]:
        """For each URL, return the most recent task.

        Omits URLs with no tasks. Uses a subquery to find the max created_at
        per URL, then joins back to get the full task row.

        Args:
            urls: List of URL strings to look up.

        Returns:
            List of ArchiveTask objects, one per URL that has tasks.
            Each returned task is the most recent one for its URL.
        """
        if not urls:
            return []

        # Subquery: find (url, max_created_at) for each URL
        latest_subq = (
            select(
                ArchiveTask.url,
                func.max(ArchiveTask.created_at).label("max_created_at"),
            )
            .where(ArchiveTask.url.in_(urls))
            .group_by(ArchiveTask.url)
            .subquery()
        )

        # Join back to get full task rows
        stmt = (
            select(ArchiveTask)
            .join(
                latest_subq,
                (ArchiveTask.url == latest_subq.c.url)
                & (ArchiveTask.created_at == latest_subq.c.max_created_at),
            )
        )

        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_queue_stats(self, window_hours: int = 24) -> dict[str, int]:
        """Return queue statistics for health check.

        Computes:
        - queue_depth: count of tasks in 'pending' status
        - success_count: tasks with 'success' status within the time window
        - failure_count: tasks with 'failed' status within the time window

        Args:
            window_hours: Time window in hours for success/failure counts.
                Defaults to 24 hours.

        Returns:
            Dict with keys: queue_depth, success_count, failure_count.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)

        # Single query with GROUP BY status + time window filter
        # Counts pending (no time filter) and success/failed within window
        stmt = (
            select(
                ArchiveTask.status,
                func.count(ArchiveTask.task_id).label("count"),
            )
            .where(
                or_(
                    ArchiveTask.status == TaskStatus.PENDING,
                    ArchiveTask.status == TaskStatus.SUCCESS,
                    ArchiveTask.status == TaskStatus.FAILED,
                )
            )
            .where(
                or_(
                    ArchiveTask.status == TaskStatus.PENDING,
                    ArchiveTask.updated_at >= cutoff,
                )
            )
            .group_by(ArchiveTask.status)
        )

        result = await self._session.execute(stmt)
        rows = result.all()

        # Initialize defaults
        stats = {
            "queue_depth": 0,
            "success_count": 0,
            "failure_count": 0,
        }

        # Map results to stats dict
        for row in rows:
            status, count = row.status, row.count
            if status == TaskStatus.PENDING:
                stats["queue_depth"] = count
            elif status == TaskStatus.SUCCESS:
                stats["success_count"] = count
            elif status == TaskStatus.FAILED:
                stats["failure_count"] = count

        return stats
