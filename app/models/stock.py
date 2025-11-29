# app/models/stock.py
import uuid
from datetime import datetime

from sqlalchemy import (
    String,
    Integer,
    DateTime,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class StockItem(Base):
    """
    Represents a medicine or consumable item in a hospital's inventory.

    Tenant-scoped; lives in the tenant schema.
    """

    __tablename__ = "stock_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        doc="Optional internal / catalog code.",
    )

    unit: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        doc="e.g., tablet, ml, mg, bottle, vial, etc.",
    )

    quantity_available: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
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