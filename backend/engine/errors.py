class EngineError(Exception):
    """Base exception for engine infrastructure failures."""


class StorageError(EngineError):
    """Raised when durable engine storage cannot complete an operation."""


class StorageBusyError(StorageError):
    """Raised when SQLite remains locked after its configured busy timeout."""


class NotificationError(EngineError):
    """Base exception for notification configuration and delivery state errors."""


class NotificationConfigurationError(NotificationError, ValueError):
    """Raised when notification delivery is not configured safely."""


class MissingNotificationCredentialsError(NotificationConfigurationError):
    """Raised when the configured channel has no credentials."""


class InvalidNotificationDestinationError(NotificationConfigurationError):
    """Raised when a notification destination is malformed or unsafe."""


class NotificationStateError(NotificationError):
    """Raised when notification persistence violates its service contract."""


class JobError(EngineError):
    """Base exception for job execution infrastructure failures."""


class JobLifecycleError(JobError):
    """Raised when an operation is invalid for the current job-system state."""


class JobShutdownTimeout(JobError, TimeoutError):
    """Raised after a job handler is forced to stop on shutdown timeout."""


class SchedulerError(EngineError):
    """Raised for invalid scheduler lifecycle or registration operations."""


class MarketplaceError(EngineError):
    """Base exception for marketplace integration failures."""


class MarketplaceAuthenticationError(MarketplaceError, PermissionError):
    """Raised when a marketplace rejects the supplied credentials."""


class MarketplaceUnavailableError(MarketplaceError, ConnectionError):
    """Raised when a marketplace cannot temporarily serve a request."""


class MarketplaceTimeoutError(MarketplaceError, TimeoutError):
    """Raised when a marketplace request exceeds its timeout."""


class MarketplaceRateLimitError(MarketplaceUnavailableError):
    """Raised when a marketplace asks the caller to slow down."""

    def __init__(self, message: str, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class MarketplaceResponseError(MarketplaceError, ValueError):
    """Raised when a marketplace response cannot be normalized safely."""


class MarketplaceRegistrationError(MarketplaceError, ValueError):
    """Raised when a marketplace cannot be registered."""


class InvalidMarketplaceError(MarketplaceRegistrationError, TypeError):
    """Raised when an object does not implement the marketplace contract."""


class DuplicateMarketplaceError(MarketplaceRegistrationError):
    """Raised when a marketplace identifier is already registered."""


class UnknownMarketplaceError(MarketplaceError, LookupError):
    """Raised when a requested marketplace has not been registered."""
