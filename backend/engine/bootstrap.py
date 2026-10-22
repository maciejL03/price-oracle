"""Explicit dependency composition for the market engine."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

import httpx

from engine.alerts import AlertService
from engine.catalog import ByMykelItemCatalog
from engine.config import EngineSettings
from engine.jobs import FetchMarketJob, JobHandler, JobScheduler
from engine.market_engine import AsyncCloser, MarketEngine
from engine.marketplaces import (
    CSFloatMarketplace,
    Marketplace,
    MarketplaceRegistry,
    SkinportMarketplace,
    SteamMarketplace,
)
from engine.models import Item, MarketQuote, QuoteType
from engine.notifications import NotificationService
from engine.services import (
    CollectionService,
    MarketplaceComparisonService,
    PricingService,
)
from engine.storage import StorageService
from engine.watchlists import WatchlistService


def create_market_engine(
    settings: EngineSettings | None = None,
    *,
    marketplaces: Iterable[Marketplace] | None = None,
    notification_http_client: httpx.AsyncClient | None = None,
) -> MarketEngine:
    """Construct an engine without starting I/O or background tasks.

    Marketplace resources and the notification client are transferred to the
    returned engine, which closes them during shutdown.
    """
    actual_settings = settings or EngineSettings()
    storage = StorageService(
        actual_settings.database_path,
        busy_timeout_seconds=actual_settings.database_busy_timeout_seconds,
    )

    owned_closers: list[AsyncCloser] = []
    providers: tuple[Marketplace, ...]
    catalog: ByMykelItemCatalog | None = None
    comparisons: MarketplaceComparisonService | None = None
    if marketplaces is None:
        configured: list[Marketplace] = []
        if actual_settings.csfloat_enabled:
            csfloat = CSFloatMarketplace(
                currency=actual_settings.csfloat_currency,
                api_key=(
                    None
                    if actual_settings.csfloat_api_key is None
                    else actual_settings.csfloat_api_key.get_secret_value()
                ),
                timeout=actual_settings.csfloat_timeout_seconds,
            )
            configured.append(csfloat)
        catalog = ByMykelItemCatalog(
            url=actual_settings.catalog_url,
            cache_path=actual_settings.cache_dir / "bymykel_items.json",
            cache_ttl_seconds=actual_settings.catalog_cache_ttl_seconds,
            timeout_seconds=actual_settings.http_timeout_seconds,
        )
        steam = SteamMarketplace(
            country=actual_settings.steam_country,
            currency_code=actual_settings.steam_currency_code,
            currency=actual_settings.steam_currency,
            app_id=actual_settings.steam_app_id,
            user_agent=actual_settings.user_agent,
            timeout_seconds=actual_settings.http_timeout_seconds,
        )
        skinport = SkinportMarketplace(
            currency=actual_settings.skinport_currency,
            app_id=actual_settings.steam_app_id,
            base_url=actual_settings.skinport_base_url,
            cache_path=(
                actual_settings.cache_dir
                / f"skinport_{actual_settings.skinport_currency.lower()}.json"
            ),
            cache_ttl_seconds=actual_settings.skinport_cache_ttl_seconds,
            timeout_seconds=actual_settings.http_timeout_seconds,
        )
        configured.extend((steam, skinport))
        # The catalog is not a registered marketplace, so it needs its own
        # lifecycle entry. Registered provider closers are added once below.
        owned_closers.append(catalog.aclose)
        providers = tuple(configured)
    else:
        providers = tuple(marketplaces)

    registry = MarketplaceRegistry()
    for provider in providers:
        registry.register(provider)
        closer = getattr(provider, "aclose", None)
        if callable(closer):
            owned_closers.append(closer)

    collection = CollectionService(registry)
    max_quote_age = timedelta(seconds=actual_settings.quote_max_age_seconds)
    pricing = PricingService(max_quote_age=max_quote_age)
    if catalog is not None:
        comparisons = MarketplaceComparisonService(
            catalog,
            steam,
            skinport,
            pricing,
            currency=actual_settings.steam_currency,
        )
    alerts = AlertService(storage, max_quote_age=max_quote_age)

    notifications: NotificationService | None = None
    if actual_settings.discord_webhook_url is not None:
        http_client = notification_http_client
        if http_client is None:
            http_client = httpx.AsyncClient()
        owned_closers.append(http_client.aclose)
        notifications = NotificationService(
            storage,
            http_client,
            discord_webhook_url=actual_settings.discord_webhook_url,
        )

    async def process_persisted_quotes(quotes: tuple[MarketQuote, ...]) -> None:
        for observation in quotes:
            if observation.quote_type is not QuoteType.LOWEST_ASK:
                continue
            events = await alerts.evaluate(observation, as_of=datetime.now(UTC))
            if notifications is not None:
                for event in events:
                    await notifications.deliver(event)

    job_handler = JobHandler(
        worker_count=actual_settings.worker_count,
        queue_capacity=actual_settings.job_queue_capacity,
        shutdown_timeout=actual_settings.job_shutdown_timeout_seconds,
    )
    scheduler = JobScheduler(
        job_handler,
        poll_seconds=actual_settings.scheduler_poll_seconds,
    )

    def fetch_job_factory(marketplace: str, item: Item) -> FetchMarketJob:
        return FetchMarketJob(
            marketplace,
            item,
            collection,
            storage,
            after_persist=process_persisted_quotes,
        )

    watchlists = WatchlistService(
        storage,
        scheduler,
        registry,
        fetch_job_factory,
    )
    return MarketEngine(
        storage=storage,
        registry=registry,
        collection=collection,
        job_handler=job_handler,
        scheduler=scheduler,
        watchlists=watchlists,
        pricing=pricing,
        alerts=alerts,
        notifications=notifications,
        owned_resource_closers=owned_closers,
        catalog=catalog,
        comparisons=comparisons,
    )


__all__ = ["create_market_engine"]
