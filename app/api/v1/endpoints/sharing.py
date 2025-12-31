# app/api/v1/endpoints/sharing.py

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.tenant_context import TenantContext, get_tenant_context
from app.core.tenant_db import ensure_search_path
from app.dependencies.authz import require_permission
from app.models.sharing import SharingRequest
from app.models.tenant_global import Tenant
from app.models.user import User
from app.schemas.sharing import SharingRequestCreate, SharingRequestResponse
from app.services.sharing_service import create_sharing_request

router = APIRouter()


@router.post(
    "/",
    response_model=SharingRequestResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["sharing"],
)
def create_sharing_request_endpoint(
    payload: SharingRequestCreate,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
    current_user: User = Depends(require_permission("sharing:create")),
) -> SharingRequestResponse:
    """
    Create a cross-tenant patient sharing request.

    Why we set search_path here (even though SharingRequest is in public):
    We have seen cases where the request pipeline/session ends up with a wrong search_path
    during commit/refresh/serialization, leading to "it worked but UI shows failed".
    Keeping tenant schema first is a safe default across endpoints.
    """
    ensure_search_path(db, ctx.tenant.schema_name)

    if payload.to_tenant_id == ctx.tenant.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot share patient with the same hospital.",
        )

    to_tenant = db.query(Tenant).filter(Tenant.id == payload.to_tenant_id).first()
    if not to_tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Target hospital not found.",
        )

    req = create_sharing_request(
        db=db,
        from_tenant_id=ctx.tenant.id,
        payload=payload,
    )

    # Defensive: after commit/refresh, ensure we still have the expected tenant search_path
    # before response serialization touches ORM state.
    ensure_search_path(db, ctx.tenant.schema_name)

    return SharingRequestResponse.model_validate(req)


@router.get(
    "/",
    response_model=list[SharingRequestResponse],
    tags=["sharing"],
)
def list_sharing_requests(
    direction: str = Query("outgoing", description="'incoming' or 'outgoing'"),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
    current_user: User = Depends(require_permission("sharing:view")),
) -> list[SharingRequestResponse]:
    """
    List sharing requests for the current tenant.
    - direction='outgoing': requests sent by this tenant
    - direction='incoming': requests received by this tenant
    """
    ensure_search_path(db, ctx.tenant.schema_name)

    if direction == "incoming":
        query = db.query(SharingRequest).filter(SharingRequest.to_tenant_id == ctx.tenant.id)
    else:
        query = db.query(SharingRequest).filter(SharingRequest.from_tenant_id == ctx.tenant.id)

    requests = query.order_by(SharingRequest.created_at.desc()).all()
    return [SharingRequestResponse.model_validate(r) for r in requests]


@router.get(
    "/tenants",
    response_model=list[dict],
    tags=["sharing"],
)
def list_tenants_for_sharing(
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> list[dict]:
    """
    List all active tenants (for sharing dropdown).
    Excludes the current tenant.
    """
    ensure_search_path(db, ctx.tenant.schema_name)

    tenants = (
        db.query(Tenant)
        .filter(
            Tenant.id != ctx.tenant.id,
            Tenant.status == "ACTIVE",
        )
        .order_by(Tenant.name)
        .all()
    )

    return [
        {
            "id": str(t.id),
            "name": t.name,
            "contact_email": t.contact_email,
        }
        for t in tenants
    ]