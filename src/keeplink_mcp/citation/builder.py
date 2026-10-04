"""Deterministic citation builder — pure formatting, no I/O.

Single Responsibility: given archive state and caller-supplied metadata,
produce a Citation payload (structured fields + a paste-ready `formatted`
string). Performs NO network request, page fetch, LLM call, or DB access.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class CitationFormat(str, Enum):
    """Supported citation output formats for the `formatted` field.

    str-Enum so it serializes directly and Pydantic can validate a request
    string against it. `markdown` is the default (backward compatible).
    """

    MARKDOWN = "markdown"
    BIBTEX = "bibtex"
    APA = "apa"
    PLAIN = "plain"


# Formatter signatures: complete needs the five fields; pending only needs
# original_url + task_id. Each formatter is a pure function with no I/O.
CompleteFormatter = Callable[..., str]
PendingFormatter = Callable[..., str]


@dataclass(frozen=True)
class Citation:
    """Immutable structured citation.

    Invariants (see Correctness Properties):
    - original_url, task_id, formatted are always non-null.
    - Pending  : archived_url is None  AND archived_at is None.
    - Complete : archived_url is not None AND archived_at is not None.
    """

    title: str | None
    original_url: str
    archived_url: str | None
    archived_at: datetime | None
    task_id: str
    formatted: str


def normalize_title(title: str | None) -> str | None:
    """Return a trimmed title, or None if it is missing or whitespace-only.

    Encodes Requirement 5.3 (whitespace-only title → null). Kept as a small
    reusable helper so both the endpoint and the builder share one rule.
    """
    if title is None:
        return None
    # str.strip() removes all Unicode whitespace, including \t and the
    # full-width space U+3000, so no explicit character set is needed.
    stripped = title.strip()
    return stripped or None


def build_citation(
    *,
    title: str | None,
    original_url: str,
    task_id: str,
    archived_url: str | None,
    archived_at: datetime | None,
    format: CitationFormat = CitationFormat.MARKDOWN,
) -> Citation:
    """Build a Citation deterministically from the given archive state.

    `format` selects how `formatted` is rendered; it does NOT change any
    structured field (title/original_url/archived_url/archived_at/task_id keep
    their existing semantics, Req 3.10). Defaults to MARKDOWN so existing
    callers and outputs are byte-for-byte unchanged (Req 3.2).

    Contract (unchanged from before):
    - If archived_url is None (pending) → archived_at MUST be None; produces a
      Pending_Citation whose `formatted` states archiving is in progress and
      includes original_url and task_id.
    - If archived_url is not None (complete) → archived_at MUST be provided;
      produces a complete `formatted` reference.
    - title is normalized via normalize_title() before use.

    Raises:
        ValueError: if the (archived_url, archived_at) pair is inconsistent
                    (fail-fast — one set but not the other).
    """
    normalized_title = normalize_title(title)

    # Fail-fast: pending vs complete is determined solely by archived_url; a
    # mismatched archived_at represents an impossible state and must surface
    # immediately rather than be masked by a fallback.
    if (archived_url is None) != (archived_at is None):
        raise ValueError(
            "Inconsistent archive state: archived_url and archived_at must "
            "both be set (complete) or both be None (pending)."
        )

    if archived_url is None:
        # Select the pending formatter by format (task 5.2 fills bodies).
        formatted = _PENDING_FORMATTERS[format](
            original_url=original_url, task_id=task_id
        )
        return Citation(
            title=normalized_title,
            original_url=original_url,
            archived_url=None,
            archived_at=None,
            task_id=task_id,
            formatted=formatted,
        )

    # Select the complete formatter by format (task 5.2 fills bodies).
    formatted = _COMPLETE_FORMATTERS[format](
        title=normalized_title,
        original_url=original_url,
        archived_url=archived_url,
        archived_at=archived_at,
    )
    return Citation(
        title=normalized_title,
        original_url=original_url,
        archived_url=archived_url,
        archived_at=archived_at,
        task_id=task_id,
        formatted=formatted,
    )


def _complete_markdown(
    *, title: str | None, original_url: str, archived_url: str, archived_at: datetime
) -> str:
    """Format a completed citation as markdown (the default, unchanged output).

    - With title    : `[title](archived_url) (original: original_url, archived YYYY-MM-DD)`
    - Without title  : archived_url is used as the link text in place of a title.
    Date is rendered as YYYY-MM-DD (Requirement 3.5).

    This is the former `_format_complete`; its output is preserved byte-for-byte
    so the markdown default stays backward compatible (Req 3.2).
    """
    # A null title falls back to archived_url as the link text so the output
    # stays a valid, deterministic markdown link (Requirement 3.4).
    link_text = title if title is not None else archived_url
    archived_date = archived_at.strftime("%Y-%m-%d")
    return (
        f"[{link_text}]({archived_url}) "
        f"(original: {original_url}, archived {archived_date})"
    )


def _pending_markdown(*, original_url: str, task_id: str) -> str:
    """Format a pending citation as markdown (the default, unchanged output).

    States archiving is in progress, including both original_url and task_id
    (Requirement 4.3). This is the former `_format_pending`; its output is
    preserved byte-for-byte for backward compatibility (Req 3.2).
    """
    return (
        f"Archiving in progress for {original_url} "
        f"(task_id: {task_id}); citation will be complete once archiving finishes."
    )


def _complete_bibtex(
    *, title: str | None, original_url: str, archived_url: str, archived_at: datetime
) -> str:
    """Format a completed citation as BibTeX.

    Renders a `@misc` entry whose `url` is the archived snapshot and whose
    `note` carries the archive date (YYYY-MM-DD) plus the original URL, so the
    entry is a valid, paste-ready reference to the preserved copy (Req 3.1/3.5).
    A null title falls back to archived_url so the `title` field is never empty
    (Req 3.4). Deterministic: derived only from inputs + strftime.
    """
    # archived_url doubles as the citation key seed and the title fallback so
    # the entry stays well-formed even when the caller supplied no title.
    entry_title = title if title is not None else archived_url
    archived_date = archived_at.strftime("%Y-%m-%d")
    return (
        f"@misc{{{_bibtex_key(archived_url, archived_date)},\n"
        f"  title = {{{entry_title}}},\n"
        f"  url = {{{archived_url}}},\n"
        f"  note = {{Archived {archived_date}; original: {original_url}}}\n"
        f"}}"
    )


def _complete_apa(
    *, title: str | None, original_url: str, archived_url: str, archived_at: datetime
) -> str:
    """Format a completed citation in APA style.

    Shape: `{title}. Retrieved YYYY-MM-DD, from {archived_url} (original:
    {original_url})`. A null title falls back to archived_url so the leading
    element is never empty (Req 3.4). Deterministic: inputs + strftime only.
    """
    lead = title if title is not None else archived_url
    retrieved_date = archived_at.strftime("%Y-%m-%d")
    return (
        f"{lead}. Retrieved {retrieved_date}, from {archived_url} "
        f"(original: {original_url})"
    )


def _complete_plain(
    *, title: str | None, original_url: str, archived_url: str, archived_at: datetime
) -> str:
    """Format a completed citation as plain text, no markdown link syntax.

    Shape: `{title 或 archived_url}. Original: {original_url}. Archived:
    {YYYY-MM-DD}. Archived URL: {archived_url}`. Contains no `[text](url)`
    markdown link syntax (Req 3.4). A null title falls back to archived_url.
    """
    lead = title if title is not None else archived_url
    archived_date = archived_at.strftime("%Y-%m-%d")
    return (
        f"{lead}. Original: {original_url}. "
        f"Archived: {archived_date}. Archived URL: {archived_url}"
    )


def _pending_bibtex(*, original_url: str, task_id: str) -> str:
    """Format a pending citation as BibTeX using original_url + task_id.

    No snapshot exists yet, so `url` points at the original page and `note`
    states archiving is in progress with the task_id — never fabricating an
    archived_url (Req 3.5). Deterministic: derived only from inputs.
    """
    return (
        f"@misc{{{_bibtex_key(original_url, task_id)},\n"
        f"  url = {{{original_url}}},\n"
        f"  note = {{Archiving in progress; task_id: {task_id}}}\n"
        f"}}"
    )


def _pending_apa(*, original_url: str, task_id: str) -> str:
    """Format a pending citation in APA style using original_url + task_id.

    Shape: `{original_url}. Archiving in progress (task_id: ...); original:
    {original_url}`. No archived_url is invented (Req 3.5). In the pending state
    the builder has no title, so original_url leads. Deterministic.
    """
    return (
        f"{original_url}. Archiving in progress (task_id: {task_id}); "
        f"original: {original_url}"
    )


def _pending_plain(*, original_url: str, task_id: str) -> str:
    """Format a pending citation as plain text using original_url + task_id.

    Plain, no markdown link syntax (Req 3.4). States archiving is in progress
    and includes both original_url and task_id (Req 3.5). Deterministic.
    """
    return (
        f"Original: {original_url}. "
        f"Archiving in progress (task_id: {task_id})."
    )


def _bibtex_key(seed: str, suffix: str) -> str:
    """Derive a deterministic, syntactically valid BibTeX citation key.

    BibTeX keys may not contain characters like `/`, `:`, `.`, or spaces, so we
    reduce the seed URL to its alphanumeric characters and append a suffix. Kept
    as a tiny helper so both bibtex formatters share one key-derivation rule
    (DRY). Purely deterministic — no randomness or hashing with a seed.
    """
    safe_seed = "".join(ch for ch in seed if ch.isalnum())
    safe_suffix = "".join(ch for ch in suffix if ch.isalnum())
    return f"keeplink_{safe_seed}_{safe_suffix}"


# Two dispatch tables (state × format). Adding a new format = add one pure
# formatter and register it here (open/closed, single responsibility).
_COMPLETE_FORMATTERS: dict[CitationFormat, CompleteFormatter] = {
    CitationFormat.MARKDOWN: _complete_markdown,
    CitationFormat.BIBTEX: _complete_bibtex,
    CitationFormat.APA: _complete_apa,
    CitationFormat.PLAIN: _complete_plain,
}
_PENDING_FORMATTERS: dict[CitationFormat, PendingFormatter] = {
    CitationFormat.MARKDOWN: _pending_markdown,
    CitationFormat.BIBTEX: _pending_bibtex,
    CitationFormat.APA: _pending_apa,
    CitationFormat.PLAIN: _pending_plain,
}
