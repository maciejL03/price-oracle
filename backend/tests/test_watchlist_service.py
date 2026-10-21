import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest

from engine.errors import SchedulerError, StorageError, UnknownMarketplaceError
from engine.jobs import EngineJob, JobHandler, JobScheduler
from engine.marketplaces import MarketplaceRegistry
from engine.models import Item, MarketQuote
from engine.watchlists import (
    SyncResult,
    Watchlist,
    WatchlistItem,
    WatchlistService,
    WatchlistSyncError,
    fetch_job_key,
)

REDLINE = Item(market_hash_name="AK-47 | Redline (Field-Tested)")
ASIIMOV = Item(market_hash_name="AWP | Asiimov (Field-Tested)")


class FakeMarketplace:
    def __init__(self, name: str) -> None:
        self.name = name
        self.fetch_calls = 0

    async def fetch(self, item: Item) -> list[MarketQuote]:
        del item
        self.fetch_calls += 1
        return []


class FakeStorage:
    def __init__(self, watchlists: list[Watchlist] | None = None) -> None:
        self.watchlists = {watchlist.id: watchlist for watchlist in (watchlists or [])}
        self.save_calls = 0
        self.fail_save = False
        self.fail_list = False

    async def list_watchlists(self) -> list[Watchlist]:
        if self.fail_list:
            raise StorageError("simulated list failure")
        return list(self.watchlists.values())

    async def get_watchlist(self, watchlist_id: UUID) -> Watchlist | None:
        return self.watchlists.get(watchlist_id)

    async def save_watchlist(self, watchlist: Watchlist) -> None:
        if self.fail_save:
            raise StorageError("simulated save failure")
        self.save_calls += 1
        self.watchlists[watchlist.id] = watchlist

    async def delete_watchlist(self, watchlist_id: UUID) -> bool:
        return self.watchlists.pop(watchlist_id, None) is not None


@dataclass
class Registration:
    factory: Callable[[], EngineJob]
    interval_seconds: float
    run_immediately: bool


class FakeScheduler:
    def __init__(self) -> None:
        self.registrations: dict[str, Registration] = {}
        self.register_calls: list[str] = []
        self.unregister_calls: list[str] = []
        self.fail_register_once: set[str] = set()
        self.fail_unregister_once: set[str] = set()

    def register(
        self,
        key: str,
        job_factory: Callable[[], EngineJob],
        interval_seconds: float,
        *,
        run_immediately: bool = False,
    ) -> None:
        self.register_calls.append(key)
        if key in self.fail_register_once:
            self.fail_register_once.remove(key)
            raise SchedulerError("simulated registration failure")
        if key in self.registrations:
            raise SchedulerError(f"Schedule is already registered: {key}")
        self.registrations[key] = Registration(
            job_factory, interval_seconds, run_immediately
        )

    def unregister(self, key: str) -> bool:
        self.unregister_calls.append(key)
        if key in self.fail_unregister_once:
            self.fail_unregister_once.remove(key)
            raise SchedulerError("simulated unregistration failure")
        return self.registrations.pop(key, None) is not None


class FakeFetchJob(EngineJob):
    def __init__(self, marketplace: str, item: Item) -> None:
        self._key = fetch_job_key(marketplace, item)
        self.executions = 0

    @property
    def key(self) -> str:
        return self._key

    async def execute(self) -> None:
        self.executions += 1


class FakeJobFactory:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Item]] = []

    def __call__(self, marketplace: str, item: Item) -> EngineJob:
        self.calls.append((marketplace, item))
        return FakeFetchJob(marketplace, item)


def make_registry() -> tuple[MarketplaceRegistry, dict[str, FakeMarketplace]]:
    registry = MarketplaceRegistry()
    providers = {
        "csfloat": FakeMarketplace("csfloat"),
        "skinport": FakeMarketplace("skinport"),
    }
    for provider in providers.values():
        registry.register(provider)
    return registry, providers


def make_service(
    storage: FakeStorage | None = None,
    scheduler: FakeScheduler | None = None,
    factory: FakeJobFactory | None = None,
) -> tuple[WatchlistService, FakeStorage, FakeScheduler, FakeJobFactory]:
    actual_storage = storage or FakeStorage()
    actual_scheduler = scheduler or FakeScheduler()
    actual_factory = factory or FakeJobFactory()
    registry, _ = make_registry()
    service = WatchlistService(
        actual_storage,
        actual_scheduler,
        registry,
        actual_factory,
    )
    return service, actual_storage, actual_scheduler, actual_factory


@pytest.mark.asyncio
async def test_create_and_list_watchlist() -> None:
    service, storage, scheduler, _ = make_service()

    created = await service.create_watchlist("Trade targets", interval_seconds=60)

    assert created.name == "Trade targets"
    assert created.interval_seconds == 60
    assert created.items == ()
    assert await service.list_watchlists() == [created]
    assert storage.save_calls == 1
    assert scheduler.registrations == {}


@pytest.mark.asyncio
async def test_add_item_registers_an_immediate_recurring_fetch() -> None:
    service, _, scheduler, factory = make_service()
    watchlist = await service.create_watchlist("Targets", interval_seconds=30)

    updated = await service.add_item(watchlist.id, REDLINE, ["csfloat"])

    assert updated.items == (WatchlistItem(item=REDLINE, marketplaces={"csfloat"}),)
    key = fetch_job_key("csfloat", REDLINE)
    registration = scheduler.registrations[key]
    assert registration.interval_seconds == 30
    assert registration.run_immediately
    assert factory.calls == []
    assert registration.factory().key == key


@pytest.mark.asyncio
async def test_remove_item_unregisters_its_fetch() -> None:
    service, _, scheduler, _ = make_service()
    watchlist = await service.create_watchlist("Targets", interval_seconds=30)
    await service.add_item(watchlist.id, REDLINE, ["csfloat"])

    updated = await service.remove_item(watchlist.id, REDLINE)

    assert updated.items == ()
    assert scheduler.registrations == {}
    assert scheduler.unregister_calls == [fetch_job_key("csfloat", REDLINE)]


@pytest.mark.asyncio
async def test_one_item_can_monitor_multiple_marketplaces() -> None:
    service, _, scheduler, _ = make_service()
    watchlist = await service.create_watchlist("Targets", interval_seconds=30)

    updated = await service.add_item(watchlist.id, REDLINE, ["csfloat", "skinport"])

    assert updated.items[0].marketplaces == frozenset({"csfloat", "skinport"})
    assert set(scheduler.registrations) == {
        fetch_job_key("csfloat", REDLINE),
        fetch_job_key("skinport", REDLINE),
    }


@pytest.mark.asyncio
async def test_duplicate_entries_are_idempotent_and_reconcile() -> None:
    service, storage, scheduler, _ = make_service()
    watchlist = await service.create_watchlist("Targets", interval_seconds=30)
    first = await service.add_item(watchlist.id, REDLINE, ["csfloat"])
    save_calls = storage.save_calls
    register_calls = list(scheduler.register_calls)

    second = await service.add_item(watchlist.id, REDLINE, ["csfloat", "csfloat"])

    assert second == first
    assert storage.save_calls == save_calls
    assert scheduler.register_calls == register_calls


@pytest.mark.asyncio
async def test_adding_marketplace_to_existing_item_registers_only_new_fetch() -> None:
    service, _, scheduler, _ = make_service()
    watchlist = await service.create_watchlist("Targets", interval_seconds=30)
    await service.add_item(watchlist.id, REDLINE, ["csfloat"])
    scheduler.register_calls.clear()

    updated = await service.add_item(watchlist.id, REDLINE, ["skinport"])

    assert updated.items[0].marketplaces == frozenset({"csfloat", "skinport"})
    assert scheduler.register_calls == [fetch_job_key("skinport", REDLINE)]


@pytest.mark.asyncio
async def test_two_watchlists_share_one_fetch_at_the_shortest_interval() -> None:
    service, _, scheduler, _ = make_service()
    slow = await service.create_watchlist("Slow", interval_seconds=120)
    fast = await service.create_watchlist("Fast", interval_seconds=30)
    await service.add_item(slow.id, REDLINE, ["csfloat"])

    await service.add_item(fast.id, REDLINE, ["csfloat"])

    key = fetch_job_key("csfloat", REDLINE)
    assert list(scheduler.registrations) == [key]
    assert scheduler.registrations[key].interval_seconds == 30
    assert scheduler.unregister_calls == [key]


@pytest.mark.asyncio
async def test_removing_shared_item_keeps_fetch_required_elsewhere() -> None:
    service, _, scheduler, _ = make_service()
    first = await service.create_watchlist("First", interval_seconds=30)
    second = await service.create_watchlist("Second", interval_seconds=30)
    await service.add_item(first.id, REDLINE, ["csfloat"])
    await service.add_item(second.id, REDLINE, ["csfloat"])
    scheduler.unregister_calls.clear()

    await service.remove_item(first.id, REDLINE)

    assert fetch_job_key("csfloat", REDLINE) in scheduler.registrations
    assert scheduler.unregister_calls == []


@pytest.mark.asyncio
async def test_removing_faster_shared_item_restores_slower_interval() -> None:
    service, _, scheduler, _ = make_service()
    slow = await service.create_watchlist("Slow", interval_seconds=120)
    fast = await service.create_watchlist("Fast", interval_seconds=30)
    await service.add_item(slow.id, REDLINE, ["csfloat"])
    await service.add_item(fast.id, REDLINE, ["csfloat"])
    scheduler.unregister_calls.clear()

    await service.remove_item(fast.id, REDLINE)

    key = fetch_job_key("csfloat", REDLINE)
    assert scheduler.registrations[key].interval_seconds == 120
    assert scheduler.unregister_calls == [key]


@pytest.mark.asyncio
async def test_set_marketplaces_registers_and_unregisters_exact_difference() -> None:
    service, _, scheduler, _ = make_service()
    watchlist = await service.create_watchlist("Targets", interval_seconds=30)
    await service.add_item(watchlist.id, REDLINE, ["csfloat", "skinport"])
    scheduler.unregister_calls.clear()

    updated = await service.set_item_marketplaces(watchlist.id, REDLINE, ["skinport"])

    assert updated.items[0].marketplaces == frozenset({"skinport"})
    assert scheduler.unregister_calls == [fetch_job_key("csfloat", REDLINE)]
    assert set(scheduler.registrations) == {fetch_job_key("skinport", REDLINE)}


@pytest.mark.asyncio
async def test_sync_reconciles_persistent_changes_and_interval_updates() -> None:
    service, storage, scheduler, _ = make_service()
    watchlist = await service.create_watchlist("Targets", interval_seconds=60)
    await service.add_item(watchlist.id, REDLINE, ["csfloat"])
    storage.watchlists[watchlist.id] = Watchlist(
        id=watchlist.id,
        name=watchlist.name,
        interval_seconds=15,
        items=(WatchlistItem(item=ASIIMOV, marketplaces={"skinport"}),),
    )

    result = await service.sync_jobs()

    assert result == SyncResult(registered=1, unregistered=1)
    assert set(scheduler.registrations) == {fetch_job_key("skinport", ASIIMOV)}
    assert (
        scheduler.registrations[fetch_job_key("skinport", ASIIMOV)].interval_seconds
        == 15
    )


@pytest.mark.asyncio
async def test_sync_updates_interval_for_existing_identity() -> None:
    service, storage, scheduler, _ = make_service()
    watchlist = await service.create_watchlist("Targets", interval_seconds=60)
    stored = await service.add_item(watchlist.id, REDLINE, ["csfloat"])
    storage.watchlists[watchlist.id] = Watchlist(
        id=stored.id,
        name=stored.name,
        interval_seconds=10,
        items=stored.items,
    )

    result = await service.sync_jobs()

    key = fetch_job_key("csfloat", REDLINE)
    assert result == SyncResult(updated=1)
    assert scheduler.registrations[key].interval_seconds == 10


@pytest.mark.asyncio
async def test_repeated_sync_does_not_duplicate_registrations() -> None:
    service, _, scheduler, _ = make_service()
    watchlist = await service.create_watchlist("Targets", interval_seconds=30)
    await service.add_item(watchlist.id, REDLINE, ["csfloat"])
    scheduler.register_calls.clear()

    first = await service.sync_jobs()
    second = await service.sync_jobs()

    assert first == SyncResult(unchanged=1)
    assert second == SyncResult(unchanged=1)
    assert scheduler.register_calls == []


@pytest.mark.asyncio
async def test_sync_restores_jobs_from_persistent_configuration() -> None:
    watchlist = Watchlist(
        id=uuid4(),
        name="Restored",
        interval_seconds=45,
        items=(
            WatchlistItem(
                item=REDLINE,
                marketplaces={"csfloat", "skinport"},
            ),
        ),
    )
    service, _, scheduler, _ = make_service(FakeStorage([watchlist]))

    result = await service.sync_jobs()

    assert result == SyncResult(registered=2)
    assert all(reg.run_immediately for reg in scheduler.registrations.values())


@pytest.mark.asyncio
async def test_invalid_marketplace_is_rejected_before_persistence() -> None:
    service, storage, scheduler, _ = make_service()
    watchlist = await service.create_watchlist("Targets", interval_seconds=30)
    save_calls = storage.save_calls

    with pytest.raises(UnknownMarketplaceError):
        await service.add_item(watchlist.id, REDLINE, ["missing"])

    assert storage.save_calls == save_calls
    assert scheduler.registrations == {}


@pytest.mark.asyncio
async def test_storage_failure_does_not_change_schedules() -> None:
    service, storage, scheduler, _ = make_service()
    watchlist = await service.create_watchlist("Targets", interval_seconds=30)
    storage.fail_save = True

    with pytest.raises(StorageError, match="save failure"):
        await service.add_item(watchlist.id, REDLINE, ["csfloat"])

    assert scheduler.registrations == {}
    assert storage.watchlists[watchlist.id].items == ()


@pytest.mark.asyncio
async def test_storage_read_failure_stops_reconciliation() -> None:
    service, storage, scheduler, _ = make_service()
    watchlist = await service.create_watchlist("Targets", interval_seconds=30)
    await service.add_item(watchlist.id, REDLINE, ["csfloat"])
    registrations = dict(scheduler.registrations)
    storage.fail_list = True

    with pytest.raises(StorageError, match="list failure"):
        await service.sync_jobs()

    assert scheduler.registrations == registrations


@pytest.mark.asyncio
async def test_registration_failure_leaves_saved_configuration_recoverable() -> None:
    service, storage, scheduler, _ = make_service()
    watchlist = await service.create_watchlist("Targets", interval_seconds=30)
    key = fetch_job_key("csfloat", REDLINE)
    scheduler.fail_register_once.add(key)

    with pytest.raises(WatchlistSyncError) as error:
        await service.add_item(watchlist.id, REDLINE, ["csfloat"])

    assert storage.watchlists[watchlist.id].items[0].item == REDLINE
    assert error.value.failures[0].key == key
    assert scheduler.registrations == {}

    assert await service.sync_jobs() == SyncResult(registered=1)
    assert key in scheduler.registrations


@pytest.mark.asyncio
async def test_partial_sync_continues_and_recovers_on_next_attempt() -> None:
    watchlist = Watchlist(
        id=uuid4(),
        name="Targets",
        interval_seconds=30,
        items=(
            WatchlistItem(
                item=REDLINE,
                marketplaces={"csfloat", "skinport"},
            ),
        ),
    )
    scheduler = FakeScheduler()
    failed_key = fetch_job_key("skinport", REDLINE)
    scheduler.fail_register_once.add(failed_key)
    service, _, scheduler, _ = make_service(FakeStorage([watchlist]), scheduler)

    with pytest.raises(WatchlistSyncError):
        await service.sync_jobs()

    assert set(scheduler.registrations) == {fetch_job_key("csfloat", REDLINE)}
    result = await service.sync_jobs()
    assert result == SyncResult(registered=1, unchanged=1)
    assert set(scheduler.registrations) == {
        fetch_job_key("csfloat", REDLINE),
        failed_key,
    }


@pytest.mark.asyncio
async def test_unregistration_failure_is_retried() -> None:
    service, storage, scheduler, _ = make_service()
    watchlist = await service.create_watchlist("Targets", interval_seconds=30)
    await service.add_item(watchlist.id, REDLINE, ["csfloat"])
    key = fetch_job_key("csfloat", REDLINE)
    storage.watchlists[watchlist.id] = Watchlist(
        id=watchlist.id,
        name=watchlist.name,
        interval_seconds=30,
    )
    scheduler.fail_unregister_once.add(key)

    with pytest.raises(WatchlistSyncError):
        await service.sync_jobs()

    assert key in scheduler.registrations
    assert await service.sync_jobs() == SyncResult(unregistered=1)
    assert scheduler.registrations == {}


@pytest.mark.asyncio
async def test_deleting_watchlist_removes_only_no_longer_required_jobs() -> None:
    service, storage, scheduler, _ = make_service()
    first = await service.create_watchlist("First", interval_seconds=30)
    second = await service.create_watchlist("Second", interval_seconds=30)
    await service.add_item(first.id, REDLINE, ["csfloat"])
    await service.add_item(second.id, ASIIMOV, ["skinport"])

    await service.delete_watchlist(first.id)

    assert first.id not in storage.watchlists
    assert set(scheduler.registrations) == {fetch_job_key("skinport", ASIIMOV)}


@pytest.mark.asyncio
async def test_service_never_executes_jobs_or_calls_marketplaces() -> None:
    registry, providers = make_registry()
    storage = FakeStorage()
    scheduler = FakeScheduler()
    factory = FakeJobFactory()
    service = WatchlistService(storage, scheduler, registry, factory)
    watchlist = await service.create_watchlist("Targets", interval_seconds=30)

    await service.add_item(watchlist.id, REDLINE, ["csfloat"])

    assert factory.calls == []
    assert providers["csfloat"].fetch_calls == 0


@pytest.mark.asyncio
async def test_removing_schedule_does_not_cancel_running_fetch() -> None:
    registry, _ = make_registry()
    storage = FakeStorage()
    handler = JobHandler(worker_count=1)
    scheduler = JobScheduler(handler, poll_seconds=0.001)
    started = asyncio.Event()
    release = asyncio.Event()
    completed = asyncio.Event()

    class BlockingFetchJob(EngineJob):
        def __init__(self, marketplace: str, item: Item) -> None:
            self._key = fetch_job_key(marketplace, item)

        @property
        def key(self) -> str:
            return self._key

        async def execute(self) -> None:
            started.set()
            await release.wait()
            completed.set()

    service = WatchlistService(storage, scheduler, registry, BlockingFetchJob)
    watchlist = await service.create_watchlist("Targets", interval_seconds=60)
    await service.add_item(watchlist.id, REDLINE, ["csfloat"])
    await handler.start()
    await scheduler.start()
    try:
        await asyncio.wait_for(started.wait(), 1)
        await service.remove_item(watchlist.id, REDLINE)
        assert handler.is_active(fetch_job_key("csfloat", REDLINE))
        release.set()
        await asyncio.wait_for(completed.wait(), 1)
    finally:
        release.set()
        await scheduler.stop()
        await handler.stop()


def test_fetch_job_key_matches_fetch_market_job_identity_and_escapes_item() -> None:
    item = Item(market_hash_name="Sticker | A/B #1")

    assert (
        fetch_job_key("csfloat", item)
        == "fetch-market:csfloat:Sticker%20%7C%20A%2FB%20%231"
    )
