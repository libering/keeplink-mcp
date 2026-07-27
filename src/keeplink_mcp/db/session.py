"""Async SQLAlchemy engine and session factory with WAL mode for SQLite."""

from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from keeplink_mcp.db.models import Base


def _enable_wal(dbapi_conn, connection_record) -> None:  # noqa: N802
    """Enable WAL mode on every new raw SQLite connection.

    WAL (Write-Ahead Logging) allows concurrent reads during writes,
    mitigating the SQLite write-lock bottleneck noted in the PRD risk section.
    """
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def build_engine(db_path: Path) -> AsyncEngine:
    """Create an async SQLAlchemy engine pointing at the given SQLite file.

    Args:
        db_path: Filesystem path to the SQLite database file.

    Returns:
        Configured AsyncEngine with WAL mode enabled.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite+aiosqlite:///{db_path.resolve()}"

    engine = create_async_engine(url, echo=False, pool_pre_ping=True)

    # Register WAL pragma on every raw connection checkout
    event.listen(engine.sync_engine, "connect", _enable_wal)

    return engine


def build_session_factory(engine: AsyncEngine) -> sessionmaker:
    """Create an async session factory bound to the given engine.

    Args:
        engine: The AsyncEngine to bind sessions to.

    Returns:
        A sessionmaker configured for AsyncSession usage.
    """
    return sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db(engine: AsyncEngine) -> None:
    """Create all tables if they do not exist.

    Args:
        engine: The AsyncEngine to create tables on.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
