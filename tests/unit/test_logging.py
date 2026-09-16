"""Unit tests for the structured-logging helpers."""

import logging

from app.observability.logging import JsonFormatter, safe_extra


def test_safe_extra_passes_ordinary_keys_through() -> None:
    assert safe_extra(device_id="sensor-042", count=3) == {
        "device_id": "sensor-042",
        "count": 3,
    }


def test_safe_extra_renames_reserved_logrecord_attributes() -> None:
    # `created` is the LogRecord's creation time; passing it via extra makes
    # logging.makeRecord raise KeyError and turns a log line into a 500.
    assert safe_extra(created=1, module="x", name="y") == {
        "created_": 1,
        "module_": "x",
        "name_": "y",
    }


def test_reserved_extra_keys_do_not_raise_when_logged() -> None:
    logger = logging.getLogger("test.safe_extra")
    logger.setLevel(logging.INFO)

    # The regression: this raised KeyError before safe_extra existed.
    logger.info("outcome", extra=safe_extra(created=1, duplicate=0))


def test_formatter_emits_parseable_json_with_custom_fields() -> None:
    import json

    record = logging.LogRecord(
        name="app.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    record.readings_created = 2

    payload = json.loads(JsonFormatter().format(record))

    assert payload["message"] == "hello"
    assert payload["level"] == "INFO"
    assert payload["readings_created"] == 2
    assert payload["timestamp"].endswith("Z")
