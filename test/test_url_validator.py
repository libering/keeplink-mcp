# Feature: keeplink-mcp
"""URL Validator property tests and unit tests.

Uses hypothesis for property-based testing to verify universal correctness
of URL validation across a wide input space, complemented by unit tests
for specific known-good and known-bad examples.
"""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from keeplink_mcp.mcp_server.url_validator import ValidationError, validate_url

# ---------------------------------------------------------------------------
# Strategies — smart generators that constrain to realistic input spaces
# ---------------------------------------------------------------------------

# Generate valid URLs: http(s) scheme + hostname with at least one dot + optional path
_VALID_SCHEME = st.sampled_from(["http", "https"])
_VALID_HOSTNAME = st.from_regex(
    r"[a-z][a-z0-9]{0,10}\.[a-z]{2,6}", fullmatch=True
)
_OPTIONAL_PATH = st.from_regex(r"(/[a-z0-9\-]{1,10}){0,3}", fullmatch=True)
_OPTIONAL_WHITESPACE = st.from_regex(r"[ \t]{0,3}", fullmatch=True)

VALID_URL_STRATEGY = st.builds(
    lambda scheme, host, path, ws_before, ws_after: (
        f"{ws_before}{scheme}://{host}{path}{ws_after}"
    ),
    scheme=_VALID_SCHEME,
    host=_VALID_HOSTNAME,
    path=_OPTIONAL_PATH,
    ws_before=_OPTIONAL_WHITESPACE,
    ws_after=_OPTIONAL_WHITESPACE,
)

# Generate invalid URLs: either wrong scheme or hostname without dot
_INVALID_SCHEME = st.sampled_from(["ftp", "file", "ssh", "mailto", "data", "ws", "wss", ""])
_NO_DOT_HOSTNAME = st.from_regex(r"[a-z][a-z0-9]{1,10}", fullmatch=True)

INVALID_SCHEME_URL = st.builds(
    lambda scheme, host: f"{scheme}://{host}.com/path" if scheme else f"{host}.com/path",
    scheme=_INVALID_SCHEME,
    host=_NO_DOT_HOSTNAME,
)

INVALID_HOST_URL = st.builds(
    lambda scheme, host: f"{scheme}://{host}",
    scheme=st.sampled_from(["http", "https"]),
    host=_NO_DOT_HOSTNAME,
)

# Combine both invalid strategies
INVALID_URL_STRATEGY = st.one_of(INVALID_SCHEME_URL, INVALID_HOST_URL)


# ---------------------------------------------------------------------------
# Property Tests
# ---------------------------------------------------------------------------


class TestURLValidatorProperties:
    """Property-based tests for URL validation correctness."""

    @settings(max_examples=100)
    @given(url=VALID_URL_STRATEGY)
    def test_property_1_idempotence(self, url: str) -> None:
        """Property 1: URL 驗證 round-trip 一致性.

        Any URL that passes validate_url should produce the same result
        when passed through validate_url again (idempotent).

        **Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**
        """
        result = validate_url(url)
        assert validate_url(result) == result

    @settings(max_examples=100)
    @given(url=INVALID_URL_STRATEGY)
    def test_property_2_invalid_rejection(self, url: str) -> None:
        """Property 2: 無效 URL 一律被拒絕.

        Any string without http/https scheme or with a hostname lacking a dot
        must be rejected with ValidationError.

        **Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**
        """
        with pytest.raises(ValidationError):
            validate_url(url)


# ---------------------------------------------------------------------------
# Unit Tests — specific examples for known edge cases
# ---------------------------------------------------------------------------


class TestURLValidatorUnit:
    """Unit tests for validate_url with specific inputs."""

    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com",
            "http://sub.domain.org/path?q=1",
        ],
    )
    def test_valid_urls_pass(self, url: str) -> None:
        """Valid URLs should pass validation and be returned as-is."""
        result = validate_url(url)
        assert result == url

    @pytest.mark.parametrize(
        "url",
        [
            "ftp://example.com",
            "https://localhost",
            "not-a-url",
            "",
        ],
    )
    def test_invalid_urls_raise(self, url: str) -> None:
        """Invalid URLs should raise ValidationError."""
        with pytest.raises(ValidationError):
            validate_url(url)

    def test_whitespace_stripping(self) -> None:
        """Leading/trailing whitespace should be stripped before validation."""
        result = validate_url("  https://example.com  ")
        assert result == "https://example.com"

    def test_idempotence_concrete(self) -> None:
        """Concrete round-trip: validate_url(validate_url(x)) == validate_url(x)."""
        url = "https://example.com/path?key=value"
        first = validate_url(url)
        second = validate_url(first)
        assert first == second
