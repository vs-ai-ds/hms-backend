# app/models/vital.py
import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.patient import Patient
from app.models.user import User


class Vital(Base):
    """
    Represents a vitals reading for a patient (e.g. BP, pulse, temperature).
    Tenant-scoped in the tenant schema.
    """

    __tablename__ = "vitals"

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
    appointment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("appointments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        doc="Linked OPD appointment (if vitals recorded during appointment)",
    )
    admission_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("admissions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        doc="Linked IPD admission (if vitals recorded during admission)",
    )
    recorded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Vital Signs
    systolic_bp: Mapped[float | None] = mapped_column(Float, nullable=True)
    diastolic_bp: Mapped[float | None] = mapped_column(Float, nullable=True)
    heart_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    temperature_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    respiratory_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    spo2: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    height_cm: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Notes
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Timestamps
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    patient: Mapped["Patient"] = relationship("Patient")
    recorded_by: Mapped["User"] = relationship("User")
