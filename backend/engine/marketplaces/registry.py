import re
from inspect import iscoroutinefunction

from engine.errors import MarketplaceRegistrationError, UnknownMarketplaceError

from .base import Marketplace

_MARKETPLACE_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


class MarketplaceRegistry:
    """Map stable marketplace identifiers to provider instances."""

    def __init__(self) -> None:
        self._providers: dict[str, Marketplace] = {}

    def register(self, provider: Marketplace) -> None:
        name = getattr(provider, "name", None)
        fetch = getattr(provider, "fetch", None)
        if not isinstance(name, str) or _MARKETPLACE_NAME.fullmatch(name) is None:
            raise MarketplaceRegistrationError(
                "Marketplace name must be a lowercase provider identifier"
            )
        if not callable(fetch) or not iscoroutinefunction(fetch):
            raise MarketplaceRegistrationError(
                f"Marketplace '{name}' must provide an async fetch method"
            )
        if name in self._providers:
            raise MarketplaceRegistrationError(
                f"Marketplace is already registered: {name}"
            )
        self._providers[name] = provider

    def get(self, name: str) -> Marketplace:
        try:
            return self._providers[name]
        except KeyError as error:
            raise UnknownMarketplaceError(
                f"Marketplace is not registered: {name}"
            ) from error

    @property
    def names(self) -> tuple[str, ...]:
        """Return registered identifiers in insertion order."""
        return tuple(self._providers)
