"""Explicit FastAPI dependencies for the single application-owned engine."""

from fastapi import Request

from engine.market_engine import MarketEngine
from engine.services import MarketplaceComparisonService


def get_engine(request: Request) -> MarketEngine:
    return request.app.state.market_engine  # type: ignore[no-any-return]


def get_comparisons(request: Request) -> MarketplaceComparisonService:
    comparisons = get_engine(request).comparisons
    if comparisons is None:
        raise RuntimeError("market comparison services are not configured")
    return comparisons
