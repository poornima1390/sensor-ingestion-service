"""Prometheus collectors.

Registered on the default registry at import time, so they are created once per
process no matter how many app instances a test session builds.
"""

from prometheus_client import Counter, Histogram

# Label used when no route matched. Without it the raw URL would become the
# label value, and a scan for /wp-admin.php, /.env and friends would mint a new
# time series per probe — an unbounded-cardinality memory leak dressed up as
# observability.
UNMATCHED_PATH = "<unmatched>"

REQUESTS = Counter(
    "http_requests_total",
    "HTTP requests by method, route template and status code.",
    ["method", "path", "status"],
)

REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency by method and route template.",
    ["method", "path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

READINGS_INGESTED = Counter(
    "readings_ingested_total",
    "Readings by sensor type and ingest outcome.",
    ["sensor_type", "status"],
)

# received_at - timestamp. This is the metric that makes the difference between
# "measured" and "received" visible: a fleet going offline and replaying its
# buffer shows up here long before it shows up in the data.
INGEST_LAG = Histogram(
    "readings_ingest_lag_seconds",
    "Delay between when a reading was measured and when it was received.",
    buckets=(1.0, 5.0, 15.0, 60.0, 300.0, 900.0, 3600.0, 21600.0, 86400.0),
)


def observe_request(method: str, path: str, status: int, duration_seconds: float) -> None:
    REQUESTS.labels(method=method, path=path, status=str(status)).inc()
    REQUEST_DURATION.labels(method=method, path=path).observe(duration_seconds)
