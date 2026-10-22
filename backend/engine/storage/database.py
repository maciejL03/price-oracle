"""Private SQLAlchemy engine and session configuration."""

from pathlib import Path
from typing import Any

from sqlalchemy import URL, event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

IN_MEMORY_DATABASE = ":memory:"

SessionFactory = async_sessionmaker[AsyncSession]


def create_database_resources(
    database_path: str | Path,
    *,
    busy_timeout_seconds: float,
) -> tuple[AsyncEngine, SessionFactory, bool]:
    """Build an async engine without opening a database connection."""
    if busy_timeout_seconds <= 0:
        raise ValueError("busy_timeout_seconds must be greater than zero")

    path_text = str(database_path)
    is_in_memory = path_text == IN_MEMORY_DATABASE
    url = URL.create(
        "sqlite+aiosqlite",
        database=IN_MEMORY_DATABASE if is_in_memory else path_text,
    )
    engine_options: dict[str, Any] = {
        "connect_args": {"timeout": busy_timeout_seconds},
    }
    if is_in_memory:
        # All sessions must see the same transient database. File databases retain
        # SQLAlchemy's normal async-aware pool and therefore support concurrent readers.
        engine_options["poolclass"] = StaticPool

    engine = create_async_engine(url, **engine_options)
    _install_sqlite_configuration(
        engine,
        busy_timeout_milliseconds=max(1, round(busy_timeout_seconds * 1000)),
    )
    return engine, async_sessionmaker(engine, expire_on_commit=False), is_in_memory


def _install_sqlite_configuration(
    engine: AsyncEngine,
    *,
    busy_timeout_milliseconds: int,
) -> None:
    """Configure every DB-API connection owned by the async engine."""

    @event.listens_for(engine.sync_engine, "connect")
    def configure_sqlite_connection(
        dbapi_connection: Any,
        _connection_record: Any,
    ) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute(f"PRAGMA busy_timeout={busy_timeout_milliseconds}")
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()


def create_database_parent(database_path: str | Path) -> None:
    """Create the parent directory for a file-backed database."""
    path_text = str(database_path)
    if path_text == IN_MEMORY_DATABASE:
        return

    parent = Path(path_text).expanduser().parent
    parent.mkdir(parents=True, exist_ok=True)
