"""Property-based tests for TaskRepository.retry_task state transitions.

# Feature: keeplink-v1x-improvements

Validates the failed -> pending reset invariant, the non-failed no-mutation
guarantee, and the retry idempotence boundary for the manual retry feature
(改進 C). Uses in-memory SQLite (via the shared session_factory fixture) and
hypothesis to explore arbitrary initial field values.

Properties covered (mapped to design.md):
- Property 2: retry 重設不變式 (Failed -> Pending Reset Invariant)
- Property 3: 非 failed 一律拒絕且不改任何欄位 (Non-failed Rejected, No Mutation)
- Property 4: 重試冪等邊界 (Retry Idempotence Boundary)
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy.orm import sessionmaker

from keeplink_mcp.db.models import ArchiveTask, TaskStatus
from keeplink_mcp.db.repository import RetryRejected, TaskRepository

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Arbitrary retry_count including 0 and >0 values.
_retry_counts = st.integers(min_value=0, max_value=50)

# next_retry_at: None or an arbitrary timezone-aware datetime (non-null cases).
_next_retry_ats = st.one_of(
    st.none(),
    st.datetimes(
        min_value=datetime(2000, 1, 1),
        max_value=datetime(2100, 1, 1),
    ).map(lambda dt: dt.replace(tzinfo=timezone.utc)),
)

# error_message: None or an arbitrary non-null string.
_error_messages = st.one_of(st.none(), st.text(min_size=1, max_size=200))

# result_url: None or an arbitrary non-null string (used for no-mutation checks).
_result_urls = st.one_of(st.none(), st.text(min_size=1, max_size=200))

# Statuses that are NOT failed — retry must reject these.
_non_failed_statuses = st.sampled_from(
    [TaskStatus.PENDING, TaskStatus.PROCESSING, TaskStatus.SUCCESS]
)


async def _insert_task(
    session_factory: sessionmaker,
    *,
    status: TaskStatus,
    retry_count: int,
    next_retry_at: datetime | None,
    error_message: str | None,
    result_url: str | None = None,
) -> str:
    """Insert an ArchiveTask with fully specified field values.

    Returns the generated task_id. A fresh unique url + task_id is used per
    call so that hypothesis examples sharing the function-scoped in-memory DB
    never collide.
    """
    task_id = uuid4().hex
    task = ArchiveTask(
        task_id=task_id,
        url=f"https://example.com/{task_id}",
        status=status,
        retry_count=retry_count,
        next_retry_at=next_retry_at,
        result_url=result_url,
        error_message=error_message,
    )
    async with session_factory() as session:
        session.add(task)
        await session.commit()
    return task_id


def _snapshot(task: ArchiveTask) -> dict[str, object]:
    """Capture every field value for exact before/after comparison."""
    return {
        "task_id": task.task_id,
        "url": task.url,
        "status": task.status,
        "retry_count": task.retry_count,
        "next_retry_at": task.next_retry_at,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "result_url": task.result_url,
        "error_message": task.error_message,
    }


# ---------------------------------------------------------------------------
# Property 2: retry 重設不變式 (Failed -> Pending Reset Invariant)
# ---------------------------------------------------------------------------


class TestProperty2ResetInvariant:
    # Feature: keeplink-v1x-improvements, Property 2: retry 重設不變式 — failed 任務經
    # retry 後 status==pending、retry_count==0、next_retry_at is None、
    # error_message is None，回傳 task_id 等於原值。
    """**Validates: Requirements 2.3, 2.4**"""

    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        retry_count=_retry_counts,
        next_retry_at=_next_retry_ats,
        error_message=_error_messages,
    )
    async def test_failed_retry_resets_fields(
        self,
        session_factory: sessionmaker,
        retry_count: int,
        next_retry_at: datetime | None,
        error_message: str | None,
    ) -> None:
        task_id = await _insert_task(
            session_factory,
            status=TaskStatus.FAILED,
            retry_count=retry_count,
            next_retry_at=next_retry_at,
            error_message=error_message,
        )

        async with session_factory() as session:
            repo = TaskRepository(session)
            result = await repo.retry_task(task_id)

        assert isinstance(result, ArchiveTask)
        assert result.task_id == task_id
        assert result.status == TaskStatus.PENDING
        assert result.retry_count == 0
        assert result.next_retry_at is None
        assert result.error_message is None


# ---------------------------------------------------------------------------
# Property 3: 非 failed 一律拒絕且不改任何欄位 (Non-failed Rejected, No Mutation)
# ---------------------------------------------------------------------------


class TestProperty3NonFailedRejected:
    # Feature: keeplink-v1x-improvements, Property 3: 非 failed 一律拒絕且不改任何欄位 —
    # 非 failed 任務 retry 回 RetryRejected(current_status) 且所有欄位與呼叫前逐一相等。
    """**Validates: Requirements 2.6**"""

    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        status=_non_failed_statuses,
        retry_count=_retry_counts,
        next_retry_at=_next_retry_ats,
        error_message=_error_messages,
        result_url=_result_urls,
    )
    async def test_non_failed_rejected_no_mutation(
        self,
        session_factory: sessionmaker,
        status: TaskStatus,
        retry_count: int,
        next_retry_at: datetime | None,
        error_message: str | None,
        result_url: str | None,
    ) -> None:
        task_id = await _insert_task(
            session_factory,
            status=status,
            retry_count=retry_count,
            next_retry_at=next_retry_at,
            error_message=error_message,
            result_url=result_url,
        )

        # Snapshot every field before the retry call.
        async with session_factory() as session:
            repo = TaskRepository(session)
            before = _snapshot(await repo.get_task(task_id))

        async with session_factory() as session:
            repo = TaskRepository(session)
            result = await repo.retry_task(task_id)

        assert isinstance(result, RetryRejected)
        assert result.current_status == status

        # Every field must be identical to the pre-call snapshot (no side effects).
        async with session_factory() as session:
            repo = TaskRepository(session)
            after = _snapshot(await repo.get_task(task_id))

        assert after == before


# ---------------------------------------------------------------------------
# Property 4: 重試冪等邊界 (Retry Idempotence Boundary)
# ---------------------------------------------------------------------------


class TestProperty4IdempotenceBoundary:
    # Feature: keeplink-v1x-improvements, Property 4: 重試冪等邊界 — failed 任務 retry
    # 成功轉 pending 後再次 retry 回 RetryRejected(current_status==pending) 且不改任何欄位。
    """**Validates: Requirements 2.9**"""

    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        retry_count=_retry_counts,
        next_retry_at=_next_retry_ats,
        error_message=_error_messages,
    )
    async def test_second_retry_rejected_and_no_mutation(
        self,
        session_factory: sessionmaker,
        retry_count: int,
        next_retry_at: datetime | None,
        error_message: str | None,
    ) -> None:
        task_id = await _insert_task(
            session_factory,
            status=TaskStatus.FAILED,
            retry_count=retry_count,
            next_retry_at=next_retry_at,
            error_message=error_message,
        )

        # First retry: failed -> pending (success).
        async with session_factory() as session:
            repo = TaskRepository(session)
            first = await repo.retry_task(task_id)
        assert isinstance(first, ArchiveTask)
        assert first.status == TaskStatus.PENDING

        # Snapshot after the successful first retry (task is now pending).
        async with session_factory() as session:
            repo = TaskRepository(session)
            before = _snapshot(await repo.get_task(task_id))

        # Second retry: must be rejected because status is now pending.
        async with session_factory() as session:
            repo = TaskRepository(session)
            second = await repo.retry_task(task_id)

        assert isinstance(second, RetryRejected)
        assert second.current_status == TaskStatus.PENDING

        # No field may change on the second (rejected) retry.
        async with session_factory() as session:
            repo = TaskRepository(session)
            after = _snapshot(await repo.get_task(task_id))

        assert after == before
