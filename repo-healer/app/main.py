"""FastAPI application factory.

Mounts all routers under /api/v1 and configures middleware.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.context_store import InMemoryContextStore, create_context_store
from app.core.exceptions import RepoHealerError, repo_healer_error_handler
from app.core.logging import setup_logging

# Import all routers
from app.modules.analyzer.router import router as analyzer_router
from app.modules.complexity.router import router as complexity_router
from app.modules.pr.router import router as pr_router
from app.modules.risk.router import router as risk_router
from app.modules.validation.router import router as validation_router
from app.pipeline.router import context_router, router as pipeline_router

# Import router modules to inject shared store
from app.modules.analyzer import router as analyzer_router_module
from app.modules.complexity import router as complexity_router_module
from app.modules.risk import router as risk_router_module
from app.modules.validation import router as validation_router_module
from app.modules.pr import router as pr_router_module
from app.pipeline import router as pipeline_router_module


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan — set up shared state."""
    settings = get_settings()
    setup_logging(settings.log_level)

    # Create shared context store
    store = create_context_store()

    # Inject the shared store into all router modules
    def _get_store():  # type: ignore[no-untyped-def]
        return store

    # Override the get_store dependency in each router
    from app.modules.analyzer.router import get_store as _ag
    from app.modules.complexity.router import get_store as _cg
    from app.modules.risk.router import get_store as _rg
    from app.modules.validation.router import get_store as _vg
    from app.modules.pr.router import get_store as _pg
    from app.pipeline.router import get_store as _plg

    app.dependency_overrides[_ag] = _get_store
    app.dependency_overrides[_cg] = _get_store
    app.dependency_overrides[_rg] = _get_store
    app.dependency_overrides[_vg] = _get_store
    app.dependency_overrides[_pg] = _get_store
    app.dependency_overrides[_plg] = _get_store

    yield


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Repo Healer",
        description="AI-powered repository code health analyzer and healer",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS middleware — allow all origins in development
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Global exception handler
    app.add_exception_handler(RepoHealerError, repo_healer_error_handler)  # type: ignore[arg-type]

    # Mount all routers under /api/v1
    app.include_router(analyzer_router, prefix="/api/v1")
    app.include_router(complexity_router, prefix="/api/v1")
    app.include_router(risk_router, prefix="/api/v1")
    app.include_router(validation_router, prefix="/api/v1")
    app.include_router(pr_router, prefix="/api/v1")
    app.include_router(pipeline_router, prefix="/api/v1")
    app.include_router(context_router, prefix="/api/v1")

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
