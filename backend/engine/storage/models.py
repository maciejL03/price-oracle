"""Private persistence mappings for storage-owned tables."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from engine.alerts.models import AlertCondition, AlertEvent, AlertRule
from engine.models import MarketQuote, QuoteType
from engine.notifications.models import (
    DeliveryFailureCode,
    DeliveryStatus,
    NotificationDelivery,
)
from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


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


class AlertRuleRecord(Base):
    """Persisted configuration for one alert rule."""

    __tablename__ = "alert_rules"
    __table_args__ = (
        CheckConstraint("length(marketplace) > 0", name="ck_alert_marketplace_present"),
        CheckConstraint("length(market_hash_name) > 0", name="ck_alert_item_present"),
        CheckConstraint("length(currency) = 3", name="ck_alert_currency_length"),
        CheckConstraint(
            "quote_type = 'lowest_ask'", name="ck_alert_quote_type_supported"
        ),
        CheckConstraint(
            "condition IN ('below', 'above')", name="ck_alert_condition_known"
        ),
        Index(
            "ix_alert_rules_observation",
            "market_hash_name",
            "marketplace",
            "currency",
            "quote_type",
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    market_hash_name: Mapped[str] = mapped_column(Text, nullable=False)
    marketplace: Mapped[str] = mapped_column(Text, nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    quote_type: Mapped[str] = mapped_column(Text, nullable=False)
    condition: Mapped[str] = mapped_column(Text, nullable=False)
    threshold: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)

    @classmethod
    def from_domain(cls, rule: AlertRule) -> "AlertRuleRecord":
        return cls(
            id=str(rule.id),
            market_hash_name=rule.item.market_hash_name,
            marketplace=rule.marketplace,
            currency=rule.currency,
            quote_type=rule.quote_type.value,
            condition=rule.condition.value,
            threshold=rule.threshold,
        )

    def to_domain_values(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "item": {"market_hash_name": self.market_hash_name},
            "marketplace": self.marketplace,
            "currency": self.currency,
            "quote_type": QuoteType(self.quote_type),
            "condition": AlertCondition(self.condition),
            "threshold": self.threshold,
        }


class AlertRuleStateRecord(Base):
    """Latest durable edge-trigger state for one alert rule."""

    __tablename__ = "alert_rule_states"

    rule_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("alert_rules.id", ondelete="CASCADE"),
        primary_key=True,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_observed_at: Mapped[datetime | None] = mapped_column(
        UTCDateTimeText(), nullable=True
    )


class AlertEventRecord(Base):
    """Append-only snapshot of a committed alert transition."""

    __tablename__ = "alert_events"
    __table_args__ = (
        CheckConstraint(
            "condition IN ('below', 'above')", name="ck_alert_event_condition_known"
        ),
        Index("ix_alert_events_rule", "rule_id", "triggered_at"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    # Deliberately not a foreign key: deleting a rule must not erase its events.
    rule_id: Mapped[str] = mapped_column(Text, nullable=False)
    marketplace: Mapped[str] = mapped_column(Text, nullable=False)
    market_hash_name: Mapped[str] = mapped_column(Text, nullable=False)
    price: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTimeText(), nullable=False)
    quote_type: Mapped[str] = mapped_column(Text, nullable=False)
    external_listing_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    condition: Mapped[str] = mapped_column(Text, nullable=False)
    threshold: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)
    triggered_at: Mapped[datetime] = mapped_column(UTCDateTimeText(), nullable=False)

    @classmethod
    def from_domain(cls, event: AlertEvent) -> "AlertEventRecord":
        quote = event.quote
        return cls(
            id=str(event.id),
            rule_id=str(event.rule_id),
            marketplace=quote.marketplace,
            market_hash_name=quote.market_hash_name,
            price=quote.price,
            currency=quote.currency,
            observed_at=quote.observed_at,
            quote_type=quote.quote_type.value,
            external_listing_id=quote.external_listing_id,
            condition=event.condition.value,
            threshold=event.threshold,
            triggered_at=event.triggered_at,
        )

    def to_domain_values(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "rule_id": self.rule_id,
            "quote": {
                "marketplace": self.marketplace,
                "market_hash_name": self.market_hash_name,
                "price": self.price,
                "currency": self.currency,
                "observed_at": self.observed_at,
                "quote_type": QuoteType(self.quote_type),
                "external_listing_id": self.external_listing_id,
            },
            "condition": AlertCondition(self.condition),
            "threshold": self.threshold,
            "triggered_at": self.triggered_at,
        }


class NotificationDeliveryRecord(Base):
    """Durable claim and outcome for delivery of one alert event."""

    __tablename__ = "notification_deliveries"
    __table_args__ = (
        CheckConstraint(
            "channel = 'discord'", name="ck_notification_channel_supported"
        ),
        CheckConstraint(
            "status IN ('pending', 'succeeded', 'failed')",
            name="ck_notification_status_known",
        ),
        CheckConstraint("attempts >= 0", name="ck_notification_attempts_non_negative"),
        CheckConstraint(
            "failure_code IS NULL OR failure_code IN "
            "('network_error', 'timeout', 'rate_limited', 'provider_error')",
            name="ck_notification_failure_code_known",
        ),
        Index("ix_notification_deliveries_status", "status", "started_at"),
    )

    event_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("alert_events.id"),
        primary_key=True,
    )
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTimeText(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(
        UTCDateTimeText(), nullable=True
    )
    failure_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    @classmethod
    def from_domain(
        cls, delivery: NotificationDelivery
    ) -> "NotificationDeliveryRecord":
        return cls(
            event_id=str(delivery.event_id),
            channel=delivery.channel,
            status=delivery.status.value,
            attempts=delivery.attempts,
            started_at=delivery.started_at,
            completed_at=delivery.completed_at,
            failure_code=(
                None if delivery.failure_code is None else delivery.failure_code.value
            ),
            failure_detail=delivery.failure_detail,
        )

    def to_domain_values(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "channel": self.channel,
            "status": DeliveryStatus(self.status),
            "attempts": self.attempts,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "failure_code": (
                None
                if self.failure_code is None
                else DeliveryFailureCode(self.failure_code)
            ),
            "failure_detail": self.failure_detail,
        }
