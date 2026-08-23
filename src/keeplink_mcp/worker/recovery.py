"""Stuck task recovery: transitions orphaned processing tasks back to pending on startup.

When the service terminates unexpectedly (crash, SIGKILL, power failure),
tasks that were being processed may be left in 'processing' state. This module
provides a startup recovery mechanism to revert those tasks to 'pending' so
they can be retried.

Usage:
    At service startup, before the worker polling loop begins:

        recovered = await recover_stuck_tasks(session_factory, logger)
        if recovered > 0:
            logger.info("Recovered %d stuck tasks", recovered)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import sessionmaker


async def recover_stuck_tasks(
    session_factory: sessionmaker,
    logger: logging.Logger,
) -> int:
    """Find all tasks stuck in 'processing' and revert them to 'pending'.

    Must be called BEFORE the worker polling loop starts to ensure
    orphaned tasks are re-queued for processing.

    Args:
        session_factory: Async session factory for DB access.
        logger: Logger for recording recovery count.

    Returns:
        Number of tasks that were recovered (transitioned from processing to pending).

    Raises:
        DatabaseError: If the recovery query or update fails. The caller should
            abort startup rather than proceeding with a potentially inconsistent
            task queue.
    """
    from keeplink_mcp.db.repository import TaskRepository

    async with session_factory() as session:
        repo = TaskRepository(session)
        count = await repo.recover_processing_tasks()

    if count > 0:
        logger.info(
            "Recovered stuck tasks at startup",
            extra={"action": "stuck_task_recovery", "recovered_count": count},
        )
    else:
        logger.info(
            "No stuck tasks found at startup",
            extra={"action": "stuck_task_recovery", "recovered_count": 0},
        )

    return count
