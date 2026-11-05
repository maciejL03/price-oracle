from typing import Any

import pytest

from engine.errors import DuplicateMarketplaceError, UnknownMarketplaceError
from engine.marketplaces import Marketplace, MarketplaceRegistry
from engine.models import Item, MarketQuote


class FakeMarketplace:
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    async def fetch(self, item: Item) -> list[MarketQuote]:
        return []


def test_register_and_retrieve_marketplace() -> None:
    registry = MarketplaceRegistry()
    marketplace = FakeMarketplace("steam")

    registry.register(marketplace)

    assert registry.get("steam") is marketplace


def test_register_multiple_marketplaces_and_list_in_registration_order() -> None:
    registry = MarketplaceRegistry()
    steam = FakeMarketplace("steam")
    csfloat = FakeMarketplace("csfloat")

    registry.register(steam)
    registry.register(csfloat)

    assert registry.list() == (steam, csfloat)
    assert all(isinstance(value, Marketplace) for value in registry.list())


def test_duplicate_registration_is_rejected_without_replacement() -> None:
    registry = MarketplaceRegistry()
    original = FakeMarketplace("steam")
    registry.register(original)

    with pytest.raises(DuplicateMarketplaceError, match="already registered"):
        registry.register(FakeMarketplace("steam"))

    assert registry.get("steam") is original
    assert registry.list() == (original,)


def test_unknown_marketplace_is_rejected() -> None:
    registry = MarketplaceRegistry()

    with pytest.raises(UnknownMarketplaceError, match="not registered"):
        registry.get("steam")


@pytest.mark.parametrize(
    "name",
    ["", " ", "Steam", "steam market", " steam", "steam ", "steam/market"],
)
def test_invalid_marketplace_names_are_rejected(name: str) -> None:
    registry = MarketplaceRegistry()

    with pytest.raises(ValueError, match="Marketplace name"):
        registry.register(FakeMarketplace(name))


def test_non_string_marketplace_name_is_rejected() -> None:
    registry = MarketplaceRegistry()
    marketplace = FakeMarketplace("temporary")
    marketplace._name = 123  # type: ignore[assignment]

    with pytest.raises(TypeError, match="must be a string"):
        registry.register(marketplace)


@pytest.mark.parametrize("name", ["", "Steam", "steam market"])
def test_invalid_lookup_names_are_rejected(name: str) -> None:
    registry = MarketplaceRegistry()

    with pytest.raises(ValueError, match="Marketplace name"):
        registry.get(name)


@pytest.mark.parametrize(
    "marketplace",
    [object(), type("MissingFetch", (), {"name": "missing-fetch"})()],
)
def test_incomplete_marketplace_is_rejected(marketplace: Any) -> None:
    registry = MarketplaceRegistry()

    with pytest.raises(TypeError, match="Marketplace protocol"):
        registry.register(marketplace)


def test_synchronous_fetch_is_rejected() -> None:
    class SynchronousMarketplace:
        name = "synchronous"

        def fetch(self, item: Item) -> list[MarketQuote]:
            return []

    registry = MarketplaceRegistry()

    with pytest.raises(TypeError, match="async method"):
        registry.register(SynchronousMarketplace())


def test_registry_instances_have_independent_state() -> None:
    first = MarketplaceRegistry()
    second = MarketplaceRegistry()
    marketplace = FakeMarketplace("steam")

    first.register(marketplace)

    assert first.list() == (marketplace,)
    assert second.list() == ()
    with pytest.raises(UnknownMarketplaceError):
        second.get("steam")
