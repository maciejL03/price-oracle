from pathlib import Path
from typing import Annotated, Literal

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
    cache_dir: Path = Path(".cache")
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
    catalog_url: str = (
        "https://raw.githubusercontent.com/ByMykel/CSGO-API/main/public/api/en/all.json"
    )
    catalog_cache_ttl_seconds: Annotated[float, Field(gt=0)] = 86_400.0
    steam_enabled: bool = True
    steam_country: str = "GB"
    steam_currency_code: Annotated[int, Field(gt=0)] = 2
    steam_currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")] = "GBP"
    steam_app_id: Annotated[int, Field(gt=0)] = 730
    steam_cache_ttl_seconds: Annotated[float, Field(gt=0)] = 60.0
    skinport_enabled: bool = True
    skinport_currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")] = "GBP"
    skinport_base_url: str = "https://api.skinport.com/v1"
    skinport_cache_ttl_seconds: Annotated[float, Field(ge=300)] = 300.0
    http_timeout_seconds: Annotated[float, Field(gt=0)] = 30.0
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    operating_mode: Literal["demo", "public"] = "demo"
    demo_seed_enabled: bool = True
