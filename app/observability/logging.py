"""Structured JSON logging with request-ID propagation.

The request ID lives in a ContextVar rather than being threaded through every
function signature, so a log line emitted deep in the storage layer still
carries the ID of the request that caused it.
"""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# Standard LogRecord attributes; anything outside this set was passed by the
# caller via `extra=` and belongs in the JSON payload.
_RESERVED = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
    "taskName",
    # uvicorn attaches an ANSI-coloured duplicate of the message.
    "color_message",
}


def safe_extra(**fields: object) -> dict[str, object]:
    """Build a logging `extra` dict that cannot collide with LogRecord internals.

    logging.makeRecord raises KeyError when `extra` contains a reserved
    attribute name such as `created`, `module` or `name`. That turns a log line
    into a 500 on the request it was meant to describe, and it only fires when
    the level is actually enabled — so a suite that runs at WARNING will not
    catch it. Renaming rather than dropping keeps the value in the log.
    """
    return {(f"{key}_" if key in _RESERVED else key): value for key, value in fields.items()}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        request_id = request_id_var.get()
        if request_id:
            payload["request_id"] = request_id

        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


def configure_logging(level: str) -> None:
    """Route every logger through one JSON handler on stdout.

    uvicorn's access logger is silenced because our own middleware already emits
    one structured line per request; leaving both on double-logs every call.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())

    for name in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers = [handler]
        logger.propagate = False

    logging.getLogger("uvicorn.access").disabled = True
