"""Domain models for threshold-based market alerts."""

from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Annotated, Any
from uuid import UUID

from engine.models import Item, MarketQuote, QuoteType
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

MarketplaceId = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")]
CurrencyCode = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
PositiveThreshold = Annotated[Decimal, Field(gt=0, allow_inf_nan=False)]


class AlertCondition(str, Enum):
    """A strict comparison between an observed price and a threshold."""

    BELOW = "below"
    ABOVE = "above"


class AlertRule(BaseModel):
    """One marketplace-specific threshold rule for an exact item."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    item: Item
    marketplace: MarketplaceId
    currency: CurrencyCode
    quote_type: QuoteType
    condition: AlertCondition
    threshold: PositiveThreshold

    @field_validator("threshold", mode="before")
    @classmethod
    def reject_float_threshold(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("threshold must not be provided as a float")
        return value

    @field_validator("quote_type")
    @classmethod
    def require_supported_quote_type(cls, value: QuoteType) -> QuoteType:
        if value is not QuoteType.LOWEST_ASK:
            raise ValueError("only lowest_ask alert rules are supported")
        return value


class AlertEvent(BaseModel):
    """A durable snapshot of an inactive-to-active rule transition."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    rule_id: UUID
    quote: MarketQuote
    condition: AlertCondition
    threshold: PositiveThreshold
    triggered_at: AwareDatetime

    @field_validator("threshold", mode="before")
    @classmethod
    def reject_float_threshold(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("threshold must not be provided as a float")
        return value

    @field_validator("triggered_at")
    @classmethod
    def normalize_triggered_at(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)
