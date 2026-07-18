"""MCP Server standalone process entry point.

Loads configuration, sets up logging, and starts the MCP server
with stdio transport for communication with AI clients.

Requirements: 11.2
"""

import asyncio

from mcp.server.stdio import stdio_server

from omniarchive_mcp.config import load_config
from omniarchive_mcp.logging_setup import setup_logging
from omniarchive_mcp.mcp_server.server import create_mcp_server


async def _run() -> None:
    """Initialize and run the MCP server over stdio transport."""
    config = load_config()
    setup_logging(level=config.log_level, log_file=config.log_file)
    server = create_mcp_server(config)

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main() -> None:
    """Entry point for the MCP server process."""
    asyncio.run(_run())


if __name__ == "__main__":
    main()
