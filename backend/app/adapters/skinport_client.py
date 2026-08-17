import json
import time
from pathlib import Path

import httpx

from app.config import settings
from app.domain.models import SkinportPrice
from app.ports.marketplaces import SkinportMarket


class SkinportApiClient(SkinportMarket):
    def __init__(self) -> None:
        self._index: dict[str, SkinportPrice] = {}
        self._loaded_at: float = 0.0

    async def _ensure_index(self) -> None:
        if self._index and (time.time() - self._loaded_at) < settings.skinport_cache_ttl_seconds:
            return

        cache_path = Path(settings.cache_dir) / f"skinport_{settings.skinport_currency.lower()}.json"
        cache_path.parent.mkdir(parents=True, exist_ok=True)

        raw: list[dict]
        if cache_path.exists():
            age = time.time() - cache_path.stat().st_mtime
            if age < settings.skinport_cache_ttl_seconds:
                raw = json.loads(cache_path.read_text(encoding="utf-8"))
                self._build_index(raw)
                self._loaded_at = time.time()
                return

        params = {
            "app_id": settings.steam_app_id,
            "currency": settings.skinport_currency,
            "tradable": 1,
        }

        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
            response = await client.get(
                f"{settings.skinport_base_url}/items",
                params=params,
                headers={"Accept-Encoding": "br"},
            )
            response.raise_for_status()
            raw = response.json()

        cache_path.write_text(json.dumps(raw), encoding="utf-8")
        self._build_index(raw)
        self._loaded_at = time.time()

    def _build_index(self, raw: list[dict]) -> None:
        index: dict[str, SkinportPrice] = {}

        for entry in raw:
            market_hash_name = entry.get("market_hash_name")
            min_price = entry.get("min_price")
            if not market_hash_name or min_price is None:
                continue

            index[market_hash_name] = SkinportPrice(
                min_price=float(min_price),
                median_price=_optional_float(entry.get("median_price")),
                mean_price=_optional_float(entry.get("mean_price")),
                max_price=_optional_float(entry.get("max_price")),
                suggested_price=_optional_float(entry.get("suggested_price")),
                quantity=entry.get("quantity"),
                item_page=entry.get("item_page"),
                market_page=entry.get("market_page"),
            )

        self._index = index

    async def get_price(self, market_hash_name: str) -> SkinportPrice | None:
        await self._ensure_index()
        return self._index.get(market_hash_name)


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)
