# app/models/sharing.py
import uuid
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    String,
    DateTime,
    Enum,
    ForeignKey,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.tenant_global import Tenant


class SharingStatus(str, PyEnum):
    PENDING_PATIENT = "PENDING_PATIENT"
    PENDING_ADMIN = "PENDING_ADMIN"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class SharingRequest(Base):
    """
    Cross-tenant patient record sharing request.

    Lives in the public schema because it references multiple tenants.
    """

    __tablename__ = "sharing_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    from_tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    to_tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )

    patient_global_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        doc="A patient identifier used across tenants / PHC/CHC (e.g., ABHA or national health ID).",
    )

    reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    status: Mapped[SharingStatus] = mapped_column(
        Enum(SharingStatus, name="sharing_status_enum"),
        nullable=False,
        server_default=text("'PENDING_PATIENT'"),
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

    from_tenant: Mapped["Tenant"] = relationship(
        "Tenant",
        foreign_keys=[from_tenant_id],
    )
    to_tenant: Mapped["Tenant"] = relationship(
        "Tenant",
        foreign_keys=[to_tenant_id],
    )