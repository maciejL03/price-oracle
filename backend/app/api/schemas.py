from pydantic import BaseModel, Field

from app.domain.models import CheaperMarket


class CatalogItemSchema(BaseModel):
    market_hash_name: str
    name: str
    image: str | None = None


class SearchResponse(BaseModel):
    items: list[CatalogItemSchema]


class SteamPriceSchema(BaseModel):
    lowest_price: float
    median_price: float | None
    volume: int | None
    currency: str
    source: str


class SkinportPriceSchema(BaseModel):
    min_price: float
    median_price: float | None
    mean_price: float | None
    max_price: float | None
    suggested_price: float | None
    quantity: int | None
    item_page: str | None
    market_page: str | None
    currency: str


class ComparisonResponse(BaseModel):
    market_hash_name: str
    item_name: str
    item_image: str | None
    steam: SteamPriceSchema
    skinport: SkinportPriceSchema
    cheaper: CheaperMarket
    difference: float = Field(description="Absolute price difference in GBP")
    percentage: float = Field(description="Percentage difference relative to the cheaper price")


class ErrorResponse(BaseModel):
    detail: str
