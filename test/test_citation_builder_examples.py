# Feature: archive-and-cite
"""Golden example unit tests for the deterministic Citation builder.

Complements the property-based tests with concrete, human-readable golden
strings that pin the exact `formatted` output for the three citation states
(complete-with-title, complete-without-title, pending) and asserts the
fail-fast ValueError contract for inconsistent (archived_url, archived_at)
pairs.
"""

import logging
from datetime import datetime

import mcp.types as mcp_types
import pytest
from httpx import ASGITransport, AsyncClient

from keeplink_mcp.api.app import create_app
from keeplink_mcp.citation import Citation, build_citation
from keeplink_mcp.citation import build_citation as _build_citation
from keeplink_mcp.citation.builder import CitationFormat
from keeplink_mcp.config import Config
from keeplink_mcp.mcp_server.server import create_mcp_server

# ---------------------------------------------------------------------------
# Fixed inputs — deterministic values shared across golden examples
# ---------------------------------------------------------------------------

_TITLE = "Example Article"
_ORIGINAL_URL = "https://example.com/article"
_ARCHIVED_URL = "https://web.archive.org/web/20240101000000/https://example.com/article"
_TASK_ID = "task-abc-123"
# A fixed datetime so the rendered YYYY-MM-DD date is deterministic.
_ARCHIVED_AT = datetime(2024, 1, 1, 12, 30, 45)


class TestCitationGoldenStrings:
    """Exact `formatted` golden strings for each citation state."""

    def test_complete_with_title(self) -> None:
        """complete-with-title: title used as markdown link text.

        **Validates: Requirements 3.3, 3.4, 7.2**
        """
        citation = build_citation(
            title=_TITLE,
            original_url=_ORIGINAL_URL,
            task_id=_TASK_ID,
            archived_url=_ARCHIVED_URL,
            archived_at=_ARCHIVED_AT,
        )

        expected = (
            f"[{_TITLE}]({_ARCHIVED_URL}) "
            f"(original: {_ORIGINAL_URL}, archived 2024-01-01)"
        )
        assert citation.formatted == expected
        assert isinstance(citation, Citation)
        assert citation.title == _TITLE
        assert citation.archived_url == _ARCHIVED_URL
        assert citation.archived_at == _ARCHIVED_AT

    def test_complete_without_title(self) -> None:
        """complete-without-title: archived_url used as the link text.

        **Validates: Requirements 3.4, 7.2**
        """
        citation = build_citation(
            title=None,
            original_url=_ORIGINAL_URL,
            task_id=_TASK_ID,
            archived_url=_ARCHIVED_URL,
            archived_at=_ARCHIVED_AT,
        )

        expected = (
            f"[{_ARCHIVED_URL}]({_ARCHIVED_URL}) "
            f"(original: {_ORIGINAL_URL}, archived 2024-01-01)"
        )
        assert citation.formatted == expected
        assert citation.title is None

    def test_pending(self) -> None:
        """pending: archiving-in-progress message with original_url and task_id.

        **Validates: Requirements 4.3, 7.3**
        """
        citation = build_citation(
            title=_TITLE,
            original_url=_ORIGINAL_URL,
            task_id=_TASK_ID,
            archived_url=None,
            archived_at=None,
        )

        expected = (
            f"Archiving in progress for {_ORIGINAL_URL} "
            f"(task_id: {_TASK_ID}); citation will be complete once archiving finishes."
        )
        assert citation.formatted == expected
        assert citation.archived_url is None
        assert citation.archived_at is None


class TestCitationInconsistentState:
    """Fail-fast contract: partial (archived_url, archived_at) pairs raise."""

    def test_only_archived_url_set_raises(self) -> None:
        """Only archived_url set (archived_at=None) must raise ValueError.

        **Validates: Requirements 7.2, 7.3**
        """
        with pytest.raises(ValueError):
            build_citation(
                title=_TITLE,
                original_url=_ORIGINAL_URL,
                task_id=_TASK_ID,
                archived_url=_ARCHIVED_URL,
                archived_at=None,
            )

    def test_only_archived_at_set_raises(self) -> None:
        """Only archived_at set (archived_url=None) must raise ValueError.

        **Validates: Requirements 7.2, 7.3**
        """
        with pytest.raises(ValueError):
            build_citation(
                title=_TITLE,
                original_url=_ORIGINAL_URL,
                task_id=_TASK_ID,
                archived_url=None,
                archived_at=_ARCHIVED_AT,
            )


# ===========================================================================
# Improvement H — multi-format citation golden / example unit tests
# ===========================================================================
#
# Golden strings for the four Citation_Format values across the complete and
# pending states, plus schema-default, tool-schema, and invalid-format-logging
# checks. Derived from the concrete formatter implementations in
# ``keeplink_mcp.citation.builder`` using the fixed inputs above so each golden
# string pins the exact `formatted` output byte-for-byte.

# Bare original URL (no path) so the pending-state golden strings are readable
# and match the fixed inputs shared across the improvement-H examples.
_H_TITLE = "Example"
_H_ORIGINAL_URL = "https://example.com/article"
_H_ARCHIVED_URL = (
    "https://web.archive.org/web/20240101000000/https://example.com/article"
)
_H_TASK_ID = "task-abc-123"
_H_ARCHIVED_AT = datetime(2024, 1, 1, 12, 30, 45)
# Deterministic BibTeX keys derived by builder._bibtex_key (alnum-only seed +
# alnum-only suffix). Pinned here so the golden strings are self-contained.
_H_COMPLETE_BIBTEX_KEY = (
    "keeplink_httpswebarchiveorgweb20240101000000httpsexamplecomarticle_20240101"
)
_H_PENDING_BIBTEX_KEY = "keeplink_httpsexamplecomarticle_taskabc123"


def _complete(fmt: CitationFormat):
    return _build_citation(
        title=_H_TITLE,
        original_url=_H_ORIGINAL_URL,
        task_id=_H_TASK_ID,
        archived_url=_H_ARCHIVED_URL,
        archived_at=_H_ARCHIVED_AT,
        format=fmt,
    )


def _pending(fmt: CitationFormat):
    return _build_citation(
        title=_H_TITLE,
        original_url=_H_ORIGINAL_URL,
        task_id=_H_TASK_ID,
        archived_url=None,
        archived_at=None,
        format=fmt,
    )


class TestCompleteFormatGoldenStrings:
    """Exact `formatted` golden strings for a Complete_Citation × 4 formats.

    **Validates: Requirements 3.1, 3.4**
    """

    def test_complete_markdown(self) -> None:
        expected = (
            f"[{_H_TITLE}]({_H_ARCHIVED_URL}) "
            f"(original: {_H_ORIGINAL_URL}, archived 2024-01-01)"
        )
        assert _complete(CitationFormat.MARKDOWN).formatted == expected

    def test_complete_bibtex(self) -> None:
        expected = (
            f"@misc{{{_H_COMPLETE_BIBTEX_KEY},\n"
            f"  title = {{{_H_TITLE}}},\n"
            f"  url = {{{_H_ARCHIVED_URL}}},\n"
            f"  note = {{Archived 2024-01-01; original: {_H_ORIGINAL_URL}}}\n"
            f"}}"
        )
        assert _complete(CitationFormat.BIBTEX).formatted == expected

    def test_complete_apa(self) -> None:
        expected = (
            f"{_H_TITLE}. Retrieved 2024-01-01, from {_H_ARCHIVED_URL} "
            f"(original: {_H_ORIGINAL_URL})"
        )
        assert _complete(CitationFormat.APA).formatted == expected

    def test_complete_plain(self) -> None:
        expected = (
            f"{_H_TITLE}. Original: {_H_ORIGINAL_URL}. "
            f"Archived: 2024-01-01. Archived URL: {_H_ARCHIVED_URL}"
        )
        formatted = _complete(CitationFormat.PLAIN).formatted
        assert formatted == expected
        # plain must carry no markdown link syntax (Req 3.4).
        assert "](" not in formatted


class TestPendingFormatGoldenStrings:
    """Exact `formatted` golden strings for a Pending_Citation × 4 formats.

    **Validates: Requirements 3.1, 3.4, 3.5**
    """

    def test_pending_markdown(self) -> None:
        expected = (
            f"Archiving in progress for {_H_ORIGINAL_URL} "
            f"(task_id: {_H_TASK_ID}); citation will be complete once archiving "
            f"finishes."
        )
        assert _pending(CitationFormat.MARKDOWN).formatted == expected

    def test_pending_bibtex(self) -> None:
        expected = (
            f"@misc{{{_H_PENDING_BIBTEX_KEY},\n"
            f"  url = {{{_H_ORIGINAL_URL}}},\n"
            f"  note = {{Archiving in progress; task_id: {_H_TASK_ID}}}\n"
            f"}}"
        )
        formatted = _pending(CitationFormat.BIBTEX).formatted
        assert formatted == expected
        # pending bibtex expresses progress via original_url + task_id (Req 3.5).
        assert _H_ORIGINAL_URL in formatted
        assert _H_TASK_ID in formatted

    def test_pending_apa(self) -> None:
        expected = (
            f"{_H_ORIGINAL_URL}. Archiving in progress (task_id: {_H_TASK_ID}); "
            f"original: {_H_ORIGINAL_URL}"
        )
        formatted = _pending(CitationFormat.APA).formatted
        assert formatted == expected
        assert _H_ORIGINAL_URL in formatted
        assert _H_TASK_ID in formatted

    def test_pending_plain(self) -> None:
        expected = (
            f"Original: {_H_ORIGINAL_URL}. "
            f"Archiving in progress (task_id: {_H_TASK_ID})."
        )
        formatted = _pending(CitationFormat.PLAIN).formatted
        assert formatted == expected
        assert "](" not in formatted


class TestCiteRequestFormatDefault:
    """CiteRequest.format defaults to markdown when omitted.

    **Validates: Requirements 3.6**
    """

    def test_format_defaults_to_markdown(self) -> None:
        from keeplink_mcp.api.schemas import CiteRequest

        request = CiteRequest(url=_H_ORIGINAL_URL)
        assert request.format == CitationFormat.MARKDOWN


class TestArchiveAndCiteFormatSchema:
    """archive_and_cite inputSchema exposes an optional 4-value `format` enum.

    **Validates: Requirements 3.7**
    """

    async def _archive_and_cite_tool(self) -> mcp_types.Tool:
        server = create_mcp_server(Config())
        handler = server.request_handlers[mcp_types.ListToolsRequest]
        result = await handler(
            mcp_types.ListToolsRequest(method="tools/list", params=None)
        )
        tool = next(
            (t for t in result.root.tools if t.name == "archive_and_cite"), None
        )
        assert tool is not None
        return tool

    async def test_format_enum_is_the_four_values(self) -> None:
        tool = await self._archive_and_cite_tool()
        fmt = tool.inputSchema["properties"]["format"]
        assert fmt["enum"] == ["markdown", "bibtex", "apa", "plain"]

    async def test_format_is_optional(self) -> None:
        tool = await self._archive_and_cite_tool()
        # format is declared but absent from required (url stays the only one).
        assert "format" in tool.inputSchema["properties"]
        assert "format" not in tool.inputSchema["required"]


class TestInvalidFormatLogsThen422:
    """An out-of-enum `format` is logged (logger.warning) then rejected 422.

    **Validates: Requirements 3.8**
    """

    async def test_invalid_format_logs_warning_and_returns_422(
        self, session_factory, caplog
    ) -> None:
        app = create_app(session_factory)
        transport = ASGITransport(app=app)
        with caplog.at_level(logging.WARNING, logger="keeplink_mcp.api.app"):
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/cite",
                    json={"url": _H_ORIGINAL_URL, "format": "not-a-format"},
                )

        assert resp.status_code == 422
        # The invalid value is logged BEFORE rejection so it is never silently
        # coerced to the default (Req 3.8 log面).
        warnings = [
            r for r in caplog.records if r.levelno == logging.WARNING
        ]
        assert any(
            "invalid format value" in r.getMessage()
            and "not-a-format" in r.getMessage()
            for r in warnings
        )
