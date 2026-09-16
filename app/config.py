"""Application configuration, sourced entirely from environment variables."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Every value has a safe local default so the app
    boots with no environment set, but nothing here is hardcoded at the call site.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "sensor-ingestion-service"
    version: str = "0.1.0"

    # DO App Platform injects PORT; 8080 is its default.
    port: int = 8080
    log_level: str = "INFO"

    # The single line that switches SQLite -> Postgres.
    database_url: str = "sqlite:///./.data/readings.db"

    # Ingest cap.
    max_batch_size: int = 1000

    # Comma-separated browser origins allowed to call the API. Defaults to "*",
    # which is safe here specifically because there is no auth and no cookies:
    # CORS is a browser-side control, and every endpoint is already reachable by
    # any non-browser client. It would NOT be safe alongside credentialed auth.
    cors_allow_origins: str = "*"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allow_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """Cached so the environment is read once per process, not per request."""
    return Settings()
