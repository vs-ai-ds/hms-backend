# app/services/permission_service.py
"""
Service for resolving user permissions from tenant-scoped roles.
"""

from uuid import UUID

from sqlalchemy.orm import Session

from app.models.tenant_role import TenantRole, TenantRolePermission, TenantUserRole
from app.models.user import User


def get_user_permissions(db: Session, user: User, tenant_id: UUID) -> set[str]:
    """
    Resolve all permission codes for a user within a specific tenant.

    This queries the tenant schema's user_roles, roles, and role_permissions tables
    to build the complete set of permissions the user has in this tenant.

    Args:
        db: Database session (must have tenant schema in search_path)
        user: User object from public.users
        tenant_id: Tenant ID to resolve permissions for

    Returns:
        Set of permission code strings (e.g., {"dashboard:view", "patients:create", ...})
    """
    # Query tenant-scoped user roles
    user_roles = (
        db.query(TenantUserRole).filter(TenantUserRole.user_id == user.id).all()
    )

    if not user_roles:
        return set()

    # Get all role IDs
    role_ids = [ur.role_id for ur in user_roles]

    # Query role permissions for these roles
    role_permissions = (
        db.query(TenantRolePermission.permission_code)
        .filter(TenantRolePermission.role_id.in_(role_ids))
        .distinct()
        .all()
    )

    # Extract permission codes
    permissions = {rp[0] for rp in role_permissions}
    return permissions


def get_user_roles_with_permissions(
    db: Session, user: User, tenant_id: UUID
) -> list[dict]:
    """
    Get user's roles with their permissions in a tenant.
    Returns a list of dicts with 'name' and 'permissions' keys.
    This matches the frontend's expected CurrentUser.roles shape.

    Args:
        db: Database session (must have tenant schema in search_path)
        user: User object from public.users
        tenant_id: Tenant ID

    Returns:
        List of dicts: [{"name": "DOCTOR", "permissions": [{"code": "patients:view"}, ...]}, ...]
    """
    # Query tenant-scoped user roles
    user_roles = (
        db.query(TenantUserRole)
        .join(TenantRole, TenantUserRole.role_id == TenantRole.id)
        .filter(TenantUserRole.user_id == user.id)
        .all()
    )

    result = []
    for ur in user_roles:
        role = ur.role
        # Get permissions for this role
        role_perms = (
            db.query(TenantRolePermission.permission_code)
            .filter(TenantRolePermission.role_id == role.id)
            .all()
        )

        permissions = [{"code": rp[0]} for rp in role_perms]
        result.append(
            {
                "name": role.name,
                "permissions": permissions,
            }
        )

    return result
