# Feature: archive-and-cite
"""Property-based test for the archive_and_cite MCP tool layer.

Verifies one tool-level correctness property from design.md:

- Property 10: for any pending CitationResponse returned by the backend
  (archived_url is null), the tool result includes guidance instructing the
  caller to invoke get_archive_status with the returned task_id.

The backend HTTP call (POST /api/cite) is stubbed with an httpx.MockTransport
injected into the AsyncClient the handler creates, so no real service is
required. Each hypothesis example generates a distinct pending CitationResponse
(arbitrary task_id / title / original_url / formatted, with archived_url and
archived_at both null) and asserts the tool surfaces guidance text mentioning
get_archive_status AND the exact task_id.

Why MockTransport rather than respx: respx patches httpx globally and, when its
router is torn down and rebuilt on every hypothesis example (hundreds of times),
it deadlocks on this platform. Injecting a per-example MockTransport keeps the
stub local to the one client the handler opens and is loop-safe under the
fresh event loop we spin per example via asyncio.run.
"""

import asyncio
import functools
import json

import httpx
from hypothesis import given, settings
from hypothesis import strategies as st

import keeplink_mcp.mcp_server.server as server_mod
from keeplink_mcp.mcp_server.server import _handle_archive_and_cite

BASE_URL = "http://127.0.0.1:9210"

# The URL passed to the tool must pass validate_url (dotted http/https host),
# since the handler validates before forwarding. Property 10 concerns how the
# tool handles a *pending backend response*, not URL variety, so a fixed valid
# URL is sufficient here — the varied input space is the backend payload.
_TOOL_URL = "https://example.com/source"

# task_id: non-empty tokens (uuid-like hex, arbitrary identifiers).
_TASK_ID_STRATEGY = st.text(
    alphabet=st.characters(min_codepoint=0x21, max_codepoint=0x7E),
    min_size=1,
    max_size=40,
)

# original_url: valid-looking http/https URLs echoed back by the backend.
_ORIGINAL_URL_STRATEGY = st.builds(
    lambda scheme, host, path: f"{scheme}://{host}{path}",
    scheme=st.sampled_from(["http", "https"]),
    host=st.from_regex(r"[a-z][a-z0-9]{0,10}\.[a-z]{2,6}", fullmatch=True),
    path=st.from_regex(r"(/[a-z0-9\-]{1,10}){0,3}", fullmatch=True),
)

# title: None or arbitrary BMP text incl. unicode + markdown special chars.
_TITLE_STRATEGY = st.one_of(
    st.none(),
    st.text(
        alphabet=st.characters(min_codepoint=0x20, max_codepoint=0x2FFF),
        max_size=40,
    ),
)

# formatted: any non-empty pending-style string the builder might emit.
_FORMATTED_STRATEGY = st.text(
    alphabet=st.characters(min_codepoint=0x20, max_codepoint=0x2FFF),
    min_size=1,
    max_size=120,
)


def _client_factory_returning(response_json: dict):
    """Return a drop-in AsyncClient factory whose requests are served by a
    MockTransport yielding a fixed 200 JSON response.

    Wraps the real httpx.AsyncClient so timeout/base_url kwargs from the handler
    are preserved; only the transport is overridden.
    """
    real_client_cls = server_mod.httpx.AsyncClient

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response_json)

    @functools.wraps(real_client_cls)
    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client_cls(*args, **kwargs)

    return factory


# Feature: archive-and-cite, Property 10: 進行中引導文字 — for any pending
# CitationResponse returned by the backend (archived_url is null, arbitrary
# task_id / title / original_url / formatted), the archive_and_cite tool result
# includes guidance instructing the caller to invoke get_archive_status with the
# returned task_id.
@settings(max_examples=100, deadline=None)
@given(
    task_id=_TASK_ID_STRATEGY,
    title=_TITLE_STRATEGY,
    original_url=_ORIGINAL_URL_STRATEGY,
    formatted=_FORMATTED_STRATEGY,
)
def test_property_pending_guidance_at_tool_layer(
    task_id: str, title, original_url: str, formatted: str
):
    """**Validates: Requirements 4.4**

    Property 10: For any pending CitationResponse (archived_url is null), the
    archive_and_cite tool result must include guidance instructing the caller
    to invoke get_archive_status with the returned task_id.

    The handler is async; we drive it with a fresh event loop per hypothesis
    example (asyncio.run) rather than relying on pytest-asyncio, which does not
    re-enter its loop for each generated example.
    """
    pending_response = {
        "title": title,
        "original_url": original_url,
        "archived_url": None,  # pending → null
        "archived_at": None,  # pending → null
        "task_id": task_id,
        "formatted": formatted,
    }

    original_client_cls = server_mod.httpx.AsyncClient
    server_mod.httpx.AsyncClient = _client_factory_returning(pending_response)
    try:
        result = asyncio.run(_handle_archive_and_cite({"url": _TOOL_URL}, BASE_URL))
    finally:
        server_mod.httpx.AsyncClient = original_client_cls

    # The tool returns the citation JSON plus a guidance TextContent for pending.
    assert len(result) >= 2

    # First item is the CitationResponse JSON echoing the pending payload.
    citation = json.loads(result[0].text)
    assert citation["task_id"] == task_id
    assert citation["archived_url"] is None

    # Guidance appears in the tool output and mentions get_archive_status AND the
    # exact task_id so the caller knows how to complete the citation.
    combined_text = " ".join(item.text for item in result)
    assert "get_archive_status" in combined_text
    assert task_id in combined_text
