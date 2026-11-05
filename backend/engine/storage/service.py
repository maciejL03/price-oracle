"""Public persistence boundary for engine domain objects."""

import asyncio
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from engine.alerts.models import AlertEvent, AlertRule
from engine.errors import StorageBusyError, StorageError
from engine.models import Item, MarketQuote, QuoteType
from engine.notifications.models import DeliveryStatus, NotificationDelivery
from engine.watchlists.models import Watchlist, WatchlistItem
from sqlalchemy import Select, delete, func, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.sql.elements import ColumnElement

from .database import (
    IN_MEMORY_DATABASE,
    create_database_parent,
    create_database_resources,
)
from .models import (
    AlertEventRecord,
    AlertRuleRecord,
    AlertRuleStateRecord,
    Base,
    NotificationDeliveryRecord,
    QuoteRecord,
    WatchlistEntryRecord,
    WatchlistRecord,
)


class StorageService:
    """Store and retrieve domain objects without exposing ORM details."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        busy_timeout_seconds: float = 5.0,
    ) -> None:
        normalized_path: str | Path = (
            IN_MEMORY_DATABASE
            if str(database_path) == IN_MEMORY_DATABASE
            else Path(database_path).expanduser()
        )
        self._database_path = normalized_path
        self._engine, self._sessions, self._is_in_memory = create_database_resources(
            normalized_path,
            busy_timeout_seconds=busy_timeout_seconds,
        )
        self._lifecycle_lock = asyncio.Lock()
        self._initialized = False
        self._closed = False

    async def __aenter__(self) -> "StorageService":
        await self.initialize()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def initialize(self) -> None:
        """Create the database directory and currently owned tables."""
        async with self._lifecycle_lock:
            if self._closed:
                raise StorageError("storage has already been closed")
            if self._initialized:
                return
            try:
                create_database_parent(self._database_path)
                async with self._engine.connect() as connection:
                    if not self._is_in_memory:
                        result = await connection.exec_driver_sql(
                            "PRAGMA journal_mode=WAL"
                        )
                        journal_mode = result.scalar_one()
                        await connection.commit()
                        if str(journal_mode).lower() != "wal":
                            raise StorageError(
                                "could not initialize storage: SQLite refused WAL mode"
                            )
                    async with connection.begin():
                        await connection.run_sync(Base.metadata.create_all)
            except (OSError, SQLAlchemyError) as exc:
                raise _translate_database_error("initialize storage", exc) from exc
            self._initialized = True

    async def close(self) -> None:
        """Release pooled database connections; repeated calls are harmless."""
        async with self._lifecycle_lock:
            if self._closed:
                return
            await self._engine.dispose()
            self._closed = True
            self._initialized = False

    async def save_quotes(self, quotes: Iterable[MarketQuote]) -> None:
        """Atomically append a batch of quote observations."""
        self._ensure_ready()
        records = [QuoteRecord.from_domain(quote) for quote in quotes]
        if not records:
            return

        try:
            async with self._sessions() as session:
                async with session.begin():
                    session.add_all(records)
        except SQLAlchemyError as exc:
            raise _translate_database_error("save quotes", exc) from exc

    async def get_latest_quotes(
        self,
        item: Item,
        *,
        marketplace: str | None = None,
        currency: str | None = None,
        quote_type: QuoteType | None = None,
    ) -> list[MarketQuote]:
        """Return the newest observation in each matching quote series.

        A series is identified by marketplace, currency, and quote type. All rows
        tied at a series' newest observation timestamp are retained.
        """
        self._ensure_ready()
        filters = self._quote_filters(
            item,
            marketplace=marketplace,
            currency=currency,
            quote_type=quote_type,
        )
        observation_rank = func.dense_rank().over(
            partition_by=(
                QuoteRecord.marketplace,
                QuoteRecord.currency,
                QuoteRecord.quote_type,
            ),
            order_by=QuoteRecord.observed_at.desc(),
        ).label("observation_rank")
        ranked = select(QuoteRecord.id.label("quote_id"), observation_rank).where(
            *filters
        ).subquery()
        statement = (
            select(QuoteRecord)
            .join(ranked, QuoteRecord.id == ranked.c.quote_id)
            .where(ranked.c.observation_rank == 1)
            .order_by(
                QuoteRecord.marketplace,
                QuoteRecord.currency,
                QuoteRecord.quote_type,
                QuoteRecord.id,
            )
        )
        return await self._read_quotes(statement, "get latest quotes")

    async def get_price_history(
        self,
        item: Item,
        *,
        marketplace: str | None = None,
        currency: str | None = None,
        quote_type: QuoteType | None = None,
        observed_from: datetime | None = None,
        observed_to: datetime | None = None,
        limit: int | None = None,
    ) -> list[MarketQuote]:
        """Return matching observations in chronological insertion order.

        ``observed_from`` is inclusive and ``observed_to`` is exclusive.
        """
        self._ensure_ready()
        if limit is not None and limit <= 0:
            raise ValueError("limit must be greater than zero")
        normalized_from = _normalize_query_datetime(observed_from, "observed_from")
        normalized_to = _normalize_query_datetime(observed_to, "observed_to")
        if (
            normalized_from is not None
            and normalized_to is not None
            and normalized_from >= normalized_to
        ):
            raise ValueError("observed_from must be earlier than observed_to")

        filters = self._quote_filters(
            item,
            marketplace=marketplace,
            currency=currency,
            quote_type=quote_type,
        )
        if normalized_from is not None:
            filters.append(QuoteRecord.observed_at >= normalized_from)
        if normalized_to is not None:
            filters.append(QuoteRecord.observed_at < normalized_to)
        statement = select(QuoteRecord).where(*filters).order_by(
            QuoteRecord.observed_at,
            QuoteRecord.id,
        )
        if limit is not None:
            statement = statement.limit(limit)
        return await self._read_quotes(statement, "get price history")

    async def list_watchlists(self) -> list[Watchlist]:
        """Return all persisted watchlists with their item associations."""
        self._ensure_ready()
        try:
            async with self._sessions() as session:
                async with session.begin():
                    watchlists = list(
                        (
                            await session.scalars(
                                select(WatchlistRecord).order_by(WatchlistRecord.id)
                            )
                        ).all()
                    )
                    entries = list(
                        (
                            await session.scalars(
                                select(WatchlistEntryRecord).order_by(
                                    WatchlistEntryRecord.watchlist_id,
                                    WatchlistEntryRecord.market_hash_name,
                                    WatchlistEntryRecord.marketplace,
                                )
                            )
                        ).all()
                    )
        except SQLAlchemyError as exc:
            raise _translate_database_error("list watchlists", exc) from exc
        return _build_watchlists(watchlists, entries)

    async def get_watchlist(self, watchlist_id: UUID) -> Watchlist | None:
        """Return one persisted watchlist aggregate, if it exists."""
        self._ensure_ready()
        if not isinstance(watchlist_id, UUID):
            raise TypeError("watchlist_id must be a UUID")
        identifier = str(watchlist_id)
        try:
            async with self._sessions() as session:
                async with session.begin():
                    watchlist = await session.scalar(
                        select(WatchlistRecord).where(WatchlistRecord.id == identifier)
                    )
                    if watchlist is None:
                        return None
                    entries = list(
                        (
                            await session.scalars(
                                select(WatchlistEntryRecord)
                                .where(
                                    WatchlistEntryRecord.watchlist_id == identifier
                                )
                                .order_by(
                                    WatchlistEntryRecord.market_hash_name,
                                    WatchlistEntryRecord.marketplace,
                                )
                            )
                        ).all()
                    )
        except SQLAlchemyError as exc:
            raise _translate_database_error("get watchlist", exc) from exc
        return _build_watchlists([watchlist], entries)[0]

    async def save_watchlist(self, watchlist: Watchlist) -> None:
        """Atomically create or replace a complete watchlist aggregate."""
        self._ensure_ready()
        if not isinstance(watchlist, Watchlist):
            raise TypeError("watchlist must be a Watchlist")
        identifier = str(watchlist.id)
        statement = (
            sqlite_insert(WatchlistRecord)
            .values(
                id=identifier,
                name=watchlist.name,
                interval_seconds=watchlist.interval_seconds,
            )
            .on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "name": watchlist.name,
                    "interval_seconds": watchlist.interval_seconds,
                },
            )
        )
        entries = [
            WatchlistEntryRecord(
                watchlist_id=identifier,
                market_hash_name=entry.item.market_hash_name,
                marketplace=marketplace,
            )
            for entry in watchlist.items
            for marketplace in sorted(entry.marketplaces)
        ]
        try:
            async with self._sessions() as session:
                async with session.begin():
                    await session.execute(statement)
                    await session.execute(
                        delete(WatchlistEntryRecord).where(
                            WatchlistEntryRecord.watchlist_id == identifier
                        )
                    )
                    session.add_all(entries)
        except SQLAlchemyError as exc:
            raise _translate_database_error("save watchlist", exc) from exc

    async def delete_watchlist(self, watchlist_id: UUID) -> bool:
        """Atomically delete a watchlist and all of its associations."""
        self._ensure_ready()
        if not isinstance(watchlist_id, UUID):
            raise TypeError("watchlist_id must be a UUID")
        try:
            async with self._sessions() as session:
                async with session.begin():
                    result = await session.execute(
                        delete(WatchlistRecord).where(
                            WatchlistRecord.id == str(watchlist_id)
                        )
                    )
                    deleted = result.rowcount == 1
        except SQLAlchemyError as exc:
            raise _translate_database_error("delete watchlist", exc) from exc
        return deleted

    async def save_alert_rule(self, rule: AlertRule) -> None:
        """Persist a rule and its initially inactive edge-trigger state."""
        self._ensure_ready()
        if not isinstance(rule, AlertRule):
            raise TypeError("rule must be an AlertRule")

        try:
            async with self._sessions() as session:
                async with session.begin():
                    session.add(AlertRuleRecord.from_domain(rule))
                    session.add(
                        AlertRuleStateRecord(rule_id=str(rule.id), is_active=False)
                    )
        except SQLAlchemyError as exc:
            raise _translate_database_error("save alert rule", exc) from exc

    async def list_alert_rules(
        self,
        *,
        market_hash_name: str | None = None,
        marketplace: str | None = None,
        currency: str | None = None,
        quote_type: QuoteType | None = None,
    ) -> list[AlertRule]:
        """Return rules matching every supplied observation attribute."""
        self._ensure_ready()
        filters: list[ColumnElement[bool]] = []
        if market_hash_name is not None:
            filters.append(AlertRuleRecord.market_hash_name == market_hash_name)
        if marketplace is not None:
            filters.append(AlertRuleRecord.marketplace == marketplace)
        if currency is not None:
            filters.append(AlertRuleRecord.currency == currency)
        if quote_type is not None:
            filters.append(AlertRuleRecord.quote_type == quote_type.value)
        statement = select(AlertRuleRecord).where(*filters).order_by(AlertRuleRecord.id)

        try:
            async with self._sessions() as session:
                async with session.begin():
                    records = list((await session.scalars(statement)).all())
        except SQLAlchemyError as exc:
            raise _translate_database_error("list alert rules", exc) from exc

        try:
            return [
                AlertRule.model_validate(record.to_domain_values())
                for record in records
            ]
        except (TypeError, ValueError) as exc:
            message = "list alert rules returned invalid persisted data"
            raise StorageError(message) from exc

    async def delete_alert_rule(self, rule_id: UUID) -> bool:
        """Delete a rule and its state without deleting historical events."""
        self._ensure_ready()
        if not isinstance(rule_id, UUID):
            raise TypeError("rule_id must be a UUID")

        try:
            async with self._sessions() as session:
                async with session.begin():
                    result = await session.execute(
                        delete(AlertRuleRecord).where(
                            AlertRuleRecord.id == str(rule_id)
                        )
                    )
                    deleted = result.rowcount == 1
        except SQLAlchemyError as exc:
            raise _translate_database_error("delete alert rule", exc) from exc
        return deleted

    async def commit_alert_evaluation(
        self,
        rule_id: UUID,
        *,
        observation: MarketQuote,
        condition_active: bool,
        candidate_event: AlertEvent,
    ) -> AlertEvent | None:
        """Atomically advance rule state and persist a newly active event.

        Only observations newer than the stored timestamp can mutate state. A
        conditional update claims an inactive-to-active transition before the
        candidate event is inserted in the same transaction.
        """
        self._ensure_ready()
        if not isinstance(rule_id, UUID):
            raise TypeError("rule_id must be a UUID")
        if not isinstance(observation, MarketQuote):
            raise TypeError("observation must be a MarketQuote")
        if not isinstance(condition_active, bool):
            raise TypeError("condition_active must be a bool")
        if not isinstance(candidate_event, AlertEvent):
            raise TypeError("candidate_event must be an AlertEvent")
        if candidate_event.rule_id != rule_id:
            raise ValueError("candidate_event rule_id does not match rule_id")
        if candidate_event.quote != observation:
            raise ValueError("candidate_event quote does not match observation")

        newer_observation = or_(
            AlertRuleStateRecord.last_observed_at.is_(None),
            AlertRuleStateRecord.last_observed_at < observation.observed_at,
        )
        committed_event: AlertEvent | None = None
        try:
            async with self._sessions() as session:
                async with session.begin():
                    if condition_active:
                        transition = await session.execute(
                            update(AlertRuleStateRecord)
                            .where(
                                AlertRuleStateRecord.rule_id == str(rule_id),
                                AlertRuleStateRecord.is_active.is_(False),
                                newer_observation,
                            )
                            .values(
                                is_active=True,
                                last_observed_at=observation.observed_at,
                            )
                        )
                        if transition.rowcount == 1:
                            session.add(AlertEventRecord.from_domain(candidate_event))
                            committed_event = candidate_event
                        else:
                            await session.execute(
                                update(AlertRuleStateRecord)
                                .where(
                                    AlertRuleStateRecord.rule_id == str(rule_id),
                                    AlertRuleStateRecord.is_active.is_(True),
                                    newer_observation,
                                )
                                .values(last_observed_at=observation.observed_at)
                            )
                    else:
                        await session.execute(
                            update(AlertRuleStateRecord)
                            .where(
                                AlertRuleStateRecord.rule_id == str(rule_id),
                                newer_observation,
                            )
                            .values(
                                is_active=False,
                                last_observed_at=observation.observed_at,
                            )
                        )
        except SQLAlchemyError as exc:
            raise _translate_database_error("commit alert evaluation", exc) from exc
        return committed_event

    async def list_alert_events(
        self,
        *,
        rule_id: UUID | None = None,
    ) -> list[AlertEvent]:
        """Return durable alert events in trigger order."""
        self._ensure_ready()
        statement = select(AlertEventRecord)
        if rule_id is not None:
            if not isinstance(rule_id, UUID):
                raise TypeError("rule_id must be a UUID")
            statement = statement.where(AlertEventRecord.rule_id == str(rule_id))
        statement = statement.order_by(
            AlertEventRecord.triggered_at,
            AlertEventRecord.id,
        )

        try:
            async with self._sessions() as session:
                async with session.begin():
                    records = list((await session.scalars(statement)).all())
        except SQLAlchemyError as exc:
            raise _translate_database_error("list alert events", exc) from exc

        try:
            return [
                AlertEvent.model_validate(record.to_domain_values())
                for record in records
            ]
        except (TypeError, ValueError) as exc:
            message = "list alert events returned invalid persisted data"
            raise StorageError(message) from exc

    async def claim_notification_delivery(
        self, delivery: NotificationDelivery
    ) -> bool:
        """Atomically claim an alert event for at most one delivery caller."""
        self._ensure_ready()
        if not isinstance(delivery, NotificationDelivery):
            raise TypeError("delivery must be a NotificationDelivery")
        if delivery.status is not DeliveryStatus.PENDING:
            raise ValueError("a notification claim must be pending")

        record = NotificationDeliveryRecord.from_domain(delivery)
        statement = (
            sqlite_insert(NotificationDeliveryRecord)
            .values(
                event_id=record.event_id,
                channel=record.channel,
                status=record.status,
                attempts=record.attempts,
                started_at=record.started_at,
                completed_at=record.completed_at,
                failure_code=record.failure_code,
                failure_detail=record.failure_detail,
            )
            .on_conflict_do_nothing(index_elements=["event_id"])
        )
        try:
            async with self._sessions() as session:
                async with session.begin():
                    result = await session.execute(statement)
                    claimed = result.rowcount == 1
        except SQLAlchemyError as exc:
            raise _translate_database_error(
                "claim notification delivery", exc
            ) from exc
        return claimed

    async def get_notification_delivery(
        self, event_id: UUID
    ) -> NotificationDelivery | None:
        """Return the durable delivery state for an alert event, if present."""
        self._ensure_ready()
        if not isinstance(event_id, UUID):
            raise TypeError("event_id must be a UUID")
        statement = select(NotificationDeliveryRecord).where(
            NotificationDeliveryRecord.event_id == str(event_id)
        )
        try:
            async with self._sessions() as session:
                async with session.begin():
                    record = await session.scalar(statement)
        except SQLAlchemyError as exc:
            raise _translate_database_error(
                "get notification delivery", exc
            ) from exc

        if record is None:
            return None
        try:
            return NotificationDelivery.model_validate(record.to_domain_values())
        except (TypeError, ValueError) as exc:
            raise StorageError(
                "get notification delivery returned invalid persisted data"
            ) from exc

    async def complete_notification_delivery(
        self, delivery: NotificationDelivery
    ) -> None:
        """Persist a claimed delivery's final success or failure outcome."""
        self._ensure_ready()
        if not isinstance(delivery, NotificationDelivery):
            raise TypeError("delivery must be a NotificationDelivery")
        if delivery.status is DeliveryStatus.PENDING:
            raise ValueError("a completed notification delivery cannot be pending")

        statement = (
            update(NotificationDeliveryRecord)
            .where(
                NotificationDeliveryRecord.event_id == str(delivery.event_id),
                NotificationDeliveryRecord.status == DeliveryStatus.PENDING.value,
            )
            .values(
                channel=delivery.channel,
                status=delivery.status.value,
                attempts=delivery.attempts,
                started_at=delivery.started_at,
                completed_at=delivery.completed_at,
                failure_code=(
                    None
                    if delivery.failure_code is None
                    else delivery.failure_code.value
                ),
                failure_detail=delivery.failure_detail,
            )
        )
        try:
            async with self._sessions() as session:
                async with session.begin():
                    result = await session.execute(statement)
                    if result.rowcount != 1:
                        raise StorageError(
                            "could not complete notification delivery: "
                            "pending claim does not exist"
                        )
        except SQLAlchemyError as exc:
            raise _translate_database_error(
                "complete notification delivery", exc
            ) from exc

    async def _read_quotes(
        self,
        statement: Select[tuple[QuoteRecord]],
        operation: str,
    ) -> list[MarketQuote]:
        try:
            async with self._sessions() as session:
                async with session.begin():
                    records = list((await session.scalars(statement)).all())
        except SQLAlchemyError as exc:
            raise _translate_database_error(operation, exc) from exc

        try:
            return [
                MarketQuote.model_validate(record.to_domain_values())
                for record in records
            ]
        except (TypeError, ValueError) as exc:
            message = f"{operation} returned invalid persisted quote data"
            raise StorageError(message) from exc

    @staticmethod
    def _quote_filters(
        item: Item,
        *,
        marketplace: str | None,
        currency: str | None,
        quote_type: QuoteType | None,
    ) -> list[ColumnElement[bool]]:
        filters = [QuoteRecord.market_hash_name == item.market_hash_name]
        if marketplace is not None:
            filters.append(QuoteRecord.marketplace == marketplace)
        if currency is not None:
            filters.append(QuoteRecord.currency == currency)
        if quote_type is not None:
            filters.append(QuoteRecord.quote_type == quote_type.value)
        return filters

    def _ensure_ready(self) -> None:
        if self._closed:
            raise StorageError("storage has already been closed")
        if not self._initialized:
            raise StorageError("storage has not been initialized")


def _normalize_query_datetime(
    value: datetime | None,
    field_name: str,
) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _build_watchlists(
    records: Iterable[WatchlistRecord],
    entries: Iterable[WatchlistEntryRecord],
) -> list[Watchlist]:
    grouped: dict[str, dict[str, set[str]]] = {}
    for entry in entries:
        grouped.setdefault(entry.watchlist_id, {}).setdefault(
            entry.market_hash_name, set()
        ).add(entry.marketplace)

    try:
        return [
            Watchlist(
                id=UUID(record.id),
                name=record.name,
                interval_seconds=record.interval_seconds,
                items=tuple(
                    WatchlistItem(
                        item=Item(market_hash_name=market_hash_name),
                        marketplaces=marketplaces,
                    )
                    for market_hash_name, marketplaces in grouped.get(
                        record.id, {}
                    ).items()
                ),
            )
            for record in records
        ]
    except (TypeError, ValueError) as exc:
        raise StorageError("stored watchlist data is invalid") from exc


def _translate_database_error(operation: str, error: BaseException) -> StorageError:
    original: BaseException = error
    if isinstance(error, SQLAlchemyError) and getattr(error, "orig", None) is not None:
        original = error.orig
    code = getattr(original, "sqlite_errorcode", None)
    primary_code = code & 0xFF if isinstance(code, int) else None
    if primary_code in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
        return StorageBusyError(f"could not {operation}: SQLite database remained busy")
    return StorageError(f"could not {operation}: {original}")
