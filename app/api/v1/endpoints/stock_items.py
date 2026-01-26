# app/api/v1/endpoints/stock_items.py
from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.tenant_context import TenantContext, get_tenant_context
from app.core.tenant_db import ensure_search_path
from app.dependencies.authz import require_permission
from app.models.stock import StockItem, StockItemType
from app.models.user import User
from app.schemas.stock import StockItemCreate, StockItemResponse, StockItemUpdate

router = APIRouter()
logger = logging.getLogger(__name__)


def _reload_stock_item(
    db: Session, stock_item_id: UUID, tenant_schema_name: str
) -> StockItem:
    """
    Re-query after commit so we return a fresh, attached ORM object
    and avoid expired/lazy-load surprises after transaction boundaries.
    Does NOT set search_path - caller must ensure it's set.
    """
    ensure_search_path(db, tenant_schema_name)

    item = db.query(StockItem).filter(StockItem.id == stock_item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Stock item not found.")
    return item


@router.get("", response_model=list[StockItemResponse], tags=["stock-items"])
def list_stock_items(
    search: Optional[str] = Query(
        None, description="Search by name or generic_name (case-insensitive)"
    ),
    type: Optional[StockItemType] = Query(
        None, description="Filter by type (MEDICINE, EQUIPMENT, or CONSUMABLE)"
    ),
    limit: int = Query(20, ge=1, le=50, description="Maximum number of results"),
    include_inactive: bool = Query(
        False, description="Include inactive items (requires manage permission)"
    ),
    sort_by: Optional[str] = Query(
        None, description="Sort by field: 'name', 'type', or 'current_stock'"
    ),
    sort_dir: Optional[str] = Query(
        "asc", description="Sort direction: 'asc' or 'desc'"
    ),
    current_user: User = Depends(require_permission("stock_items:view")),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> list[StockItemResponse]:
    """
    List stock items for the current tenant.
    """
    ensure_search_path(db, ctx.tenant.schema_name)

    try:
        query = db.query(StockItem)

        # include_inactive requires manage permission; otherwise force active only
        if include_inactive:
            from app.services.permission_service import get_user_permissions

            user_permissions = get_user_permissions(db, ctx.user, ctx.tenant.id)
            if "stock_items:manage" not in user_permissions:
                include_inactive = False

        if not include_inactive:
            query = query.filter(StockItem.is_active.is_(True))

        if type:
            query = query.filter(StockItem.type == type)

        if search and search.strip():
            search_term = f"%{search.strip()}%"
            query = query.filter(
                or_(
                    StockItem.name.ilike(search_term),
                    StockItem.generic_name.ilike(search_term),
                )
            )

        # Sorting
        sort_dir_lower = (sort_dir or "asc").lower()
        desc = sort_dir_lower == "desc"

        if sort_by == "name":
            query = query.order_by(
                StockItem.name.desc() if desc else StockItem.name.asc()
            )
        elif sort_by == "type":
            query = query.order_by(
                StockItem.type.desc() if desc else StockItem.type.asc()
            )
        elif sort_by == "current_stock":
            query = query.order_by(
                StockItem.current_stock.desc()
                if desc
                else StockItem.current_stock.asc()
            )
        else:
            query = query.order_by(StockItem.name.asc())

        items = query.limit(limit).all()

    except Exception:
        # Keep your current behavior: don't blow the UI up if tenant table missing,
        # but DO log the real error.
        logger.exception("Error querying stock_items tenant=%s", ctx.tenant.schema_name)
        return []

    result: list[StockItemResponse] = []
    for item in items:
        try:
            result.append(StockItemResponse.model_validate(item))
        except Exception:
            logger.exception(
                "Skipping stock item due to validation error item_id=%s",
                getattr(item, "id", None),
            )
            continue
    return result


@router.post(
    "",
    response_model=StockItemResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["stock-items"],
)
def create_stock_item(
    payload: StockItemCreate,
    current_user: User = Depends(require_permission("stock_items:manage")),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> StockItemResponse:
    """
    Create a new stock item for the current tenant.
    """
    ensure_search_path(db, ctx.tenant.schema_name)

    existing = (
        db.query(StockItem)
        .filter(
            StockItem.type == payload.type,
            StockItem.name == payload.name,
            StockItem.form == payload.form,
            StockItem.strength == payload.strength,
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A stock item with the same name, form and strength already exists.",
        )

    stock_item = StockItem(
        type=payload.type,
        name=payload.name,
        generic_name=payload.generic_name,
        form=payload.form,
        strength=payload.strength,
        route=payload.route,
        default_dosage=payload.default_dosage,
        default_frequency=payload.default_frequency,
        default_duration=payload.default_duration,
        default_instructions=payload.default_instructions,
        current_stock=payload.current_stock,
        reorder_level=payload.reorder_level,
        is_active=payload.is_active,
        created_by_id=ctx.user.id,
    )

    try:
        db.add(stock_item)
        db.flush()
        stock_item_id = stock_item.id
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create stock item.",
        )

    stock_item = _reload_stock_item(db, stock_item_id, ctx.tenant.schema_name)
    return StockItemResponse.model_validate(stock_item)


@router.get(
    "/{stock_item_id}",
    response_model=StockItemResponse,
    tags=["stock-items"],
)
def get_stock_item(
    stock_item_id: UUID,
    current_user: User = Depends(require_permission("stock_items:view")),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> StockItemResponse:
    """
    Get a single stock item by ID.
    """
    ensure_search_path(db, ctx.tenant.schema_name)

    stock_item = db.query(StockItem).filter(StockItem.id == stock_item_id).first()
    if not stock_item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Stock item not found.",
        )

    return StockItemResponse.model_validate(stock_item)


@router.patch(
    "/{stock_item_id}",
    response_model=StockItemResponse,
    tags=["stock-items"],
)
def update_stock_item(
    stock_item_id: UUID,
    payload: StockItemUpdate,
    current_user: User = Depends(require_permission("stock_items:manage")),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> StockItemResponse:
    """
    Update a stock item.
    Supports full updates and partial updates (e.g., toggling is_active).
    """
    ensure_search_path(db, ctx.tenant.schema_name)

    stock_item = db.query(StockItem).filter(StockItem.id == stock_item_id).first()
    if not stock_item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Stock item not found.",
        )

    # Determine effective type
    effective_type = payload.type if payload.type is not None else stock_item.type

    # Validate medicine rules
    if effective_type == StockItemType.MEDICINE:
        updating_medicine_fields = (
            payload.form is not None
            or payload.strength is not None
            or payload.default_dosage is not None
            or payload.default_frequency is not None
            or payload.default_duration is not None
        )

        if updating_medicine_fields or payload.type is not None:
            effective_form = (
                payload.form if payload.form is not None else stock_item.form
            )
            effective_strength = (
                payload.strength
                if payload.strength is not None
                else stock_item.strength
            )
            effective_dosage = (
                payload.default_dosage
                if payload.default_dosage is not None
                else stock_item.default_dosage
            )
            effective_frequency = (
                payload.default_frequency
                if payload.default_frequency is not None
                else stock_item.default_frequency
            )
            effective_duration = (
                payload.default_duration
                if payload.default_duration is not None
                else stock_item.default_duration
            )

            errors: list[str] = []
            if not effective_form or not effective_form.strip():
                errors.append("Form is required for MEDICINE type")
            if not effective_strength or not effective_strength.strip():
                errors.append("Strength is required for MEDICINE type")
            if not effective_dosage or not effective_dosage.strip():
                errors.append("Default dosage is required for MEDICINE type")
            if not effective_frequency or not effective_frequency.strip():
                errors.append("Default frequency is required for MEDICINE type")
            if not effective_duration or not effective_duration.strip():
                errors.append("Default duration is required for MEDICINE type")

            dosage_fields = [effective_dosage, effective_frequency, effective_duration]
            filled_count = sum(1 for f in dosage_fields if f and f.strip())
            if filled_count > 0 and filled_count < 3:
                errors.append(
                    "If any of default_dosage, default_frequency, or default_duration is provided, all three must be present"
                )

            if errors:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="; ".join(errors),
                )

    # Uniqueness check
    effective_name = payload.name if payload.name is not None else stock_item.name
    effective_form = payload.form if payload.form is not None else stock_item.form
    effective_strength = (
        payload.strength if payload.strength is not None else stock_item.strength
    )

    if (
        effective_type != stock_item.type
        or effective_name != stock_item.name
        or effective_form != stock_item.form
        or effective_strength != stock_item.strength
    ):
        existing = (
            db.query(StockItem)
            .filter(
                StockItem.id != stock_item_id,
                StockItem.type == effective_type,
                StockItem.name == effective_name,
                StockItem.form == effective_form,
                StockItem.strength == effective_strength,
            )
            .first()
        )
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A stock item with the same name, form and strength already exists.",
            )

    # Apply partial updates
    for field in (
        "type",
        "name",
        "generic_name",
        "form",
        "strength",
        "route",
        "default_dosage",
        "default_frequency",
        "default_duration",
        "default_instructions",
        "current_stock",
        "reorder_level",
        "is_active",
    ):
        value = getattr(payload, field, None)
        if value is not None:
            setattr(stock_item, field, value)

    # Persist changes
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update stock item.",
        )

    # Reload so response serialization is stable after commit
    stock_item = _reload_stock_item(db, stock_item_id, ctx.tenant.schema_name)

    # Best-effort low stock notifications (never fail API)
    try:
        if (
            stock_item.reorder_level
            and stock_item.reorder_level > 0
            and stock_item.current_stock <= stock_item.reorder_level
        ):
            from app.models.user import User as PublicUser
            from app.models.user import UserStatus
            from app.services.notification_service import send_notification_email

            admins = (
                db.query(PublicUser)
                .filter(
                    PublicUser.tenant_id == ctx.tenant.id,
                    PublicUser.status == UserStatus.ACTIVE,
                )
                .all()
            )

            for admin in admins:
                # roles may be relationship; protect against lazy-load failures
                try:
                    user_roles = (
                        [r.name for r in admin.roles]
                        if hasattr(admin, "roles") and admin.roles
                        else []
                    )
                except Exception:
                    logger.exception(
                        "Failed to read roles for admin_id=%s while sending low stock email",
                        admin.id,
                    )
                    user_roles = []

                if (
                    "HOSPITAL_ADMIN" in user_roles or "PHARMACIST" in user_roles
                ) and admin.email:
                    try:
                        send_notification_email(
                            db=db,
                            to_email=admin.email,
                            subject=f"Low Stock Alert - {stock_item.name}",
                            body=(
                                f"Stock item {stock_item.name} has dropped below reorder level "
                                f"({stock_item.current_stock} / {stock_item.reorder_level})."
                            ),
                            triggered_by=ctx.user,
                            reason="stock_low_alert",
                            tenant_schema_name=ctx.tenant.schema_name,
                        )
                    except Exception:
                        logger.exception(
                            "Low stock email failed to=%s item_id=%s",
                            admin.email,
                            stock_item.id,
                        )
    except Exception:
        logger.exception(
            "Low stock notification block failed item_id=%s", stock_item.id
        )

    return StockItemResponse.model_validate(stock_item)
