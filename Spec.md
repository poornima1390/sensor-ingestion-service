# SPEC.md

---

## 1. Service Summary

# Practice Challenge: Sensor Ingestion & Analytics API

A self-contained mock problem sized to a 3-hour build + 45-min architecture review, mirroring the DigitalOcean Interview Day format. Time yourself for real — the clock pressure is part of what you're practicing.

---

## Scenario

A fleet-monitoring client has thousands of IoT sensors (temperature, humidity, pressure, battery) reporting readings over HTTP. You're building the ingestion and analytics service that receives, validates, stores, and summarizes this data.

## Functional Requirements

**1. `POST /readings`** — ingest one reading or a batch (array) of readings.

Each reading:
```json
{
  "device_id": "sensor-042",
  "sensor_type": "temperature",
  "value": 22.5,
  "unit": "celsius",
  "timestamp": "2026-09-13T14:00:00Z"
}
```

Validation rules:
- `device_id`: required, non-empty
- `sensor_type`: required, one of `temperature | humidity | pressure | battery`
- `value`: required numeric, plausible range per type (you decide and document the ranges, e.g. temperature -50 to 150°C)
- `timestamp`: required ISO-8601, rejected if in the future
- `unit`: optional, but must be consistent with `sensor_type` if present

Behavior:
- Single valid reading → `201` with the stored record.
- Batch → partial success is allowed. Return per-item results (which succeeded, which failed and why) rather than failing the whole batch on one bad item.

**2. `GET /readings`** — list/query with filters: `device_id`, `sensor_type`, `start`, `end` (time range), plus pagination (`limit`/`offset` or cursor).

**3. `GET /readings/stats`** — aggregation endpoint: min/max/avg value, grouped by `device_id` and/or `sensor_type`, over an optional time window.

**4. `GET /healthz`** — health check suitable for a platform liveness probe.

**Storage**: in-memory or embedded (SQLite) is fine — don't burn your window standing up Postgres. Note in your README what you'd swap in for production and why.

## Non-Functional Bar (this is what's actually being graded)

| Area | What "good" looks like |
|---|---|
| **Engineering quality** | Clear separation of layers (handlers / validation / storage), no God-file, meaningful error messages with correct HTTP status codes (400 vs 422 vs 500) |
| **Testing** | Unit tests on validation logic, integration tests hitting the actual endpoints, at least one test for the partial-batch-failure path |
| **Automation** | A `Makefile` or scripts for `build/test/lint/run`, a `Dockerfile`, ideally a minimal CI config (GitHub Actions running tests on push) |
| **Operational excellence** | Structured (JSON) logs with request IDs, config via env vars (port, log level), graceful shutdown on SIGTERM, thread-safe storage access, a documented scaling path |

## Deployment Step (don't skip this in practice)

- Containerize with Docker.
- Deploy to a DigitalOcean App Platform app or a Droplet running the container.
- Confirm with a `curl` from your own machine (not localhost) that it's live.
- Budget real time for this — auth, builds, and DNS propagation eat minutes you don't expect.

## Suggested Time Budget (3 hrs)

1. **0:00–0:15** — Read reqs, sketch the architecture, scaffold the repo, decide storage/framework.
2. **0:15–1:45** — Core build: ingest + validation + storage + query/stats endpoints.
3. **1:45–2:15** — Tests: unit + integration.
4. **2:15–2:45** — Dockerize and deploy to DigitalOcean.
5. **2:45–3:00** — README, logging polish, buffer for whatever broke.

## Prep for the Architecture & Decision Review (45 min)

Practice narrating, out loud, in under 5 minutes each:
- Why this language/framework, and what you gave up by choosing it.
- Why this storage choice, and the first thing you'd change at 10x scale.
- How partial-batch-failure is handled and why you designed the contract that way.
- One thing you'd do differently with a full week instead of 3 hours.
- Where the service would break first under load, and what you'd add to see it happen (metrics, tracing, alerting).

## Self-Check Before You Call It Done

- [ ] Can I `curl` the deployed URL right now and get a real response?
- [ ] Does a malformed request return a clear 4xx with a useful message, not a 500?
- [ ] Do my tests actually run with one command?
- [ ] Can I explain every line of my Dockerfile?
- [ ] Is there a single env var that changes behavior (port/log level) I can demo live?

---

**Stretch goals** if you finish early: `/metrics` endpoint (Prometheus format), basic rate limiting, idempotency key support on ingestion, or a `docker-compose.yml` for local dev.

## 2. Tech Stack

- **Language / framework:** Python 3.12, FastAPI
- **Validation:** Pydantic v2 models (`schemas/`) — kept separate from persistence models
- **Data access:** SQLAlchemy 2.x ORM (**not** SQLModel — see tradeoff below)
- **Database:** SQLite for local dev and tests → DigitalOcean Managed Postgres for the deployed app, selected by a single `DATABASE_URL` env var
- **Testing:** pytest + FastAPI `TestClient`
- **Config:** `pydantic-settings`
- **Metrics:** `prometheus-client`
- **Driver:** `psycopg[binary]` (Postgres), stdlib `sqlite3` via SQLAlchemy (local)
- **Deployment target:** DigitalOcean App Platform (Dockerfile build)

### Stack tradeoffs (for the review)

**Why FastAPI, and what it cost.** Pydantic validation, OpenAPI docs, and `TestClient`
integration tests come free — three items straight off the grading rubric, in a 3-hour box.
What I gave up: a Go service would ship as a single static binary in a ~15MB image with a
cleaner concurrency story, and DigitalOcean is a Go shop. But Go means hand-rolling
validation and losing generated API docs — 30–45 minutes I didn't have.

**Why SQLAlchemy over SQLModel.** SQLModel collapses the API contract and the persistence
model into one class. That's less code, but "clear separation of layers" is the first line
of the rubric. Separate `schemas/reading.py` (Pydantic, the wire contract) and
`storage/models.py` (SQLAlchemy, the table) means the two can change independently — adding
an internal column doesn't leak into the public API.

**Why sync `def` handlers, not `async def`.** The DB drivers here are blocking; blocking
calls inside `async def` stall the event loop and serialize the whole process. Sync handlers
run in FastAPI's threadpool and are safe by default. The upgrade path, if throughput
demanded it, is `async def` + `asyncpg` with an async session — a change I'd make
deliberately, not by accident.

**Why real Postgres for the deployed app** even though the prompt says SQLite is fine:
App Platform filesystems are ephemeral and a SQLite file would diverge per instance the
moment you scale past one. Managed Postgres costs ~20 minutes of the deploy budget but buys
an honest multi-instance story. Mitigation for the clock: **provision the database at 0:15,
not at 2:15** — the several-minute wait overlaps the build instead of landing on the
critical path.

**Dialect drift is the risk this creates.** Testing on SQLite while running on Postgres is a
good way to get a green suite that lies. Mitigations: keep the ORM dialect-neutral (no
`ON CONFLICT`, no Postgres-only column types), normalize timestamps to UTC in application
code rather than relying on the column type, and have CI run the suite twice — once on
in-memory SQLite (fast local loop) and once against a Postgres service container.

## 3. Data Model(s)

**`Reading`** — table `readings`

| Field | Type | Required | Constraints |
|---|---|---|---|
| `id` | int | yes | primary key, autoincrement |
| `device_id` | str(64) | yes | non-empty after strip; indexed |
| `sensor_type` | enum(str) | yes | one of `temperature`, `humidity`, `pressure`, `battery` |
| `value` | float | yes | finite (no NaN/Inf); range enforced per `sensor_type` — see §5 |
| `unit` | str(16) | no | if present, must match the canonical unit for `sensor_type`; stored lowercased |
| `timestamp` | datetime (UTC) | yes | ISO-8601; normalized to UTC; not more than 60s in the future |
| `received_at` | datetime (UTC) | yes | server-assigned at insert; never client-supplied |

**Indexes**

- `ix_readings_device_timestamp` on `(device_id, timestamp)` — serves the most common query filter
- `ix_readings_type_timestamp` on `(sensor_type, timestamp)` — serves `/readings/stats`
- `uq_readings_natural_key` **UNIQUE** on `(device_id, sensor_type, timestamp)` — dedup key

**Design notes**

- *Integer PK, not UUID.* Autoincrement gives monotonic ordering, which makes the `id` a
  natural tiebreaker for stable pagination today and a keyset cursor later. At the scale
  where writes are sharded, UUIDv7 is the swap.
- *`received_at` is separate from `timestamp`.* Sensor clocks drift and devices buffer
  readings while offline, so "when it was measured" and "when we got it" are different facts.
  Keeping both makes ingestion lag measurable instead of invisible.
- *Timezone handling is done in the app layer.* SQLite has no real tz-aware type and
  Postgres does; normalizing to UTC before persisting makes both dialects behave identically
  and keeps the test suite honest.

## 4. Endpoints

### `POST /readings`

- **Purpose:** Ingest a single reading or a batch, with per-item partial success.
- **Request:** JSON body — either an object or an array of objects (max 1000 items).
  Optional `X-Request-ID` header, echoed on the response.
- **Response (success):**
  - Single object → `201` with the bare stored record.
  - Array, all items created → `201` with the results envelope.
  - Array, mixed outcomes (or all duplicates) → `207 Multi-Status` with the results envelope.
- **Response (error cases):**
  - `400` — body is not valid JSON, or is neither an object nor an array.
  - `409` — single object whose natural key already exists.
  - `413` — array larger than `MAX_BATCH_SIZE` (default 1000).
  - `422` — single object that fails validation; or an array where **every** item failed validation.
  - `500` — unhandled error.
- **Notes:**
  - Results envelope:
    ```json
    {
      "summary": {"received": 3, "created": 1, "duplicate": 1, "rejected": 1},
      "results": [
        {"index": 0, "status": "created", "reading": { "...": "..." }},
        {"index": 1, "status": "duplicate", "reading": { "...": "..." }},
        {"index": 2, "status": "rejected",
         "errors": [{"field": "value", "code": "out_of_range",
                     "message": "pressure value 9999 outside plausible range 300..1100"}]}
      ]
    }
    ```
  - **Results correlate by `index`, not by `device_id`** — a batch legitimately contains many
    readings from the same device, so the index is the only stable handle the client has.
  - Item `status` is one of `created`, `duplicate`, `rejected`.
  - Valid items are committed even when siblings fail. That is the whole point of partial
    success: a single malformed reading from one flaky sensor must not cost a fleet-wide
    batch its good data.
  - **Why the status code varies:** the caller can branch on the status line alone for the
    common cases (`201` = everything landed, `422` = nothing landed) and only parse the
    envelope when it's `207`. The cost is that `207` is unusual enough that some HTTP clients
    treat it as an error; that's a documented contract, not a surprise.

### `GET /readings`

- **Purpose:** List readings with filtering and pagination.
- **Request:** query params — `device_id`, `sensor_type`, `start`, `end` (ISO-8601, `start`
  inclusive / `end` exclusive), `limit` (default 50, max 1000), `offset` (default 0).
- **Response (success):** `200`
  ```json
  {
    "items": [{ "...": "..." }],
    "pagination": {"limit": 50, "offset": 0, "total": 1284}
  }
  ```
- **Response (error cases):**
  - `400` — unparseable `start`/`end`, or non-integer `limit`/`offset`.
  - `422` — `limit` over the max, negative `offset`, unknown `sensor_type`, or `start > end`.
- **Notes:**
  - Sort order is `timestamp DESC, id DESC`. **The `id` tiebreaker is load-bearing** — batch
    ingests produce many identical timestamps, and without a deterministic second sort key
    consecutive pages silently overlap and drop rows.
  - No filter matches → `200` with an empty `items` array. An empty result set is a
    successful query, not a `404`.
  - Offset pagination is a deliberate time-box choice; see §9.

### `GET /readings/stats`

- **Purpose:** min / max / avg / count aggregation over an optional window.
- **Request:** query params — `group_by` (comma-separated: `sensor_type`, `device_id`;
  **defaults to `sensor_type`**), plus the same `device_id`, `sensor_type`, `start`, `end`
  filters as `GET /readings`.
- **Response (success):** `200`
  ```json
  {
    "group_by": ["sensor_type"],
    "window": {"start": "2026-09-13T00:00:00Z", "end": null},
    "groups": [
      {"key": {"sensor_type": "temperature"},
       "count": 812, "min": -4.2, "max": 38.9, "avg": 21.4471}
    ]
  }
  ```
- **Response (error cases):**
  - `422` — `group_by` contains a field that isn't groupable; invalid window.
- **Notes:**
  - **`group_by` defaults to `sensor_type` rather than returning one global aggregate**,
    because an average taken across temperature (°C), pressure (hPa) and battery (%) is a
    number with no meaning. Defaulting to a per-type breakdown means the endpoint can't
    return nonsense by accident.
  - `avg` is rounded to 4 decimal places; `min`/`max` are returned as stored.
  - Aggregation runs as SQL `GROUP BY` in the database, not in Python — the rows never leave
    the DB.

### `GET /healthz`

- **Purpose:** Liveness probe for App Platform.
- **Request:** none.
- **Response (success):** `200` — `{"status": "ok"}`
- **Response (error cases):** none by design — if the process can answer, it is alive.
- **Notes:** **Deliberately does not touch the database.** A liveness probe that checks a
  dependency turns a brief DB blip into a platform-wide restart storm, which takes a
  recoverable incident and makes it worse. Dependency health belongs in `/readyz`.

### `GET /readyz`

- **Purpose:** Readiness probe — should this instance receive traffic?
- **Request:** none.
- **Response (success):** `200` — `{"status": "ready", "checks": {"database": "ok"}}`
- **Response (error cases):** `503` — `{"status": "not_ready", "checks": {"database": "unreachable"}}`
- **Notes:** Runs `SELECT 1` against the pool with a short timeout.

### `GET /metrics`

- **Purpose:** Prometheus scrape endpoint (stretch goal, in scope).
- **Request:** none.
- **Response (success):** `200`, `text/plain; version=0.0.4`
- **Notes:** Exposes `readings_ingested_total{sensor_type,status}`,
  `http_requests_total{method,path,status}`, an `http_request_duration_seconds`
  histogram, and `readings_ingest_lag_seconds` (`received_at - timestamp`).
  The `path` label is the matched **route template**, never the raw URL: raw
  paths would mint a time series per scanned URL, so unmatched requests collapse
  into a single `<unmatched>` label. Per-instance counters — with multiple App
  Platform instances the scrape target must aggregate across them.

## 5. Validation Rules

### Plausible ranges and canonical units

| `sensor_type` | Valid range (inclusive) | Canonical `unit` |
|---|---|---|
| `temperature` | −50 to 150 | `celsius` |
| `humidity` | 0 to 100 | `percent` |
| `pressure` | 300 to 1100 | `hpa` |
| `battery` | 0 to 100 | `percent` |

Ranges are chosen to bracket physically plausible environmental readings — temperature
covers industrial enclosures, pressure covers sea level through high altitude, and the two
percentage types are bounded by definition.

### Field rules

- `device_id` is required, must be a string, non-empty after stripping whitespace, max 64
  characters.
- `sensor_type` is required and must be one of the four enum values (compared lowercase).
- `value` is required, must be a finite number (`NaN` and `Infinity` are rejected), and must
  fall within the range for its `sensor_type`.
- `timestamp` is required and must parse as ISO-8601. A naive timestamp is **assumed UTC**
  and normalized; an offset-bearing timestamp is converted to UTC.
- `timestamp` is rejected if more than **60 seconds** in the future. The tolerance is
  deliberate: IoT device clocks drift, and rejecting a reading that is 200ms ahead of the
  server is a bug you find in production, not in tests.
- `unit` is optional. If present, it is compared case-insensitively against the canonical
  unit for the `sensor_type` and rejected on mismatch. It is **not** converted — see §9.
- Unknown fields in a reading are ignored rather than rejected, so that a device firmware
  adding a field doesn't break ingestion.
- A batch may not exceed `MAX_BATCH_SIZE` (default 1000) items → `413`.

### Per-item validation is an architectural requirement, not a detail

Typing the request body as `list[Reading]` would make Pydantic reject the **entire batch**
on the first bad item — exactly the behavior the requirements forbid. So:

1. The body is parsed loosely as `dict | list[dict]`.
2. Each item is passed individually to a pure function,
   `validate_reading(raw: dict) -> Reading | list[FieldError]`, which catches
   `ValidationError` per item.
3. Only after validation does anything touch the database.

Keeping `validate_reading` pure — no DB, no request object, no clock except an injected
`now` — is what makes the range and timestamp rules unit-testable without spinning up the
app.

### Error status mapping for validation

- Requests with a body that isn't valid JSON → `400`.
- Requests that are well-formed JSON but semantically invalid → `422` with field-level
  detail. FastAPI's default `RequestValidationError` handler is overridden so framework-level
  errors emit the same shape as ours.

## 6. Auth

- **Mechanism:** **None.** The prompt does not require authentication, so it is explicitly
  out of scope rather than overlooked.
- **Protected endpoints:** none.
- **What production would need:** ingest is the sensitive surface — an unauthenticated
  `POST /readings` lets anyone poison the fleet's analytics. The realistic production answer
  is a per-device credential at an edge gateway (mTLS or a signed device token), with the
  service trusting an authenticated `device_id` rather than accepting whatever the body
  claims. Read endpoints would sit behind a standard bearer token with tenant scoping.

## 7. Error Handling Conventions

**Standard error shape** — every non-2xx response from this service, including FastAPI's own
validation errors:

```json
{
  "error": "validation_failed",
  "message": "One or more fields are invalid.",
  "detail": [
    {"field": "sensor_type", "code": "invalid_enum",
     "message": "must be one of: temperature, humidity, pressure, battery"}
  ],
  "request_id": "0f9c1f0a-3b2e-4a1d-9c77-6a2f1b0e5d33"
}
```

`error` is a stable machine-readable code; `message` is for humans; `detail` is omitted when
there is nothing field-specific to say. `request_id` is always present so a user-reported
failure can be grepped straight out of the logs.

**Status code mapping**

| Code | When |
|---|---|
| `400` | Malformed JSON, body neither object nor array, unparseable query param |
| `409` | Single reading whose `(device_id, sensor_type, timestamp)` already exists |
| `413` | Batch exceeds `MAX_BATCH_SIZE` |
| `422` | Well-formed JSON that fails schema or semantic validation |
| `207` | Batch with mixed per-item outcomes (not an error — see §4) |
| `503` | `/readyz` when the database is unreachable |
| `500` | Unhandled exception — should be rare |

**The 400 vs 422 line:** `400` means "I could not understand the request at all"; `422`
means "I understood it and it is wrong." Bad JSON syntax is `400`. A perfectly-formed reading
with `sensor_type: "humidty"` is `422`.

**`500` handling:** a global exception handler logs the full traceback with the request ID at
`ERROR`, and returns a generic message with no internal detail. Stack traces go to logs,
never to clients.

## 8. Operational Concerns

- [x] `/healthz` returning `200` for liveness (no DB call) and `/readyz` returning `200`
      when the DB connection is healthy, `503` when it isn't
- [x] Structured (JSON) logging for requests and errors
- [x] Config via environment variables — no hardcoded values
- [x] Graceful shutdown on SIGTERM
- [x] Request ID propagation

**Logging.** One JSON line per request emitted by middleware: `request_id`, `method`, `path`,
`status`, `duration_ms`, plus `device_count` on ingest. The request ID is taken from an
inbound `X-Request-ID` header when present (so it stitches to an upstream trace) or generated,
stored in a `contextvar` so handler and storage logs carry it without threading it through
every signature, and echoed back on the response header.

**Config** (`pydantic-settings`, all env-driven):

| Var | Default | Purpose |
|---|---|---|
| `PORT` | `8080` | App Platform injects this |
| `LOG_LEVEL` | `INFO` | The live-demo env var — flip to `DEBUG` and show log output change |
| `DATABASE_URL` | `sqlite:///./readings.db` | The one line that switches SQLite → Postgres |
| `MAX_BATCH_SIZE` | `1000` | Ingest cap |

**Graceful shutdown.** FastAPI's `lifespan` disposes the SQLAlchemy engine on shutdown;
uvicorn stops accepting new connections on SIGTERM and drains in-flight requests before the
process exits, so a deploy doesn't truncate a batch mid-insert.

**Connection handling.** SQLAlchemy `QueuePool` with `pool_pre_ping=True` — managed Postgres
drops idle connections, and without pre-ping the first request after an idle period fails
with a stale-connection error. `sslmode=require` on the DO connection string.

**Thread safety.** Handlers are sync and run in a threadpool, so storage access must be
thread-safe: one `Session` per request via a FastAPI dependency, never a module-level shared
session. Concurrency correctness on the write path is enforced by the database's unique
constraint rather than by application-level locking.

**Scaling note (for the architecture discussion):** the first bottleneck is ingest write
throughput — per-item `INSERT` round trips exhaust the connection pool long before Postgres
itself is stressed, so the symptom is request queuing and rising p99 latency, not database
CPU. First change at 10x: batch the inserts into a single `executemany` per request and size
the pool to the instance count. At 100x: decouple ingest from storage — the endpoint
validates and publishes to a queue, workers write in bulk — then time-partition `readings` and
maintain pre-aggregated rollups so `/stats` reads summaries instead of scanning raw rows.
The instrumentation that makes this visible before users report it: the request-duration
histogram, a pool-saturation gauge, and an ingest-lag metric derived from
`received_at - timestamp`.

## 9. Out of Scope (explicitly deferred)

- **Auth** — the prompt doesn't require it. See §6 for what production would need.
- **Rate limiting** — in-process limiting gives a false sense of protection the moment you
  run more than one instance; real throttling belongs at the edge/load balancer, and doing it
  badly here would be worse than not doing it.
- **Cursor pagination** — offset is correct at this data size. Keyset pagination on
  `(timestamp, id)` is the documented upgrade for when deep offsets start scanning.
- **Alembic migrations** — schema is created with `create_all()` on startup. Fine for a
  greenfield single-table service; Alembic is the first thing added the moment the schema
  has to change without dropping data.
- **Unit conversion** — a `fahrenheit` temperature is rejected, not converted. Silent unit
  coercion is how you get a dataset nobody trusts; explicit rejection is the safer default.
- **Per-device calibration / schema** — all devices of a type share one range table.
- **Backfill and deletion endpoints** — no `DELETE` or `PATCH`; readings are immutable facts.
- **Access control on `/metrics`** — it is served on the public ingress, which
  leaks traffic shape and fleet size. In production it belongs behind network
  policy or on an internal-only listener.

## 10. Verification / Definition of Done

- [ ] `POST /readings` with a single valid reading returns `201` and the stored record
- [ ] `POST /readings` with `sensor_type: "humidty"` returns `422` naming the field and the allowed values
- [ ] `POST /readings` with a timestamp 1 hour in the future returns `422`; one 5 seconds ahead succeeds
- [ ] `POST /readings` with a 3-item batch (1 valid, 1 out-of-range, 1 duplicate) returns `207` with correct per-index statuses and a matching `summary`
- [ ] `POST /readings` with a batch where every item is invalid returns `422`
- [ ] Re-posting an identical reading returns `409` (single) / `duplicate` (batch), and does not create a second row
- [ ] `POST /readings` with malformed JSON returns `400`, not `500`
- [ ] `GET /readings?device_id=…&start=…&end=…` filters correctly and `limit`/`offset` paginate without overlap across pages of identical timestamps
- [ ] `GET /readings/stats` returns correct min/max/avg/count grouped by `sensor_type`, and by `device_id,sensor_type` when asked
- [ ] Every error response carries the standard shape including `request_id`
- [ ] All tests pass via `pytest` (one command) — green on **both** SQLite and Postgres in CI
- [ ] `docker build` succeeds and the container runs with only env vars for config
- [ ] Service deploys to DO App Platform; `/healthz` and `/readyz` both return `200` to a `curl` from off-box
- [ ] Changing `LOG_LEVEL` visibly changes log output in the deployed app

## 11. Example Payloads

**Valid request — single reading:**
```json
{
  "device_id": "sensor-042",
  "sensor_type": "temperature",
  "value": 22.5,
  "unit": "celsius",
  "timestamp": "2026-09-13T14:00:00Z"
}
```
→ `201`
```json
{
  "id": 1,
  "device_id": "sensor-042",
  "sensor_type": "temperature",
  "value": 22.5,
  "unit": "celsius",
  "timestamp": "2026-09-13T14:00:00Z",
  "received_at": "2026-09-13T14:00:02Z"
}
```

**Valid request — batch:**
```json
[
  {"device_id": "sensor-042", "sensor_type": "temperature", "value": 22.5, "unit": "celsius", "timestamp": "2026-09-13T14:00:00Z"},
  {"device_id": "sensor-042", "sensor_type": "humidity", "value": 61.0, "unit": "percent", "timestamp": "2026-09-13T14:00:00Z"},
  {"device_id": "sensor-108", "sensor_type": "battery", "value": 87.0, "timestamp": "2026-09-13T14:00:01Z"}
]
```
→ `201` with `summary.created == 3`.

**Mixed batch — the partial-success path:**
```json
[
  {"device_id": "sensor-042", "sensor_type": "temperature", "value": 22.5, "unit": "celsius", "timestamp": "2026-09-13T14:05:00Z"},
  {"device_id": "sensor-042", "sensor_type": "temperature", "value": 22.5, "unit": "celsius", "timestamp": "2026-09-13T14:00:00Z"},
  {"device_id": "sensor-108", "sensor_type": "pressure", "value": 9999, "unit": "hpa", "timestamp": "2026-09-13T14:05:00Z"}
]
```
→ `207 Multi-Status`
```json
{
  "summary": {"received": 3, "created": 1, "duplicate": 1, "rejected": 1},
  "results": [
    {
      "index": 0,
      "status": "created",
      "reading": {
        "id": 4,
        "device_id": "sensor-042",
        "sensor_type": "temperature",
        "value": 22.5,
        "unit": "celsius",
        "timestamp": "2026-09-13T14:05:00Z",
        "received_at": "2026-09-13T14:05:03Z"
      }
    },
    {
      "index": 1,
      "status": "duplicate",
      "reading": {
        "id": 1,
        "device_id": "sensor-042",
        "sensor_type": "temperature",
        "value": 22.5,
        "unit": "celsius",
        "timestamp": "2026-09-13T14:00:00Z",
        "received_at": "2026-09-13T14:00:02Z"
      }
    },
    {
      "index": 2,
      "status": "rejected",
      "errors": [
        {
          "field": "value",
          "code": "out_of_range",
          "message": "pressure value 9999.0 outside plausible range 300..1100"
        }
      ]
    }
  ]
}
```

**Invalid requests (and why):**

*Timestamp in the future* → `422`, `code: "timestamp_in_future"`
```json
{"device_id": "sensor-042", "sensor_type": "temperature", "value": 22.5, "timestamp": "2027-01-01T00:00:00Z"}
```

*Unit inconsistent with sensor_type* → `422`, `code: "unit_mismatch"` (rejected, not converted)
```json
{"device_id": "sensor-042", "sensor_type": "temperature", "value": 72.5, "unit": "fahrenheit", "timestamp": "2026-09-13T14:00:00Z"}
```

*Empty device_id and unknown sensor_type* → `422` with two entries in `detail`
```json
{"device_id": "   ", "sensor_type": "vibration", "value": 3.1, "timestamp": "2026-09-13T14:00:00Z"}
```

*Malformed JSON* → `400`, `code: "malformed_json"`
```
{"device_id": "sensor-042", "value": }
```

---

## Appendix A — Planned Repo Layout

Written down up front so "no God-file" is a design decision rather than something discovered
at 2:30 into the build.

```
app/
  main.py                      # app factory, lifespan, middleware, router wiring
  config.py                    # pydantic-settings
  api/
    routes/readings.py         # POST /readings, GET /readings, GET /readings/stats
    routes/health.py           # /healthz, /readyz
    routes/metrics.py          # /metrics
    errors.py                  # exception handlers + the standard error shape
  schemas/reading.py           # Pydantic — the wire contract
  domain/validation.py         # range table, unit map, validate_reading()
  storage/
    database.py                # engine, session dependency, lifespan hooks
    models.py                  # SQLAlchemy ORM models
    repository.py              # ReadingRepository — query/insert, swappable
  observability/
    logging.py                 # JSON formatter, request-id contextvar
    middleware.py              # request logging + ID propagation
    metrics.py                 # Prometheus collectors
tests/
  unit/test_validation.py      # ranges, units, timestamps — pure, no app
  integration/test_readings_api.py
  integration/test_batch_partial_failure.py
  integration/test_stats_api.py
  integration/test_health.py
Dockerfile
docker-compose.yml             # local Postgres for dialect-parity checks
Makefile                       # build / test / lint / run
.github/workflows/ci.yml       # pytest on SQLite + on a Postgres service container
```

`repository.py` exists so the query layer is behind an interface — it keeps SQL out of the
handlers and makes it possible to test the API against an in-memory implementation if the
DB ever becomes the slow part of the test suite.

## Appendix B — Revised Time Budget

Adjusted from §1 because deploying real Postgres changes the critical path.

1. **0:00–0:10** — Read reqs, sketch architecture, scaffold repo per Appendix A.
2. **0:10–0:20** — **Provision DO Managed Postgres now** and create the App Platform app
   shell. Provisioning takes minutes; start it early so it's warm, not blocking.
3. **0:20–1:40** — Core build: schemas → validation → repository → endpoints.
4. **1:40–2:10** — Tests: unit on validation, integration on endpoints, explicit
   partial-batch-failure test.
5. **2:10–2:40** — Dockerfile, wire `DATABASE_URL` to the managed DB, deploy, curl from
   off-box.
6. **2:40–3:00** — README, logging polish, `/metrics`, buffer.
