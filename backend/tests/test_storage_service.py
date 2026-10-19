import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import aiosqlite
import pytest

from engine.errors import StorageBusyError, StorageError
from engine.models import Item, MarketQuote, QuoteType
from engine.storage import StorageService

ITEM_NAME = "AK-47 | Redline (Field-Tested)"


def make_quote(**overrides: object) -> MarketQuote:
    values: dict[str, object] = {
        "marketplace": "skinport",
        "market_hash_name": ITEM_NAME,
        "price": Decimal("22.3900"),
        "currency": "GBP",
        "observed_at": datetime(2026, 1, 2, 3, 4, 5, 678901, tzinfo=UTC),
        "quote_type": QuoteType.LOWEST_ASK,
        "external_listing_id": None,
    }
    values.update(overrides)
    return MarketQuote.model_validate(values)


@pytest.mark.asyncio
async def test_initialize_creates_parent_and_database(tmp_path: Path) -> None:
    database_path = tmp_path / "nested" / "quotes.sqlite3"

    async with StorageService(database_path):
        assert database_path.is_file()

    with sqlite3.connect(database_path) as connection:
        table = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = 'market_quotes'"
        ).fetchone()
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()

    assert table == ("market_quotes",)
    assert journal_mode == ("wal",)


@pytest.mark.asyncio
async def test_quote_round_trip_preserves_every_domain_field(tmp_path: Path) -> None:
    offset_time = datetime(
        2026,
        2,
        3,
        16,
        5,
        6,
        123456,
        tzinfo=timezone(timedelta(hours=5, minutes=30)),
    )
    quote = make_quote(
        price=Decimal("12345678901234567890.12345678901234567890"),
        observed_at=offset_time,
        quote_type=QuoteType.INDIVIDUAL_LISTING,
        external_listing_id="listing-123",
    )

    async with StorageService(tmp_path / "quotes.sqlite3") as storage:
        await storage.save_quotes([quote])
        restored = await storage.get_price_history(Item(market_hash_name=ITEM_NAME))

    assert restored == [quote]
    assert restored[0].price.as_tuple() == quote.price.as_tuple()
    assert restored[0].observed_at == offset_time.astimezone(UTC)


@pytest.mark.asyncio
async def test_save_empty_batch_is_a_successful_no_op(tmp_path: Path) -> None:
    async with StorageService(tmp_path / "quotes.sqlite3") as storage:
        await storage.save_quotes([])

        assert await storage.get_price_history(Item(market_hash_name=ITEM_NAME)) == []


@pytest.mark.asyncio
async def test_failed_batch_rolls_back_every_row(tmp_path: Path) -> None:
    valid = make_quote()
    invalid = MarketQuote.model_construct(
        **{**valid.model_dump(), "currency": "GB"}
    )

    async with StorageService(tmp_path / "quotes.sqlite3") as storage:
        with pytest.raises(StorageError) as error:
            await storage.save_quotes([valid, invalid])

        history = await storage.get_price_history(Item(market_hash_name=ITEM_NAME))

    assert history == []
    assert error.value.__cause__ is not None


@pytest.mark.asyncio
async def test_latest_quotes_returns_newest_per_series_and_keeps_ties(
    tmp_path: Path,
) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    quotes = [
        make_quote(price=Decimal("10"), observed_at=start),
        make_quote(price=Decimal("11"), observed_at=start + timedelta(minutes=1)),
        make_quote(price=Decimal("12"), observed_at=start + timedelta(minutes=1)),
        make_quote(
            marketplace="steam",
            currency="USD",
            price=Decimal("13"),
            observed_at=start,
            quote_type=QuoteType.MEDIAN_PRICE,
        ),
        make_quote(
            market_hash_name="M4A1-S | Decimator (Minimal Wear)",
            price=Decimal("99"),
            observed_at=start + timedelta(days=1),
        ),
    ]

    async with StorageService(tmp_path / "quotes.sqlite3") as storage:
        await storage.save_quotes(quotes)
        latest = await storage.get_latest_quotes(Item(market_hash_name=ITEM_NAME))
        filtered = await storage.get_latest_quotes(
            Item(market_hash_name=ITEM_NAME),
            marketplace="steam",
            currency="USD",
            quote_type=QuoteType.MEDIAN_PRICE,
        )

    assert [quote.price for quote in latest] == [
        Decimal("11"),
        Decimal("12"),
        Decimal("13"),
    ]
    assert [quote.price for quote in filtered] == [Decimal("13")]


@pytest.mark.asyncio
async def test_history_filters_time_range_and_orders_equal_timestamps_by_insert(
    tmp_path: Path,
) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    quotes = [
        make_quote(price=Decimal("3"), observed_at=start + timedelta(hours=2)),
        make_quote(price=Decimal("1"), observed_at=start),
        make_quote(price=Decimal("2"), observed_at=start + timedelta(hours=1)),
        make_quote(price=Decimal("2.5"), observed_at=start + timedelta(hours=1)),
        make_quote(
            marketplace="steam",
            price=Decimal("8"),
            observed_at=start + timedelta(hours=1),
        ),
    ]

    async with StorageService(tmp_path / "quotes.sqlite3") as storage:
        await storage.save_quotes(quotes)
        history = await storage.get_price_history(
            Item(market_hash_name=ITEM_NAME),
            marketplace="skinport",
            observed_from=start + timedelta(hours=1),
            observed_to=start + timedelta(hours=3),
            limit=2,
        )

    assert [quote.price for quote in history] == [Decimal("2"), Decimal("2.5")]


@pytest.mark.asyncio
async def test_history_rejects_invalid_range_and_limit(tmp_path: Path) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    naive = datetime(2026, 1, 1)

    async with StorageService(tmp_path / "quotes.sqlite3") as storage:
        with pytest.raises(ValueError, match="limit"):
            await storage.get_price_history(Item(market_hash_name=ITEM_NAME), limit=0)
        with pytest.raises(ValueError, match="timezone-aware"):
            await storage.get_price_history(
                Item(market_hash_name=ITEM_NAME), observed_from=naive
            )
        with pytest.raises(ValueError, match="earlier"):
            await storage.get_price_history(
                Item(market_hash_name=ITEM_NAME),
                observed_from=start,
                observed_to=start,
            )


@pytest.mark.asyncio
async def test_concurrent_operations_use_independent_sessions(tmp_path: Path) -> None:
    async with StorageService(
        tmp_path / "quotes.sqlite3", busy_timeout_seconds=1.0
    ) as storage:
        await asyncio.gather(
            *(
                storage.save_quotes(
                    [
                        make_quote(
                            price=Decimal(index + 1),
                            observed_at=datetime(2026, 1, 1, index, tzinfo=UTC),
                        )
                    ]
                )
                for index in range(10)
            )
        )
        history = await storage.get_price_history(Item(market_hash_name=ITEM_NAME))

    assert len(history) == 10


@pytest.mark.asyncio
async def test_lock_contention_times_out_and_service_recovers(tmp_path: Path) -> None:
    database_path = tmp_path / "quotes.sqlite3"
    async with StorageService(
        database_path, busy_timeout_seconds=0.02
    ) as storage:
        async with aiosqlite.connect(database_path) as locking_connection:
            await locking_connection.execute("BEGIN IMMEDIATE")
            with pytest.raises(StorageBusyError) as error:
                await storage.save_quotes([make_quote()])
            await locking_connection.rollback()

        await storage.save_quotes([make_quote()])
        history = await storage.get_price_history(Item(market_hash_name=ITEM_NAME))

    assert len(history) == 1
    assert error.value.__cause__ is not None


@pytest.mark.asyncio
async def test_lifecycle_misuse_fails_explicitly(tmp_path: Path) -> None:
    storage = StorageService(tmp_path / "quotes.sqlite3")

    with pytest.raises(StorageError, match="not been initialized"):
        await storage.save_quotes([make_quote()])

    await storage.initialize()
    await storage.initialize()
    await storage.close()
    await storage.close()

    with pytest.raises(StorageError, match="already been closed"):
        await storage.get_latest_quotes(Item(market_hash_name=ITEM_NAME))
    with pytest.raises(StorageError, match="already been closed"):
        await storage.initialize()


@pytest.mark.parametrize("timeout", [0, -1])
def test_non_positive_busy_timeout_is_rejected(timeout: float) -> None:
    with pytest.raises(ValueError, match="busy_timeout_seconds"):
        StorageService(":memory:", busy_timeout_seconds=timeout)
