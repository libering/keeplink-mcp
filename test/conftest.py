"""Shared test fixtures for KeepLink MCP integration tests.

# Feature: keeplink-mcp

Provides:
- In-memory SQLite async engine
- Session factory
- Database initialization
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from keeplink_mcp.db.models import Base


@pytest.fixture
async def engine() -> AsyncEngine:
    """Create an in-memory SQLite async engine for test isolation."""
    eng = create_async_engine("sqlite+aiosqlite:///", echo=False)
    yield eng
    await eng.dispose()


@pytest.fixture
async def init_db(engine: AsyncEngine) -> None:
    """Initialize database schema in the in-memory engine."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@pytest.fixture
async def session_factory(engine: AsyncEngine, init_db: None) -> sessionmaker:
    """Provide an async session factory bound to the test engine."""
    return sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
