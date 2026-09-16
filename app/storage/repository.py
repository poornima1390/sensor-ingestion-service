"""Data access for readings.

SQL lives here, never in the handlers, and everything is dialect-neutral: the
same statements have to work on SQLite in the test suite and Postgres in
production. That rules out `INSERT ... ON CONFLICT`, which is why deduplication
is a pre-check plus a constraint backstop rather than an upsert.
"""

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.schemas.query import ReadingFilters
from app.schemas.reading import ReadingIn
from app.storage.models import Reading

logger = logging.getLogger(__name__)

# (device_id, sensor_type, timestamp-as-naive-UTC)
NaturalKey = tuple[str, str, datetime]


def natural_key(device_id: str, sensor_type: str, timestamp: datetime) -> NaturalKey:
    """Build the dedup key.

    Timestamps are reduced to naive UTC because SQLite returns naive datetimes
    and Postgres returns aware ones; comparing them directly would make the same
    reading look like a duplicate on one dialect and not the other.
    """
    if timestamp.tzinfo is not None:
        timestamp = timestamp.astimezone(UTC).replace(tzinfo=None)
    return (device_id, str(sensor_type), timestamp)


class ReadingRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def find_existing(self, candidates: Sequence[ReadingIn]) -> dict[NaturalKey, Reading]:
        """Fetch any stored readings matching the candidates' natural keys.

        Filtered by device_id plus the candidates' time span rather than a tuple
        IN clause: it uses the (device_id, timestamp) index, and tuple-IN support
        is uneven across dialects. The final match is done in Python.
        """
        if not candidates:
            return {}

        device_ids = {candidate.device_id for candidate in candidates}
        timestamps = [candidate.timestamp for candidate in candidates]

        rows = (
            self.session.execute(
                select(Reading).where(
                    Reading.device_id.in_(device_ids),
                    Reading.timestamp >= min(timestamps),
                    Reading.timestamp <= max(timestamps),
                )
            )
            .scalars()
            .all()
        )

        wanted = {natural_key(c.device_id, c.sensor_type, c.timestamp) for c in candidates}
        found = {natural_key(row.device_id, row.sensor_type, row.timestamp): row for row in rows}
        return {key: row for key, row in found.items() if key in wanted}

    def insert_many(self, items: Sequence[ReadingIn]) -> list[tuple[ReadingIn, Reading | None]]:
        """Insert readings, returning one (item, row) pair per input, in order.

        `row` is None when that item lost a natural-key race. Returning a pair
        per input keeps the caller's index correlation intact, which the batch
        response contract depends on.

        Fast path is one batched INSERT. A collision here means another request
        wrote the same natural key between our pre-check and this insert, which
        the unique constraint catches; we then fall back to per-item savepoints
        to find out exactly which rows lost the race instead of failing the whole
        batch. Rare enough not to be the common path, real enough to handle.
        """
        if not items:
            return []

        savepoint = self.session.begin_nested()
        rows = [self._to_row(item) for item in items]
        try:
            self.session.add_all(rows)
            self.session.flush()
        except IntegrityError:
            savepoint.rollback()
            for row in rows:
                self.session.expunge(row)
            logger.info(
                "batch insert hit a natural-key race; retrying per item",
                extra={"batch_size": len(items)},
            )
            return self._insert_individually(items)

        savepoint.commit()
        return list(zip(items, rows, strict=True))

    def _insert_individually(
        self, items: Sequence[ReadingIn]
    ) -> list[tuple[ReadingIn, Reading | None]]:
        outcomes: list[tuple[ReadingIn, Reading | None]] = []

        for item in items:
            row = self._to_row(item)
            savepoint = self.session.begin_nested()
            try:
                self.session.add(row)
                self.session.flush()
            except IntegrityError:
                savepoint.rollback()
                self.session.expunge(row)
                outcomes.append((item, None))
                continue
            savepoint.commit()
            outcomes.append((item, row))

        return outcomes

    @staticmethod
    def _to_row(item: ReadingIn) -> Reading:
        return Reading(
            device_id=item.device_id,
            sensor_type=item.sensor_type.value,
            value=item.value,
            unit=item.unit,
            timestamp=item.timestamp,
        )

    # --- read paths --------------------------------------------------------

    def _apply_filters(self, statement: Select[Any], filters: ReadingFilters) -> Select[Any]:
        if filters.device_id is not None:
            statement = statement.where(Reading.device_id == filters.device_id)
        if filters.sensor_type is not None:
            statement = statement.where(Reading.sensor_type == filters.sensor_type.value)
        if filters.start is not None:
            statement = statement.where(Reading.timestamp >= filters.start)
        if filters.end is not None:
            # Exclusive upper bound, so adjacent windows tile without overlapping
            # and a reading is never counted in two of them.
            statement = statement.where(Reading.timestamp < filters.end)
        return statement

    def list_readings(
        self, filters: ReadingFilters, limit: int, offset: int
    ) -> tuple[list[Reading], int]:
        """Return one page of readings plus the total matching count."""
        total = self.session.scalar(self._apply_filters(select(func.count(Reading.id)), filters))

        rows = (
            self.session.execute(
                self._apply_filters(select(Reading), filters)
                # The id tiebreaker is load-bearing, not cosmetic: batch ingests
                # routinely produce identical timestamps, and without a
                # deterministic second sort key consecutive pages silently
                # overlap and drop rows.
                .order_by(Reading.timestamp.desc(), Reading.id.desc())
                .limit(limit)
                .offset(offset)
            )
            .scalars()
            .all()
        )

        return list(rows), int(total or 0)

    def aggregate(
        self, filters: ReadingFilters, group_by: Sequence[str]
    ) -> list[tuple[dict[str, str], int, float, float, float]]:
        """Aggregate in the database, never in Python — the rows never leave it."""
        columns = [getattr(Reading, field) for field in group_by]

        statement = self._apply_filters(
            select(
                *columns,
                func.count(Reading.id),
                func.min(Reading.value),
                func.max(Reading.value),
                func.avg(Reading.value),
            ),
            filters,
        ).group_by(*columns)

        rows = self.session.execute(statement.order_by(*columns)).all()

        results = []
        for row in rows:
            keys = {field: str(row[index]) for index, field in enumerate(group_by)}
            count, minimum, maximum, average = row[len(group_by) :]
            # Postgres returns AVG as Decimal and SQLite as float; coercing here
            # keeps the JSON identical on both.
            results.append(
                (keys, int(count), float(minimum), float(maximum), round(float(average), 4))
            )
        return results
