import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from engine.alerts import AlertCondition, AlertRule
from engine.bootstrap import create_market_engine
from engine.config import EngineSettings
from engine.models import Item, MarketQuote, QuoteType
from engine.notifications import DeliveryStatus
from engine.runtime import run_market_engine
from engine.storage import StorageService
from engine.watchlists import Watchlist, WatchlistItem

ITEM = Item(market_hash_name="AK-47 | Redline (Field-Tested)")
OBSERVED_AT = datetime.now(UTC)


class FakeMarketplace:
    def __init__(
        self,
        name: str,
        fetch: Callable[[Item], Awaitable[list[MarketQuote]]],
    ) -> None:
        self.name = name
        self._fetch = fetch
        self.calls = 0
        self.closed = False

    async def fetch(self, item: Item) -> list[MarketQuote]:
        self.calls += 1
        return await self._fetch(item)

    async def aclose(self) -> None:
        self.closed = True


def _settings(database_path: Path, **overrides: object) -> EngineSettings:
    values: dict[str, object] = {
        "database_path": database_path,
        "csfloat_enabled": False,
        "worker_count": 3,
        "job_queue_capacity": 8,
        "scheduler_poll_seconds": 0.001,
        "quote_max_age_seconds": 3600,
    }
    values.update(overrides)
    return EngineSettings(_env_file=None, _secrets_dir=None, **values)


def _quote(
    marketplace: str,
    price: str,
    *,
    currency: str = "GBP",
    quote_type: QuoteType = QuoteType.LOWEST_ASK,
    observed_at: datetime = OBSERVED_AT,
) -> MarketQuote:
    return MarketQuote(
        marketplace=marketplace,
        market_hash_name=ITEM.market_hash_name,
        price=Decimal(price),
        currency=currency,
        observed_at=observed_at,
        quote_type=quote_type,
    )


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
            await asyncio.sleep(0)


async def _seed_watchlist(
    database_path: Path,
    marketplaces: set[str],
    *,
    interval_seconds: float = 60,
    rule: AlertRule | None = None,
) -> None:
    async with StorageService(database_path) as storage:
        await storage.save_watchlist(
            Watchlist(
                id=uuid4(),
                name="Integration targets",
                interval_seconds=interval_seconds,
                items=(WatchlistItem(item=ITEM, marketplaces=marketplaces),),
            )
        )
        if rule is not None:
            await storage.save_alert_rule(rule)


async def _stop_process(
    shutdown_event: asyncio.Event,
    process: asyncio.Task[None],
) -> None:
    shutdown_event.set()
    await asyncio.wait_for(process, 1)


@pytest.mark.asyncio
async def test_process_collects_persists_and_prices_compatible_quotes(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "engine.sqlite3"
    await _seed_watchlist(database_path, {"alpha", "beta", "usd"})

    async def alpha_fetch(_: Item) -> list[MarketQuote]:
        return [_quote("alpha", "10.0100")]

    async def beta_fetch(_: Item) -> list[MarketQuote]:
        return [_quote("beta", "11.0200")]

    async def usd_fetch(_: Item) -> list[MarketQuote]:
        return [_quote("usd", "8.00", currency="USD")]

    marketplaces = [
        FakeMarketplace("alpha", alpha_fetch),
        FakeMarketplace("beta", beta_fetch),
        FakeMarketplace("usd", usd_fetch),
    ]
    engine = create_market_engine(
        _settings(database_path),
        marketplaces=marketplaces,
    )
    shutdown_event = asyncio.Event()
    process = asyncio.create_task(
        run_market_engine(
            engine,
            shutdown_event=shutdown_event,
            handle_signals=False,
        )
    )

    async def all_quotes_were_persisted() -> bool:
        return len(await engine.storage.get_latest_quotes(ITEM)) == 3

    try:
        await _wait_until(lambda: engine.running)
        await _wait_until(all_quotes_were_persisted)
        observations = await engine.storage.get_latest_quotes(ITEM)
        comparison = engine.pricing.compare(
            ITEM,
            observations,
            currency="GBP",
            quote_type=QuoteType.LOWEST_ASK,
            as_of=OBSERVED_AT + timedelta(seconds=1),
        )

        assert comparison.lowest_price == Decimal("10.0100")
        assert comparison.highest_price == Decimal("11.0200")
        assert comparison.spread == Decimal("1.0100")
        assert comparison.best_quote is not None
        assert comparison.best_quote.marketplace == "alpha"
        assert [
            excluded.quote.marketplace
            for excluded in comparison.excluded_quotes
        ] == ["usd"]
    finally:
        await _stop_process(shutdown_event, process)

    assert all(marketplace.closed for marketplace in marketplaces)

    async with StorageService(database_path) as storage:
        assert len(await storage.get_price_history(ITEM)) == 3


@pytest.mark.asyncio
async def test_restart_restores_watchlist_and_alert_state_then_rearms(
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
    await _seed_watchlist(database_path, {"fake"}, rule=rule)

    requests: list[httpx.Request] = []

    def send(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    async def first_fetch(_: Item) -> list[MarketQuote]:
        return [_quote("fake", "12", observed_at=OBSERVED_AT)]

    first_client = httpx.AsyncClient(transport=httpx.MockTransport(send))
    first_marketplace = FakeMarketplace("fake", first_fetch)
    first_engine = create_market_engine(
        _settings(
            database_path,
            discord_webhook_url=SecretStr(
                "https://discord.com/api/webhooks/123/test-token"
            ),
        ),
        marketplaces=[first_marketplace],
        notification_http_client=first_client,
    )
    first_shutdown = asyncio.Event()
    first_process = asyncio.create_task(
        run_market_engine(
            first_engine,
            shutdown_event=first_shutdown,
            handle_signals=False,
        )
    )

    async def first_delivery_succeeded() -> bool:
        events = await first_engine.storage.list_alert_events(rule_id=rule.id)
        if len(events) != 1:
            return False
        delivery = await first_engine.storage.get_notification_delivery(events[0].id)
        return delivery is not None and delivery.status is DeliveryStatus.SUCCEEDED

    try:
        await _wait_until(lambda: first_engine.running)
        await _wait_until(first_delivery_succeeded)
    finally:
        await _stop_process(first_shutdown, first_process)

    async with StorageService(database_path) as storage:
        watchlist = (await storage.list_watchlists())[0]
        await storage.save_watchlist(
            watchlist.model_copy(update={"interval_seconds": 0.01})
        )

    prices = iter(("11", "25", "10"))

    async def restarted_fetch(_: Item) -> list[MarketQuote]:
        price = next(prices, "9")
        return [
            _quote(
                "fake",
                price,
                observed_at=datetime.now(UTC),
            )
        ]

    second_client = httpx.AsyncClient(transport=httpx.MockTransport(send))
    restarted_marketplace = FakeMarketplace("fake", restarted_fetch)
    restarted_engine = create_market_engine(
        _settings(
            database_path,
            discord_webhook_url=SecretStr(
                "https://discord.com/api/webhooks/123/test-token"
            ),
        ),
        marketplaces=[restarted_marketplace],
        notification_http_client=second_client,
    )
    second_shutdown = asyncio.Event()
    second_process = asyncio.create_task(
        run_market_engine(
            restarted_engine,
            shutdown_event=second_shutdown,
            handle_signals=False,
        )
    )

    async def rearmed_alert_was_delivered() -> bool:
        events = await restarted_engine.storage.list_alert_events(rule_id=rule.id)
        if len(events) != 2:
            return False
        deliveries = [
            await restarted_engine.storage.get_notification_delivery(event.id)
            for event in events
        ]
        return all(
            delivery is not None and delivery.status is DeliveryStatus.SUCCEEDED
            for delivery in deliveries
        )

    try:
        await _wait_until(lambda: restarted_engine.running)
        await _wait_until(rearmed_alert_was_delivered)
    finally:
        await _stop_process(second_shutdown, second_process)

    assert restarted_marketplace.calls >= 3
    assert len(requests) == 2
    assert first_marketplace.closed
    assert restarted_marketplace.closed
    assert first_client.is_closed
    assert second_client.is_closed

    async with StorageService(database_path) as storage:
        assert len(await storage.list_alert_events(rule_id=rule.id)) == 2
        assert len(await storage.get_price_history(ITEM)) >= 4
