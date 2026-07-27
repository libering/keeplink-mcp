"""Data access layer for ArchiveTask CRUD operations.

Single Responsibility: encapsulates all DB queries so other layers
never import sqlalchemy directly.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from keeplink_mcp.db.models import ArchiveTask, TaskStatus


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

    async def _update_status(self, task_id: str, status: TaskStatus) -> None:
        """Generic status update helper."""
        stmt = (
            update(ArchiveTask)
            .where(ArchiveTask.task_id == task_id)
            .values(status=status, updated_at=datetime.now(timezone.utc))
        )
        await self._session.execute(stmt)
        await self._session.commit()
