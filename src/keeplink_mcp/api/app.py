"""FastAPI application factory for KeepLink MCP internal API.

Provides a factory function that assembles the FastAPI application with
all dependencies injected. The app is designed for localhost-only access
(binding to 127.0.0.1 is controlled at uvicorn startup, not here).

Corresponds to Requirement 10.6.
"""

from collections.abc import Callable

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker

from keeplink_mcp.api.batch_routes import router as batch_router
from keeplink_mcp.api.health import (
    health_router,
    set_health_session_factory,
    set_worker_running_flag,
)
from keeplink_mcp.api.routes import router, set_session_factory


def create_app(
    session_factory: async_sessionmaker,
    worker_running_getter: Callable[[], bool] | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application instance.

    Assembles the application by:
    1. Creating a FastAPI instance with project metadata
    2. Injecting the async session factory into the route layer
    3. Injecting the session factory and worker running flag into health module
    4. Including the API routers with all endpoint handlers

    Args:
        session_factory: An async_sessionmaker bound to an AsyncEngine,
            used by route handlers to obtain DB sessions.
        worker_running_getter: Optional callable that returns True if the
            worker polling loop is running. Used by health check endpoint.

    Returns:
        A fully configured FastAPI application ready to be served.
    """
    app = FastAPI(title="KeepLink MCP", version="1.1.1")

    # Inject DB session factory into the route module's dependency system
    set_session_factory(session_factory)

    # Inject DB session factory into health module for readiness checks
    set_health_session_factory(session_factory)

    # Inject worker running flag getter if provided
    if worker_running_getter is not None:
        set_worker_running_flag(worker_running_getter)

    # Mount batch query routes (GET /api/status/batch)
    # NOTE: Must be mounted BEFORE main router so /api/status/batch matches
    # before /api/status/{task_id} (otherwise "batch" would match as task_id)
    app.include_router(batch_router)

    # Mount all API routes (POST /api/archive, GET /api/status, etc.)
    app.include_router(router)

    # Mount health check router (GET /api/health)
    app.include_router(health_router)

    return app
