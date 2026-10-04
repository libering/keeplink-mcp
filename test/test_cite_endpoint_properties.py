# Feature: archive-and-cite
"""Property-based tests for the Cite_Endpoint (POST /api/cite).

Uses hypothesis with an in-memory SQLite async engine and an ASGI test client
to verify two endpoint-level correctness properties from design.md:

- Property 8: an invalid URL is rejected with HTTP 422 and creates NO task.
- Property 9: a validated URL with a dedup-eligible task within the window is
  reused (no new task created) and the returned task_id equals the existing one.

The DB is a single in-memory instance shared across all hypothesis examples in
one test invocation (the ``session_factory`` fixture is function-scoped), so
each example uses a distinct URL to keep examples independent. Task counts are
therefore asserted per-URL rather than globally.
"""

import itertools

import pytest
from httpx import ASGITransport, AsyncClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import func, select

from keeplink_mcp.api.app import create_app
from keeplink_mcp.citation.builder import CitationFormat, build_citation
from keeplink_mcp.db.models import ArchiveTask, TaskStatus

# The session_factory fixture is function-scoped, so a single in-memory DB is
# shared across every hypothesis example within one test invocation. A drawn
# URL can repeat across examples, so we append a process-unique counter to make
# each example's URL distinct and its per-URL task count unambiguous.
_EXAMPLE_COUNTER = itertools.count()

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Invalid URLs: cover the ValidationError branches in validate_url —
# bad/absent scheme, hostname without a dot, empty, and whitespace-only.
# Every value here MUST fail validate_url (scheme not in http/https OR
# hostname empty / missing a dot).
_INVALID_URL_STRATEGY = st.one_of(
    st.just(""),
    st.just("   "),
    st.just("not-a-url"),
    st.just("http://localhost"),  # hostname has no dot
    st.just("https://localhost"),  # hostname has no dot
    st.just("http://"),  # empty hostname
    st.just("https:///path"),  # empty hostname
    # Non-http(s) schemes with an otherwise-plausible dotted host.
    st.builds(
        lambda scheme, host: f"{scheme}://{host}",
        scheme=st.sampled_from(["ftp", "file", "gopher", "mailto", "ws", "javascript"]),
        host=st.from_regex(r"[a-z]{2,8}\.[a-z]{2,4}", fullmatch=True),
    ),
    # http(s) scheme but hostname without a dot.
    st.builds(
        lambda scheme, host: f"{scheme}://{host}",
        scheme=st.sampled_from(["http", "https"]),
        host=st.from_regex(r"[a-z]{2,12}", fullmatch=True),
    ),
)

# Valid URLs: dotted hostname + optional path, http/https — these pass
# validate_url unchanged (validate_url does not lowercase or reformat).
_VALID_URL_STRATEGY = st.builds(
    lambda scheme, host, path: f"{scheme}://{host}{path}",
    scheme=st.sampled_from(["http", "https"]),
    host=st.from_regex(r"[a-z][a-z0-9]{0,10}\.[a-z]{2,6}", fullmatch=True),
    path=st.from_regex(r"(/[a-z0-9\-]{1,10}){0,3}", fullmatch=True),
)

# Dedup-eligible statuses (what find_recent_task returns within the window).
# A success task additionally needs a result_url to be a cache-hit, but any of
# these must be reused rather than duplicated.
_DEDUP_STATUS_STRATEGY = st.sampled_from(
    [TaskStatus.PENDING, TaskStatus.PROCESSING, TaskStatus.SUCCESS]
)

# Optional caller title (Property 9 must hold regardless of title). Covers
# None, empty, whitespace-only, and meaningful text incl. unicode + markdown
# special characters. The alphabet is bounded (BMP printable range) to keep the
# generated text well-formed for JSON transport across the ASGI client.
_TITLE_STRATEGY = st.one_of(
    st.none(),
    st.just(""),
    st.just("  "),
    st.text(
        alphabet=st.characters(min_codepoint=0x20, max_codepoint=0x2FFF),
        max_size=30,
    ),
)


async def _count_tasks(session_factory, url: str) -> int:
    """Return the number of ArchiveTask rows for a given URL."""
    async with session_factory() as session:
        stmt = select(func.count()).select_from(ArchiveTask).where(ArchiveTask.url == url)
        result = await session.execute(stmt)
        return result.scalar()


# ---------------------------------------------------------------------------
# Property 8: 無效 URL 拒絕且無副作用 (Invalid URL Rejected, No Side Effect)
# Validates: Requirements 2.1, 6.1, 6.2, 6.3
# ---------------------------------------------------------------------------


# Feature: archive-and-cite, Property 8: 無效 URL 拒絕且無副作用 — for any url that
# fails validate_url, POST /api/cite returns HTTP 422 and creates NO ArchiveTask
# (total task count in the DB is unchanged).
@settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(bad_url=_INVALID_URL_STRATEGY, title=_TITLE_STRATEGY)
@pytest.mark.asyncio
async def test_property_invalid_url_rejected_no_side_effect(
    bad_url: str, title, session_factory
):
    """**Validates: Requirements 2.1, 6.1, 6.2, 6.3**

    Property 8: For any url string that fails validate_url, POST /api/cite
    returns HTTP 422 with an error detail and creates no ArchiveTask.
    """
    app = create_app(session_factory)
    transport = ASGITransport(app=app)

    async def _total_task_count() -> int:
        async with session_factory() as session:
            stmt = select(func.count()).select_from(ArchiveTask)
            result = await session.execute(stmt)
            return result.scalar()

    count_before = await _total_task_count()

    body: dict = {"url": bad_url}
    if title is not None:
        body["title"] = title

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/cite", json=body)

    # 422 with a descriptive error detail (fail-fast rejection).
    assert resp.status_code == 422
    assert "detail" in resp.json()

    # No side effect: the total task count is unchanged.
    count_after = await _total_task_count()
    assert count_after == count_before


# ---------------------------------------------------------------------------
# Property 9: 去重複用 (Dedup Reuse)
# Validates: Requirements 2.3
# ---------------------------------------------------------------------------


# Feature: archive-and-cite, Property 9: 去重複用 — for any validated url that
# already has a dedup-eligible task within the dedup window, POST /api/cite
# reuses that task (no new task created) and the returned task_id equals the
# existing task's task_id.
@settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(url=_VALID_URL_STRATEGY, status=_DEDUP_STATUS_STRATEGY, title=_TITLE_STRATEGY)
@pytest.mark.asyncio
async def test_property_dedup_reuse(url: str, status: TaskStatus, title, session_factory):
    """**Validates: Requirements 2.3**

    Property 9: For any validated url with an existing dedup-eligible task
    (pending / processing / success) within the window, POST /api/cite reuses
    that task — no new task is created and the returned task_id matches.
    """
    # A drawn URL can repeat across hypothesis examples that share one in-memory
    # DB; append a per-example counter so the seeded task is unique and the
    # per-URL task count is unambiguous.
    seeded_url = f"{url}?ex={next(_EXAMPLE_COUNTER)}"

    # Seed exactly one dedup-eligible task for this URL.
    async with session_factory() as session:
        task = ArchiveTask(
            url=seeded_url,
            status=status,
            result_url=(
                "https://web.archive.org/web/20240101/example"
                if status == TaskStatus.SUCCESS
                else None
            ),
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        existing_id = task.task_id

    count_before = await _count_tasks(session_factory, seeded_url)

    app = create_app(session_factory)
    transport = ASGITransport(app=app)

    body: dict = {"url": seeded_url}
    if title is not None:
        body["title"] = title

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/cite", json=body)

    assert resp.status_code == 200
    payload = resp.json()

    # Reuse: the returned task_id equals the existing task's task_id.
    assert payload["task_id"] == existing_id

    # No new task created for this URL.
    count_after = await _count_tasks(session_factory, seeded_url)
    assert count_after == count_before == 1

# ===========================================================================
# Feature: keeplink-v1x-improvements — Properties 11 & 12
#
# These properties cover improvement H (multi-format citation) at the endpoint
# boundary. They are grouped in a dedicated class so the archive-and-cite
# properties above (8, 9) remain untouched.
#
# The session_factory fixture is function-scoped, so a single in-memory DB is
# shared across every hypothesis example within one test invocation. Each
# example therefore uses a per-example-unique URL to keep task counts and
# dedup lookups unambiguous.
# ===========================================================================

# All four valid formats (full enum coverage per design.md Strategies).
_VALID_FORMAT_STRATEGY = st.sampled_from(list(CitationFormat))

# Task states that the cite endpoint distinguishes:
# - SUCCESS + result_url  → complete citation (archived_url/archived_at set)
# - PENDING / PROCESSING  → pending citation (archived_url/archived_at None)
# - SUCCESS without result_url is treated as pending by the endpoint.
_ENDPOINT_STATE_STRATEGY = st.sampled_from(
    [TaskStatus.PENDING, TaskStatus.PROCESSING, TaskStatus.SUCCESS]
)

# Invalid format strings: any value NOT equal to one of the enum values.
# We exclude the four valid values so every drawn string must fail validation.
_VALID_FORMAT_VALUES = frozenset(f.value for f in CitationFormat)
_INVALID_FORMAT_STRATEGY = st.one_of(
    st.just(""),
    st.just("MARKDOWN"),  # enum is case-sensitive lowercase
    st.just("Markdown"),
    st.just("md"),
    st.just("json"),
    st.just("html"),
    st.just("chicago"),
    st.text(
        alphabet=st.characters(min_codepoint=0x20, max_codepoint=0x7E),
        max_size=20,
    ),
).filter(lambda s: s not in _VALID_FORMAT_VALUES)


class TestCiteEndpointFormatProperties:
    """Endpoint-level format properties for improvement H (Properties 11, 12)."""

    # Feature: keeplink-v1x-improvements, Property 11: 端點 format 端到端一致 — for
    # any valid format and any task state, POST /api/cite response.formatted
    # equals build_citation(...) called with the same state + format.
    @settings(
        max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    @given(
        url=_VALID_URL_STRATEGY,
        state=_ENDPOINT_STATE_STRATEGY,
        fmt=_VALID_FORMAT_STRATEGY,
        title=_TITLE_STRATEGY,
    )
    @pytest.mark.asyncio
    async def test_property_endpoint_format_end_to_end_consistent(
        self, url: str, state: TaskStatus, fmt: CitationFormat, title, session_factory
    ):
        """**Validates: Requirements 3.9**

        Property 11: The endpoint dispatches the request `format` straight
        through to build_citation without altering it. For any valid format and
        task state, the response `formatted` string is byte-for-byte identical
        to build_citation() invoked with the same archive state and format.
        """
        # Unique per-example URL: the in-memory DB is shared across examples, so
        # a repeated URL would collide via dedup. The counter keeps it distinct.
        seeded_url = f"{url}?ex={next(_EXAMPLE_COUNTER)}"

        result_url = (
            "https://web.archive.org/web/20240101/example"
            if state == TaskStatus.SUCCESS
            else None
        )

        # Seed exactly one task in the chosen state so the endpoint's dedup
        # branch reuses it (rather than creating a fresh pending task).
        async with session_factory() as session:
            task = ArchiveTask(url=seeded_url, status=state, result_url=result_url)
            session.add(task)
            await session.commit()
            await session.refresh(task)
            task_id = task.task_id

        # The endpoint treats only SUCCESS+result_url as complete; it then uses
        # the row's updated_at as archived_at. Read those exact values back so
        # the expected build_citation call mirrors the endpoint's inputs.
        if state == TaskStatus.SUCCESS and result_url:
            async with session_factory() as session:
                reloaded = await session.get(ArchiveTask, task_id)
                expected_archived_url = reloaded.result_url
                expected_archived_at = reloaded.updated_at
        else:
            expected_archived_url = None
            expected_archived_at = None

        expected = build_citation(
            title=title,
            original_url=seeded_url,
            task_id=task_id,
            archived_url=expected_archived_url,
            archived_at=expected_archived_at,
            format=fmt,
        )

        app = create_app(session_factory)
        transport = ASGITransport(app=app)

        body: dict = {"url": seeded_url, "format": fmt.value}
        if title is not None:
            body["title"] = title

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/cite", json=body)

        assert resp.status_code == 200
        payload = resp.json()

        # End-to-end consistency: the endpoint's formatted output equals the
        # pure builder's output for the same state + format.
        assert payload["formatted"] == expected.formatted
        assert payload["task_id"] == task_id

    # Feature: keeplink-v1x-improvements, Property 12: 非法 format fail-fast 且不產生
    # CitationResponse — for any format string not in the enum, POST /api/cite
    # returns HTTP 422, produces no CitationResponse, and does not silently fall
    # back to the default format.
    @settings(
        max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    @given(bad_format=_INVALID_FORMAT_STRATEGY, title=_TITLE_STRATEGY)
    @pytest.mark.asyncio
    async def test_property_invalid_format_rejected_no_citation(
        self, bad_format: str, title, session_factory
    ):
        """**Validates: Requirements 3.8, 3.11**

        Property 12: For any format string outside CitationFormat, POST
        /api/cite fails fast with HTTP 422 and returns a validation-error body
        (never a CitationResponse), so an invalid format is never silently
        coerced to the markdown default.
        """
        app = create_app(session_factory)
        transport = ASGITransport(app=app)

        # A per-example-unique, otherwise-valid URL: the request must be
        # rejected on `format` alone, not because the URL is bad.
        seeded_url = f"https://example.com/cite-fmt?ex={next(_EXAMPLE_COUNTER)}"

        count_before = await _count_tasks(session_factory, seeded_url)

        body: dict = {"url": seeded_url, "format": bad_format}
        if title is not None:
            body["title"] = title

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/cite", json=body)

        # Fail-fast rejection with a validation-error detail.
        assert resp.status_code == 422
        payload = resp.json()
        assert "detail" in payload

        # No CitationResponse was produced: none of its required, non-null
        # fields (task_id, formatted, original_url) leaked into the 422 body.
        assert "formatted" not in payload
        assert "task_id" not in payload
        assert "original_url" not in payload

        # No silent fallback to a default citation: the invalid format must not
        # have created a task via the pipeline for this URL.
        count_after = await _count_tasks(session_factory, seeded_url)
        assert count_after == count_before == 0
