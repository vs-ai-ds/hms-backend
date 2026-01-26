# app/services/user_role_service.py
"""
Helper service to get user roles from tenant schemas.
Replaces the old user.roles relationship.
"""

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.tenant_role import TenantRole, TenantUserRole
from app.models.user import User


def get_user_role_names(
    db: Session, user: User, tenant_schema_name: str | None = None
) -> set[str]:
    """
    Get user's role names from tenant schema.
    Returns a set of role name strings.

    Args:
        db: Database session
        user: User object
        tenant_schema_name: Optional tenant schema name. If provided, uses it directly.
                           If None, looks up from user.tenant_id.
    """
    if not user.tenant_id:
        # SUPER_ADMIN - users with tenant_id=None are SUPER_ADMIN
        # Check if there's a platform-level SUPER_ADMIN role assignment
        # For now, if tenant_id is None, assume SUPER_ADMIN
        return {"SUPER_ADMIN"}

    from app.models.tenant_global import Tenant

    # If schema name is provided, use it; otherwise look up tenant
    if tenant_schema_name:
        schema_name = tenant_schema_name
    else:
        tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
        if not tenant:
            return set()
        schema_name = tenant.schema_name

    # Check current search_path
    conn = db.connection()
    try:
        current_path = conn.execute(text("SHOW search_path")).scalar()
    except Exception:
        # If transaction is aborted, rollback first
        db.rollback()
        current_path = conn.execute(text("SHOW search_path")).scalar()

    # Only change search_path if it's not already set to the tenant schema
    needs_path_change = tenant_schema_name is None or schema_name not in current_path

    if needs_path_change:
        original_path = current_path
        try:
            conn.execute(text(f'SET search_path TO "{schema_name}", public'))
        except Exception:
            # If transaction is aborted, rollback first
            db.rollback()
            conn.execute(text(f'SET search_path TO "{schema_name}", public'))

    try:
        # Query tenant-scoped user roles
        user_roles = (
            db.query(TenantUserRole)
            .join(TenantRole, TenantUserRole.role_id == TenantRole.id)
            .filter(TenantUserRole.user_id == user.id)
            .all()
        )

        role_names = {ur.role.name for ur in user_roles}
        return role_names
    except Exception:
        # If query fails, rollback and restore search_path
        db.rollback()
        if needs_path_change:
            try:
                conn.execute(text(f"SET search_path TO {original_path}"))
            except Exception:
                pass
        raise
    finally:
        # Restore original search_path only if we changed it
        if needs_path_change:
            try:
                conn.execute(text(f"SET search_path TO {original_path}"))
            except Exception:
                # If transaction is aborted, rollback first
                try:
                    db.rollback()
                    conn.execute(text(f"SET search_path TO {original_path}"))
                except Exception:
                    pass
