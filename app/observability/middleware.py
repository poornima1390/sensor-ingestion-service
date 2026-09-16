"""Request-scoped logging and request-ID propagation."""

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.observability.logging import request_id_var, safe_extra
from app.observability.metrics import UNMATCHED_PATH, observe_request

logger = logging.getLogger("app.request")

REQUEST_ID_HEADER = "X-Request-ID"

# Platform probes hit these on a short interval. Logging them at INFO buries the
# traffic that actually matters, so they drop to DEBUG.
_LOW_SIGNAL_PATHS = {"/healthz", "/readyz", "/metrics"}


def _route_template(request: Request) -> str:
    """The matched route's template, not the raw URL.

    Metric labels must come from a bounded set. Using request.url.path would mint
    a new time series for every unmatched URL a scanner tries, which is an
    unbounded-cardinality leak rather than a metric.
    """
    route = request.scope.get("route")
    return getattr(route, "path", None) or UNMATCHED_PATH


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
            elapsed = time.perf_counter() - started
            # A crashed request is still a request; leaving it out of the metrics
            # makes an outage look like a traffic drop.
            observe_request(request.method, _route_template(request), 500, elapsed)
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

        elapsed = time.perf_counter() - started
        duration_ms = round(elapsed * 1000, 2)
        observe_request(request.method, _route_template(request), response.status_code, elapsed)
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
