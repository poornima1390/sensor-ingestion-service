"""Shared fixtures.

Tests run against in-memory SQLite by default for speed, and against whatever
DATABASE_URL points at when CI runs the same suite on Postgres.
"""

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.config import Settings
from app.main import create_app
from app.storage.database import get_engine
from app.storage.models import Reading


@pytest.fixture(scope="session")
def settings() -> Settings:
    # INFO, not WARNING, on purpose. Logging bugs (an `extra` key that collides
    # with a reserved LogRecord attribute, for instance) only fire when the level
    # is actually enabled, so a suite that runs quiet cannot see them.
    return Settings(
        database_url=os.getenv("TEST_DATABASE_URL", "sqlite://"),
        log_level="INFO",
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    # The context manager is required: it triggers the lifespan, which is what
    # initialises the engine. Without it every DB-touching test fails.
    with TestClient(create_app(settings)) as test_client:
        # A fresh in-memory SQLite is empty per engine, but the Postgres run in
        # CI shares one database across the whole suite, so state must be
        # cleared explicitly or duplicate-detection tests contaminate each other.
        with get_engine().begin() as connection:
            connection.execute(delete(Reading))
        yield test_client


def reading_payload(**overrides: object) -> dict[str, object]:
    """A valid reading, with fields overridable per test."""
    payload: dict[str, object] = {
        "device_id": "sensor-042",
        "sensor_type": "temperature",
        "value": 22.5,
        "unit": "celsius",
        "timestamp": "2026-09-13T14:00:00Z",
    }
    payload.update(overrides)
    return payload
