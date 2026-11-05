from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from engine.models import Item, MarketQuote, QuoteType
from engine.services import PricingService, QuoteExclusionReason


ITEM = Item(market_hash_name="AK-47 | Redline (Field-Tested)")
NOW = datetime(2026, 1, 2, 12, 30, tzinfo=UTC)


def make_quote(**overrides: object) -> MarketQuote:
    values: dict[str, object] = {
        "marketplace": "steam",
        "market_hash_name": ITEM.market_hash_name,
        "price": Decimal("30.15"),
        "currency": "GBP",
        "observed_at": NOW - timedelta(minutes=1),
        "quote_type": QuoteType.LOWEST_ASK,
    }
    values.update(overrides)
    return MarketQuote.model_validate(values)


@pytest.fixture
def service() -> PricingService:
    return PricingService(max_quote_age=timedelta(minutes=5))


def compare(
    service: PricingService, quotes: object, **overrides: object
):
    arguments: dict[str, object] = {
        "currency": "GBP",
        "quote_type": QuoteType.LOWEST_ASK,
        "as_of": NOW,
    }
    arguments.update(overrides)
    return service.compare(ITEM, quotes, **arguments)  # type: ignore[arg-type]


def test_one_quote_is_the_best_and_only_price(service: PricingService) -> None:
    quote = make_quote()

    result = compare(service, [quote])

    assert result.quotes == (quote,)
    assert result.best_quote == quote
    assert result.lowest_price == Decimal("30.15")
    assert result.highest_price == Decimal("30.15")
    assert result.spread == Decimal("0.00")
    assert result.excluded_quotes == ()


def test_empty_input_returns_an_explicit_empty_comparison(
    service: PricingService,
) -> None:
    result = compare(service, [])

    assert result.quotes == ()
    assert result.best_quote is None
    assert result.lowest_price is None
    assert result.highest_price is None
    assert result.spread is None


def test_quotes_are_ranked_by_buy_price_and_spread_is_exact(
    service: PricingService,
) -> None:
    expensive = make_quote(marketplace="steam", price="30.15")
    cheapest = make_quote(marketplace="skinport", price="22.39")
    middle = make_quote(marketplace="csfloat", price="25.00")

    result = compare(service, [expensive, cheapest, middle])

    assert result.quotes == (cheapest, middle, expensive)
    assert result.best_quote == cheapest
    assert result.spread == Decimal("7.76")


def test_equal_prices_have_a_deterministic_marketplace_order(
    service: PricingService,
) -> None:
    steam = make_quote(marketplace="steam", price="20")
    skinport = make_quote(marketplace="skinport", price="20")

    result = compare(service, [steam, skinport])

    assert result.quotes == (skinport, steam)
    assert result.lowest_price == result.highest_price == Decimal("20")


def test_decimal_precision_is_not_rounded(service: PricingService) -> None:
    lower = make_quote(marketplace="a", price="1.00000000000000000001")
    higher = make_quote(marketplace="b", price="1.00000000000000000002")

    result = compare(service, [higher, lower])

    assert result.lowest_price == Decimal("1.00000000000000000001")
    assert result.spread == Decimal("0.00000000000000000001")


@pytest.mark.parametrize(
    ("quote", "reason"),
    [
        (
            make_quote(market_hash_name="AK-47 | Redline (Minimal Wear)"),
            QuoteExclusionReason.DIFFERENT_ITEM,
        ),
        (
            make_quote(
                market_hash_name="StatTrak™ AK-47 | Redline (Field-Tested)"
            ),
            QuoteExclusionReason.DIFFERENT_ITEM,
        ),
        (make_quote(currency="USD"), QuoteExclusionReason.DIFFERENT_CURRENCY),
        (
            make_quote(quote_type=QuoteType.MEDIAN_PRICE),
            QuoteExclusionReason.DIFFERENT_QUOTE_TYPE,
        ),
        (
            make_quote(quote_type=QuoteType.INDIVIDUAL_LISTING),
            QuoteExclusionReason.DIFFERENT_QUOTE_TYPE,
        ),
    ],
)
def test_incompatible_quotes_are_reported(
    service: PricingService,
    quote: MarketQuote,
    reason: QuoteExclusionReason,
) -> None:
    result = compare(service, [quote])

    assert result.quotes == ()
    assert result.excluded_quotes[0].quote == quote
    assert result.excluded_quotes[0].reason is reason


def test_quote_types_can_be_compared_in_separate_semantic_groups(
    service: PricingService,
) -> None:
    listing = make_quote(
        quote_type=QuoteType.INDIVIDUAL_LISTING,
        external_listing_id="listing-1",
    )
    aggregate = make_quote(quote_type=QuoteType.LOWEST_ASK)

    result = compare(
        service,
        [aggregate, listing],
        quote_type=QuoteType.INDIVIDUAL_LISTING,
    )

    assert result.quotes == (listing,)
    assert result.excluded_quotes[0].reason is QuoteExclusionReason.DIFFERENT_QUOTE_TYPE


def test_exact_freshness_boundary_is_included(service: PricingService) -> None:
    quote = make_quote(observed_at=NOW - timedelta(minutes=5))

    result = compare(service, [quote])

    assert result.quotes == (quote,)


def test_stale_quote_is_reported(service: PricingService) -> None:
    quote = make_quote(observed_at=NOW - timedelta(minutes=5, microseconds=1))

    result = compare(service, [quote])

    assert result.quotes == ()
    assert result.excluded_quotes[0].reason is QuoteExclusionReason.STALE


def test_future_quote_is_reported(service: PricingService) -> None:
    quote = make_quote(observed_at=NOW + timedelta(microseconds=1))

    result = compare(service, [quote])

    assert result.excluded_quotes[0].reason is QuoteExclusionReason.OBSERVED_IN_FUTURE


def test_mixed_observation_times_retain_only_fresh_quotes(
    service: PricingService,
) -> None:
    fresh = make_quote(marketplace="fresh", observed_at=NOW - timedelta(minutes=2))
    stale = make_quote(marketplace="stale", observed_at=NOW - timedelta(hours=1))

    result = compare(service, [stale, fresh])

    assert result.quotes == (fresh,)
    assert result.excluded_quotes[0].quote == stale


def test_non_utc_aware_as_of_is_normalized(service: PricingService) -> None:
    plus_two = timezone(timedelta(hours=2))
    local_as_of = datetime(2026, 1, 2, 14, 30, tzinfo=plus_two)

    result = compare(service, [make_quote()], as_of=local_as_of)

    assert result.as_of == NOW
    assert result.as_of.tzinfo is UTC


def test_duplicate_observation_is_excluded_once(service: PricingService) -> None:
    quote = make_quote()

    result = compare(service, [quote, quote])

    assert result.quotes == (quote,)
    assert result.excluded_quotes[0].reason is QuoteExclusionReason.DUPLICATE


def test_same_listing_observed_at_different_times_is_not_an_exact_duplicate(
    service: PricingService,
) -> None:
    older = make_quote(external_listing_id="listing-1")
    newer = make_quote(
        external_listing_id="listing-1", observed_at=NOW - timedelta(seconds=30)
    )

    result = compare(service, [older, newer])

    assert len(result.quotes) == 2
    assert result.excluded_quotes == ()


def test_generator_input_is_supported(service: PricingService) -> None:
    quotes = (quote for quote in [make_quote()])

    assert len(compare(service, quotes).quotes) == 1


@pytest.mark.parametrize("max_age", [timedelta(0), timedelta(seconds=-1)])
def test_freshness_window_must_be_positive(max_age: timedelta) -> None:
    with pytest.raises(ValueError, match="max_quote_age"):
        PricingService(max_quote_age=max_age)


@pytest.mark.parametrize("currency", ["", "gbp", "GB", "GBP "])
def test_requested_currency_must_be_valid(
    service: PricingService, currency: str
) -> None:
    with pytest.raises(ValueError, match="currency"):
        compare(service, [], currency=currency)


def test_naive_as_of_is_rejected(service: PricingService) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        compare(service, [], as_of=datetime(2026, 1, 2, 12, 30))


def test_invalid_quote_collection_is_rejected(service: PricingService) -> None:
    with pytest.raises(TypeError, match="iterable"):
        compare(service, 42)

    with pytest.raises(TypeError, match="MarketQuote"):
        compare(service, [object()])


def test_result_serialization_preserves_decimal_values(
    service: PricingService,
) -> None:
    result = compare(service, [make_quote(price="0.123456789012345678901")])

    payload = result.model_dump_json()

    assert '"lowest_price":"0.123456789012345678901"' in payload
    assert '"spread":"0E-21"' in payload


def test_service_has_no_marketplace_network_or_storage_dependency() -> None:
    service = PricingService(max_quote_age=timedelta(minutes=5))

    assert vars(service) == {"_max_quote_age": timedelta(minutes=5)}
