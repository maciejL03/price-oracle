"""Central lifecycle coordination for the market engine."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from enum import Enum

from engine.alerts import AlertService
from engine.errors import EngineError
from engine.jobs import JobHandler, JobScheduler
from engine.marketplaces import MarketplaceRegistry
from engine.notifications import NotificationService
from engine.services import CollectionService, PricingService
from engine.storage import StorageService
from engine.watchlists import WatchlistService

logger = logging.getLogger(__name__)

AsyncCloser = Callable[[], Awaitable[None]]


class EngineState(str, Enum):
    """Externally observable lifecycle state."""

    NEW = "new"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"


class EngineLifecycleError(EngineError):
    """Raised when the engine lifecycle cannot complete safely."""


class EngineStartupError(EngineLifecycleError):
    """Raised after startup fails and initialized resources are cleaned up."""

    def __init__(self, stage: str, cleanup_failures: tuple[Exception, ...]) -> None:
        self.stage = stage
        self.cleanup_failures = cleanup_failures
        message = f"MarketEngine startup failed during {stage}"
        if cleanup_failures:
            message += f"; {len(cleanup_failures)} cleanup operation(s) also failed"
        super().__init__(message)


class EngineShutdownError(EngineLifecycleError):
    """Raised after shutdown attempts every cleanup operation but some fail."""

    def __init__(self, failures: tuple[Exception, ...]) -> None:
        self.failures = failures
        super().__init__(
            f"MarketEngine shutdown completed with {len(failures)} failure(s)"
        )


class MarketEngine:
    """Start and stop already-composed engine services in dependency order."""

    def __init__(
        self,
        *,
        storage: StorageService,
        registry: MarketplaceRegistry,
        collection: CollectionService,
        job_handler: JobHandler,
        scheduler: JobScheduler,
        watchlists: WatchlistService,
        pricing: PricingService,
        alerts: AlertService,
        notifications: NotificationService | None,
        owned_resource_closers: Iterable[AsyncCloser] = (),
    ) -> None:
        self.storage = storage
        self.registry = registry
        self.collection = collection
        self.job_handler = job_handler
        self.scheduler = scheduler
        self.watchlists = watchlists
        self.pricing = pricing
        self.alerts = alerts
        self.notifications = notifications

        self._owned_resource_closers = tuple(owned_resource_closers)
        self._lifecycle_lock = asyncio.Lock()
        self._state = EngineState.NEW
        self._handler_started = False
        self._scheduler_started = False
        self._resources_closed = False
        self._storage_closed = False

    @property
    def state(self) -> EngineState:
        return self._state

    @property
    def running(self) -> bool:
        return self._state is EngineState.RUNNING

    async def __aenter__(self) -> MarketEngine:
        await self.start()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.stop()

    async def start(self) -> None:
        """Initialize persistence, workers, restored schedules, and scheduling."""
        async with self._lifecycle_lock:
            if self._state is not EngineState.NEW:
                raise EngineLifecycleError(
                    f"MarketEngine cannot start from state {self._state.value}"
                )

            self._state = EngineState.STARTING
            stage = "storage initialization"
            try:
                await self.storage.initialize()

                stage = "job handler startup"
                await self.job_handler.start()
                self._handler_started = True

                stage = "watchlist restoration"
                await self.watchlists.sync_jobs()

                stage = "job scheduler startup"
                await self.scheduler.start()
                self._scheduler_started = True
            except asyncio.CancelledError:
                logger.warning("MarketEngine startup was cancelled during %s", stage)
                await self._cleanup_after_start_failure()
                self._state = EngineState.STOPPED
                raise
            except Exception as error:
                logger.exception("MarketEngine startup failed during %s", stage)
                cleanup_failures, cleanup_cancelled = (
                    await self._cleanup_after_start_failure()
                )
                self._state = EngineState.STOPPED
                if cleanup_cancelled:
                    raise asyncio.CancelledError() from error
                raise EngineStartupError(stage, cleanup_failures) from error

            self._state = EngineState.RUNNING
            logger.info("MarketEngine started")

    async def stop(self) -> None:
        """Stop scheduling, drain workers, and release all owned resources."""
        async with self._lifecycle_lock:
            if self._state is EngineState.STOPPED:
                return

            self._state = EngineState.STOPPING
            cleanup_task = asyncio.create_task(
                self._cleanup_components(), name="market-engine-cleanup"
            )
            cancelled = False
            try:
                failures = await asyncio.shield(cleanup_task)
            except asyncio.CancelledError:
                cancelled = True
                logger.warning(
                    "MarketEngine shutdown cancellation requested; finishing cleanup"
                )
                failures = await asyncio.shield(cleanup_task)

            self._state = EngineState.STOPPED
            logger.info("MarketEngine stopped")

            if cancelled:
                for failure in failures:
                    logger.error(
                        "Shutdown failure while handling cancellation: %s",
                        type(failure).__name__,
                        exc_info=failure,
                    )
                raise asyncio.CancelledError()
            if failures:
                raise EngineShutdownError(failures)

    async def _cleanup_after_start_failure(
        self,
    ) -> tuple[tuple[Exception, ...], bool]:
        cleanup_task = asyncio.create_task(
            self._cleanup_components(), name="market-engine-startup-rollback"
        )
        try:
            return await asyncio.shield(cleanup_task), False
        except asyncio.CancelledError:
            return await asyncio.shield(cleanup_task), True

    async def _cleanup_components(self) -> tuple[Exception, ...]:
        failures: list[Exception] = []

        if self._scheduler_started:
            await self._attempt_cleanup(
                "job scheduler", self.scheduler.stop, failures
            )
            self._scheduler_started = False

        if self._handler_started:
            await self._attempt_cleanup(
                "job handler", self.job_handler.stop, failures
            )
            self._handler_started = False

        if not self._resources_closed:
            for index, closer in reversed(
                tuple(enumerate(self._owned_resource_closers))
            ):
                await self._attempt_cleanup(
                    f"owned async resource {index}", closer, failures
                )
            self._resources_closed = True

        if not self._storage_closed:
            await self._attempt_cleanup("storage", self.storage.close, failures)
            self._storage_closed = True

        return tuple(failures)

    @staticmethod
    async def _attempt_cleanup(
        component: str,
        operation: AsyncCloser,
        failures: list[Exception],
    ) -> None:
        try:
            await operation()
        except Exception as error:
            failures.append(error)
            logger.exception("Failed to stop MarketEngine component: %s", component)


__all__ = [
    "AsyncCloser",
    "EngineLifecycleError",
    "EngineShutdownError",
    "EngineStartupError",
    "EngineState",
    "MarketEngine",
]
