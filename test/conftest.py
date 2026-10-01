"""Shared test fixtures for KeepLink MCP integration tests.

# Feature: keeplink-mcp

Provides:
- In-memory SQLite async engine
- Session factory
- Database initialization
- Repo root on ``sys.path`` so ``from scripts.check_version import ...`` resolves
  (version-consistency-gate, Req 9.6 — tests import the Version_Check_Script
  functions instead of re-implementing extraction/consistency logic).
"""

import sys
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

# Register the repo root on sys.path so ``scripts`` is importable as a package
# under pytest regardless of the invocation directory. Kept idempotent and at
# the front so the in-repo ``scripts`` package shadows any same-named installed
# distribution. Paired with scripts/__init__.py which marks it a package.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from keeplink_mcp.db.models import Base  # noqa: E402  (import after sys.path setup)


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
