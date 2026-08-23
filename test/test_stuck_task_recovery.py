"""Property-based test for stuck task recovery.

# Feature: keeplink-v1-improvements

Property 3: Stuck task recovery preserves non-processing tasks

For any set of tasks in the database, after recovery executes:
- All previously-processing tasks SHALL have status pending
- All tasks that were NOT in processing status SHALL remain unchanged

**Validates: Requirements 4.2**
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy.orm import sessionmaker

from keeplink_mcp.db.models import ArchiveTask, TaskStatus
from keeplink_mcp.db.repository import TaskRepository

# ---------------------------------------------------------------------------
# Strategies for task state generation
# ---------------------------------------------------------------------------


@st.composite
def task_state_strategy(draw: st.DrawFn) -> tuple[TaskStatus, int, datetime | None]:
    """Generate a task's initial state for testing recovery.

    Returns:
        Tuple of (status, retry_count, next_retry_at) representing a task state.
        - processing tasks may have next_retry_at set (from interrupted retries)
        - non-processing tasks preserve their original attributes
    """
    status = draw(st.sampled_from(TaskStatus))
    retry_count = draw(st.integers(min_value=0, max_value=10))

    # next_retry_at: only relevant for pending tasks awaiting retry,
    # but we test that it gets cleared for recovered processing tasks
    if status == TaskStatus.PROCESSING:
        # Processing tasks might have a next_retry_at from a scheduled retry
        # that was interrupted before the task was picked up
        # Use st.integers for offset minutes to avoid timezone issues
        offset_minutes = draw(st.integers(min_value=-60, max_value=60))
        next_retry_at = draw(
            st.one_of(
                st.none(),
                st.just(
                    datetime.now(timezone.utc) + timedelta(minutes=offset_minutes)
                ),
            )
        )
    else:
        # Non-processing tasks: next_retry_at varies based on status
        next_retry_at = draw(st.none())

    return (status, retry_count, next_retry_at)


@st.composite
def task_set_strategy(
    draw: st.DrawFn,
) -> list[tuple[str, TaskStatus, int, datetime | None]]:
    """Generate a set of tasks with various states for recovery testing.

    Returns:
        List of tuples (url, status, retry_count, next_retry_at).
    """
    num_tasks = draw(st.integers(min_value=0, max_value=20))
    tasks = []

    for i in range(num_tasks):
        url = f"https://test-{i}.example.com"
        status, retry_count, next_retry_at = draw(task_state_strategy())
        tasks.append((url, status, retry_count, next_retry_at))

    return tasks


# ---------------------------------------------------------------------------
# Property-Based Tests
# ---------------------------------------------------------------------------


class TestProperty3StuckTaskRecovery:
    """Property 3: Stuck task recovery preserves non-processing tasks.

    After recovery executes, all previously-processing tasks SHALL have
    status pending, and all tasks that were NOT in processing status
    SHALL remain unchanged.

    **Validates: Requirements 4.2**
    """

    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(task_set_strategy())
    async def test_recovery_preserves_non_processing_tasks(
        self, session_factory: sessionmaker,
        initial_tasks: list[tuple[str, TaskStatus, int, datetime | None]],
    ) -> None:
        """After recovery, processing→pending, others unchanged.

        This property verifies:
        1. All processing tasks become pending after recovery
        2. All non-processing tasks retain their original status
        3. retry_count is preserved for all tasks
        4. next_retry_at is cleared for recovered tasks
        """
        async with session_factory() as session:
            repo = TaskRepository(session)

            # Create tasks with the generated states
            created_tasks: list[tuple[ArchiveTask, TaskStatus, int, datetime | None]] = []

            for url, status, retry_count, next_retry_at in initial_tasks:
                task = ArchiveTask(
                    url=url,
                    status=status,
                    retry_count=retry_count,
                    next_retry_at=next_retry_at,
                )
                session.add(task)
                created_tasks.append((task, status, retry_count, next_retry_at))

            await session.commit()

            # Refresh to get generated IDs
            for task, _, _, _ in created_tasks:
                await session.refresh(task)

            # Record pre-recovery state
            pre_recovery: dict[str, tuple[TaskStatus, int, datetime | None]] = {}
            for task, status, retry_count, next_retry_at in created_tasks:
                pre_recovery[task.task_id] = (status, retry_count, next_retry_at)

            # Execute recovery
            recovered_count = await repo.recover_processing_tasks()

            # Verify returned count matches processing tasks
            expected_recovered = sum(
                1 for _, status, _, _ in created_tasks if status == TaskStatus.PROCESSING
            )
            assert recovered_count == expected_recovered, (
                f"Expected {expected_recovered} recovered, got {recovered_count}"
            )

            # Verify post-recovery state for each task
            for task, original_status, original_retry, original_next_retry in created_tasks:
                updated = await repo.get_task(task.task_id)
                assert updated is not None, f"Task {task.task_id} not found after recovery"

                if original_status == TaskStatus.PROCESSING:
                    # Processing tasks should become pending
                    assert updated.status == TaskStatus.PENDING, (
                        f"Processing task {task.task_id} should be pending, "
                        f"got {updated.status}"
                    )
                    # retry_count preserved
                    assert updated.retry_count == original_retry, (
                        f"Retry count changed from {original_retry} to {updated.retry_count}"
                    )
                    # next_retry_at cleared to None
                    assert updated.next_retry_at is None, (
                        f"next_retry_at should be None, got {updated.next_retry_at}"
                    )
                else:
                    # Non-processing tasks should remain unchanged
                    assert updated.status == original_status, (
                        f"Non-processing task {task.task_id} changed from "
                        f"{original_status} to {updated.status}"
                    )
                    assert updated.retry_count == original_retry, (
                        f"retry_count changed from {original_retry} to {updated.retry_count}"
                    )
                    # next_retry_at should be preserved
                    assert updated.next_retry_at == original_next_retry, (
                        f"next_retry_at changed from {original_next_retry} to "
                        f"{updated.next_retry_at}"
                    )

    async def test_empty_queue_recovery(self, session_factory: sessionmaker) -> None:
        """Recovery on an empty queue should return 0 and not fail."""
        async with session_factory() as session:
            repo = TaskRepository(session)

            recovered = await repo.recover_processing_tasks()

        assert recovered == 0

    async def test_all_processing_recovered(
        self, session_factory: sessionmaker,
    ) -> None:
        """All processing tasks should be recovered to pending."""
        async with session_factory() as session:
            repo = TaskRepository(session)

            # Create multiple processing tasks
            tasks = []
            for i in range(5):
                task = await repo.create_task(f"https://test-{i}.example.com")
                await repo.mark_processing(task.task_id)
                tasks.append(task)

            recovered = await repo.recover_processing_tasks()

        assert recovered == 5

        # Verify all are now pending
        async with session_factory() as session:
            repo = TaskRepository(session)
            for task in tasks:
                updated = await repo.get_task(task.task_id)
                assert updated is not None
                assert updated.status == TaskStatus.PENDING

    async def test_mixed_statuses_only_processing_recovered(
        self, session_factory: sessionmaker,
    ) -> None:
        """Only processing tasks should be affected by recovery."""
        async with session_factory() as session:
            repo = TaskRepository(session)

            # Create tasks in various states
            pending_task = await repo.create_task("https://pending.example.com")
            # stays pending

            processing_task = await repo.create_task("https://processing.example.com")
            await repo.mark_processing(processing_task.task_id)

            success_task = await repo.create_task("https://success.example.com")
            await repo.mark_processing(success_task.task_id)
            await repo.mark_success(
                success_task.task_id, "https://web.archive.org/web/123"
            )

            failed_task = await repo.create_task("https://failed.example.com")
            await repo.mark_processing(failed_task.task_id)
            await repo.mark_failed(failed_task.task_id, "permanent error")

            # Execute recovery
            recovered = await repo.recover_processing_tasks()

        # Only one processing task should be recovered
        assert recovered == 1

        # Verify final states
        async with session_factory() as session:
            repo = TaskRepository(session)

            pending = await repo.get_task(pending_task.task_id)
            assert pending is not None
            assert pending.status == TaskStatus.PENDING

            processing = await repo.get_task(processing_task.task_id)
            assert processing is not None
            assert processing.status == TaskStatus.PENDING  # Recovered

            success = await repo.get_task(success_task.task_id)
            assert success is not None
            assert success.status == TaskStatus.SUCCESS  # Unchanged

            failed = await repo.get_task(failed_task.task_id)
            assert failed is not None
            assert failed.status == TaskStatus.FAILED  # Unchanged

    async def test_retry_count_preserved_on_recovery(
        self, session_factory: sessionmaker,
    ) -> None:
        """Recovery should preserve retry_count from processing tasks."""
        async with session_factory() as session:
            repo = TaskRepository(session)

            # Create task and simulate multiple retries
            task = await repo.create_task("https://retry.example.com")

            for i in range(3):
                await repo.mark_processing(task.task_id)
                retry_at = datetime.now(timezone.utc) + timedelta(minutes=2**i)
                await repo.schedule_retry(task.task_id, f"error {i}", retry_at)
                # Task is now pending with incremented retry_count

            # Mark as processing again (simulating being picked up before crash)
            await repo.mark_processing(task.task_id)

            # Get current retry_count
            processing_task = await repo.get_task(task.task_id)
            assert processing_task is not None
            assert processing_task.retry_count == 3

            # Execute recovery
            recovered = await repo.recover_processing_tasks()

        assert recovered == 1

        # Verify retry_count preserved
        async with session_factory() as session:
            repo = TaskRepository(session)
            recovered_task = await repo.get_task(task.task_id)

        assert recovered_task is not None
        assert recovered_task.status == TaskStatus.PENDING
        assert recovered_task.retry_count == 3
        assert recovered_task.next_retry_at is None
