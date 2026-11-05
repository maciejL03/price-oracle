import pytest

_ENGINE_ENVIRONMENT_VARIABLES = (
    "CS2_OPERATING_MODE",
    "CS2_DEMO_SEED_ENABLED",
    "CS2_WORKER_COUNT",
    "CS2_JOB_QUEUE_CAPACITY",
    "CS2_JOB_SHUTDOWN_TIMEOUT_SECONDS",
    "CS2_DATABASE_PATH",
    "CS2_DATABASE_BUSY_TIMEOUT_SECONDS",
    "CS2_SCHEDULER_POLL_SECONDS",
    "CS2_QUOTE_MAX_AGE_SECONDS",
    "CS2_CSFLOAT_ENABLED",
    "CS2_CSFLOAT_CURRENCY",
    "CS2_CSFLOAT_API_KEY",
    "CS2_CSFLOAT_TIMEOUT_SECONDS",
    "CS2_STEAM_ENABLED",
    "CS2_STEAM_CACHE_TTL_SECONDS",
    "CS2_SKINPORT_ENABLED",
    "CS2_DISCORD_WEBHOOK_URL",
)


@pytest.fixture(autouse=True)
def isolate_engine_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent developer and CI engine settings from influencing tests."""
    for variable in _ENGINE_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(variable, raising=False)
