"""Integration tests for the Prometheus endpoint.

Collectors live on the process-wide default registry, so values accumulate
across tests. Assertions compare deltas rather than absolutes.
"""

from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families

from tests.conftest import reading_payload


def sample(text: str, name: str, **labels: str) -> float:
    """Read one sample's value out of the exposition text."""
    for family in text_string_to_metric_families(text):
        for item in family.samples:
            if item.name == name and all(
                item.labels.get(key) == value for key, value in labels.items()
            ):
                return item.value
    return 0.0


def scrape(client: TestClient) -> str:
    response = client.get("/metrics")
    assert response.status_code == 200
    return response.text


def test_metrics_endpoint_serves_prometheus_text(client: TestClient) -> None:
    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    assert "http_requests_total" in body
    assert "http_request_duration_seconds" in body
    assert "readings_ingested_total" in body
    assert "readings_ingest_lag_seconds" in body


def test_request_counter_increments_for_a_matched_route(client: TestClient) -> None:
    before = sample(scrape(client), "http_requests_total", path="/healthz", status="200")

    client.get("/healthz")

    after = sample(scrape(client), "http_requests_total", path="/healthz", status="200")
    assert after == before + 1


def test_labels_use_the_route_template_not_the_raw_url(client: TestClient) -> None:
    client.get("/readings", params={"device_id": "sensor-042", "limit": 5})

    body = scrape(client)

    # The template, not the query string or a per-request URL.
    assert sample(body, "http_requests_total", path="/readings", status="200") >= 1
    assert "sensor-042" not in body


def test_unmatched_paths_collapse_into_one_label(client: TestClient) -> None:
    """The cardinality guard.

    Without it, every URL a vulnerability scanner tries would mint a new time
    series — an unbounded memory leak wearing the costume of observability.
    """
    before = sample(scrape(client), "http_requests_total", path="<unmatched>", status="404")

    client.get("/wp-admin.php")
    client.get("/.env")
    client.get("/some/deeply/made/up/path")

    body = scrape(client)
    after = sample(body, "http_requests_total", path="<unmatched>", status="404")

    assert after == before + 3
    # None of the probed URLs became label values.
    assert "wp-admin" not in body
    assert ".env" not in body


def test_readings_counter_labels_by_outcome(client: TestClient) -> None:
    def created() -> float:
        return sample(
            scrape(client),
            "readings_ingested_total",
            sensor_type="temperature",
            status="created",
        )

    def rejected() -> float:
        return sample(
            scrape(client), "readings_ingested_total", sensor_type="unknown", status="rejected"
        )

    created_before, rejected_before = created(), rejected()

    client.post(
        "/readings",
        json=[
            reading_payload(),
            reading_payload(),  # duplicate of the first, within the batch
            reading_payload(sensor_type="vibration"),
        ],
    )

    assert created() == created_before + 1
    # A rejected item may have failed because its sensor_type was bad, so there
    # is nothing trustworthy to label it with.
    assert rejected() == rejected_before + 1
    assert (
        sample(
            scrape(client),
            "readings_ingested_total",
            sensor_type="temperature",
            status="duplicate",
        )
        >= 1
    )


def test_ingest_lag_is_observed_for_created_readings(client: TestClient) -> None:
    before = sample(scrape(client), "readings_ingest_lag_seconds_count")

    client.post("/readings", json=reading_payload())

    assert sample(scrape(client), "readings_ingest_lag_seconds_count") == before + 1


def test_metrics_endpoint_is_not_in_the_public_schema(client: TestClient) -> None:
    # It is an operational endpoint, not part of the API contract.
    schema = client.get("/openapi.json").json()

    assert "/metrics" not in schema["paths"]
    assert "/readings" in schema["paths"]
