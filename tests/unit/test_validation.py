"""Unit tests for per-item validation.

These never touch the database or the app; `validate_reading` is pure and the
clock is injected, which is what makes that possible.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.sensors import SensorType
from app.domain.validation import validate_reading

NOW = datetime(2026, 9, 13, 15, 0, 0, tzinfo=UTC)


def valid(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "device_id": "sensor-042",
        "sensor_type": "temperature",
        "value": 22.5,
        "unit": "celsius",
        "timestamp": "2026-09-13T14:00:00Z",
    }
    payload.update(overrides)
    return payload


def codes(errors) -> set[str]:
    return {error.code for error in errors}


def fields(errors) -> set[str]:
    return {error.field for error in errors}


def test_valid_reading_passes() -> None:
    reading, errors = validate_reading(valid(), now=NOW)

    assert errors == []
    assert reading is not None
    assert reading.device_id == "sensor-042"
    assert reading.sensor_type is SensorType.TEMPERATURE


# --- device_id -------------------------------------------------------------


@pytest.mark.parametrize("device_id", ["", "   ", "\t\n"])
def test_blank_device_id_is_rejected(device_id: str) -> None:
    _, errors = validate_reading(valid(device_id=device_id), now=NOW)

    assert "empty_device_id" in codes(errors)


def test_device_id_is_stripped() -> None:
    reading, errors = validate_reading(valid(device_id="  sensor-042  "), now=NOW)

    assert errors == []
    assert reading is not None
    assert reading.device_id == "sensor-042"


def test_overlong_device_id_is_rejected() -> None:
    _, errors = validate_reading(valid(device_id="x" * 65), now=NOW)

    assert "device_id_too_long" in codes(errors)


# --- sensor_type -----------------------------------------------------------


def test_unknown_sensor_type_is_rejected() -> None:
    _, errors = validate_reading(valid(sensor_type="vibration"), now=NOW)

    assert "invalid_enum" in codes(errors)
    assert "sensor_type" in fields(errors)


def test_missing_field_is_reported_as_required() -> None:
    payload = valid()
    del payload["value"]

    _, errors = validate_reading(payload, now=NOW)

    assert "required" in codes(errors)
    assert "value" in fields(errors)


# --- value ranges ----------------------------------------------------------


@pytest.mark.parametrize(
    ("sensor_type", "unit", "value"),
    [
        ("temperature", "celsius", -50.0),
        ("temperature", "celsius", 150.0),
        ("humidity", "percent", 0.0),
        ("humidity", "percent", 100.0),
        ("pressure", "hpa", 300.0),
        ("pressure", "hpa", 1100.0),
        ("battery", "percent", 50.0),
    ],
)
def test_values_at_and_inside_range_bounds_are_accepted(
    sensor_type: str, unit: str, value: float
) -> None:
    _, errors = validate_reading(valid(sensor_type=sensor_type, unit=unit, value=value), now=NOW)

    assert errors == []


@pytest.mark.parametrize(
    ("sensor_type", "unit", "value"),
    [
        ("temperature", "celsius", -50.1),
        ("temperature", "celsius", 150.1),
        ("humidity", "percent", -0.1),
        ("humidity", "percent", 100.1),
        ("pressure", "hpa", 299.9),
        ("pressure", "hpa", 9999.0),
        ("battery", "percent", 101.0),
    ],
)
def test_values_outside_range_are_rejected(sensor_type: str, unit: str, value: float) -> None:
    _, errors = validate_reading(valid(sensor_type=sensor_type, unit=unit, value=value), now=NOW)

    assert "out_of_range" in codes(errors)
    # The error must name the field that caused it, not the body as a whole.
    assert "value" in fields(errors)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_values_are_rejected(value: float) -> None:
    _, errors = validate_reading(valid(value=value), now=NOW)

    assert "not_finite" in codes(errors)


def test_range_check_is_skipped_when_sensor_type_is_invalid() -> None:
    # Reporting "value out of range" for an unknown type would be noise; the
    # sensor_type error is the one worth acting on.
    _, errors = validate_reading(valid(sensor_type="vibration", value=9999), now=NOW)

    assert "out_of_range" not in codes(errors)


# --- units -----------------------------------------------------------------


def test_unit_is_optional() -> None:
    payload = valid()
    del payload["unit"]

    reading, errors = validate_reading(payload, now=NOW)

    assert errors == []
    assert reading is not None
    assert reading.unit is None


def test_unit_matching_is_case_insensitive() -> None:
    reading, errors = validate_reading(valid(unit="CELSIUS"), now=NOW)

    assert errors == []
    assert reading is not None
    assert reading.unit == "celsius"


def test_mismatched_unit_is_rejected_not_converted() -> None:
    # Silently converting fahrenheit is how you get a dataset nobody trusts.
    _, errors = validate_reading(valid(unit="fahrenheit", value=72.5), now=NOW)

    assert "unit_mismatch" in codes(errors)
    assert "unit" in fields(errors)


# --- timestamps ------------------------------------------------------------


def test_naive_timestamp_is_assumed_utc() -> None:
    reading, errors = validate_reading(valid(timestamp="2026-09-13T14:00:00"), now=NOW)

    assert errors == []
    assert reading is not None
    assert reading.timestamp == datetime(2026, 9, 13, 14, 0, tzinfo=UTC)


def test_offset_timestamp_is_converted_to_utc() -> None:
    reading, errors = validate_reading(valid(timestamp="2026-09-13T16:00:00+02:00"), now=NOW)

    assert errors == []
    assert reading is not None
    assert reading.timestamp == datetime(2026, 9, 13, 14, 0, tzinfo=UTC)


def test_far_future_timestamp_is_rejected() -> None:
    future = (NOW + timedelta(hours=1)).isoformat()

    _, errors = validate_reading(valid(timestamp=future), now=NOW)

    assert "timestamp_in_future" in codes(errors)


def test_slight_clock_skew_is_tolerated() -> None:
    # Device clocks drift; rejecting a reading five seconds ahead is a bug you
    # find in production, not in tests.
    skewed = (NOW + timedelta(seconds=5)).isoformat()

    _, errors = validate_reading(valid(timestamp=skewed), now=NOW)

    assert errors == []


def test_unparseable_timestamp_is_rejected() -> None:
    _, errors = validate_reading(valid(timestamp="not-a-date"), now=NOW)

    assert "invalid_timestamp" in codes(errors)


# --- shape -----------------------------------------------------------------


def test_unknown_fields_are_ignored() -> None:
    # A firmware update adding a field must not break ingestion fleet-wide.
    reading, errors = validate_reading(valid(firmware_version="2.1.0"), now=NOW)

    assert errors == []
    assert reading is not None


@pytest.mark.parametrize("payload", ["a string", 42, None, ["nested"]])
def test_non_object_items_are_rejected(payload: object) -> None:
    reading, errors = validate_reading(payload, now=NOW)

    assert reading is None
    assert "invalid_object" in codes(errors)


def test_multiple_independent_failures_are_all_reported() -> None:
    # Returning only the first error would make a client fix one thing at a time.
    _, errors = validate_reading(valid(device_id="   ", sensor_type="vibration"), now=NOW)

    assert fields(errors) == {"device_id", "sensor_type"}
