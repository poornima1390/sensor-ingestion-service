# Sensor Ingestion & Analytics API

Ingestion and analytics service for IoT sensor readings (temperature, humidity,
pressure, battery). Python 3.12 / FastAPI / SQLAlchemy, deployed to DigitalOcean
App Platform.

The full design — endpoint contracts, validation rules, error conventions and the
scaling path — lives in [Spec.md](Spec.md).

## Status

**Phase 1 — scaffold and deployment pipeline.** Layering, configuration, logging
and health probes are live and deployed. The `/readings` endpoints land in Phase 2.

| Endpoint | Status |
|---|---|
| `GET /healthz` | ✅ live — liveness probe, no DB dependency |
| `GET /readyz` | ✅ live — readiness probe, checks the database |
| `POST /readings` | Phase 2 |
| `GET /readings` | Phase 2 |
| `GET /readings/stats` | Phase 2 |
| `GET /metrics` | Phase 3 |

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
make test-pg   # same suite against Postgres (docker compose up -d postgres first)
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
