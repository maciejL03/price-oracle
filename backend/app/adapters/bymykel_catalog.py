import json
import time
from pathlib import Path

import httpx

from app.config import settings
from app.domain.models import CatalogItem
from app.ports.marketplaces import ItemCatalog


class ByMykelCatalog(ItemCatalog):
    def __init__(self) -> None:
        self._items: list[CatalogItem] = []
        self._by_hash_name: dict[str, CatalogItem] = {}
        self._loaded_at: float = 0.0

    async def _ensure_loaded(self) -> None:
        if self._items and (time.time() - self._loaded_at) < settings.items_cache_ttl_seconds:
            return

        cache_path = Path(settings.cache_dir) / "bymykel_items.json"
        cache_path.parent.mkdir(parents=True, exist_ok=True)

        if cache_path.exists():
            age = time.time() - cache_path.stat().st_mtime
            if age < settings.items_cache_ttl_seconds:
                raw = json.loads(cache_path.read_text(encoding="utf-8"))
                self._populate(raw)
                self._loaded_at = time.time()
                return

        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
            response = await client.get(settings.bymykel_items_url)
            response.raise_for_status()
            raw = response.json()

        cache_path.write_text(json.dumps(raw), encoding="utf-8")
        self._populate(raw)
        self._loaded_at = time.time()

    def _populate(self, raw: dict) -> None:
        items: list[CatalogItem] = []
        by_hash: dict[str, CatalogItem] = {}

        for entry in raw.values():
            if not isinstance(entry, dict):
                continue

            market_hash_name = entry.get("market_hash_name")
            if not market_hash_name:
                continue

            item = CatalogItem(
                market_hash_name=market_hash_name,
                name=entry.get("name", market_hash_name),
                image=entry.get("image"),
            )
            items.append(item)
            by_hash[market_hash_name] = item

        self._items = items
        self._by_hash_name = by_hash

    async def search(self, query: str, limit: int = 20) -> list[CatalogItem]:
        await self._ensure_loaded()

        normalized = query.lower().strip()
        if not normalized:
            return []

        exact = [
            item
            for item in self._items
            if item.market_hash_name.lower() == normalized
        ]
        if exact:
            return exact[:limit]

        prefix = [
            item
            for item in self._items
            if item.market_hash_name.lower().startswith(normalized)
        ]
        if prefix:
            return prefix[:limit]

        contains = [
            item
            for item in self._items
            if normalized in item.market_hash_name.lower()
        ]
        contains.sort(
            key=lambda item: (
                item.market_hash_name.lower().index(normalized),
                len(item.market_hash_name),
            )
        )
        return contains[:limit]

    async def get_by_market_hash_name(self, market_hash_name: str) -> CatalogItem | None:
        await self._ensure_loaded()
        return self._by_hash_name.get(market_hash_name)
