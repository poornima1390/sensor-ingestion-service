"""Integration tests for GET /readings and GET /readings/stats."""

from fastapi.testclient import TestClient

from tests.conftest import reading_payload


def seed(client: TestClient, readings: list[dict[str, object]]) -> None:
    response = client.post("/readings", json=readings)
    assert response.status_code in (201, 207), response.text


def ids(body: dict) -> list[int]:
    return [item["id"] for item in body["items"]]


# --- filtering -------------------------------------------------------------


def test_empty_result_is_200_with_an_empty_list(client: TestClient) -> None:
    # No matches is a successful query, not a 404.
    response = client.get("/readings", params={"device_id": "nope"})

    assert response.status_code == 200
    assert response.json() == {
        "items": [],
        "pagination": {"limit": 50, "offset": 0, "total": 0},
    }


def test_filter_by_device_id(client: TestClient) -> None:
    seed(
        client,
        [
            reading_payload(device_id="sensor-a", timestamp="2026-09-13T14:00:00Z"),
            reading_payload(device_id="sensor-b", timestamp="2026-09-13T14:01:00Z"),
        ],
    )

    body = client.get("/readings", params={"device_id": "sensor-a"}).json()

    assert body["pagination"]["total"] == 1
    assert {item["device_id"] for item in body["items"]} == {"sensor-a"}


def test_filter_by_sensor_type(client: TestClient) -> None:
    seed(
        client,
        [
            reading_payload(),
            reading_payload(sensor_type="humidity", unit="percent", value=61.0),
        ],
    )

    body = client.get("/readings", params={"sensor_type": "humidity"}).json()

    assert body["pagination"]["total"] == 1
    assert body["items"][0]["sensor_type"] == "humidity"


def test_time_window_start_is_inclusive_and_end_is_exclusive(
    client: TestClient,
) -> None:
    # Adjacent windows must tile without double-counting a reading.
    seed(
        client,
        [
            reading_payload(device_id="d1", timestamp="2026-09-13T14:00:00Z"),
            reading_payload(device_id="d2", timestamp="2026-09-13T15:00:00Z"),
            reading_payload(device_id="d3", timestamp="2026-09-13T16:00:00Z"),
        ],
    )

    body = client.get(
        "/readings",
        params={"start": "2026-09-13T14:00:00Z", "end": "2026-09-13T16:00:00Z"},
    ).json()

    returned = {item["device_id"] for item in body["items"]}
    assert returned == {"d1", "d2"}  # 14:00 included, 16:00 excluded


def test_filters_combine(client: TestClient) -> None:
    seed(
        client,
        [
            reading_payload(device_id="d1", timestamp="2026-09-13T14:00:00Z"),
            reading_payload(device_id="d1", sensor_type="humidity", unit="percent", value=61.0),
            reading_payload(device_id="d2", timestamp="2026-09-13T14:00:00Z"),
        ],
    )

    body = client.get("/readings", params={"device_id": "d1", "sensor_type": "temperature"}).json()

    assert body["pagination"]["total"] == 1


# --- ordering and pagination ----------------------------------------------


def test_results_are_newest_first(client: TestClient) -> None:
    seed(
        client,
        [
            reading_payload(device_id="d1", timestamp="2026-09-13T14:00:00Z"),
            reading_payload(device_id="d2", timestamp="2026-09-13T16:00:00Z"),
            reading_payload(device_id="d3", timestamp="2026-09-13T15:00:00Z"),
        ],
    )

    body = client.get("/readings").json()

    timestamps = [item["timestamp"] for item in body["items"]]
    assert timestamps == sorted(timestamps, reverse=True)


def test_total_reflects_all_matches_not_just_the_page(client: TestClient) -> None:
    seed(
        client,
        [reading_payload(device_id=f"d{n}", timestamp="2026-09-13T14:00:00Z") for n in range(5)],
    )

    body = client.get("/readings", params={"limit": 2}).json()

    assert len(body["items"]) == 2
    assert body["pagination"]["total"] == 5


def test_paging_identical_timestamps_never_overlaps_or_drops_rows(
    client: TestClient,
) -> None:
    """The reason ORDER BY carries an `id` tiebreaker.

    Batch ingests routinely produce many readings sharing one timestamp. Ordering
    by timestamp alone leaves their relative order undefined, so the database is
    free to return them differently per query — and consecutive pages then repeat
    some rows and silently skip others.
    """
    seed(
        client,
        [
            reading_payload(device_id=f"device-{n:02d}", timestamp="2026-09-13T14:00:00Z")
            for n in range(10)
        ],
    )

    collected: list[int] = []
    for offset in range(0, 10, 3):
        page = client.get("/readings", params={"limit": 3, "offset": offset}).json()
        collected.extend(ids(page))

    assert len(collected) == 10
    assert len(set(collected)) == 10  # no row seen twice, none missed


def test_offset_past_the_end_returns_an_empty_page(client: TestClient) -> None:
    seed(client, [reading_payload()])

    body = client.get("/readings", params={"offset": 500}).json()

    assert body["items"] == []
    assert body["pagination"]["total"] == 1


# --- query parameter errors -----------------------------------------------


def test_unparseable_timestamp_is_400_not_422(client: TestClient) -> None:
    # "I could not understand the request at all" rather than
    # "I understood it and it is wrong".
    response = client.get("/readings", params={"start": "not-a-date"})

    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "invalid_query_parameter"
    assert body["detail"][0]["field"] == "start"
    assert body["detail"][0]["code"] == "invalid_timestamp"


def test_non_integer_limit_is_400(client: TestClient) -> None:
    response = client.get("/readings", params={"limit": "many"})

    assert response.status_code == 400
    assert response.json()["detail"][0]["code"] == "invalid_integer"


def test_limit_above_maximum_is_422(client: TestClient) -> None:
    # Parsed fine, violates a constraint.
    response = client.get("/readings", params={"limit": 5000})

    assert response.status_code == 422
    assert response.json()["error"] == "validation_failed"


def test_negative_offset_is_422(client: TestClient) -> None:
    assert client.get("/readings", params={"offset": -1}).status_code == 422


def test_unknown_sensor_type_filter_is_422(client: TestClient) -> None:
    response = client.get("/readings", params={"sensor_type": "vibration"})

    assert response.status_code == 422
    assert response.json()["detail"][0]["field"] == "sensor_type"


def test_start_after_end_is_422(client: TestClient) -> None:
    response = client.get(
        "/readings",
        params={"start": "2026-09-13T16:00:00Z", "end": "2026-09-13T14:00:00Z"},
    )

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_window"


# --- stats -----------------------------------------------------------------


def test_stats_defaults_to_grouping_by_sensor_type(client: TestClient) -> None:
    """An average across °C, hPa and % is a number with no meaning.

    Defaulting to a per-type breakdown means the endpoint cannot return nonsense
    by accident.
    """
    seed(
        client,
        [
            reading_payload(value=10.0),
            reading_payload(value=20.0, timestamp="2026-09-13T14:01:00Z"),
            reading_payload(sensor_type="pressure", unit="hpa", value=1000.0),
        ],
    )

    body = client.get("/readings/stats").json()

    assert body["group_by"] == ["sensor_type"]
    groups = {group["key"]["sensor_type"]: group for group in body["groups"]}
    assert groups["temperature"] == {
        "key": {"sensor_type": "temperature"},
        "count": 2,
        "min": 10.0,
        "max": 20.0,
        "avg": 15.0,
    }
    assert groups["pressure"]["count"] == 1


def test_stats_average_is_rounded(client: TestClient) -> None:
    seed(
        client,
        [
            reading_payload(value=1.0, timestamp="2026-09-13T14:00:00Z"),
            reading_payload(value=2.0, timestamp="2026-09-13T14:01:00Z"),
            reading_payload(value=4.0, timestamp="2026-09-13T14:02:00Z"),
        ],
    )

    group = client.get("/readings/stats").json()["groups"][0]

    assert group["avg"] == 2.3333


def test_stats_group_by_device_id(client: TestClient) -> None:
    seed(
        client,
        [
            reading_payload(device_id="d1", value=10.0),
            reading_payload(device_id="d2", value=30.0),
        ],
    )

    body = client.get("/readings/stats", params={"group_by": "device_id"}).json()

    assert body["group_by"] == ["device_id"]
    assert {group["key"]["device_id"] for group in body["groups"]} == {"d1", "d2"}


def test_stats_group_by_multiple_dimensions(client: TestClient) -> None:
    seed(
        client,
        [
            reading_payload(device_id="d1", value=10.0),
            reading_payload(device_id="d1", sensor_type="humidity", unit="percent", value=50.0),
            reading_payload(device_id="d2", value=30.0),
        ],
    )

    body = client.get("/readings/stats", params={"group_by": "device_id,sensor_type"}).json()

    assert body["group_by"] == ["device_id", "sensor_type"]
    assert len(body["groups"]) == 3
    # The key carries only the grouped dimensions, so the shape follows the request.
    assert set(body["groups"][0]["key"]) == {"device_id", "sensor_type"}


def test_stats_respects_filters_and_echoes_the_window(client: TestClient) -> None:
    seed(
        client,
        [
            reading_payload(device_id="d1", value=10.0, timestamp="2026-09-13T14:00:00Z"),
            reading_payload(device_id="d1", value=90.0, timestamp="2026-09-13T20:00:00Z"),
        ],
    )

    body = client.get(
        "/readings/stats",
        params={"start": "2026-09-13T13:00:00Z", "end": "2026-09-13T15:00:00Z"},
    ).json()

    assert body["window"] == {
        "start": "2026-09-13T13:00:00Z",
        "end": "2026-09-13T15:00:00Z",
    }
    assert body["groups"][0]["count"] == 1
    assert body["groups"][0]["max"] == 10.0


def test_stats_with_no_data_returns_200_and_no_groups(client: TestClient) -> None:
    body = client.get("/readings/stats").json()

    assert body["groups"] == []
    assert body["window"] == {"start": None, "end": None}


def test_stats_rejects_an_ungroupable_field(client: TestClient) -> None:
    response = client.get("/readings/stats", params={"group_by": "value"})

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_group_by"


def test_stats_deduplicates_repeated_group_by_fields(client: TestClient) -> None:
    seed(client, [reading_payload()])

    body = client.get("/readings/stats", params={"group_by": "sensor_type,sensor_type"}).json()

    assert body["group_by"] == ["sensor_type"]
