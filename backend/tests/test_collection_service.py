import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from engine.errors import UnknownMarketplaceError
from engine.marketplaces import MarketplaceRegistry
from engine.models import Item, MarketQuote, QuoteType
from engine.services import CollectionPolicy, CollectionService


class TemporaryProviderError(Exception):
    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class FakeMarketplace:
    def __init__(
        self,
        name: str,
        fetch: Callable[[Item], Awaitable[list[MarketQuote]]],
    ) -> None:
        self.name = name
        self._fetch = fetch
        self.items: list[Item] = []

    async def fetch(self, item: Item) -> list[MarketQuote]:
        self.items.append(item)
        return await self._fetch(item)


def registry_with(*providers: FakeMarketplace) -> MarketplaceRegistry:
    registry = MarketplaceRegistry()
    for provider in providers:
        registry.register(provider)
    return registry


class ControlledTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
        await asyncio.sleep(0)


ITEM = Item(market_hash_name="AK-47 | Redline (Field-Tested)")
QUOTE = MarketQuote(
    marketplace="example",
    market_hash_name=ITEM.market_hash_name,
    price=Decimal("12.34"),
    currency="GBP",
    observed_at=datetime(2026, 1, 1, tzinfo=UTC),
    quote_type=QuoteType.LOWEST_ASK,
)


async def return_quote(_: Item) -> list[MarketQuote]:
    return [QUOTE]


async def wait_until(
    predicate: Callable[[], bool], timeout: float = 1.0
) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_collect_uses_registry_and_returns_normalized_quotes_unchanged() -> None:
    observations = [QUOTE]

    async def fetch(_: Item) -> list[MarketQuote]:
        return observations

    provider = FakeMarketplace("example", fetch)
    registry = registry_with(provider)

    result = await CollectionService(registry).collect("example", ITEM)

    assert registry.get("example") is provider
    assert provider.items == [ITEM]
    assert result is observations


@pytest.mark.asyncio
async def test_unknown_marketplace_error_propagates() -> None:
    service = CollectionService(MarketplaceRegistry())

    with pytest.raises(UnknownMarketplaceError, match="not registered"):
        await service.collect("missing", ITEM)


@pytest.mark.asyncio
async def test_successful_empty_response_is_returned() -> None:
    async def fetch(_: Item) -> list[MarketQuote]:
        return []

    service = CollectionService(
        registry_with(FakeMarketplace("example", fetch))
    )

    assert await service.collect("example", ITEM) == []


@pytest.mark.asyncio
async def test_temporary_failure_is_retried_with_bounded_backoff() -> None:
    attempts = 0
    controlled_time = ControlledTime()

    async def fetch(_: Item) -> list[MarketQuote]:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise TemporaryProviderError("temporarily unavailable")
        return [QUOTE]

    policy = CollectionPolicy(
        max_attempts=3,
        initial_backoff_seconds=1,
        backoff_multiplier=2,
        max_backoff_seconds=10,
        retryable_exceptions=(TemporaryProviderError,),
    )
    service = CollectionService(
        registry_with(FakeMarketplace("example", fetch)),
        {"example": policy},
        _clock=controlled_time.clock,
        _sleep=controlled_time.sleep,
    )

    assert await service.collect("example", ITEM) == [QUOTE]
    assert attempts == 3
    assert controlled_time.sleeps == [1, 2]


@pytest.mark.asyncio
async def test_non_retryable_failure_propagates_immediately() -> None:
    attempts = 0

    async def fetch(_: Item) -> list[MarketQuote]:
        nonlocal attempts
        attempts += 1
        raise PermissionError("invalid credentials")

    policy = CollectionPolicy(
        max_attempts=3,
        retryable_exceptions=(TemporaryProviderError,),
    )
    service = CollectionService(
        registry_with(FakeMarketplace("example", fetch)),
        {"example": policy},
    )

    with pytest.raises(PermissionError, match="invalid credentials"):
        await service.collect("example", ITEM)
    assert attempts == 1


@pytest.mark.asyncio
async def test_retry_exhaustion_reraises_last_provider_failure() -> None:
    failures = [
        TemporaryProviderError("failure one"),
        TemporaryProviderError("failure two"),
        TemporaryProviderError("failure three"),
    ]
    controlled_time = ControlledTime()

    async def fetch(_: Item) -> list[MarketQuote]:
        raise failures.pop(0)

    policy = CollectionPolicy(
        max_attempts=3,
        initial_backoff_seconds=0,
        max_backoff_seconds=0,
        retryable_exceptions=(TemporaryProviderError,),
    )
    service = CollectionService(
        registry_with(FakeMarketplace("example", fetch)),
        {"example": policy},
        _clock=controlled_time.clock,
        _sleep=controlled_time.sleep,
    )

    with pytest.raises(TemporaryProviderError, match="failure three"):
        await service.collect("example", ITEM)
    assert failures == []


@pytest.mark.asyncio
async def test_timeout_is_retryable_without_an_outer_service_timeout() -> None:
    attempts = 0
    controlled_time = ControlledTime()

    async def fetch(_: Item) -> list[MarketQuote]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TimeoutError("provider request timed out")
        return [QUOTE]

    service = CollectionService(
        registry_with(FakeMarketplace("example", fetch)),
        default_policy=CollectionPolicy(
            initial_backoff_seconds=0,
            max_backoff_seconds=0,
        ),
        _clock=controlled_time.clock,
        _sleep=controlled_time.sleep,
    )

    assert await service.collect("example", ITEM) == [QUOTE]
    assert attempts == 2


@pytest.mark.asyncio
async def test_cancellation_propagates_and_releases_provider_permit() -> None:
    first_started = asyncio.Event()
    block_first = asyncio.Event()
    calls = 0

    async def fetch(_: Item) -> list[MarketQuote]:
        nonlocal calls
        calls += 1
        if calls == 1:
            first_started.set()
            await block_first.wait()
        return [QUOTE]

    service = CollectionService(
        registry_with(FakeMarketplace("example", fetch)),
        {"example": CollectionPolicy(max_concurrency=1)},
    )
    task = asyncio.create_task(service.collect("example", ITEM))
    await asyncio.wait_for(first_started.wait(), 1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert await asyncio.wait_for(service.collect("example", ITEM), 1) == [QUOTE]
    assert calls == 2


@pytest.mark.asyncio
async def test_same_provider_concurrency_is_limited() -> None:
    release = asyncio.Event()
    active = 0
    peak = 0
    started = 0

    async def fetch(_: Item) -> list[MarketQuote]:
        nonlocal active, peak, started
        active += 1
        started += 1
        peak = max(peak, active)
        try:
            await release.wait()
            return [QUOTE]
        finally:
            active -= 1

    service = CollectionService(
        registry_with(FakeMarketplace("example", fetch)),
        {"example": CollectionPolicy(max_concurrency=2)},
    )
    tasks = [asyncio.create_task(service.collect("example", ITEM)) for _ in range(4)]
    await wait_until(lambda: started == 2)
    assert peak == 2

    release.set()
    assert await asyncio.gather(*tasks) == [[QUOTE]] * 4
    assert peak == 2


@pytest.mark.asyncio
async def test_different_providers_do_not_block_each_other() -> None:
    first_started = asyncio.Event()
    second_started = asyncio.Event()
    release = asyncio.Event()

    async def first(_: Item) -> list[MarketQuote]:
        first_started.set()
        await release.wait()
        return [QUOTE]

    async def second(_: Item) -> list[MarketQuote]:
        second_started.set()
        await release.wait()
        return [QUOTE]

    registry = registry_with(
        FakeMarketplace("first", first), FakeMarketplace("second", second)
    )
    service = CollectionService(
        registry,
        {
            "first": CollectionPolicy(max_concurrency=1),
            "second": CollectionPolicy(max_concurrency=1),
        },
    )
    tasks = [
        asyncio.create_task(service.collect("first", ITEM)),
        asyncio.create_task(service.collect("second", ITEM)),
    ]

    await asyncio.wait_for(first_started.wait(), 1)
    await asyncio.wait_for(second_started.wait(), 1)
    release.set()
    assert await asyncio.gather(*tasks) == [[QUOTE], [QUOTE]]


@pytest.mark.asyncio
async def test_minimum_request_interval_uses_controlled_clock() -> None:
    controlled_time = ControlledTime()
    provider = FakeMarketplace("example", return_quote)
    service = CollectionService(
        registry_with(provider),
        {"example": CollectionPolicy(minimum_interval_seconds=2)},
        _clock=controlled_time.clock,
        _sleep=controlled_time.sleep,
    )

    await service.collect("example", ITEM)
    await service.collect("example", ITEM)

    assert controlled_time.sleeps == [2]


@pytest.mark.asyncio
async def test_retry_after_defers_next_provider_attempt() -> None:
    controlled_time = ControlledTime()
    attempts = 0

    async def fetch(_: Item) -> list[MarketQuote]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TemporaryProviderError("limited", retry_after=3)
        return [QUOTE]

    policy = CollectionPolicy(
        max_attempts=2,
        initial_backoff_seconds=1,
        max_backoff_seconds=1,
        retryable_exceptions=(TemporaryProviderError,),
    )
    service = CollectionService(
        registry_with(FakeMarketplace("example", fetch)),
        {"example": policy},
        _clock=controlled_time.clock,
        _sleep=controlled_time.sleep,
    )

    assert await service.collect("example", ITEM) == [QUOTE]
    assert controlled_time.sleeps == [1, 2]


@pytest.mark.asyncio
async def test_failure_releases_provider_permit() -> None:
    calls = 0

    async def fetch(_: Item) -> list[MarketQuote]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("bad response")
        return [QUOTE]

    service = CollectionService(
        registry_with(FakeMarketplace("example", fetch)),
        {"example": CollectionPolicy(max_concurrency=1)},
    )

    with pytest.raises(ValueError, match="bad response"):
        await service.collect("example", ITEM)
    assert await asyncio.wait_for(service.collect("example", ITEM), 1) == [QUOTE]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_concurrency": 0}, "max_concurrency"),
        ({"max_attempts": 0}, "max_attempts"),
        ({"minimum_interval_seconds": -1}, "minimum_interval_seconds"),
        ({"initial_backoff_seconds": -1}, "initial_backoff_seconds"),
        ({"backoff_multiplier": 0.5}, "backoff_multiplier"),
        (
            {"initial_backoff_seconds": 2, "max_backoff_seconds": 1},
            "max_backoff_seconds",
        ),
    ],
)
def test_policy_rejects_invalid_values(
    kwargs: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        CollectionPolicy(**kwargs)


def test_collection_module_has_no_job_or_storage_dependencies() -> None:
    import ast
    import inspect

    import engine.services.collection as collection_module

    imports = {
        node.module
        for node in ast.walk(ast.parse(inspect.getsource(collection_module)))
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert not any(module.startswith("engine.jobs") for module in imports)
    assert not any(module.startswith("engine.storage") for module in imports)
