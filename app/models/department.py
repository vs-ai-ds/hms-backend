# app/models/department.py
import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, String, text
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base


class Department(Base):
    """
    Represents a department within a hospital tenant.
    Lives in the per-tenant schema (tenant_<id>).
    """

    __tablename__ = "departments"

    # Primary Key
    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )

    # Department Information
    name = Column(String(100), nullable=False)
    description = Column(String(500), nullable=True)

    # Flags
    is_for_staff = Column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
        doc="If true, this department can be assigned to staff/users",
    )
    is_for_patients = Column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
        doc="If true, this department can be assigned to patients",
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
