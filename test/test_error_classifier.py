# Feature: omniarchive-mcp
"""Property-based and unit tests for the Error Classifier module.

Validates: Requirements 8.1, 8.2, 8.3, 8.4, 8.5
"""

from __future__ import annotations

import httpx
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from omniarchive_mcp.worker.error_classifier import (
    ErrorCategory,
    classify_error,
)

# ---------------------------------------------------------------------------
# Property 3: 錯誤分類的完整性
# Any status_code in {429, 500, 502, 503, 504} → RETRYABLE
# Any status_code in {401, 403} → NON_RETRYABLE
# Any retryable exception type → RETRYABLE
# ---------------------------------------------------------------------------


RETRYABLE_STATUS_CODES = [429, 500, 502, 503, 504]
NON_RETRYABLE_STATUS_CODES = [401, 403]


class TestProperty3ErrorClassificationCompleteness:
    """Property 3: 錯誤分類的完整性 — Validates: Requirements 8.1, 8.2, 8.3, 8.4, 8.5"""

    @given(status_code=st.sampled_from(RETRYABLE_STATUS_CODES))
    @settings(max_examples=100)
    def test_retryable_status_codes_always_retryable(self, status_code: int) -> None:
        """Any status code in {429, 500, 502, 503, 504} is classified as RETRYABLE."""
        result = classify_error(status_code=status_code)
        assert result == ErrorCategory.RETRYABLE

    @given(status_code=st.sampled_from(NON_RETRYABLE_STATUS_CODES))
    @settings(max_examples=100)
    def test_non_retryable_status_codes_always_non_retryable(self, status_code: int) -> None:
        """Any status code in {401, 403} is classified as NON_RETRYABLE."""
        result = classify_error(status_code=status_code)
        assert result == ErrorCategory.NON_RETRYABLE

    @given(
        exc_type=st.sampled_from([
            httpx.TimeoutException,
            httpx.ConnectError,
            ConnectionError,
            TimeoutError,
        ])
    )
    @settings(max_examples=100)
    def test_retryable_exceptions_always_retryable(self, exc_type: type[Exception]) -> None:
        """Any retryable exception type is classified as RETRYABLE."""
        # Construct a minimal exception instance.
        exc = exc_type("simulated failure")
        result = classify_error(exception=exc)
        assert result == ErrorCategory.RETRYABLE


# ---------------------------------------------------------------------------
# Unit Tests: 具體 status code 範例
# ---------------------------------------------------------------------------


class TestStatusCodeExamples:
    """Unit tests verifying specific status code classifications."""

    @pytest.mark.parametrize(
        ("code", "expected"),
        [
            (429, ErrorCategory.RETRYABLE),
            (500, ErrorCategory.RETRYABLE),
            (502, ErrorCategory.RETRYABLE),
            (503, ErrorCategory.RETRYABLE),
            (504, ErrorCategory.RETRYABLE),
            (401, ErrorCategory.NON_RETRYABLE),
            (403, ErrorCategory.NON_RETRYABLE),
        ],
    )
    def test_known_status_codes(self, code: int, expected: ErrorCategory) -> None:
        assert classify_error(status_code=code) == expected

    @pytest.mark.parametrize("code", [400, 404, 405, 408, 418, 422, 451])
    def test_unknown_status_codes_are_non_retryable(self, code: int) -> None:
        """Unknown status codes default to NON_RETRYABLE (fail-fast principle)."""
        assert classify_error(status_code=code) == ErrorCategory.NON_RETRYABLE


# ---------------------------------------------------------------------------
# Unit Tests: Exception 範例
# ---------------------------------------------------------------------------


class TestExceptionExamples:
    """Unit tests verifying specific exception classifications."""

    def test_httpx_timeout_is_retryable(self) -> None:
        exc = httpx.TimeoutException("read timeout")
        assert classify_error(exception=exc) == ErrorCategory.RETRYABLE

    def test_httpx_connect_error_is_retryable(self) -> None:
        exc = httpx.ConnectError("connection refused")
        assert classify_error(exception=exc) == ErrorCategory.RETRYABLE

    def test_connection_error_is_retryable(self) -> None:
        exc = ConnectionError("network unreachable")
        assert classify_error(exception=exc) == ErrorCategory.RETRYABLE

    def test_timeout_error_is_retryable(self) -> None:
        exc = TimeoutError("socket timed out")
        assert classify_error(exception=exc) == ErrorCategory.RETRYABLE

    def test_unknown_exception_is_non_retryable(self) -> None:
        """Unrecognized exception types default to NON_RETRYABLE."""
        exc = RuntimeError("unexpected")
        assert classify_error(exception=exc) == ErrorCategory.NON_RETRYABLE

    def test_value_error_is_non_retryable(self) -> None:
        exc = ValueError("bad input")
        assert classify_error(exception=exc) == ErrorCategory.NON_RETRYABLE


# ---------------------------------------------------------------------------
# Unit Test: ValueError when both args are None
# ---------------------------------------------------------------------------


class TestInvalidInput:
    """Unit tests for invalid invocations of classify_error."""

    def test_raises_value_error_when_both_none(self) -> None:
        """classify_error SHALL raise ValueError when neither status_code nor exception is given."""
        with pytest.raises(ValueError, match="At least one of"):
            classify_error()

    def test_raises_value_error_explicit_none(self) -> None:
        with pytest.raises(ValueError, match="At least one of"):
            classify_error(status_code=None, exception=None)
