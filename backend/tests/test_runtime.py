import asyncio
import logging
import signal

import pytest

from engine.runtime import EXIT_FAILURE, EXIT_SUCCESS, _request_shutdown, run


class FakeEngine:
    def __init__(
        self,
        *,
        start_error: Exception | None = None,
        stop_error: Exception | None = None,
    ) -> None:
        self.start_error = start_error
        self.stop_error = stop_error
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True
        if self.start_error is not None:
            raise self.start_error

    async def stop(self) -> None:
        self.stopped = True
        if self.stop_error is not None:
            raise self.stop_error


@pytest.mark.asyncio
async def test_runtime_starts_then_stops_engine_when_shutdown_requested() -> None:
    engine = FakeEngine()
    shutdown_requested = asyncio.Event()
    shutdown_requested.set()

    result = await run(lambda: engine, shutdown_requested=shutdown_requested)

    assert result == EXIT_SUCCESS
    assert engine.started
    assert engine.stopped


@pytest.mark.asyncio
async def test_runtime_returns_failure_when_startup_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = FakeEngine(start_error=RuntimeError("database unavailable"))

    with caplog.at_level(logging.ERROR):
        result = await run(lambda: engine, shutdown_requested=asyncio.Event())

    assert result == EXIT_FAILURE
    assert engine.started
    assert not engine.stopped
    assert "Engine startup failed" in caplog.text


@pytest.mark.asyncio
async def test_runtime_returns_failure_when_shutdown_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = FakeEngine(stop_error=RuntimeError("close failed"))
    shutdown_requested = asyncio.Event()
    shutdown_requested.set()

    with caplog.at_level(logging.ERROR):
        result = await run(lambda: engine, shutdown_requested=shutdown_requested)

    assert result == EXIT_FAILURE
    assert engine.stopped
    assert "Engine shutdown failed" in caplog.text


def test_signal_handler_requests_shutdown_once(caplog: pytest.LogCaptureFixture) -> None:
    shutdown_requested = asyncio.Event()

    _request_shutdown(shutdown_requested, signal.SIGTERM)
    _request_shutdown(shutdown_requested, signal.SIGTERM)

    assert shutdown_requested.is_set()
    assert "graceful shutdown is already in progress" in caplog.text
