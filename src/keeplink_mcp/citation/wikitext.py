"""Wikipedia ``{{cite web}}`` helpers: constants and escaping.

Pure functions, no I/O. Standard library only. This module must not import
``builder.py``: ``builder.py`` imports from here, so the reverse would create
a circular import.

Escaping rules follow the wikitext-citation-format design (D5-D8, D11):
title text is entity-escaped, URLs are percent-encoded.
"""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import quote

# KeepLink pre-archives at citation time and does not probe whether the
# original URL is still alive, so "live" is the only claim we can support.
URL_STATUS_LIVE: str = "live"

# Single-pass table: replacements never get re-escaped. "=", "&" and ">" are
# deliberately absent (named parameters tolerate "=", and escaping "&" would
# turn "AT&T" into "AT&amp;T").
_TITLE_TRANSLATION: dict[int, str] = {
    ord("|"): "&#124;",
    ord("["): "&#91;",
    ord("]"): "&#93;",
    ord("{"): "&#123;",
    ord("}"): "&#125;",
    ord("<"): "&#60;",
}

# Whitespace (all Unicode) plus the characters CS1 requires encoded in URLs.
# "%" is not matched so already-encoded URLs are not double-encoded.
_URL_UNSAFE_RE: re.Pattern[str] = re.compile(r"""[\s"'<>\[\]{|}]""")


def escape_title(title: str) -> str:
    """Escape characters in ``title`` that would break the template syntax.

    Line-break characters (as recognised by ``str.splitlines``) are joined
    with a single space, then ``| [ ] { } <`` are replaced with numeric HTML
    entities in one ``str.translate`` pass. ``=``, ``&`` and ``>`` are left
    untouched.
    """
    return " ".join(title.splitlines()).translate(_TITLE_TRANSLATION)


def escape_url(url: str) -> str:
    """Percent-encode (UTF-8) characters in ``url`` that break the template.

    Matches whitespace and ``" ' < > [ ] { | }``; every other character,
    including ``%``, is returned unchanged.
    """
    return _URL_UNSAFE_RE.sub(lambda m: quote(m.group(0), safe=""), url)


def complete_wikitext(
    *, title: str | None, original_url: str, archived_url: str, archived_at: datetime
) -> str:
    """Format a completed citation as a single-line ``{{cite web}}`` template.

    Parameter order is fixed: title, url, url-status, archive-url,
    archive-date. A null title yields an empty ``|title=`` rather than a
    substitute value: filling it with a URL would present a title we do not
    have, while an empty value makes Wikipedia flag the missing title.
    """
    title_value = "" if title is None else escape_title(title)
    archive_date = archived_at.strftime("%Y-%m-%d")
    return (
        "{{cite web"
        f" |title={title_value}"
        f" |url={escape_url(original_url)}"
        f" |url-status={URL_STATUS_LIVE}"
        f" |archive-url={escape_url(archived_url)}"
        f" |archive-date={archive_date}"
        "}}"
    )


def pending_wikitext(*, original_url: str, task_id: str) -> str:
    """Format a pending citation: only ``url`` plus a hidden HTML comment.

    ``cite web`` has no parameter for progress info, so it goes in a comment.
    ``-->`` inside task_id is rewritten to ``-- >`` so it cannot close the
    comment early and leak the remainder into the rendered template.
    """
    safe_task_id = task_id.replace("-->", "-- >")
    return (
        f"{{{{cite web |url={escape_url(original_url)} "
        f"<!-- Archiving in progress; task_id: {safe_task_id} -->}}}}"
    )
