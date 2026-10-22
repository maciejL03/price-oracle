"""Skinport aggregate item observations for the engine."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from engine.errors import (
    MarketplaceResponseError,
    MarketplaceTimeoutError,
    MarketplaceUnavailableError,
)
from engine.models import Item, MarketQuote, QuoteType


@dataclass(frozen=True, slots=True)
class SkinportSnapshot:
    quote: MarketQuote
    median_price: Decimal | None
    mean_price: Decimal | None
    max_price: Decimal | None
    suggested_price: Decimal | None
    quantity: int | None
    item_page: str | None
    market_page: str | None


class SkinportMarketplace:
    """Cache Skinport's item index and return exact aggregate observations."""

    name = "skinport"

    def __init__(
        self,
        *,
        currency: str,
        app_id: int,
        base_url: str,
        cache_path: Path,
        cache_ttl_seconds: float,
        timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._currency = currency
        self._app_id = app_id
        self._base_url = base_url.rstrip("/")
        self._cache_path = cache_path
        self._cache_ttl_seconds = cache_ttl_seconds
        self._timeout_seconds = timeout_seconds
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient()
        self._index: dict[str, SkinportSnapshot] = {}
        self._loaded_at = 0.0
        self._load_lock = asyncio.Lock()

    async def fetch(self, item: Item) -> list[MarketQuote]:
        snapshot = await self.fetch_snapshot(item)
        return [] if snapshot is None else [snapshot.quote]

    async def fetch_snapshot(self, item: Item) -> SkinportSnapshot | None:
        await self._ensure_index()
        return self._index.get(item.market_hash_name)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _ensure_index(self) -> None:
        if self._index and time.monotonic() - self._loaded_at < self._cache_ttl_seconds:
            return
        async with self._load_lock:
            if self._index and time.monotonic() - self._loaded_at < self._cache_ttl_seconds:
                return
            raw = await self._read_fresh_cache()
            if raw is None:
                try:
                    response = await self._client.get(
                        f"{self._base_url}/items",
                        params={"app_id": self._app_id, "currency": self._currency, "tradable": 1},
                        headers={"Accept-Encoding": "br"},
                        timeout=self._timeout_seconds,
                    )
                    response.raise_for_status()
                    raw = response.json()
                except httpx.TimeoutException as error:
                    raise MarketplaceTimeoutError("Skinport request timed out") from error
                except httpx.HTTPStatusError as error:
                    raise MarketplaceUnavailableError(
                        f"Skinport returned HTTP {error.response.status_code}"
                    ) from error
                except httpx.RequestError as error:
                    raise MarketplaceUnavailableError("Skinport request failed") from error
                except ValueError as error:
                    raise MarketplaceResponseError("Skinport returned invalid JSON") from error
                await asyncio.to_thread(self._write_cache, raw)
            self._build_index(raw)
            self._loaded_at = time.monotonic()

    async def _read_fresh_cache(self) -> list[dict[str, Any]] | None:
        def read() -> list[dict[str, Any]] | None:
            if not self._cache_path.exists():
                return None
            if time.time() - self._cache_path.stat().st_mtime >= self._cache_ttl_seconds:
                return None
            try:
                value = json.loads(self._cache_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return None
            return value if isinstance(value, list) else None

        return await asyncio.to_thread(read)

    def _write_cache(self, raw: object) -> None:
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_path.write_text(json.dumps(raw), encoding="utf-8")

    def _build_index(self, raw: object) -> None:
        if not isinstance(raw, list):
            raise MarketplaceResponseError("Skinport response must be an item array")
        index: dict[str, SkinportSnapshot] = {}
        observed_at = datetime.now(UTC)
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            market_hash_name = entry.get("market_hash_name")
            min_price = entry.get("min_price")
            if not isinstance(market_hash_name, str) or min_price is None:
                continue
            try:
                price = _decimal(min_price)
                if price <= 0:
                    continue
                index[market_hash_name] = SkinportSnapshot(
                    quote=MarketQuote(
                        marketplace=self.name,
                        market_hash_name=market_hash_name,
                        price=price,
                        currency=self._currency,
                        observed_at=observed_at,
                        quote_type=QuoteType.LOWEST_ASK,
                    ),
                    median_price=_optional_decimal(entry.get("median_price")),
                    mean_price=_optional_decimal(entry.get("mean_price")),
                    max_price=_optional_decimal(entry.get("max_price")),
                    suggested_price=_optional_decimal(entry.get("suggested_price")),
                    quantity=entry.get("quantity") if isinstance(entry.get("quantity"), int) else None,
                    item_page=entry.get("item_page") if isinstance(entry.get("item_page"), str) else None,
                    market_page=entry.get("market_page") if isinstance(entry.get("market_page"), str) else None,
                )
            except (ValueError, TypeError):
                continue
        self._index = index


def _decimal(value: object) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("boolean is not a price")
    return Decimal(str(value))


def _optional_decimal(value: object) -> Decimal | None:
    return None if value is None else _decimal(value)
