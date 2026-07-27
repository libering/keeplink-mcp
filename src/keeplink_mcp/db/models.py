"""SQLAlchemy ORM models for the archive task queue."""

import enum
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, Enum, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class TaskStatus(str, enum.Enum):
    """Lifecycle states of an archive task."""

    PENDING = "pending"
    PROCESSING = "processing"
    SUCCESS = "success"
    FAILED = "failed"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_task_id() -> str:
    return uuid4().hex


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""

    pass


class ArchiveTask(Base):
    """Represents a single URL archiving job in the queue.

    Lifecycle: pending -> processing -> success | failed
    On retryable errors (429/50x), status reverts to pending with incremented retry_count.
    """

    __tablename__ = "archive_tasks"

    task_id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=_new_task_id
    )
    url: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus), nullable=False, default=TaskStatus.PENDING, index=True
    )
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )
    result_url: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)

    def __repr__(self) -> str:
        return (
            f"<ArchiveTask(id={self.task_id[:8]}..., "
            f"url={self.url[:40]}, status={self.status.value})>"
        )
