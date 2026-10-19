"""Private persistence mappings for storage-owned tables."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, Index, Integer, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from engine.models import MarketQuote, QuoteType


class Base(DeclarativeBase):
    pass


class DecimalText(TypeDecorator[Decimal]):
    """Persist Decimal values without SQLite floating-point conversion."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Decimal | None, dialect: Any) -> str | None:
        del dialect
        if value is None:
            return None
        if not isinstance(value, Decimal):
            raise TypeError("stored prices must be Decimal values")
        return str(value)

    def process_result_value(self, value: str | None, dialect: Any) -> Decimal | None:
        del dialect
        return None if value is None else Decimal(value)


class UTCDateTimeText(TypeDecorator[datetime]):
    """Store UTC datetimes as fixed-width, lexically sortable text."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> str | None:
        del dialect
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("stored timestamps must be timezone-aware")
        return value.astimezone(UTC).isoformat(timespec="microseconds")

    def process_result_value(self, value: str | None, dialect: Any) -> datetime | None:
        del dialect
        if value is None:
            return None
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("stored timestamp is not timezone-aware")
        return parsed.astimezone(UTC)


class QuoteRecord(Base):
    """Append-only representation of one MarketQuote observation."""

    __tablename__ = "market_quotes"
    __table_args__ = (
        CheckConstraint("length(marketplace) > 0", name="ck_quote_marketplace_present"),
        CheckConstraint("length(market_hash_name) > 0", name="ck_quote_item_present"),
        CheckConstraint("length(currency) = 3", name="ck_quote_currency_length"),
        CheckConstraint(
            "quote_type IN ('individual_listing', 'lowest_ask', 'median_price')",
            name="ck_quote_type_known",
        ),
        Index(
            "ix_market_quotes_latest",
            "market_hash_name",
            "marketplace",
            "currency",
            "quote_type",
            "observed_at",
        ),
        Index("ix_market_quotes_history", "market_hash_name", "observed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    marketplace: Mapped[str] = mapped_column(Text, nullable=False)
    market_hash_name: Mapped[str] = mapped_column(Text, nullable=False)
    price: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTimeText(), nullable=False)
    quote_type: Mapped[str] = mapped_column(Text, nullable=False)
    external_listing_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    @classmethod
    def from_domain(cls, quote: MarketQuote) -> "QuoteRecord":
        return cls(
            marketplace=quote.marketplace,
            market_hash_name=quote.market_hash_name,
            price=quote.price,
            currency=quote.currency,
            observed_at=quote.observed_at,
            quote_type=quote.quote_type.value,
            external_listing_id=quote.external_listing_id,
        )

    def to_domain_values(self) -> dict[str, Any]:
        return {
            "marketplace": self.marketplace,
            "market_hash_name": self.market_hash_name,
            "price": self.price,
            "currency": self.currency,
            "observed_at": self.observed_at,
            "quote_type": QuoteType(self.quote_type),
            "external_listing_id": self.external_listing_id,
        }
