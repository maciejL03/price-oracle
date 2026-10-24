"""Frontend-compatible routes backed by engine services."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import ValidationError

from engine.alerts import AlertCondition, AlertRule
from engine.catalog import ItemCatalog
from engine.errors import MarketplaceError
from engine.market_engine import MarketEngine
from engine.models import Item, MarketQuote, QuoteType
from engine.services import ComparisonNotFoundError, MarketplaceComparisonService
from engine.watchlists import Watchlist, WatchlistNotFoundError

from .dependencies import get_catalog, get_comparisons, get_engine
from .schemas import (
    CatalogItemSchema,
    ComparisonResponse,
    AlertEventCollectionResponse,
    AlertEventResponse,
    AlertRuleCollectionResponse,
    AlertRuleCreateRequest,
    AlertRuleResponse,
    SearchResponse,
    StoredComparisonResponse,
    SystemStatusResponse,
    MarketplaceResponse,
    NotificationDeliveryResponse,
    QuoteCollectionResponse,
    QuoteResponse,
    SkinportPriceSchema,
    SteamPriceSchema,
    WatchlistCollectionResponse,
    WatchlistCreateRequest,
    WatchlistItemRequest,
    WatchlistItemResponse,
    WatchlistResponse,
    WatchlistUpdateRequest,
)

router = APIRouter(prefix="/api", tags=["api"])


@router.get("/health")
async def health(engine: MarketEngine = Depends(get_engine)) -> dict[str, str]:
    if not engine.running:
        raise HTTPException(status_code=503, detail="Market engine is not running")
    return {"status": "ok"}


@router.get("/status", response_model=SystemStatusResponse)
async def status(engine: MarketEngine = Depends(get_engine)) -> SystemStatusResponse:
    mode = engine.operating_mode
    return SystemStatusResponse(
        status="ok" if engine.running else "unavailable",
        operating_mode=mode,
        data_kind="simulated" if mode == "demo" else "live",
        marketplaces=list(engine.registry.names),
        notifications=(
            "simulated"
            if mode == "demo"
            else "discord" if engine.notifications is not None else "disabled"
        ),
    )


@router.get("/items/search", response_model=SearchResponse)
async def search_items(
    q: str = Query(min_length=1, max_length=120, description="Partial item name"),
    limit: int = Query(default=20, ge=1, le=50),
    catalog: ItemCatalog = Depends(get_catalog),
) -> SearchResponse:
    items = await catalog.search(q, limit=limit)
    return SearchResponse(
        items=[
            CatalogItemSchema(
                market_hash_name=item.market_hash_name,
                name=item.name,
                image=item.image,
            )
            for item in items
        ]
    )


@router.get("/prices/compare", response_model=ComparisonResponse)
async def compare_prices(
    market_hash_name: str = Query(min_length=1, max_length=200),
    comparisons: MarketplaceComparisonService = Depends(get_comparisons),
) -> ComparisonResponse:
    try:
        comparison = await comparisons.compare(market_hash_name)
    except ComparisonNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except MarketplaceError as error:
        raise HTTPException(status_code=502, detail=f"Marketplace error: {error}") from error

    return ComparisonResponse(
        market_hash_name=comparison.item.market_hash_name,
        item_name=comparison.item.name,
        item_image=comparison.item.image,
        steam=SteamPriceSchema(
            lowest_price=comparison.steam.quote.price,
            median_price=comparison.steam.median_price,
            volume=comparison.steam.volume,
            currency=comparison.steam.quote.currency,
            source=comparison.steam.source,
        ),
        skinport=SkinportPriceSchema(
            min_price=comparison.skinport.quote.price,
            median_price=comparison.skinport.median_price,
            mean_price=comparison.skinport.mean_price,
            max_price=comparison.skinport.max_price,
            suggested_price=comparison.skinport.suggested_price,
            quantity=comparison.skinport.quantity,
            item_page=comparison.skinport.item_page,
            market_page=comparison.skinport.market_page,
            currency=comparison.skinport.quote.currency,
        ),
        cheaper=comparison.cheaper,
        difference=comparison.ranking.spread,
        percentage=comparison.percentage,
    )


@router.get("/marketplaces", response_model=MarketplaceResponse)
async def list_marketplaces(engine: MarketEngine = Depends(get_engine)) -> MarketplaceResponse:
    return MarketplaceResponse(marketplaces=list(engine.registry.names))


@router.get(
    "/items/{market_hash_name}/comparison",
    response_model=StoredComparisonResponse,
)
async def compare_stored_prices(
    market_hash_name: str,
    currency: str = Query(default="GBP", pattern=r"^[A-Z]{3}$"),
    quote_type: QuoteType = QuoteType.LOWEST_ASK,
    engine: MarketEngine = Depends(get_engine),
) -> StoredComparisonResponse:
    item = _item_or_422(market_hash_name)
    quotes = await engine.storage.get_latest_quotes(
        item, currency=currency, quote_type=quote_type
    )
    comparison = engine.pricing.compare(
        item,
        quotes,
        currency=currency,
        quote_type=quote_type,
        as_of=datetime.now(UTC),
    )
    return StoredComparisonResponse(
        market_hash_name=market_hash_name,
        currency=currency,
        quote_type=quote_type,
        simulated=(
            bool(comparison.quotes)
            and all(
                quote.marketplace in engine.simulated_marketplaces
                for quote in comparison.quotes
            )
        ),
        quotes=[_quote_response(quote) for quote in comparison.quotes],
        lowest_price=comparison.lowest_price,
        highest_price=comparison.highest_price,
        spread=comparison.spread,
        excluded_count=len(comparison.excluded_quotes),
    )


@router.get("/items/{market_hash_name}/quotes", response_model=QuoteCollectionResponse)
async def get_latest_quotes(
    market_hash_name: str,
    marketplace: str | None = None,
    currency: str | None = Query(default=None, pattern=r"^[A-Z]{3}$"),
    quote_type: QuoteType | None = None,
    engine: MarketEngine = Depends(get_engine),
) -> QuoteCollectionResponse:
    quotes = await engine.storage.get_latest_quotes(
        _item_or_422(market_hash_name),
        marketplace=marketplace,
        currency=currency,
        quote_type=quote_type,
    )
    return QuoteCollectionResponse(quotes=[_quote_response(quote) for quote in quotes])


@router.get("/items/{market_hash_name}/history", response_model=QuoteCollectionResponse)
async def get_price_history(
    market_hash_name: str,
    marketplace: str | None = None,
    currency: str | None = Query(default=None, pattern=r"^[A-Z]{3}$"),
    quote_type: QuoteType | None = None,
    observed_from: datetime | None = None,
    observed_to: datetime | None = None,
    limit: int | None = Query(default=None, ge=1, le=10_000),
    engine: MarketEngine = Depends(get_engine),
) -> QuoteCollectionResponse:
    try:
        quotes = await engine.storage.get_price_history(
            _item_or_422(market_hash_name),
            marketplace=marketplace,
            currency=currency,
            quote_type=quote_type,
            observed_from=observed_from,
            observed_to=observed_to,
            limit=limit,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return QuoteCollectionResponse(quotes=[_quote_response(quote) for quote in quotes])


@router.post("/watchlists", response_model=WatchlistResponse, status_code=201)
async def create_watchlist(
    payload: WatchlistCreateRequest,
    engine: MarketEngine = Depends(get_engine),
) -> WatchlistResponse:
    watchlist = await engine.watchlists.create_watchlist(
        payload.name, interval_seconds=payload.interval_seconds
    )
    return _watchlist_response(watchlist)


@router.get("/watchlists", response_model=WatchlistCollectionResponse)
async def list_watchlists(
    engine: MarketEngine = Depends(get_engine),
) -> WatchlistCollectionResponse:
    return WatchlistCollectionResponse(
        watchlists=[_watchlist_response(watchlist) for watchlist in await engine.watchlists.list_watchlists()]
    )


@router.patch("/watchlists/{watchlist_id}", response_model=WatchlistResponse)
async def set_watchlist_interval(
    watchlist_id: UUID,
    payload: WatchlistUpdateRequest,
    engine: MarketEngine = Depends(get_engine),
) -> WatchlistResponse:
    try:
        watchlist = await engine.watchlists.set_interval(
            watchlist_id, payload.interval_seconds
        )
    except WatchlistNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return _watchlist_response(watchlist)


@router.delete("/watchlists/{watchlist_id}", status_code=204)
async def delete_watchlist(
    watchlist_id: UUID,
    engine: MarketEngine = Depends(get_engine),
) -> Response:
    try:
        await engine.watchlists.delete_watchlist(watchlist_id)
    except WatchlistNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return Response(status_code=204)


@router.put("/watchlists/{watchlist_id}/items", response_model=WatchlistResponse)
async def add_watchlist_item(
    watchlist_id: UUID,
    payload: WatchlistItemRequest,
    engine: MarketEngine = Depends(get_engine),
) -> WatchlistResponse:
    try:
        watchlist = await engine.watchlists.add_item(
            watchlist_id,
            _item_or_422(payload.market_hash_name),
            payload.marketplaces,
        )
    except WatchlistNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except MarketplaceError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _watchlist_response(watchlist)


@router.delete("/watchlists/{watchlist_id}/items/{market_hash_name}", response_model=WatchlistResponse)
async def remove_watchlist_item(
    watchlist_id: UUID,
    market_hash_name: str,
    engine: MarketEngine = Depends(get_engine),
) -> WatchlistResponse:
    try:
        watchlist = await engine.watchlists.remove_item(
            watchlist_id, _item_or_422(market_hash_name)
        )
    except WatchlistNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return _watchlist_response(watchlist)


@router.post("/alerts", response_model=AlertRuleResponse, status_code=201)
async def create_alert_rule(
    payload: AlertRuleCreateRequest,
    engine: MarketEngine = Depends(get_engine),
) -> AlertRuleResponse:
    rule = AlertRule(
        id=uuid4(),
        item=_item_or_422(payload.market_hash_name),
        marketplace=payload.marketplace,
        currency=payload.currency,
        quote_type=QuoteType.LOWEST_ASK,
        condition=AlertCondition(payload.condition),
        threshold=payload.threshold,
    )
    await engine.alerts.create_rule(rule)
    return _alert_rule_response(rule)


@router.get("/alerts", response_model=AlertRuleCollectionResponse)
async def list_alert_rules(
    engine: MarketEngine = Depends(get_engine),
) -> AlertRuleCollectionResponse:
    return AlertRuleCollectionResponse(
        rules=[_alert_rule_response(rule) for rule in await engine.alerts.list_rules()]
    )


@router.get("/alerts/events", response_model=AlertEventCollectionResponse)
async def list_alert_events(
    rule_id: UUID | None = None,
    engine: MarketEngine = Depends(get_engine),
) -> AlertEventCollectionResponse:
    events = await engine.storage.list_alert_events(rule_id=rule_id)
    return AlertEventCollectionResponse(
        events=[
            AlertEventResponse(
                id=event.id,
                rule_id=event.rule_id,
                quote=_quote_response(event.quote),
                condition=event.condition.value,
                threshold=event.threshold,
                triggered_at=event.triggered_at,
            )
            for event in events
        ]
    )


@router.delete("/alerts/{rule_id}", status_code=204)
async def delete_alert_rule(
    rule_id: UUID,
    engine: MarketEngine = Depends(get_engine),
) -> Response:
    if not await engine.alerts.delete_rule(rule_id):
        raise HTTPException(status_code=404, detail=f"Alert rule does not exist: {rule_id}")
    return Response(status_code=204)


@router.get("/notifications/{event_id}", response_model=NotificationDeliveryResponse)
async def get_notification_delivery(
    event_id: UUID,
    engine: MarketEngine = Depends(get_engine),
) -> NotificationDeliveryResponse:
    delivery = await engine.storage.get_notification_delivery(event_id)
    if delivery is None:
        raise HTTPException(status_code=404, detail=f"Notification delivery does not exist: {event_id}")
    return NotificationDeliveryResponse(
        event_id=delivery.event_id,
        channel=delivery.channel,
        status=delivery.status.value,
        attempts=delivery.attempts,
        started_at=delivery.started_at,
        completed_at=delivery.completed_at,
        failure_code=None if delivery.failure_code is None else delivery.failure_code.value,
        failure_detail=delivery.failure_detail,
    )


def _quote_response(quote: MarketQuote) -> QuoteResponse:
    if not isinstance(quote, MarketQuote):
        raise TypeError("expected MarketQuote")
    return QuoteResponse(
        marketplace=quote.marketplace,
        market_hash_name=quote.market_hash_name,
        price=quote.price,
        currency=quote.currency,
        observed_at=quote.observed_at,
        quote_type=quote.quote_type,
        external_listing_id=quote.external_listing_id,
    )


def _item_or_422(market_hash_name: str) -> Item:
    try:
        return Item(market_hash_name=market_hash_name)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=error.errors()) from error


def _watchlist_response(watchlist: Watchlist) -> WatchlistResponse:
    if not isinstance(watchlist, Watchlist):
        raise TypeError("expected Watchlist")
    return WatchlistResponse(
        id=watchlist.id,
        name=watchlist.name,
        interval_seconds=watchlist.interval_seconds,
        items=[
            WatchlistItemResponse(
                market_hash_name=entry.item.market_hash_name,
                marketplaces=sorted(entry.marketplaces),
            )
            for entry in watchlist.items
        ],
    )


def _alert_rule_response(rule: AlertRule) -> AlertRuleResponse:
    return AlertRuleResponse(
        id=rule.id,
        market_hash_name=rule.item.market_hash_name,
        marketplace=rule.marketplace,
        currency=rule.currency,
        quote_type=rule.quote_type,
        condition=rule.condition.value,
        threshold=rule.threshold,
    )
