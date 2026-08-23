"""Token bucket rate limiter for SPN2 API call throttling.

Validates: Requirements 1.1, 1.2, 1.4, 1.5
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimitConfig:
    """Configuration for the token bucket rate limiter.

    Attributes:
        max_tokens: Maximum tokens (burst capacity). 0 or negative = disabled.
        interval_sec: Token replenishment interval in seconds.
        wait_timeout_sec: Max wait time before declaring rate-limited.

    Validated by Property 1: Token bucket enforces rate ceiling
    """

    max_tokens: int
    interval_sec: float
    wait_timeout_sec: float = 30.0

    @property
    def is_disabled(self) -> bool:
        """Return True if rate limiting is disabled (max_tokens <= 0)."""
        return self.max_tokens <= 0


class TokenBucketLimiter:
    """Async token bucket that controls outbound SPN2 call rate.

    The bucket starts full with max_tokens tokens. Each acquire() consumes
    one token. Tokens are replenished at a rate of 1 token per interval_sec.

    When the bucket is empty, callers wait until a token becomes available
    or the specified timeout elapses.

    Usage:
        config = RateLimitConfig(max_tokens=15, interval_sec=60.0)
        limiter = TokenBucketLimiter(config)
        acquired = await limiter.acquire(timeout=30.0)
        if not acquired:
            # Handle rate-limited case (timeout exceeded)
            pass

    Concurrency Safety:
        Uses asyncio.Lock to protect token state across concurrent callers.
        Uses asyncio.Event to notify waiting callers when tokens are replenished.

    Validated by Property 1: Token bucket enforces rate ceiling
    """

    def __init__(self, config: RateLimitConfig) -> None:
        """Initialize the token bucket with the given configuration.

        Args:
            config: Rate limit configuration specifying token capacity,
                replenishment interval, and default timeout.
        """
        self._config = config
        self._tokens: float = float(config.max_tokens)
        self._lock = asyncio.Lock()
        self._token_available = asyncio.Event()
        self._token_available.set()  # Initially tokens are available
        self._last_refill_time: float = time.monotonic()

    @property
    def available_tokens(self) -> int:
        """Return the current number of available tokens (for health stats).

        This is a snapshot and may be stale by the time it's used.
        Returns 0 if rate limiting is disabled (to avoid misleading stats).
        """
        if self._config.is_disabled:
            return 0
        return int(self._tokens)

    async def acquire(self, timeout: float | None = None) -> bool:
        """Attempt to acquire a token.

        When rate limiting is disabled (max_tokens <= 0), returns True immediately.

        When tokens are available, consumes one token and returns True immediately.

        When no tokens are available, waits until either:
        - A token is replenished (returns True)
        - The timeout elapses (returns False)

        Args:
            timeout: Maximum time to wait for a token. If None, uses the
                configured wait_timeout_sec.

        Returns:
            True if a token was acquired, False if timeout exceeded.

        Validated by Property 1: Token bucket enforces rate ceiling
        """
        # Disabled path: bypass all rate limit logic (Requirement 1.6)
        if self._config.is_disabled:
            return True

        effective_timeout = timeout if timeout is not None else self._config.wait_timeout_sec

        async with self._lock:
            self._refill_tokens()

            if self._tokens >= 1.0:
                self._tokens -= 1.0
                if self._tokens < 1.0:
                    self._token_available.clear()
                return True

        # No tokens available, wait for replenishment
        try:
            await asyncio.wait_for(self._wait_for_token(), timeout=effective_timeout)
            async with self._lock:
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    if self._tokens < 1.0:
                        self._token_available.clear()
                    return True
            # Token was consumed by another waiter, try again
            return await self.acquire(timeout=0)  # Non-blocking retry
        except asyncio.TimeoutError:
            return False

    async def _wait_for_token(self) -> None:
        """Wait until a token becomes available or the event is set."""
        await self._token_available.wait()

    def _refill_tokens(self) -> None:
        """Refill tokens based on elapsed time since last refill.

        This method must be called while holding self._lock.
        Tokens are replenished at a rate of 1 token per interval_sec,
        up to max_tokens capacity.
        """
        now = time.monotonic()
        elapsed = now - self._last_refill_time

        if elapsed <= 0:
            return

        # Calculate how many tokens to add based on elapsed time
        tokens_to_add = elapsed / self._config.interval_sec
        new_tokens = min(self._config.max_tokens, self._tokens + tokens_to_add)

        if new_tokens >= 1.0 and self._tokens < 1.0:
            # Transition from empty to having tokens - notify waiters
            self._token_available.set()

        self._tokens = new_tokens
        self._last_refill_time = now
