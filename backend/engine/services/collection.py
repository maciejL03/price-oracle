from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

from engine.marketplaces import MarketplaceRegistry
from engine.models import Item, MarketQuote

Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]


@dataclass(frozen=True, slots=True)
class CollectionPolicy:
    """Request controls for one marketplace.

    ``None`` concurrency and a zero minimum interval deliberately mean that no
    provider quota has been assumed. Deployments should configure only limits
    documented by their marketplace agreements.
    """

    max_concurrency: int | None = None
    minimum_interval_seconds: float = 0.0
    max_attempts: int = 3
    initial_backoff_seconds: float = 0.25
    backoff_multiplier: float = 2.0
    max_backoff_seconds: float = 5.0
    retryable_exceptions: tuple[type[Exception], ...] = (ConnectionError, TimeoutError)

    def __post_init__(self) -> None:
        if self.max_concurrency is not None and (
            isinstance(self.max_concurrency, bool) or self.max_concurrency < 1
        ):
            raise ValueError("max_concurrency must be positive or None")
        if isinstance(self.max_attempts, bool) or self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")

        non_negative_values = {
            "minimum_interval_seconds": self.minimum_interval_seconds,
            "initial_backoff_seconds": self.initial_backoff_seconds,
            "max_backoff_seconds": self.max_backoff_seconds,
        }
        for name, value in non_negative_values.items():
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")

        if not math.isfinite(self.backoff_multiplier) or self.backoff_multiplier < 1:
            raise ValueError("backoff_multiplier must be finite and at least 1")
        if self.max_backoff_seconds < self.initial_backoff_seconds:
            raise ValueError(
                "max_backoff_seconds must be greater than or equal to initial_backoff_seconds"
            )
        if not all(
            isinstance(error_type, type) and issubclass(error_type, Exception)
            for error_type in self.retryable_exceptions
        ):
            raise TypeError("retryable_exceptions must contain Exception subclasses")


class _ProviderControl:
    def __init__(self, policy: CollectionPolicy) -> None:
        self.policy = policy
        self.semaphore = (
            asyncio.Semaphore(policy.max_concurrency)
            if policy.max_concurrency is not None
            else None
        )
        self.admission_lock = asyncio.Lock()
        self.not_before = 0.0

    @asynccontextmanager
    async def request_slot(self) -> AsyncIterator[None]:
        if self.semaphore is None:
            yield
            return

        async with self.semaphore:
            yield

    async def wait_for_admission(self, clock: Clock, sleep: Sleep) -> None:
        async with self.admission_lock:
            delay = self.not_before - clock()
            if delay > 0:
                await sleep(delay)

            self.not_before = clock() + self.policy.minimum_interval_seconds

    async def defer(self, seconds: float, clock: Clock) -> None:
        async with self.admission_lock:
            self.not_before = max(self.not_before, clock() + seconds)


class CollectionService:
    """Coordinate normalized quote collection through registered marketplaces.

    Construct one service for the engine and share it between collection jobs;
    its per-provider controls then apply across all concurrent calls.
    """

    def __init__(
        self,
        registry: MarketplaceRegistry,
        policies: Mapping[str, CollectionPolicy] | None = None,
        *,
        default_policy: CollectionPolicy | None = None,
        _clock: Clock = time.monotonic,
        _sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._registry = registry
        self._policies = dict(policies or {})
        self._default_policy = default_policy or CollectionPolicy()
        self._clock = _clock
        self._sleep = _sleep
        self._controls: dict[str, _ProviderControl] = {
            name: _ProviderControl(policy) for name, policy in self._policies.items()
        }

    async def collect(self, marketplace: str, item: Item) -> list[MarketQuote]:
        """Fetch normalized observations from a selected registered marketplace."""
        provider = self._registry.get(marketplace)
        control = self._control_for(marketplace)

        for attempt in range(1, control.policy.max_attempts + 1):
            try:
                async with control.request_slot():
                    await control.wait_for_admission(self._clock, self._sleep)
                    return await provider.fetch(item)
            except control.policy.retryable_exceptions as error:
                retry_after = _retry_after_seconds(error)
                if retry_after is not None:
                    await control.defer(retry_after, self._clock)

                if attempt == control.policy.max_attempts:
                    raise

                await self._sleep(_backoff_seconds(control.policy, attempt))

        raise AssertionError("collection retry loop terminated unexpectedly")

    def _control_for(self, marketplace: str) -> _ProviderControl:
        control = self._controls.get(marketplace)
        if control is None:
            policy = self._policies.get(marketplace, self._default_policy)
            control = _ProviderControl(policy)
            self._controls[marketplace] = control
        return control


def _backoff_seconds(policy: CollectionPolicy, failed_attempt: int) -> float:
    delay = policy.initial_backoff_seconds * policy.backoff_multiplier ** (
        failed_attempt - 1
    )
    return min(delay, policy.max_backoff_seconds)


def _retry_after_seconds(error: Exception) -> float | None:
    """Read optional provider-neutral retry metadata without importing an adapter."""
    value = getattr(error, "retry_after_seconds", None)
    if value is None:
        value = getattr(error, "retry_after", None)
    if value is None:
        return None

    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return seconds
