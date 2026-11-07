"""Property-based tests for the wikitext ``{{cite web}}`` formatter.

Feature: wikitext-citation-format. Complete-state properties 1-5 call
``complete_wikitext`` directly. Everything the assertions rely on (the parser
and the expected-value helpers) is written independently here and shares no
code with ``wikitext.py``, so a bug in the implementation cannot also bend the
oracle.
"""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import quote

from hypothesis import given, settings
from hypothesis import strategies as st

from keeplink_mcp.citation.wikitext import complete_wikitext, pending_wikitext

EXPECTED_PARAMS = ["title", "url", "url-status", "archive-url", "archive-date"]

# ---------------------------------------------------------------------------
# Independent test oracle
# ---------------------------------------------------------------------------

_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def parse_cite_web(formatted: str) -> list[tuple[str, str]]:
    """Parse a ``{{cite web ...}}`` string into ordered (name, value) pairs.

    Strips the ``{{cite web`` prefix and ``}}`` suffix, removes HTML comments,
    splits on ``|`` and splits each segment at the first ``=``.
    """
    assert formatted.startswith("{{cite web")
    assert formatted.endswith("}}")
    body = formatted[len("{{cite web") : -len("}}")]
    body = _COMMENT_RE.sub("", body)
    pairs: list[tuple[str, str]] = []
    for segment in body.split("|")[1:]:  # [0] is the text before the first "|"
        name, sep, value = segment.partition("=")
        assert sep == "=", f"segment without '=': {segment!r}"
        pairs.append((name.strip(), value.strip()))
    return pairs


_URL_RESERVED = set("\"'<>[]{|}")


def expected_url(url: str) -> str:
    """Percent-encode whitespace and CS1-reserved characters (design D11)."""
    return "".join(
        quote(ch, safe="") if ch.isspace() or ch in _URL_RESERVED else ch
        for ch in url
    )


_ENTITIES = {
    "&#124;": "|",
    "&#91;": "[",
    "&#93;": "]",
    "&#123;": "{",
    "&#125;": "}",
    "&#60;": "<",
}


def unescape_title(value: str) -> str:
    for entity, char in _ENTITIES.items():
        value = value.replace(entity, char)
    return value


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_DANGEROUS = st.sampled_from(["|", "{{", "}}", "=", "[", "]", "<!--", "\n"])

# Mix arbitrary text with dangerous fragments so they appear frequently.
TITLE_STRATEGY = st.lists(
    st.one_of(st.text(max_size=12), _DANGEROUS), max_size=8
).map("".join)

# Titles without "&": design excludes it from the round-trip property because
# a literal "&#124;" in the input is indistinguishable from an escaped "|".
TITLE_NO_AMP_STRATEGY = TITLE_STRATEGY.filter(lambda t: "&" not in t)

_URL_PATH_CHARS = st.one_of(
    st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-_/.%=&?", max_size=10),
    st.sampled_from([" ", "\t", '"', "'", "<", ">", "[", "]", "{", "}", "|", "é", "中"]),
)
URL_STRATEGY = st.builds(
    lambda scheme, host, path: f"{scheme}://{host}/{path}",
    scheme=st.sampled_from(["http", "https"]),
    host=st.from_regex(r"[a-z]{1,8}\.[a-z]{2,4}", fullmatch=True),
    path=st.lists(_URL_PATH_CHARS, max_size=6).map("".join),
)

# min_value 1000: strftime("%Y") zero-padding for years < 1000 is
# platform-dependent and irrelevant to real archive dates.
DATETIME_STRATEGY = st.datetimes(min_value=datetime(1000, 1, 1))


def _complete(title, original_url, archived_url, archived_at) -> str:
    return complete_wikitext(
        title=title,
        original_url=original_url,
        archived_url=archived_url,
        archived_at=archived_at,
    )


# ---------------------------------------------------------------------------
# Property 1-5: complete state
# ---------------------------------------------------------------------------


# Feature: wikitext-citation-format, Property 1: 範本封裝與單行
# **Validates: Requirements 2.1**
@settings(max_examples=100)
@given(TITLE_STRATEGY, URL_STRATEGY, URL_STRATEGY, DATETIME_STRATEGY)
def test_property1_template_wrapper_and_single_line(title, url, archive, at):
    formatted = _complete(title, url, archive, at)
    assert formatted.startswith("{{cite web ")
    assert formatted.endswith("}}")
    assert len(formatted.splitlines()) == 1
    assert "\n" not in formatted and "\r" not in formatted


# Feature: wikitext-citation-format, Property 2: 參數集合與順序不被破壞
# **Validates: Requirements 2.2, 2.6, 3.3, 3.5, 4.1**
@settings(max_examples=100)
@given(TITLE_STRATEGY, URL_STRATEGY, URL_STRATEGY, DATETIME_STRATEGY)
def test_property2_parameter_set_and_order_intact(title, url, archive, at):
    formatted = _complete(title, url, archive, at)
    names = [name for name, _ in parse_cite_web(formatted)]
    assert names == EXPECTED_PARAMS
    inner = formatted[len("{{") : -len("}}")]
    assert "{" not in inner and "}" not in inner


# Feature: wikitext-citation-format, Property 3: 欄位值正確
# **Validates: Requirements 2.3, 2.4, 2.5**
@settings(max_examples=100)
@given(TITLE_STRATEGY, URL_STRATEGY, URL_STRATEGY, DATETIME_STRATEGY)
def test_property3_field_values_correct(title, url, archive, at):
    params = dict(parse_cite_web(_complete(title, url, archive, at)))
    assert params["url"] == expected_url(url).strip()
    assert params["archive-url"] == expected_url(archive).strip()
    assert params["archive-date"] == f"{at.year:04d}-{at.month:02d}-{at.day:02d}"
    assert params["url-status"] == "live"


# Feature: wikitext-citation-format, Property 4: title 跳脫往返
# **Validates: Requirements 3.1, 3.3, 3.4**
@settings(max_examples=100)
@given(TITLE_NO_AMP_STRATEGY, URL_STRATEGY, URL_STRATEGY, DATETIME_STRATEGY)
def test_property4_title_escape_round_trip(title, url, archive, at):
    params = dict(parse_cite_web(_complete(title, url, archive, at)))
    expected = " ".join(title.splitlines()).strip()
    assert unescape_title(params["title"]) == expected


# Feature: wikitext-citation-format, Property 5: title 缺失不捏造
# **Validates: Requirements 3.2**
@settings(max_examples=100)
@given(URL_STRATEGY, URL_STRATEGY, DATETIME_STRATEGY)
def test_property5_missing_title_is_not_fabricated(url, archive, at):
    formatted = _complete(None, url, archive, at)
    pairs = parse_cite_web(formatted)
    assert [name for name, _ in pairs] == EXPECTED_PARAMS
    assert dict(pairs)["title"] == ""
    assert formatted.startswith("{{cite web |title= |url=")


# ---------------------------------------------------------------------------
# Property 6: pending state
# ---------------------------------------------------------------------------

# task_id fragments that stress the HTML-comment wrapper: "-->", "|", spaces.
_TASK_ID_FRAGMENTS = st.sampled_from(["-->", "--", ">", "|", " ", "<!--", "=", "\t"])
TASK_ID_STRATEGY = st.one_of(
    st.uuids().map(str),
    st.lists(
        st.one_of(
            st.text(alphabet="abcdef0123456789-_", max_size=8), _TASK_ID_FRAGMENTS
        ),
        min_size=1,
        max_size=8,
    ).map("".join),
)


# Feature: wikitext-citation-format, Property 6: pending 不含封存欄位
# **Validates: Requirements 5.1, 5.2**
@settings(max_examples=100)
@given(URL_STRATEGY, TASK_ID_STRATEGY)
def test_property6_pending_has_no_archive_fields(url, task_id):
    formatted = pending_wikitext(original_url=url, task_id=task_id)
    assert formatted.startswith("{{cite web ")
    assert formatted.endswith("}}")
    assert [name for name, _ in parse_cite_web(formatted)] == ["url"]
    assert "task_id" in formatted
    assert "Archiving in progress" in formatted
    for forbidden in ("archive-url", "archive-date", "url-status", "title="):
        assert forbidden not in formatted


# Validates: Requirements 5.3
def test_pending_task_id_with_comment_terminator_is_rewritten():
    formatted = pending_wikitext(
        original_url="https://example.com/a", task_id="abc-->def"
    )
    assert "abc-- >def" in formatted
    # Only the wrapper's own terminator may remain.
    assert formatted.count("-->") == 1
    assert formatted.endswith(" -->}}")
    assert [name for name, _ in parse_cite_web(formatted)] == ["url"]
