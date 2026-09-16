"""Shared fixtures.

Tests run against in-memory SQLite by default for speed, and against whatever
DATABASE_URL points at when CI runs the same suite on Postgres.
"""

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(
        database_url=os.getenv("TEST_DATABASE_URL", "sqlite://"),
        log_level="WARNING",
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    # The context manager is required: it triggers the lifespan, which is what
    # initialises the engine. Without it every DB-touching test fails.
    with TestClient(create_app(settings)) as test_client:
        yield test_client
