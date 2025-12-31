# app/core/tenant_context.py
from uuid import UUID

from fastapi import Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.v1.endpoints.auth import get_current_user
from app.core.database import get_db
from app.models.tenant_global import Tenant, TenantStatus
from app.models.user import User


class TenantContext:
    """
    Wraps the current tenant and user for tenant-scoped operations.

    - tenant: row from public.tenants
    - user:   current authenticated user (tenant user)
    """

    def __init__(self, tenant: Tenant, user: User):
        self.tenant = tenant
        self.user = user


def _set_tenant_search_path(db: Session, schema_name: str) -> None:
    """
    Set Postgres search_path so tenant-domain tables
    (patients, appointments, prescriptions, documents)
    are read/written in the correct schema.

    Order: tenant_schema, public.
    """
    conn = db.connection()
    conn.execute(text(f'SET search_path TO "{schema_name}", public'))


def get_tenant_context(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TenantContext:
    """
    Resolve tenant from current_user and set search_path.

    - SUPER_ADMIN: tenant_id must NOT be used for tenant-scoped endpoints.
    - Tenant users (HOSPITAL_ADMIN / DOCTOR / etc.): tenant_id must not be None.
    """
    if current_user.tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant-scoped operation requires a tenant user.",
        )

    tenant = db.query(Tenant).filter(Tenant.id == UUID(str(current_user.tenant_id))).first()
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found.",
        )

    # Enforce tenant status: only ACTIVE tenants can access tenant-scoped endpoints
    if tenant.status != TenantStatus.ACTIVE:
        if tenant.status == TenantStatus.PENDING:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Hospital registration is pending email verification. Please check your email and verify your account.",
            )
        elif tenant.status == TenantStatus.VERIFIED:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Hospital registration is pending activation. Please contact support.",
            )
        elif tenant.status == TenantStatus.SUSPENDED:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Hospital account is suspended. Please contact support.",
            )
        elif tenant.status == TenantStatus.INACTIVE:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Hospital account is inactive. Please contact support.",
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Hospital account is not active. Please contact support.",
            )

    # Ensure all tenant tables exist (in case they were created before all models were added)
    from app.services.tenant_service import ensure_tenant_tables_exist

    try:
        ensure_tenant_tables_exist(db, tenant.schema_name)
    except Exception as e:
        import logging

        logger = logging.getLogger(__name__)
        logger.error(f"Could not ensure tenant tables exist for schema {tenant.schema_name}: {e}", exc_info=True)
        # Don't fail the request, but log the error - tables should be created on next request

    _set_tenant_search_path(db, tenant.schema_name)

    return TenantContext(tenant=tenant, user=current_user)
