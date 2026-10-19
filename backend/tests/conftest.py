import pytest

_ENGINE_ENVIRONMENT_VARIABLES = (
    "CS2_WORKER_COUNT",
    "CS2_JOB_QUEUE_CAPACITY",
    "CS2_JOB_SHUTDOWN_TIMEOUT_SECONDS",
    "CS2_DATABASE_PATH",
    "CS2_DATABASE_BUSY_TIMEOUT_SECONDS",
)


@pytest.fixture(autouse=True)
def isolate_engine_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent developer and CI engine settings from influencing tests."""
    for variable in _ENGINE_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(variable, raising=False)
