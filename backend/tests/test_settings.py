from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from engine.config import EngineSettings
from engine.jobs import JobHandler


def test_default_settings_are_valid_and_typed() -> None:
    settings = EngineSettings(_env_file=None, _secrets_dir=None)

    assert settings.worker_count == 5
    assert isinstance(settings.worker_count, int)
    assert settings.job_queue_capacity == 100
    assert isinstance(settings.job_queue_capacity, int)
    assert settings.job_shutdown_timeout_seconds == 10.0
    assert isinstance(settings.job_shutdown_timeout_seconds, float)
    assert settings.database_path == Path("data/engine.sqlite3")
    assert isinstance(settings.database_path, Path)
    assert settings.database_busy_timeout_seconds == 5.0
    assert isinstance(settings.database_busy_timeout_seconds, float)
    assert settings.scheduler_poll_seconds == 0.1
    assert settings.quote_max_age_seconds == 300.0
    assert settings.csfloat_enabled
    assert settings.csfloat_currency == "GBP"
    assert settings.csfloat_api_key is None
    assert settings.csfloat_timeout_seconds == 10.0
    assert settings.discord_webhook_url is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("worker_count", 0),
        ("worker_count", -1),
        ("job_queue_capacity", 0),
        ("job_queue_capacity", -1),
        ("job_shutdown_timeout_seconds", 0),
        ("job_shutdown_timeout_seconds", -0.1),
        ("database_busy_timeout_seconds", 0),
        ("database_busy_timeout_seconds", -0.1),
        ("scheduler_poll_seconds", 0),
        ("quote_max_age_seconds", 0),
        ("csfloat_timeout_seconds", 0),
    ],
)
def test_non_positive_job_settings_are_rejected(field: str, value: int | float) -> None:
    with pytest.raises(ValidationError) as error:
        EngineSettings(_env_file=None, _secrets_dir=None, **{field: value})

    assert error.value.errors()[0]["loc"] == (field,)
    assert error.value.errors()[0]["type"] == "greater_than"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("worker_count", "many"),
        ("job_queue_capacity", "unbounded"),
        ("job_shutdown_timeout_seconds", "eventually"),
        ("database_busy_timeout_seconds", "eventually"),
        ("scheduler_poll_seconds", "eventually"),
        ("quote_max_age_seconds", "stale"),
        ("csfloat_timeout_seconds", "eventually"),
    ],
)
def test_invalid_types_are_rejected(field: str, value: str) -> None:
    with pytest.raises(ValidationError) as error:
        EngineSettings(_env_file=None, _secrets_dir=None, **{field: value})

    assert error.value.errors()[0]["loc"] == (field,)


def test_settings_convert_supported_string_inputs() -> None:
    settings = EngineSettings(
        _env_file=None,
        _secrets_dir=None,
        worker_count="3",
        job_queue_capacity="20",
        job_shutdown_timeout_seconds="2.5",
        database_path="var/lib/quotes.sqlite3",
        database_busy_timeout_seconds="1.25",
    )

    assert settings.worker_count == 3
    assert settings.job_queue_capacity == 20
    assert settings.job_shutdown_timeout_seconds == 2.5
    assert settings.database_path == Path("var/lib/quotes.sqlite3")
    assert settings.database_busy_timeout_seconds == 1.25


def test_settings_dump_contains_only_validated_values() -> None:
    settings = EngineSettings(
        _env_file=None,
        _secrets_dir=None,
        worker_count=2,
        job_queue_capacity=12,
        job_shutdown_timeout_seconds=1.5,
        database_path=Path("state/quotes.sqlite3"),
        database_busy_timeout_seconds=0.75,
    )

    assert settings.model_dump() == {
        "worker_count": 2,
        "job_queue_capacity": 12,
        "job_shutdown_timeout_seconds": 1.5,
        "database_path": Path("state/quotes.sqlite3"),
        "database_busy_timeout_seconds": 0.75,
        "scheduler_poll_seconds": 0.1,
        "quote_max_age_seconds": 300.0,
        "csfloat_enabled": True,
        "csfloat_currency": "GBP",
        "csfloat_api_key": None,
        "csfloat_timeout_seconds": 10.0,
        "discord_webhook_url": None,
    }


def test_discord_webhook_is_loaded_as_a_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "notification-secret-token"
    webhook_url = f"https://discord.com/api/webhooks/123/{token}"
    monkeypatch.setenv("CS2_DISCORD_WEBHOOK_URL", webhook_url)

    settings = EngineSettings(_env_file=None, _secrets_dir=None)

    assert isinstance(settings.discord_webhook_url, SecretStr)
    assert settings.discord_webhook_url.get_secret_value() == webhook_url
    assert token not in repr(settings)


def test_csfloat_api_key_is_loaded_as_a_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_key = "csfloat-secret-key"
    monkeypatch.setenv("CS2_CSFLOAT_API_KEY", api_key)

    settings = EngineSettings(_env_file=None, _secrets_dir=None)

    assert isinstance(settings.csfloat_api_key, SecretStr)
    assert settings.csfloat_api_key.get_secret_value() == api_key
    assert api_key not in repr(settings)


@pytest.mark.parametrize("currency", ["gbp", "GB", "GBPP"])
def test_invalid_csfloat_currency_is_rejected(currency: str) -> None:
    with pytest.raises(ValidationError) as error:
        EngineSettings(
            _env_file=None,
            _secrets_dir=None,
            csfloat_currency=currency,
        )

    assert error.value.errors()[0]["loc"] == ("csfloat_currency",)


@pytest.mark.asyncio
async def test_settings_can_be_injected_into_job_handler() -> None:
    settings = EngineSettings(
        _env_file=None,
        _secrets_dir=None,
        worker_count=2,
        job_queue_capacity=10,
        job_shutdown_timeout_seconds=1.0,
    )
    handler = JobHandler(
        worker_count=settings.worker_count,
        queue_capacity=settings.job_queue_capacity,
        shutdown_timeout=settings.job_shutdown_timeout_seconds,
    )

    await handler.start()
    assert handler.running
    await handler.stop()
    assert not handler.running


def test_invalid_settings_prevent_job_handler_construction() -> None:
    handler: JobHandler | None = None

    with pytest.raises(ValidationError):
        settings = EngineSettings(_env_file=None, _secrets_dir=None, worker_count=0)
        handler = JobHandler(worker_count=settings.worker_count)

    assert handler is None


def test_default_dotenv_path_is_relative_to_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text("CS2_WORKER_COUNT=7\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert EngineSettings(_secrets_dir=None).worker_count == 7
