from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Annotated, Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from .item import MarketHashName


class QuoteType(str, Enum):
    """The market observation represented by a quote."""

    INDIVIDUAL_LISTING = "individual_listing"
    LOWEST_ASK = "lowest_ask"
    MEDIAN_PRICE = "median_price"


MarketplaceId = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")]
CurrencyCode = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
PositivePrice = Annotated[Decimal, Field(gt=0, allow_inf_nan=False)]


class MarketQuote(BaseModel):
    """A normalized, currency-explicit price observation from a marketplace."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    marketplace: MarketplaceId
    market_hash_name: MarketHashName
    price: PositivePrice
    currency: CurrencyCode
    observed_at: AwareDatetime
    quote_type: QuoteType
    external_listing_id: str | None = None

    @field_validator("price", mode="before")
    @classmethod
    def reject_float_price(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("price must not be provided as a float")
        return value

    @field_validator("observed_at")
    @classmethod
    def normalize_observed_at(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @field_validator("external_listing_id")
    @classmethod
    def validate_external_listing_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("external_listing_id must not be empty")
        if value != value.strip():
            raise ValueError(
                "external_listing_id must not have leading or trailing whitespace"
            )
        return value
