# app/dependencies/authz.py
from typing import Iterable

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.v1.endpoints.auth import get_current_user
from app.core.database import get_db
from app.core.tenant_context import TenantContext, get_tenant_context
from app.models.user import RoleName, User
from app.services.permission_service import get_user_permissions


def require_roles(required_roles: Iterable[RoleName]):
    """
    Dependency factory for role-based access.

    Usage:

    @router.get("/admin")
    def admin_only(user = Depends(require_roles([RoleName.HOSPITAL_ADMIN]))):
        ...

    Returns the current_user if they have at least one required role.
    Note: This now queries tenant-scoped roles.
    """

    required = {r.value if isinstance(r, RoleName) else str(r) for r in required_roles}

    def dependency(
        current_user: User = Depends(get_current_user),
        ctx: TenantContext = Depends(get_tenant_context),
        db: Session = Depends(get_db),
    ) -> User:
        from app.models.tenant_role import TenantRole, TenantUserRole

        # Query tenant-scoped user roles
        user_roles = (
            db.query(TenantUserRole)
            .join(TenantRole, TenantUserRole.role_id == TenantRole.id)
            .filter(TenantUserRole.user_id == current_user.id)
            .all()
        )

        user_role_names = {ur.role.name for ur in user_roles}
        if not user_role_names.intersection(required):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient role permissions.",
            )
        return current_user

    return dependency


def require_permission(permission_code: str):
    """
    Dependency factory for permission-based access control (ABAC).

    Usage:

    @router.get("/users")
    def list_users(
        current_user: User = Depends(require_permission("users:view")),
        ctx: TenantContext = Depends(get_tenant_context),
        ...
    ):
        ...

    Returns the current_user if they have the required permission.
    Now queries tenant-scoped roles and permissions.
    """

    def dependency(
        current_user: User = Depends(get_current_user),
        ctx: TenantContext = Depends(get_tenant_context),
        db: Session = Depends(get_db),
    ) -> User:
        # Get permissions from tenant-scoped roles
        user_permissions = get_user_permissions(db, current_user, ctx.tenant.id)

        if permission_code not in user_permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions. Required: {permission_code}",
            )

        return current_user

    return dependency
