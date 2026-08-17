from fastapi import APIRouter, Depends, HTTPException, Query

from app.adapters.bymykel_catalog import ByMykelCatalog
from app.adapters.skinport_client import SkinportApiClient
from app.adapters.steam_client import SteamPriceOverviewClient
from app.api.schemas import (
    CatalogItemSchema,
    ComparisonResponse,
    SearchResponse,
    SkinportPriceSchema,
    SteamPriceSchema,
)
from app.services.oracle import ItemSearchService, PriceOracleService

router = APIRouter(prefix="/api", tags=["api"])

_catalog = ByMykelCatalog()
_steam = SteamPriceOverviewClient()
_skinport = SkinportApiClient()
_search_service = ItemSearchService(_catalog)
_oracle_service = PriceOracleService(_steam, _skinport)


def get_search_service() -> ItemSearchService:
    return _search_service


def get_oracle_service() -> PriceOracleService:
    return _oracle_service


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/items/search", response_model=SearchResponse)
async def search_items(
    q: str = Query(min_length=1, max_length=120, description="Partial item name"),
    limit: int = Query(default=20, ge=1, le=50),
    search_service: ItemSearchService = Depends(get_search_service),
) -> SearchResponse:
    items = await search_service.search(q, limit=limit)
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
    search_service: ItemSearchService = Depends(get_search_service),
    oracle_service: PriceOracleService = Depends(get_oracle_service),
) -> ComparisonResponse:
    catalog_item = await search_service.resolve(market_hash_name)
    if catalog_item is None:
        raise HTTPException(status_code=404, detail=f"Unknown item: {market_hash_name}")

    try:
        comparison = await oracle_service.compare(market_hash_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Marketplace error: {exc}") from exc

    return ComparisonResponse(
        market_hash_name=comparison.market_hash_name,
        item_name=catalog_item.name,
        item_image=catalog_item.image,
        steam=SteamPriceSchema(
            lowest_price=comparison.steam.lowest_price,
            median_price=comparison.steam.median_price,
            volume=comparison.steam.volume,
            currency=comparison.steam.currency,
            source=comparison.steam.source,
        ),
        skinport=SkinportPriceSchema(
            min_price=comparison.skinport.min_price,
            median_price=comparison.skinport.median_price,
            mean_price=comparison.skinport.mean_price,
            max_price=comparison.skinport.max_price,
            suggested_price=comparison.skinport.suggested_price,
            quantity=comparison.skinport.quantity,
            item_page=comparison.skinport.item_page,
            market_page=comparison.skinport.market_page,
            currency=comparison.skinport.currency,
        ),
        cheaper=comparison.cheaper,
        difference=comparison.difference,
        percentage=comparison.percentage,
    )
