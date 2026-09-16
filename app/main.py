"""Application factory and process lifecycle."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import install_exception_handlers
from app.api.routes import health, metrics, readings
from app.config import Settings, get_settings
from app.observability.logging import configure_logging
from app.observability.middleware import RequestContextMiddleware
from app.storage.database import create_all, dispose_engine, init_engine

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = app.state.settings
    init_engine(settings.database_url)
    create_all()
    logger.info(
        "service started",
        extra={"service": settings.service_name, "version": settings.version},
    )
    yield
    # uvicorn stops accepting connections on SIGTERM and drains in-flight
    # requests before this runs, so disposing here cannot cut a request short.
    dispose_engine()
    logger.info("service stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="Sensor Ingestion & Analytics API",
        version=settings.version,
        lifespan=lifespan,
    )
    app.state.settings = settings

    app.add_middleware(RequestContextMiddleware)
    # Added last, so it ends up outermost: preflight is answered before anything
    # else runs, and CORS headers are attached even to error responses.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        # No cookies or Authorization header, so credentials stay off. This is
        # what makes a wildcard origin legitimate rather than a hole.
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
        # So a browser client can read the correlation ID off the response.
        expose_headers=["X-Request-ID"],
    )
    install_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(readings.router)
    app.include_router(metrics.router)

    return app


app = create_app()
