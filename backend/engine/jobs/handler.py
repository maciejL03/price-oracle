import asyncio
import logging
from enum import Enum, auto

from engine.errors import JobLifecycleError, JobShutdownTimeout

from .base import EngineJob

logger = logging.getLogger(__name__)


class SubmissionResult(Enum):
    """Expected outcomes from a non-blocking job submission."""

    ACCEPTED = auto()
    DUPLICATE = auto()
    QUEUE_FULL = auto()


class JobHandler:
    """Execute jobs with a bounded queue and a fixed async worker pool."""

    def __init__(
        self, worker_count: int = 5, queue_capacity: int = 100, shutdown_timeout: float = 10.0
    ) -> None:
        if worker_count < 1:
            raise ValueError("worker_count must be positive")
        if queue_capacity < 1:
            raise ValueError("queue_capacity must be positive")
        if shutdown_timeout <= 0:
            raise ValueError("shutdown_timeout must be positive")

        self._queue: asyncio.Queue[tuple[str, EngineJob]] = asyncio.Queue(maxsize=queue_capacity)
        self._worker_count = worker_count
        self._shutdown_timeout = shutdown_timeout
        self._workers: list[asyncio.Task[None]] = []
        self._active_keys: set[str] = set()
        self._started = False
        self._accepting = False
        self._stopping = False

    @property
    def running(self) -> bool:
        """Whether the handler is running and accepting submissions."""
        return self._accepting

    async def start(self) -> None:
        """Start the configured worker pool."""
        if self._started:
            raise JobLifecycleError("JobHandler is already running")

        self._started = True
        self._accepting = True
        self._workers = [
            asyncio.create_task(self._worker(), name=f"job-worker-{index}")
            for index in range(self._worker_count)
        ]

    def submit(self, job: EngineJob) -> SubmissionResult:
        """Try to enqueue a job without waiting for queue space."""
        if not self._accepting:
            raise JobLifecycleError("JobHandler is not accepting jobs")

        key = job.key
        if not isinstance(key, str) or not key:
            raise ValueError("Job key must be a non-empty string")
        if key in self._active_keys:
            return SubmissionResult.DUPLICATE

        try:
            self._queue.put_nowait((key, job))
        except asyncio.QueueFull:
            return SubmissionResult.QUEUE_FULL

        self._active_keys.add(key)
        return SubmissionResult.ACCEPTED

    def is_active(self, key: str) -> bool:
        """Return whether a matching job is queued or running."""
        return key in self._active_keys

    async def stop(self, timeout: float | None = None) -> None:
        """Drain accepted work, stop workers, and reject further submissions.

        If the timeout expires, running jobs are cancelled, queued jobs are
        discarded, and JobShutdownTimeout is raised after cleanup completes.
        """
        if not self._started:
            return
        if self._stopping:
            raise JobLifecycleError("JobHandler is already stopping")

        drain_timeout = self._shutdown_timeout if timeout is None else timeout
        if drain_timeout <= 0:
            raise ValueError("timeout must be positive")

        self._stopping = True
        self._accepting = False
        timed_out = False

        try:
            try:
                await asyncio.wait_for(self._queue.join(), timeout=drain_timeout)
            except TimeoutError:
                timed_out = True
                logger.warning(
                    "JobHandler shutdown timed out after %.3f seconds; cancelling outstanding jobs",
                    drain_timeout,
                )

            await self._terminate_workers()
        except asyncio.CancelledError:
            await self._terminate_workers()
            raise
        finally:
            self._workers.clear()
            self._active_keys.clear()
            self._started = False
            self._stopping = False

        if timed_out:
            raise JobShutdownTimeout(
                f"JobHandler shutdown timed out after {drain_timeout:.3f} seconds"
            )

    async def _worker(self) -> None:
        while True:
            key, job = await self._queue.get()
            try:
                await job.execute()
            except asyncio.CancelledError:
                task = asyncio.current_task()
                if task is not None and task.cancelling():
                    raise
                logger.warning("Job cancelled itself: %s", key)
            except Exception:
                logger.exception("Job failed: %s", key)
            finally:
                self._active_keys.discard(key)
                self._queue.task_done()

    async def _terminate_workers(self) -> None:
        for worker in self._workers:
            worker.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)

        while True:
            try:
                key, _ = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            self._active_keys.discard(key)
            self._queue.task_done()
