"""One-shot market collection and persistence job."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING
from urllib.parse import quote

from engine.models import Item, MarketQuote
from engine.services import CollectionService

if TYPE_CHECKING:
    from engine.storage import StorageService

from .base import EngineJob

logger = logging.getLogger(__name__)

QuoteBatchHandler = Callable[[tuple[MarketQuote, ...]], Awaitable[None]]


class FetchMarketJob(EngineJob):
    """Collect and persist one marketplace observation batch for one item."""

    def __init__(
        self,
        marketplace: str,
        item: Item,
        collection_service: CollectionService,
        storage_service: StorageService,
        *,
        after_persist: QuoteBatchHandler | None = None,
    ) -> None:
        self._marketplace = marketplace
        self._item = item
        self._collection_service = collection_service
        self._storage_service = storage_service
        self._after_persist = after_persist
        self._key = (
            f"fetch-market:{quote(marketplace, safe='')}:"
            f"{quote(item.market_hash_name, safe='')}"
        )

    @property
    def key(self) -> str:
        """Return the stable identity of this marketplace and item fetch."""
        return self._key

    async def execute(self) -> None:
        """Collect normalized quotes once and persist the complete result."""
        logger.info(
            "Collecting market quotes: marketplace=%s item=%s",
            self._marketplace,
            self._item.market_hash_name,
        )
        quotes = await self._collection_service.collect(self._marketplace, self._item)
        await self._storage_service.save_quotes(quotes)
        logger.info(
            "Persisted %d market quotes: marketplace=%s item=%s",
            len(quotes),
            self._marketplace,
            self._item.market_hash_name,
        )
        if self._after_persist is not None:
            await self._after_persist(tuple(quotes))
