# app/models/document.py
import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.patient import Patient
from app.models.user import User


class Document(Base):
    __tablename__ = "documents"

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
    uploaded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Document Information
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    document_type: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        doc="Type of document (e.g., 'Prescription', 'Lab Report', 'X-Ray', 'Other')",
    )
    mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    storage_path: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        doc="Relative path or key to storage backend (local or S3/Supabase)",
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    patient: Mapped["Patient"] = relationship("Patient", backref="documents")
    uploaded_by: Mapped["User"] = relationship("User")
