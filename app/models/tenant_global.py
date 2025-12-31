import uuid
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base


class TenantStatus(str, PyEnum):
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    INACTIVE = "INACTIVE"


class Tenant(Base):
    """
    Represents a hospital tenant.

    Stored in the public schema so the platform (SUPER_ADMIN)
    can see and manage all tenants.
    """

    __tablename__ = "tenants"
    __table_args__ = {"schema": "public"}

    # Primary Key
    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )

    # Business Identifiers
    name = Column(String(255), nullable=False)
    license_number = Column(String(100), nullable=False, unique=True)
    schema_name = Column(
        String(100),
        nullable=False,
        unique=True,
        doc="PostgreSQL schema name for this tenant (e.g. tenant_ab12cd34)",
    )

    # Contact Information
    contact_email = Column(String(255), nullable=False)
    contact_phone = Column(String(50), nullable=True)
    address = Column(String(500), nullable=True)

    # Status and Configuration
    status = Column(
        Enum(TenantStatus, name="tenant_status_enum"),
        nullable=False,
        server_default=text("'PENDING'"),
    )
    max_users = Column(
        Integer,
        nullable=True,
        doc="Maximum number of users allowed for this tenant (null = unlimited)",
    )
    max_patients = Column(
        Integer,
        nullable=True,
        doc="Maximum number of patients allowed for this tenant (null = unlimited)",
    )

    # Timestamps
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=datetime.utcnow,
    )
