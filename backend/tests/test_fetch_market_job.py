import ast
import asyncio
import inspect
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from engine.jobs import (
    EngineJob,
    FetchMarketJob,
    JobHandler,
    JobScheduler,
    SubmissionResult,
)
from engine.jobs.fetch_market import QuoteBatchHandler
from engine.models import Item, MarketQuote, QuoteType
from engine.services import CollectionService
from engine.storage import StorageService

ITEM = Item(market_hash_name="AK-47 | Redline (Field-Tested)")
OTHER_ITEM = Item(market_hash_name="M4A1-S | Decimator (Minimal Wear)")
QUOTE = MarketQuote(
    marketplace="example",
    market_hash_name=ITEM.market_hash_name,
    price=Decimal("12.34"),
    currency="GBP",
    observed_at=datetime(2026, 1, 1, tzinfo=UTC),
    quote_type=QuoteType.LOWEST_ASK,
)

Collect = Callable[[str, Item], Awaitable[list[MarketQuote]]]
Save = Callable[[list[MarketQuote]], Awaitable[None]]


async def return_quote(_: str, __: Item) -> list[MarketQuote]:
    return [QUOTE]


async def save_successfully(_: list[MarketQuote]) -> None:
    return None


class FakeCollectionService(CollectionService):
    def __init__(self, collect: Collect = return_quote) -> None:
        self._collect = collect
        self.calls: list[tuple[str, Item]] = []

    async def collect(self, marketplace: str, item: Item) -> list[MarketQuote]:
        self.calls.append((marketplace, item))
        return await self._collect(marketplace, item)


class FakeStorageService(StorageService):
    def __init__(self, save: Save = save_successfully) -> None:
        self._save = save
        self.calls: list[list[MarketQuote]] = []

    async def save_quotes(self, quotes: Iterable[MarketQuote]) -> None:
        batch = list(quotes)
        self.calls.append(batch)
        await self._save(batch)


def make_job(
    *,
    marketplace: str = "example",
    item: Item = ITEM,
    collection: CollectionService | None = None,
    storage: StorageService | None = None,
    after_persist: QuoteBatchHandler | None = None,
) -> FetchMarketJob:
    return FetchMarketJob(
        marketplace,
        item,
        collection or FakeCollectionService(),
        storage or FakeStorageService(),
        after_persist=after_persist,
    )


async def wait_until(predicate: Callable[[], bool], timeout: float = 1.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0)


def test_job_has_stable_fetch_identity_and_engine_job_contract() -> None:
    first = make_job()
    identical = make_job(item=Item(market_hash_name=ITEM.market_hash_name))

    assert isinstance(first, EngineJob)
    assert first.key == (
        "fetch-market:example:AK-47%20%7C%20Redline%20%28Field-Tested%29"
    )
    assert identical.key == first.key


def test_job_identity_changes_with_marketplace_or_item_without_delimiter_collisions() -> None:
    original = make_job()
    other_marketplace = make_job(marketplace="another")
    other_item = make_job(item=OTHER_ITEM)
    encoded_delimiters = make_job(
        marketplace="example:variant",
        item=Item(market_hash_name="item:variant%2F"),
    )

    assert len({original.key, other_marketplace.key, other_item.key}) == 3
    assert encoded_delimiters.key == "fetch-market:example%3Avariant:item%3Avariant%252F"


@pytest.mark.asyncio
async def test_execute_collects_requested_marketplace_and_item_then_persists() -> None:
    collection = FakeCollectionService()
    storage = FakeStorageService()

    await make_job(collection=collection, storage=storage).execute()

    assert collection.calls == [("example", ITEM)]
    assert storage.calls == [[QUOTE]]


@pytest.mark.asyncio
async def test_post_persistence_hook_receives_an_immutable_batch_in_order() -> None:
    storage = FakeStorageService()
    calls: list[str] = []

    async def after_persist(quotes: tuple[MarketQuote, ...]) -> None:
        assert storage.calls == [[QUOTE]]
        assert quotes == (QUOTE,)
        calls.append("processed")

    await make_job(storage=storage, after_persist=after_persist).execute()

    assert calls == ["processed"]


@pytest.mark.asyncio
async def test_empty_successful_collection_is_persisted_without_fabrication() -> None:
    async def collect_empty(_: str, __: Item) -> list[MarketQuote]:
        return []

    collection = FakeCollectionService(collect_empty)
    storage = FakeStorageService()

    await make_job(collection=collection, storage=storage).execute()

    assert storage.calls == [[]]


@pytest.mark.asyncio
async def test_collection_failure_propagates_without_persistence() -> None:
    failure = RuntimeError("provider failed")

    async def fail_collection(_: str, __: Item) -> list[MarketQuote]:
        raise failure

    collection = FakeCollectionService(fail_collection)
    storage = FakeStorageService()

    with pytest.raises(RuntimeError, match="provider failed") as raised:
        await make_job(collection=collection, storage=storage).execute()

    assert raised.value is failure
    assert storage.calls == []


@pytest.mark.asyncio
async def test_persistence_failure_propagates_after_collection() -> None:
    failure = RuntimeError("database failed")

    async def fail_storage(_: list[MarketQuote]) -> None:
        raise failure

    collection = FakeCollectionService()
    storage = FakeStorageService(fail_storage)

    with pytest.raises(RuntimeError, match="database failed") as raised:
        await make_job(collection=collection, storage=storage).execute()

    assert raised.value is failure
    assert collection.calls == [("example", ITEM)]
    assert storage.calls == [[QUOTE]]


@pytest.mark.asyncio
async def test_cancellation_during_collection_propagates_without_persistence() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocked_collection(_: str, __: Item) -> list[MarketQuote]:
        started.set()
        await release.wait()
        return [QUOTE]

    collection = FakeCollectionService(blocked_collection)
    storage = FakeStorageService()
    task = asyncio.create_task(make_job(collection=collection, storage=storage).execute())
    await asyncio.wait_for(started.wait(), 1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert storage.calls == []


@pytest.mark.asyncio
async def test_cancellation_during_persistence_propagates() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocked_storage(_: list[MarketQuote]) -> None:
        started.set()
        await release.wait()

    storage = FakeStorageService(blocked_storage)
    task = asyncio.create_task(make_job(storage=storage).execute())
    await asyncio.wait_for(started.wait(), 1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert storage.calls == [[QUOTE]]


@pytest.mark.asyncio
async def test_independent_jobs_can_execute_concurrently() -> None:
    both_started = asyncio.Event()
    release = asyncio.Event()
    started: set[Item] = set()

    async def blocked_collection(marketplace: str, item: Item) -> list[MarketQuote]:
        started.add(item)
        if len(started) == 2:
            both_started.set()
        await release.wait()
        return []

    collection = FakeCollectionService(blocked_collection)
    storage = FakeStorageService()
    jobs = [
        make_job(item=ITEM, collection=collection, storage=storage),
        make_job(item=OTHER_ITEM, collection=collection, storage=storage),
    ]
    tasks = [asyncio.create_task(job.execute()) for job in jobs]
    await asyncio.wait_for(both_started.wait(), 1)

    assert jobs[0].key != jobs[1].key
    assert started == {ITEM, OTHER_ITEM}
    release.set()
    await asyncio.gather(*tasks)
    assert storage.calls == [[], []]


@pytest.mark.asyncio
async def test_job_handler_rejects_duplicate_fetch_while_first_is_active() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocked_collection(_: str, __: Item) -> list[MarketQuote]:
        started.set()
        await release.wait()
        return [QUOTE]

    collection = FakeCollectionService(blocked_collection)
    storage = FakeStorageService()
    first = make_job(collection=collection, storage=storage)
    duplicate = make_job(collection=collection, storage=storage)
    handler = JobHandler(worker_count=1)
    await handler.start()
    try:
        assert handler.submit(first) is SubmissionResult.ACCEPTED
        await asyncio.wait_for(started.wait(), 1)
        assert handler.submit(duplicate) is SubmissionResult.DUPLICATE
        release.set()
        await wait_until(lambda: not handler.is_active(first.key))
    finally:
        release.set()
        await handler.stop()

    assert collection.calls == [("example", ITEM)]
    assert storage.calls == [[QUOTE]]


@pytest.mark.asyncio
async def test_job_is_compatible_with_scheduler_factory_and_key_validation() -> None:
    persisted = asyncio.Event()

    async def mark_persisted(_: list[MarketQuote]) -> None:
        persisted.set()

    collection = FakeCollectionService()
    storage = FakeStorageService(mark_persisted)
    definition = make_job(collection=collection, storage=storage)
    handler = JobHandler()
    scheduler = JobScheduler(handler, poll_seconds=0.001)
    await handler.start()
    scheduler.register(
        definition.key,
        lambda: make_job(collection=collection, storage=storage),
        interval_seconds=60,
        run_immediately=True,
    )
    await scheduler.start()
    try:
        await asyncio.wait_for(persisted.wait(), 1)
    finally:
        await scheduler.stop()
        await handler.stop()

    assert collection.calls == [("example", ITEM)]
    assert storage.calls == [[QUOTE]]


def test_job_module_has_no_direct_http_database_or_task_dependencies() -> None:
    import engine.jobs.fetch_market as fetch_market_module

    tree = ast.parse(inspect.getsource(fetch_market_module))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    imported_modules.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )

    forbidden_prefixes = (
        "aiosqlite",
        "asyncio",
        "engine.adapters",
        "engine.marketplaces",
        "engine.storage.database",
        "httpx",
        "sqlalchemy",
    )
    assert not any(
        module.startswith(forbidden_prefixes) for module in imported_modules
    )
