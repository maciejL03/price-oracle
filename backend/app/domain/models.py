from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True)
class CatalogItem:
    market_hash_name: str
    name: str
    image: str | None = None


@dataclass(frozen=True)
class SteamPrice:
    lowest_price: float
    median_price: float | None
    volume: int | None
    currency: str = "GBP"
    source: str = "priceoverview"


@dataclass(frozen=True)
class SkinportPrice:
    min_price: float
    median_price: float | None
    mean_price: float | None
    max_price: float | None
    suggested_price: float | None
    quantity: int | None
    item_page: str | None
    market_page: str | None
    currency: str = "GBP"


class CheaperMarket(str, Enum):
    STEAM = "steam"
    SKINPORT = "skinport"
    SAME = "same"


@dataclass(frozen=True)
class PriceComparison:
    market_hash_name: str
    steam: SteamPrice
    skinport: SkinportPrice
    cheaper: CheaperMarket
    difference: float
    percentage: float
