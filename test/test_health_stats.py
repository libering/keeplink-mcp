# Feature: keeplink-mcp
"""Property-based tests for health stats correctness.

Validates: Requirements 2.4
Property 2: Health stats reflect actual task distribution
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import delete

from keeplink_mcp.db.models import ArchiveTask, TaskStatus
from keeplink_mcp.db.repository import TaskRepository

# ---------------------------------------------------------------------------
# Strategies for generating test data
# ---------------------------------------------------------------------------


@st.composite
def task_status_strategy(draw):
    """Generate a valid TaskStatus value."""
    return draw(st.sampled_from([
        TaskStatus.PENDING, TaskStatus.PROCESSING,
        TaskStatus.SUCCESS, TaskStatus.FAILED,
    ]))


@st.composite
def task_distribution_strategy(draw):
    """Generate a distribution of tasks with various statuses and timestamps.

    Returns a list of (status, hours_ago) tuples where hours_ago indicates
    how many hours ago the task was updated.
    """
    num_tasks = draw(st.integers(min_value=0, max_value=50))

    tasks = []
    for _ in range(num_tasks):
        status = draw(st.sampled_from([
            TaskStatus.PENDING,
            TaskStatus.PROCESSING,
            TaskStatus.SUCCESS,
            TaskStatus.FAILED,
        ]))
        # Generate hours_ago: pending tasks don't care about time window,
        # but success/failed tasks should have varied times
        if status == TaskStatus.PENDING:
            hours_ago = draw(st.floats(min_value=0, max_value=48))
        elif status == TaskStatus.PROCESSING:
            hours_ago = draw(st.floats(min_value=0, max_value=48))
        else:
            # For success/failed, generate some within and some outside 24h window
            hours_ago = draw(st.floats(min_value=0, max_value=48))

        tasks.append((status, hours_ago))

    return tasks


# ---------------------------------------------------------------------------
# Property 2: Health stats reflect actual task distribution
# For any set of tasks: queue_depth == count(pending),
# success_count == count(success in 24h), failure_count == count(failed in 24h)
# ---------------------------------------------------------------------------


class TestHealthStatsReflectActualTaskDistribution:
    """Property-based tests for health stats correctness.

    **Validates: Requirements 2.4**
    """

    @settings(
        max_examples=100, deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(task_distribution=task_distribution_strategy())
    async def test_queue_depth_equals_pending_count(
        self, task_distribution: list[tuple[TaskStatus, float]], session_factory
    ) -> None:
        """queue_depth SHALL equal the count of pending tasks.

        **Validates: Requirements 2.4**

        Test Strategy:
            - Create tasks with arbitrary status distribution
            - Call get_queue_stats()
            - Assert queue_depth matches count of tasks with PENDING status
        """
        async with session_factory() as session:
            repo = TaskRepository(session)

            # Clear all existing tasks for clean state
            await session.execute(delete(ArchiveTask))
            await session.commit()

            # Create tasks according to distribution
            pending_count = 0
            for status, hours_ago in task_distribution:
                task = ArchiveTask(
                    url=f"https://example.com/{hash((status, hours_ago))}",
                    status=status,
                    updated_at=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
                )
                session.add(task)
                if status == TaskStatus.PENDING:
                    pending_count += 1

            await session.commit()

            # Get stats
            stats = await repo.get_queue_stats(window_hours=24)

            assert stats["queue_depth"] == pending_count, (
                f"queue_depth ({stats['queue_depth']}) != pending_count ({pending_count})"
            )

    @settings(
        max_examples=100, deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(task_distribution=task_distribution_strategy())
    async def test_success_count_equals_success_in_window(
        self, task_distribution: list[tuple[TaskStatus, float]], session_factory
    ) -> None:
        """success_count SHALL equal count of success tasks within 24h window.

        **Validates: Requirements 2.4**

        Test Strategy:
            - Create tasks with various updated_at timestamps
            - Call get_queue_stats() with 24h window
            - Assert success_count matches count of SUCCESS tasks updated within 24h
        """
        async with session_factory() as session:
            repo = TaskRepository(session)

            # Clear all existing tasks for clean state
            await session.execute(delete(ArchiveTask))
            await session.commit()

            # Create tasks according to distribution
            expected_success_count = 0
            window_hours = 24

            for status, hours_ago in task_distribution:
                task = ArchiveTask(
                    url=f"https://example.com/{hash((status, hours_ago))}",
                    status=status,
                    updated_at=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
                )
                session.add(task)

                # Count success tasks within window
                if status == TaskStatus.SUCCESS and hours_ago < window_hours:
                    expected_success_count += 1

            await session.commit()

            # Get stats
            stats = await repo.get_queue_stats(window_hours=window_hours)

            assert stats["success_count"] == expected_success_count, (
                f"success_count ({stats['success_count']}) != expected ({expected_success_count})"
            )

    @settings(
        max_examples=100, deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(task_distribution=task_distribution_strategy())
    async def test_failure_count_equals_failed_in_window(
        self, task_distribution: list[tuple[TaskStatus, float]], session_factory
    ) -> None:
        """failure_count SHALL equal count of failed tasks within 24h window.

        **Validates: Requirements 2.4**

        Test Strategy:
            - Create tasks with various updated_at timestamps
            - Call get_queue_stats() with 24h window
            - Assert failure_count matches count of FAILED tasks updated within 24h
        """
        async with session_factory() as session:
            repo = TaskRepository(session)

            # Clear all existing tasks for clean state
            await session.execute(delete(ArchiveTask))
            await session.commit()

            # Create tasks according to distribution
            expected_failure_count = 0
            window_hours = 24

            for status, hours_ago in task_distribution:
                task = ArchiveTask(
                    url=f"https://example.com/{hash((status, hours_ago))}",
                    status=status,
                    updated_at=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
                )
                session.add(task)

                # Count failed tasks within window
                if status == TaskStatus.FAILED and hours_ago < window_hours:
                    expected_failure_count += 1

            await session.commit()

            # Get stats
            stats = await repo.get_queue_stats(window_hours=window_hours)

            assert stats["failure_count"] == expected_failure_count, (
                f"failure_count ({stats['failure_count']}) != expected ({expected_failure_count})"
            )

    @settings(
        max_examples=100, deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(task_distribution=task_distribution_strategy())
    async def test_all_stats_correct_together(
        self, task_distribution: list[tuple[TaskStatus, float]], session_factory
    ) -> None:
        """All stats SHALL be correct simultaneously for any task distribution.

        **Validates: Requirements 2.4**

        Test Strategy:
            - Create tasks with arbitrary status and timestamp distribution
            - Call get_queue_stats()
            - Assert all three stats are correct simultaneously
        """
        async with session_factory() as session:
            repo = TaskRepository(session)

            # Clear all existing tasks for clean state
            await session.execute(delete(ArchiveTask))
            await session.commit()

            # Create tasks according to distribution
            expected_pending = 0
            expected_success = 0
            expected_failure = 0
            window_hours = 24

            for status, hours_ago in task_distribution:
                task = ArchiveTask(
                    url=f"https://example.com/{hash((status, hours_ago))}",
                    status=status,
                    updated_at=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
                )
                session.add(task)

                if status == TaskStatus.PENDING:
                    expected_pending += 1
                elif status == TaskStatus.SUCCESS and hours_ago < window_hours:
                    expected_success += 1
                elif status == TaskStatus.FAILED and hours_ago < window_hours:
                    expected_failure += 1

            await session.commit()

            # Get stats
            stats = await repo.get_queue_stats(window_hours=window_hours)

            assert stats["queue_depth"] == expected_pending, (
                f"queue_depth ({stats['queue_depth']}) != expected ({expected_pending})"
            )
            assert stats["success_count"] == expected_success, (
                f"success_count ({stats['success_count']}) != expected ({expected_success})"
            )
            assert stats["failure_count"] == expected_failure, (
                f"failure_count ({stats['failure_count']}) != expected ({expected_failure})"
            )


class TestHealthStatsEdgeCases:
    """Edge case tests for health stats.

    **Validates: Requirements 2.4**
    """

    async def test_empty_database_returns_zero_stats(self, session_factory) -> None:
        """When database has no tasks, all stats SHALL be zero."""
        async with session_factory() as session:
            repo = TaskRepository(session)

            stats = await repo.get_queue_stats(window_hours=24)

            assert stats["queue_depth"] == 0
            assert stats["success_count"] == 0
            assert stats["failure_count"] == 0

    async def test_processing_tasks_not_counted(self, session_factory) -> None:
        """PROCESSING tasks SHALL NOT be counted in any stat."""
        async with session_factory() as session:
            repo = TaskRepository(session)

            # Create processing tasks
            for i in range(5):
                task = ArchiveTask(
                    url=f"https://example.com/processing/{i}",
                    status=TaskStatus.PROCESSING,
                    updated_at=datetime.now(timezone.utc) - timedelta(hours=1),
                )
                session.add(task)

            await session.commit()

            stats = await repo.get_queue_stats(window_hours=24)

            assert stats["queue_depth"] == 0
            assert stats["success_count"] == 0
            assert stats["failure_count"] == 0

    async def test_success_outside_window_not_counted(self, session_factory) -> None:
        """SUCCESS tasks outside the time window SHALL NOT be counted."""
        async with session_factory() as session:
            repo = TaskRepository(session)

            # Create success task outside 24h window
            task = ArchiveTask(
                url="https://example.com/old-success",
                status=TaskStatus.SUCCESS,
                updated_at=datetime.now(timezone.utc) - timedelta(hours=25),
            )
            session.add(task)

            await session.commit()

            stats = await repo.get_queue_stats(window_hours=24)

            assert stats["success_count"] == 0

    async def test_failed_outside_window_not_counted(self, session_factory) -> None:
        """FAILED tasks outside the time window SHALL NOT be counted."""
        async with session_factory() as session:
            repo = TaskRepository(session)

            # Create failed task outside 24h window
            task = ArchiveTask(
                url="https://example.com/old-failed",
                status=TaskStatus.FAILED,
                updated_at=datetime.now(timezone.utc) - timedelta(hours=30),
            )
            session.add(task)

            await session.commit()

            stats = await repo.get_queue_stats(window_hours=24)

            assert stats["failure_count"] == 0

    async def test_pending_always_counted_regardless_of_time(self, session_factory) -> None:
        """PENDING tasks SHALL be counted regardless of updated_at timestamp."""
        async with session_factory() as session:
            repo = TaskRepository(session)

            # Create pending tasks with various timestamps (some very old)
            for hours_ago in [1, 24, 48, 100]:
                task = ArchiveTask(
                    url=f"https://example.com/pending/{hours_ago}",
                    status=TaskStatus.PENDING,
                    updated_at=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
                )
                session.add(task)

            await session.commit()

            stats = await repo.get_queue_stats(window_hours=24)

            # All 4 pending tasks should be counted
            assert stats["queue_depth"] == 4

    async def test_custom_window_hours(self, session_factory) -> None:
        """get_queue_stats SHALL respect custom window_hours parameter."""
        async with session_factory() as session:
            repo = TaskRepository(session)

            # Create success task at 3 hours ago
            task = ArchiveTask(
                url="https://example.com/recent-success",
                status=TaskStatus.SUCCESS,
                updated_at=datetime.now(timezone.utc) - timedelta(hours=3),
            )
            session.add(task)

            await session.commit()

            # With 2h window, should be 0
            stats_2h = await repo.get_queue_stats(window_hours=2)
            assert stats_2h["success_count"] == 0

            # With 5h window, should be 1
            stats_5h = await repo.get_queue_stats(window_hours=5)
            assert stats_5h["success_count"] == 1
