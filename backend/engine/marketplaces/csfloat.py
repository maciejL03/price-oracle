from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from engine.errors import (
    MarketplaceAuthenticationError,
    MarketplaceRateLimitError,
    MarketplaceResponseError,
    MarketplaceTimeoutError,
    MarketplaceUnavailableError,
)
from engine.models import Item, MarketQuote, QuoteType

Clock = Callable[[], datetime]


class CSFloatMarketplace:
    """Read active buy-now listings from the CSFloat Market API."""

    name = "csfloat"
    _DEFAULT_BASE_URL = "https://csfloat.com/api/v1"
    _PAGE_SIZE = 50

    def __init__(
        self,
        *,
        currency: str,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = 10.0,
        base_url: str = _DEFAULT_BASE_URL,
        _clock: Clock | None = None,
    ) -> None:
        if (
            not isinstance(currency, str)
            or len(currency) != 3
            or not currency.isalpha()
            or not currency.isupper()
        ):
            raise ValueError("currency must be an uppercase three-letter ISO code")
        if api_key is not None and (
            not isinstance(api_key, str)
            or not api_key.strip()
            or api_key != api_key.strip()
        ):
            raise ValueError("api_key must be a non-empty string without surrounding whitespace")
        if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("base_url must not be empty")

        self._currency = currency
        self._api_key = api_key
        self._timeout = timeout
        self._base_url = base_url.rstrip("/")
        self._clock = _clock or (lambda: datetime.now(UTC))
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient()

    async def fetch(self, item: Item) -> list[MarketQuote]:
        """Return the first page of active buy-now listings for an exact item."""
        params = {
            "market_hash_name": item.market_hash_name,
            "type": "buy_now",
            "sort_by": "lowest_price",
            "limit": self._PAGE_SIZE,
        }
        headers = {"Accept": "application/json"}
        if self._api_key is not None:
            headers["Authorization"] = self._api_key

        response = await self._request(params, headers)
        observed_at = self._clock()
        payload = self._decode_json(response)
        listings = self._extract_listings(payload)
        return [self._normalize(entry, item, observed_at) for entry in listings]

    async def _request(
        self, params: Mapping[str, str | int], headers: Mapping[str, str]
    ) -> httpx.Response:
        try:
            response = await self._client.get(
                f"{self._base_url}/listings",
                params=params,
                headers=headers,
                timeout=self._timeout,
            )
        except httpx.TimeoutException as error:
            raise MarketplaceTimeoutError("CSFloat request timed out") from error
        except httpx.RequestError as error:
            raise MarketplaceUnavailableError("CSFloat request failed") from error

        if response.status_code in (401, 403):
            raise MarketplaceAuthenticationError(
                f"CSFloat rejected the request with HTTP {response.status_code}"
            )
        if response.status_code == 429:
            raise MarketplaceRateLimitError(
                "CSFloat rate limit exceeded",
                retry_after_seconds=self._retry_after(response.headers.get("Retry-After")),
            )
        if response.status_code >= 500:
            raise MarketplaceUnavailableError(
                f"CSFloat is temporarily unavailable (HTTP {response.status_code})"
            )
        if not response.is_success:
            raise MarketplaceResponseError(
                f"CSFloat rejected the request with HTTP {response.status_code}"
            )
        return response

    @staticmethod
    def _decode_json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError as error:
            raise MarketplaceResponseError("CSFloat returned invalid JSON") from error

    @staticmethod
    def _extract_listings(payload: Any) -> Sequence[Mapping[str, Any]]:
        raw_listings: Any
        if isinstance(payload, list):
            raw_listings = payload
        elif isinstance(payload, dict) and "data" in payload:
            raw_listings = payload["data"]
        else:
            raise MarketplaceResponseError(
                "CSFloat response must be a listing array or a data envelope"
            )

        if not isinstance(raw_listings, list):
            raise MarketplaceResponseError("CSFloat response data must be a listing array")
        if not all(isinstance(entry, dict) for entry in raw_listings):
            raise MarketplaceResponseError("CSFloat listing entries must be objects")
        return raw_listings

    def _normalize(
        self, entry: Mapping[str, Any], requested_item: Item, observed_at: datetime
    ) -> MarketQuote:
        listing_id = entry.get("id")
        if isinstance(listing_id, bool) or not isinstance(listing_id, (str, int)):
            raise MarketplaceResponseError("CSFloat listing id is missing or invalid")
        external_listing_id = str(listing_id)
        if not external_listing_id:
            raise MarketplaceResponseError("CSFloat listing id must not be empty")

        price = entry.get("price")
        if isinstance(price, bool) or not isinstance(price, int) or price <= 0:
            raise MarketplaceResponseError(
                "CSFloat listing price must be a positive integer in minor units"
            )

        listing_type = entry.get("type")
        if listing_type != "buy_now":
            raise MarketplaceResponseError("CSFloat returned a non-buy-now listing")
        state = entry.get("state")
        if state is not None and state != "listed":
            raise MarketplaceResponseError("CSFloat returned an inactive listing")

        raw_item = entry.get("item")
        if not isinstance(raw_item, dict):
            raise MarketplaceResponseError("CSFloat listing item is missing or invalid")
        market_hash_name = raw_item.get("market_hash_name")
        if market_hash_name != requested_item.market_hash_name:
            raise MarketplaceResponseError(
                "CSFloat returned a different item variant than requested"
            )

        return MarketQuote(
            marketplace=self.name,
            market_hash_name=market_hash_name,
            price=Decimal(price) / Decimal(100),
            currency=self._currency,
            observed_at=observed_at,
            quote_type=QuoteType.INDIVIDUAL_LISTING,
            external_listing_id=external_listing_id,
        )

    def _retry_after(self, value: str | None) -> float | None:
        if value is None:
            return None
        try:
            seconds = float(value)
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(value)
            except (TypeError, ValueError):
                return None
            if retry_at.tzinfo is None:
                return None
            seconds = (retry_at - self._clock()).total_seconds()

        if not math.isfinite(seconds):
            return None
        return max(0.0, seconds)

    async def aclose(self) -> None:
        """Close the HTTP client only when this instance created it."""
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> CSFloatMarketplace:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()
