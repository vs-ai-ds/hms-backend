# app/models/tenant_role.py
"""
Tenant-scoped role and permission models.
These models are used when the search_path is set to a tenant schema.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class TenantRole(Base):
    """
    Tenant-scoped role (e.g., "Doctor", "Nurse", "Custom Role").
    Each tenant can have their own roles with custom permissions.
    Stored in tenant schema (tenant_XXXX).
    """

    __tablename__ = "roles"
    __table_args__ = {"extend_existing": True}  # Allow redefinition in different schemas

    # Primary Key
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )

    # Role Information
    name: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    system_key: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        index=True,
        doc="System role key (e.g., 'DOCTOR', 'HOSPITAL_ADMIN') for system roles. NULL for custom roles.",
    )

    # Flags
    is_system: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
        doc="True if this is a system role (cannot be deleted, renamed, or have permissions changed)",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
        doc="If false, role is disabled and cannot be assigned to users.",
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
    permissions: Mapped[list["TenantRolePermission"]] = relationship(
        "TenantRolePermission",
        back_populates="role",
        cascade="all, delete-orphan",
        lazy="joined",
    )

    user_roles: Mapped[list["TenantUserRole"]] = relationship(
        "TenantUserRole",
        back_populates="role",
        cascade="all, delete-orphan",
    )


class TenantRolePermission(Base):
    """
    Assigns permission codes to tenant roles.
    References permission codes from public.permission_definitions.
    Stored in tenant schema (tenant_XXXX).
    """

    __tablename__ = "role_permissions"
    __table_args__ = {"extend_existing": True}  # Allow redefinition in different schemas

    # Primary Key
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )

    # Foreign Keys
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Permission Reference
    permission_code: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
        doc="References public.permission_definitions.code",
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    # Relationships
    role: Mapped["TenantRole"] = relationship("TenantRole", back_populates="permissions")


class TenantUserRole(Base):
    """
    Assigns global users (from public.users) to tenant roles.
    Stored in tenant schema (tenant_XXXX).
    """

    __tablename__ = "user_roles"
    __table_args__ = (
        UniqueConstraint("user_id", "role_id", name="uq_user_role"),
        {"extend_existing": True},  # Allow redefinition in different schemas
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
        nullable=False,
        index=True,
        doc="References public.users.id (FK constraint handled at application level)",
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    # Relationships
    role: Mapped["TenantRole"] = relationship("TenantRole", back_populates="user_roles")
