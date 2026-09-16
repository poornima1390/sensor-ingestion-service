"""Liveness and readiness probes.

These are two endpoints on purpose. A liveness probe that checks the database
turns a brief DB blip into a platform-wide restart storm: every instance fails
the probe at once, the platform kills them all, and a recoverable incident
becomes an outage. Liveness answers "is this process wedged?"; readiness answers
"should this instance receive traffic right now?".
"""

from fastapi import APIRouter, Response

from app.config import get_settings
from app.storage.database import check_database

router = APIRouter(tags=["health"])


@router.get("/healthz", summary="Liveness probe")
def healthz() -> dict[str, str]:
    settings = get_settings()
    return {
        "status": "ok",
        "service": settings.service_name,
        "version": settings.version,
    }


@router.get("/readyz", summary="Readiness probe")
def readyz(response: Response) -> dict[str, object]:
    database_ok = check_database()
    if not database_ok:
        response.status_code = 503
        return {"status": "not_ready", "checks": {"database": "unreachable"}}
    return {"status": "ready", "checks": {"database": "ok"}}
