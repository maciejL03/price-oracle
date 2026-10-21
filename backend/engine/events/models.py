"""Small immutable event values emitted by engine components."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from engine.models import MarketQuote


@dataclass(frozen=True, slots=True, kw_only=True)
class EngineEvent:
    """Base value for an in-process event with an explicit UTC occurrence time."""

    occurred_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.occurred_at, datetime):
            raise TypeError("occurred_at must be a datetime")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        object.__setattr__(self, "occurred_at", self.occurred_at.astimezone(UTC))


@dataclass(frozen=True, slots=True, kw_only=True)
class MarketQuoteStored(EngineEvent):
    """A normalized quote whose containing storage transaction has committed."""

    quote: MarketQuote

    def __post_init__(self) -> None:
        EngineEvent.__post_init__(self)
        if not isinstance(self.quote, MarketQuote):
            raise TypeError("quote must be a MarketQuote")
