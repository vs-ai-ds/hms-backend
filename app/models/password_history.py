# app/models/password_history.py
"""
Password history model for tracking user password changes.
Stored in public schema since users are in public schema.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.user import User


class PasswordHistory(Base):
    """
    Stores password history for users.
    Used to enforce "cannot reuse last N passwords" policy.
    """

    __tablename__ = "password_history"
    __table_args__ = (
        Index("idx_password_history_user_created", "user_id", "created_at"),
        {"schema": "public"},
    )

    # Primary Key
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # Foreign Keys
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Password Information
    password_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        doc="Hashed password (same algorithm as User.hashed_password)",
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        index=True,
    )

    user: Mapped["User"] = relationship("User", backref="password_history")
