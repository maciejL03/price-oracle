from decimal import Decimal
from urllib.parse import quote

import httpx
import pytest

from engine.errors import (
    MarketplaceRateLimitError,
    MarketplaceResponseError,
    MarketplaceUnavailableError,
)
from engine.marketplaces import SteamMarketplace
from engine.models import Item


def _marketplace(client: httpx.AsyncClient, **overrides: object) -> SteamMarketplace:
    values = {
        "country": "DE",
        "currency_code": 3,
        "currency": "EUR",
        "app_id": 730,
        "user_agent": "price-oracle-test",
        "timeout_seconds": 1.0,
        "cache_ttl_seconds": 60.0,
        "client": client,
    }
    values.update(overrides)
    return SteamMarketplace(**values)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_localized_price_and_exact_variant_are_normalized_and_cached() -> None:
    requests: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "success": True,
                "lowest_price": "1.234,56€",
                "median_price": "1.200,10€",
                "volume": "1.234",
            },
        )

    item = Item(market_hash_name="StatTrak™ AK-47 | Redline (Field-Tested)")
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        marketplace = _marketplace(client)
        first = await marketplace.fetch_snapshot(item)
        second = await marketplace.fetch_snapshot(item)

    assert len(requests) == 1
    assert requests[0].url.params["appid"] == "730"
    assert requests[0].url.params["market_hash_name"] == item.market_hash_name
    assert quote(item.market_hash_name) in requests[0].headers["Referer"]
    assert first is second
    assert first.quote.price == Decimal("1234.56")
    assert first.median_price == Decimal("1200.10")
    assert first.volume == 1234


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "payload", "error"),
    [
        (429, {"success": False}, MarketplaceRateLimitError),
        (403, {"success": False}, MarketplaceUnavailableError),
        (200, {"success": False}, MarketplaceResponseError),
        (200, {"success": True}, MarketplaceResponseError),
    ],
)
async def test_provider_failures_remain_distinct(
    status: int, payload: object, error: type[Exception]
) -> None:
    async def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload, headers={"Retry-After": "9"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(error):
            await _marketplace(client).fetch(
                Item(market_hash_name="AK-47 | Redline (Field-Tested)")
            )


@pytest.mark.asyncio
async def test_invalid_json_is_not_reported_as_an_empty_market() -> None:
    async def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>blocked</html>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(MarketplaceResponseError, match="invalid JSON"):
            await _marketplace(client).fetch(
                Item(market_hash_name="Souvenir AWP | Pink DDPAT (Field-Tested)")
            )
