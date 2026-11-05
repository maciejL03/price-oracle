"""Domain models for durable notification delivery outcomes."""

from datetime import UTC, datetime
from enum import Enum
from typing import Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class DeliveryStatus(str, Enum):
    """Durable lifecycle states for one alert-event delivery."""

    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class DeliveryFailureCode(str, Enum):
    """Sanitized failure categories safe to persist and display."""

    NETWORK_ERROR = "network_error"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    PROVIDER_ERROR = "provider_error"


class NotificationDelivery(BaseModel):
    """Persisted state and final outcome for one alert-event delivery."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: UUID
    channel: Literal["discord", "demo"]
    status: DeliveryStatus
    attempts: int = Field(ge=0)
    started_at: AwareDatetime
    completed_at: AwareDatetime | None = None
    failure_code: DeliveryFailureCode | None = None
    failure_detail: str | None = Field(default=None, max_length=500)

    @field_validator("started_at", "completed_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime | None) -> datetime | None:
        return None if value is None else value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_lifecycle(self) -> Self:
        if self.status is DeliveryStatus.PENDING:
            if self.attempts != 0 or self.completed_at is not None:
                raise ValueError(
                    "pending deliveries cannot have attempts or completion"
                )
            if self.failure_code is not None or self.failure_detail is not None:
                raise ValueError("pending deliveries cannot have failure details")
            return self

        if self.attempts < 1 or self.completed_at is None:
            raise ValueError("completed deliveries require attempts and completed_at")
        if self.completed_at < self.started_at:
            raise ValueError("completed_at must not be earlier than started_at")

        has_failure = self.failure_code is not None or self.failure_detail is not None
        if self.status is DeliveryStatus.SUCCEEDED and has_failure:
            raise ValueError("successful deliveries cannot have failure details")
        if self.status is DeliveryStatus.FAILED and (
            self.failure_code is None or self.failure_detail is None
        ):
            raise ValueError("failed deliveries require failure details")
        return self
