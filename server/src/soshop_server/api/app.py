"""FastAPI application factory: wires routes, CORS, middleware, and tracing."""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from soshop.api.routes import (
    agents,
    broadcasts,
    channels,
    customize,
    favorites,
    feed,
    feed_event,
    feed_swipe,
    live,
    personas,
    promo,
    studio,
)
from soshop.observability import metrics as metrics_obs
from soshop.services import studio_assets
from soshop_server.api.errors import install_error_handlers
from soshop_server.api.middleware import RequestIDLogFilter, RequestIDMiddleware
from soshop_server.api.routes import assets, health
from soshop_server.core.config import settings
from soshop_server.main import lifespan

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s [req=%(request_id)s] %(message)s",
)
# Attach the request-id filter to the root logger so child loggers inherit
# it without each module having to opt in.
_root_logger = logging.getLogger()
if not any(isinstance(f, RequestIDLogFilter) for f in _root_logger.filters):
    _root_logger.addFilter(RequestIDLogFilter())
for _handler in _root_logger.handlers:
    if not any(isinstance(f, RequestIDLogFilter) for f in _handler.filters):
        _handler.addFilter(RequestIDLogFilter())


def create_app() -> FastAPI:
    app = FastAPI(title="SoShop Server API", lifespan=lifespan)

    origins = [o.strip() for o in settings.CORS_ALLOW_ORIGINS.split(",") if o.strip()]
    allow_all = origins == ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=not allow_all,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Added AFTER CORS so that FastAPI's reverse-add execution order runs
    # RequestIDMiddleware FIRST on the inbound path — every subsequent
    # middleware (including CORS) sees the request-ID contextvar populated.
    app.add_middleware(RequestIDMiddleware)

    # Canonical error envelope is wired *before* routers so every
    # router-raised HTTPException flows through the shared formatter.
    install_error_handlers(app)

    app.include_router(health.router)
    app.include_router(assets.router)
    app.include_router(feed.router)
    app.include_router(feed_swipe.router)
    app.include_router(feed_event.router)
    app.include_router(live.router)
    app.include_router(customize.router)
    app.include_router(personas.router)
    app.include_router(favorites.router)
    app.include_router(channels.router)
    app.include_router(agents.router)
    app.include_router(studio.router)
    app.include_router(promo.router)
    app.include_router(broadcasts.router)
    # Studio uploads (model portraits, product images) are plain local files.
    app.mount(
        "/media/uploads",
        StaticFiles(directory=studio_assets.uploads_dir()),
        name="studio-uploads",
    )
    app.include_router(metrics_obs.metrics_router)

    if settings.OTEL_ENABLED:
        # Lazy-imported so the OpenTelemetry SDK is only loaded when
        # tracing is actually turned on — keeps cold start light for the
        # common case where this stays off.
        from soshop_server.observability.tracing import setup_tracing

        setup_tracing(app)
    return app


app = create_app()
