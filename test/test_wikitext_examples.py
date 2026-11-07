"""Golden literal-string tests for the wikitext ``{{cite web}}`` formatter.

Expected strings are derived from the wikitext-citation-format design
(field order D1, date D2, url-status D4, title escaping D5-D8, URL encoding
D11, pending comment D12), not copied from implementation output. No mocks.
"""

from __future__ import annotations

from datetime import datetime

from keeplink_mcp.citation.wikitext import complete_wikitext, pending_wikitext

ORIGINAL_URL = "https://example.com/article"
ARCHIVED_URL = "https://web.archive.org/web/20240101000000/https://example.com/article"
ARCHIVED_AT = datetime(2024, 1, 1, 12, 30, 45)


def _complete(title: str | None, original_url: str = ORIGINAL_URL) -> str:
    return complete_wikitext(
        title=title,
        original_url=original_url,
        archived_url=ARCHIVED_URL,
        archived_at=ARCHIVED_AT,
    )


def test_complete_plain_title() -> None:
    assert _complete("Example") == (
        "{{cite web |title=Example |url=https://example.com/article "
        "|url-status=live "
        "|archive-url=https://web.archive.org/web/20240101000000/"
        "https://example.com/article "
        "|archive-date=2024-01-01}}"
    )


def test_complete_title_none_yields_empty_title_parameter() -> None:
    assert _complete(None) == (
        "{{cite web |title= |url=https://example.com/article "
        "|url-status=live "
        "|archive-url=https://web.archive.org/web/20240101000000/"
        "https://example.com/article "
        "|archive-date=2024-01-01}}"
    )


def test_complete_title_with_reserved_characters_is_entity_escaped() -> None:
    expected = (
        "{{cite web |title=A &#124; B &#123;&#123;x&#125;&#125; &#91;y&#93;=z "
        "|url=https://example.com/article |url-status=live "
        "|archive-url=https://web.archive.org/web/20240101000000/"
        "https://example.com/article "
        "|archive-date=2024-01-01}}"
    )
    assert _complete("A | B {{x}} [y]=z") == expected


def test_complete_url_with_space_and_pipe_is_percent_encoded() -> None:
    assert _complete("Example", "https://example.com/a b|c") == (
        "{{cite web |title=Example |url=https://example.com/a%20b%7Cc "
        "|url-status=live "
        "|archive-url=https://web.archive.org/web/20240101000000/"
        "https://example.com/article "
        "|archive-date=2024-01-01}}"
    )


def test_pending_has_only_url_and_hidden_comment() -> None:
    result = pending_wikitext(original_url=ORIGINAL_URL, task_id="task-abc-123")
    assert result == (
        "{{cite web |url=https://example.com/article "
        "<!-- Archiving in progress; task_id: task-abc-123 -->}}"
    )


def test_archive_date_is_iso_date_without_time_and_no_access_date() -> None:
    result = _complete("Example")
    assert "|archive-date=2024-01-01}}" in result
    assert "12:30:45" not in result
    assert "access-date" not in result
