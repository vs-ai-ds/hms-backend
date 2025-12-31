# app/models/appointment.py
import uuid
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    DateTime,
    ForeignKey,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.patient import Patient
from app.models.user import User


class AppointmentStatus(str, PyEnum):
    SCHEDULED = "SCHEDULED"
    CHECKED_IN = "CHECKED_IN"
    IN_CONSULTATION = "IN_CONSULTATION"
    COMPLETED = "COMPLETED"
    NO_SHOW = "NO_SHOW"
    CANCELLED = "CANCELLED"


# Enum type definition - create_type=False prevents SQLAlchemy from auto-creating the type
# The type is created manually in tenant_service.py
APPOINTMENT_STATUS_ENUM = ENUM(
    AppointmentStatus,
    name="appointment_status_enum",
    create_type=False,
)


class Appointment(Base):
    __tablename__ = "appointments"

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
    )
    department_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("departments.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        doc="Department for this appointment (required for OPD)",
    )
    doctor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="SET NULL"),
        nullable=False,
        index=True,
        doc="Doctor user (User with role DOCTOR) assigned to this appointment",
    )

    # Appointment Details
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    status: Mapped[AppointmentStatus] = mapped_column(
        APPOINTMENT_STATUS_ENUM,
        nullable=False,
        default=AppointmentStatus.SCHEDULED,
        server_default=text("'SCHEDULED'"),
    )
    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    # OPD Lifecycle fields
    checked_in_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="When patient checked in for appointment",
    )
    consultation_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="When doctor started consultation",
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="When appointment was completed",
    )
    no_show_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="When appointment was marked as no-show",
    )

    # Cancellation fields
    cancelled_reason: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        doc="Reason for cancellation: PATIENT_REQUEST, ADMITTED_TO_IPD, DOCTOR_UNAVAILABLE, OTHER",
    )
    cancelled_note: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        doc="Additional notes about cancellation",
    )

    # IPD linking
    linked_ipd_admission_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("admissions.id", ondelete="SET NULL"),
        nullable=True,
        doc="If appointment was cancelled/converted due to IPD admission, link to admission",
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    patient: Mapped["Patient"] = relationship("Patient")
    doctor: Mapped["User"] = relationship("User", foreign_keys=[doctor_user_id])
