"""Per-item validation.

This is the crux of the partial-success contract. Typing the request body as
`list[ReadingIn]` would make Pydantic reject the *entire* batch on the first bad
item — exactly the behaviour the requirements forbid. So the body is parsed
loosely and each item is validated on its own through this module.

Everything here is pure: no database, no request object, and the clock is
injected. That is what makes the rules unit-testable without starting the app.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from app.schemas.reading import ReadingIn


@dataclass(frozen=True)
class FieldError:
    field: str
    code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"field": self.field, "code": self.code, "message": self.message}


# Pydantic's built-in error types are implementation detail; these are the codes
# the API contract promises.
_CODE_MAP = {
    "missing": "required",
    "enum": "invalid_enum",
    "string_type": "invalid_type",
    "float_type": "invalid_type",
    "float_parsing": "invalid_number",
    "int_parsing": "invalid_number",
    "datetime_type": "invalid_timestamp",
    "datetime_parsing": "invalid_timestamp",
    "datetime_from_date_parsing": "invalid_timestamp",
}


def _field_path(loc: tuple[Any, ...]) -> str:
    parts = [str(part) for part in loc if part not in ("body",)]
    return ".".join(parts) if parts else "body"


def _to_field_error(raw: dict[str, Any]) -> FieldError:
    error_type = str(raw.get("type", "invalid"))
    return FieldError(
        field=_field_path(raw.get("loc", ())),
        code=_CODE_MAP.get(error_type, error_type),
        message=str(raw.get("msg", "invalid value")),
    )


def validate_reading(
    raw: Any,
    *,
    now: datetime | None = None,
) -> tuple[ReadingIn | None, list[FieldError]]:
    """Validate one reading.

    Returns `(reading, [])` on success and `(None, errors)` on failure. Never
    raises for invalid input — a bad item is a value to report, not an
    exception, because its siblings still have to be processed.
    """
    if not isinstance(raw, dict):
        return None, [FieldError("body", "invalid_object", "each reading must be a JSON object")]

    try:
        reading = ReadingIn.model_validate(raw, context={"now": now})
    except ValidationError as exc:
        return None, [_to_field_error(err) for err in exc.errors()]

    return reading, []
