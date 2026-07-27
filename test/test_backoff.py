# Feature: keeplink-mcp
"""Property-based and unit tests for BackgroundWorker exponential backoff calculation.

Validates: Requirements 7.2
Property 4: 指數退避計算正確性
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

from hypothesis import given, settings
from hypothesis import strategies as st

from keeplink_mcp.config import Config
from keeplink_mcp.worker.archiver import BackgroundWorker


def _make_worker(base_backoff_sec: float = 60.0) -> BackgroundWorker:
    """Create a BackgroundWorker with mock dependencies for isolated backoff testing."""
    config = Config(base_backoff_sec=base_backoff_sec)
    session_factory = MagicMock()
    logger = logging.getLogger("test_backoff")
    return BackgroundWorker(config=config, session_factory=session_factory, logger=logger)


# ---------------------------------------------------------------------------
# Property 4: 指數退避計算正確性
# For any retry_count in [0, max_retries), backoff = base_backoff_sec * 2^retry_count
# and the sequence is strictly monotonically increasing.
# ---------------------------------------------------------------------------


class TestBackoffProperty:
    """Property-based tests for exponential backoff correctness."""

    @settings(max_examples=100)
    @given(
        retry_count=st.integers(min_value=0, max_value=4),
        base_backoff_sec=st.floats(min_value=1.0, max_value=3600.0, allow_nan=False),
    )
    def test_backoff_equals_base_times_two_power_retry(
        self, retry_count: int, base_backoff_sec: float
    ) -> None:
        """Backoff value must equal base_backoff_sec * 2^retry_count.

        **Validates: Requirements 7.2**
        """
        worker = _make_worker(base_backoff_sec=base_backoff_sec)
        result = worker._calculate_backoff(retry_count)
        expected = base_backoff_sec * (2**retry_count)
        assert result == expected

    @settings(max_examples=100)
    @given(
        base_backoff_sec=st.floats(min_value=1.0, max_value=3600.0, allow_nan=False),
    )
    def test_backoff_sequence_is_strictly_monotonic(self, base_backoff_sec: float) -> None:
        """The backoff sequence across retries 0..4 must be strictly increasing.

        **Validates: Requirements 7.2**
        """
        worker = _make_worker(base_backoff_sec=base_backoff_sec)
        sequence = [worker._calculate_backoff(i) for i in range(5)]

        for i in range(1, len(sequence)):
            assert sequence[i] > sequence[i - 1], (
                f"Backoff not strictly increasing at index {i}: "
                f"{sequence[i - 1]} >= {sequence[i]}"
            )


# ---------------------------------------------------------------------------
# Unit test: verify concrete values under default configuration
# ---------------------------------------------------------------------------


class TestBackoffUnitValues:
    """Unit tests verifying specific backoff values with default config (base=60s)."""

    def test_default_config_backoff_values(self) -> None:
        """With base_backoff_sec=60, retries 0-4 produce 60, 120, 240, 480, 960.

        **Validates: Requirements 7.2**
        """
        worker = _make_worker(base_backoff_sec=60.0)
        expected_values = [60, 120, 240, 480, 960]

        for retry_count, expected in enumerate(expected_values):
            result = worker._calculate_backoff(retry_count)
            assert result == expected, (
                f"retry_count={retry_count}: expected {expected}, got {result}"
            )

    def test_backoff_at_retry_zero_equals_base(self) -> None:
        """First retry (count=0) should equal exactly base_backoff_sec."""
        worker = _make_worker(base_backoff_sec=60.0)
        assert worker._calculate_backoff(0) == 60.0

    def test_backoff_doubles_each_retry(self) -> None:
        """Each subsequent retry should produce exactly double the previous backoff."""
        worker = _make_worker(base_backoff_sec=60.0)
        for i in range(4):
            current = worker._calculate_backoff(i)
            next_val = worker._calculate_backoff(i + 1)
            assert next_val == current * 2
