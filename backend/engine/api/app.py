"""Application factory and lifecycle bridge between FastAPI and MarketEngine."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from engine.bootstrap import create_market_engine
from engine.config import EngineSettings
from engine.errors import StorageBusyError, StorageError
from engine.market_engine import MarketEngine

from .routes import router

EngineFactory = Callable[[], MarketEngine]


def create_app(
    *,
    engine_factory: EngineFactory | None = None,
    settings: EngineSettings | None = None,
) -> FastAPI:
    """Build an app which owns exactly one engine for its lifespan."""

    actual_settings = settings or EngineSettings()
    factory = engine_factory or (lambda: create_market_engine(actual_settings))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = factory()
        app.state.market_engine = engine
        await engine.start()
        try:
            yield
        finally:
            await engine.stop()

    app = FastAPI(
        title="CS2 Price Oracle",
        description="Self-hosted CS2 marketplace monitoring API.",
        version="2.0.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=actual_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(StorageBusyError)
    async def storage_busy_handler(
        _request: Request, _error: StorageBusyError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"detail": "Storage is busy; retry the request."},
        )

    @app.exception_handler(StorageError)
    async def storage_error_handler(
        _request: Request, _error: StorageError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={"detail": "Storage operation failed."},
        )

    app.include_router(router)
    return app
