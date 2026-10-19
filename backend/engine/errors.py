class EngineError(Exception):
    """Base exception for engine infrastructure failures."""


class StorageError(EngineError):
    """Raised when durable engine storage cannot complete an operation."""


class StorageBusyError(StorageError):
    """Raised when SQLite remains locked after its configured busy timeout."""


class JobError(EngineError):
    """Base exception for job execution infrastructure failures."""


class JobLifecycleError(JobError):
    """Raised when an operation is invalid for the current job-system state."""


class JobShutdownTimeout(JobError, TimeoutError):
    """Raised after a job handler is forced to stop on shutdown timeout."""


class SchedulerError(EngineError):
    """Raised for invalid scheduler lifecycle or registration operations."""
