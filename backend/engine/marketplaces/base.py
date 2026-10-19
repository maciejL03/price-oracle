from typing import Protocol

from engine.models import Item, MarketQuote


class Marketplace(Protocol):
    """Structural contract implemented by quote-producing marketplaces."""

    name: str

    async def fetch(self, item: Item) -> list[MarketQuote]:
        """Fetch normalized current observations for an exact item variant."""
        ...
