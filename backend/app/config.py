from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    steam_country: str = "GB"
    steam_currency: int = 2  # GBP
    steam_app_id: int = 730  # CS2

    skinport_currency: str = "GBP"
    skinport_base_url: str = "https://api.skinport.com/v1"

    bymykel_items_url: str = (
        "https://raw.githubusercontent.com/ByMykel/CSGO-API/main/public/api/en/all.json"
    )

    cache_dir: str = ".cache"
    items_cache_ttl_seconds: int = 86_400  # 24 hours
    skinport_cache_ttl_seconds: int = 300  # 5 minutes

    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    http_timeout_seconds: float = 30.0
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )


settings = Settings()
