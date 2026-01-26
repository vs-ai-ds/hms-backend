# app/models/background_task.py
"""
Background task tracking model for long-running operations.
Stored in public schema for platform-wide task management.
"""

import uuid
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class TaskStatus(str, PyEnum):
    """Status of a background task."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class TaskType(str, PyEnum):
    """Type of background task."""

    DEMO_SEED = "DEMO_SEED"
    DEMO_FRESHEN = "DEMO_FRESHEN"
    DEMO_RESET = "DEMO_RESET"


class BackgroundTask(Base):
    """
    Tracks background tasks for long-running operations.
    Used as fallback when Redis is unavailable.
    """

    __tablename__ = "background_tasks"
    __table_args__ = {"schema": "public"}

    # Primary Key
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )

    # Foreign Keys
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Task Information
    task_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        Enum(TaskStatus, name="task_status_enum"),
        nullable=False,
        server_default=text("'PENDING'"),
        index=True,
    )
    progress: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
        doc="Progress percentage (0-100)",
    )
    message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Current status message",
    )
    error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Error message if task failed",
    )

    # Task Parameters (stored as JSON string for flexibility)
    parameters: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="JSON string of task parameters",
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        index=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
