# app/models/user_tenant.py
"""
User-Tenant membership table in public schema.
Allows users to belong to multiple tenants (future support).
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.tenant_global import Tenant
from app.models.user import User


class UserTenant(Base):
    """
    Membership table linking users to tenants.
    Stored in public schema for platform-wide access.
    """

    __tablename__ = "user_tenants"
    __table_args__ = (
        UniqueConstraint("user_id", "tenant_id", name="uq_user_tenant"),
        {"schema": "public"},
    )

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
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Flags
    is_default: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
        doc="True if this is the user's default tenant",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
        doc="True if the user is active in this tenant",
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=datetime.utcnow,
    )

    # Relationships
    user: Mapped["User"] = relationship("User", backref="tenant_memberships")
    tenant: Mapped["Tenant"] = relationship("Tenant", backref="user_memberships")
