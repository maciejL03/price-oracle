import asyncio
from collections.abc import Callable
from pathlib import Path

import pytest

from engine.bootstrap import create_market_engine
from engine.config import EngineSettings
from engine.market_engine import EngineState
from engine.runtime import run_market_engine


def _settings(database_path: Path) -> EngineSettings:
    return EngineSettings(
        database_path=database_path,
        csfloat_enabled=False,
        worker_count=1,
        scheduler_poll_seconds=0.001,
        _env_file=None,
        _secrets_dir=None,
    )


async def _wait_until(predicate: Callable[[], bool], timeout: float = 1.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_runtime_starts_engine_and_stops_when_requested(tmp_path: Path) -> None:
    engine = create_market_engine(_settings(tmp_path / "engine.sqlite3"), marketplaces=[])
    shutdown_event = asyncio.Event()

    process = asyncio.create_task(
        run_market_engine(
            engine,
            shutdown_event=shutdown_event,
            handle_signals=False,
        )
    )
    try:
        await _wait_until(lambda: engine.state is EngineState.RUNNING)
    finally:
        shutdown_event.set()
        await asyncio.wait_for(process, 1)

    assert engine.state is EngineState.STOPPED
    assert not engine.job_handler.running
    assert not engine.scheduler.running


@pytest.mark.asyncio
async def test_runtime_cancellation_still_closes_engine(tmp_path: Path) -> None:
    engine = create_market_engine(_settings(tmp_path / "engine.sqlite3"), marketplaces=[])
    process = asyncio.create_task(
        run_market_engine(engine, handle_signals=False),
        name="test-engine-process",
    )
    try:
        await _wait_until(lambda: engine.state is EngineState.RUNNING)
    finally:
        process.cancel()
        with pytest.raises(asyncio.CancelledError):
            await process

    assert engine.state is EngineState.STOPPED
    assert not engine.job_handler.running
    assert not engine.scheduler.running


def test_runtime_rejects_invalid_dependencies() -> None:
    with pytest.raises(TypeError, match="MarketEngine"):
        asyncio.run(run_market_engine(object(), handle_signals=False))  # type: ignore[arg-type]
