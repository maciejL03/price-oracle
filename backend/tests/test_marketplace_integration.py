from datetime import UTC, datetime
from decimal import Decimal

import pytest

from engine.marketplaces import MarketplaceRegistry
from engine.models import Item, MarketQuote, QuoteType


class QuoteMarketplace:
    def __init__(self, name: str, price: str, quote_type: QuoteType) -> None:
        self._name = name
        self._price = price
        self._quote_type = quote_type

    @property
    def name(self) -> str:
        return self._name

    async def fetch(self, item: Item) -> list[MarketQuote]:
        return [
            MarketQuote(
                marketplace=self.name,
                market_hash_name=item.market_hash_name,
                price=Decimal(self._price),
                currency="GBP",
                observed_at=datetime(2026, 1, 2, 12, 30, tzinfo=UTC),
                quote_type=self._quote_type,
            )
        ]


class FailingMarketplace:
    name = "failing"

    async def fetch(self, item: Item) -> list[MarketQuote]:
        raise RuntimeError("simulated provider failure")


@pytest.mark.asyncio
async def test_registered_marketplaces_produce_consistent_domain_quotes() -> None:
    registry = MarketplaceRegistry()
    registry.register(QuoteMarketplace("steam", "30.15", QuoteType.LOWEST_ASK))
    registry.register(QuoteMarketplace("csfloat", "28.40", QuoteType.INDIVIDUAL_LISTING))
    item = Item(market_hash_name="AK-47 | Redline (Field-Tested)")

    steam_quotes = await registry.get("steam").fetch(item)
    csfloat_quotes = await registry.get("csfloat").fetch(item)

    assert [quote.marketplace for quote in steam_quotes + csfloat_quotes] == [
        "steam",
        "csfloat",
    ]
    assert all(isinstance(quote, MarketQuote) for quote in steam_quotes + csfloat_quotes)
    assert {quote.market_hash_name for quote in steam_quotes + csfloat_quotes} == {
        item.market_hash_name
    }


@pytest.mark.asyncio
async def test_marketplace_failure_does_not_corrupt_registry_state() -> None:
    registry = MarketplaceRegistry()
    working = QuoteMarketplace("working", "25.00", QuoteType.LOWEST_ASK)
    failing = FailingMarketplace()
    registry.register(working)
    registry.register(failing)
    item = Item(market_hash_name="AK-47 | Redline (Field-Tested)")

    with pytest.raises(RuntimeError, match="simulated provider failure"):
        await registry.get("failing").fetch(item)

    assert registry.list() == (working, failing)
    assert (await registry.get("working").fetch(item))[0].marketplace == "working"


def test_registry_requires_no_service_or_client_dependencies() -> None:
    registry = MarketplaceRegistry()

    assert registry.list() == ()
