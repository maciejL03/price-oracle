"""Process entrypoint for running the self-hosted market engine."""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Callable

from engine.bootstrap import create_market_engine
from engine.market_engine import MarketEngine

logger = logging.getLogger(__name__)

EXIT_SUCCESS = 0
EXIT_FAILURE = 1

EngineFactory = Callable[[], MarketEngine]


def configure_logging() -> None:
    """Configure concise process logging when no application handler exists."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _install_signal_handlers(
    loop: asyncio.AbstractEventLoop, shutdown_requested: asyncio.Event
) -> tuple[signal.Signals, ...]:
    """Request a clean shutdown on process termination signals."""
    installed: list[signal.Signals] = []

    for received_signal in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(
                received_signal,
                _request_shutdown,
                shutdown_requested,
                received_signal,
            )
        except (NotImplementedError, RuntimeError):
            logger.warning(
                "Signal handlers are unavailable; use task cancellation to stop the engine"
            )
            break
        installed.append(received_signal)

    return tuple(installed)


def _remove_signal_handlers(
    loop: asyncio.AbstractEventLoop, installed: tuple[signal.Signals, ...]
) -> None:
    for received_signal in installed:
        loop.remove_signal_handler(received_signal)


def _request_shutdown(
    shutdown_requested: asyncio.Event, received_signal: signal.Signals
) -> None:
    if shutdown_requested.is_set():
        logger.warning(
            "Received %s again; graceful shutdown is already in progress",
            received_signal.name,
        )
        return
    logger.info("Received %s; starting graceful shutdown", received_signal.name)
    shutdown_requested.set()


async def run(
    factory: EngineFactory = create_market_engine,
    *,
    shutdown_requested: asyncio.Event | None = None,
) -> int:
    """Start one engine, wait for shutdown, and release every owned resource."""
    try:
        engine = factory()
        await engine.start()
    except asyncio.CancelledError:
        logger.info("Engine startup was cancelled")
        raise
    except Exception:
        logger.exception("Engine startup failed")
        return EXIT_FAILURE

    event = shutdown_requested or asyncio.Event()
    loop = asyncio.get_running_loop()
    installed = _install_signal_handlers(loop, event)
    shutdown_failed = False
    try:
        logger.info("Market engine is running")
        await event.wait()
    except asyncio.CancelledError:
        logger.info("Engine task cancelled; starting graceful shutdown")
        raise
    finally:
        _remove_signal_handlers(loop, installed)
        try:
            await engine.stop()
        except Exception:
            shutdown_failed = True
            logger.exception("Engine shutdown failed")

    return EXIT_FAILURE if shutdown_failed else EXIT_SUCCESS


async def run_market_engine(
    engine: MarketEngine,
    *,
    shutdown_event: asyncio.Event | None = None,
    handle_signals: bool = True,
) -> None:
    """Run an already-composed engine until an explicit shutdown request."""
    if not isinstance(engine, MarketEngine):
        raise TypeError("engine must be a MarketEngine")
    if shutdown_event is not None and not isinstance(shutdown_event, asyncio.Event):
        raise TypeError("shutdown_event must be an asyncio.Event")

    event = shutdown_event or asyncio.Event()
    loop = asyncio.get_running_loop()
    installed = _install_signal_handlers(loop, event) if handle_signals else ()
    try:
        await engine.start()
        logger.info("Market engine is running")
        await event.wait()
    finally:
        _remove_signal_handlers(loop, installed)
        await engine.stop()


def main() -> int:
    """Run the engine process and translate an interrupted task into an exit code."""
    configure_logging()
    try:
        return asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("Engine process interrupted")
        return EXIT_SUCCESS


if __name__ == "__main__":  # pragma: no cover - exercised through ``main``.
    raise SystemExit(main())
