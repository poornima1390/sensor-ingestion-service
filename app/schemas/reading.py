"""Wire contracts for the readings API.

These are the API's shape, deliberately separate from the SQLAlchemy model in
app/storage/models.py so the two can evolve independently — adding an internal
column must not leak into the public contract.
"""

import math
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationInfo,
    field_serializer,
    field_validator,
    model_serializer,
)
from pydantic_core import PydanticCustomError

from app.domain.sensors import (
    FUTURE_TOLERANCE_SECONDS,
    MAX_DEVICE_ID_LENGTH,
    SENSOR_RULES,
    SensorType,
)


def _to_utc(value: datetime) -> datetime:
    """Normalise to aware UTC. A naive timestamp is assumed to be UTC."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class ReadingIn(BaseModel):
    """One inbound reading.

    Field order is load-bearing: Pydantic validates in declaration order and
    exposes already-validated fields via `info.data`, so `value` and `unit` can
    check themselves against `sensor_type`. Doing these as field validators
    rather than a model validator is what puts each error on the field that
    caused it instead of on the body as a whole.
    """

    # Unknown fields are ignored rather than rejected: a firmware update adding
    # a field must not break ingestion for the whole fleet.
    model_config = ConfigDict(extra="ignore")

    device_id: str
    sensor_type: SensorType
    value: float
    unit: str | None = None
    timestamp: datetime

    @field_validator("device_id")
    @classmethod
    def _validate_device_id(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise PydanticCustomError("empty_device_id", "must not be empty")
        if len(stripped) > MAX_DEVICE_ID_LENGTH:
            raise PydanticCustomError(
                "device_id_too_long",
                "must be at most {limit} characters",
                {"limit": MAX_DEVICE_ID_LENGTH},
            )
        return stripped

    @field_validator("value")
    @classmethod
    def _validate_value(cls, value: float, info: ValidationInfo) -> float:
        if not math.isfinite(value):
            raise PydanticCustomError("not_finite", "must be a finite number")

        sensor_type = info.data.get("sensor_type")
        if sensor_type is None:
            # sensor_type already failed; its own error is the useful one.
            return value

        rule = SENSOR_RULES[sensor_type]
        if not rule.contains(value):
            raise PydanticCustomError(
                "out_of_range",
                "{sensor_type} value {value} outside plausible range {minimum}..{maximum}",
                {
                    "sensor_type": sensor_type.value,
                    "value": value,
                    "minimum": rule.minimum,
                    "maximum": rule.maximum,
                },
            )
        return value

    @field_validator("unit")
    @classmethod
    def _validate_unit(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None

        normalised = value.strip().lower()
        sensor_type = info.data.get("sensor_type")
        if sensor_type is None:
            return normalised

        expected = SENSOR_RULES[sensor_type].unit
        if normalised != expected:
            # Rejected, never converted. Silently coercing units is how you get a
            # dataset nobody trusts.
            raise PydanticCustomError(
                "unit_mismatch",
                "unit '{given}' is not valid for {sensor_type}; expected '{expected}'",
                {"given": value, "sensor_type": sensor_type.value, "expected": expected},
            )
        return normalised

    @field_validator("timestamp")
    @classmethod
    def _validate_timestamp(cls, value: datetime, info: ValidationInfo) -> datetime:
        value = _to_utc(value)

        # `now` is injected through the validation context so the rule is
        # testable without freezing the system clock.
        now: datetime = (info.context or {}).get("now") or datetime.now(UTC)
        drift = (value - now).total_seconds()
        if drift > FUTURE_TOLERANCE_SECONDS:
            raise PydanticCustomError(
                "timestamp_in_future",
                "timestamp is {drift} seconds in the future (tolerance {tolerance}s)",
                {"drift": round(drift, 3), "tolerance": FUTURE_TOLERANCE_SECONDS},
            )
        return value


class ReadingOut(BaseModel):
    """One stored reading, as returned to clients."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    device_id: str
    sensor_type: SensorType
    value: float
    unit: str | None
    timestamp: datetime
    received_at: datetime

    @field_serializer("timestamp", "received_at")
    def _serialize_utc(self, value: datetime) -> str:
        # SQLite hands back naive datetimes and Postgres hands back aware ones;
        # normalising here means the API emits identical JSON on both.
        return _to_utc(value).isoformat().replace("+00:00", "Z")


ItemStatus = Literal["created", "duplicate", "rejected"]


class ItemError(BaseModel):
    field: str
    code: str
    message: str


class ItemResult(BaseModel):
    """Per-item outcome, correlated by index.

    Index rather than device_id, because a batch legitimately contains many
    readings from the same device — the index is the only stable handle a client
    has for matching results back to what it sent.
    """

    index: int
    status: ItemStatus
    reading: ReadingOut | None = None
    errors: list[ItemError] | None = None

    @model_serializer(mode="wrap")
    def _omit_absent(self, handler: Any) -> dict[str, Any]:
        # A created item has no errors and a rejected one has no reading;
        # emitting explicit nulls for the inapplicable half is noise. Scoped to
        # this model so a stored reading still reports `unit: null` honestly.
        return {key: value for key, value in handler(self).items() if value is not None}


class BatchSummary(BaseModel):
    received: int
    created: int
    duplicate: int
    rejected: int


class BatchResponse(BaseModel):
    summary: BatchSummary
    results: list[ItemResult]


def reading_payload(reading: Any) -> dict[str, Any]:
    """Serialise a stored reading to plain JSON types."""
    return ReadingOut.model_validate(reading).model_dump(mode="json")


class PaginationMeta(BaseModel):
    limit: int
    offset: int
    total: int


class ReadingListResponse(BaseModel):
    items: list[ReadingOut]
    pagination: PaginationMeta


class StatsWindow(BaseModel):
    start: datetime | None = None
    end: datetime | None = None

    @field_serializer("start", "end")
    def _serialize_utc(self, value: datetime | None) -> str | None:
        return None if value is None else _to_utc(value).isoformat().replace("+00:00", "Z")


class StatGroup(BaseModel):
    """One aggregation bucket.

    `key` carries only the fields that were grouped on, so the response shape
    follows the request rather than always carrying every possible dimension.
    """

    key: dict[str, str]
    count: int
    min: float
    max: float
    avg: float


class StatsResponse(BaseModel):
    group_by: list[str]
    window: StatsWindow
    groups: list[StatGroup]
