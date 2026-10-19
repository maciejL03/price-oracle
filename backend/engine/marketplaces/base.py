from typing import Protocol, runtime_checkable

from engine.models import Item, MarketQuote


@runtime_checkable
class Marketplace(Protocol):
    """Fetch normalized quotes from one marketplace implementation."""

    @property
    def name(self) -> str:
        """Return the marketplace's stable identifier."""
        ...

    async def fetch(self, item: Item) -> list[MarketQuote]:
        """Fetch and normalize the available quotes for an item."""
        ...
