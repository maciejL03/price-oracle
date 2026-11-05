from pathlib import Path

import pytest
from pydantic import ValidationError

from engine.config import EngineSettings


def write_setting(path: Path, name: str, value: str) -> None:
    path.joinpath(name).write_text(value, encoding="utf-8")


def test_environment_variables_are_loaded_and_converted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CS2_WORKER_COUNT", "3")
    monkeypatch.setenv("CS2_JOB_QUEUE_CAPACITY", "40")
    monkeypatch.setenv("CS2_JOB_SHUTDOWN_TIMEOUT_SECONDS", "2.5")
    monkeypatch.setenv("CS2_DATABASE_PATH", "state/test.sqlite3")
    monkeypatch.setenv("CS2_DATABASE_BUSY_TIMEOUT_SECONDS", "1.25")

    settings = EngineSettings(_env_file=None, _secrets_dir=None)

    assert settings.worker_count == 3
    assert settings.job_queue_capacity == 40
    assert settings.job_shutdown_timeout_seconds == 2.5
    assert settings.database_path == Path("state/test.sqlite3")
    assert settings.database_busy_timeout_seconds == 1.25


def test_unprefixed_environment_variables_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKER_COUNT", "9")

    assert EngineSettings(_env_file=None, _secrets_dir=None).worker_count == 5


def test_temporary_dotenv_file_is_loaded(tmp_path: Path) -> None:
    env_file = tmp_path / "engine.env"
    env_file.write_text(
        "CS2_WORKER_COUNT=4\n"
        "CS2_JOB_QUEUE_CAPACITY=60\n"
        "CS2_JOB_SHUTDOWN_TIMEOUT_SECONDS=3.5\n",
        encoding="utf-8",
    )

    settings = EngineSettings(_env_file=env_file, _secrets_dir=None)

    assert settings.worker_count == 4
    assert settings.job_queue_capacity == 60
    assert settings.job_shutdown_timeout_seconds == 3.5


def test_unrelated_dotenv_entries_are_ignored(tmp_path: Path) -> None:
    env_file = tmp_path / "shared.env"
    env_file.write_text("UNRELATED_APPLICATION_SETTING=value\n", encoding="utf-8")

    settings = EngineSettings(_env_file=env_file, _secrets_dir=None)

    assert settings.worker_count == 5


def test_explicit_values_override_environment_and_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / "engine.env"
    env_file.write_text("CS2_WORKER_COUNT=2\n", encoding="utf-8")
    monkeypatch.setenv("CS2_WORKER_COUNT", "3")

    settings = EngineSettings(worker_count=4, _env_file=env_file, _secrets_dir=None)

    assert settings.worker_count == 4


def test_environment_overrides_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / "engine.env"
    env_file.write_text("CS2_WORKER_COUNT=2\n", encoding="utf-8")
    monkeypatch.setenv("CS2_WORKER_COUNT", "3")

    settings = EngineSettings(_env_file=env_file, _secrets_dir=None)

    assert settings.worker_count == 3


def test_dotenv_overrides_docker_secrets(tmp_path: Path) -> None:
    env_file = tmp_path / "engine.env"
    env_file.write_text("CS2_WORKER_COUNT=3\n", encoding="utf-8")
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    write_setting(secrets_dir, "CS2_WORKER_COUNT", "2")

    settings = EngineSettings(_env_file=env_file, _secrets_dir=secrets_dir)

    assert settings.worker_count == 3


def test_docker_secret_overrides_default(tmp_path: Path) -> None:
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    write_setting(secrets_dir, "CS2_WORKER_COUNT", "2")

    settings = EngineSettings(_env_file=None, _secrets_dir=secrets_dir)

    assert settings.worker_count == 2


def test_docker_secret_filenames_include_environment_prefix(tmp_path: Path) -> None:
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    write_setting(secrets_dir, "WORKER_COUNT", "8")
    write_setting(secrets_dir, "CS2_WORKER_COUNT", "6")

    settings = EngineSettings(_env_file=None, _secrets_dir=secrets_dir)

    assert settings.worker_count == 6


def test_unprefixed_docker_secret_filename_is_ignored(tmp_path: Path) -> None:
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    write_setting(secrets_dir, "WORKER_COUNT", "8")

    settings = EngineSettings(_env_file=None, _secrets_dir=secrets_dir)

    assert settings.worker_count == 5


def test_missing_optional_secrets_directory_uses_defaults(tmp_path: Path) -> None:
    missing_dir = tmp_path / "not-mounted"

    with pytest.warns(UserWarning, match="does not exist"):
        settings = EngineSettings(_env_file=None, _secrets_dir=missing_dir)

    assert settings.worker_count == 5


def test_invalid_environment_value_is_not_silently_replaced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CS2_WORKER_COUNT", "0")

    with pytest.raises(ValidationError) as error:
        EngineSettings(_env_file=None, _secrets_dir=None)

    assert error.value.errors()[0]["loc"] == ("worker_count",)


def test_invalid_docker_secret_is_not_silently_replaced(tmp_path: Path) -> None:
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    write_setting(secrets_dir, "CS2_JOB_QUEUE_CAPACITY", "not-a-number")

    with pytest.raises(ValidationError) as error:
        EngineSettings(_env_file=None, _secrets_dir=secrets_dir)

    assert error.value.errors()[0]["loc"] == ("job_queue_capacity",)


def test_monkeypatched_environment_does_not_escape_its_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with monkeypatch.context() as context:
        context.setenv("CS2_WORKER_COUNT", "9")
        assert EngineSettings(_env_file=None, _secrets_dir=None).worker_count == 9

    assert EngineSettings(_env_file=None, _secrets_dir=None).worker_count == 5
