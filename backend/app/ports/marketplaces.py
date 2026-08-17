from abc import ABC, abstractmethod

from app.domain.models import CatalogItem, SkinportPrice, SteamPrice


class ItemCatalog(ABC):
    @abstractmethod
    async def search(self, query: str, limit: int = 20) -> list[CatalogItem]:
        ...

    @abstractmethod
    async def get_by_market_hash_name(self, market_hash_name: str) -> CatalogItem | None:
        ...


class SteamMarket(ABC):
    @abstractmethod
    async def get_price(self, market_hash_name: str) -> SteamPrice:
        ...


class SkinportMarket(ABC):
    @abstractmethod
    async def get_price(self, market_hash_name: str) -> SkinportPrice | None:
        ...
