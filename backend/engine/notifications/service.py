"""Discord delivery orchestration for durable alert events."""

from __future__ import annotations

import asyncio
import logging
import math
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

import httpx
from pydantic import SecretStr

from engine.alerts import AlertEvent
from engine.errors import (
    InvalidNotificationDestinationError,
    MissingNotificationCredentialsError,
    NotificationStateError,
)

from .models import DeliveryFailureCode, DeliveryStatus, NotificationDelivery

Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], datetime]

_DISCORD_WEBHOOK_PATH = re.compile(
    r"^/api(?:/v\d+)?/webhooks/\d+/[A-Za-z0-9._-]+$"
)
_DISCORD_WEBHOOK_IN_TEXT = re.compile(
    r"(https://discord\.com/api(?:/v\d+)?/webhooks/\d+/)[A-Za-z0-9._-]+"
)


class _DiscordWebhookLogFilter(logging.Filter):
    """Prevent httpx's request logger from exposing webhook path tokens."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not record.args:
            return True
        if isinstance(record.args, tuple):
            record.args = tuple(_redact_webhook_token(value) for value in record.args)
        elif isinstance(record.args, dict):
            record.args = {
                key: _redact_webhook_token(value) for key, value in record.args.items()
            }
        return True


def _redact_webhook_token(value: object) -> object:
    if isinstance(value, (str, httpx.URL)):
        return _DISCORD_WEBHOOK_IN_TEXT.sub(r"\1<redacted>", str(value))
    return value


logging.getLogger("httpx").addFilter(_DiscordWebhookLogFilter())


class NotificationStorage(Protocol):
    """Persistence operations required by :class:`NotificationService`."""

    async def claim_notification_delivery(
        self, delivery: NotificationDelivery
    ) -> bool: ...

    async def get_notification_delivery(
        self, event_id: UUID
    ) -> NotificationDelivery | None: ...

    async def complete_notification_delivery(
        self, delivery: NotificationDelivery
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class NotificationPolicy:
    """Bounded HTTP retry and timeout controls for synchronous delivery."""

    max_attempts: int = 3
    request_timeout_seconds: float = 10.0
    initial_backoff_seconds: float = 0.5
    backoff_multiplier: float = 2.0
    max_backoff_seconds: float = 5.0
    max_retry_after_seconds: float = 60.0

    def __post_init__(self) -> None:
        if isinstance(self.max_attempts, bool) or self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        positive = {"request_timeout_seconds": self.request_timeout_seconds}
        non_negative = {
            "initial_backoff_seconds": self.initial_backoff_seconds,
            "max_backoff_seconds": self.max_backoff_seconds,
            "max_retry_after_seconds": self.max_retry_after_seconds,
        }
        for name, value in positive.items():
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        for name, value in non_negative.items():
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if not math.isfinite(self.backoff_multiplier) or self.backoff_multiplier < 1:
            raise ValueError("backoff_multiplier must be finite and at least 1")
        if self.max_backoff_seconds < self.initial_backoff_seconds:
            raise ValueError(
                "max_backoff_seconds must be at least initial_backoff_seconds"
            )


@dataclass(frozen=True, slots=True)
class _AttemptFailure:
    code: DeliveryFailureCode
    detail: str
    retryable: bool
    retry_after_seconds: float | None = None


class NotificationService:
    """Deliver durable alert events to one configured Discord webhook."""

    def __init__(
        self,
        storage: NotificationStorage,
        http_client: httpx.AsyncClient,
        *,
        discord_webhook_url: SecretStr | None,
        policy: NotificationPolicy | None = None,
        _clock: Clock | None = None,
        _sleep: Sleep = asyncio.sleep,
    ) -> None:
        if discord_webhook_url is None:
            raise MissingNotificationCredentialsError(
                "Discord webhook credentials are not configured"
            )
        if not isinstance(discord_webhook_url, SecretStr):
            raise TypeError("discord_webhook_url must be a SecretStr")
        if not isinstance(http_client, httpx.AsyncClient):
            raise TypeError("http_client must be an httpx.AsyncClient")

        validate_discord_webhook_url(discord_webhook_url)
        self._storage = storage
        self._http_client = http_client
        self._discord_webhook_url = discord_webhook_url
        self._policy = policy or NotificationPolicy()
        self._clock = _clock or _utc_now
        self._sleep = _sleep

    async def deliver(self, event: AlertEvent) -> NotificationDelivery:
        """Deliver one event or return its previously claimed durable outcome."""
        if not isinstance(event, AlertEvent):
            raise TypeError("event must be an AlertEvent")

        pending = NotificationDelivery(
            event_id=event.id,
            channel="discord",
            status=DeliveryStatus.PENDING,
            attempts=0,
            started_at=self._now(),
        )
        claimed = await self._storage.claim_notification_delivery(pending)
        if not claimed:
            existing = await self._storage.get_notification_delivery(event.id)
            if existing is None:
                raise NotificationStateError(
                    "delivery was already claimed but its state could not be read"
                )
            return existing

        failure: _AttemptFailure | None = None
        for attempt in range(1, self._policy.max_attempts + 1):
            failure = await self._attempt_delivery(event)
            if failure is None:
                result = pending.model_copy(
                    update={
                        "status": DeliveryStatus.SUCCEEDED,
                        "attempts": attempt,
                        "completed_at": self._now(),
                    }
                )
                await self._storage.complete_notification_delivery(result)
                return result

            if attempt == self._policy.max_attempts or not failure.retryable:
                break

            delay = failure.retry_after_seconds
            if delay is None:
                delay = self._backoff_seconds(attempt)
            elif delay > self._policy.max_retry_after_seconds:
                break
            await self._sleep(delay)

        if failure is None:  # pragma: no cover - the retry loop always runs
            raise AssertionError("notification retry loop terminated unexpectedly")

        result = pending.model_copy(
            update={
                "status": DeliveryStatus.FAILED,
                "attempts": attempt,
                "completed_at": self._now(),
                "failure_code": failure.code,
                "failure_detail": failure.detail,
            }
        )
        await self._storage.complete_notification_delivery(result)
        return result

    async def _attempt_delivery(self, event: AlertEvent) -> _AttemptFailure | None:
        try:
            response = await self._http_client.post(
                self._discord_webhook_url.get_secret_value(),
                params={"wait": "true"},
                json=_discord_payload(event),
                timeout=self._policy.request_timeout_seconds,
            )
        except httpx.TimeoutException:
            return _AttemptFailure(
                DeliveryFailureCode.TIMEOUT,
                "Discord request timed out",
                retryable=True,
            )
        except httpx.RequestError:
            return _AttemptFailure(
                DeliveryFailureCode.NETWORK_ERROR,
                "Discord request failed due to a network error",
                retryable=True,
            )

        if 200 <= response.status_code < 300:
            return None
        if response.status_code == 429:
            return _AttemptFailure(
                DeliveryFailureCode.RATE_LIMITED,
                "Discord rate limit prevented delivery",
                retryable=True,
                retry_after_seconds=_discord_retry_after(response),
            )
        if 500 <= response.status_code < 600:
            return _AttemptFailure(
                DeliveryFailureCode.PROVIDER_ERROR,
                f"Discord returned retryable HTTP {response.status_code}",
                retryable=True,
            )
        return _AttemptFailure(
            DeliveryFailureCode.PROVIDER_ERROR,
            f"Discord returned non-retryable HTTP {response.status_code}",
            retryable=False,
        )

    def _backoff_seconds(self, failed_attempt: int) -> float:
        delay = (
            self._policy.initial_backoff_seconds
            * self._policy.backoff_multiplier ** (failed_attempt - 1)
        )
        return min(delay, self._policy.max_backoff_seconds)

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("notification clock must return a timezone-aware datetime")
        return value.astimezone(UTC)


class SimulatedNotificationService:
    """Persist successful demo delivery without contacting an external service."""

    def __init__(self, storage: NotificationStorage, *, _clock: Clock | None = None) -> None:
        self._storage = storage
        self._clock = _clock or _utc_now

    async def deliver(self, event: AlertEvent) -> NotificationDelivery:
        if not isinstance(event, AlertEvent):
            raise TypeError("event must be an AlertEvent")
        started_at = self._now()
        pending = NotificationDelivery(
            event_id=event.id,
            channel="demo",
            status=DeliveryStatus.PENDING,
            attempts=0,
            started_at=started_at,
        )
        if not await self._storage.claim_notification_delivery(pending):
            existing = await self._storage.get_notification_delivery(event.id)
            if existing is None:
                raise NotificationStateError(
                    "simulated delivery was claimed but could not be read"
                )
            return existing

        result = pending.model_copy(
            update={
                "status": DeliveryStatus.SUCCEEDED,
                "attempts": 1,
                "completed_at": self._now(),
            }
        )
        await self._storage.complete_notification_delivery(result)
        logging.getLogger(__name__).info(
            "Simulated notification delivered: event=%s item=%s",
            event.id,
            event.quote.market_hash_name,
        )
        return result

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("notification clock must return a timezone-aware datetime")
        return value.astimezone(UTC)


def validate_discord_webhook_url(value: SecretStr) -> None:
    raw_url = value.get_secret_value()
    try:
        parsed = httpx.URL(raw_url)
    except (TypeError, ValueError):
        raise InvalidNotificationDestinationError(
            "Discord webhook URL is invalid"
        ) from None

    valid = (
        parsed.scheme == "https"
        and parsed.host == "discord.com"
        and parsed.port in {None, 443}
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment
        and _DISCORD_WEBHOOK_PATH.fullmatch(parsed.path) is not None
    )
    if not valid:
        raise InvalidNotificationDestinationError(
            "Discord webhook URL must be an HTTPS discord.com webhook URL"
        )


def _discord_payload(event: AlertEvent) -> dict[str, Any]:
    quote = event.quote
    item_name = quote.market_hash_name
    if len(item_name) > 512:
        item_name = f"{item_name[:509]}..."
    content = "\n".join(
        (
            "**CS2 price alert**",
            item_name,
            f"Price: {_format_decimal(quote.price)} {quote.currency}",
            (
                f"Condition: {event.condition.value} "
                f"{_format_decimal(event.threshold)} {quote.currency}"
            ),
            f"Marketplace: {quote.marketplace}",
            f"Observed: {quote.observed_at.isoformat()}",
            f"Event: {event.id}",
        )
    )
    return {"content": content, "allowed_mentions": {"parse": []}}


def _format_decimal(value: Decimal) -> str:
    return format(value, "f")


def _discord_retry_after(response: httpx.Response) -> float | None:
    header_value = response.headers.get("Retry-After")
    parsed = _non_negative_float(header_value)
    if parsed is not None:
        return parsed

    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    return _non_negative_float(payload.get("retry_after"))


def _non_negative_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed < 0:
        return None
    return parsed


def _utc_now() -> datetime:
    return datetime.now(UTC)
