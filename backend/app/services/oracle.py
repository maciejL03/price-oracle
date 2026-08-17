from app.domain.comparison import compare_prices
from app.domain.models import CatalogItem, PriceComparison
from app.ports.marketplaces import ItemCatalog, SkinportMarket, SteamMarket


class ItemSearchService:
    def __init__(self, catalog: ItemCatalog) -> None:
        self._catalog = catalog

    async def search(self, query: str, limit: int = 20) -> list[CatalogItem]:
        return await self._catalog.search(query, limit=limit)

    async def resolve(self, market_hash_name: str) -> CatalogItem | None:
        return await self._catalog.get_by_market_hash_name(market_hash_name)


class PriceOracleService:
    def __init__(
        self,
        steam: SteamMarket,
        skinport: SkinportMarket,
    ) -> None:
        self._steam = steam
        self._skinport = skinport

    async def compare(self, market_hash_name: str) -> PriceComparison:
        steam_price = await self._steam.get_price(market_hash_name)
        skinport_price = await self._skinport.get_price(market_hash_name)

        if skinport_price is None:
            raise ValueError(f"'{market_hash_name}' was not found on Skinport.")

        if skinport_price.min_price is None:
            raise ValueError(f"Skinport has no active listings for '{market_hash_name}'.")

        return compare_prices(market_hash_name, steam_price, skinport_price)
