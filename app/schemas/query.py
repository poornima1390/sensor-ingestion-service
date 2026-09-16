"""Query-parameter contracts for the read endpoints.

Parsed into typed objects by FastAPI dependencies so the handlers receive
validated values and never touch `request.query_params` themselves.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Query

from app.domain.sensors import SensorType

DEFAULT_LIMIT = 50
MAX_LIMIT = 1000

# The fields a caller is allowed to aggregate by. Anything else would either be
# meaningless (grouping by `value`) or unbounded (grouping by `timestamp`).
GROUPABLE_FIELDS = ("device_id", "sensor_type")
DEFAULT_GROUP_BY = ("sensor_type",)


def _to_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@dataclass(frozen=True)
class ReadingFilters:
    device_id: str | None = None
    sensor_type: SensorType | None = None
    start: datetime | None = None
    end: datetime | None = None


@dataclass(frozen=True)
class Pagination:
    limit: int = DEFAULT_LIMIT
    offset: int = 0


def reading_filters(
    device_id: Annotated[str | None, Query(description="Exact device match")] = None,
    sensor_type: Annotated[SensorType | None, Query()] = None,
    start: Annotated[datetime | None, Query(description="ISO-8601, inclusive lower bound")] = None,
    end: Annotated[datetime | None, Query(description="ISO-8601, exclusive upper bound")] = None,
) -> ReadingFilters:
    start, end = _to_utc(start), _to_utc(end)

    # Cross-parameter rules cannot live on a single field, so they are checked
    # here rather than in a validator.
    if start is not None and end is not None and start > end:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_window",
                "message": "`start` must not be later than `end`.",
            },
        )

    if device_id is not None:
        device_id = device_id.strip()
        if not device_id:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "invalid_filter",
                    "message": "`device_id` must not be empty.",
                },
            )

    return ReadingFilters(device_id=device_id, sensor_type=sensor_type, start=start, end=end)


def pagination(
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Pagination:
    return Pagination(limit=limit, offset=offset)


def group_by(
    group_by: Annotated[
        str | None,
        Query(description="Comma-separated: device_id, sensor_type"),
    ] = None,
) -> tuple[str, ...]:
    """Parse and validate the aggregation grouping.

    Defaults to `sensor_type` rather than a single global aggregate, because an
    average taken across temperature (°C), pressure (hPa) and battery (%) is a
    number with no meaning. Defaulting to a per-type breakdown means the endpoint
    cannot return nonsense by accident.
    """
    if group_by is None or not group_by.strip():
        return DEFAULT_GROUP_BY

    requested = [field.strip() for field in group_by.split(",") if field.strip()]
    unknown = [field for field in requested if field not in GROUPABLE_FIELDS]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_group_by",
                "message": (
                    f"Cannot group by {', '.join(unknown)}. "
                    f"Allowed: {', '.join(GROUPABLE_FIELDS)}."
                ),
            },
        )

    # Dedupe while preserving the caller's order.
    return tuple(dict.fromkeys(requested))


Filters = Annotated[ReadingFilters, Depends(reading_filters)]
Page = Annotated[Pagination, Depends(pagination)]
GroupBy = Annotated[tuple[str, ...], Depends(group_by)]
