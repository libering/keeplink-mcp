"""既有四種引用格式的回歸基準(凍結字面字串)。

這些字串在 builder.py 加入 wikitext 之前,以當時未修改的程式碼產生。
若斷言失敗,代表既有格式的輸出被改動,違反 Requirements 7.1 / 7.2。
"""

from __future__ import annotations

from datetime import datetime

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from keeplink_mcp.citation.builder import (
    CitationFormat,
    _complete_apa,
    _complete_bibtex,
    _complete_markdown,
    _complete_plain,
    _pending_apa,
    _pending_bibtex,
    _pending_markdown,
    _pending_plain,
    build_citation,
    normalize_title,
)

_H_TITLE = "Example"
_H_ORIGINAL_URL = "https://example.com/article"
_H_ARCHIVED_URL = (
    "https://web.archive.org/web/20240101000000/https://example.com/article"
)
_H_TASK_ID = "task-abc-123"
_H_ARCHIVED_AT = datetime(2024, 1, 1, 12, 30, 45)

_MARKDOWN_COMPLETE = (
    "[Example](https://web.archive.org/web/20240101000000/https://example.com/article)"
    " (original: https://example.com/article, archived 2024-01-01)"
)
_MARKDOWN_PENDING = (
    "Archiving in progress for https://example.com/article (task_id: task-abc-123);"
    " citation will be complete once archiving finishes."
)
_BIBTEX_COMPLETE = (
    "@misc{keeplink_httpswebarchiveorgweb20240101000000httpsexamplecomarticle_20240101,\n"
    "  title = {Example},\n"
    "  url = {https://web.archive.org/web/20240101000000/https://example.com/article},\n"
    "  note = {Archived 2024-01-01; original: https://example.com/article}\n"
    "}"
)
_BIBTEX_PENDING = (
    "@misc{keeplink_httpsexamplecomarticle_taskabc123,\n"
    "  url = {https://example.com/article},\n"
    "  note = {Archiving in progress; task_id: task-abc-123}\n"
    "}"
)
_APA_COMPLETE = (
    "Example. Retrieved 2024-01-01, from "
    "https://web.archive.org/web/20240101000000/https://example.com/article"
    " (original: https://example.com/article)"
)
_APA_PENDING = (
    "https://example.com/article. Archiving in progress (task_id: task-abc-123);"
    " original: https://example.com/article"
)
_PLAIN_COMPLETE = (
    "Example. Original: https://example.com/article. Archived: 2024-01-01."
    " Archived URL: https://web.archive.org/web/20240101000000/https://example.com/article"
)
_PLAIN_PENDING = (
    "Original: https://example.com/article. Archiving in progress (task_id: task-abc-123)."
)


def _complete(fmt: CitationFormat | None = None) -> str:
    kwargs = {} if fmt is None else {"format": fmt}
    return build_citation(
        title=_H_TITLE,
        original_url=_H_ORIGINAL_URL,
        task_id=_H_TASK_ID,
        archived_url=_H_ARCHIVED_URL,
        archived_at=_H_ARCHIVED_AT,
        **kwargs,
    ).formatted


def _pending(fmt: CitationFormat) -> str:
    return build_citation(
        title=_H_TITLE,
        original_url=_H_ORIGINAL_URL,
        task_id=_H_TASK_ID,
        archived_url=None,
        archived_at=None,
        format=fmt,
    ).formatted


@pytest.mark.parametrize(
    ("fmt", "expected"),
    [
        (CitationFormat.MARKDOWN, _MARKDOWN_COMPLETE),
        (CitationFormat.BIBTEX, _BIBTEX_COMPLETE),
        (CitationFormat.APA, _APA_COMPLETE),
        (CitationFormat.PLAIN, _PLAIN_COMPLETE),
    ],
)
def test_complete_formats_match_frozen_literals(fmt, expected):
    assert _complete(fmt) == expected


@pytest.mark.parametrize(
    ("fmt", "expected"),
    [
        (CitationFormat.MARKDOWN, _MARKDOWN_PENDING),
        (CitationFormat.BIBTEX, _BIBTEX_PENDING),
        (CitationFormat.APA, _APA_PENDING),
        (CitationFormat.PLAIN, _PLAIN_PENDING),
    ],
)
def test_pending_formats_match_frozen_literals(fmt, expected):
    assert _pending(fmt) == expected


def test_default_format_equals_markdown_literal():
    assert _complete() == _MARKDOWN_COMPLETE

_COMPLETE_DIRECT = {
    CitationFormat.MARKDOWN: _complete_markdown,
    CitationFormat.BIBTEX: _complete_bibtex,
    CitationFormat.APA: _complete_apa,
    CitationFormat.PLAIN: _complete_plain,
}
_PENDING_DIRECT = {
    CitationFormat.MARKDOWN: _pending_markdown,
    CitationFormat.BIBTEX: _pending_bibtex,
    CitationFormat.APA: _pending_apa,
    CitationFormat.PLAIN: _pending_plain,
}
_LEGACY_FORMATS = st.sampled_from(list(_COMPLETE_DIRECT))
_TITLES = st.one_of(st.none(), st.text(max_size=60))
_URLS = st.text(min_size=1, max_size=80)
_TASK_IDS = st.text(min_size=1, max_size=40)
_DATETIMES = st.datetimes(min_value=datetime(1970, 1, 1))


# Feature: wikitext-citation-format, Property 8: 既有格式逐位元組不變
# Validates: Requirements 7.1, 7.2
@settings(max_examples=100)
@given(
    fmt=_LEGACY_FORMATS,
    title=_TITLES,
    original_url=_URLS,
    task_id=_TASK_IDS,
    archived_url=_URLS,
    archived_at=_DATETIMES,
)
def test_property_8_complete_matches_private_formatter(
    fmt, title, original_url, task_id, archived_url, archived_at
):
    actual = build_citation(
        title=title,
        original_url=original_url,
        task_id=task_id,
        archived_url=archived_url,
        archived_at=archived_at,
        format=fmt,
    ).formatted
    expected = _COMPLETE_DIRECT[fmt](
        title=normalize_title(title),
        original_url=original_url,
        archived_url=archived_url,
        archived_at=archived_at,
    )
    assert actual == expected


# Feature: wikitext-citation-format, Property 8: 既有格式逐位元組不變
# Validates: Requirements 7.1, 7.2
@settings(max_examples=100)
@given(fmt=_LEGACY_FORMATS, title=_TITLES, original_url=_URLS, task_id=_TASK_IDS)
def test_property_8_pending_matches_private_formatter(
    fmt, title, original_url, task_id
):
    actual = build_citation(
        title=title,
        original_url=original_url,
        task_id=task_id,
        archived_url=None,
        archived_at=None,
        format=fmt,
    ).formatted
    expected = _PENDING_DIRECT[fmt](original_url=original_url, task_id=task_id)
    assert actual == expected
