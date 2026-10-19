"""Public persistence boundary for engine domain objects."""

import asyncio
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Select, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.sql.elements import ColumnElement

from engine.errors import StorageBusyError, StorageError
from engine.models import Item, MarketQuote, QuoteType

from .database import (
    IN_MEMORY_DATABASE,
    create_database_parent,
    create_database_resources,
)
from .models import Base, QuoteRecord


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


def _translate_database_error(operation: str, error: BaseException) -> StorageError:
    original: BaseException = error
    if isinstance(error, SQLAlchemyError) and getattr(error, "orig", None) is not None:
        original = error.orig
    code = getattr(original, "sqlite_errorcode", None)
    primary_code = code & 0xFF if isinstance(code, int) else None
    if primary_code in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
        return StorageBusyError(f"could not {operation}: SQLite database remained busy")
    return StorageError(f"could not {operation}: {original}")
