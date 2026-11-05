import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from engine.events import EngineEvent, EventBus, MarketQuoteStored
from engine.models import MarketQuote, QuoteType


@dataclass(frozen=True, slots=True, kw_only=True)
class ExampleEvent(EngineEvent):
    value: str


@dataclass(frozen=True, slots=True, kw_only=True)
class OtherEvent(EngineEvent):
    number: int


OCCURRED_AT = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


def event(value: str = "value") -> ExampleEvent:
    return ExampleEvent(occurred_at=OCCURRED_AT, value=value)


def quote() -> MarketQuote:
    return MarketQuote(
        marketplace="csfloat",
        market_hash_name="AK-47 | Redline (Field-Tested)",
        price=Decimal("14.25"),
        currency="GBP",
        observed_at=OCCURRED_AT,
        quote_type=QuoteType.LOWEST_ASK,
    )


def test_event_timestamp_is_immutable_and_normalized_to_utc() -> None:
    original = datetime(2026, 10, 21, 13, 0, tzinfo=timezone(timedelta(hours=1)))
    created = event()
    normalized = ExampleEvent(occurred_at=original, value="normalized")

    assert created.occurred_at == OCCURRED_AT
    assert normalized.occurred_at == OCCURRED_AT
    with pytest.raises(ValueError, match="timezone-aware"):
        ExampleEvent(occurred_at=datetime(2026, 10, 21, 12, 0), value="naive")


def test_market_quote_stored_preserves_the_committed_quote_payload() -> None:
    stored = MarketQuoteStored(occurred_at=OCCURRED_AT, quote=quote())

    assert stored.quote == quote()
    with pytest.raises(TypeError, match="MarketQuote"):
        MarketQuoteStored(occurred_at=OCCURRED_AT, quote=object())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_publish_delivers_event_to_registered_subscriber() -> None:
    bus = EventBus()
    received: list[ExampleEvent] = []

    async def handler(received_event: ExampleEvent) -> None:
        received.append(received_event)

    assert bus.subscribe(ExampleEvent, handler)
    published = event()
    await bus.publish(published)

    assert received == [published]


@pytest.mark.asyncio
async def test_multiple_subscribers_run_in_registration_order() -> None:
    bus = EventBus()
    calls: list[str] = []

    async def first(_: ExampleEvent) -> None:
        calls.append("first")

    async def second(_: ExampleEvent) -> None:
        calls.append("second")

    bus.subscribe(ExampleEvent, first)
    bus.subscribe(ExampleEvent, second)

    await bus.publish(event())

    assert calls == ["first", "second"]


@pytest.mark.asyncio
async def test_event_types_are_dispatched_independently() -> None:
    bus = EventBus()
    received: list[object] = []

    async def example_handler(value: ExampleEvent) -> None:
        received.append(value)

    async def other_handler(value: OtherEvent) -> None:
        received.append(value)

    bus.subscribe(ExampleEvent, example_handler)
    bus.subscribe(OtherEvent, other_handler)
    first = event("first")
    second = OtherEvent(occurred_at=OCCURRED_AT, number=2)

    await bus.publish(first)
    await bus.publish(second)

    assert received == [first, second]


@pytest.mark.asyncio
async def test_unsubscribe_and_duplicate_subscription_results() -> None:
    bus = EventBus()
    calls: list[ExampleEvent] = []

    async def handler(value: ExampleEvent) -> None:
        calls.append(value)

    assert bus.subscribe(ExampleEvent, handler)
    assert not bus.subscribe(ExampleEvent, handler)
    assert bus.unsubscribe(ExampleEvent, handler)
    assert not bus.unsubscribe(ExampleEvent, handler)

    await bus.publish(event())

    assert calls == []


@pytest.mark.asyncio
async def test_publish_without_subscribers_is_a_noop() -> None:
    await EventBus().publish(event())


@pytest.mark.asyncio
async def test_subscriber_failures_are_logged_and_raised_after_other_handlers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bus = EventBus()
    handled: list[str] = []
    failure = ValueError("simulated subscriber failure")

    async def fail(_: ExampleEvent) -> None:
        raise failure

    async def succeed(_: ExampleEvent) -> None:
        handled.append("succeeded")

    bus.subscribe(ExampleEvent, fail)
    bus.subscribe(ExampleEvent, succeed)

    with caplog.at_level(logging.ERROR, logger="engine.events.bus"):
        with pytest.raises(ExceptionGroup) as raised:
            await bus.publish(event())

    assert raised.value.exceptions == (failure,)
    assert handled == ["succeeded"]
    assert "Event subscriber failed: event_type=ExampleEvent" in caplog.text


@pytest.mark.asyncio
async def test_cancellation_during_dispatch_propagates_without_running_later_handlers() -> None:
    bus = EventBus()
    started = asyncio.Event()
    later_handler_ran = False

    async def blocked(_: ExampleEvent) -> None:
        started.set()
        await asyncio.Event().wait()

    async def later(_: ExampleEvent) -> None:
        nonlocal later_handler_ran
        later_handler_ran = True

    bus.subscribe(ExampleEvent, blocked)
    bus.subscribe(ExampleEvent, later)
    publishing = asyncio.create_task(bus.publish(event()))
    await asyncio.wait_for(started.wait(), 1)
    publishing.cancel()

    with pytest.raises(asyncio.CancelledError):
        await publishing

    assert not later_handler_ran


@pytest.mark.asyncio
async def test_concurrent_publications_have_independent_dispatches() -> None:
    bus = EventBus()
    both_started = asyncio.Event()
    release = asyncio.Event()
    started = 0
    received: list[str] = []

    async def handler(value: ExampleEvent) -> None:
        nonlocal started
        started += 1
        if started == 2:
            both_started.set()
        await release.wait()
        received.append(value.value)

    bus.subscribe(ExampleEvent, handler)
    first = asyncio.create_task(bus.publish(event("first")))
    second = asyncio.create_task(bus.publish(event("second")))
    await asyncio.wait_for(both_started.wait(), 1)
    release.set()
    await asyncio.gather(first, second)

    assert set(received) == {"first", "second"}


@pytest.mark.asyncio
async def test_nested_publication_is_completed_before_outer_dispatch_continues() -> None:
    bus = EventBus()
    calls: list[str] = []

    async def outer_first(_: ExampleEvent) -> None:
        calls.append("outer-first-before")
        await bus.publish(OtherEvent(occurred_at=OCCURRED_AT, number=1))
        calls.append("outer-first-after")

    async def outer_second(_: ExampleEvent) -> None:
        calls.append("outer-second")

    async def nested(_: OtherEvent) -> None:
        calls.append("nested")

    bus.subscribe(ExampleEvent, outer_first)
    bus.subscribe(ExampleEvent, outer_second)
    bus.subscribe(OtherEvent, nested)

    await bus.publish(event())

    assert calls == [
        "outer-first-before",
        "nested",
        "outer-first-after",
        "outer-second",
    ]


@pytest.mark.asyncio
async def test_subscription_changes_during_dispatch_apply_to_the_next_publish() -> None:
    bus = EventBus()
    calls: list[str] = []

    async def added(_: ExampleEvent) -> None:
        calls.append("added")

    async def mutating(_: ExampleEvent) -> None:
        calls.append("mutating")
        bus.subscribe(ExampleEvent, added)

    bus.subscribe(ExampleEvent, mutating)

    await bus.publish(event("first"))
    await bus.publish(event("second"))

    assert calls == ["mutating", "mutating", "added"]


@pytest.mark.asyncio
async def test_bus_instances_are_isolated_and_create_no_background_tasks() -> None:
    first = EventBus()
    second = EventBus()
    received: list[str] = []

    async def handler(_: ExampleEvent) -> None:
        received.append("first")

    first.subscribe(ExampleEvent, handler)
    await second.publish(event())
    await first.publish(event())
    await asyncio.sleep(0)

    assert received == ["first"]
    assert not any(
        task.get_name().startswith("event-bus")
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task()
    )


@pytest.mark.asyncio
async def test_invalid_event_and_subscription_inputs_are_rejected() -> None:
    bus = EventBus()

    async def handler(_: ExampleEvent) -> None:
        return None

    with pytest.raises(TypeError, match="event_type"):
        bus.subscribe(str, handler)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="handler"):
        bus.subscribe(ExampleEvent, object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="event must"):
        await bus.publish(object())  # type: ignore[arg-type]
