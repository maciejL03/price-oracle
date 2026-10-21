import asyncio
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from engine.alerts import AlertCondition, AlertEvent, AlertRule, AlertService
from engine.errors import StorageError
from engine.models import Item, MarketQuote, QuoteType
from engine.services import PricingService
from engine.storage import StorageService
from pydantic import ValidationError

ITEM = Item(market_hash_name="AK-47 | Redline (Field-Tested)")
OBSERVED_AT = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
AS_OF = OBSERVED_AT + timedelta(minutes=1)


def make_rule(**overrides: object) -> AlertRule:
    values: dict[str, object] = {
        "id": uuid4(),
        "item": ITEM,
        "marketplace": "skinport",
        "currency": "GBP",
        "quote_type": QuoteType.LOWEST_ASK,
        "condition": AlertCondition.BELOW,
        "threshold": Decimal("15"),
    }
    values.update(overrides)
    return AlertRule.model_validate(values)


def make_quote(**overrides: object) -> MarketQuote:
    values: dict[str, object] = {
        "marketplace": "skinport",
        "market_hash_name": ITEM.market_hash_name,
        "price": Decimal("20"),
        "currency": "GBP",
        "observed_at": OBSERVED_AT,
        "quote_type": QuoteType.LOWEST_ASK,
    }
    values.update(overrides)
    return MarketQuote.model_validate(values)


def service(storage: Any) -> AlertService:
    return AlertService(storage, max_quote_age=timedelta(minutes=5))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("condition", "threshold", "price"),
    [
        (AlertCondition.BELOW, "15", "14.99"),
        (AlertCondition.ABOVE, "15", "15.01"),
    ],
)
async def test_above_and_below_conditions_trigger(
    tmp_path: Path,
    condition: AlertCondition,
    threshold: str,
    price: str,
) -> None:
    async with StorageService(tmp_path / "alerts.sqlite3") as storage:
        alert_service = service(storage)
        rule = make_rule(condition=condition, threshold=threshold)
        await alert_service.create_rule(rule)

        events = await alert_service.evaluate(make_quote(price=price), as_of=AS_OF)

        assert len(events) == 1
        assert events[0].rule_id == rule.id
        assert events[0].condition is condition
        assert events[0].threshold == Decimal(threshold)
        assert await storage.list_alert_events(rule_id=rule.id) == list(events)


@pytest.mark.asyncio
@pytest.mark.parametrize("condition", list(AlertCondition))
async def test_boundary_equality_does_not_trigger(
    tmp_path: Path, condition: AlertCondition
) -> None:
    async with StorageService(tmp_path / "alerts.sqlite3") as storage:
        alert_service = service(storage)
        await alert_service.create_rule(make_rule(condition=condition, threshold="15"))

        events = await alert_service.evaluate(make_quote(price="15"), as_of=AS_OF)

        assert events == ()
        assert await storage.list_alert_events() == []


@pytest.mark.asyncio
async def test_decimal_precision_is_not_rounded(tmp_path: Path) -> None:
    threshold = Decimal("1.00000000000000000002")
    observed = Decimal("1.00000000000000000001")
    async with StorageService(tmp_path / "alerts.sqlite3") as storage:
        alert_service = service(storage)
        await alert_service.create_rule(make_rule(threshold=threshold))

        events = await alert_service.evaluate(make_quote(price=observed), as_of=AS_OF)

        assert events[0].quote.price == observed
        assert events[0].threshold == threshold


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rule_override",
    [
        {"marketplace": "steam"},
        {"item": Item(market_hash_name="M4A1-S | Decimator (Minimal Wear)")},
        {"currency": "USD"},
    ],
)
async def test_incompatible_rules_are_not_compared(
    tmp_path: Path, rule_override: dict[str, object]
) -> None:
    async with StorageService(tmp_path / "alerts.sqlite3") as storage:
        alert_service = service(storage)
        await alert_service.create_rule(make_rule(**rule_override))

        events = await alert_service.evaluate(make_quote(price="10"), as_of=AS_OF)

        assert events == ()
        assert await storage.list_alert_events() == []


@pytest.mark.asyncio
async def test_no_matching_rules_returns_no_events(tmp_path: Path) -> None:
    async with StorageService(tmp_path / "alerts.sqlite3") as storage:
        assert (
            await service(storage).evaluate(make_quote(price="10"), as_of=AS_OF) == ()
        )


@pytest.mark.parametrize(
    "threshold",
    [0, "0", "-0.01", "NaN", "Infinity", "-Infinity", 1.5],
)
def test_invalid_thresholds_are_rejected(threshold: object) -> None:
    with pytest.raises(ValidationError, match="threshold"):
        make_rule(threshold=threshold)


@pytest.mark.parametrize(
    "quote_type", [QuoteType.INDIVIDUAL_LISTING, QuoteType.MEDIAN_PRICE]
)
def test_unsupported_rule_quote_types_are_rejected(quote_type: QuoteType) -> None:
    with pytest.raises(ValidationError, match="lowest_ask"):
        make_rule(quote_type=quote_type)


@pytest.mark.asyncio
async def test_unsupported_observation_quote_type_is_rejected(tmp_path: Path) -> None:
    async with StorageService(tmp_path / "alerts.sqlite3") as storage:
        with pytest.raises(ValueError, match="lowest_ask"):
            await service(storage).evaluate(
                make_quote(quote_type=QuoteType.MEDIAN_PRICE), as_of=AS_OF
            )


@pytest.mark.asyncio
async def test_timestamp_validation_and_normalization(tmp_path: Path) -> None:
    async with StorageService(tmp_path / "alerts.sqlite3") as storage:
        alert_service = service(storage)
        with pytest.raises(ValueError, match="timezone-aware"):
            await alert_service.evaluate(
                make_quote(), as_of=datetime(2026, 10, 20, 12, 1)
            )
        with pytest.raises(ValueError, match="future"):
            await alert_service.evaluate(
                make_quote(observed_at=AS_OF + timedelta(seconds=1)), as_of=AS_OF
            )
        with pytest.raises(ValueError, match="stale"):
            await alert_service.evaluate(
                make_quote(observed_at=AS_OF - timedelta(minutes=5, microseconds=1)),
                as_of=AS_OF,
            )

        local_as_of = AS_OF.astimezone(timezone(timedelta(hours=2)))
        rule = make_rule()
        await alert_service.create_rule(rule)
        events = await alert_service.evaluate(make_quote(price="10"), as_of=local_as_of)
        event = events[0]
        assert event.triggered_at == AS_OF
        assert event.triggered_at.tzinfo is UTC


@pytest.mark.asyncio
async def test_exact_freshness_boundary_is_accepted(tmp_path: Path) -> None:
    async with StorageService(tmp_path / "alerts.sqlite3") as storage:
        alert_service = service(storage)
        await alert_service.create_rule(make_rule())

        events = await alert_service.evaluate(
            make_quote(price="14", observed_at=AS_OF - timedelta(minutes=5)),
            as_of=AS_OF,
        )

        assert len(events) == 1


@pytest.mark.parametrize("max_age", [timedelta(0), timedelta(seconds=-1)])
def test_freshness_window_must_be_positive(max_age: timedelta) -> None:
    with pytest.raises(ValueError, match="max_quote_age"):
        AlertService(object(), max_quote_age=max_age)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_duplicate_is_suppressed_until_condition_rearms(tmp_path: Path) -> None:
    async with StorageService(tmp_path / "alerts.sqlite3") as storage:
        alert_service = service(storage)
        rule = make_rule()
        await alert_service.create_rule(rule)

        first = await alert_service.evaluate(make_quote(price="14"), as_of=AS_OF)
        duplicate = await alert_service.evaluate(
            make_quote(price="13", observed_at=OBSERVED_AT + timedelta(seconds=1)),
            as_of=AS_OF,
        )
        rearmed = await alert_service.evaluate(
            make_quote(price="17", observed_at=OBSERVED_AT + timedelta(seconds=2)),
            as_of=AS_OF,
        )
        retriggered = await alert_service.evaluate(
            make_quote(price="14", observed_at=OBSERVED_AT + timedelta(seconds=3)),
            as_of=AS_OF,
        )

        assert len(first) == 1
        assert duplicate == ()
        assert rearmed == ()
        assert len(retriggered) == 1
        assert retriggered[0].id != first[0].id
        assert len(await storage.list_alert_events(rule_id=rule.id)) == 2


@pytest.mark.asyncio
async def test_repeated_and_out_of_order_observations_do_not_change_state(
    tmp_path: Path,
) -> None:
    async with StorageService(tmp_path / "alerts.sqlite3") as storage:
        alert_service = service(storage)
        rule = make_rule()
        await alert_service.create_rule(rule)
        newest = make_quote(price="14", observed_at=OBSERVED_AT + timedelta(seconds=2))

        assert len(await alert_service.evaluate(newest, as_of=AS_OF)) == 1
        assert await alert_service.evaluate(newest, as_of=AS_OF) == ()
        assert (
            await alert_service.evaluate(
                make_quote(price="20", observed_at=OBSERVED_AT + timedelta(seconds=1)),
                as_of=AS_OF,
            )
            == ()
        )
        assert (
            await alert_service.evaluate(
                make_quote(price="13", observed_at=OBSERVED_AT + timedelta(seconds=3)),
                as_of=AS_OF,
            )
            == ()
        )


@pytest.mark.asyncio
async def test_independent_rules_trigger_independent_events(tmp_path: Path) -> None:
    async with StorageService(tmp_path / "alerts.sqlite3") as storage:
        alert_service = service(storage)
        rules = [make_rule(threshold="15"), make_rule(threshold="12")]
        for rule in rules:
            await alert_service.create_rule(rule)

        events = await alert_service.evaluate(make_quote(price="10"), as_of=AS_OF)

        assert {event.rule_id for event in events} == {rule.id for rule in rules}
        assert len(await storage.list_alert_events()) == 2


@pytest.mark.asyncio
async def test_rule_management_round_trip_and_delete(tmp_path: Path) -> None:
    async with StorageService(tmp_path / "alerts.sqlite3") as storage:
        alert_service = service(storage)
        rule = make_rule(threshold="15.0000")

        await alert_service.create_rule(rule)
        assert await alert_service.list_rules() == (rule,)
        assert await alert_service.delete_rule(rule.id) is True
        assert await alert_service.delete_rule(rule.id) is False
        assert await alert_service.list_rules() == ()


@pytest.mark.asyncio
async def test_rule_deletion_preserves_historical_events(tmp_path: Path) -> None:
    async with StorageService(tmp_path / "alerts.sqlite3") as storage:
        alert_service = service(storage)
        rule = make_rule()
        await alert_service.create_rule(rule)
        event = (await alert_service.evaluate(make_quote(price="14"), as_of=AS_OF))[0]

        assert await alert_service.delete_rule(rule.id) is True
        assert await storage.list_alert_events(rule_id=rule.id) == [event]


@pytest.mark.asyncio
async def test_state_and_events_survive_service_and_storage_restart(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "alerts.sqlite3"
    rule = make_rule()
    async with StorageService(database_path) as storage:
        first_service = service(storage)
        await first_service.create_rule(rule)
        assert (
            len(await first_service.evaluate(make_quote(price="14"), as_of=AS_OF)) == 1
        )

    async with StorageService(database_path) as restarted_storage:
        restarted_service = service(restarted_storage)
        duplicate = await restarted_service.evaluate(
            make_quote(price="13", observed_at=OBSERVED_AT + timedelta(seconds=1)),
            as_of=AS_OF,
        )

        assert duplicate == ()
        assert len(await restarted_storage.list_alert_events(rule_id=rule.id)) == 1


@pytest.mark.asyncio
async def test_concurrent_evaluation_claims_transition_once(tmp_path: Path) -> None:
    async with StorageService(
        tmp_path / "alerts.sqlite3", busy_timeout_seconds=2
    ) as storage:
        alert_service = service(storage)
        rule = make_rule()
        quote = make_quote(price="14")
        await alert_service.create_rule(rule)

        results = await asyncio.gather(
            *(alert_service.evaluate(quote, as_of=AS_OF) for _ in range(12))
        )

        assert sum(len(events) for events in results) == 1
        assert len(await storage.list_alert_events(rule_id=rule.id)) == 1


class FailingStorage:
    def __init__(self, rule: AlertRule) -> None:
        self.rule = rule

    async def list_alert_rules(self, **filters: object) -> list[AlertRule]:
        del filters
        return [self.rule]

    async def commit_alert_evaluation(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise StorageError("simulated persistence failure")


@pytest.mark.asyncio
async def test_persistence_failure_propagates() -> None:
    with pytest.raises(StorageError, match="simulated persistence failure"):
        await service(FailingStorage(make_rule())).evaluate(
            make_quote(price="14"), as_of=AS_OF
        )


@pytest.mark.asyncio
async def test_duplicate_rule_persistence_failure_propagates(tmp_path: Path) -> None:
    async with StorageService(tmp_path / "alerts.sqlite3") as storage:
        alert_service = service(storage)
        rule = make_rule()
        await alert_service.create_rule(rule)

        with pytest.raises(StorageError, match="save alert rule"):
            await alert_service.create_rule(rule)


@pytest.mark.asyncio
async def test_failed_event_insert_rolls_back_state_transition(tmp_path: Path) -> None:
    duplicate_event_id = UUID(int=42)
    async with StorageService(tmp_path / "alerts.sqlite3") as storage:
        fixed_id_service = AlertService(
            storage,
            max_quote_age=timedelta(minutes=5),
            _event_id_factory=lambda: duplicate_event_id,
        )
        rule = make_rule()
        await fixed_id_service.create_rule(rule)
        assert (
            len(await fixed_id_service.evaluate(make_quote(price="14"), as_of=AS_OF))
            == 1
        )
        assert (
            await fixed_id_service.evaluate(
                make_quote(price="20", observed_at=OBSERVED_AT + timedelta(seconds=1)),
                as_of=AS_OF,
            )
            == ()
        )

        retrigger = make_quote(
            price="14", observed_at=OBSERVED_AT + timedelta(seconds=2)
        )
        with pytest.raises(StorageError, match="commit alert evaluation"):
            await fixed_id_service.evaluate(retrigger, as_of=AS_OF)

        # The failed event insert and active-state update shared one transaction.
        recovered = await service(storage).evaluate(retrigger, as_of=AS_OF)
        assert len(recovered) == 1
        assert len(await storage.list_alert_events(rule_id=rule.id)) == 2


@pytest.mark.asyncio
async def test_pricing_result_quote_is_compatible(tmp_path: Path) -> None:
    quote = make_quote(price="14")
    comparison = PricingService(max_quote_age=timedelta(minutes=5)).compare(
        ITEM,
        [quote],
        currency="GBP",
        quote_type=QuoteType.LOWEST_ASK,
        as_of=AS_OF,
    )
    assert comparison.best_quote is not None

    async with StorageService(tmp_path / "alerts.sqlite3") as storage:
        alert_service = service(storage)
        await alert_service.create_rule(make_rule())

        events = await alert_service.evaluate(comparison.best_quote, as_of=AS_OF)

        assert events[0].quote == comparison.best_quote


def test_service_has_no_notification_dependency() -> None:
    alert_service = service(object())

    assert all("notification" not in name.lower() for name in vars(alert_service))
    assert set(vars(alert_service)) == {
        "_storage",
        "_max_quote_age",
        "_event_id_factory",
    }


def test_public_methods_reject_wrong_argument_types() -> None:
    alert_service = service(object())

    async def exercise() -> None:
        with pytest.raises(TypeError, match="AlertRule"):
            await alert_service.create_rule(object())  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="UUID"):
            await alert_service.delete_rule("not-a-uuid")  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="MarketQuote"):
            await alert_service.evaluate(object(), as_of=AS_OF)  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="datetime"):
            await alert_service.evaluate(make_quote(), as_of=object())  # type: ignore[arg-type]

    asyncio.run(exercise())


def test_event_model_preserves_decimal_and_utc_serialization() -> None:
    event = AlertEvent(
        id=UUID(int=1),
        rule_id=UUID(int=2),
        quote=make_quote(price="0.123456789012345678901"),
        condition=AlertCondition.BELOW,
        threshold="0.123456789012345678902",
        triggered_at=AS_OF.astimezone(timezone(timedelta(hours=3))),
    )

    payload = event.model_dump_json()
    restored = AlertEvent.model_validate_json(payload)

    assert '"threshold":"0.123456789012345678902"' in payload
    assert restored == event
    assert restored.triggered_at.tzinfo is UTC
