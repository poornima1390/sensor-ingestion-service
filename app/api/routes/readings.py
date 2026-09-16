"""Ingestion endpoint.

The handler deliberately contains no validation and no SQL. Its only job is
translating the outcome of a batch into an HTTP status code.
"""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.errors import error_body
from app.config import Settings, get_settings
from app.domain.ingest import ingest_items
from app.observability.logging import safe_extra
from app.schemas.query import Filters, GroupBy, Page
from app.schemas.reading import (
    BatchResponse,
    BatchSummary,
    ItemResult,
    PaginationMeta,
    ReadingListResponse,
    ReadingOut,
    StatGroup,
    StatsResponse,
    StatsWindow,
)
from app.storage.database import get_session
from app.storage.repository import ReadingRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/readings", tags=["readings"])


@router.post("", summary="Ingest one reading or a batch of readings")
def ingest_readings(
    # Typed as Any on purpose. Typing this as list[ReadingIn] would make Pydantic
    # reject the entire batch on one bad item, which is exactly what the
    # partial-success contract forbids. Malformed JSON still fails earlier, in
    # FastAPI's parser, and is mapped to 400.
    payload: Annotated[Any, Body()],
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> JSONResponse:
    if isinstance(payload, dict):
        return _ingest_single(payload, session)
    if isinstance(payload, list):
        return _ingest_batch(payload, session, settings)

    return JSONResponse(
        status_code=400,
        content=error_body(
            "bad_request",
            "Body must be a reading object or an array of reading objects.",
        ),
    )


def _ingest_single(payload: dict[str, Any], session: Session) -> JSONResponse:
    result = ingest_items([payload], ReadingRepository(session))[0]

    if result.status == "rejected":
        return JSONResponse(
            status_code=422,
            content=error_body(
                "validation_failed",
                "One or more fields are invalid.",
                [error.model_dump() for error in result.errors or []],
            ),
        )

    if result.status == "duplicate":
        return JSONResponse(
            status_code=409,
            content=error_body(
                "duplicate_reading",
                "A reading already exists for this device, sensor type and timestamp.",
            ),
        )

    session.commit()
    _log_outcome([result])
    # Per the contract, a single reading returns the bare stored record rather
    # than the batch envelope.
    return JSONResponse(
        status_code=201,
        content=result.reading.model_dump(mode="json") if result.reading else None,
    )


def _ingest_batch(payload: list[Any], session: Session, settings: Settings) -> JSONResponse:
    if not payload:
        return JSONResponse(
            status_code=422,
            content=error_body("empty_batch", "Batch must contain at least one reading."),
        )

    if len(payload) > settings.max_batch_size:
        return JSONResponse(
            status_code=413,
            content=error_body(
                "batch_too_large",
                f"Batch of {len(payload)} exceeds the maximum of "
                f"{settings.max_batch_size} readings.",
            ),
        )

    results = ingest_items(payload, ReadingRepository(session))
    session.commit()
    _log_outcome(results)

    summary = BatchSummary(
        received=len(results),
        created=sum(1 for r in results if r.status == "created"),
        duplicate=sum(1 for r in results if r.status == "duplicate"),
        rejected=sum(1 for r in results if r.status == "rejected"),
    )

    # The status line alone answers the common cases, so a client only has to
    # parse the envelope when the outcome was genuinely mixed.
    if summary.created == summary.received:
        status_code = 201
    elif summary.rejected == summary.received:
        status_code = 422
    else:
        status_code = 207

    body = BatchResponse(summary=summary, results=results)
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


def _log_outcome(results: list[ItemResult]) -> None:
    # Keys are prefixed because bare `created` collides with a reserved
    # LogRecord attribute; safe_extra is the backstop if that is ever forgotten.
    logger.info(
        "readings ingested",
        extra=safe_extra(
            readings_received=len(results),
            readings_created=sum(1 for r in results if r.status == "created"),
            readings_duplicate=sum(1 for r in results if r.status == "duplicate"),
            readings_rejected=sum(1 for r in results if r.status == "rejected"),
        ),
    )


@router.get("", response_model=ReadingListResponse, summary="List and filter readings")
def list_readings(
    filters: Filters,
    page: Page,
    session: Annotated[Session, Depends(get_session)],
) -> ReadingListResponse:
    rows, total = ReadingRepository(session).list_readings(filters, page.limit, page.offset)

    return ReadingListResponse(
        items=[ReadingOut.model_validate(row) for row in rows],
        pagination=PaginationMeta(limit=page.limit, offset=page.offset, total=total),
    )


@router.get("/stats", response_model=StatsResponse, summary="Aggregate readings")
def reading_stats(
    filters: Filters,
    grouping: GroupBy,
    session: Annotated[Session, Depends(get_session)],
) -> StatsResponse:
    groups = ReadingRepository(session).aggregate(filters, grouping)

    return StatsResponse(
        group_by=list(grouping),
        window=StatsWindow(start=filters.start, end=filters.end),
        groups=[
            StatGroup(key=key, count=count, min=minimum, max=maximum, avg=average)
            for key, count, minimum, maximum, average in groups
        ],
    )
