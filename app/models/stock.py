# app/models/stock.py
import uuid
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.user import User


class StockItemType(str, PyEnum):
    MEDICINE = "MEDICINE"
    EQUIPMENT = "EQUIPMENT"
    CONSUMABLE = "CONSUMABLE"


STOCK_ITEM_TYPE_ENUM = ENUM(
    StockItemType,
    name="stock_item_type_enum",
    create_type=False,
)


class StockItem(Base):
    """
    Stock master for medicines and equipment.
    Tenant-scoped (lives in tenant schema).
    This is a catalog/master table, not an inventory table.
    """

    __tablename__ = "stock_items"

    # Primary Key
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # Foreign Keys
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Item Type
    type: Mapped[StockItemType] = mapped_column(
        STOCK_ITEM_TYPE_ENUM,
        nullable=False,
        index=True,
    )

    # Item Information
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    generic_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    form: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )  # e.g. Tablet, Syrup
    strength: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )  # e.g. 500 mg
    route: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )  # e.g. oral, IV

    # Default Prescription Values
    default_dosage: Mapped[str | None] = mapped_column(String(50), nullable=True)
    default_frequency: Mapped[str | None] = mapped_column(String(50), nullable=True)
    default_duration: Mapped[str | None] = mapped_column(String(50), nullable=True)
    default_instructions: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Stock Tracking
    current_stock: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    reorder_level: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )

    # Flags
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
        index=True,
    )

    # Timestamps
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

    created_by: Mapped["User"] = relationship("User")

    __table_args__ = (
        # Unique constraint: (type, name, form, strength) per tenant
        # Note: Since this is tenant-scoped, uniqueness is enforced within tenant schema
        UniqueConstraint(
            "type", "name", "form", "strength", name="uq_stock_item_tenant"
        ),
        # Index for filtering by type and active status (used in autocomplete)
        Index("idx_stock_item_type_active", "type", "is_active"),
    )
