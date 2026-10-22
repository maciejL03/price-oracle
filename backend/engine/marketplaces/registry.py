import re
from inspect import iscoroutinefunction

from engine.errors import (
    DuplicateMarketplaceError,
    InvalidMarketplaceError,
    MarketplaceRegistrationError,
    UnknownMarketplaceError,
)

from .base import Marketplace

_MARKETPLACE_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


class MarketplaceRegistry:
    """Store explicitly registered marketplace instances by identifier."""

    def __init__(self) -> None:
        self._providers: dict[str, Marketplace] = {}

    def register(self, provider: Marketplace) -> None:
        """Register a ready-to-use provider without replacing an existing one."""
        if not isinstance(provider, Marketplace):
            raise InvalidMarketplaceError(
                "marketplace must implement the Marketplace protocol with an async fetch method"
            )

        name = provider.name
        self._validate_name(name)
        if not iscoroutinefunction(provider.fetch):
            raise InvalidMarketplaceError(
                "marketplace fetch must be an async method; async fetch is required"
            )
        if name in self._providers:
            raise DuplicateMarketplaceError(
                f"Marketplace is already registered: {name}"
            )
        self._providers[name] = provider

    def get(self, name: str) -> Marketplace:
        """Return a registered marketplace by its identifier."""
        self._validate_name(name)
        try:
            return self._providers[name]
        except KeyError:
            raise UnknownMarketplaceError(
                f"Marketplace is not registered: {name}"
            ) from None

    def list(self) -> tuple[Marketplace, ...]:
        """Return an immutable provider snapshot in registration order."""
        return tuple(self._providers.values())

    @property
    def names(self) -> tuple[str, ...]:
        """Return registered identifiers in insertion order."""
        return tuple(self._providers)

    @staticmethod
    def _validate_name(name: str) -> None:
        if not isinstance(name, str):
            raise TypeError("Marketplace name must be a string")
        if _MARKETPLACE_NAME.fullmatch(name) is None:
            raise MarketplaceRegistrationError(
                "Marketplace name must start with a lowercase letter or digit and contain "
                "only lowercase letters, digits, dots, underscores, or hyphens"
            )
