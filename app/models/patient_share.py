# app/models/patient_share.py
"""
Patient sharing models for cross-tenant patient data sharing.
These tables live in the public schema since they span multiple tenants.
"""

import uuid
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.tenant_global import Tenant
from app.models.user import User


class ShareMode(str, PyEnum):
    READ_ONLY_LINK = "READ_ONLY_LINK"
    CREATE_RECORD = "CREATE_RECORD"


class ShareStatus(str, PyEnum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


class PatientShare(Base):
    """
    Represents a patient record share between tenants.
    Stored in public schema for cross-tenant access.
    """

    __tablename__ = "patient_shares"
    __table_args__ = (
        Index("idx_patient_share_token", "token"),
        Index("idx_patient_share_expires", "expires_at"),
        {"schema": "public"},
    )

    # Primary Key
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # Foreign Keys
    source_tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target_tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        doc="NULL for read-only link mode",
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Share Information
    patient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
        doc="Patient ID in source tenant schema",
    )
    share_mode: Mapped[ShareMode] = mapped_column(
        Enum(ShareMode, name="share_mode_enum"),
        nullable=False,
    )
    token: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
        doc="Secure random token for read-only link access",
    )
    note: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Optional note from sharing user",
    )

    # Status
    status: Mapped[ShareStatus] = mapped_column(
        Enum(ShareStatus, name="share_status_enum"),
        nullable=False,
        default=ShareStatus.ACTIVE,
        server_default=text("'ACTIVE'"),
    )

    # Timestamps
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    source_tenant: Mapped["Tenant"] = relationship(
        "Tenant",
        foreign_keys=[source_tenant_id],
        backref="shared_patients_source",
    )
    target_tenant: Mapped["Tenant | None"] = relationship(
        "Tenant",
        foreign_keys=[target_tenant_id],
        backref="shared_patients_target",
    )
    created_by: Mapped["User"] = relationship("User")

    links: Mapped[list["PatientShareLink"]] = relationship(
        "PatientShareLink", back_populates="share"
    )

    access_logs: Mapped[list["PatientShareAccessLog"]] = relationship(
        "PatientShareAccessLog", back_populates="share"
    )


class PatientShareLink(Base):
    """
    Links a source patient to a target patient when share_mode is CREATE_RECORD.
    """

    __tablename__ = "patient_share_links"
    __table_args__ = {"schema": "public"}

    # Primary Key
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # Foreign Keys
    share_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.patient_shares.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Link Information
    source_patient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        doc="Patient ID in source tenant schema",
    )
    target_patient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        doc="Patient ID in target tenant schema",
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    share: Mapped["PatientShare"] = relationship("PatientShare", back_populates="links")


class PatientShareAccessLog(Base):
    """
    Logs access to shared patient records.
    """

    __tablename__ = "patient_share_access_logs"
    __table_args__ = {"schema": "public"}

    # Primary Key
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # Foreign Keys
    share_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.patient_shares.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    accessed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Access Information
    ip_address: Mapped[str | None] = mapped_column(
        String(45),
        nullable=True,
        doc="IPv4 or IPv6 address",
    )
    user_agent: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    # Timestamps
    accessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        index=True,
    )

    share: Mapped["PatientShare"] = relationship(
        "PatientShare", back_populates="access_logs"
    )
    accessed_by: Mapped["User | None"] = relationship("User")
