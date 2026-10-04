# Feature: archive-and-cite
"""Property-based tests for the pure Citation_Builder functions.

Uses hypothesis to verify universal correctness of ``build_citation`` and
``normalize_title`` across a wide input space (title variants, timezone-aware
datetimes spanning month/year boundaries, and both pending/complete archive
states). Each test maps to a single Correctness Property from design.md.

All functions under test are pure (no I/O); Property 11 asserts this at
runtime by forbidding socket creation while they run.
"""

import socket
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from hypothesis import given, settings
from hypothesis import strategies as st

from keeplink_mcp.citation import Citation, build_citation, normalize_title
from keeplink_mcp.citation.builder import (
    CitationFormat,
    _complete_markdown,
    _pending_markdown,
)

# ---------------------------------------------------------------------------
# Strategies — smart generators constrained to the design's input space
# ---------------------------------------------------------------------------

# Titles: None, empty, whitespace-only (incl. \t and full-width space U+3000),
# and general strings containing unicode + markdown special characters.
_WHITESPACE_CHARS = [" ", "\t", "\n", "\r", "\v", "\f", "\u3000"]
_WHITESPACE_ONLY = st.lists(st.sampled_from(_WHITESPACE_CHARS), min_size=1, max_size=6).map(
    "".join
)
_MEANINGFUL_TITLE = st.text(
    alphabet=st.characters(
        # Include markdown-significant chars and unicode; the builder must treat
        # them as opaque text (no escaping), so they are valid link-text inputs.
        min_codepoint=0x20,
        max_codepoint=0x2FFF,
    ),
    min_size=1,
    max_size=40,
).filter(lambda s: s.strip() != "")

TITLE_STRATEGY = st.one_of(
    st.none(),
    st.just(""),
    _WHITESPACE_ONLY,
    _MEANINGFUL_TITLE,
)

# URLs: valid http/https URLs with a dotted hostname and optional path.
_SCHEME = st.sampled_from(["http", "https"])
_HOSTNAME = st.from_regex(r"[a-z][a-z0-9]{0,10}\.[a-z]{2,6}", fullmatch=True)
_PATH = st.from_regex(r"(/[a-z0-9\-]{1,10}){0,3}", fullmatch=True)

URL_STRATEGY = st.builds(
    lambda scheme, host, path: f"{scheme}://{host}{path}",
    scheme=_SCHEME,
    host=_HOSTNAME,
    path=_PATH,
)

TASK_ID_STRATEGY = st.uuids().map(str)

# Timezone-aware datetimes spanning month/year boundaries and single-digit
# month/day, to exercise the YYYY-MM-DD rendering. A fixed UTC offset drawn
# from a small set keeps the datetimes deterministically timezone-aware.
_TZ_OFFSETS = st.sampled_from([-12, -5, 0, 1, 8, 14])
ARCHIVED_AT_STRATEGY = st.builds(
    lambda dt, offset: dt.replace(tzinfo=timezone.utc).astimezone(
        timezone(timedelta(hours=offset))
    ),
    dt=st.datetimes(
        min_value=datetime(1999, 1, 1),
        max_value=datetime(2035, 12, 31, 23, 59, 59),
    ),
    offset=_TZ_OFFSETS,
)


def _build_kwargs(title, original_url, task_id, archived_url, archived_at):
    return {
        "title": title,
        "original_url": original_url,
        "task_id": task_id,
        "archived_url": archived_url,
        "archived_at": archived_at,
    }


# ---------------------------------------------------------------------------
# Property Tests
# ---------------------------------------------------------------------------


class TestCitationBuilderProperties:
    """Property-based tests for the deterministic Citation_Builder."""

    # Feature: archive-and-cite, Property 1: 格式化確定性 (Determinism) — for any
    # inputs and archive state, build_citation produces an identical Citation
    # (all fields incl. formatted) on repeated calls.
    @settings(max_examples=200)
    @given(
        title=TITLE_STRATEGY,
        original_url=URL_STRATEGY,
        task_id=TASK_ID_STRATEGY,
        archived_url=st.one_of(st.none(), URL_STRATEGY),
        archived_at=ARCHIVED_AT_STRATEGY,
    )
    def test_property_1_determinism(
        self, title, original_url, task_id, archived_url, archived_at
    ) -> None:
        """Property 1: 格式化確定性 (Determinism).

        **Validates: Requirements 8.1, 8.2**
        """
        # Keep the (archived_url, archived_at) pair consistent so build_citation
        # exercises a valid state; both pending and complete are covered.
        effective_at = archived_at if archived_url is not None else None
        kwargs = _build_kwargs(title, original_url, task_id, archived_url, effective_at)

        first = build_citation(**kwargs)
        second = build_citation(**kwargs)

        assert first == second
        assert first.formatted == second.formatted

    # Feature: archive-and-cite, Property 2: 兩態完整性不變式 (Two-state Completeness
    # Invariant) — original_url/task_id/formatted always non-null; pending => both
    # archived_url and archived_at null; complete => both non-null.
    @settings(max_examples=200)
    @given(
        title=TITLE_STRATEGY,
        original_url=URL_STRATEGY,
        task_id=TASK_ID_STRATEGY,
        is_complete=st.booleans(),
        archived_url=URL_STRATEGY,
        archived_at=ARCHIVED_AT_STRATEGY,
    )
    def test_property_2_two_state_completeness_invariant(
        self, title, original_url, task_id, is_complete, archived_url, archived_at
    ) -> None:
        """Property 2: 兩態完整性不變式 (Two-state Completeness Invariant).

        **Validates: Requirements 1.3, 4.1, 4.2, 7.1, 7.2, 7.3**
        """
        if is_complete:
            citation = build_citation(
                **_build_kwargs(title, original_url, task_id, archived_url, archived_at)
            )
        else:
            citation = build_citation(
                **_build_kwargs(title, original_url, task_id, None, None)
            )

        # Always-present fields (all six exist since Citation is a dataclass).
        assert citation.original_url is not None
        assert citation.task_id is not None
        assert citation.formatted is not None
        assert isinstance(citation, Citation)

        if is_complete:
            assert citation.archived_url is not None
            assert citation.archived_at is not None
        else:
            assert citation.archived_url is None
            assert citation.archived_at is None

    # Feature: archive-and-cite, Property 3: Cache-hit 欄位對映 — a complete citation
    # maps archived_url == result_url and archived_at == updated_at verbatim.
    @settings(max_examples=100)
    @given(
        title=TITLE_STRATEGY,
        original_url=URL_STRATEGY,
        task_id=TASK_ID_STRATEGY,
        result_url=URL_STRATEGY,
        updated_at=ARCHIVED_AT_STRATEGY,
    )
    def test_property_3_cache_hit_mapping(
        self, title, original_url, task_id, result_url, updated_at
    ) -> None:
        """Property 3: Cache-hit 欄位對映.

        **Validates: Requirements 3.1, 3.2**
        """
        citation = build_citation(
            **_build_kwargs(title, original_url, task_id, result_url, updated_at)
        )
        assert citation.archived_url == result_url
        assert citation.archived_at == updated_at

    # Feature: archive-and-cite, Property 4: 完整引用（含 title）格式 — for a complete
    # citation with a non-null title, formatted equals
    # [title](archived_url) (original: original_url, archived YYYY-MM-DD).
    @settings(max_examples=200)
    @given(
        title=_MEANINGFUL_TITLE,
        original_url=URL_STRATEGY,
        task_id=TASK_ID_STRATEGY,
        archived_url=URL_STRATEGY,
        archived_at=ARCHIVED_AT_STRATEGY,
    )
    def test_property_4_complete_with_title_format(
        self, title, original_url, task_id, archived_url, archived_at
    ) -> None:
        """Property 4: 完整引用（含 title）格式.

        **Validates: Requirements 3.3, 3.5**
        """
        citation = build_citation(
            **_build_kwargs(title, original_url, task_id, archived_url, archived_at)
        )
        normalized = normalize_title(title)
        date = archived_at.strftime("%Y-%m-%d")
        expected = (
            f"[{normalized}]({archived_url}) "
            f"(original: {original_url}, archived {date})"
        )
        assert citation.formatted == expected

    # Feature: archive-and-cite, Property 5: 完整引用（無 title）格式 — for a complete
    # citation with a null title, archived_url is used as the link text and the
    # output still includes the original URL and YYYY-MM-DD date.
    @settings(max_examples=200)
    @given(
        title=st.one_of(st.none(), st.just(""), _WHITESPACE_ONLY),
        original_url=URL_STRATEGY,
        task_id=TASK_ID_STRATEGY,
        archived_url=URL_STRATEGY,
        archived_at=ARCHIVED_AT_STRATEGY,
    )
    def test_property_5_complete_without_title_format(
        self, title, original_url, task_id, archived_url, archived_at
    ) -> None:
        """Property 5: 完整引用（無 title）格式.

        **Validates: Requirements 3.4, 3.5**
        """
        citation = build_citation(
            **_build_kwargs(title, original_url, task_id, archived_url, archived_at)
        )
        # Title normalizes to None, so archived_url becomes the link text.
        assert citation.title is None
        date = archived_at.strftime("%Y-%m-%d")
        expected = (
            f"[{archived_url}]({archived_url}) "
            f"(original: {original_url}, archived {date})"
        )
        assert citation.formatted == expected
        assert f"[{archived_url}]" in citation.formatted
        assert original_url in citation.formatted
        assert date in citation.formatted

    # Feature: archive-and-cite, Property 6: 進行中引用格式 — a pending citation states
    # archiving is in progress and includes both original_url and task_id.
    @settings(max_examples=200)
    @given(
        title=TITLE_STRATEGY,
        original_url=URL_STRATEGY,
        task_id=TASK_ID_STRATEGY,
    )
    def test_property_6_pending_format(self, title, original_url, task_id) -> None:
        """Property 6: 進行中引用格式.

        **Validates: Requirements 4.3**
        """
        citation = build_citation(
            **_build_kwargs(title, original_url, task_id, None, None)
        )
        assert original_url in citation.formatted
        assert task_id in citation.formatted
        # States that archiving is in progress (pending is null archive state).
        assert "progress" in citation.formatted.lower()

    # Feature: archive-and-cite, Property 7: 標題正規化 — normalize_title returns None
    # for None/whitespace-only and the stripped value otherwise; Citation.title
    # equals this normalized value.
    @settings(max_examples=200)
    @given(
        title=TITLE_STRATEGY,
        original_url=URL_STRATEGY,
        task_id=TASK_ID_STRATEGY,
        archived_url=st.one_of(st.none(), URL_STRATEGY),
        archived_at=ARCHIVED_AT_STRATEGY,
    )
    def test_property_7_title_normalization(
        self, title, original_url, task_id, archived_url, archived_at
    ) -> None:
        """Property 7: 標題正規化.

        **Validates: Requirements 5.1, 5.2, 5.3**
        """
        normalized = normalize_title(title)

        if title is None or title.strip() == "":
            assert normalized is None
        else:
            assert normalized == title.strip()

        effective_at = archived_at if archived_url is not None else None
        citation = build_citation(
            **_build_kwargs(title, original_url, task_id, archived_url, effective_at)
        )
        assert citation.title == normalized

    # Feature: archive-and-cite, Property 11: 無 I/O 純粹性 (No-I/O Purity) — while
    # producing a Citation, build_citation/normalize_title perform no network,
    # page fetch, LLM call, or DB access. Enforced at runtime by forbidding
    # socket creation.
    @settings(max_examples=100)
    @given(
        title=TITLE_STRATEGY,
        original_url=URL_STRATEGY,
        task_id=TASK_ID_STRATEGY,
        archived_url=st.one_of(st.none(), URL_STRATEGY),
        archived_at=ARCHIVED_AT_STRATEGY,
    )
    def test_property_11_no_io_purity(
        self, title, original_url, task_id, archived_url, archived_at
    ) -> None:
        """Property 11: 無 I/O 純粹性 (No-I/O Purity).

        **Validates: Requirements 2.4, 2.5, 8.3**
        """

        def _forbidden(*args, **kwargs):
            raise AssertionError(
                "build_citation/normalize_title must not perform any I/O "
                "(socket creation attempted)"
            )

        effective_at = archived_at if archived_url is not None else None

        # Any network/DB access ultimately opens a socket; forbidding it turns
        # a purity violation into an immediate, loud failure (fail-fast). A
        # context manager (rather than the monkeypatch fixture) is used so the
        # guard resets on every hypothesis-generated input without tripping the
        # function-scoped-fixture health check.
        with patch.object(socket, "socket", _forbidden), patch.object(
            socket, "create_connection", _forbidden
        ):
            normalize_title(title)
            citation = build_citation(
                **_build_kwargs(title, original_url, task_id, archived_url, effective_at)
            )
        assert citation.formatted is not None


# ===========================================================================
# Feature: keeplink-v1x-improvements — multi-format Citation_Builder
# ---------------------------------------------------------------------------
# The properties below exercise the `format` parameter (markdown/bibtex/apa/
# plain) added in improvement H. They live alongside the archive-and-cite
# properties above because both cover the same pure `build_citation` unit
# (design.md maps Properties 5,6,7,8,9,10,13 to this file).
# ===========================================================================

# All four supported output formats; used to sweep the format dimension.
FORMAT_STRATEGY = st.sampled_from(list(CitationFormat))

# Markdown link syntax marker: the `](` bridge in a `[text](url)` link. Plain
# text must never contain this structure (Property 8 / Requirement 3.4).
_MARKDOWN_LINK_MARKER = "]("


class TestMultiFormatCitationBuilderProperties:
    """Property-based tests for the multi-format Citation_Builder (improvement H)."""

    # Feature: keeplink-v1x-improvements, Property 5: markdown 預設向後相容 — 不傳
    # format 與 format=MARKDOWN 產生逐字元相同的 formatted，且等於既有 markdown
    # 邏輯（_complete_markdown / _pending_markdown）的輸出。
    @settings(max_examples=100)
    @given(
        title=TITLE_STRATEGY,
        original_url=URL_STRATEGY,
        task_id=TASK_ID_STRATEGY,
        archived_url=st.one_of(st.none(), URL_STRATEGY),
        archived_at=ARCHIVED_AT_STRATEGY,
    )
    def test_property_5_markdown_default_backward_compatible(
        self, title, original_url, task_id, archived_url, archived_at
    ) -> None:
        """Property 5: markdown 預設向後相容.

        **Validates: Requirements 3.2**
        """
        effective_at = archived_at if archived_url is not None else None
        kwargs = _build_kwargs(title, original_url, task_id, archived_url, effective_at)

        default = build_citation(**kwargs)
        explicit = build_citation(**kwargs, format=CitationFormat.MARKDOWN)

        # Default and explicit markdown are byte-for-byte identical.
        assert default.formatted == explicit.formatted

        # ...and equal to the pre-existing markdown formatter output.
        normalized_title = normalize_title(title)
        if archived_url is None:
            legacy = _pending_markdown(original_url=original_url, task_id=task_id)
        else:
            legacy = _complete_markdown(
                title=normalized_title,
                original_url=original_url,
                archived_url=archived_url,
                archived_at=effective_at,
            )
        assert default.formatted == legacy

    # Feature: keeplink-v1x-improvements, Property 6: 多格式確定性 — 相同輸入（任一
    # format × 任一 archive state）多次呼叫 build_citation 產生完全相同的 Citation
    # （所有欄位含 formatted 逐一相等）。
    @settings(max_examples=100)
    @given(
        title=TITLE_STRATEGY,
        original_url=URL_STRATEGY,
        task_id=TASK_ID_STRATEGY,
        archived_url=st.one_of(st.none(), URL_STRATEGY),
        archived_at=ARCHIVED_AT_STRATEGY,
        fmt=FORMAT_STRATEGY,
    )
    def test_property_6_multi_format_determinism(
        self, title, original_url, task_id, archived_url, archived_at, fmt
    ) -> None:
        """Property 6: 多格式確定性.

        **Validates: Requirements 3.1, 3.3**
        """
        effective_at = archived_at if archived_url is not None else None
        kwargs = _build_kwargs(title, original_url, task_id, archived_url, effective_at)

        first = build_citation(**kwargs, format=fmt)
        second = build_citation(**kwargs, format=fmt)

        assert first == second
        assert first.formatted == second.formatted

    # Feature: keeplink-v1x-improvements, Property 7: 兩態 × 四格式完整性 — 任一
    # archive state（pending 或 complete）× 任一 format 皆產生非空 formatted，
    # 無未涵蓋組合、無例外。
    @settings(max_examples=100)
    @given(
        title=TITLE_STRATEGY,
        original_url=URL_STRATEGY,
        task_id=TASK_ID_STRATEGY,
        archived_url=st.one_of(st.none(), URL_STRATEGY),
        archived_at=ARCHIVED_AT_STRATEGY,
        fmt=FORMAT_STRATEGY,
    )
    def test_property_7_two_state_four_format_completeness(
        self, title, original_url, task_id, archived_url, archived_at, fmt
    ) -> None:
        """Property 7: 兩態 × 四格式完整性.

        **Validates: Requirements 3.1**
        """
        effective_at = archived_at if archived_url is not None else None
        citation = build_citation(
            **_build_kwargs(title, original_url, task_id, archived_url, effective_at),
            format=fmt,
        )
        # Every state × format combination yields a non-empty formatted string.
        assert isinstance(citation.formatted, str)
        assert citation.formatted != ""
        assert citation.formatted.strip() != ""

    # Feature: keeplink-v1x-improvements, Property 8: plain 格式不含 markdown 連結
    # 語法 — 當 format == plain 時，formatted 不出現 markdown 連結結構（`](`）。
    @settings(max_examples=100)
    @given(
        title=TITLE_STRATEGY,
        original_url=URL_STRATEGY,
        task_id=TASK_ID_STRATEGY,
        archived_url=st.one_of(st.none(), URL_STRATEGY),
        archived_at=ARCHIVED_AT_STRATEGY,
    )
    def test_property_8_plain_has_no_markdown_link_syntax(
        self, title, original_url, task_id, archived_url, archived_at
    ) -> None:
        """Property 8: plain 格式不含 markdown 連結語法.

        **Validates: Requirements 3.4**
        """
        effective_at = archived_at if archived_url is not None else None
        citation = build_citation(
            **_build_kwargs(title, original_url, task_id, archived_url, effective_at),
            format=CitationFormat.PLAIN,
        )
        # No `[text](url)` markdown link structure appears in plain output.
        assert _MARKDOWN_LINK_MARKER not in citation.formatted

    # Feature: keeplink-v1x-improvements, Property 9: pending 態 bibtex/apa 以
    # original_url + task_id 表達進行中 — pending + bibtex/apa 時 formatted 同時
    # 含 original_url 與 task_id 且明確標示存檔進行中（不虛構 archived_url）。
    @settings(max_examples=100)
    @given(
        title=TITLE_STRATEGY,
        original_url=URL_STRATEGY,
        task_id=TASK_ID_STRATEGY,
        fmt=st.sampled_from([CitationFormat.BIBTEX, CitationFormat.APA]),
    )
    def test_property_9_pending_bibtex_apa_fallback(
        self, title, original_url, task_id, fmt
    ) -> None:
        """Property 9: pending 態 bibtex/apa fallback.

        **Validates: Requirements 3.5**
        """
        citation = build_citation(
            **_build_kwargs(title, original_url, task_id, None, None),
            format=fmt,
        )
        # Pending fallback references the original page + task, marks progress,
        # and never invents an archived_url.
        assert original_url in citation.formatted
        assert task_id in citation.formatted
        assert "progress" in citation.formatted.lower()
        assert citation.archived_url is None

    # Feature: keeplink-v1x-improvements, Property 10: 結構化欄位不受 format 影響 —
    # 任一 format 回傳的結構化欄位（title/original_url/archived_url/archived_at/
    # task_id）與 format=markdown 時相同；format 只影響 formatted。
    @settings(max_examples=100)
    @given(
        title=TITLE_STRATEGY,
        original_url=URL_STRATEGY,
        task_id=TASK_ID_STRATEGY,
        archived_url=st.one_of(st.none(), URL_STRATEGY),
        archived_at=ARCHIVED_AT_STRATEGY,
        fmt=FORMAT_STRATEGY,
    )
    def test_property_10_structured_fields_format_invariant(
        self, title, original_url, task_id, archived_url, archived_at, fmt
    ) -> None:
        """Property 10: 結構化欄位不受 format 影響.

        **Validates: Requirements 3.10**
        """
        effective_at = archived_at if archived_url is not None else None
        kwargs = _build_kwargs(title, original_url, task_id, archived_url, effective_at)

        markdown = build_citation(**kwargs, format=CitationFormat.MARKDOWN)
        other = build_citation(**kwargs, format=fmt)

        # Structured fields are identical regardless of format; only formatted
        # is allowed to differ.
        assert other.title == markdown.title
        assert other.original_url == markdown.original_url
        assert other.archived_url == markdown.archived_url
        assert other.archived_at == markdown.archived_at
        assert other.task_id == markdown.task_id

    # Feature: keeplink-v1x-improvements, Property 13: Citation_Builder 無 I/O
    # 純粹性 — 產生 Citation 的過程（任一 format）不執行任何網路請求、頁面抓取、
    # LLM 呼叫或 DB 存取。以禁止 socket 建立於執行期強制驗證。
    @settings(max_examples=100)
    @given(
        title=TITLE_STRATEGY,
        original_url=URL_STRATEGY,
        task_id=TASK_ID_STRATEGY,
        archived_url=st.one_of(st.none(), URL_STRATEGY),
        archived_at=ARCHIVED_AT_STRATEGY,
        fmt=FORMAT_STRATEGY,
    )
    def test_property_13_no_io_purity(
        self, title, original_url, task_id, archived_url, archived_at, fmt
    ) -> None:
        """Property 13: Citation_Builder 無 I/O 純粹性.

        **Validates: Requirements 3.3**
        """

        def _forbidden(*args, **kwargs):
            raise AssertionError(
                "build_citation must not perform any I/O "
                "(socket creation attempted)"
            )

        effective_at = archived_at if archived_url is not None else None

        # Any network/page-fetch/LLM/DB access ultimately opens a socket;
        # forbidding it turns a purity violation into an immediate, loud
        # failure. A context manager (not the monkeypatch fixture) keeps the
        # guard reset per hypothesis input without the function-scoped-fixture
        # health-check warning.
        with patch.object(socket, "socket", _forbidden), patch.object(
            socket, "create_connection", _forbidden
        ):
            citation = build_citation(
                **_build_kwargs(title, original_url, task_id, archived_url, effective_at),
                format=fmt,
            )
        assert citation.formatted is not None
