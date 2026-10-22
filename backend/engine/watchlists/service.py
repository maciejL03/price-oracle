"""Persistent watchlist management and recurring fetch reconciliation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import quote
from uuid import UUID, uuid4

from engine.errors import EngineError
from engine.jobs import EngineJob
from engine.marketplaces import MarketplaceRegistry
from engine.models import Item

from .models import Watchlist, WatchlistItem


class WatchlistStorage(Protocol):
    """Minimum persistence contract required by WatchlistService."""

    async def list_watchlists(self) -> list[Watchlist]: ...

    async def get_watchlist(self, watchlist_id: UUID) -> Watchlist | None: ...

    async def save_watchlist(self, watchlist: Watchlist) -> None: ...

    async def delete_watchlist(self, watchlist_id: UUID) -> bool: ...


class RecurringScheduler(Protocol):
    """Existing JobScheduler operations used by the service."""

    def register(
        self,
        key: str,
        job_factory: Callable[[], EngineJob],
        interval_seconds: float,
        *,
        run_immediately: bool = False,
    ) -> None: ...

    def unregister(self, key: str) -> bool: ...


type FetchJobFactory = Callable[[str, Item], EngineJob]


class WatchlistError(EngineError):
    """Base error for watchlist management failures."""


class WatchlistNotFoundError(WatchlistError, LookupError):
    """Raised when a requested persistent watchlist does not exist."""


class WatchlistItemNotFoundError(WatchlistError, LookupError):
    """Raised when an item is not present in a requested watchlist."""


@dataclass(frozen=True, slots=True)
class SyncFailure:
    operation: str
    key: str
    error: Exception


class WatchlistSyncError(WatchlistError):
    """Raised after reconciliation makes as much progress as possible."""

    def __init__(self, failures: tuple[SyncFailure, ...]) -> None:
        self.failures = failures
        summary = ", ".join(
            f"{failure.operation} {failure.key}: {failure.error}"
            for failure in failures
        )
        super().__init__(
            "schedule synchronization was incomplete; persistent watchlist "
            f"configuration remains recoverable ({summary})"
        )


@dataclass(frozen=True, slots=True)
class SyncResult:
    """Summary of one successful schedule reconciliation."""

    registered: int = 0
    updated: int = 0
    unregistered: int = 0
    unchanged: int = 0


@dataclass(frozen=True, slots=True)
class _Schedule:
    interval_seconds: float
    factory: Callable[[], EngineJob]


class WatchlistService:
    """Manage persistent watchlists and reconcile their recurring fetch jobs."""

    def __init__(
        self,
        storage: WatchlistStorage,
        scheduler: RecurringScheduler,
        registry: MarketplaceRegistry,
        fetch_job_factory: FetchJobFactory,
        *,
        _id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        self._storage = storage
        self._scheduler = scheduler
        self._registry = registry
        self._fetch_job_factory = fetch_job_factory
        self._id_factory = _id_factory
        self._lock = asyncio.Lock()
        self._scheduled: dict[str, _Schedule] = {}

    async def create_watchlist(
        self, name: str, *, interval_seconds: float
    ) -> Watchlist:
        """Persist an empty watchlist and reconcile schedules."""
        watchlist = Watchlist(
            id=self._id_factory(),
            name=name,
            interval_seconds=interval_seconds,
        )
        async with self._lock:
            await self._storage.save_watchlist(watchlist)
            await self._sync_jobs_locked()
        return watchlist

    async def list_watchlists(self) -> list[Watchlist]:
        """Return all persisted watchlists."""
        return await self._storage.list_watchlists()

    async def delete_watchlist(self, watchlist_id: UUID) -> None:
        """Delete a watchlist before reconciling its schedules."""
        async with self._lock:
            deleted = await self._storage.delete_watchlist(watchlist_id)
            if not deleted:
                raise WatchlistNotFoundError(
                    f"Watchlist does not exist: {watchlist_id}"
                )
            await self._sync_jobs_locked()

    async def add_item(
        self,
        watchlist_id: UUID,
        item: Item,
        marketplaces: Iterable[str],
    ) -> Watchlist:
        """Add an item or union marketplaces into its existing association."""
        normalized_marketplaces = self._validate_marketplaces(marketplaces)
        async with self._lock:
            watchlist = await self._get_watchlist(watchlist_id)
            entries = list(watchlist.items)
            for index, entry in enumerate(entries):
                if entry.item == item:
                    entries[index] = WatchlistItem(
                        item=item,
                        marketplaces=entry.marketplaces | normalized_marketplaces,
                    )
                    break
            else:
                entries.append(
                    WatchlistItem(item=item, marketplaces=normalized_marketplaces)
                )

            updated = _replace_watchlist(watchlist, items=tuple(entries))
            if updated != watchlist:
                await self._storage.save_watchlist(updated)
            await self._sync_jobs_locked()
        return updated

    async def remove_item(self, watchlist_id: UUID, item: Item) -> Watchlist:
        """Remove an item and all of its marketplace associations."""
        async with self._lock:
            watchlist = await self._get_watchlist(watchlist_id)
            entries = tuple(entry for entry in watchlist.items if entry.item != item)
            updated = _replace_watchlist(watchlist, items=entries)
            if updated != watchlist:
                await self._storage.save_watchlist(updated)
            await self._sync_jobs_locked()
        return updated

    async def set_item_marketplaces(
        self,
        watchlist_id: UUID,
        item: Item,
        marketplaces: Iterable[str],
    ) -> Watchlist:
        """Replace the marketplace associations for an existing item."""
        normalized_marketplaces = self._validate_marketplaces(marketplaces)
        async with self._lock:
            watchlist = await self._get_watchlist(watchlist_id)
            entries = list(watchlist.items)
            for index, entry in enumerate(entries):
                if entry.item == item:
                    entries[index] = WatchlistItem(
                        item=item,
                        marketplaces=normalized_marketplaces,
                    )
                    break
            else:
                raise WatchlistItemNotFoundError(
                    f"Item is not in watchlist {watchlist_id}: {item.market_hash_name}"
                )

            updated = _replace_watchlist(watchlist, items=tuple(entries))
            if updated != watchlist:
                await self._storage.save_watchlist(updated)
            await self._sync_jobs_locked()
        return updated

    async def set_interval(
        self, watchlist_id: UUID, interval_seconds: float
    ) -> Watchlist:
        """Change a watchlist's requested recurring fetch interval."""
        async with self._lock:
            watchlist = await self._get_watchlist(watchlist_id)
            updated = _replace_watchlist(watchlist, interval_seconds=interval_seconds)
            if updated != watchlist:
                await self._storage.save_watchlist(updated)
            await self._sync_jobs_locked()
        return updated

    async def sync_jobs(self) -> SyncResult:
        """Restore or reconcile recurring fetches from persistent configuration."""
        async with self._lock:
            return await self._sync_jobs_locked()

    async def _get_watchlist(self, watchlist_id: UUID) -> Watchlist:
        watchlist = await self._storage.get_watchlist(watchlist_id)
        if watchlist is None:
            raise WatchlistNotFoundError(f"Watchlist does not exist: {watchlist_id}")
        return watchlist

    def _validate_marketplaces(self, marketplaces: Iterable[str]) -> frozenset[str]:
        if isinstance(marketplaces, str):
            raise TypeError(
                "marketplaces must be an iterable of identifiers, not a string"
            )
        normalized = frozenset(marketplaces)
        if not normalized:
            raise ValueError("marketplaces must not be empty")
        for marketplace in normalized:
            self._registry.get(marketplace)
        return normalized

    async def _sync_jobs_locked(self) -> SyncResult:
        desired = self._desired_schedules(await self._storage.list_watchlists())
        registered = 0
        updated = 0
        unregistered = 0
        unchanged = 0
        failures: list[SyncFailure] = []

        for key, schedule in desired.items():
            current = self._scheduled.get(key)
            if (
                current is not None
                and current.interval_seconds == schedule.interval_seconds
            ):
                unchanged += 1
                continue

            is_update = current is not None
            if is_update:
                try:
                    self._scheduler.unregister(key)
                except Exception as error:  # noqa: BLE001 - preserve partial sync progress
                    failures.append(SyncFailure("unregister", key, error))
                    continue
                self._scheduled.pop(key, None)

            try:
                self._scheduler.register(
                    key,
                    schedule.factory,
                    schedule.interval_seconds,
                    run_immediately=True,
                )
            except Exception as error:  # noqa: BLE001 - retry independent schedules
                failures.append(SyncFailure("register", key, error))
                continue

            self._scheduled[key] = schedule
            if is_update:
                updated += 1
            else:
                registered += 1

        for key in tuple(self._scheduled.keys() - desired.keys()):
            try:
                removed = self._scheduler.unregister(key)
            except Exception as error:  # noqa: BLE001 - retain ownership for retry
                failures.append(SyncFailure("unregister", key, error))
                continue
            self._scheduled.pop(key, None)
            if removed:
                unregistered += 1

        if failures:
            raise WatchlistSyncError(tuple(failures))
        return SyncResult(
            registered=registered,
            updated=updated,
            unregistered=unregistered,
            unchanged=unchanged,
        )

    def _desired_schedules(
        self, watchlists: Iterable[Watchlist]
    ) -> dict[str, _Schedule]:
        desired: dict[str, _Schedule] = {}
        for watchlist in watchlists:
            for entry in watchlist.items:
                for marketplace in sorted(entry.marketplaces):
                    self._registry.get(marketplace)
                    key = fetch_job_key(marketplace, entry.item)
                    existing = desired.get(key)
                    if (
                        existing is not None
                        and existing.interval_seconds <= watchlist.interval_seconds
                    ):
                        continue

                    def factory(
                        marketplace: str = marketplace,
                        item: Item = entry.item,
                    ) -> EngineJob:
                        return self._fetch_job_factory(marketplace, item)

                    desired[key] = _Schedule(watchlist.interval_seconds, factory)
        return desired


def fetch_job_key(marketplace: str, item: Item) -> str:
    """Return the stable identity used by FetchMarketJob."""
    return (
        f"fetch-market:{quote(marketplace, safe='')}:"
        f"{quote(item.market_hash_name, safe='')}"
    )


def _replace_watchlist(
    watchlist: Watchlist,
    *,
    interval_seconds: float | None = None,
    items: tuple[WatchlistItem, ...] | None = None,
) -> Watchlist:
    return Watchlist(
        id=watchlist.id,
        name=watchlist.name,
        interval_seconds=(
            watchlist.interval_seconds if interval_seconds is None else interval_seconds
        ),
        items=watchlist.items if items is None else items,
    )
