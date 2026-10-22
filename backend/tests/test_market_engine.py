import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

import engine.bootstrap as bootstrap_module
from engine.alerts import AlertCondition, AlertRule
from engine.bootstrap import create_market_engine
from engine.config import EngineSettings
from engine.market_engine import (
    EngineLifecycleError,
    EngineShutdownError,
    EngineStartupError,
    EngineState,
    MarketEngine,
)
from engine.models import Item, MarketQuote, QuoteType
from engine.notifications import DeliveryStatus
from engine.storage import StorageService
from engine.watchlists import Watchlist, WatchlistItem

ITEM = Item(market_hash_name="AK-47 | Redline (Field-Tested)")


async def _wait_until(
    predicate: Callable[[], bool | Awaitable[bool]], timeout: float = 1.0
) -> None:
    async with asyncio.timeout(timeout):
        while True:
            result = predicate()
            if isinstance(result, Awaitable):
                result = await result
            if result:
                return
            await asyncio.sleep(0.001)


def _settings(database_path: Path, **overrides: object) -> EngineSettings:
    values: dict[str, object] = {
        "database_path": database_path,
        "csfloat_enabled": False,
        "worker_count": 1,
        "scheduler_poll_seconds": 0.001,
        "quote_max_age_seconds": 3600,
    }
    values.update(overrides)
    return EngineSettings(_env_file=None, _secrets_dir=None, **values)


def _quote(*, quote_type: QuoteType = QuoteType.INDIVIDUAL_LISTING) -> MarketQuote:
    return MarketQuote(
        marketplace="fake",
        market_hash_name=ITEM.market_hash_name,
        price=Decimal("12.34"),
        currency="GBP",
        observed_at=datetime.now(UTC),
        quote_type=quote_type,
        external_listing_id=(
            "listing-1" if quote_type is QuoteType.INDIVIDUAL_LISTING else None
        ),
    )


class FakeMarketplace:
    name = "fake"

    def __init__(
        self,
        fetch: Callable[[Item], Awaitable[list[MarketQuote]]] | None = None,
    ) -> None:
        self._fetch = fetch
        self.calls = 0
        self.closed = False

    async def fetch(self, item: Item) -> list[MarketQuote]:
        self.calls += 1
        if self._fetch is not None:
            return await self._fetch(item)
        return [_quote()]

    async def aclose(self) -> None:
        self.closed = True


async def _seed_watchlist(
    database_path: Path,
    *,
    interval_seconds: float = 60,
    rule: AlertRule | None = None,
) -> None:
    async with StorageService(database_path) as storage:
        await storage.save_watchlist(
            Watchlist(
                id=uuid4(),
                name="Targets",
                interval_seconds=interval_seconds,
                items=(
                    WatchlistItem(item=ITEM, marketplaces={"fake"}),
                ),
            )
        )
        if rule is not None:
            await storage.save_alert_rule(rule)


@pytest.mark.asyncio
async def test_bootstrap_starts_restores_fetches_and_stops_cleanly(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "engine.sqlite3"
    await _seed_watchlist(database_path)
    marketplace = FakeMarketplace()
    engine = create_market_engine(
        _settings(database_path), marketplaces=[marketplace]
    )

    await engine.start()

    async def quote_was_persisted() -> bool:
        return bool(await engine.storage.get_price_history(ITEM))

    await _wait_until(quote_was_persisted)
    assert engine.state is EngineState.RUNNING
    assert engine.job_handler.running
    assert engine.scheduler.running
    assert engine.registry.names == ("fake",)
    assert marketplace.calls == 1
    assert engine.notifications is None

    with pytest.raises(EngineLifecycleError, match="cannot start"):
        await engine.start()

    await engine.stop()
    await engine.stop()

    assert engine.state is EngineState.STOPPED
    assert not engine.job_handler.running
    assert not engine.scheduler.running
    assert marketplace.closed
    with pytest.raises(EngineLifecycleError, match="cannot start"):
        await engine.start()

    async with StorageService(database_path) as storage:
        history = await storage.get_price_history(ITEM)
        assert len(history) == 1
        assert history[0].marketplace == "fake"
        assert history[0].quote_type is QuoteType.INDIVIDUAL_LISTING


@pytest.mark.asyncio
async def test_default_bootstrap_closes_each_owned_http_resource_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    close_counts: dict[str, int] = {"catalog": 0, "steam": 0, "skinport": 0}

    class Catalog:
        def __init__(self, **_: object) -> None:
            pass

        async def search(self, query: str, *, limit: int) -> list[object]:
            return []

        async def resolve(self, market_hash_name: str) -> None:
            return None

        async def aclose(self) -> None:
            close_counts["catalog"] += 1

    class Provider:
        def __init__(self, name: str) -> None:
            self.name = name

        async def fetch(self, item: Item) -> list[MarketQuote]:
            return []

        async def fetch_snapshot(self, item: Item) -> None:
            return None

        async def aclose(self) -> None:
            close_counts[self.name] += 1

    monkeypatch.setattr(bootstrap_module, "ByMykelItemCatalog", Catalog)
    monkeypatch.setattr(
        bootstrap_module, "SteamMarketplace", lambda **_: Provider("steam")
    )
    monkeypatch.setattr(
        bootstrap_module, "SkinportMarketplace", lambda **_: Provider("skinport")
    )

    engine = create_market_engine(_settings(tmp_path / "engine.sqlite3"))
    await engine.start()
    await engine.stop()

    assert close_counts == {"catalog": 1, "steam": 1, "skinport": 1}


@pytest.mark.asyncio
async def test_startup_and_shutdown_follow_dependency_order() -> None:
    calls: list[str] = []

    class Storage:
        async def initialize(self) -> None:
            calls.append("storage.initialize")

        async def close(self) -> None:
            calls.append("storage.close")

    class Handler:
        async def start(self) -> None:
            calls.append("handler.start")

        async def stop(self) -> None:
            calls.append("handler.stop")

    class Scheduler:
        async def start(self) -> None:
            calls.append("scheduler.start")

        async def stop(self) -> None:
            calls.append("scheduler.stop")

    class Watchlists:
        async def sync_jobs(self) -> None:
            calls.append("watchlists.sync")

    async def close_first() -> None:
        calls.append("resource.first")

    async def close_second() -> None:
        calls.append("resource.second")

    engine = MarketEngine(
        storage=Storage(),  # type: ignore[arg-type]
        registry=object(),  # type: ignore[arg-type]
        collection=object(),  # type: ignore[arg-type]
        job_handler=Handler(),  # type: ignore[arg-type]
        scheduler=Scheduler(),  # type: ignore[arg-type]
        watchlists=Watchlists(),  # type: ignore[arg-type]
        pricing=object(),  # type: ignore[arg-type]
        alerts=object(),  # type: ignore[arg-type]
        notifications=None,
        owned_resource_closers=[close_first, close_second],
    )

    await engine.start()
    assert calls == [
        "storage.initialize",
        "handler.start",
        "watchlists.sync",
        "scheduler.start",
    ]

    await engine.stop()
    assert calls == [
        "storage.initialize",
        "handler.start",
        "watchlists.sync",
        "scheduler.start",
        "scheduler.stop",
        "handler.stop",
        "resource.second",
        "resource.first",
        "storage.close",
    ]


@pytest.mark.asyncio
async def test_startup_failure_releases_every_initialized_resource() -> None:
    calls: list[str] = []

    class Storage:
        async def initialize(self) -> None:
            calls.append("storage.initialize")

        async def close(self) -> None:
            calls.append("storage.close")

    class Handler:
        async def start(self) -> None:
            calls.append("handler.start")

        async def stop(self) -> None:
            calls.append("handler.stop")

    class Scheduler:
        async def start(self) -> None:
            calls.append("scheduler.start")

        async def stop(self) -> None:
            calls.append("scheduler.stop")

    class Watchlists:
        async def sync_jobs(self) -> None:
            calls.append("watchlists.sync")
            raise RuntimeError("restore failed")

    async def close_resource() -> None:
        calls.append("resource.close")

    engine = MarketEngine(
        storage=Storage(),  # type: ignore[arg-type]
        registry=object(),  # type: ignore[arg-type]
        collection=object(),  # type: ignore[arg-type]
        job_handler=Handler(),  # type: ignore[arg-type]
        scheduler=Scheduler(),  # type: ignore[arg-type]
        watchlists=Watchlists(),  # type: ignore[arg-type]
        pricing=object(),  # type: ignore[arg-type]
        alerts=object(),  # type: ignore[arg-type]
        notifications=None,
        owned_resource_closers=[close_resource],
    )

    with pytest.raises(EngineStartupError, match="watchlist restoration") as raised:
        await engine.start()

    assert isinstance(raised.value.__cause__, RuntimeError)
    assert engine.state is EngineState.STOPPED
    assert calls == [
        "storage.initialize",
        "handler.start",
        "watchlists.sync",
        "handler.stop",
        "resource.close",
        "storage.close",
    ]


@pytest.mark.asyncio
async def test_shutdown_reports_failures_after_attempting_all_cleanup() -> None:
    calls: list[str] = []

    class Storage:
        async def initialize(self) -> None:
            calls.append("storage.initialize")

        async def close(self) -> None:
            calls.append("storage.close")

    class Handler:
        async def start(self) -> None:
            calls.append("handler.start")

        async def stop(self) -> None:
            calls.append("handler.stop")
            raise RuntimeError("handler cleanup failed")

    class Scheduler:
        async def start(self) -> None:
            calls.append("scheduler.start")

        async def stop(self) -> None:
            calls.append("scheduler.stop")

    class Watchlists:
        async def sync_jobs(self) -> None:
            calls.append("watchlists.sync")

    async def close_resource() -> None:
        calls.append("resource.close")

    engine = MarketEngine(
        storage=Storage(),  # type: ignore[arg-type]
        registry=object(),  # type: ignore[arg-type]
        collection=object(),  # type: ignore[arg-type]
        job_handler=Handler(),  # type: ignore[arg-type]
        scheduler=Scheduler(),  # type: ignore[arg-type]
        watchlists=Watchlists(),  # type: ignore[arg-type]
        pricing=object(),  # type: ignore[arg-type]
        alerts=object(),  # type: ignore[arg-type]
        notifications=None,
        owned_resource_closers=[close_resource],
    )
    await engine.start()

    with pytest.raises(EngineShutdownError) as raised:
        await engine.stop()

    assert len(raised.value.failures) == 1
    assert calls[-4:] == [
        "scheduler.stop",
        "handler.stop",
        "resource.close",
        "storage.close",
    ]
    assert engine.state is EngineState.STOPPED


@pytest.mark.asyncio
async def test_failed_recurring_job_does_not_terminate_engine(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    database_path = tmp_path / "engine.sqlite3"
    await _seed_watchlist(database_path, interval_seconds=0.01)

    async def fail_once(_: Item) -> list[MarketQuote]:
        if marketplace.calls == 1:
            raise RuntimeError("simulated marketplace failure")
        return [_quote()]

    marketplace = FakeMarketplace(fail_once)
    engine = create_market_engine(
        _settings(database_path), marketplaces=[marketplace]
    )
    with caplog.at_level(logging.ERROR, logger="engine.jobs.handler"):
        await engine.start()

        async def quote_was_persisted() -> bool:
            return bool(await engine.storage.get_price_history(ITEM))

        await _wait_until(quote_was_persisted)

    assert marketplace.calls >= 2
    assert engine.running
    assert "Job failed:" in caplog.text
    await engine.stop()


@pytest.mark.asyncio
async def test_lowest_ask_fetch_creates_event_and_delivers_notification(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "engine.sqlite3"
    rule = AlertRule(
        id=uuid4(),
        item=ITEM,
        marketplace="fake",
        currency="GBP",
        quote_type=QuoteType.LOWEST_ASK,
        condition=AlertCondition.BELOW,
        threshold=Decimal("20"),
    )
    await _seed_watchlist(database_path, rule=rule)

    async def fetch_lowest_ask(_: Item) -> list[MarketQuote]:
        return [_quote(quote_type=QuoteType.LOWEST_ASK)]

    requests: list[httpx.Request] = []

    def deliver(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(deliver))
    engine = create_market_engine(
        _settings(
            database_path,
            discord_webhook_url=SecretStr(
                "https://discord.com/api/webhooks/123/test-token"
            ),
        ),
        marketplaces=[FakeMarketplace(fetch_lowest_ask)],
        notification_http_client=http_client,
    )
    await engine.start()

    async def notification_succeeded() -> bool:
        events = await engine.storage.list_alert_events(rule_id=rule.id)
        if not events:
            return False
        delivery_state = await engine.storage.get_notification_delivery(events[0].id)
        return (
            delivery_state is not None
            and delivery_state.status is DeliveryStatus.SUCCEEDED
        )

    await _wait_until(notification_succeeded)
    events = await engine.storage.list_alert_events(rule_id=rule.id)
    assert len(events) == 1
    assert len(requests) == 1

    await engine.stop()
    assert http_client.is_closed


@pytest.mark.asyncio
async def test_cancelled_shutdown_finishes_active_work_and_leaves_no_tasks(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "engine.sqlite3"
    await _seed_watchlist(database_path)
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocked_fetch(_: Item) -> list[MarketQuote]:
        started.set()
        await release.wait()
        return [_quote()]

    marketplace = FakeMarketplace(blocked_fetch)
    engine = create_market_engine(
        _settings(database_path), marketplaces=[marketplace]
    )
    await engine.start()
    await asyncio.wait_for(started.wait(), 1)

    stop_task = asyncio.create_task(engine.stop())
    await _wait_until(lambda: not engine.job_handler.running)
    stop_task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await stop_task

    assert engine.state is EngineState.STOPPED
    assert marketplace.closed
    await asyncio.sleep(0)
    engine_task_names = {
        task.get_name()
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task()
        and (
            task.get_name().startswith("job-worker-")
            or task.get_name() in {"job-scheduler", "market-engine-cleanup"}
        )
    }
    assert engine_task_names == set()

    async with StorageService(database_path) as storage:
        assert len(await storage.get_price_history(ITEM)) == 1


@pytest.mark.asyncio
async def test_cancelled_startup_rolls_back_without_orphaned_workers() -> None:
    sync_started = asyncio.Event()

    class Storage:
        closed = False

        async def initialize(self) -> None:
            return None

        async def close(self) -> None:
            self.closed = True

    class Handler:
        running = False

        async def start(self) -> None:
            self.running = True
            self.worker = asyncio.create_task(
                asyncio.Event().wait(), name="test-cancelled-start-worker"
            )

        async def stop(self) -> None:
            self.running = False
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)

    class Scheduler:
        async def start(self) -> None:
            raise AssertionError("scheduler must not start")

        async def stop(self) -> None:
            return None

    class Watchlists:
        async def sync_jobs(self) -> None:
            sync_started.set()
            await asyncio.Event().wait()

    storage = Storage()
    handler = Handler()
    engine = MarketEngine(
        storage=storage,  # type: ignore[arg-type]
        registry=object(),  # type: ignore[arg-type]
        collection=object(),  # type: ignore[arg-type]
        job_handler=handler,  # type: ignore[arg-type]
        scheduler=Scheduler(),  # type: ignore[arg-type]
        watchlists=Watchlists(),  # type: ignore[arg-type]
        pricing=object(),  # type: ignore[arg-type]
        alerts=object(),  # type: ignore[arg-type]
        notifications=None,
    )
    start_task = asyncio.create_task(engine.start())
    await asyncio.wait_for(sync_started.wait(), 1)

    start_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await start_task

    assert engine.state is EngineState.STOPPED
    assert not handler.running
    assert storage.closed
    assert handler.worker.done()
