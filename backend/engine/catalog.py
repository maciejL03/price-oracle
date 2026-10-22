"""Canonical CS2 item catalog used by HTTP-facing engine services."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx

from engine.errors import EngineError


class CatalogError(EngineError):
    """Base exception for canonical item-catalog failures."""


class CatalogUnavailableError(CatalogError, ConnectionError):
    """Raised when the remote item catalog cannot currently be read."""


class CatalogResponseError(CatalogError, ValueError):
    """Raised when the remote item catalog returns unusable data."""


@dataclass(frozen=True, slots=True)
class CatalogItem:
    """A displayable catalog entry keyed by its Steam market hash name."""

    market_hash_name: str
    name: str
    image: str | None = None


class ItemCatalog(Protocol):
    """Lookup capability required by item-search and comparison APIs."""

    async def search(self, query: str, *, limit: int) -> list[CatalogItem]: ...

    async def resolve(self, market_hash_name: str) -> CatalogItem | None: ...


class ByMykelItemCatalog:
    """Cache the public ByMykel catalog and expose deterministic local searches."""

    def __init__(
        self,
        *,
        url: str,
        cache_path: Path,
        cache_ttl_seconds: float,
        timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = url
        self._cache_path = cache_path
        self._cache_ttl_seconds = cache_ttl_seconds
        self._timeout_seconds = timeout_seconds
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient()
        self._items: list[CatalogItem] = []
        self._by_hash_name: dict[str, CatalogItem] = {}
        self._loaded_at = 0.0
        self._load_lock = asyncio.Lock()

    async def search(self, query: str, *, limit: int) -> list[CatalogItem]:
        await self._ensure_loaded()
        normalized = query.lower().strip()
        if not normalized:
            return []

        exact = [
            item for item in self._items if item.market_hash_name.lower() == normalized
        ]
        if exact:
            return exact[:limit]
        prefix = [
            item
            for item in self._items
            if item.market_hash_name.lower().startswith(normalized)
        ]
        if prefix:
            return prefix[:limit]
        contains = [
            item for item in self._items if normalized in item.market_hash_name.lower()
        ]
        contains.sort(
            key=lambda item: (
                item.market_hash_name.lower().index(normalized),
                len(item.market_hash_name),
            )
        )
        return contains[:limit]

    async def resolve(self, market_hash_name: str) -> CatalogItem | None:
        await self._ensure_loaded()
        return self._by_hash_name.get(market_hash_name)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _ensure_loaded(self) -> None:
        if (
            self._items
            and time.monotonic() - self._loaded_at < self._cache_ttl_seconds
        ):
            return
        async with self._load_lock:
            if (
                self._items
                and time.monotonic() - self._loaded_at < self._cache_ttl_seconds
            ):
                return
            raw = await self._read_fresh_cache()
            if raw is None:
                try:
                    response = await self._client.get(
                        self._url, timeout=self._timeout_seconds
                    )
                    response.raise_for_status()
                except httpx.TimeoutException as error:
                    raise CatalogUnavailableError(
                        "item catalog request timed out"
                    ) from error
                except httpx.HTTPStatusError as error:
                    raise CatalogUnavailableError(
                        f"item catalog returned HTTP {error.response.status_code}"
                    ) from error
                except httpx.RequestError as error:
                    raise CatalogUnavailableError(
                        "item catalog request failed"
                    ) from error
                try:
                    raw = response.json()
                except ValueError as error:
                    raise CatalogResponseError(
                        "item catalog returned invalid JSON"
                    ) from error
                await asyncio.to_thread(self._write_cache, raw)
            try:
                self._populate(raw)
            except ValueError as error:
                raise CatalogResponseError(
                    "item catalog response must be an object"
                ) from error
            self._loaded_at = time.monotonic()

    async def _read_fresh_cache(self) -> dict[object, object] | None:
        def read() -> dict[object, object] | None:
            if not self._cache_path.exists():
                return None
            if (
                time.time() - self._cache_path.stat().st_mtime
                >= self._cache_ttl_seconds
            ):
                return None
            try:
                value = json.loads(self._cache_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return None
            return value if isinstance(value, dict) else None

        return await asyncio.to_thread(read)

    def _write_cache(self, raw: object) -> None:
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_path.write_text(json.dumps(raw), encoding="utf-8")

    def _populate(self, raw: object) -> None:
        if not isinstance(raw, dict):
            raise ValueError("item catalog response must be an object")
        items: list[CatalogItem] = []
        by_hash_name: dict[str, CatalogItem] = {}
        for entry in raw.values():
            if not isinstance(entry, dict):
                continue
            market_hash_name = entry.get("market_hash_name")
            if not isinstance(market_hash_name, str) or not market_hash_name:
                continue
            name = entry.get("name")
            image = entry.get("image")
            item = CatalogItem(
                market_hash_name=market_hash_name,
                name=name if isinstance(name, str) else market_hash_name,
                image=image if isinstance(image, str) else None,
            )
            items.append(item)
            by_hash_name[market_hash_name] = item
        self._items = items
        self._by_hash_name = by_hash_name
