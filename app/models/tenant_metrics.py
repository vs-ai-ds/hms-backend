# app/models/tenant_metrics.py
"""
Platform-level metrics table to track aggregated counts across all tenants.
Stored in public schema for SUPER_ADMIN dashboard.
"""

import uuid

from sqlalchemy import Column, DateTime, Integer, text
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base


class TenantMetrics(Base):
    """
    Stores aggregated platform-level metrics.
    Updated via triggers or service functions when records are created/deleted.
    """

    __tablename__ = "tenant_metrics"
    __table_args__ = {"schema": "public"}

    # Single row - use a fixed ID
    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=lambda: uuid.UUID("00000000-0000-0000-0000-000000000001"),
        nullable=False,
    )

    # Aggregated counts (across ALL tenants, regardless of status)
    total_tenants = Column(Integer, nullable=False, default=0, server_default=text("0"))
    total_users = Column(Integer, nullable=False, default=0, server_default=text("0"))
    total_patients = Column(Integer, nullable=False, default=0, server_default=text("0"))
    total_appointments = Column(Integer, nullable=False, default=0, server_default=text("0"))
    total_prescriptions = Column(Integer, nullable=False, default=0, server_default=text("0"))

    # Timestamps
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=text("CURRENT_TIMESTAMP"),
    )
