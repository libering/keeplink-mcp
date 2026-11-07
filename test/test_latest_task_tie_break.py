"""Regression: tasks sharing the same created_at must not duplicate results.

On Windows with Python < 3.13 the clock resolution (~15ms) lets two rapid
create_task calls get identical created_at, which used to make
get_latest_tasks_by_urls return both rows for one URL.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import sessionmaker

from keeplink_mcp.db.repository import TaskRepository

TIED_AT = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


async def test_tied_created_at_returns_single_task_per_url(
    session_factory: sessionmaker,
) -> None:
    url = "https://tie.example.com/page"
    async with session_factory() as session:
        repo = TaskRepository(session)
        first = await repo.create_task(url)
        second = await repo.create_task(url)
        first.created_at = TIED_AT
        second.created_at = TIED_AT
        await session.commit()

        results = await repo.get_latest_tasks_by_urls([url])
        single = await repo.get_latest_task_by_url(url)

    expected = max(first.task_id, second.task_id)
    assert [t.task_id for t in results] == [expected]
    assert single is not None and single.task_id == expected
