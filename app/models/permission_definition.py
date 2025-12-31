# app/models/permission_definition.py
"""
Canonical permission definitions stored in public schema.
These are platform-wide permission codes that can be referenced by tenant roles.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PermissionDefinition(Base):
    """
    Canonical list of permission codes available in the platform.
    Stored in public schema so all tenants can reference the same permission codes.
    """

    __tablename__ = "permission_definitions"
    __table_args__ = {"schema": "public"}

    # Primary Key
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )

    # Permission Information
    code: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    category: Mapped[str | None] = mapped_column(String(50), nullable=True)  # e.g., "dashboard", "patients"

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
