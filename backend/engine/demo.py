"""Deterministic, network-free data used by the first-run demo mode."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from engine.alerts import AlertCondition, AlertRule
from engine.catalog import CatalogItem, ItemCatalog
from engine.errors import MarketplaceResponseError
from engine.models import Item, MarketQuote, QuoteType
from engine.storage import StorageService
from engine.watchlists import Watchlist, WatchlistItem

DEMO_ITEM_NAME = "AK-47 | Redline (Field-Tested)"
DEMO_WATCHLIST_ID = UUID("00000000-0000-4000-8000-000000000001")
DEMO_ALERT_ID = UUID("00000000-0000-4000-8000-000000000002")

DEMO_ITEMS = (
    CatalogItem(DEMO_ITEM_NAME, "AK-47 | Redline"),
    CatalogItem("StatTrak™ AK-47 | Redline (Field-Tested)", "StatTrak™ AK-47 | Redline"),
    CatalogItem("Souvenir AWP | Pink DDPAT (Field-Tested)", "Souvenir AWP | Pink DDPAT"),
    CatalogItem("Dreams & Nightmares Case", "Dreams & Nightmares Case"),
)

_DEMO_PRICES: dict[str, tuple[Decimal, Decimal]] = {
    DEMO_ITEMS[0].market_hash_name: (Decimal("12.40"), Decimal("13.15")),
    DEMO_ITEMS[1].market_hash_name: (Decimal("31.50"), Decimal("33.00")),
    DEMO_ITEMS[2].market_hash_name: (Decimal("75.00"), Decimal("78.25")),
    DEMO_ITEMS[3].market_hash_name: (Decimal("0.80"), Decimal("0.85")),
}


class DemoItemCatalog(ItemCatalog):
    """Small bundled catalog which performs no I/O."""

    async def search(self, query: str, *, limit: int) -> list[CatalogItem]:
        normalized = query.strip().casefold()
        if not normalized:
            return []
        return [
            item for item in DEMO_ITEMS if normalized in item.market_hash_name.casefold()
        ][:limit]

    async def resolve(self, market_hash_name: str) -> CatalogItem | None:
        return next(
            (item for item in DEMO_ITEMS if item.market_hash_name == market_hash_name),
            None,
        )


class DemoMarketplace:
    """Return clearly-labelled simulated lowest asks for exact item variants."""

    def __init__(self, name: str, price_index: int) -> None:
        if name not in {"demo-market-a", "demo-market-b"}:
            raise ValueError("unsupported demo marketplace name")
        if price_index not in {0, 1}:
            raise ValueError("demo price index must be 0 or 1")
        self.name = name
        self._price_index = price_index

    async def fetch(self, item: Item) -> list[MarketQuote]:
        prices = _DEMO_PRICES.get(item.market_hash_name)
        if prices is None:
            raise MarketplaceResponseError(
                f"Demo fixture has no simulated data for '{item.market_hash_name}'"
            )
        return [
            MarketQuote(
                marketplace=self.name,
                market_hash_name=item.market_hash_name,
                price=prices[self._price_index],
                currency="GBP",
                observed_at=datetime.now(UTC),
                quote_type=QuoteType.LOWEST_ASK,
            )
        ]


def create_demo_marketplaces() -> tuple[DemoMarketplace, DemoMarketplace]:
    return DemoMarketplace("demo-market-a", 0), DemoMarketplace("demo-market-b", 1)


async def seed_demo_configuration(storage: StorageService) -> None:
    """Create idempotent monitoring configuration; collection remains job-driven."""
    if await storage.get_watchlist(DEMO_WATCHLIST_ID) is None:
        await storage.save_watchlist(
            Watchlist(
                id=DEMO_WATCHLIST_ID,
                name="Offline demo — simulated prices",
                interval_seconds=30.0,
                items=(
                    WatchlistItem(
                        item=Item(market_hash_name=DEMO_ITEM_NAME),
                        marketplaces=frozenset({"demo-market-a", "demo-market-b"}),
                    ),
                ),
            )
        )

    rules = await storage.list_alert_rules()
    if not any(rule.id == DEMO_ALERT_ID for rule in rules):
        await storage.save_alert_rule(
            AlertRule(
                id=DEMO_ALERT_ID,
                item=Item(market_hash_name=DEMO_ITEM_NAME),
                marketplace="demo-market-a",
                currency="GBP",
                quote_type=QuoteType.LOWEST_ASK,
                condition=AlertCondition.BELOW,
                threshold=Decimal("13.00"),
            )
        )
