"""Background worker that polls pending tasks and archives them via Internet Archive SPN2 API.

Architecture: The worker runs an async polling loop inside the FastAPI Service process.
Each cycle fetches up to `worker_concurrency` pending tasks and processes them in parallel
using asyncio.gather. Waybackpy's synchronous save() is offloaded to a thread executor
to avoid blocking the event loop.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from functools import partial
from typing import TYPE_CHECKING

import waybackpy

from keeplink_mcp.worker.error_classifier import ErrorCategory, classify_error

if TYPE_CHECKING:
    from sqlalchemy.orm import sessionmaker

    from keeplink_mcp.config import Config
    from keeplink_mcp.db.models import ArchiveTask


# Default user-agent for Wayback Machine SPN2 API requests.
_USER_AGENT = "KeepLink-MCP/1.0 (https://github.com/keeplink/keeplink-mcp)"


class BackgroundWorker:
    """Background worker that polls pending tasks and archives them via SPN2 API.

    Lifecycle: instantiate → start() → runs until stop() is called →
    completes current tasks → exits.
    """

    def __init__(
        self,
        config: Config,
        session_factory: sessionmaker,
        logger: logging.Logger,
    ) -> None:
        """Initialize the background worker.

        Args:
            config: Application configuration (poll interval, concurrency, backoff settings).
            session_factory: Async SQLAlchemy session factory for DB access.
            logger: Structured logger instance for operational visibility.
        """
        self._config = config
        self._session_factory = session_factory
        self._logger = logger
        self._running = False

    async def start(self) -> None:
        """Start the polling loop. Runs until stop() sets _running to False."""
        self._running = True
        self._logger.info("Background worker started", extra={"action": "worker_start"})

        while self._running:
            try:
                await self._poll_and_process()
            except Exception:
                # Never crash the loop — log and continue on next cycle.
                self._logger.exception("Unexpected error in poll cycle")

            # Sleep between cycles, but check _running flag to allow fast shutdown.
            if self._running:
                await asyncio.sleep(self._config.worker_poll_interval_sec)

        self._logger.info("Background worker stopped", extra={"action": "worker_stop"})

    async def stop(self) -> None:
        """Gracefully stop the worker after the current processing cycle completes."""
        self._logger.info(
            "Background worker stop requested",
            extra={"action": "worker_stop_request"},
        )
        self._running = False

    async def _poll_and_process(self) -> None:
        """Single poll cycle: fetch pending tasks and process them concurrently."""
        from keeplink_mcp.db.repository import TaskRepository

        async with self._session_factory() as session:
            repo = TaskRepository(session)
            tasks = await repo.fetch_pending_tasks(limit=self._config.worker_concurrency)

        if not tasks:
            return

        self._logger.info(
            "Processing batch",
            extra={"action": "poll_batch", "task_count": len(tasks)},
        )

        # Process tasks concurrently; each gets its own DB session.
        await asyncio.gather(
            *(self._process_task(task) for task in tasks),
            return_exceptions=True,
        )

    async def _process_task(self, task: ArchiveTask) -> None:
        """Process a single archive task through the SPN2 API.

        Flow: mark_processing → call SPN2 → success/retry/fail based on result.
        All exceptions are caught to guarantee the worker never crashes.
        """
        from keeplink_mcp.db.repository import TaskRepository

        async with self._session_factory() as session:
            repo = TaskRepository(session)
            await repo.mark_processing(task.task_id)

        try:
            archive_url = await self._call_spn2(task.url)
            await self._handle_success(task, archive_url)
        except Exception as exc:
            await self._handle_error(task, exc)

    async def _call_spn2(self, url: str) -> str:
        """Call the Wayback Machine SPN2 API to save a URL.

        Runs the synchronous waybackpy call in a thread executor
        to avoid blocking the async event loop.

        Args:
            url: The URL to archive.

        Returns:
            The resulting archive URL from Wayback Machine.
        """
        loop = asyncio.get_running_loop()
        save_api = waybackpy.WaybackMachineSaveAPI(url, _USER_AGENT)
        result = await loop.run_in_executor(None, partial(save_api.save))
        return result.archive_url

    async def _handle_success(self, task: ArchiveTask, archive_url: str) -> None:
        """Mark task as successfully archived."""
        from keeplink_mcp.db.repository import TaskRepository

        async with self._session_factory() as session:
            repo = TaskRepository(session)
            await repo.mark_success(task.task_id, archive_url)

        self._logger.info(
            "Archive success",
            extra={
                "action": "archive_success",
                "task_id": task.task_id,
                "url": task.url,
                "archive_url": archive_url,
            },
        )

    async def _handle_error(self, task: ArchiveTask, exc: Exception) -> None:
        """Classify error and either schedule retry or mark as failed.

        Retry logic: if the error is retryable AND retry_count < max_retry_count,
        schedule a retry with exponential backoff. Otherwise, mark permanently failed.
        """
        from keeplink_mcp.db.repository import TaskRepository

        # Extract HTTP status code from httpx-based exceptions if available.
        status_code = _extract_status_code(exc)
        category = classify_error(status_code=status_code, exception=exc)
        error_msg = f"{type(exc).__name__}: {exc}"

        async with self._session_factory() as session:
            repo = TaskRepository(session)

            can_retry = (
                category == ErrorCategory.RETRYABLE
                and task.retry_count < self._config.max_retry_count
            )
            if can_retry:
                backoff = self._calculate_backoff(task.retry_count)
                next_retry = datetime.now(timezone.utc) + timedelta(seconds=backoff)
                await repo.schedule_retry(task.task_id, error_msg, next_retry)

                self._logger.warning(
                    "Archive retry scheduled",
                    extra={
                        "action": "archive_retry",
                        "task_id": task.task_id,
                        "url": task.url,
                        "retry_count": task.retry_count + 1,
                        "backoff_sec": backoff,
                    },
                )
            else:
                await repo.mark_failed(task.task_id, error_msg)

                self._logger.error(
                    "Archive failed permanently",
                    extra={
                        "action": "archive_failed",
                        "task_id": task.task_id,
                        "url": task.url,
                        "error": error_msg,
                        "category": category.value,
                    },
                )

    def _calculate_backoff(self, retry_count: int) -> float:
        """Calculate exponential backoff delay for a given retry attempt.

        Formula: base_backoff_sec * 2^retry_count

        Args:
            retry_count: The current retry count (0-based, before increment).

        Returns:
            Backoff delay in seconds.
        """
        return self._config.base_backoff_sec * (2**retry_count)


def _extract_status_code(exc: Exception) -> int | None:
    """Attempt to extract an HTTP status code from an exception.

    Supports httpx.HTTPStatusError which carries a response object,
    and waybackpy exceptions that may expose a status_code attribute.

    Args:
        exc: The exception to inspect.

    Returns:
        HTTP status code if extractable, None otherwise.
    """
    # httpx.HTTPStatusError has response.status_code
    if hasattr(exc, "response") and hasattr(exc.response, "status_code"):
        return exc.response.status_code

    # Some waybackpy exceptions carry status_code directly
    if hasattr(exc, "status_code"):
        return exc.status_code

    return None
