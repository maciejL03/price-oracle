import json
import time
from pathlib import Path

import httpx

from app.adapters.cache_io import read_json_cache, write_json_cache
from app.config import settings
from app.domain.models import SkinportPrice
from app.ports.marketplaces import SkinportMarket

_BROTLI_HELP = (
    "Skinport requires Brotli decompression. "
    "From the backend folder run: pip install --force-reinstall brotli"
)


class SkinportApiClient(SkinportMarket):
    def __init__(self) -> None:
        self._index: dict[str, SkinportPrice] = {}
        self._loaded_at: float = 0.0

    async def _ensure_index(self) -> None:
        if self._index and (time.time() - self._loaded_at) < settings.skinport_cache_ttl_seconds:
            return

        cache_path = Path(settings.cache_dir) / f"skinport_{settings.skinport_currency.lower()}.json"

        raw: list[dict] | None = None
        if cache_path.exists():
            age = time.time() - cache_path.stat().st_mtime
            if age < settings.skinport_cache_ttl_seconds:
                cached = read_json_cache(cache_path)
                if isinstance(cached, list):
                    raw = cached
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
            raw = _parse_items_response(response)

        write_json_cache(cache_path, raw)
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


def _parse_items_response(response: httpx.Response) -> list[dict]:
    payload = _response_json(response)

    if isinstance(payload, dict):
        errors = payload.get("errors")
        if errors:
            message = errors[0].get("message", "Skinport API error")
            raise ValueError(message)
        raise ValueError("Unexpected Skinport response format.")

    if not isinstance(payload, list):
        raise ValueError("Unexpected Skinport response format.")

    return payload


def _response_json(response: httpx.Response) -> object:
    try:
        return response.json()
    except (UnicodeDecodeError, json.JSONDecodeError):
        return json.loads(_decompress_brotli(response.content).decode("utf-8"))


def _decompress_brotli(data: bytes) -> bytes:
    try:
        import brotli
    except ImportError as exc:
        raise RuntimeError(_BROTLI_HELP) from exc

    try:
        return brotli.decompress(data)
    except Exception as exc:
        raise RuntimeError(_BROTLI_HELP) from exc


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)
