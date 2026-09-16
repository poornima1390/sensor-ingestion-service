"""Integration tests for the probe endpoints."""

from fastapi.testclient import TestClient


def test_healthz_returns_ok(client: TestClient) -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "sensor-ingestion-service"


def test_healthz_does_not_depend_on_the_database(client: TestClient, monkeypatch) -> None:
    # The point of splitting the probes: liveness must stay green while the
    # database is down, or a DB blip becomes a fleet-wide restart storm.
    monkeypatch.setattr("app.storage.database.check_database", lambda: False)

    assert client.get("/healthz").status_code == 200


def test_readyz_reports_ready_when_database_reachable(client: TestClient) -> None:
    response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"database": "ok"}}


def test_readyz_returns_503_when_database_unreachable(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr("app.api.routes.health.check_database", lambda: False)

    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "checks": {"database": "unreachable"}}


def test_every_response_carries_a_request_id(client: TestClient) -> None:
    response = client.get("/healthz")

    assert response.headers["X-Request-ID"]


def test_inbound_request_id_is_echoed(client: TestClient) -> None:
    # Lets a caller's trace ID survive across the service boundary.
    response = client.get("/healthz", headers={"X-Request-ID": "trace-abc-123"})

    assert response.headers["X-Request-ID"] == "trace-abc-123"


def test_unknown_route_uses_the_standard_error_shape(client: TestClient) -> None:
    response = client.get("/nope")

    assert response.status_code == 404
    body = response.json()
    assert body["error"] == "not_found"
    assert "message" in body
    assert body["request_id"]
