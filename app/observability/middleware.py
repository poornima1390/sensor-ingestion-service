"""Request-scoped logging and request-ID propagation."""

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.observability.logging import request_id_var, safe_extra

logger = logging.getLogger("app.request")

REQUEST_ID_HEADER = "X-Request-ID"

# Platform probes hit these on a short interval. Logging them at INFO buries the
# traffic that actually matters, so they drop to DEBUG.
_LOW_SIGNAL_PATHS = {"/healthz", "/readyz", "/metrics"}


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Honour an upstream ID when present so traces stitch together across
        # services; generate one otherwise.
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
        token = request_id_var.set(request_id)
        request.state.request_id = request_id

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "request failed",
                extra=safe_extra(
                    http_method=request.method,
                    http_path=request.url.path,
                    duration_ms=round((time.perf_counter() - started) * 1000, 2),
                ),
            )
            # Deliberately not resetting the ContextVar here. The 500 handler
            # runs in ServerErrorMiddleware, which sits *outside* this one in the
            # same task, so resetting would strip the request ID from exactly the
            # response that most needs it. Each request runs in its own task with
            # its own context, so nothing leaks between requests.
            raise

        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        response.headers[REQUEST_ID_HEADER] = request_id

        level = logging.DEBUG if request.url.path in _LOW_SIGNAL_PATHS else logging.INFO
        logger.log(
            level,
            "request",
            extra=safe_extra(
                http_method=request.method,
                http_path=request.url.path,
                status_code=response.status_code,
                duration_ms=duration_ms,
            ),
        )
        request_id_var.reset(token)
        return response
