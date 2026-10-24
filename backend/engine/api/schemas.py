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


class SystemStatusResponse(BaseModel):
    status: str
    operating_mode: str
    data_kind: str
    marketplaces: list[str]
    notifications: str


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


class StoredComparisonResponse(BaseModel):
    market_hash_name: str
    currency: str
    quote_type: QuoteType
    simulated: bool
    quotes: list[QuoteResponse]
    lowest_price: Decimal | None
    highest_price: Decimal | None
    spread: Decimal | None
    excluded_count: int


class WatchlistItemRequest(BaseModel):
    market_hash_name: str = Field(min_length=1, max_length=200)
    marketplaces: set[str] = Field(min_length=1)


class WatchlistCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    interval_seconds: float = Field(gt=0)


class WatchlistUpdateRequest(BaseModel):
    interval_seconds: float = Field(gt=0)


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
    marketplace: str = Field(min_length=1, max_length=100)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    condition: str = Field(pattern=r"^(below|above)$")
    threshold: Decimal = Field(gt=0)

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


class AlertEventResponse(BaseModel):
    id: UUID
    rule_id: UUID
    quote: QuoteResponse
    condition: str
    threshold: Decimal
    triggered_at: datetime


class AlertEventCollectionResponse(BaseModel):
    events: list[AlertEventResponse]


class NotificationDeliveryResponse(BaseModel):
    event_id: UUID
    channel: str
    status: str
    attempts: int
    started_at: datetime
    completed_at: datetime | None
    failure_code: str | None
    failure_detail: str | None
