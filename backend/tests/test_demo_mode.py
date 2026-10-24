import asyncio
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import SecretStr

from engine.bootstrap import create_market_engine
from engine.config import EngineSettings
from engine.demo import DEMO_ALERT_ID, DEMO_ITEM_NAME, DEMO_WATCHLIST_ID
from engine.models import Item, QuoteType
from engine.notifications import DeliveryStatus


async def _wait_until(check: Callable[[], object], timeout: float = 2.0) -> object:
    async with asyncio.timeout(timeout):
        while not (result := await check()):
            await asyncio.sleep(0.005)
    return result


def _settings(path: Path) -> EngineSettings:
    return EngineSettings(
        _env_file=None,
        _secrets_dir=None,
        operating_mode="demo",
        database_path=path,
        worker_count=1,
        scheduler_poll_seconds=0.001,
    )


@pytest.mark.asyncio
async def test_demo_mode_runs_complete_keyless_pipeline_and_restores_state(
    tmp_path: Path,
) -> None:
    path = tmp_path / "demo.sqlite3"
    engine = create_market_engine(_settings(path))
    assert engine.registry.names == ("demo-market-a", "demo-market-b")
    assert engine.notifications is not None

    await engine.start()

    async def complete() -> object:
        quotes = await engine.storage.get_latest_quotes(
            Item(market_hash_name=DEMO_ITEM_NAME),
            currency="GBP",
            quote_type=QuoteType.LOWEST_ASK,
        )
        events = await engine.storage.list_alert_events(rule_id=DEMO_ALERT_ID)
        if len(quotes) != 2 or len(events) != 1:
            return False
        delivery = await engine.storage.get_notification_delivery(events[0].id)
        return delivery if delivery and delivery.status is DeliveryStatus.SUCCEEDED else False

    delivery = await _wait_until(complete)
    assert delivery.channel == "demo"
    comparison = engine.pricing.compare(
        Item(market_hash_name=DEMO_ITEM_NAME),
        await engine.storage.get_latest_quotes(Item(market_hash_name=DEMO_ITEM_NAME)),
        currency="GBP",
        quote_type=QuoteType.LOWEST_ASK,
        as_of=delivery.completed_at,
    )
    assert comparison.lowest_price is not None
    assert comparison.spread is not None
    await engine.stop()

    restarted = create_market_engine(_settings(path))
    await restarted.start()
    try:
        watchlist = await restarted.storage.get_watchlist(DEMO_WATCHLIST_ID)
        assert watchlist is not None
        assert watchlist.items[0].item.market_hash_name == DEMO_ITEM_NAME
        assert len(await restarted.storage.list_alert_events(rule_id=DEMO_ALERT_ID)) == 1
    finally:
        await restarted.stop()


def test_public_mode_registers_keyless_providers_without_credentials(tmp_path: Path) -> None:
    settings = EngineSettings(
        _env_file=None,
        _secrets_dir=None,
        operating_mode="public",
        database_path=tmp_path / "public.sqlite3",
        csfloat_api_key=None,
        discord_webhook_url=None,
    )
    engine = create_market_engine(settings)

    assert engine.registry.names == ("csfloat", "steam", "skinport")
    assert engine.notifications is None


def test_invalid_optional_credentials_disable_only_the_related_features(
    tmp_path: Path,
) -> None:
    settings = EngineSettings(
        _env_file=None,
        _secrets_dir=None,
        operating_mode="public",
        database_path=tmp_path / "public.sqlite3",
        csfloat_api_key=SecretStr("invalid key "),
        discord_webhook_url=SecretStr("https://example.test/not-discord"),
    )

    engine = create_market_engine(settings)

    assert engine.registry.names == ("steam", "skinport")
    assert engine.notifications is None
