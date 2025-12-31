# app/models/email_log.py
"""
Email log model for tracking invitation and system emails.
Stored in public schema since it tracks emails for users in public schema.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.user import User


class EmailLog(Base):
    """
    Logs email sends for debugging and audit purposes.
    Tracks invitation emails, password resets, etc.
    """

    __tablename__ = "email_logs"
    __table_args__ = (
        Index("idx_email_log_to_created", "to", "created_at"),
        Index("idx_email_log_template_status", "template", "status"),
        {"schema": "public"},
    )

    # Primary Key
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # Foreign Keys
    triggered_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="SET NULL"),
        nullable=True,
        doc="User who triggered this email",
    )
    related_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        doc="User this email is about (e.g., invitation recipient)",
    )

    # Email Information
    to: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
        doc="Recipient email address",
    )
    template: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        doc="Email template name (e.g., 'user_invitation', 'password_reset')",
    )
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        doc="Status: 'SENT', 'FAILED'",
    )
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Error message if status is FAILED",
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        index=True,
    )

    # Relationships
    triggered_by: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[triggered_by_id],
        backref="emails_triggered",
    )
    related_user: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[related_user_id],
        backref="emails_received",
    )
