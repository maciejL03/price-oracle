from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest

from engine.errors import (
    MarketplaceAuthenticationError,
    MarketplaceRateLimitError,
    MarketplaceResponseError,
    MarketplaceTimeoutError,
    MarketplaceUnavailableError,
)
from engine.marketplaces import CSFloatMarketplace, MarketplaceRegistry
from engine.models import Item, QuoteType

ITEM = Item(market_hash_name="AK-47 | Redline (Field-Tested)")
OBSERVED_AT = datetime(2026, 10, 19, 12, 30, tzinfo=UTC)


def listing(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "id": "listing-123",
        "price": 12345,
        "state": "listed",
        "type": "buy_now",
        "created_at": "2026-10-19T11:30:00Z",
        "item": {
            "market_hash_name": ITEM.market_hash_name,
            "float_value": 0.234567,
            "paint_seed": 123,
            "is_stattrak": False,
            "is_souvenir": False,
        },
    }
    value.update(overrides)
    return value


def marketplace_with(handler: httpx.MockTransport) -> tuple[CSFloatMarketplace, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=handler)
    marketplace = CSFloatMarketplace(
        currency="USD", api_key="secret-key", client=client, _clock=lambda: OBSERVED_AT
    )
    return marketplace, client


@pytest.mark.asyncio
async def test_fetch_requests_and_normalizes_individual_listings() -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": [listing()], "cursor": "next"})

    marketplace, client = marketplace_with(httpx.MockTransport(handle))
    async with client:
        quotes = await marketplace.fetch(ITEM)

    assert len(requests) == 1
    request = requests[0]
    assert str(request.url).startswith("https://csfloat.com/api/v1/listings?")
    assert dict(request.url.params) == {
        "market_hash_name": ITEM.market_hash_name,
        "type": "buy_now",
        "sort_by": "lowest_price",
        "limit": "50",
    }
    assert request.headers["Authorization"] == "secret-key"
    assert request.headers["Accept"] == "application/json"

    assert len(quotes) == 1
    quote = quotes[0]
    assert quote.marketplace == "csfloat"
    assert quote.market_hash_name == ITEM.market_hash_name
    assert quote.price == Decimal("123.45")
    assert quote.currency == "USD"
    assert quote.observed_at == OBSERVED_AT
    assert quote.quote_type is QuoteType.INDIVIDUAL_LISTING
    assert quote.external_listing_id == "listing-123"


@pytest.mark.asyncio
async def test_fetch_does_not_send_authorization_without_key() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert "Authorization" not in request.headers
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        marketplace = CSFloatMarketplace(currency="USD", client=client)
        assert await marketplace.fetch(ITEM) == []


@pytest.mark.asyncio
async def test_fetch_accepts_documented_top_level_listing_array() -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=[listing(id=987)]))
    marketplace, client = marketplace_with(transport)

    async with client:
        quotes = await marketplace.fetch(ITEM)

    assert quotes[0].external_listing_id == "987"


@pytest.mark.asyncio
async def test_fetch_preserves_minor_unit_precision() -> None:
    raw_price = 123456789012345678901
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, json={"data": [listing(price=raw_price)]})
    )
    marketplace, client = marketplace_with(transport)

    async with client:
        quote = (await marketplace.fetch(ITEM))[0]

    assert quote.price == Decimal("1234567890123456789.01")


@pytest.mark.asyncio
async def test_fetch_preserves_exact_item_variants() -> None:
    stattrak = Item(market_hash_name="StatTrak™ AK-47 | Redline (Field-Tested)")
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            json={"data": [listing(item={"market_hash_name": stattrak.market_hash_name})]},
        )
    )
    marketplace, client = marketplace_with(transport)

    async with client:
        quote = (await marketplace.fetch(stattrak))[0]

    assert quote.market_hash_name == stattrak.market_hash_name
    assert quote.market_hash_name != ITEM.market_hash_name


@pytest.mark.asyncio
async def test_fetch_rejects_different_item_variant() -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            json={
                "data": [
                    listing(
                        item={
                            "market_hash_name": "StatTrak™ AK-47 | Redline (Field-Tested)"
                        }
                    )
                ]
            },
        )
    )
    marketplace, client = marketplace_with(transport)

    async with client:
        with pytest.raises(MarketplaceResponseError, match="different item variant"):
            await marketplace.fetch(ITEM)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("entry", "message"),
    [
        (listing(id=None), "id"),
        (listing(id=""), "id"),
        (listing(price=None), "price"),
        (listing(price=1.25), "price"),
        (listing(price=0), "price"),
        (listing(type="auction"), "non-buy-now"),
        (listing(state="sold"), "inactive"),
        (listing(item=None), "item"),
        (listing(item={}), "different item variant"),
    ],
)
async def test_fetch_rejects_malformed_or_contradictory_listing(
    entry: dict[str, Any], message: str
) -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, json={"data": [entry]})
    )
    marketplace, client = marketplace_with(transport)

    async with client:
        with pytest.raises(MarketplaceResponseError, match=message):
            await marketplace.fetch(ITEM)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"listings": []},
        {"data": {}},
        {"data": [None]},
        "unexpected",
    ],
)
async def test_fetch_rejects_unexpected_response_structures(payload: Any) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    marketplace, client = marketplace_with(transport)

    async with client:
        with pytest.raises(MarketplaceResponseError, match="response|listing"):
            await marketplace.fetch(ITEM)


@pytest.mark.asyncio
async def test_fetch_rejects_invalid_json() -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, content=b"not json", headers={"Content-Type": "text/plain"})
    )
    marketplace, client = marketplace_with(transport)

    async with client:
        with pytest.raises(MarketplaceResponseError, match="invalid JSON"):
            await marketplace.fetch(ITEM)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_fetch_maps_authentication_failures(status: int) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(status))
    marketplace, client = marketplace_with(transport)

    async with client:
        with pytest.raises(MarketplaceAuthenticationError, match=str(status)):
            await marketplace.fetch(ITEM)


@pytest.mark.asyncio
async def test_fetch_maps_rate_limit_and_retry_after() -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(429, headers={"Retry-After": "2.5"})
    )
    marketplace, client = marketplace_with(transport)

    async with client:
        with pytest.raises(MarketplaceRateLimitError) as captured:
            await marketplace.fetch(ITEM)

    assert captured.value.retry_after_seconds == 2.5
    assert isinstance(captured.value, ConnectionError)


@pytest.mark.asyncio
async def test_fetch_maps_http_date_retry_after() -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            429, headers={"Retry-After": "Mon, 19 Oct 2026 12:30:05 GMT"}
        )
    )
    marketplace, client = marketplace_with(transport)

    async with client:
        with pytest.raises(MarketplaceRateLimitError) as captured:
            await marketplace.fetch(ITEM)

    assert captured.value.retry_after_seconds == 5.0


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [500, 502, 503])
async def test_fetch_maps_temporary_provider_failures(status: int) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(status))
    marketplace, client = marketplace_with(transport)

    async with client:
        with pytest.raises(MarketplaceUnavailableError, match=str(status)):
            await marketplace.fetch(ITEM)


@pytest.mark.asyncio
async def test_fetch_maps_non_retryable_http_failure() -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(400))
    marketplace, client = marketplace_with(transport)

    async with client:
        with pytest.raises(MarketplaceResponseError, match="400"):
            await marketplace.fetch(ITEM)


@pytest.mark.asyncio
async def test_fetch_maps_request_timeout() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow provider", request=request)

    marketplace, client = marketplace_with(httpx.MockTransport(timeout))
    async with client:
        with pytest.raises(MarketplaceTimeoutError, match="timed out"):
            await marketplace.fetch(ITEM)


@pytest.mark.asyncio
async def test_fetch_maps_network_failure_without_leaking_api_key() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("secret-key network error", request=request)

    marketplace, client = marketplace_with(httpx.MockTransport(fail))
    async with client:
        with pytest.raises(MarketplaceUnavailableError) as captured:
            await marketplace.fetch(ITEM)

    assert "secret-key" not in str(captured.value)


@pytest.mark.asyncio
async def test_aclose_does_not_close_injected_client() -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200)))
    marketplace = CSFloatMarketplace(currency="USD", client=client)

    await marketplace.aclose()

    assert not client.is_closed
    await client.aclose()


@pytest.mark.asyncio
async def test_context_manager_closes_owned_client() -> None:
    marketplace = CSFloatMarketplace(currency="USD")
    client = marketplace._client

    async with marketplace:
        assert not client.is_closed

    assert client.is_closed


@pytest.mark.asyncio
async def test_registry_accepts_csfloat_marketplace() -> None:
    client = httpx.AsyncClient()
    marketplace = CSFloatMarketplace(currency="USD", client=client)
    registry = MarketplaceRegistry()

    registry.register(marketplace)

    assert registry.get("csfloat") is marketplace
    await client.aclose()


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"currency": "usd"}, "currency"),
        ({"currency": "US"}, "currency"),
        ({"currency": 123}, "currency"),
        ({"currency": "USD", "api_key": ""}, "api_key"),
        ({"currency": "USD", "api_key": " key"}, "api_key"),
        ({"currency": "USD", "api_key": 123}, "api_key"),
        ({"currency": "USD", "timeout": 0}, "timeout"),
        ({"currency": "USD", "timeout": True}, "timeout"),
        ({"currency": "USD", "timeout": float("inf")}, "timeout"),
        ({"currency": "USD", "base_url": "  "}, "base_url"),
        ({"currency": "USD", "base_url": 123}, "base_url"),
    ],
)
def test_constructor_rejects_invalid_configuration(
    kwargs: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        CSFloatMarketplace(**kwargs)
