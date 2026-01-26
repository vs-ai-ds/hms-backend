# app/models/prescription.py
import uuid
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.appointment import Appointment
from app.models.base import Base
from app.models.patient import Patient
from app.models.user import User


class PrescriptionStatus(str, PyEnum):
    DRAFT = "DRAFT"
    ISSUED = "ISSUED"
    DISPENSED = "DISPENSED"
    CANCELLED = "CANCELLED"


# Enum type definition - create_type=False prevents SQLAlchemy from auto-creating the type
# The type is created manually in tenant_service.py
PRESCRIPTION_STATUS_ENUM = ENUM(
    PrescriptionStatus,
    name="prescription_status_enum",
    create_type=False,
)


class Prescription(Base):
    __tablename__ = "prescriptions"

    # Primary Key
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # Business Identifier
    prescription_code: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        unique=True,
        index=True,
        doc="Unique prescription identifier (format: {tenantId}-RX-{sequential})",
    )

    # Foreign Keys
    patient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("patients.id", ondelete="CASCADE"),
        nullable=False,
    )
    doctor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="SET NULL"),
        nullable=False,
        index=True,
        doc="Doctor user (User with role DOCTOR) who created this prescription",
    )
    appointment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("appointments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        doc="Linked OPD appointment (if prescription is for OPD visit)",
    )
    admission_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("admissions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        doc="Linked IPD admission (if prescription is for IPD stay)",
    )

    # Status
    status: Mapped[PrescriptionStatus] = mapped_column(
        PRESCRIPTION_STATUS_ENUM,
        nullable=False,
        default=PrescriptionStatus.DRAFT,
        server_default=text("'DRAFT'"),
    )

    # Clinical Information
    chief_complaint: Mapped[str | None] = mapped_column(
        String(2000),
        nullable=True,
        doc="Chief complaint / what patient told doctor",
    )
    diagnosis: Mapped[str | None] = mapped_column(
        String(1000),
        nullable=True,
        doc="Diagnosis or clinical notes for this prescription",
    )

    # Cancellation fields
    cancelled_reason: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        doc="Reason for prescription cancellation",
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="When prescription was cancelled",
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    patient: Mapped["Patient"] = relationship("Patient")
    doctor: Mapped["User"] = relationship("User", foreign_keys=[doctor_user_id])
    appointment: Mapped["Appointment"] = relationship(
        "Appointment", backref="prescriptions"
    )


class PrescriptionItem(Base):
    __tablename__ = "prescription_items"

    # Primary Key
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # Foreign Keys
    prescription_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("prescriptions.id", ondelete="CASCADE"),
        nullable=False,
    )
    stock_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("stock_items.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        doc="Optional link to stock master",
    )

    # Medicine Information
    medicine_name: Mapped[str] = mapped_column(String(255), nullable=False)
    dosage: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )  # e.g. "500mg"
    frequency: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )  # e.g. "every 8 hours"
    duration: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )  # e.g. "5 days"
    instructions: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )  # e.g. "after food"
    quantity: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        doc="Quantity required when stock_item_id is present (for stock deduction on dispense)",
    )

    prescription: Mapped["Prescription"] = relationship("Prescription", backref="items")
