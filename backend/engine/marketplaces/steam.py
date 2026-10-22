"""Steam Community Market observations for the engine."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import quote

import httpx

from engine.errors import MarketplaceResponseError, MarketplaceTimeoutError, MarketplaceUnavailableError
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
    _PRICE_PATTERN = re.compile(r"[\d,.]+")

    def __init__(
        self,
        *,
        country: str,
        currency_code: int,
        currency: str,
        app_id: int,
        user_agent: str,
        timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._country = country
        self._currency_code = currency_code
        self._currency = currency
        self._app_id = app_id
        self._user_agent = user_agent
        self._timeout_seconds = timeout_seconds
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient()

    async def fetch(self, item: Item) -> list[MarketQuote]:
        return [(await self.fetch_snapshot(item)).quote]

    async def fetch_snapshot(self, item: Item) -> SteamSnapshot:
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
                        f"{self._app_id}/{quote(item.market_hash_name)}"
                    ),
                },
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
        except httpx.TimeoutException as error:
            raise MarketplaceTimeoutError("Steam request timed out") from error
        except httpx.HTTPStatusError as error:
            raise MarketplaceUnavailableError(
                f"Steam returned HTTP {error.response.status_code}"
            ) from error
        except httpx.RequestError as error:
            raise MarketplaceUnavailableError("Steam request failed") from error

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
            parsed_volume = int(volume.replace(",", "")) if isinstance(volume, str) else None
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
        match = cls._PRICE_PATTERN.search(value.replace("\xa3", "").replace("£", ""))
        if match is None:
            raise ValueError(value)
        return Decimal(match.group().replace(",", ""))
