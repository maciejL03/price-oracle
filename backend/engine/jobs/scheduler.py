import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from engine.errors import JobLifecycleError, SchedulerError

from .base import EngineJob
from .handler import JobHandler, SubmissionResult

logger = logging.getLogger(__name__)


@dataclass
class _RecurringJob:
    key: str
    factory: Callable[[], EngineJob]
    interval_seconds: float
    next_run: float
    awaiting_completion: bool = False
    pending_job: EngineJob | None = None


class JobScheduler:
    """Submit recurring job instances to an existing JobHandler."""

    def __init__(self, handler: JobHandler, poll_seconds: float = 0.1) -> None:
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        self._handler = handler
        self._poll_seconds = poll_seconds
        self._registrations: dict[str, _RecurringJob] = {}
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None

    def register(
        self,
        key: str,
        job_factory: Callable[[], EngineJob],
        interval_seconds: float,
        *,
        run_immediately: bool = False,
    ) -> None:
        """Register a recurring definition without executing it directly."""
        if not isinstance(key, str) or not key:
            raise ValueError("Schedule key must be a non-empty string")
        if not callable(job_factory):
            raise ValueError("job_factory must be callable")
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        if key in self._registrations:
            raise SchedulerError(f"Schedule is already registered: {key}")

        now = time.monotonic()
        self._registrations[key] = _RecurringJob(
            key=key,
            factory=job_factory,
            interval_seconds=interval_seconds,
            next_run=now if run_immediately else now + interval_seconds,
        )

    def unregister(self, key: str) -> bool:
        """Prevent future submissions while allowing accepted work to finish."""
        return self._registrations.pop(key, None) is not None

    async def start(self) -> None:
        if self._task is not None:
            raise SchedulerError("JobScheduler is already running")
        if not self._handler.running:
            raise SchedulerError("JobHandler must be started before JobScheduler")
        self._task = asyncio.create_task(self._run(), name="job-scheduler")

    async def stop(self) -> None:
        if self._task is None:
            return

        task = self._task
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise
        finally:
            self._task = None

    async def _run(self) -> None:
        while True:
            now = time.monotonic()
            for registration in list(self._registrations.values()):
                self._process_registration(registration, now)
            await asyncio.sleep(self._poll_seconds)

    def _process_registration(self, registration: _RecurringJob, now: float) -> None:
        if registration.awaiting_completion:
            if not self._handler.is_active(registration.key):
                registration.awaiting_completion = False
                registration.next_run = now + registration.interval_seconds
            return

        if now < registration.next_run:
            return

        if self._handler.is_active(registration.key):
            registration.pending_job = None
            registration.awaiting_completion = True
            return

        if registration.pending_job is None:
            try:
                job = registration.factory()
                if job.key != registration.key:
                    raise SchedulerError(
                        f"Factory for {registration.key!r} returned job with key {job.key!r}"
                    )
                registration.pending_job = job
            except Exception:
                logger.exception("Unable to create recurring job: %s", registration.key)
                registration.next_run = now + registration.interval_seconds
                return

        try:
            result = self._handler.submit(registration.pending_job)
        except JobLifecycleError:
            logger.exception("Unable to submit recurring job: %s", registration.key)
            registration.pending_job = None
            registration.next_run = now + registration.interval_seconds
            return

        if result is SubmissionResult.QUEUE_FULL:
            return

        registration.pending_job = None
        registration.awaiting_completion = True
