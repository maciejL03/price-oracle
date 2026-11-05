from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from engine.api import create_app
from engine.bootstrap import create_market_engine
from engine.catalog import CatalogItem, CatalogUnavailableError
from engine.config import EngineSettings
from engine.errors import MarketplaceUnavailableError
from engine.marketplaces import SkinportSnapshot, SteamSnapshot
from engine.models import Item, MarketQuote, QuoteType
from engine.services import (
    ComparisonNotFoundError,
    MarketplaceComparison,
    PricingService,
)

ITEM = CatalogItem(
    market_hash_name="AK-47 | Redline (Field-Tested)",
    name="AK-47 | Redline",
    image="https://example.test/redline.png",
)


def _comparison() -> MarketplaceComparison:
    now = datetime.now(UTC)
    item = Item(market_hash_name=ITEM.market_hash_name)
    steam = SteamSnapshot(
        quote=MarketQuote(
            marketplace="steam",
            market_hash_name=ITEM.market_hash_name,
            price=Decimal("30.15"),
            currency="GBP",
            observed_at=now,
            quote_type=QuoteType.LOWEST_ASK,
        ),
        median_price=Decimal("31.00"),
        volume=42,
    )
    skinport = SkinportSnapshot(
        quote=MarketQuote(
            marketplace="skinport",
            market_hash_name=ITEM.market_hash_name,
            price=Decimal("22.39"),
            currency="GBP",
            observed_at=now,
            quote_type=QuoteType.LOWEST_ASK,
        ),
        median_price=Decimal("23.00"),
        mean_price=Decimal("23.50"),
        max_price=Decimal("25.00"),
        suggested_price=Decimal("22.75"),
        quantity=7,
        item_page="https://skinport.example/item",
        market_page="https://skinport.example/market",
    )
    pricing = PricingService(max_quote_age=timedelta(minutes=5))
    return MarketplaceComparison(
        item=ITEM,
        steam=steam,
        skinport=skinport,
        ranking=pricing.compare(
            item,
            (steam.quote, skinport.quote),
            currency="GBP",
            quote_type=QuoteType.LOWEST_ASK,
            as_of=now,
        ),
    )


class FakeComparisons:
    def __init__(self) -> None:
        self.comparison = _comparison()
        self.error: Exception | None = None
        self.search_error: Exception | None = None

    async def search(self, query: str, *, limit: int) -> list[CatalogItem]:
        if self.search_error is not None:
            raise self.search_error
        return [ITEM][:limit] if query.lower() in "redline" else []

    async def compare(self, market_hash_name: str) -> MarketplaceComparison:
        if self.error is not None:
            raise self.error
        return self.comparison


class FakeEngine:
    def __init__(self, comparisons: FakeComparisons) -> None:
        self.comparisons = comparisons
        self.running = False
        self.starts = 0
        self.stops = 0

    async def start(self) -> None:
        self.starts += 1
        self.running = True

    async def stop(self) -> None:
        self.stops += 1
        self.running = False


def _client(comparisons: FakeComparisons | None = None) -> tuple[TestClient, FakeEngine]:
    fake_engine = FakeEngine(comparisons or FakeComparisons())
    app = create_app(
        engine_factory=lambda: fake_engine,  # type: ignore[arg-type]
        settings=EngineSettings(
            _env_file=None,
            _secrets_dir=None,
            csfloat_enabled=False,
        ),
    )
    return TestClient(app), fake_engine


def test_health_is_backed_by_the_lifespan_managed_engine() -> None:
    client, engine = _client()
    with client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert engine.starts == engine.stops == 1


def test_search_contract_and_validation_are_frontend_compatible() -> None:
    client, _ = _client()
    with client:
        response = client.get("/api/items/search", params={"q": "red", "limit": 20})
        invalid = client.get("/api/items/search", params={"q": "", "limit": 0})

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "market_hash_name": ITEM.market_hash_name,
                "name": ITEM.name,
                "image": ITEM.image,
            }
        ]
    }
    assert invalid.status_code == 422


def test_comparison_contract_preserves_fields_and_decimal_serialization() -> None:
    client, _ = _client()
    with client:
        response = client.get(
            "/api/prices/compare", params={"market_hash_name": ITEM.market_hash_name}
        )

    assert response.status_code == 200
    assert response.json() == {
        "market_hash_name": ITEM.market_hash_name,
        "item_name": ITEM.name,
        "item_image": ITEM.image,
        "steam": {
            "lowest_price": 30.15,
            "median_price": 31.0,
            "volume": 42,
            "currency": "GBP",
            "source": "priceoverview",
        },
        "skinport": {
            "min_price": 22.39,
            "median_price": 23.0,
            "mean_price": 23.5,
            "max_price": 25.0,
            "suggested_price": 22.75,
            "quantity": 7,
            "item_page": "https://skinport.example/item",
            "market_page": "https://skinport.example/market",
            "currency": "GBP",
        },
        "cheaper": "skinport",
        "difference": 7.76,
        "percentage": 34.66,
    }


def test_comparison_not_found_and_marketplace_errors_keep_meaningful_statuses() -> None:
    comparisons = FakeComparisons()
    comparisons.error = ComparisonNotFoundError("Unknown item: test")
    client, _ = _client(comparisons)
    with client:
        missing = client.get("/api/prices/compare", params={"market_hash_name": "test"})

    comparisons = FakeComparisons()
    comparisons.error = MarketplaceUnavailableError("provider unavailable")
    client, _ = _client(comparisons)
    with client:
        unavailable = client.get(
            "/api/prices/compare", params={"market_hash_name": ITEM.market_hash_name}
        )

    assert missing.status_code == 404
    assert missing.json() == {"detail": "Unknown item: test"}
    assert unavailable.status_code == 502
    assert unavailable.json() == {"detail": "Marketplace error: provider unavailable"}


def test_catalog_failures_are_reported_as_upstream_errors() -> None:
    comparisons = FakeComparisons()
    comparisons.search_error = CatalogUnavailableError("catalog unavailable")
    client, _ = _client(comparisons)
    with client:
        search = client.get("/api/items/search", params={"q": "red"})

    comparisons = FakeComparisons()
    comparisons.error = CatalogUnavailableError("catalog unavailable")
    client, _ = _client(comparisons)
    with client:
        comparison = client.get(
            "/api/prices/compare", params={"market_hash_name": ITEM.market_hash_name}
        )

    assert search.status_code == 502
    assert search.json() == {"detail": "Catalog error: catalog unavailable"}
    assert comparison.status_code == 502
    assert comparison.json() == {"detail": "Catalog error: catalog unavailable"}


def test_engine_native_routes_use_the_running_engine_services(tmp_path: Path) -> None:
    class FakeMarketplace:
        name = "fake"

        async def fetch(self, item: Item) -> list[MarketQuote]:
            return []

    settings = EngineSettings(
        _env_file=None,
        _secrets_dir=None,
        database_path=tmp_path / "engine.sqlite3",
        csfloat_enabled=False,
        worker_count=1,
        scheduler_poll_seconds=0.001,
    )
    engine = create_market_engine(settings, marketplaces=[FakeMarketplace()])
    app = create_app(engine_factory=lambda: engine, settings=settings)

    with TestClient(app) as client:
        marketplaces = client.get("/api/marketplaces")
        created_watchlist = client.post(
            "/api/watchlists", json={"name": "Targets", "interval_seconds": 60}
        )
        watchlist_id = created_watchlist.json()["id"]
        updated_watchlist = client.put(
            f"/api/watchlists/{watchlist_id}/items",
            json={"market_hash_name": ITEM.market_hash_name, "marketplaces": ["fake"]},
        )
        history = client.get(f"/api/items/{ITEM.market_hash_name}/history")
        created_alert = client.post(
            "/api/alerts",
            json={
                "market_hash_name": ITEM.market_hash_name,
                "marketplace": "fake",
                "currency": "GBP",
                "condition": "below",
                "threshold": "20.00",
            },
        )
        unknown_alert = client.post(
            "/api/alerts",
            json={
                "market_hash_name": ITEM.market_hash_name,
                "marketplace": "missing",
                "currency": "GBP",
                "condition": "below",
                "threshold": "20.00",
            },
        )
        invalid_alert = client.post(
            "/api/alerts",
            json={
                "market_hash_name": ITEM.market_hash_name,
                "marketplace": "NOT VALID",
                "currency": "GBP",
                "condition": "below",
                "threshold": "20.00",
            },
        )
        invalid_watchlist = client.post(
            "/api/watchlists", json={"name": "   ", "interval_seconds": 60}
        )
        listed_alerts = client.get("/api/alerts")

    assert marketplaces.json() == {"marketplaces": ["fake"]}
    assert created_watchlist.status_code == 201
    assert updated_watchlist.status_code == 200
    assert updated_watchlist.json()["items"] == [
        {"market_hash_name": ITEM.market_hash_name, "marketplaces": ["fake"]}
    ]
    assert history.status_code == 200
    assert history.json() == {"quotes": []}
    assert created_alert.status_code == 201
    assert unknown_alert.status_code == 422
    assert unknown_alert.json() == {
        "detail": "Marketplace is not registered: missing"
    }
    assert invalid_alert.status_code == 422
    assert invalid_watchlist.status_code == 422
    assert listed_alerts.json()["rules"] == [created_alert.json()]


def test_concurrent_search_requests_share_one_running_engine() -> None:
    client, engine = _client()
    with client:
        with ThreadPoolExecutor(max_workers=8) as executor:
            responses = list(
                executor.map(
                    lambda _: client.get("/api/items/search", params={"q": "red"}),
                    range(16),
                )
            )

    assert all(response.status_code == 200 for response in responses)
    assert all(response.json()["items"][0]["market_hash_name"] == ITEM.market_hash_name for response in responses)
    assert engine.starts == engine.stops == 1
