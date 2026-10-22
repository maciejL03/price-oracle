from typing import Protocol, runtime_checkable

from engine.models import Item, MarketQuote


@runtime_checkable
class Marketplace(Protocol):
    """Structural contract implemented by quote-producing marketplaces."""

    @property
    def name(self) -> str:
        """Return the marketplace's stable identifier."""
        ...

    async def fetch(self, item: Item) -> list[MarketQuote]:
        """Fetch normalized current observations for an exact item variant."""
        ...
