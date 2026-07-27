"""FastAPI Service main entry point.

Starts the FastAPI HTTP server with a background worker managed via lifespan.
Uvicorn handles SIGTERM/SIGINT graceful shutdown, triggering the lifespan's
shutdown path which stops the worker cleanly.

Corresponds to Requirements 11.1, 11.3, 11.4, 11.5, 11.6.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from keeplink_mcp.config import load_config
from keeplink_mcp.db.session import build_engine, build_session_factory, init_db
from keeplink_mcp.logging_setup import setup_logging
from keeplink_mcp.worker.archiver import BackgroundWorker


def main() -> None:
    """Application entry point: configure, build app, and run via uvicorn."""
    config = load_config()
    logger = setup_logging(config.log_level, config.log_file)

    # Phase 1: Initialize DB schema using a temporary engine + event loop.
    # asyncio.run() creates and destroys its own event loop, so the engine
    # created here cannot be reused by uvicorn's loop (aiosqlite connections
    # are bound to the loop that created them).
    init_engine = build_engine(config.db_path)
    try:
        asyncio.run(_init_db(init_engine))
    except Exception as exc:
        logger.error("Database initialization failed: %s", exc)
        sys.exit(1)

    # Phase 2: Build a fresh engine for the application lifetime.
    # This engine will be used by uvicorn's event loop.
    engine = build_engine(config.db_path)
    session_factory = build_session_factory(engine)
    worker = BackgroundWorker(config, session_factory, logger)

    app = _build_app_with_lifespan(session_factory, worker, logger)

    logger.info(
        "KeepLink MCP Service starting on %s:%d, db=%s",
        config.api_host,
        config.api_port,
        config.db_path,
    )

    # Use uvicorn.Config + Server for better event-loop control on Windows.
    # uvicorn.run() manages its own loop and can trigger winerror 10049
    # when binding 127.0.0.1 on certain Windows network configurations.
    uvi_config = uvicorn.Config(
        app,
        host=config.api_host,
        port=config.api_port,
        log_level="warning",
        loop="asyncio",
    )
    server = uvicorn.Server(uvi_config)
    server.run()


async def _init_db(engine) -> None:
    """Create tables if they don't exist, then dispose the temporary engine."""
    await init_db(engine)
    await engine.dispose()


def _build_app_with_lifespan(
    session_factory,
    worker: BackgroundWorker,
    logger: logging.Logger,
) -> FastAPI:
    """Create the FastAPI app with a lifespan that manages the background worker."""
    from keeplink_mcp.api.app import create_app

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup: launch the background worker as an asyncio task.
        worker_task = asyncio.create_task(worker.start())
        yield
        # Shutdown: stop worker gracefully, then cancel the task.
        await worker.stop()
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
        logger.info("Shutdown complete")

    app = create_app(session_factory)
    app.router.lifespan_context = lifespan
    return app


if __name__ == "__main__":
    main()
