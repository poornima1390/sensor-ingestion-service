"""Batch ingestion orchestration.

Lives outside the route so the partial-success logic — which is the interesting
part of this service — can be tested without going through HTTP, and so the
handler is left with nothing but status-code selection.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from app.domain.validation import validate_reading
from app.schemas.reading import ItemError, ItemResult, ReadingIn, ReadingOut
from app.storage.models import Reading
from app.storage.repository import NaturalKey, ReadingRepository, natural_key


def _key_of(reading: ReadingIn) -> NaturalKey:
    return natural_key(reading.device_id, reading.sensor_type, reading.timestamp)


def ingest_items(
    raw_items: Sequence[Any],
    repository: ReadingRepository,
    *,
    now: datetime | None = None,
) -> list[ItemResult]:
    """Validate and store a batch, returning one result per input item.

    Valid items are stored even when their siblings fail. That is the whole
    point of partial success: one malformed reading from a flaky sensor must not
    cost a fleet-wide batch its good data.
    """
    now = now or datetime.now(UTC)

    results: dict[int, ItemResult] = {}
    valid: list[tuple[int, ReadingIn]] = []

    for index, raw in enumerate(raw_items):
        reading, errors = validate_reading(raw, now=now)
        if reading is None:
            results[index] = ItemResult(
                index=index,
                status="rejected",
                errors=[ItemError(**error.as_dict()) for error in errors],
            )
        else:
            valid.append((index, reading))

    if valid:
        _store(valid, repository, results)

    return [results[index] for index in sorted(results)]


def _store(
    valid: list[tuple[int, ReadingIn]],
    repository: ReadingRepository,
    results: dict[int, ItemResult],
) -> None:
    stored_by_key: dict[NaturalKey, Reading] = repository.find_existing(
        [reading for _, reading in valid]
    )

    to_insert: list[ReadingIn] = []
    insert_indexes: list[int] = []
    duplicate_indexes: dict[int, NaturalKey] = {}
    claimed: set[NaturalKey] = set()

    for index, reading in valid:
        key = _key_of(reading)
        # `claimed` catches the case where one batch contains the same reading
        # twice: the first occurrence is stored, the rest are duplicates of it.
        if key in stored_by_key or key in claimed:
            duplicate_indexes[index] = key
            continue
        claimed.add(key)
        to_insert.append(reading)
        insert_indexes.append(index)

    raced: dict[int, NaturalKey] = {}
    for index, (item, row) in zip(insert_indexes, repository.insert_many(to_insert), strict=True):
        if row is None:
            raced[index] = _key_of(item)
            continue
        stored_by_key[_key_of(item)] = row
        results[index] = ItemResult(
            index=index, status="created", reading=ReadingOut.model_validate(row)
        )

    if raced:
        # Another writer won the race; re-read so the client still gets the
        # stored record rather than a bare "duplicate" with no body.
        winners = repository.find_existing([reading for index, reading in valid if index in raced])
        stored_by_key.update(winners)
        duplicate_indexes.update(raced)

    for index, key in duplicate_indexes.items():
        existing = stored_by_key.get(key)
        results[index] = ItemResult(
            index=index,
            status="duplicate",
            reading=ReadingOut.model_validate(existing) if existing is not None else None,
        )
