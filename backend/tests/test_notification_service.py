import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr

from engine.alerts import AlertCondition, AlertEvent, AlertRule, AlertService
from engine.errors import (
    InvalidNotificationDestinationError,
    MissingNotificationCredentialsError,
    StorageError,
)
from engine.models import Item, MarketQuote, QuoteType
from engine.notifications import (
    DeliveryFailureCode,
    DeliveryStatus,
    NotificationDelivery,
    NotificationPolicy,
    NotificationService,
)
from engine.storage import StorageService

WEBHOOK_TOKEN = "super-secret-webhook-token"
WEBHOOK_URL = SecretStr(f"https://discord.com/api/webhooks/123456/{WEBHOOK_TOKEN}")
OBSERVED_AT = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
TRIGGERED_AT = OBSERVED_AT + timedelta(seconds=1)


def make_event(**overrides: object) -> AlertEvent:
    values: dict[str, object] = {
        "id": uuid4(),
        "rule_id": uuid4(),
        "quote": MarketQuote(
            marketplace="csfloat",
            market_hash_name="AK-47 | Redline (Field-Tested)",
            price=Decimal("14.25"),
            currency="GBP",
            observed_at=OBSERVED_AT,
            quote_type=QuoteType.LOWEST_ASK,
        ),
        "condition": AlertCondition.BELOW,
        "threshold": Decimal("15.00"),
        "triggered_at": TRIGGERED_AT,
    }
    values.update(overrides)
    return AlertEvent.model_validate(values)


class FakeNotificationStorage:
    def __init__(
        self, *, fail_claim: bool = False, fail_complete: bool = False
    ) -> None:
        self.records: dict[UUID, NotificationDelivery] = {}
        self.fail_claim = fail_claim
        self.fail_complete = fail_complete

    async def claim_notification_delivery(
        self, delivery: NotificationDelivery
    ) -> bool:
        if self.fail_claim:
            raise StorageError("claim failed")
        if delivery.event_id in self.records:
            return False
        self.records[delivery.event_id] = delivery
        return True

    async def get_notification_delivery(
        self, event_id: UUID
    ) -> NotificationDelivery | None:
        return self.records.get(event_id)

    async def complete_notification_delivery(
        self, delivery: NotificationDelivery
    ) -> None:
        if self.fail_complete:
            raise StorageError("completion failed")
        self.records[delivery.event_id] = delivery


def policy(**overrides: object) -> NotificationPolicy:
    values: dict[str, object] = {
        "max_attempts": 3,
        "request_timeout_seconds": 1.0,
        "initial_backoff_seconds": 0.0,
        "max_backoff_seconds": 0.0,
        "max_retry_after_seconds": 10.0,
    }
    values.update(overrides)
    return NotificationPolicy(**values)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_successful_delivery_waits_for_confirmation_and_persists() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "message-id"})

    storage = FakeNotificationStorage()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = NotificationService(
            storage, client, discord_webhook_url=WEBHOOK_URL, policy=policy()
        )
        result = await service.deliver(make_event())

    assert result.status is DeliveryStatus.SUCCEEDED
    assert result.attempts == 1
    assert storage.records[result.event_id] == result
    assert len(requests) == 1
    assert requests[0].url.params["wait"] == "true"
    payload = json.loads(requests[0].content)
    assert payload["allowed_mentions"] == {"parse": []}
    assert "AK-47 | Redline" in payload["content"]
    assert "14.25 GBP" in payload["content"]


@pytest.mark.parametrize(
    "url",
    [
        "http://discord.com/api/webhooks/123/token",
        "https://example.com/api/webhooks/123/token",
        "https://discord.com/not-a-webhook/123/token",
        "https://discord.com/api/webhooks/123/token?wait=false",
        "not a url",
    ],
)
@pytest.mark.asyncio
async def test_invalid_delivery_destination_is_rejected(url: str) -> None:
    async with httpx.AsyncClient() as client:
        with pytest.raises(InvalidNotificationDestinationError):
            NotificationService(
                FakeNotificationStorage(),
                client,
                discord_webhook_url=SecretStr(url),
            )


@pytest.mark.asyncio
async def test_missing_credentials_are_rejected() -> None:
    async with httpx.AsyncClient() as client:
        with pytest.raises(MissingNotificationCredentialsError):
            NotificationService(
                FakeNotificationStorage(), client, discord_webhook_url=None
            )


@pytest.mark.asyncio
async def test_network_failure_is_retried_and_recorded() -> None:
    attempts = 0
    sleeps: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("connection failed", request=request)

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    storage = FakeNotificationStorage()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = NotificationService(
            storage,
            client,
            discord_webhook_url=WEBHOOK_URL,
            policy=policy(max_attempts=3),
            _sleep=sleep,
        )
        result = await service.deliver(make_event())

    assert attempts == 3
    assert sleeps == [0.0, 0.0]
    assert result.status is DeliveryStatus.FAILED
    assert result.failure_code is DeliveryFailureCode.NETWORK_ERROR
    assert storage.records[result.event_id] == result


@pytest.mark.asyncio
async def test_request_timeout_is_retried_and_recorded() -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("timed out", request=request)

    storage = FakeNotificationStorage()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await NotificationService(
            storage,
            client,
            discord_webhook_url=WEBHOOK_URL,
            policy=policy(max_attempts=2),
        ).deliver(make_event())

    assert attempts == 2
    assert result.failure_code is DeliveryFailureCode.TIMEOUT
    assert result.attempts == 2


@pytest.mark.asyncio
async def test_rate_limit_delay_is_respected_before_retry() -> None:
    attempts = 0
    sleeps: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "0.25"})
        return httpx.Response(200, json={"id": "message-id"})

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await NotificationService(
            FakeNotificationStorage(),
            client,
            discord_webhook_url=WEBHOOK_URL,
            policy=policy(),
            _sleep=sleep,
        ).deliver(make_event())

    assert result.status is DeliveryStatus.SUCCEEDED
    assert result.attempts == 2
    assert sleeps == [0.25]


@pytest.mark.asyncio
async def test_rate_limit_body_is_used_and_excessive_delay_is_not_retried() -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(429, json={"retry_after": 30.0})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await NotificationService(
            FakeNotificationStorage(),
            client,
            discord_webhook_url=WEBHOOK_URL,
            policy=policy(max_retry_after_seconds=5.0),
        ).deliver(make_event())

    assert attempts == 1
    assert result.status is DeliveryStatus.FAILED
    assert result.failure_code is DeliveryFailureCode.RATE_LIMITED


@pytest.mark.asyncio
async def test_non_retryable_provider_error_is_recorded_once() -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(401, json={"message": "invalid webhook token"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await NotificationService(
            FakeNotificationStorage(),
            client,
            discord_webhook_url=WEBHOOK_URL,
            policy=policy(),
        ).deliver(make_event())

    assert attempts == 1
    assert result.failure_code is DeliveryFailureCode.PROVIDER_ERROR
    assert result.failure_detail == "Discord returned non-retryable HTTP 401"


@pytest.mark.asyncio
async def test_retryable_provider_error_exhausts_bounded_attempts() -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503, text="temporarily unavailable")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await NotificationService(
            FakeNotificationStorage(),
            client,
            discord_webhook_url=WEBHOOK_URL,
            policy=policy(max_attempts=3),
        ).deliver(make_event())

    assert attempts == 3
    assert result.status is DeliveryStatus.FAILED
    assert result.attempts == 3
    assert result.failure_detail == "Discord returned retryable HTTP 503"


@pytest.mark.asyncio
async def test_claim_storage_failure_prevents_external_request() -> None:
    requests = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = NotificationService(
            FakeNotificationStorage(fail_claim=True),
            client,
            discord_webhook_url=WEBHOOK_URL,
        )
        with pytest.raises(StorageError, match="claim failed"):
            await service.deliver(make_event())

    assert requests == 0


@pytest.mark.asyncio
async def test_completion_storage_failure_is_not_reported_as_success() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    storage = FakeNotificationStorage(fail_complete=True)
    event = make_event()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = NotificationService(
            storage, client, discord_webhook_url=WEBHOOK_URL
        )
        with pytest.raises(StorageError, match="completion failed"):
            await service.deliver(event)

    assert storage.records[event.id].status is DeliveryStatus.PENDING


@pytest.mark.asyncio
async def test_duplicate_delivery_returns_existing_result_without_request() -> None:
    requests = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200)

    storage = FakeNotificationStorage()
    event = make_event()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = NotificationService(
            storage, client, discord_webhook_url=WEBHOOK_URL
        )
        first = await service.deliver(event)
        duplicate = await service.deliver(event)

    assert requests == 1
    assert duplicate == first


@pytest.mark.asyncio
async def test_cancellation_propagates_and_leaves_durable_pending_claim() -> None:
    request_started = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        request_started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    storage = FakeNotificationStorage()
    event = make_event()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = NotificationService(
            storage, client, discord_webhook_url=WEBHOOK_URL
        )
        delivery = asyncio.create_task(service.deliver(event))
        await request_started.wait()
        delivery.cancel()
        with pytest.raises(asyncio.CancelledError):
            await delivery

    assert storage.records[event.id].status is DeliveryStatus.PENDING


@pytest.mark.asyncio
async def test_multiple_events_have_independent_delivery_records() -> None:
    requests = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200)

    storage = FakeNotificationStorage()
    first_event = make_event()
    second_event = make_event()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = NotificationService(
            storage, client, discord_webhook_url=WEBHOOK_URL
        )
        first, second = await asyncio.gather(
            service.deliver(first_event), service.deliver(second_event)
        )

    assert requests == 2
    assert first.event_id != second.event_id
    assert set(storage.records) == {first.event_id, second.event_id}


@pytest.mark.asyncio
async def test_credentials_never_appear_in_logs_or_failure_result(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "invalid webhook token"})

    caplog.set_level(logging.DEBUG)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await NotificationService(
            FakeNotificationStorage(),
            client,
            discord_webhook_url=WEBHOOK_URL,
            policy=policy(max_attempts=1),
        ).deliver(make_event())

    assert WEBHOOK_TOKEN not in caplog.text
    assert WEBHOOK_TOKEN not in repr(result)
    assert WEBHOOK_TOKEN not in (result.failure_detail or "")


@pytest.mark.asyncio
async def test_real_storage_persists_outcome_and_atomically_blocks_duplicate(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "notifications.sqlite3"
    request_started = asyncio.Event()
    release_request = asyncio.Event()
    requests = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        request_started.set()
        await release_request.wait()
        return httpx.Response(200, json={"id": "message-id"})

    async with StorageService(database_path) as storage:
        event = await _create_durable_alert_event(storage)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            service = NotificationService(
                storage, client, discord_webhook_url=WEBHOOK_URL
            )
            first_task = asyncio.create_task(service.deliver(event))
            await request_started.wait()
            concurrent_duplicate = await service.deliver(event)
            release_request.set()
            first = await first_task
            later_duplicate = await service.deliver(event)

        persisted = await storage.get_notification_delivery(event.id)

    assert requests == 1
    assert concurrent_duplicate.status is DeliveryStatus.PENDING
    assert first.status is DeliveryStatus.SUCCEEDED
    assert later_duplicate == first
    assert persisted == first


@pytest.mark.asyncio
async def test_real_storage_persists_failed_outcome(tmp_path: Path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Unknown Webhook"})

    async with StorageService(tmp_path / "failed-notification.sqlite3") as storage:
        event = await _create_durable_alert_event(storage)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await NotificationService(
                storage, client, discord_webhook_url=WEBHOOK_URL
            ).deliver(event)
        persisted = await storage.get_notification_delivery(event.id)

    assert result.status is DeliveryStatus.FAILED
    assert result.failure_detail == "Discord returned non-retryable HTTP 404"
    assert persisted == result


async def _create_durable_alert_event(storage: StorageService) -> AlertEvent:
    rule = AlertRule(
        id=uuid4(),
        item=Item(market_hash_name="AK-47 | Redline (Field-Tested)"),
        marketplace="csfloat",
        currency="GBP",
        quote_type=QuoteType.LOWEST_ASK,
        condition=AlertCondition.BELOW,
        threshold=Decimal("15.00"),
    )
    alerts = AlertService(storage, max_quote_age=timedelta(minutes=5))
    await alerts.create_rule(rule)
    events = await alerts.evaluate(make_event().quote, as_of=TRIGGERED_AT)
    return events[0]
