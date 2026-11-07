"""Builder-level property tests for the wikitext citation format.

Covers Properties 7 and 9 of the wikitext-citation-format design plus the
fail-fast example for inconsistent archive state (Requirement 6.5).
No mocks: every assertion runs against the real ``build_citation``.
"""

from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from keeplink_mcp.citation import build_citation
from keeplink_mcp.citation.builder import CitationFormat

_TITLE = st.one_of(
    st.none(),
    st.text(max_size=60),
    st.builds(
        lambda a, tok, b: f"{a}{tok}{b}",
        st.text(max_size=15),
        st.sampled_from(["|", "{{", "}}", "=", "[", "]", "<!--", "\n"]),
        st.text(max_size=15),
    ),
)
_URL = st.builds(
    lambda scheme, host, path: f"{scheme}://{host}{path}",
    st.sampled_from(["http", "https"]),
    st.from_regex(r"[a-z]{1,10}\.(com|org|net)", fullmatch=True),
    st.from_regex(r"(/[a-z0-9\-]{1,10}){0,3}", fullmatch=True),
)
_TASK_ID = st.uuids().map(str)
_ARCHIVED_AT = st.datetimes(
    min_value=datetime(2000, 1, 1),
    max_value=datetime(2100, 1, 1),
    timezones=st.sampled_from([None, timezone.utc, timezone(timedelta(hours=8))]),
)
# None archived_url == pending state; a URL == complete state.
_ARCHIVED_URL = st.one_of(st.none(), _URL)


def _build(title, original_url, task_id, archived_url, archived_at, fmt):
    # Pending state must carry no archived_at (builder invariant).
    return build_citation(
        title=title,
        original_url=original_url,
        task_id=task_id,
        archived_url=archived_url,
        archived_at=None if archived_url is None else archived_at,
        format=fmt,
    )


class TestWikitextBuilderProperties:
    # Feature: wikitext-citation-format, Property 7: 確定性
    # Validates: Requirements 6.1
    @settings(max_examples=100)
    @given(
        title=_TITLE,
        original_url=_URL,
        task_id=_TASK_ID,
        archived_url=_ARCHIVED_URL,
        archived_at=_ARCHIVED_AT,
    )
    def test_wikitext_build_is_deterministic(
        self, title, original_url, task_id, archived_url, archived_at
    ):
        args = (title, original_url, task_id, archived_url, archived_at)
        first = _build(*args, CitationFormat.WIKITEXT)
        second = _build(*args, CitationFormat.WIKITEXT)
        assert first == second
        assert first.formatted.startswith("{{cite web ")

    # Feature: wikitext-citation-format, Property 9: 結構化欄位不受 format 影響
    # Validates: Requirements 6.3
    @settings(max_examples=100)
    @given(
        title=_TITLE,
        original_url=_URL,
        task_id=_TASK_ID,
        archived_url=_ARCHIVED_URL,
        archived_at=_ARCHIVED_AT,
    )
    def test_structured_fields_independent_of_format(
        self, title, original_url, task_id, archived_url, archived_at
    ):
        args = (title, original_url, task_id, archived_url, archived_at)
        wiki = _build(*args, CitationFormat.WIKITEXT)
        md = _build(*args, CitationFormat.MARKDOWN)
        assert wiki.title == md.title
        assert wiki.original_url == md.original_url
        assert wiki.archived_url == md.archived_url
        assert wiki.archived_at == md.archived_at
        assert wiki.task_id == md.task_id


class TestWikitextInconsistentArchiveState:
    # Validates: Requirements 6.5
    def test_archived_url_without_archived_at_raises(self):
        with pytest.raises(ValueError):
            build_citation(
                title="T",
                original_url="https://example.com/a",
                task_id="task-1",
                archived_url="https://web.archive.org/web/2024/https://example.com/a",
                archived_at=None,
                format=CitationFormat.WIKITEXT,
            )

    # Validates: Requirements 6.5
    def test_archived_at_without_archived_url_raises(self):
        with pytest.raises(ValueError):
            build_citation(
                title="T",
                original_url="https://example.com/a",
                task_id="task-1",
                archived_url=None,
                archived_at=datetime(2024, 1, 1, 12, 0, 0),
                format=CitationFormat.WIKITEXT,
            )
