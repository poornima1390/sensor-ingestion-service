"""Integration tests for POST /readings, exercised through the real app."""

from fastapi.testclient import TestClient

from tests.conftest import reading_payload

# --- single reading --------------------------------------------------------


def test_single_valid_reading_returns_201_with_the_stored_record(
    client: TestClient,
) -> None:
    response = client.post("/readings", json=reading_payload())

    assert response.status_code == 201
    body = response.json()
    # A single reading returns the bare record, not the batch envelope.
    assert "results" not in body
    assert body["id"]
    assert body["device_id"] == "sensor-042"
    assert body["value"] == 22.5
    assert body["timestamp"] == "2026-09-13T14:00:00Z"
    assert body["received_at"].endswith("Z")


def test_single_invalid_reading_returns_422_with_field_detail(
    client: TestClient,
) -> None:
    response = client.post("/readings", json=reading_payload(sensor_type="humidty"))

    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "validation_failed"
    assert body["detail"][0]["field"] == "sensor_type"
    assert body["request_id"]


def test_repeating_a_single_reading_returns_409(client: TestClient) -> None:
    assert client.post("/readings", json=reading_payload()).status_code == 201

    response = client.post("/readings", json=reading_payload())

    assert response.status_code == 409
    assert response.json()["error"] == "duplicate_reading"


def test_duplicate_does_not_create_a_second_row(client: TestClient) -> None:
    client.post("/readings", json=reading_payload())
    client.post("/readings", json=reading_payload())

    # Same natural key, different value: still one row, still the original.
    response = client.post("/readings", json=reading_payload(value=99.0))

    assert response.status_code == 409


def test_same_timestamp_different_sensor_type_is_not_a_duplicate(
    client: TestClient,
) -> None:
    # The natural key includes sensor_type, so one device reporting temperature
    # and humidity at the same instant is two legitimate readings.
    first = client.post("/readings", json=reading_payload())
    second = client.post(
        "/readings",
        json=reading_payload(sensor_type="humidity", unit="percent", value=61.0),
    )

    assert first.status_code == 201
    assert second.status_code == 201


# --- batches ---------------------------------------------------------------


def test_fully_valid_batch_returns_201(client: TestClient) -> None:
    payload = [
        reading_payload(),
        reading_payload(sensor_type="humidity", unit="percent", value=61.0),
        reading_payload(device_id="sensor-108", sensor_type="battery", unit="percent", value=87.0),
    ]

    response = client.post("/readings", json=payload)

    assert response.status_code == 201
    body = response.json()
    assert body["summary"] == {
        "received": 3,
        "created": 3,
        "duplicate": 0,
        "rejected": 0,
    }
    assert [r["status"] for r in body["results"]] == ["created"] * 3


def test_partial_batch_failure_returns_207_with_per_item_results(
    client: TestClient,
) -> None:
    """The headline requirement: one bad item must not cost the batch its good data."""
    client.post("/readings", json=reading_payload())  # seed the duplicate

    payload = [
        reading_payload(timestamp="2026-09-13T14:05:00Z"),  # created
        reading_payload(),  # duplicate
        reading_payload(sensor_type="pressure", unit="hpa", value=9999),  # rejected
    ]

    response = client.post("/readings", json=payload)

    assert response.status_code == 207
    body = response.json()
    assert body["summary"] == {
        "received": 3,
        "created": 1,
        "duplicate": 1,
        "rejected": 1,
    }

    created, duplicate, rejected = body["results"]

    assert created["index"] == 0
    assert created["status"] == "created"
    assert created["reading"]["id"]

    assert duplicate["index"] == 1
    assert duplicate["status"] == "duplicate"
    # The duplicate result carries the record that already exists, so the client
    # can reconcile without a second request.
    assert duplicate["reading"]["timestamp"] == "2026-09-13T14:00:00Z"

    assert rejected["index"] == 2
    assert rejected["status"] == "rejected"
    assert rejected["errors"][0]["field"] == "value"
    assert rejected["errors"][0]["code"] == "out_of_range"


def test_valid_items_in_a_mixed_batch_are_actually_persisted(
    client: TestClient,
) -> None:
    # Partial success is only real if the good rows survive the request.
    client.post(
        "/readings",
        json=[reading_payload(), reading_payload(sensor_type="vibration")],
    )

    # The valid item is now a duplicate, which proves it was committed.
    assert client.post("/readings", json=reading_payload()).status_code == 409


def test_batch_where_every_item_fails_returns_422(client: TestClient) -> None:
    payload = [
        reading_payload(sensor_type="vibration"),
        reading_payload(device_id=""),
    ]

    response = client.post("/readings", json=payload)

    assert response.status_code == 422
    body = response.json()
    assert body["summary"]["rejected"] == 2
    assert body["summary"]["created"] == 0


def test_all_duplicate_batch_returns_207(client: TestClient) -> None:
    client.post("/readings", json=reading_payload())

    response = client.post("/readings", json=[reading_payload()])

    assert response.status_code == 207
    assert response.json()["summary"]["duplicate"] == 1


def test_duplicates_within_one_batch_are_detected(client: TestClient) -> None:
    # The same reading twice in one request: the first is stored, the second is
    # a duplicate of it, even though neither existed beforehand.
    response = client.post("/readings", json=[reading_payload(), reading_payload()])

    assert response.status_code == 207
    body = response.json()
    assert body["summary"] == {
        "received": 2,
        "created": 1,
        "duplicate": 1,
        "rejected": 0,
    }
    assert body["results"][1]["reading"]["id"] == body["results"][0]["reading"]["id"]


def test_results_are_ordered_by_index(client: TestClient) -> None:
    # Clients correlate by index, so order and index must both be dependable.
    payload = [
        reading_payload(sensor_type="vibration"),
        reading_payload(),
        reading_payload(value=9999),
    ]

    results = client.post("/readings", json=payload).json()["results"]

    assert [r["index"] for r in results] == [0, 1, 2]
    assert [r["status"] for r in results] == ["rejected", "created", "rejected"]


# --- request shape ---------------------------------------------------------


def test_malformed_json_returns_400_not_500(client: TestClient) -> None:
    response = client.post(
        "/readings",
        content=b'{"device_id": "sensor-042", "value": }',
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "malformed_json"


def test_body_that_is_neither_object_nor_array_returns_400(client: TestClient) -> None:
    response = client.post("/readings", json="just a string")

    assert response.status_code == 400
    assert response.json()["error"] == "bad_request"


def test_empty_batch_returns_422(client: TestClient) -> None:
    response = client.post("/readings", json=[])

    assert response.status_code == 422
    assert response.json()["error"] == "empty_batch"


def test_oversized_batch_returns_413(client: TestClient) -> None:
    payload = [reading_payload(timestamp=f"2026-09-13T14:00:{second:02d}Z") for second in range(60)]

    response = client.post("/readings", json=payload * 20)  # 1200 > 1000

    assert response.status_code == 413
    assert response.json()["error"] == "batch_too_large"


def test_ingest_response_carries_a_request_id(client: TestClient) -> None:
    response = client.post("/readings", json=reading_payload(device_id=""))

    assert response.headers["X-Request-ID"]
    assert response.json()["request_id"]


# --- failure handling ------------------------------------------------------


def test_unhandled_error_returns_500_carrying_the_request_id(settings, monkeypatch) -> None:
    """A 500 is the case where the request ID matters most.

    The exception handler runs in ServerErrorMiddleware, outside the request
    middleware, so an eagerly-reset ContextVar used to strip the ID from exactly
    these responses.
    """
    from fastapi.testclient import TestClient

    from app.main import create_app

    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("storage exploded")

    monkeypatch.setattr("app.api.routes.readings.ingest_items", boom)

    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        response = client.post("/readings", json=reading_payload())

    assert response.status_code == 500
    body = response.json()
    assert body["error"] == "internal_error"
    # No internal detail leaks to the caller.
    assert "storage exploded" not in response.text
    assert body["request_id"]
    assert response.headers.get("X-Request-ID")


def test_item_results_omit_inapplicable_fields(client: TestClient) -> None:
    payload = [reading_payload(), reading_payload(sensor_type="vibration")]

    results = client.post("/readings", json=payload).json()["results"]

    created, rejected = results
    assert "errors" not in created
    assert "reading" not in rejected
    # A genuinely absent unit is still reported, rather than silently dropped.
    assert "unit" in created["reading"]
