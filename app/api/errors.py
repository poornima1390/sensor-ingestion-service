"""One error shape for every non-2xx response this service produces.

FastAPI's own validation errors are re-rendered through the same shape, so a
client never has to parse two different error formats from one API.
"""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.observability.logging import request_id_var

logger = logging.getLogger(__name__)


def error_body(
    error: str,
    message: str,
    detail: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the standard error payload. `detail` is omitted when empty."""
    body: dict[str, Any] = {"error": error, "message": message}
    if detail:
        body["detail"] = detail
    request_id = request_id_var.get()
    if request_id:
        body["request_id"] = request_id
    return body


# Maps an HTTP status to the machine-readable `error` code used when a handler
# raises HTTPException without supplying one.
# Errors meaning "this value could not be parsed at all" — a 400. Constraint
# failures on a value that *did* parse (over a maximum, negative, unknown enum)
# stay 422. Scoped to query parameters: request bodies reach our own per-item
# validation instead, which reports its own codes.
_PARSE_ERROR_TYPES = {
    "int_parsing",
    "int_type",
    "float_parsing",
    "bool_parsing",
    "datetime_parsing",
    "datetime_type",
    "datetime_from_date_parsing",
    "uuid_parsing",
}

_STATUS_CODES = {
    400: "bad_request",
    404: "not_found",
    409: "conflict",
    413: "payload_too_large",
    422: "validation_failed",
    503: "service_unavailable",
}


_CODE_MAP_QUERY = {
    "int_parsing": "invalid_integer",
    "int_type": "invalid_integer",
    "float_parsing": "invalid_number",
    "bool_parsing": "invalid_boolean",
    "datetime_parsing": "invalid_timestamp",
    "datetime_type": "invalid_timestamp",
    "datetime_from_date_parsing": "invalid_timestamp",
    "uuid_parsing": "invalid_uuid",
}


def _field_path(loc: tuple[Any, ...]) -> str:
    """Render a Pydantic error location as a dotted path, dropping the leading
    'body'/'query' marker that is noise to an API consumer."""
    parts = [str(part) for part in loc if part not in ("body", "query", "path")]
    return ".".join(parts) if parts else "body"


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        raw = exc.errors()

        # Unparseable JSON is a 400 ("I could not understand the request"),
        # not a 422 ("I understood it and it is wrong").
        if any(err.get("type") == "json_invalid" for err in raw):
            return JSONResponse(
                status_code=400,
                content=error_body(
                    "malformed_json",
                    "Request body is not valid JSON.",
                ),
            )

        unparseable = [
            err
            for err in raw
            if tuple(err.get("loc", ()))[:1] == ("query",) and err.get("type") in _PARSE_ERROR_TYPES
        ]
        if unparseable:
            return JSONResponse(
                status_code=400,
                content=error_body(
                    "invalid_query_parameter",
                    "One or more query parameters could not be parsed.",
                    [
                        {
                            "field": _field_path(err.get("loc", ())),
                            "code": _CODE_MAP_QUERY.get(str(err.get("type")), "invalid_value"),
                            "message": str(err.get("msg", "could not be parsed")),
                        }
                        for err in unparseable
                    ],
                ),
            )

        detail = [
            {
                "field": _field_path(err.get("loc", ())),
                "code": err.get("type", "invalid"),
                "message": err.get("msg", "invalid value"),
            }
            for err in raw
        ]
        return JSONResponse(
            status_code=422,
            content=error_body(
                "validation_failed",
                "One or more fields are invalid.",
                detail,
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        # A handler may raise HTTPException(detail={"error": ..., "message": ...})
        # to supply its own code; otherwise fall back to the status map.
        if isinstance(exc.detail, dict) and "error" in exc.detail:
            body = error_body(
                str(exc.detail["error"]),
                str(exc.detail.get("message", "")),
                exc.detail.get("detail"),
            )
        else:
            body = error_body(
                _STATUS_CODES.get(exc.status_code, "error"),
                str(exc.detail),
            )
        return JSONResponse(status_code=exc.status_code, content=body, headers=exc.headers)

    @app.exception_handler(Exception)
    async def _unhandled(_request: Request, exc: Exception) -> JSONResponse:
        # Full traceback to the logs, nothing internal to the client.
        logger.exception("unhandled exception", extra={"exception_type": type(exc).__name__})
        # This handler runs in ServerErrorMiddleware, outside the request
        # middleware, so it has to attach the correlation header itself — the
        # middleware re-raised before it got the chance.
        request_id = request_id_var.get()
        return JSONResponse(
            status_code=500,
            content=error_body(
                "internal_error",
                "An unexpected error occurred. The incident has been logged.",
            ),
            headers={"X-Request-ID": request_id} if request_id else None,
        )
