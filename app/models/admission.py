# app/models/admission.py
import uuid
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    DateTime,
    ForeignKey,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.patient import Patient
from app.models.user import User


class AdmissionStatus(str, PyEnum):
    ACTIVE = "ACTIVE"
    DISCHARGED = "DISCHARGED"
    CANCELLED = "CANCELLED"


# Enum type definition - create_type=False prevents SQLAlchemy from auto-creating the type
# The type is created manually in tenant_service.py
ADMISSION_STATUS_ENUM = ENUM(
    AdmissionStatus,
    name="admission_status_enum",
    create_type=False,
)


class Admission(Base):
    """
    IPD (In-Patient Department) admission record.
    Tenant-scoped in the tenant schema.
    """

    __tablename__ = "admissions"

    # Primary Key
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # Foreign Keys
    patient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("patients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    department_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("departments.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        doc="Department for this admission (required for IPD)",
    )
    primary_doctor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="SET NULL"),
        nullable=False,
        index=True,
        doc="Primary doctor user (User with role DOCTOR) responsible for this admission",
    )

    # Admission Details
    admit_datetime: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
        doc="Date and time when patient was admitted",
    )
    discharge_datetime: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="Date and time when patient was discharged",
    )
    discharge_summary: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Clinical summary and notes at time of discharge",
    )
    notes: Mapped[str | None] = mapped_column(
        String(1000),
        nullable=True,
        doc="Additional notes about the admission",
    )

    # Status
    status: Mapped[AdmissionStatus] = mapped_column(
        ADMISSION_STATUS_ENUM,
        nullable=False,
        default=AdmissionStatus.ACTIVE,
        server_default=text("'ACTIVE'"),
        index=True,
    )

    # Link to source OPD appointment if converted from OPD
    source_opd_appointment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("appointments.id", ondelete="SET NULL"),
        nullable=True,
        doc="If admission was created from an OPD appointment, link to that appointment",
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
    patient: Mapped["Patient"] = relationship("Patient", backref="admissions")
    primary_doctor: Mapped["User"] = relationship(
        "User", foreign_keys=[primary_doctor_user_id]
    )
