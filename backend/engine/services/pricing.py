from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum
from re import fullmatch
from typing import Iterable

from pydantic import AwareDatetime, BaseModel, ConfigDict

from engine.models import Item, MarketQuote, QuoteType


class QuoteExclusionReason(str, Enum):
    """Why a quote was not safe to include in a comparison."""

    DIFFERENT_ITEM = "different_item"
    DIFFERENT_CURRENCY = "different_currency"
    DIFFERENT_QUOTE_TYPE = "different_quote_type"
    STALE = "stale"
    OBSERVED_IN_FUTURE = "observed_in_future"
    DUPLICATE = "duplicate"


class ExcludedQuote(BaseModel):
    """A rejected quote together with its first incompatibility."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    quote: MarketQuote
    reason: QuoteExclusionReason


class PriceComparison(BaseModel):
    """A deterministic, pre-fee ranking of mutually comparable quotes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    item: Item
    currency: str
    quote_type: QuoteType
    as_of: AwareDatetime
    max_quote_age: timedelta
    quotes: tuple[MarketQuote, ...]
    excluded_quotes: tuple[ExcludedQuote, ...]
    lowest_price: Decimal | None
    highest_price: Decimal | None
    spread: Decimal | None

    @property
    def best_quote(self) -> MarketQuote | None:
        """Return the lowest-priced eligible quote, if one exists."""

        return self.quotes[0] if self.quotes else None


class PricingService:
    """Compare normalized quotes without fetching data or applying hidden rules."""

    def __init__(self, *, max_quote_age: timedelta) -> None:
        if max_quote_age <= timedelta(0):
            raise ValueError("max_quote_age must be greater than zero")
        self._max_quote_age = max_quote_age

    def compare(
        self,
        item: Item,
        quotes: Iterable[MarketQuote],
        *,
        currency: str,
        quote_type: QuoteType,
        as_of: datetime,
    ) -> PriceComparison:
        """Rank compatible quotes from lowest to highest price.

        Compatibility is exact: item identity, currency, and quote type must all
        match the requested comparison. Quotes outside the freshness window are
        reported as exclusions. Prices are pre-fee and are never converted or
        rounded.
        """

        self._validate_request(item, quotes, currency, quote_type, as_of)
        as_of_utc = as_of.astimezone(UTC)
        eligible: list[MarketQuote] = []
        excluded: list[ExcludedQuote] = []
        seen: set[MarketQuote] = set()

        for quote in quotes:
            if not isinstance(quote, MarketQuote):
                raise TypeError("quotes must contain only MarketQuote objects")

            reason = self._exclusion_reason(
                quote,
                item=item,
                currency=currency,
                quote_type=quote_type,
                as_of=as_of_utc,
            )
            if reason is None and quote in seen:
                reason = QuoteExclusionReason.DUPLICATE

            if reason is not None:
                excluded.append(ExcludedQuote(quote=quote, reason=reason))
                continue

            seen.add(quote)
            eligible.append(quote)

        ordered = tuple(sorted(eligible, key=self._sort_key))
        lowest_price = ordered[0].price if ordered else None
        highest_price = ordered[-1].price if ordered else None
        spread = highest_price - lowest_price if ordered else None

        return PriceComparison(
            item=item,
            currency=currency,
            quote_type=quote_type,
            as_of=as_of_utc,
            max_quote_age=self._max_quote_age,
            quotes=ordered,
            excluded_quotes=tuple(excluded),
            lowest_price=lowest_price,
            highest_price=highest_price,
            spread=spread,
        )

    @staticmethod
    def _validate_request(
        item: Item,
        quotes: Iterable[MarketQuote],
        currency: str,
        quote_type: QuoteType,
        as_of: datetime,
    ) -> None:
        if not isinstance(item, Item):
            raise TypeError("item must be an Item")
        if isinstance(quotes, (str, bytes)) or not isinstance(quotes, Iterable):
            raise TypeError("quotes must be an iterable of MarketQuote objects")
        if not isinstance(currency, str) or fullmatch(r"[A-Z]{3}", currency) is None:
            raise ValueError("currency must be a three-letter uppercase code")
        if not isinstance(quote_type, QuoteType):
            raise TypeError("quote_type must be a QuoteType")
        if not isinstance(as_of, datetime):
            raise TypeError("as_of must be a datetime")
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")

    def _exclusion_reason(
        self,
        quote: MarketQuote,
        *,
        item: Item,
        currency: str,
        quote_type: QuoteType,
        as_of: datetime,
    ) -> QuoteExclusionReason | None:
        if quote.market_hash_name != item.market_hash_name:
            return QuoteExclusionReason.DIFFERENT_ITEM
        if quote.currency != currency:
            return QuoteExclusionReason.DIFFERENT_CURRENCY
        if quote.quote_type is not quote_type:
            return QuoteExclusionReason.DIFFERENT_QUOTE_TYPE

        age = as_of - quote.observed_at
        if age < timedelta(0):
            return QuoteExclusionReason.OBSERVED_IN_FUTURE
        if age > self._max_quote_age:
            return QuoteExclusionReason.STALE
        return None

    @staticmethod
    def _sort_key(quote: MarketQuote) -> tuple[Decimal, str, datetime, str]:
        return (
            quote.price,
            quote.marketplace,
            quote.observed_at,
            quote.external_listing_id or "",
        )
