"""Unit tests for URL normalisation.

Worth its own test because the deployed app depends on it: DigitalOcean injects
`postgresql://`, SQLAlchemy 2 refuses a URL with no driver, and the failure only
shows up at container start in production.
"""

import pytest

from app.storage.database import normalize_database_url


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("postgres://u:p@h:5432/db", "postgresql+psycopg://u:p@h:5432/db"),
        ("postgresql://u:p@h:5432/db", "postgresql+psycopg://u:p@h:5432/db"),
        ("postgresql+psycopg://u:p@h:5432/db", "postgresql+psycopg://u:p@h:5432/db"),
        ("sqlite:///./readings.db", "sqlite:///./readings.db"),
        ("sqlite://", "sqlite://"),
    ],
)
def test_normalize_database_url(given: str, expected: str) -> None:
    assert normalize_database_url(given) == expected


def test_query_params_survive_normalisation() -> None:
    # DO appends ?sslmode=require; losing it would break the connection.
    url = "postgresql://u:p@h:25060/db?sslmode=require"
    assert normalize_database_url(url).endswith("?sslmode=require")
