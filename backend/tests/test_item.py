import pytest
from pydantic import ValidationError

from engine.models import Item


def test_item_constructs_with_canonical_identifier() -> None:
    item = Item(market_hash_name="AK-47 | Redline (Field-Tested)")

    assert item.market_hash_name == "AK-47 | Redline (Field-Tested)"


def test_items_with_the_same_identifier_compare_equal() -> None:
    first = Item(market_hash_name="AK-47 | Redline (Field-Tested)")
    second = Item.model_validate(
        {"market_hash_name": "AK-47 | Redline (Field-Tested)"}
    )

    assert first == second


@pytest.mark.parametrize(
    "market_hash_name",
    [
        "StatTrak™ AK-47 | Redline (Field-Tested)",
        "AK-47 | Redline (Minimal Wear)",
    ],
)
def test_item_variants_have_distinct_identities(market_hash_name: str) -> None:
    base = Item(market_hash_name="AK-47 | Redline (Field-Tested)")

    assert Item(market_hash_name=market_hash_name) != base


@pytest.mark.parametrize("market_hash_name", ["", "   ", " padded", "padded "])
def test_item_rejects_empty_or_padded_identifier(market_hash_name: str) -> None:
    with pytest.raises(ValidationError, match="market_hash_name"):
        Item(market_hash_name=market_hash_name)


def test_item_requires_identifier() -> None:
    with pytest.raises(ValidationError, match="market_hash_name"):
        Item.model_validate({})


def test_item_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="display_name"):
        Item.model_validate({"market_hash_name": "Test Item", "display_name": "Test"})


def test_item_is_immutable() -> None:
    item = Item(market_hash_name="Test Item")

    with pytest.raises(ValidationError):
        item.market_hash_name = "Different Item"
