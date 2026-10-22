"""HTTP schemas, deliberately separate from engine domain models."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from engine.models import QuoteType


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
    cheaper: str
    difference: float = Field(description="Absolute price difference in GBP")
    percentage: float = Field(description="Percentage difference relative to the cheaper price")


class MarketplaceResponse(BaseModel):
    marketplaces: list[str]


class QuoteResponse(BaseModel):
    marketplace: str
    market_hash_name: str
    price: Decimal
    currency: str
    observed_at: datetime
    quote_type: QuoteType
    external_listing_id: str | None


class QuoteCollectionResponse(BaseModel):
    quotes: list[QuoteResponse]


class WatchlistItemRequest(BaseModel):
    market_hash_name: str = Field(min_length=1, max_length=200)
    marketplaces: set[str] = Field(min_length=1)


class WatchlistCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    interval_seconds: float = Field(gt=0, allow_inf_nan=False)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("name must be non-empty without surrounding whitespace")
        return value


class WatchlistUpdateRequest(BaseModel):
    interval_seconds: float = Field(gt=0, allow_inf_nan=False)


class WatchlistItemResponse(BaseModel):
    market_hash_name: str
    marketplaces: list[str]


class WatchlistResponse(BaseModel):
    id: UUID
    name: str
    interval_seconds: float
    items: list[WatchlistItemResponse]


class WatchlistCollectionResponse(BaseModel):
    watchlists: list[WatchlistResponse]


class AlertRuleCreateRequest(BaseModel):
    market_hash_name: str = Field(min_length=1, max_length=200)
    marketplace: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^[a-z0-9][a-z0-9._-]*$",
    )
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    condition: str = Field(pattern=r"^(below|above)$")
    threshold: Decimal = Field(gt=0, allow_inf_nan=False)

    @field_validator("threshold", mode="before")
    @classmethod
    def reject_float_threshold(cls, value: object) -> object:
        if isinstance(value, float):
            raise ValueError("threshold must be provided as a decimal string")
        return value


class AlertRuleResponse(BaseModel):
    id: UUID
    market_hash_name: str
    marketplace: str
    currency: str
    quote_type: QuoteType
    condition: str
    threshold: Decimal


class AlertRuleCollectionResponse(BaseModel):
    rules: list[AlertRuleResponse]


class NotificationDeliveryResponse(BaseModel):
    event_id: UUID
    channel: str
    status: str
    attempts: int
    started_at: datetime
    completed_at: datetime | None
    failure_code: str | None
    failure_detail: str | None
