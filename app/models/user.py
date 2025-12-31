import uuid
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.tenant_global import Tenant


class UserStatus(str, PyEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    LOCKED = "LOCKED"
    PASSWORD_EXPIRED = "PASSWORD_EXPIRED"


class RoleName(str, PyEnum):
    SUPER_ADMIN = "SUPER_ADMIN"
    HOSPITAL_ADMIN = "HOSPITAL_ADMIN"
    DOCTOR = "DOCTOR"
    NURSE = "NURSE"
    PHARMACIST = "PHARMACIST"
    RECEPTIONIST = "RECEPTIONIST"


class User(Base):
    """
    Represents a platform user.
    - SUPER_ADMIN: tenant_id is NULL
    - Tenant users (HOSPITAL_ADMIN, DOCTOR, etc.): tenant_id references Tenant.id
    """

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email", "tenant_id", name="uq_users_email_tenant"),
        {"schema": "public"},
    )

    # Primary Key
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # Foreign Keys
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="CASCADE"),
        nullable=True,
    )

    # Authentication
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)

    # Personal Information
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Professional Information
    department: Mapped[str | None] = mapped_column(String(100), nullable=True)
    specialization: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Status
    status: Mapped[UserStatus] = mapped_column(
        Enum(UserStatus, name="user_status_enum"),
        nullable=False,
        server_default=text("'ACTIVE'"),
    )

    # Flags
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
        doc="If false, user cannot login. Use this instead of hard delete.",
    )
    is_deleted: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        doc="Soft delete flag. If true, user is considered deleted but records remain.",
    )
    must_change_password: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        doc="If true, user must change password on next login",
    )
    email_verified: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        doc="If true, user's email has been verified (user has successfully logged in with temp password)",
    )

    # Soft Delete Timestamp
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="Timestamp when user was soft-deleted",
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
    tenant: Mapped["Tenant"] = relationship("Tenant", backref="users")
