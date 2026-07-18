"""FastAPI application factory for OmniArchive MCP internal API.

Provides a factory function that assembles the FastAPI application with
all dependencies injected. The app is designed for localhost-only access
(binding to 127.0.0.1 is controlled at uvicorn startup, not here).

Corresponds to Requirement 10.6.
"""

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker

from omniarchive_mcp.api.routes import router, set_session_factory


def create_app(session_factory: async_sessionmaker) -> FastAPI:
    """Create and configure the FastAPI application instance.

    Assembles the application by:
    1. Creating a FastAPI instance with project metadata
    2. Injecting the async session factory into the route layer
    3. Including the API router with all endpoint handlers

    Args:
        session_factory: An async_sessionmaker bound to an AsyncEngine,
            used by route handlers to obtain DB sessions.

    Returns:
        A fully configured FastAPI application ready to be served.
    """
    app = FastAPI(title="OmniArchive MCP", version="1.0.0")

    # Inject DB session factory into the route module's dependency system
    set_session_factory(session_factory)

    # Mount all API routes (POST /api/archive, GET /api/status, etc.)
    app.include_router(router)

    return app
