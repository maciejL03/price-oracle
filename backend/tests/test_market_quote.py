from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from engine.models import Item, MarketQuote, QuoteType


def make_quote(**overrides: object) -> MarketQuote:
    values: dict[str, object] = {
        "marketplace": "steam",
        "market_hash_name": "AK-47 | Redline (Field-Tested)",
        "price": Decimal("30.15"),
        "currency": "GBP",
        "observed_at": datetime(2026, 1, 2, 12, 30, tzinfo=UTC),
        "quote_type": QuoteType.LOWEST_ASK,
    }
    values.update(overrides)
    return MarketQuote.model_validate(values)


def test_market_quote_constructs_with_normalized_fields() -> None:
    quote = make_quote(external_listing_id="listing-123")

    assert quote.marketplace == "steam"
    assert quote.market_hash_name == "AK-47 | Redline (Field-Tested)"
    assert quote.price == Decimal("30.15")
    assert quote.currency == "GBP"
    assert quote.observed_at == datetime(2026, 1, 2, 12, 30, tzinfo=UTC)
    assert quote.quote_type is QuoteType.LOWEST_ASK
    assert quote.external_listing_id == "listing-123"


def test_market_quote_preserves_decimal_precision() -> None:
    quote = make_quote(price="0.123456789012345678901")

    assert quote.price == Decimal("0.123456789012345678901")


@pytest.mark.parametrize("price", ["0", "-0.01", "NaN", "Infinity", "-Infinity"])
def test_market_quote_rejects_invalid_price(price: str) -> None:
    with pytest.raises(ValidationError, match="price"):
        make_quote(price=price)


def test_market_quote_rejects_float_price() -> None:
    with pytest.raises(ValidationError, match="float"):
        make_quote(price=30.15)


@pytest.mark.parametrize("currency", ["", "gbp", "GB", "GBP ", "US12"])
def test_market_quote_rejects_invalid_currency(currency: str) -> None:
    with pytest.raises(ValidationError, match="currency"):
        make_quote(currency=currency)


@pytest.mark.parametrize("marketplace", ["", "Steam", "steam market", " steam"])
def test_market_quote_rejects_invalid_marketplace_identifier(marketplace: str) -> None:
    with pytest.raises(ValidationError, match="marketplace"):
        make_quote(marketplace=marketplace)


@pytest.mark.parametrize("market_hash_name", ["", "   ", "Test Item "])
def test_market_quote_rejects_invalid_item_identifier(market_hash_name: str) -> None:
    with pytest.raises(ValidationError, match="market_hash_name"):
        make_quote(market_hash_name=market_hash_name)


def test_market_quote_rejects_naive_timestamp() -> None:
    with pytest.raises(ValidationError, match="observed_at"):
        make_quote(observed_at=datetime(2026, 1, 2, 12, 30))


def test_market_quote_rejects_malformed_timestamp() -> None:
    with pytest.raises(ValidationError, match="observed_at"):
        make_quote(observed_at="not-a-timestamp")


def test_market_quote_normalizes_timestamp_to_utc() -> None:
    observed_at = datetime(2026, 1, 2, 12, 30, tzinfo=timezone(timedelta(hours=2)))

    quote = make_quote(observed_at=observed_at)

    assert quote.observed_at == datetime(2026, 1, 2, 10, 30, tzinfo=UTC)
    assert quote.observed_at.tzinfo is UTC


@pytest.mark.parametrize("quote_type", list(QuoteType))
def test_market_quote_preserves_quote_type_semantics(quote_type: QuoteType) -> None:
    quote = make_quote(quote_type=quote_type)

    assert quote.quote_type is quote_type


def test_different_quote_types_do_not_compare_equal() -> None:
    lowest_ask = make_quote(quote_type=QuoteType.LOWEST_ASK)
    median = make_quote(quote_type=QuoteType.MEDIAN_PRICE)

    assert lowest_ask != median


@pytest.mark.parametrize("external_listing_id", ["", "   ", " listing-123"])
def test_market_quote_rejects_invalid_external_listing_id(
    external_listing_id: str,
) -> None:
    with pytest.raises(ValidationError, match="external_listing_id"):
        make_quote(external_listing_id=external_listing_id)


@pytest.mark.parametrize(
    "missing_field",
    [
        "marketplace",
        "market_hash_name",
        "price",
        "currency",
        "observed_at",
        "quote_type",
    ],
)
def test_market_quote_requires_core_fields(missing_field: str) -> None:
    values = make_quote().model_dump()
    del values[missing_field]

    with pytest.raises(ValidationError, match=missing_field):
        MarketQuote.model_validate(values)


def test_market_quote_rejects_unknown_fields() -> None:
    values = make_quote().model_dump()
    values["marketplace_fee"] = "1.00"

    with pytest.raises(ValidationError, match="marketplace_fee"):
        MarketQuote.model_validate(values)


def test_market_quote_json_round_trip_is_lossless() -> None:
    quote = make_quote(
        price=Decimal("1234.567890123456789"),
        observed_at=datetime(2026, 1, 2, 12, 30, 45, 123456, tzinfo=UTC),
        quote_type=QuoteType.INDIVIDUAL_LISTING,
        external_listing_id="listing-123",
    )

    serialized = quote.model_dump_json()
    restored = MarketQuote.model_validate_json(serialized)

    assert '"price":"1234.567890123456789"' in serialized
    assert restored == quote
    assert isinstance(restored.price, Decimal)
    assert restored.observed_at.tzinfo is UTC


def test_representative_marketplace_data_normalizes_without_source_dependencies() -> None:
    raw = {
        "market_hash_name": "StatTrak™ AK-47 | Redline (Field-Tested)",
        "min_price": "22.39",
        "currency": "GBP",
        "captured_at": "2026-01-02T12:30:00+00:00",
    }

    item = Item(market_hash_name=raw["market_hash_name"])
    quote = MarketQuote(
        marketplace="skinport",
        market_hash_name=raw["market_hash_name"],
        price=raw["min_price"],
        currency=raw["currency"],
        observed_at=raw["captured_at"],
        quote_type=QuoteType.LOWEST_ASK,
    )

    assert quote.market_hash_name == item.market_hash_name
    assert quote.price == Decimal("22.39")
    assert "skinport" not in type(quote).__module__


def test_different_item_variants_remain_distinct_across_models() -> None:
    normal = Item(market_hash_name="AK-47 | Redline (Field-Tested)")
    stattrak_quote = make_quote(
        market_hash_name="StatTrak™ AK-47 | Redline (Field-Tested)"
    )

    assert normal.market_hash_name != stattrak_quote.market_hash_name


def test_market_quote_is_immutable() -> None:
    quote = make_quote()

    with pytest.raises(ValidationError):
        quote.price = Decimal("1.00")
