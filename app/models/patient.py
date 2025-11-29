# app/models/patient.py
import uuid
from datetime import datetime, date
from enum import Enum

from sqlalchemy import (
    String,
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.user import User


class PatientType(str, Enum):
    OPD = "OPD"
    IPD = "IPD"


class Patient(Base):
    """
    Tenant-scoped patient entity.

    NOTE:
    - This table lives in the tenant schema (per-tenant).
    - No tenant_id column; isolation is via Postgres schema.
    """

    __tablename__ = "patients"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    dob: Mapped[date | None] = mapped_column(Date, nullable=True)
    gender: Mapped[str | None] = mapped_column(String(10), nullable=True)
    blood_group: Mapped[str | None] = mapped_column(String(10), nullable=True)

    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    emergency_contact: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # ABAC: department-based access
    department: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        doc="Department this patient is primarily associated with (e.g., Cardiology, Orthopedics).",
    )

    patient_type: Mapped[PatientType] = mapped_column(
        SAEnum(PatientType, name="patient_type_enum"),
        nullable=False,
    )

    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

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

    created_by: Mapped["User"] = relationship("User")