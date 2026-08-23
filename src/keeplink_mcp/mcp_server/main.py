"""MCP Server standalone process entry point.

Automatically spawns the FastAPI backend service as a subprocess before
starting the MCP stdio loop. This enables "plug and play" usage in any
AI IDE — users only need to configure this single command.

Requirements: 11.2
"""

import asyncio
import logging
import subprocess
import sys
import time

import httpx
from mcp.server.stdio import stdio_server

from keeplink_mcp.config import load_config
from keeplink_mcp.logging_setup import setup_logging
from keeplink_mcp.mcp_server.server import create_mcp_server

logger = logging.getLogger("keeplink.mcp_entry")


def _start_backend(config) -> subprocess.Popen:
    """Spawn the FastAPI backend as a detached subprocess.

    Runs `python -m keeplink_mcp.main` with the same environment as the
    current process, ensuring config env vars propagate. Stdout/stderr
    redirected to DEVNULL to avoid polluting the MCP stdio channel.
    """
    cmd = [sys.executable, "-m", "keeplink_mcp.main"]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )
    logger.info("Backend subprocess started (PID %d)", proc.pid)
    return proc


def _wait_for_backend(host: str, port: int, timeout: float = 15.0) -> bool:
    """Block until the FastAPI backend responds to HTTP, or timeout."""
    base_url = f"http://{host}:{port}"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(f"{base_url}/api/status/ping", timeout=2.0)
            # Any HTTP response (even 404) means server is up
            if r.status_code in (200, 404, 405, 422):
                return True
        except (httpx.ConnectError, httpx.ConnectTimeout, OSError):
            pass
        time.sleep(0.5)
    return False


async def _run() -> None:
    """Start backend, wait for ready, then run MCP server.

    Lifecycle:
    1. Spawn FastAPI backend subprocess
    2. Wait for it to accept HTTP connections
    3. Run MCP stdio loop (blocks until client disconnects)
    4. Terminate backend on exit
    """
    config = load_config()
    setup_logging(
        level=config.log_level,
        log_file=config.log_file,
        max_bytes=config.log_max_bytes,
        backup_count=config.log_backup_count,
    )

    backend_proc = _start_backend(config)

    try:
        ready = _wait_for_backend(config.api_host, config.api_port)
        if not ready:
            logger.error(
                "Backend failed to start within timeout (%s:%d)",
                config.api_host,
                config.api_port,
            )
            backend_proc.terminate()
            sys.exit(1)

        logger.info("Backend ready at %s:%d", config.api_host, config.api_port)

        server = create_mcp_server(config)
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )
    finally:
        if backend_proc.poll() is None:
            logger.info("Terminating backend (PID %d)", backend_proc.pid)
            backend_proc.terminate()
            try:
                backend_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                backend_proc.kill()


def main() -> None:
    """Entry point for the MCP server process."""
    asyncio.run(_run())


if __name__ == "__main__":
    main()
