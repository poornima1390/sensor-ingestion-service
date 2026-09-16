# Sensor Ingestion & Analytics API

Ingestion and analytics service for IoT sensor readings (temperature, humidity,
pressure, battery). Python 3.12 / FastAPI / SQLAlchemy, deployed to DigitalOcean
App Platform.

The full design — endpoint contracts, validation rules, error conventions and the
scaling path — lives in [Spec.md](Spec.md).

## Status

**Phase 3 — query and aggregation.** All four functional endpoints are live and
deployed. `/metrics` lands in Phase 4.

| Endpoint | Status |
|---|---|
| `GET /healthz` | ✅ live — liveness probe, no DB dependency |
| `GET /readyz` | ✅ live — readiness probe, checks the database |
| `POST /readings` | ✅ live — single or batch, partial success |
| `GET /readings` | ✅ live — filtering + pagination |
| `GET /readings/stats` | ✅ live — min/max/avg/count aggregation |
| `GET /metrics` | Phase 4 |

## Ingestion contract

`POST /readings` accepts either one reading object or an array of them.

A **single** reading returns the bare stored record on `201`, `422` if it fails
validation, `409` if it duplicates an existing reading.

A **batch** returns a per-item envelope, and the status line alone answers the
common cases so a client only parses the body when the outcome was mixed:

| Outcome | Status |
|---|---|
| every item stored | `201` |
| mixed, or all duplicates | `207 Multi-Status` |
| every item rejected | `422` |
| empty array | `422` |
| more than `MAX_BATCH_SIZE` items | `413` |

```jsonc
{
  "summary": {"received": 3, "created": 1, "duplicate": 1, "rejected": 1},
  "results": [
    {"index": 0, "status": "created",   "reading": {"id": 2, "...": "..."}},
    {"index": 1, "status": "duplicate", "reading": {"id": 1, "...": "..."}},
    {"index": 2, "status": "rejected",
     "errors": [{"field": "value", "code": "out_of_range",
                 "message": "pressure value 9999.0 outside plausible range 300.0..1100.0"}]}
  ]
}
```

Results correlate by **`index`**, not `device_id` — a batch legitimately contains
many readings from the same device, so the index is the only stable handle the
client has.

Valid items are stored even when siblings fail. That is the point of partial
success: one malformed reading from a flaky sensor must not cost a fleet-wide
batch its good data.

## Query

`GET /readings` filters on `device_id`, `sensor_type`, `start` and `end`, and
paginates with `limit` (default 50, max 1000) and `offset`.

```jsonc
{
  "items": [ /* newest first */ ],
  "pagination": {"limit": 50, "offset": 0, "total": 1284}
}
```

The time window is **`start` inclusive, `end` exclusive**, so adjacent windows
tile without counting a reading twice.

Results are ordered `timestamp DESC, id DESC`. The `id` tiebreaker is
load-bearing rather than cosmetic: batch ingests routinely produce many readings
sharing one timestamp, and ordering by timestamp alone leaves their relative
order undefined — consecutive pages then repeat some rows and silently skip
others. There is a test that pages through ten identical timestamps and asserts
every row is seen exactly once.

Offset pagination is a deliberate time-box choice; keyset pagination on
`(timestamp, id)` is the documented upgrade for when deep offsets start scanning.

`GET /readings/stats` aggregates `min`/`max`/`avg`/`count`, with `group_by`
accepting `device_id`, `sensor_type`, or both comma-separated.

```jsonc
{
  "group_by": ["sensor_type"],
  "window": {"start": null, "end": null},
  "groups": [
    {"key": {"sensor_type": "temperature"},
     "count": 3, "min": 10.0, "max": 30.0, "avg": 20.0}
  ]
}
```

**`group_by` defaults to `sensor_type`, not to a single global aggregate**,
because an average taken across temperature (°C), pressure (hPa) and battery (%)
is a number with no meaning. Defaulting to a per-type breakdown means the
endpoint cannot return nonsense by accident.

Aggregation runs as a SQL `GROUP BY`; the rows never leave the database. Postgres
returns `AVG` as `Decimal` and SQLite as `float`, so it is coerced and rounded in
the repository to keep the JSON identical on both.

### 400 vs 422 on query parameters

`400` means the value could not be parsed at all (`start=not-a-date`,
`limit=many`). `422` means it parsed and then failed a rule (`limit=5000`,
`offset=-1`, `sensor_type=vibration`, `start` later than `end`).

### Validation

| `sensor_type` | Plausible range | Canonical `unit` |
|---|---|---|
| `temperature` | −50 to 150 | `celsius` |
| `humidity` | 0 to 100 | `percent` |
| `pressure` | 300 to 1100 | `hpa` |
| `battery` | 0 to 100 | `percent` |

Timestamps are ISO-8601; naive values are assumed UTC, and anything more than 60
seconds in the future is rejected. The tolerance is deliberate — device clocks
drift, and rejecting a reading 200ms ahead of the server is a bug you find in
production rather than in tests.

A mismatched `unit` is **rejected, never converted**. Silently coercing
fahrenheit to celsius is how you get a dataset nobody trusts. Unknown fields are
ignored, so a firmware update adding one does not break ingestion fleet-wide.

Deduplication uses the natural key `(device_id, sensor_type, timestamp)` backed by
a unique index, so routine sensor retries do not skew the averages. It needs no
client cooperation — no idempotency key to send.

## Quickstart

```bash
make venv install     # creates ~/.venvs/sensor-ingestion
make test             # in-memory SQLite
make run              # http://localhost:8080/docs
```

> **The virtualenv lives in `~/.venvs`, not in this directory, and that is
> deliberate.** This project sits under `~/Documents`, which is iCloud-synced.
> iCloud evicts files to dataless stubs and writes hidden `.pth` files into
> package directories, which breaks editable installs and makes imports hang
> rather than fail. Keeping the venv outside the synced tree avoids all of it.

## Configuration

Everything is environment-driven; there are no hardcoded values at the call site.

| Variable | Default | Purpose |
|---|---|---|
| `PORT` | `8080` | Injected by App Platform |
| `LOG_LEVEL` | `INFO` | Set `DEBUG` to include probe traffic in the logs |
| `DATABASE_URL` | `sqlite:///./.data/readings.db` | The single line that switches SQLite → Postgres |
| `MAX_BATCH_SIZE` | `1000` | Ingest cap (enforced in Phase 2) |

`DATABASE_URL` is normalised at startup: DigitalOcean emits `postgresql://`, and
SQLAlchemy 2 requires an explicit driver, so it is rewritten to
`postgresql+psycopg://`. Query parameters such as `?sslmode=require` are preserved.

## Layout

```
app/
  main.py                  app factory, lifespan, middleware wiring
  config.py                pydantic-settings
  api/routes/health.py     /healthz, /readyz
  api/errors.py            the single error shape + handlers
  domain/                  validation rules            (Phase 2)
  schemas/                 Pydantic wire contracts     (Phase 2)
  storage/database.py      engine, session, readiness check
  observability/           JSON logging, request-ID middleware
tests/unit/                pure logic, no app
tests/integration/         real requests through the app
```

Handlers are sync `def`, not `async def`: the database drivers here are blocking,
and blocking calls inside `async def` stall the event loop and serialise the whole
process. Sync handlers run in FastAPI's threadpool, so a `Session` is created per
request via a dependency and never shared across threads.

## Testing

```bash
make test      # in-memory SQLite — the fast inner loop
make test-pg   # same suite against Postgres (starts it on :5440 for you)
```

CI runs both. Testing only on SQLite while deploying to Postgres produces a green
run that does not mean anything, so the suite is exercised on both dialects and
the ORM is kept dialect-neutral.

## Deployment

Containerised and deployed to DigitalOcean App Platform from [.do/app.yaml](.do/app.yaml).
The spec attaches a dev-tier Postgres and injects its connection string, so no
credentials exist in the image or the repo.

```bash
make build      # docker build
make deploy     # push the spec to App Platform
```

App Platform's health check points at `/healthz`, **not** `/readyz`. A restart
probe aimed at a dependency turns a brief database blip into a fleet-wide restart
storm; liveness asks "is this process wedged?", readiness asks "should this
instance take traffic?".

## What would change for production

- **Alembic migrations.** The schema is currently created with `create_all()` on
  startup, which is fine for a greenfield single-table service and unacceptable the
  moment the schema must change without dropping data.
- **A production-tier database.** The dev-tier Postgres has no standby and no
  connection pooler.
- **Auth on ingest.** An unauthenticated `POST /readings` lets anyone poison the
  fleet's analytics. See [Spec.md](Spec.md) §6.
- **Queue-backed ingestion** at roughly 100x load, plus time-partitioned storage
  and pre-aggregated rollups so `/readings/stats` stops scanning raw rows.
