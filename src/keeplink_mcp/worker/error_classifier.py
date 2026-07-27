"""Error classification for SPN2 API failures.

Categorizes errors as retryable (transient) or non-retryable (permanent)
to guide the Background Worker's retry/fail-fast decisions.
"""

from __future__ import annotations

from enum import Enum

import httpx


class ErrorCategory(str, Enum):
    """Classification of archive errors for retry strategy."""

    RETRYABLE = "retryable"
    NON_RETRYABLE = "non_retryable"


# HTTP status codes that indicate transient server-side issues — safe to retry.
_RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})

# HTTP status codes that indicate permanent auth/permission failures — no point retrying.
_NON_RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({401, 403})

# Exception types representing network-level transient failures.
_RETRYABLE_EXCEPTION_TYPES: tuple[type[Exception], ...] = (
    httpx.TimeoutException,
    httpx.ConnectError,
    ConnectionError,
    TimeoutError,
)


def classify_error(
    status_code: int | None = None,
    exception: Exception | None = None,
) -> ErrorCategory:
    """Classify an archive error as retryable or non-retryable.

    Determines whether the Background Worker should schedule a retry
    (with exponential backoff) or mark the task as permanently failed.

    Args:
        status_code: HTTP status code from the SPN2 API response, if available.
        exception: The exception raised during the SPN2 API call, if available.

    Returns:
        ErrorCategory.RETRYABLE for transient failures (429, 5xx, network errors).
        ErrorCategory.NON_RETRYABLE for permanent failures (401, 403, unknown).

    Raises:
        ValueError: If neither status_code nor exception is provided.
    """
    if status_code is None and exception is None:
        raise ValueError("At least one of status_code or exception must be provided")

    # Status code takes precedence — it's the most explicit signal.
    if status_code is not None:
        if status_code in _RETRYABLE_STATUS_CODES:
            return ErrorCategory.RETRYABLE
        if status_code in _NON_RETRYABLE_STATUS_CODES:
            return ErrorCategory.NON_RETRYABLE
        # Unknown HTTP errors → fail-fast to avoid masking unexpected states.
        return ErrorCategory.NON_RETRYABLE

    # Fall through to exception-based classification.
    if isinstance(exception, _RETRYABLE_EXCEPTION_TYPES):
        return ErrorCategory.RETRYABLE

    # Unknown exception types → non-retryable (fail-fast principle).
    return ErrorCategory.NON_RETRYABLE
