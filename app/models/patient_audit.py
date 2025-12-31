# app/models/patient_audit.py
import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PatientAuditLog(Base):
    """
    Audit log for patient record changes.
    Lives in the tenant schema.
    """

    __tablename__ = "patient_audit_logs"

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
    changed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Audit Information
    action: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        doc="Action type: CREATE, UPDATE, DELETE, MERGE, DUPLICATE_OVERRIDE",
    )
    change_reason: Mapped[str | None] = mapped_column(
        String(500), nullable=True, doc="Reason for change (user-provided)"
    )
    old_values: Mapped[str | None] = mapped_column(Text, nullable=True, doc="JSON snapshot of old values")
    new_values: Mapped[str | None] = mapped_column(Text, nullable=True, doc="JSON snapshot of new values")
    metadata_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Additional metadata (e.g., duplicate_match_id, merge_target_id)",
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        index=True,
    )
