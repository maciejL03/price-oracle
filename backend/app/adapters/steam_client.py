import re
from urllib.parse import quote

import httpx

from app.config import settings
from app.domain.models import SteamPrice
from app.ports.marketplaces import SteamMarket


class SteamPriceOverviewClient(SteamMarket):
    _PRICE_PATTERN = re.compile(r"[\d,.]+")

    async def get_price(self, market_hash_name: str) -> SteamPrice:
        params = {
            "country": settings.steam_country,
            "currency": settings.steam_currency,
            "appid": settings.steam_app_id,
            "market_hash_name": market_hash_name,
        }

        headers = {
            "User-Agent": settings.user_agent,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "en-GB,en;q=0.9",
            "Referer": (
                f"https://steamcommunity.com/market/listings/"
                f"{settings.steam_app_id}/{quote(market_hash_name)}"
            ),
        }

        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
            response = await client.get(
                "https://steamcommunity.com/market/priceoverview/",
                params=params,
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()

        if not data.get("success"):
            raise ValueError(f"Steam returned no price data for '{market_hash_name}'.")

        lowest = data.get("lowest_price")
        if not lowest:
            raise ValueError(f"Steam has no listings for '{market_hash_name}'.")

        median_raw = data.get("median_price")
        volume_raw = data.get("volume")

        return SteamPrice(
            lowest_price=self._parse_price(lowest),
            median_price=self._parse_price(median_raw) if median_raw else None,
            volume=int(volume_raw.replace(",", "")) if volume_raw else None,
        )

    @classmethod
    def _parse_price(cls, value: str) -> float:
        match = cls._PRICE_PATTERN.search(value.replace("\xa3", "").replace("£", ""))
        if not match:
            raise ValueError(f"Unable to parse Steam price: {value!r}")
        return float(match.group().replace(",", ""))
