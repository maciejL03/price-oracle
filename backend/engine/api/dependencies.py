"""Explicit FastAPI dependencies for the single application-owned engine."""

from fastapi import Request

from engine.catalog import ItemCatalog
from engine.market_engine import MarketEngine
from engine.services import MarketplaceComparisonService


def get_engine(request: Request) -> MarketEngine:
    return request.app.state.market_engine  # type: ignore[no-any-return]


def get_comparisons(request: Request) -> MarketplaceComparisonService:
    comparisons = get_engine(request).comparisons
    if comparisons is None:
        raise RuntimeError("market comparison services are not configured")
    return comparisons


def get_catalog(request: Request) -> ItemCatalog:
    engine = get_engine(request)
    catalog = getattr(engine, "catalog", None)
    if catalog is not None:
        return catalog
    comparisons = getattr(engine, "comparisons", None)
    if comparisons is not None:
        return comparisons
    raise RuntimeError("item catalog is not configured")
