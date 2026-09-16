"""Prometheus scrape endpoint."""

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

router = APIRouter(tags=["observability"])


@router.get("/metrics", summary="Prometheus metrics", include_in_schema=False)
def metrics() -> Response:
    # Counters are per-instance. With more than one App Platform instance the
    # scrape config has to target them individually and aggregate at query time;
    # a single load-balanced endpoint would return one arbitrary instance's view.
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
