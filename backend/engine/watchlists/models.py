"""Immutable domain models for persistent watchlist configuration."""

from __future__ import annotations

from typing import Annotated, Any, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from engine.models import Item

IntervalSeconds = Annotated[float, Field(gt=0, allow_inf_nan=False)]


class WatchlistItem(BaseModel):
    """One item and the marketplaces from which it should be collected."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    item: Item
    marketplaces: frozenset[str]

    @field_validator("marketplaces")
    @classmethod
    def validate_marketplaces(cls, value: frozenset[str]) -> frozenset[str]:
        if not value:
            raise ValueError("marketplaces must not be empty")
        for marketplace in value:
            if not isinstance(marketplace, str) or not marketplace:
                raise ValueError("marketplace identifiers must be non-empty strings")
            if marketplace != marketplace.strip():
                raise ValueError(
                    "marketplace identifiers must not have leading or trailing whitespace"
                )
        return value


class Watchlist(BaseModel):
    """Persistent configuration for a named collection of monitored items."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    name: str
    interval_seconds: IntervalSeconds
    items: tuple[WatchlistItem, ...] = ()

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must not be empty")
        if value != value.strip():
            raise ValueError("name must not have leading or trailing whitespace")
        return value

    @field_validator("interval_seconds", mode="before")
    @classmethod
    def reject_boolean_interval(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise TypeError("interval_seconds must be a positive number")
        return value

    @model_validator(mode="after")
    def reject_duplicate_items(self) -> Self:
        names = [entry.item.market_hash_name for entry in self.items]
        if len(names) != len(set(names)):
            raise ValueError("a watchlist cannot contain duplicate items")
        return self
