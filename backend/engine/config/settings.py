from pathlib import Path
from typing import Annotated

from pydantic import Field, SecretStr, StringConstraints
from pydantic_settings import BaseSettings, SettingsConfigDict

_DOCKER_SECRETS_DIR = Path("/run/secrets")


class EngineSettings(BaseSettings):
    """Load and validate startup configuration for engine infrastructure."""

    model_config = SettingsConfigDict(
        env_prefix="CS2_",
        env_file=".env",
        env_file_encoding="utf-8",
        secrets_dir=_DOCKER_SECRETS_DIR if _DOCKER_SECRETS_DIR.is_dir() else None,
        extra="ignore",
    )

    worker_count: Annotated[int, Field(gt=0)] = 5
    job_queue_capacity: Annotated[int, Field(gt=0)] = 100
    job_shutdown_timeout_seconds: Annotated[float, Field(gt=0)] = 10.0
    database_path: Path = Path("data/engine.sqlite3")
    database_busy_timeout_seconds: Annotated[float, Field(gt=0)] = 5.0
    scheduler_poll_seconds: Annotated[float, Field(gt=0)] = 0.1
    quote_max_age_seconds: Annotated[float, Field(gt=0)] = 300.0
    csfloat_enabled: bool = True
    csfloat_currency: Annotated[
        str, StringConstraints(pattern=r"^[A-Z]{3}$")
    ] = "GBP"
    csfloat_api_key: SecretStr | None = None
    csfloat_timeout_seconds: Annotated[float, Field(gt=0)] = 10.0
    discord_webhook_url: SecretStr | None = None
