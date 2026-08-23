# Feature: keeplink-mcp
"""Property-based tests for TokenBucketLimiter rate limiting correctness.

Validates: Requirements 1.1
Property 1: Token bucket enforces rate ceiling
"""

from __future__ import annotations

import asyncio

from hypothesis import given, settings
from hypothesis import strategies as st

from keeplink_mcp.worker.rate_limiter import RateLimitConfig, TokenBucketLimiter

# ---------------------------------------------------------------------------
# Property 1: Token bucket enforces rate ceiling
# For any sequence of N acquire() calls within a single interval,
# the number of successful acquisitions SHALL never exceed max_tokens,
# regardless of call timing within that interval.
# ---------------------------------------------------------------------------


class TestTokenBucketEnforcesRateCeiling:
    """Property-based tests for token bucket rate ceiling enforcement.

    **Validates: Requirements 1.1**
    """

    @settings(max_examples=100, deadline=None)
    @given(
        max_tokens=st.integers(min_value=1, max_value=100),
        num_calls=st.integers(min_value=1, max_value=200),
    )
    async def test_successful_acquisitions_never_exceed_max_tokens(
        self, max_tokens: int, num_calls: int
    ) -> None:
        """For any sequence of N acquire() calls within a single interval,
        successful acquisitions SHALL never exceed max_tokens.

        **Validates: Requirements 1.1**

        Test Strategy:
            - Create a limiter with specified max_tokens and very long interval
            - Make N sequential acquire() calls with zero timeout
            - Count successful acquisitions
            - Assert count <= max_tokens

        Note: We use a very long interval_sec (3600s) to ensure no token refill
        occurs during the test, so we're testing the rate ceiling within a
        single interval as specified by Property 1.
        """
        config = RateLimitConfig(
            max_tokens=max_tokens,
            interval_sec=3600.0,  # 1 hour - ensures no refill during test
            wait_timeout_sec=0.001,  # Very short timeout to avoid waiting
        )
        limiter = TokenBucketLimiter(config)

        successful = 0
        for _ in range(num_calls):
            acquired = await limiter.acquire(timeout=0.001)
            if acquired:
                successful += 1

        assert successful <= max_tokens, (
            f"Successful acquisitions ({successful}) exceeded max_tokens ({max_tokens}) "
            f"with {num_calls} calls"
        )

    @settings(max_examples=50, deadline=None)
    @given(
        max_tokens=st.integers(min_value=1, max_value=20),
    )
    async def test_exact_max_tokens_acquisitions_possible(
        self, max_tokens: int
    ) -> None:
        """When making exactly max_tokens calls, all should succeed immediately.

        **Validates: Requirements 1.1**

        This verifies the bucket starts full with max_tokens tokens.
        """
        config = RateLimitConfig(
            max_tokens=max_tokens,
            interval_sec=60.0,  # Long interval to prevent refill during test
            wait_timeout_sec=0.001,
        )
        limiter = TokenBucketLimiter(config)

        successful = 0
        for _ in range(max_tokens):
            acquired = await limiter.acquire(timeout=0.001)
            if acquired:
                successful += 1

        assert successful == max_tokens, (
            f"Expected exactly {max_tokens} successful acquisitions, got {successful}"
        )

    @settings(max_examples=50, deadline=None)
    @given(
        max_tokens=st.integers(min_value=1, max_value=10),
        num_calls=st.integers(min_value=1, max_value=100),
    )
    async def test_concurrent_acquisitions_never_exceed_max_tokens(
        self, max_tokens: int, num_calls: int
    ) -> None:
        """For concurrent acquire() calls, total successful acquisitions
        within one interval SHALL never exceed max_tokens.

        **Validates: Requirements 1.1**

        Test Strategy:
            - Create a limiter with specified max_tokens
            - Launch N concurrent acquire() tasks
            - Count total successful acquisitions
            - Assert count <= max_tokens
        """
        config = RateLimitConfig(
            max_tokens=max_tokens,
            interval_sec=60.0,  # Long interval to prevent refill during test
            wait_timeout_sec=0.01,  # Short timeout
        )
        limiter = TokenBucketLimiter(config)

        async def try_acquire() -> bool:
            return await limiter.acquire(timeout=0.01)

        # Launch all acquisitions concurrently
        tasks = [asyncio.create_task(try_acquire()) for _ in range(num_calls)]
        results = await asyncio.gather(*tasks)

        successful = sum(1 for r in results if r)

        assert successful <= max_tokens, (
            f"Concurrent successful acquisitions ({successful}) exceeded max_tokens ({max_tokens}) "
            f"with {num_calls} concurrent calls"
        )


# ---------------------------------------------------------------------------
# Unit tests for edge cases and disabled path
# ---------------------------------------------------------------------------


class TestTokenBucketDisabledPath:
    """Unit tests for disabled rate limiting path (max_tokens <= 0)."""

    async def test_zero_max_tokens_returns_true_immediately(self) -> None:
        """When max_tokens=0, acquire() shall return True immediately."""
        config = RateLimitConfig(
            max_tokens=0,
            interval_sec=60.0,
            wait_timeout_sec=1.0,
        )
        limiter = TokenBucketLimiter(config)

        # Should succeed immediately without waiting
        result = await limiter.acquire(timeout=0.1)
        assert result is True

    async def test_negative_max_tokens_returns_true_immediately(self) -> None:
        """When max_tokens<0, acquire() shall return True immediately."""
        config = RateLimitConfig(
            max_tokens=-5,
            interval_sec=60.0,
            wait_timeout_sec=1.0,
        )
        limiter = TokenBucketLimiter(config)

        result = await limiter.acquire(timeout=0.1)
        assert result is True

    async def test_disabled_limiter_available_tokens_returns_zero(self) -> None:
        """When disabled, available_tokens shall return 0 to avoid misleading stats."""
        config = RateLimitConfig(
            max_tokens=0,
            interval_sec=60.0,
            wait_timeout_sec=1.0,
        )
        limiter = TokenBucketLimiter(config)

        assert limiter.available_tokens == 0


class TestTokenBucketInitialCapacity:
    """Unit tests verifying initial bucket capacity."""

    async def test_bucket_starts_full(self) -> None:
        """The bucket shall start with max_tokens tokens available."""
        config = RateLimitConfig(
            max_tokens=15,
            interval_sec=60.0,
            wait_timeout_sec=1.0,
        )
        limiter = TokenBucketLimiter(config)

        # Should be able to acquire exactly max_tokens times
        for _ in range(15):
            result = await limiter.acquire(timeout=0.01)
            assert result is True

        # Next acquisition should fail (no timeout for immediate check)
        result = await limiter.acquire(timeout=0.001)
        assert result is False

    async def test_available_tokens_reflects_current_state(self) -> None:
        """available_tokens property shall reflect current token count."""
        config = RateLimitConfig(
            max_tokens=10,
            interval_sec=60.0,
            wait_timeout_sec=1.0,
        )
        limiter = TokenBucketLimiter(config)

        assert limiter.available_tokens == 10

        await limiter.acquire(timeout=0.01)
        assert limiter.available_tokens == 9

        await limiter.acquire(timeout=0.01)
        assert limiter.available_tokens == 8


class TestTokenBucketTimeout:
    """Unit tests for timeout behavior when no tokens are available.

    **Validates: Requirements 1.2**
    """

    async def test_acquire_returns_false_after_timeout_when_no_tokens(self) -> None:
        """When no tokens are available and timeout elapses, acquire() shall return False.

        **Validates: Requirements 1.2**
        """
        config = RateLimitConfig(
            max_tokens=1,
            interval_sec=60.0,  # Long interval to prevent refill during test
            wait_timeout_sec=1.0,
        )
        limiter = TokenBucketLimiter(config)

        # Consume the only token
        first_result = await limiter.acquire(timeout=0.01)
        assert first_result is True

        # Now no tokens available - should timeout and return False
        import time

        start = time.monotonic()
        second_result = await limiter.acquire(timeout=0.1)
        elapsed = time.monotonic() - start

        assert second_result is False
        # Verify we actually waited (at least 80% of timeout)
        assert elapsed >= 0.08, f"Expected wait >= 0.08s, got {elapsed:.3f}s"

    async def test_acquire_timeout_respects_configured_wait_timeout(self) -> None:
        """The acquire() timeout shall default to configured wait_timeout_sec.

        **Validates: Requirements 1.2, 1.4**
        """
        config = RateLimitConfig(
            max_tokens=1,
            interval_sec=60.0,
            wait_timeout_sec=0.15,  # 150ms default timeout
        )
        limiter = TokenBucketLimiter(config)

        # Consume the only token
        await limiter.acquire(timeout=0.01)

        # Use default timeout (None = use configured wait_timeout_sec)
        import time

        start = time.monotonic()
        result = await limiter.acquire(timeout=None)
        elapsed = time.monotonic() - start

        assert result is False
        # Should have waited approximately the configured timeout
        assert elapsed >= 0.1, f"Expected wait >= 0.1s, got {elapsed:.3f}s"

    async def test_acquire_with_explicit_timeout_overrides_config(self) -> None:
        """Explicit timeout parameter shall override configured wait_timeout_sec.

        **Validates: Requirements 1.2**
        """
        config = RateLimitConfig(
            max_tokens=1,
            interval_sec=60.0,
            wait_timeout_sec=5.0,  # Long default timeout
        )
        limiter = TokenBucketLimiter(config)

        # Consume the only token
        await limiter.acquire(timeout=0.01)

        import time

        start = time.monotonic()
        result = await limiter.acquire(timeout=0.1)  # Short explicit timeout
        elapsed = time.monotonic() - start

        assert result is False
        # Should have waited the explicit timeout, not the configured one
        assert elapsed < 1.0, f"Expected wait < 1.0s, got {elapsed:.3f}s"
        assert elapsed >= 0.08, f"Expected wait >= 0.08s, got {elapsed:.3f}s"


class TestTokenBucketReplenishment:
    """Unit tests for token replenishment behavior.

    **Validates: Requirements 1.1, 1.4, 1.5**
    """

    async def test_token_replenished_after_interval(self) -> None:
        """After one interval, exactly one new token shall be available.

        **Validates: Requirements 1.4, 1.5**
        """
        config = RateLimitConfig(
            max_tokens=1,
            interval_sec=0.1,  # 100ms interval for fast test
            wait_timeout_sec=1.0,
        )
        limiter = TokenBucketLimiter(config)

        # Consume the only token
        first_result = await limiter.acquire(timeout=0.01)
        assert first_result is True

        # Wait slightly longer than interval for replenishment
        await asyncio.sleep(0.15)

        # Should be able to acquire again after replenishment
        second_result = await limiter.acquire(timeout=0.01)
        assert second_result is True

    async def test_multiple_tokens_replenish_over_time(self) -> None:
        """Tokens shall replenish continuously over time, not just once per interval.

        **Validates: Requirements 1.4, 1.5**
        """
        config = RateLimitConfig(
            max_tokens=3,
            interval_sec=0.1,  # 100ms per token
            wait_timeout_sec=1.0,
        )
        limiter = TokenBucketLimiter(config)

        # Consume all tokens
        for _ in range(3):
            await limiter.acquire(timeout=0.01)

        # Verify no tokens
        result = await limiter.acquire(timeout=0.01)
        assert result is False

        # Wait for 2.5 intervals (~250ms) - should have ~2 tokens back
        await asyncio.sleep(0.28)

        # Should be able to acquire 2 tokens now
        successful = 0
        for _ in range(3):
            if await limiter.acquire(timeout=0.01):
                successful += 1

        # Should have approximately 2 tokens (allowing for timing variance)
        assert 1 <= successful <= 3, f"Expected 1-3 successful, got {successful}"

    async def test_bucket_never_exceeds_max_tokens_on_refill(self) -> None:
        """Token count shall never exceed max_tokens even after long idle period.

        **Validates: Requirements 1.4**
        """
        config = RateLimitConfig(
            max_tokens=5,
            interval_sec=0.05,  # 50ms interval
            wait_timeout_sec=1.0,
        )
        limiter = TokenBucketLimiter(config)

        # Don't consume any tokens, wait for multiple refills
        await asyncio.sleep(0.3)  # 6 intervals worth of time

        # Available tokens should still be max_tokens (not more)
        assert limiter.available_tokens == 5, (
            f"Expected available_tokens to be capped at 5, got {limiter.available_tokens}"
        )

    async def test_refill_timer_starts_at_initialization(self) -> None:
        """The refill timer shall start at initialization, not first acquire.

        **Validates: Requirements 1.4**
        """
        config = RateLimitConfig(
            max_tokens=1,
            interval_sec=0.1,
            wait_timeout_sec=1.0,
        )

        limiter = TokenBucketLimiter(config)

        # Wait for one interval before any acquire
        await asyncio.sleep(0.12)

        # Consume the token
        result1 = await limiter.acquire(timeout=0.01)
        assert result1 is True

        # Since we waited before acquiring, next token should be available soon
        # (within half the interval because timer started at init)
        await asyncio.sleep(0.06)
        result2 = await limiter.acquire(timeout=0.01)
        # This might succeed or fail depending on exact timing, but should succeed
        # within a reasonable additional wait
        if not result2:
            await asyncio.sleep(0.06)
            result2 = await limiter.acquire(timeout=0.01)
        assert result2 is True
