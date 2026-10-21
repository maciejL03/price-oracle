"""Operating-system process lifecycle for the composed market engine."""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Iterable

from engine.market_engine import MarketEngine

logger = logging.getLogger(__name__)

_SHUTDOWN_SIGNALS = (signal.SIGINT, signal.SIGTERM)


async def run_market_engine(
    engine: MarketEngine,
    *,
    shutdown_event: asyncio.Event | None = None,
    handle_signals: bool = True,
) -> None:
    """Run an engine until shutdown is requested, then release its resources.

    Production callers normally leave signal handling enabled. Tests and other
    embedding applications can supply an event and retain signal ownership.
    Cancellation follows the same cleanup path as an operating-system signal.
    """
    if not isinstance(engine, MarketEngine):
        raise TypeError("engine must be a MarketEngine")
    if shutdown_event is not None and not isinstance(shutdown_event, asyncio.Event):
        raise TypeError("shutdown_event must be an asyncio.Event")

    stop_requested = shutdown_event or asyncio.Event()
    loop = asyncio.get_running_loop()
    registered_signals = (
        _install_signal_handlers(loop, stop_requested, _SHUTDOWN_SIGNALS)
        if handle_signals
        else ()
    )

    try:
        await engine.start()
        logger.info("Price Oracle engine process is ready")
        await stop_requested.wait()
        logger.info("Price Oracle engine process received a shutdown request")
    finally:
        _remove_signal_handlers(loop, registered_signals)
        await engine.stop()


def _install_signal_handlers(
    loop: asyncio.AbstractEventLoop,
    shutdown_event: asyncio.Event,
    signals: Iterable[signal.Signals],
) -> tuple[signal.Signals, ...]:
    registered: list[signal.Signals] = []
    for process_signal in signals:
        try:
            loop.add_signal_handler(process_signal, shutdown_event.set)
        except (NotImplementedError, RuntimeError):
            logger.warning(
                "Event loop does not support %s handlers; cancellation remains available",
                process_signal.name,
            )
            continue
        registered.append(process_signal)
    return tuple(registered)


def _remove_signal_handlers(
    loop: asyncio.AbstractEventLoop,
    signals: Iterable[signal.Signals],
) -> None:
    for process_signal in signals:
        loop.remove_signal_handler(process_signal)


__all__ = ["run_market_engine"]
