from datetime import UTC
from decimal import Decimal

import httpx
import pytest

from engine.catalog import ByMykelItemCatalog, CatalogUnavailableError
from engine.marketplaces import SkinportMarketplace, SteamMarketplace
from engine.models import Item, QuoteType

ITEM = Item(market_hash_name="AK-47 | Redline (Field-Tested)")


@pytest.mark.asyncio
async def test_catalog_preserves_exact_prefix_then_contains_search_order(tmp_path) -> None:
    response_payload = {
        "one": {
            "market_hash_name": "AK-47 | Redline (Field-Tested)",
            "name": "AK-47 | Redline",
            "image": "https://example.test/redline.png",
        },
        "two": {"market_hash_name": "Redline Sticker", "name": "Redline Sticker"},
        "three": {"market_hash_name": "StatTrak™ AK-47 | Redline", "name": "StatTrak™ AK"},
    }

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response_payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        catalog = ByMykelItemCatalog(
            url="https://catalog.example/items",
            cache_path=tmp_path / "catalog.json",
            cache_ttl_seconds=3600,
            timeout_seconds=1,
            client=client,
        )
        exact = await catalog.search(ITEM.market_hash_name, limit=20)
        prefix = await catalog.search("red", limit=20)
        contains = await catalog.search("line", limit=20)

    assert [item.market_hash_name for item in exact] == [ITEM.market_hash_name]
    assert [item.market_hash_name for item in prefix] == ["Redline Sticker"]
    assert [item.market_hash_name for item in contains] == [
        "Redline Sticker",
        ITEM.market_hash_name,
        "StatTrak™ AK-47 | Redline",
    ]


@pytest.mark.asyncio
async def test_catalog_refetches_when_a_fresh_cache_is_corrupt(tmp_path) -> None:
    cache_path = tmp_path / "catalog.json"
    cache_path.write_text("{truncated", encoding="utf-8")

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "item": {
                    "market_hash_name": ITEM.market_hash_name,
                    "name": "AK-47 | Redline",
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        catalog = ByMykelItemCatalog(
            url="https://catalog.example/items",
            cache_path=cache_path,
            cache_ttl_seconds=3600,
            timeout_seconds=1,
            client=client,
        )
        result = await catalog.search("redline", limit=20)

    assert [item.market_hash_name for item in result] == [ITEM.market_hash_name]


@pytest.mark.asyncio
async def test_catalog_normalizes_remote_http_failures(tmp_path) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        catalog = ByMykelItemCatalog(
            url="https://catalog.example/items",
            cache_path=tmp_path / "catalog.json",
            cache_ttl_seconds=3600,
            timeout_seconds=1,
            client=client,
        )
        with pytest.raises(CatalogUnavailableError, match="HTTP 503"):
            await catalog.search("redline", limit=20)


@pytest.mark.asyncio
async def test_steam_provider_normalizes_an_aggregate_lowest_ask() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "success": True,
                "lowest_price": "£30.15",
                "median_price": "£31.00",
                "volume": "1,234",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        marketplace = SteamMarketplace(
            country="GB",
            currency_code=2,
            currency="GBP",
            app_id=730,
            user_agent="test-agent",
            timeout_seconds=1,
            client=client,
        )
        snapshot = await marketplace.fetch_snapshot(ITEM)

    assert requests[0].url.params["market_hash_name"] == ITEM.market_hash_name
    assert snapshot.quote.price == Decimal("30.15")
    assert snapshot.quote.currency == "GBP"
    assert snapshot.quote.quote_type is QuoteType.LOWEST_ASK
    assert snapshot.median_price == Decimal("31.00")
    assert snapshot.volume == 1234
    assert snapshot.quote.observed_at.tzinfo is UTC


@pytest.mark.asyncio
async def test_skinport_provider_preserves_snapshot_statistics(tmp_path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert dict(request.url.params) == {
            "app_id": "730",
            "currency": "GBP",
            "tradable": "1",
        }
        return httpx.Response(
            200,
            json=[
                {
                    "market_hash_name": ITEM.market_hash_name,
                    "min_price": 22.39,
                    "median_price": 23,
                    "mean_price": 23.5,
                    "max_price": 25,
                    "suggested_price": 22.75,
                    "quantity": 7,
                    "item_page": "https://skinport.example/item",
                    "market_page": "https://skinport.example/market",
                }
            ],
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        marketplace = SkinportMarketplace(
            currency="GBP",
            app_id=730,
            base_url="https://skinport.example/v1",
            cache_path=tmp_path / "skinport.json",
            cache_ttl_seconds=3600,
            timeout_seconds=1,
            client=client,
        )
        snapshot = await marketplace.fetch_snapshot(ITEM)

    assert snapshot is not None
    assert snapshot.quote.price == Decimal("22.39")
    assert snapshot.median_price == Decimal("23")
    assert snapshot.mean_price == Decimal("23.5")
    assert snapshot.quantity == 7
    assert snapshot.item_page == "https://skinport.example/item"


@pytest.mark.asyncio
async def test_skinport_refetches_when_a_fresh_cache_is_corrupt(tmp_path) -> None:
    cache_path = tmp_path / "skinport.json"
    cache_path.write_text("[truncated", encoding="utf-8")

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[{"market_hash_name": ITEM.market_hash_name, "min_price": 22.39}],
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        marketplace = SkinportMarketplace(
            currency="GBP",
            app_id=730,
            base_url="https://skinport.example/v1",
            cache_path=cache_path,
            cache_ttl_seconds=3600,
            timeout_seconds=1,
            client=client,
        )
        snapshot = await marketplace.fetch_snapshot(ITEM)

    assert snapshot is not None
    assert snapshot.quote.price == Decimal("22.39")
