"""Steam Community Market observations for the engine."""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import quote

import httpx

from engine.errors import (
    MarketplaceRateLimitError,
    MarketplaceResponseError,
    MarketplaceTimeoutError,
    MarketplaceUnavailableError,
)
from engine.models import Item, MarketQuote, QuoteType


@dataclass(frozen=True, slots=True)
class SteamSnapshot:
    quote: MarketQuote
    median_price: Decimal | None
    volume: int | None
    source: str = "priceoverview"


class SteamMarketplace:
    """Fetch Steam's current aggregate price overview for one exact item."""

    name = "steam"
    _PRICE_PATTERN = re.compile(r"\d[\d.,'\u00a0\u202f ]*")

    def __init__(
        self,
        *,
        country: str,
        currency_code: int,
        currency: str,
        app_id: int,
        user_agent: str,
        timeout_seconds: float,
        cache_ttl_seconds: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._country = country
        self._currency_code = currency_code
        self._currency = currency
        self._app_id = app_id
        self._user_agent = user_agent
        self._timeout_seconds = timeout_seconds
        self._cache_ttl_seconds = cache_ttl_seconds
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient()
        self._cache: dict[str, tuple[float, SteamSnapshot]] = {}
        self._request_lock = asyncio.Lock()

    async def fetch(self, item: Item) -> list[MarketQuote]:
        return [(await self.fetch_snapshot(item)).quote]

    async def fetch_snapshot(self, item: Item) -> SteamSnapshot:
        cached = self._cache.get(item.market_hash_name)
        if cached is not None and time.monotonic() - cached[0] < self._cache_ttl_seconds:
            return cached[1]
        async with self._request_lock:
            cached = self._cache.get(item.market_hash_name)
            if cached is not None and time.monotonic() - cached[0] < self._cache_ttl_seconds:
                return cached[1]
            snapshot = await self._request_snapshot(item)
            self._cache[item.market_hash_name] = (time.monotonic(), snapshot)
            return snapshot

    async def _request_snapshot(self, item: Item) -> SteamSnapshot:
        try:
            response = await self._client.get(
                "https://steamcommunity.com/market/priceoverview/",
                params={
                    "country": self._country,
                    "currency": self._currency_code,
                    "appid": self._app_id,
                    "market_hash_name": item.market_hash_name,
                },
                headers={
                    "User-Agent": self._user_agent,
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "Accept-Language": "en-GB,en;q=0.9",
                    "Referer": (
                        "https://steamcommunity.com/market/listings/"
                        f"{self._app_id}/{quote(item.market_hash_name, safe='')}"
                    ),
                },
                timeout=self._timeout_seconds,
            )
        except httpx.TimeoutException as error:
            raise MarketplaceTimeoutError("Steam request timed out") from error
        except httpx.RequestError as error:
            raise MarketplaceUnavailableError("Steam request failed") from error

        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            try:
                retry_after_seconds = None if retry_after is None else float(retry_after)
            except ValueError:
                retry_after_seconds = None
            raise MarketplaceRateLimitError(
                "Steam rate limit exceeded",
                retry_after_seconds=retry_after_seconds,
            )
        if response.status_code in {403, 503} or response.status_code >= 500:
            raise MarketplaceUnavailableError(
                f"Steam returned HTTP {response.status_code}"
            )
        if not response.is_success:
            raise MarketplaceResponseError(
                f"Steam rejected the request with HTTP {response.status_code}"
            )

        try:
            payload = response.json()
        except ValueError as error:
            raise MarketplaceResponseError("Steam returned invalid JSON") from error
        if not isinstance(payload, dict) or not payload.get("success"):
            raise MarketplaceResponseError(
                f"Steam returned no price data for '{item.market_hash_name}'."
            )
        lowest = payload.get("lowest_price")
        if not isinstance(lowest, str) or not lowest:
            raise MarketplaceResponseError(
                f"Steam has no listings for '{item.market_hash_name}'."
            )
        median = payload.get("median_price")
        volume = payload.get("volume")
        try:
            volume_digits = (
                re.sub(r"\D", "", volume) if isinstance(volume, str) else None
            )
            parsed_volume = int(volume_digits) if volume_digits else None
            lowest_price = self._parse_price(lowest)
            median_price = self._parse_price(median) if isinstance(median, str) else None
        except ValueError as error:
            raise MarketplaceResponseError("Steam returned an invalid price overview") from error
        observed_at = datetime.now(UTC)
        return SteamSnapshot(
            quote=MarketQuote(
                marketplace=self.name,
                market_hash_name=item.market_hash_name,
                price=lowest_price,
                currency=self._currency,
                observed_at=observed_at,
                quote_type=QuoteType.LOWEST_ASK,
            ),
            median_price=median_price,
            volume=parsed_volume,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @classmethod
    def _parse_price(cls, value: str) -> Decimal:
        match = cls._PRICE_PATTERN.search(value)
        if match is None:
            raise ValueError(value)
        number = (
            match.group()
            .replace("\u00a0", "")
            .replace("\u202f", "")
            .replace(" ", "")
            .replace("'", "")
        )
        if not number:
            raise ValueError(value)

        if "." in number and "," in number:
            decimal_separator = "." if number.rfind(".") > number.rfind(",") else ","
            grouping_separator = "," if decimal_separator == "." else "."
            number = number.replace(grouping_separator, "").replace(decimal_separator, ".")
        elif "," in number or "." in number:
            separator = "," if "," in number else "."
            parts = number.split(separator)
            if len(parts[-1]) in {1, 2}:
                number = "".join(parts[:-1]) + "." + parts[-1]
            else:
                number = "".join(parts)
        return Decimal(number)
