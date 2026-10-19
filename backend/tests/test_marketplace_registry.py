from datetime import UTC, datetime
from decimal import Decimal

import pytest

from engine.errors import MarketplaceRegistrationError, UnknownMarketplaceError
from engine.marketplaces import MarketplaceRegistry
from engine.models import Item, MarketQuote, QuoteType


class FakeMarketplace:
    name = "example"

    async def fetch(self, item: Item) -> list[MarketQuote]:
        return [
            MarketQuote(
                marketplace=self.name,
                market_hash_name=item.market_hash_name,
                price=Decimal("1.23"),
                currency="USD",
                observed_at=datetime(2026, 1, 1, tzinfo=UTC),
                quote_type=QuoteType.LOWEST_ASK,
            )
        ]


def test_registry_registers_and_returns_structural_marketplace() -> None:
    provider = FakeMarketplace()
    registry = MarketplaceRegistry()

    registry.register(provider)

    assert registry.get("example") is provider
    assert registry.names == ("example",)


def test_registry_rejects_duplicate_name() -> None:
    registry = MarketplaceRegistry()
    registry.register(FakeMarketplace())

    with pytest.raises(MarketplaceRegistrationError, match="already registered"):
        registry.register(FakeMarketplace())


@pytest.mark.parametrize("name", ["", "CSFloat", "cs float", " csfloat"])
def test_registry_rejects_invalid_name(name: str) -> None:
    provider = FakeMarketplace()
    provider.name = name

    with pytest.raises(MarketplaceRegistrationError, match="name"):
        MarketplaceRegistry().register(provider)


def test_registry_rejects_provider_without_fetch() -> None:
    class InvalidProvider:
        name = "invalid"

    with pytest.raises(MarketplaceRegistrationError, match="fetch"):
        MarketplaceRegistry().register(InvalidProvider())  # type: ignore[arg-type]


def test_registry_rejects_synchronous_fetch() -> None:
    class InvalidProvider:
        name = "invalid"

        def fetch(self, item: Item) -> list[MarketQuote]:
            return []

    with pytest.raises(MarketplaceRegistrationError, match="async fetch"):
        MarketplaceRegistry().register(InvalidProvider())  # type: ignore[arg-type]


def test_registry_rejects_unknown_marketplace() -> None:
    with pytest.raises(UnknownMarketplaceError, match="not registered"):
        MarketplaceRegistry().get("missing")
