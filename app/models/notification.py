# app/models/notification.py
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
from app.models.user import User


class NotificationChannel(str, PyEnum):
    EMAIL = "EMAIL"
    SMS = "SMS"
    WHATSAPP = "WHATSAPP"


class NotificationStatus(str, PyEnum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"


# Enum type definitions - create_type=False prevents SQLAlchemy from auto-creating the types
# The types are created manually in tenant_service.py
NOTIFICATION_CHANNEL_ENUM = ENUM(
    NotificationChannel,
    name="notification_channel_enum",
    create_type=False,
)

NOTIFICATION_STATUS_ENUM = ENUM(
    NotificationStatus,
    name="notification_status_enum",
    create_type=False,
)


class Notification(Base):
    """
    Tenant-scoped notification log.
    Lives in the tenant schema (created when we add it to TENANT_TABLES).

    You can use this later to show a notification history per patient/user.
    """

    __tablename__ = "notifications"

    # Primary Key
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # Foreign Keys
    triggered_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Notification Details
    channel: Mapped[NotificationChannel] = mapped_column(
        NOTIFICATION_CHANNEL_ENUM,
        nullable=False,
    )
    recipient: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        doc="Email address or phone number.",
    )
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    message: Mapped[str] = mapped_column(String(2000), nullable=False)

    # Status
    status: Mapped[NotificationStatus] = mapped_column(
        NOTIFICATION_STATUS_ENUM,
        nullable=False,
        server_default=text("'PENDING'"),
    )
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    triggered_by: Mapped["User"] = relationship("User")
