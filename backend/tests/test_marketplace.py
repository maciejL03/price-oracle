import inspect
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from engine.marketplaces import Marketplace
from engine.models import Item, MarketQuote, QuoteType


class FakeMarketplace:
    name = "fake"

    def __init__(self, quotes: list[MarketQuote] | None = None) -> None:
        self._quotes = quotes or []

    async def fetch(self, item: Item) -> list[MarketQuote]:
        return [quote for quote in self._quotes if quote.market_hash_name == item.market_hash_name]


def make_quote() -> MarketQuote:
    return MarketQuote(
        marketplace="fake",
        market_hash_name="AK-47 | Redline (Field-Tested)",
        price=Decimal("30.15"),
        currency="GBP",
        observed_at=datetime(2026, 1, 2, 12, 30, tzinfo=UTC),
        quote_type=QuoteType.LOWEST_ASK,
    )


def test_concrete_marketplace_satisfies_structural_contract() -> None:
    marketplace = FakeMarketplace()

    assert isinstance(marketplace, Marketplace)
    assert marketplace.name == "fake"
    assert inspect.iscoroutinefunction(marketplace.fetch)


@pytest.mark.asyncio
async def test_marketplace_fetch_returns_normalized_quotes() -> None:
    item = Item(market_hash_name="AK-47 | Redline (Field-Tested)")
    quote = make_quote()

    result = await FakeMarketplace([quote]).fetch(item)

    assert result == [quote]
    assert all(isinstance(value, MarketQuote) for value in result)


@pytest.mark.asyncio
async def test_marketplace_fetch_can_return_an_empty_result() -> None:
    item = Item(market_hash_name="AK-47 | Redline (Field-Tested)")

    assert await FakeMarketplace().fetch(item) == []


def test_incomplete_implementation_does_not_satisfy_contract() -> None:
    class MissingFetch:
        name = "incomplete"

    assert not isinstance(MissingFetch(), Marketplace)
