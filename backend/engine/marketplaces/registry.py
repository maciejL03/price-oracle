import inspect
import re

from engine.errors import DuplicateMarketplaceError, UnknownMarketplaceError

from .base import Marketplace

_MARKETPLACE_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


class MarketplaceRegistry:
    """Store explicitly registered marketplace instances by identifier."""

    def __init__(self) -> None:
        self._marketplaces: dict[str, Marketplace] = {}

    def register(self, marketplace: Marketplace) -> None:
        """Register a ready-to-use marketplace without replacing an existing one."""
        if not isinstance(marketplace, Marketplace):
            raise TypeError("marketplace must implement the Marketplace protocol")

        name = marketplace.name
        self._validate_name(name)
        if not inspect.iscoroutinefunction(marketplace.fetch):
            raise TypeError("marketplace fetch must be an async method")
        if name in self._marketplaces:
            raise DuplicateMarketplaceError(f"Marketplace is already registered: {name}")

        self._marketplaces[name] = marketplace

    def get(self, name: str) -> Marketplace:
        """Return a registered marketplace by its identifier."""
        self._validate_name(name)
        try:
            return self._marketplaces[name]
        except KeyError:
            raise UnknownMarketplaceError(f"Marketplace is not registered: {name}") from None

    def list(self) -> tuple[Marketplace, ...]:
        """Return an immutable snapshot in registration order."""
        return tuple(self._marketplaces.values())

    @staticmethod
    def _validate_name(name: str) -> None:
        if not isinstance(name, str):
            raise TypeError("Marketplace name must be a string")
        if _MARKETPLACE_NAME_PATTERN.fullmatch(name) is None:
            raise ValueError(
                "Marketplace name must start with a lowercase letter or digit and contain "
                "only lowercase letters, digits, dots, underscores, or hyphens"
            )
