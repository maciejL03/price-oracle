"""Application service for durable, edge-triggered price alerts."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from engine.models import MarketQuote, QuoteType

from .models import AlertCondition, AlertEvent, AlertRule

EventIdFactory = Callable[[], UUID]


class AlertStorage(Protocol):
    """Persistence operations required by :class:`AlertService`."""

    async def save_alert_rule(self, rule: AlertRule) -> None: ...

    async def list_alert_rules(
        self,
        *,
        market_hash_name: str | None = None,
        marketplace: str | None = None,
        currency: str | None = None,
        quote_type: QuoteType | None = None,
    ) -> list[AlertRule]: ...

    async def delete_alert_rule(self, rule_id: UUID) -> bool: ...

    async def commit_alert_evaluation(
        self,
        rule_id: UUID,
        *,
        observation: MarketQuote,
        condition_active: bool,
        candidate_event: AlertEvent,
    ) -> AlertEvent | None: ...


class AlertService:
    """Evaluate fresh lowest-ask observations against persisted alert rules."""

    def __init__(
        self,
        storage: AlertStorage,
        *,
        max_quote_age: timedelta,
        _event_id_factory: EventIdFactory = uuid4,
    ) -> None:
        if max_quote_age <= timedelta(0):
            raise ValueError("max_quote_age must be greater than zero")
        self._storage = storage
        self._max_quote_age = max_quote_age
        self._event_id_factory = _event_id_factory

    async def create_rule(self, rule: AlertRule) -> None:
        """Persist a validated alert rule and its initially inactive state."""
        if not isinstance(rule, AlertRule):
            raise TypeError("rule must be an AlertRule")
        await self._storage.save_alert_rule(rule)

    async def list_rules(self) -> tuple[AlertRule, ...]:
        """Return all configured rules in storage-defined deterministic order."""
        return tuple(await self._storage.list_alert_rules())

    async def delete_rule(self, rule_id: UUID) -> bool:
        """Delete a rule and its transition state, preserving existing events."""
        if not isinstance(rule_id, UUID):
            raise TypeError("rule_id must be a UUID")
        return await self._storage.delete_alert_rule(rule_id)

    async def evaluate(
        self,
        observation: MarketQuote,
        *,
        as_of: datetime,
    ) -> tuple[AlertEvent, ...]:
        """Persist and return events for newly active matching rules.

        A returned event has already been committed by storage. Observations at
        or before a rule's last processed timestamp are ignored by the atomic
        storage transition, making repeated delivery idempotent.
        """
        if not isinstance(observation, MarketQuote):
            raise TypeError("observation must be a MarketQuote")
        if observation.quote_type is not QuoteType.LOWEST_ASK:
            raise ValueError("only lowest_ask observations are supported")

        evaluated_at = self._normalize_as_of(as_of)
        age = evaluated_at - observation.observed_at
        if age < timedelta(0):
            raise ValueError("observation must not be in the future")
        if age > self._max_quote_age:
            raise ValueError("observation is stale")

        rules = await self._storage.list_alert_rules(
            market_hash_name=observation.market_hash_name,
            marketplace=observation.marketplace,
            currency=observation.currency,
            quote_type=observation.quote_type,
        )
        events: list[AlertEvent] = []
        for rule in rules:
            if not self._matches(rule, observation):
                continue

            active = self._condition_is_active(rule, observation)
            candidate = AlertEvent(
                id=self._event_id_factory(),
                rule_id=rule.id,
                quote=observation,
                condition=rule.condition,
                threshold=rule.threshold,
                triggered_at=evaluated_at,
            )
            event = await self._storage.commit_alert_evaluation(
                rule.id,
                observation=observation,
                condition_active=active,
                candidate_event=candidate,
            )
            if event is not None:
                events.append(event)
        return tuple(events)

    @staticmethod
    def _normalize_as_of(as_of: datetime) -> datetime:
        if not isinstance(as_of, datetime):
            raise TypeError("as_of must be a datetime")
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        return as_of.astimezone(UTC)

    @staticmethod
    def _matches(rule: AlertRule, observation: MarketQuote) -> bool:
        return (
            rule.item.market_hash_name == observation.market_hash_name
            and rule.marketplace == observation.marketplace
            and rule.currency == observation.currency
            and rule.quote_type is observation.quote_type
        )

    @staticmethod
    def _condition_is_active(rule: AlertRule, observation: MarketQuote) -> bool:
        if rule.condition is AlertCondition.BELOW:
            return observation.price < rule.threshold
        return observation.price > rule.threshold
