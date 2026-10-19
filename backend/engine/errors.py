class EngineError(Exception):
    """Base exception for engine infrastructure failures."""


class JobError(EngineError):
    """Base exception for job execution infrastructure failures."""


class JobLifecycleError(JobError):
    """Raised when an operation is invalid for the current job-system state."""


class JobShutdownTimeout(JobError, TimeoutError):
    """Raised after a job handler is forced to stop on shutdown timeout."""


class SchedulerError(EngineError):
    """Raised for invalid scheduler lifecycle or registration operations."""


class DuplicateMarketplaceError(EngineError):
    """Raised when a marketplace identifier is already registered."""


class UnknownMarketplaceError(EngineError):
    """Raised when a marketplace identifier is not registered."""
