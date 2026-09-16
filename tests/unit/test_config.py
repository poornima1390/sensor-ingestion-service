"""Configuration comes from the environment, with safe local defaults."""

from app.config import Settings


def test_defaults_allow_boot_with_no_environment() -> None:
    settings = Settings(_env_file=None)
    assert settings.port == 8080
    assert settings.log_level == "INFO"
    assert settings.database_url.startswith("sqlite://")
    assert settings.max_batch_size == 1000


def test_environment_overrides_defaults(monkeypatch) -> None:
    monkeypatch.setenv("PORT", "9000")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h:5432/db")

    settings = Settings(_env_file=None)

    assert settings.port == 9000
    assert settings.log_level == "DEBUG"
    assert settings.database_url == "postgresql://u:p@h:5432/db"
