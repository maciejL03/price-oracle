"""Engine-owned compatibility comparison orchestration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from engine.catalog import CatalogItem, ItemCatalog
from engine.marketplaces import SkinportMarketplace, SkinportSnapshot, SteamMarketplace, SteamSnapshot
from engine.models import Item, QuoteType

from .pricing import PriceComparison, PricingService


class ComparisonNotFoundError(LookupError):
    """A requested item or market observation was unavailable."""


@dataclass(frozen=True, slots=True)
class MarketplaceComparison:
    """The exact information required by the retained website contract."""

    item: CatalogItem
    steam: SteamSnapshot
    skinport: SkinportSnapshot
    ranking: PriceComparison

    @property
    def cheaper(self) -> str:
        best = self.ranking.best_quote
        if best is None:  # defensive: both snapshots above are always eligible
            raise RuntimeError("comparison has no eligible quotes")
        if self.ranking.spread == Decimal("0"):
            return "same"
        return best.marketplace

    @property
    def percentage(self) -> Decimal:
        lowest = self.ranking.lowest_price
        spread = self.ranking.spread
        if lowest is None or spread is None or lowest == 0:
            return Decimal("0")
        return (spread / lowest * Decimal("100")).quantize(Decimal("0.01"))


class MarketplaceComparisonService:
    """Fetch two engine providers and rank their normalized lowest-ask quotes."""

    def __init__(
        self,
        catalog: ItemCatalog,
        steam: SteamMarketplace,
        skinport: SkinportMarketplace,
        pricing: PricingService,
        *,
        currency: str,
    ) -> None:
        self._catalog = catalog
        self._steam = steam
        self._skinport = skinport
        self._pricing = pricing
        self._currency = currency

    async def search(self, query: str, *, limit: int) -> list[CatalogItem]:
        return await self._catalog.search(query, limit=limit)

    async def compare(self, market_hash_name: str) -> MarketplaceComparison:
        item = await self._catalog.resolve(market_hash_name)
        if item is None:
            raise ComparisonNotFoundError(f"Unknown item: {market_hash_name}")
        domain_item = Item(market_hash_name=item.market_hash_name)
        steam, skinport = await asyncio.gather(
            self._steam.fetch_snapshot(domain_item),
            self._skinport.fetch_snapshot(domain_item),
        )
        if skinport is None:
            raise ComparisonNotFoundError(
                f"'{market_hash_name}' was not found on Skinport."
            )
        ranking = self._pricing.compare(
            domain_item,
            (steam.quote, skinport.quote),
            currency=self._currency,
            quote_type=QuoteType.LOWEST_ASK,
            as_of=datetime.now(UTC),
        )
        return MarketplaceComparison(
            item=item,
            steam=steam,
            skinport=skinport,
            ranking=ranking,
        )
