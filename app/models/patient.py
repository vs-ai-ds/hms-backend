# app/models/patient.py
import uuid
from datetime import date, datetime
from enum import Enum

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.user import User


class PatientType(str, Enum):
    OPD = "OPD"
    IPD = "IPD"


class Gender(str, Enum):
    MALE = "MALE"
    FEMALE = "FEMALE"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class Patient(Base):
    """
    Tenant-scoped patient entity.

    NOTE:
    - This table lives in the tenant schema (per-tenant).
    - No tenant_id column; isolation is via Postgres schema.
    """

    __tablename__ = "patients"

    # Primary Key
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # Business Identifier
    patient_code: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        unique=True,
        index=True,
        doc="Unique patient identifier (format: {tenantId}-P-{sequential})",
    )

    # Foreign Keys
    # NOTE: department_id removed - department is per-visit (appointment/admission), not per-patient
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Personal Information
    first_name: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    middle_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    gender: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )  # MALE/FEMALE/OTHER/UNKNOWN

    # Date of Birth
    dob: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    dob_unknown: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    age_only: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Contact Information
    phone_primary: Mapped[str | None] = mapped_column(
        String(20), nullable=True, index=True
    )
    phone_alternate: Mapped[str | None] = mapped_column(String(20), nullable=True)
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)

    # Address
    address_line1: Mapped[str | None] = mapped_column(String(120), nullable=True)
    address_line2: Mapped[str | None] = mapped_column(String(120), nullable=True)
    city: Mapped[str | None] = mapped_column(String(80), nullable=True)
    state: Mapped[str | None] = mapped_column(String(100), nullable=True)
    country: Mapped[str | None] = mapped_column(String(100), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Emergency Contact
    emergency_contact_name: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    emergency_contact_relation: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )
    emergency_contact_phone: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )

    # Medical Information
    blood_group: Mapped[str | None] = mapped_column(String(10), nullable=True)
    marital_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    preferred_language: Mapped[str | None] = mapped_column(String(50), nullable=True)
    known_allergies: Mapped[str | None] = mapped_column(Text, nullable=True)
    chronic_conditions: Mapped[str | None] = mapped_column(Text, nullable=True)
    clinical_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # National ID
    national_id_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    national_id_number: Mapped[str | None] = mapped_column(
        String(32), nullable=True, index=True
    )

    # Photo
    photo_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # NOTE: patient_type is NOT stored - it is derived from active admission
    # Use get_patient_type() method or check for active admission directly

    # Flags
    is_dnr: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_deceased: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    consent_sms: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    consent_email: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Deceased Information
    date_of_death: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Timestamps
    last_visited_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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

    created_by: Mapped["User"] = relationship("User", foreign_keys=[created_by_id])
    updated_by: Mapped["User"] = relationship("User", foreign_keys=[updated_by_id])
