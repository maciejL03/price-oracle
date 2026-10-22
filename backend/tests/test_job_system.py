import asyncio
import logging
from collections.abc import Awaitable, Callable

import pytest

from engine.errors import JobLifecycleError, JobShutdownTimeout, SchedulerError
from engine.jobs import EngineJob, JobHandler, JobScheduler, SubmissionResult


class FunctionJob(EngineJob):
    def __init__(self, key: str, work: Callable[[], Awaitable[None] | None]) -> None:
        self._key = key
        self._work = work

    @property
    def key(self) -> str:
        return self._key

    async def execute(self) -> None:
        result = self._work()
        if result is not None:
            await result


async def do_nothing() -> None:
    pass


async def wait_until(predicate: Callable[[], bool], timeout: float = 1.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.001)


def test_engine_job_enforces_abstract_contract() -> None:
    with pytest.raises(TypeError):
        EngineJob()


@pytest.mark.asyncio
async def test_concrete_job_executes() -> None:
    executed = False

    async def work() -> None:
        nonlocal executed
        executed = True

    job = FunctionJob("concrete", work)
    await job.execute()

    assert job.key == "concrete"
    assert executed


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"worker_count": 0}, "worker_count"),
        ({"queue_capacity": 0}, "queue_capacity"),
        ({"shutdown_timeout": 0}, "shutdown_timeout"),
    ],
)
def test_handler_rejects_invalid_configuration(kwargs: dict[str, int], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        JobHandler(**kwargs)


@pytest.mark.asyncio
async def test_handler_executes_single_job() -> None:
    completed = asyncio.Event()
    handler = JobHandler()
    await handler.start()
    try:
        assert handler.submit(FunctionJob("single", completed.set)) is SubmissionResult.ACCEPTED
        await asyncio.wait_for(completed.wait(), 1)
        await wait_until(lambda: not handler.is_active("single"))
    finally:
        await handler.stop()


@pytest.mark.asyncio
async def test_handler_executes_multiple_jobs_concurrently() -> None:
    release = asyncio.Event()
    all_started = asyncio.Event()
    started = 0

    async def work() -> None:
        nonlocal started
        started += 1
        if started == 3:
            all_started.set()
        await release.wait()

    handler = JobHandler(worker_count=3)
    await handler.start()
    try:
        for index in range(3):
            assert handler.submit(FunctionJob(str(index), work)) is SubmissionResult.ACCEPTED
        await asyncio.wait_for(all_started.wait(), 1)
    finally:
        release.set()
        await handler.stop()


@pytest.mark.asyncio
async def test_handler_respects_worker_limit() -> None:
    active = 0
    peak = 0

    async def work() -> None:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1

    handler = JobHandler(worker_count=2)
    await handler.start()
    try:
        for index in range(8):
            assert handler.submit(FunctionJob(str(index), work)) is SubmissionResult.ACCEPTED
        await wait_until(lambda: not any(handler.is_active(str(index)) for index in range(8)))
        assert peak == 2
    finally:
        await handler.stop()


@pytest.mark.asyncio
async def test_handler_reports_queue_capacity() -> None:
    release = asyncio.Event()
    started = asyncio.Event()

    async def blocked() -> None:
        started.set()
        await release.wait()

    handler = JobHandler(worker_count=1, queue_capacity=1)
    await handler.start()
    try:
        assert handler.submit(FunctionJob("running", blocked)) is SubmissionResult.ACCEPTED
        await asyncio.wait_for(started.wait(), 1)
        assert handler.submit(FunctionJob("queued", blocked)) is SubmissionResult.ACCEPTED
        assert handler.submit(FunctionJob("rejected", blocked)) is SubmissionResult.QUEUE_FULL
        assert not handler.is_active("rejected")
    finally:
        release.set()
        await handler.stop()


@pytest.mark.asyncio
async def test_handler_rejects_duplicate_queued_or_running_jobs() -> None:
    first_release = asyncio.Event()
    duplicate_release = asyncio.Event()
    first_started = asyncio.Event()
    duplicate_started = asyncio.Event()

    async def first() -> None:
        first_started.set()
        await first_release.wait()

    async def duplicate() -> None:
        duplicate_started.set()
        await duplicate_release.wait()

    handler = JobHandler(worker_count=1)
    await handler.start()
    try:
        assert handler.submit(FunctionJob("first", first)) is SubmissionResult.ACCEPTED
        await asyncio.wait_for(first_started.wait(), 1)
        assert handler.submit(FunctionJob("duplicate", duplicate)) is SubmissionResult.ACCEPTED
        assert handler.submit(FunctionJob("duplicate", duplicate)) is SubmissionResult.DUPLICATE
        first_release.set()
        await asyncio.wait_for(duplicate_started.wait(), 1)
        assert handler.submit(FunctionJob("duplicate", duplicate)) is SubmissionResult.DUPLICATE
    finally:
        first_release.set()
        duplicate_release.set()
        await handler.stop()


@pytest.mark.asyncio
async def test_handler_rejects_submissions_before_start_and_during_shutdown() -> None:
    release = asyncio.Event()
    started = asyncio.Event()

    async def blocked() -> None:
        started.set()
        await release.wait()

    handler = JobHandler()
    with pytest.raises(JobLifecycleError):
        handler.submit(FunctionJob("early", do_nothing))

    await handler.start()
    assert handler.submit(FunctionJob("running", blocked)) is SubmissionResult.ACCEPTED
    await asyncio.wait_for(started.wait(), 1)
    stop_task = asyncio.create_task(handler.stop())
    await wait_until(lambda: not handler.running)
    with pytest.raises(JobLifecycleError):
        handler.submit(FunctionJob("late", do_nothing))
    release.set()
    await stop_task


@pytest.mark.asyncio
async def test_job_failure_does_not_terminate_worker(caplog: pytest.LogCaptureFixture) -> None:
    completed = asyncio.Event()

    async def fail() -> None:
        raise ValueError("simulated failure")

    handler = JobHandler(worker_count=1)
    await handler.start()
    try:
        with caplog.at_level(logging.ERROR, logger="engine.jobs.handler"):
            assert handler.submit(FunctionJob("failure", fail)) is SubmissionResult.ACCEPTED
            result = handler.submit(FunctionJob("success", completed.set))
            assert result is SubmissionResult.ACCEPTED
            await asyncio.wait_for(completed.wait(), 1)
        assert "Job failed: failure" in caplog.text
        assert "simulated failure" in caplog.text
    finally:
        await handler.stop()


@pytest.mark.asyncio
async def test_self_cancelled_job_does_not_terminate_worker() -> None:
    completed = asyncio.Event()

    async def cancel() -> None:
        raise asyncio.CancelledError

    handler = JobHandler(worker_count=1)
    await handler.start()
    try:
        assert handler.submit(FunctionJob("cancel", cancel)) is SubmissionResult.ACCEPTED
        assert handler.submit(FunctionJob("next", completed.set)) is SubmissionResult.ACCEPTED
        await asyncio.wait_for(completed.wait(), 1)
    finally:
        await handler.stop()


@pytest.mark.asyncio
async def test_graceful_shutdown_finishes_running_and_queued_jobs() -> None:
    release = asyncio.Event()
    started = asyncio.Event()
    completed: list[str] = []

    async def first() -> None:
        started.set()
        await release.wait()
        completed.append("first")

    async def second() -> None:
        completed.append("second")

    handler = JobHandler(worker_count=1)
    await handler.start()
    assert handler.submit(FunctionJob("first", first)) is SubmissionResult.ACCEPTED
    assert handler.submit(FunctionJob("second", second)) is SubmissionResult.ACCEPTED
    await asyncio.wait_for(started.wait(), 1)

    stop_task = asyncio.create_task(handler.stop())
    await wait_until(lambda: not handler.running)
    release.set()
    await stop_task

    assert completed == ["first", "second"]
    assert not handler.is_active("first")
    assert not handler.is_active("second")


@pytest.mark.asyncio
async def test_shutdown_timeout_cleans_state_and_raises() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def blocked() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    handler = JobHandler(worker_count=1, queue_capacity=1, shutdown_timeout=0.01)
    await handler.start()
    assert handler.submit(FunctionJob("running", blocked)) is SubmissionResult.ACCEPTED
    await asyncio.wait_for(started.wait(), 1)
    assert handler.submit(FunctionJob("queued", do_nothing)) is SubmissionResult.ACCEPTED

    with pytest.raises(JobShutdownTimeout):
        await handler.stop()

    assert cancelled.is_set()
    assert not handler.running
    assert not handler.is_active("running")
    assert not handler.is_active("queued")

    await handler.start()
    await handler.stop()


@pytest.mark.asyncio
async def test_cancelling_shutdown_cleans_up_workers() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def blocked() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    handler = JobHandler()
    await handler.start()
    assert handler.submit(FunctionJob("blocked", blocked)) is SubmissionResult.ACCEPTED
    await asyncio.wait_for(started.wait(), 1)

    stop_task = asyncio.create_task(handler.stop())
    await wait_until(lambda: not handler.running)
    stop_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stop_task

    assert cancelled.is_set()
    assert not handler.is_active("blocked")
    await handler.start()
    await handler.stop()


def test_scheduler_registration_validation() -> None:
    scheduler = JobScheduler(JobHandler())

    with pytest.raises(ValueError, match="key"):
        scheduler.register("", lambda: FunctionJob("job", do_nothing), 1)
    with pytest.raises(ValueError, match="interval"):
        scheduler.register("job", lambda: FunctionJob("job", do_nothing), 0)

    scheduler.register("job", lambda: FunctionJob("job", do_nothing), 1)
    with pytest.raises(SchedulerError, match="already registered"):
        scheduler.register("job", lambda: FunctionJob("job", do_nothing), 1)
    assert scheduler.unregister("job")
    assert not scheduler.unregister("job")


@pytest.mark.asyncio
async def test_scheduler_immediate_and_delayed_first_execution() -> None:
    immediate = asyncio.Event()
    delayed = asyncio.Event()
    handler = JobHandler(worker_count=2)
    scheduler = JobScheduler(handler, poll_seconds=0.002)
    await handler.start()
    scheduler.register(
        "immediate", lambda: FunctionJob("immediate", immediate.set), 1, run_immediately=True
    )
    scheduler.register("delayed", lambda: FunctionJob("delayed", delayed.set), 0.04)
    await scheduler.start()
    try:
        await asyncio.wait_for(immediate.wait(), 0.1)
        await asyncio.sleep(0.015)
        assert not delayed.is_set()
        await asyncio.wait_for(delayed.wait(), 0.1)
    finally:
        await scheduler.stop()
        await handler.stop()


@pytest.mark.asyncio
async def test_scheduler_honours_different_intervals() -> None:
    counts = {"fast": 0, "slow": 0}

    def factory(key: str) -> FunctionJob:
        async def count() -> None:
            counts[key] += 1

        return FunctionJob(key, count)

    handler = JobHandler(worker_count=2)
    scheduler = JobScheduler(handler, poll_seconds=0.002)
    await handler.start()
    scheduler.register("fast", lambda: factory("fast"), 0.015, run_immediately=True)
    scheduler.register("slow", lambda: factory("slow"), 0.05, run_immediately=True)
    await scheduler.start()
    try:
        await asyncio.sleep(0.13)
    finally:
        await scheduler.stop()
        await handler.stop()

    assert counts["fast"] >= 4
    assert counts["slow"] >= 2
    assert counts["fast"] > counts["slow"]


@pytest.mark.asyncio
async def test_recurring_job_does_not_overlap_or_accumulate_missed_runs() -> None:
    active = 0
    peak = 0
    executions = 0

    async def slow_work() -> None:
        nonlocal active, peak, executions
        active += 1
        peak = max(peak, active)
        executions += 1
        await asyncio.sleep(0.03)
        active -= 1

    handler = JobHandler(worker_count=3)
    scheduler = JobScheduler(handler, poll_seconds=0.002)
    await handler.start()
    scheduler.register(
        "slow", lambda: FunctionJob("slow", slow_work), 0.01, run_immediately=True
    )
    await scheduler.start()
    try:
        await asyncio.sleep(0.12)
    finally:
        await scheduler.stop()
        await handler.stop()

    assert peak == 1
    assert 2 <= executions <= 4


@pytest.mark.asyncio
async def test_recurring_interval_is_measured_after_completion() -> None:
    starts: list[float] = []
    finishes: list[float] = []

    async def work() -> None:
        loop = asyncio.get_running_loop()
        starts.append(loop.time())
        await asyncio.sleep(0.012)
        finishes.append(loop.time())

    handler = JobHandler()
    scheduler = JobScheduler(handler, poll_seconds=0.001)
    await handler.start()
    scheduler.register("timed", lambda: FunctionJob("timed", work), 0.018, run_immediately=True)
    await scheduler.start()
    try:
        await wait_until(lambda: len(finishes) >= 3)
    finally:
        await scheduler.stop()
        await handler.stop()

    gaps = [start - finish for start, finish in zip(starts[1:], finishes)]
    assert gaps
    assert all(gap >= 0.016 for gap in gaps)


@pytest.mark.asyncio
async def test_scheduler_retries_same_job_when_queue_is_full() -> None:
    release = asyncio.Event()
    blocker_started = asyncio.Event()
    recurring_ran = asyncio.Event()
    factory_calls = 0

    async def blocked() -> None:
        blocker_started.set()
        await release.wait()

    def recurring_factory() -> FunctionJob:
        nonlocal factory_calls
        factory_calls += 1
        return FunctionJob("recurring", recurring_ran.set)

    handler = JobHandler(worker_count=1, queue_capacity=1)
    scheduler = JobScheduler(handler, poll_seconds=0.002)
    await handler.start()
    assert handler.submit(FunctionJob("running", blocked)) is SubmissionResult.ACCEPTED
    await asyncio.wait_for(blocker_started.wait(), 1)
    assert handler.submit(FunctionJob("queued", blocked)) is SubmissionResult.ACCEPTED
    scheduler.register("recurring", recurring_factory, 1, run_immediately=True)
    await scheduler.start()
    try:
        await asyncio.sleep(0.015)
        assert factory_calls == 1
        assert not recurring_ran.is_set()
        release.set()
        await asyncio.wait_for(recurring_ran.wait(), 1)
        assert factory_calls == 1
    finally:
        release.set()
        await scheduler.stop()
        await handler.stop()


@pytest.mark.asyncio
async def test_scheduler_supports_dynamic_registration_and_unregistration() -> None:
    executions = 0

    async def work() -> None:
        nonlocal executions
        executions += 1

    handler = JobHandler()
    scheduler = JobScheduler(handler, poll_seconds=0.002)
    await handler.start()
    await scheduler.start()
    try:
        scheduler.register(
            "dynamic", lambda: FunctionJob("dynamic", work), 0.01, run_immediately=True
        )
        await wait_until(lambda: executions >= 2)
        assert scheduler.unregister("dynamic")
        count_after_unregister = executions
        await asyncio.sleep(0.035)
        assert executions == count_after_unregister
    finally:
        await scheduler.stop()
        await handler.stop()


@pytest.mark.asyncio
async def test_scheduler_defers_to_manual_job_with_same_key() -> None:
    release = asyncio.Event()
    manual_started = asyncio.Event()
    recurring_runs = 0

    async def manual() -> None:
        manual_started.set()
        await release.wait()

    async def recurring() -> None:
        nonlocal recurring_runs
        recurring_runs += 1

    handler = JobHandler(worker_count=2)
    scheduler = JobScheduler(handler, poll_seconds=0.002)
    await handler.start()
    assert handler.submit(FunctionJob("shared", manual)) is SubmissionResult.ACCEPTED
    await asyncio.wait_for(manual_started.wait(), 1)
    scheduler.register(
        "shared", lambda: FunctionJob("shared", recurring), 0.025, run_immediately=True
    )
    await scheduler.start()
    try:
        await asyncio.sleep(0.015)
        assert recurring_runs == 0
        release.set()
        await asyncio.sleep(0.012)
        assert recurring_runs == 0
        await wait_until(lambda: recurring_runs == 1)
    finally:
        release.set()
        await scheduler.stop()
        await handler.stop()


@pytest.mark.asyncio
async def test_scheduler_startup_shutdown_and_restart() -> None:
    handler = JobHandler()
    scheduler = JobScheduler(handler)

    with pytest.raises(SchedulerError, match="JobHandler"):
        await scheduler.start()

    await handler.start()
    await scheduler.start()
    assert scheduler.running
    with pytest.raises(SchedulerError, match="already running"):
        await scheduler.start()
    await scheduler.stop()
    assert not scheduler.running
    await scheduler.stop()
    await scheduler.start()
    await scheduler.stop()
    await handler.stop()


@pytest.mark.asyncio
async def test_integration_one_off_and_multiple_recurring_jobs_survive_failure() -> None:
    completed: set[str] = set()

    async def record(name: str) -> None:
        completed.add(name)

    async def fail() -> None:
        raise RuntimeError("expected integration failure")

    handler = JobHandler(worker_count=3)
    scheduler = JobScheduler(handler, poll_seconds=0.002)
    await handler.start()
    scheduler.register(
        "recurring-a",
        lambda: FunctionJob("recurring-a", lambda: record("recurring-a")),
        1,
        run_immediately=True,
    )
    scheduler.register(
        "recurring-b",
        lambda: FunctionJob("recurring-b", lambda: record("recurring-b")),
        1,
        run_immediately=True,
    )
    await scheduler.start()
    assert handler.submit(FunctionJob("failure", fail)) is SubmissionResult.ACCEPTED
    assert (
        handler.submit(FunctionJob("one-off", lambda: record("one-off")))
        is SubmissionResult.ACCEPTED
    )
    try:
        await wait_until(
            lambda: {"recurring-a", "recurring-b", "one-off"}.issubset(completed)
        )
    finally:
        await scheduler.stop()
        await handler.stop()

    assert completed == {"recurring-a", "recurring-b", "one-off"}
